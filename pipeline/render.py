"""
Stage 8 - turn the markdown report into a styled document.

Always writes a self-contained HTML file (charts embedded as base64, so
it survives being emailed as a single attachment). Then tries to convert
that to PDF with whichever engine is installed, in order of output
quality:

    1. playwright   - Chromium. Best CSS support, most reliable.
                      pip install playwright && playwright install chromium
    2. weasyprint   - pure-ish Python. Good, but needs GTK on Windows.
    3. pdfkit       - wrapper around a wkhtmltopdf binary you install.

If none is available the HTML is still written, and the console tells you
to open it and print to PDF from the browser, which gives an identical
result for zero setup. The pipeline never fails because of this stage.

The styling is deliberately plain: serif body, sans headings, colour used
only to carry meaning. It is designed to be readable printed and to
survive being pasted into a deck.
"""

import base64
import os
import re

CSS = """
/* Deliberately plain. This is a working document, not a client
   deliverable: dense type, tight leading, colour only where it carries
   meaning. It should photocopy and paste into a deck without fuss. */

@page { size: A4; margin: 13mm 14mm; }
* { box-sizing: border-box; }
body { font-family: "Helvetica Neue", Helvetica, Arial, sans-serif;
       color: #1c1c1c; font-size: 8.8pt; line-height: 1.42; margin: 0; }

h1 { font-size: 15pt; margin: 0 0 3px 0; letter-spacing: -0.2px;
     border-bottom: 2px solid #1c1c1c; padding-bottom: 5px; }
h1 + p { font-size: 8.6pt; color: #6b6660; font-style: italic;
         margin-bottom: 14px; }
h2 { font-size: 11.5pt; margin: 18px 0 1px 0; padding-top: 9px;
     border-top: 1px solid #1c1c1c; page-break-after: avoid; }
h2 + p em { color: #6b6660; font-size: 8pt; }
h3 { font-size: 9.5pt; margin: 12px 0 3px 0; page-break-after: avoid; }

p { margin: 0 0 6px 0; }
strong { font-weight: 600; }
hr { display: none; }

/* Bold-only paragraphs are section labels: Background, So what, etc. */
p.subhead { font-size: 7pt; letter-spacing: 1.2px; text-transform: uppercase;
            color: #8a857e; margin: 11px 0 3px 0; }
p.subhead strong { font-weight: 600; }

table { width: 100%; border-collapse: collapse; font-size: 7.9pt;
        margin: 5px 0 9px 0; page-break-inside: avoid; }
th { text-align: left; font-size: 6.6pt; letter-spacing: 0.9px;
     text-transform: uppercase; color: #8a857e; font-weight: 600;
     border-bottom: 1.2px solid #1c1c1c; padding: 3px 7px 3px 0;
     vertical-align: bottom; }
td { padding: 4.5px 7px 4.5px 0; border-bottom: 1px solid #eceae4;
     vertical-align: top; line-height: 1.32; }

.v-yes { color: #3f7047; font-weight: 700; }
.v-mid { color: #a8800f; font-weight: 700; }
.v-no  { color: #b3452f; font-weight: 700; }

blockquote { border-left: 2px solid #b3452f; padding: 0 0 0 9px;
             margin: 6px 0; page-break-inside: avoid; }
blockquote p { font-size: 8.4pt; color: #2a2620; margin: 0 0 1px 0;
               font-style: italic; }
blockquote p:last-child:not(:first-child) {
             font-style: normal; font-size: 7.4pt; color: #7a746c; }
blockquote em { font-style: normal; font-size: 7.4pt; color: #7a746c; }

.box { background: #f4f1ea; border-left: 2.5px solid #1c1c1c;
       padding: 7px 10px; margin: 8px 0; page-break-inside: avoid; }
.box p:last-child { margin-bottom: 0; }
.box h4 { font-size: 7pt; letter-spacing: 1.2px; text-transform: uppercase;
          color: #1c1c1c; margin: 0 0 4px 0; }

ul, ol { margin: 3px 0 8px 0; padding-left: 15px; }
li { margin-bottom: 3px; }

img { max-width: 100%; margin: 6px 0; }
code { background: #f0ede7; padding: 1px 3px; font-size: 7.6pt; }
"""

# Words the synthesis prompt is allowed to use as a verdict, and the
# colour band each falls into.
VERDICT_BANDS = {
    "yes": "v-yes", "loud": "v-yes",
    "partly": "v-mid", "barely": "v-mid", "not used": "v-mid",
    "no": "v-no", "backfired": "v-no",
}


def _embed_images(html, base_dir):
    """
    Inline every local <img> as base64 so the HTML is one portable file.
    """
    def replace(match):
        whole, src = match.group(0), match.group(1)
        if src.startswith(("http://", "https://", "data:")):
            return whole
        path = os.path.join(base_dir, src)
        if not os.path.exists(path):
            return ""          # chart missing: drop the tag, keep going
        with open(path, "rb") as handle:
            encoded = base64.b64encode(handle.read()).decode("ascii")
        return whole.replace(f'src="{src}"',
                             f'src="data:image/png;base64,{encoded}"')

    # src may appear after alt, so match the whole tag and swap inside it.
    return re.sub(r'<img\b[^>]*?src="([^"]+)"[^>]*>', replace, html)


