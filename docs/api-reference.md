# API reference

HTTP contract for the FastAPI backend. Local base URL: `http://127.0.0.1:8000/api`.

Related: [Setup](setup.md), [Architecture](architecture.md), [Product](../README.md).

---

## Conventions

- FastAPI binds `127.0.0.1:8000`. The public Cloudflare URL forwards to that origin.
- Fail-closed HTTP Basic Auth protects every request before routing. Any non-empty username works with the shared `APP_PASSWORD`.
- JSON responses use `camelCase`. SSE progress events use `snake_case` because they carry `adapter.py` dictionaries directly.
- IDs are UUID v4. Timestamps are ISO 8601 with a timezone.
- Uploads use `multipart/form-data`. SSE uses `text/event-stream`. Downloads return their recorded MIME type.

### Errors

API errors use one unwrapped shape:

```ts
type ApiError = {
  error: string;
  message: string;
  field: string | null;
};
```

Example:

```json
{
  "error": "VALIDATION_ERROR",
  "message": "Include at least one Key Message before continuing.",
  "field": "messages"
}
```

FastAPI `HTTPException` and request-validation handlers both return this flat object. Clients never receive a `detail` envelope.

Authentication failures return `401` and include:

```text
WWW-Authenticate: Basic realm="YouTube Intelligence", charset="UTF-8"
```

Common status codes:

| Status | Meaning |
|---|---|
| `200` | Success. |
| `201` | Resource created. |
| `202` | Run accepted and started in a background thread. |
| `204` | Deletion complete. Delete routes are idempotent. |
| `401` | Missing, malformed, or wrong Basic Auth credentials. |
| `404` | Resource not found. |
| `409` | Current state prevents the operation. |
| `413` | Upload exceeds 10 MB. |
| `422` | Request content failed validation. |

---

## Shared shapes

### Key Message

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
```

`id:null` creates a server UUID. The submitted array defines order; the server does not trust the submitted `order` value. Labels are trimmed, required, and limited to 120 characters. Descriptions are trimmed and limited to 500 characters.

### Run snapshot

```ts
type RunStage =
  | "queued"
  | "collect"
  | "brief"
  | "brief_pause"
  | "themes"
  | "classify"
  | "emotion"
  | "report"
  | "complete"
  | "error";

