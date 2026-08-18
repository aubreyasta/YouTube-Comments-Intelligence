# Product Requirements Document

The blueprint for this project, and the record of what has shipped against it. Newest first. The `docs/` files describe the system as it stands.

Entry format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). This project is not versioned; entries sit under dated releases or `[Unreleased]` for planned and in-progress work.

---

## [Unreleased]

This is the execution brief for the final product. It is a contract, not a list of suggestions. An implementation agent reads `AGENTS.md`, one task below, and only that task's listed files and named prerequisite outputs. It does not scan the repository.

### Goal

Deploy the existing YouTube Intelligence web app on one office Windows workstation with an RTX 4060 Ti 16GB. Let any person who knows the shared password open a public HTTPS link from any browser, create or resume a Session, run the analysis on the office GPU, and download the six public artifacts. Keep YouTube credentials, model execution, Session data, and generated files on the workstation.

Ship one deliberately simple operating shape: FastAPI serves the frontend and API on loopback; Ollama serves pinned local Qwen models on loopback; `cloudflared` publishes FastAPI through a free quick tunnel; HTTP Basic Auth protects every request; SQLite and local storage remain the shared system of record.

Done is observable when an external client on another network authenticates through the generated `*.trycloudflare.com` URL, completes one real Session, restores it after closing the tab, downloads all six artifacts, and repeats access after a workstation reboot according to the recorded quick-tunnel startup procedure.

### Out of scope

- Model benchmarking, model comparison, F1 scoring, answer-key annotation, private benchmark corpora, benchmark result files, and benchmark-driven release gates.
- Accounts, invitations, per-user ownership, roles, per-Session authorization, audit logs, password reset, and self-service password changes.
- A permanent or branded hostname, DNS delegation, a named Cloudflare tunnel, Cloudflare Access, an `@innocean.co.id` email rule, and Vercel.
- Router port forwarding, LAN binding, direct Ollama exposure, CORS, and a second frontend origin.
- Multiple concurrent analyses, a run queue, cancellation, GPU scheduling, and per-user quotas.
- Backups, replication, remote object storage, high availability, active-run recovery after backend/workstation restart, and automatic migration to another workstation.
- Automatic distribution of a rotated quick-tunnel URL. The operator communicates the current URL out of band.
- HuggingFace affect encoders, `torch`, `transformers`, calibrated confidence scores, and confidence columns in exports.
- Key visuals, chat, global search, source discovery, OCR, custom lenses, cross-group reports, and run history.

### Locked decisions

- Model selection is by decision, not by benchmark. There is no F1 scoring, no answer-key labelling, no private benchmark corpus, and no timing/quality gate in this product. Benchmarking was removed on 2026-08-18; see the matching Revisions entry.
- The shipping text model is `qwen3:8b-q4_K_M` (~5GB), pinned by decision. It fits 16GB with room for KV cache on long comment batches. The vision model is `qwen3-vl:8b-instruct-q4_K_M`, loaded on demand for image User Inputs. Ollama loads one at a time, so 16GB is never over-committed.
- Sentiment and emotion are produced by the merged Qwen classify pass, not by HuggingFace encoders. One model pass over each batch emits theme, echoed Key Messages, one sentiment, and one emotion per comment. `analyze.affect` reads those columns instead of running encoder inference. `torch` and `transformers` are removed as runtime dependencies.
- `sentiment_confidence` and `emotion_confidence` are removed from `comments.csv`. An LLM's self-reported confidence is not a calibrated probability, and there is no encoder score to report.
- Sentiment is one label per comment, not one per Key Message. A comment's single sentiment applies to every Key Message it mentions.
- The merged classify pass emits resolved affect labels directly, constrained by the JSON schema `enum`, not single-character codes. Sentiment is one of `positive`, `negative`, `neutral`. Emotion is one of `joy`, `anger`, `sadness`, `fear`, `other_neutral`, where `other_neutral` is the catch-all when no clear emotion reads. The schema makes an out-of-set value unrepresentable, so there is no invalid-code path. `report.py` already recognizes `positive`/`negative`/`neutral` for sentiment aggregation; the emotion labels are counted as opaque strings. The benchmark's single-character code map is retired with benchmarking.
- Product language support is Indonesian, English, and mixed Indonesian-English.
- Closing a browser tab does not stop a run. Backend or computer restart recovery is out of scope.
- Users may add and delete Key Messages during setup and `brief_pause`. The server generates IDs for new rows.
- Transcript reconciliation preserves edited messages, updates case-insensitive generated matches without changing IDs, keeps unmatched existing messages, and appends transcript additions.
- Label and description changes use an explicit Save control. Inclusion and ordering save immediately.
- Key visuals, chat, global search, source discovery, OCR, custom lenses, cross-group reports, run history, and cancellation are not part of the final product.
- The product ships as one shared office instance reachable over the public internet, not a per-device install and not localhost-only. One workstation (RTX 4060 Ti 16GB) runs Ollama, the FastAPI backend, and the frontend. Every client is a browser anywhere. Recorded in full under Waves A and D.
- Access control is one shared password, enforced in the app by HTTP Basic Auth middleware reading `APP_PASSWORD` from the workstation environment. The server is fail-closed: if `APP_PASSWORD` is unset or empty, the process refuses to serve rather than running open. No accounts, no per-user isolation, no per-Session authorization. Every authenticated person shares one workspace and reads and downloads every Session, upload, and report. Accepted.
- The public URL is a free Cloudflare quick tunnel (`cloudflared tunnel --url http://127.0.0.1:8000`), which yields an anonymous `*.trycloudflare.com` hostname. A quick tunnel cannot carry a Cloudflare Access policy, which is why the password lives in the app. There is no branded hostname, no DNS delegation, and no IT dependency. Cloudflare Access and the `@innocean.co.id` email gate are dropped.
- The backend keeps binding `127.0.0.1:8000` and keeps serving `app/` itself. No LAN bind, no router port forward, no CORS, no second origin. `cloudflared` connects outbound from the workstation and is the only path in.
- No backups. The workstation disk holds the only copy of `data/`. Accepted.
- `YOUTUBE_API_KEY` and `APP_PASSWORD` live in the workstation environment only, never on a client device and never in the repo.
- A single GPU serves every run, so only one analysis runs at a time. `start_run` rejects a new run whenever any Session holds a `queued` or `running` run.
- The 3,000-comment-in-2-hours figure is a one-time real-run sanity check recorded during deployment, not a release gate. Nothing in the product enforces it.

### Delivery state

Full root-cause narration for each commit below lives in that commit's message. This section keeps outcomes and evidence counts only.

