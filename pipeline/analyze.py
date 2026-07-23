"""
Turn comments into numbers.

The architectural point of this file: the model writes the rules, the
code does the counting.

  build()   one model call reads a sample and writes a codebook: the
            themes present in this conversation and the keywords for each
  apply()   regex applies that codebook to every comment, free

That inversion is what makes cost flat in corpus size, results
reproducible, and every figure traceable to a rule you can read in
debug/codebook.json.

It gives up paraphrase and negation. A comment praising something without
using a keyword is missed, and "not worth it" matches a "worth" keyword.
Transfer figures therefore say whether a subject arrived, not its exact
share.
"""

import re

import pandas as pd

from pipeline import llm
import config

CODEBOOK_PROMPT = """Below is a sample of {n} comments from a YouTube
comment section. The videos are: {summary}

Identify the 5 to 8 THEMES that actually appear. A theme is what a comment
is ABOUT. Base them on what you see, not on what you would expect. Include
the mundane ones (off-topic banter, generic praise, questions) when they
are present, because they show how much of the thread is substantive.

COMMENTS:
{sample}

Return JSON:
{{"themes": [{{"name": "short name",
               "definition": "one sentence",
               "keywords": ["10 to 20 lowercase words or short phrases in the language(s) the comments use"]}}]}}

Order from most specific to most generic: the first match wins when these
are applied, so catch-all themes like generic praise go last."""

TOPUP_PROMPT = """These comments matched no existing theme.
Videos: {summary}
Themes already defined: {existing}

COMMENTS:
{sample}

Identify 1 to 4 ADDITIONAL themes covering them. Do not duplicate an
existing theme. If they are genuinely noise with no shared subject, return
an empty list.

Return JSON: {{"themes": [{{"name": "...", "definition": "...", "keywords": ["..."]}}]}}"""


# ------------------------------------------------------------- sampling

