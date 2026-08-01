"""
Work out what each campaign put forward.

One call per group, covering all of that group's videos, rather than one
call per video plus one per group. For six videos across three groups
that is 3 calls instead of 9, and each call still sees only one
campaign, so nothing is diluted.

The call is grounded only: every claim is read from the transcripts and
is checkable. No ungrounded background is generated or returned.
"""

from pipeline import llm
from pipeline.config_types import PipelineConfig

LENS = {
    "brand_ad": "the CREATIVE DECISIONS the brand made: the narrative or "
                "hook it leads with, the positioning, the attributes it "
                "foregrounds, and any mechanic such as scarcity, a price "
                "claim or a co-branding partner",
    "review": "what the CREATOR foregrounded: which features they spent "
              "time on, what they praised, what they criticised, and the "
              "comparisons they drew",
    "explainer": "the CLAIMS AND TOPICS put forward: the main argument, "
                 "the sub-topics, the framing, and anything presented as "
                 "surprising or contentious",
    "auto": "the main things put forward: the hook or argument and the "
            "specific points foregrounded. First decide whether this is a "
            "brand ad, an independent review, or an explainer",
}

PROMPT = """Analyse one advertising campaign, or one set of related videos.

CAMPAIGN: {group}
CONTEXT SUPPLIED: {context}

For each video below, identify {lens}.

{videos}

Return JSON:
{{
  "summary": "one sentence on what this campaign is",
  "points": [
    {{
      "label": "short name for the idea, 3 to 6 words",
      "video_id": "which video above this came from",
      "description": "one sentence on what the video says about it"
    }}
  ]
}}

Give 4 to 8 points per video, ordered by how central they are."""


def run(meta_df, cfg: PipelineConfig, context_map=None,
        images_map=None):
    """Return (grounded_markdown, points)."""
    grounded, points = [], []
    context_map = context_map or {}

    for group, sub in meta_df.groupby("group", sort=False):
        kinds = [k for k in sub["kind"].unique() if k in LENS]
        lens = LENS[kinds[0]] if len(kinds) == 1 else LENS["auto"]

        blocks = []
        for _, row in sub.iterrows():
            transcript = row["transcript"] or "(no captions available)"
            blocks.append(
                f"--- VIDEO {row['video_id']} ({row['kind']})\n"
                f"TITLE: {row['title']}\n"
                f"CHANNEL: {row['channel']}\n"
                f"DESCRIPTION: {row['description'][:1200]}\n"
                f"TRANSCRIPT: {transcript[:10000]}")

        print(f"    briefing {group} ({len(sub)} video"
              f"{'s' if len(sub) > 1 else ''})")
        images = images_map.get(group) if images_map else None
        result = llm.ask_json(
            PROMPT.format(
                group=group, lens=lens,
                context=context_map.get(group, "(none supplied)"),
                videos="\n\n".join(blocks)),
            cfg,
            grounded=False,
            images=images)

        known = set(sub["video_id"])
        for point in result.get("points", []):
            vid = point.get("video_id", "")
            points.append({
                "group": group,
                "video_id": vid if vid in known else sub.iloc[0]["video_id"],
                "label": point["label"],
                "description": point.get("description", "")})

        lines = [f"## {group}", "", result.get("summary", ""), "",
                 "**What the videos put forward:**", ""]
        for point in result.get("points", []):
            lines.append(f"- **{point['label']}** ({point.get('video_id','')})"
                         f" - {point.get('description', '')}")
        if not sub["has_transcript"].any():
            lines += ["", "> No captions on any video in this group, so this "
                          "brief rests on titles and descriptions. Weaker "
                          "evidence."]
        grounded.append("\n".join(lines))

    return ("\n\n".join(grounded), points)