# Setup

Install the application, configure the local model boundary, and start the server.

Related: [Deployment](deployment.md), [Architecture](architecture.md), [API reference](api-reference.md), [README](../README.md).

---

## Prerequisites

The shared deployment target is a MacBook Pro M1 Max with 32 GB of unified memory.

Install:

- macOS 14 or newer.
- Python 3.10 or newer.
- Git.
- LM Studio with the `lms` CLI.
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

Load the model with a 32,768-token context. Disable thinking in the model settings because the pipeline requires direct schema-only responses. Keep the server on its default loopback bind.

Verify the bind:

```bash
lsof -nP -iTCP:1234 -sTCP:LISTEN
```

The listener must be `127.0.0.1:1234` or `localhost:1234`. Do not enable **Serve on Local Network**.

See [Deployment](deployment.md) for model smoke tests and real-device acceptance.

---

## Configure the backend

Create `.env` in the repository root:

```text
YOUTUBE_API_KEY=<YouTube Data API v3 key>
APP_PASSWORD=<long unique shared password>
LLM_BASE_URL=http://127.0.0.1:1234
LLM_MODEL=<exact model key returned by LM Studio>
LLM_CONTEXT_LENGTH=32768
LLM_TIMEOUT_SECONDS=600
CLASSIFY_BATCH_SIZE=8
```

- `YOUTUBE_API_KEY` stays on the Mac. The browser never receives it.
- `APP_PASSWORD` protects the frontend, API, downloads, and SSE stream. The server refuses to start when it is empty.
- `LLM_BASE_URL` must be a loopback HTTP origin without a path, credentials, query, or fragment.
- `LLM_MODEL` must exactly match the LM Studio model inventory.
- `LLM_CONTEXT_LENGTH` and `CLASSIFY_BATCH_SIZE` are starting values. Change them only after a real-device run shows memory pressure or unacceptable throughput.
- `.env`, `config.py`, and `data/` are gitignored. Never commit them.
- The application has no LM Studio API-token setting. Keep LM Studio on loopback. Do not publish port 1234 or replace `LLM_BASE_URL` with a remote URL.

Check the exclusions:

```bash
git check-ignore -v .env config.py data/
```

The Mac deployment starts with an empty `data/` directory. Do not copy the Windows workstation database, uploads, runs, or artifacts.

---

## Start the application

Activate the environment and start FastAPI:

```bash
source .venv/bin/activate
python server.py
```

The backend binds `127.0.0.1:8000`. It serves the frontend and API from one origin.

Check the authentication boundary from a second terminal:

```bash
curl -i http://127.0.0.1:8000/
curl -i -u office:wrong-password http://127.0.0.1:8000/api/sessions
curl -i -u office:<the-password> http://127.0.0.1:8000/api/sessions
lsof -nP -iTCP:8000 -sTCP:LISTEN
```

Require these results:

- Missing or wrong credentials return `401`.
- Every `401` includes `WWW-Authenticate: Basic realm="YouTube Intelligence", charset="UTF-8"`.
- Correct credentials return `200`.
- FastAPI listens only on `127.0.0.1:8000`.

Open <http://127.0.0.1:8000>, authenticate, and create a Session.

---

## Publish the application

Start a free Cloudflare quick tunnel:

```bash
cloudflared tunnel --url http://127.0.0.1:8000
```

Share the generated `https://<random>.trycloudflare.com` URL and the password through separate trusted channels. The URL changes whenever `cloudflared` restarts.

Publish only port 8000. Never publish LM Studio on port 1234.

See [Deployment](deployment.md) for external security checks, automatic login startup, and the full acceptance run.

---

## Developing against a remote LM Studio

`LLM_BASE_URL` must stay a loopback URL. `pipeline/llm.py`'s `_validated_base_url()` rejects any other host, and the application has no LM Studio API-token setting. This holds even when the person developing works on a different machine than the one running LM Studio.

Run a local relay instead of changing that boundary. The relay is a small process on the developer's own machine:

- It listens on `http://127.0.0.1:<port>`.
- It forwards every request to the remote LM Studio endpoint, using whatever network path and credentials that endpoint requires (a private mesh network and an API token, for example).
- It returns the response unchanged.

Set `LLM_BASE_URL=http://127.0.0.1:<port>` to the relay's own port, not LM Studio's. `pipeline/llm.py` then sees a plain loopback URL and needs no other change.

The relay is a personal development tool, not part of the application. Keep its script and any token it holds out of the repository. Never commit them.

With the relay running, follow the rest of this file as written: create `.env`, start `python server.py`, and use the application at `http://127.0.0.1:8000` like any other local run. Every model call now reaches the remote LM Studio; everything else stays local.

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

Run the focused assert-based scripts directly:

```bash
python tests/test_llm.py
python tests/test_classify.py
python tests/test_evidence.py
python tests/e2e_product_flow.py
node --check app/app.js
node --check app/live.js
```

Open `app/self-check.html` for the frontend state-machine checks.

After a provider or model change, complete the real-device acceptance procedure in [Deployment](deployment.md). Offline tests cannot prove MLX memory fit, vision support, structured-output behavior, or throughput on the M1 Max.

---

## Common errors

| Symptom | Cause | Fix |
|---|---|---|
| `RuntimeError: APP_PASSWORD must be set before the server can start.` | `.env` is missing or `APP_PASSWORD` is empty | Create `.env` in the repository root and restart FastAPI. |
| LM Studio connection failure | The daemon or API server is stopped | Run `lms daemon up`, then `lms server start --port 1234`. |
| Model not found | `LLM_MODEL` does not exactly match LM Studio's model key | Read `curl -s http://127.0.0.1:1234/api/v1/models` and copy the exact key. |
| Vision preflight failure | The downloaded build does not expose vision support | Download a vision-capable `Qwen3.8-27B` 4-bit MLX build. |
| Structured response fails validation | Thinking is enabled or the local runtime did not enforce the schema | Disable thinking, confirm the selected model, and run the structured-output smoke test. |
| Run fails at the report stage | Playwright Chromium is missing | Run `python -m playwright install chromium`. |
| `409` when starting a run | Another Session has a queued or running run | Wait for the active run to finish. |
| Public URL stopped working | `cloudflared` restarted | Read and share the new quick-tunnel URL. |
| Run is slower than expected | The model or KV cache is using too much unified memory | Close memory-heavy applications, inspect Activity Monitor and `lms ps`, then lower `LLM_CONTEXT_LENGTH` only if the real run requires it. |
