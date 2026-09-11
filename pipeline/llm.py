"""LM Studio model boundary and strict structured-output contracts."""

from __future__ import annotations

import base64
import ipaddress
import json
import socket
import time
from collections.abc import Callable
from urllib import error, parse, request

from pipeline.config_types import PipelineConfig


class LMStudioError(RuntimeError):
    """Base class for safe LM Studio boundary errors."""


class LMStudioConnectionError(LMStudioError):
    """LM Studio could not be reached or returned a retryable HTTP error."""


class LMStudioModelError(LMStudioError):
    """A configured model is missing or rejected the request."""


class LMStudioResponseError(LMStudioError):
    """LM Studio returned an invalid, incomplete, or schema-invalid response."""


_STRING = {"type": "string", "minLength": 1}
_THEME = {
    "type": "object",
    "properties": {"name": _STRING, "definition": _STRING},
    "required": ["name", "definition"],
    "additionalProperties": False,
}

IMAGE_OBSERVATION_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": _STRING,
        "visible_text": {"type": "array", "items": {"type": "string"}},
        "observations": {"type": "array", "items": _STRING, "minItems": 1},
    },
    "required": ["summary", "visible_text", "observations"],
    "additionalProperties": False,
}

BRIEF_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": _STRING,
        "points": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": _STRING,
                    "video_id": _STRING,
                    "description": _STRING,
                },
                "required": ["label", "video_id", "description"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["summary", "points"],
    "additionalProperties": False,
}

THEME_DISCOVERY_SCHEMA = {
    "type": "object",
    "properties": {
        "themes": {"type": "array", "items": _THEME, "minItems": 5, "maxItems": 8}
    },
    "required": ["themes"],
    "additionalProperties": False,
}

EXTEND_THEME_SCHEMA = {
    "type": "object",
    "properties": {
        "themes": {"type": "array", "items": _THEME, "maxItems": 4}
    },
    "required": ["themes"],
    "additionalProperties": False,
}

RESULTS_PROSE_SCHEMA = {
    "type": "object",
    "properties": {
        "title": _STRING,
        "interpretation": _STRING,
        "quote": {
            "type": "object",
            "properties": {"text": _STRING, "attr": {"type": "string"}},
            "required": ["text", "attr"],
            "additionalProperties": False,
        },
        "caveat": _STRING,
    },
    "required": ["title", "interpretation", "quote", "caveat"],
    "additionalProperties": False,
}


SENTIMENT_LABELS = ("positive", "negative", "neutral")
EMOTION_LABELS = ("joy", "anger", "sadness", "fear", "other_neutral")


def classification_schema(theme_names: list[str], point_labels: list[str]) -> dict:
    """Build the strict per-batch classification schema."""
    themes = list(dict.fromkeys([*theme_names, "Other"]))
    if point_labels:
        # uniqueItems is enforced by validate_classification instead: some
        # structured-output runtimes (LM Studio's MLX backend included)
        # reject the "uniqueItems" schema keyword outright.
        echoed_schema = {"type": "array",
                         "items": {"type": "string", "enum": list(point_labels)}}
    else:
        # No Key Messages for this video: only the empty array is valid.
        # `enum: []` on items would be impossible to satisfy for any
        # non-empty array, which some structured-output engines reject or
        # mishandle; maxItems: 0 says the same thing unambiguously.
        echoed_schema = {"type": "array", "maxItems": 0}
    return {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "index": {"type": "integer"},
                "theme": {"type": "string", "enum": themes},
                "echoed": echoed_schema,
                "sentiment": {"type": "string",
                              "enum": list(SENTIMENT_LABELS)},
                "emotion": {"type": "string",
                            "enum": list(EMOTION_LABELS)},
            },
            "required": ["index", "theme", "echoed", "sentiment", "emotion"],
            "additionalProperties": False,
        },
    }


def _object(value: object, keys: set[str], name: str) -> dict:
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError(f"{name} must contain exactly: {', '.join(sorted(keys))}")
    return value


def _text(value: object, name: str, *, empty: bool = False) -> str:
    if not isinstance(value, str) or (not empty and not value.strip()):
        raise ValueError(f"{name} must be a nonempty string")
    return value


