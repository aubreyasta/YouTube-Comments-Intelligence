"""One-off migration from the hand-labelled corpus to the join-keyed labels file.

Adds a comment_hash join key, and collapses five Key Message columns into
the four fixed Key Message columns.
"""

import hashlib
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
SRC = HERE / "labelled.csv"
DEST = HERE / "labels.csv"
COMMENTS = HERE / "comments.csv"

OUT_COLUMNS = [
    "video_id", "comment_hash", "theme", "sentiment", "emotion",
    "km_authentic_green_chili_flavor", "km_bolder_upgraded_taste",
    "km_real_green_chili", "km_jumbo_size_variant",
]

ALLOWED_THEMES = {
    "Comeback celebration", "Bring back other variants",
    "Not like the old recipe", "Spice level", "Where to buy it",
    "Jingle and the ad itself", "Personal memory", "Other",
}

SENTIMENTS = {"positive", "negative", "neutral"}
EMOTIONS = {"joy", "anger", "sadness", "fear", "other_neutral"}

# ponytail: these index lists are hand-assigned for one frozen corpus;
# a second corpus needs a real classify pass instead of positional indices.
# Rows judging whether it still tastes like cabe ijo.
AUTHENTIC_TRUE = [6, 7, 11, 69, 75, 79, 84, 130, 134, 135, 141, 148, 151, 153, 164, 175]

# Rows on the visible green chili / colour claim.
REAL_CHILI_TRUE = [141, 142, 144, 151, 171]

EXTRA_TRUE = {
    "km_authentic_green_chili_flavor": AUTHENTIC_TRUE,
    "km_real_green_chili": REAL_CHILI_TRUE,
}


def comment_hash(text: str) -> str:
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()[:12]


def _to_bool_series(df: pd.DataFrame, column: str) -> pd.Series:
    values = df[column]
    bad = values[~values.isin(["True", "False"])]
    if len(bad):
        raise SystemExit(f"column {column} has non-boolean value: {bad.iloc[0]!r}")
    return values == "True"


def main():
    src = pd.read_csv(SRC, dtype=str, encoding="utf-8", keep_default_na=False)

    if len(src) != 181:
        raise SystemExit(f"expected 181 rows in labelled.csv, found {len(src)}")

    required_km_columns = [
        "key_message_indomie_cabe_ijo_is_back",
        "key_message_now_spicier",
        "key_message_more_savory_and_bolder",
        "key_message_available_in_jumbo_size",
        "key_message_it_never_disappeared_just_got_scarce",
    ]
    for col in required_km_columns:
        if col not in src.columns:
            raise SystemExit(f"missing required column: {col}")

    bad_themes = sorted(set(src["theme"]) - ALLOWED_THEMES)
    if bad_themes:
        raise SystemExit(f"unknown values in column theme: {bad_themes}")

    bad_sentiments = sorted(set(src["sentiment"]) - SENTIMENTS)
    if bad_sentiments:
        raise SystemExit(f"unknown values in column sentiment: {bad_sentiments}")

    bad_emotions = sorted(set(src["emotion"]) - EMOTIONS)
    if bad_emotions:
        raise SystemExit(f"unknown values in column emotion: {bad_emotions}")

    now_spicier = _to_bool_series(src, "key_message_now_spicier")
    more_savory = _to_bool_series(src, "key_message_more_savory_and_bolder")
    jumbo = _to_bool_series(src, "key_message_available_in_jumbo_size")

    # key_message_indomie_cabe_ijo_is_back and
    # key_message_it_never_disappeared_just_got_scarce are deliberately
    # dropped: the comeback and scarcity claims are not among the four
    # fixed Key Messages and are carried by the themes instead.

    out = pd.DataFrame()
    out["video_id"] = src["video_id"]
    out["comment_hash"] = src["comment"].apply(comment_hash)
    out["theme"] = src["theme"]
    out["sentiment"] = src["sentiment"]
    out["emotion"] = src["emotion"]
    out["km_bolder_upgraded_taste"] = now_spicier | more_savory
    out["km_jumbo_size_variant"] = jumbo

    authentic = pd.Series(False, index=out.index)
    authentic.iloc[AUTHENTIC_TRUE] = True
    out["km_authentic_green_chili_flavor"] = authentic

    real_chili = pd.Series(False, index=out.index)
    real_chili.iloc[REAL_CHILI_TRUE] = True
    out["km_real_green_chili"] = real_chili

    km_columns = [
        "km_authentic_green_chili_flavor", "km_bolder_upgraded_taste",
        "km_real_green_chili", "km_jumbo_size_variant",
    ]
    for col in km_columns:
        out[col] = out[col].map({True: "True", False: "False"})

    out = out.reindex(columns=OUT_COLUMNS)

    comments = pd.read_csv(COMMENTS, encoding="utf-8", keep_default_na=False)
    comments_hashes = comments["comment"].apply(comment_hash)

    out_dupes = out["comment_hash"][out["comment_hash"].duplicated()].unique()
    if len(out_dupes):
        raise SystemExit(
            f"duplicate comment_hash in labelled.csv: {len(out_dupes)} "
            f"duplicates, sample: {list(out_dupes[:5])}"
        )

    comments_dupes = comments_hashes[comments_hashes.duplicated()].unique()
    if len(comments_dupes):
        raise SystemExit(
            f"duplicate comment_hash in comments.csv: {len(comments_dupes)} "
            f"duplicates, sample: {list(comments_dupes[:5])}"
        )

    if len(out) != len(comments):
        raise SystemExit(
            f"row count mismatch: labels.csv would have {len(out)} rows, "
            f"comments.csv has {len(comments)} rows"
        )

    out_hash_set = set(out["comment_hash"])
    comments_hash_set = set(comments_hashes)
    missing_in_comments = sorted(out_hash_set - comments_hash_set)
    missing_in_labels = sorted(comments_hash_set - out_hash_set)
    if missing_in_comments or missing_in_labels:
        raise SystemExit(
            f"comment_hash sets differ: {len(missing_in_comments)} hashes "
            f"only in labelled.csv (sample {missing_in_comments[:5]}), "
            f"{len(missing_in_labels)} hashes only in comments.csv "
            f"(sample {missing_in_labels[:5]})"
        )

    if list(out.columns) != OUT_COLUMNS:
        raise SystemExit(f"output columns do not match OUT_COLUMNS: {list(out.columns)}")
    if len(out) != 181:
        raise SystemExit(f"expected 181 output rows, found {len(out)}")

    out.to_csv(DEST, index=False, encoding="utf-8", lineterminator="\n")

    print(f"wrote {DEST.name}: {len(out)} rows")
    for col in km_columns:
        count = (out[col] == "True").sum()
        print(f"{col}: {count} True")
    theme_counts = out["theme"].value_counts()
    theme_counts = theme_counts.sort_values(ascending=False)
    for theme in sorted(theme_counts.index, key=lambda t: (-theme_counts[t], t)):
        print(f"{theme}: {theme_counts[theme]}")


if __name__ == "__main__":
    main()
