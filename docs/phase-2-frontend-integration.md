# Phase 2 - Frontend integration plan

Status: planned. Backend is shipped (see `architecture.md`). The frontend in
`app/` still runs on an in-memory fixture layer. This plan wires it to the real
API. Decisions are locked; an engineer can execute without judgment calls.

Related docs: [architecture.md](architecture.md), [api-reference.md](api-reference.md).

---

## Scope

Frontend integration, one scope. Two small backend additions exist only because
the frontend cannot function without them (see "Contract additions").

Out of scope, unchanged: OCR, custom lenses, cross-session assistant, key-visual
route, brief-point add/delete routes, video metadata at add time. Each stays
deferred or is resolved by an honest-disconnect decision below.

## Mode model

The frontend keeps two modes behind one API boundary:

- **Live**: the app is served by `server.py` at `http://localhost:8000/`. Every
  `demoApi` call is a real `fetch`. Drives the run screen from SSE.
- **Demo**: backend unreachable, or `live.js` not loaded. The existing store and
  run engine run unchanged. `self-check.html` always runs in demo mode.

Mode resolves once at boot:

1. `app.js` checks for `window.__liveApi` (defined by `live.js`).
2. If present, it probes `GET /api/sessions` with a 1200 ms timeout
   (`AbortController`). Success selects live; any failure selects demo.
3. `self-check.html` loads only `app.js`, never `live.js`, so it always tests
   the store.
4. `demoApi.mode` exposes `"live"` or `"demo"` for renderers.

The probe needs no CORS middleware. The app only reaches live mode when the
backend serves it (same-origin). Served standalone on another port, or via
`file://`, the fetch is cross-origin and the app falls back to demo.

## Contract additions

Frontend-before-backend: these two additions are defined here first, then the
frontend builds against them.

### 1. `latestRun` on the session serializer

The session list must show a running run's state and link to it after a page
reload. Nothing in the API exposes the run id of the latest run on list
responses today.

`_ser_session()` in `server.py` gains:

```json
"latestRun": {
  "id": "...",
  "status": "queued | running | complete | failed",
  "stage": "connecting | running | complete | failed",
  "pct": 0,
  "message": "",
  "error": null
}
```

`null` when the session has no run. Source: the most recent run row by
`started_at DESC, rowid DESC` (same ordering as `_session_status`), serialized
with the existing `_ser_run` field mapping minus `briefPointIds` and
`artifacts`.

### 2. `GET /api/assets/{asset_id}/file`

The Files screen previews and downloads uploaded images and documents. No route
serves uploaded file bytes today. Add:

- `GET /api/assets/{asset_id}/file` -> `FileResponse` of `assets.file_path`
  with `Content-Disposition: attachment`.
- `404` when the asset is missing or has no `file_path` (articles).

Same shape as `GET /runs/{id}/artifacts/{artifact_id}`.

## Mockup-vs-backend decisions (locked)

Each gap resolves one way. The demo mode keeps current behavior; live mode
applies these.

| Gap | Live-mode resolution |
|---|---|
| Videos have no `title`/`channel`/`commentCount` in the API | Video row shows the pasted URL; the Short/Video tag derives from `parseYouTubeUrl(v.url).kind`. The "N comments available" line is removed. |
| `setKeyVisual` has no route; `isKeyVisual` is always `false` | The key-visual toggle and its "Used as the key visual" sub-line are hidden. Image sub-line reads "image context at run time". |
| Brief-point add/delete has no route; PATCH skips unknown ids | The "Add an idea" button and per-row delete are hidden. The review note adds: "Ideas are fixed for this run. Exclude one to drop it." |
| SSE `detail` is a string, not an object | The stepper and rail counters parse the known detail strings; unparseable detail shows "—". No object access. |
| SSE has no `connecting` stage; terminal stage is `error` | `STAGE_TO_STEP` maps `error` to `-2` and `running` to `-1`. `onEvent` treats `stage === "error"` as failure (`state.failed = e.detail || e.message`). |
| `demoApi.getCampaign(id)` needs a session id | Live body: list sessions, find the one whose `campaignIds` includes `id`, then `GET /sessions/{sid}` and return `.campaigns.find(c => c.id === id)`. |
| Report artifact blobs are not served | Live `getArtifact(id)` fetches the artifact route, returns `{...artifact, content: blob}`. Renderers keep calling `downloadBlob(a.content, ...)` unchanged. |
| Uploaded file blobs are not served | Live `getAssetData(id)` fetches `GET /api/assets/{id}/file` and returns the blob. |
| Evidence filter pills are fixture-specific (`Joy`/`Skeptical`/`Neutral`) | Live pills derive from the unique emotion labels in `report.evidence`, plus "Most liked". The demo keeps its fixed pills. |
| `report.evidence` has `sentiment`, no `author` | The evidence card meta reads `emotion · likes`. `sentiment` shows when present. |
| Overwrite semantics: a new run deletes prior results | Live "Run analysis" shows `window.confirm` when `session.status !== "ready"`: "A new run replaces this session's previous results. Continue?". Demo keeps its fresh-run behavior. |
| Session list has no live progress bar | Live row shows the status pill and `latestRun.message`; no bar (the server does not persist `pct`). Demo keeps its bar. |
| Article asset `name` is the raw URL | `assetRowHtml` displays host + " — article" when the name starts with `http`. |
| Fixture-specific copy | Home, session list, run screen, evidence drawer, and OCR note drop "local demo" / "fixture data" phrasing in live mode. |

