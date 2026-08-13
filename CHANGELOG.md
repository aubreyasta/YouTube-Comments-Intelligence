# Changelog

Notable changes to this project. Newest first. This file is the record of what changed and why; the `docs/` files describe the system as it stands.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). This project is not versioned; entries sit under dated releases or `[Unreleased]` for planned and in-progress work.

---

## [Unreleased]

This is the execution brief for the final product. It is a contract, not a list of suggestions. An implementation agent reads `AGENTS.md`, one task below, and only that task's listed files and named prerequisite outputs. It does not scan the repository.

### Locked decisions

- A 3,000-comment Qwen run must finish within 40 minutes to ship. This is a release threshold, not a forced run cancellation.
- Closing a browser tab does not stop a run. Backend or computer restart recovery is out of scope.
- Product language support is Indonesian, English, and mixed Indonesian-English.
- Macro F1 selects classifiers. Accuracy is reported second.
- Users may add and delete Key Messages during setup and `brief_pause`. The server generates IDs for new rows.
- Transcript reconciliation preserves edited messages, updates case-insensitive generated matches without changing IDs, keeps unmatched existing messages, and appends transcript additions.
- Label and description changes use an explicit Save control. Inclusion and ordering save immediately.
- Key visuals, chat, global search, source discovery, OCR, custom lenses, cross-group reports, run history, and cancellation are not part of the final product.
- Raw benchmark comments and predictions remain ignored under `benchmark-data/private/`. Only schemas, runbooks, and sanitized aggregate results are committed.

### Delivery state

- Commit `6eaa00b`: local Ollama boundary, documentation realignment, Session Key Message schema, and atomic PATCH foundation.
- Commit `81bfe27`: setup drafting, draft coalescing and stale state, asset extraction coverage, brief pipeline split, and benchmark harnesses.
- Commits `1a5a3e4`, `8a3de04`: Task 2.2 done. `pipeline/report.py` exports the exact CSV contract (`comments.csv`, `themes.csv`, `sentiment.csv`, `emotions.csv`, `key-messages.csv`); `summary.csv`, `chart_transfer.csv`, `chart_themes.csv` removed.
- Commit `36e294b`: Task 2.1 done. `runs.stage` migration; active-run guard; Session-to-run Key Message snapshot; transcript reconciliation before `brief_pause`; unified run/error contract (`_ser_run`, `_404`/`_409`/`_422`/`_413`, Starlette `HTTPException` handler); Key Messages and brief points on one `messages: KeyMessageIn[]` replace contract.
  - Verified before packaging: expanded suite, 40/40 assertions passed (not re-run at commit time; diff unchanged since that pass).
  - Verified post-rebase: `python -m py_compile db.py server.py` clean; `python tests/test_run_key_messages.py` 10/10; `python tests/test_key_messages_patch.py` 8/8; `git diff --check main...HEAD` clean (no whitespace errors).
- Commit `1d38d34`: Task 2.3 done. `adapter.py` registers all seven required artifacts and prevents incomplete output from reaching `complete`; `server.py` returns six public artifacts in fixed contract order with exact filenames and MIME types, keeps `report_json` internal, and safely skips legacy artifact rows; `run.py` lists the six final public filenames; `tests/test_run_artifacts.py` is the focused direct check.
  - Verified: `python -m py_compile adapter.py server.py run.py tests/test_run_artifacts.py` clean; `python tests/test_run_artifacts.py` 9/9.
- Worktree `YouTube Intelligence-wt-2.1` on branch `work/wave2-task-2.1`, HEAD `36e294b`, fast-forwarded into `main` and not deleted.
- Commit `8d07da3798ee11a209b8b247b7197a10341b57e7`: finished Task 2.4 Report JSON, Session comment count, `KeyMessageDraft.revision`, Task 2.5 backend key-visual removal, and Task 2.6 passing QA.
  - All 26 listed commands exited 0. Evaluator-reported totals: 125 assertions across 14 test files, all passing.
  - Per-script results: `test_db_schema.py` 5/5; `test_run_concurrency_guard.py` 3/3; `test_run_key_messages.py` 10/10, after repairing its report-writer mocks to create the six required files; `test_brief_key_messages.py` 5/5; `test_report_themes_csv.py` 2/2 each run; `test_report_sentiment_emotions_csv.py` 2/2 each run; `test_report_key_messages_csv.py` 2/2 each run; `test_classify.py` 7/7; `test_run_artifacts.py` 10/10; `test_evidence.py` 27/27 each run; `test_asset_extraction.py` 3/3; `test_key_messages_patch.py` 9/9; `test_key_messages_draft.py` 9/9; both benchmark self-checks passed.
  - Every `py_compile` command exited 0.
  - `git diff --check` exited 0: no whitespace errors, one CRLF-to-LF advisory for `config-template.py`.
  - Restricted backend key-visual search (Task 2.5 listed files, `KEY_VISUALS|isKeyVisual|keyvis`): zero matches.
  - Old backend artifact/report-key search: zero matches.
  - No excluded path (`.env`, `config.py`, `data/`, `output/`, `benchmark-data/private/`) is tracked.
  - Seven stored artifacts and six public artifacts confirmed; `report_json` internal.
  - `session-ses_004f.md` remains untracked, scheduled for removal before commit.
  - Commit has not been pushed at this record point.
  - Resume point: commit this execution-record update, push `main`, then begin Wave 3 Task 3.1 in a later frontend session.

