# YouTube Comment Intelligence

Put YouTube video links in. Get a reception report out.

Works on brand ads, creator reviews, and explainer or viral content. Handles the two questions separately - what did the video put forward, and what did the audience talk about - so "did the message land" does not collapse into a circular yes.

---

## Quick start

```bash
pip install -r requirements.txt
pip install playwright && playwright install chromium
```

Then either run the CLI:

```bash
# edit config.py: paste your links and two API keys
python run.py
```

or start the backend API:

```bash
# put YOUTUBE_API_KEY and GEMINI_API_KEY in a .env file at repo root
pip install -r requirements.txt -r requirements-server.txt
pip install playwright && playwright install chromium
python server.py
# API at http://localhost:8000/api
# browser UI at http://localhost:8000 (fixture data only - pipeline wiring is a later phase)
```

Full install, keys, PDF engine, and troubleshooting notes live in [docs/setup.md](docs/setup.md).

---

## What it does

Two questions, answered separately and then joined:

1. **What did the video put forward?** Read from the transcript, title, and description. Never from the comments.
2. **What did the audience talk about?** Read from the comments. Never from the video.

Then: which of the video's ideas appeared in the comments, and what filled the space instead. Keeping the two apart is the point.

A low signal-transfer score means an idea **did not arrive**, which is a different diagnosis from being rejected. The fix for the first is execution and distribution. The fix for the second is the idea itself.

Alongside the transfer numbers you get a theme mix (what commenters actually talked about, discovered from the corpus, not preset) and emotion and sentiment breakdowns (both run locally, no tokens).

---

## What you get

Each run writes to `output/<session-name>/`:

- **`report.pdf`** - the internal debrief. Per campaign: verdict table, what filled the conversation instead, verbatim quotes with English glosses, two actions. Then a cross-group section and limitations. Every claim is grounded on the video transcripts, titles, and descriptions.
- **`comments.csv`** - one row per comment with theme, emotion, emotion_confidence, sentiment, sentiment_confidence, echoed ideas, likes, language. Sorted by group then likes, so the top comments in each block are the ones people rallied around. This is the file for handpicking quotes.
- **`summary.csv`** - every number in tidy long format, one row per figure. Filter on `metric` to get the data behind one chart.
- **`chart_transfer.csv`, `chart_themes.csv`** - the two chart tables the design team draws from.

The report PDF is an internal debrief, not a client deliverable.

Set `KEEP_INTERMEDIATE = True` in `config.py` to also get a `debug/` folder with the codebook, briefs, and intermediate markdown for auditing.

---

## Two entry points

- **CLI** (`python run.py`): edit `config.py`, run, get a folder in `output/`. The fastest way to produce a report.
- **Backend server** (`python server.py`): FastAPI at `localhost:8000` with sessions, campaigns, uploads, and a brief-review interrupt. The API is functional and runs the same pipeline internally. The browser UI at `/` currently runs against fixture data - pipeline wiring is a later phase. The CLI is the fully working end-to-end path today.

Both share the same analysis engine in `pipeline/`.

---

## Files

| File | Does |
|---|---|
| `config.py` | Links, keys, settings. The only file you normally edit for CLI use |
| `run.py` | Preflight, session folder, orchestration for the CLI |
| `server.py` | FastAPI backend |
| `adapter.py` | Wraps the pipeline for the server, streams progress |
| `db.py`, `storage.py`, `assets.py` | SQLite, file paths, text extraction |
| `pipeline/llm.py` | All model access. Swap provider here |
| `pipeline/collect.py` | Fetch comments and transcripts, clean, language filter |
| `pipeline/brief.py` | What each campaign put forward, grounded on transcripts only |
| `pipeline/analyze.py` | Codebook discovery, per-comment classification, signal transfer |
| `pipeline/report.py` | Write, render, export |
| `app/` | Single-page vanilla JS frontend |

---

## Documentation

- [docs/setup.md](docs/setup.md) - install, configure, run, troubleshoot.
- [docs/architecture.md](docs/architecture.md) - how the pipeline, backend, and frontend fit together.
- [docs/api-reference.md](docs/api-reference.md) - HTTP contract for the backend.

For contributors and agents: [AGENTS.md](AGENTS.md) covers code style and repo conventions.

---

## Limits

- Emotion labels are per comment with no surrounding context. Sarcasm and measured criticism both read as anger. The theme mix is a better answer to "how was this received"; emotion is the answer to "the client asked for sentiment".
- No captions means the brief falls back to title and description, flagged in the report.
- Groups under about 100 comments are not statistically reliable. The report is instructed to say so per group.
- Commenters are not buyers. This is directional qualitative input, not market research.

---

## Cost

- YouTube quota covers comments and transcripts on the free tier.
- LLM cost scales with corpus size: one briefing call per group, one codebook call (or two if the first misses too much), then per-comment classification in batches, and one report call. Current setup uses Gemini free tier.
- Emotion and sentiment run locally on ~500 MB HuggingFace models total. First run downloads them; subsequent runs cost nothing.

Free-tier prompts may be used to train the provider's models. Use a paid tier or Vertex AI for confidential work.
