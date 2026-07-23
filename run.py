"""
Run the whole pipeline.

    pip install -r requirements.txt
    # edit config.py
    python run.py

Each run creates its own folder under output/, named after what was
analysed, and puts three files in it:

    report.pdf     the debrief. Read this.
    comments.csv   every comment, labelled, for digging through by hand.
    summary.csv    every number in tidy long format, for building charts.

Set SESSION_NAME in config.py to name the folder yourself, otherwise it is
built from the group names. Set KEEP_INTERMEDIATE to also get a debug/
subfolder with the working files. No stage reads those: data passes between
stages in memory, so they are there purely for auditing.
"""

import json
import os
import re

import config
from pipeline import (fetch, clean, video_brief, campaign_brief, codebook,
                      emotion, chart, synthesize, render, export)


def make_run_folder(parent):
    """
    This run's folder, named for what is being analysed rather than for
    what the report concludes. A folder called "kia-rivals" tells you
    which project it was; one called "verifiable-ideas-travel" does not.

    Re-running the same subject gives -2, -3 rather than overwriting.
    """
    name = config.SESSION_NAME
    if not name:
        groups = list(dict.fromkeys(
            entry.get("group", "") for entry in config.VIDEOS
            if entry.get("group")))
        name = "-".join(groups[:3])
        if len(groups) > 3:
            name += f"-and-{len(groups) - 3}-more"

    slug = re.sub(r"[^\w\s-]", "", name.lower())
    slug = re.sub(r"[\s_]+", "-", slug).strip("-")[:60] or "run"

    path = os.path.join(parent, slug)
    counter = 2
    while os.path.exists(path):
        path = os.path.join(parent, f"{slug}-{counter}")
        counter += 1
    os.makedirs(path)
    return path


def main():
    out_dir = make_run_folder(config.OUTPUT_DIR)
    debug_dir = os.path.join(out_dir, "debug")
    if config.KEEP_INTERMEDIATE:
        os.makedirs(debug_dir, exist_ok=True)

    def save_debug(name, content):
        """Write a working file, or do nothing if debug is off."""
        if not config.KEEP_INTERMEDIATE:
            return
        with open(os.path.join(debug_dir, name), "w",
                  encoding="utf-8") as handle:
            handle.write(content)

    print(f"Session: {out_dir}\n")
    print("[1/7] Fetching comments and transcripts")
    comments, meta = fetch.run()
    save_debug("01_comments_raw.csv", comments.to_csv(index=False))
    save_debug("01_video_meta.csv",
               meta.drop(columns=["transcript"]).to_csv(index=False))

    print("[2/7] Cleaning and filtering")
    comments = clean.run(comments)
    base = comments[comments["in_base"]].reset_index(drop=True)
    print(f"    analysis base: {len(base)} comments")

    print("[3/7] Reading what each video pushed")
    brief_md, points = video_brief.run(meta)
    save_debug("03_video_brief.md", brief_md)
    save_debug("03_points.json",
               json.dumps(points, ensure_ascii=False, indent=2))

    background_md = ""
    if config.CAMPAIGN_BACKGROUND:
        print("    building campaign background briefs")
        background_md = campaign_brief.run(meta, config.CAMPAIGN_CONTEXT)
        save_debug("03b_campaign_background.md",
                   "> Model-generated. Not grounded in the comment data. "
                   "Verify before client use.\n\n" + background_md)

    print("[4/7] Writing and applying the codebook")
    summary = "; ".join(meta["title"].fillna("").astype(str).head(6))
    themes = codebook.build(base, summary)
    for theme in themes:
        print(f"    theme: {theme['name']}")

    base = codebook.apply_themes(base, themes)
    themes, unclassified = codebook.extend(base, themes, summary)
    if unclassified >= config.UNCLASSIFIED_LIMIT:
        base = codebook.apply_themes(base, themes)

    leftover = (base["theme"] == "Unclassified").mean() * 100
    print(f"    unclassified after coding: {leftover:.0f}%")
    if leftover > 40:
        print("    WARNING: the codebook is not covering this conversation "
              "well. Turn on KEEP_INTERMEDIATE and check it before "
              "trusting the report.")
    save_debug("04_codebook.json",
               json.dumps(themes, ensure_ascii=False, indent=2))

    base, point_columns = codebook.apply_points(base, points)
    theme_table, transfer_table = codebook.summarise(base, point_columns)

    print("[5/7] Emotion labels")
    base, emotion_result = emotion.run(base)

    print("[6/7] Writing the debrief")
    report = synthesize.run(brief_md, background_md, theme_table,
                            transfer_table, emotion_result, base)
    with open(os.path.join(out_dir, "report.md"), "w",
              encoding="utf-8") as handle:
        handle.write(report)
    html_path, pdf_path = render.run(report, out_dir, "report")

    print("[7/7] Exporting data files")
    export.run(base, theme_table, transfer_table, emotion_result, meta,
               out_dir)

    if config.KEEP_INTERMEDIATE:
        chart.run(theme_table, transfer_table, debug_dir)

    print(f"\nDone. {out_dir}")
    print(f"  {os.path.basename(pdf_path or html_path)}")
    print("  comments.csv")
    print("  summary.csv")
    if config.KEEP_INTERMEDIATE:
        print("  debug/")


if __name__ == "__main__":
    main()