### Cross-chat handoff

- Treat this file as the cross-chat execution record. At the start of a new session, read `AGENTS.md`, then read `Delivery state`, this handoff, the next task, and that task's named prerequisite outputs.
- Continue from the stated resume point. Treat existing uncommitted changes as the baseline and do not discard, overwrite, or duplicate them.
- After each task, update `Delivery state` with completion status, changed files, exact verification that ran, commit state, and the next resume point.
- Record only commands that actually ran. Mark omitted checks as not run instead of inferring a pass.
- Keep completed task contracts in place. They remain the implementation record and interface reference for later tasks.

### Shared contracts

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

- `comments.csv`: `video_id,group,comment,likes,language,theme,sentiment,sentiment_confidence,emotion,emotion_confidence`, followed by one `key_message_<stable-id>` boolean column in Key Message order. Missing source values are empty. No internal/debug columns are exported.
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

### Wave 2 - Finish backend and outputs

#### Task 2.1 - Validate run integration

- Files: `db.py`, `server.py`, `adapter.py`, `pipeline/brief.py`, `tests/test_db_schema.py`, `tests/test_run_concurrency_guard.py`, `tests/test_run_key_messages.py`, `tests/test_brief_key_messages.py`.
- Symbols: `db.init`; `server._ser_run`, `server.start_run`, `server.update_brief_points`, `server.proceed_run`; `adapter._set_run_stage`, `adapter._push`, `adapter._load_session_key_messages`, `adapter._replace_brief_points`, `adapter._execute`; `brief.reconcile`.
- Consumes: ordered `key_messages` rows and `collect.fetch(...) -> (comments_df, meta_df)` where `meta_df` contains transcripts.
- Produces: `runs.stage TEXT NOT NULL DEFAULT 'queued'`; immutable run `brief_points`; persisted `brief_pause`; the run/error contracts above.
- Behavior: reject queued/running before deleting files or rows; terminal overwrite remains. Snapshot Session messages before collection. Reconcile after collection. Edited rows remain byte-for-byte except normalized order. Generated case-insensitive label matches retain IDs and receive transcript description updates. Unmatched existing rows remain. Additions append with server IDs. Empty setup may draft from transcripts. Atomically replace run rows before pause. Never write Session `key_messages` during a run. Session `updated_at` may change.
- Verification:
  - `python -m py_compile db.py server.py adapter.py pipeline/brief.py`
  - `python tests/test_db_schema.py`
  - `python tests/test_run_concurrency_guard.py`
  - `python tests/test_run_key_messages.py`
  - `python tests/test_brief_key_messages.py`
- Stop: no command may call YouTube, Ollama, or HuggingFace. Stop if a listed symbol is absent.

#### Task 2.2 - Finish exports

- Files: `pipeline/report.py`, `tests/test_report_themes_csv.py`, `tests/test_report_sentiment_emotions_csv.py`, `tests/test_report_key_messages_csv.py`, `tests/test_classify.py`.
- Symbols: `report.export`, `report._label_counts_csv`, `report._label_to_pt_col`, `report._key_messages_csv`.
- Consumes: raw comment DataFrame columns named in the CSV contract and the ordered `transfer` rows `group,point,echoed_pct,n` used only to map applicable messages.
- Produces: the five CSVs in the CSV contract. `report.pdf` remains `report.render` output.
- Behavior: remove `summary.csv`, `chart_transfer.csv`, and `chart_themes.csv` generation. Make `comments.csv` match the exact schema. Update old assertions in `test_classify.py`.
- Verification:
  - `python -m py_compile pipeline/report.py tests/test_report_themes_csv.py tests/test_report_sentiment_emotions_csv.py tests/test_report_key_messages_csv.py tests/test_classify.py`
  - `python tests/test_report_themes_csv.py`
  - `python tests/test_report_sentiment_emotions_csv.py`
  - `python tests/test_report_key_messages_csv.py`
  - `python tests/test_classify.py`
- Stop: header, order, rounding, null, zero-denominator, and empty-input assertions must all exist before completion.

#### Task 2.3 - Register artifacts

