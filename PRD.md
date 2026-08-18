# Product Requirements Document

The blueprint for this project, and the record of what has shipped against it. The `docs/`
files describe the system as it stands; git history holds the root-cause narration for every
commit named below.

## Goal

YouTube Intelligence runs as one shared office service. A single Windows workstation with an
RTX 4060 Ti 16GB runs Ollama, the FastAPI backend, and the frontend. Anyone who knows the
shared password opens a public HTTPS link from any browser, creates or resumes a Session,
runs the analysis on the office GPU, and downloads the six public artifacts. YouTube
credentials, model execution, Session data, and generated files never leave the workstation.
FastAPI serves the frontend and API on loopback; Ollama serves the pinned local Qwen model on
loopback; `cloudflared` publishes FastAPI through a free quick tunnel; HTTP Basic Auth
protects every request; SQLite and local storage are the shared system of record.

## Out of scope

- Accounts, invitations, per-user ownership, roles, per-Session authorization, audit logs,
  password reset, and self-service password changes.
- A permanent or branded hostname, DNS delegation, a named tunnel, and an `@innocean.co.id`
  email rule. The operator communicates the current quick-tunnel URL out of band; nothing
  distributes it automatically.
- Router port forwarding, LAN binding, direct Ollama exposure, CORS, a second frontend
  origin, multiple concurrent analyses, a run queue, cancellation, GPU scheduling, and
  per-user quotas.
- Backups, replication, remote object storage, high availability, active-run recovery after
  a backend or workstation restart, and migration to another workstation.
- Calibrated confidence scores and confidence columns in exports. Key visuals, chat, global
  search, source discovery, OCR, custom lenses, cross-group reports, and run history.

## Locked decisions

- One model serves everything: `PipelineConfig.MODEL` is `qwen3.5:4b`, one multimodal tag
  handling both comment classification and image User Inputs. One context setting,
  `OLLAMA_NUM_CTX`, defaults to 32768. Model selection is by decision, not by benchmark: no
  F1 scoring, no answer key, no timing or quality gate.
- One merged classify pass emits theme, echoed Key Messages, one sentiment, and one emotion
  per comment. `analyze.affect` reads those columns and aggregates in Python. No encoder
  inference, and no `torch` or `transformers` at runtime.
- Affect labels are resolved words constrained by the JSON Schema `enum`, so an out-of-set
  value is unrepresentable. Sentiment is `positive`, `negative`, or `neutral`. Emotion is
  `joy`, `anger`, `sadness`, `fear`, or `other_neutral`. One sentiment per comment, not one per
  Key Message; it applies to every Key Message the comment mentions. No confidence columns in
  `comments.csv`: an LLM's self-reported confidence is not a calibrated probability.
- Product language support is Indonesian, English, and mixed Indonesian-English. Closing a
  browser tab does not stop a run; backend or computer restart recovery is out of scope. No
  backups: the workstation disk holds the only copy of `data/`. Accepted.
- Users may add and delete Key Messages during setup and `brief_pause`; the server generates
  IDs for new rows. Transcript reconciliation preserves edited messages, updates
  case-insensitive generated matches without changing IDs, keeps unmatched existing messages,
  and appends transcript additions. Label and description changes use an explicit Save
  control; inclusion and ordering save immediately.
- Access control is one shared password, enforced by HTTP Basic Auth middleware reading
  `APP_PASSWORD` from the workstation environment. The server is fail-closed: an unset or
  empty value refuses to serve rather than running open. Every authenticated person shares
  one workspace and reads every Session, upload, and report. Accepted.
- The public URL is a free Cloudflare quick tunnel
  (`cloudflared tunnel --url http://127.0.0.1:8000`), yielding an anonymous
  `*.trycloudflare.com` hostname that may change after a restart. A quick tunnel cannot carry
  an access policy, which is why the password lives in the app.
- The backend binds `127.0.0.1:8000` and serves `app/` itself. No LAN bind, no port
  forward, no CORS, no second origin. `cloudflared` connects outbound and is the only path
  in. `YOUTUBE_API_KEY` and `APP_PASSWORD` live in the workstation environment only, never
  on a client device and never in the repo.
- One GPU serves every run, so only one analysis runs at a time. `start_run` rejects a new
  run whenever any Session holds a `queued` or `running` run.

## Shared contracts

### Public deployment boundary

```text
browser -> https://<random>.trycloudflare.com -> Cloudflare quick tunnel
        -> cloudflared (outbound, on the workstation) -> http://127.0.0.1:8000
        -> FastAPI Basic Auth middleware -> static frontend or /api route
FastAPI pipeline -> http://127.0.0.1:11434 -> Ollama local model only
```

- `cloudflared` publishes only `http://127.0.0.1:8000`. It never publishes Ollama, a filesystem
  path, a second web server, or a LAN address.
