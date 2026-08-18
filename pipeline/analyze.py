"""
Turn comments into numbers.

The architectural point of this file: the LLM labels each comment against
a fixed theme set (discovered from a sample), and the code counts the
labels to produce percentages.

  build()     one model call reads a stratified sample and writes a
              theme book: themes + definitions. No keywords.
  classify()  LLM labels every comment; pt__ columns track Key Message mentions.
  extend()    if "Other" is too large, one top-up pass over that subset.
  summarise() count labels -> the two tables the report reasons over.
  affect()    count the sentiment/emotion labels classify() already wrote.
"""

import re
from collections.abc import Callable

import pandas as pd

from pipeline import llm
from pipeline.config_types import PipelineConfig

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
               "definition": "one sentence"}}]}}"""

TOPUP_PROMPT = """These comments matched no existing theme.
Videos: {summary}
Themes already defined: {existing}

COMMENTS:
{sample}

Identify 1 to 4 ADDITIONAL themes covering them. Do not duplicate an
existing theme. If they are genuinely noise with no shared subject, return
an empty list.

Return JSON: {{"themes": [{{"name": "...", "definition": "..."}}]}}"""

CLASSIFY_PROMPT = """Classify each comment below.

THEMES (pick exactly one name, or "Other" if none fit):
{themes}

BRIEF POINTS for this video (list any the comment echoes):
{points}

SENTIMENT (pick exactly one):
- positive: the comment expresses approval, liking, praise, or support.
- negative: the comment expresses disapproval, dislike, complaint, or attack.
- neutral: the comment is factual, mixed, or its polarity is unclear. Use
  neutral rather than guessing.

EMOTION (pick exactly one):
- joy: happiness, delight, amusement, excitement.
- anger: irritation, outrage, contempt.
- sadness: disappointment, grief, regret.
- fear: worry, anxiety, dread.
- other_neutral: no clear emotion, or an emotion outside the four above
  (surprise and disgust belong here).

COMMENTS:
{comments}

Return a JSON array, one entry per comment:
[{{"index": <int>, "theme": "<theme name or Other>",
   "echoed": [<point labels that appear in the comment>],
   "sentiment": "<positive|negative|neutral>",
   "emotion": "<joy|anger|sadness|fear|other_neutral>"}}]

