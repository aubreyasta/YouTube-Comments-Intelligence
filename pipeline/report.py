"""
Produce the deliverables.

    report.pdf         the debrief
    comments.csv       every comment, labelled, for digging through by hand
    key-messages.csv   per-(group, Key Message) mentions and sentiment split
    themes.csv         per-(group, theme) counts from raw comments
    sentiment.csv       per-(group, sentiment) counts from raw comments
    emotions.csv        per-(group, emotion) counts from raw comments

The model that writes the report reads real labeled comments alongside the
deterministic tables. Numbers in the report still come only from those tables;
the model is explicitly told not to invent chart figures - the [[CHART:...]]
tokens are replaced by code-generated CSS bars after the LLM returns.
"""

import base64
import os
import re

import pandas as pd

from pipeline import llm
from pipeline.config_types import PipelineConfig

# ==================================================================
# 1. WRITE
# ==================================================================

VERDICTS = ("Yes (clearly entered the conversation), Partly (present but "
            "weaker than intended), Barely (a handful of mentions), No "
            "(absent), Backfired (arrived and was turned against the "
            "brand), Not used (the brand did not make this argument but "
            "the audience did), Loud (not a brand decision at all: "
            "something the audience brought itself)")

PROMPT = """You are a strategist writing an internal debrief for a creative
team. Not a client deliverable. Dense, plain, no ceremony.

The reader cannot change the product. They can only change the story, so
every conclusion must be about messaging, narrative or execution, never
about specification.

Length discipline matters more than completeness. If a sentence does not
change what somebody does on Monday, cut it.

=====================================================================
GROUNDED - read from the videos. Rely on this.
=====================================================================
{brief}

=====================================================================
NUMBERS
=====================================================================
Theme mix per group (% of that group's comments):
{themes}

Key Message mentions, did each idea the video pushed appear in the comments:
{transfer}

Emotion distribution:
{emotion}

Sentiment distribution:
{sentiment}

Sample sizes:
{sizes}

=====================================================================
CANDIDATE QUOTES - verbatim. Quote ONLY from this list.
Each line: [group | theme | emotion | echoed: ideas | N likes] "text"
Use the labels to pick quotes that illustrate specific verdicts and themes.
Verdicts in the Key Message table and glosses under quotes must derive
from what these labeled comments show, not from assumptions.
=====================================================================
{quotes}

=====================================================================
STRUCTURE - follow exactly, in markdown
=====================================================================

# [Title stating the finding, not the topic. Under 8 words.]

*[One line: what this covers and the base. Under 25 words.]*

[[CHART:transfer]]

Then for EACH group:

## [Group name]

*[tagline if known] - [n] comments - [dominant emotion and share, one phrase]*

**Background**

[2 to 3 sentences. What it is, what it led with, the mechanic it used.
Write "unverified" beside anything from the BACKGROUND section.]

| Decision | Travelled? | How the audience handled it |
|---|---|---|

[One row per idea in the Key Message table for this group, plus one
final row for anything the audience raised loudly that the brand did not.
Verdict must be exactly one of: {verdicts}. Third column: under 15 words,
derived from what the labeled comments above actually show.]

**Talked about instead**

[[CHART:themes]]

[1 to 2 sentences from the theme mix. Name the largest themes and what
they mean.]

**Comments**

[2 to 3 quotes only, chosen from the candidate list to illustrate the
verdicts above, each as:]

> "[copied EXACTLY from the candidate list]"
>
> *[One line. Translate if not English, then say what it shows.]*

**So what**

[Exactly 2 bullets. Each names something the team can DO. Start with a
verb. Under 25 words each.]

---

After all groups:

## Across the groups

[One paragraph, 3 to 4 sentences. Patterns holding across more than one
group. Be specific about what kind of idea travelled and what kind did not.]

| Group | Led with | Audience told instead | Priority fix |
|---|---|---|---|

**The one thing to carry into the next brief**

[Two sentences. A claim, not a summary.]

## Read this before quoting it

[4 to 6 short lines, one per limitation, no elaboration. Cover: any group
under 100 comments is unreliable; low transfer means the idea did not
arrive rather than being rejected; emotion labels have no context so
sarcasm reads as anger; commenters are not buyers.]

=====================================================================
RULES
=====================================================================
- Use ONLY the numbers supplied. Never invent or estimate a figure.
- The [[CHART:transfer]] and [[CHART:themes]] tokens are replaced by
  code-generated charts. Do NOT invent numbers for them. Keep the tokens
  exactly as written.
- Quote ONLY from the candidate list, verbatim. Translation goes in the
  gloss line underneath, never inside the quote.
- Verdicts come from the closed vocabulary above. No other words.
- No em-dashes. No filler. Short declarative sentences.
- Do not restate the method or explain what a percentage is.
- Any group under 100 comments: say so in that group's header line.
- Write in {language}."""