- The Basic Auth middleware runs before routing, so one check protects static files, APIs,
  downloads, and SSE. Authentication answers "may this request enter?" Not ownership.
- Ollama stays loopback-only: `pipeline.llm._validated_base_url` rejects a non-loopback
  `OLLAMA_BASE_URL`.

### HTTP Basic Auth

- `APP_PASSWORD` is a non-empty shared secret. The username is not an identity; any non-empty
  username is accepted. Examples use `office`. Startup raises
  `RuntimeError("APP_PASSWORD must be set before the server can start.")` when the value is
  missing, empty, or whitespace-only.
- Every request requires `Authorization: Basic <base64(username:password)>`. Missing, malformed,
  or wrong credentials return 401 with
  `WWW-Authenticate: Basic realm="YouTube Intelligence", charset="UTF-8"`.
- The server compares passwords as UTF-8 bytes with `secrets.compare_digest`, because that
  function raises `TypeError` on a non-ASCII `str`. It never logs the header, decoded
  credentials, or the configured password. There is no unauthenticated route, development
  bypass, cookie session, logout endpoint, account record, or role.

### Article-fetch boundary

Article URLs accept public HTTP/HTTPS hosts on default ports only. The server resolves and pins
a public destination before every connection and revalidates every redirect. Any non-public or
mixed resolution returns 422 and creates no asset. An ordinary public-host timeout or extraction
failure keeps the existing empty-text asset.

### HTTP errors

Every API error is the unwrapped JSON object `ApiError`; the FastAPI handler prevents
`{ "detail": ... }` from reaching the browser. Exact `error`/`message`/`field` triples:

```ts
type ApiError = { error: string; message: string; field: string | null };

409 RUN_IN_PROGRESS  "This session already has a run in progress."               field: null
409 RUN_IN_PROGRESS  "Another analysis is already running. Wait for it to finish." field: null
422 VALIDATION_ERROR "Include at least one Key Message before continuing."       field: "messages"
422 VALIDATION_ERROR "That link points to a private address and cannot be fetched." field: "url"
404 NOT_FOUND        "Artifact not found."                                       field: null
    FEATURE_UNAVAILABLE "Key visuals are not supported."  // client-side rejection, retained shim
```

### Key Messages

```ts
type KeyMessageInput = { id: string | null; label: string; description: string; included: boolean; order: number };
type KeyMessage = KeyMessageInput & { id: string };
type KeyMessageDraft = { status: "empty" | "drafting" | "ready" | "stale" | "failed"; messages: KeyMessage[]; error: string | null; revision: number };
type SaveKeyMessagesRequest = { messages: KeyMessageInput[] };
type BriefPointInput = KeyMessageInput; type BriefPoint = KeyMessage;
```

- `POST /api/sessions/{sessionId}/key_messages/draft` reads the latest persisted User Inputs,
  preserves edited rows, keeps previous rows on failure, and coalesces concurrent requests into
  one latest rerun. Each completed pass, including empty and failed passes, increments
  `revision`; `PATCH` returns the current revision without incrementing it.
- `PATCH /api/sessions/{sessionId}/key_messages` validates the complete list before one
  transaction. `id:null` creates a server-generated UUID. Unknown, foreign, or duplicate
  non-null IDs return `422 VALIDATION_ERROR` with `field:"messages"`. Empty and all-excluded
  lists are valid during setup. Order is the submitted array order. Labels are trimmed,
  required, at most 120 characters; descriptions are trimmed, may be empty, at most 500.
- `PATCH /api/runs/{runId}/brief_points` uses the same creation rule and ownership checks.
  At least one included row is required before `POST /api/runs/{runId}/proceed` succeeds;
  that endpoint returns the current `RunSnapshot`.

### Runs and SSE

```ts
type RunStage = "queued" | "collect" | "brief" | "brief_pause" | "classify" | "emotion" | "report" | "complete" | "error";
type ProgressEvent = { stage: RunStage; pct: number; message: string; brief_points?: BriefPoint[]; error?: string };
type Artifact = { id: string; kind: string; filename: string; contentType: string; downloadUrl: string };
type RunSnapshot = {
  id: string; sessionId: string; status: "queued" | "running" | "complete" | "failed";
  stage: RunStage; pct: number; message: string; error: string | null;
  skipPause: boolean; briefPoints: BriefPoint[]; artifacts: Artifact[];
};
```

- `POST /api/sessions/{sessionId}/runs` accepts `{ skipPause?: boolean }`; omitted means
  `false`. Only one `queued` or `running` run may exist across all Sessions.
  `skipPause:true` bypasses `brief_pause` only when reconciliation leaves at least one
  included Key Message; zero included messages always pause.
