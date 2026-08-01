# Architecture

What the moving parts are, how they fit together, and where the boundaries are. Read this before changing anything non-trivial.

Related docs:
- [Setup](setup.md) - how to install and run.
- [API reference](api-reference.md) - HTTP contract.
- [README](../README.md) - short human overview.

---

## The three parts

There are three things in this repo, each with its own scope:

1. **Pipeline** (`run.py`, `pipeline/`) - the CLI. YouTube URLs in, report out. This is the only real analysis engine.
2. **Backend** (`server.py`, `db.py`, `storage.py`, `assets.py`, `adapter.py`) - a FastAPI wrapper that exposes the pipeline over HTTP with sessions, campaigns, uploads, and a run lifecycle.
3. **Frontend** (`app/`) - a single-page vanilla JS demo. It runs entirely against an in-memory fixture layer and does not yet call the backend, whether or not the server is running. Wiring it to the API is a later phase.

The pipeline can be used alone via `python run.py`. The backend wraps it. The frontend is the user-facing surface.

---

## Pipeline

Five stages, orchestrated by `run.py`:

1. **collect** - fetch comments and transcripts from YouTube, clean, filter by language.
2. **brief** - describe what each video put forward. Returns `(grounded_markdown, points)`. Grounded only; every claim traces back to transcripts, titles, descriptions, user-supplied articles/documents, and user images.
3. **analyze** - discover a codebook, LLM-label every comment, run both affect models, measure signal transfer.
4. **report** - write markdown, render PDF, export CSVs, build `report.json`.

Data passes between stages in memory. Files in `output/<session>/debug/` are write-only audit artifacts that no stage reads.

### Brief - grounded only

`brief.run()` returns `(grounded_markdown, points)`. There is no background brief. Every claim in the brief comes from something the user provided: transcripts, titles, descriptions, user-supplied articles and documents (text), and user-uploaded images passed as multimodal parts to Gemini (up to 6 images per group; files larger than 5 MB are skipped). No OCR path is used for images.

### Config contract

Every pipeline module receives a `PipelineConfig` dataclass (defined in `pipeline/config_types.py`) instead of importing a global config module. The CLI builds a `PipelineConfig` from `config.py`; the backend builds one from DB rows and environment variables via `adapter._build_config()`. There is no `sys.modules["config"]` shim.

`llm.py` has no module-global client. `_get_client(cfg)` constructs one per API key, cached by key value within a process.

### Codebook and classification

`analyze.build()` reads a stratified sample (150-500 comments) in a single LLM call and writes a codebook: 5-8 themes, each with a one-sentence definition. No keywords.

`analyze.classify()` sends every filtered comment to the LLM in batches (`CLASSIFY_BATCH_SIZE` per call). Each comment gets exactly one theme and zero or more echoed brief-point labels, all in one pass.

`analyze.extend()` runs one top-up pass only when the full-corpus "Other" share is >= 30% AND >= 25 comments. It samples from the Other subset only, discovers 1-4 new labels, and reclassifies only those Other rows. The reported `other_share` is the post-recalculation value.

`analyze.summarise()` counts labels into the two tables the report reasons over: theme mix and signal transfer. Themes are one per comment; brief-point echoes are zero-or-more per comment. Signal transfer = share of comments echoing a given brief point.

Every percentage in the report traces to a per-comment label. Cost scales with corpus size; the codebook stage is one call regardless.

### Affect (emotion and sentiment)

`analyze.affect()` always runs both models: an emotion model and a sentiment model, sequentially, over the full analysis base. There is no mode toggle. Each comment receives `emotion`, `emotion_confidence`, `sentiment`, and `sentiment_confidence` columns. Labels are the raw model output with no remapping.

Both models are local HuggingFace models, no tokens. Labels are assigned per comment without surrounding context, so sarcasm and measured criticism both read as anger. The pipeline tracks the share of low-confidence labels and pipes that caveat into the report prompt so numbers cannot be presented without it.