## Files

- `app/live.js` - new IIFE defining `window.__liveApi`. The live implementation.
- `app/app.js` - `demoApi` becomes a mode dispatcher; renderers lose direct
  `store.*` reads; live branches for the disconnects above.
- `app/index.html` - load `live.js` before `app.js`.
- `server.py` - the two contract additions.
- `docs/api-reference.md`, `docs/architecture.md` - document the additions and
  the updated disconnect table.

## Tasks

### Task 1 - server.py contract additions

Consumes: existing `db`, `storage`, `_ser_run` field mapping.
Produces: `latestRun` in `_ser_session`; route `GET /api/assets/{asset_id}/file`.

- In `_ser_session`, query the latest run row and add `latestRun` (or `null`).
  Reuse `_session_status` ordering. Include `id`, `status`, `stage`, `pct`,
  `message`, `error` using the same derivation as `_ser_run` (`stage` is
  `"connecting"` when queued, `"running"` otherwise, `"complete"`/`"failed"`
  when terminal).
- Add the asset file route after `remove_asset`. Return `FileResponse` with the
  attachment header. `404` for missing asset or missing `file_path`.

### Task 2 - app/live.js

Consumes: the route map in `architecture.md`, the contract additions, the
existing `demoApi` signatures.
Produces: `window.__liveApi` with one method per `demoApi` signature.

Rules:

- Base URL: `location.origin` (same-origin). Relative `/api/...` paths.
- Error normalization: parse the `{detail: {error, message, field}}` and the
  flat `{error, message, field}` shapes; throw `Error(message)` with `code` and
  `field` set, matching `demoError`. Renderers read `err.message` unchanged.
- `createSession(input)`: `POST /sessions`, `POST /sessions/{id}/campaigns`,
  then one `POST /campaigns/{id}/videos` per URL. Return
  `{session, campaign}` from the first two responses. On a video `422`, throw
  with `field: "videos"`. A failed video leaves the session and campaign on the
  server; document this, do not clean up.
- `addVideo(campaignId, url)`: `POST /campaigns/{id}/videos` with
  `{url, kind: "auto"}`. Server `kind` values are `auto`/`brand_ad`/`review`/
  `explainer`; the Short tag is a display concern derived from the URL.
- `uploadAsset(campaignId, file)`: `FormData` with field `file`.
- `getCampaign(campaignId)`: sessions list scan, then `GET /sessions/{sid}`,
  return `campaigns[0]` matching the id.
- `getRunningRun(sessionId)`: return `latestRun` from
  `GET /sessions/{sessionId}` when `status` is `queued` or `running`, else
  `null`.
- `getRun(id)`: `GET /runs/{id}`. Pass `briefPoints` and `artifacts` through
  unchanged.
- `updateBriefPoints(runId, points)`: `PATCH /runs/{id}/brief_points` with
  body `{points}` where each point carries `id`, `label`, `description`,
  `included`, `order` (`approved` optional).
- `subscribeRun(id, handlers)`: `EventSource` on `/api/runs/{id}/events`.
  `onmessage` parses JSON and calls `handlers.onEvent`. `onopen` calls
  `handlers.onReconnect` when previously disconnected. `onerror` calls
  `handlers.onDisconnect` and lets the browser reconnect (native backoff).
  On a terminal event (`stage` `complete` or `error`): deliver it, then close
  the `EventSource` and stop reconnecting. Return an unsubscribe that closes
  the `EventSource`.
- `getReport(id)`: `GET /runs/{id}/report`. Pass through.
- `getArtifact(id)`: `GET /runs/{id}/artifacts/{artifactId}` as a blob; return
  `{id, runId, name, kind, content: blob}`. The id is known from the run's
  `artifacts` list; keep `runId` on the artifact.
- `getAssetData(assetId)`: `GET /api/assets/{assetId}/file` as a blob; `null`
  when the asset is an article.
- `listFiles()`: fetch all sessions, compose assets from
  `session.campaigns[].assets` (mark `_file: "asset"`) and artifacts from
  `session.runs[]` with `status === "complete"` (mark `_file: "artifact"`,
  attach `campaignId` from `session.campaignIds[0]` and `campaignName`). Sort
  by `addedAt` descending.
- `listArtifacts(runId)`: from `getRun(runId).artifacts` when complete, else
  `[]`.
- `setKeyVisual`, `simulateDisconnect`, `simulateFailure`: live bodies throw a
  `conflict` error ("Not available in live mode"). Renderers never call them in
  live mode.

