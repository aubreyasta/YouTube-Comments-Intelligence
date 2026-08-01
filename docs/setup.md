# Setup

Everything you need to get a working install, keys in the right place, and both entry points running.

Related docs:
- [Architecture](architecture.md) - what the pieces are.
- [API reference](api-reference.md) - HTTP contract.
- [README](../README.md) - short human overview.

---

## Prerequisites

- Python 3.10 or newer.
- A Conda environment is recommended. The repo is developed against one named `YouTubeIntelligence`, but any isolated environment works.
- Windows, macOS, or Linux. Notes below flag Windows-only gotchas.

Check you are on the right interpreter before running anything:

```bash
python -c "import sys; print(sys.executable)"
```

On Windows, use `python`, never `py`. `py` picks the system Python and ignores your active environment, which produces confusing `ModuleNotFoundError` failures.

---

## Install

Two requirement files. Install what you need for the entry point you plan to use.

```bash
# CLI pipeline only
pip install -r requirements.txt
pip install playwright && playwright install chromium

# Backend server (adds the CLI's dependencies plus FastAPI stack)
pip install -r requirements.txt -r requirements-server.txt
pip install playwright && playwright install chromium
```

The Chromium download from `playwright install chromium` is not part of the Python package and cannot be checked by `pip`. If the pipeline's preflight passes but PDF rendering later fails, this is the reason.

Emotion and sentiment classification pull ~500 MB of HuggingFace models on first run. Both run locally and cost no tokens.

---

## Configuration

Two API keys are required:

- `YOUTUBE_API_KEY` - YouTube Data API v3, for comments and metadata.
- `GEMINI_API_KEY` - Google AI Studio, for the LLM stages.

Where to put them depends on which entry point you use.

### CLI pipeline

Edit `config.py`. Copy `config-template.py` to `config.py` if it does not exist yet.

```python
YOUTUBE_API_KEY = "..."
GEMINI_API_KEY  = "..."
VIDEOS = [
    {"url": "https://...", "group": "Campaign A", "kind": "brand_ad"},
    ...
]
```

`config.py` is gitignored. Never commit real keys.

### Backend server

Environment variables, read from the process environment or a `.env` file at the repo root:

```
YOUTUBE_API_KEY=...
GEMINI_API_KEY=...
GEMINI_MODEL=...   # optional, see "Model IDs go stale" below
```

`.env` is gitignored. Never commit it. The browser never sees these keys; only the server process reads them.

### Model IDs go stale

`MODEL` in `config.py` names a Gemini model ID for the CLI pipeline. Google renames or retires models every few months. A `404` from Gemini means the ID is out of date; open the current model list and paste the new ID in.

The backend server does not read `config.py`'s `MODEL` at all. Each run's config shim sets `MODEL` from the `GEMINI_MODEL` environment variable (or `.env` entry) if set, otherwise falls back to the server's built-in default (`gemini-3-pro`).

### Pasted keys with hidden characters

A tab or newline that rides along with a copy-pasted key produces:

```
httpx.InvalidURL: Invalid non-printable ASCII character
```

Check the value directly:

```bash
python -c "import config; print(repr(config.GEMINI_API_KEY))"
```

`repr` shows escape codes, so you will see the stray `\t` or `\n`.

---

## Running

### CLI pipeline

```bash
python run.py
```

`run.py` starts with a preflight check. If any of these are missing or wrong, it reports them all at once and exits before any API call or model download:

- `YOUTUBE_API_KEY`, `GEMINI_API_KEY` set
- `VIDEOS` not empty
- `transformers` importable
- A PDF engine is installed

After the hard checks pass, preflight prints the configured `EMOTION_MODEL` and `SENTIMENT_MODEL` values. Both models always run; there is no flag to disable either.

Each run creates `output/<session-name>/` with `report.pdf`, `comments.csv`, `summary.csv`, `chart_transfer.csv`, and `chart_themes.csv`. Rerunning the same session adds `-2`, `-3`, and so on rather than overwriting.