- `GET /api/runs/{runId}/events` emits `ProgressEvent` fields in `snake_case`; all other HTTP
  JSON uses `camelCase`. An idle stream emits the comment frame `: heartbeat\n\n` every
  `server._SSE_HEARTBEAT_SECONDS`; comment frames never reach `EventSource.onmessage` and
  never change a snapshot.
- The initial GET snapshot renders first; a later SSE event may advance it. `complete`,
  `error`, and persisted `brief_pause` from a fresh GET override stale buffered events.
  Reopening at `brief_pause` renders `briefPoints` without waiting for SSE replay.

### Artifacts

Seven artifacts are stored in this fixed order, `kind` / `filename` / MIME. The first six
are public; `report_json` is internal and never appears in `RunSnapshot.artifacts` or Files.

1. `report_pdf` / `report.pdf` / `application/pdf`
2. `comments_csv` / `comments.csv` / `text/csv`
3. `key_messages_csv` / `key-messages.csv` / `text/csv`
4. `themes_csv` / `themes.csv` / `text/csv`
5. `sentiment_csv` / `sentiment.csv` / `text/csv`
6. `emotions_csv` / `emotions.csv` / `text/csv`
7. `report_json` / `report.json` / `application/json` - internal

`GET /api/runs/{runId}/artifacts/{artifactId}` serves public blobs with that MIME and
`Content-Disposition: attachment`. `GET /api/runs/{runId}/report` reads `report_json`.
`commentCount` counts CSV records from the latest complete run's `comments_csv`; it is 0
without a readable complete artifact, and is never parsed from `report_json`.

`ReportJson` has an exact top-level key set: `overallTransfer`, `keyMessages`, `themes`,
`emotions`, `keyMessageSentiment`, `evidence`. Python counts every number. `pipeline/report.py`
and `tests/test_evidence.py` are the authority on metric shapes, applicability, evidence
ordering, and metric-ID collision rules.

### CSVs

All CSVs use UTF-8, comma separators, a header, `\n` line endings, one-decimal percentages,
and deterministic group-first ordering. Groups follow first appearance. Labels sort by count
descending, then case-insensitive label. Empty results still write headers.

- `comments.csv`: `video_id,group,comment,likes,language,theme,sentiment,emotion`, then
  one `key_message_<stable-id>` boolean column in Key Message order.
- `themes.csv`, `sentiment.csv`, `emotions.csv`: `group,<label>,count,percent,base_n`. Eligible
  rows carry a non-null, non-empty label. `base_n` is eligible rows in the group. Zero-count
  labels are omitted.
- `key-messages.csv`:
  `group,key_message,count,percent,base_n,positive_count,positive_percent,negative_count,negative_percent,sentiment_base_n`.
  Every applicable group/message pair appears, including zero mentions. Percentages may sum
  above 100. `sentiment_base_n` counts mentioned rows with a recognized sentiment, including
  neutral. A zero denominator produces `0.0`.

## Delivery state

- `6eaa00b`, `81bfe27`: local Ollama boundary, grounded User Inputs, Session Key Message schema,
  atomic full-list PATCH, draft coalescing, brief pipeline split.
- `1a5a3e4`, `8a3de04`, `36e294b`, `1d38d34`, `8d07da3`: persisted run stages, active-run
  guard, immutable run `brief_points`, transcript reconciliation, exact CSVs, deterministic
  Report JSON, seven stored and six public artifacts.
- `f695f37`, `46cd35a`: frontend - accessible setup editor, paused-run restore, six ordered
  downloads, demo/live parity, live results rendering.
- `5bb2f9a`, `c645ced`: `CHANGELOG.md` renamed to `PRD.md`; key remediation in `config.py`.
- `ba08b23`, `c398038`: SSRF guard on article fetching, fail-closed Basic Auth, global one-run
  guard with `BEGIN IMMEDIATE`, `runs.skip_pause`, SSE keepalive, Vercel removed.
- `3ea628f`: sentiment and emotion merged into the Qwen classify pass; `torch` and
  `transformers` dropped; confidence columns removed from `comments.csv`.
- `fd05e99`, `ca76b13`, `d12d32d`: skip-pause control, benchmark machinery deleted, `qwen3.5:4b`
  pinned as the only model, `docs/deployment.md` written.
- `7f622f7`: verification debt cleared. 94 assertions across 10 test files, plus
  `tests/e2e_product_flow.py` 15/15 and `node --check` clean on both frontend files. The one
  production edit is `_SSE_HEARTBEAT_SECONDS` at module level, so a test can patch it instead
  of waiting 15 real seconds.
- Deployment is done. The user confirmed on 2026-08-19 that the workstation runs the backend
  and Ollama, the quick tunnel serves the public URL, Basic Auth gates it, and a real Session
  completes end to end on `qwen3.5:4b`. The evidence tables in `docs/deployment.md` are still
  empty, so that document is the procedure and this bullet is the record.

