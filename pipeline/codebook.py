"""
Stage 4 - build a codebook, then apply it to every comment.

Two halves:
  A) The model reads a SAMPLE of comments and writes the rules:
     which themes exist in this particular conversation, and what
     words indicate each one. One cheap call.

  B) Python applies those rules to ALL comments with plain regex.
     Zero further token cost, and every classification is auditable
     because the rules are written to disk.

So the model does the judgement, the code does the counting.

On sampling: the sample is only used to DISCOVER themes and vocabulary.
Every percentage in the output is counted over the full corpus, so the
sample never has to be proportionally accurate. It only has to contain
at least one example of each theme worth naming. That is a much easier
job, which is why a few hundred comments is enough even for a corpus of
twenty thousand.

The sample is drawn stratified rather than purely at random, because a
flat random draw over-represents whichever group is largest and misses
the high-engagement comments that carry disproportionate weight.
"""

import re
import pandas as pd

from pipeline import llm
import config as config

CODEBOOK_PROMPT = """Below is a random sample of {n} comments from a YouTube
video's comment section. The video is: {summary}

Read the comments and identify the 5 to 8 THEMES that actually appear.
A theme is what a comment is ABOUT. Base them on what you see, not on
what you would expect. Include mundane ones (off-topic banter, generic
praise, questions) if they are present, because they matter for working
out how much of the thread is substantive.

COMMENTS:
{sample}

Return JSON:
{{
  "themes": [
    {{
      "name": "short theme name",
      "definition": "one sentence",
      "keywords": ["10-20 lowercase words or short phrases in the language(s) the comments use"]
    }}
  ]
}}

Order themes from most specific to most generic, because the first
match wins when they are applied. Put catch-all themes like generic
praise or off-topic last."""

TOPUP_PROMPT = """These comments were not captured by any existing theme.
The video is: {summary}
Themes already defined: {existing}

COMMENTS:
{sample}

Identify 1 to 4 ADDITIONAL themes that cover what these comments are
about. Do not duplicate an existing theme. If the comments are genuinely
just noise with no shared subject, return an empty list.

Return JSON: {{"themes": [{{"name": "...", "definition": "...", "keywords": ["..."]}}]}}"""


def sample_size_for(n_comments):
    """
    Scale the sample with the corpus, within sane bounds.

    Discovering themes needs coverage, not proportion. A theme present in
    5% of comments turns up in a 150-comment draw virtually every time;
    one present in 1% needs a few hundred. Above roughly 500 the returns
    flatten, so we cap there to keep the call cheap.
    """
    target = max(config.CODEBOOK_SAMPLE_SIZE, int(n_comments * 0.08))
    return int(min(target, config.CODEBOOK_SAMPLE_MAX, n_comments))


def stratified_sample(df, size):
    """
    Draw across groups and across engagement levels.

    Three slices per group, because they contain different language:
      - most-liked comments (the ones the thread rallied around)
      - longest comments (where the reasoning lives)
      - a random draw (everything else)
    """
    picks = []
    groups = df["group"].unique()
    per_group = max(30, size // max(len(groups), 1))

    for group in groups:
        sub = df[df["group"] == group]
        take = min(per_group, len(sub))
        n_top = take // 4
        n_long = take // 4
        n_rand = take - n_top - n_long

        top = sub.nlargest(n_top, "likes")
        rest = sub.drop(top.index)
        longest = (rest.assign(_len=rest["comment"].str.len())
                       .nlargest(n_long, "_len")
                       .drop(columns=["_len"]))
        rest = rest.drop(longest.index)
        rand = rest.sample(min(n_rand, len(rest)), random_state=0)

        picks.extend([top, longest, rand])

    return pd.concat(picks).drop_duplicates(subset=["comment"])


def build(df, video_summary):
    """Ask the model to write the theme rules for this conversation."""
    size = sample_size_for(len(df))
    sample = stratified_sample(df, size)
    print(f"    codebook sample: {len(sample)} of {len(df)} comments "
          f"(stratified by group and engagement)")

    numbered = "\n".join(f"- {c[:220]}" for c in sample["comment"])
    result = llm.ask_json(CODEBOOK_PROMPT.format(
        n=len(sample), summary=video_summary, sample=numbered))
    return result.get("themes", [])


def extend(df, themes, video_summary):
    """
    Second pass, only if the first codebook left too much unclassified.

    A high Unclassified share means the sample missed vocabulary that
    the rest of the corpus uses. Rather than guess, show the model the
    comments that fell through and let it add themes.
    """
    leftover = df[df["theme"] == "Unclassified"]
    share = len(leftover) / len(df) * 100
    if share < config.UNCLASSIFIED_LIMIT or len(leftover) < 25:
        return themes, share

    print(f"    {share:.0f}% unclassified, running one top-up pass")
    sample = stratified_sample(leftover, sample_size_for(len(leftover)))
    existing = ", ".join(t["name"] for t in themes)
    result = llm.ask_json(TOPUP_PROMPT.format(
        summary=video_summary, existing=existing,
        sample="\n".join(f"- {c[:220]}" for c in sample["comment"])))
    return themes + result.get("themes", []), share


def _safe_pattern(keywords):
    """Turn a keyword list into one regex, escaping anything odd."""
    parts = [re.escape(k.strip().lower()) for k in keywords if k.strip()]
    return "|".join(parts) if parts else r"(?!x)x"   # matches nothing


def apply_themes(df, themes):
    """First match wins, same as before."""
    compiled = [(t["name"], re.compile(_safe_pattern(t.get("keywords", [])),
                                       re.IGNORECASE)) for t in themes]

    def theme_of(text):
        for name, pattern in compiled:
            if pattern.search(text):
                return name
        return "Unclassified"

    df["theme"] = df["comment"].apply(theme_of)
    return df


def apply_points(df, points):
    """
    Signal transfer: for each thing the VIDEO pushed, did it show up
    in the comments? These overlap on purpose, so they are boolean
    columns rather than one label.
    """
    seen = {}
    for point in points:
        column = "pt__" + re.sub(r"\W+", "_", point["label"].lower())[:40]
        if column in seen:
            continue
        seen[column] = point["label"]
        pattern = re.compile(_safe_pattern(point.get("keywords", [])),
                             re.IGNORECASE)
        # Only measure a point against the video it came from.
        mask = df["video_id"] == point["video_id"]
        df[column] = False
        df.loc[mask, column] = df.loc[mask, "comment"].apply(
            lambda t: bool(pattern.search(t)))
    return df, seen


def summarise(df, point_columns):
    """Build the two tables the final stage reasons over."""
    theme_table = (pd.crosstab(df["group"], df["theme"], normalize="index")
                   .mul(100).round(1))

    rows = []
    for column, label in point_columns.items():
        for group, sub in df.groupby("group"):
            hits = int(sub[column].sum())
            if hits == 0:
                continue          # this point came from another group's video
            rows.append({"group": group, "point": label,
                         "echoed_pct": round(sub[column].mean() * 100, 1),
                         "n": hits})
    transfer_table = pd.DataFrame(rows)

    return theme_table, transfer_table