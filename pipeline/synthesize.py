"""
Stage 6 - hand the model the numbers and the evidence, ask for a report.

The output is not a loose summary. It follows a fixed structure, because
a report a creative team can act on needs the same furniture every time:
a verdict per creative decision, quotes in the original language with a
gloss underneath, and a "so what" that names the fix.

Three rules baked into the prompt, all learned the hard way:

  1. The model gets the STATISTICS, not the raw corpus. It may interpret
     a number it was given and may never invent one.

  2. Quotes must be copied verbatim from the shortlist supplied. The
     model is told explicitly not to compose, tidy or translate in place.

  3. Every creative decision gets a verdict from a closed vocabulary.
     Free-text verdicts drift into waffle; a fixed set forces a call.
"""

import pandas as pd

from pipeline import llm
import config

# Closed vocabulary. The model must pick one of these per decision.
VERDICTS = """
- **Yes** - the idea clearly entered the conversation
- **Partly** - present, but weaker or different from what was intended
- **Barely** - a handful of mentions, effectively did not land
- **No** - absent from the conversation
- **Backfired** - it arrived, and the audience turned it against the brand
- **Not used** - the brand did not make this argument, but the audience did
- **Loud** - not a brand decision at all: something the audience brought
  itself and talked about more than anything the video said
"""

PROMPT = """You are a strategist writing a reception report for a creative
team. Your job is to explain which ideas from the videos reached the
audience, which did not, and what the audience talked about instead.

The reader is a creative or planner. They cannot change the product. They
can only change the story, so every conclusion must be about messaging,
narrative or execution, never about specification.

=====================================================================
GROUNDED EVIDENCE - read from the videos themselves. You may rely on this.
=====================================================================

{brief}

=====================================================================
BACKGROUND - model-generated, NOT grounded in the data. Framing only.
Never present anything here as a finding. If it contradicts the grounded
section, trust the grounded section.
=====================================================================

{background}

=====================================================================
NUMBERS
=====================================================================

Theme mix per group (% of that group's comments):
{themes}

Signal transfer - did each idea the video pushed appear in the comments:
{transfer}

Emotion labels:
{emotion}

Sample sizes:
{sizes}

Videos analysed:
{videos}

=====================================================================
CANDIDATE QUOTES - verbatim. Quote ONLY from this list.
=====================================================================

{quotes}

=====================================================================
WRITE THE REPORT
=====================================================================

Follow this structure exactly. Use markdown.

# [A title that states the finding, not the topic]

*[One italic line: what this covers, in under 20 words.]*

[Two short paragraphs. First: what these campaigns or videos have in
common, and what was actually up for decision by a creative team. Second:
state the question this note answers, which is whether the idea the brand
or creator led with showed up in what people said back. Make the point
that an idea which never appears did not fail because people disliked it,
it failed because it never arrived.]

[One line stating the base: how many comments, how many videos, and any
filtering applied.]

---

Then, for EACH group, a section like this:

### [Group name]
*"[tagline or hook if known]" - [n] comments*

**The creative decisions**

[One paragraph. What the brand or creator chose: the hook, the
positioning, the featured attributes, any mechanic such as scarcity or a
price claim. Concrete, not abstract.]

**What travelled**

[One short paragraph. The headline answer for this group.]

| Creative decision | Travelled? | How the audience handled it |
|---|---|---|
| ... | ... | ... |

[One row per idea from the signal transfer table for this group, plus a
row for anything the audience raised loudly that the brand did not.
The verdict must be one of:{verdicts}]

[Then 2 to 4 quotes. Format each exactly like this:]

> "[quote copied EXACTLY from the candidate list]"
>
> *[One line of gloss in English. If the quote is not in English,
> translate it here. Say what it shows, not merely what it says.]*

**For the creative team**

[2 to 3 short paragraphs. Each must name something the team can DO.
Not observations, instructions. Lead each paragraph with the action.]

---

## At a glance

![Signal transfer]({transfer_chart})

[One paragraph naming the patterns that hold across more than one group.
Be specific about what kind of idea travelled and what kind did not.]

### Cross-campaign comparison

| Campaign | The idea it led with | The story the audience told instead | Best creative asset in the thread | Priority fix |
|---|---|---|---|---|
| ... | ... | ... | ... | ... |

### The one thing to carry into the next brief

[One paragraph. The single most portable lesson. Make it a claim, not a
summary.]

{emotion_section}

### Limitations

[Be specific and honest. Name any group under 100 comments as unreliable.
State that low transfer means an idea did not arrive, which is different
from being rejected. Note that keyword matching undercounts paraphrase.
Note that commenters are not buyers.]

### Videos analysed

| Group | URL | Comments |
|---|---|---|
[One row per video, from the list supplied above.]

*[Closing italic line with the full base, the collection date if known,
and the directional-not-market-research caveat.]*

=====================================================================
RULES YOU MUST FOLLOW
=====================================================================

- Use ONLY the numbers supplied above. Never invent or estimate a figure.
- Quote ONLY from the candidate list. Do not edit, tidy, shorten or
  translate a quote in place. The gloss goes underneath it.
- Every verdict must come from the closed vocabulary. No other words.
- If a group has a small sample, say so in that group's own section, not
  only in the limitations.
- No em-dashes anywhere. No filler. No "in conclusion", no "it is worth
  noting", no "delve".
- Plain declarative prose. Short sentences.
- If the evidence does not support a conclusion, say the evidence is thin
  rather than reaching for one.
- Anything drawn from the BACKGROUND section must be flagged as unverified
  in the sentence where it appears.
- Write in {language}."""