def _is_true(value) -> bool:
    """
    Exact True check for a pt__ echo flag.

    pt__ columns hold Python bool, numpy.bool_, or pd.NA (rows whose video
    is not associated with that Key Message). `if value:` crashes on pd.NA
    ("boolean value of NA is ambiguous"); this reads NA/None/NaN as not
    echoed instead.
    """
    return False if pd.isna(value) else bool(value)


def _quotes(df, cap=120):
    """
    Build a labeled comment pool for the model.

    Per (group, theme): top-3 by likes + top-2 longest, deduped.
    Each line tagged: [group | theme | emotion | echoed: A, B | N likes] "text"
    Cap at ~120 lines total.
    """
    pt_cols = [c for c in df.columns if c.startswith("pt__")]

    picks = []
    for (group, theme), sub in df.groupby(["group", "theme"]):
        top_liked = sub.nlargest(3, "likes")
        top_long = (sub.assign(_len=sub["comment"].str.len())
                      .nlargest(2, "_len").drop(columns="_len"))
        combined = (pd.concat([top_liked, top_long])
                      .drop_duplicates(subset=["comment"]))
        for _, row in combined.iterrows():
            # Collect echoed point labels for this row.
            echoed = []
            for c in pt_cols:
                if _is_true(row.get(c)):
                    echoed.append(c[4:].replace("_", " "))
            echoed_str = (", ".join(echoed)) if echoed else "none"
            emotion = row.get("emotion", "")
            likes = int(row.get("likes", 0))
            text = str(row["comment"])[:300]
            picks.append(
                f'[{group} | {theme} | {emotion} | echoed: {echoed_str}'
                f' | {likes} likes] "{text}"')

    return "\n".join(picks[:cap])


def write(brief, themes, transfer, affect_result, df, cfg: PipelineConfig):
    emotion = affect_result["emotion"]
    sentiment = affect_result["sentiment"]
    return llm.ask(PROMPT.format(
        brief=brief[:9000],
        themes=themes.to_string(),
        transfer=(transfer.to_string(index=False) if not transfer.empty
                  else "(none measured)"),
        emotion=(emotion["table"].to_string() + "\n\nCaveat: "
                 + emotion["caveat"]),
        sentiment=(sentiment["table"].to_string() + "\n\nCaveat: "
                   + sentiment["caveat"]),
        sizes=df.groupby("group").size().to_string(),
        quotes=_quotes(df),
        verdicts=VERDICTS,
        language=cfg.REPORT_LANGUAGE),
        cfg, num_predict=4096)


# ==================================================================
# 2. RENDER
# ==================================================================