- Files: `adapter.py`, `server.py`, `run.py`, `tests/test_run_artifacts.py`.
- Symbols: `adapter._execute`; `server._ARTIFACT_TIER`, `server._ser_artifact`, `server._ser_run`, `server.download_artifact`, `server.get_report`; `run.main` output handling.
- Consumes: seven files in the artifact contract.
- Produces: seven `run_artifacts` rows and six ordered public `Artifact` objects.
- Behavior: delete all `summary_csv`, `chart_transfer_csv`, and `chart_themes_csv` mappings. Keep internal report retrieval. Apply the exact missing-artifact error. CLI output uses final filenames.
- Verification: create `tests/test_run_artifacts.py` as a direct assert script with `main()`, temporary DB/storage, and no framework; run `python -m py_compile adapter.py server.py run.py tests/test_run_artifacts.py` and `python tests/test_run_artifacts.py`. Assert seven rows, six public objects in fixed order, exact MIME/filenames, working report endpoint, and no old kinds.
- Depends on: Task 2.2.
- Stop: do not edit frontend artifact consumers.

#### Task 2.4 - Finalize report JSON

- Files: `adapter.py`, `tests/test_evidence.py`.
- Symbols: `adapter._build_report_json`, `adapter._build_evidence`.
- Consumes/Produces: deterministic DataFrames to exact `ReportJson`.
- Behavior: use only the six exact top-level keys and nested fields above. Applicability comes from exact `transfer_table.group` values matched against trim/case-insensitive `point`; aggregate every applicable group per message; the denominator counts non-null applicability cells, the count is exact `True` cells; included messages with no applicability keep zero values as a shell row, not an omission. `overallTransfer` counts each Session comment once when it mentions at least one message applicable to its group, over all Session comments. Merge Theme and Emotion labels case-insensitively, keep the first-seen spelling, and use non-empty category labels as the denominator. Round every report percentage to one decimal. Sentiment `baseN` recognizes positive, negative, neutral; neutral counts toward the denominator; unknown/null does not. Evidence: drop empty text, coerce invalid likes to 0, leave missing video ID as an empty string; order by likes descending, then text length descending, then source order, capped at eight. Key Message Sentiment evidence takes up to four positive and four negative first, then backfills from the best unselected recognized comments including neutral. The same source comment may appear across different metrics but not twice inside one metric. Metric IDs retain existing `m-t-*`/`m-is-*` values; an empty slug falls back to `message`; colliding slugs get `-2`, `-3`, ... applied identically to both members of a paired ID.
- Verification: `python -m py_compile adapter.py tests/test_evidence.py`; `python tests/test_evidence.py`. Assert the exact key set and nested types at every level (no extra/missing fields at any depth); fixed fixture numbers; unchanged metric IDs; absence of old keys; a message applicable to its own group counts correctly; a message with only unrelated-group rows produces a zero shell, not an omission; aggregation sums correctly across two or more applicable groups for one message; an all-null applicability column yields a zero denominator, not a crash or `null` percent; a comment applicable to two messages in `overallTransfer` counts once; Theme/Emotion labels differing only by case merge into one entry under the first-seen spelling; two slugs colliding produce suffixed IDs consistent across paired metrics; `baseN` includes neutral and excludes unknown/null; Key Message Sentiment evidence is balanced up to four/four and backfills without exceeding eight; empty-text comments are excluded from evidence; invalid likes normalize to 0 instead of erroring; tie-breaking on likes and text length is deterministic across repeated runs; every evidence list is capped at eight; and all emitted values are JSON-serializable (no NaN, no numpy types).
- Depends on: Task 2.3, because both edit `adapter.py`.
- Stop: do not add compatibility aliases or model-generated numbers.

#### Task 2.5 - Remove backend key visuals

- Files: `pipeline/config_types.py`, `adapter.py`, `run.py`, `config-template.py`, `server.py`, `pipeline/report.py`, `tests/test_asset_extraction.py`, `tests/test_report_themes_csv.py`, `tests/test_report_sentiment_emotions_csv.py`, `tests/test_report_key_messages_csv.py`.
- Symbols: `PipelineConfig.KEY_VISUALS`; `adapter._build_config`; `run._load_cfg`; `server._ser_asset`, `server.set_key_visual`; `report.render` key-visual HTML/CSS and now-unused `base64` import.
- Consumes/Produces: image User Inputs remain uploaded, stored, and passed to `brief.draft_from_inputs`; no image is selectable or embedded as a report key visual.
- Behavior: remove every named backend symbol and config field. Do not remove image validation, paths, multimodal observations, or uploaded-image serving.
- Verification:
  - `python -m py_compile pipeline/config_types.py adapter.py run.py config-template.py server.py pipeline/report.py`
  - `python tests/test_asset_extraction.py`
  - Run the three report tests from Task 2.2.
  - Search only these listed files for `KEY_VISUALS|isKeyVisual|keyvis`; zero matches are allowed.
  - `test_asset_extraction.py` asserts the retired key-visual field is absent from the asset response by checking the response's key set, without spelling the retired key literal in the assertion.
