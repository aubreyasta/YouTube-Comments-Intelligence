# Setup

Install the application, configure the local model boundary, and start the server.

Related: [Deployment](deployment.md), [Architecture](architecture.md), [API reference](api-reference.md), [README](../README.md).

---

## Prerequisites

The server runs wherever Python 3.10 or newer runs. LM Studio runs on the same machine or on another machine the server can reach (see [Using a remote LM Studio](#using-a-remote-lm-studio)). The shell commands below target macOS and Linux. On Windows, use `python` and `.venv\Scripts\activate`.

Install:

- Python 3.10 or newer.
- Git.
- LM Studio with the `lms` CLI, on the machine that serves the model.
- `cloudflared` for public access.

Check the local tools:

```bash
python3 --version
git --version
lms --version
cloudflared --version
```

Use an isolated Python environment. The environment name does not affect the application.

---

## Install the application

Clone the repository, create the environment, and install both dependency sets:

```bash
git clone https://github.com/aubreyasta/YouTube-Comments-Intelligence.git
cd YouTube-Comments-Intelligence
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt -r requirements-server.txt
python -m playwright install chromium
```

Playwright Chromium renders `report.pdf`. Install it before the first run. Otherwise, a run can reach the report stage and fail after model work has completed.

---

## Prepare LM Studio

Install LM Studio from <https://lmstudio.ai/download>. Open it once to initialize the `lms` CLI.

Download a 4-bit MLX build of `Qwen3.8-27B`. Select a build that LM Studio reports as vision-capable. Do not use the repository display name as the API model identifier.

Start the local service and inspect the downloaded models:

```bash
lms daemon up
lms server start --port 1234
lms ls
curl -s http://127.0.0.1:1234/api/v1/models
```

Record the exact model key returned by LM Studio. Set that key as `LLM_MODEL`.

Load the model with a 32,768-token context. Disable thinking in the model settings because the pipeline requires direct schema-only responses. When LM Studio runs on the server's machine, keep the server on its default loopback bind.

Verify the bind:

```bash
lsof -nP -iTCP:1234 -sTCP:LISTEN
```

For a same-machine setup, the listener must be `127.0.0.1:1234` or `localhost:1234`. Enable **Serve on Local Network** only for a remote setup, and only with an API token (see [Using a remote LM Studio](#using-a-remote-lm-studio)).

See [Deployment](deployment.md) for model smoke tests and real-device acceptance.

---

## Create the Google sign-in client

Users sign in with their Google Workspace account. Create one OAuth client per deployment URL:

1. In Google Cloud Console, open **APIs & Services > OAuth consent screen**. Choose **Internal** so only accounts in your Workspace organization can use it.
2. Open **APIs & Services > Credentials > Create credentials > OAuth client ID**. Choose **Web application**.
3. Under **Authorized redirect URIs**, add `<APP_BASE_URL>/auth/callback`. For local development, add `http://localhost:8000/auth/callback`.
4. Copy the client ID and client secret into `.env` (next section).

The redirect URI must match `APP_BASE_URL` exactly, including scheme and port. A URL that changes on restart, such as a Cloudflare quick tunnel, breaks sign-in.

---

## Configure the backend

Create `.env` in the repository root:

```text
YOUTUBE_API_KEY=<YouTube Data API v3 key>
GOOGLE_CLIENT_ID=<OAuth client ID>
GOOGLE_CLIENT_SECRET=<OAuth client secret>
APP_BASE_URL=http://localhost:8000
AUTH_ALLOWED_DOMAINS=<company.com>
ADMIN_EMAILS=<admin@company.com>
LLM_BASE_URL=http://127.0.0.1:1234
LLM_MODEL=<exact model key returned by LM Studio>
LLM_CONTEXT_LENGTH=32768
LLM_TIMEOUT_SECONDS=600
CLASSIFY_BATCH_SIZE=16
```

- `YOUTUBE_API_KEY` stays on the server. The browser never receives it.
- `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `APP_BASE_URL`, and `AUTH_ALLOWED_DOMAINS` are required. The server refuses to start when one is empty. Sign-in protects the frontend, API, downloads, and SSE stream.
- `APP_BASE_URL` is the URL users open, with no trailing path. Use `https` for any deployment reached over the internet; the session cookie is `Secure` only then.
- `AUTH_ALLOWED_DOMAINS` is a comma-separated list of Google Workspace domains. Personal Google accounts cannot sign in.
- `ADMIN_EMAILS` (optional) is a comma-separated list of admin emails. Admins see the Users screen and can block or erase users. Restart FastAPI after a change.
- `LLM_BASE_URL` is an `http` or `https` URL with any host and an optional path prefix. It must not contain credentials, a query, or a fragment.
- `LLM_HEADERS` (optional) is a JSON object of headers sent on every model request, for example `{"Authorization": "Bearer <LM Studio API token>"}`. Values must be single-line strings. Error messages never include them.
- `LLM_ALLOW_INSECURE=true` (optional) skips TLS certificate verification for an `https` `LLM_BASE_URL`. Use it only for a self-signed endpoint on a network you control.
- `LLM_MODEL` must exactly match the LM Studio model inventory.
- `CLASSIFY_BATCH_SIZE=16` is validated for `qwen/qwen3.8-27b`: a 574-comment run labelled every comment with no validation failure in 32 minutes, against 2h27m at batch 4. The code default is `8`, so set `16` explicitly.
- `LLM_CONTEXT_LENGTH` is a starting value. Change it only after a real run shows memory pressure.
- `.env`, `config.py`, and `data/` are gitignored. Never commit them.
- Never publish LM Studio to the internet. A remote LM Studio belongs on a private network and requires an API token.

Check the exclusions:

```bash
git check-ignore -v .env config.py data/
```

A new deployment starts with an empty `data/` directory. Do not copy another machine's database, uploads, runs, or artifacts.

---

## Start the application

Activate the environment and start FastAPI:

```bash
source .venv/bin/activate
python server.py
```

The backend binds `127.0.0.1:8000`. It serves the frontend and API from one origin.

Check the sign-in boundary from a second terminal:

```bash
curl -i http://127.0.0.1:8000/
curl -i http://127.0.0.1:8000/api/sessions
lsof -nP -iTCP:8000 -sTCP:LISTEN
```

Require these results:

- `/` returns `302` to `/auth/login`.
- `/api/sessions` returns `401` with `"error": "UNAUTHENTICATED"`.
- FastAPI listens only on `127.0.0.1:8000`.

Open the `APP_BASE_URL` (for local development, <http://localhost:8000>, not `127.0.0.1`, so the redirect URI matches). Sign in with a Workspace account and create a Session.

---

## Publish the application

Publish port 8000 at a stable `https` hostname, such as a Cloudflare named tunnel or a reverse proxy with a domain. Set `APP_BASE_URL` to that hostname and add `<APP_BASE_URL>/auth/callback` to the OAuth client. A quick tunnel's random `trycloudflare.com` hostname does not work, because it changes on every restart.

Publish only port 8000. Never publish LM Studio on port 1234.

See [Deployment](deployment.md) for external security checks, automatic login startup, and the full acceptance run.

---

## Using a remote LM Studio

The server can call LM Studio on another machine, for example over a private mesh network such as Tailscale, or from a container that has no host network.

On the LM Studio machine:

1. Enable **Serve on Local Network**.
2. Enable API token authentication and create a token.
3. Keep port 1234 off the public internet.

In the server's `.env`:

```text
LLM_BASE_URL=http://<lm-studio-host>:1234
LLM_HEADERS={"Authorization": "Bearer <LM Studio API token>"}
```

A reverse proxy in front of LM Studio also works. Include its path prefix in `LLM_BASE_URL` and its credentials in `LLM_HEADERS`.

Then follow the rest of this file as written. Every model call reaches the remote LM Studio; everything else stays local. A wrong token fails preflight with `LM Studio request failed (HTTP 401).`

A code change needs only a restart of `python server.py` to test. No redeploy is required. The remote machine's only requirement is that LM Studio and the configured model stay loaded and running.

---

## CLI debug entry point

`python run.py` runs the pipeline without the web product. Copy `config-template.py` to the gitignored `config.py`, add local inputs, and run:

```bash
python run.py
```

Use this entry point only for pipeline debugging. The deployed product uses `server.py` and `.env`.

`KEEP_INTERMEDIATE = True` writes audit files under `output/<session>/debug/`. Check `codebook.json` when a Theme looks wrong and `classified.csv` when a per-comment label looks wrong.

---

## Verify a change

Run every assert-based script directly. Each prints `PASS (n/n)` and exits `0`:

```bash
for f in tests/*.py; do python "$f" || echo "FAILED: $f"; done
node --check app/app.js
node --check app/live.js
```

The accepted baseline (2026-09-17) is 22 scripts, including `tests/e2e_product_flow.py` 23/23.

Open `app/self-check.html` for the frontend state-machine checks.

After a provider or model change, run one real Session through the web app against the target model, as in [Deployment](deployment.md#step-7---run-one-real-external-session). Offline tests cannot prove memory fit, vision support, structured-output behavior, or throughput on the model host.

---

## Common errors

| Symptom | Cause | Fix |
|---|---|---|
| `RuntimeError: GOOGLE_CLIENT_ID must be set before the server can start.` (or another sign-in variable) | `.env` is missing or the variable is empty | Set every variable in [Configure the backend](#configure-the-backend) and restart FastAPI. `APP_PASSWORD` is no longer used. |
| Pages load, but every save or create fails with `Request from another site refused.` (`403 CROSS_ORIGIN`) | The browser opened the app at a different address than `APP_BASE_URL`, such as `127.0.0.1` instead of `localhost` | Open the app at `APP_BASE_URL` exactly. Behind a proxy or tunnel, set `APP_BASE_URL` to the public address. |
| Google shows `Error 400: redirect_uri_mismatch` | The OAuth client does not list `<APP_BASE_URL>/auth/callback`, or the browser opened a different host | Add the exact redirect URI to the OAuth client, and open the app at `APP_BASE_URL`. |
| "This Google account can't sign in" | The account is personal, outside `AUTH_ALLOWED_DOMAINS`, or blocked | Sign in with a Workspace account in an allowed domain. An admin can unblock the account on the Users screen. |
| Users screen is missing | The signed-in email is not in `ADMIN_EMAILS` | Add the email to `ADMIN_EMAILS` and restart FastAPI. |
| LM Studio connection failure | The daemon or API server is stopped | Run `lms daemon up`, then `lms server start --port 1234`. |
| `LM Studio request failed (HTTP 401).` | LM Studio requires a token and `LLM_HEADERS` is missing or wrong | Set `LLM_HEADERS` to the current token. See [Using a remote LM Studio](#using-a-remote-lm-studio). |
| `LLM_HEADERS must be a JSON object of string values` | `LLM_HEADERS` is not valid JSON | Quote the header name and value with double quotes, as in the example. |
| LM Studio connection failure over `https` with a certificate error | The endpoint uses a self-signed certificate | Install a trusted certificate, or set `LLM_ALLOW_INSECURE=true` on a network you control. |
| Model not found | `LLM_MODEL` does not exactly match LM Studio's model key | Read `curl -s http://127.0.0.1:1234/api/v1/models` and copy the exact key. |
| Vision preflight failure | The downloaded build does not expose vision support | Download a vision-capable `Qwen3.8-27B` 4-bit MLX build. |
| Structured response fails validation | Thinking is enabled or the local runtime did not enforce the schema | Disable thinking, confirm the selected model, and run the structured-output smoke test. |
| Run fails at the report stage | Playwright Chromium is missing | Run `python -m playwright install chromium`. |
| `409` when starting a run | This Session already has a running run | Wait for it to finish. Runs from other Sessions queue instead of failing. |
| Public URL stopped working | `cloudflared` restarted | Read and share the new quick-tunnel URL. |
| Run is slower than expected | The model or KV cache is using too much unified memory | Close memory-heavy applications, inspect Activity Monitor and `lms ps`, then lower `LLM_CONTEXT_LENGTH` only if the real run requires it. |
