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
- For the demo presentation: reskinning live mode, any new backend route, moving off the
  `qwen3.5:4b` pin, and Playwright coverage of demo mode. The presenter opens localhost and
  clicks through instead.

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
- Demo mode is entered only by the `?demo=1` query flag, held in `sessionStorage`, scoped to
  one browser tab. The mode probe is skipped and `demoApi.mode` pins to `"demo"`. Demo mode
  never consults `window.__liveApi`, so no wrapper gap can reach the live database. A plain
  `/` stays live, because the backend keeps serving office users during the presentation.
- Every demo artifact is generated ahead of time by `pipeline/report.py` from hand-labelled
  real comments, then committed under `app/demo/`. The frontend downloads those files as
  static assets. No number in the demo is typed by hand: Python counts every percentage from
  the labels, the same division of labour the product uses.

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

### Demo fixture

The demo presents the Indomie Goreng Cabe Ijo relaunch, an Innocean internal campaign. Both
videos are real, both comment sets are scraped, and every label is assigned by hand.

```text
demo_data/comments.csv   181 scraped comments, 169 on Rl-sPdzYlXc, 12 on bvtSIAULS88
demo_data/videos.csv     real titles, channel, descriptions, view counts; no transcripts
demo_data/labels.csv     one hand-assigned label row per comment, joined on comment_hash
app/demo/                the six generated artifacts, plus fixture.js for the results page
```

- Both videos carry group `Indomie`. `bvtSIAULS88` contributes 12 comments, so its share of
  every group-split metric is small and honestly reported.
- The four Key Messages are fixed: `Authentic green chili flavor`, `Bolder, upgraded taste`,
  `Real green chili`, `Jumbo Size Variant`.
- `demoVideoMeta` resolves `Rl-sPdzYlXc` and `bvtSIAULS88` to their real titles and comment
  counts through an exact-ID lookup. Every other ID keeps the existing hash fallback, so a
  mistyped URL on stage degrades to a generic title instead of raising.
- `labels.csv` columns are `video_id,comment_hash,theme,sentiment,emotion` plus one boolean
  column per Key Message. Sentiment and emotion use the product's enums, so the generated CSVs
  satisfy the same constraints a real run does.

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
- Waves D1 and D2 are implemented and verified, uncommitted. D1 wrote `demo_data/labels.csv`
  (181 hand-assigned rows), `demo_data/build_demo_artifacts.py`, and the six artifacts plus
  `fixture.js` under `app/demo/`. D2 added the `?demo=1` entry flag held in `sessionStorage`,
  the exact-ID Indomie video lookup, the four Indomie Key Messages, and a `finalizeRun` that
  reads every number from `window.__demoFixture`. Deleted: `buildDemoPdf`, the `mkArtifact` CSV
  assembly block, six placeholder fixture constants, and every synthetic comment-count floor.
  Evidence: `node --check app/app.js` exit 0, `node --check app/demo/fixture.js` exit 0,
  `python demo_data/build_demo_artifacts.py` exit 0 writing seven files, 133 fixture evidence
  comments each carrying a valid `emotion` with zero null, `comments.csv` header matching the
  contract with four `key_message_*` columns, `report.pdf` 95,810 bytes starting `%PDF`, and
  41 assertions across `tests/test_evidence.py` 27/27, `tests/test_classify.py` 8/8,
  `tests/test_report_key_messages_csv.py` 2/2, `tests/test_report_themes_csv.py` 2/2,
  `tests/test_report_sentiment_emotions_csv.py` 2/2. Not verified: the by-hand walkthrough at
  `http://127.0.0.1:8000/?demo=1`. Demo mode has no Playwright coverage by decision, so the
  presenter click-through is the only check of the six downloads opening natively.
- Wave D3 is implemented and verified, uncommitted. `app/style.css` gained a `Motion` section
  holding four keyframes: `view-rise` on route entry, `step-advance` on the stepper dot,
  `drawer-in` and `backdrop-in` on the evidence drawer. All eight animated selectors are
  neutralized inside the existing `prefers-reduced-motion` block. `app/app.js` gained one
  `renderRun` local, `paintedStep`, which marks the single stepper row that just advanced.
  Evidence: `node --check` exit 0 on `app/app.js` and `app/demo/fixture.js`, a 12-assertion
  static audit, and 52 test assertions across `tests/test_evidence.py` 27/27,
  `tests/test_classify.py` 8/8, `tests/test_skip_pause.py` 7/7, and
  `tests/test_run_artifacts.py` 10/10. Not verified: motion as seen by an eye. No test
  selects an animated class, by decision, so the presenter walkthrough is the only check.

## Demo presentation waves

A live presentation runs on the office workstation while the backend keeps serving office
users. The client accepted that no end-to-end run happens on stage: the card cannot hold the
9b model the analysis wants. The demo therefore replays a real analysis of real comments
rather than performing one.

### Wave D1: labelled fixture and generated artifacts

Offline Python. No frontend change.

- Task D1.1: write `demo_data/labels.csv`, one row per scraped comment.
  - Acceptance: 181 rows. Every row joins 1:1 against `demo_data/comments.csv`. Every
    sentiment is `positive`, `negative`, or `neutral`. Every emotion is `joy`, `anger`,
    `sadness`, `fear`, or `other_neutral`.