- Depends on: Tasks 2.2-2.4.
- Stop: frontend compatibility methods are Wave 3 scope.

#### Task 2.6 - Read-only QA and commit

- QA files: every file changed by Tasks 2.1-2.5, read-only.
- QA commands: every command listed in Tasks 2.1-2.5, then `python tests/test_asset_extraction.py`, `python tests/test_key_messages_patch.py`, `python tests/test_key_messages_draft.py`, `python tests/test_evidence.py`, `python tests/bench_qwen.py --self-check`, `python tests/bench_classifiers.py --self-check`, `git diff --check`, and `git status --short`.
- QA rejects secrets, `.env`, `config.py`, `data/`, generated reports, old artifact names, old report keys, and backend key-visual references.
- Owners fix reported defects in their own task files. After a complete rerun passes, an explicitly authorized integration agent creates one Wave 2 commit. It does not push.

### Wave 3 - Frontend integration

#### Task 3.1 - Remove dropped UI

- Files: `app/index.html`, `app/app.js`, `app/live.js`, `app/style.css`, `app/self-check.html`.
- Symbols to remove: `#sb-chat`; home `.hero-prompt`; Session/Files `.searchbox`; `.discovery` and `#discovery-h`; `ocrNotice`; lens cards; `[data-kv]`, `.kv-toggle`, key-visual copy and fixture fields.
- Produces: no visible chat, search, discovery, OCR, lens, or key-visual surface.
- Retain `demoApi.setKeyVisual(campaignId, assetId)` and matching live method with no caller. Both return `Promise.reject(ApiError)` using the locked unsupported-feature error.
- Verification: `node --check app/app.js`; `node --check app/live.js`; search only the five listed files for the removed selectors/copy, allowing only the two compatibility method definitions. Serve `app/` with `python -m http.server 8765 --bind 127.0.0.1 --directory app`, open `http://127.0.0.1:8765/self-check.html` with `webapp-testing`, and require every displayed assertion to pass with no browser-console error.
- Depends on: Wave 2 pass.
- Stop: do not change any existing `demoApi` signature.

#### Task 3.2 - Add Session Key Message methods

- Files: `app/live.js`, `app/app.js`, `app/self-check.html`.
- Symbols: `window.__liveApi`, `demoApi`, `wrapForLive`.
- Produces identical methods:

```ts
getKeyMessages(sessionId: string): Promise<KeyMessageDraft>;
draftKeyMessages(sessionId: string): Promise<KeyMessageDraft>;
updateKeyMessages(sessionId: string, messages: KeyMessageInput[]): Promise<KeyMessageDraft>;
```

- `getKeyMessages` reads `GET /api/sessions/{id}` and returns `.keyMessages`; the others use the shared routes. Rejections carry `ApiError` fields directly.
- Demo Session state adds `keyMessages:{status:"empty",messages:[],error:null,revision:0}`. User Input methods keep their current signatures and never call drafting.
- Verification: `node --check app/live.js`; `node --check app/app.js`; self-check mocked fetch asserts exact methods, URLs, bodies, return shapes, and demo/live parity.
- Depends on: Task 3.1.
- Stop: do not render the editor in this task.

#### Task 3.3 - Build setup editor

- Files: `app/app.js`, `app/style.css`, `app/self-check.html`.
- Symbols: `renderCampaign`, `uploadFiles`, the existing `#c-article` submit listener, the existing `[data-rm-asset]` click branch, and new functions `renderSetupKeyMessages`, `saveSetupKeyMessages`, `requestKeyMessageDraft`.
- State: `{inFlight:boolean,requestedRevision:number,acceptedRevision:number,rerunRequested:boolean,dirtyIds:Set<string>,status:KeyMessageDraft.status}` per Session.
- Flow: finish and render a successful asset mutation first, then request drafting. While drafting, another successful mutation increments `requestedRevision` and sets one `rerunRequested`; it does not start another call. Accept a response only when its request revision equals the latest requested revision. Otherwise run exactly one latest request. Preserve dirty label, description, included, and order fields. Failure with prior messages becomes stale; failure without messages becomes failed. Failed asset mutation never drafts.
- Controls: Add appends `id:null`; Delete removes a row; Save validates and full-PATCHes label/description; include and Up/Down reorder full-PATCH immediately. Save is disabled while pending. Navigation with dirty text opens Continue/Cancel confirmation: `Unsaved Key Message changes will be lost. Continue?`
- Copy: `Drafting Key Messages...`; `Key Messages may be out of date.`; `Key Message drafting failed.`; `Retry`; stale-run confirmation `Key Messages may be out of date. Continue and reconcile them with video transcripts?` with Continue/Cancel.
- Accessibility: status region `aria-live="polite"`; editor `aria-busy`; invalid fields use `aria-invalid` and `aria-describedby`; background updates do not replace focused controls; Up/Down are buttons usable with Enter/Space; pending controls are disabled.
- Verification: self-check covers first draft, save-before-draft, failed asset, one coalesced rerun, obsolete response rejection, stale/failed Retry, dirty preservation, add/delete, validation limits, immediate include/reorder PATCH, explicit text Save, all-excluded setup, dirty navigation, stale-run warning, keyboard and ARIA states.
- Depends on: Task 3.2.
- Stop: do not add transcript-change markers.