CSS = """
/* A working document, not a client deliverable: dense type, tight
   leading, colour only where it carries meaning. */
@page { size: A4; margin: 13mm 14mm; }
* { box-sizing: border-box; }
body { font-family: "Helvetica Neue", Helvetica, Arial, sans-serif;
       color: #1c1c1c; font-size: 8.8pt; line-height: 1.42; margin: 0; }
h1 { font-size: 15pt; margin: 0 0 3px 0; border-bottom: 2px solid #1c1c1c;
     padding-bottom: 5px; }
h1 + p { font-size: 8.6pt; color: #6b6660; font-style: italic;
         margin-bottom: 14px; }
h2 { font-size: 11.5pt; margin: 18px 0 1px 0; padding-top: 9px;
     border-top: 1px solid #1c1c1c; page-break-after: avoid; }
h2 + p em { color: #6b6660; font-size: 8pt; }
h3 { font-size: 9.5pt; margin: 12px 0 3px 0; page-break-after: avoid; }
p { margin: 0 0 6px 0; }
strong { font-weight: 600; }
hr { display: none; }
p.subhead { font-size: 7pt; letter-spacing: 1.2px; text-transform: uppercase;
            color: #8a857e; margin: 11px 0 3px 0; }
table { width: 100%; border-collapse: collapse; font-size: 7.9pt;
        margin: 5px 0 9px 0; page-break-inside: avoid; }
th { text-align: left; font-size: 6.6pt; letter-spacing: 0.9px;
     text-transform: uppercase; color: #8a857e; font-weight: 600;
     border-bottom: 1.2px solid #1c1c1c; padding: 3px 7px 3px 0; }
td { padding: 4.5px 7px 4.5px 0; border-bottom: 1px solid #eceae4;
     vertical-align: top; line-height: 1.32; }
.v-yes { color: #3f7047; font-weight: 700; }
.v-mid { color: #a8800f; font-weight: 700; }
.v-no  { color: #b3452f; font-weight: 700; }
blockquote { border-left: 2px solid #b3452f; padding-left: 9px; margin: 6px 0;
             page-break-inside: avoid; }
blockquote p { font-size: 8.4pt; margin: 0 0 1px 0; font-style: italic; }
blockquote em, blockquote p:last-child:not(:first-child) {
             font-style: normal; font-size: 7.4pt; color: #7a746c; }
.box { background: #f4f1ea; border-left: 2.5px solid #1c1c1c;
       padding: 7px 10px; margin: 8px 0; page-break-inside: avoid; }
.box p:last-child { margin-bottom: 0; }
.box h4 { font-size: 7pt; letter-spacing: 1.2px; text-transform: uppercase;
          margin: 0 0 4px 0; }
ul, ol { margin: 3px 0 8px 0; padding-left: 15px; }
li { margin-bottom: 3px; }
.chart { margin: 8px 0 12px 0; page-break-inside: avoid; }
.chart-title { font-size: 7.5pt; font-weight: 600; margin-bottom: 1px; }
.chart-sub { font-size: 6.8pt; color: #6b6660; margin-bottom: 5px; }
.bar { display: flex; align-items: center; margin-bottom: 3px; }
.bar-label { font-size: 7pt; color: #1c1c1c; width: 160px;
             min-width: 160px; padding-right: 6px; line-height: 1.2; }
.bar-track { flex: 1; background: #eceae4; height: 7px; border-radius: 2px; }
.bar-fill { height: 7px; background: #1c1c1c; border-radius: 2px; }
.bar-val { font-size: 6.6pt; color: #6b6660; width: 34px;
           min-width: 34px; text-align: right; padding-left: 5px; }
.keyvis { max-width: 180px; float: right; margin: 0 0 8px 12px;
          border-radius: 4px; }
"""

BANDS = {"yes": "v-yes", "loud": "v-yes", "partly": "v-mid",
         "barely": "v-mid", "not used": "v-mid", "no": "v-no",
         "backfired": "v-no"}

BOXED = ("For the creative team",
         "The one thing to carry into the next brief")


