# Benchmark runbook

This document drives two benchmarks. One selects the Sentiment and Emotions producer. One measures whether Qwen can label 3,000 comments inside the release threshold while keeping Theme and Key Message quality. Both need an answer key that a person writes by hand.

---

## Hardware

- Required: one RTX 4060 Ti 16GB, 288 GB/s memory bandwidth.
- The Qwen candidates are `qwen3:14b-q4_K_M` (primary, roughly 9GB of weights) and `qwen3:8b-q4_K_M` (fallback challenger, roughly 5.2GB of weights before any KV cache).
- A 4GB card such as an RTX 3050 Laptop runs neither candidate, so no Qwen work can start on such a machine. The encoder candidates are base-size RoBERTa models and do run on CPU, so the Sentiment and Emotions encoder scoring is not blocked by the GPU.
- Ollama must be installed and its daemon running. Minimum version 0.12.7.
- `OLLAMA_NUM_PARALLEL` is 1, the Ollama default. Ollama sizes its KV cache as `OLLAMA_NUM_PARALLEL` times `OLLAMA_CONTEXT_LENGTH`, so raising it raises memory for no benefit here: the packed-prompt shape needs no concurrency.

---

## The answer key

The benchmark grades every model against labels a person decided by hand, not against anything the pipeline produces. Theme and Key Messages need their candidate lists fixed up front, because the model picks its answer by position in a list rather than by writing free text.

### Fixed Theme book

| Index | Theme | Definition |
|---|---|---|
| 0 | Product praise | Comment expresses satisfaction with or admiration for the product itself. |
| 1 | Product criticism | Comment expresses dissatisfaction with, or a complaint about, the product itself. |
| 2 | Ad reaction | Comment reacts to the advertisement or creative execution, not the product it depicts. |
| 3 | Question or request | Comment asks for information or requests details not given in the video. |
| 4 | Competitor comparison | Comment compares the product against a named or implied competitor or alternative. |
| 5 | Off-topic or spam | Comment is unrelated to the product or ad, or is promotional spam. |

### Fixed Key Messages

| Index | ID | Label | Description |
|---|---|---|---|
| 0 | km-01 | Fuel efficiency | The vehicle delivers class-leading fuel economy. |
| 1 | km-02 | Safety technology | The vehicle includes advanced driver-assistance and safety systems. |
| 2 | km-03 | Interior comfort | The vehicle offers spacious, comfortable, premium-feeling interior space. |
| 3 | km-04 | Value for price | The vehicle offers more features or quality than its price suggests. |
| 4 | km-05 | Design and styling | The vehicle stands out for its exterior design and styling. |

The `Index` column is the position in the corpus row's `allowed_theme_labels` and `allowed_key_message_ids` arrays, and it is the number the model emits. Never reorder either array between building the prompt and decoding the reply, or every label resolves to the wrong thing.

### Sentiment and Emotions codes

| Field | Label | Code |
|---|---|---|
| Sentiment | positive | `P` |
| Sentiment | negative | `N` |
| Sentiment | neutral | `U` |
| Emotions | anger | `A` |
| Emotions | fear | `F` |
| Emotions | joy | `J` |
| Emotions | sadness | `S` |
| Emotions | other_neutral | `O` |

`neutral` takes `U`, not `N`, because `N` is negative Sentiment. `other_neutral` is evaluation-only: report its prevalence, exclude it from the four-label Emotions Macro F1, never ship it as a label.

---

## Annotation protocol

- 150 rows total: 50 Indonesian, 50 English, 50 mixed Indonesian-English.
- Within each language stratum, at least 25 rows are short or emoji-only, and at least 25 carry negation, sarcasm, slang, or conflicting Sentiment and Emotions signals. These are the rows where a classifier actually fails; a stratum of easy rows measures nothing.
- Per comment the annotator assigns: `language`, `sentiment`, `emotion`, exactly one `true_theme` from the Theme book, and zero or more `true_key_message_ids`.
- Zero Key Messages is a normal and common answer. Do not force a match.
- Every one of `km-01` through `km-05` must appear as a true label on at least 5 rows across the whole set. This is a hard requirement, not a preference. Key Message Macro F1 is a mean of one-vs-rest F1 across all five allowed IDs, and an ID that never appears scores `0.0` and drags the mean down. Leave two of the five unmentioned and the ceiling drops to 0.6, below the 0.70 floor, so the gate fails on corpus composition rather than model quality. Check the five counts before you stop annotating.
- Annotate before any model runs. A label written after seeing a prediction is not an independent label.

---

## Corpus sourcing

- The pilot may draw from `output/kia-carens/debug/comments_raw.csv`: 358 rows across 2 videos, all unique text, 356 clearing the 4-letter floor. It is a pre-filter debug dump from a run that did not complete, it is local only, and `output/` is gitignored.
- Nothing in the pipeline produces the `mixed` value. `langdetect` returns exactly one ISO language code per comment, so a code-switched comment is forced to whichever single language wins. The `mixed` stratum is therefore assigned by hand during annotation, never extracted.
- For a larger set, a fresh collection is the only path. Three-call shape:

```python
import run
from pipeline import collect

cfg = run._load_cfg()
comments, meta = collect.fetch(cfg)
comments = collect.clean(comments, cfg)
```

`collect.clean` applies spam, minimum-length, and exact-duplicate filtering but drops nothing on language, and callers normally filter on the `in_base` column afterwards. For a benchmark corpus, skip that filter so off-language and code-switched rows survive into annotation. `collect.fetch` needs `YOUTUBE_API_KEY` set in `.env` and reads `VIDEOS` from `config.py`.