#### Task 3.4 - Restore and edit `brief_pause`

- Files: `app/live.js`, `app/app.js`, `app/self-check.html`.
- Symbols: `window.__liveApi.getRun`, `renderRun`, `renderBriefReview`, `subscribeRun`, `updateBriefPoints`, `proceedRun`.
- Behavior: render the initial `RunSnapshot`. If `stage==="brief_pause"`, show editable rows immediately. Add/delete uses `id:null`; Save sends the complete ordered run list, then Proceed. Enforce at least one included before Proceed. Disable pending controls. A failed save retains values, order, and focus. Apply the GET/SSE precedence contract. No transcript-change badges.
- Verification: `node --check app/live.js`; `node --check app/app.js`; serve and open `app/self-check.html` with the Task 3.1 command and require checks for paused reopen with no SSE, add/edit/delete/reorder, all-excluded rejection, save/proceed, stale-SSE rejection, and edit retention after failure.
- Depends on: Task 3.3 and Wave 2 persisted stage.
- Stop: backend restart recovery is not added.

#### Task 3.5 - Six downloads and demo parity

- Files: `app/app.js`, `app/live.js`, `app/self-check.html`.
- Symbols: `finalizeRun`, `renderResults`, `renderFiles`, `downloadBlob`, `demoApi.getArtifact`, `demoApi.listFiles`, `window.__liveApi.getArtifact`, `window.__liveApi.listFiles`.
- Behavior: show the six public artifacts in contract order. Fetch every live blob before download. Use server `Content-Disposition`. Never expose `report_json`. Demo fixtures use exact names, MIME types, and deterministic header-valid CSV blobs. Missing artifact controls are disabled with `This file was not generated.` A failed download announces `Download failed. Try again.` in a live error region. Remove references to undefined Files variables and use the handler's artifact parameter.
- Verification: `node --check app/live.js`; `node --check app/app.js`; serve and open `app/self-check.html` with the Task 3.1 command and require checks for six ordered controls/files, no old names or report JSON, blob flow, filenames/MIME, disabled missing state, and announced failure.
- Depends on: Task 3.4.
- Stop: do not invent unavailable artifacts.

#### Task 3.6 - Frontend read-only QA and commit

- QA files: all Wave 3 files plus new `tests/e2e_product_flow.py`, read-only after its owner writes it.
- E2E owner files: `tests/e2e_product_flow.py` only. Use Playwright from `webapp-testing`; start the backend on `127.0.0.1` with a fresh port and temporary DB/storage; replace YouTube, Ollama, and classifier calls through explicit test mocks; no external network or real model.
- Commands: `node --check app/app.js`; `node --check app/live.js`; serve `app/` with `python -m http.server 8765 --bind 127.0.0.1 --directory app`, open `http://127.0.0.1:8765/self-check.html` through `webapp-testing`, require every assertion and console check to pass, stop that server, then run `python tests/e2e_product_flow.py`.
- E2E cases: Session creation, upload then draft, stale warning, setup add/edit/delete/order, run start, persisted pause reopen, run edit/proceed, completion fixture, six downloads, report JSON hidden, keyboard/focus/ARIA, reduced motion, and no dropped controls.
- QA reports defects only. Owners fix them. After a full pass, an explicitly authorized integration agent creates one Wave 3 commit and does not push.

### Wave 4 - Benchmarks and final verification

#### Benchmark files and schemas

