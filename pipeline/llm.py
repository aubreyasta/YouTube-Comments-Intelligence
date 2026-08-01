"""
All model access, in one place.

Swapping provider means editing this file and nothing else. Anything with
an OpenAI-compatible endpoint drops in here.
"""

from __future__ import annotations

import json
import re
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pipeline.config_types import PipelineConfig

# ponytail: per-call client construction is fine for most workloads; this
# dict cache avoids rebuilding the client on every call. Remove if profiling
# shows no overhead, or replace with an LRU if multiple keys rotate.
_clients: dict[str, object] = {}


def _get_client(cfg: PipelineConfig):
    from google import genai
    key = cfg.GEMINI_API_KEY
    if key not in _clients:
        _clients[key] = genai.Client(api_key=key)
    return _clients[key]


def ask(prompt, cfg: PipelineConfig, grounded=False, retries=4, images=None):
    """
    Send a prompt, return the text.

    grounded=True switches on Google Search so the model can look things
    up and return sources. Grounding is metered separately and is not on
    every tier, so it degrades to a plain call rather than failing.

    images, if provided, is a list[tuple[bytes, str]] of (data, mime_type).
    """
    model = cfg.MODEL
    client = _get_client(cfg)
    settings = None

    if grounded:
        try:
            from google.genai import types
            settings = types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())])
        except Exception:
            grounded = False

    if images:
        from google.genai import types
        contents = [prompt] + [
            types.Part.from_bytes(data=b, mime_type=m) for b, m in images
        ]
    else:
        contents = prompt

    for attempt in range(retries):
        try:
            response = client.models.generate_content(
                model=model, contents=contents, config=settings)
            return response.text + _sources(response)
        except Exception as error:
            message = str(error)
            if grounded and ("tool" in message.lower()
                             or "search" in message.lower()):
                print("      grounding unavailable, retrying without it")
                grounded, settings = False, None
                continue
            # 429: rate limit. Back off rather than crashing.
            if "429" in message or "RESOURCE_EXHAUSTED" in message:
                wait = 2 ** attempt * 5
                print(f"      rate limited, waiting {wait}s")
                time.sleep(wait)
                continue
            raise
    raise RuntimeError("Gave up after repeated rate limits")


def _sources(response):
    """Append any URLs the grounding metadata carried."""
    urls = []
    for candidate in getattr(response, "candidates", []) or []:
        meta = getattr(candidate, "grounding_metadata", None)
        for chunk in getattr(meta, "grounding_chunks", []) or []:
            web = getattr(chunk, "web", None)
            if web is not None and getattr(web, "uri", None):
                urls.append(f"- {web.title or web.uri}: {web.uri}")
    if not urls:
        return ""
    return "\n\n**Sources consulted:**\n" + "\n".join(dict.fromkeys(urls))


def _repair(text):
    """Fix the three ways models usually break JSON."""
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(),
                  flags=re.MULTILINE).strip()
    text = re.sub(r",\s*([}\]])", r"\1", text)          # trailing commas
    # Literal newlines inside string values, the usual culprit.
    text = re.sub(r'"[^"\\]*(?:\\.[^"\\]*)*"',
                  lambda m: m.group(0).replace("\n", "\\n").replace("\r", ""),
                  text, flags=re.DOTALL)
    return text


def ask_json(prompt, cfg: PipelineConfig, retries=3, grounded=False,
             images=None):
    """
    Ask for JSON and parse it. On a parse failure the broken output goes
    back to the model to be fixed, which succeeds far more often than
    re-asking from scratch.

    grounded=True is forwarded to ask() to enable Google Search on the call.
    Degrades gracefully to a plain call if the tier does not support it.
    """
    raw = ask(prompt + "\n\nReturn ONLY valid JSON. No markdown fences, no "
                       "commentary. Keep every string on a single line.",
              cfg, grounded=grounded, images=images)

    for attempt in range(retries):
        text = _repair(raw)
        for candidate in (text, _outermost(text)):
            if candidate:
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError:
                    pass
        if attempt == retries - 1:
            raise ValueError(f"Could not parse JSON.\nGot: {raw[:400]}")
        print(f"      JSON parse failed, asking model to fix it")
        raw = ask("This should be valid JSON but is not. Return only the "
                  "corrected JSON.\n\n" + raw[:6000], cfg)


def _outermost(text):
    match = re.search(r"[\{\[].*[\}\]]", text, re.DOTALL)
    return match.group(0) if match else None


def classify_batch(prompt, theme_names, point_labels, cfg: PipelineConfig):
    """
    Classify a batch of comments against a fixed theme enum and a set of
    brief points (signal transfer).

    Returns a list of dicts:
        [{"index": int, "theme": str, "echoed": [str, ...]}, ...]

    "theme" is one of theme_names or "Other".
    "echoed" is a subset of point_labels (may be empty).

    Tries enum-constrained structured output first. On any exception
    mentioning schema/response_schema/tool, degrades to ask_json so the
    prompt text itself constrains the labels.
    """
    model = cfg.MODEL
    client = _get_client(cfg)
    all_themes = list(theme_names) + ["Other"]

    # Build the response schema: array of {index, theme, echoed}.
    schema = {
        "type": "ARRAY",
        "items": {
            "type": "OBJECT",
            "properties": {
                "index": {"type": "INTEGER"},
                "theme": {"type": "STRING", "enum": all_themes},
                "echoed": {
                    "type": "ARRAY",
                    "items": {"type": "STRING", "enum": list(point_labels)}
                    if point_labels else {"type": "STRING"},
                },
            },
            "required": ["index", "theme", "echoed"],
        },
    }

    for attempt in range(4):
        try:
            from google.genai import types
            gcfg = types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=schema)
            response = client.models.generate_content(
                model=model, contents=prompt, config=gcfg)
            return json.loads(response.text)
        except Exception as error:
            message = str(error).lower()
            if "429" in message or "resource_exhausted" in message:
                wait = 2 ** attempt * 5
                print(f"      rate limited, waiting {wait}s")
                time.sleep(wait)
                continue
            # Schema not supported on this endpoint/tier - degrade gracefully.
            if any(k in message for k in ("schema", "response_schema", "tool")):
                print("      structured output unavailable, falling back to ask_json")
                return ask_json(prompt, cfg)
            raise

    raise RuntimeError("Gave up after repeated rate limits")