def _chart_html(rows, title, subtitle=""):
    """
    Horizontal CSS bar chart, ranked descending.

    rows: list of (label, value) where value is a float percentage.
    Returns an HTML string using .chart/.bar/.bar-fill/.bar-label/.bar-val.
    """
    if not rows:
        return ""
    rows_sorted = sorted(rows, key=lambda r: r[1], reverse=True)
    max_val = rows_sorted[0][1] if rows_sorted else 1.0
    if max_val == 0:
        max_val = 1.0

    bars = []
    for label, value in rows_sorted:
        pct = value / max_val * 100
        bars.append(
            f'<div class="bar">'
            f'<div class="bar-label">{label}</div>'
            f'<div class="bar-track">'
            f'<div class="bar-fill" style="width:{pct:.1f}%"></div>'
            f'</div>'
            f'<div class="bar-val">{value:.1f}%</div>'
            f'</div>')

    sub_html = (f'<div class="chart-sub">{subtitle}</div>'
                if subtitle else "")
    return (f'<div class="chart">'
            f'<div class="chart-title">{title}</div>'
            f'{sub_html}'
            + "".join(bars)
            + '</div>')


def _style(html):
    """Three small transforms on the markdown output."""
    # A paragraph that is only bold text is a section label.
    html = re.sub(r"<p><strong>([^<]{3,60})</strong></p>",
                  r'<p class="subhead"><strong>\1</strong></p>', html)

    # Named sections become tinted boxes. Match h3 only, never h4: h4 is
    # what this emits, so allowing it would nest a box inside itself.
    for trigger in BOXED:
        for pattern in (r"(<p[^>]*><strong>" + trigger + r"</strong></p>)"
                        r"((?:\s*<p>.*?</p>)+)",
                        r"(<h3[^>]*>" + trigger + r"</h3>)"
                        r"((?:\s*<p>.*?</p>)+)"):
            html = re.sub(
                pattern,
                lambda m: f'<div class="box"><h4>{trigger}</h4>'
                          f'{m.group(2)}</div>',
                html, flags=re.DOTALL | re.IGNORECASE)

    # Colour a table cell only when the whole cell is a verdict, so
    # ordinary prose containing "no" is untouched.
    def verdict(match):
        text = re.sub(r"<[^>]+>", "", match.group(1)).strip().lower()
        band = BANDS.get(text)
        return (f'<td><span class="{band}">{match.group(1)}</span></td>'
                if band else match.group(0))

    return re.sub(r"<td>(.*?)</td>", verdict, html, flags=re.DOTALL)


PDF_ENGINES = ("playwright", "weasyprint", "pdfkit")


def pdf_engine():
    """
    Return the name of the first available PDF engine, or None.

    Called before anything expensive runs, so a machine with no engine
    fails in a second rather than after five model calls.
    """
    from importlib.util import find_spec
    for name in PDF_ENGINES:
        if find_spec(name):
            return name
    return None


def _render_pdf(engine, html_path, pdf_path):
    if engine == "playwright":
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page.goto("file://" + os.path.abspath(html_path))
            page.pdf(path=pdf_path, format="A4", print_background=True,
                     margin={"top": "13mm", "bottom": "13mm",
                             "left": "14mm", "right": "14mm"})
            browser.close()

    elif engine == "weasyprint":
        from weasyprint import HTML
        HTML(filename=html_path).write_pdf(pdf_path)

    else:
        import pdfkit
        pdfkit.from_file(html_path, pdf_path, options={
            "page-size": "A4", "enable-local-file-access": None,
            "quiet": ""})


def _build_charts(df, transfer):
    """
    Pre-compute both chart HTML blocks for token replacement in render().

    Returns (transfer_html, themes_html).
    """
    # Transfer chart: label = "group - point", value = echoed_pct, sorted desc.
    if not transfer.empty:
        t_rows = [(f"{r.group} - {r.point}", float(r.echoed_pct))
                  for r in transfer.itertuples()]
        transfer_html = _chart_html(
            t_rows,
            title="Which ideas arrived",
            subtitle=("Share of each conversation that echoed the idea"
                      " the brand led with."))
    else:
        transfer_html = ""

    # Themes chart: overall frequency across the full corpus, sorted desc.
    theme_counts = df["theme"].value_counts(normalize=True) * 100
    th_rows = [(theme, float(pct)) for theme, pct in theme_counts.items()]
    themes_html = _chart_html(th_rows, title="What the audience talked about")

    return transfer_html, themes_html


