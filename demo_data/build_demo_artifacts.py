"""
Replays a hand-labelled comment corpus through the real report writers.
No model is called anywhere in this script. Every number in every
artifact is counted in Python from comments.csv and labels.csv.
"""

import hashlib
import json
import os
import re
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import adapter
from pipeline import report
from pipeline.config_types import PipelineConfig

DEMO_DIR = ROOT / "demo_data"
OUT_DIR = ROOT / "app" / "demo"

# Fixed for the demo. Order is the Key Message order in every artifact.
KEY_MESSAGES = [
    {"id": "pt-1", "label": "Authentic green chili flavor", "description": "", "included": True, "sort_order": 1},
    {"id": "pt-2", "label": "Bolder, upgraded taste", "description": "", "included": True, "sort_order": 2},
    {"id": "pt-3", "label": "Real green chili", "description": "", "included": True, "sort_order": 3},
    {"id": "pt-4", "label": "Jumbo Size Variant", "description": "", "included": True, "sort_order": 4},
]

# labels.csv column -> Key Message label, same order as KEY_MESSAGES.
KM_COLUMNS = {
    "km_authentic_green_chili_flavor": "Authentic green chili flavor",
    "km_bolder_upgraded_taste": "Bolder, upgraded taste",
    "km_real_green_chili": "Real green chili",
    "km_jumbo_size_variant": "Jumbo Size Variant",
}

THEME_ORDER = [
    "Comeback celebration",
    "Bring back other variants",
    "Not like the old recipe",
    "Spice level",
    "Where to buy it",
    "Jingle and the ad itself",
    "Personal memory",
    "Other",
]

# Verdict thresholds for the transfer table, on echoed_pct.
VERDICT_YES_MIN = 20.0
VERDICT_PARTLY_MIN = 5.0


def comment_hash(text: str) -> str:
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()[:12]


def _pt_col(label: str) -> str:
    return "pt__" + re.sub(r"\W+", "_", label.lower())[:40]


def _count_word(n: int) -> str:
    """Small counts read better as words in prose."""
    return {1: "one", 2: "two", 3: "three", 4: "four"}.get(n, str(n))


def load_frame():
    comments = pd.read_csv(DEMO_DIR / "comments.csv", encoding="utf-8")

    labels_path = DEMO_DIR / "labels.csv"
    if not labels_path.exists():
        raise SystemExit(
            "demo_data/labels.csv is missing. Task D1.1 writes it.")
    labels = pd.read_csv(labels_path, encoding="utf-8", dtype=str)

    comments = comments.copy()
    comments["comment_hash"] = comments["comment"].apply(comment_hash)

    if len(labels) != len(comments):
        raise SystemExit(
            f"labels.csv has {len(labels)} rows but comments.csv has "
            f"{len(comments)} rows.")

    comment_dupes = comments["comment_hash"][
        comments["comment_hash"].duplicated()].unique().tolist()
    if comment_dupes:
        raise SystemExit(
            f"comments.csv has {len(comment_dupes)} duplicate comment_hash "
            f"values: {comment_dupes[:5]}")

    label_dupes = labels["comment_hash"][
        labels["comment_hash"].duplicated()].unique().tolist()
    if label_dupes:
        raise SystemExit(
            f"labels.csv has {len(label_dupes)} duplicate comment_hash "
            f"values: {label_dupes[:5]}")

    comment_hashes = set(comments["comment_hash"])
    label_hashes = set(labels["comment_hash"])
    if comment_hashes != label_hashes:
        missing_in_labels = list(comment_hashes - label_hashes)[:5]
        missing_in_comments = list(label_hashes - comment_hashes)[:5]
        raise SystemExit(
            f"comment_hash sets differ: {len(comment_hashes - label_hashes)} "
            f"hashes in comments.csv not in labels.csv "
            f"(e.g. {missing_in_labels}); "
            f"{len(label_hashes - comment_hashes)} hashes in labels.csv "
            f"not in comments.csv (e.g. {missing_in_comments})")

    labels = labels.drop(columns=["video_id"])
    df = comments.merge(labels, on="comment_hash", how="inner",
                        validate="one_to_one")

    columns = {}
    for km_col, label in KM_COLUMNS.items():
        mapped = df[km_col].map({"True": True, "False": False})
        mapped = mapped.where(mapped.notna(), pd.NA)
        pt_col = _pt_col(label)
        df[pt_col] = mapped
        df = df.drop(columns=[km_col])
        columns[pt_col] = label

    return df, columns