Two figures result: an emotion distribution across all comments, and a per-idea sentiment split (positive/neutral/negative share for each brief point's echo set).

The theme mix is the better answer to "how was this received". Affect answers "the client asked for sentiment/emotion".

### Thread-safety constraint

`googleapiclient` sits on `httplib2`, which is not thread-safe. Sharing a service object across threads has them reading from one socket, which surfaces as SSL record-layer failures or `NoneType has no attribute read` from deep inside `http.client`.

Rule: each thread builds its own service via `collect._service()`. Never pass service objects between threads or store them outside thread-local storage. Preserve this pattern when extending `collect.py`.

Transient errors (socket, SSL) retry three times with backoff, discarding the thread's client each time. `HttpError` is never retried; a `403` means comments are disabled and a `404` means the video is gone. One bad video does not kill the run.

### Model access

All model calls live in `pipeline/llm.py`. Swap provider by editing that file. Current setup is Gemini free tier.

Two warnings apply to the free tier: prompts may be used to train the provider's models (use a paid tier or Vertex AI for confidential work), and model IDs change every few months.

---

## Backend

FastAPI app in `server.py` at `127.0.0.1:8000`. Single-user, unauthenticated, localhost-only by design.

```
Browser (app/)
  |  fetch + EventSource -> http://localhost:8000
  v
server.py       FastAPI: /api routes + app/ mounted as static files
  |
  +-- db.py     SQLite at data/app.db (stdlib sqlite3, WAL, no ORM)
  +-- storage.py  paths and writes: data/uploads, data/runs, data/artifacts
  +-- assets.py   .pdf/.docx/.pptx text extraction and article fetching
  +-- adapter.py  builds PipelineConfig from DB rows; runs pipeline in a
  |               daemon thread; streams progress via a per-run queue
  +-- pipeline/   existing CLI code; only adapter.py calls it
```

The backend owns the API keys. It reads them from environment variables (or a `.env` file at repo root) and never sends them to the browser.

### Entities

UUID v4 ids, stable once created. Seven tables in `data/app.db`:

| Entity | Key fields |
|---|---|
| `sessions` | `id`, `name`, `created_at`, `updated_at` |
| `campaigns` | `id`, `session_id`, `name` |
| `videos` | `id`, `campaign_id`, `url`, `youtube_id`, `kind` (`auto`/`brand_ad`/`review`/`explainer`) |
| `assets` | `id`, `campaign_id`, `kind` (`document`/`image`/`article`), `filename`, `url`, `title`, `text`, `retrieved_at`, `file_path` |
| `runs` | `id`, `session_id`, `state` (`queued`/`running`/`complete`/`failed`), `started_at`, `finished_at`, `error` |
| `brief_points` | `id`, `run_id`, `campaign_id`, `video_id`, `label`, `description`, `approved`, `edited`, `included`, `sort_order` |
| `run_artifacts` | `id`, `run_id`, `kind`, `file_path` |

Frontend fixtures mirror these in camelCase; see the [API reference](api-reference.md) for the exact serialization shapes.

### One campaign per session

`POST /api/sessions/{id}/campaigns` returns `409` if a campaign already exists for that session. `adapter._load_campaign()` fetches the single campaign row. The pipeline processes exactly that one campaign.

### Run lifecycle

`POST /api/sessions/{id}/runs` returns `202`. Before inserting the new run row, it deletes all prior runs for that session (DB rows cascade to `brief_points` and `run_artifacts`) and calls `storage.clear_run()` on each to remove their files from `data/runs/<id>` and `data/artifacts/<id>`. There is one result per session; reruns overwrite the previous one. There is no run history and no `-2`/`-3` suffix behavior in the backend.

The adapter thread then executes:

1. Builds a `PipelineConfig` from DB rows and environment variables via `_build_config()`.
2. Extracts upload text via `assets.extract_upload()` and fetches article URLs via `assets.fetch_article()`. Collects image assets into `images_by_group` (max 6 per group, files > 5 MB skipped). Results written back to `assets` rows.
3. Runs `collect.fetch()` and `collect.clean()`.
4. Runs `brief.run(meta_df, cfg, context_map, images_map)`, then inserts `BriefPoint` rows and pushes a `brief_pause` progress event. Blocks on a `threading.Event` until `POST /runs/{id}/proceed`.
5. Re-reads approved/edited brief points from DB. Runs `analyze.build()` -> `analyze.classify()` -> `analyze.extend()` -> `analyze.summarise()` -> `analyze.affect()`.
6. Runs `report.write()`, `report.render()`, `report.export()`.
7. Builds `report.json` via `_build_report_json()`: `_build_evidence()` computes transfer/theme numbers and evidence sampling directly from the DataFrames (no markdown parsing); `_build_prose()` makes one dedicated Gemini call for title/interpretation/quote/caveat, falling back to a deterministic template if that call fails.
8. Copies outputs to `data/artifacts/{run_id}/` and inserts `run_artifacts` rows.
9. Pushes `complete`. On exception: pushes `error` and sets `Run.state = "failed"`.

### report.json - frontend data contract

`GET /api/runs/{id}/report` returns the contents of `report.json` directly. Keys:

| Key | Description |
|---|---|
| `runId` | run UUID |
| `title` | two-line finding title |
| `subtitle` | comment count, video count, theme count, languages |
| `overallTransfer` | % of base comments echoing at least one brief point |
| `transfers` | per-brief-point transfer percentages |
| `themes` | theme distribution |
| `emotions` | emotion label distribution (raw model labels) |
| `ideaSentiment` | per-brief-point positive/neutral/negative split |
| `interpretation` | prose summary |
| `quote` | single representative verbatim comment |
| `caveat` | limitations including affect model caveat |
| `evidence` | up to 8 supporting comments per metric, ranked by likes then length |

The five downloadable artifacts (`report.pdf`, `comments.csv`, `summary.csv`, `chart_transfer.csv`, `chart_themes.csv`) are tiered primary/advanced. `comments.csv` is advanced.

### Evidence

For each clickable metric (theme %, transfer %, per-idea sentiment), `_build_evidence()` precomputes up to 8 supporting comments ranked by likes descending then text-length descending. Evidence rows contain `id`, `metricId`, `text`, `emotion`, `sentiment`, `likes`. No author field (not collected from the API).

### Streaming progress (SSE)

`GET /api/runs/{id}/events` is a Server-Sent Events stream carrying `adapter.py`'s internal progress dict straight through, unserialized - this is why the payload is `snake_case` while every other response is `camelCase`. Full event shape, heartbeat, and replay-on-reconnect behavior: [API reference](api-reference.md#get-runsidevents).

`brief_pause` signals the review interrupt. The stream stays open through the pause; classification resumes only after `POST /runs/{id}/proceed`.

### Assets and evidence

When a run starts, uploads with no extracted text yet pass through `assets.extract_upload()` (pypdf for `.pdf`, python-docx for `.docx`, python-pptx for `.pptx`; images return empty text). Article URLs with no snapshot yet pass through `assets.fetch_article()` (httpx with a 15 s timeout, BeautifulSoup, capped at 20 000 characters).

Per-campaign asset text concatenates into the `context_map` passed to `brief.run()`. The review covers what campaigns put forward, not what commenters said.

`cfg.KEY_VISUALS` is always `{}` in the backend; `_ser_asset()` always returns `isKeyVisual: false` (see the `ponytail:` comment in `server.py`). The frontend's key-visual picker (`setKeyVisual`, the asset toggle button) is a frontend-only stub with no backing route - see the mockup-vs-backend disconnects table below.

---

## Frontend

Single-page vanilla app. No framework, no build step.

- `app/index.html` - the shell.
- `app/app.js` - fixture store, `demoApi`, run engine, hash router, all screen renderers as one IIFE.
- `app/style.css` - pink design tokens and per-screen styles.
- `app/self-check.html` - assert-based store and state-machine checks.

Targets desktop and tablet (768 px and wider).

The frontend is still fixture-only and does not yet call the backend. Wiring `demoApi` bodies to real fetch calls is a later phase.

### Fixture layer and the backend swap

`app/app.js` reads and writes through a single object called `demoApi`. Every method is async and returns copies of store data. When the backend is running, each method should be replaced with a `fetch` call to the corresponding route; the method signatures stay the same so call sites do not change.

The route map:

| `demoApi` method | Target route |
|---|---|
| `listSessions` | `GET /api/sessions` |
| `getSession` | `GET /api/sessions/{id}` |
| `createSession` | `POST /api/sessions` then `POST /api/sessions/{id}/campaigns` then `POST /api/campaigns/{id}/videos` |
| `getCampaign` | `GET /api/sessions/{id}/campaigns` (single) |
| `addVideo` | `POST /api/campaigns/{id}/videos` |
| `removeVideo` | `DELETE /api/videos/{id}` |
| `uploadAsset` | `POST /api/campaigns/{id}/assets/upload` |
| `addArticle` | `POST /api/campaigns/{id}/assets/article` |
| `removeAsset` | `DELETE /api/assets/{id}` |
| `startRun` | `POST /api/sessions/{id}/runs` |
| `getRun` | `GET /api/runs/{id}` |
| `subscribeRun` | `GET /api/runs/{id}/events` (SSE) |
| `updateBriefPoints` | `PATCH /api/runs/{id}/brief_points` |
| `proceedRun` | `POST /api/runs/{id}/proceed` |
| `getReport` | `GET /api/runs/{id}/report` |
| `getArtifact` | `GET /api/runs/{id}/artifacts/{artifact_id}` |
| `listFiles` | composed client-side from artifacts and assets |
| `getAssetData`, `getRunningRun` | demo-only store accessors |
| `setKeyVisual` | frontend-only stub; no backing route yet |
| `simulateDisconnect`, `simulateFailure` | demo-only, no backend equivalent |

### Run engine (demo mode)

In fixture mode the run engine holds all the timing. `startRun` mints a fresh run id. Flow: `connecting` -> `collect` -> `brief` -> `brief_pause`, wait for `proceedRun`, then `classify` -> `emotion` -> `report` -> `complete`.

The report and its artifacts are created only at `complete`. `subscribeRun` returns an unsubscribe function and guards against duplicate timers and listeners. `Run.stage` mostly matches the SSE stages so swapping in `EventSource` is mechanical, but two values differ: the demo adds a `connecting` stage before `collect` (SSE starts directly at `collect`), and the demo's terminal failure stage is `failed` where SSE emits `error`. Account for both when wiring the real `EventSource` handler.

### Screens

Seven screens: **Home / empty state**, **Session list**, **New session**, **Campaign detail**, **Run progress and brief review**, **Results**, **Files**.

New-session setup collects one campaign; additional campaigns are not supported (enforced by the backend). Video metadata in the demo is labelled as demo data and generated after URL validation, not fetched.

Brief points stay empty until the run hits `brief_pause`. At the pause they become editable numbered rows with include/exclude toggles, reorder, delete, and add. At least one must remain included; edits save only on "Confirm and continue".

The results screen's evidence drawer starts closed and opens on a clicked metric. It filters by All / emotion / Most liked, closes on its button or Escape with focus restored. Downloads produce valid CSV blobs and a valid minimal PDF blob, both labelled demo data in fixture mode.

### URL validation

YouTube URLs are added one at a time. Accepted forms:

- `youtube.com/watch?v=...`
- `youtu.be/...`
- `youtube.com/shorts/...`

Playlists (`?list=...`) and duplicates within a campaign are rejected with a field-level message. The frontend enforces this against `demoApi` responses in fixture mode; the backend enforces it in `_parse_youtube_url()` in `server.py`.

### Mockup-vs-backend disconnects

The mockup drew a finished product; the backend supports a subset. Every gap resolves one of four ways:

- **Disabled-and-visible**: Chat, global search, "Save as draft", source discovery, settings. Native `disabled`, out of tab order, with an explanatory note. Re-enable only when a backing route lands.
- **Removed**: Cancel run, share, email notifications, lens controls.
- **Frontend-only stub**: key-visual selection (no route yet), OCR note (uploads keep the file, no OCR happens).
- **Corrected**: per-metric CSV export replaced with the full `comments.csv` link; upload types now match what the backend accepts (`.pdf`, `.pptx`, `.docx`, `.png`, `.jpg`, `.jpeg`, `.webp`); background brief gone (backend and CLI both grounded-only now); one campaign per session enforced.
- **Not yet wired**: the frontend fixture layer does not call the backend. Emotion label vocabulary in fixtures may not match the raw labels the HuggingFace models return. Both gaps remain open.

Never make a disabled control silently do nothing. If a control cannot do what it looks like it does, it must be visibly disabled and explain why.

---

## What not to change without a reason

- Thread-local service pattern in `collect.py` (see thread-safety above).
- Preflight checks in `run.py`.
- `PipelineConfig` as the config contract. Every pipeline module receives a `PipelineConfig` argument. Do not reintroduce a global config module or a `sys.modules["config"]` shim.
- PDF requirement. HTML is a build artifact, not an output.
- `demoApi` method signatures in `app/app.js`. Change bodies, keep signatures.
- Disabled-feature honesty in the frontend.
- Per-session overwrite in `server.py`. Starting a new run deletes all prior runs and their files for that session.

---

## Design constraints, summarized

- **LLM labels every comment against a fixed theme set.** The codebook is discovered from a sample and applied to the full corpus by LLM, not by regex.
- **Brief is grounded only.** Every claim traces back to user-provided content: transcripts, descriptions, uploaded documents, and uploaded images passed as multimodal parts.
- **Affect runs locally.** Both emotion and sentiment models run per-comment with no context; sarcasm reads as anger. The caveat travels into the report.
- **Runs overwrite.** Starting a new run deletes the session's prior run and its files. No run history.
- **One campaign per session.** Enforced at the API layer.
- **Uploads are text or image.** Documents (`.pdf`, `.docx`, `.pptx`) get text-extracted at run start; images pass as multimodal parts to the brief, no OCR.
- **Article URLs are snapshotted at run start.** A failed fetch returns empty text; the run continues.
- **Reports sample evidence.** The drawer shows up to 8 comments per metric; `comments.csv` is the full export.

---

## Risks and seams

| Risk | Handling |
|---|---|
| Article fetch hangs | 15 s httpx timeout; empty text on failure; run continues. |
| pypdf empty on encrypted or scanned PDFs | Warning logged, empty text returned. OCR is out of scope. |
| Fixture and backend contracts drift | Route map above is the mapping. Keep `demoApi` signatures stable when swapping bodies. |
| Affect label vocabulary mismatch | Fixture layer uses placeholder labels; real model returns raw HuggingFace labels. Align when wiring the frontend. |

### Team-deployment seams

Nothing designed in now, but the shape supports:

- Auth in front of `/api`; a `user_id` FK on the tables for tenancy.
- PostgreSQL in place of `db.py`.
- Celery/ARQ + Redis in place of the daemon thread and queue.
- S3-compatible storage in place of `storage.py`.
- A `cancelled` state and a `cancel_event` beside `proceed_event`, which also restores the removed Cancel control.

---

## Deferred features

Each corresponds to a disabled or removed frontend control.

- **OCR** (unblocks the OCR note): in `assets.extract_upload()`, when pypdf yields under 100 characters from a PDF, fall back to `pytesseract` or `easyocr`. Add `ocr_used: bool` to the upload response and a badge on that asset.
- **Custom lenses** (unblocks lens controls): add `lens` and `lens_prompt` columns to `sessions`. Restore the four lens cards, wire them on `POST /api/sessions`. Pass `lens` to `brief.py` via `PipelineConfig`.
- **Cross-session assistant** (unblocks Chat and Source discovery): new tables `conversations` and `conversation_turns`; routes `POST /api/conversations`, `GET /api/conversations`, `POST /api/conversations/{id}/turns`. Turns load `report_json` and chart CSVs for scope, send them to Gemini with a system prompt forbidding fabricated numbers, and stream via SSE. Reads reports and charts only, never raw comments.