- Files: `.gitignore`, `docs/benchmark-runbook.md`, `benchmark-results/classifiers.json`, `benchmark-results/qwen.json`, `benchmark-results/verification.md`.
- Add `benchmark-data/private/` to `.gitignore`.
- Private files: `labels.jsonl`, `sentiment-predictions.jsonl`, `emotion-predictions.jsonl`, `qwen-corpus.jsonl`, `qwen-comparison.jsonl`.
- Labels row: `{"id":string,"text":string,"language":"id"|"en"|"mixed","sentiment":"negative"|"neutral"|"positive","emotion":"anger"|"fear"|"joy"|"sadness"|"other_neutral"}`.
- Prediction row: `{"id":string,"sentiment":string,"confidence":number,"model":string}` or the same with `emotion`.
- Qwen corpus row: `{"id":string,"video_id":string,"text":string,"allowed_theme_labels":string[],"allowed_key_message_ids":string[]}`.
- Qwen comparison row: `{"id":string,"true_theme":string,"predicted_theme":string,"true_key_message_ids":string[],"predicted_key_message_ids":string[]}`.
- Files are UTF-8 JSONL, one object per line, unique non-empty IDs. Loaders reject duplicates, missing fields, invalid enums, and malformed JSON with file/line errors.
- `other_neutral` is evaluation-only. Report its prevalence and coverage; exclude it from four-label Emotion Macro F1. It is not a shipping label.

#### Task 4.1 - Prepare labels and runbook

- Files: `.gitignore`, `docs/benchmark-runbook.md`; private files are local outputs and never staged.
- Produce at least 600 resolved labels: 200 `id`, 200 `en`, 200 `mixed`. Each stratum includes at least 25 short/emoji and 25 negation/sarcasm/slang/mixed-affect rows. Two bilingual annotators label independently and resolve disagreement before model scoring. The runbook records counts and agreement, not private text.
- Symbols: add or validate `bench_classifiers.main` support for `--labels PATH --validate-only`; this mode loads and validates labels, prints counts by language and label, performs no model work, and exits zero only for a valid file.
- Verification: `python tests/bench_classifiers.py --self-check`; `python tests/bench_classifiers.py --labels benchmark-data/private/labels.jsonl --validate-only`; `git status --short` must not list private files.
- Stop: no benchmark runs before labels are locked.

#### Task 4.2 - Classifier benchmark

- Files: `tests/bench_classifiers.py`, `benchmark-results/classifiers.json`.
- Candidates: sentiment multilingual `cardiffnlp/twitter-xlm-roberta-base-sentiment`; routed `id` `w11wo/indonesian-roberta-base-sentiment-classifier`, `en` `cardiffnlp/twitter-roberta-base-sentiment-latest`, `mixed` the multilingual candidate. Emotion multilingual `MilaNLProc/xlm-emo-t`; routed diagnostic `id` `StevenLimcorn/indonesian-roberta-base-emotion-classifier`, `en` `j-hartmann/emotion-english-distilroberta-base`, `mixed` the multilingual candidate.
- CLI contract: `python tests/bench_classifiers.py --labels benchmark-data/private/labels.jsonl --sentiment-predictions benchmark-data/private/sentiment-predictions.jsonl --emotion-predictions benchmark-data/private/emotion-predictions.jsonl --output benchmark-results/classifiers.json`.
- Output fields: `generatedAt`, `hardware`, `systems[]` with `task,model,revision,license,downloadBytes,runtimeSeconds,gpuMemoryMiB,overallMacroF1,accuracy,strata,labels,confusion,coverage`, and `decision`.
- Acceptance: multilingual ships only when no `id`, `en`, or `mixed` stratum is more than 0.02 Macro F1 below routed, multilingual beats routed on `mixed`, and every required shipping label has F1 at least 0.60. Sentiment uses routing when multilingual fails. Emotion never ships incompatible routed vocabularies; select or train a shared four-label head instead. Missing or unclear redistribution license disqualifies shipping. Pin selected revisions.
- Verification: `python tests/bench_classifiers.py --self-check`; run the CLI; rerun and compare deterministic metrics.
- Depends on: Task 4.1.

#### Task 4.3 - Qwen benchmark

