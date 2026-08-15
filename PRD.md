# Product Requirements Document

The blueprint for this project, and the record of what has shipped against it. Newest first. The `docs/` files describe the system as it stands.

Entry format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). This project is not versioned; entries sit under dated releases or `[Unreleased]` for planned and in-progress work.

---

## [Unreleased]

This is the execution brief for the final product. It is a contract, not a list of suggestions. An implementation agent reads `AGENTS.md`, one task below, and only that task's listed files and named prerequisite outputs. It does not scan the repository.

### Locked decisions

- A 3,000-comment Qwen run must finish within 2 hours to ship. This is a release threshold, not a forced run cancellation.
- Closing a browser tab does not stop a run. Backend or computer restart recovery is out of scope.
- Product language support is Indonesian, English, and mixed Indonesian-English.
- Macro F1 selects classifiers. Accuracy is reported second.
- Benchmark hardware is one RTX 4060 Ti 16GB, 288 GB/s. The plain RTX 4060 ships 8GB and cannot hold `qwen3:14b-q4_K_M` without a CPU/GPU split.
- Qwen runs in non-thinking mode. Reasoning traces do not fit the release threshold, and closed-set classification against a fixed Theme book and a fixed Key Message list does not need them.
- Sentiment and emotion are one label per comment, not one label per Key Message. A comment's single sentiment applies to every Key Message it mentions.
- The sentiment and emotion producer is selected by benchmark, not assumed. Qwen in the merged labelling prompt competes against the HuggingFace encoders on the same labels. Macro F1 selects.
- If Qwen wins that benchmark, `sentiment_confidence` and `emotion_confidence` are removed from `comments.csv`. An LLM's self-reported confidence is not a calibrated probability, and a fabricated number is worse than an absent column.
- Users may add and delete Key Messages during setup and `brief_pause`. The server generates IDs for new rows.
- The benchmark answer key is hand-authored, not discovered. `docs/benchmark-runbook.md` holds a fixed six-entry Theme book and a fixed five-entry Key Message list, and those are the source of every corpus row's `allowed_theme_labels` and `allowed_key_message_ids`. The benchmark therefore measures labelling against a fixed reasonable book, not against the book the product would build from a real Session. Recorded as a limitation, not a result.
- Sentiment and Emotions codes are single characters resolved through a fixed map: `P` positive, `N` negative, `U` neutral; `A` anger, `F` fear, `J` joy, `S` sadness, `O` other_neutral. `neutral` takes `U` because `N` is negative Sentiment. An unmapped code is invalid and counts toward the matching invalid counter.
- Transcript reconciliation preserves edited messages, updates case-insensitive generated matches without changing IDs, keeps unmatched existing messages, and appends transcript additions.
- Label and description changes use an explicit Save control. Inclusion and ordering save immediately.
- Key visuals, chat, global search, source discovery, OCR, custom lenses, cross-group reports, run history, and cancellation are not part of the final product.
- Raw benchmark comments and predictions remain ignored under `benchmark-data/private/`. Only schemas, runbooks, and sanitized aggregate results are committed.

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

Resume at Wave 4 Task 4.1 label production. Every offline prerequisite is in place; the blockers are hardware and labels, not code. The benchmark hardware is not available on the current machine: it carries an RTX 3050 Laptop with 4096 MiB, which holds neither Qwen candidate, and no Ollama daemon runs. `torch` and `transformers` are installed, so encoder scoring runs on CPU once labels exist. Wave 4 task order stays 4.1, then 4.3, then 4.2, because Task 4.2 scores Qwen predictions that Task 4.3 produces.

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

- `comments.csv`: `video_id,group,comment,likes,language,theme,sentiment,sentiment_confidence,emotion,emotion_confidence`, followed by one `key_message_<stable-id>` boolean column in Key Message order. Missing source values are empty. No internal/debug columns are exported. Task 4.4 removes `sentiment_confidence` when Qwen wins sentiment and `emotion_confidence` when Qwen wins emotion, and rewrites this header line to the shipped result.
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
- The E2E test exists and runs. It found four open defects that mix product faults with harness faults. Tasks 3.6a-3.6d below close them in order. `app/app.js` and `tests/e2e_product_flow.py` are uncommitted and are the baseline; do not discard or duplicate them.
- Ordering is locked: repair the harness before the app. A harness whose console capture is inert cannot verify an app fix, and an assertion that fails on a non-leak must stop lying before it gates a commit.
- One commit covers Tasks 3.6a-3.6c, created in Task 3.6d after every listed command passes. No push.

