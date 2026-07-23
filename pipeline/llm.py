"""
All model access, in one place.

Swapping provider means editing this file and nothing else. Anything with
an OpenAI-compatible endpoint drops in here.
"""

import json
import re
import time

import config

_client = None


def _get_client():
    global _client
    if _client is None:
        from google import genai
        _client = genai.Client(api_key=config.GEMINI_API_KEY)
    return _client


def ask(prompt, model=None, grounded=False, retries=4):
    """
    Send a prompt, return the text.

    grounded=True switches on Google Search so the model can look things
    up and return sources. Grounding is metered separately and is not on
    every tier, so it degrades to a plain call rather than failing.
    """
    model = model or config.MODEL_CHEAP
    client = _get_client()
    settings = None

    if grounded:
        try:
            from google.genai import types
            settings = types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())])
        except Exception:
            grounded = False

    for attempt in range(retries):
        try:
            response = client.models.generate_content(
                model=model, contents=prompt, config=settings)
            return response.text + _sources(response)
        except Exception as error:
            message = str(error)
            if grounded and ("tool" in message.lower()
                             or "search" in message.lower()):
                print("      grounding unavailable, retrying without it")
                grounded, settings = False, None
                continue
            # 429: free tiers are tight. Back off rather than crashing.
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


def ask_json(prompt, model=None, retries=3):
    """
    Ask for JSON and parse it. On a parse failure the broken output goes
    back to the model to be fixed, which succeeds far more often than
    re-asking from scratch.
    """
    raw = ask(prompt + "\n\nReturn ONLY valid JSON. No markdown fences, no "
                       "commentary. Keep every string on a single line.",
              model)

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
                  "corrected JSON.\n\n" + raw[:6000], model)


def _outermost(text):
    match = re.search(r"[\{\[].*[\}\]]", text, re.DOTALL)
    return match.group(0) if match else None