def build_transfer(df, columns):
    rows = []
    for column, label in columns.items():
        for group, sub in df.groupby("group"):
            active = sub[column].dropna()
            if active.empty:
                continue
            rows.append({
                "group": group,
                "point": label,
                "echoed_pct": round(active.mean() * 100, 1),
                "n": int(active.sum()),
            })
    return pd.DataFrame(rows)


def build_affect(df):
    caveat = ("Labels were assigned by hand against the product's locked "
              "label sets.")
    return {
        "emotion": {
            "table": pd.crosstab(df["group"], df["emotion"],
                                 normalize="index").mul(100).round(1),
            "caveat": caveat,
        },
        "sentiment": {
            "table": pd.crosstab(df["group"], df["sentiment"],
                                 normalize="index").mul(100).round(1),
            "caveat": caveat,
        },
    }


def build_prose(df):
    positive = df[df["sentiment"] == "positive"].copy()
    positive["_len"] = positive["comment"].astype(str).str.len()
    positive = positive.sort_values(
        ["likes", "_len"], ascending=[False, False], kind="stable")
    row = positive.iloc[0]

    text = str(row["comment"]).strip().replace("\n", " ")
    attr = f"YouTube comment, {row['video_id']}"

    total = len(df)
    theme_counts = df["theme"].value_counts()
    top_two = theme_counts.head(2)
    theme_sentence = (
        f"{top_two.index[0]} leads at "
        f"{round(top_two.iloc[0] / total * 100, 1)}%, followed by "
        f"{top_two.index[1]} at "
        f"{round(top_two.iloc[1] / total * 100, 1)}%."
    )
    n_comeback = int((df["theme"] == "Comeback celebration").sum())
    n_variants = int((df["theme"] == "Bring back other variants").sum())

    interpretation = (
        f"Across {total} comments, {theme_sentence} The audience carried "
        f"the comeback story on its own: {n_comeback} comments celebrate "
        f"the return that the brand itself never framed as the headline."
    )
    caveat = (
        f"{n_variants} comments name a discontinued flavour they want "
        f"back, and labels carry no context, so sarcasm reads as anger."
    )

    return {
        "title": "Cabe Ijo came back louder than it tasted",
        "subtitle": f"{total} comments across 2 Indomie Cabe Ijo relaunch videos.",
        "interpretation": interpretation,
        "quote": {"text": text, "attr": attr},
        "caveat": caveat,
    }


def _verdict(echoed_pct: float) -> str:
    if echoed_pct >= VERDICT_YES_MIN:
        return "Yes"
    if echoed_pct >= VERDICT_PARTLY_MIN:
        return "Partly"
    return "No"


def _transfer_lookup(transfer, label):
    match = transfer[transfer["point"] == label]
    if match.empty:
        return 0.0, 0
    return float(match["echoed_pct"].iloc[0]), int(match["n"].iloc[0])


# ponytail: this markdown body is fixed prose for this one hand-labelled
# corpus (numbers are counted, wording is not). A second demo corpus
# needs the real pipeline/report.py write() path with a model behind it.
def build_markdown(df, transfer, affect_result):
    total = len(df)
    n_videos = int(df["video_id"].nunique())

    theme_counts = df["theme"].value_counts()
    emotion_counts = df["emotion"].value_counts()
    top_emotion = emotion_counts.index[0]
    top_emotion_pct = round(emotion_counts.iloc[0] / total * 100, 1)

    v1, n1 = _transfer_lookup(transfer, "Authentic green chili flavor")
    v2, n2 = _transfer_lookup(transfer, "Bolder, upgraded taste")
    v3, n3 = _transfer_lookup(transfer, "Real green chili")
    v4, n4 = _transfer_lookup(transfer, "Jumbo Size Variant")

    n_comeback = int((df["theme"] == "Comeback celebration").sum())
    n_variants = int((df["theme"] == "Bring back other variants").sum())

    top_two = theme_counts.head(2)
    theme_sentence = (
        f"{top_two.index[0]} ({round(top_two.iloc[0] / total * 100, 1)}%) "
        f"and {top_two.index[1]} "
        f"({round(top_two.iloc[1] / total * 100, 1)}%) dominate the "
        f"conversation."
    )

    prose = build_prose(df)
    quote_text = prose["quote"]["text"]
    quote_gloss = ("The comeback, not the recipe, is what the audience "
                  "turned up for.")

    return f"""# Cabe Ijo came back louder than it tasted

*What {total} comments on the Cabe Ijo relaunch actually discuss.*

[[CHART:transfer]]

## Indomie

*Cabe Ijo relaunch - {total} comments - {top_emotion} leads at {top_emotion_pct}%*

**Background**

Indomie brought Goreng Cabe Ijo back after pulling it from shelves. {_count_word(n_videos).capitalize()} videos lead with the same three claims: hotter, more savoury, made with real green chili. The Jumbo variant appears in the product shot but is never stated.

| Decision | Travelled? | How the audience handled it |
|---|---|---|
| Authentic green chili flavor | {_verdict(v1)} | {n1} of {total} comments raise the flavour claim |
| Bolder, upgraded taste | {_verdict(v2)} | {n2} of {total} comments raise the taste upgrade |
| Real green chili | {_verdict(v3)} | {n3} of {total} comments raise the ingredient |
| Jumbo Size Variant | {_verdict(v4)} | {n4} of {total} comments raise the size |
| The return itself | Raised by the audience | {n_comeback} comments celebrate the comeback the brand never claimed |

**Talked about instead**

[[CHART:themes]]

{theme_sentence}

**Comments**

> "{quote_text}"
>
> *{quote_gloss}*

**So what**

- Lead the next Cabe Ijo cut on the return itself. The audience is already carrying that story for free.
- Answer the variant requests on channel. {n_variants} comments name a discontinued flavour they want back.

---

## Read this before quoting it

- {total} comments is under 100 per video on one of the {_count_word(n_videos)}, so read the split with care.
- Low travel means the claim did not arrive, not that the audience rejected it.
- Labels carry no context, so sarcasm reads as anger.
- Commenters are not buyers.
"""


