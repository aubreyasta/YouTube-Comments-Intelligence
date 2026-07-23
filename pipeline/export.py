"""
Stage 8 - build the two CSVs a human or a slide tool actually uses.

Everything else the pipeline computes lives in memory and is passed
directly to the next stage. These two files, plus the report, are the
deliverables. Intermediate artifacts are written only when
KEEP_INTERMEDIATE is on, into output/debug/, for auditing.

    comments.csv   one row per comment, fully labelled. For a human who
                   wants to dig through and handpick.

    summary.csv    every number the report cites, in tidy long format,
                   so it pivots cleanly and drops straight into Sheets
                   or Slides to build charts.
"""

import os
import pandas as pd

# Columns worth keeping in the human-facing comments file, in order.
# Anything not listed is dropped: internal flags and scratch columns
# make the file harder to read without adding anything.
COMMENT_COLUMNS = [
    "group", "kind", "video_id", "comment", "theme", "emotion",
    "emotion_confidence", "likes", "is_reply", "lang", "n_words",
    "is_question", "mentions_price", "mentions_competitor",
    "published_at",
]


def comments_csv(df, path):
    """One row per comment, human-readable, sorted for browsing."""
    out = df.copy()

    # Signal columns are prefixed pt__. Collapse them into one readable
    # column listing which of the video's ideas that comment echoed,
    # rather than leaving a dozen sparse boolean columns.
    signal_cols = [c for c in out.columns if c.startswith("pt__")]
    if signal_cols:
        def echoed(row):
            hits = [c[4:].replace("_", " ") for c in signal_cols if row[c]]
            return "; ".join(hits)
        out["echoed_ideas"] = out.apply(echoed, axis=1)

    keep = [c for c in COMMENT_COLUMNS if c in out.columns]
    if "echoed_ideas" in out.columns:
        keep.insert(keep.index("theme") + 1, "echoed_ideas")

    out = out[keep].sort_values(
        ["group", "likes"], ascending=[True, False])
    out.to_csv(path, index=False, encoding="utf-8-sig")
    print(f"    {os.path.basename(path)}: {len(out)} comments, "
          f"{len(keep)} columns")
    return out


def summary_csv(theme_table, transfer_table, emotion_result, df, meta_df,
                path):
    """
    Tidy long format: one row per number.

        group | metric | label | value | unit | n

    Long rather than wide because it pivots without reshaping, which is
    what a slide tool or a pivot table wants. Filter on `metric` to get
    the data for one chart.
    """
    rows = []

    # --- base sizes
    for group, sub in df.groupby("group"):
        rows.append({"group": group, "metric": "base",
                     "label": "comments analysed", "value": len(sub),
                     "unit": "count", "n": len(sub)})

    # --- videos per group
    counts = df.groupby("video_id").size().to_dict()
    for _, row in meta_df.iterrows():
        rows.append({
            "group": row["group"], "metric": "video",
            "label": f"https://www.youtube.com/watch?v={row['video_id']}",
            "value": counts.get(row["video_id"], 0), "unit": "count",
            "n": counts.get(row["video_id"], 0)})

    # --- theme mix
    if theme_table is not None and not theme_table.empty:
        sizes = df.groupby("group").size().to_dict()
        for group in theme_table.index:
            for theme in theme_table.columns:
                pct = float(theme_table.loc[group, theme])
                rows.append({
                    "group": group, "metric": "theme", "label": theme,
                    "value": round(pct, 1), "unit": "percent",
                    "n": int(round(pct / 100 * sizes.get(group, 0)))})

    # --- signal transfer
    if transfer_table is not None and not transfer_table.empty:
        for row in transfer_table.itertuples():
            rows.append({"group": row.group, "metric": "signal_transfer",
                         "label": row.point, "value": row.echoed_pct,
                         "unit": "percent", "n": row.n})

    # --- emotion
    if emotion_result:
        table = emotion_result["table"]
        sizes = df.groupby("group").size().to_dict()
        for group in table.index:
            for label in table.columns:
                pct = float(table.loc[group, label])
                rows.append({
                    "group": group, "metric": "emotion", "label": label,
                    "value": round(pct, 1), "unit": "percent",
                    "n": int(round(pct / 100 * sizes.get(group, 0)))})

    out = pd.DataFrame(rows, columns=["group", "metric", "label", "value",
                                      "unit", "n"])
    out.to_csv(path, index=False, encoding="utf-8-sig")

    breakdown = out["metric"].value_counts().to_dict()
    print(f"    {os.path.basename(path)}: {len(out)} rows  {breakdown}")
    return out


def run(df, theme_table, transfer_table, emotion_result, meta_df,
        output_dir):
    comments_path = os.path.join(output_dir, "comments.csv")
    summary_path = os.path.join(output_dir, "summary.csv")
    comments_csv(df, comments_path)
    summary_csv(theme_table, transfer_table, emotion_result, df, meta_df,
                summary_path)
    return comments_path, summary_path