EMOTION_SECTION = """### Emotion mix

[Report the split per group. Then state plainly what it does and does not
tell you, including the confidence caveat given above. Do not let it lead:
the theme mix is stronger evidence. If most labels are low confidence, say
the numbers are directional at best.]"""


def pick_quotes(df, per_theme=3, max_total=60):
    """
    Shortlist candidate quotes: the most-liked comment in each theme per
    group, plus the longest, which is where the reasoning tends to live.
    """
    picks = []
    for (group, theme), sub in df.groupby(["group", "theme"]):
        top = sub.sort_values("likes", ascending=False).head(per_theme)
        longest = (sub.assign(_len=sub["comment"].str.len())
                      .nlargest(2, "_len"))
        combined = pd.concat([top, longest]).drop_duplicates(
            subset=["comment"])
        for _, row in combined.iterrows():
            picks.append(f'[{group} | {theme} | {row["likes"]} likes] '
                         f'"{row["comment"][:300]}"')
    return "\n".join(picks[:max_total])


def format_videos(meta_df, df):
    """Build the videos-analysed rows the model turns into a table."""
    counts = df.groupby("video_id").size().to_dict()
    lines = []
    for _, row in meta_df.iterrows():
        video_id = row["video_id"]
        lines.append(
            f"{row['group']} | https://www.youtube.com/watch?v={video_id} "
            f"| {counts.get(video_id, 0)} comments in base "
            f"| title: {str(row.get('title', ''))[:80]}")
    return "\n".join(lines)


def run(brief, background, theme_table, transfer_table, emotion_result,
        df, meta_df, charts):
    sizes = df.groupby("group").size().to_string()
    transfer_text = (
        transfer_table.to_string(index=False)
        if transfer_table is not None and not transfer_table.empty
        else "(none measured)")

    if emotion_result:
        emotion_text = (emotion_result["table"].to_string()
                        + "\n\nCaveat: " + emotion_result["caveat"])
        emotion_section = EMOTION_SECTION
    else:
        emotion_text = "(not run)"
        emotion_section = ("(emotion was not run - omit this section "
                           "entirely)")

    prompt = PROMPT.format(
        brief=brief[:9000],
        background=(background or "(not generated)")[:6000],
        themes=theme_table.to_string(),
        transfer=transfer_text,
        emotion=emotion_text,
        sizes=sizes,
        videos=format_videos(meta_df, df),
        quotes=pick_quotes(df),
        verdicts=VERDICTS,
        transfer_chart=charts.get("transfer", "05_signal_transfer.png"),
        emotion_section=emotion_section,
        language=config.REPORT_LANGUAGE,
    )
    return llm.ask(prompt, model=config.MODEL_SMART)