---

## Private files

| File | Purpose |
|---|---|
| `labels.jsonl` | The hand-written answer key: 150 rows with `sentiment`, `emotion`, `true_theme`, `true_key_message_ids`. |
| `sentiment-predictions.jsonl` | Encoder candidate Sentiment predictions. |
| `emotion-predictions.jsonl` | Encoder candidate Emotions predictions. |
| `qwen-sentiment-predictions.jsonl` | Qwen candidate Sentiment predictions. |
| `qwen-emotion-predictions.jsonl` | Qwen candidate Emotions predictions. |
| `qwen-corpus.jsonl` | Rows fed to Qwen, with the Theme and Key Message candidate arrays attached per row. |
| `qwen-comparison.jsonl` | Resolved true vs. predicted Theme and Key Messages for scoring. |

All live under `benchmark-data/private/`, which is gitignored and never staged.

Labels:

```json
{"id":"c1","text":"...","language":"id","sentiment":"positive","emotion":"joy","true_theme":"...","true_key_message_ids":["km-01"]}
```

Prediction (Sentiment):

```json
{"id":"c1","sentiment":"positive","confidence":1.0,"model":"..."}
```

Prediction (Emotions):

```json
{"id":"c1","emotion":"joy","confidence":1.0,"model":"..."}
```

Qwen corpus:

```json
{"id":"c1","video_id":"abc123","text":"...","allowed_theme_labels":["..."],"allowed_key_message_ids":["km-01"]}
```

Qwen comparison:

```json
{"id":"c1","true_theme":"...","predicted_theme":"...","true_key_message_ids":["km-01"],"predicted_key_message_ids":["km-01"]}
```

Every file is UTF-8 JSONL, one object per line, with unique non-empty IDs. Loaders reject duplicates, missing fields, invalid enum values, and malformed JSON with a `path:line` error.

`confidence` in a prediction row is a schema filler. Qwen writes the constant `1.0` there. No acceptance rule, selection rule, or exported column may read it, because a model's self-reported confidence is not a calibrated probability.

Comparison rows store resolved labels and IDs, never indices.

---

## Commands

Run every command from the repository root, and use `python`, never `py`.

Validate labels before anything else:

```bash
python tests/bench_classifiers.py --labels benchmark-data/private/labels.jsonl --validate-only
```

Both offline self-checks:

```bash
python tests/bench_classifiers.py --self-check
python tests/bench_qwen.py --self-check
```

The Qwen single-batch timing gate. Run this first, every time, and read its projection before committing to a full run:

```bash
python tests/bench_qwen.py --model qwen3:14b-q4_K_M --corpus benchmark-data/private/qwen-corpus.jsonl --comparison benchmark-data/private/qwen-comparison.jsonl --limit 20 --batch-size 20 --output benchmark-results/qwen-gate.json
```

The full 14B run, then the 8B fallback challenger. Each writes its own record; never point two runs at one output path:

```bash
python tests/bench_qwen.py --model qwen3:14b-q4_K_M --corpus benchmark-data/private/qwen-corpus.jsonl --comparison benchmark-data/private/qwen-comparison.jsonl --limit 3000 --batch-size 20 --output benchmark-results/qwen.json
```

```bash
python tests/bench_qwen.py --model qwen3:8b-q4_K_M --corpus benchmark-data/private/qwen-corpus.jsonl --comparison benchmark-data/private/qwen-comparison.jsonl --limit 3000 --batch-size 20 --output benchmark-results/qwen-8b.json
```

`--output` names the benchmark record: timings, invalid counts, Macro F1 scores, and the pass verdict. It is not where predictions go. The two Qwen prediction files are written by the prediction pass over `labels.jsonl`, which is what feeds the scoring run below.

The classifier scoring run:

```bash
python tests/bench_classifiers.py --labels benchmark-data/private/labels.jsonl --sentiment-predictions benchmark-data/private/sentiment-predictions.jsonl --emotion-predictions benchmark-data/private/emotion-predictions.jsonl --qwen-sentiment-predictions benchmark-data/private/qwen-sentiment-predictions.jsonl --qwen-emotion-predictions benchmark-data/private/qwen-emotion-predictions.jsonl --output benchmark-results/classifiers.json
```

The two Qwen prediction flags are optional. Omitting them scores encoders only, recording the Qwen systems as not run.

Timing gate rule: process exactly one packed 20-comment batch, record wall-clock seconds and output token count, project the 3,000-row time, and abort instead of starting the full run when the projection exceeds 7,200 seconds. A projection is always labelled a projection, never a measurement.

---

## Limitations of the pilot

- One annotator, single pass. No second independent annotator, so there is no inter-annotator agreement number and no way to separate a hard row from an inconsistent one.
- N=150 means 50 rows per language stratum. The acceptance rule compares variants on margins as narrow as 0.02 Macro F1, and at 50 rows that margin is inside the sampling noise.
- The Theme book and Key Message list are hand-authored, not discovered from the comments by the pipeline and not drawn from real user setup. The benchmark therefore measures how well the model labels against a fixed reasonable book, not against the book the product would actually build.
- This pilot validates the tooling, the file formats, and the annotation protocol. It does not decide what ships. A ship decision needs a larger set, two independent annotators, and a Theme book from a real Session.

---

## Privacy

`benchmark-data/private/` holds real comment text and is never staged. Only schemas, this runbook, and sanitized aggregate results under `benchmark-results/` are committed. Aggregate results carry counts and metrics, never comment text.