- Commit `6eaa00b`: local Ollama boundary, doc realignment, Session Key Message schema, atomic PATCH foundation.
- Commit `81bfe27`: setup drafting, draft coalescing/stale state, asset extraction coverage, brief pipeline split, benchmark harnesses.
- Commits `1a5a3e4`, `8a3de04`: Task 2.2 done. Exact CSV contract exported; old chart/summary CSVs removed.
- Commit `36e294b`: Task 2.1 done. `runs.stage` migration, active-run guard, Key Message snapshot/reconciliation, unified run/error contract. Verified post-rebase: py_compile clean, `test_run_key_messages.py` 10/10, `test_key_messages_patch.py` 8/8, `git diff --check` clean.
- Commit `1d38d34`: Task 2.3 done. Seven artifacts registered, six public in fixed order, `report_json` internal. `test_run_artifacts.py` 9/9.
- Commit `8d07da3`: Tasks 2.4-2.6 done (Report JSON, comment count, `KeyMessageDraft.revision`, key-visual removal, QA). 125 assertions across 14 test files passing, all py_compile/search checks clean, no excluded path tracked.
- Commit `f695f37`: Wave 3 Tasks 3.1-3.5 done (dropped-UI removal, Session Key Message parity, setup editor, `brief_pause` restore, six ordered downloads). Browser self-check 188/188, zero console/page/failed-request errors. Pushed to `origin/main`; scope limit recorded - the 188/188 result stubs `getRun` with populated `briefPoints` and is not evidence for real `brief_pause` snapshot handling.
- Commit `46cd35a`: Tasks 3.6a-3.6e done. Fixed inert console capture and a mis-scoped leak assertion in the E2E harness itself, the `brief_pause` reopen render defect (frozen mount-time snapshot + `[]` treated as valid + dead SSE fallback), the Key Message retry row count (deferred repaint never flushed, unclaimable blank row survived merge), and a live-mode results-screen crash (`renderResults` written against the demo fixture's shape). `tests/e2e_product_flow.py` 15/15, browser self-check 188/188, both Node syntax checks pass. One harness fault (a `:checked` locator race) had been the sole cause of five of the seven original failures.
  - Also recorded: `webapp-testing` was cited in `AGENTS.md` since `1b665be` but never installed until this session; the skill and its `self_check.py` driver now exist and are proven against both a passing and a deliberately-broken fixture.
- Commit `5bb2f9a`: renamed `CHANGELOG.md` to `PRD.md` (the file always held the blueprint alongside the delivery record); updated all references.
- Commit `c645ced`: Wave 4 offline preparation. `.gitignore` benchmark rules, `config.py`/`config-template.py` key remediation, both benchmark harnesses brought to contract, `docs/benchmark-runbook.md` created. No model ran, no comments were collected, no labels were written. Verified: `python tests/bench_classifiers.py --self-check` and `python tests/bench_qwen.py --self-check` both exit zero; `python -m py_compile` clean on all four changed Python files; `node --check` clean on `app/app.js` and `app/live.js`; 13 backend test files pass; `python tests/e2e_product_flow.py` 15/15; `git diff --check` clean. Two probe files proved the `.gitignore` rules behave: `benchmark-data/private/` is ignored and `benchmark-results/*.json` stages. Not pushed.
  - Also fixed during verification: the classifier record labelled every system with a placeholder instead of the model identifier its prediction rows carry, which Task 4.4 needs to pin revisions. A prediction file whose rows disagree about `model` now fails loudly.
  - Two packet errors were mine and were corrected mid-task: a rule to preserve every existing assertion contradicted the deliberate rename of the Qwen record, and the batching algorithm that splits a corpus into per-prompt index spaces was never specified, which crashed the first attempt.

- Commit `ba08b23` (not pushed): Tasks A.1, A.2, and A.6 implemented. **Nothing was verified.** The user scoped this session to implementation only, so no command ran - not the two named test files, not `py_compile`, not the existing suite. Treat every claim below as unproven code, not evidence.
  - A.1: `assets.BlockedUrl`, `_is_public_ip`, `_resolve_public_ips`, `_validate_url`, and a rewritten `fetch_article` with a bounded manual redirect loop. Rebinding is closed by requesting the resolved literal address while preserving the hostname through the `Host` header and the httpx `sni_hostname` extension (httpcore 1.0.9 reads it at `_sync/connection.py:107`), so the connection cannot follow a second uncontrolled resolution. One 15-second monotonic budget spans DNS, all hops, and body reading. `server.add_article` converts `BlockedUrl` to the 422 contract and creates no asset.
  - A.2: fail-closed `_startup`, `_app_password`, `_basic_password`, and the `_require_basic_auth` HTTP middleware, defined before the static mount so one check covers `/`, static files, every `/api/*`, downloads, and SSE. Credentials compare as UTF-8 bytes, because `secrets.compare_digest` raises `TypeError` on a non-ASCII `str` and would have turned a wrong password into a 500. The line 1 `UNAUTHENTICATED` comment now states the real boundary.
  - A.2 test migration: the 7 files that drive `server.app` set `APP_PASSWORD` before importing `server` and carry the credential from one place - `TestClient(..., headers=...)` in the 6 direct-client files, and a Playwright `browser.new_context(http_credentials=...)` in `tests/e2e_product_flow.py`, which is what reaches navigations, XHR, EventSource, and downloads alike.
  - A.6: `vercel.json` deleted and the three `!vercel.json` `.gitignore` lines reverted. Both were uncommitted working-tree additions, so `.gitignore` is back at its committed content.
  - Deliberately not done: `tests/test_article_fetch_guard.py` and `tests/test_basic_auth.py` were not written. A.1 and A.2 stay open until a verification session writes them, runs them, and reruns the 7 migrated files plus `tests/e2e_product_flow.py`.

- Commit `c398038` (not pushed): Tasks A.3, A.4, and A.5 implemented. **Nothing was verified.** The user scoped this session to implementation only, so no command ran - not `py_compile`, not the existing suite. Treat every claim below as unproven code, not evidence.
  - A.3: the `start_run` active-run query dropped its `session_id` filter, so the guard is global. A conflict in the requesting Session keeps the existing message; a conflict elsewhere returns `Another analysis is already running. Wait for it to finish.` The check-then-insert gap is closed by `conn.isolation_level = None` plus an explicit `BEGIN IMMEDIATE`, which takes SQLite's write lock before the check and holds it through the INSERT. The mid-function `conn.commit()` that used to land the prior-run deletes before the new row is gone, so a rejected request no longer destroys a prior run's rows on its way out.
  - A.4: `runs.skip_pause INTEGER NOT NULL DEFAULT 0` added to the schema and to `db.init`'s guarded-ALTER migration alongside `stage`. `StartRunBody{skipPause: bool = False}` accepts an absent body, so an existing client that posts nothing still works. `_ser_run` emits `skipPause`, which covers GET, start, and proceed in one edit. In `adapter._execute` step 7 the reconciled list is persisted before the branch either way; with `skip_pause` set and at least one included message the run pushes stage `brief` and walks past `get_proceed_event().wait()`, otherwise it pauses as before. The skip branch pushes `brief` rather than nothing so the persisted stage never says `brief_pause` for a run that is already classifying.
  - A.5: no code change. `server.run_events` near line 1459 already implements the 15-second idle timer, resets it on both a real event and a heartbeat, closes on a terminal stage, and drains the queue on terminal detection. The contract text was corrected from `: ping\n\n` to the shipped `: heartbeat\n\n` instead.
  - Deliberately not done: `tests/test_run_concurrency_guard.py`, `tests/test_skip_pause.py`, and the `tests/test_db_schema.py` additions were not written, and Task A.7 QA was skipped. Task C.1 (the frontend checkbox) is unaffected and still pending; the backend contract it depends on now exists but is unproven.
  - Highest unproven risk, for whoever runs verification later: the `runs.skip_pause` migration on a pre-existing database, the `BEGIN IMMEDIATE` transaction under two simultaneous start requests, and the `adapter._execute` skip branch reaching `classify` without a `proceed` call. Each is a first-run-only failure mode that no existing test covers.

Wave A is implemented end to end and verified nowhere. Every test file named by Tasks A.1-A.5 is deliberately unwritten, and Task A.7 (read-only QA, whose command list is those files) was skipped by the user's decision on 2026-08-18. Testing across the remaining waves is deferred as a batch, not abandoned: no wave should record a pass it did not earn, and Waves B and C carry the same standing decision unless the user reverses it. What did run before the Wave A commit: `python -m py_compile db.py server.py adapter.py assets.py` exits zero, `git diff --check` is clean, and no excluded path is tracked. That is syntax and hygiene, not behavior.

Resume at Wave B Task B.1. On 2026-08-18 the plan was re-scoped: benchmarking is removed entirely, models are pinned by decision (`qwen3:8b-q4_K_M` text, `qwen3-vl:8b-instruct-q4_K_M` vision), Cloudflare Access is replaced by an in-app HTTP Basic Auth password, and the public URL is a free Cloudflare quick tunnel. The old Waves 4, 5, and 6 are superseded by Waves A through E below and are preserved in git history (commit `b7472b5` and earlier); do not implement from them.

The office RTX 4060 Ti 16GB is available. All Wave A, B, and C tasks are code and need no live model. Wave D (deployment) needs the workstation, Ollama, and the two model pulls. Wave E is documentation. No task is blocked on IT or DNS any more.

### Cross-chat handoff

- Treat this file as the cross-chat execution record. At the start of a new session, read `AGENTS.md`, then read `Delivery state`, this handoff, the next task, and that task's named prerequisite outputs.
- Continue from the stated resume point. Treat existing uncommitted changes as the baseline and do not discard, overwrite, or duplicate them.
- After each task, update `Delivery state` with completion status, changed files, exact verification that ran, commit state, and the next resume point.
- Record only commands that actually ran. Mark omitted checks as not run instead of inferring a pass.
- Keep completed task contracts in place. They remain the implementation record and interface reference for later tasks.

### Shared contracts

#### Public deployment boundary

```text
External browser
  -> HTTPS https://<random>.trycloudflare.com
  -> Cloudflare quick tunnel
  -> cloudflared outbound connection on the office workstation
  -> http://127.0.0.1:8000
  -> FastAPI HTTP Basic Auth middleware
  -> static frontend or /api route

FastAPI pipeline
  -> http://127.0.0.1:11434
  -> Ollama local models only
```

- `cloudflared` publishes only `http://127.0.0.1:8000`. It never publishes Ollama, a filesystem path, a second web server, or a LAN address.
- FastAPI serves both `app/` and `/api/*`. The Basic Auth middleware runs before routing, so one check protects static files, APIs, downloads, and SSE.
- Ollama remains loopback-only. `pipeline.llm._validated_base_url` rejects non-loopback `OLLAMA_BASE_URL` values.
- The quick-tunnel hostname is runtime output. It is not stored as application config and may change after restart.
- Every authenticated client sees the same SQLite database and local artifact store. Authentication answers "may this request enter?" It does not assign ownership.

#### HTTP Basic Auth

- Environment input: `APP_PASSWORD`, a non-empty shared secret. The username is not an identity; any non-empty username is accepted. User-facing examples use `office`.
- Startup fails with `RuntimeError("APP_PASSWORD must be set before the server can start.")` when the value is missing, empty, or whitespace-only.
- Every request requires `Authorization: Basic <base64(username:password)>`.
- Missing, malformed, or wrong credentials return HTTP 401 and `WWW-Authenticate: Basic realm="YouTube Intelligence", charset="UTF-8"`.
- The server compares the password with `secrets.compare_digest`. It never logs the header, decoded username/password, or configured password.
- There is no unauthenticated route, development bypass, cookie session, logout endpoint, account record, or role.

#### Start-run request

```ts
type StartRunRequest = { skipPause?: boolean };
```

- `POST /api/sessions/{sessionId}/runs` accepts `StartRunRequest`. Omitted `skipPause` means `false`.
- Only one `queued` or `running` run may exist across all Sessions.
- A conflict in the requesting Session returns the existing same-Session 409 message.
- A conflict in another Session returns `{"error":"RUN_IN_PROGRESS","message":"Another analysis is already running. Wait for it to finish.","field":null}`.
- `skipPause:true` bypasses `brief_pause` only when reconciliation leaves at least one included Key Message. Zero included messages always enter `brief_pause`.

#### Article-fetch boundary

- Article URLs accept public HTTP/HTTPS hosts on default ports only.
- The server resolves and pins a public destination before every connection and repeats validation for every redirect.
- Any non-public or mixed public/non-public resolution returns HTTP 422 `{"error":"VALIDATION_ERROR","message":"That link points to a private address and cannot be fetched.","field":"url"}` and creates no asset.
- Ordinary public-host timeout or extraction failure preserves the current empty-text asset behavior.

#### HTTP errors

Every API error is the unwrapped JSON object below. The FastAPI handler must prevent `{ "detail": ... }` from reaching the browser.

```ts
type ApiError = { error: string; message: string; field: string | null };
```

| Case | HTTP | Body |
|---|---:|---|
| Session already has `queued` or `running` run | 409 | `{"error":"RUN_IN_PROGRESS","message":"This session already has a run in progress.","field":null}` |
| No included Key Message at `brief_pause` | 422 | `{"error":"VALIDATION_ERROR","message":"Include at least one Key Message before continuing.","field":"messages"}` |
| Unknown artifact | 404 | `{"error":"NOT_FOUND","message":"Artifact not found.","field":null}` |
| Retained key-visual compatibility method | client rejection | `{"error":"FEATURE_UNAVAILABLE","message":"Key visuals are not supported.","field":null}` |

#### Key Messages

```ts
type KeyMessageInput = {
  id: string | null;
  label: string;
  description: string;
  included: boolean;
  order: number;
};
type KeyMessage = KeyMessageInput & { id: string };
type KeyMessageDraft = {
  status: "empty" | "drafting" | "ready" | "stale" | "failed";
  messages: KeyMessage[];
  error: string | null;
  revision: number;
};
type SaveKeyMessagesRequest = { messages: KeyMessageInput[] };
```

- `GET /api/sessions/{sessionId}` returns `keyMessages: KeyMessageDraft`.
- `POST /api/sessions/{sessionId}/key_messages/draft` returns `KeyMessageDraft`. It reads the latest persisted User Inputs, preserves edited rows, keeps the previous rows on failure, and coalesces concurrent requests into one latest rerun.
- `PATCH /api/sessions/{sessionId}/key_messages` accepts `SaveKeyMessagesRequest` and returns `KeyMessageDraft`. It validates the complete list before one transaction. `id:null` creates a server-generated UUID string. Unknown non-null IDs, IDs owned by another Session, and duplicate non-null IDs return `422 VALIDATION_ERROR` with `field:"messages"`. Empty and all-excluded lists are valid during setup. Order is the submitted array order and is also written as `order`/`sort_order` from zero.
- Labels are trimmed, required, and at most 120 characters. Descriptions are trimmed, may be empty, and are at most 500 characters.
- `PATCH /api/runs/{runId}/brief_points` accepts `{messages: BriefPointInput[]}` and returns `{messages: BriefPoint[]}`. It uses the same `id:null` creation rule and ownership checks. At least one included row is required before `POST /api/runs/{runId}/proceed` succeeds.
- `POST /api/runs/{runId}/proceed` returns the current `RunSnapshot`.
- Every `KeyMessageDraft` response includes integer `revision`. Each completed drafting pass, including empty and failed passes, increments it. `PATCH` returns the current revision without incrementing it. Coalesced calls return the final completed revision.

#### Session comment count

`commentCount` counts CSV records from the latest complete run's internal `comments_csv`. It is 0 without a readable complete artifact. Active and failed runs do not replace the latest complete result. It is not parsed from `report_json`.

#### Runs and SSE

```ts
type RunStage = "queued" | "collect" | "brief" | "brief_pause" | "classify" | "emotion" | "report" | "complete" | "error";
type BriefPointInput = KeyMessageInput;
type BriefPoint = KeyMessage;
type ProgressEvent = {
  stage: RunStage;
  pct: number;
  message: string;
  brief_points?: BriefPoint[];
  error?: string;
};
type Artifact = {
  id: string;
  kind: "report_pdf" | "comments_csv" | "key_messages_csv" | "themes_csv" | "sentiment_csv" | "emotions_csv";
  filename: string;
  contentType: "application/pdf" | "text/csv";
  downloadUrl: string;
};
type RunSnapshot = {
  id: string;
  sessionId: string;
  status: "queued" | "running" | "complete" | "failed";
  stage: RunStage;
  pct: number;
  message: string;
  error: string | null;
  briefPoints: BriefPoint[];
  artifacts: Artifact[];
};
```

- `GET /api/runs/{runId}` returns `RunSnapshot`.
- `GET /api/runs/{runId}/events` emits `ProgressEvent` fields in `snake_case`; all other HTTP JSON uses `camelCase`.
- The initial GET snapshot renders first. A later SSE event may advance it. `complete`, `error`, and persisted `brief_pause` from a fresh GET override stale buffered events. Reopening at `brief_pause` renders `briefPoints` without waiting for SSE replay.

#### Artifacts

Stored and public order is fixed:

| Order | Kind | Filename | MIME | Public |
|---:|---|---|---|---|
| 1 | `report_pdf` | `report.pdf` | `application/pdf` | yes |
| 2 | `comments_csv` | `comments.csv` | `text/csv` | yes |
| 3 | `key_messages_csv` | `key-messages.csv` | `text/csv` | yes |
| 4 | `themes_csv` | `themes.csv` | `text/csv` | yes |
| 5 | `sentiment_csv` | `sentiment.csv` | `text/csv` | yes |
| 6 | `emotions_csv` | `emotions.csv` | `text/csv` | yes |
| 7 | `report_json` | `report.json` | `application/json` | no |

- `GET /api/runs/{runId}/artifacts/{artifactId}` serves public blobs with the table MIME and `Content-Disposition: attachment; filename="<filename>"`.
- `GET /api/runs/{runId}/report` reads the internal `report_json`. `report_json` never appears in `RunSnapshot.artifacts` or Files.

#### Report JSON

```ts
type MetricComment = { text: string; likes: number; videoId: string; sentiment: "positive" | "negative" | "neutral" | null };
type KeyMessageMetric = { id: string; metricId: string; label: string; description: string; count: number; percent: number };
type ThemeMetric = { metricId: string; label: string; count: number; percent: number };
type EmotionMetric = { metricId: string; label: string; count: number; percent: number };
type KeyMessageSentimentMetric = {
  id: string; metricId: string; label: string;
  positiveCount: number; positivePercent: number;
  negativeCount: number; negativePercent: number;
  baseN: number;
};
type EvidenceMetric = { metricId: string; comments: MetricComment[] };
type ReportJson = {
  overallTransfer: number;
  keyMessages: KeyMessageMetric[];
  themes: ThemeMetric[];
  emotions: EmotionMetric[];
  keyMessageSentiment: KeyMessageSentimentMetric[];
  evidence: EvidenceMetric[];
};
```

The top-level key set is exact. `transfers` and `ideaSentiment` do not exist. Existing `m-t-*` and `m-is-*` metric IDs remain opaque strings. Python counts every number.

Key Message applicability comes from exact `group` values in transfer rows whose `point` matches after trim/case-insensitive normalization. Message results aggregate all applicable groups. Non-null applicability cells form the denominator; `True` cells form the count. Included messages without applicability remain with zero values. `overallTransfer` counts each Session comment once when it mentions at least one message applicable to its group, divided by all Session comments. Theme and Emotion labels merge case-insensitively, retain first spelling, and use non-empty category labels as denominator. Report percentages use one decimal. Sentiment `baseN` recognizes positive, negative, neutral; neutral enters the denominator; unknown/null does not.

Evidence rules: empty text excluded; invalid likes 0; missing video ID empty. Ordinary evidence orders likes descending, text length descending, source order, and caps at eight. Key Message Sentiment evidence selects up to four positive and four negative, then fills from best unselected recognized comments including neutral. Evidence may repeat one source comment across matching metrics but not within one metric.

Metric ID collision rule: retain `m-t-*`/`m-is-*`; use `message` for empty slugs; suffix collisions `-2`, `-3` consistently across the paired IDs.

#### CSVs

All CSVs use UTF-8, comma separators, a header, `\n` line endings, one-decimal percentages, and deterministic group-first ordering. Groups follow first appearance. Labels sort by count descending, then case-insensitive label. Empty results still write headers.

- `comments.csv`: `video_id,group,comment,likes,language,theme,sentiment,emotion`, followed by one `key_message_<stable-id>` boolean column in Key Message order. The `sentiment_confidence` and `emotion_confidence` columns are removed, because the merged Qwen pass produces no calibrated score. Missing source values are empty; a null affect label from an unmapped code is an empty cell. No internal/debug columns are exported. Task B.4 rewrites this header to the shipped result.
- `themes.csv`: `group,theme,count,percent,base_n`.
- `sentiment.csv`: `group,sentiment,count,percent,base_n`.
- `emotions.csv`: `group,emotion,count,percent,base_n`.
- For the preceding three aggregate files, eligible rows have a non-null, non-empty label. `base_n` is eligible rows in the group. Zero-count labels are omitted. `percent=count/base_n*100`.
- `key-messages.csv`: `group,key_message,count,percent,base_n,positive_count,positive_percent,negative_count,negative_percent,sentiment_base_n`. Emit every applicable group/message pair, including zero mentions. `base_n` is non-null applicability rows. Key Message percentages may sum above 100. `sentiment_base_n` includes all mentioned rows with a recognized non-null sentiment, including neutral. Positive and negative select normalized exact labels. A zero denominator produces `0.0`.

### Dispatch rules

- One task per agent. Inspect and edit only `Files`. Read only named prerequisite outputs under `Consumes`.
- Treat existing uncommitted code as the baseline. Validate it first and change it only when a listed assertion fails.
- Do not discover interfaces from other files. If a named symbol or contract is absent, stop and report the blocker.
- Run commands from the repository root with `python`, never `py`.
- A pass requires every listed command to exit zero. Never claim an unrun pass.
- No network, model, browser, commit, or push unless the task explicitly permits it.
- Read-only evaluators report defects by owning task. They never edit, stage, or commit.
- No edits outside `Files`. Stop after the listed verification and return changed files and exact results.

### Verified foundation - Waves 1-3 complete

Waves 1-3 are implemented and verified. Their detailed execution packets were removed from the active plan on 2026-08-18 because the code, tests, Shared contracts above, Delivery state, and commit history are authoritative. Do not rerun or reimplement them as pending work.

- Foundation and setup (`6eaa00b`, `81bfe27`): local Ollama boundary, grounded User Inputs, Session Key Message schema, atomic full-list PATCH, draft coalescing, stale/failed states, extraction coverage, brief pipeline split, and initial harnesses.
- Backend and outputs (`1a5a3e4`, `8a3de04`, `36e294b`, `1d38d34`, `8d07da3`): persisted run stages, active-run guard, immutable run `brief_points`, transcript reconciliation, persisted `brief_pause`, exact CSVs, deterministic Report JSON/evidence, seven stored/six public artifacts, Session comment count, and backend key-visual removal.
- Frontend (`f695f37`, `46cd35a`): dropped controls removed, Session Key Message API parity, accessible setup editor, paused-run restore/edit/proceed, six ordered downloads, demo/live parity, honest console capture, retry-state fixes, and live results rendering.
- Final Wave 3 evidence: `tests/e2e_product_flow.py` 15/15; browser self-check 188/188; zero console, page, and failed-request errors; both Node syntax checks pass.
- Contracts that future work must preserve remain under Shared contracts. Full root-cause detail remains in the listed commit messages and git history.

### Wave A - Secure the public backend boundary

Wave A changes only backend behavior and backend tests. It closes the security and resource-control gaps created when a localhost tool becomes reachable through a public tunnel. Complete it before any public tunnel starts. No task in this wave needs Ollama, a browser, or external network access.

#### Task A.1 - Guard article fetching against internal addresses

- Files: `assets.py`, `server.py`, `tests/test_article_fetch_guard.py`.
- Symbols: `assets.fetch_article` (function starts near `assets.py:96`; current unrestricted request is `httpx.get(url, follow_redirects=True)` near line 114); new `assets._resolve_public_ips`; new bounded redirect loop inside `fetch_article`; `server.add_article` URL validation near `server.py:1057-1059`.
- Consumes: a user-supplied article URL from `POST /api/campaigns/{campaign_id}/assets/article`.
- Produces: either the existing extracted article text for a public URL, the existing empty-text asset for an ordinary public-host fetch failure, or a `422 VALIDATION_ERROR` for a URL that can reach a non-public address.
- Defect: the current code accepts any string matching `^https?://`, follows redirects automatically, and persists up to 20,000 response characters. Once the workstation is public, a password holder can request `http://127.0.0.1:11434/`, a private LAN service, or a public URL that redirects to one. The response then becomes readable through the shared workspace. This is server-side request forgery. The guard is new work; no equivalent guard exists in the current code.
- URL rules:
  - Accept only `http` and `https`.
  - Reject embedded username or password components.
  - Reject fragments before fetching; query strings remain allowed.
  - Accept only default ports: 80 for HTTP and 443 for HTTPS. Reject every explicit non-standard port, including `11434`.
  - Require a hostname. Normalize an international hostname through the URL parser before DNS resolution.
  - Resolve every hostname before opening a connection. Reject the URL if resolution fails, returns no addresses, or returns any non-public address. Mixed public/non-public DNS answers are rejected, not filtered.
  - Treat loopback, private, link-local, multicast, reserved, unspecified, IPv4-mapped private IPv6, and IPv6 unique-local addresses as non-public.
  - Disable `httpx` automatic redirects. For each 3xx response, parse and resolve the next absolute URL (including relative `Location` values) through the same rules before requesting it.
  - Follow at most five redirects. Preserve one 15-second total timeout budget across DNS, redirects, and response reading; do not reset the timeout to 15 seconds at each hop.
  - Do not trust a validated DNS answer indefinitely. Connect only to the resolved public address for that hop while preserving the original hostname for TLS and the `Host` header, or use an `httpx` transport mechanism that prevents a second uncontrolled DNS resolution. A pre-check followed by a normal hostname request is vulnerable to DNS rebinding and does not satisfy this task.
- Error contract: a rejected URL returns HTTP 422 with `{"error":"VALIDATION_ERROR","message":"That link points to a private address and cannot be fetched.","field":"url"}`. Do not create an asset for this case.
- Existing failure contract: a syntactically valid public URL that times out, returns an ordinary network error, or cannot yield readable article text still creates the current empty-text asset. A security rejection is the only fetch failure promoted to a user input error.
- Verification:
  - `python -m py_compile assets.py server.py tests/test_article_fetch_guard.py`
  - `python tests/test_article_fetch_guard.py`
  - Assert rejection of `127.0.0.1`, `localhost`, `10.0.0.1`, `172.16.0.1`, `192.168.1.1`, `169.254.169.254`, `0.0.0.0`, `[::1]`, `[fd00::1]`, IPv4-mapped private IPv6, a credentialed URL, a `file://` URL, a non-standard port, a public host with one private DNS answer, and a public host redirecting to `127.0.0.1`.
  - Assert a public host fetches, a relative redirect between two public URLs fetches, six redirects fail safely, and a public-host timeout keeps the existing empty-text behavior.
  - Use a stub resolver and stub transport. Make no live network request.
- Stop: do not add an allowlist override. Do not weaken the rule for office hosts. Do not expose a raw resolver or transport error to the user.

#### Task A.2 - Add fail-closed shared-password authentication

- Files: `server.py`, `tests/test_basic_auth.py`.
- Symbols: `server.app` near line 35; `server._startup`; new `server._require_basic_auth` HTTP middleware; static mount near line 1489; `run_events` SSE route near line 1341.
- Environment contract: `APP_PASSWORD` is one non-empty shared password stored in the workstation environment. There is no username setting. Clients may send any non-empty username; only the password is authoritative. Documentation uses `office` as the example username and never as a secret.
- Startup behavior: `_startup` reads `APP_PASSWORD`. If it is absent, empty, or whitespace-only, raise `RuntimeError("APP_PASSWORD must be set before the server can start.")`. Do not warn and continue. Do not define a development bypass, default password, query parameter, cookie fallback, IP allowlist, or unauthenticated health route.
- Middleware behavior:
  - Intercept every HTTP path before routing, including `/`, static assets, all `/api/*` routes, artifact downloads, and `/api/runs/{run_id}/events`.
  - Require `Authorization: Basic <base64(username:password)>`.
  - Reject a missing header, wrong scheme, malformed Base64, missing colon, empty username, or wrong password with HTTP 401.
  - Return `WWW-Authenticate: Basic realm="YouTube Intelligence", charset="UTF-8"` on every 401 so browsers show the native credential prompt.
  - Return a small plain-text or JSON error body that contains no submitted credential data.
  - Decode credentials as UTF-8. Treat decode failure as unauthenticated.
  - Compare the supplied password to `APP_PASSWORD` with `secrets.compare_digest` over equal-type values. Never log the header, decoded credentials, or password.
  - Let authenticated requests pass unchanged. Do not interfere with streaming response bodies; authenticating the initial SSE GET is sufficient.
- Boundary comment: replace the obsolete line 1 claim (`UNAUTHENTICATED ... localhost-only`) with the actual boundary: loopback FastAPI, public `cloudflared` path, HTTP Basic Auth in this process, and no authorization between authenticated users.
- Rationale: a free Cloudflare quick tunnel has no Cloudflare Access policy. HTTP Basic Auth is the smallest real gate that protects static files and APIs at one chokepoint. HTTPS terminates at Cloudflare, so credentials are encrypted in transit. The browser resends them on each request by design.
- Verification:
  - `python -m py_compile server.py tests/test_basic_auth.py`
  - `python tests/test_basic_auth.py`
  - Assert startup fails for missing, empty, and whitespace-only `APP_PASSWORD`.
  - Assert unauthenticated `/`, a static asset, an API GET, an API mutation, an artifact download, and the SSE endpoint all return 401 with the exact `WWW-Authenticate` header.
  - Assert malformed Basic headers and wrong passwords return 401 without credential text in logs or bodies.
  - Assert valid Basic credentials reach each route. For SSE, assert the authenticated response starts streaming and is not buffered by the middleware.
  - Patch the environment inside the test; never write a real password to a file.
- Stop: do not build a login page, cookie session, logout route, account table, role system, password reset, rate limiter, or Cloudflare Access integration.

#### Task A.3 - Enforce one analysis at a time

- Files: `server.py`, `tests/test_run_concurrency_guard.py`.
- Symbols: `server.start_run` active-run guard near line 1143.
- Behavior: widen the existing per-Session guard to a global guard. Before deleting any prior run, artifact, or row, query for every `queued` or `running` run.
- Same-Session conflict: preserve HTTP 409 `{"error":"RUN_IN_PROGRESS","message":"This session already has a run in progress.","field":null}`.
- Cross-Session conflict: return HTTP 409 `{"error":"RUN_IN_PROGRESS","message":"Another analysis is already running. Wait for it to finish.","field":null}`.
- Terminal behavior: a `complete` or `failed` run in another Session does not block. Starting a new run still overwrites the requesting Session's terminal prior run under the existing transaction semantics.
- Race rule: perform the conflict check and new-run insertion under one transaction or lock that prevents two simultaneous requests from both passing the check. A check-then-insert gap does not satisfy the one-GPU invariant.
- Rationale: one RTX 4060 Ti serves one Ollama model at a time. Parallel analyses contend for VRAM and YouTube quota. The shared UI has no queue, so reject rather than silently queue.
- Verification:
  - `python -m py_compile server.py tests/test_run_concurrency_guard.py`
  - `python tests/test_run_concurrency_guard.py`
  - Assert same-Session and cross-Session errors exactly, terminal runs do not block, and rejected requests do not delete prior files or rows.
  - Use two concurrent start attempts in the direct test and assert exactly one succeeds and exactly one active run exists afterward.
- Stop: do not add a queue, cancellation, priority, per-user ownership, or GPU scheduler.

#### Task A.4 - Add optional skip of the Key Message pause (backend)

- Files: `db.py`, `server.py`, `adapter.py`, `tests/test_db_schema.py`, `tests/test_skip_pause.py`.
- Symbols: `db.init`; `server.start_run`, `server._ser_run`; `adapter._execute` near line 996 and its transition to `brief_pause` near line 1098.
- Schema: add `runs.skip_pause INTEGER NOT NULL DEFAULT 0`. Existing databases migrate in place and backfill existing rows to `0` through the SQLite default. Keep the migration idempotent.
- Request contract: `POST /api/sessions/{sessionId}/runs` accepts optional JSON `{"skipPause": boolean}`. Omitted means `false`. Pydantic rejects non-booleans through the standard `ApiError` normalization.
- Response contract: `RunSnapshot` gains `skipPause: boolean`. `_ser_run` converts the integer DB value to a JSON boolean. GET, start, proceed, and SSE-adjacent snapshot paths return the same field.
- Run behavior:
  - Snapshot and reconcile Key Messages exactly as today.
  - Persist the full reconciled `brief_points` list before choosing the next stage.
  - If `skip_pause` is false, enter persisted `brief_pause` unchanged.
  - If `skip_pause` is true and at least one reconciled message is included, continue directly to `classify` without waiting for `POST /api/runs/{runId}/proceed`.
  - If zero reconciled messages are included, enter `brief_pause` even when `skip_pause` is true. The user must include at least one before proceeding.
  - Do not mutate Session `key_messages` during the run.
- Verification:
  - `python -m py_compile db.py server.py adapter.py tests/test_db_schema.py tests/test_skip_pause.py`
  - `python tests/test_db_schema.py`
  - `python tests/test_skip_pause.py`
  - Assert new and migrated databases carry default `0`; omitted/false pauses; true with included messages reaches `classify` without `proceed`; true with zero included messages pauses; every serialized snapshot has a boolean `skipPause`; and restart/reopen reads the persisted value.
- Stop: do not remove `brief_pause`, `POST /proceed`, or run-time Key Message editing. Frontend control belongs to Task C.1.

#### Task A.5 - Keep the progress stream alive

- Files: `server.py`, `tests/test_sse_keepalive.py`.
- Symbols: `server.run_events` near line 1341. The current function already has a 15-second heartbeat interval near line 1376; validate and finish that behavior rather than adding a second mechanism.
- Behavior: when no `ProgressEvent` has been emitted for 15 seconds, emit exactly one SSE comment frame `: heartbeat\n\n`. Reset the idle timer after any real event or heartbeat. Continue until the stream reaches its existing terminal condition or disconnect cleanup.
- Frame text: the shipped frame is `: heartbeat\n\n`, matching the code that already existed. An earlier draft of this task specified `: ping\n\n`; both are SSE comments and no client parses either, so the code wins and this contract was corrected rather than the code changed.
- Contract: heartbeat frames are comments, not `data:` records. `EventSource.onmessage` never receives them. They do not change `RunSnapshot`, `ProgressEvent`, GET/SSE precedence, stage, percent, or message.
- Rationale: classification may be quiet long enough for an intermediary to close an idle response. A comment frame keeps the public tunnel path alive without inventing progress.
- Verification:
  - `python -m py_compile server.py tests/test_sse_keepalive.py`
  - `python tests/test_sse_keepalive.py`
  - Assert an idle stream emits `: heartbeat\n\n` at the configured interval, a real event resets the interval, the heartbeat never parses as a `ProgressEvent`, terminal streams close, and disconnect cleanup does not leak a task.
  - Use a controllable clock or short injected interval in the direct test. Do not wait 15 real seconds.
- Stop: do not send fake percentages or stages. Do not add frontend ping handling.

#### Task A.6 - Retire Vercel configuration

- Files: `vercel.json` (delete), `.gitignore`.
- Behavior: delete `vercel.json`. Delete the `!vercel.json` negation from `.gitignore`. Keep every unrelated ignore rule unchanged.
- Rationale: FastAPI serves the real frontend and API from one origin. A Vercel frontend would need CORS and still depend on the office tunnel, so it adds a second deployment with no product value.
- Verification:
  - `git status --short` shows `vercel.json` deleted or absent.
  - Search current files for `vercel`; allow only historical text in `PRD.md` Revisions.
  - `git diff --check`
- Stop: change no executable code in this task.

#### Task A.7 - Backend boundary QA

- QA files: every file changed by Tasks A.1-A.6, read-only.
- Run every command listed in Tasks A.1-A.6, then:
  - `python tests/test_asset_extraction.py`
  - `python tests/test_run_concurrency_guard.py`
  - `python tests/test_run_key_messages.py`
  - `python tests/test_run_artifacts.py`
  - `python tests/e2e_product_flow.py`
  - `git diff --check`
  - `git status --short`
- Security inspection: confirm every server path, including static files, downloads, and SSE, crosses the Basic Auth middleware; confirm no credential reaches logs; confirm article fetching cannot connect after a second uncontrolled hostname resolution; confirm `APP_PASSWORD`, `.env`, `config.py`, `data/`, `output/`, and generated reports are absent from Git.
- Result: record exact assertion counts and exit codes in Delivery state. Owners fix defects in their own files. Do not commit or push unless the user explicitly authorizes Git delivery.

### Wave B - Merge affect labels into Qwen classification

Wave B changes the classification contract and removes the HuggingFace runtime path. Complete it after Wave A and before deployment. The implementation uses the already-pinned `qwen3:8b-q4_K_M`; it does not compare models, score labels, or run a benchmark.

#### Merged classification contract

```ts
type ClassificationRow = {
  index: number;
  theme: string;
  echoed: string[];
  sentiment: "positive" | "negative" | "neutral";
  emotion: "joy" | "anger" | "sadness" | "fear" | "other_neutral";
};
```

- `index` must cover every requested DataFrame index exactly once. A boolean is not an integer.
- `theme` must be one supplied Theme name or `Other`.
- `echoed` must contain unique supplied Key Message labels only. With no available messages, it must be empty.
- `sentiment` and `emotion` use resolved labels, not codes. JSON Schema `enum` and Python validation enforce the exact sets.
- One Ollama request produces all five fields for every row in its batch. There is no second LLM affect request and no encoder inference.
- Batch application is atomic. Validate the complete response before writing any Theme, Key Message, Sentiment, or Emotion cell.

#### Task B.1 - Extend the strict LLM classification boundary

- Files: `pipeline/analyze.py`, `pipeline/llm.py`, `tests/test_classify.py`.
- Symbols: `analyze.CLASSIFY_PROMPT` near line 52; `analyze.classify` near line 142 and its atomic apply loop near lines 265-269; `analyze.extend` near line 278; `llm.classification_schema` near line 115; `llm.validate_classification` near line 212; `llm.classify_batch` near line 397.
- Prompt behavior:
  - Ask for exactly one Theme, zero or more echoed Key Messages, one Sentiment, and one Emotion per comment.
  - Define `positive`, `negative`, and `neutral` in plain language. Tell the model to use `neutral` for factual, mixed, or unclear polarity rather than inventing certainty.
  - Define `joy`, `anger`, `sadness`, and `fear`. Tell the model to use `other_neutral` for no clear emotion, surprise, disgust, or an affect outside the four named classes. Do not silently map unknown words after generation.
  - Keep existing Theme and Key Message grounding. The model may choose only labels supplied in the prompt.
  - Keep exact original DataFrame indices in output.
  - Request non-thinking mode through the existing Ollama request option used by this codebase. Do not add reasoning text to the JSON response or context.
- Schema behavior: add `sentiment` and `emotion` properties with exact `enum` values. Add both to `required`. Keep `additionalProperties: false`.
- Validator behavior: change `_object`'s expected key set from `{index, theme, echoed}` to all five keys. Reject out-of-set affect values, booleans as indices, duplicate/missing indices, unknown Themes, duplicate/unknown Key Messages, and any extra key.
- Apply behavior: after the whole batch validates, write `theme`, `sentiment`, `emotion`, and matching `pt__*` booleans. If any row is invalid, write nothing from that batch.
- `extend()` behavior: `extend()` reclassifies only rows whose Theme is `Other`. Its `classify()` call now returns affect columns too. During the top-up pass, copy back only `theme` and `pt__*` columns as today; preserve the original `sentiment` and `emotion` from the first full pass. Theme refinement must not silently relabel affect on only one subset.
- Compatibility rule: retain `classify(df, themes, points, cfg=None, ...)` only as required by the existing direct test. Do not create a second legacy classification shape. Test stubs must return all five fields once this task lands.
- Verification:
  - `python -m py_compile pipeline/analyze.py pipeline/llm.py tests/test_classify.py`
  - `python tests/test_classify.py`
  - Assert schema exactness, all five fields, exact affect enums, no-extra-keys, complete unique index coverage, empty-message behavior, atomic no-write on one invalid row, successful writes for every field, and unchanged affect during `extend()` Theme top-up.
  - Assert the prompt includes affect definitions and does not request codes, confidence, statistics, reasoning, or free-form labels.
- Stop: do not add confidence values, per-Key-Message sentiment, language routing, fallback labels, a second model call, or benchmark hooks.

#### Task B.2 - Remove encoder inference and preserve affect summaries

- Files: `pipeline/analyze.py`, `adapter.py`, `run.py`, `tests/test_classify.py`, `tests/test_evidence.py`.
- Symbols: `analyze.affect` near line 351; callers `adapter._execute` near line 1159 and `run.main` near line 191; report/evidence consumers of the returned affect summary.
- New `affect(df, cfg)` behavior:
  - Do not import `transformers` or load a model.
  - Validate that `df` already has `sentiment` and `emotion` columns and that every non-null value belongs to the locked sets. Raise a clear `ValueError` for an internal contract violation; do not attempt to repair it.
  - Preserve the function signature so both production callers stay small. `cfg` remains accepted because `PipelineConfig` is the pipeline contract, even though this function no longer reads a model name.
  - Build the existing per-group percentage tables from the two columns with `pd.crosstab`, preserving the return shape consumed by report generation: `{"sentiment":{"table":...},"emotion":{"table":...}}` plus only fields current consumers demonstrably require.
  - Delete `low_confidence_pct` and confidence-based caveat text. Replace any required caveat field with concise source text: `Labels were assigned per comment by the local Qwen classification pass.` Do not claim measured accuracy.
  - Return a copy and never add `sentiment_confidence` or `emotion_confidence`.
- Pipeline behavior: callers still run the affect stage to produce tables, but it becomes deterministic Python aggregation over labels already present. Do not rename stages or change progress contracts in this task.
- Verification:
  - `python -m py_compile pipeline/analyze.py adapter.py run.py tests/test_classify.py tests/test_evidence.py`
  - `python tests/test_classify.py`
  - `python tests/test_evidence.py`
  - Add direct assertions that `affect()` performs no model import/call, keeps row labels unchanged, emits deterministic percentage tables, rejects missing/invalid columns, carries no confidence fields, and returns only JSON/report-compatible values expected by callers.
- Stop: do not fold summary-table counting into the model. Do not remove `affect()` unless both production callers and all report consumers become simpler in the same bounded change.

#### Task B.3 - Pin Qwen models and remove encoder configuration

- Files: `pipeline/config_types.py`, `adapter.py`, `run.py`, `config-template.py`, `requirements.txt`, `tests/test_brief_key_messages.py`, `tests/test_classify.py`.
- Symbols: `PipelineConfig.EMOTION_MODEL`, `PipelineConfig.SENTIMENT_MODEL`, `PipelineConfig.TEXT_MODEL`, `PipelineConfig.VISION_MODEL`; `adapter._build_config` near lines 362-363; the separate `PipelineConfig(...)` constructor in `run.py` near lines 51-52 and encoder preflight output near lines 86-89; test constructors.
- Config contract:
  - Delete required fields `EMOTION_MODEL` and `SENTIMENT_MODEL` from `PipelineConfig`.
  - Set `TEXT_MODEL: str = "qwen3:8b-q4_K_M"`.
  - Keep `VISION_MODEL: str = "qwen3-vl:8b-instruct-q4_K_M"`.
  - Keep `OLLAMA_BASE_URL` loopback-only and every existing context, timeout, and keep-alive setting.
  - Delete encoder settings from `config-template.py`.
- Constructor migration: remove the two deleted keyword arguments from `adapter._build_config`, `run.py`, `tests/test_brief_key_messages.py`, and `tests/test_classify.py`. Search the full repository after editing; zero executable references to either field may remain.
- Preflight behavior: remove encoder model prints/checks/download assumptions from `run.py`. Keep YouTube, Ollama, text-model, vision-model, PDF, and other existing preflight behavior.
- Dependency behavior: remove `transformers>=4.40`, `torch>=2.0`, and their obsolete comment from `requirements.txt`. Do not add a replacement dependency; affect labels now come from existing Ollama calls.
- Verification:
  - `python -m py_compile pipeline/config_types.py adapter.py run.py config-template.py tests/test_brief_key_messages.py tests/test_classify.py`
  - `python tests/test_brief_key_messages.py`
  - `python tests/test_classify.py`
  - Search executable Python for `EMOTION_MODEL|SENTIMENT_MODEL|transformers|torch`; allow no current runtime hit. Historical prose in `PRD.md` Revisions may remain.
  - Instantiate `PipelineConfig` through both production constructors and assert exact model tags.
- Stop: do not add configurable model tiers, provider fallbacks, automatic model selection, or environment overrides for values that never vary in this deployment.

#### Task B.4 - Remove confidence columns from exports

- Files: `pipeline/report.py`, `tests/test_classify.py`, `tests/test_run_key_messages.py`, `tests/e2e_product_flow.py`, `tests/test_report_sentiment_emotions_csv.py` if it asserts raw comment fixtures.
- Symbols: `report.COMMENTS_HEADER` near lines 496-498; `report._comments_csv` near line 525; fixtures and header assertions that contain `sentiment_confidence` or `emotion_confidence`.
- Exact shipped header: `video_id,group,comment,likes,language,theme,sentiment,emotion`, followed by `key_message_<slug>` boolean columns in existing Key Message order.
- Behavior: delete only the two confidence columns. Keep all sort, encoding, line-ending, missing-value, boolean-column, and deterministic-order behavior unchanged. Do not insert replacement columns or constant scores.
- Fixture migration: remove obsolete confidence values from direct-test DataFrames where they exist only to satisfy the old header. If a fixture uses them for a distinct assertion, rewrite that assertion against the actual label contract rather than leaving dead data.
- Verification:
  - `python -m py_compile pipeline/report.py tests/test_classify.py tests/test_run_key_messages.py tests/e2e_product_flow.py tests/test_report_sentiment_emotions_csv.py`
  - `python tests/test_classify.py`
  - `python tests/test_run_key_messages.py`
  - `python tests/test_report_sentiment_emotions_csv.py`
  - `python tests/e2e_product_flow.py`
  - Assert exact header on populated and empty inputs; assert neither removed name appears in generated CSV text; assert key-message columns still follow `emotion`.
- Stop: do not change aggregate CSV schemas, public artifact names, Report JSON, or evidence filters.

#### Task B.5 - Merged-model QA

- QA files: every file changed by Tasks B.1-B.4, read-only.
- Run:
  - `python -m py_compile pipeline/config_types.py pipeline/llm.py pipeline/analyze.py pipeline/report.py adapter.py run.py config-template.py`
  - `python tests/test_brief_key_messages.py`
  - `python tests/test_classify.py`
  - `python tests/test_evidence.py`
  - `python tests/test_report_themes_csv.py`
  - `python tests/test_report_sentiment_emotions_csv.py`
  - `python tests/test_report_key_messages_csv.py`
  - `python tests/test_run_key_messages.py`
  - `python tests/e2e_product_flow.py`
  - `git diff --check`
  - `git status --short`
- Inspect the exact Ollama boundary: one classification request per batch; strict five-field schema; no confidence; no encoder import; no model benchmark or scoring path.
- Reject secrets, `.env`, `config.py`, `data/`, `output/`, generated reports, and model binaries from Git.
- Record exact results in Delivery state. Do not run a real model in this wave; real-model evidence belongs to Wave D.

### Wave C - Remove benchmark machinery and finish frontend integration

Wave C deletes the abandoned benchmark path and exposes the already-locked skip-pause choice in the UI. Deletion is the main implementation. Do not replace benchmark files with new scripts, placeholders, or result schemas.

#### Task C.1 - Add the skip-pause control

- Files: `app/app.js`, `app/index.html`, `app/style.css`, `app/self-check.html`, `tests/e2e_product_flow.py`.
- Symbols: `renderCampaign` near `app/app.js:2017`; existing run-start control and `demoApi`/live API method that posts a new run.
- UI contract:
  - Render one unchecked checkbox beside the start-analysis control.
  - Label: `Skip the review step and run straight through`.
  - Helper text: `We will not pause to ask you to confirm the Key Messages.`
  - Keep unchecked as the default on every fresh Session render. Do not persist a global preference.
  - Associate the visible label with the checkbox. Keep it keyboard reachable. Preserve the existing focus ring, 4.5:1 text contrast, reduced-motion behavior, and disabled state while start is pending.
- Request behavior: pass `skipPause` as the checkbox's boolean value in `POST /api/sessions/{sessionId}/runs`. Demo and live implementations use the same method signature and body shape. Do not send a string or omit false inconsistently between modes.
- Run behavior: the frontend does not predict whether zero included Key Messages will override the choice. Render the backend `RunSnapshot`; if it returns `brief_pause`, show the existing review UI regardless of the checkbox.
- Error behavior: a failed start keeps the checkbox value and focus, announces the existing start error, and re-enables controls.
- Verification:
  - `node --check app/app.js`
  - `node --check app/live.js`
  - Serve `app/` on a fresh loopback port and open `app/self-check.html` with `webapp-testing`.
  - Require every displayed assertion to pass with zero console errors, zero page errors, and zero failed requests.
  - `python tests/e2e_product_flow.py`
  - Assert default false body, checked true body, keyboard operation, label association, pending disabled state, failed-start retention, direct completion when true, and forced pause when true but zero messages are included.
- Depends on: Task A.4.
- Stop: do not remove the review UI, add a remembered preference, or change `demoApi` method signatures beyond the optional request field already required by the backend contract.

#### Task C.2 - Delete benchmark code, data contracts, and result paths

- Files: `tests/bench_qwen.py` (delete), `tests/bench_classifiers.py` (delete), `docs/benchmark-runbook.md` (delete), `.gitignore`, `pipeline/llm.py` (comment only), `PRD.md` read-only except Delivery state after completion.
- Directory state: `benchmark-results/` has no tracked files at planning time. Delete it only if implementation finds files created by this repository and confirms they are disposable; do not delete unknown user data. `benchmark-data/private/` may contain ignored private files; leave local data untouched and remove only repo rules that exist solely for the abandoned benchmark after confirming they protect no other workflow.
- Delete behavior:
  - Delete both benchmark harnesses and the runbook.
  - Remove `.gitignore` rules that exist only for benchmark inputs/results, including `!benchmark-results/*.json`, after verifying each exact line.
  - Delete the obsolete `pipeline/llm.py` module comment that names `tests/bench_qwen.py`. Change no executable LLM code in this task.
  - Remove benchmark commands from current QA/task text only where they are still presented as active work. Preserve dated Revisions and commit history references as historical facts.
  - Do not create `tests/test_model_labels.py`; it never existed and belonged only to the superseded benchmark-selection task.
- Verification:
  - Search current executable code and active docs for `bench_qwen|bench_classifiers|benchmark-results|benchmark-data/private|benchmark-runbook|test_model_labels`; allow only dated historical entries in `PRD.md` Revisions.
  - `python -m py_compile pipeline/llm.py`
  - `git diff --check`
  - `git status --short` shows the three tracked deletions and no private benchmark content.
- Stop: do not delete user-created ignored data. Do not add a replacement benchmark, smoke benchmark, model selector, F1 metric, or output record.

#### Task C.3 - Frontend and teardown QA

- QA files: every file changed by Tasks C.1-C.2, read-only.
- Run:
  - `node --check app/app.js`
  - `node --check app/live.js`
  - Browser `app/self-check.html` through `webapp-testing`: all assertions pass; zero console errors, page errors, and failed requests.
  - `python tests/e2e_product_flow.py`
  - `python tests/test_skip_pause.py`
  - `git diff --check`
  - `git status --short`
- Search for active benchmark references and removed confidence columns. Allow removed names only in dated history and explicit migration assertions that prove absence.
- Record exact results in Delivery state. Do not commit or push without explicit authorization.

### Wave D - Set up and prove the office workstation

Wave D is an operations session on the RTX 4060 Ti 16GB workstation. It runs only after Waves A-C pass. It installs local software, starts the public quick tunnel, and proves the real user flow from another network. It does not modify product code to work around a failed setup step.

#### Task D.1 - Inspect the workstation and record prerequisites

- Files: `docs/deployment.md` (create; user-approved path is this existing plan's named deployment record). Never place secrets in it.
- Record the exact date, Windows edition/build, CPU, RAM, GPU name, NVIDIA driver, free disk, Python version, Git version, and whether administrator rights are available. Record commands and exit codes, not paraphrased claims.
- Minimum conditions:
  - NVIDIA reports an RTX 4060 Ti with 16GB VRAM.
  - Python meets the repo's 3.10+ rule.
  - Enough free disk exists for the repo, Python environment, Ollama, both model blobs, uploads, and reports. Record the measured free space; do not invent a universal threshold.
  - The operator can install Ollama and `cloudflared` and configure startup behavior. If not, stop and report the exact permission blocker.
- Power behavior: configure Windows to never sleep or hibernate while plugged in. If BIOS/UEFI exposes restore-after-power-loss and the operator can change it safely, enable it; otherwise record it as not configured, not passed.
- Verification: capture command output and exit codes in `docs/deployment.md`. Never infer a pass from software appearing in a menu.

#### Task D.2 - Install dependencies and local models

- Files: `docs/deployment.md`; local environment and machine software only. Do not edit repo dependency files during setup.
- Install Python dependencies from the final `requirements.txt` and `requirements-server.txt` into the project's chosen environment. Install Playwright Chromium if the PDF path requires it.
- Install Ollama from its official distribution. Keep Ollama bound to loopback. Confirm no firewall rule exposes port 11434 and no tunnel points to it.
- Pull exactly:
  - `qwen3:8b-q4_K_M`
  - `qwen3-vl:8b-instruct-q4_K_M`
- Record Ollama version, each exact tag, digest, and reported size. Record `nvidia-smi` before and after one inference.
- Run one real text inference through the product's `pipeline.llm` boundary using `qwen3:8b-q4_K_M` and the strict merged classification schema. Require one valid five-field row.
- Run one real image-context inference through `pipeline.llm.extract_image_context` using a harmless local test image and `qwen3-vl:8b-instruct-q4_K_M`. Require valid structured output.
- Confirm model switching/unload behavior does not leave both models resident beyond 16GB. Do not run both concurrently.
- Stop on an invalid structured response, CUDA/VRAM failure, cloud-model fallback, or non-loopback Ollama URL. Record the blocker; do not swap models or loosen validation in this task.

#### Task D.3 - Configure secrets and start the local application

- Files: `docs/deployment.md`; workstation environment only.
- Set `YOUTUBE_API_KEY` and `APP_PASSWORD` in the account or service environment that will launch the backend. Use a strong unique shared password. Never pass either value on a command line that is recorded in shell history or the deployment document.
- Confirm `.env`, `config.py`, and environment dumps containing either secret are untracked. Do not print values for evidence. Record only `set/non-empty`.
- Start the backend through its supported command. Confirm it binds only `127.0.0.1:8000`.
- Local authentication checks:
  - Without credentials, `/`, one static asset, and one API route return 401 with the Basic challenge.
  - With credentials, `/` serves the real UI and one API GET succeeds.
  - A wrong password returns 401.
  - Ollama remains reachable only on loopback.
- Run one local golden path: open UI, authenticate, create a Session, add a small valid YouTube input, and reach at least setup state. If network/API quota prevents collection, record the exact external blocker rather than modifying code.

#### Task D.4 - Publish through a Cloudflare quick tunnel

- Files: `docs/deployment.md`; workstation software only.
- Install `cloudflared` from its official distribution and record the version.
- Start exactly `cloudflared tunnel --url http://127.0.0.1:8000`. Do not create a named tunnel, DNS record, Cloudflare Access application, router port forward, or LAN bind.
- Capture the generated `https://<random>.trycloudflare.com` hostname. Treat it as operational data, not a stable product contract: a quick-tunnel URL can change when restarted.
- From a client on a different network (mobile data or another external connection):
  - Open the generated HTTPS URL.
  - Confirm the browser presents the Basic Auth prompt.
  - Confirm wrong credentials fail and correct credentials load the UI.
  - Confirm no mixed-content warning and no direct exposure of port 8000 or 11434.
  - Create a Session and verify static assets, API calls, and SSE all work through one origin.
- Security probe: submit a blocked article URL such as `http://127.0.0.1:11434/` and require the exact 422 contract. Use no real internal target beyond loopback.
- Stop if any path is reachable without credentials, TLS is invalid, Ollama is exposed, or the tunnel points anywhere except `127.0.0.1:8000`.

#### Task D.5 - Run one real end-to-end analysis

- Files: `docs/deployment.md`; generated local `data/` and outputs remain ignored.
- From the external client, authenticate and run one real Session through the product UI:
  1. Create a Session.
  2. Add valid YouTube input and at least one grounded User Input.
  3. Review or generate Key Messages.
  4. Start with `skipPause:false`; reach `brief_pause`, edit one message, save, and proceed.
  5. Close the browser tab during processing, reopen the URL, authenticate again, and restore the run.
  6. Reach `complete`.
  7. Open the results screen and download all six public artifacts.
  8. Confirm `report_json` is not offered as a download.
- Inspect `comments.csv`: exact header is `video_id,group,comment,likes,language,theme,sentiment,emotion` plus Key Message columns; no confidence columns; labels fall inside locked enums.
- Inspect one report: Theme, Key Message, Sentiment, and Emotion sections render without missing-field errors. PDF opens.
- Record total comments and wall-clock duration. If the Session has approximately 3,000 comments, compare the measured duration to two hours as a sanity note only. Never write `passed:false` or block deployment because of that comparison; there is no timing gate.
- Run a second small Session with `skipPause:true` and included Key Messages. Confirm it goes from reconciliation to classification without user intervention. Do not wait for a second full report if reaching `classify` proves the contract and cancelling remains out of scope; use a naturally small input when possible.
- Record failures exactly. Do not change model, batch size, timeout, schema, or quality requirements during this operations task.

#### Task D.6 - Configure restart behavior and record limitations

- Files: `docs/deployment.md`; workstation service/task configuration only.
- Configure Ollama and the FastAPI backend to start when the workstation boots or the operator signs in, using the least privileged mechanism available on the machine.
- Quick-tunnel limitation: the generated hostname can change after `cloudflared` restarts. Because the user chose the free quick tunnel, do not claim one permanent URL. Choose one of these operational behaviors and record which the workstation supports:
  - launch `cloudflared` at sign-in and let the operator copy the new URL from logs, or
  - launch it manually when access is needed.
- Do not add code, scrape logs, email the URL, publish it to a third party, or create a dynamic-discovery service. A stable URL requires a future named tunnel/domain decision and is out of scope.
- Reboot the workstation. Without manually starting Ollama or the backend, confirm local Basic Auth and one API GET work. Start or verify `cloudflared` according to the selected quick-tunnel behavior, obtain the current URL, and repeat the external authenticated UI check.
- Record service/task names, startup triggers, log locations, stop/restart commands, and the quick-tunnel URL rotation limitation.
- Record accepted operational limits: shared workspace, shared password, no per-user audit, one run at a time, no backups, workstation outage stops service, backend restart loses active-run recovery, quick-tunnel URL may rotate.

#### Task D.7 - Deployment verification record

- Files: `docs/deployment.md` only. Defects return to their owning wave.
- Record pass/fail/not-run, exact command, exit code, and concise evidence for:
  - both real model inferences;
  - backend loopback bind;
  - Basic Auth over static/API/download/SSE paths;
  - public HTTPS from a different network;
  - SSRF rejection;
  - one-run guard;
  - SSE persistence through the tunnel;
  - pause reopen and proceed;
  - skip-pause path;
  - one completed real Session;
  - six public downloads and hidden `report_json`;
  - exact `comments.csv` header and affect enums;
  - reboot behavior.
- Record versions: OS, Python, Node if used, browser, NVIDIA driver, GPU, Ollama, both model tags/digests, `cloudflared`, and current commit.
- Redact secrets completely. Do not include Basic headers, API keys, passwords, `.env` content, private comments, or full artifact contents.
- The record is evidence, not a gate fabrication. Mark anything unrun as `NOT RUN` and anything blocked with the exact blocker.

### Wave E - Align documentation with the shipped office service

Every Wave E task loads `compact-technical-writing`. Documentation consumes the final code and `docs/deployment.md`; it does not revive benchmark terminology or infer evidence that the deployment record lacks.

#### Task E.1 - Product and setup documentation

- Files: `README.md`, `docs/setup.md`, `requirements.txt`, `requirements-server.txt`, `docs/deployment.md` read-only.
- README:
  - Describe one shared office service reached by browser, not per-device installation.
  - Document the Session flow, setup Key Messages, optional skip-pause, transcript reconciliation, merged Theme/Key Message/Sentiment/Emotion labelling, results, and six downloads.
  - State shared-password access, shared workspace visibility, one run at a time, no backups, tab-close continuation, backend-restart limitation, and rotating quick-tunnel URL.
  - Name `qwen3:8b-q4_K_M` and `qwen3-vl:8b-instruct-q4_K_M`. Do not mention benchmark selection.
- Setup guide:
  - Document Python/Ollama/`cloudflared` prerequisites, exact model pulls, `YOUTUBE_API_KEY`, `APP_PASSWORD`, server start, Basic Auth behavior, quick-tunnel command, PDF engine, verification commands, startup/reboot operations, and troubleshooting.
  - State that `APP_PASSWORD` missing means startup failure.
  - Explain that `*.trycloudflare.com` changes after restart and that a stable hostname needs a future named tunnel/domain.
  - Remove HuggingFace encoder, torch, transformers, Gemini, Vercel, Cloudflare Access, DNS delegation, benchmark, and confidence-column instructions.
- Dependency files: change only if final verified runtime imports disagree. Do not add optional packages for hypothetical deployments.
- Verification: `python tests/check_docs.py README.md docs/setup.md` when that checker exists; otherwise validate local links manually and run `git diff --check`.

#### Task E.2 - Architecture and API reference

- Files: `docs/architecture.md`, `docs/api-reference.md`, `docs/deployment.md` read-only.
- Architecture:
  - Document one workstation, loopback Ollama, loopback FastAPI, outbound quick tunnel, HTTP Basic Auth middleware, shared DB/storage, and browser clients.
  - Document the trust boundaries: internet client to Cloudflare HTTPS, Cloudflare to loopback tunnel target, middleware before static/API/SSE, SSRF guard before article connections, and no authorization inside the workspace.
  - Document merged Qwen classification and Python aggregation. Remove the HuggingFace boundary and confidence path.
  - Document global one-run guard, persisted `brief_pause`, optional skip-pause, tab-close continuation, backend-restart limitation, seven stored/six public artifacts, and quick-tunnel URL rotation.
- API reference:
  - Add HTTP Basic Auth as a requirement for every path, including static resources and SSE. Document exact 401 challenge behavior without including credentials.
  - Add `skipPause` to start-run request and `RunSnapshot`.
  - Add global-run conflict error and article private-address 422 error.
  - Keep camelCase HTTP and snake_case SSE rules.
  - Update `comments.csv` to the eight fixed columns plus Key Message columns.
  - Preserve all other current route, artifact, Report JSON, validation, and extraction contracts.
- Verification: `python tests/check_docs.py docs/architecture.md docs/api-reference.md` when available; inspect links and `git diff --check`.

#### Task E.3 - Contributor rules and product record

- Files: `AGENTS.md`, `PRD.md`, `docs/deployment.md` read-only.
- AGENTS.md:
  - Update terminology mapping so Sentiment and Emotions no longer name confidence columns.
  - Add `APP_PASSWORD` to the never-commit rule.
  - Record merged classify contract, one-run invariant, Basic Auth boundary, SSRF guard, and loopback Ollama rule under "Things that must not change without justification."
  - Remove HuggingFace encoder, benchmark, Gemini, Vercel, and Cloudflare Access instructions from current guidance.
  - Keep thread-local YouTube clients, grounded-only Key Messages, Python counting, required PDF, per-Session terminal overwrite, `PipelineConfig`, frozen `demoApi` signatures, accessibility, and exact verification ownership.
- PRD.md:
  - Preserve completed task contracts and Revisions.
  - Move Waves A-E outcomes into Delivery state only after their QA actually passes. Never pre-mark work complete.
  - Keep superseded benchmark/deployment detail only in dated history, not as active tasks.
- Verification: `python tests/check_docs.py AGENTS.md PRD.md` when available; `git diff --check`.

#### Task E.4 - Documentation QA

- Owner files: `tests/check_docs.py` only if it does not yet exist. Create a stdlib direct script with `main()` that accepts Markdown paths, checks local relative links and duplicate/malformed headings, and exits nonzero on a defect. Do not add a documentation framework.
- Read-only QA files: `README.md`, `PRD.md`, `AGENTS.md`, `docs/setup.md`, `docs/deployment.md`, `docs/architecture.md`, `docs/api-reference.md`.
- Run:
  - `python tests/check_docs.py README.md PRD.md AGENTS.md docs/setup.md docs/deployment.md docs/architecture.md docs/api-reference.md`
  - `git diff --check`
  - `git status --short`
- Search current-product sections for stale active guidance: `bench_qwen|bench_classifiers|benchmark-results|benchmark-runbook|HuggingFace|transformers|torch|sentiment_confidence|emotion_confidence|Vercel|Cloudflare Access|analysis.innocean.co.id|DNS delegation|qwen3:14b`. Allow dated Revisions and explicit migration history only.
- Confirm `.env`, `config.py`, `data/`, `output/`, private benchmark data, generated reports, model files, API keys, and passwords are absent from Git.
- Record exact results in Delivery state. Do not commit or push without explicit authorization.

---

## July 2026

### Backend and frontend

FastAPI backend (`server.py`) wrapping the pipeline with Sessions, uploads, a run lifecycle, and SSE progress. SQLite persistence, local file storage for uploads and artifacts. Single-page vanilla frontend in `app/`, live against the backend when served by it and on an in-memory fixture store otherwise.

Key Message review interrupt added: the run pauses after drafting, the user edits and confirms, then labelling proceeds.

### Per-comment LLM labelling

Replaced regex keyword matching with LLM classification. The model discovers a Theme book from a stratified sample, then labels each comment in batches against that fixed set: exactly one Theme, zero or more Key Messages, one pass. Python counts the labels.

The driver was accuracy. Regex undercounted paraphrase and mishandled negation ("not worth it" matching a `worth` keyword). Counting stayed in Python, which was never in question.

### Grounded-only brief

Removed the background brief, where the model wrote what it knew about a campaign from memory. Taglines, unit counts, and launch dates are exactly what a model invents fluently. Every claim now traces to something the user provided.

## Revisions

Detailed root-cause narration for each entry below lives in the matching commit message.

- 2026-08-18: Implemented Wave A Tasks A.3 (global one-run guard), A.4 (optional skip of the Key Message pause), and A.5 (SSE keepalive), again with no verification by the user's explicit scoping. A.5 turned out to need no code: the heartbeat loop was already complete, so the mismatch recorded at the previous resume point was settled in favour of the shipped `: heartbeat\n\n` and the contract text was corrected. A.3 exposed a defect the task text named but the old code did not have: the guard ran, then `conn.commit()` landed the prior-run deletes, then the INSERT followed, so two simultaneous requests could both pass the check and a rejected one could still delete rows. An explicit `BEGIN IMMEDIATE` over the whole check-delete-insert sequence closes both. A.4's skip branch pushes stage `brief` rather than skipping the push, because a run that walks past the proceed wait while its row still reads `brief_pause` would render a review screen to a reopened tab. Left the three named test files unwritten rather than writing test code nobody would run. Wave A is now implemented end to end and verified nowhere; Task A.7 owns all of it.

- 2026-08-18: Implemented Wave A Tasks A.1 (SSRF guard on article fetching), A.2 (fail-closed HTTP Basic Auth), and A.6 (Vercel removal), with no verification by the user's explicit scoping of the session to implementation only. Two design points were decided against the plan text, which named the requirement without naming a mechanism. The anti-rebinding rule ("do not trust a validated DNS answer indefinitely") is satisfied by connecting to the resolved literal address and carrying the original hostname in the `Host` header and the httpx `sni_hostname` extension; a pre-check followed by a by-name request would have re-resolved and failed the rule. The Playwright end-to-end harness needed `http_credentials` on a browser context rather than a request header, because its `new_page()` call at `tests/e2e_product_flow.py:795` has nowhere to attach one and navigations, EventSource, and downloads each need the credential. Corrected one defect in the auth middleware before it was recorded: `secrets.compare_digest` over two `str` values raises `TypeError` on any non-ASCII character, so a submitted password with one would have produced a 500 rather than a 401; both sides now compare as UTF-8 bytes. Left the two named test files unwritten rather than writing test code nobody would run.

- 2026-08-18: Re-scoped remaining delivery around the available office RTX 4060 Ti 16GB and direct implementation. Removed model benchmarking, answer-key annotation, F1 gates, private benchmark corpus work, benchmark result records, and HuggingFace affect encoders from the active product. Pinned `qwen3:8b-q4_K_M` for text and `qwen3-vl:8b-instruct-q4_K_M` for image User Inputs. Locked one merged Qwen classification response per batch with Theme, Key Messages, Sentiment, and Emotion; removed both confidence columns from the shipping `comments.csv` contract. Replaced the branded Cloudflare Access deployment with a free rotating `*.trycloudflare.com` quick tunnel protected by fail-closed HTTP Basic Auth from `APP_PASSWORD`. Kept loopback FastAPI/Ollama, SSRF protection, one global active run, optional skip-pause, and SSE keepalive. Added detailed Waves A-E for backend boundary, merged model path, teardown/frontend, workstation deployment, and documentation. Recorded quick-tunnel URL rotation, shared-workspace exposure, one-run limit, no backups, and no backend-restart recovery as accepted limits. Compacted completed Waves 1-3 into one verified-foundation summary; their exact contracts remain in Shared contracts, Delivery state, tests, and git history. Deleted untracked `docs/deployment-brief.html` and `docs/deployment-brief.pdf` at the user's request.

- 2026-08-15: Added Wave 6 (office deployment) and placed it before Wave 4. The RTX 4060 Ti is both the deployment target and the locked benchmark hardware, so it must be set up before Wave 4 can run. Locked the topology: one workstation runs Ollama, the backend, and the frontend; `pipeline/llm.py:239` rejects any non-loopback `OLLAMA_BASE_URL`, so co-location is a code requirement rather than a preference. The backend keeps its `127.0.0.1:8000` bind and keeps serving `app/` itself, with `cloudflared` publishing it and Cloudflare Access restricting login to `@innocean.co.id`. Dropped Vercel, because the backend already serves the real UI and a second origin would need CORS while reaching the same tunnel. Reversed the original per-device storage idea: all Sessions, uploads, and artifacts live on the one workstation, which the existing re-download endpoints (`server.py:1116`, `server.py:1466`) and Files page (`app/app.js:3044`) already serve unchanged. Accepted three consequences explicitly: no accounts, so every authenticated user reads every Session; no backups, so the workstation disk holds the only copy; and a plain error when Ollama is unreachable. Found one real defect that the deployment itself creates: `assets.fetch_article` (`assets.py:114`) follows redirects to any host passing the `^https?://` check at `server.py:1058` and persists the response body, which becomes server-side request forgery against the office network once the tunnel is open. Task 6.1 closes it with pre-connection resolution, per-redirect re-checking, and a 422 rather than the never-raise empty-text path. Added the global one-run guard, the optional skip of the Key Message pause with a zero-included-messages fallback, and a 15-second SSE keepalive for hour-long silent `classify` stages.

- 2026-08-15: Prepared Wave 4 offline. Measured the machine instead of assuming it: an RTX 3050 Laptop with 4096 MiB holds neither Qwen candidate, and no Ollama daemon runs, so every model-dependent step is blocked on hardware. Closed two contract holes found by auditing the harnesses against their tasks. Task 4.1 produced only Sentiment and Emotions labels while Task 4.3 gated on `themeMacroF1` and `keyMessageMacroF1`, so those two gates had no answer key; the labels row now carries `true_theme` and `true_key_message_ids`. Nothing produced the `allowed_theme_labels` and `allowed_key_message_ids` arrays the index encoding needs; a hand-authored six-entry Theme book and five-entry Key Message list in `docs/benchmark-runbook.md` are now their source. Reduced the label set from 600 across two independent annotators to a 150-row single-annotator pilot, and recorded that this validates tooling rather than deciding what ships. Locked the single-character Sentiment and Emotions code map. Added the rule that every Key Message ID must appear on at least 5 rows, because an unmentioned ID scores `0.0` in a Macro F1 mean and would fail the 0.70 gate on corpus composition rather than model quality. Removed two plaintext API keys from `config.py`, which now reads `YOUTUBE_API_KEY` from the environment; the unused `GEMINI_API_KEY` was deleted rather than migrated. Both harnesses reached their locked CLI, loader, index-resolution, and output-record contracts with offline self-checks proving each. No model ran, no comments were collected, and no benchmark result was produced.

- 2026-08-15: Revised Wave 4 against confirmed benchmark hardware (RTX 4060 Ti 16GB). Raised the 3,000-comment release threshold from 40 minutes to 2 hours. Merged sentiment and emotion into the single Qwen labelling prompt and made the producer a benchmark decision instead of an assumption, so Task 4.2 now scores three systems and depends on Task 4.3. Locked non-thinking mode, index encoding for Theme and Key Message IDs, `OLLAMA_NUM_PARALLEL=1`, and a single-batch timing gate before any full run. Ruled out every larger Qwen3 option on VRAM. Added the conditional removal of `sentiment_confidence` and `emotion_confidence` when Qwen wins the matching task. Kept sentiment per comment, so `keyMessageSentiment` and `key-messages.csv` are unchanged. Deleted the untracked `PRD.backup-2026-08-15.md`; its narration remains in the commit messages. No project code changed in this session.
- 2026-08-15: Completed Tasks 3.6a-3.6e (commit `46cd35a`); Wave 3 complete and verified; 15/15 E2E, 188/188 browser self-check. Added unplanned Task 3.6e (live-mode results-screen crash) once completed runs became reachable. Authored the `webapp-testing` skill, previously cited but never installed.
- 2026-08-15: Split Task 3.6 into ordered sub-tasks 3.6a-3.6d, locking harness-repair before app-fix ordering; traced all four open defects to source; corrected two earlier wrong-contract guesses during planning. No project code changed in this session.
- 2026-08-14: Task 3.6 in progress, 8/15 E2E passing. Fixed three `app/app.js` defects (lost Save click, dirty state not cleared, duplicate draft ID); four defects stayed open pending 3.6a-e.
- 2026-08-14: Wave 3 Tasks 3.1-3.5 implemented and verified at the frontend stopping point (Node checks clean, browser self-check 188/188). E2E coverage not yet implemented.
- 2026-08-13: Approved the Report JSON, Session comment count, `KeyMessageDraft.revision`, and key-visual-removal contracts; Tasks 2.4-2.5 implemented against them, uncommitted pending Task 2.6.
- 2026-08-13: Recorded Task 2.3 completion (commit `1d38d34`, 9/9 focused verification); added cross-chat handoff rules.
- 2026-08-13: Task 2.6 read-only QA rerun complete: 125 assertions across 14 test files passing, no excluded path tracked.
- 2026-08-13: Committed Tasks 2.4-2.6 at `8d07da3`; push to `main` pending at that point.
