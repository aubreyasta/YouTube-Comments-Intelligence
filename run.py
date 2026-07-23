"""
    pip install -r requirements.txt
    # edit config.py
    python run.py

Each run creates a folder under output/, named after what was analysed,
holding exactly three files:

    report.pdf     the debrief. Read this.
    comments.csv   every comment, labelled, for digging through by hand.
    summary.csv    every number in tidy long format, for building charts.

Set KEEP_INTERMEDIATE in config.py to also get a debug/ subfolder with the
working files. No stage reads those: data passes between stages in memory,
so they exist purely for auditing. The one worth having is codebook.json,
which holds the exact keyword rules behind every percentage.
"""

import json
import os
import re

import config
from pipeline import collect, brief, analyze, report


def preflight():
    """
    Check everything the run depends on before spending a single call.

    Without this a missing PDF engine fails at the very last step, after
    five model calls and a model download have already been paid for.
    """
    from importlib.util import find_spec

    problems = []
    if "PASTE" in config.YOUTUBE_API_KEY or not config.YOUTUBE_API_KEY:
        problems.append("YOUTUBE_API_KEY is not set in config.py")
    if "PASTE" in config.GEMINI_API_KEY or not config.GEMINI_API_KEY:
        problems.append("GEMINI_API_KEY is not set in config.py")
    if not config.VIDEOS:
        problems.append("VIDEOS is empty in config.py")
    if config.EMOTION_MODE not in ("emotion", "sentiment"):
        problems.append("EMOTION_MODE must be 'emotion' or 'sentiment'")
    if not find_spec("transformers"):
        problems.append("transformers is not installed "
                        "(pip install transformers torch)")
    if not report.pdf_engine():
        problems.append(
            "no PDF engine found. Install one:\n"
            "        pip install playwright && playwright install chromium")

    if problems:
        raise SystemExit("Cannot start:\n  - " + "\n  - ".join(problems))


def run_folder():
    """
    Named for what is being analysed, so sessions are distinguishable.
    Repeat runs of the same subject get -2, -3 rather than overwriting.
    """
    name = config.SESSION_NAME
    if not name:
        groups = list(dict.fromkeys(v.get("group", "") for v in config.VIDEOS
                                    if v.get("group")))
        name = "-".join(groups[:3])
        if len(groups) > 3:
            name += f"-and-{len(groups) - 3}-more"

    slug = re.sub(r"[\s_]+", "-",
                  re.sub(r"[^\w\s-]", "", name.lower())).strip("-")[:60]
    path = os.path.join(config.OUTPUT_DIR, slug or "run")
    counter = 2
    while os.path.exists(path):
        path = os.path.join(config.OUTPUT_DIR, f"{slug}-{counter}")
        counter += 1
    os.makedirs(path)
    return path


def main():
    preflight()
    out_dir = run_folder()
    debug_dir = os.path.join(out_dir, "debug")
    if config.KEEP_INTERMEDIATE:
        os.makedirs(debug_dir, exist_ok=True)

    def save(name, content):
        if config.KEEP_INTERMEDIATE:
            with open(os.path.join(debug_dir, name), "w",
                      encoding="utf-8") as handle:
                handle.write(content)

    print(f"Session: {out_dir}\n")

    print("[1/5] Collecting")
    comments, meta = collect.fetch()
    save("comments_raw.csv", comments.to_csv(index=False))
    comments = collect.clean(comments)
    base = comments[comments["in_base"]].reset_index(drop=True)
    print(f"    analysis base: {len(base)} comments")

    print("[2/5] Reading the videos")
    grounded, background, points = brief.run(meta, config.CAMPAIGN_CONTEXT)
    save("video_brief.md", grounded)
    save("points.json", json.dumps(points, ensure_ascii=False, indent=2))
    if background:
        save("campaign_background.md",
             "> Model-generated. Not grounded in the comment data. Verify "
             "before client use.\n\n" + background)

    print("[3/5] Coding the comments")
    summary = "; ".join(meta["title"].fillna("").astype(str).head(6))
    themes = analyze.build(base, summary)
    for theme in themes:
        print(f"    theme: {theme['name']}")

    base = analyze.apply_themes(base, themes)
    themes, _ = analyze.extend(base, themes, summary)
    base = analyze.apply_themes(base, themes)
    save("codebook.json", json.dumps(themes, ensure_ascii=False, indent=2))

    leftover = (base["theme"] == "Unclassified").mean() * 100
    print(f"    unclassified: {leftover:.0f}%")
    if leftover > 40:
        print("    WARNING: the codebook is not covering this conversation. "
              "Turn on KEEP_INTERMEDIATE and read codebook.json before "
              "trusting the report.")

    base, columns = analyze.apply_points(base, points)
    theme_table, transfer_table = analyze.summarise(base, columns)

    print("[4/5] Emotion")
    base, emotion_result = analyze.emotion(base)

    print("[5/5] Writing")
    markdown = report.write(grounded, background, theme_table,
                            transfer_table, emotion_result, base)
    report.render(markdown, out_dir,
                  debug_dir if config.KEEP_INTERMEDIATE else None)
    report.export(base, theme_table, transfer_table, emotion_result, meta,
                  out_dir)

    print(f"\nDone. {out_dir}")
    for name in ("report.pdf", "comments.csv", "summary.csv"):
        print(f"  {name}")
    if config.KEEP_INTERMEDIATE:
        print("  debug/")


if __name__ == "__main__":
    main()