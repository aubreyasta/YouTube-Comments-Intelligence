# YouTube Comment Intelligence

Put video links in. Get a reception report out. Works on brand ads, creator
reviews, and explainer or viral content.

```bash
pip install -r requirements.txt
pip install playwright && playwright install chromium
# edit config.py: paste your links and two API keys
python run.py
```

---

## The idea

Two questions, answered separately and then joined:

1. **What did the video put forward?** Read from the transcript, title and
   description. Never from the comments.
2. **What did the audience talk about?** Read from the comments. Never from
   the video.

Then: which of the video's ideas appear in the comments, and what filled the
space instead. Keeping the two apart is the point. If the model saw the
comments while describing the video, "did the message land" becomes circular
and always answers yes.

A low score means an idea **did not arrive**, which is a different diagnosis
from being rejected. The fix for the first is execution and distribution. The
fix for the second is the idea.

---

## Why the model writes rules instead of labels

The obvious design sends every comment to an LLM to classify. That is
expensive, unrepeatable, and impossible to audit. This does it the other way:

- The model reads a **stratified sample** and writes a **codebook**: the
  themes present in this conversation and the keywords for each.
- Python applies that codebook to **every** comment with regex.

Model does the judgement, code does the counting. Cost is flat in corpus
size, results are deterministic, and every figure traces to a rule you can
read in `debug/codebook.json`.

**What it gives up:** paraphrase and negation. A comment praising something
without using a keyword is missed, and "not worth it" matches a `worth`
keyword. Transfer figures say whether a subject *arrived*, not its exact
share.

**About the sample.** It only ever discovers themes; every percentage is
counted over the full corpus, so it never needs to be proportionally
representative. It needs one example of everything worth naming, which is a
much easier bar. Size is `max(150, 8% of corpus)` capped at 500, drawn per
group from the most-liked, the longest, and a random remainder. If more than
30% of comments match nothing, one extra call extends the codebook.

**Why this matters, concretely.** An early version measured a campaign's
concept-car heritage hook at 7.5% transfer. The codebook showed nine of those
ten matches were the brand's own dealer account posting the trim name, which
was in the keyword list. The real figure was under 1%. The briefing prompt
now forbids brand and product names in keywords and says why, but the error
was findable only because the rule was written somewhere a human could read.

---

## Files

| File | Does |
|---|---|
| `config.py` | Links, keys, settings. The only file you normally edit |
| `run.py` | Preflight, session folder, orchestration |
| `pipeline/llm.py` | All model access. Swap provider here and nowhere else |
| `pipeline/collect.py` | Fetch comments and transcripts, clean, language filter |
| `pipeline/brief.py` | What each campaign put forward, grounded and background |
| `pipeline/analyze.py` | Codebook, signal transfer, emotion |
| `pipeline/report.py` | Write, render, export |

About 1,300 lines total.

---

## Cost

Four to five model calls per run, whatever the corpus size.

| Stage | Calls |
|---|---|
| Briefing | 1 per group |
| Codebook | 1, or 2 if the first misses too much |
| Report | 1 |

Comments and transcripts use the free YouTube quota. Emotion runs locally.

---

## Outputs

Each run gets its own folder, named after what was analysed.

```
output/
  kia-sonet-carens-rivals/      <- session A
      report.pdf
      comments.csv
      summary.csv
  converse-campaign/            <- session B, unrelated
      ...
```

Set `SESSION_NAME` in `config.py` to name it yourself, or leave it blank to
build the name from the group names. Repeat runs get `-2`, `-3` rather than
overwriting.

**`report.pdf`** is an internal debrief, not a client deliverable. Per group:
background in two or three sentences, a verdict table using a closed
vocabulary (`Yes`, `Partly`, `Barely`, `No`, `Backfired`, `Not used`,
`Loud`), what filled the conversation instead, two or three verbatim quotes
with English glosses, and exactly two actions. Then a cross-group section and
a short limitations block. Emotion is folded into each group's header rather
than given a section: it is the weakest evidence here and a section would
give it more weight than it earns.

**`comments.csv`** is one row per comment with theme, emotion, which ideas it
echoed, likes, language and the cheap flags. Sorted by group then likes, so
the comments people rallied around are at the top of each block. This is the
file for handpicking quotes.

**`summary.csv`** is tidy long format, one row per number:

```
group,metric,label,value,unit,n
Honda BR-V N7X,base,comments analysed,110,count,110
Honda BR-V N7X,theme,Price pushback,7.3,percent,8
Honda BR-V N7X,signal_transfer,Hero colour,10.4,percent,11
Honda BR-V N7X,emotion,happy,62.1,percent,68
```

Long rather than wide because it pivots without reshaping. Filter on `metric`
to get the data behind one chart and drop it into Sheets. The pipeline draws
no charts itself: this file exists so the design team builds their own.

### debug/

`KEEP_INTERMEDIATE = True` adds `output/<session>/debug/` with the raw fetch,
briefs, background, codebook, and the report markdown and HTML.

