# Changelog

Notable changes to this project. Newest first. This file is the record of what changed and why; the `docs/` files describe the system as it stands.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). This project is not versioned; entries sit under dated releases or `[Unreleased]` for planned and in-progress work.

---

## [Unreleased]

Approved plan closing the gap between the shipped code and the product described in [README.md](README.md). Backend and pipeline first, frontend second, verification and docs last.

### Decisions

- 40 minutes for a 3,000-comment run is a release benchmark, not a forced cancellation.
- Closing the tab does not stop a run. Recovery from a backend or computer restart is out of scope.
- No model winner until tomorrow's benchmark.
- Multilingual scope: Indonesian, English, and mixed Indonesian-English comments.
- Macro F1 is the selection metric; overall accuracy is secondary.

### Shared API contract

Session response additions:

```ts
type KeyMessage = {
  id: string;
  label: string;
  description: string;
  included: boolean;
  order: number;
};

type KeyMessageDraft = {
  status: "empty" | "drafting" | "ready" | "stale" | "failed";
  messages: KeyMessage[];
  error: string | null;
};
```

**Draft Key Messages** - `POST /api/sessions/{sessionId}/key_messages/draft`
- Uses latest User Inputs.
- Preserves manually edited messages.
- Keeps the previous draft if generation fails.
- Coalesces concurrent requests into one latest rerun.
- Returns `KeyMessageDraft`.

**Save Key Messages** - `PATCH /api/sessions/{sessionId}/key_messages`

```ts
type SaveKeyMessagesRequest = { messages: KeyMessage[] };
```

- Replaces the complete ordered list in one DB transaction.
- Accepts an empty list and zero included messages during setup.
- Rejects duplicate or unknown IDs.
- Returns the complete saved list.
- At `brief_pause`, requires at least one included message before continuing.

### Phase 1 - Backend and pipeline

1. **Prepare local models.** Remove remaining Gemini assumptions. Prepare the largest Qwen candidate expected to fit 16 GB VRAM. Add tomorrow's reproducible quality, VRAM, and 3,000-comment timing benchmark; no selection or pass/fail claim today. Prepare multilingual Sentiment and Emotion candidates and select by Macro F1 tomorrow.
   Interfaces: consumes `PipelineConfig`, the Ollama HTTP API, labelled benchmark comments; produces unchanged LLM JSON contracts and benchmark results with Macro F1, accuracy, runtime, and peak VRAM.
2. **Move User Input extraction to setup.** Extract documents and fetch articles when saved. A failed article fetch remains an asset with empty text. Remove duplicate run-time extraction. Preserve image multimodal input. Remove key-visual config and report embedding.
   Interfaces: consumes existing upload and article requests; produces persisted asset text and image metadata for the draft endpoint.
3. **Add Session-level Key Messages.** Stable IDs, order, inclusion, manual-edit metadata, draft status. Clean local DB reset, no migration. Split initial drafting from transcript reconciliation. No-input Sessions draft from transcripts. Snapshot Session messages into the run before reconciliation.
   Interfaces: consumes extracted User Inputs, transcripts, `PipelineConfig`; produces `KeyMessageDraft` and an immutable run-scoped `brief_points`.
4. **Implement drafting and atomic editing.** Add the two Session routes above. Save assets first and draft separately. Preserve manual edits. Failure keeps the previous list, marked stale. A stale or failed draft may still start a run. A second active run returns `409`.
   Interfaces: consumes `SaveKeyMessagesRequest` and the current Session revision; produces camelCase responses and standard `{"error", "message", "field"}` errors.
5. **Update reports and artifacts.** Remove the summary and old chart names. Produce six downloads plus an internal report JSON. Rename JSON keys to `keyMessages` and `keyMessageSentiment`, no compatibility keys. Remove cross-group content. Counting stays in Python.
   Interfaces: consumes final per-comment labels and evidence; produces `report.pdf`, `comments.csv`, `key-messages.csv`, `themes.csv`, `sentiment.csv`, `emotions.csv`, and internal `report.json`.

### Phase 2 - Frontend

1. Remove chat, search, source discovery, OCR notes, lenses, and key-visual UI.
2. Add setup Key Message pending, ready, stale, failed, and Retry states.
3. Keep old messages visible during redraft and preserve active edits.
4. Allow a run with a stale draft after a warning.
5. Restore `brief_pause` after reopening a tab without the original SSE event.
6. Do not visually mark transcript reconciliation changes.
7. Show exactly six downloads; never expose `report.json`.
8. Update demo mode to the same flow and API shapes.
9. Retain unused `demoApi.setKeyVisual(campaignId, assetId)` as a no-op compatibility stub, because AGENTS.md freezes existing signatures.

Interfaces: consumes Session Key Message routes, run snapshots, SSE, and artifact metadata; produces complete ordered PATCH requests, accessible setup states, and six download actions.

### Phase 3 - Verification and documentation

1. Keep direct assertion scripts, not pytest.
2. Add checks for: stable IDs and manual edits; stale, retry, and coalescing behaviour; atomic PATCH; no-input transcript drafting; stale-run start; tab-close pause recovery; seven stored artifacts and six downloads; removed old keys and names.
3. Tomorrow: run the Qwen quality and 3,000-comment timing benchmark, the multilingual classifier Macro F1 benchmark, backend scripts, `node --check app/app.js`, the browser self-check, and an end-to-end run.
4. Update `README.md`, `CHANGELOG.md`, `AGENTS.md`, `docs/setup.md`, `docs/architecture.md`, and `docs/api-reference.md` only after behavior stabilizes.
5. Document clean deletion of local `data/` before using the new schema.

---

## July 2026

### Backend and frontend

FastAPI backend (`server.py`) wrapping the pipeline with Sessions, uploads, a run lifecycle, and SSE progress. SQLite persistence, local file storage for uploads and artifacts. Single-page vanilla frontend in `app/`, live against the backend when served by it and on an in-memory fixture store otherwise.

Key Message review interrupt added: the run pauses after drafting, the user edits and confirms, then labelling proceeds.

### Per-comment LLM labelling

Replaced regex keyword matching with LLM classification. The model discovers a Theme book from a stratified sample, then labels each comment in batches against that fixed set: exactly one Theme, zero or more Key Messages, one pass. Python counts the labels.

The driver was accuracy. Regex undercounted paraphrase and mishandled negation ("not worth it" matching a `worth` keyword). Counting stayed in Python, which was never in question.

### Grounded-only brief

Removed the background brief, where the model wrote what it knew about a campaign from memory. Taglines, unit counts, and launch dates are exactly what a model invents fluently. Every claim now traces to something the user provided.