Set `KEEP_INTERMEDIATE = True` in `config.py` to also write `output/<session>/debug/` with the codebook, briefs, and intermediate markdown. No pipeline stage reads these; they exist for auditing. Check `codebook.json` first when a theme looks wrong (it holds the discovered themes and their definitions) and `classified.csv` when a label looks wrong (a sample of how the first comments were classified).

### Backend server

```bash
python -m uvicorn server:app --host 127.0.0.1 --port 8000
```

or

```bash
python server.py
```

Both start the API on `http://localhost:8000/api` and serve the frontend at `http://localhost:8000/`. The browser UI currently runs against fixture data - pipeline wiring is a later phase. The CLI (`python run.py`) is the fully working end-to-end path today. The server binds `127.0.0.1` by design - it is single-user, unauthenticated, and localhost only.

On first start, `db.init()` creates `data/app.db` with the seven required tables. Uploads land in `data/uploads/`. Run outputs go to `data/runs/<run_id>/` and `data/artifacts/<run_id>/`.

### Frontend, static only

If you want the browser demo without the backend, any static server works:

```bash
python -m http.server 8797 --bind 127.0.0.1 --directory app
```

Bind to `127.0.0.1` on a fresh port. Reusing a stale port causes `ERR_EMPTY_RESPONSE` from lingering processes. The static-only mode runs against the frontend's in-memory fixture; nothing hits the pipeline.

---

## PDF engine

The pipeline refuses to start without a PDF engine. Engines are tried in this order:

| Engine | Notes |
|---|---|
| `playwright>=1.49` | Recommended. Same output on every OS. Two steps: `pip install playwright` then `playwright install chromium`. |
| `weasyprint>=68` | Needs GTK on Windows. `68` is the floor because that release includes the fix that keeps it current with Python and CSS support. |
| `pdfkit>=1.0.0` | Thin wrapper around `wkhtmltopdf`, which installs separately and is archived upstream. Last resort. |

The HTML the report is rendered from is a build artifact, not an output. It is written to a temp file and deleted. Set `KEEP_INTERMEDIATE = True` in `config.py` to keep it, along with the source markdown.

---

## Verifying

No pytest suite for the pipeline; verify by running it end-to-end on real data. The preflight catches the common misconfigurations before you spend calls.

Frontend checks:

- `node --check app/app.js` for syntax.
- Open `app/self-check.html` in a browser for the assert-based store and state-machine checks.
- For end-to-end flows, use Playwright headless with the `webapp-testing` skill.

Backend acceptance, once keys are set:

- Server starts on `127.0.0.1:8000`.
- All seven tables exist in `data/app.db`.
- `POST /api/sessions` returns `201` with a UUID id.
- Upload endpoint rejects a bad extension with `422` and an oversized file with `413`.
- A full run on the Nike/Adidas URLs in `config-template.py` streams `brief_pause` over SSE, records brief points, unblocks on `POST /runs/{id}/proceed`, emits `complete`, and yields a downloadable `report.pdf`.

---

## Common errors

| Symptom | Cause | Fix |
|---|---|---|
| `ModuleNotFoundError: googleapiclient` | Ran with `py` instead of `python` on Windows. | Use `python run.py`. |
| `httpx.InvalidURL: Invalid non-printable ASCII character` | Tab or newline in a pasted API key. | `python -c "import config; print(repr(config.GEMINI_API_KEY))"` and re-paste. |
| `404` from Gemini | Stale model ID. | Update `MODEL` in `config.py` from Google's current model list. |
| `JSONDecodeError` during briefing | Model returned malformed or truncated JSON. | `llm.ask_json` retries once by sending it back; a hard failure usually means the response was cut off. Rerun. |
| SSL record-layer failure | Was a thread-safety bug in `collect.py`, since fixed. | If it returns, something is sharing a `googleapiclient` service across threads. |
| Empty page / `ERR_EMPTY_RESPONSE` serving `app/` | Stale processes on the port. | Bind `127.0.0.1` on a fresh port. |
| Preflight passes, PDF rendering fails | Chromium is not installed; preflight can only check the Python package. | `playwright install chromium`. |
| `409` on `POST /api/sessions/{id}/runs` | Another run is queued or running. Only one active run at a time by design. | Wait for the current one, or check `GET /api/sessions` for its status. |