def validate_image_observation(value: object) -> object:
    item = _object(value, {"summary", "visible_text", "observations"}, "image observation")
    _text(item["summary"], "summary")
    for key, allow_empty in (("visible_text", True), ("observations", False)):
        rows = item[key]
        if not isinstance(rows, list) or (not allow_empty and not rows):
            raise ValueError(f"{key} must be a {'nonempty ' if not allow_empty else ''}list")
        for row in rows:
            _text(row, key)
    return item


def validate_brief(value: object) -> object:
    result = _object(value, {"summary", "points"}, "brief")
    _text(result["summary"], "summary")
    if not isinstance(result["points"], list):
        raise ValueError("points must be a list")
    for point in result["points"]:
        point = _object(point, {"label", "video_id", "description"}, "brief point")
        for key in point:
            _text(point[key], key)
    return result


def _validate_themes(value: object, minimum: int, maximum: int) -> object:
    result = _object(value, {"themes"}, "theme result")
    themes = result["themes"]
    if not isinstance(themes, list) or not minimum <= len(themes) <= maximum:
        raise ValueError(f"themes must contain {minimum} to {maximum} entries")
    names = []
    for theme in themes:
        theme = _object(theme, {"name", "definition"}, "theme")
        names.append(_text(theme["name"], "theme name"))
        _text(theme["definition"], "theme definition")
    if len(names) != len(set(names)):
        raise ValueError("theme names must be unique")
    return result


def validate_theme_discovery(value: object) -> object:
    return _validate_themes(value, 5, 8)


def validate_extend_themes(value: object) -> object:
    return _validate_themes(value, 0, 4)


def validate_results_prose(value: object) -> object:
    result = _object(value, {"title", "interpretation", "quote", "caveat"}, "results prose")
    for key in ("title", "interpretation", "caveat"):
        _text(result[key], key)
    quote = _object(result["quote"], {"text", "attr"}, "quote")
    _text(quote["text"], "quote text")
    _text(quote["attr"], "quote attribution", empty=True)
    return result


def validate_classification(value: object, expected_indices: list[int],
                            theme_names: list[str], point_labels: list[str]) -> object:
    if not isinstance(value, list):
        raise ValueError("classification must be a list")
    if any(isinstance(index, bool) or not isinstance(index, int) for index in expected_indices):
        raise ValueError("expected indices must be integers, not booleans")
    allowed_themes = {*theme_names, "Other"}
    allowed_points = set(point_labels)
    expected = set(expected_indices)
    seen = []
    kept = []
    for row in value:
        row = _object(row, {"index", "theme", "echoed", "sentiment", "emotion"}, "classification row")
        index = row["index"]
        if isinstance(index, bool) or not isinstance(index, int):
            raise ValueError("classification index must be an integer, not a boolean")
        if not isinstance(row["theme"], str) or row["theme"] not in allowed_themes:
            raise ValueError("classification contains an unknown theme")
        echoed = row["echoed"]
        if not isinstance(echoed, list) or any(not isinstance(label, str) for label in echoed):
            raise ValueError("echoed must be a list of strings")
        if len(echoed) != len(set(echoed)) or not set(echoed) <= allowed_points:
            raise ValueError("echoed must contain unique allowed Key Message labels")
        if row["sentiment"] not in SENTIMENT_LABELS:
            raise ValueError("classification contains an unknown sentiment")
        if row["emotion"] not in EMOTION_LABELS:
            raise ValueError("classification contains an unknown emotion")
        # Some structured-output backends over-generate rows for indices
        # nobody asked about (observed on LM Studio's MLX backend: a batch
        # of 4 requested indices came back with 6 rows). The schema can't
        # bound the array to the exact requested indices, so drop the
        # noise here instead of failing the whole batch on it.
        if index in expected:
            seen.append(index)
            kept.append(row)
    if len(seen) != len(set(seen)) or set(seen) != expected or len(seen) != len(expected):
        raise ValueError("classification indices must exactly cover the requested indices once")
    return kept


