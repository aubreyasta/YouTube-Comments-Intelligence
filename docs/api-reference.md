# API reference

HTTP contract for the FastAPI backend. Base URL: `http://localhost:8000/api`.

Related docs:
- [Setup](setup.md) - how to install and start the server.
- [Architecture](architecture.md) - what the pieces are.
- [README](../README.md) - short human overview.

---

## Conventions

- **Content type**: `application/json` for request and response bodies, unless noted (`multipart/form-data` for uploads, `text/event-stream` for the events stream).
- **Timestamps**: ISO-8601 with timezone, e.g. `2026-08-01T14:22:31+00:00`.
- **IDs**: UUID v4, stable once created.
- **Casing**: response bodies use `camelCase`; request bodies match what each endpoint documents. Exception: `GET /runs/{id}/events` (SSE) payloads use `snake_case` (`run_id`, not `runId`) - see the events section below.
- **Base URL**: `http://localhost:8000/api`. The server binds `127.0.0.1` only.

### Error format

Errors return a JSON object:

```json
{
  "error": "snake_case_code",
  "message": "Human-readable description.",
  "field": "optional_field_name_or_null"
}
```

FastAPI wraps error responses in a `detail` envelope for `HTTPException`, so a full error looks like:

```json
{
  "detail": {
    "error": "validation",
    "message": "Only youtube.com/watch?v=, youtu.be/ and youtube.com/shorts/ links work.",
    "field": "url"
  }
}
```

Pydantic validation errors (missing or malformed request bodies) return the flat shape without the `detail` wrapper.

### Status codes

| Status | Meaning |
|---|---|
| `200` | OK, response includes a body. |
| `201` | Created. Response body is the new resource. |
| `202` | Accepted. Used by `POST /sessions/{id}/runs`; the run starts in a background thread. |
| `204` | No content. Used by `DELETE` endpoints. Idempotent: deleting a missing id still returns `204`. |
| `404` | Not found. Returned by `_404()` helper. |
| `409` | Conflict / state violation. E.g. a session already has a campaign, editing brief points after proceed, requesting a report before completion. |
| `413` | Payload too large. Uploads capped at 10 MB. |
| `422` | Validation error. Bad URL format, missing name, unknown `kind`, empty file, duplicate video, etc. |

---

## Sessions

### `POST /sessions`

Create a new session.

Request:

```json
{ "name": "My session" }
```

Response `201`:

```json
{
  "id": "...",
  "name": "My session",
  "campaignIds": [],
  "commentCount": 0,
  "status": "ready",
  "updatedAt": "...",
  "createdAt": "..."
}
```

Errors: `422` if `name` is empty.

### `GET /sessions`

List all sessions, newest first. Each entry adds `campaignCount` (integer).

Response `200`:

```json
[
  {
    "id": "...",
    "name": "...",
    "campaignIds": ["..."],
    "campaignCount": 1,
    "commentCount": 0,
    "status": "ready",
    "updatedAt": "...",
    "createdAt": "...",
    "latestRun": {
      "id": "...",
      "status": "queued | running | complete | failed",
      "stage": "connecting | running | complete | failed",
      "pct": 0,
      "message": "",
      "error": null
    }
  }
]
```

`status` values: `ready`, `running`, `complete`, `failed`. Derived from the most recent run.

`commentCount` is read from the latest complete run's `report.json`; `0` if no complete run yet.

`latestRun` is the most recent run for the session, ordered by `started_at DESC, rowid DESC`. Fields use the same derivation as `GET /runs/{id}` (`stage` is `"connecting"` when queued, `"running"` when active, `"complete"` or `"failed"` when terminal). `latestRun` is `null` when the session has no run. `briefPointIds` and `artifacts` are omitted.

### `GET /sessions/{id}`

Return a session with nested campaigns (each with nested videos and assets) and run summaries.

Response `200`:

```json
{
  "id": "...",
  "name": "...",
  "campaignIds": ["..."],
  "commentCount": 1234,
  "status": "complete",
  "updatedAt": "...",
  "createdAt": "...",
  "campaigns": [ { "..." : "..." } ],
  "runs": [ { "..." : "..." } ],
  "latestRun": {
    "id": "...",
    "status": "queued | running | complete | failed",
    "stage": "connecting | running | complete | failed",
    "pct": 0,
    "message": "",
    "error": null
  }
}
```

`latestRun` is the same shape as in `GET /sessions`. `null` when the session has no run.

Errors: `404` if the session does not exist.

---

## Campaigns

Each session holds at most one campaign. Attempting to create a second returns `409`.

### `POST /sessions/{id}/campaigns`

Create a campaign under a session.

Request:

```json
{ "name": "Campaign A" }
```

Response `201`: campaign object (see below).

Errors:
- `404` session not found.
- `409` the session already has a campaign. Message: `"This session already has a campaign."`
- `422` empty `name`.

### `GET /sessions/{id}/campaigns`

Return the session's campaigns, each with nested videos, assets, and brief-point ids from the latest run.

Response `200`:

```json
[
  {
    "id": "...",
    "sessionId": "...",
    "name": "...",
    "videoIds": ["..."],
    "assetIds": ["..."],
    "briefPointIds": ["..."],
    "videos": [ { "..." : "..." } ],
    "assets": [ { "..." : "..." } ]
  }
]
```

Errors: `404` session not found.

---

## Videos

### `POST /campaigns/{id}/videos`

Add a video to a campaign. Accepts `youtube.com/watch?v=`, `youtu.be/`, and `youtube.com/shorts/` URLs. Rejects playlist URLs and duplicates within the same campaign.

Request:

```json
{ "url": "https://youtu.be/...", "kind": "auto" }
```

`kind` is optional; defaults to `auto`. Allowed values: `auto`, `brand_ad`, `review`, `explainer`.

Response `201`:

```json
{
  "id": "...",
  "campaignId": "...",
  "url": "https://youtu.be/...",
  "videoId": "...",
  "kind": "auto"
}
```

Errors:
- `404` campaign not found.
- `422` empty URL, playlist URL, unrecognized URL format, missing video id, unknown `kind`, or duplicate within the campaign.

### `DELETE /videos/{id}`

Remove a video. Idempotent: `204` whether or not the id exists.

---

## Assets

Two kinds of assets: uploads (`document` or `image`) and articles.

### `POST /campaigns/{id}/assets/upload`

Upload a file to a campaign. `multipart/form-data`, field name `file`.

Accepted extensions: `.pdf`, `.pptx`, `.docx`, `.png`, `.jpg`, `.jpeg`, `.webp`. Max size 10 MB.

Text extraction is deferred to run start, not done on upload. Documents get text extracted then; images add visual context without text.

Response `201`: asset object.

Errors:
- `404` campaign not found.
- `413` file exceeds 10 MB.
- `422` empty filename, unrecognized extension.

### `POST /campaigns/{id}/assets/article`

Add an article URL to a campaign. The URL is snapshotted at run start (15 s timeout, capped at 20 000 characters); a failed fetch returns empty text and the run continues.

Request:

```json
{ "url": "https://..." }
```

Response `201`: asset object.

Errors:
- `404` campaign not found.
- `422` URL is not `http://` or `https://`.

### `DELETE /assets/{id}`

Remove an asset. For uploaded files, the file on disk is also removed. Idempotent.

### `GET /assets/{id}/file`

Download the raw bytes of an uploaded asset. Returns the file with `Content-Disposition: attachment` set.

Same shape as `GET /runs/{id}/artifacts/{artifact_id}`.

Errors:
- `404` asset record not found, asset has no `file_path` (article assets), or file missing on disk.

### Asset object shape

```json
{
  "id": "...",
  "campaignId": "...",
  "kind": "document | image | article",
  "name": "filename or URL",
  "sourceUrl": "URL or null",
  "mimeType": "...",
  "size": 12345,
  "addedAt": "...",
  "isKeyVisual": false,
  "status": "ready"
}
```

