"""
Stage 3 - work out what each video was actually pushing.

This reads the transcript, title and description, NOT the comments.

Output is one markdown brief per video, plus a structured list of
claims that stage 4 turns into measurable rules.
"""

import json
from pipeline import llm
import config as config

PROMPTS = {
    "brand_ad": """You are analysing a brand's own advertisement or launch video.

Identify the CREATIVE DECISIONS the brand made: the narrative or hook it
leads with, the positioning, the specific features or attributes it
chose to foreground, the visual or tonal choices, and any mechanic such
as scarcity, a price claim, or a co-branding partner.""",

    "review": """You are analysing an independent creator's review.

Identify what the CREATOR chose to foreground: which features or aspects
they spent time on, what they praised, what they criticised, and the
comparisons they drew. These are the talking points the audience was
handed.""",

    "explainer": """You are analysing an explainer, news or commentary video.

Identify the CLAIMS AND TOPICS the video puts forward: the main argument,
the sub-topics covered, the framing, and anything presented as
surprising or contentious.""",

    "auto": """You are analysing a YouTube video. First decide whether it is
(a) a brand advertisement, (b) an independent review, or (c) an explainer
or commentary piece. Then identify the main things the video puts
forward: its hook or argument, and the specific points it foregrounds.""",
}

TEMPLATE = """{role}

VIDEO TITLE: {title}
CHANNEL: {channel}
DESCRIPTION: {description}

TRANSCRIPT (may be partial or absent):
{transcript}

Return JSON shaped exactly like this:
{{
  "detected_kind": "brand_ad | review | explainer",
  "one_line_summary": "what this video is, in one sentence",
  "points": [
    {{
      "label": "short name for the idea, 3-6 words",
      "description": "one sentence on what the video says about it",
      "keywords": ["8-15 words or short phrases, in the SAME LANGUAGE as the comments would be, that someone would use if they were talking about this idea. Do NOT include the product or brand name itself, because the brand's own posts will match it and inflate the count."]
    }}
  ]
}}

Give between 4 and 8 points. Order them by how central they are to the
video. Keywords should be lowercase and specific."""


def run(meta_df):
    briefs, all_points = [], []

    for _, row in meta_df.iterrows():
        kind = row.get("kind", "auto")
        role = PROMPTS.get(kind, PROMPTS["auto"])
        transcript = row.get("transcript") or "(no captions available)"

        print(f"    briefing {row['video_id']} ({row['group']})")
        result = llm.ask_json(TEMPLATE.format(
            role=role, title=row.get("title", ""),
            channel=row.get("channel", ""),
            description=row.get("description", "")[:2000],
            transcript=transcript[:12000],
        ))

        for point in result.get("points", []):
            all_points.append({
                "group": row["group"], "video_id": row["video_id"],
                "kind": result.get("detected_kind", kind),
                "label": point["label"],
                "description": point.get("description", ""),
                "keywords": point.get("keywords", []),
            })

        lines = [f"### {row.get('title','(untitled)')}",
                 f"*{row.get('channel','')} &middot; {row['video_id']} &middot; "
                 f"group: {row['group']} &middot; "
                 f"detected as: {result.get('detected_kind', kind)}*", "",
                 result.get("one_line_summary", ""), "",
                 "**What the video puts forward:**", ""]
        for point in result.get("points", []):
            lines.append(f"- **{point['label']}** &mdash; "
                         f"{point.get('description','')}")
        if not row.get("has_transcript"):
            lines.append("")
            lines.append("> No captions were available for this video, so "
                         "this brief is based on title and description only. "
                         "Treat it as weaker evidence.")
        briefs.append("\n".join(lines))

    return "\n\n---\n\n".join(briefs), all_points