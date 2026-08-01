# Backend Rework - Decision Record and Plan

Status: superseded. Part B (Phase A plan) shipped; see [docs/architecture.md](../../docs/architecture.md) for the current backend. Part C ("Phase B, not planned") also shipped as the same backend - session/campaign persistence, asset ingestion, and the frontend all exist now. Kept as historical record of the research and decisions behind the classify-per-comment design (Part A remains accurate and useful). The tier framing in Part A/B ("Pro tier ... rate limit not cost") did not hold: the project runs on the free tier (see [README.md](../../README.md#cost)); read `MODEL = "gemini-3-pro"` as a model-name choice, not a billing-tier claim.
Date: July 2026.

This document has three parts:

- Part A: the research that settled the core architecture decision.
- Part B: the Phase A plan (backend), decision-complete.
- Part C: an indication of what Phase B (campaign manager) would entail. Not a plan.

---

## Context

The pipeline analyses YouTube comment corpora to measure (a) what themes appear
in a conversation and (b) whether the ideas a brand pushed "transferred" into the
comments. It then produces percentages, a report, and (after this rework) charts.

Two things drove the rework:

1. The report quality bar is a Claude-generated creative-strategy note
   (`campaign_strategy_note (1) (1).pdf`). We want the automated pipeline to reach
   that level. Its structure already matches the current report template; the gap
   is that the current pipeline never lets the model read real comments, only
   statistics.
2. Token frugality is no longer the constraint. The project now runs on the Gemini
   Pro tier. The binding constraint is rate limit, not token cost. This unlocks
   reading real comments and classifying every comment individually.

---

# Part A - Research findings (Sergio Busquets)

Question researched: for classifying a few thousand short, code-switched
Indonesian/English comments and reporting percentages, what is the industry
standard - having an LLM output aggregate numbers directly (Option A), or having
the LLM label each item and computing aggregates in code (Option B)?

Bottom line: Option B. Every authoritative source points the same way - numbers
come from deterministic counting over inspectable per-item labels, never from an
LLM's own aggregate estimate.

### Q1 - Best-practice pattern at scale
The standard is LLM-as-annotator: the model labels individual items, aggregates
are computed downstream in code. No authoritative source recommends asking an LLM
to emit population statistics directly.
- Gilardi, Alizadeh, Kubli (2023), PNAS - "ChatGPT outperforms crowd workers for
  text-annotation tasks." https://www.pnas.org/doi/10.1073/pnas.2305016120
  (arXiv: https://arxiv.org/abs/2303.15056)
- Tan et al. (2024), EMNLP - "Large Language Models for Data Annotation and
  Synthesis: A Survey." https://arxiv.org/abs/2402.13446
- OpenAI Batch cookbook - vendor examples issue one request per item, aggregation
  left to the caller. https://cookbook.openai.com/examples/batch_processing
Evidence: strong and consistent (peer-reviewed + vendor).

### Q2 - Can LLMs count/aggregate over large sets in one pass?
No. This is the strongest point against Option A. Two failure modes:
1. Numerical weakness - Li et al. (2025), ACL Findings, NumericBench: GPT-4 and
   DeepSeek "perform surprisingly poorly" on fundamental numerical tasks;
   aggregation is a named weakness. https://arxiv.org/abs/2502.11075
2. Long-context degradation - Liu et al. (2023), TACL, "Lost in the Middle":
   accuracy is highest when relevant info is at the start or end and "significantly
   degrades" in the middle, "even for explicitly long-context models."
   https://arxiv.org/abs/2307.03172
A single mega-prompt asking for percentages compounds a counting weakness with a
position-based reliability decay across the very items being counted.
Evidence: strong (ACL, TACL).

### Q3 - Batching classification
Batching to save calls measurably degrades per-item accuracy and adds order/position
sensitivity. Keep classification per-item or in small batches; use structured
output with a fixed enum for label consistency.
- Lin et al. (2023/24), BatchPrompt: batched data in longer contexts "will
  inevitably lead to worse performance"; performance is "significantly correlated
  with the positions and order of the batched data." https://arxiv.org/abs/2309.00384
- OpenAI Structured Outputs - constrain output to a JSON Schema with a fixed enum so
  the model can only emit predefined labels (no drift across batches).
  https://platform.openai.com/docs/guides/structured-outputs
- OpenAI Batch API - sanctioned way to run many per-item requests cheaply.
  https://cookbook.openai.com/examples/batch_processing
Guidance: small batches, theme set pinned as an enum, batch size treated as an
accuracy knob to validate, not a free lunch.
Evidence: strong (dedicated study + vendor docs).

### Q4 - Reproducibility / auditability
Traceability is a core methodological requirement. Quantitative content analysis is
defined by an explicit codebook, applied to individual units, with inter-coder
reliability computed over per-unit codes. A percentage that cannot be traced to
inspectable per-item labels does not clear standard methodological review. Option B
provides this; Option A destroys it.
- Gilardi et al. (PNAS) report intercoder agreement, computable only when every unit
  has an individual inspectable label.
- Zhu et al. (2023), "Can ChatGPT Reproduce Human-Generated Labels?" - performance
  "varies substantially across individual labels" (avg accuracy 0.609), discoverable
  only because labels are per-item. https://arxiv.org/abs/2304.10145
Caveat on provenance: the classical content-analysis standard (codebook + inter-coder
reliability, e.g. Krippendorff) is asserted from established domain knowledge; a
primary methodology URL was not retrievable during research (search access blocked).
The Gilardi and Zhu papers concretely demonstrate the per-item-label practice.
Evidence: strong for the demonstrated practice; the textbook attribution is
uncited but uncontroversial.

### Q5 - LLM labeling vs keyword/regex for paraphrase, negation, sarcasm
Yes for paraphrase and negation (high confidence). Sarcasm: better than keywords but
still weak in absolute terms - treat sarcasm-sensitive labels cautiously either way.
- Zhang et al. (2023), "Sentiment Analysis in the Era of LLMs: A Reality Check" -
  LLMs "significantly outperform" domain-trained small models few-shot, but "lag
  behind in more complex tasks." https://arxiv.org/abs/2305.15005
This directly fixes the current regex codebook's named failures ("undercounts
paraphrase, overcounts negation - 'not worth it' matches worth").
Evidence: strong for paraphrase/negation; moderate and hedged for sarcasm.

### Q6 - Cost / latency / rate limit for a few thousand short items
Per-item labeling is affordable. Gilardi et al. report per-annotation cost under
$0.003; at 8,000 comments that is order-of-magnitude tens of dollars on a paid tier,
effectively free on the tier this project uses. A single 8,000-item mega-prompt risks
context limits and output truncation (already a known hard-failure mode here). Small
units parallelise cleanly, stay within rate limits, and are robust to retry.
Evidence: strong on cost; latency/robustness are straightforward engineering
consequences.

### Recommendation adopted
Option B. LLM discovers a fixed theme set from a sample, classifies each comment
against that fixed set (enum-constrained structured output), and Python counts labels
to produce percentages.

Refinements adopted:
1. Counting stays in code. Percentages come from counting per-item labels, never from
   an LLM aggregate. Non-negotiable.
2. Upgrade the labeler from regex to per-item LLM classification against the fixed
   codebook. Fixes paraphrase/negation.
3. Constrain output with a strict JSON Schema enum of theme labels for cross-batch
   consistency.
4. Batch conservatively; validate accuracy against batch size.
5. Preserve auditability - every percentage traces to an inspectable per-comment
   label.
6. Keep the sarcasm caveat in the report.

---

# Part B - Phase A plan (backend)

Scope: backend only. Delete regex keyword matching; the LLM labels each comment
against a fixed theme set (discovered by the LLM); Python counts the labels.

## Decisions locked

- Option B, as above.
- Single model. The project runs on the Pro tier. There is no cheap/smart split;
  a single `MODEL` constant is used for discovery, classification, and the report.
  Consequence: classification issues many Pro calls, and Pro has tighter rate limits
  than Flash. Mitigations: the existing 429 backoff in `llm.ask`, a tunable batch
  size (raise to cut call count), and the fact that this is an offline job where
  latency does not matter. Rate limit is the accepted binding constraint.
- Emotion stays local (HuggingFace), unchanged. Folding it into the LLM pass would
  fix the sarcasm caveat nearly for free - noted as a future option, not done now.
- Two charts, each shipping its own CSV for Google Slides: a signal-transfer bar
  (ranked descending, "which ideas arrived") and a theme-frequency bar ("what the
  audience talked about").
- Charts are CSS bars rendered by the existing markdown -> HTML -> PDF path. No new
  dependency. Bar widths are computed in code from deterministic counts; the model
  never writes a chart number.
- Key visuals: optional image path per group in config, embedded in the report.
  Sourcing them automatically is Phase B.

## What "remove the codebook" actually means

The regex keyword matching is deleted (`_pattern`, keyword lists, first-match-wins).
The codebook survives, transformed: instead of a keyword rulebook applied by regex,
it becomes a short list of themes + definitions that the LLM applies per comment as
an enum. Numbers still come from counting labels, so auditability is preserved and
the reference report's own "keyword matching undercounts paraphrase" caveat can be
deleted - we no longer keyword-match.

## Discovery sampling (the supervisor's concern)

Key reframe: under Option B the LLM classifies every comment, so percentages are
computed over the full corpus and are representative by construction. The discovery
sample is used only to build the theme list - it never touches the numbers. It
therefore needs to be comprehensive (contain an example of every real theme), not
proportionally representative.

The sample draws four strata per video:
- most-liked - consensus themes, what the audience agreed with.
- most-replied - controversial themes, what sparked argument. Requires capturing
  reply count (see Task 1); `collect.py` currently discards `totalReplyCount`.
- longest - where multi-topic, reasoned comments live.
- random tail - catches niche/minority themes that no engagement slice surfaces.

Why not most-liked + most-replied alone: both over-weight popular comments, so a
real but unpopular theme (a specific complaint, a minority reading) never enters the
sample, never becomes a theme, and every comment about it falls into "Other". The
random slice plus the definitions below prevent that.

Two safety nets, both showable to the supervisor:
1. On the Pro tier the discovery sample can be large (target 500-800 comments) since
   it is one call - coverage is cheap.
2. `extend()` re-scans the "Other" pile; if its share exceeds the threshold it asks
   for additional themes and reclassifies only that subset. Even if discovery misses
   a theme, the top-up recovers it. Self-correcting coverage.

## Data contract

After classification the `base` DataFrame carries new columns; everything else
unchanged, so downstream (`summarise`, `emotion`, CSV export) keeps working:
- `theme: str` - one discovered theme name, or `"Other"`.
- `pt__<slug>: bool` - one column per brief point, True when the comment echoed that
  idea. Same naming as the old `apply_points`, so `summarise`/`export` are untouched.

## Tasks

### Task 1 - collect.py: capture reply count
Consumes: unchanged. Produces: `reply_count` column on the comments DataFrame.
- In `_comments`, read `item["snippet"]["totalReplyCount"]` on each top-level thread
  and store it on the top-level row; replies get 0.
- Add `reply_count` to the row dict. Everything else in `collect.py` unchanged,
  including the thread-local service pattern (do not touch).

### Task 2 - llm.py: single model + enum-constrained classification
Consumes: nothing new. Produces:
`classify_batch(prompt, theme_names, point_labels, model=None) -> list[dict]`
returning `[{"index": int, "theme": <enum>, "echoed": [<subset of point_labels>]}]`.
- Replace `MODEL_CHEAP`/`MODEL_SMART` usage with a single `config.MODEL`.
- Build a response schema: array of objects
  `{index: INTEGER, theme: STRING enum=theme_names+["Other"], echoed: ARRAY of STRING enum=point_labels}`.
- Use `from google.genai import types`;
  `config=types.GenerateContentConfig(response_mime_type="application/json", response_schema=<schema>)`.
- On any exception mentioning schema/response_schema/tool: degrade to `ask_json`
  (the prompt itself lists valid labels), mirroring the existing `grounded` degrade.
- Reuse the existing 429 backoff loop.

### Task 3 - brief.py: drop point keywords
Consumes: unchanged (`meta_df`, `context_map`).
Produces: `points: list[{group, video_id, label, description}]`.
- Delete the `"keywords"` line from the `points` schema in `PROMPT` and the trailing
  "Keywords are the important part..." block.
- In `run()`, stop reading `point["keywords"]`. Grounded/background briefs unchanged.

### Task 4 - analyze.py: replace regex with LLM classification
Delete `_pattern`, `apply_themes`, `apply_points`, and keyword use in `build`/`extend`.
Keep `summarise`, `emotion` unchanged. Rework `_sample` for four strata (Task's
sampling section).
- `build(df, summary) -> themes`: stratified discovery call; themes now carry
  `name` + `definition` only (no keywords). Order no longer matters. Sample size
  targets 500-800, capped by `config.CODEBOOK_SAMPLE_MAX`.
- `classify(df, themes, points, summary) -> (df, columns)` (new, core):
  - group points by `video_id`; `theme_names = [t["name"] for t in themes]`.
  - iterate comments grouped by `video_id`, chunk into batches of
    `config.CLASSIFY_BATCH_SIZE`.
  - per batch build a prompt with: themes + definitions (pick exactly one name or
    "Other"), that video's points as `label - description`, and numbered comments
    (index: text, each truncated to ~300 chars).
  - call `llm.classify_batch(...)`; assign `theme`; omitted indices -> "Other".
  - build `pt__` columns with the old naming
    (`"pt__" + re.sub(r"\W+","_",label.lower())[:40]`), init False, set True for
    echoed labels on their own video's rows. Return `(df, columns)`.
  - `extend()`: if "Other" share >= `config.UNCLASSIFIED_LIMIT` and count >= 25, one
    discovery pass over the Other subset for 1-4 extra themes, then reclassify only
    that subset against the extended set.
Produces: `themes`, `base` with `theme` + `pt__` columns, `columns` dict.

### Task 5 - report.py: real-comment writer, CSS charts, per-chart CSV, key visual
- Writer: `_quotes()` builds a richer labeled pool (per group+theme: top-liked +
  longest, cap ~120), each line tagged with labels:
  `[group | theme | emotion | echoed: A, B | N likes] "text"`. Keep feeding the
  deterministic tables. Add tokens `[[CHART:transfer]]` and `[[CHART:themes]]` into
  the prompt's "At a glance" structure; the model must not invent chart numbers -
  tokens are replaced by code. Single `config.MODEL`. Keep VERDICTS, structure,
  REPORT_LANGUAGE.
- Charts: `_chart_html(rows, title, subtitle)` renders horizontal CSS bars, ranked
  descending, in the existing editorial style (add `.chart/.bar/.bar-fill/.bar-label/
  .bar-val` to CSS; width = value/max*100%). Transfer chart from `transfer_table`
  (label `group - point`, value `echoed_pct`). Theme chart from
  `base["theme"].value_counts(normalize=True)*100`. In `render()`, after
  markdown->HTML, regex-replace the surviving `[[CHART:...]]` paragraphs with chart
  HTML.
- Per-chart CSV in `export()`, written to `out_dir` (not debug):
  `chart_transfer.csv` (idea, group, percent, n) and `chart_themes.csv`
  (theme, percent, n), both sorted descending. `summary.csv` stays as the full tidy
  export.
- Key visual: read `config.KEY_VISUALS` (`{group_name: image_path}`); for each
  `## {group}` heading, if the file exists, inject a base64 data-URI `<img
  class="keyvis">` after the heading (no file-access flags needed). Missing file ->
  skip silently. Add `.keyvis` CSS.
Produces: `report.pdf`, `comments.csv`, `summary.csv`, `chart_transfer.csv`,
`chart_themes.csv`.

### Task 6 - config, run.py
- `config-template.py` and `config.py`:
  - replace `MODEL_CHEAP`/`MODEL_SMART` with a single `MODEL` (Pro tier).
  - add `CLASSIFY_BATCH_SIZE = 25` (smaller = more accurate, more calls).
  - add `KEY_VISUALS = {}` with a commented example.
  - update codebook comments: discovery builds a theme schema the LLM applies per
    comment; `UNCLASSIFIED_LIMIT` now governs the Other-share top-up.
- `run.py`: rewire stage 3 to `build -> classify -> extend/reclassify -> summarise`;
  update stage-3 prints ("classifying N comments in B batches", "Other: X%"). Keep
  preflight and `KEEP_INTERMEDIATE` saves (`codebook.json` now themes+definitions;
  add a `classified.csv` sample to debug).
- `requirements.txt`: no change (verify nothing new is imported).

### Task 7 - Verification
1. Offline self-check (no API): assert-based script stubbing `llm.classify_batch`
   with canned labels over ~10 fake comments across 2 videos; assert every comment
   gets a theme, omitted indices become "Other", `pt__` columns are True only on the
   right video's rows, and `summarise` percentages match a hand count. Locks the
   counting logic without spending calls.
2. End-to-end (if keys set): `python run.py` on the existing config. Confirm the
   pipeline completes, `report.pdf` renders with two CSS bar charts and any key
   visuals, both chart CSVs open cleanly. Use `python`, not `py`. If keys are absent,
   state E2E was not run and why.
3. Remove any temp test file.

## What Phase A achieves vs the reference PDF
- Kills keyword matching -> the reference's own paraphrase caveat can be dropped.
- Writer reads labeled real comments -> verdicts and glosses reach the reference's
  quality.
- Two charts + their CSVs -> the "at a glance" bar and the Slides handoff.
- Numbers stay deterministic and auditable -> chart values are real counts.

---

# Part C - Phase B (campaign manager) - indication only

Not planned. Documented so scope is clear and Phase A does not accidentally block it.

Intent: turn the pipeline from a config-file batch job into a managed workspace.

- Session -> campaigns hierarchy. A session holds several campaigns (e.g. Nike,
  Adidas). This generalises today's single-config run.
- Per campaign, two input types:
  - YouTube links (as today).
  - Drag-and-drop assets: PDFs, images (posters/key visuals), and article links.
    These feed campaign context and supply the key visuals Phase A only accepts by
    manual path.
- A sourcing chatbot per campaign: "find sources or contacts for this campaign" -
  an assistant that helps discover relevant videos/articles to add.

Why it is a separate scope: it spans a new data model (session/campaign persistence),
file ingestion and parsing (PDF/image/article extraction), a frontend (the manager
UI; reference images already exist under `references/`), and an interactive agent -
none of which the Phase A backend touches.

Contract-first note: when Phase B is planned, define the shared API contract between
frontend and backend before splitting into scope phases. Phase A's outputs
(per-group results, chart CSVs, report) become one consumer of that contract.

Open questions to resolve at planning time (not now):
- Persistence: where do sessions/campaigns/assets live (local files, SQLite, a DB)?
- Asset extraction: how PDFs/images/articles convert into campaign context and
  whether images auto-populate `KEY_VISUALS`.
- Chatbot grounding: what sources it may search and how a suggested video becomes a
  pipeline input.
- How a Phase B run invokes the Phase A pipeline per campaign and collects results.