##### Task 3.6a - Make the harness honest

- Files: `tests/e2e_product_flow.py` only.
- Console capture, line 744: `msg.text()` must read `msg.text`. `ConsoleMessage.text` is a string property in Playwright 1.61.0, so the call raises `TypeError: 'str' object is not callable` and `_CONSOLE_ERRORS` never fills, making `no_console_errors` pass vacuously. Line 745 already reads `msg.type` correctly as a property; leave it. Because the `TypeError` only fires inside the true branch, the lost events are exactly the error events the list exists to capture.
- No diagnostic dump shares this fault. The two dump blocks at lines 778-794 and 804-820 are `page.evaluate()` JavaScript strings that read DOM properties and touch no Playwright event object. Change only line 744.
- `report_json_never_exposed`, lines 664-666: replace the URL-only filter. `_REQUESTS` already records `kind` and `status` (`_register_network_capture`, lines 78-93), so the assertion can read them. A `GET /report` is not a leak in either direction: it returns 409 for an incomplete run (`server.py:1436-1437`), and it returns 200 for a completed one because `renderResults` fetches the report to draw the results screen (`app/app.js:2844` through `live.js:243`). Assert that no `report_json` artifact is exposed as a downloadable artifact: no public artifact carries kind `report_json` or filename `report.json`, and `RunSnapshot.artifacts` holds only the six public kinds. Keep the rendered-surface assertions at lines 653-662; they pass for real reasons.
- Correction to an earlier version of this contract: an assertion that `GET /report` never returns 200 is wrong and would fail a healthy completed run. Do not write it.
- Fix the uncheck loop at lines 515-517. `checked.count()` is evaluated once, but `paint()` rebuilds the whole subtree on every `change` (`app/app.js:2548`), so the loop bound and the live DOM can disagree. Re-query until no checked box remains.
- Behavior: change no assertion that currently passes for a real reason. Do not weaken a case to make it pass.
- Verification:
  - `python -m py_compile tests/e2e_product_flow.py`
  - `python tests/e2e_product_flow.py`
  - Prove the console capture works: emit one deliberate console error, confirm the capture records it, then remove that probe.
- Produces: `no_console_errors` that can fail, and `report_json_never_exposed` asserting exposure rather than traffic.
- Stop: do not edit `app/app.js` or any Wave 3 frontend file in this task.

##### Task 3.6b - Fix the `brief_pause` render defect

- Files: `app/app.js`; `tests/e2e_product_flow.py` only for the count assertion named below.
- Symbols: `briefPointsSnapshot` (`app/app.js:2387`), `renderBriefReview` (`2500`, source select `2503`, visibility `2505`, row markup `2518`), the `onEvent` brief branch (`2662-2667`), `briefRendered` (`2386`, `2663`, `2708`), the mount call site (`2707-2710`).
- Defect: on `brief_pause` reopen, `#brief-review` becomes visible and renders zero `.brief-item` rows. `app/app.js:2505` sets `hidden = false` before any row exists, so an empty source array produces exactly this symptom. This blocks `brief_pause_all_excluded_rejected`, `brief_pause_edit_and_proceed`, `run_completes`, and `six_downloads_in_order`, because `POST /api/runs/{runId}/proceed` never fires and the run stays pinned at `brief_pause`.
- The backend is correct. `server._ser_run` emits `briefPoints` in camelCase (`server.py:375`) with the five `_ser_brief_point` fields, and `live.getRun` (`app/live.js:187-189`) passes the JSON through untouched. Key casing is not a factor.
- Traced root cause, three compounding faults:
  - `briefPointsSnapshot` is captured once at mount (`app/app.js:2387`) from `run.briefPoints` and never refreshed. `demoApi.getRun` is called exactly once, at `app/app.js:2301`. A mount that happens before the run reaches `brief_pause` freezes an empty array, and the later paint renders zero rows from it. No code re-fetches `/api/runs/{runId}` after mount.
  - `app/app.js:2503` reads `(seedPoints || briefPointsSnapshot)`. An empty array is truthy, so an empty seed silently wins over a populated snapshot.
  - The SSE fallback at `app/app.js:2666` is dead in live mode. `adapter._push` (`adapter.py:135-143`) emits only `run_id`, `stage`, `message`, `pct`, and `detail`, never `brief_points`; at `brief_pause` `detail` is a bare count string (`adapter.py:1098-1100`) that `parseDetailStr` resolves to `null`. So live mode always falls back to the frozen snapshot.
