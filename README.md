# Comment Intelligence Pipeline

Put YouTube links in, get a reception report out. Works on brand ads,
creator reviews, and explainer or news videos.

```bash
pip install -r requirements.txt
# edit config.py: paste your links and two API keys
python run.py
```

Everything lands in `output/`.

---

## The idea

Two questions, answered separately and then joined:

1. **What did the video put forward?** Read from the transcript, title and
   description. Never from the comments.
2. **What did the audience talk about?** Read from the comments. Never from
   the video.

Then: which of the video's ideas appear in the comments, and what filled the
space instead. Keeping the two sources apart is the whole point. If the model
saw the comments when describing the video, "did the message land" becomes
circular and the answer is always yes.

---

## Why the model writes the rules instead of doing the labelling

The obvious design is to send every comment to an AI and ask it to label
them. That is expensive, slow, unrepeatable, and impossible to audit.

This does it the other way round:

- The model reads a **sample** of comments once and writes a **codebook**:
  the themes present in this particular conversation, and the keywords that
  indicate each one.
- Python then applies that codebook to **all** the comments with plain regex.

So the model does the judgement and the code does the counting. Cost stays
flat whether you have 200 comments or 20,000, results are reproducible, and
`output/04_codebook.json` shows you exactly what rules produced every number.
If a rule is wrong you edit it and re-run stage 4 without paying again.

This is also what makes it generic. The old version had Indonesian automotive
keywords hardcoded, so it only worked on one topic. Here the vocabulary is
derived from whatever conversation you point it at.

### About that sample

Fair question, and worth being precise about, because the sample is doing a
narrower job than it looks like it is.

**The sample never produces a number.** Every percentage in the output is
counted over the whole corpus by regex. The sample is used only to *discover*
which themes exist and what words people use for them. So it does not need to
be proportionally representative. It only needs to contain at least one
example of everything worth naming, which is a far easier bar.

That difference matters. If you were estimating "what share of this thread is
about price", 150 of 20,000 would be a real problem. Naming price as a theme
at all needs one clear example.

**It is deliberately not a flat random draw.** A flat draw over-represents
whichever video got the most comments and misses the high-engagement
comments, which is where a thread's shared vocabulary tends to live. So
`stratified_sample()` takes, per group: the most-liked comments, the longest
comments, and a random remainder. It is intentionally skewed toward comments
with more in them.

**It scales.** Actual size is `max(150, 8% of corpus)`, capped at 500. A
400-comment corpus gets 150; a 5,000-comment corpus gets 400. The cap exists
because returns flatten: a theme present in 5% of comments turns up in a
150-comment draw essentially every time, and one present in 1% needs a few
hundred. Beyond that you are paying tokens to rediscover things you already
have.

**It checks itself.** After coding, the pipeline reports the share of
comments matching no theme. If that exceeds `UNCLASSIFIED_LIMIT` (default
30%), it runs one extra call over the leftovers and asks the model to add
themes it missed, then re-codes. Above 40% it prints a warning telling you not
to trust the report until you have looked at the codebook.

**Where it can still fail.** A theme present in well under 1% of a large
corpus can be missed entirely, and rare does not mean unimportant. If you are
specifically hunting for a niche reaction, raise `CODEBOOK_SAMPLE_MAX`, or
grep the coded CSV directly rather than relying on the codebook to have found
it for you.

---

## Stages

| # | File | Does | Costs tokens |
|---|---|---|---|
| 1 | `pipeline/fetch.py` | Comments via YouTube Data API, captions via youtube-transcript-api | no |
| 2 | `pipeline/clean.py` | Spam removal, dedupe, language detection, cheap flags | no |
| 3 | `pipeline/video_brief.py` | What each video pushed, read from the video itself | 1 call per video |
| 3b | `pipeline/campaign_brief.py` | Campaign background, with search grounding | 1 call per group |
| 4 | `pipeline/codebook.py` | Model writes themes, code applies them to everything | 1 call, 2 if the first misses too much |
| 4b | `pipeline/emotion.py` | Emotion or sentiment labels, run locally | no |
| 6 | `pipeline/chart.py` | Signal transfer and theme mix PNGs | no |
| 7 | `pipeline/synthesize.py` | Structured report as markdown | 1 call total |
| 8 | `pipeline/render.py` | Styled HTML, then PDF if an engine is present | no |

For six videos across three campaigns that is eleven or twelve API calls for
the whole run, which sits inside any free tier. The emotion stage adds none,
because it runs on your machine.

## Two briefs, kept apart

Stage 3 and stage 3b both describe the campaign, and the separation is
deliberate.

**`03_video_brief.md` is grounded.** It comes from the transcript, title and
description. Every claim in it traces to something in the video that you can
go and check.

**`03b_campaign_background.md` is not.** It is the model reporting what it
believes about the campaign: tagline, positioning, target audience, launch
context, competitive framing. Search grounding is switched on where the tier
supports it, so claims come back with sources, and the prompt requires the
model to mark anything it is inferring as unverified.

That context is genuinely useful. A transcript will not tell you who a
campaign was aimed at or what the tagline was. But a model asked "what was
campaign X about" produces fluent, specific, confident detail whether or not
it actually knows, and taglines, unit counts and launch dates are exactly the
kind of thing it will invent.

So the two go into the report stage separately and clearly labelled. The
synthesis prompt is told to treat the background as framing rather than
evidence, to flag anything drawn from it as unverified in the sentence where
it appears, and to trust the grounded brief where the two disagree.

**Check the background file before it reaches a client.** It is the one
output in this pipeline that is not traceable to your own data.

