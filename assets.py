"""assets.py - text extraction from uploaded files and article fetching.

Both public functions are defensive: they never raise on bad input and
always return the documented type, so a run can continue past bad assets.
"""

import logging
import os
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_TEXT_EXTENSIONS = {".pdf", ".docx", ".pptx"}
_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}


def extract_upload(file_path: str) -> str:
    """Extract text from an uploaded file. Returns "" on images or unknown types.

    Supported:
      .pdf   - pypdf; returns "" and logs a warning on encrypted/empty PDFs.
      .docx  - python-docx; joins paragraph text.
      .pptx  - python-pptx; joins shape text across slides (no speaker notes).
      .png/.jpg/.jpeg/.webp - returns "" (OCR deferred to 12.1).
      other  - returns "".

    Never raises.
    """
    ext = os.path.splitext(file_path)[1].lower()

    if ext in _IMAGE_EXTENSIONS:
        return ""

    if ext not in _TEXT_EXTENSIONS:
        return ""

    try:
        if ext == ".pdf":
            return _extract_pdf(file_path)
        if ext == ".docx":
            return _extract_docx(file_path)
        if ext == ".pptx":
            return _extract_pptx(file_path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("extract_upload failed for %s: %s", file_path, exc)
        return ""

    return ""


def _extract_pdf(file_path: str) -> str:
    import pypdf  # local import keeps module importable without pypdf installed

    try:
        reader = pypdf.PdfReader(file_path)
    except pypdf.errors.FileNotDecryptedError:
        logger.warning("PDF is encrypted, cannot extract text: %s", file_path)
        return ""

    pages = []
    for page in reader.pages:
        try:
            text = page.extract_text() or ""
            pages.append(text)
        except Exception as exc:  # noqa: BLE001
            logger.warning("PDF page extraction failed in %s: %s", file_path, exc)

    result = "\n".join(pages).strip()
    if not result:
        logger.warning("PDF yielded no text (possibly scanned): %s", file_path)
    return result


def _extract_docx(file_path: str) -> str:
    import docx as python_docx

    doc = python_docx.Document(file_path)
    return "\n".join(p.text for p in doc.paragraphs if p.text)


def _extract_pptx(file_path: str) -> str:
    from pptx import Presentation

    prs = Presentation(file_path)
    texts = []
    for slide in prs.slides:
        for shape in slide.shapes:
            if shape.has_text_frame:
                for para in shape.text_frame.paragraphs:
                    line = "".join(run.text for run in para.runs)
                    if line:
                        texts.append(line)
    return "\n".join(texts)


def fetch_article(url: str) -> dict:
    """Fetch and extract readable text from a URL.

    Returns a dict with keys:
      title       (str)  - page <title> or "" on failure
      text        (str)  - extracted body text, capped at 20 000 chars; "" on failure
      retrieved_at (str) - ISO-8601 UTC timestamp of the attempt

    Never raises. On timeout or any network/parse error the run continues
    with empty text (see Risks table: "Article fetch hangs").
    """
    import httpx
    from bs4 import BeautifulSoup

    retrieved_at = datetime.now(timezone.utc).isoformat()
    empty = {"title": "", "text": "", "retrieved_at": retrieved_at}

    try:
        response = httpx.get(url, timeout=15.0, follow_redirects=True)
        response.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        logger.warning("fetch_article failed for %s: %s", url, exc)
        return empty

    try:
        soup = BeautifulSoup(response.text, "html.parser")

        # Remove noise before extracting text
        for tag in soup(["script", "style"]):
            tag.decompose()

        title = (soup.title.string or "").strip() if soup.title else ""
        text = soup.get_text(separator="\n", strip=True)[:20_000]

        return {"title": title, "text": text, "retrieved_at": retrieved_at}
    except Exception as exc:  # noqa: BLE001
        logger.warning("fetch_article parse failed for %s: %s", url, exc)
        return empty
