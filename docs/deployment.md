# Deployment record - office workstation

This file is both the setup guide and the evidence record for the office
workstation that runs YouTube Intelligence. Run every command from the
repository root on the workstation, not on a laptop.

Never put a password, an API key, or an `Authorization` header in this file.
Record `set/non-empty`, never the value.

Target shape:

```text
External browser
  -> https://<random>.trycloudflare.com   (Cloudflare HTTPS)
  -> cloudflared, outbound from the workstation
  -> http://127.0.0.1:8000                (FastAPI, loopback only)
  -> HTTP Basic Auth middleware
  -> static frontend or /api route
  -> http://127.0.0.1:11434               (Ollama, loopback only)
```

`cloudflared` publishes only port 8000. Ollama is never published.

---

## Step 1 - Confirm the machine

```powershell
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv
python --version
git --version
Get-PSDrive C
```

Required:

- NVIDIA RTX 4060 Ti. Record the `memory.total` figure exactly. `nvidia-smi`
  reports dedicated VRAM only. Windows Task Manager adds a separate "Shared GPU
  memory" line, which is system RAM the GPU may borrow over PCIe. Shared memory
  is not usable VRAM for inference. A model that spills into it keeps running at
  roughly CPU speed. Size every decision below against the `nvidia-smi` number,
  never the Task Manager total.
- Python 3.10 or newer. On Windows call `python`, never `py`.
- Free disk for the repo, the virtual environment, Ollama, one model blob
  (roughly 3 GB), Playwright Chromium (roughly 450 MB), uploads,
  and reports. Measure and record the actual free space.

Set the machine never to sleep while plugged in:

```powershell
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
powercfg /change monitor-timeout-ac 15
```

Record the output and exit code of each command. Do not record a pass because
an application appears in a menu.

### Evidence

| Item | Value | Recorded |
|---|---|---|
| Date | | |
| Windows edition/build | | |
| CPU / RAM | | |
| GPU / dedicated VRAM from `nvidia-smi` | | |
| NVIDIA driver | | |
| Free disk on C: | | |
| Python version | | |
| Git version | | |
| Administrator rights | | |
| Sleep and hibernate disabled | | |

---

## Step 2 - Install Ollama and pull the model

Install Ollama from <https://ollama.com/download/windows>. The Windows
installer runs Ollama as a background service on `127.0.0.1:11434`. Accept
that default. Do not set `OLLAMA_HOST` to `0.0.0.0`, and do not open port
11434 in the firewall. `pipeline/llm.py` rejects a non-loopback
`OLLAMA_BASE_URL`, so a remote Ollama will not work even if you configure one.

Confirm the service answers:

```powershell
ollama --version
curl.exe http://127.0.0.1:11434/api/version
```

`ollama --version` printing `Warning: could not connect to a running Ollama
instance` means the client is installed but the service is not up. Start the
Ollama app from the Start menu, or run `ollama serve` in its own window, then
repeat the check.

Pull exactly this tag. The pipeline never pulls a model itself.

```powershell
ollama pull qwen3.5:4b
ollama list
```

One multimodal model serves both text and image User Inputs. `preflight` in
`pipeline/llm.py` checks this tag against `ollama list` before every run and
aborts when it is missing. The tag in `ollama list` must match `MODEL` exactly,
suffix for suffix; `qwen3.5:4b` and `qwen3.5:4b-q4_K_M` are different tags to
that check.

Keep one request in flight at a time. Set this once, as a user environment
variable, then restart the Ollama service:

```powershell
[Environment]::SetEnvironmentVariable("OLLAMA_NUM_PARALLEL", "1", "User")
```

### Size the context window to the card

Model weights are not the whole VRAM cost. The KV cache is sized by the context
window, and it is the larger variable. Budget for an 8 GB card:

| Item | Approximate VRAM |
|---|---|
| `qwen3.5:4b` weights | 2.6 GB |
| KV cache at `num_ctx` 32768 | 2.4 GB |
| Windows desktop on the same GPU | 0.5 GB |
| Total at the 32768 default | 5.5 GB |

The 32768 default fits an 8 GB card with headroom, so no override is needed.
Set `OLLAMA_NUM_CTX` in `.env` only if `ollama ps` shows a spill.

These figures are estimates. `ollama ps` on the real machine is the
measurement; the table is only for choosing a starting point.

Smoke test the model and confirm it is fully on the GPU:

```powershell
nvidia-smi --query-gpu=memory.used --format=csv
ollama run qwen3.5:4b "Reply with the single word: ready"
ollama ps
nvidia-smi --query-gpu=memory.used --format=csv
```

