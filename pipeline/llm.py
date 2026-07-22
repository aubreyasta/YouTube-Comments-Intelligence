"""
One thin wrapper around the Gemini API, used by every AI stage.

Kept in its own file so that swapping providers later means editing
one function instead of four. Anything with an OpenAI-compatible
endpoint (Groq, Mistral, OpenRouter) can be dropped in here.
"""

import json
import re
import time

import config as config

_client = None


def _get_client():
    global _client
    if _client is None:
        from google import genai
        _client = genai.Client(api_key=config.GEMINI_API_KEY)
    return _client


def ask(prompt, model=None, retries=4):
    """Send a prompt, return the text. Retries on rate limits."""
    model = model or config.MODEL_CHEAP
    client = _get_client()

    for attempt in range(retries):
        try:
            response = client.models.generate_content(
                model=model, contents=prompt)
            return response.text
        except Exception as error:
            message = str(error)
            # 429 = too many requests. Free tiers are tight, so back off
            # and try again rather than crashing the whole run.
            if "429" in message or "RESOURCE_EXHAUSTED" in message:
                wait = 2 ** attempt * 5
                print(f"      rate limited, waiting {wait}s")
                time.sleep(wait)
                continue
            raise
    raise RuntimeError("Gave up after repeated rate limits")


def ask_grounded(prompt, model=None, retries=3):
    """
    Same as ask(), but with Google Search grounding switched on so the
    model can look things up and return sources.

    Grounding is not available on every model or every tier, and it is
    metered separately from ordinary requests. Callers should catch the
    exception and fall back to ask().
    """
    from google.genai import types
    model = model or config.MODEL_CHEAP
    client = _get_client()

    tools = [types.Tool(google_search=types.GoogleSearch())]
    settings = types.GenerateContentConfig(tools=tools)

    for attempt in range(retries):
        try:
            response = client.models.generate_content(
                model=model, contents=prompt, config=settings)
            text = response.text
            # Append whatever sources the grounding metadata carries.
            sources = []
            for candidate in getattr(response, "candidates", []) or []:
                meta = getattr(candidate, "grounding_metadata", None)
                for chunk in getattr(meta, "grounding_chunks", []) or []:
                    web = getattr(chunk, "web", None)
                    if web is not None and getattr(web, "uri", None):
                        sources.append(f"- {web.title or web.uri}: {web.uri}")
            if sources:
                unique = list(dict.fromkeys(sources))
                text += "\n\n**Sources consulted:**\n" + "\n".join(unique)
            return text
        except Exception as error:
            if "429" in str(error) or "RESOURCE_EXHAUSTED" in str(error):
                wait = 2 ** attempt * 5
                print(f"      rate limited, waiting {wait}s")
                time.sleep(wait)
                continue
            raise
    raise RuntimeError("Gave up after repeated rate limits")


def ask_json(prompt, model=None):
    """Same, but insists on JSON back and parses it."""
    text = ask(prompt + "\n\nReturn ONLY valid JSON. No markdown fences, "
                        "no commentary before or after.", model)
    # Models sometimes wrap JSON in ```json fences anyway.
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(),
                  flags=re.MULTILINE).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Last resort: grab the outermost {...} or [...] block.
        match = re.search(r"[\{\[].*[\}\]]", text, re.DOTALL)
        if match:
            return json.loads(match.group(0))
        raise