def _colour_verdicts(html):
    """
    Wrap verdict words in table cells with a colour class.
    Only fires on a cell whose entire content is a verdict, so ordinary
    prose containing the word "no" is untouched.
    """
    def replace(match):
        inner = match.group(1)
        stripped = re.sub(r"<[^>]+>", "", inner).strip().lower()
        band = VERDICT_BANDS.get(stripped)
        if band:
            return f'<td><span class="{band}">{inner}</span></td>'
        return match.group(0)

    return re.sub(r"<td>(.*?)</td>", replace, html, flags=re.DOTALL)


def _boxify(html):
    """
    Turn the "For the creative team" and "The one thing..." sections into
    tinted boxes. The synthesis template always emits these as a bold
    paragraph or h3 followed by prose.
    """
    triggers = [
        r"For the creative team",
        r"The one thing to carry into the next brief",
    ]
    for trigger in triggers:
        # Bold-paragraph form
        pattern = (r"(<p[^>]*><strong>" + trigger +
                   r"</strong></p>)((?:\s*<p>.*?</p>)+)")
        html = re.sub(
            pattern,
            lambda m: ('<div class="box"><h4>'
                       + re.sub(r"<[^>]+>", "", m.group(1))
                       + "</h4>" + m.group(2) + "</div>"),
            html, flags=re.DOTALL | re.IGNORECASE)
        # Heading form. Match h3 only: h4 is what this function emits,
        # so allowing it here would re-match our own output and nest the
        # box inside itself.
        pattern = (r"(<h3[^>]*>" + trigger +
                   r"</h3>)((?:\s*<p>.*?</p>)+)")
        html = re.sub(
            pattern,
            lambda m: ('<div class="box"><h4>'
                       + re.sub(r"<[^>]+>", "", m.group(1))
                       + "</h4>" + m.group(2) + "</div>"),
            html, flags=re.DOTALL | re.IGNORECASE)
    return html


def _subheads(html):
    """A paragraph that is nothing but bold text is a sub-header."""
    return re.sub(r"<p><strong>([^<]{3,60})</strong></p>",
                  r'<p class="subhead"><strong>\1</strong></p>', html)


def markdown_to_html(markdown_text, base_dir, title="Reception Report"):
    try:
        import markdown as md
    except ImportError:
        raise RuntimeError(
            "The 'markdown' package is required. pip install markdown")

    body = md.markdown(markdown_text,
                       extensions=["tables", "fenced_code", "sane_lists"])
    body = _subheads(body)
    body = _boxify(body)
    body = _colour_verdicts(body)
    body = _embed_images(body, base_dir)

    return (f"<!DOCTYPE html><html><head><meta charset='utf-8'>"
            f"<title>{title}</title><style>{CSS}</style></head>"
            f"<body>{body}</body></html>")


def _try_playwright(html_path, pdf_path):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto("file://" + os.path.abspath(html_path))
        page.pdf(path=pdf_path, format="A4", print_background=True,
                 margin={"top": "14mm", "bottom": "14mm",
                         "left": "15mm", "right": "15mm"})
        browser.close()
    return True


def _try_weasyprint(html_path, pdf_path):
    from weasyprint import HTML
    HTML(filename=html_path).write_pdf(pdf_path)
    return True


def _try_pdfkit(html_path, pdf_path):
    import pdfkit
    pdfkit.from_file(html_path, pdf_path, options={
        "page-size": "A4", "margin-top": "14mm", "margin-bottom": "14mm",
        "margin-left": "15mm", "margin-right": "15mm",
        "enable-local-file-access": None, "quiet": ""})
    return True


def run(markdown_text, output_dir, stem="06_report"):
    """
    Write the HTML, then try each PDF engine in turn.
    Returns (html_path, pdf_path_or_None).
    """
    html_path = os.path.join(output_dir, stem + ".html")
    pdf_path = os.path.join(output_dir, stem + ".pdf")

    html = markdown_to_html(markdown_text, output_dir)
    with open(html_path, "w", encoding="utf-8") as handle:
        handle.write(html)
    print(f"    html: {os.path.basename(html_path)}")

    for name, engine in (("playwright", _try_playwright),
                         ("weasyprint", _try_weasyprint),
                         ("pdfkit", _try_pdfkit)):
        try:
            engine(html_path, pdf_path)
            print(f"    pdf:  {os.path.basename(pdf_path)} (via {name})")
            return html_path, pdf_path
        except ImportError:
            continue
        except Exception as error:
            print(f"    {name} failed: {str(error)[:90]}")
            continue

    print("    No PDF engine available. The HTML is complete and styled.")
    print("    Open it in a browser and print to PDF for an identical "
          "result, or:")
    print("      pip install playwright && playwright install chromium")
    return html_path, None