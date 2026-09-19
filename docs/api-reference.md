# API reference

HTTP contract for the FastAPI backend. Local base URL: `http://127.0.0.1:8000/api`.

Related: [Setup](setup.md), [Architecture](architecture.md), [Product](../README.md).

---

## Conventions

- FastAPI binds `127.0.0.1:8000`. The public Cloudflare URL forwards to that origin.
- A login session protects every request before routing, except `/auth/login`, `/auth/callback`, and `/auth/logout`. Users sign in with Google. See [Authentication](#authentication).
- JSON responses and SSE progress events use `camelCase`.
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

A request without a valid login session gets:

- `401` with `{"error": "UNAUTHENTICATED", "message": "Sign in required.", "field": null}` on `/api/*`.
- `302` to `/auth/login` on any other path, including the frontend and static files.

A `POST`, `PATCH`, or `DELETE` whose `Origin` header is not the origin of `APP_BASE_URL` gets `403` with `{"error": "CROSS_ORIGIN", "message": "Request from another site refused.", "field": null}` on every path, before the session check. A request without an `Origin` header, such as from `curl`, is not checked.

Common status codes:

| Status | Meaning |
|---|---|
| `200` | Success. |
| `201` | Resource created. |
| `202` | Run accepted and started in a background thread. |
| `204` | Deletion, sign-out, or user block change complete. Session content delete routes are idempotent; `DELETE /users/{id}` returns `404` for an unknown user. |
| `401` | No login session, or the session expired, or the user is blocked. |
| `403` | The signed-in user is not an admin (`FORBIDDEN`), or a write came from another origin (`CROSS_ORIGIN`). |
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

### Brief point

A run's Key Message. Session-level Key Messages carry no `source`.

```ts
type BriefPoint = KeyMessage & {
  source: "input" | "sharpened" | "transcript";
};
```

| `source` | Meaning |
|---|---|
| `input` | Came from the Session's Key Messages (User Inputs) or was added at review. Kept as given. |
| `sharpened` | An unedited Session Key Message whose label matched a transcript-derived point and whose description changed to the transcript-grounded one. |
| `transcript` | Derived from transcripts alone; no Session Key Message matched it. |

Rows stored before `source` existed read `input`.

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

type RunProgress = {
  stage: RunStage;
  pct: number;
  message: string;
  counts: {
    total?: number;      // comments in the analysis base, set after `collect`
    themes?: number;     // Themes discovered, set when `classify` starts
    labelled?: number;   // comments labelled so far
    batch?: number;      // finished classify batches
    batches?: number;    // total classify batches
    otherShare?: number; // percent left in `Other`, set when `classify` ends
  };
  error: string | null;
  queuePosition: number | null; // 1 = next to start; null once the run has started
};

type RunSnapshot = RunProgress & {
  id: string;
  sessionId: string;
  createdAt: string;
  status: "queued" | "running" | "complete" | "failed";
  skipPause: boolean;
  briefPoints: BriefPoint[];
  artifacts: Artifact[];
};
```

`RunProgress` is the Run progress snapshot. The server persists it on the run row, so `GET /runs/{id}` and every SSE event carry the same value. A reopened or second tab repaints from it without replay.

`createdAt` is the time the run joined the queue while it is `queued`, and its start time once it runs; the results page dates the strategy note from it. `briefPoints` and `artifacts` are always present. They are empty until data exists. A later stage never removes an earlier count. A terminal `status` wins: `complete` reads stage `complete`, and `failed` reads stage `error`.

### Artifact

```ts
type Artifact = {
  id: string;
  kind: string;
  filename: string;
  contentType: string;
  downloadUrl: string;
  size: number | null;
};
```

`size` is the stored file's size in bytes, or `null` when the file is missing on disk.

---

## Authentication

Google OAuth (authorization code flow with PKCE) signs users in. The `/auth/*` routes sit at the origin root, outside `/api`.

### `GET /auth/login`

Redirect `302` to Google's account picker. Sets the short-lived `yi_oauth` cookie (state, nonce, PKCE verifier; path `/auth`, 10 minutes).

### `GET /auth/callback`

Google redirects here. The server exchanges the code, then requires a matching state and nonce, `email_verified`, and an `hd` claim in `AUTH_ALLOWED_DOMAINS`. A personal Google account has no `hd` claim and fails.

- Success: creates the user on first sign-in, sets the `yi_session` cookie (HttpOnly, SameSite=Lax, `Secure` when `APP_BASE_URL` is `https`, 7 days), and redirects `302` to `/`.
- Failure: `403` HTML page "This Google account can't sign in". The page is the same for every cause, including a blocked user.

The database stores only the SHA-256 hash of the session token.

### `POST /auth/logout`

Delete the login session and clear the cookie. Response `204`.

### `GET /me`

```json
{ "email": "person@example.com", "name": "Person", "isAdmin": false }
```

`isAdmin` is true when the email is in `ADMIN_EMAILS`. The server reads the list on every request.

### User object

```ts
type User = {
  id: string;
  email: string;
  name: string | null;
  blocked: boolean;
  isAdmin: boolean;
  lastLoginAt: string | null;
};
```

### `GET /users`

Admin only. List users, most recent sign-in first. Users who never signed in come last.

### `PATCH /users/{id}`

Admin only. Block or unblock a user. Blocking deletes the user's login sessions, so the next request from that user gets `401`.

```json
{ "blocked": true }
```

Response `204`.

Errors: `404` when the user does not exist; `422` when an admin blocks their own account.

### `DELETE /users/{id}`

Admin only. Erase a user and their login sessions. Sessions they created stay, with `createdBy` set to `null`. An erased user in an allowed domain can sign in again; block the user to keep them out.

Response `204`. Errors: `404` when the user does not exist; `422` when an admin erases their own account.

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
  "createdBy": "person@example.com",
  "latestRun": null,
  "keyMessages": {
    "status": "empty",
    "messages": [],
    "error": null,
    "revision": 0
  }
}
```

`createdBy` is the creator's email. It is `null` for Sessions created before sign-in existed or by an erased user. Every signed-in user can read and change every Session.

Error: `422` when `name` is empty.

### `GET /sessions`

List Sessions newest first. Each item adds `campaignCount`.

`status` is `ready`, `queued`, `running`, `complete`, or `failed`, derived from the latest run. `queued` means the Session's run waits in the queue. `commentCount` counts CSV records in the latest complete run's readable `comments_csv`; otherwise it is `0`.

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

### `PATCH /sessions/{id}`

Rename a Session.

```json
{ "name": "New name" }
```

The name is trimmed and validated like `POST /sessions`. One transaction updates the Session name, its campaign name (the two never diverge), and `updatedAt`. A running run keeps the name it started with.

Response `200`: the `GET /sessions/{id}` shape.

Errors: `404` Session not found; `422` empty or missing name, `field` is `name`.

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

### `PATCH /videos/{id}`

Change a video's kind.

```json
{ "kind": "review" }
```

`kind` is `auto`, `brand_ad`, `review`, or `explainer`. The route also updates the owning Session's `updatedAt`. It has no running-run guard: a run reads kinds once at start, so a change during a run applies to the next run.

Response `200`: video object, as returned by `POST /campaigns/{id}/videos`.

Errors: `404` video not found; `422` invalid kind, `field` is `kind`.

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

Queue one analysis. Runs start one at a time, in start order, across all Sessions.

Request body is optional. Omitted means `skipPause:false`.

```json
{ "skipPause": false }
```

The server inserts a `queued` run and starts it at once if no run is `running`. Otherwise the run waits, and `queuePosition` gives its place in line. A new start replaces the Session's own queued run and keeps its place in line. A Session with a `running` run refuses a new start.

When the new run starts, it deletes the Session's prior runs and files. There is no run history. Until then, the prior result stays readable.

Response `202`: `RunSnapshot`. When the run starts immediately, its `status` is already `running`.

Errors:

- `404` Session not found.
- `409 RUN_IN_PROGRESS` with `"This session already has a run in progress."`

`skipPause:true` bypasses `brief_pause` only when reconciliation leaves at least one included Key Message. Zero included rows always pause.

### `GET /runs/{id}`

Return `RunSnapshot`.

Error: `404` run not found.

### `DELETE /runs/{id}`

Remove a `queued` run from the queue. The running run and the Session's prior result are not changed.

Response `204`, also for an unknown id.

Error: `409 CONFLICT` with `"This run has already started, so it can no longer leave the queue."`

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

`id:null` creates a server UUID. Omitted existing rows are deleted. Submitted array order wins. An empty or all-excluded list can be saved; proceeding still requires one included row. The request carries no `source`: a kept row keeps its stored `source` by id, and an `id:null` row gets `input`.

Response `200`:

```json
{ "messages": [ { "id": "...", "label": "...", "description": "...", "included": true, "order": 0, "source": "input" } ] }
```

Errors: `404` run or campaign not found; `409` review is not open or already continued; `422` invalid, duplicate, unknown, or foreign ID.

### `POST /runs/{id}/proceed`

Continue a run paused at `brief_pause`.

Response `200`: current `RunSnapshot`.

Errors: `404` run not found; `409` run is not waiting; `422` no Key Message is included.

### `POST /runs/{id}/review_activity`

Report activity on the Key Message review. A run at `brief_pause` with no activity for 10 minutes, while another run is `queued`, fails with `"Stopped: the Key Message review had no activity for 10 minutes while another analysis was waiting."`. That frees the run slot for the queue. With nothing queued, the review waits indefinitely. Each call restarts that clock. The review page sends it at most every 30 seconds while someone interacts with it.

Response `204`.

Error: `409 CONFLICT` with `"This run is not waiting for review."`, also for an unknown id.

### `GET /runs/{id}/events`

Open the SSE progress stream. Each data event is a `RunProgress` (see [Run snapshot](#run-snapshot)):

```text
data: {"stage":"classify","pct":60,"message":"Classifying comments","counts":{"total":574,"themes":7,"labelled":120,"batch":3,"batches":15},"error":null}\n\n
```

The server reads the persisted snapshot about once a second and sends it when it changes. Every open stream on a run receives the same events.

Stages:

| Stage | Typical percent | Meaning |
|---|---:|---|
| `queued` | 0 | Run waits in the queue; `queuePosition` gives its place. |
| `collect` | 2-20 | Load context, fetch comments and transcripts, clean rows. |
| `brief` | 22-40 | Reconcile Key Messages. A skip-pause run may continue from this stage. |
| `brief_pause` | 40 | Wait for review and `/proceed`. Fails after 10 idle minutes if a run is queued. |
| `themes` | 42 | Discover Themes from a comment sample. |
| `classify` | 50-65 | Classify all labels, optionally refine `Other`. |
| `emotion` | 67-75 | Validate and aggregate Sentiment and Emotion already assigned by classification. |
| `report` | 77-88 | Write Report JSON, PDF, and CSVs. |
| `complete` | 100 | Run complete. |
| `error` | - | Run failed; `error` carries the exception string. `pct` keeps its last value. |

`classify` updates `counts.labelled`, `counts.batch`, and `counts.batches` once per finished batch. `labelled` counts comments, not batches.

An idle stream emits `: heartbeat\n\n` every 15 seconds. Comment frames do not trigger `EventSource.onmessage`. The stream sends the terminal snapshot and closes.

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
  "sentiment": [
    { "metricId": "m-se-positive", "label": "positive", "count": 34, "percent": 41.0 }
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
          "sentiment": "positive",
          "emotion": "joy"
        }
      ]
    }
  ],
  "title": "The Meme Outlived\nthe Message",
  "interpretation": "...\n\n...",
  "quote": { "text": "...", "attr": "comment · 47 likes" },
  "caveat": "..."
}
```

`overallTransfer` is the share of eligible rows that mention at least one applicable included Key Message. `keyMessages`, `themes`, `emotions`, `sentiment`, and `keyMessageSentiment` carry Python-counted values. Percentages use one decimal. `emotions` and `sentiment` count over every row in the analysis base, matching `emotions.csv` and `sentiment.csv`; `keyMessageSentiment` counts over the rows that mention that Key Message and carry a recognized Sentiment.

Each evidence group contains up to eight comments, ranked by likes and then text length. Groups run in metric order: Key Messages, Themes, Emotions, Sentiment, Key Message Sentiment. Key Message Sentiment evidence selects up to four positive and four negative rows, then backfills from the best remaining recognized Sentiment rows. A comment's `sentiment` or `emotion` is `null` when the label is missing or unrecognized. Metric IDs remain unique when labels slugify to the same value.

`title`, `interpretation`, `quote`, and `caveat` are the written read the results screen renders. They are the only model-written fields in the response; every number above them is counted in Python. A run that completed before these fields existed omits them, and the screen drops the section they feed.

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

FastAPI serves `app/` after all `/api` routes. `/` serves `index.html`; assets such as `/app.js` and `/style.css` use the same signed-in origin. No CORS configuration or second frontend server is required.