`ollama ps` prints a PROCESSOR column. `100% GPU` is the required result. Any
CPU percentage means the model has spilled and the run will be slow. Lower
`OLLAMA_NUM_CTX` and repeat.

Note that `ollama run` uses its own context setting, not the pipeline's. Repeat
this `ollama ps` check during the real run in Step 7, which is where the
pipeline's own `num_ctx` and its 25-comment classification batch apply.

### Evidence

| Item | Value | Recorded |
|---|---|---|
| Ollama version | | |
| `qwen3.5:4b` digest and size | | |
| `nvidia-smi` used VRAM before inference | | |
| `nvidia-smi` used VRAM after inference | | |
| `ollama ps` PROCESSOR column | | |
| `OLLAMA_NUM_CTX` value, or omitted | | |
| Port 11434 reachable only on loopback | | |

---

## Step 3 - Install the application

Clone the repository, then create and activate a virtual environment:

```powershell
git clone https://github.com/aubreyasta/YouTube-Comments-Intelligence.git
cd YouTube-Comments-Intelligence
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

If PowerShell refuses the activation script, allow signed local scripts for
your user once:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

Install both dependency sets. `requirements.txt` is the pipeline,
`requirements-server.txt` is the web backend. Both are needed.

```powershell
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install -r requirements-server.txt
python -m playwright install chromium
```

Playwright Chromium is the PDF engine. Without it the run reaches the report
stage and fails there, after collection and classification have already spent
GPU time.

---

## Step 4 - Configure secrets

The backend reads its configuration from the environment. `server.py` loads a
`.env` file next to itself at import time, so a `.env` file is the simplest
route. `.gitignore` already excludes it.

Create `.env` in the repository root with these lines:

```text
YOUTUBE_API_KEY=<your YouTube Data API v3 key>
APP_PASSWORD=<a long unique shared password>
OLLAMA_MODEL=qwen3.5:4b
```

Notes on each:

- `APP_PASSWORD` is the only access control. Anyone who has it reads every
  Session, every upload, and every report from every user. There are no
  accounts. Choose a long password and share it out of band.
- The server refuses to start when `APP_PASSWORD` is missing, empty, or only
  whitespace. That is deliberate: a server that starts without a password
  publishes the whole workspace to whoever finds the tunnel URL.
- `OLLAMA_MODEL` is the variable `adapter.py` reads. Its default is already
  `qwen3.5:4b`, so the line is optional; setting it makes the pinned tag
  explicit and survives a default changing later.
- `OLLAMA_NUM_CTX` defaults to 32768, which fits an 8 GB card with this model.
  Add it only if `ollama ps` shows a spill. See the sizing table in Step 2.
- `config.py` is only used by the `run.py` command-line pipeline. The web
  backend never imports it. You do not need to create or edit it for this
  deployment.

Confirm nothing secret is tracked:

```powershell
git status --short
git check-ignore -v .env config.py data/
```

### Evidence

| Item | Value | Recorded |
|---|---|---|
| `YOUTUBE_API_KEY` set, non-empty | | |
| `APP_PASSWORD` set, non-empty | | |
| `OLLAMA_MODEL` matches a tag in `ollama list` | | |
| `.env` ignored by Git | | |
| `config.py` and `data/` ignored by Git | | |

---

## Step 5 - Start the backend and check authentication locally

```powershell
python server.py
```

The server binds `127.0.0.1:8000` and serves both the frontend and the API
from that one origin. Leave the window open; it is the running service.

A `RuntimeError: APP_PASSWORD must be set before the server can start.` on
startup means the `.env` file is missing, in the wrong directory, or has an
empty value. That is the fail-closed behavior working.

In a second PowerShell window, check the gate. Do not use a browser for these
first checks; a browser caches credentials and hides the failure.

```powershell
# No credentials: all three must return 401 with a Basic challenge.
curl.exe -i http://127.0.0.1:8000/
curl.exe -i http://127.0.0.1:8000/app.js
curl.exe -i http://127.0.0.1:8000/api/sessions

# Wrong password: must return 401.
curl.exe -i -u office:wrong-password http://127.0.0.1:8000/api/sessions

