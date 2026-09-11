# Architecture

How the shipped pipeline, backend, frontend, and local model boundary fit together. Read this before changing non-trivial behavior.

Product terms are defined in [README.md](../README.md). The exact HTTP contract is [docs/api-reference.md](api-reference.md).

Related: [Setup](setup.md), [Deployment](deployment.md), [API reference](api-reference.md).

---

## System boundary

The shared deployment runs on one MacBook Pro M1 Max:

```text
external browser
  -> Cloudflare quick tunnel
  -> FastAPI 127.0.0.1:8000
  -> Basic Auth middleware
  -> frontend or /api route

FastAPI pipeline
  -> LM Studio 127.0.0.1:1234
  -> Qwen3.8-27B 4-bit MLX
```

Only FastAPI is published. LM Studio, YouTube credentials, Session data, uploads, and generated files stay on the Mac.

The application has three code areas:

1. **Pipeline** - `run.py` and `pipeline/`. YouTube and User Inputs in, labels and reports out.
2. **Backend** - `server.py`, `db.py`, `storage.py`, `assets.py`, and `adapter.py`. FastAPI exposes Sessions, User Inputs, Key Messages, runs, and files.
3. **Frontend** - `app/`. A vanilla JavaScript single-page application served by FastAPI.

`python run.py` runs the pipeline without the web product. It is a debugging entry point configured through the gitignored `config.py`.

---

## Pipeline

Four stages run in `run.py` for the CLI and `adapter.py` for the web product:

1. **collect** fetches YouTube metadata, transcripts, and comments, then cleans and filters comments.
2. **brief** drafts grounded Key Messages from transcripts and User Inputs.
3. **analyze** discovers Themes, classifies every eligible comment, and counts Theme, Key Message, Sentiment, and Emotion labels.
4. **report** writes `report.pdf`, five public CSVs, and the internal `report.json`.

Data passes between stages in memory. Debug files are write-only audit output. No pipeline stage reads them back.

### Configuration

Every pipeline module receives `pipeline.config_types.PipelineConfig`. The backend builds it in `adapter._build_config()` from Session rows and environment variables. The CLI builds it from `config.py` in `run._load_cfg()`.

The local model fields are:

- `LLM_BASE_URL`
- `LLM_MODEL`
- `LLM_CONTEXT_LENGTH`
- `LLM_TIMEOUT_SECONDS`

The backend also reads `CLASSIFY_BATCH_SIZE` from the environment. The deployment starts with a 32,768-token context and batch size 8.

### Grounded Key Messages

Key Messages come only from content supplied to the Session: titles, descriptions, transcripts, uploaded documents, article snapshots, and uploaded images. The pipeline does not ask the model what it already knows about a campaign.

Adding or deleting a User Input updates the Session draft. The server coalesces overlapping draft requests and keeps the latest requested revision. When a run starts, transcript reconciliation preserves edited rows, updates matching generated rows, keeps unmatched existing rows, and appends transcript additions.

Users can add, edit, include, exclude, delete, and reorder Key Messages during setup and at `brief_pause`. The server validates and saves the complete list atomically.

### Theme discovery and classification

`analyze.build()` reads a stratified comment sample and produces 5 to 8 Themes with definitions. The sample discovers labels only. Python computes every percentage over the full eligible corpus.

`analyze.classify()` sends batches containing the Theme book and approved Key Messages. One Qwen response assigns each comment:

- exactly one Theme;
- zero or more Key Message mentions;
- exactly one Sentiment: `positive`, `negative`, or `neutral`;
- exactly one Emotion: `joy`, `anger`, `sadness`, `fear`, or `other_neutral`.

Strict JSON Schema constrains the labels. Python validators still check exact row coverage and allowed values. `analyze.affect()` performs no inference; it validates the Sentiment and Emotion columns and aggregates them.

`analyze.extend()` performs one optional Theme top-up when the `Other` share reaches the configured threshold and minimum count. It reclassifies only those rows.

The model never produces report percentages. Python counts per-comment labels. The pipeline emits no confidence columns because model self-confidence is not a calibrated probability.

### LM Studio boundary

All model calls live in `pipeline/llm.py`. Callers use `ask()`, `ask_json()`, `classify_batch()`, and `extract_image_context()` without handling the provider wire format.

`pipeline.llm._validated_base_url()` accepts only an HTTP loopback origin without credentials, a path, a query, or a fragment. The supported deployment does not use a remote model server, an LM Studio API token, provider selection, or a cloud fallback.

Calls use non-streaming OpenAI-compatible `POST /v1/chat/completions`. Structured calls add strict JSON Schema. Image calls use base64 `data:` URLs with the original MIME type. Connection failures, timeouts, HTTP 429, and HTTP 5xx retry three times.

Preflight reads LM Studio's local inventory. It requires an exact `LLM_MODEL` match and vision support. LM Studio owns model download, loading, context allocation, and unloading.

### Collection safety

Each collection thread creates its own `googleapiclient` service. `httplib2` is not thread-safe, so service objects must never cross thread boundaries.

Transient socket and SSL failures retry three times with a fresh client. YouTube HTTP errors are not retried. One unavailable video does not fail a run if another video yields data.

---

## Backend

`server.py` serves FastAPI on `127.0.0.1:8000`. Fail-closed HTTP Basic Auth middleware runs before routing and protects static files, API routes, downloads, and SSE. `APP_PASSWORD` must be non-empty. Any non-empty username is accepted because the product has one shared workspace, not user accounts.

The backend reads `.env` before reading configuration. It never sends `YOUTUBE_API_KEY`, `APP_PASSWORD`, or model configuration to the browser.

### Storage