- Fix direction: `brief_pause` must render from a snapshot that is current at paint time, not at mount time. Re-fetch the run snapshot when entering `brief_pause`, and treat an empty array as absent when selecting the source. Do not rely on SSE carrying `brief_points`; the backend does not send it.
- Test defect in the same case, fix it here: `tests/e2e_product_flow.py:508-510` asserts `.brief-item` count equals the *included* count from the snapshot. `app/app.js:2518` renders every point and marks excluded ones with the `excluded` class, which is the specified behavior. Assert against the total `briefPoints` count, and assert the excluded rows carry the class.
- Correction to an earlier version of this contract: the suspected cause was a `briefPointIds` key present in the self-check fixture and absent from `_ser_run`. That is wrong. `briefPointIds` is returned only by the demo `getRun` (`app/app.js:876-887`) and no brief-path code reads it. The real reason `app/self-check.html` stays green is that its fixture always supplies populated `briefPoints` at mount, so the frozen-snapshot fault cannot appear.
- Verification:
  - `node --check app/app.js`
  - `python -m py_compile tests/e2e_product_flow.py`
  - Browser `app/self-check.html` through `webapp-testing`: every assertion passes, zero console errors, zero page errors, zero failed requests
  - `python tests/e2e_product_flow.py`: `brief_pause_reopen_persisted`, `brief_pause_all_excluded_rejected`, `brief_pause_edit_and_proceed`, `run_completes`, and `six_downloads_in_order` all pass
- Depends on: Task 3.6a, because its console capture is the evidence this task is verified against.
- Stop: do not change the run/SSE precedence contract, do not add backend restart recovery, and do not add `brief_points` to the SSE payload.

##### Task 3.6c - Fix the retry row count

- Files: `app/app.js`; `tests/e2e_product_flow.py` only if the expected count below proves wrong after the app faults are fixed.
- Symbols: `kmMergeDraft` (`app/app.js:1321`, append pass `1373-1375`, survivor pass `1379-1384`), `kmLocalKey` (`1313`), `kmNormLabel` (`1308`), `requestKeyMessageDraft` (`1673`, coalesce guard `1676`), the `#km-retry` handler (`1943-1953`), `deferredRepaint` (`1725`), `isFocusInside` (`1729`), `acceptDraft` (`1981-1992`).
- Defect: `draft_failure_is_stale_then_retry` leaves three `.km-row` rows where two are expected after `#km-retry`, and the stale status message never clears.
- Traced causes, both product faults, both independently sufficient:
  - The retry repaint never runs. The `#km-retry` handler defers when focus sits inside the container (`app/app.js:1947-1951`). Clicking the button focuses it, and the button is inside the container, so `isFocusInside()` is true when the draft resolves. `rows` updates to the correct two, the DOM keeps the pre-retry three, and the "Drafting Key Messages..." message stays painted even though `st.status` was already overwritten at `1699`. Nothing flushes `deferredRepaint` for the retry button: the container `click` recovery at `1968-1971` fires synchronously before the await resolves, and the only other flush is the `blur` handler on `[data-km-f]` inputs (`1856-1858`).
  - A blank local row survives the merge. `kmMergeDraft` keeps every local row a draft did not claim (`1379-1384`). A row added through `#km-add` (`1906-1911`) has `id: null` and an empty label, so its `byId` key is a `local-*` key the server never returns (`1325`) and `kmNormLabel("")` is falsy so it never enters `byLabel` (`1327-1328`). It can never be claimed, so two drafted rows append beside it and the count reaches three.
- Also fix in the same task: `requestKeyMessageDraft` drops the retry's `onAccepted` when a draft is already in flight (`app/app.js:1676`). The coalesced rerun at `1704-1708` invokes the original caller's callback instead, so a retry clicked during an in-flight draft never updates the editor and `st.status` stays `"drafting"`.
- Correction to an earlier version of this contract: this defect was recorded as unattributed and possibly a wrong test expectation. The deferred-repaint fault is a real user-visible defect, so the fix belongs in `app/app.js`. Re-examine the expected count of two only after both faults are fixed, because `setup_edit_add_delete_order` renames a row earlier in the flow and `PATCH /key_messages` writes `edited=1` for every row (`server.py:876`), which `_merge_key_messages` preserves (`server.py:663-667`). If the surviving edited row is legitimate product behavior, correct the test expectation and record why.
- Verification:
  - `node --check app/app.js`
  - `python -m py_compile tests/e2e_product_flow.py` when the test changes
  - Browser `app/self-check.html`: no regression
  - `python tests/e2e_product_flow.py`: `draft_failure_is_stale_then_retry` passes