# Correct password: must return 200 and the real UI.
curl.exe -i -u office:<the password> http://127.0.0.1:8000/
curl.exe -i -u office:<the password> http://127.0.0.1:8000/api/sessions
```

Every 401 must carry
`WWW-Authenticate: Basic realm="YouTube Intelligence", charset="UTF-8"`.
That header is what makes the browser show its native credential prompt.

The username is not an identity. Any non-empty username is accepted; only the
password is checked. `office` is the example username used throughout.

Confirm the bind is loopback-only, not the whole network:

```powershell
netstat -ano | findstr ":8000"
```

The local address must read `127.0.0.1:8000`, never `0.0.0.0:8000`.

Then open <http://127.0.0.1:8000> in a browser on the workstation, enter the
credentials, create a Session, add one YouTube URL, and confirm you reach the
setup screen. Stop there for now.

### Evidence

| Check | Result | Recorded |
|---|---|---|
| Startup fails without `APP_PASSWORD` | | |
| Unauthenticated `/` returns 401 + challenge | | |
| Unauthenticated static asset returns 401 | | |
| Unauthenticated API returns 401 | | |
| Wrong password returns 401 | | |
| Correct password serves the UI | | |
| Bind is `127.0.0.1:8000` | | |
| Local Session reaches setup | | |

---

## Step 6 - Publish through a Cloudflare quick tunnel

Install `cloudflared` from
<https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/>,
or with winget:

```powershell
winget install --id Cloudflare.cloudflared
cloudflared --version
```

Start the tunnel in its own window, with the backend already running:

```powershell
cloudflared tunnel --url http://127.0.0.1:8000
```

The output prints a generated hostname of the form
`https://<random-words>.trycloudflare.com`. That URL is the public entrance.

Two properties of a free quick tunnel matter operationally:

- The hostname is regenerated every time `cloudflared` restarts. There is no
  stable URL. Send the current one to users out of band each time it changes.
- A quick tunnel cannot carry a Cloudflare Access policy. That is exactly why
  the password lives in the application instead.

Create no named tunnel, no DNS record, no Access application, no router port
forward, and no LAN bind. `cloudflared` connects outbound; nothing inbound is
opened on the office network.

### External checks

From a phone on mobile data, or any machine on another network:

1. Open the generated HTTPS URL.
2. Confirm the browser shows a credential prompt before any content.
3. Enter a wrong password. Confirm it fails.
4. Enter the correct password. Confirm the UI loads with no mixed-content
   warning.
5. Create a Session and confirm static assets, API calls, and live progress
   updates all work over that one origin.
6. Confirm neither port 8000 nor port 11434 is reachable directly from
   outside.

Security probe. Add an article User Input with the URL
`http://127.0.0.1:11434/` and confirm the response is HTTP 422 with:

```json
{"error":"VALIDATION_ERROR","message":"That link points to a private address and cannot be fetched.","field":"url"}
```

No asset may be created. This proves the article fetcher cannot be used to
read internal office services through the public tunnel.

Stop and fix before going further if any path is reachable without
credentials, TLS is invalid, Ollama is exposed, or the tunnel points anywhere
other than `127.0.0.1:8000`.

### Evidence

| Check | Result | Recorded |
|---|---|---|
| `cloudflared` version | | |
| Generated hostname | | |
| External credential prompt appears | | |
| Wrong password rejected externally | | |
| Correct password loads UI externally | | |
| No mixed-content warning | | |
| Ports 8000 and 11434 not directly reachable | | |
| `http://127.0.0.1:11434/` article returns 422, no asset | | |

---

## Step 7 - Run one real end-to-end analysis

Run this from the external client, through the public URL, not from the
workstation. The point is to prove the real user path.

1. Create a Session.
2. Add a valid YouTube video URL and at least one grounded User Input.
3. Review or generate the Key Messages.
4. Start the analysis with the skip-pause checkbox **unchecked**.
5. At the review pause, edit one Key Message, save it, and continue.
6. Close the browser tab while processing runs. Reopen the URL, authenticate
   again, and confirm the run restores.
7. Wait for the run to complete.
8. Download all six artifacts: `report.pdf`, `comments.csv`,
   `key-messages.csv`, `themes.csv`, `sentiment.csv`, `emotions.csv`.
9. Confirm `report.json` is never offered as a download.

Inspect `comments.csv`. The header must be exactly:

```text
video_id,group,comment,likes,language,theme,sentiment,emotion
```

followed by one `key_message_<id>` column per Key Message. There must be no
`sentiment_confidence` or `emotion_confidence` column. Every `sentiment` value
must be one of `positive`, `negative`, `neutral`. Every `emotion` value must
be one of `joy`, `anger`, `sadness`, `fear`, `other_neutral`.

Open `report.pdf`. The Theme, Key Message, Sentiment, and Emotion sections
must render with no missing-field errors.

While classification is running, check the split from a PowerShell window on
the workstation:

```powershell
ollama ps
```

This is the check that matters. It uses the pipeline's own context setting and
its 25-comment batch, unlike the Step 2 smoke test. The PROCESSOR column must
read `100% GPU`. Any CPU percentage means the model has spilled into shared
memory and the run is proceeding at roughly CPU speed. Let the run finish and
record the number. Lower `OLLAMA_NUM_CTX` before the next run.