### Task 3 - app/app.js mode dispatcher

Consumes: `demoApi` store bodies, `window.__liveApi`.
Produces: `demoApi.mode`; `demoApi` methods delegate to live or store.

- Resolve mode at boot: `window.__liveApi` present and probe succeeds -> live,
  else demo. Do not probe when the shell elements (`#view`, `#topbar`,
  `#overlay-root`) are absent; `self-check.html` then never probes.
- Each `demoApi` method body becomes: `if (demoApi.mode === "live") return
  window.__liveApi.<method>(...args); return <existing store body>;`.
- Keep `window.demoApi` export and all signatures.

### Task 4 - renderers: store-free reads and live branches

Consumes: `demoApi.mode`, the live data shapes.
Produces: screens that render identically in both modes.

Remove direct `store.*` reads from renderers:

- `renderSessions`: per row, `demoApi.getSession(s.id)` for the campaign name
  tag, the run link target, and the running status. `demoApi.getRunningRun` for
  the live running cell.
- `renderCampaign`: campaign data from `demoApi.getSession(sessionId).campaigns`
  and `demoApi.getCampaign(campaignId)`; the ideas rail from the latest run's
  `briefPoints` via `demoApi.getRun` (empty until a run reaches `brief_pause`).
- `renderRun`: campaign from `demoApi.getSession(run.sessionId).campaigns[0]`;
  brief review reads `run.briefPoints`, not `run.briefPointIds` plus store.
- `renderResults`: artifacts from `run.artifacts` (complete runs carry them);
  campaign from the session.
- `renderFiles`: campaign column from `f.campaignName ?? store lookup`.

Live branches (see the decisions table):

- `renderRun`: hide the Demo controls disclosure; remove the fixture-specific
  "Early read" notice or replace with "The full picture lands with the note.";
  hide Add/Delete in the brief review; update `STAGE_TO_STEP` and `onEvent` for
  `error`/`running`; parse counters from detail strings; live sub-copy about
  leaving the page.
- `renderResults` + evidence drawer: dynamic pills from evidence emotions in
  live mode; live footer "Showing X of N comments. Full list in comments.csv.";
  sentiment in the meta line when present; `report._totalComments` already
  resolves from `session.commentCount`.
- `renderCampaign`: video rows show the URL; the kind tag derives from
  `parseYouTubeUrl(v.url).kind`; the comment-count line is dropped; the
  key-visual toggle and sub-line are hidden; "Run analysis" confirms overwrite
  in live mode when `session.status !== "ready"`.
- `assetRowHtml`: article name prettify; live image/document sub-lines without
  key-visual wording.
- Home, session list, run, and OCR copy: drop "local demo" / "fixture data"
  phrasing in live mode.

### Task 5 - app/index.html

Load `live.js` before `app.js`:

```html
<script src="live.js"></script>
<script src="app.js"></script>
```

### Task 6 - docs

Consumes: the locked decisions and contract additions.
Produces: updated `docs/api-reference.md` and `docs/architecture.md`.

- API reference: document `latestRun` in the session shapes and the asset file
  route.
- Architecture: rewrite the "Fixture layer and the backend swap" section to the
  live/demo mode model; update the route map (mark `setKeyVisual` and the
  simulate methods demo-only); update the mockup-vs-backend disconnect table
  with the live-mode resolutions above; note that `self-check.html` always runs
  in demo mode.

### Task 7 - Verification

1. Demo regression: open `app/self-check.html` from the file system. All
   assertions pass (store mode, no probe).
2. Live smoke: start the server (`python server.py`), open
   `http://localhost:8000/`. Confirm live mode resolves (a session created now
   persists across reloads). Create a session, add videos and an asset, open
   the image preview, run an analysis, confirm the overwrite prompt on the
   second run, review and confirm brief points, open the results screen, open
   the evidence drawer, filter by a real emotion label, download the PDF and
   chart CSVs. Check the Files screen lists assets and all six artifacts.
3. Full E2E run: only with `YOUTUBE_API_KEY` and `GEMINI_API_KEY` set. Confirm
   the run streams `collect` -> `brief` -> `brief_pause` -> `classify` ->
   `emotion` -> `report` -> `complete` over SSE and the results screen shows
   real numbers. If keys are absent, state that E2E was not run and why.
4. Remove any temp test files.

## Risks

| Risk | Handling |
|---|---|
| Fixture and live shapes drift | One dispatcher, one signature set. `self-check.html` pins the store side; the E2E steps pin the live side. |
| SSE reconnect loop after terminal events | `subscribeRun` closes the `EventSource` on `complete`/`error`. |
| Cross-origin probe failure on non-server serving | Documented: live mode requires serving from `localhost:8000`. |
| `detail` string parsing breaks if adapter copy changes | Parsing matches the exact strings in `adapter.py` (`"N themes"`, `"other_share=X"`); unparseable values render "—". |
