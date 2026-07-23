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

PROMPT = """You are a strategist writing an internal debrief for a
creative team. Not a client deliverable. Dense, plain, no ceremony.

The reader cannot change the product. They can only change the story, so
every conclusion must be about messaging, narrative or execution, never
about specification.

Length discipline matters more than completeness. This should read like a
sharp internal memo, not a report. If a sentence does not change what
somebody does on Monday, cut it.

=====================================================================
GROUNDED EVIDENCE - read from the videos themselves. Rely on this.
=====================================================================

{brief}

=====================================================================
BACKGROUND - model-generated, NOT grounded in the data. Framing only.
Never present as a finding. Trust the grounded section on any conflict.
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

=====================================================================
CANDIDATE QUOTES - verbatim. Quote ONLY from this list.
=====================================================================

{quotes}

=====================================================================
WRITE IT
=====================================================================

Follow this structure exactly. Use markdown. Keep it tight.

# [Title stating the finding, not the topic. Under 8 words.]

*[One line: what this covers and the base. Under 25 words.]*

Then for EACH group:

## [Group name]

*[tagline if known] - [n] comments - [dominant emotion and its share, one
phrase only]*

**Background**

[2 to 3 sentences maximum. What the campaign or video is, what it led
with, and the mechanic it used. If anything here comes from the
BACKGROUND section rather than the video, write "unverified" beside it.]

| Decision | Travelled? | How the audience handled it |
|---|---|---|
| ... | ... | ... |

[One row per idea in the signal transfer table for this group. Add one
final row for anything the audience raised loudly that the brand did not.
Verdict must be exactly one of:{verdicts}
Keep the third column under 15 words.]

**Talked about instead**

[1 to 2 sentences. What filled the conversation, drawn from the theme
mix. Name the largest themes and what they mean.]

**Comments**

[2 to 3 quotes only. Format each as:]

> "[copied EXACTLY from the candidate list]"
>
> *[One line. Translate if not English, then say what it shows.]*

**So what**

[Exactly 2 bullets. Each names something the team can DO. Start each with
a verb. Under 25 words each.]

---

After all groups:

## Across the groups

[One paragraph, 3 to 4 sentences. The patterns that hold across more than
one group. Be specific about what kind of idea travelled and what kind did
not.]

| Group | Led with | Audience told instead | Priority fix |
|---|---|---|---|
| ... | ... | ... | ... |

**The one thing to carry into the next brief**

[Two sentences. A claim, not a summary.]

## Read this before quoting it

[4 to 6 short lines, one per limitation. Cover: any group under 100
comments and that it is unreliable; that low transfer means the idea did
not arrive rather than being rejected; that keyword matching undercounts
paraphrase; that emotion labels have no context so sarcasm reads as anger;
that commenters are not buyers. One line each, no elaboration.]

=====================================================================
RULES
=====================================================================

- Use ONLY the numbers supplied. Never invent or estimate a figure.
- Quote ONLY from the candidate list, verbatim. Translation goes in the
  gloss line underneath, never inside the quote.
- Verdicts come from the closed vocabulary. No other words.
- No em-dashes. No filler. No "in conclusion", "it is worth noting",
  "delve", "landscape", "leverage" as a verb.
- Short sentences. Plain declaratives.
- Do not restate the method. Do not explain what a percentage is. Do not
  describe the pipeline. The reader knows.
- If a group has under 100 comments, say so in that group's own header
  line.
- Anything from the BACKGROUND section is flagged unverified inline.
- Write in {language}."""

# Emotion is folded into each group's header line rather than given a
# section of its own. It is the weakest evidence in the pipeline and a
# dedicated section gives it more weight than it earns.
EMOTION_SECTION = ""


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


def run(brief, background, theme_table, transfer_table, emotion_result,
        df):
    sizes = df.groupby("group").size().to_string()
    transfer_text = (
        transfer_table.to_string(index=False)
        if transfer_table is not None and not transfer_table.empty
        else "(none measured)")

    if emotion_result:
        emotion_text = (emotion_result["table"].to_string()
                        + "\n\nCaveat: " + emotion_result["caveat"])
    else:
        emotion_text = ("(not run - omit the emotion phrase from each "
                        "group header)")

    prompt = PROMPT.format(
        brief=brief[:9000],
        background=(background or "(not generated)")[:6000],
        themes=theme_table.to_string(),
        transfer=transfer_text,
        emotion=emotion_text,
        sizes=sizes,
        quotes=pick_quotes(df),
        verdicts=VERDICTS,
        language=config.REPORT_LANGUAGE,
    )
    return llm.ask(prompt, model=config.MODEL_SMART)