def render(markdown_text, out_dir, cfg: PipelineConfig, debug_dir=None, _df=None, _transfer=None):
    """
    Write report.pdf.

    _df and _transfer are passed by run.py so chart tokens can be replaced.
    The HTML is a build artifact: goes to a temp file, or to debug/ when on.
    """
    import tempfile

    import markdown as md

    body = _style(md.markdown(markdown_text,
                              extensions=["tables", "sane_lists"]))

    # Inject key visuals after each ## {group} heading.
    key_visuals = cfg.KEY_VISUALS or {}
    for group, img_path in key_visuals.items():
        if not img_path or not os.path.isfile(img_path):
            continue
        ext = os.path.splitext(img_path)[1].lstrip(".").lower()
        if ext == "jpg":
            ext = "jpeg"
        try:
            with open(img_path, "rb") as fh:
                b64 = base64.b64encode(fh.read()).decode("ascii")
            img_tag = (f'<img class="keyvis" '
                       f'src="data:image/{ext};base64,{b64}">')
            # Match the rendered h2 for this group exactly.
            escaped = re.escape(group)
            body = re.sub(
                r'(<h2[^>]*>' + escaped + r'</h2>)',
                r'\1' + img_tag,
                body)
        except Exception:
            pass  # missing or unreadable -> skip silently

    # Replace [[CHART:transfer]] and [[CHART:themes]] tokens.
    # markdown wraps bare paragraphs, so the token arrives as <p>[[CHART:...]]</p>.
    if _df is not None and _transfer is not None:
        transfer_html, themes_html = _build_charts(_df, _transfer)
    else:
        transfer_html = themes_html = ""

    body = re.sub(r"<p>\[\[CHART:transfer\]\]</p>", transfer_html, body)
    body = re.sub(r"<p>\[\[CHART:themes\]\]</p>", themes_html, body)

    document = (f"<!DOCTYPE html><html><head><meta charset='utf-8'>"
                f"<style>{CSS}</style></head><body>{body}</body></html>")

    if debug_dir:
        for name, content in (("report.md", markdown_text),
                               ("report.html", document)):
            with open(os.path.join(debug_dir, name), "w",
                      encoding="utf-8") as handle:
                handle.write(content)

    handle = tempfile.NamedTemporaryFile("w", suffix=".html", delete=False,
                                         encoding="utf-8")
    handle.write(document)
    handle.close()

    pdf_path = os.path.join(out_dir, "report.pdf")
    errors = []
    try:
        for engine in PDF_ENGINES:
            try:
                _render_pdf(engine, handle.name, pdf_path)
                print(f"    report.pdf (via {engine})")
                return pdf_path
            except ImportError:
                continue
            except Exception as error:
                errors.append(f"{engine}: {str(error)[:90]}")
    finally:
        os.unlink(handle.name)

    raise RuntimeError(
        "Could not produce a PDF. Tried: "
        + ("; ".join(errors) if errors else "no engine installed")
        + "\nInstall one:  pip install playwright && playwright install "
          "chromium")


# ==================================================================
# 3. EXPORT
# ==================================================================

# comments.csv header, fixed order. key_message_<slug> columns are
# appended after this, one per pt__ column, in Key Message order.
COMMENTS_HEADER = ["video_id", "group", "comment", "likes", "language",
                   "theme", "sentiment", "sentiment_confidence",
                   "emotion", "emotion_confidence"]

# lang is the code identifier for the language column; see AGENTS.md
# terminology mapping.
_SOURCE_COL = {"language": "lang"}


def _group_order(df):
    """Groups in first-appearance order, for deterministic sort keys."""
    return df["group"].drop_duplicates().tolist() if "group" in df.columns else []


def _sort_group_first(frame, group_order, label_col, count_col):
    """
    Sort a rows DataFrame group-first (by first appearance), then by
    count descending, then by case-insensitive label. Shared ordering
    rule for every aggregate CSV in the contract.
    """
    rank = pd.Categorical(frame["group"], categories=group_order, ordered=True)
    label_lower = frame[label_col].astype(str).str.lower()
    order = pd.DataFrame({"rank": rank, "count": frame[count_col],
                          "label_lower": label_lower}, index=frame.index)
    order = order.sort_values(["rank", "count", "label_lower"],
                              ascending=[True, False, True], kind="stable")
    return frame.loc[order.index]


