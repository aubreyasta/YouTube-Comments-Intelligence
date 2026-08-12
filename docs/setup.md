# Setup

Getting a working install, keys in the right place, and the app running.

Related: [Architecture](architecture.md), [API reference](api-reference.md), [README](../README.md).

---

## Prerequisites

- Python 3.10 or newer.
- A Conda environment is recommended. The repo is developed against one named `YouTubeIntelligence`, but any isolated environment works.
- Windows, macOS, or Linux. Windows-only gotchas are flagged below.

Check the interpreter before running anything:

```bash
python -c "import sys; print(sys.executable)"
```

On Windows use `python`, never `py`. `py` picks the system Python and ignores the active environment, which produces confusing `ModuleNotFoundError` failures.

---

## Install

```bash
pip install -r requirements.txt -r requirements-server.txt
playwright install chromium
```

The Chromium download is not a Python package, so `pip` cannot check it. If preflight passes but PDF rendering fails later, this is why.

Sentiment and Emotion classification pull roughly 500 MB of HuggingFace models on first run. Both run locally and cost nothing per run.

---

## Configuration

The backend reads configuration from the process environment or a `.env` file at the repo root. `.env` is gitignored. The browser never sees any of it.

```
YOUTUBE_API_KEY=...
GEMINI_API_KEY=...
GEMINI_MODEL=...
```

`YOUTUBE_API_KEY` is a YouTube Data API v3 key. It fetches comments and video metadata and stays required.

### About the Gemini keys

**Gemini is a debugging stand-in, not the product.** The shipping model is a local Qwen served by Ollama, so that no client material leaves the building and no run costs anything. The Gemini path exists because the swap has not landed yet. See workstream 2 in the [CHANGELOG](../CHANGELOG.md).

Two things follow from that. Do not build anything new against the Gemini client; `pipeline/llm.py` is the only file that should know a provider exists. And do not put confidential client material through it, because free-tier prompts may be used to train the provider's models.

`GEMINI_MODEL` names a model ID. Google renames and retires models every few months, so a `404` from Gemini means the ID is stale. Set it explicitly rather than relying on the built-in fallback, which was current when it was written and will not stay that way.

### Pasted keys with hidden characters

A tab or newline riding along with a copy-pasted key produces:

```
httpx.InvalidURL: Invalid non-printable ASCII character
```

Check the value with `repr`, which shows the escape codes:

```bash
python -c "import os; print(repr(os.environ['GEMINI_API_KEY']))"
```

---

## Running

```bash
python -m uvicorn server:app --host 127.0.0.1 --port 8000
```

or

```bash
python server.py
```

Both start the API at `http://localhost:8000/api` and serve the app at `http://localhost:8000/`. At boot the frontend probes `GET /api/sessions` from the same origin; when the probe succeeds it switches to live mode and starting a run invokes the real pipeline, streaming progress over SSE.

The server binds `127.0.0.1` by design. It is single-user, unauthenticated, and localhost only.

On first start, `db.init()` creates `data/app.db` with its tables. Uploads land in `data/uploads/`. Run outputs go to `data/runs/<run_id>/` and `data/artifacts/<run_id>/`.

### Frontend without the backend

Serve `app/` from any static server:

```bash
python -m http.server 8797 --bind 127.0.0.1 --directory app
```

The backend probe is cross-origin from there and fails, so the app stays in demo mode for the whole session. Data is in-memory fixture content, the run engine simulates progress on fixed timing, and nothing reaches YouTube or a model. Everything resets on refresh.

Bind `127.0.0.1` on a fresh port. Reusing a stale one causes `ERR_EMPTY_RESPONSE` from lingering processes.

### CLI debug entry point

`python run.py` runs the pipeline without the web layer, configured through `config.py` (copy `config-template.py` if it does not exist; it is gitignored).

This is for debugging the pipeline in isolation. It is not the product, it is not what anyone at the agency uses, and it is not maintained to the same standard as the server path. It writes `report.pdf`, `comments.csv`, `summary.csv`, `chart_transfer.csv`, and `chart_themes.csv` into `output/<session-name>/`, with `-2` and `-3` suffixes on repeat runs.

`run.py` starts with a preflight check. Anything missing is reported all at once, before any API call or model download:

- both keys set
- `VIDEOS` not empty
- `transformers` importable
- a PDF engine installed

Preflight then prints the configured `EMOTION_MODEL` and `SENTIMENT_MODEL`. Both always run; there is no flag to disable either.

`KEEP_INTERMEDIATE = True` also writes `output/<session>/debug/` with the Theme book, the Key Message drafts, and intermediate markdown. No stage reads these; they exist for auditing. Check `codebook.json` first when a Theme looks wrong, and `classified.csv` when a label looks wrong.

---

## PDF engine

The pipeline refuses to start without one. Engines are tried in order:

| Engine | Notes |
|---|---|
| `playwright>=1.49` | Recommended. Same output on every OS. Installed via `requirements.txt`; run `playwright install chromium` once. |
| `weasyprint>=68` | Needs GTK on Windows, which is the painful part. |
| `pdfkit>=1.0.0` | Thin wrapper around `wkhtmltopdf`, which installs separately and is archived upstream. Last resort. |

The HTML the report renders from is a build artifact, not an output. It goes to a temp file and is deleted unless `KEEP_INTERMEDIATE` is on.

---

## Verifying

There is no pytest suite covering the pipeline end to end. Two assert-based scripts live under `tests/`:

- `tests/test_classify.py` labelling behaviour.
- `tests/test_evidence.py` evidence selection and ranking.

Run them directly with `python`. Beyond that, verify by running the pipeline on real data. Preflight catches the common misconfigurations before you spend a run.

Frontend:

- `node --check app/app.js` for syntax.
- Open `app/self-check.html` in a browser for the store and state-machine checks.
- For end-to-end flows, Playwright headless with the `webapp-testing` skill.

Backend acceptance, once keys are set:

- Server starts on `127.0.0.1:8000`.
- `POST /api/sessions` returns `201` with a UUID.
- The upload endpoint rejects a bad extension with `422` and an oversized file with `413`.
- A full run streams `brief_pause` over SSE, records Key Messages, unblocks on `POST /runs/{id}/proceed`, emits `complete`, and yields a downloadable `report.pdf`.

---

## Common errors

| Symptom | Cause | Fix |
|---|---|---|
| `ModuleNotFoundError: googleapiclient` | Ran with `py` instead of `python` on Windows | `python run.py` |
| `httpx.InvalidURL: Invalid non-printable ASCII character` | Tab or newline in a pasted key | Check with `repr`, re-paste |
| `404` from Gemini | Stale model ID | Set `GEMINI_MODEL` from the current model list |
| `JSONDecodeError` during the brief stage | Model returned malformed or truncated JSON | `llm.ask_json` retries once. A hard failure usually means the response was cut off. Rerun |
| SSL record-layer failure | Something is sharing a `googleapiclient` service across threads | Each thread builds its own via `collect._service()` |
| `ERR_EMPTY_RESPONSE` serving `app/` | Stale processes on the port | Bind `127.0.0.1` on a fresh port |
| Preflight passes, PDF rendering fails | Chromium not installed; preflight only sees the Python package | `playwright install chromium` |
| `409` on `POST /api/sessions/{id}/runs` | Another run is queued or running | Wait, or check `GET /api/sessions` |