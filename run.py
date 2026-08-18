"""
    pip install -r requirements.txt
    # edit config.py
    python run.py

Each run creates a folder under output/, named after what was analysed,
holding six files: report.pdf, comments.csv, key-messages.csv, themes.csv,
sentiment.csv, emotions.csv. See README.md#what-you-get for what each one
is for.

Set KEEP_INTERMEDIATE in config.py for a debug/ subfolder of working files;
see docs/setup.md#running for what's in it.
"""

import json
import os
import re

import config as _config_module
from pipeline import collect, brief, analyze, report, llm
from pipeline.config_types import PipelineConfig


def _load_cfg() -> PipelineConfig:
    """Build a PipelineConfig from the config.py module-level names."""
    return PipelineConfig(
        YOUTUBE_API_KEY=_config_module.YOUTUBE_API_KEY,
        OLLAMA_BASE_URL=getattr(
            _config_module, "OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
        TEXT_MODEL=getattr(
            _config_module, "TEXT_MODEL", "qwen3:14b-q4_K_M"),
        VISION_MODEL=getattr(
            _config_module, "VISION_MODEL", "qwen3-vl:8b-instruct-q4_K_M"),
        OLLAMA_TEXT_NUM_CTX=getattr(
            _config_module, "OLLAMA_TEXT_NUM_CTX", 32768),
        OLLAMA_VISION_NUM_CTX=getattr(
            _config_module, "OLLAMA_VISION_NUM_CTX", 8192),
        OLLAMA_TIMEOUT_SECONDS=getattr(
            _config_module, "OLLAMA_TIMEOUT_SECONDS", 600),
        OLLAMA_KEEP_ALIVE=getattr(_config_module, "OLLAMA_KEEP_ALIVE", "10m"),
        VIDEOS=_config_module.VIDEOS,
        SESSION_NAME=_config_module.SESSION_NAME,
        OUTPUT_DIR=_config_module.OUTPUT_DIR,
        KEEP_LANGUAGES=_config_module.KEEP_LANGUAGES,
        MIN_COMMENT_LETTERS=_config_module.MIN_COMMENT_LETTERS,
        MAX_COMMENTS_PER_VIDEO=_config_module.MAX_COMMENTS_PER_VIDEO,
        CODEBOOK_SAMPLE_SIZE=_config_module.CODEBOOK_SAMPLE_SIZE,
        CODEBOOK_SAMPLE_MAX=_config_module.CODEBOOK_SAMPLE_MAX,
        CLASSIFY_BATCH_SIZE=_config_module.CLASSIFY_BATCH_SIZE,
        UNCLASSIFIED_LIMIT=_config_module.UNCLASSIFIED_LIMIT,
        REPORT_LANGUAGE=_config_module.REPORT_LANGUAGE,
        CAMPAIGN_CONTEXT=_config_module.CAMPAIGN_CONTEXT,
        KEEP_INTERMEDIATE=getattr(_config_module, "KEEP_INTERMEDIATE", False),
    )


def preflight(cfg: PipelineConfig):
    """
    Check everything the run depends on before spending a single call.

    Without this a missing PDF engine fails at the very last step, after
    five model calls and a model download have already been paid for.
    """
    problems = []
    if "PASTE" in cfg.YOUTUBE_API_KEY or not cfg.YOUTUBE_API_KEY:
        problems.append("YOUTUBE_API_KEY is not set in config.py")
    if not cfg.VIDEOS:
        problems.append("VIDEOS is empty in config.py")
    if not report.pdf_engine():
        problems.append(
            "no PDF engine found. Install one:\n"
            "        pip install playwright && playwright install chromium")

    if problems:
        raise SystemExit("Cannot start:\n  - " + "\n  - ".join(problems))

    llm.preflight(cfg)


def run_folder(cfg: PipelineConfig) -> str:
    """
    Named for what is being analysed, so sessions are distinguishable.
    Repeat runs of the same subject get -2, -3 rather than overwriting.
    """
    name = cfg.SESSION_NAME
    if not name:
        groups = list(dict.fromkeys(v.get("group", "") for v in cfg.VIDEOS
                                    if v.get("group")))
        name = "-".join(groups[:3])
        if len(groups) > 3:
            name += f"-and-{len(groups) - 3}-more"

    slug = re.sub(r"[\s_]+", "-",
                  re.sub(r"[^\w\s-]", "", name.lower())).strip("-")[:60]
    path = os.path.join(cfg.OUTPUT_DIR, slug or "run")
    counter = 2
    while os.path.exists(path):
        path = os.path.join(cfg.OUTPUT_DIR, f"{slug}-{counter}")
        counter += 1
    os.makedirs(path)
    return path


def main():
    cfg = _load_cfg()
    try:
        preflight(cfg)
        out_dir = run_folder(cfg)
        debug_dir = os.path.join(out_dir, "debug")
        if cfg.KEEP_INTERMEDIATE:
            os.makedirs(debug_dir, exist_ok=True)

        def save(name, content):
            if cfg.KEEP_INTERMEDIATE:
                with open(os.path.join(debug_dir, name), "w",
                          encoding="utf-8") as handle:
                    handle.write(content)

        print(f"Campaign: {out_dir}\n")

        print("[1/5] Collecting")
        comments, meta = collect.fetch(cfg)
        save("comments_raw.csv", comments.to_csv(index=False))
        comments = collect.clean(comments, cfg)
        base = comments[comments["in_base"]].reset_index(drop=True)
        print(f"    analysis base: {len(base)} comments")

        print("[2/5] Reading the videos")
    # CAMPAIGN_CONTEXT in config.py may be a dict (group->text) or a plain str.
        context_map = (cfg.CAMPAIGN_CONTEXT
                       if isinstance(cfg.CAMPAIGN_CONTEXT, dict)
                       else {})
        grounded, points = brief.run(meta, cfg, context_map=context_map)
        save("video_brief.md", grounded)
        save("points.json", json.dumps(points, ensure_ascii=False, indent=2))

        print("[3/5] Classifying the comments")
        summary = "; ".join(meta["title"].fillna("").astype(str).head(6))
        themes = analyze.build(base, summary, cfg)
        for theme in themes:
            print(f"    theme: {theme['name']}")

        n_comments = len(base)
        batch_size = cfg.CLASSIFY_BATCH_SIZE
        n_batches = sum(
            -(-len(g) // batch_size)
            for _, g in base.groupby("video_id"))
        print(f"    classifying {n_comments} comments in {n_batches} batches")
        base, columns = analyze.classify(
            base, themes, points, cfg,
            on_progress=lambda done, total: print(
                f"    classified batch {done} of {total}"))

        base, themes, other_share = analyze.extend(
            base, themes, points, summary, cfg,
            on_progress=lambda msg: print(msg))

        save("codebook.json", json.dumps(themes, ensure_ascii=False, indent=2))
        if cfg.KEEP_INTERMEDIATE:
            save("classified.csv", base.head(100).to_csv(index=False))

        print(f"    Other: {other_share:.0f}%")
        if other_share > 40:
            print("    WARNING: the theme set is not covering this conversation. "
                  "Turn on KEEP_INTERMEDIATE and read codebook.json before "
                  "trusting the report.")

        theme_table, transfer_table = analyze.summarise(base, columns)

        print("[4/5] Emotion and sentiment")
        base, affect_result = analyze.affect(base, cfg)

    # other_share is post-recalc from extend(); use it directly.
        if other_share > 40:
            print("    WARNING: Other still above 40% after Extend function.")

        print("[5/5] Writing")
        markdown = report.write(grounded, theme_table,
                                transfer_table, affect_result, base, cfg)
        report.render(markdown, out_dir, cfg,
                      debug_dir if cfg.KEEP_INTERMEDIATE else None,
                      _df=base, _transfer=transfer_table)
        report.export(base, theme_table, transfer_table, affect_result, meta,
                      out_dir)

        print(f"\nDone. {out_dir}")
        for name in ("report.pdf", "comments.csv", "key-messages.csv",
                     "themes.csv", "sentiment.csv", "emotions.csv"):
            print(f"  {name}")
        if cfg.KEEP_INTERMEDIATE:
            print("  debug/")
    finally:
        for model in (cfg.VISION_MODEL, cfg.TEXT_MODEL):
            try:
                llm.unload(model, cfg)
            except Exception:
                pass


if __name__ == "__main__":
    main()