def _comments_csv(df, out_path):
    """
    Write comments.csv: one row per comment, the fixed header columns,
    plus one boolean key_message_<slug> column per pt__ column (Key
    Message order = df's pt__ column order, set by analyze.classify()'s
    point iteration). Missing source values are empty; NA pt__ cells
    (video not associated with that Key Message) are empty too - pandas
    writes both blank by default.

    Groups follow first appearance; within a group, likes descending.
    """
    # Build on a fresh positional RangeIndex: df's index may carry
    # duplicate labels (e.g. after pd.concat elsewhere in the pipeline),
    # and `.loc[dup_label]` returns every row sharing that label, which
    # would silently multiply rows during the sort step below.
    out = pd.DataFrame(index=pd.RangeIndex(len(df)))
    for col in COMMENTS_HEADER:
        source = _SOURCE_COL.get(col, col)
        out[col] = df[source].to_numpy() if source in df.columns else ""

    for col in [c for c in df.columns if c.startswith("pt__")]:
        out["key_message_" + col[4:]] = df[col].to_numpy()

    if not out.empty:
        group_order = _group_order(df)
        rank = pd.Categorical(out["group"], categories=group_order, ordered=True)
        likes = pd.to_numeric(out["likes"], errors="coerce")
        order = pd.DataFrame({"rank": rank, "likes": likes}).sort_values(
            ["rank", "likes"], ascending=[True, False], kind="stable")
        out = out.loc[order.index]

    out.to_csv(out_path, index=False, encoding="utf-8-sig",
               lineterminator="\n")
    print(f"    comments.csv: {len(out)} rows")


def _label_counts_csv(df, label_col, out_path):
    """
    Write group/label/count/percent/base_n csv from raw comment-level df.

    One row per observed non-null, non-empty label per group. base_n is
    the count of eligible labels for that column within the group.
    Groups follow first appearance; within a group, rows sort by count
    descending, then case-insensitive label. Empty/missing input still
    writes a header-only file.
    """
    cols = ["group", label_col, "count", "percent", "base_n"]
    if label_col not in df.columns:
        labeled = df.iloc[0:0]
    else:
        text = df[label_col].astype(str).str.strip()
        labeled = df[df[label_col].notna() & (text != "")]

    if labeled.empty:
        rows = pd.DataFrame(columns=cols)
    else:
        base_n = labeled.groupby("group")[label_col].transform("size")
        counted = (labeled.assign(base_n=base_n)
                   .groupby(["group", label_col, "base_n"], sort=False)
                   .size().reset_index(name="count"))
        counted["percent"] = (counted["count"] / counted["base_n"]
                              * 100).round(1)
        counted = _sort_group_first(counted, _group_order(df), label_col, "count")
        rows = counted[cols]
    rows.to_csv(out_path, index=False, encoding="utf-8-sig",
               lineterminator="\n")
    print(f"    {os.path.basename(out_path)}: {len(rows)} rows")


def _label_to_pt_col(df, label):
    """
    Recover the pt__ column analyze.classify() built for a Key Message
    label, from its slug convention (same lower/non-word-strip/40-char
    rule). Exact whenever the label did not collide with another label's
    truncated slug - the common case, since analyze.classify() only
    appends a numeric suffix on collision.

    # ponytail: report.export() is not handed analyze.classify()'s exact
    # columns dict, so a genuine truncation collision (two labels sharing
    # the same 40-char slug) can misroute one of them here. Upgrade: pass
    # columns through run.py's report.export() call if that ever bites.
    """
    base = "pt__" + re.sub(r"\W+", "_", label.lower())[:40]
    if base in df.columns:
        return base
    i = 2
    while f"{base}_{i}" in df.columns:
        return f"{base}_{i}"
    return None


