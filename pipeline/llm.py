"""
One thin wrapper around the Gemini API, used by every AI stage.

Kept in its own file so that swapping providers later means editing
one function instead of four. Anything with an OpenAI-compatible
endpoint (Groq, Mistral, OpenRouter) can be dropped in here.
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


def _clean_json_text(text):
    """
    Best-effort cleanup of common ways models break JSON.
    Applied before every parse attempt.
    """
    # Strip markdown fences
    text = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.MULTILINE)
    text = re.sub(r"\s*```$", "", text.strip(), flags=re.MULTILINE)
    text = text.strip()

    # Trailing commas before ] or } (technically illegal JSON)
    text = re.sub(r",\s*([}\]])", r"\1", text)

    # Unescaped newlines inside string values — the most common cause of
    # "Expecting ',' delimiter" errors. Replace literal \n inside quotes.
    def fix_newlines(m):
        return m.group(0).replace("\n", "\\n").replace("\r", "")
    text = re.sub(r'"[^"\\]*(?:\\.[^"\\]*)*"', fix_newlines, text,
                  flags=re.DOTALL)

    return text


def ask_json(prompt, model=None, retries=3):
    """
    Send a prompt and parse the response as JSON.
    Retries up to `retries` times; on each failure after the first it
    sends the broken output back to the model and asks it to fix it.
    """
    suffix = ("\n\nReturn ONLY valid JSON. No markdown fences, "
              "no commentary before or after. All string values must be "
              "on a single line — no literal newlines inside strings.")

    raw = ask(prompt + suffix, model)

    for attempt in range(retries):
        text = _clean_json_text(raw)
        # Try the whole response first
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        # Try extracting the outermost {...} or [...] block
        match = re.search(r"[\{\[].*[\}\]]", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass

        if attempt < retries - 1:
            print(f"      JSON parse failed (attempt {attempt + 1}), "
                  f"asking model to fix it")
            fix_prompt = (
                "The following text was supposed to be valid JSON but is "
                "not. Fix it so it parses correctly. Return ONLY the "
                "corrected JSON, nothing else.\n\n" + raw[:6000])
            raw = ask(fix_prompt, model)
        else:
            raise ValueError(
                f"Could not parse JSON after {retries} attempts.\n"
                f"Last response (first 500 chars):\n{raw[:500]}")