"""
Run the whole pipeline.

    pip install -r requirements.txt
    # edit config.py
    python run.py

Everything lands in output/.
"""

import json
import os
import pandas as pd

import config as config
from pipeline import (fetch, clean, video_brief, campaign_brief,
                      codebook, emotion, synthesize)


def main():
    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    out = lambda name: os.path.join(config.OUTPUT_DIR, name)

    print("[1/7] Fetching comments and transcripts")
    comments, meta = fetch.run()
    comments.to_csv(out("01_comments_raw.csv"), index=False,
                    encoding="utf-8-sig")
    meta.drop(columns=["transcript"]).to_csv(out("01_video_meta.csv"),
                                             index=False, encoding="utf-8-sig")

    print("[2/7] Cleaning and filtering")
    comments = clean.run(comments)
    comments.to_csv(out("02_comments_labelled.csv"), index=False,
                    encoding="utf-8-sig")
    base = comments[comments["in_base"]].reset_index(drop=True)
    print(f"    analysis base: {len(base)} comments")

    print("[3/7] Reading what each video pushed")
    brief_md, points = video_brief.run(meta)
    with open(out("03_video_brief.md"), "w", encoding="utf-8") as handle:
        handle.write("# What the videos put forward\n\n" + brief_md)
    with open(out("03_points.json"), "w", encoding="utf-8") as handle:
        json.dump(points, handle, ensure_ascii=False, indent=2)

    background_md = ""
    if config.CAMPAIGN_BACKGROUND:
        print("[3b/7] Building campaign background briefs")
        background_md = campaign_brief.run(meta, config.CAMPAIGN_CONTEXT)
        with open(out("03b_campaign_background.md"), "w",
                  encoding="utf-8") as handle:
            handle.write("# Campaign background\n\n"
                         "> Model-generated. Not grounded in the comment "
                         "data. Verify before client use.\n\n"
                         + background_md)

    print("[4/7] Writing and applying the codebook")
    summary = "; ".join(meta["title"].fillna("").astype(str).head(6))
    themes = codebook.build(base, summary)
    with open(out("04_codebook.json"), "w", encoding="utf-8") as handle:
        json.dump(themes, handle, ensure_ascii=False, indent=2)
    for theme in themes:
        print(f"    theme: {theme['name']}")

    base = codebook.apply_themes(base, themes)

    # If too much fell through, the sample missed vocabulary. Extend once.
    themes, unclassified = codebook.extend(base, themes, summary)
    if unclassified >= config.UNCLASSIFIED_LIMIT:
        base = codebook.apply_themes(base, themes)
        with open(out("04_codebook.json"), "w", encoding="utf-8") as handle:
            json.dump(themes, handle, ensure_ascii=False, indent=2)
    final_unclassified = (base["theme"] == "Unclassified").mean() * 100
    print(f"    unclassified after coding: {final_unclassified:.0f}%")
    if final_unclassified > 40:
        print("    WARNING: high unclassified share. The codebook is not "
              "covering this conversation well. Check 04_codebook.json "
              "before trusting the report.")

    base, point_columns = codebook.apply_points(base, points)
    theme_table, transfer_table = codebook.summarise(base, point_columns)

    print("[5/7] Emotion labels")
    base, emotion_result = emotion.run(base)
    if emotion_result:
        emotion_result["table"].to_csv(out("05_emotion_mix.csv"))
        print("\n" + emotion_result["table"].to_string() + "\n")
    base.to_csv(out("04_comments_coded.csv"), index=False,
                encoding="utf-8-sig")
    theme_table.to_csv(out("04_theme_mix.csv"))
    transfer_table.to_csv(out("04_signal_transfer.csv"), index=False)

    print("\n" + theme_table.to_string() + "\n")
    if not transfer_table.empty:
        print(transfer_table.to_string(index=False) + "\n")

    print("[6/7] Writing the report")
    report = synthesize.run(brief_md, background_md, theme_table,
                            transfer_table, emotion_result, base)
    with open(out("06_report.md"), "w", encoding="utf-8") as handle:
        handle.write(report)

    print("[7/7] Done. See output/06_report.md")
    print("      Check output/04_codebook.json to see the rules the model "
          "wrote, and 04_comments_coded.csv to check them against comments.")


if __name__ == "__main__":
    main()