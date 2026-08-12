# AGENTS.md

Repository conventions for agents and contributors. This file is about how to change the code without breaking it. It does not duplicate end-user documentation.

If you need:

- What the product is, the user flow, the output files: [README.md](README.md).
- What is planned and in what order: [CHANGELOG.md](CHANGELOG.md).
- Install, config, keys, GPU, troubleshooting: [docs/setup.md](docs/setup.md).
- How pipeline, backend, and frontend fit together: [docs/architecture.md](docs/architecture.md).
- HTTP contract, error codes, request and response shapes: [docs/api-reference.md](docs/api-reference.md).

Read the doc that matches the change you are making.

---

## Terminology

Product terms are defined once, in [README.md](README.md). Use those words in prose, comments, commit messages, and docs.

Code identifiers still carry older names. Do not rename them opportunistically; a rename touches the DB, the API, and the frontend at once, and is scheduled in [CHANGELOG.md](CHANGELOG.md). The mapping:

| Product term | Code identifier |
|---|---|
| Session | `sessions`, plus a single `campaigns` row per session |
| User Inputs | `assets`, `CAMPAIGN_CONTEXT` |
| Key Messages | `brief_points`, `points`, `pt__` columns |
| Travel | `transfer`, `transfers`, `echoed` |
| Theme book | the discovered theme set, `codebook.json` in debug output |
| Themes | `theme` column |
| Sentiment | `sentiment`, `sentiment_confidence` |
| Emotions | `emotion`, `emotion_confidence` |

Never in prose: "signal transfer", "codebook", "echoed", "affect", "campaign" as a user-facing unit.

---

## Environment and commands

- Python 3.10+. On Windows use `python`, never `py`. See [docs/setup.md](docs/setup.md#prerequisites).
- Never edit files through the shell (`sed`, `awk`, redirection, PowerShell `Set-Content`). Use the editor tools. Shell is for `git`, `pip`, `python`, `node`, and similar tooling.
- Serving `app/` standalone: bind `127.0.0.1` on a fresh port. Stale ports cause `ERR_EMPTY_RESPONSE`.

---

## Coding conventions

### Language and style

- Python: standard library first (`pathlib`, `sqlite3`, `threading`, `queue`, `dataclasses`). Add a dependency only when a few lines will not do.
- JavaScript: vanilla, no framework, no build step. One IIFE per file in `app/`.
- CSS: design tokens live in `app/style.css`. Do not add tokens without a reason.

### Minimalism

The best code is the code never written. Before writing anything, stop at the first rung that holds:

1. Does this need to exist at all?
2. Does the standard library cover it?
3. Does a native platform feature cover it? (CSS over JS, a DB constraint over app code.)
4. Does an already-installed dependency solve it?
5. Can it be one line?
6. Only then: the minimum code that works.

No unrequested abstractions. No interface with one implementation. No config for a value that never changes. No scaffolding for later. Deletion beats addition.

Mark deliberate simplifications with a `# ponytail:` comment naming the ceiling and the upgrade path. Examples are in `server.py`.

### What never gets simplified away

- Input validation at trust boundaries. Server-side URL parsing (`_parse_youtube_url`), extension checks, size checks, article URL scheme checks. These stay on the server even when the frontend also validates.
- Error handling that prevents data loss. `finally` blocks closing DB connections in `adapter.py`, timeouts on external calls.
- Security. Keys stay server-side. `.env` and `config.py` are gitignored.
- Accessibility. Focus rings, keyboard-operable controls, `aria-live` announcements, `aria-pressed` states, reduced-motion support, contrast at 4.5:1 or better.

### Naming

- Python: `snake_case` functions, `PascalCase` classes, `SCREAMING_SNAKE_CASE` module constants.
- API responses: `camelCase`, except SSE progress events, which are `snake_case` because they pass `adapter.py`'s progress dict through unserialized.
- DB columns and Python dict keys derived from them: `snake_case`.
- The serializers in `server.py` handle the case flip. Do not push `snake_case` out over HTTP.

### Errors

Server errors go through the helpers in `server.py`:

```python
_404("Session not found.")
_409("This session already has a run in progress.")
_422("kind must be one of …", "kind")
_413("Files are limited to 10 MB.")
```

Every helper produces the standard `{"error", "message", "field"}` shape. Do not raise bare `HTTPException` for these cases. Pydantic validation errors are normalized to the same shape by the `RequestValidationError` handler; do not remove that handler.

---

## Things that must not change without justification

Read the relevant section of [docs/architecture.md](docs/architecture.md) before touching any of these.

- **Thread-local `googleapiclient` service** in `collect.py`. `httplib2` is not thread-safe.
- **Preflight checks** in `run.py`. Saves a wasted run on a misconfigured machine.
- **Grounded-only Key Messages** in `brief.py`. Every claim traces to user-provided material: transcripts, titles, descriptions, uploaded documents, and uploaded images as multimodal parts. Do not reintroduce a background or search-grounded brief.
- **Counting happens in Python.** Percentages come from counting per-comment labels. The model never emits a statistic directly. This is not negotiable.
- **PDF requirement** in `run.py`. HTML is a build artifact.
- **`demoApi` method signatures** in `app/app.js`. Change bodies, keep signatures.
- **Disabled-feature honesty** in the frontend. A control that cannot do what it looks like it does must be visibly disabled and explain why. Never silently do nothing.
- **Per-session overwrite** in `server.py`. A new run deletes prior runs and their files for that session.
- **`PipelineConfig` as the config contract.** Every pipeline module receives a `PipelineConfig` argument. No global config module, no `sys.modules["config"]` shim. `llm` has no module-global client.

---

## Verification

- For non-trivial logic, leave one runnable check behind: an assert-based self-check in the same file, or a script under `tests/` alongside `test_classify.py` and `test_evidence.py`. No frameworks unless one already exists there.
- If a bug is reported, reproduce it end to end before fixing.
- Do not fabricate a passing test. If you did not run it, say so.

Frontend: `node --check app/app.js`, then `app/self-check.html` in a browser. Full checklist in [docs/setup.md](docs/setup.md#verifying).

---

## Git and commits

- Never commit `config.py`, `.env`, or `data/`. All gitignored; keep them that way.
- Never commit real API keys. `config-template.py` is the reference.
- Never add agent names as commit co-authors.
- Confirm before destructive git operations (`push --force`, `reset --hard`, `clean -f`, `branch -D`).

---

## When making changes

- Read the affected file before editing.
- Read the relevant `docs/` file if the change touches an area you have not worked in.
- Match the existing style and libraries. Do not introduce a new dependency or pattern for something the codebase already handles another way.
- If a change spans multiple files, describe the plan before editing.
- If an approach fails twice, stop and diagnose. A third attempt with a small variation of the same idea is usually the wrong move.

---

## Frontend UI and UX work

Any user-visible change (new screens, restyling, layout, content presentation, interaction feedback, loading, empty, and error states) is scoped work that needs design intent, not just implementation. The visual language is in [docs/architecture.md](docs/architecture.md).

Never re-enable a disabled control without a backing route landing first. Never fake an unbuilt backend feature.

---

## Skills

- `compact-technical-writing` for commit messages, README edits, architecture notes, any technical prose.
- `webapp-testing` for Playwright end-to-end checks of `app/`.
- `tdd` only when explicitly asked for test-first work.