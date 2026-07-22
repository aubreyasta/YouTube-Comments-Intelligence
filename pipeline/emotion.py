"""
Stage 4b - emotion / sentiment labels.

Optional. On by default, but can be turned off as these labels are usually 
worse than the theme mix at explaining reception, and a client who asks for 
"sentiment" often actually wants to know what people talked about. 
But clients do ask, so it is here.

Runs LOCALLY via HuggingFace transformers, not through an API. That means:
  - no token cost at all, whatever the corpus size
  - a one-time model download of roughly 500 MB
  - slower on a laptop with no GPU, but workable

Set EMOTION_MODE in config.py to "emotion", "sentiment", or None.
"""

import pandas as pd
import config as config

# Indonesian-language models. Swap these in config.py for other languages.
MODELS = {
    "emotion": config.EMOTION_MODEL,
    "sentiment": config.SENTIMENT_MODEL,
}


def run(df):
    mode = config.EMOTION_MODE
    if not mode:
        print("    emotion stage disabled")
        return df, None

    model_name = MODELS.get(mode)
    if not model_name:
        print(f"    unknown EMOTION_MODE '{mode}', skipping")
        return df, None

    from transformers import pipeline as hf_pipeline
    print(f"    loading {model_name} (first run downloads it)")
    classifier = hf_pipeline("text-classification", model=model_name)

    results = classifier(df["comment"].tolist(), truncation=True,
                         max_length=128, batch_size=16)
    df["emotion"] = [r["label"] for r in results]
    df["emotion_confidence"] = [round(r["score"], 3) for r in results]

    table = (pd.crosstab(df["group"], df["emotion"], normalize="index")
             .mul(100).round(1))

    low = (df["emotion_confidence"] < 0.6).mean() * 100
    print(f"    {low:.0f}% of labels below 0.6 confidence")
    if low > 40:
        print("    WARNING: most labels are low-confidence. Report these "
              "as directional at best, and spot-check before presenting.")

    # Attach the caveat to the data itself so the report stage sees it.
    caveat = (f"Model: {model_name}. {low:.0f}% of labels scored below 0.6 "
              f"confidence. Labels are assigned per comment with no context, "
              f"so sarcasm and measured criticism are frequently misread.")
    return df, {"table": table, "caveat": caveat, "low_confidence_pct": low}