Every index must appear. Use "Other" when no theme fits.
Valid theme names: {theme_names}
Valid point labels: {point_labels}"""


def _bounded_sample_prompt(template, *, sample, **values):
    prompt = template.format(sample=sample, **values)
    if len(prompt) <= 80000:
        return prompt
    fixed = template.format(sample="", **values)
    return template.format(sample=sample[:max(0, 80000 - len(fixed))],
                           **values)


# ------------------------------------------------------------- sampling

def _sample(df, size):
    """
    Draw four strata per video: most-liked, most-replied, longest, random
    tail. The sample is used only to DISCOVER themes; percentages are
    counted over the full corpus afterwards. The sample must contain at
    least one example of everything worth naming - not be proportionally
    representative.
    """
    videos = df["video_id"].unique()
    per_video = max(30, size // max(len(videos), 1))
    picks = []

    for vid in videos:
        sub = df[df["video_id"] == vid]
        take = min(per_video, len(sub))
        stratum = max(1, take // 4)

        liked = sub.nlargest(stratum, "likes")
        rest = sub.drop(liked.index)

        replied = (rest.nlargest(stratum, "reply_count")
                   if "reply_count" in rest.columns
                   else pd.DataFrame(columns=rest.columns))
        rest = rest.drop(replied.index, errors="ignore")

        longest = (rest.assign(_len=rest["comment"].str.len())
                       .nlargest(stratum, "_len").drop(columns="_len"))
        rest = rest.drop(longest.index, errors="ignore")

        tail_n = min(take - stratum * 3, len(rest))
        tail = rest.sample(max(0, tail_n), random_state=0)

        picks += [liked, replied, longest, tail]

    return pd.concat(picks).drop_duplicates(subset=["comment"])


# ------------------------------------------------------------- theme book

def build(df, summary, cfg: "PipelineConfig"):
    """One model call. Returns the theme list (name + definition only)."""
    target = max(cfg.CODEBOOK_SAMPLE_SIZE, int(len(df) * 0.08))
    size = int(min(target, cfg.CODEBOOK_SAMPLE_MAX, len(df)))
    # Cap at 800 per spec (500-800 target on Pro tier).
    size = min(size, 800)
    sample = _sample(df, size)
    print(f"    theme discovery sample: {len(sample)} of {len(df)}, "
          f"stratified by video and engagement")

    result = llm.ask_json(_bounded_sample_prompt(
        CODEBOOK_PROMPT, n=len(sample), summary=summary,
        sample="\n".join(f"- {c[:220]}" for c in sample["comment"])),
        cfg, schema=llm.THEME_DISCOVERY_SCHEMA,
        validation=llm.validate_theme_discovery, num_predict=2048)
    return result.get("themes", [])


# ------------------------------------------------------------- classification

def classify(df, themes, points, cfg: "PipelineConfig" = None,
             on_progress: Callable[[int, int], None] | None = None):
    """
    LLM labels every comment with a theme, mentioned Key Messages, a
    sentiment, and an emotion.

    Returns (df_with_new_cols, columns) where columns maps
    pt__slug -> original label string.

    cfg is required for production use; accepts None only so the existing
    test_classify.py stub (which calls the 3-arg form) keeps working.
    """
    df = df.copy()
    df["theme"] = "Other"
    df["sentiment"] = pd.NA
    df["emotion"] = pd.NA

    theme_names = [t["name"] for t in themes]

    # Build pt__ columns: one per unique label across all points.
    # Collision-safe: two labels that truncate to the same 40-char slug
    # get a numeric suffix so they never silently merge.
    columns = {}         # pt__col -> original label
    label_to_col = {}    # original label -> pt__col (for write-back)
    for point in points:
        base = "pt__" + re.sub(r"\W+", "_", point["label"].lower())[:40]
        col = base
        i = 2
        while col in columns:
            col = f"{base}_{i}"
            i += 1
        columns[col] = point["label"]
        # pd.NA sentinel: rows whose video is NOT associated with this point
        # stay NA so summarise can skip non-associated (group, column) pairs
        # without a separate points lookup. Associated rows are set to False
        # (then True if echoed) in the per-video loop below.
        df[col] = pd.NA
        label_to_col[point["label"]] = col

    # Group points by video so each batch only sees its own video's points.
    # A point with video_id None is a Session-level Key Message: it was
    # never scoped to one video, so it is broadcast into every video's
    # batch rather than excluded from all but one.
    all_video_ids = df["video_id"].unique().tolist()
    points_by_video = {}
    for point in points:
        vid = point.get("video_id")
        targets = all_video_ids if vid is None else [vid]
        for t in targets:
            points_by_video.setdefault(t, []).append(point)

    batch_size = cfg.CLASSIFY_BATCH_SIZE if cfg is not None else 25

    themes_text = "\n".join(
        f"- {t['name']}: {t['definition']}" for t in themes)

    batches = []
    for vid, group_df in df.groupby("video_id", sort=False):
        vid_points = points_by_video.get(vid, [])
        point_labels = [p["label"] for p in vid_points]
        points_text = "\n".join(
            f"- {p['label']}: {p['description']}" for p in vid_points
        ) if vid_points else "(none)"
        indices = group_df.index.tolist()
        start = 0
        while start < len(indices):
            end = min(start + batch_size, len(indices))
            while True:
                chunk_idx = indices[start:end]
                comments_text = "\n".join(
                    f"{i}: {str(df.at[i, 'comment'])[:300]}" for i in chunk_idx)
                prompt = CLASSIFY_PROMPT.format(
                    themes=themes_text, points=points_text,
                    comments=comments_text,
                    theme_names=", ".join(
                        f'"{n}"' for n in theme_names + ["Other"]),
                    point_labels=", ".join(
                        f'"{label}"' for label in point_labels) or "(none)",
                )
                if len(prompt) <= 80000 or len(chunk_idx) == 1:
                    break
                end = start + max(1, len(chunk_idx) // 2)
            if len(prompt) > 80000:
                raise ValueError("A single comment classification prompt exceeds 80000 characters")
            batches.append((vid, group_df.index, chunk_idx, prompt))
            start = end

    total_batches = len(batches)
    completed_batches = 0
    activated_videos = set()

    for vid, group_index, chunk_idx, prompt in batches:
        vid_points = points_by_video.get(vid, [])
        point_labels = [p["label"] for p in vid_points]

        # Activate (False) only the columns that belong to this video.
        # Rows for other videos stay NA and are excluded from summarise.
        if vid not in activated_videos:
            for vp in vid_points:
                col = label_to_col[vp["label"]]
                df.loc[group_index, col] = False
            activated_videos.add(vid)

        results = llm.classify_batch(
            prompt, chunk_idx, theme_names, point_labels, cfg)

        # llm.classify_batch already enforces exact index coverage, theme
        # membership, and Key Message subset via validate_classification
        # before it returns - these checks are not needed against the real
        # boundary. They stay here because tests replace llm.classify_batch
        # wholesale (bypassing that validation) to exercise the atomic-apply
        # contract of this function specifically; removing them would make
        # analyze.classify() trust unvalidated input from any future caller
        # that stubs classify_batch, which is a correctness regression, not
        # just a test break.
        expected = set(chunk_idx)
        returned = [row.get("index") for row in results]
        if len(returned) != len(expected) or set(returned) != expected:
            raise ValueError("Classification batch did not return every index exactly once")
        if any(row.get("theme") not in theme_names + ["Other"]
               or not isinstance(row.get("echoed"), list)
               or not set(row["echoed"]).issubset(point_labels)
               or row.get("sentiment") not in llm.SENTIMENT_LABELS
               or row.get("emotion") not in llm.EMOTION_LABELS
               for row in results):
            raise ValueError("Classification batch returned invalid labels")

        # Apply only after the complete batch has passed every check.
        for row in results:
            idx = row["index"]
            df.at[idx, "theme"] = row["theme"]
            df.at[idx, "sentiment"] = row["sentiment"]
            df.at[idx, "emotion"] = row["emotion"]
            for label in row["echoed"]:
                df.at[idx, label_to_col[label]] = True

        completed_batches += 1
        if on_progress:
            on_progress(completed_batches, total_batches)

    return df, columns


def extend(df, themes, points, summary, cfg: "PipelineConfig",
           on_progress=None):
    """
    Second pass only if too much fell through. Reclassifies only the
    Other subset against the extended theme set.

    Returns (df, themes, other_share) where other_share is the POST-recalc
    share so callers always see the current state, not the stale pre-topup
    value.
    """
    leftover = df[df["theme"] == "Other"]
    leftover_pct = len(leftover) / len(df) * 100
    if leftover_pct < cfg.UNCLASSIFIED_LIMIT or len(leftover) < 25:
        return df, themes, leftover_pct

    if on_progress:
        on_progress("Refining themes - high uncategorised count")

    print(f"    {leftover_pct:.0f}% Other, one top-up pass")
    sample = _sample(leftover, min(cfg.CODEBOOK_SAMPLE_MAX,
                                   max(60, len(leftover) // 4)))
    result = llm.ask_json(_bounded_sample_prompt(
        TOPUP_PROMPT, summary=summary,
        existing=", ".join(t["name"] for t in themes),
        sample="\n".join(f"- {c[:220]}" for c in sample["comment"])),
        cfg, schema=llm.EXTEND_THEME_SCHEMA,
        validation=llm.validate_extend_themes, num_predict=1536)
    new_themes = result.get("themes", [])
    if not new_themes:
        other_share = float((df["theme"] == "Other").mean() * 100)
        return df, themes, other_share

    extended = themes + new_themes[:4]
    # Reclassify only the Other rows against the extended set.
    sub_df, _ = classify(leftover, extended, points, cfg)
    df = df.copy()
    # Copy back theme and pt__ only. The top-up pass reclassifies one subset,
    # so taking its sentiment/emotion would leave the corpus with affect
    # labels from two different prompts. First-pass affect stands.
    df.loc[leftover.index, "theme"] = sub_df["theme"]
    # Update pt__ columns from the reclassified subset too.
    for col in [c for c in df.columns if c.startswith("pt__")]:
        if col in sub_df.columns:
            df.loc[leftover.index, col] = sub_df[col]

    # Return the POST-recalc Other share, not the stale pre-topup value.
    other_share = float((df["theme"] == "Other").mean() * 100)
    return df, extended, other_share


# -------------------------------------------------------------- signals

def summarise(df, columns):
    """The two tables the report reasons over."""
    themes = (pd.crosstab(df["group"], df["theme"], normalize="index")
              .mul(100).round(1))

    rows = []
    for column, label in columns.items():
        for group, sub in df.groupby("group"):
            # Skip groups whose rows are all NA for this column - those
            # groups were never shown this brief point, so a zero row
            # would be spurious. Groups that were shown it but got 0
            # echoes produce n=0, echoed_pct=0.0 (the meaningful zero case).
            active = sub[column].dropna()
            if active.empty:
                continue
            hits = int(active.sum())
            rows.append({"group": group, "point": label,
                         "echoed_pct": round(active.mean() * 100, 1),
                         "n": hits})
    return themes, pd.DataFrame(rows)


# -------------------------------------------------------------- affect

def affect(df, cfg: "PipelineConfig"):
    """
    Count the sentiment and emotion labels the classification pass already
    wrote. No model runs here: the labels arrive with the Theme and Key
    Message labels from one Ollama request per batch.

    Returns:
        (df, {"emotion": {"table": ..., "caveat": ...},
              "sentiment": {"table": ..., "caveat": ...}})

    cfg is accepted because PipelineConfig is the pipeline contract, even
    though this function reads nothing from it.
    """
    caveat = ("Labels were assigned per comment by the local Qwen "
              "classification pass.")
    allowed = {"sentiment": set(llm.SENTIMENT_LABELS),
               "emotion": set(llm.EMOTION_LABELS)}

    df = df.copy()
    result = {}

    for column in ("emotion", "sentiment"):
        if column not in df.columns:
            raise ValueError(
                f"affect() requires the '{column}' column written by classify()")
        values = df[column].dropna()
        unknown = set(values) - allowed[column]
        if unknown:
            raise ValueError(
                f"affect() found {column} values outside the locked set: "
                f"{sorted(unknown)}")

        table = (pd.crosstab(df["group"], df[column], normalize="index")
                 .mul(100).round(1))
        result[column] = {"table": table, "caveat": caveat}

    return df, result