## Open work

**1. Commit the skip-pause end-to-end coverage.** `tests/e2e_product_flow.py` is uncommitted
with five new tests: unchecked start sends `skipPause:false` and pauses, checked start runs
straight through, checked start with zero included messages still pauses, the control is
label-associated and keyboard operable, and a failed start keeps the checked value and restores
focus. Run `python tests/e2e_product_flow.py` and require 20/20 with zero console errors, page
errors, and failed requests. Then commit. This is the last owed test file.

**2. Move to `qwen3.5:9b`.** The card holds 16GB and the pin is a 4b model. Repin
`pipeline/config_types.py:30`, `adapter.py:335`, `run.py:30`, `config-template.py:55`, the
model references in `docs/deployment.md`, and the two test constructors at
`tests/test_brief_key_messages.py:28` and `tests/test_classify.py:56`. Pull the tag, run one
real Session, and compare label quality against 4b. Watch the context budget: `OLLAMA_NUM_CTX`
is 32768 and a 9b model's KV cache is larger. Revert if quality or throughput regresses.

**3. Correct the stale documentation.** `README.md:115`, `docs/setup.md:50-56`,
`docs/setup.md:167`, and `docs/architecture.md:82` still claim the LLM is Gemini over the API
and that the Qwen swap has not landed. It landed in `ca76b13`. `README.md:94` also names an
"RTX 4060, 16 GB" where the card is a 4060 Ti. In the same pass, update `AGENTS.md`: add
`APP_PASSWORD` to the never-commit rule, and record the merged classify contract, the one-run
invariant, the Basic Auth boundary, the SSRF guard, and the loopback Ollama rule under the
do-not-change list.

## Revisions

- 2026-08-19: Contracted this file from 1094 lines, deleting the shipped Wave 1-3, A-E, and T
  packets and the verification-debt tables. Resolved four contradictions in favour of the
  code: the plan named two models (`qwen3:8b-q4_K_M`, `qwen3-vl:8b-instruct-q4_K_M`) with
  separate `TEXT_MODEL`/`VISION_MODEL` and `OLLAMA_TEXT_NUM_CTX`/`OLLAMA_VISION_NUM_CTX`, but
  `ca76b13` had collapsed each pair into one `MODEL` and one `OLLAMA_NUM_CTX` at `qwen3.5:4b`;
  VRAM read 16GB in one section and 8 GB in another, and the card is a 4060 Ti 16GB; the
  resume point named an implemented task; deployment was recorded as pending while running.
- 2026-08-19: Cleared the Wave A-C verification debt (`7f622f7`). No production behavior was
  wrong - every baseline failure was a test asserting a contract the code had already left
  behind. Two repairs closed real holes: the two-concurrent-starts assertion is the only check
  exercising the `BEGIN IMMEDIATE` fix, and the strict affect validator had only an accidental
  test.
- 2026-08-18: Shipped Waves A-C. A.1 closed the SSRF hole by connecting to the resolved literal
  address while carrying the hostname in the `Host` header and the httpx `sni_hostname`
  extension; a pre-check followed by a by-name request would have re-resolved and stayed open
  to rebinding. A.3 found the old guard checked, committed the prior-run deletes, then
  inserted, so two simultaneous requests could both pass and a rejected one could still destroy
  rows. B merged affect into the classify pass. C added the skip-pause checkbox and deleted the
  benchmark harnesses.
- 2026-08-18: Re-scoped around the office workstation. Dropped model benchmarking, F1 gates,
  answer-key annotation, and HuggingFace affect encoders. Replaced the branded Cloudflare
  Access deployment with a free rotating quick tunnel behind fail-closed Basic Auth, and
  dropped Vercel because the backend already serves the real UI from one origin.
- 2026-08-15: Locked the deployment topology. `pipeline/llm.py` rejects a non-loopback
  `OLLAMA_BASE_URL`, so co-locating Ollama with the backend is a code requirement, not a
  preference. Every Session, upload, and artifact lives on the one workstation. Removed two
  plaintext API keys from `config.py`, which now reads `YOUTUBE_API_KEY` from the environment.
- 2026-08-13 to 2026-08-15: Waves 1-3 delivered and verified at `tests/e2e_product_flow.py`
  15/15 and a 188/188 browser self-check. Authored the `webapp-testing` skill, which
  `AGENTS.md` had cited since `1b665be` without it ever being installed.
- July 2026: Built the FastAPI backend, the vanilla single-page frontend, and the Key Message
  review interrupt. Replaced regex keyword matching with LLM classification, because regex
  undercounted paraphrase and mishandled negation. Removed the background brief, where the
  model wrote what it knew about a campaign from memory: taglines, unit counts, and launch
  dates are exactly what a model invents fluently. Every claim now traces to something the
  user provided, and Python still does the counting.