type RunSnapshot = {
  id: string;
  sessionId: string;
  status: "queued" | "running" | "complete" | "failed";
  stage: RunStage;
  pct: number;
  message: string;
  error: string | null;
  skipPause: boolean;
  totalComments: number | null;
  briefPoints: KeyMessage[];
  artifacts: Artifact[];
};
```

`briefPoints` and `artifacts` are always present. They are empty until data exists. A fresh GET uses the persisted stage, so a paused run restores as `brief_pause` without SSE replay. `totalComments` is `null` until the `collect` stage finishes, then holds the analysis-base comment count (persisted the same way as `stage`), so a reopened run can paint it without SSE replay.

### Artifact

```ts
type Artifact = {
  id: string;
  kind: string;
  filename: string;
  contentType: string;
  downloadUrl: string;
};
```

---

## Sessions

### `POST /sessions`

Create a Session.

Request:

```json
{ "name": "Campaign analysis" }
```

Response `201`:

```json
{
  "id": "...",
  "name": "Campaign analysis",
  "campaignIds": [],
  "commentCount": 0,
  "status": "ready",
  "updatedAt": "...",
  "createdAt": "...",
  "latestRun": null,
  "keyMessages": {
    "status": "empty",
    "messages": [],
    "error": null,
    "revision": 0
  }
}
```

Error: `422` when `name` is empty.

### `GET /sessions`

List Sessions newest first. Each item adds `campaignCount`.

`status` is `ready`, `running`, `complete`, or `failed`, derived from the latest run. `commentCount` counts CSV records in the latest complete run's readable `comments_csv`; otherwise it is `0`.

`latestRun` is `null` or:

```json
{
  "id": "...",
  "status": "queued | running | complete | failed",
  "stage": "queued | collect | brief | brief_pause | themes | classify | emotion | report | complete | error",
  "pct": 0,
  "message": "",
  "error": null
}
```

### `GET /sessions/{id}`

Return one Session. The response adds nested `campaigns` and `runs`. Every run uses `RunSnapshot`.

Error: `404` when the Session does not exist.

---

## Session Key Messages

### `POST /sessions/{id}/key_messages/draft`

Draft Key Messages from the latest persisted User Inputs. The route returns `KeyMessageDraft`.

One drafting pass runs at a time per Session. A request that arrives during a pass requests one coalesced rerun against the latest saved inputs, waits, and returns that result.

Draft merge rules preserve edited rows. A matching unedited row receives the fresh generated label and description while retaining inclusion and order. Unmatched edited rows stay. New proposals append.

Each completed pass, including failed and empty passes, increments `revision`. A failure keeps existing rows and returns `stale`; without existing rows it returns `failed`.

Error: `404` when the Session does not exist.

### `PATCH /sessions/{id}/key_messages`

Atomically replace the complete Session Key Message list.

Request:

```json
{
  "messages": [
    {
      "id": null,
      "label": "Real green chili",
      "description": "The product uses real green chili.",
      "included": true,
      "order": 0
    }
  ]
}
```

Response `200`: `KeyMessageDraft` with the saved rows. This route does not increment `revision`.

An empty list and an all-excluded list are valid during setup. Non-null IDs must be unique and belong to this Session.

Errors:

- `404` Session not found.
- `422` invalid label or description, duplicate ID, unknown ID, or foreign ID. `field` is `messages`.

---

## Campaigns

A Session holds at most one internal campaign row.

### `POST /sessions/{id}/campaigns`

Request:

```json
{ "name": "Campaign analysis" }
```

Response `201`: campaign object.

Errors: `404` Session not found; `409` campaign already exists; `422` empty name.

### `GET /sessions/{id}/campaigns`

Return campaign rows with nested videos, User Inputs, and the latest run's `briefPointIds`.

Error: `404` Session not found.

---

## Videos

### `POST /campaigns/{id}/videos`

Add a YouTube video.

```json
{ "url": "https://youtu.be/...", "kind": "auto" }
```

`kind` is `auto`, `brand_ad`, `review`, or `explainer`. The route accepts `youtube.com/watch?v=`, `youtu.be/`, and `youtube.com/shorts/`. It rejects playlists and duplicates within the campaign.

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

Errors: `404` campaign not found; `422` invalid URL, duplicate URL, or invalid kind.

### `DELETE /videos/{id}`

Delete a video. Returns `204`, including when the ID does not exist.

---

## User Inputs

### `POST /campaigns/{id}/assets/upload`

Upload field `file` as `multipart/form-data`. Accepted extensions: `.pdf`, `.pptx`, `.docx`, `.png`, `.jpg`, `.jpeg`, `.webp`. Maximum size: 10 MB.

The route stores the file and extracts document text before returning. Images retain their file path for multimodal model calls and have empty extracted text.

Response `201`: asset object.

Errors: `404` campaign not found; `413` file over 10 MB; `422` empty or unsupported filename.

### `POST /campaigns/{id}/assets/article`

Fetch and store an article snapshot.

```json
{ "url": "https://example.com/article" }
```

The server accepts public HTTP and HTTPS destinations on default ports. It resolves and pins the destination before connecting and revalidates redirects. A private, loopback, link-local, non-default-port, or mixed public/private destination returns:

```json
{
  "error": "VALIDATION_ERROR",
  "message": "That link points to a private address and cannot be fetched.",
  "field": "url"
}
```

An ordinary public-host timeout or extraction failure may create an asset with empty text.

Response `201`: asset object.

Errors: `404` campaign not found; `422` invalid or prohibited URL.

### Asset object

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
  "status": "ready"
}
```

### `DELETE /assets/{id}`

Delete a User Input and its stored upload file. Returns `204` when the ID does not exist.

### `GET /assets/{id}/file`

Download an uploaded file. Articles have no downloadable file.

Error: `404` when the row, path, or file does not exist.

---

## Runs

### `POST /sessions/{id}/runs`

Start one analysis in a background thread.

Request body is optional. Omitted means `skipPause:false`.

```json
{ "skipPause": false }
```

The server acquires an immediate SQLite write transaction and rejects a start while any Session has a `queued` or `running` run. It performs this guard before deleting the target Session's prior result.

After admission, the new run replaces that Session's prior run and files. There is no run history.

Response `202`: `RunSnapshot`.

Errors:

- `404` Session not found.
- `409 RUN_IN_PROGRESS` with `"This session already has a run in progress."`
- `409 RUN_IN_PROGRESS` with `"Another analysis is already running. Wait for it to finish."`

`skipPause:true` bypasses `brief_pause` only when reconciliation leaves at least one included Key Message. Zero included rows always pause.

### `GET /runs/{id}`

Return `RunSnapshot`.

Error: `404` run not found.

### `PATCH /runs/{id}/brief_points`

Atomically replace the complete run Key Message list while the persisted stage is `brief_pause`.

Request:

```json
{
  "messages": [
    {
      "id": null,
      "label": "New Key Message",
      "description": "Added during review.",
      "included": true,
      "order": 0
    }
  ]
}
```

`id:null` creates a server UUID. Omitted existing rows are deleted. Submitted array order wins. An empty or all-excluded list can be saved; proceeding still requires one included row.

Response `200`:

```json
{ "messages": [ { "id": "...", "label": "...", "description": "...", "included": true, "order": 0 } ] }
```

Errors: `404` run or campaign not found; `409` review is not open or already continued; `422` invalid, duplicate, unknown, or foreign ID.

### `POST /runs/{id}/proceed`

Continue a run paused at `brief_pause`.

Response `200`: current `RunSnapshot`.

Errors: `404` run not found; `409` run is not waiting; `422` no Key Message is included.

### `GET /runs/{id}/events`

Open the SSE progress stream. Data events use `snake_case`:

```text
data: {"run_id":"...","stage":"classify","message":"Classifying comments","pct":60,"detail":null}\n\n
```

Stages:

| Stage | Typical percent | Meaning |
|---|---:|---|
| `collect` | 2-20 | Load context, fetch comments and transcripts, clean rows. |
| `brief` | 22-40 | Reconcile Key Messages. A skip-pause run may continue from this stage. |
| `brief_pause` | 40 | Wait for review and `/proceed`. |
| `themes` | 42 | Discover Themes from a comment sample. |
| `classify` | 50-65 | Classify all labels, optionally refine `Other`. |
| `emotion` | 67-75 | Validate and aggregate Sentiment and Emotion already assigned by classification. |
| `report` | 77-88 | Write Report JSON, PDF, and CSVs. |
| `complete` | 100 | Run complete. |
| `error` | 0 | Run failed; `detail` carries the exception string. |

An idle stream emits `: heartbeat\n\n` every 15 seconds. Comment frames do not trigger `EventSource.onmessage`. A terminal run replays one terminal event and closes.

Error: `404` before the stream opens.

---

## Report JSON

### `GET /runs/{id}/report`

Return the internal `report_json` after completion.

Errors: `404` run not found; `409` run incomplete, artifact missing, or file unreadable.

Exact top-level keys:

```json
{
  "overallTransfer": 41.2,
  "keyMessages": [
    {
      "id": "...",
      "metricId": "m-t-real-green-chili",
      "label": "Real green chili",
      "description": "...",
      "count": 34,
      "percent": 41.2
    }
  ],
  "themes": [
    { "metricId": "m-th-0", "label": "Flavor", "count": 42, "percent": 50.6 }
  ],
  "emotions": [
    { "metricId": "m-em-joy", "label": "joy", "count": 30, "percent": 36.1 }
  ],
  "keyMessageSentiment": [
    {
      "id": "...",
      "metricId": "m-is-real-green-chili",
      "label": "Real green chili",
      "positiveCount": 20,
      "positivePercent": 58.8,
      "negativeCount": 4,
      "negativePercent": 11.8,
      "baseN": 34
    }
  ],
  "evidence": [
    {
      "metricId": "m-t-real-green-chili",
      "comments": [
        {
          "text": "...",
          "likes": 123,
          "videoId": "...",
          "sentiment": "positive"
        }
      ]
    }
  ]
}
```

`overallTransfer` is the share of eligible rows that mention at least one applicable included Key Message. `keyMessages`, `themes`, `emotions`, and `keyMessageSentiment` carry Python-counted values. Percentages use one decimal.

Each evidence group contains up to eight comments, ranked by likes and then text length. Key Message Sentiment evidence selects up to four positive and four negative rows, then backfills from the best remaining recognized Sentiment rows. Metric IDs remain unique when labels slugify to the same value.

---

## Artifacts

### `GET /runs/{id}/artifacts/{artifactId}`

Download one public artifact with `Content-Disposition: attachment`.

| Kind | Filename | Content type | Public |
|---|---|---|---|
| `report_pdf` | `report.pdf` | `application/pdf` | Yes |
| `comments_csv` | `comments.csv` | `text/csv` | Yes |
| `key_messages_csv` | `key-messages.csv` | `text/csv` | Yes |
| `themes_csv` | `themes.csv` | `text/csv` | Yes |
| `sentiment_csv` | `sentiment.csv` | `text/csv` | Yes |
| `emotions_csv` | `emotions.csv` | `text/csv` | Yes |
| `report_json` | `report.json` | `application/json` | No |

`RunSnapshot.artifacts` contains only the first six, in this order. `report_json` is available only through `/runs/{id}/report`. Requesting its artifact record returns `404`.

Error: `404` when the record is unknown, internal, or missing on disk.

### CSV artifact shapes

All five CSVs use UTF-8, comma separators, a header row, `\n` line endings, one-decimal percentages, and deterministic group-first ordering (groups follow first appearance; labels sort by count descending, then case-insensitive label). Empty results still write headers. `pipeline/report.py` is the implementation authority.

- `comments.csv`: `video_id,group,comment,likes,language,theme,sentiment,emotion`, then one `key_message_<stable-id>` boolean column per Key Message, in Key Message order.
- `themes.csv`, `sentiment.csv`, `emotions.csv`: `group,<label>,count,percent,base_n`. Eligible rows carry a non-null, non-empty label. `base_n` is eligible rows in the group. Zero-count labels are omitted.
- `key-messages.csv`: `group,key_message,count,percent,base_n,positive_count,positive_percent,negative_count,negative_percent,sentiment_base_n`. Every applicable group/message pair appears, including zero mentions. Percentages may sum above 100 (a comment can mention several Key Messages). `sentiment_base_n` counts mentioned rows with a recognized sentiment, including neutral. A zero denominator produces `0.0`.

---

## Static frontend

FastAPI serves `app/` after all `/api` routes. `/` serves `index.html`; assets such as `/app.js` and `/style.css` use the same authenticated origin. No CORS configuration or second frontend server is required.