def _validated_base_url(cfg: PipelineConfig) -> str:
    if not isinstance(cfg.LLM_BASE_URL, str):
        raise ValueError("LLM_BASE_URL must be a string")
    url = parse.urlsplit(cfg.LLM_BASE_URL)
    if url.scheme != "http" or not url.hostname or url.username or url.password:
        raise ValueError("LLM_BASE_URL must be an HTTP loopback URL without credentials")
    if url.query or url.fragment or url.path not in ("", "/"):
        raise ValueError("LLM_BASE_URL must not contain a path, query, or fragment")
    try:
        loopback = ipaddress.ip_address(url.hostname).is_loopback
    except ValueError:
        loopback = url.hostname.lower() == "localhost"
    if not loopback:
        raise ValueError("LLM_BASE_URL host must be loopback")
    try:
        url.port
    except ValueError as exc:
        raise ValueError("LLM_BASE_URL has an invalid port") from exc
    return cfg.LLM_BASE_URL.rstrip("/")


def _validate_config(cfg: PipelineConfig) -> str:
    base_url = _validated_base_url(cfg)
    model = cfg.LLM_MODEL
    if not isinstance(model, str) or not model.strip():
        raise ValueError("LLM_MODEL must be a nonempty string")
    for name in ("LLM_CONTEXT_LENGTH", "LLM_TIMEOUT_SECONDS"):
        value = getattr(cfg, name)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"{name} must be a positive integer")
    return base_url


def _call(cfg: PipelineConfig, method: str, path: str, payload: dict | None = None) -> dict:
    base_url = _validate_config(cfg)
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"} if data is not None else {}
    retry_statuses = {429, 500, 502, 503, 504}
    last = ""
    for attempt in range(3):
        try:
            req = request.Request(base_url + path, data=data, headers=headers, method=method)
            with request.urlopen(req, timeout=cfg.LLM_TIMEOUT_SECONDS) as response:
                raw = response.read()
            parsed = json.loads(raw)
            if not isinstance(parsed, dict):
                raise LMStudioResponseError("LM Studio returned a non-object response.")
            return parsed
        except error.HTTPError as exc:
            if exc.code in (400, 404):
                raise LMStudioModelError(f"LM Studio rejected the request (HTTP {exc.code}).") from None
            if exc.code not in retry_statuses:
                raise LMStudioConnectionError(f"LM Studio request failed (HTTP {exc.code}).") from None
            last = f"HTTP {exc.code}"
        except (error.URLError, socket.timeout, TimeoutError, ConnectionError, OSError):
            last = "connection failure"
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise LMStudioResponseError("LM Studio returned invalid JSON.") from None
        if attempt < 2:
            time.sleep((2, 5)[attempt])
    raise LMStudioConnectionError(f"LM Studio request failed after 3 attempts ({last}).")


def _generate(prompt: str, cfg: PipelineConfig, *, model: str | None, num_predict: int,
              schema: dict | None = None, images: list[tuple[bytes, str]] | None = None) -> str:
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("prompt must be a nonempty string")
    if isinstance(num_predict, bool) or not isinstance(num_predict, int) or num_predict <= 0:
        raise ValueError("num_predict must be a positive integer")
    chosen_model = model or cfg.LLM_MODEL
    if not isinstance(chosen_model, str) or not chosen_model.strip():
        raise ValueError("model must be a nonempty string")

    # Build message content: plain string for text-only, content array for images
    if images:
        content_parts = [{"type": "text", "text": prompt}]
        for image_bytes, mime_type in images:
            if (not isinstance(image_bytes, bytes) or not image_bytes
                    or not isinstance(mime_type, str) or not mime_type.strip()):
                raise ValueError("images must contain (bytes, MIME type) tuples")
            encoded = base64.b64encode(image_bytes).decode("ascii")
            content_parts.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime_type};base64,{encoded}"},
            })
        messages = [{"role": "user", "content": content_parts}]
    else:
        messages = [{"role": "user", "content": prompt}]

    payload = {
        "model": chosen_model,
        "messages": messages,
        "temperature": 0,
        "seed": 0,
        "max_tokens": num_predict,
        "stream": False,
    }
    if schema is not None:
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": "response",
                "strict": True,
                "schema": schema,
            },
        }

    response = _call(cfg, "POST", "/v1/chat/completions", payload)

    # Validate response structure
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        raise LMStudioResponseError("LM Studio returned no choices.")
    choice = choices[0]
    if not isinstance(choice, dict):
        raise LMStudioResponseError("LM Studio returned an invalid choice.")

    finish_reason = choice.get("finish_reason")
    if finish_reason in ("length", "max_tokens"):
        raise LMStudioResponseError("LM Studio returned a truncated response.")

    message = choice.get("message")
    if not isinstance(message, dict):
        raise LMStudioResponseError("LM Studio returned an invalid message.")
    text = message.get("content")
    if schema is not None and (not isinstance(text, str) or not text.strip()):
        text = message.get("reasoning_content")
    if not isinstance(text, str) or not text.strip():
        raise LMStudioResponseError("LM Studio returned an empty response.")

    print(f"  ctx  predict={num_predict} context_length={cfg.LLM_CONTEXT_LENGTH}")
    return text