---

## Emotion and sentiment

On by default now, since clients ask for it. Set `EMOTION_MODE` in
`config.py` to `"emotion"`, `"sentiment"`, or `None`.

It runs **locally** through HuggingFace transformers rather than through an
API, so it costs nothing in tokens no matter how many comments you have. Cost
is a one-time ~500 MB download and some patience on a laptop without a GPU.

Two things worth knowing before you put the chart in a deck. These models
label each comment with no surrounding context, so sarcasm and measured
criticism both tend to land as anger. And a five-label emotion split maps
badly onto product reception: a careful complaint about a missing feature and
an insult score identically. The pipeline prints the share of low-confidence
labels and warns you when it is high, and the report stage is instructed to
report the split with its caveat and not to lead with it.

The theme mix is the better answer to "how was it received". Emotion is the
answer to "the client asked for sentiment".

---

### The cheap flags in stage 2

`lang`, `n_words`, `is_question`, `has_emoji`, `mentions_price`,
`mentions_competitor`. These are deliberately topic-neutral so they work on
any video, and they carry a surprising amount of the signal. A thread that is
40% questions is a different situation from one that is 40% price talk,
before any AI is involved.

---

## Outputs

```
output/
  01_comments_raw.csv        everything collected
  01_video_meta.csv          titles, channels, view counts
  02_comments_labelled.csv   cleaned, with language and cheap flags
  03_video_brief.md          what each video put forward (grounded)
  03_points.json             those ideas as measurable keyword rules
  03b_campaign_background.md model-generated context  <- verify this
  04_codebook.json           the themes the model wrote  <- check this
  04_comments_coded.csv      every comment with its theme and signal flags
  04_theme_mix.csv           theme percentages per group
  04_signal_transfer.csv     did each idea from the video reach the comments
  05_emotion_mix.csv         emotion split per group, if enabled
  05_signal_transfer.png     chart embedded in the report
  05_theme_mix.png           chart
  06_report.md               the report as markdown
  06_report.html             styled, self-contained, charts embedded
  06_report.pdf              if a PDF engine is installed
```

**Read `04_codebook.json` before you trust `06_report.md`.** It takes a
minute and it is where mistakes are visible.

---

## The report

`06_report.md` follows a fixed template, not free-form prose: a verdict table
per group with a closed vocabulary (`Yes`, `Partly`, `Barely`, `No`,
`Backfired`, `Not used`, `Loud`), verbatim quotes with English glosses
underneath, a "for the creative team" block written as instructions, a
cross-campaign comparison table, and a videos-analysed table.

Set `REPORT_LANGUAGE` in `config.py` to change the prose language. Comments
stay in their original language regardless.

### Getting a PDF

`render.py` always writes a styled, self-contained HTML file with the charts
embedded as base64. It then tries three PDF engines in order and uses the
first one it finds:

```
pip install playwright && playwright install chromium   # best output
pip install weasyprint                                  # GTK needed on Windows
pip install pdfkit                                      # needs a wkhtmltopdf binary
```

If none is installed you still get the HTML. Open it in a browser and print
to PDF: the result is identical, and it costs no setup. The pipeline never
fails because of this stage.

See `PIPELINE_DOCUMENTATION.pdf` for the full architecture, diagrams and
stage-by-stage detail.

---

## Which model to use

Recommendation: **Gemini Flash-tier on the free plan** for all three AI
stages. Reasons, stated plainly:

- The free tier needs no credit card and does not expire, which no other
  major provider currently matches.
- Large context, so a full transcript plus a comment sample fits in one call.
- Indonesian and other Southeast Asian languages are handled noticeably
  better than by the free Llama and Mistral endpoints, which matters here
  more than raw reasoning does.
- This pipeline makes fewer than ten calls per run, so the tight
  requests-per-day limits are irrelevant.

Alternatives worth knowing: **Groq** is far faster and free, but weaker on
Indonesian. **OpenRouter** aggregates free models, useful as a fallback when
you hit a rate limit.

### Two warnings

**Free-tier prompts may be used to train the provider's models.** For agency
work on a client's competitors this is a real consideration. If the input is
confidential, use a paid tier or Vertex AI, which does not train on your
data. Public YouTube comments are public, but the video brief you generate is
your own analysis.

**Model IDs change every few months.** `MODEL_CHEAP` and `MODEL_SMART` in
`config.py` are variables for that reason. If you get a 404, check
<https://ai.google.dev/gemini-api/docs/models> for the current free-tier
Flash ID. Free-tier quotas have also been cut at least once, so verify rather
than assuming.

---

## Guardrails in the synthesis prompt

The final stage is given the statistics, not the raw corpus, and is told:

- use only the numbers supplied, never invent a figure
- quote only from a supplied shortlist, verbatim, no editing or composing
- name any group under 100 comments as unreliable in that group's own section
- treat low transfer as "did not arrive", which is different from "was
  rejected"

These exist because a model handed a pile of comments will happily produce
confident percentages that are not in the data.

---

## Known limits

- Keyword matching undercounts paraphrase. Someone praising a feature without
  using any of the keywords is missed. Treat transfer figures as showing
  whether a subject arrived, not as precise shares.
- Emotion labels are weak evidence. See the section above. They are included
  because clients ask, not because they are the best available answer.
- The campaign background brief is not grounded in your data and needs a
  human to check it.
- Videos without captions get a brief built from title and description only.
  The pipeline flags these in `03_video_brief.md`.
- Comment sections are not audiences. This is directional qualitative input.