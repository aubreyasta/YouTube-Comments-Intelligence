# Deployment

This file records the current deployment state and keeps the reference procedure for a single Mac host. Run commands from the repository root unless a step says otherwise.

Never record a password, API key, or `Authorization` header. Record `set/non-empty`, not the value.

---

## Current state (2026-09-11)

The project is finished. Acceptance ran on a developer laptop against the real model through a local relay (see [Setup](setup.md#developing-against-a-remote-lm-studio)). No production deployment serves real users yet.

A Dokploy instance on a remote workstation keeps a test copy in sync with `main`:

- A push to `main` runs `.github/workflows/trigger.yml`, which calls the Dokploy deploy webhook stored in the `WEBHOOK_MAIN` secret. The job fails when that secret is empty.
- Dokploy builds the image from `railpack.json`: a virtual environment at `/app/.venv`, the Python dependencies, and Playwright Chromium.
- The container starts with `python server.py`. The image contains no `cloudflared`, and `start.sh` is unused.

Open operational decisions:

- **Model access.** The container cannot reach LM Studio. Dokploy runs the app as a Docker Swarm service, which has no host-network mode, and `pipeline/llm.py` accepts only a loopback `LLM_BASE_URL`. Either rebuild the service as plain Docker Compose, or relax the loopback check. Neither option is chosen.
- **Persistence.** The Dokploy service has no persistent volume for `data/`. A redeploy loses Sessions, uploads, and reports.

---

## Reference procedure: single Mac host

This procedure describes the original target: FastAPI, LM Studio, and `cloudflared` on one MacBook Pro M1 Max. Nobody ran it to completion. The evidence tables below are empty, and the 2026-09-11 web-app acceptance replaced it (see [README](../README.md#status)). Keep it as the procedure for any single-machine deployment.

Target shape:

```text
External browser
  -> https://<random>.trycloudflare.com   Cloudflare HTTPS
  -> cloudflared, outbound from the Mac
  -> http://127.0.0.1:8000                FastAPI, loopback only
  -> HTTP Basic Auth middleware
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
APP_PASSWORD=<long unique shared password>
LLM_BASE_URL=http://127.0.0.1:1234
LLM_MODEL=<exact LM Studio API identifier>
LLM_CONTEXT_LENGTH=32768
LLM_TIMEOUT_SECONDS=600
CLASSIFY_BATCH_SIZE=16
```

Keep `APP_PASSWORD` long and unique. Anyone who has it can read every Session, upload, and report. There are no accounts or per-user permissions.

Do not add an LM Studio API token. The approved boundary relies on loopback isolation. If the LM Studio server requires a token, stop and update the application contract rather than placing an unsupported secret in `.env`.

### Evidence

| Item | Value | Recorded |
|---|---|---|
| `YOUTUBE_API_KEY` set/non-empty | | |
| `APP_PASSWORD` set/non-empty | | |
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
curl -i -u office:wrong-password http://127.0.0.1:8000/api/sessions
curl -i -u office:<the-password> http://127.0.0.1:8000/
curl -i -u office:<the-password> http://127.0.0.1:8000/api/sessions
lsof -nP -iTCP:8000 -sTCP:LISTEN
```

Require:

- Missing and wrong credentials return `401`.
- Every `401` includes `WWW-Authenticate: Basic realm="YouTube Intelligence", charset="UTF-8"`.
- Correct credentials return `200`.
- Port 8000 listens only on loopback.

Open <http://127.0.0.1:8000>, authenticate, create a Session, and reach setup. Stop before starting an analysis.

### Evidence

| Check | Result | Recorded |
|---|---|---|
| Startup fails without `APP_PASSWORD` | | |
| Unauthenticated frontend returns 401 | | |
| Unauthenticated static asset returns 401 | | |
| Unauthenticated API returns 401 | | |
| Wrong password returns 401 | | |
| Correct password serves the UI | | |
| Port 8000 loopback-only | | |
| Local Session reaches setup | | |

---

## Step 6 - Publish through a Cloudflare quick tunnel

Install `cloudflared`:

```bash
brew install cloudflared
cloudflared --version
```

Start the tunnel with FastAPI already running:

```bash
cloudflared tunnel --url http://127.0.0.1:8000
```

Record the generated `https://<random>.trycloudflare.com` URL. The hostname changes whenever `cloudflared` restarts.

Do not create a named tunnel, DNS record, Access application, router port forward, or LAN bind.

From a phone on mobile data or another external network:

1. Open the generated HTTPS URL.
2. Confirm the credential prompt appears before content.
3. Enter a wrong password and confirm rejection.
4. Enter the correct password and confirm the UI loads without a mixed-content warning.
5. Confirm ports 8000 and 1234 are not directly reachable externally.
6. Add an article User Input with `http://127.0.0.1:1234/`.
7. Require HTTP 422 and no new asset:

```json
{"error":"VALIDATION_ERROR","message":"That link points to a private address and cannot be fetched.","field":"url"}
```

Stop if any content is reachable without credentials, either local port is externally exposed, TLS fails, or the tunnel points anywhere except `127.0.0.1:8000`.

### Evidence

| Check | Result | Recorded |
|---|---|---|
| `cloudflared` version | | |
| Generated hostname | | |
| External credential prompt | | |
| Wrong password rejected | | |
| Correct password loads UI | | |
| No mixed-content warning | | |
| Ports 8000 and 1234 not externally reachable | | |
| Private-address article returns 422 and creates no asset | | |

---

## Step 7 - Run one real external Session

Run this from an external browser through the quick-tunnel URL:

1. Create a Session.
2. Add a valid YouTube video URL.
3. Add at least one grounded User Input, including one image.
4. Generate or review the Key Messages.
5. Start the analysis with skip-pause unchecked.
6. At `brief_pause`, edit one Key Message, save it, and continue.
7. Close the browser tab while processing continues.
8. Reopen the URL, authenticate, and confirm the run restores.
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

### Cloudflare quick tunnel

Create a second user LaunchAgent for:

```text
<absolute-path-to-cloudflared> tunnel --url http://127.0.0.1:8000
```

Set `RunAtLoad` and `KeepAlive` to `true`. Write stdout and stderr to files under the repository's gitignored `data/` directory. Read the active quick-tunnel URL from that log after login.

Do not add code that emails, publishes, or scrapes the URL into another service. A stable URL requires a named tunnel and domain, which remain out of scope.

Log out and back in. Require:

1. LM Studio responds on `127.0.0.1:1234` with the configured model available.
2. FastAPI responds on `127.0.0.1:8000` with a Basic Auth challenge.
3. `cloudflared` records a current quick-tunnel URL.
4. External authenticated access succeeds through that URL.

A FastAPI restart requires no new public URL while `cloudflared` remains running. An `.env` or code change requires a FastAPI restart. A `cloudflared` restart creates a new public URL.

### Evidence

| Item | Value | Recorded |
|---|---|---|
| LM Studio login startup | | |
| Backend LaunchAgent label | | |
| Cloudflare LaunchAgent label | | |
| Log paths | | |
| Stop and restart commands | | |
| Local checks pass after login | | |
| Current quick-tunnel URL recorded | | |
| External check passes after login | | |

---

## Accepted operational limits

- One shared password protects one shared workspace.
- One analysis runs at a time. A second start request is rejected, not queued.
- The Mac disk holds the only copy of `data/`.
- The service is down when the Mac is off, asleep, or offline.
- A FastAPI or Mac restart loses an active run. Closing a browser tab does not.
- Restarting `cloudflared` changes the public URL.
- The 27B model shares 32 GB unified memory with macOS and every other application.
- Model throughput depends on the selected MLX build, context, corpus, and current memory pressure. There is no guaranteed duration.

---

## Troubleshooting

**FastAPI reports that `APP_PASSWORD` is missing.** Create `.env` in the repository root, set a non-empty value, and restart FastAPI.

**LM Studio does not answer on port 1234.** Run `lms daemon up`, `lms server start --port 1234`, and `lms server status`.

**Preflight reports a missing model.** Compare `LLM_MODEL` with the exact identifier returned by `curl -s http://127.0.0.1:1234/api/v1/models`.

**Preflight reports missing vision support.** Download a vision-capable 4-bit MLX build of `Qwen3.8-27B` and update `LLM_MODEL`.

**Structured output contains reasoning or fails validation.** Disable thinking for the model. Repeat the structured-output smoke test before another real run.

**The run reaches the report stage and fails.** Run `python -m playwright install chromium` inside the active virtual environment.

**Memory Pressure turns yellow or red.** Close other memory-heavy applications. Record the run state and swap first. Lower `LLM_CONTEXT_LENGTH` only in a separate test. Keep the model at 4-bit. Lower `CLASSIFY_BATCH_SIZE` from 16 to 8 only if a real run on this Mac shows memory pressure.

**The public URL stopped working.** Read the current `cloudflared` log. A restarted quick tunnel has a new hostname.

**The backend works locally but not after login.** Inspect the LaunchAgent error log. Confirm every executable and working-directory path in the plist is absolute.