- Depends on: Task 3.6a.
- Stop: do not change the three baseline `app/app.js` fixes for the lost Save click, uncleared dirty state, or duplicate draft ID.

##### Task 3.6d - Read-only QA and one commit

- QA files: every file changed by Tasks 3.6a-3.6c, read-only.
- Confirm the verification tooling executes before trusting any result. A search or shell command that returns unrelated output is a blocked check, not a pass.
- QA commands:
  - `node --check app/app.js`
  - `node --check app/live.js`
  - `python -m py_compile tests/e2e_product_flow.py`
  - Browser `app/self-check.html` through `webapp-testing`: every assertion passes with zero console errors, zero page errors, and zero failed requests
  - `python tests/e2e_product_flow.py`: 15 of 15 cases pass and the process exits 0
  - `git diff --check`
  - `git status --short`
- QA rejects secrets, `.env`, `config.py`, `data/`, `output/`, `benchmark-data/private/`, and generated reports from Git. Remove the untracked `session-ses_004f.md` before the commit.
- QA reports defects only. Owners fix them in their own task files. After a complete rerun passes, an explicitly authorized integration agent creates one Wave 3 QA commit. It does not push.

### Wave 4 - Benchmarks and final verification

#### Benchmark files and schemas

- Files: `.gitignore`, `docs/benchmark-runbook.md`, `benchmark-results/classifiers.json`, `benchmark-results/qwen.json`, `benchmark-results/verification.md`.
- Add `benchmark-data/private/` to `.gitignore`.
- Private files: `labels.jsonl`, `sentiment-predictions.jsonl`, `emotion-predictions.jsonl`, `qwen-sentiment-predictions.jsonl`, `qwen-emotion-predictions.jsonl`, `qwen-corpus.jsonl`, `qwen-comparison.jsonl`.
- Labels row: `{"id":string,"text":string,"language":"id"|"en"|"mixed","sentiment":"negative"|"neutral"|"positive","emotion":"anger"|"fear"|"joy"|"sadness"|"other_neutral","true_theme":string,"true_key_message_ids":string[]}`. `true_theme` is one exact label from the fixed Theme book. `true_key_message_ids` holds zero or more fixed Key Message IDs; an empty list is a valid and common answer. The Sentiment and Emotions loader validates the first five fields and ignores the last two, so one file serves both benchmarks.
- Prediction row: `{"id":string,"sentiment":string,"confidence":number,"model":string}` or the same with `emotion`. The two Qwen prediction files use this identical row format, so they load through the existing prediction loader with no new schema.
- Qwen prediction rows carry `confidence` as the constant `1.0` and `model` as the Qwen tag. That constant is a schema filler, not a calibrated probability. No acceptance rule, selection rule, or exported column may read it.
- Qwen corpus row: `{"id":string,"video_id":string,"text":string,"allowed_theme_labels":string[],"allowed_key_message_ids":string[]}`. Both arrays are ordered, and that order is the index space the model emits against. Never reorder either array between the prompt and the decode.
- Qwen comparison row: `{"id":string,"true_theme":string,"predicted_theme":string,"true_key_message_ids":string[],"predicted_key_message_ids":string[]}`. Comparison rows store resolved labels and IDs, never indices.
- Files are UTF-8 JSONL, one object per line, unique non-empty IDs. Loaders reject duplicates, missing fields, invalid enums, and malformed JSON with file/line errors.
- `other_neutral` is evaluation-only. Report its prevalence and coverage; exclude it from four-label Emotion Macro F1. It is not a shipping label.

#### Task 4.1 - Prepare labels and runbook