Record the total comment count and the wall-clock duration. If the Session has
roughly 3,000 comments, note how the duration compares to two hours. This is a
sanity note only. There is no timing gate, and a slow run does not block
deployment.

Then run a second, small Session with the skip-pause checkbox **checked** and
at least one Key Message included. Confirm it moves from reconciliation
straight into classification with no review pause.

Change nothing during this step. Do not adjust the model, batch size, timeout,
or schema to make a run succeed. Record the failure instead.

### Evidence

| Check | Result | Recorded |
|---|---|---|
| Session completed end to end | | |
| Review pause edit and continue | | |
| Tab close and restore | | |
| Six artifacts downloaded | | |
| `report.json` not offered | | |
| `comments.csv` header exact | | |
| Affect labels inside locked sets | | |
| PDF opens and renders every section | | |
| `ollama ps` PROCESSOR during classification | | |
| Total comments / duration | | |
| Skip-pause Session reaches classify | | |

---

## Step 8 - Restart behavior

Ollama installs as a Windows service and starts on boot by itself. The backend
does not; give it a startup trigger.

The least privileged mechanism is a Task Scheduler task that runs at sign-in
under your own account:

```powershell
$action  = New-ScheduledTaskAction -Execute "<repo>\.venv\Scripts\python.exe" `
                                   -Argument "server.py" `
                                   -WorkingDirectory "<repo>"
$trigger = New-ScheduledTaskTrigger -AtLogOn
Register-ScheduledTask -TaskName "YouTubeIntelligence" `
                       -Action $action -Trigger $trigger
```

Because the quick-tunnel hostname changes on every restart, pick one of these
and record which:

- Start `cloudflared` at sign-in and read the current URL from its window or
  log, or
- Start `cloudflared` by hand whenever external access is needed.

Do not write code to scrape the URL from a log, email it, or publish it
anywhere. A stable hostname needs a named tunnel and a domain, which is a
separate future decision.

Reboot the workstation. Without starting anything by hand, confirm the local
UI answers with the Basic Auth prompt and one API call succeeds. Then start or
verify `cloudflared`, take the current URL, and repeat one external
authenticated check.

### Evidence

| Item | Value | Recorded |
|---|---|---|
| Backend startup mechanism and task name | | |
| Ollama startup mechanism | | |
| `cloudflared` startup behavior chosen | | |
| Log locations | | |
| Stop and restart commands | | |
| Local checks pass after reboot | | |
| External check passes after reboot | | |

---

## Accepted operational limits

These are consequences of decisions already taken, not defects.

- One shared password, one shared workspace. Every authenticated person reads
  and downloads every Session, upload, and report.
- No per-user audit trail. The server cannot say who did what.
- One analysis at a time across the whole system. A second start request is
  rejected, not queued.
- No backups. The workstation disk holds the only copy of `data/`.
- The service is down whenever the workstation is off, asleep, or offline.
- A backend restart loses an in-flight run. Closing a browser tab does not.
- The public URL changes every time `cloudflared` restarts.

---

## Troubleshooting

**`RuntimeError: APP_PASSWORD must be set before the server can start.`**
`.env` is missing, is not in the repository root, or the value is empty. This
is intended behavior, not a bug.

**Everything returns 401 even with the right password.** Check for stray
whitespace or quotes around the value in `.env`. The server strips surrounding
whitespace from the configured password but compares the submitted one exactly.

**`ollama --version` warns it cannot connect.** The client is installed but
the service is not running. Launch the Ollama app, or run `ollama serve`.

**A run fails at the report stage.** Playwright Chromium is missing. Run
`python -m playwright install chromium`.

**A run fails with a model-not-found error.** The tag in `.env` does not match
`ollama list`. The pipeline never pulls a model for you.

**Out-of-memory or CUDA errors during classification.** Something else is
holding VRAM. Check `nvidia-smi`, and confirm `OLLAMA_NUM_PARALLEL` is `1`.

**The run works but is far slower than expected.** The model has spilled into
shared system memory. Run `ollama ps` during classification. If PROCESSOR shows
any CPU percentage, lower `OLLAMA_NUM_CTX` in `.env` and restart the
backend. Close other GPU consumers first, including a browser with hardware
acceleration on. See the sizing table in Step 2.

**The public URL stopped working.** `cloudflared` restarted and generated a
new hostname. Read the current one from its window and share it again.

**The frontend loads but calls fail with `ERR_EMPTY_RESPONSE`.** The backend
window closed. Restart `python server.py`.
