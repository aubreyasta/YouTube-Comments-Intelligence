# Changelog

Notable changes to this project. Newest first. This file is the record of what
changed and why; the `docs/` files describe the system as it stands now.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
This project is not versioned; entries are grouped under dated releases or
`[Unreleased]` for planned and in-progress work.

---

## [Unreleased]

### Themes replaced with top keywords (planned)

Status: planned. Decisions locked. Not yet implemented.

The theme system is removed and replaced with keyword frequency. The driver is
per-run LLM cost: theme classification sent every comment to the model in
batches, so cost scaled with corpus size. Keyword counting keeps one model call
to build the keyword set, then measures frequency with regex over the full
corpus at zero token cost.

Key messages (signal transfer, the `pt__` columns) stay on the LLM. They are the
headline accuracy metric, and regex misses semantic echoes that share no words
with the message. A regex-vs-LLM comparison for key-message echo detection was
considered and postponed.

**Mechanism**

- `analyze.build()` stops emitting themes (name plus definition) and instead
  emits curated keywords, each with spelling and abbreviation variants for the
  multilingual comment mix (for example `harga` also written `hrg`).
- A new `analyze.count_keywords()` applies those keywords with word-boundary,
  case-insensitive regex. One `kw__` boolean column per keyword. A comment
  counts once for a keyword regardless of repeats.
- `analyze.classify()` keeps only the key-message echo pass (`pt__` columns).
  Theme labeling is removed.
- `analyze.extend()` (the 30%-Other top-up pass) and the `Other` bucket are
  deleted. Keyword frequency has no exclusive buckets and no leftover concept.
- `analyze.summarise()` returns a keyword frequency table instead of a theme
  crosstab. Theme percentages summed to 100 (one theme per comment); keyword
  percentages measure containment and do not sum to 100.

**Report and contract changes**

- The report LLM interprets keyword statistics into "what people talked about"
  prose. It may name a grouping and cite individual keyword percentages, but
  never invent a combined percentage that appears nowhere in the data.
- `report.json`: the `themes` key becomes `keywords`. Metric ids change from
  `m-th-<i>` to `m-kw-<slug>`. The subtitle counts keywords, not themes.
- Artifact `chart_themes.csv` becomes `chart_keywords.csv`
  (`keyword,pct_of_comments,n`). Registry kind `chart_themes_csv` becomes
  `chart_keywords_csv`.
- `comments.csv` `theme` column becomes `keywords` (semicolon-joined matched
  keywords per comment). `summary.csv` metric `theme` becomes `keyword`.

**Frontend changes**

- The results screen shows the top 10 keywords with an expand control for the
  full list. The chart is a ranked bar list, not a distribution that sums to
  100. The `Other` bar is removed.
- Progress stepper and counters drop theme and `Other` language for keyword
  extraction copy.

**Scope**

Pipeline (`analyze.py`, `run.py`), report contract (`report.py`, `adapter.py`,
`server.py`), and frontend (`app/app.js`, `app/self-check.html`). Key messages,
`pt__` echoes, signal transfer, emotion, and sentiment are unchanged.

See [docs/architecture.md](docs/architecture.md) for the current system once
implemented.