- Files: `.gitignore`, `docs/benchmark-runbook.md`; private files are local outputs and never staged.
- Produce 150 resolved labels for the pilot: 50 `id`, 50 `en`, 50 `mixed`. Each stratum includes at least 25 short/emoji rows and at least 25 rows carrying negation, sarcasm, slang, or conflicting Sentiment and Emotions signals. One annotator labels in a single pass. The runbook records counts, not private text.
- The pilot proves the tooling, the file formats, and the annotation protocol. It does not decide what ships. Task 4.2 compares variants on margins as narrow as 0.02 Macro F1, and at 50 rows per stratum that margin sits inside the sampling noise. A ship decision needs a larger set, two independent annotators, and a Theme book from a real Session.
- Each labelled row also carries `true_theme` and `true_key_message_ids`, assigned against the fixed Theme book and Key Message list in `docs/benchmark-runbook.md`. Without them Task 4.3 has nothing to score `themeMacroF1` and `keyMessageMacroF1` against.
- Produce `qwen-corpus.jsonl` and `qwen-comparison.jsonl` from the labelled rows. Corpus rows carry the fixed Theme book as `allowed_theme_labels` and the fixed Key Message IDs as `allowed_key_message_ids`, both in runbook order. Comparison rows carry the resolved true labels; Task 4.3 fills the predicted fields.
- Every one of the five Key Message IDs must appear as a true label on at least 5 rows. Key Message Macro F1 averages one-vs-rest F1 across all five allowed IDs, so an ID that never appears scores `0.0` and lowers the mean. Two unmentioned IDs cap the score at 0.6, below the 0.70 floor, and the gate then fails on corpus composition rather than model quality.
- These labels score three systems, not two: the encoder multilingual variant, the encoder routed variant, and Qwen in the merged prompt. No extra labelling work follows from the third system.
- Symbols: add or validate `bench_classifiers.main` support for `--labels PATH --validate-only`; this mode loads and validates labels, prints counts by language and label, performs no model work, and exits zero only for a valid file.
- Verification: `python tests/bench_classifiers.py --self-check`; `python tests/bench_classifiers.py --labels benchmark-data/private/labels.jsonl --validate-only`; `git status --short` must not list private files.
- Stop: no benchmark runs before labels are locked.

#### Task 4.2 - Sentiment and emotion benchmark

- Files: `tests/bench_classifiers.py`, `benchmark-results/classifiers.json`.
- Encoder candidates: sentiment multilingual `cardiffnlp/twitter-xlm-roberta-base-sentiment`; routed `id` `w11wo/indonesian-roberta-base-sentiment-classifier`, `en` `cardiffnlp/twitter-roberta-base-sentiment-latest`, `mixed` the multilingual candidate. Emotion multilingual `MilaNLProc/xlm-emo-t`; routed diagnostic `id` `StevenLimcorn/indonesian-roberta-base-emotion-classifier`, `en` `j-hartmann/emotion-english-distilroberta-base`, `mixed` the multilingual candidate.
- Third candidate: Qwen in the merged labelling prompt, scored from the two Qwen prediction files Task 4.3 produces over the same `labels.jsonl` rows. It competes as one more `systems[]` entry against the same labels, strata, and metrics. This task runs no Ollama inference of its own.
- CLI contract: `python tests/bench_classifiers.py --labels benchmark-data/private/labels.jsonl --sentiment-predictions benchmark-data/private/sentiment-predictions.jsonl --emotion-predictions benchmark-data/private/emotion-predictions.jsonl --qwen-sentiment-predictions benchmark-data/private/qwen-sentiment-predictions.jsonl --qwen-emotion-predictions benchmark-data/private/qwen-emotion-predictions.jsonl --output benchmark-results/classifiers.json`. The two Qwen arguments are optional. Omitting them scores encoders only and records the Qwen systems as not run.
- Output fields: `generatedAt`, `hardware`, `systems[]` with `task,model,revision,license,downloadBytes,runtimeSeconds,gpuMemoryMiB,overallMacroF1,accuracy,strata,labels,confusion,coverage`, and `decision`. A Qwen system carries its Ollama tag as `model`, its digest as `revision`, and null for `downloadBytes` and `gpuMemoryMiB`, which belong to the Task 4.3 record.
- Acceptance between encoder variants is unchanged: multilingual ships only when no `id`, `en`, or `mixed` stratum is more than 0.02 Macro F1 below routed, multilingual beats routed on `mixed`, and every required shipping label has F1 at least 0.60. Sentiment uses routing when multilingual fails. Emotion never ships incompatible routed vocabularies; select or train a shared four-label head instead. Missing or unclear redistribution license disqualifies shipping. Pin selected revisions.
- Selection across all three systems uses Macro F1 per the locked rule, sentiment and emotion decided independently. Qwen wins a task only when it beats the best encoder variant on overall Macro F1 and is no more than 0.02 below that variant on any of the `id`, `en`, and `mixed` strata. A tie keeps the encoder, because Qwen already carries the run-time cost the encoders would add.
- Never read `confidence` from any prediction file. Qwen's value is a constant filler.
- `decision` names the selected producer for sentiment and for emotion, and states whether `sentiment_confidence` and `emotion_confidence` survive in `comments.csv`. They survive only when an encoder wins the matching task.
- Verification: `python tests/bench_classifiers.py --self-check`; run the CLI with all five files; rerun and compare deterministic metrics. Assert that a Qwen system appears in `systems[]`, that omitting the two Qwen arguments still exits zero, and that no acceptance path reads `confidence`.
- Depends on: Tasks 4.1 and 4.3.