- Files: `tests/bench_qwen.py`, `benchmark-results/qwen.json`.
- Candidates: `qwen3:14b-q4_K_M` primary and `qwen3:8b-q4_K_M` fallback challenger. `qwen3-vl:8b-instruct-q4_K_M` remains only for image User Inputs and is not part of the text benchmark.
- CLI contract: `python tests/bench_qwen.py --model qwen3:14b-q4_K_M --corpus benchmark-data/private/qwen-corpus.jsonl --comparison benchmark-data/private/qwen-comparison.jsonl --limit 3000 --batch-size 20 --output benchmark-results/qwen.json`; repeat with the 8B model.
- Output fields: `generatedAt,hardware,ollamaVersion,model,modelDigest,batchSize,commentsProcessed,elapsedSeconds,measured3000,projected3000Seconds,gpuMemoryBeforeMiB,gpuMemoryAfterMiB,malformedFinalBatches,invalidThemeCount,invalidKeyMessageIdCount,themeMacroF1,keyMessageMacroF1,mixedThemeMacroF1,mixedKeyMessageMacroF1,passed`.
- Key Message Macro F1 is the macro mean of one-vs-rest F1 across allowed IDs. Theme Macro F1 uses exact Theme labels. A candidate passes only when an actual 3,000-row run is at most 2,400 seconds, malformed final batches are zero after retry, invalid labels/IDs are zero, Theme Macro F1 is at least 0.75, Key Message Macro F1 at least 0.70, and each mixed score is no more than 0.05 below its overall score. Among passing models choose higher quality, then lower elapsed time, then smaller model.
- Verification: `python tests/bench_qwen.py --self-check`; run the exact 14B command above; run `python tests/bench_qwen.py --model qwen3:8b-q4_K_M --corpus benchmark-data/private/qwen-corpus.jsonl --comparison benchmark-data/private/qwen-comparison.jsonl --limit 3000 --batch-size 20 --output benchmark-results/qwen-8b.json`; merge both sanitized records into `benchmark-results/qwen.json` with `candidates:[...]` and the selected `decision`; do not label a projection as measured.
- Depends on: Task 4.1.

#### Task 4.4 - Apply model decisions

- Files: `pipeline/config_types.py`, `adapter.py`, `config-template.py`, `pipeline/analyze.py`, `app/app.js`, `tests/test_classify.py`, `tests/test_model_labels.py`.
- Symbols: `PipelineConfig.TEXT_MODEL`, `PipelineConfig.VISION_MODEL`, `PipelineConfig.SENTIMENT_MODEL`, `PipelineConfig.EMOTION_MODEL`; `adapter._build_config`; `analyze.affect`; `app.js` constants or literal arrays that populate the Sentiment and Emotion evidence filter pills; new direct-test `main()` in `tests/test_model_labels.py`.
- Consumes: `benchmark-results/classifiers.json` and `benchmark-results/qwen.json`, both with `passed:true` decisions.
- Produces: exact pinned defaults from results, no provider fallback or model tiers. Vision remains the named Qwen VL model while image User Inputs exist. `tests/test_model_labels.py` directly checks normalized Sentiment labels and the selected fixed Emotion labels.
- Verification: `python -m py_compile pipeline/config_types.py adapter.py config-template.py pipeline/analyze.py tests/test_model_labels.py`; `python tests/test_model_labels.py`; `python tests/test_classify.py`; run Ollama preflight and one small real inference for each selected model and record it in `verification.md`.
- Stop: if either benchmark has no passing decision, do not change shipping defaults; report the blocker.

#### Task 4.5 - Full verification record

- Files: `benchmark-results/verification.md` only. Defects return to owning tasks.
- Run and record pass/fail/not-run for:
  - `python tests/test_db_schema.py`
  - `python tests/test_asset_extraction.py`
  - `python tests/test_key_messages_patch.py`
  - `python tests/test_key_messages_draft.py`
  - `python tests/test_brief_key_messages.py`
  - `python tests/test_run_concurrency_guard.py`
  - `python tests/test_run_key_messages.py`
  - `python tests/test_run_artifacts.py`
  - `python tests/test_report_themes_csv.py`
  - `python tests/test_report_sentiment_emotions_csv.py`
  - `python tests/test_report_key_messages_csv.py`
  - `python tests/test_classify.py`
  - `python tests/test_evidence.py`
  - `python tests/test_model_labels.py`
  - `python tests/bench_qwen.py --self-check`
  - `python tests/bench_classifiers.py --self-check`
  - `node --check app/app.js`
  - `node --check app/live.js`
  - Browser `app/self-check.html`
  - `python tests/e2e_product_flow.py`
- The E2E record explicitly covers real PDF creation, seven stored/six public artifacts, overwrite of a terminal prior run, active-run rejection, tab-close continuation, pause reopen, URL/playlist/duplicate validation, upload extension/10 MB validation, article scheme/timeout behavior, error shapes, and six downloads.
- Record date, commit, OS, Python, Node, browser, GPU, commands, exit codes, and concise evidence. Never mark an unrun item passed.

### Wave 5 - Documentation and operations

Every task loads `compact-technical-writing` and consumes `benchmark-results/verification.md` plus only its listed documentation files. It does not inspect source.

#### Task 5.1 - Product record

- Files: `README.md`, `CHANGELOG.md`, `benchmark-results/verification.md` read-only.
- Update README sections for the final Session flow, setup drafting, transcript reconciliation, always-on Sentiment/Emotion, six downloads, local models, limits, and tab-close boundary. Remove all current-gap claims that verification marks complete.
- Move completed Wave entries under a dated release while preserving this mission brief decisions and the July 2026 history.
- Verification: `python tests/check_docs.py README.md CHANGELOG.md`.

#### Task 5.2 - Setup and benchmark runbook

