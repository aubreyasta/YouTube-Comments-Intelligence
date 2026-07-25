# AGENTS.md

YouTube Comment Intelligence: put video links in, get a reception report out. Splits "what the video said" (from transcript/title/desc) from "what the audience said" (from comments), then measures which video ideas showed up in the comments.

## Tech stack

- Python 3 (conda env expected on Windows, see gotcha below)
- `google-genai>=0.3` (Gemini LLM), `google-api-python-client>=2.100` (YouTube Data API v3)
- `youtube-transcript-api`, `langdetect`, `pandas>=2.0`, `markdown>=3.5`
- `transformers>=4.40` + `torch>=2.0` for local emotion classification (~500 MB download on first run)
- PDF: `playwright>=1.49` (+ `playwright install chromium`), fallbacks `weasyprint>=68`, `pdfkit`
- No test suite, no linter config, no CI. ~1,300 lines total.

## Run

```bash
pip install -r requirements.txt
pip install playwright && playwright install chromium
# copy config-template.py -> config.py, paste links + 2 API keys
python run.py
```

Full pipeline only. No unit tests, no single-stage entrypoint. To verify a change, run end to end and read `output/<session>/report.pdf`. For debugging set `KEEP_INTERMEDIATE = True` in `config.py` to dump intermediate files to `output/<session>/debug/` (most useful: `codebook.json`).

## Structure

| Path | Role |
|---|---|
| `config.py` | Not committed (gitignored). User copies from `config-template.py`. Links, keys, all tunables. |
| `config-template.py` | The template. Edit this if adding a config knob. |
| `run.py` | Preflight + orchestration. 5 stages, data passed in memory. |
| `pipeline/llm.py` | ALL model access. Swap provider here only. |
| `pipeline/collect.py` | Fetch comments/transcripts, clean, language filter. |
| `pipeline/brief.py` | What each campaign put forward (grounded + background). |
| `pipeline/analyze.py` | Codebook, signal transfer, emotion. |
| `pipeline/report.py` | Write report via LLM, render PDF, export CSVs. |

## Architecture facts an agent will otherwise miss

- **Model writes rules, code counts.** `analyze.build()` sends a stratified sample to the LLM which returns a codebook (themes + keyword lists). `analyze.apply_themes()` then applies keywords via regex to the FULL corpus. Cost is flat in corpus size; every % traces to `codebook.json`. Do not "improve" this by classifying each comment with the LLM.
- **Grounded vs background are deliberately separate** (`brief.py`). Grounded = from transcripts, checkable. Background = model's prior knowledge, NOT checkable, labelled unverified. Never merge them or let comments leak into the video brief; keeping them apart is the core design.
- **The report LLM sees statistics + a verbatim quote shortlist, never the corpus** (`report.py`). This is what stops invented percentages. Preserve it.
- **Keywords must never include brand/product names** (`brief.py` prompt). Brand's own posts inflate transfer counts. This is documented as the #1 way numbers go wrong.

## Gotchas

- **One API client per thread.** `googleapiclient`/`httplib2` is NOT thread-safe. `collect._service()` builds a thread-local client; never pass one between threads. Sharing surfaces as SSL record-layer failures or `NoneType has no attribute read`. Keep this rule if editing `collect.py`.
- **Windows: use `python`, not `py`.** `py` uses system Python and ignores the active conda env, giving `ModuleNotFoundError: googleapiclient`.
- **Model IDs go stale.** A 404 from Gemini means update `MODEL_CHEAP` / `MODEL_SMART` in `config.py`. They are config vars for this reason.
- **Preflight can't see the browser.** `run.py` preflight checks the `playwright` package but not the chromium download. Preflight passing then render failing = missing `playwright install chromium`.
- **Transient vs permanent errors** (`collect.py`): socket/SSL retry with backoff; `HttpError` (403 disabled, 404 gone) never retried. One bad video does not kill the run; run only fails if nothing is collected.
- **JSON from the LLM is repaired** (`llm.ask_json`): strips fences, trailing commas, embedded newlines, then re-asks the model to fix on parse failure. Reuse this path for any new JSON call.
- **CSVs use `utf-8-sig`** (BOM) for Excel. Comment data contains PII (display names); `*.csv`/`*.json` and `config.py`/`output/` are gitignored. Do not commit them.
- **`langdetect` often tags Indonesian slang as `tl`** (Tagalog); that's why `tl` is in `KEEP_LANGUAGES`.

## Work conventions

- Optimize for correct, reliable, idiomatic code. Follow existing patterns in the file you touch.
- Lean, not bloated. This is operational tooling; do not add abstraction/wrappers/automation unless a concrete blocker justifies it.
- Security: comment CSVs hold PII, API keys live only in gitignored `config.py`. Never commit real keys or output. If a hardening gap is left, note it one line in `SECURITY_NOTES.md`.
- Before "done": review correctness, edge cases, thread-safety in `collect.py`, and that the grounded/background/statistics separations are intact. Use @reviewer for an adversarial pass.
- Style bans (from user global rules): no em dashes (use `-`), no auto co-author lines in commits, never hand-edit auto-generated files.