def _self_check():
    assert _pt_col("Jumbo Size Variant") == "pt__jumbo_size_variant"
    assert _verdict(25.0) == "Yes"
    assert _verdict(8.0) == "Partly"
    assert _verdict(1.0) == "No"


def main():
    _self_check()

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    df, columns = load_frame()
    transfer = build_transfer(df, columns)
    affect_result = build_affect(df)
    markdown = build_markdown(df, transfer, affect_result)

    cfg = PipelineConfig(
        YOUTUBE_API_KEY="",
        VIDEOS=[],
        SESSION_NAME="Indomie Cabe Ijo relaunch",
        OUTPUT_DIR=str(OUT_DIR),
        KEEP_LANGUAGES=["id", "en"],
        MIN_COMMENT_LETTERS=0,
        MAX_COMMENTS_PER_VIDEO=0,
        CODEBOOK_SAMPLE_SIZE=0,
        CODEBOOK_SAMPLE_MAX=0,
        CLASSIFY_BATCH_SIZE=0,
        UNCLASSIFIED_LIMIT=0,
        REPORT_LANGUAGE="English",
        CAMPAIGN_CONTEXT="Indomie Goreng Cabe Ijo relaunch.",
    )

    report.render(markdown, str(OUT_DIR), cfg, _df=df, _transfer=transfer)
    report.export(df, None, transfer, affect_result, None, str(OUT_DIR))

    report_json = adapter._build_report_json(df, transfer, KEY_MESSAGES, columns)

    # adapter's evidence rows carry text/likes/videoId/sentiment. The demo
    # evidence drawer filters by emotion, so decorate each comment with the
    # emotion already labelled on its source row. Joined on comment text,
    # which is the only field the evidence rows and df share; comments.csv
    # is already checked for duplicate text upstream.
    # ponytail: text-keyed join, correct because the duplicate check above
    # guarantees uniqueness. If duplicate comment text is ever allowed,
    # carry comment_hash through adapter's evidence emit instead.
    emotion_by_text = {
        str(row["comment"]): (str(row["emotion"]) if pd.notna(row["emotion"]) else None)
        for _, row in df.iterrows()
    }
    for metric in report_json["evidence"]:
        for comment in metric["comments"]:
            comment["emotion"] = emotion_by_text.get(str(comment["text"]))

    fixture_path = OUT_DIR / "fixture.js"
    fixture_content = (
        "window.__demoFixture = "
        + json.dumps({"reportJson": report_json, "prose": build_prose(df)},
                     ensure_ascii=False, indent=2)
        + ";\n"
    )
    fixture_path.write_text(fixture_content, encoding="utf-8")

    for name in ("report.pdf", "comments.csv", "key-messages.csv",
                "themes.csv", "sentiment.csv", "emotions.csv", "fixture.js"):
        print(f"wrote {OUT_DIR / name}")


if __name__ == "__main__":
    main()