- Files: `docs/setup.md`, `docs/benchmark-runbook.md`, `requirements.txt`, `requirements-server.txt`, `benchmark-results/verification.md` read-only.
- Document exact selected tags/revisions, Ollama pull/start/preflight, classifier first-run downloads, measured disk/GPU/runtime, YouTube key, environment fields, clean deletion of `data/`, server and CLI commands, PDF engine, every direct verification command, benchmark commands, and troubleshooting. Remove Gemini key/provider/fallback instructions. State that direct scripts are not a pytest suite.
- Change dependency files only when `verification.md` names a required installed package absent from them.
- Verification: `python tests/check_docs.py docs/setup.md docs/benchmark-runbook.md`; inspect staged diff for secrets.

#### Task 5.3 - Architecture

- Files: `docs/architecture.md`, `benchmark-results/verification.md` read-only.
- Document exactly eight tables; setup-time extraction; Session `key_messages`; draft coalescing and stale/failed states; immutable run `brief_points`; transcript reconciliation; persisted `brief_pause`; active-run guard and terminal overwrite; Ollama and HuggingFace boundaries; Python counting; seven stored/six public artifacts; report JSON; demo/live boundary; tab close versus backend restart. Remove key visuals, cross-group reports, and dropped/deferred feature sections.
- Verification: `python tests/check_docs.py docs/architecture.md`.

#### Task 5.4 - API reference

- Files: `docs/api-reference.md`, `benchmark-results/verification.md` read-only.
- Copy the Shared HTTP, Key Message, Run/SSE, Artifact, report JSON, CSV, validation, and extraction contracts from this entry. Include exact routes, bodies, statuses, errors, casing rule, MIME, filenames, order, and failed article empty-text behavior.
- Verification: `python tests/check_docs.py docs/api-reference.md`.

#### Task 5.5 - Contributor rules

- Files: `AGENTS.md`, `benchmark-results/verification.md` read-only.
- Update identifier mapping for `key_messages`, `brief_points`, `keyMessages`, and `keyMessageSentiment`. Add the final direct commands. Remove Gemini and backend key-visual rules. Preserve thread-local YouTube clients, grounded-only Key Messages, Python counting, required PDF, per-Session overwrite, `PipelineConfig`, frozen `demoApi` signatures, security, and accessibility.
- Verification: `python tests/check_docs.py AGENTS.md`.

#### Task 5.6 - Documentation QA and commit

- Owner files: `tests/check_docs.py` only. Create a stdlib direct script with `main()` that accepts Markdown paths, checks local relative links and headings, and returns nonzero on broken links.
- Read-only QA files: every Wave 5 file.
- Commands: `python tests/check_docs.py README.md CHANGELOG.md AGENTS.md docs/setup.md docs/benchmark-runbook.md docs/architecture.md docs/api-reference.md`; `git diff --check`; `git status --short`.
- Search current-product sections for `signal transfer|codebook|echoed|affect|Gemini|summary.csv|chart_transfer.csv|chart_themes.csv|transfers|ideaSentiment|KEY_VISUALS|key visuals`. Allowed matches are the unchanged July 2026 history, code identifiers inside backticks, and the explicit terminology mapping. `campaign` is allowed only as an internal identifier in backticks; user-facing prose uses Session.
- Confirm `.env`, `config.py`, `data/`, `output/`, and `benchmark-data/private/` are absent from Git. QA reports defects only. Documentation owners fix them. After a complete pass, an explicitly authorized integration agent commits Wave 5 documentation separately and does not push.

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

- 2026-08-13: Approved the Report JSON applicability and evidence contract (group/point matching, aggregation, denominators, merged Theme/Emotion labels, one-decimal percentages, sentiment `baseN`, balanced/backfilled evidence, metric-ID collision suffixes), the CSV-based Session comment count, the exposed `KeyMessageDraft.revision` semantics, and backend key-visual removal. Task 2.4 and Task 2.5 implemented against this contract with focused verification only; changes remain uncommitted pending Task 2.6.
- 2026-08-13: Recorded Task 2.3 completion at commit `1d38d34`, its focused 9/9 verification, and Task 2.4 as the next resume point. Added cross-chat handoff rules so future sessions continue without rediscovering or replacing completed work.
- 2026-08-13: Recorded Task 2.6's complete read-only QA rerun: 125 assertions across 14 test files passing, all `py_compile` and search checks clean, no excluded path tracked, changes still uncommitted. Next resume point is authorized integration cleanup, commit, and normal push of `main`, then Wave 3 Task 3.1.
- 2026-08-13: Committed Task 2.4, Task 2.5, and Task 2.6 at `8d07da3798ee11a209b8b247b7197a10341b57e7`; worktree clean immediately after. Push to `main` remains pending.
