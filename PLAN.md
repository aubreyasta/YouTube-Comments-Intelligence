# Project Plan

## Goal

Operate YouTube Intelligence as one shared internal service; see [PRODUCT.md](PRODUCT.md). Current focus: complete the real-device Mac deployment acceptance that the LM Studio provider migration left open.

## Locked implementation decisions

- Model pin: a vision-capable 4-bit MLX build of `Qwen3.8-27B` only. `LLM_CONTEXT_LENGTH=32768` and `CLASSIFY_BATCH_SIZE=8` are starting values — change only after a real-device run shows memory pressure or unacceptable throughput, and never mid-acceptance-run.
- Publish only port 8000 through the Cloudflare quick tunnel. LM Studio (port 1234) must never be published.
- The Mac deployment starts with an empty `data/`. Do not migrate the Windows workstation's database, uploads, runs, or artifacts.

## Out of scope

- Rewriting `docs/setup.md`, `docs/architecture.md`, `docs/api-reference.md`, or `docs/deployment.md` — they remain the implementation-detail authority.
- New product features (chat, search, OCR, custom lenses, run history, accounts) — see [PRODUCT.md](PRODUCT.md#scope).
- Data migration from the Windows workstation to the Mac.

## Phases

### Phase F: Real Mac deployment acceptance
**Status:** blocked (needs physical access to the target MacBook Pro M1 Max; cannot be established from this Windows workstation or by offline tests)
**Depends on:** Phase D (LM Studio provider migration)
**Scope:** deployment / operational acceptance — no code changes expected unless acceptance surfaces a defect
**Acceptance:**
- Every checklist item in `docs/deployment.md` Steps 1-8 is recorded: hardware/macOS confirmation, LM Studio model + vision + context + structured-output + image smoke tests, FastAPI Basic Auth boundary, `cloudflared` external checks, one complete real Session (including an image User Input, `brief_pause` edit, skip-pause, and tab-close/restore), six artifacts downloaded with `report.json` hidden, and login-start behavior for LM Studio/FastAPI/`cloudflared`.
**Verification:**
- The exact command and `curl` sequences already written into each step of `docs/deployment.md`.

## Completed

### Phase A: Core product MVP - 2026-08-15
Delivered: FastAPI backend + vanilla frontend with the Key Message review interrupt; grounded Key Messages from User Inputs; LLM classification replacing regex keyword matching.
Verified: `tests/e2e_product_flow.py` 15/15; `app/self-check.html` 188/188.
Remaining: none

### Phase B: Security hardening and single-run admission - 2026-08-18
Delivered: SSRF guard on article fetching, fail-closed Basic Auth, global one-run guard (`BEGIN IMMEDIATE`), sentiment/emotion merged into the classify pass (`torch`/`transformers` dropped, confidence columns removed), skip-pause control, benchmark harness removed.
Verified: commit `7f622f7` — verification debt cleared.
Remaining: none

### Phase C: Demo presentation (Waves D1-D3) - 2026-09-01
Delivered: hand-labelled Indomie fixture (181 comments) and generated artifacts via `demo_data/build_demo_artifacts.py`; `?demo=1` entry isolated from `window.__liveApi`; route/stepper/drawer motion with reduced-motion overrides.
Verified: `tests/e2e_product_flow.py` 20/20; manual walkthrough (setup, `brief_pause`, results, six downloads, live-tab isolation, motion) confirmed complete.
Remaining: none

### Phase D: LM Studio provider migration - 2026-08-31
Delivered: replaced the Windows/Ollama model boundary with LM Studio — `PipelineConfig` `LLM_BASE_URL`/`LLM_MODEL`/`LLM_CONTEXT_LENGTH`/`LLM_TIMEOUT_SECONDS`, non-streaming OpenAI-compatible chat completions, strict structured output, multimodal data URLs, exact-model + vision preflight, loopback enforcement, three-attempt retry.
Verified: 152 assertions across 18 direct Python scripts incl. `tests/test_llm.py` 21/21, `tests/test_run_key_messages.py` 10/10, `tests/test_skip_pause.py` 7/7; both `node --check` clean; `git diff --check` clean.
Remaining: browser E2E was skipped by user instruction in this phase; closed by Phase C's 20/20 pass instead.

### Phase E: Documentation reconciliation and four-document project context - 2026-09-09
Delivered: README/AGENTS/docs reconciled with the shipped LM Studio boundary; established PRODUCT.md and DESIGN.md; retired PRD.md; added this PLAN.md and CLAUDE.md's documentation map.
Verified: `tests/e2e_product_flow.py` 20/20 (2026-09-01 corpus reconstruction); this session's document changes reviewed by the user.
Remaining: none

## Open conflicts

- none identified
