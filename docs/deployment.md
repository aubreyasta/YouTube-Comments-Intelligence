# Deployment

This file records the current deployment state and keeps the reference procedure for a single Mac host. Run commands from the repository root unless a step says otherwise.

Never record a password, API key, or `Authorization` header. Record `set/non-empty`, not the value.

---

## Current state (2026-09-11)

The project is finished. Acceptance ran on a developer laptop against the real model through a local relay (see [Setup](setup.md#using-a-remote-lm-studio)). No production deployment serves real users yet.

A Dokploy instance on a remote workstation keeps a test copy in sync with `main`:

- A push to `main` runs `.github/workflows/trigger.yml`, which calls the Dokploy deploy webhook stored in the `WEBHOOK_MAIN` secret. The job fails when that secret is empty.
- Dokploy builds the image from `railpack.json`: a virtual environment at `/app/.venv`, the Python dependencies, and Playwright Chromium.
- The container starts with `python server.py`. The image contains no `cloudflared`, and `start.sh` is unused.

Open operational decisions:

- **Model access.** Dokploy runs the app as a Docker Swarm service, which has no host-network mode, so the container cannot use a loopback `LLM_BASE_URL`. Since #29, `LLM_BASE_URL` accepts any host. Point it at the workstation's private address and set `LLM_HEADERS` to an LM Studio API token (see [Setup](setup.md#using-a-remote-lm-studio)). This configuration is not applied yet.
- **Persistence.** The Dokploy service has no persistent volume for `data/`. A redeploy loses Sessions, uploads, reports, users, and login sessions.
- **Sign-in URL.** Since #30, sign-in needs a stable `https` `APP_BASE_URL` registered as the Google OAuth redirect. The Dokploy service needs a domain and the sign-in variables from [Setup](setup.md#configure-the-backend). This configuration is not applied yet.

---

## Reference procedure: single Mac host

This procedure describes the original target: FastAPI, LM Studio, and `cloudflared` on one MacBook Pro M1 Max. Nobody ran it to completion. The evidence tables below are empty, and the 2026-09-11 web-app acceptance replaced it (see [README](../README.md#status)). Keep it as the procedure for any single-machine deployment.

Target shape:

```text
External browser
  -> https://<APP_BASE_URL host>          Cloudflare HTTPS, named tunnel
  -> cloudflared, outbound from the Mac
  -> http://127.0.0.1:8000                FastAPI, loopback only
  -> Google sign-in session middleware
  -> static frontend or /api route
  -> http://127.0.0.1:1234                LM Studio, loopback only
  -> Qwen3.8-27B 4-bit MLX
```

Publish only port 8000. Never publish LM Studio.

---

## Step 1 - Confirm the Mac

Run:

```bash
sw_vers
uname -m
system_profiler SPHardwareDataType
python3 --version
git --version
df -h /
pmset -g custom
```

Require:

- macOS 14 or newer.
- `arm64` architecture.
- Apple M1 Max.
- 32 GB unified memory.
- Python 3.10 or newer.
- Enough free disk for the repository, Python environment, Playwright Chromium, LM Studio, the model, uploads, and reports.

Prevent automatic sleep while the Mac runs the service. Configure this in **System Settings > Lock Screen** and **Energy Saver**. Keep the Mac connected to power and Ethernet where available.

### Evidence

| Item | Value | Recorded |
|---|---|---|
| Date | | |
| macOS version | | |
| Architecture | | |
| Chip | | |
| Unified memory | | |
| Free disk | | |
| Python version | | |
| Git version | | |
| Automatic sleep disabled | | |

---

## Step 2 - Install LM Studio and the model

Install LM Studio from <https://lmstudio.ai/download>. Open it once to initialize the application and `lms` CLI.

Start the headless service and local API:

```bash
lms daemon up
lms server start --port 1234
lms server status
```

Download a 4-bit MLX build of `Qwen3.8-27B` in LM Studio. Select a build that reports vision support. LM Studio's catalog display name is not necessarily its API identifier.

Inspect the model inventory:

```bash
lms ls
curl -s http://127.0.0.1:1234/api/v1/models | python3 -m json.tool
```

Record the exact `key`, selected quantization, format, size, maximum context, and `capabilities.vision`. Require:

- Model family: `Qwen3.8-27B`.
- Format: `mlx`.
- Quantization: 4-bit.
- Vision: `true`.

Load the selected model with a 32,768-token context. Use the exact key from the inventory:

```bash
lms load '<exact-model-key>' --context-length 32768 --identifier youtube-intelligence
lms ps
```

If `youtube-intelligence` becomes the API identifier, use it consistently as `LLM_MODEL`. Otherwise, use the exact identifier shown by `/api/v1/models` after loading.

Disable thinking for this model in LM Studio. The pipeline requests strict structured output and must not receive a reasoning transcript.

Verify the listener:

```bash
lsof -nP -iTCP:1234 -sTCP:LISTEN
```

Require a loopback listener. Do not enable **Serve on Local Network**.

### Structured-output smoke test

Replace `<model-id>` with the exact API identifier:

```bash
curl -s http://127.0.0.1:1234/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "<model-id>",
    "messages": [{"role": "user", "content": "Return the requested object."}],
    "temperature": 0,
    "seed": 0,
    "max_tokens": 32,
    "stream": false,
    "response_format": {
      "type": "json_schema",
      "json_schema": {
        "name": "readiness",
        "strict": true,
        "schema": {
          "type": "object",
          "properties": {"status": {"type": "string", "enum": ["ready"]}},
          "required": ["status"],
          "additionalProperties": false
        }
      }
    }
  }' | python3 -m json.tool
```

Require `choices[0].message.content` to contain `{"status":"ready"}` or equivalent valid JSON. Reject output that includes reasoning text outside the JSON object.

### Image smoke test

Use a small local PNG or JPEG. Send it through LM Studio's OpenAI-compatible multimodal message format. Require a non-empty answer that describes only visible content. The application test added with the provider migration is the authoritative request-shape check.

### Evidence

| Item | Value | Recorded |
|---|---|---|
| LM Studio version | | |
| `lms` version | | |
| Exact API model identifier | | |
| Format and quantization | | |
| Model size | | |
| Vision capability | | |
| Loaded context | | |
| Thinking disabled | | |
| Structured-output smoke test | | |
| Image smoke test | | |
| Port 1234 loopback-only | | |

---

## Step 3 - Install the application

Clone the repository and create the environment:

```bash
git clone https://github.com/aubreyasta/YouTube-Comments-Intelligence.git
cd YouTube-Comments-Intelligence
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt -r requirements-server.txt
python -m playwright install chromium
```

Start with no migrated application data. Do not copy `data/`, `config.py`, uploads, or artifacts from another machine.

Confirm the exclusions:

```bash
git status --short
git check-ignore -v .env config.py data/
```

---

## Step 4 - Configure secrets and model access

Create `.env` in the repository root:

```text
YOUTUBE_API_KEY=<YouTube Data API v3 key>
GOOGLE_CLIENT_ID=<OAuth client ID>
GOOGLE_CLIENT_SECRET=<OAuth client secret>
APP_BASE_URL=https://<stable hostname>
AUTH_ALLOWED_DOMAINS=<company.com>
ADMIN_EMAILS=<admin@company.com>
LLM_BASE_URL=http://127.0.0.1:1234
LLM_MODEL=<exact LM Studio API identifier>
LLM_CONTEXT_LENGTH=32768
LLM_TIMEOUT_SECONDS=600
CLASSIFY_BATCH_SIZE=16
```

Create the OAuth client first, with `<APP_BASE_URL>/auth/callback` as its redirect URI (see [Setup](setup.md#create-the-google-sign-in-client)). Every signed-in user can read every Session, upload, and report. Admins can block or erase users; there are no other per-user permissions.

On this single-host procedure LM Studio stays on loopback, so `LLM_HEADERS` is not needed. If LM Studio moves to another host, follow [Setup](setup.md#using-a-remote-lm-studio).

### Evidence

| Item | Value | Recorded |
|---|---|---|
| `YOUTUBE_API_KEY` set/non-empty | | |
| `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` set/non-empty | | |
| `APP_BASE_URL` exact | | |
| `AUTH_ALLOWED_DOMAINS` and `ADMIN_EMAILS` exact | | |
| `LLM_BASE_URL` exact | | |
| `LLM_MODEL` matches inventory | | |
| `LLM_CONTEXT_LENGTH` | | |
| `CLASSIFY_BATCH_SIZE` | | |
| `.env`, `config.py`, and `data/` ignored | | |

---

## Step 5 - Start FastAPI and verify authentication

Activate the environment and start the backend:

```bash
source .venv/bin/activate
python server.py
```

Leave it running. In a second terminal, check the gate:

```bash
curl -i http://127.0.0.1:8000/
curl -i http://127.0.0.1:8000/app.js
curl -i http://127.0.0.1:8000/api/sessions
curl -i --cookie yi_session=wrong http://127.0.0.1:8000/api/sessions
lsof -nP -iTCP:8000 -sTCP:LISTEN
```

Require:

- The frontend and static asset return `302` to `/auth/login`.
- The API returns `401` with `"error": "UNAUTHENTICATED"`, with and without a wrong cookie.
- Port 8000 listens only on loopback.

Sign-in itself needs the public hostname, so it is checked in Step 6.

### Evidence

| Check | Result | Recorded |
|---|---|---|
| Startup fails without `GOOGLE_CLIENT_ID` | | |
| Unauthenticated frontend returns 302 | | |
| Unauthenticated static asset returns 302 | | |
| Unauthenticated API returns 401 | | |
| Wrong session cookie returns 401 | | |
| Port 8000 loopback-only | | |

---

## Step 6 - Publish through a Cloudflare named tunnel

Sign-in needs a stable hostname, so a quick tunnel's random `trycloudflare.com` URL does not work. Use a Cloudflare named tunnel on a domain you control.

Install `cloudflared` and create the tunnel:

```bash
brew install cloudflared
cloudflared --version
cloudflared tunnel login
cloudflared tunnel create youtube-intelligence
cloudflared tunnel route dns youtube-intelligence <stable hostname>
```

Start the tunnel with FastAPI already running:

```bash
cloudflared tunnel run --url http://127.0.0.1:8000 youtube-intelligence
```

Do not add a router port forward or a LAN bind.

From a phone on mobile data or another external network:

1. Open `APP_BASE_URL`. Confirm it redirects to Google before any content loads.
2. Sign in with a personal Google account. Confirm the "This Google account can't sign in" page.
3. Sign in with a Workspace account in `AUTH_ALLOWED_DOMAINS`. Confirm the UI loads without a mixed-content warning.
4. Confirm ports 8000 and 1234 are not directly reachable externally.
5. Add an article User Input with `http://127.0.0.1:1234/`.
6. Require HTTP 422 and no new asset:

```json
{"error":"VALIDATION_ERROR","message":"That link points to a private address and cannot be fetched.","field":"url"}
```

7. As an admin in `ADMIN_EMAILS`, open **Users**, block a second test account, and confirm that account's next request returns to sign-in and its sign-in is refused. Unblock it.

Stop if any content is reachable without sign-in, either local port is externally exposed, TLS fails, or the tunnel points anywhere except `127.0.0.1:8000`.

### Evidence

| Check | Result | Recorded |
|---|---|---|
| `cloudflared` version | | |
| Tunnel name and hostname | | |
| Redirect to Google before content | | |
| Personal account refused | | |
| Workspace account loads UI | | |
| No mixed-content warning | | |
| Ports 8000 and 1234 not externally reachable | | |
| Private-address article returns 422 and creates no asset | | |
| Admin block signs the user out and refuses sign-in | | |

---

## Step 7 - Run one real external Session

Run this from an external browser through `APP_BASE_URL`:

1. Create a Session.
2. Add a valid YouTube video URL.
3. Add at least one grounded User Input, including one image.
4. Generate or review the Key Messages.
5. Start the analysis with skip-pause unchecked.
6. At `brief_pause`, edit one Key Message, save it, and continue.
7. Close the browser tab while processing continues.
8. Reopen the URL and confirm the run restores without a second sign-in.
9. Wait for completion.
10. Download `report.pdf`, `comments.csv`, `key-messages.csv`, `themes.csv`, `sentiment.csv`, and `emotions.csv`.
11. Confirm `report.json` is not offered as a download.
12. Run a second small Session with skip-pause checked and at least one included Key Message. Confirm it enters classification without pausing.

During classification, inspect LM Studio and macOS:

```bash
lms ps
```

Open Activity Monitor and inspect **Memory Pressure**, LM Studio memory, and swap use. Record LM Studio tokens per second where available, total comments, and wall-clock duration.

There is no fixed timing gate. Do not change context, batch size, timeout, or quantization during this run. Record failure or memory pressure first. Tune only in a separate run.

Inspect `comments.csv`. Require:

```text
video_id,group,comment,likes,language,theme,sentiment,emotion
```

followed by one `key_message_<id>` column per Key Message. Require all sentiment and emotion values to stay inside the locked enums. Open `report.pdf` and confirm every section renders.

### Evidence

| Check | Result | Recorded |
|---|---|---|
| Session completed end to end | | |
| Image User Input succeeded | | |
| Review-pause edit and continue | | |
| Tab close and restore | | |
| Six artifacts downloaded | | |
| `report.json` hidden | | |
| `comments.csv` header exact | | |
| Labels inside locked sets | | |
| PDF renders every section | | |
| LM Studio model/context during classification | | |
| Peak Memory Pressure and swap | | |
| Tokens per second | | |
| Total comments and duration | | |
| Skip-pause Session reaches classification | | |

---

## Step 8 - Start services at login

### LM Studio

Open LM Studio settings and enable **Run the LLM server on login**. Confirm **Auto Server Start** restores the loopback server. Keep **Serve on Local Network** disabled.

### FastAPI

Create `~/Library/LaunchAgents/com.youtube-intelligence.backend.plist`. Replace every placeholder with an absolute path:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.youtube-intelligence.backend</string>
  <key>ProgramArguments</key>
  <array>
    <string>/absolute/path/to/repo/.venv/bin/python</string>
    <string>server.py</string>
  </array>
  <key>WorkingDirectory</key><string>/absolute/path/to/repo</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>/absolute/path/to/repo/data/backend.log</string>
  <key>StandardErrorPath</key><string>/absolute/path/to/repo/data/backend-error.log</string>
</dict>
</plist>
```

Load it:

```bash
plutil -lint ~/Library/LaunchAgents/com.youtube-intelligence.backend.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.youtube-intelligence.backend.plist
launchctl kickstart -k gui/$(id -u)/com.youtube-intelligence.backend
```

### Cloudflare named tunnel

Create a second user LaunchAgent for:

```text
<absolute-path-to-cloudflared> tunnel run --url http://127.0.0.1:8000 youtube-intelligence
```

Set `RunAtLoad` and `KeepAlive` to `true`. Write stdout and stderr to files under the repository's gitignored `data/` directory.

Log out and back in. Require:

1. LM Studio responds on `127.0.0.1:1234` with the configured model available.
2. FastAPI responds on `127.0.0.1:8000`: `/api/sessions` returns `401` without a login session.
3. `cloudflared` reports the tunnel connected.
4. External sign-in succeeds through `APP_BASE_URL`.

An `.env` or code change requires a FastAPI restart. A FastAPI or `cloudflared` restart keeps the same public URL. Login sessions survive a FastAPI restart because they live in `data/app.db`.

### Evidence

| Item | Value | Recorded |
|---|---|---|
| LM Studio login startup | | |
| Backend LaunchAgent label | | |
| Cloudflare LaunchAgent label | | |
| Log paths | | |
| Stop and restart commands | | |
| Local checks pass after login | | |
| Tunnel connected after login | | |
| External check passes after login | | |

---

## Accepted operational limits

- Google Workspace sign-in protects one shared workspace. Every signed-in user sees every Session.
- One analysis runs at a time. Other starts wait in a queue, in start order.
- The Mac disk holds the only copy of `data/`.
- The service is down when the Mac is off, asleep, or offline.
- A FastAPI or Mac restart loses an active run. Closing a browser tab does not.
- The 27B model shares 32 GB unified memory with macOS and every other application.
- Model throughput depends on the selected MLX build, context, corpus, and current memory pressure. There is no guaranteed duration.

---

## Troubleshooting

**FastAPI reports that a sign-in variable is missing.** Set `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `APP_BASE_URL`, and `AUTH_ALLOWED_DOMAINS` in `.env`, then restart FastAPI. `APP_PASSWORD` is no longer used.

**Google shows `redirect_uri_mismatch`.** Add `<APP_BASE_URL>/auth/callback` to the OAuth client exactly, and open the app at `APP_BASE_URL`.

**Every save or create fails with `403 CROSS_ORIGIN`.** The browser address differs from `APP_BASE_URL`. Set `APP_BASE_URL` to the public address users open, then restart FastAPI.

**LM Studio does not answer on port 1234.** Run `lms daemon up`, `lms server start --port 1234`, and `lms server status`.

**Preflight reports a missing model.** Compare `LLM_MODEL` with the exact identifier returned by `curl -s http://127.0.0.1:1234/api/v1/models`.

**Preflight reports missing vision support.** Download a vision-capable 4-bit MLX build of `Qwen3.8-27B` and update `LLM_MODEL`.

**Structured output contains reasoning or fails validation.** Disable thinking for the model. Repeat the structured-output smoke test before another real run.

**The run reaches the report stage and fails.** Run `python -m playwright install chromium` inside the active virtual environment.

**Memory Pressure turns yellow or red.** Close other memory-heavy applications. Record the run state and swap first. Lower `LLM_CONTEXT_LENGTH` only in a separate test. Keep the model at 4-bit. Lower `CLASSIFY_BATCH_SIZE` from 16 to 8 only if a real run on this Mac shows memory pressure.

**The public URL stopped working.** Read the `cloudflared` log and confirm the tunnel is connected.

**The backend works locally but not after login.** Inspect the LaunchAgent error log. Confirm every executable and working-directory path in the plist is absolute.