#### Task 4.3 - Qwen benchmark

- Files: `tests/bench_qwen.py`, `benchmark-results/qwen.json`.
- Candidates: `qwen3:14b-q4_K_M` primary and `qwen3:8b-q4_K_M` fallback challenger. `qwen3-vl:8b-instruct-q4_K_M` remains only for image User Inputs and is not part of the text benchmark. No larger Qwen3 option is a candidate: `qwen3:30b-a3b-q4_K_M` is 19 GB and `qwen3:32b-q4_K_M` is 20 GB, so both exceed 16 GB before any KV cache, and forcing a fit at Q3 would trade away the exact-label precision this task measures.
- Merged prompt: one packed prompt per batch labels every comment in that batch with exactly one Theme, zero or more Key Messages, one sentiment, and one emotion. One model pass produces all four fields.
- Non-thinking mode is required, not an optimization. Set it explicitly and record the exact mechanism in the output record.
- Index encoding: the model emits a zero-based index into `allowed_theme_labels` and zero-based indices into `allowed_key_message_ids`, never label text and never UUIDs. Python resolves indices to exact labels and IDs. An out-of-range or non-integer index is invalid and counts in `invalidThemeCount` or `invalidKeyMessageIdCount`. Sentiment and emotion use single-character codes resolved through a fixed map, and an unmapped code is invalid.
- `OLLAMA_NUM_PARALLEL` is 1, which is the Ollama default. The packed-prompt shape needs no concurrency, and Ollama scales KV cache by `OLLAMA_NUM_PARALLEL` times `OLLAMA_CONTEXT_LENGTH`. Record the effective value and the context length in the output record.
- Timing gate before any full run: process exactly one packed 20-comment batch with all four fields, record wall-clock seconds and output token count, and project the 3,000-row time from it. Abort and report instead of starting the full run when the projection exceeds the threshold. Label this projection as a projection.
- Output fields: `generatedAt,hardware,ollamaVersion,model,modelDigest,batchSize,thinkingMode,contextLength,numParallel,singleBatchSeconds,singleBatchOutputTokens,projectedFromSingleBatchSeconds,commentsProcessed,elapsedSeconds,measured3000,projected3000Seconds,gpuMemoryBeforeMiB,gpuMemoryAfterMiB,malformedFinalBatches,invalidThemeCount,invalidKeyMessageIdCount,invalidSentimentCount,invalidEmotionCount,themeMacroF1,keyMessageMacroF1,mixedThemeMacroF1,mixedKeyMessageMacroF1,passed`.
- Key Message Macro F1 is the macro mean of one-vs-rest F1 across allowed IDs. Theme Macro F1 uses exact resolved Theme labels. A candidate passes only when an actual 3,000-row run is at most 7,200 seconds, malformed final batches are zero after retry, every invalid count is zero, Theme Macro F1 is at least 0.75, Key Message Macro F1 at least 0.70, and each mixed score is no more than 0.05 below its overall score. Among passing models choose higher quality, then lower elapsed time, then smaller model.
- Affect prediction pass: run the selected model over every `labels.jsonl` row through the same merged prompt and write `qwen-sentiment-predictions.jsonl` and `qwen-emotion-predictions.jsonl` in the locked prediction row format. Task 4.2 consumes these two files and scores them.
- Verification: `python tests/bench_qwen.py --self-check`; run the single-batch timing gate; run the exact 14B command; run `python tests/bench_qwen.py --model qwen3:8b-q4_K_M --corpus benchmark-data/private/qwen-corpus.jsonl --comparison benchmark-data/private/qwen-comparison.jsonl --limit 3000 --batch-size 20 --output benchmark-results/qwen-8b.json`; merge both sanitized records into `benchmark-results/qwen.json` with `candidates:[...]` and the selected `decision`; do not label a projection as measured. The self-check asserts index resolution against a fixture, out-of-range and non-integer index rejection, unmapped affect code rejection, and that no reordering of either allowed array occurs between prompt and decode.
- CLI contract: `python tests/bench_qwen.py --model qwen3:14b-q4_K_M --corpus benchmark-data/private/qwen-corpus.jsonl --comparison benchmark-data/private/qwen-comparison.jsonl --limit 3000 --batch-size 20 --output benchmark-results/qwen.json`; repeat with the 8B model.
- Depends on: Task 4.1.
- Stop: when the merged prompt drops `themeMacroF1` below 0.75 or `keyMessageMacroF1` below 0.70 for both candidates, stop and report. Split affect back out of the prompt rather than shipping degraded Theme or Key Message quality. Never relax the threshold to make a candidate pass.