def preflight(cfg: PipelineConfig) -> None:
    response = _call(cfg, "GET", "/api/v1/models")
    models = response.get("models")
    if not isinstance(models, list):
        raise LMStudioResponseError("LM Studio models response is invalid.")

    # Find the configured model by exact key match
    target_model = None
    for item in models:
        if not isinstance(item, dict):
            continue
        if item.get("key") == cfg.LLM_MODEL:
            target_model = item
            break

    if target_model is None:
        raise LMStudioModelError(
            f"Required LM Studio model '{cfg.LLM_MODEL}' not found. "
            f"Load it in LM Studio before running the pipeline.")

    # Check vision capability
    capabilities = target_model.get("capabilities")
    if not isinstance(capabilities, dict) or not capabilities.get("vision"):
        raise LMStudioModelError(
            f"Model '{cfg.LLM_MODEL}' does not support vision. "
            f"A multimodal model is required for image analysis.")


def ask(prompt: str, cfg: PipelineConfig, *, model: str | None = None,
        num_predict: int = 2048) -> str:
    return _generate(prompt, cfg, model=model, num_predict=num_predict)


def ask_json(prompt: str, cfg: PipelineConfig, *, schema: dict,
             model: str | None = None, images: list[tuple[bytes, str]] | None = None,
             num_predict: int = 2048, validation: Callable[[object], object] | None = None,
             retries: int = 3) -> object:
    if isinstance(retries, bool) or not isinstance(retries, int) or retries <= 0:
        raise ValueError("retries must be a positive integer")
    last_error = "invalid structured response"
    for _ in range(retries):
        raw = _generate(prompt, cfg, model=model, num_predict=num_predict,
                        schema=schema, images=images)
        try:
            value = json.loads(raw)
            return validation(value) if validation else value
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            last_error = f"{exc.__class__.__name__}: {exc}"
            print(f"    ! ask_json validation failed ({last_error}); raw response: {raw[:4000]}")
    raise LMStudioResponseError(
        f"LM Studio failed to return a valid structured response after {retries} attempts ({last_error}).")


def classify_batch(prompt: str, expected_indices: list[int], theme_names: list[str],
                   point_labels: list[str], cfg: PipelineConfig) -> list[dict]:
    result = ask_json(
        prompt, cfg, schema=classification_schema(theme_names, point_labels),
        validation=lambda value: validate_classification(
            value, expected_indices, theme_names, point_labels))
    return result  # type: ignore[return-value]


def extract_image_context(images: list[tuple[bytes, str]], cfg: PipelineConfig) -> str:
    blocks = []
    prompt = ("Describe only visible evidence useful as campaign context. "
              "Transcribe visible text exactly. Do not infer identity or intent.")
    for index, image in enumerate(images, 1):
        result = ask_json(prompt, cfg, schema=IMAGE_OBSERVATION_SCHEMA,
                          images=[image], validation=validate_image_observation)
        blocks.extend([
            f"Image {index}:",
            f"Summary: {result['summary']}",
            "Visible text: " + (" | ".join(result["visible_text"]) or "(none)"),
            "Observations:",
            *(f"- {row}" for row in result["observations"]),
        ])
    return "\n".join(blocks)
