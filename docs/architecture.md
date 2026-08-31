# Architecture

What the moving parts are, how they fit together, and where the boundaries are. Read this before changing anything non-trivial.

Product terms are defined in [README.md](../README.md). The code-identifier mapping is in [AGENTS.md](../AGENTS.md#terminology).

Related: [Setup](setup.md), [API reference](api-reference.md), [PRD](../PRD.md).

---

## The three parts

1. **Pipeline** (`run.py`, `pipeline/`). The analysis engine. YouTube URLs in, report out.
2. **Backend** (`server.py`, `db.py`, `storage.py`, `assets.py`, `adapter.py`). A FastAPI wrapper exposing the pipeline over HTTP with sessions, uploads, and a run lifecycle.
3. **Frontend** (`app/`). A single-page vanilla JS app. This is the product surface.

The pipeline can run alone via `python run.py`, which is a debugging entry point and not the product. See [setup.md](setup.md#cli-debug-entry-point).

---

## Pipeline

Four stages, orchestrated by `run.py` in the CLI and by `adapter.py` in the backend:

1. **collect** fetch comments and transcripts from YouTube, clean, filter by language.
2. **brief** describe what the campaign put forward. Returns `(grounded_markdown, points)`.
3. **analyze** build the Theme book, label every comment, run the two local classifiers, count travel.
4. **report** write markdown, render PDF, export CSVs, build `report.json`.

Data passes between stages in memory. Files under `output/<session>/debug/` are write-only audit artifacts that no stage reads.

### Key Messages are grounded only

`brief.run()` returns `(grounded_markdown, points)`. There is no background brief. Every claim comes from something the user provided: transcripts, titles, descriptions, uploaded documents, and uploaded images passed to the model as multimodal parts (up to 6 per group, files over 5 MB skipped). No OCR path exists.

A model asked "what was campaign X about" produces fluent, confident detail whether or not it knows. Taglines, unit counts, and launch dates are exactly what it invents. That is why the background brief was removed and should not come back.

### Config contract

Every pipeline module receives a `PipelineConfig` dataclass (`pipeline/config_types.py`) rather than importing a global config module. The CLI builds one from `config.py`; the backend builds one from DB rows and environment variables via `adapter._build_config()`. There is no `sys.modules["config"]` shim.

The approved provider-neutral fields are `LLM_BASE_URL`, `LLM_MODEL`, `LLM_CONTEXT_LENGTH`, and `LLM_TIMEOUT_SECONDS`. The backend reads them from `.env`; the CLI reads them from the gitignored `config.py`. `pipeline/llm.py` uses standard-library HTTP and keeps no module-global provider client.

### Theme book and labelling

`analyze.build()` reads a stratified sample (150 to 500 comments, `max(CODEBOOK_SAMPLE_SIZE, 8% of corpus)` capped at `CODEBOOK_SAMPLE_MAX`) in one call and writes the Theme book: 5 to 8 Themes, each with a one-sentence definition.

The sample only ever discovers Themes. Every percentage is counted over the full corpus, so the sample does not need to be proportionally representative. It needs one example of everything worth naming, which is a much easier bar.

`analyze.classify()` sends every filtered comment to the model in batches of `CLASSIFY_BATCH_SIZE`. Each batch carries the Theme book and the approved Key Messages, and each comment comes back with exactly one Theme and zero or more Key Message mentions, in one pass. The Theme label is enum-constrained so it stays consistent across batches.

`analyze.extend()` runs one top-up pass only when the full-corpus `Other` share is at least 30% and at least 25 comments. It samples from the `Other` subset only, discovers 1 to 4 new labels, and reclassifies only those rows. The reported `other_share` is the post-recalculation value.

`analyze.summarise()` counts labels into the two tables the report reasons over: the Theme mix and Key Message travel. Themes are one per comment and sum to 100. Key Message mentions are zero-or-more per comment and do not.

Every percentage traces to a per-comment label. Labelling cost scales with corpus size; the Theme book stage is one call regardless.

### Sentiment and Emotion

The merged classification pass assigns one sentiment and one emotion with the Theme and Key Message labels. `analyze.affect()` validates those columns and counts them in Python. It runs no model.

Sentiment is `positive`, `negative`, or `neutral`. Emotion is `joy`, `anger`, `sadness`, `fear`, or `other_neutral`. The JSON Schema constrains both sets, and Python rejects an unknown value. The pipeline does not emit confidence values because an LLM's self-reported confidence is not a calibrated probability.

Labels apply to one comment without conversation-thread context. Sarcasm and measured criticism can still be misread. Reports state that the local Qwen classification pass assigned the labels.

Two figures result: an Emotion distribution across eligible comments and a per-Key-Message Sentiment split. Python computes every percentage from the per-comment labels.

### Thread safety in collect.py

`googleapiclient` sits on `httplib2`, which is not thread-safe. Sharing a service object across threads has them reading from one socket, which surfaces as SSL record-layer failures or `NoneType has no attribute read` from deep inside `http.client`.

Rule: each thread builds its own service via `collect._service()`. Never pass service objects between threads or store them outside thread-local storage.

Transient errors (socket, SSL) retry three times with backoff, discarding the thread's client each time, because a broken connection stays broken. `HttpError` is never retried: a 403 means comments are disabled, a 404 means the video is gone. One bad video does not kill the run; the run fails only if no video yields anything.

### Model access

All model calls live in `pipeline/llm.py`. Callers use `ask()`, `ask_json()`, `classify_batch()`, and `extract_image_context()` without knowing the provider wire format.

The approved deployment runs a `Qwen3.8-27B` 4-bit MLX build through LM Studio on the same Mac as FastAPI. LM Studio binds `127.0.0.1:1234`; `pipeline.llm._validated_base_url()` rejects a non-loopback `LLM_BASE_URL`. There is no cloud fallback and no remote model server.

Text and multimodal calls use non-streaming `POST /v1/chat/completions`. Structured calls send a strict JSON Schema and still pass the response through the existing Python validator. Image inputs are base64 `data:` URLs with their original MIME type. Reasoning is disabled because each pipeline call needs the requested answer or schema, not a reasoning transcript.

Preflight reads LM Studio's model inventory. It requires the exact configured model identifier and vision capability. LM Studio owns model download, loading, context allocation, and unloading. Application code does not emulate Ollama lifecycle controls.

---

## Backend

FastAPI app in `server.py` at `127.0.0.1:8000`. Single-user, unauthenticated, localhost-only by design.

```
Browser (app/)
  |  fetch + EventSource -> http://localhost:8000
  v
server.py       FastAPI: /api routes + app/ mounted as static files
  |
  +-- db.py       SQLite at data/app.db (stdlib sqlite3, WAL, no ORM)
  +-- storage.py  paths and writes: data/uploads, data/runs, data/artifacts
  +-- assets.py   .pdf/.docx/.pptx text extraction and article fetching
  +-- adapter.py  builds PipelineConfig from DB rows; runs the pipeline in a
  |               daemon thread; streams progress through a per-run queue
  +-- pipeline/   the analysis engine; only adapter.py calls it
```

The backend owns the keys. It reads them from environment variables or a `.env` file and never sends them to the browser.

### Entities

UUID v4 ids, stable once created. Seven tables in `data/app.db`:

| Table | Key fields |
|---|---|
| `sessions` | `id`, `name`, `created_at`, `updated_at` |
| `campaigns` | `id`, `session_id`, `name`. Internal. Exactly one per session |
| `videos` | `id`, `campaign_id`, `url`, `youtube_id`, `kind` (`auto`/`brand_ad`/`review`/`explainer`) |
| `assets` | `id`, `campaign_id`, `kind` (`document`/`image`/`article`), `filename`, `url`, `title`, `text`, `retrieved_at`, `file_path` |
| `runs` | `id`, `session_id`, `state` (`queued`/`running`/`complete`/`failed`), `started_at`, `finished_at`, `error` |
| `brief_points` | `id`, `run_id`, `campaign_id`, `video_id`, `label`, `description`, `approved`, `edited`, `included`, `sort_order` |
| `run_artifacts` | `id`, `run_id`, `kind`, `file_path` |

`campaigns` exists because the pipeline was built around a group concept before Sessions existed. It is never shown to the user. `POST /api/sessions/{id}/campaigns` returns `409` if one already exists, and `adapter._load_campaign()` fetches the single row.

Multi-campaign comparison is not planned. A Session is one campaign.

### Run lifecycle

`POST /api/sessions/{id}/runs` returns `202`. Before inserting the new run row it deletes all prior runs for that session (rows cascade to `brief_points` and `run_artifacts`) and calls `storage.clear_run()` on each to remove their files. There is one result per Session; a rerun overwrites the previous one. No run history, no `-2`/`-3` suffix in the backend.

The adapter thread then:

1. Builds a `PipelineConfig` from DB rows and environment variables.
2. Extracts upload text via `assets.extract_upload()` and fetches article URLs via `assets.fetch_article()`. Collects images into `images_by_group`. Results written back to `assets` rows.
3. Runs `collect.fetch()` and `collect.clean()`.
4. Runs `brief.run()`, inserts `brief_points` rows, pushes a `brief_pause` event, and blocks on a `threading.Event` until `POST /runs/{id}/proceed`.
5. Re-reads approved and edited Key Messages from the DB. Runs `analyze.build()`, `classify()`, `extend()`, `summarise()`, `affect()`.
6. Runs `report.write()`, `render()`, `export()`.
7. Builds `report.json`. `_build_evidence()` computes the numbers and evidence sampling directly from the DataFrames, with no markdown parsing. `_build_prose()` makes one dedicated model call for title, interpretation, quote, and caveat, falling back to a deterministic template if it fails.
8. Copies outputs to `data/artifacts/{run_id}/` and inserts `run_artifacts` rows.
9. Pushes `complete`. On exception, pushes `error` and sets the run to `failed`.

Step 2 moves to upload time under workstream 6 in the [PRD](../PRD.md), which also adds a `key_messages` table keyed on `session_id`.

### Artifacts

The backend registers six artifacts per run:

| `fileKind` | File | Tier |
|---|---|---|
| `report_pdf` | `report.pdf` | primary |
| `summary_csv` | `summary.csv` | primary |
| `chart_transfer_csv` | `chart_transfer.csv` | primary |
| `chart_themes_csv` | `chart_themes.csv` | primary |
| `report_json` | `report.json` | primary |
| `comments_csv` | `comments.csv` | advanced |

The CLI writes the same files minus `report.json`.

This set is wrong for the product. Workstream 4 renames the chart CSVs, drops `summary.csv`, adds `sentiment.csv` and `emotions.csv`, and keeps `report.json` as an internal file rather than a download.

### report.json

`GET /api/runs/{id}/report` returns the file directly. It is what the results screen reads, not something the user downloads. Full shape in the [API reference](api-reference.md#report-json-shape).

Keys use the older vocabulary (`transfers`, `themes`, `ideaSentiment`). They are renamed only when the export rename lands, and not before.

### Evidence

For each clickable metric, `_build_evidence()` precomputes up to 8 supporting comments ranked by likes descending, then by text length descending. Rows carry `id`, `metricId`, `text`, `emotion`, `sentiment`, `likes`. No author field; it is not collected from the API.

A fixed ranking rule rather than a selection is the point. Nobody is choosing quotes to fit a story.

### Streaming progress

`GET /api/runs/{id}/events` is a Server-Sent Events stream carrying `adapter.py`'s progress dict straight through, unserialized. That is why its payload is `snake_case` while every other response is `camelCase`. Event shape, heartbeat, and replay-on-reconnect: [API reference](api-reference.md#get-runsidevents).

`brief_pause` signals the review interrupt. The stream stays open through it; labelling resumes only after `POST /runs/{id}/proceed`.

### User Inputs

At run start, uploads with no extracted text pass through `assets.extract_upload()` (pypdf for `.pdf`, python-docx for `.docx`, python-pptx for `.pptx`; images return empty text). Article URLs with no snapshot pass through `assets.fetch_article()` (httpx with a 15 s timeout, BeautifulSoup, capped at 20,000 characters). A failed fetch returns empty text and the run continues.

Per-session asset text concatenates into the `context_map` passed to `brief.run()`. User Inputs describe what the campaign put forward. They never touch the comment side.

---

## Frontend

Single-page vanilla app. No framework, no build step. Targets desktop and tablet, 768 px and wider.

- `app/index.html` the shell.
- `app/app.js` fixture store, `demoApi` dispatcher, run engine, hash router, screen renderers, one IIFE.
- `app/live.js` `window.__liveApi`, the real `fetch` implementation, same method signatures as `demoApi`.
- `app/style.css` design tokens and per-screen styles.
- `app/self-check.html` assert-based store and state-machine checks.

### Visual language

Ink `#1A1A2E`, pink `#D6246E` (hover `#B01B5B`), pink tint `#FDEEF5`, border `#E6E6EE`, neutral `#FAFAFB`. Plus Jakarta Sans with a system fallback, no CDN.

The signature interaction: every number in the report is a dotted-underline button that opens a 400 px evidence drawer.

### Live and demo modes

Resolved once at boot:

1. `app.js` checks for `window.__liveApi`, defined by `live.js`, which `index.html` loads first.
2. If present, and the shell elements (`#view`, `#topbar`, `#overlay-root`) exist, `app.js` probes `GET /api/sessions` with a 1200 ms `AbortController` timeout. Success selects live; any failure selects demo.
3. `self-check.html` loads only `app.js`, so it never probes and always runs in demo mode.
4. `demoApi.mode` exposes `"live"` or `"demo"`.

The probe needs no CORS middleware. Live mode is reachable only when the backend serves the app on the same origin. Served standalone on another port, or over `file://`, the app falls back to demo.

In demo mode the in-memory store and a simulated run engine back every call: `connecting` -> `collect` -> `brief` -> `brief_pause`, wait for `proceedRun`, then `classify` -> `emotion` -> `report` -> `complete`. Nothing leaves the browser.

Two stage values differ from live. Demo adds `connecting` before `collect`, where SSE starts at `collect`. Demo's terminal failure stage is `failed`, where SSE emits `error`. Live maps `error` to `-2` and `running` to `-1` in `STAGE_TO_STEP`.

### Route map

| `demoApi` method | Route |
|---|---|
| `listSessions` | `GET /api/sessions` |
| `getSession` | `GET /api/sessions/{id}` |
| `createSession` | `POST /api/sessions`, then `POST /api/sessions/{id}/campaigns`, then one `POST /api/campaigns/{id}/videos` per URL |
| `getCampaign` | list sessions, match on `campaignIds`, then `GET /api/sessions/{id}` |
| `addVideo` | `POST /api/campaigns/{id}/videos` |
| `removeVideo` | `DELETE /api/videos/{id}` |
| `uploadAsset` | `POST /api/campaigns/{id}/assets/upload` |
| `addArticle` | `POST /api/campaigns/{id}/assets/article` |
| `removeAsset` | `DELETE /api/assets/{id}` |
| `getAssetData` | `GET /api/assets/{id}/file`; `null` for articles |
| `startRun` | `POST /api/sessions/{id}/runs` |
| `getRun` | `GET /api/runs/{id}` |
| `getRunningRun` | `GET /api/sessions/{id}`, returns `latestRun` when queued or running |
| `subscribeRun` | `GET /api/runs/{id}/events` (SSE) |
| `updateBriefPoints` | `PATCH /api/runs/{id}/brief_points` |
| `proceedRun` | `POST /api/runs/{id}/proceed` |
| `getReport` | `GET /api/runs/{id}/report` |
| `getArtifact` | `GET /api/runs/{id}/artifacts/{artifact_id}` (blob) |
| `listFiles` | composed client-side from all sessions' assets and complete runs' artifacts |
| `simulateDisconnect`, `simulateFailure` | demo-only, no backend equivalent |

### Screens

Seven: home and empty state, session list, new session, session detail, run progress and Key Message review, results, files.

New-session setup collects one Session. YouTube URLs are added one at a time and accept `youtube.com/watch?v=`, `youtu.be/`, and `youtube.com/shorts/`. Playlists and duplicates within a Session are rejected with a field-level message. The frontend validates and `_parse_youtube_url()` in `server.py` validates again.

Key Messages stay empty until the run reaches `brief_pause`. At the pause they become editable numbered rows with include and exclude toggles and reorder. At least one must stay included. Edits save only on "Confirm and continue".

The evidence drawer starts closed and opens on a clicked metric. It filters by All, by emotion, or by most liked, and closes on its button or Escape with focus restored.

### Known gaps between the mockup and the backend

The mockup drew a finished product. These are the honest disconnects that remain, all resolved by showing less rather than faking more.

| Gap | Resolution |
|---|---|
| The API returns no `title`, `channel`, or `commentCount` for a video | The row shows the pasted URL. The Short or Video tag derives from the URL. The "N comments available" line is gone. |
| Key Message add and delete have no route; PATCH skips unknown ids | Add and per-row delete are hidden. The review note says ideas are fixed for this run and can be excluded. |
| SSE `detail` is a string, not an object | The stepper and counters parse the known strings. Anything unparseable renders as a dash. |
| Artifact and upload blobs | Served through the artifact and asset file routes; renderers call `downloadBlob` unchanged. |
| Evidence filter pills | Derived from the emotion labels actually present in `report.evidence`, plus "Most liked". Demo keeps fixed pills. |
| A new run overwrites the previous result | Live "Run analysis" confirms before starting when the Session already has a result. |
| No live progress bar in the session list | The row shows a status pill and the latest run message. The server does not persist `pct`. |
| `KEY_VISUALS` is always empty in the backend | The key-visual toggle is hidden. `_ser_asset()` always returns `isKeyVisual: false`. Unresolved; see the PRD. |

Never make a disabled control silently do nothing. If a control cannot do what it looks like it does, it must be visibly disabled and explain why.

---

## Risks and seams

| Risk | Handling |
|---|---|
| Article fetch hangs | 15 s httpx timeout, empty text on failure, run continues. |
| pypdf returns nothing on scanned or encrypted PDFs | Warning logged, empty text returned. OCR is out of scope. |
| Fixture and live shapes drift | One dispatcher, one signature set. `self-check.html` pins the demo side; the end-to-end run pins the live side. |
| Local model drifts off the Theme book | Theme, Sentiment, Emotion, and Key Message labels are schema-constrained. Python validates exact row coverage and allowed values. |
| The 27B model exhausts unified memory | Start with a 4-bit MLX build, 32768 context, and batch size 8. Measure the real run before changing either value. |

### Team-deployment seams

Nothing is designed in, but the shape supports it:

- Auth in front of `/api`, and a `user_id` foreign key for tenancy.
- PostgreSQL in place of `db.py`.
- Celery or ARQ with Redis in place of the daemon thread and queue.
- S3-compatible storage in place of `storage.py`.
- A `cancelled` state and a `cancel_event` beside `proceed_event`, which also restores a Cancel control.