`isKeyVisual` is currently always `false`. The frontend has a key-visual selector but no backing route yet.

---

## Runs

Starting a new run deletes the session's prior run and all its artifacts. There is one result per session at any time. The client should confirm this overwrite with the user before calling `POST /sessions/{id}/runs`.

### `POST /sessions/{id}/runs`

Start a run for a session. Deletes the session's existing run (if any) and its artifacts before inserting the new run record. Returns immediately with `202`; the run executes in a background thread.

Response `202`: run object.

Errors:
- `404` session not found.

### `GET /runs/{id}`

Return the current run snapshot.

Response `200`:

```json
{
  "id": "...",
  "sessionId": "...",
  "status": "queued | running | complete | failed",
  "stage": "connecting | running | complete | failed",
  "pct": 0,
  "message": "",
  "briefPointIds": ["..."],
  "error": null,
  "createdAt": "...",
  "briefPoints": [ "..." ],
  "artifacts": [ "..." ]
}
```

`briefPoints` is included whenever brief points exist (from `brief_pause` onward). `artifacts` is included only when `status == "complete"`.

For live stage progress, use the SSE stream. `GET /runs/{id}` is a snapshot.

Errors: `404` run not found.

### `PATCH /runs/{id}/brief_points`

Bulk update brief points during the review pause.

Request:

```json
{
  "points": [
    {
      "id": "...",
      "label": "...",
      "description": "...",
      "approved": true,
      "included": true,
      "order": 0
    }
  ]
}
```

Updates only rows that already belong to this run by `id`; unknown ids are silently skipped. Does not create or delete rows. Sets `edited` to `1` on every updated row.

Response `200`: the full ordered list of brief points after the update.

Errors:
- `404` run not found.
- `409` run has moved past the brief phase (state not `running`/`queued`, no brief points inserted yet, or proceed has already been called).
- `422` empty `points`, empty `label`, or all points excluded.

### `POST /runs/{id}/proceed`

Unblock classification after brief review. Requires at least one included brief point.

Response `200`: the run object.

Errors:
- `404` run not found.
- `409` run is not waiting for review (already proceeded, terminal, or brief points not yet inserted).
- `422` no included brief points.

### `GET /runs/{id}/events`

Server-Sent Events stream. `text/event-stream`. Stays open through the brief pause.

