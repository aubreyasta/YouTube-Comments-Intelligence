"""
Produce the three deliverables.

    report.pdf          the debrief
    comments.csv        every comment, labelled, for digging through by hand
    summary.csv         every number in tidy long format, for building charts
    chart_transfer.csv  signal-transfer bars, for Google Slides
    chart_themes.csv    theme-frequency bars, for Google Slides

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

Signal transfer, did each idea the video pushed appear in the comments:
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
Verdicts in the signal-transfer table and glosses under quotes must derive
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

[One row per idea in the signal transfer table for this group, plus one
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
                if row.get(c):
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
        cfg)


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

COLUMNS = ["group", "kind", "video_id", "comment", "theme", "echoed_ideas",
           "emotion", "emotion_confidence",
           "sentiment", "sentiment_confidence",
           "likes", "is_reply", "lang",
           "n_words", "is_question", "mentions_price", "mentions_competitor",
           "published_at"]


def export(df, themes, transfer, affect_result, meta_df, out_dir):
    """
    comments.csv   for reading
    summary.csv    tidy long format for charting
    chart_transfer.csv  (idea, group, percent, n) sorted desc - Google Slides
    chart_themes.csv    (theme, percent, n) sorted desc - Google Slides
    """
    out = df.copy()

    # Collapse the sparse pt__ booleans into one readable column.
    signals = [c for c in out.columns if c.startswith("pt__")]
    if signals:
        labels = {c: c[4:].replace("_", " ") for c in signals}
        out["echoed_ideas"] = out[signals].apply(
            lambda row: "; ".join(labels[c] for c in signals if row[c]),
            axis=1)

    keep = [c for c in COLUMNS if c in out.columns]
    comments_path = os.path.join(out_dir, "comments.csv")
    (out[keep].sort_values(["group", "likes"], ascending=[True, False])
     .to_csv(comments_path, index=False, encoding="utf-8-sig"))
    print(f"    comments.csv: {len(out)} rows")

    # Tidy long format: one row per number.
    sizes = df.groupby("group").size().to_dict()
    rows = [{"group": g, "metric": "base", "label": "comments analysed",
             "value": n, "unit": "count", "n": n} for g, n in sizes.items()]

    counts = df.groupby("video_id").size().to_dict()
    rows += [{"group": r["group"], "metric": "video",
              "label": f"https://www.youtube.com/watch?v={r['video_id']}",
              "value": counts.get(r["video_id"], 0), "unit": "count",
              "n": counts.get(r["video_id"], 0)}
             for _, r in meta_df.iterrows()]

    for table, metric in ((themes, "theme"),
                          (affect_result["emotion"]["table"], "emotion"),
                          (affect_result["sentiment"]["table"], "sentiment")):
        if table.empty:
            continue
        for group in table.index:
            for label in table.columns:
                pct = float(table.loc[group, label])
                rows.append({"group": group, "metric": metric,
                             "label": label, "value": round(pct, 1),
                             "unit": "percent",
                             "n": int(round(pct / 100 * sizes.get(group, 0)))})

    if not transfer.empty:
        rows += [{"group": r.group, "metric": "signal_transfer",
                  "label": r.point, "value": r.echoed_pct,
                  "unit": "percent", "n": r.n}
                 for r in transfer.itertuples()]

    summary_path = os.path.join(out_dir, "summary.csv")
    frame = pd.DataFrame(rows, columns=["group", "metric", "label", "value",
                                        "unit", "n"])
    frame.to_csv(summary_path, index=False, encoding="utf-8-sig")
    print(f"    summary.csv: {len(frame)} rows "
          f"{frame['metric'].value_counts().to_dict()}")

    # chart_transfer.csv: one row per (group, point), sorted by percent desc.
    if not transfer.empty:
        ct = transfer[["point", "group", "echoed_pct", "n"]].copy()
        ct.columns = ["idea", "group", "percent", "n"]
        ct = ct.sort_values("percent", ascending=False)
        ct.to_csv(os.path.join(out_dir, "chart_transfer.csv"),
                  index=False, encoding="utf-8-sig")
        print(f"    chart_transfer.csv: {len(ct)} rows")
    else:
        pd.DataFrame(columns=["idea", "group", "percent", "n"]).to_csv(
            os.path.join(out_dir, "chart_transfer.csv"),
            index=False, encoding="utf-8-sig")

    # chart_themes.csv: one row per theme, sorted by percent desc.
    total = len(df)
    theme_vc = df["theme"].value_counts()
    ct2 = pd.DataFrame({
        "theme": theme_vc.index,
        "percent": (theme_vc.values / total * 100).round(1),
        "n": theme_vc.values,
    }).sort_values("percent", ascending=False)
    ct2.to_csv(os.path.join(out_dir, "chart_themes.csv"),
               index=False, encoding="utf-8-sig")
    print(f"    chart_themes.csv: {len(ct2)} rows")