def _key_messages_csv(df, transfer, out_path):
    """
    Write key-messages.csv: per (group, Key Message) mention count and
    sentiment split, recomputed from raw comment-level pt__ columns.

    transfer (analyze.summarise()'s second table) supplies the set of
    applicable (group, point) pairs - a pair appears there only when that
    group's video was shown the Key Message, which is exactly
    "applicable" here, zero-mention pairs included. Every number in the
    output is recounted from df rather than trusting transfer's already-
    rounded echoed_pct/n, per the counting-happens-in-Python rule. Rows
    are then sorted group-first (by first appearance in df), then by
    count descending, then case-insensitive key_message.
    """
    cols = ["group", "key_message", "count", "percent", "base_n",
            "positive_count", "positive_percent",
            "negative_count", "negative_percent", "sentiment_base_n"]
    if transfer.empty:
        pd.DataFrame(columns=cols).to_csv(out_path, index=False,
                                          encoding="utf-8-sig",
                                          lineterminator="\n")
        print(f"    {os.path.basename(out_path)}: 0 rows")
        return

    has_sentiment = "sentiment" in df.columns
    rows = []
    for r in transfer.itertuples():
        group, label = r.group, r.point
        col = _label_to_pt_col(df, label)
        sub = df[df["group"] == group]
        active = sub[sub[col].notna()] if col else sub.iloc[0:0]
        base_n = len(active)
        mentioned = active[active[col]] if col and base_n else active.iloc[0:0]
        count = len(mentioned)

        norm = (mentioned["sentiment"].dropna().str.strip().str.lower()
                if has_sentiment and count else pd.Series(dtype=object))
        sent = norm[norm.isin(("positive", "negative", "neutral"))]
        sentiment_base_n = len(sent)
        positive_count = int((sent == "positive").sum())
        negative_count = int((sent == "negative").sum())

        rows.append({
            "group": group,
            "key_message": label,
            "count": count,
            "percent": round(count / base_n * 100, 1) if base_n else 0.0,
            "base_n": base_n,
            "positive_count": positive_count,
            "positive_percent": (round(positive_count / sentiment_base_n * 100, 1)
                                 if sentiment_base_n else 0.0),
            "negative_count": negative_count,
            "negative_percent": (round(negative_count / sentiment_base_n * 100, 1)
                                 if sentiment_base_n else 0.0),
            "sentiment_base_n": sentiment_base_n,
        })

    out = pd.DataFrame(rows, columns=cols)
    out = _sort_group_first(out, _group_order(df), "key_message", "count")
    out.to_csv(out_path, index=False, encoding="utf-8-sig",
              lineterminator="\n")
    print(f"    {os.path.basename(out_path)}: {len(out)} rows")


def export(df, themes, transfer, affect_result, meta_df, out_dir):
    """
    Write the five CSVs in the CSV contract (CHANGELOG.md, Shared
    contracts > CSVs). report.pdf remains render()'s output.

    comments.csv      every comment, exact schema, for reading
    key-messages.csv  (group, key_message, count, percent, base_n,
                       positive/negative count+percent, sentiment_base_n)
    themes.csv     (group, theme, count, percent, base_n) from raw comments
    sentiment.csv  (group, sentiment, count, percent, base_n) from raw comments
    emotions.csv   (group, emotion, count, percent, base_n) from raw comments

    themes and meta_df are accepted for call-site stability (run.py,
    adapter.py) but are not read here: themes.csv is recomputed from raw
    comments, not from the crosstab summary.
    """
    _comments_csv(df, os.path.join(out_dir, "comments.csv"))
    _key_messages_csv(df, transfer, os.path.join(out_dir, "key-messages.csv"))

    # themes.csv, sentiment.csv, emotions.csv: one row per observed
    # (group, label), from raw comments.
    _label_counts_csv(df, "theme", os.path.join(out_dir, "themes.csv"))
    _label_counts_csv(df, "sentiment", os.path.join(out_dir, "sentiment.csv"))
    _label_counts_csv(df, "emotion", os.path.join(out_dir, "emotions.csv"))