Each event, in `snake_case` (the one exception to the camelCase convention above - this shape comes straight from `adapter.py`'s internal progress dict, not through a serializer):

```
data: {"run_id":"...","stage":"...","message":"...","pct":42,"detail":null}\n\n
```

Stages, in order:

| Stage | `pct` range | Notes |
|---|---|---|
| `collect` | 2-20 | Asset extraction, comment fetch, and cleaning. |
| `brief` | 22-38 | Transcript reading and brief point discovery. |
| `brief_pause` | 40 | Stream stays open. Waiting for user review and `POST /proceed`. |
| `classify` | 42-65 | Theme build, classification, and theme top-up. |
| `emotion` | 67-75 | Emotion and sentiment analysis (both models run under this stage name). |
| `report` | 77-88 | PDF render and CSV export. |
| `complete` | 100 | Run finished. |
| `error` | 0 | Run failed. `detail` contains the exception string, including local Ollama preflight or model errors. There is no cloud fallback. |

One additional `classify` event may appear at `pct` 60 with message `"Refining themes - high uncategorised count"`. It fires when the theme top-up pass runs because the uncategorised share exceeded the limit.

Heartbeat every 15 s:

```
: heartbeat\n\n
```

If the client connects after the run has already terminated, the stream replays a terminal event once and closes.

Errors: `404` run not found (before the stream opens).

### `GET /runs/{id}/report`

Return the report JSON built at the end of the run.

Response `200`: shape below.

Errors:
- `404` run not found.
- `409` run has not completed yet.
- `409` report artifact is missing or unreadable.

### Report JSON shape

```json
{
  "runId": "...",
  "title": "two-line title separated by \n",
  "subtitle": "1,234 comments · 2 videos · 5 themes · Indonesian / English",
  "overallTransfer": 41,
  "transfers": [
    { "id": "m-t-<slug>", "label": "...", "value": 41, "evidenceCount": 340 }
  ],
  "themes": [
    { "id": "m-th-0", "label": "...", "value": 28 }
  ],
  "emotions": [
    { "label": "joy", "value": 34, "n": 420 }
  ],
  "ideaSentiment": [
    { "id": "m-is-<slug>", "label": "...", "positive": 60, "neutral": 30, "negative": 10, "n": 340 }
  ],
  "interpretation": "2-4 paragraphs separated by \n\n",
  "quote": { "text": "...", "attr": "comment · 47 likes" },
  "caveat": "...",
  "evidence": [
    { "id": "ev-0", "metricId": "m-t-...", "text": "...", "emotion": "joy", "sentiment": "positive", "likes": 123 }
  ]
}
```

**`transfers`** - one entry per brief point. `value` is the percentage of comments that echoed it (integer, 0-100). `evidenceCount` is the raw comment count. Zero-echo ideas are included with `value: 0` and `evidenceCount: 0`. Slug is derived from the point label by lowercasing and replacing non-word characters with `-`.

**`themes`** - one entry per discovered theme, sorted by `value` descending with `Other` last. `value` is the percentage of comments in that theme (rounded integer).

**`emotions`** - distribution of emotion labels across all comments in the analysis base. `value` is the rounded percentage; `n` is the raw count. Empty array if the emotion model did not run.

**`ideaSentiment`** - one entry per transfer point, aligned by slug (`m-is-<slug>` matches the corresponding `m-t-<slug>`). `positive`, `neutral`, and `negative` are integer percentages that sum to 100. `n` is the number of comments that echoed the idea. A zero-echo idea has `n: 0` and `positive: 0, neutral: 0, negative: 0`.

**`evidence`** - up to 8 rows per metric, ranked by likes descending then comment length descending. No global cap. `metricId` references either a `transfers[].id`, a `themes[].id`, or an `ideaSentiment[].id`. There is no `author` field. `sentiment` is present on every row.

Every number traces to a per-comment label from the classification step; nothing here is model-estimated.

### `GET /runs/{id}/artifacts/{artifact_id}`

Download a run artifact. Returns the raw file with `Content-Disposition: attachment` set.

Artifact kinds:

| `fileKind` (DB) | `name` | `tier` | Content type |
|---|---|---|---|
| `report_pdf` | `report.pdf` | `primary` | `application/pdf` |
| `summary_csv` | `summary.csv` | `primary` | `text/csv` |
| `chart_transfer_csv` | `chart_transfer.csv` | `primary` | `text/csv` |
| `chart_themes_csv` | `chart_themes.csv` | `primary` | `text/csv` |
| `report_json` | `report.json` | `primary` | `application/json` |
| `comments_csv` | `comments.csv` | `advanced` | `text/csv` |

`tier: "primary"` - deck-ready downloads: the PDF report, the summary, and the chart data CSVs.
`tier: "advanced"` - raw per-comment audit data for deeper inspection.

Errors:
- `404` artifact record not found, or file missing on disk.

### Serialized artifact object

Included in `GET /runs/{id}` responses when the run is complete:

```json
{
  "id": "...",
  "runId": "...",
  "kind": "pdf | csv | json | file",
  "name": "report.pdf",
  "fileKind": "report_pdf",
  "tier": "primary | advanced",
  "size": 123456,
  "addedAt": "..."
}
```

`kind` is a client-facing hint derived from the file extension. `fileKind` is the DB `kind` value used to distinguish CSVs from each other. `tier` indicates the intended audience: `primary` for deck-ready outputs, `advanced` for raw audit data.

---

## Static files

`app/` is mounted at `/` when the directory exists. Any request that does not match an `/api/*` route falls through to the static handler, so `http://localhost:8000/` serves the frontend and `http://localhost:8000/style.css` serves the stylesheet.
