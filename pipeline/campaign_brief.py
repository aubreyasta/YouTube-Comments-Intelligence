"""
Stage 3b - campaign background brief.

Stage 3 (video_brief) reads only the video: transcript, title, description.
Everything it says is grounded in something you can go and check.

This stage does the other thing: it asks the model what it knows about the
CAMPAIGN itself. Positioning, tagline, target audience, launch context,
competitive framing. That is genuinely useful context the transcript does
not contain, and it is what a strategist would normally spend a morning
googling.

It is kept SEPARATE and clearly labelled for one reason: this output is not
grounded in your data. A model asked "what was campaign X about" will
produce fluent, plausible, specific detail whether or not it knows. Search
grounding is switched on where available so claims come back with sources,
but the output still needs a human to check it before it reaches a client.

The report stage is told which brief is which, and told to treat this one
as background rather than evidence.
"""

import config as config

PROMPT = """You are a strategist preparing background on an advertising campaign.

CAMPAIGN: {name}
CONTEXT SUPPLIED: {context}
KNOWN VIDEO TITLES: {titles}

Research and summarise:
1. What the campaign is, who launched it, and when.
2. Its tagline and stated positioning.
3. The target audience it appears aimed at.
4. The specific creative decisions: narrative hook, visual treatment,
   featured attributes, any mechanic such as scarcity, price framing,
   or co-branding.
5. Competitive context: what rivals were doing at the time.

Rules:
- If you are not confident about a specific fact such as a tagline, unit
  count, launch date, or price, say so explicitly rather than stating it.
  Write "unverified" next to anything you are inferring.
- Do not invent statistics.
- Where you used a source, name it.

Write markdown, under 400 words. Start with a line reading exactly:
`> Model-generated background. Verify before client use.`"""


def run(meta_df, campaign_context=None):
    """One brief per group. Returns markdown."""
    from pipeline import llm

    briefs = []
    for group, sub in meta_df.groupby("group"):
        titles = "; ".join(sub["title"].fillna("").astype(str).tolist())
        context = (campaign_context or {}).get(group, "(none supplied)")
        print(f"    background brief: {group}")

        try:
            text = llm.ask_grounded(
                PROMPT.format(name=group, context=context, titles=titles))
        except Exception as error:
            print(f"      grounding unavailable ({error}), falling back")
            text = llm.ask(
                PROMPT.format(name=group, context=context, titles=titles))

        briefs.append(f"## {group}\n\n{text}")

    return "\n\n---\n\n".join(briefs)