- Task D1.2: write `demo_data/build_demo_artifacts.py`, which joins comments to labels, builds
  the dataframe, and calls the real `pipeline/report.py` entry points to emit the artifacts.
  - Acceptance: the script runs clean. `app/demo/` holds `report.pdf` and the five CSVs. The
    PDF opens with rendered charts. The `comments.csv` header matches the CSV contract above
    exactly, including one `key_message_<stable-id>` column per Key Message.

### Wave D2: demo mode entry and fixture wiring

Frontend. Depends on D1.

- Task D2.1: add the `?demo=1` flag, persist it in `sessionStorage`, skip the mode probe, and
  block `window.__liveApi` while demo mode is active.
  - Acceptance: `?demo=1` renders the demo store. A second tab on `/` stays live. No demo
    interaction issues a request to `/api`.
- Task D2.2: point the demo store at the Indomie fixture. Add the exact-ID video lookup, return
  the four Key Messages from the draft, read report constants from `app/demo/fixture.js`, and
  serve artifact downloads from the committed files. Delete `buildDemoPdf` and the `mkArtifact`
  CSV assembly block.
  - Acceptance: pasting both URLs shows their real titles. The draft returns the four Key
    Messages. Results render Indomie themes. All six downloads open in their native
    application. `node --check app/app.js` passes.

### Wave D3: view transitions

Frontend. Independent of D1 and D2.

- Task D3.1: add a fade-and-rise on route change in `route()`, a stage-advance transition on
  the progress screen, and entry easing on the evidence drawer.
  - Acceptance: navigation reads as continuous with no layout shift. All motion resolves inside
    the existing `prefers-reduced-motion` block in `app/style.css`.

**Verification.** `node --check app/app.js`, then `python demo_data/build_demo_artifacts.py`,
then open `http://127.0.0.1:8000/?demo=1` and walk setup, brief-pause, results, and the six
downloads by hand.

## Open work

**0. Walk the demo by hand, then commit D1, D2, and D3.** Open
`http://127.0.0.1:8000/?demo=1`, paste both Indomie URLs, and confirm their real titles render.
Walk setup, `brief_pause`, and results. Open all six downloads in their native application.
Confirm a second tab on a plain `/` still resolves live. Watch the motion while walking: each
screen should rise into place, one stepper dot should pop as the stage advances, and the
evidence drawer should ease in from the right. Then commit `demo_data/`, `app/demo/`,
`app/app.js`, `app/index.html`, and `app/style.css`. Every planned demo wave is now built.

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

- 2026-08-19: Shipped Wave D3. Three route roots were missed on the first pass. The packet
  named `.view-pad` and `.run-layout` as the only children `#view` receives, but
  `renderNewSession` mounts `.setup-wrap`, `renderCampaign` mounts `.campaign-layout`, and
  `renderResults` mounts `.report-layout`, so three of six screens had no entry animation. An
  addendum widened the selector list to five. The results screen animates `.report-scroll`
  rather than its parent `.report-layout`, because the wide-viewport evidence drawer mounts as
  a `position: fixed` child of `.report-layout`, and a `transform` on an ancestor of a fixed
  element makes that ancestor its containing block. `.report-scroll` is the drawer's sibling,
  so the transform cannot reach it. For the same reason nothing animates `#view` itself.
  Route entry and drawer entry need no JavaScript: both replace or insert DOM nodes, so a
  keyframe animation fires on insertion. Only the stepper needed code, because `paintSteps()`
  rebuilds all five rows on every progress tick and all five would re-animate without a marker
  for the one that changed.
- 2026-08-19: Shipped Waves D1 and D2. Two decisions closed during the build. The evidence
  drawer now derives its filter pills from the emotion labels actually present, in both demo
  and live mode, which required carrying `emotion` through the fixture's evidence rows;
  `adapter.py` emits only `text`, `likes`, `videoId`, and `sentiment`, so
  `build_demo_artifacts.py` joins the label back on comment text. Results always render all
  four Key Message rows regardless of what the presenter unchecks at `brief_pause`, because
  fixture numbers exist only for those four labels. One defect was found and repaired: two
  hardcoded `8412` comment-count placeholders survived the first pass at `app/app.js` and would
  have displayed a fabricated total on stage. The `utf-8-sig` BOM on the demo CSVs is correct,
  not a defect: `pipeline/report.py` writes every production CSV that way and four test files
  read it back that way, so the demo files match live output byte for byte.
- 2026-08-19: Planned the demo presentation as Waves D1-D3. Two findings redirected the plan.
  The frontend-only demo already existed in `app/app.js` with a full staged run engine; what
  was missing was a way to enter it deliberately, since demo mode was only the fallback when
  the `/api/sessions` probe failed. And the demo's `comments.csv` wrote
  `author,emotion,key_message,text,likes`, which is not the contract header, so a downloaded
  file would have contradicted the product on stage. Generating all six artifacts through
  `pipeline/report.py` fixes that and deletes the hand-rolled `buildDemoPdf` PDF writer.
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
