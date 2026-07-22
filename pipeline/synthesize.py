"""
Stage 5 - hand the model the numbers and the evidence, ask for a report.

Two rules baked into the prompt, both learned the hard way:

  1. The model gets the STATISTICS, not the raw corpus. It is not
     allowed to invent a percentage, only to interpret ones it was
     given. That is what stops confident nonsense.

  2. Quotes must be copied verbatim from the shortlist supplied. The
     model is explicitly told not to compose or tidy a quote.
"""

import pandas as pd
from pipeline import llm
import config as config

PROMPT = """You are a strategist writing a short reception report for a
creative team. Your job is to explain which ideas from the videos reached
the audience and which did not, and what the audience talked about instead.

## GROUNDED: what the videos put forward

This was read from the videos themselves (transcript, title, description).
You may rely on it.

{brief}

## BACKGROUND: what the campaigns are believed to be

This is model-generated context, NOT grounded in the comment data or the
videos. Use it only to frame the campaign. Never present anything from
this section as a finding, and where you draw on it, say it is background
rather than evidence. If it contradicts the grounded section, trust the
grounded section.

{background}

## Theme mix per group (% of that group's comments)

{themes}

## Signal transfer: did each idea the video pushed appear in the comments?

{transfer}

## Emotion labels

{emotion}

## Sample sizes

{sizes}

## Candidate quotes (verbatim, with their theme)

{quotes}

---

Write a markdown report with these sections:

1. **What this covers** - two sentences on the videos and base size.
2. **One section per group** - what the video put forward, which ideas
   travelled and which did not, and what filled the space instead.
   Include 2-4 quotes, copied EXACTLY as given, each with a one-line
   gloss in English if the original is not English.
3. **Across the groups** - patterns that hold across more than one.
4. **Emotion mix** - include this section ONLY if emotion data was
   supplied above. Report the split, then state plainly what it does and
   does not tell you, including the confidence caveat given. Do not let
   it lead the report: the theme mix is the stronger evidence.
5. **Limitations** - be specific and honest. Name any group with fewer
   than 100 comments as unreliable. Note that low transfer means an idea
   did not arrive, which is different from being rejected.

Rules you must follow:
- Use ONLY the numbers given above. Never invent or estimate a figure.
- Quote ONLY from the candidate list. Do not edit, translate in place,
  or compose quotes.
- If a group has a small sample, say so in that group's section, not
  only in the limitations.
- No em-dashes. Plain declarative prose. No filler.
- If the evidence does not support a conclusion, say the evidence is
  thin rather than reaching for one.
- Anything taken from the BACKGROUND section must be flagged as
  unverified background in the sentence where it appears."""


def pick_quotes(df, per_theme=3, max_total=45):
    """Shortlist the most-liked comment in each theme, per group."""
    picks = []
    for (group, theme), sub in df.groupby(["group", "theme"]):
        top = sub.sort_values("likes", ascending=False).head(per_theme)
        for _, row in top.iterrows():
            picks.append(f'[{group} | {theme} | {row["likes"]} likes] '
                         f'"{row["comment"][:280]}"')
    return "\n".join(picks[:max_total])


def run(brief, background, theme_table, transfer_table, emotion_result, df):
    sizes = df.groupby("group").size().to_string()
    transfer_text = (transfer_table.to_string(index=False)
                     if not transfer_table.empty else "(none measured)")

    if emotion_result:
        emotion_text = (emotion_result["table"].to_string()
                        + "\n\nCaveat: " + emotion_result["caveat"])
    else:
        emotion_text = "(not run - omit the emotion section entirely)"

    prompt = PROMPT.format(
        brief=brief[:9000],
        background=(background or "(not generated)")[:6000],
        themes=theme_table.to_string(),
        transfer=transfer_text,
        emotion=emotion_text,
        sizes=sizes,
        quotes=pick_quotes(df),
    )
    return llm.ask(prompt, model=config.MODEL_SMART)