#### Task 4.4 - Apply model decisions

- Files: `pipeline/config_types.py`, `adapter.py`, `config-template.py`, `pipeline/analyze.py`, `pipeline/report.py`, `app/app.js`, `tests/test_classify.py`, `tests/test_model_labels.py`, and the three aggregate CSV tests when the export schema changes.
- Symbols: `PipelineConfig.TEXT_MODEL`, `PipelineConfig.VISION_MODEL`, `PipelineConfig.SENTIMENT_MODEL`, `PipelineConfig.EMOTION_MODEL`; `adapter._build_config`; `analyze.affect`; `report.export` comment-column writer; `app.js` constants or literal arrays that populate the Sentiment and Emotion evidence filter pills; new direct-test `main()` in `tests/test_model_labels.py`.
- Consumes: `benchmark-results/classifiers.json` and `benchmark-results/qwen.json`, both with `passed:true` decisions.
- Produces: exact pinned defaults from results, no provider fallback or model tiers. Vision remains the named Qwen VL model while image User Inputs exist. `tests/test_model_labels.py` directly checks normalized Sentiment labels and the selected fixed Emotion labels.
- Branch on the Task 4.2 `decision`, per task, sentiment and emotion independently:
  - Encoder wins a task: pin its exact revision in the matching `PipelineConfig` field, keep `analyze.affect` running that encoder, and keep that task's confidence column in `comments.csv`.
  - Qwen wins a task: delete the matching `PipelineConfig` field rather than pinning it, read that label from the merged Qwen pass in `analyze.affect` instead of running encoder inference, and remove that task's confidence column from `comments.csv` and from every test asserting the header.
- Column removal is exact. `sentiment_confidence` goes only when Qwen wins sentiment, `emotion_confidence` only when Qwen wins emotion. A mixed outcome removes one column and keeps the other. Update the `comments.csv` schema in the CSVs contract above to match the shipped header.
- Verification: `python -m py_compile pipeline/config_types.py adapter.py config-template.py pipeline/analyze.py pipeline/report.py tests/test_model_labels.py`; `python tests/test_model_labels.py`; `python tests/test_classify.py`; the three aggregate CSV tests when the export schema changes; `node --check app/app.js`; run Ollama preflight and one small real inference for each selected model and record it in `verification.md`. Assert the shipped `comments.csv` header matches the decision exactly, with no orphan confidence column and no removed column still asserted.
- Stop: if either benchmark has no passing decision, do not change shipping defaults; report the blocker. Never keep a confidence column populated with a constant.

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

- Files: `README.md`, `PRD.md`, `benchmark-results/verification.md` read-only.
- Update README sections for the final Session flow, setup drafting, transcript reconciliation, always-on Sentiment/Emotion, six downloads, local models, limits, and tab-close boundary. Remove all current-gap claims that verification marks complete.
- Move completed Wave entries under a dated release while preserving this mission brief decisions and the July 2026 history.
- Verification: `python tests/check_docs.py README.md PRD.md`.

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
- Commands: `python tests/check_docs.py README.md PRD.md AGENTS.md docs/setup.md docs/benchmark-runbook.md docs/architecture.md docs/api-reference.md`; `git diff --check`; `git status --short`.
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

Detailed root-cause narration for each entry below lives in the matching commit message.

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