**No stage reads these.** Data passes between stages in memory, so they exist
purely for auditing. The one worth turning on when a number looks wrong is
`codebook.json`.

---

## Two briefs, kept apart

`brief.py` returns two things and the report stage is told which is which.

**Grounded** comes from the transcripts. Every claim is checkable.

**Background** is what the model knows about the campaign: tagline,
positioning, audience, competitive context. Search grounding is on where the
tier allows, so claims come back with sources, and the prompt requires
anything inferred to be marked unverified.

A model asked "what was campaign X about" produces fluent, specific,
confident detail whether or not it knows, and taglines, unit counts and
launch dates are exactly what it invents. The synthesis prompt is told to
treat background as framing, flag it inline as unverified, and trust the
grounded section on any conflict. **Read the background file before it
reaches a client.** It is the only output not traceable to your own data.

---

## Which model

**Gemini Flash-tier, free plan.** No card, large context, and noticeably
better Indonesian than the free Llama and Mistral endpoints, which matters
here more than raw reasoning. Call volume is far below any daily cap.

Alternatives: **Groq** is faster and free but weaker on Indonesian.
**OpenRouter** aggregates free models, useful as a rate-limit fallback.

Two warnings. **Free-tier prompts may be used to train the provider's
models**, so use a paid tier or Vertex AI for confidential work. And **model
IDs change every few months**, which is why `MODEL_CHEAP` and `MODEL_SMART`
are config variables. A 404 means the ID is stale.

---

## Emotion

Always runs. `EMOTION_MODE` takes `"emotion"` or `"sentiment"`. It runs
locally through HuggingFace, so it costs no tokens whatever the corpus size.
First run downloads about 500 MB.

Labels are assigned per comment with no surrounding context, so sarcasm and
measured criticism both read as anger. The pipeline reports the share of
low-confidence labels and warns when it is high, and that caveat travels into
the report prompt so the numbers cannot be presented without it. The theme
mix is the better answer to "how was this received". Emotion is the answer to
"the client asked for sentiment".

---

## The PDF is required

The run refuses to start without a PDF engine. Engines are tried in order:

| Engine | Notes |
|---|---|
| `playwright>=1.49` | Recommended. Best output, same on every OS. Two steps: `pip install playwright` then `playwright install chromium` |
| `weasyprint>=68` | Needs GTK on Windows, which is the painful part. 68 is the floor because CVE-2025-68616 was fixed there |
| `pdfkit>=1.0.0` | Thin wrapper; the wkhtmltopdf binary installs separately. Last release 2021 and wkhtmltopdf is archived upstream. Last resort only |

HTML is a build artifact, not an output: it goes to a temp file and is
deleted. Turn on `KEEP_INTERMEDIATE` to keep it, along with the raw markdown
the model returned, in `debug/`.

### Preflight

Before anything runs, `run.py` checks both API keys are set, `VIDEOS` is not
empty, `EMOTION_MODE` is valid, `transformers` is importable, and a PDF
engine exists. Everything missing is reported as one list rather than one
failure at a time, so a bad setup costs a second instead of five model calls
and a 500 MB download.

Preflight can only see the Python package, not the browser that `playwright
install chromium` downloads. If preflight passes and rendering then fails,
the missing browser is why.

---

## Fetching notes

**One API client per thread.** `googleapiclient` sits on `httplib2`, which is
not thread-safe. Sharing a service object across threads has them reading
from one socket, which surfaces as SSL record-layer failures or `NoneType has
no attribute read` from deep inside `http.client`. `collect._service()` gives
each thread its own, and none is passed between threads. If you extend that
file, keep the rule.

**Transient errors retry, permanent ones do not.** Socket and SSL failures
get three attempts with backoff, discarding the thread's client each time
because a broken connection stays broken. `HttpError` is never retried: a 403
means comments are disabled and a 404 means the video is gone.

**One bad video does not kill the run.** It reports what happened, keeps what
it collected, and continues. The run only fails if no video yields anything.

---

## Common errors

| Symptom | Cause |
|---|---|
| `ModuleNotFoundError: googleapiclient` | Ran with `py` instead of `python` on Windows. `py` uses the system Python and ignores the active conda environment |
| `httpx.InvalidURL: Invalid non-printable ASCII character` | A tab or newline inside a pasted API key. Check with `python -c "import config; print(repr(config.GEMINI_API_KEY))"` |
| 404 from Gemini | Stale model ID. Update `MODEL_CHEAP` / `MODEL_SMART` |
| `JSONDecodeError` during briefing | The model returned malformed JSON. `llm.ask_json` retries by sending it back to be fixed; a hard failure here usually means a truncated response |
| SSL record layer failure | Was a thread-safety bug, since fixed. If it returns, check nothing is sharing a service object across threads |

---

## Limits

- Keyword matching undercounts paraphrase. Transfer means arrived or did not
  arrive, not a precise share.
- Emotion labels have no context.
- No captions means the brief falls back to title and description, flagged in
  the output.
- Groups under about 100 comments are not reliable. The report is instructed
  to say so per group.
- Commenters are not buyers. Directional qualitative input, not market
  research.