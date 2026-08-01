# AGENTS.md

Repository conventions for agents and contributors. Everything here is about how to change the code without breaking it. Nothing here duplicates end-user documentation.

If you need:

- Product overview, quick install, what the outputs look like -> [README.md](README.md).
- Full install, config, keys, PDF engine, troubleshooting -> [docs/setup.md](docs/setup.md).
- How the pipeline, backend, and frontend fit together -> [docs/architecture.md](docs/architecture.md).
- HTTP contract, error codes, request/response shapes -> [docs/api-reference.md](docs/api-reference.md).

Read the doc that matches the change you are making. Only what is not in those docs belongs here.

---

## Environment and commands

- Windows Python interpreter: see [docs/setup.md](docs/setup.md#prerequisites) (`python`, never `py`).
- Never edit files through the shell (`sed`, `awk`, redirection, PowerShell `Set-Content`). Use the editor tools. Shell is for `git`, `pip`, `python`, `node`, and similar tooling only.
- Serving `app/` standalone: bind `127.0.0.1` on a fresh port. Stale ports cause `ERR_EMPTY_RESPONSE`.

---

## Coding conventions

### Language and style

- Python target: 3.10+. Use standard-library features first (`pathlib`, `sqlite3`, `threading`, `queue`, `dataclasses`). Add a dependency only when a few lines will not do.
- JavaScript: vanilla, no framework, no build step. One IIFE per file in `app/`. Match the existing style.
- CSS: design tokens live in `app/style.css`. Do not introduce new tokens without a reason. The visual language is documented in [docs/architecture.md](docs/architecture.md).

### Minimalism

Inherited from the workspace convention: the best code is the code never written. Before writing anything, stop at the first rung that holds:

1. Does this need to exist at all?
2. Does stdlib cover it?
3. Does a native platform feature cover it? (CSS over JS, DB constraint over app code.)
4. Does an already-installed dependency solve it?
5. Can it be one line?
6. Only then: the minimum code that works.

No unrequested abstractions. No interface with one implementation. No config for a value that never changes. No scaffolding "for later". Deletion beats addition.

Mark deliberate simplifications with a `# ponytail:` comment that names the ceiling and the upgrade path. Existing examples are in `server.py` (search for `ponytail:`).

### What never gets simplified away

- Input validation at trust boundaries. Server-side URL parsing (`_parse_youtube_url`), extension checks, size checks, article URL scheme checks - these stay on the server even if the frontend also validates.
- Error handling that prevents data loss. `finally` blocks that close DB connections in `adapter.py`, timeouts on external calls.
- Security. API keys stay server-side. `.env` and `config.py` are gitignored.
- Accessibility. Focus rings, keyboard-operable controls, `aria-live` announcements, `aria-pressed` states, reduced-motion support, contrast >= 4.5:1.

### Naming

- Python: `snake_case` for functions, `PascalCase` for classes, `SCREAMING_SNAKE_CASE` for module-level constants.
- API responses: `camelCase`, except SSE progress events (`snake_case`, see [docs/api-reference.md](docs/api-reference.md#get-runsidevents)).
- DB columns and internal Python dict keys derived from the DB: `snake_case`.
- The serializers in `server.py` handle the case flip; do not push `snake_case` out over HTTP.

### Errors

Server errors go through the helpers in `server.py`:

```python
_404("Session not found.")
_409("This session already has a campaign.")
_422("kind must be one of …", "kind")
_413("Files are limited to 10 MB.")
```

Every helper produces the standard `{"error", "message", "field"}` shape. Do not raise bare `HTTPException` for these cases; use the helpers so the shape stays consistent.

Pydantic validation errors are normalized to the same shape by the `RequestValidationError` handler. Do not remove that handler.

---

## Things that must not change without justification

These have specific reasons behind them. Read the relevant section of [docs/architecture.md](docs/architecture.md) before touching any of them.

- **Thread-local `googleapiclient` service** in `collect.py`. `httplib2` is not thread-safe.
- **Preflight checks** in `run.py`. See [docs/setup.md](docs/setup.md#running) for the checklist. Saves five model calls and a 500 MB download on misconfiguration.
- **Grounded-only brief** in `brief.py`. The brief is grounded against transcripts only - do not reintroduce a background or search-grounded brief. User images feed the brief as multimodal parts via `images_map`; no OCR path is used.
- **PDF requirement** in `run.py`. HTML is a build artifact.
- **`demoApi` method signatures** in `app/app.js`. Change bodies, keep signatures.
- **Disabled-feature honesty** in the frontend. A disabled control must be visibly disabled and explain why. Never silently do nothing.
- **Per-session overwrite** in `server.py`. Starting a new run deletes all prior runs and their files for that session. The backend enforces one active run per session; a new run overwrites the previous one.
- **`PipelineConfig` as the config contract**. Every pipeline module receives a `PipelineConfig` object passed as an argument. Do not reintroduce a global config module or a `sys.modules["config"]` shim. `llm` has no module-global client; `_get_client(cfg)` constructs one per API key.

---

## Verification

Full checklist: [docs/setup.md](docs/setup.md#verifying). Rules specific to making a change:

- For non-trivial logic, leave one runnable check behind - an assert-based self-check in the same file, or a small script under `tests/`. No frameworks unless one already exists there.
- If a bug is reported, reproduce it in an end-to-end setting before fixing, so you solve the real problem.
- Do not fabricate a passing test. If you did not run it, say so.

---

## Git and commits

- Never commit `config.py`, `.env`, or `data/`. All gitignored; keep them that way.
- Never commit real API keys. Keep `config-template.py` as the reference.
- Never auto-add agent names as co-authors on commits.
- Confirm before destructive git operations (`push --force`, `reset --hard`, `clean -f`, `branch -D`).

---

## When making changes

- Read the affected file before editing.
- Read the relevant `docs/` file if the change touches an area you have not worked in before.
- Match the existing style, conventions, and libraries. Do not introduce a new dependency or pattern for something the codebase already handles a different way.
- If a change spans multiple files, describe the plan first (in your response) before editing.
- If an approach fails twice, stop and diagnose. Do not keep patching. A third attempt with a small variation of the same idea is usually the wrong move.

---

## Frontend UI/UX work

Any user-visible change - new pages, redesigns, component styling, layout, content presentation, interaction feedback, loading, empty, error states - is scoped work that requires design intent, not just implementation. See [docs/architecture.md](docs/architecture.md) for the visual language and the mockup-vs-backend disconnects table.

Never re-enable a disabled control without a backing route landing first. Never fake an unbuilt backend feature.

---

## Skills

Load these when a task matches:

- `compact-technical-writing` - commit messages, README edits, architecture notes, any technical prose.
- `webapp-testing` - Playwright end-to-end checks of `app/`.
- `vercel-react-best-practices` - only if React or Next.js gets introduced. The current frontend is vanilla, so this is rarely relevant.
- `tdd` - only when explicitly asked for test-first work.

---

## Deferred features

Three feature areas are deferred with backing UI already in place as disabled controls. Do not build them piecemeal. Read the "Deferred features" section of [docs/architecture.md](docs/architecture.md) before starting any of them:

- OCR for scanned PDFs.
- Custom lenses for the brief stage.
- Cross-session assistant (chat + source discovery).