def _sample(df, size):
    """
    Draw across groups and across engagement, not flat random.

    A flat draw over-represents whichever video got the most comments and
    misses the high-engagement comments, which is where a thread's shared
    vocabulary lives. Three slices per group: most-liked, longest, random.

    The sample only DISCOVERS themes. Every percentage is counted over the
    full corpus, so this never has to be proportionally representative. It
    only has to contain one example of everything worth naming.
    """
    groups = df["group"].unique()
    per_group = max(30, size // max(len(groups), 1))
    picks = []

    for group in groups:
        sub = df[df["group"] == group]
        take = min(per_group, len(sub))
        n_top = n_long = take // 4

        top = sub.nlargest(n_top, "likes")
        rest = sub.drop(top.index)
        longest = (rest.assign(_len=rest["comment"].str.len())
                       .nlargest(n_long, "_len").drop(columns="_len"))
        rest = rest.drop(longest.index)
        random = rest.sample(min(take - n_top - n_long, len(rest)),
                             random_state=0)
        picks += [top, longest, random]

    return pd.concat(picks).drop_duplicates(subset=["comment"])


def _pattern(keywords):
    """One regex from a keyword list. Matches nothing if the list is empty."""
    parts = [re.escape(k.strip().lower()) for k in keywords if k.strip()]
    return re.compile("|".join(parts) if parts else r"(?!x)x", re.IGNORECASE)


# ------------------------------------------------------------- codebook

def build(df, summary):
    """One model call. Returns the theme list."""
    target = max(config.CODEBOOK_SAMPLE_SIZE, int(len(df) * 0.08))
    size = int(min(target, config.CODEBOOK_SAMPLE_MAX, len(df)))
    sample = _sample(df, size)
    print(f"    codebook sample: {len(sample)} of {len(df)}, "
          f"stratified by group and engagement")

    result = llm.ask_json(CODEBOOK_PROMPT.format(
        n=len(sample), summary=summary,
        sample="\n".join(f"- {c[:220]}" for c in sample["comment"])))
    return result.get("themes", [])


def apply_themes(df, themes):
    """
    First match wins. Vectorised: str.contains runs in C, where a row-wise
    apply would loop in Python once per theme per comment.
    """
    df = df.copy()
    df["theme"] = "Unclassified"
    unassigned = pd.Series(True, index=df.index)

    for theme in themes:
        if not unassigned.any():
            break
        hits = unassigned & df["comment"].str.contains(
            _pattern(theme.get("keywords", [])), na=False)
        df.loc[hits, "theme"] = theme["name"]
        unassigned &= ~hits

    return df


def extend(df, themes, summary):
    """
    Second pass, only if too much fell through. A high unclassified share
    means the sample missed vocabulary the rest of the corpus uses, so
    show the model what it missed rather than guessing.
    """
    leftover = df[df["theme"] == "Unclassified"]
    share = len(leftover) / len(df) * 100
    if share < config.UNCLASSIFIED_LIMIT or len(leftover) < 25:
        return themes, share

    print(f"    {share:.0f}% unclassified, one top-up pass")
    sample = _sample(leftover, min(config.CODEBOOK_SAMPLE_MAX,
                                   max(60, len(leftover) // 4)))
    result = llm.ask_json(TOPUP_PROMPT.format(
        summary=summary,
        existing=", ".join(t["name"] for t in themes),
        sample="\n".join(f"- {c[:220]}" for c in sample["comment"])))
    return themes + result.get("themes", []), share


# -------------------------------------------------------------- signals

def apply_points(df, points):
    """
    For each idea a video pushed, did it appear in that video's comments?

    These overlap on purpose, so they are boolean columns rather than one
    label. A point is only measured against the video it came from.
    """
    df = df.copy()
    columns = {}

    for point in points:
        column = "pt__" + re.sub(r"\W+", "_", point["label"].lower())[:40]
        if column in columns:
            continue
        columns[column] = point["label"]
        mask = df["video_id"] == point["video_id"]
        df[column] = False
        df.loc[mask, column] = df.loc[mask, "comment"].str.contains(
            _pattern(point.get("keywords", [])), na=False)

    return df, columns


def summarise(df, columns):
    """The two tables the report reasons over."""
    themes = (pd.crosstab(df["group"], df["theme"], normalize="index")
              .mul(100).round(1))

    rows = []
    for column, label in columns.items():
        for group, sub in df.groupby("group"):
            hits = int(sub[column].sum())
            if hits:
                rows.append({"group": group, "point": label,
                             "echoed_pct": round(sub[column].mean() * 100, 1),
                             "n": hits})
    return themes, pd.DataFrame(rows)


# -------------------------------------------------------------- emotion

def emotion(df):
    """
    Runs locally through HuggingFace, so it costs no tokens whatever the
    corpus size. First run downloads roughly 500 MB.

    Weak evidence and treated as such downstream: labels are assigned per
    comment with no context, so sarcasm and measured criticism both read
    as anger. The confidence share travels with the numbers so the report
    cannot present them without the caveat.
    """
    name = {"emotion": config.EMOTION_MODEL,
            "sentiment": config.SENTIMENT_MODEL}.get(config.EMOTION_MODE)
    if not name:
        raise ValueError(
            f"EMOTION_MODE must be 'emotion' or 'sentiment', "
            f"got {config.EMOTION_MODE!r}")

    from transformers import pipeline as hf
    print(f"    loading {name}")
    classifier = hf("text-classification", model=name)
    results = classifier(df["comment"].tolist(), truncation=True,
                         max_length=128, batch_size=32)

    df = df.copy()
    df["emotion"] = [r["label"] for r in results]
    df["emotion_confidence"] = [round(r["score"], 3) for r in results]

    low = (df["emotion_confidence"] < 0.6).mean() * 100
    print(f"    {low:.0f}% of labels below 0.6 confidence")
    if low > 40:
        print("    WARNING: mostly low-confidence. Directional at best.")

    table = (pd.crosstab(df["group"], df["emotion"], normalize="index")
             .mul(100).round(1))
    return df, {"table": table, "low_confidence_pct": low,
                "caveat": f"Model: {name}. {low:.0f}% of labels scored below "
                          f"0.6 confidence. Labels are assigned per comment "
                          f"with no context, so sarcasm and measured "
                          f"criticism are frequently misread."}