`data/app.db` uses stdlib SQLite in WAL mode. Eight tables hold the shared state:

| Table | Purpose |
|---|---|
| `sessions` | Session identity, timestamps, and Key Message draft state. |
| `campaigns` | One internal group row per Session. |
| `videos` | YouTube URLs and kinds. |
| `assets` | User Input metadata, extracted text, article snapshot, and upload path. |
| `key_messages` | Editable Session-level Key Message draft. |
| `runs` | Run state, persisted stage, skip-pause choice, timestamps, and error. |
| `brief_points` | Immutable run copy of the reconciled Key Messages. |
| `run_artifacts` | Stored output file records. |

`storage.py` owns paths and file writes under `data/`. The Mac disk is the only copy. Backups and active-run recovery after a process or Mac restart are out of scope.

### User Inputs

Upload routes validate extension and size, store the file, and extract document text before returning. Images retain their file path for multimodal model calls. Article routes resolve and pin a public destination before connecting, revalidate redirects, and store the fetched snapshot. Private, loopback, link-local, and mixed public/private destinations are rejected.

Draft generation reads the persisted User Inputs. A failed ordinary public article extraction may leave empty text; an SSRF rejection creates no asset.

### Run admission and lifecycle

`POST /api/sessions/{id}/runs` uses `BEGIN IMMEDIATE` to enforce one queued or running analysis across all Sessions. The server checks the global guard before deleting the target Session's prior result. A rejected start therefore preserves existing data.

The adapter thread then:

1. Loads the Session, campaign, videos, User Inputs, and Key Message draft.
2. Fetches and cleans YouTube data.
3. Reconciles transcript-derived Key Messages and copies them into run `brief_points`.
4. Enters `brief_pause`, unless `skipPause` is true and at least one Key Message remains included.
5. Re-reads the saved run Key Messages, discovers Themes, classifies comments, and aggregates labels.
6. Writes the report files and internal Report JSON.
7. Copies all seven outputs to `data/artifacts/{run_id}/` and records them.
8. Marks the run complete. An exception marks it failed.

Closing a browser tab does not stop the thread. The persisted stage restores `brief_pause` after reopening. Restarting FastAPI or the Mac loses an active run.

### Progress

SSE events carry `adapter._push()` dictionaries in `snake_case`. All other HTTP JSON uses `camelCase`. The stream emits comment heartbeats while idle and closes after a terminal event.

The persisted run stage is authoritative when a fresh GET conflicts with an old buffered event. See [API reference](api-reference.md#get-runsidevents) for the exact event contract.

### Artifacts

A completed run stores seven artifacts in fixed order:

| Kind | File | Public |
|---|---|---|
| `report_pdf` | `report.pdf` | Yes |
| `comments_csv` | `comments.csv` | Yes |
| `key_messages_csv` | `key-messages.csv` | Yes |
| `themes_csv` | `themes.csv` | Yes |
| `sentiment_csv` | `sentiment.csv` | Yes |
| `emotions_csv` | `emotions.csv` | Yes |
| `report_json` | `report.json` | No |

`server._ARTIFACT_CONTRACT` is the API source of truth for order, filename, MIME type, and visibility. `adapter._ARTIFACT_FILES` is the matching pipeline completeness check.

`RunSnapshot.artifacts` includes only the six public files. `GET /api/runs/{id}/report` reads the internal `report_json`. The artifact download route rejects the internal record.

---

## Frontend

`app/` has no framework or build step:

- `app/index.html` provides the shell.
- `app/live.js` defines `window.__liveApi` for HTTP and SSE.
- `app/app.js` contains the shared UI, demo store, route renderers, and dispatcher.
- `app/style.css` contains tokens and component styles.
- `app/self-check.html` runs browser state-machine checks.

### Live and demo isolation

A plain `/` probes `GET /api/sessions` through `window.__liveApi`. Success selects live mode. A failed or unavailable backend can fall back to the in-memory demo store.

`?demo=1` explicitly enters the committed Indomie demo and stores that choice in `sessionStorage` for the current tab. Explicit demo mode skips the probe and never delegates to `window.__liveApi`, so demo actions cannot reach the live database. A second tab opened at plain `/` remains live.

The demo replays generated artifacts and metrics from `app/demo/`. It does not run the model or call `/api`.

### Product flow

The frontend supports:

1. Create or resume a Session.
2. Add videos and User Inputs.
3. Review, edit, add, delete, include, exclude, and reorder Session Key Messages.
4. Start with optional skip-pause behavior.
5. Restore a running or paused run after reopening.
6. Review run Key Messages at `brief_pause` when required.
7. Read Report JSON and open evidence by metric.
8. Download the six ordered public artifacts.

Every number in the results view links to deterministic evidence rows. The drawer restores focus when closed and supports keyboard operation. Reduced-motion styles neutralize view, step, and drawer animation.

---

## Operational seams

| Risk | Current handling |
|---|---|
| A 27B model exhausts unified memory | Start with 4-bit MLX, 32,768 context, and batch size 8. Measure on the M1 Max before tuning. |
| Two users start together | SQLite `BEGIN IMMEDIATE` admits only one active analysis. |
| Browser closes during a run | The backend thread continues; persisted state restores the view. |
| FastAPI or Mac restarts during a run | The active run is lost. Recovery is out of scope. |
| Cloudflare quick tunnel restarts | The public URL changes. |
| Model or schema output drifts | Strict JSON Schema plus Python validation rejects invalid labels or row coverage. |
| Public article resolves privately | Resolution pinning and redirect revalidation reject the request before asset creation. |

Future multi-user deployment would require identity and authorization, durable job execution, shared object storage, backups, and a server database. None is implemented now.
