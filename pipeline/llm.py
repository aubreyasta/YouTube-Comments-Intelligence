"""Local Ollama model boundary and strict structured-output contracts."""

from __future__ import annotations

import base64
import ipaddress
import json
import re
import socket
import time
from collections.abc import Callable
from urllib import error, parse, request

from pipeline.config_types import PipelineConfig

# Exported so a caller can check the version floor without preflight(),
# which also requires the model tag to be installed.
MIN_OLLAMA_VERSION = (0, 12, 7)


class OllamaError(RuntimeError):
    """Base class for safe Ollama boundary errors."""


class OllamaConnectionError(OllamaError):
    """Ollama could not be reached or returned a retryable HTTP error."""


class OllamaModelError(OllamaError):
    """A configured model is missing or rejected the request."""


class OllamaResponseError(OllamaError):
    """Ollama returned an invalid, incomplete, or schema-invalid response."""


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
        echoed_schema = {"type": "array",
                         "items": {"type": "string", "enum": list(point_labels)},
                         "uniqueItems": True}
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
    seen = []
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
        seen.append(index)
    if len(seen) != len(set(seen)) or set(seen) != set(expected_indices) or len(seen) != len(expected_indices):
        raise ValueError("classification indices must exactly cover the requested indices once")
    return value


def _validated_base_url(cfg: PipelineConfig) -> str:
    if not isinstance(cfg.OLLAMA_BASE_URL, str):
        raise ValueError("OLLAMA_BASE_URL must be a string")
    url = parse.urlsplit(cfg.OLLAMA_BASE_URL)
    if url.scheme != "http" or not url.hostname or url.username or url.password:
        raise ValueError("OLLAMA_BASE_URL must be an HTTP loopback URL without credentials")
    if url.query or url.fragment or url.path not in ("", "/"):
        raise ValueError("OLLAMA_BASE_URL must not contain a path, query, or fragment")
    try:
        loopback = ipaddress.ip_address(url.hostname).is_loopback
    except ValueError:
        loopback = url.hostname.lower() == "localhost"
    if not loopback:
        raise ValueError("OLLAMA_BASE_URL host must be loopback")
    try:
        url.port
    except ValueError as exc:
        raise ValueError("OLLAMA_BASE_URL has an invalid port") from exc
    return cfg.OLLAMA_BASE_URL.rstrip("/")


def _validate_config(cfg: PipelineConfig) -> str:
    base_url = _validated_base_url(cfg)
    for name in ("MODEL",):
        model = getattr(cfg, name)
        if not isinstance(model, str) or not model.strip() or model.lower().endswith("-cloud"):
            raise ValueError(f"{name} must be a nonempty local model tag and must not end in -cloud")
    for name in ("OLLAMA_NUM_CTX", "OLLAMA_TIMEOUT_SECONDS"):
        value = getattr(cfg, name)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"{name} must be a positive integer")
    if not isinstance(cfg.OLLAMA_KEEP_ALIVE, str) or not cfg.OLLAMA_KEEP_ALIVE.strip():
        raise ValueError("OLLAMA_KEEP_ALIVE must be nonempty")
    return base_url


def _call(cfg: PipelineConfig, method: str, path: str, payload: dict | None = None) -> dict:
    base_url = _validate_config(cfg)
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"} if data is not None else {}
    retry_statuses = {429, 500, 502, 503, 504}
    for attempt in range(3):
        try:
            req = request.Request(base_url + path, data=data, headers=headers, method=method)
            with request.urlopen(req, timeout=cfg.OLLAMA_TIMEOUT_SECONDS) as response:
                raw = response.read()
            parsed = json.loads(raw)
            if not isinstance(parsed, dict):
                raise OllamaResponseError("Ollama returned a non-object response.")
            return parsed
        except error.HTTPError as exc:
            if exc.code in (400, 404):
                raise OllamaModelError(f"Ollama rejected the model request (HTTP {exc.code}).") from None
            if exc.code not in retry_statuses:
                raise OllamaConnectionError(f"Ollama request failed (HTTP {exc.code}).") from None
            last = f"HTTP {exc.code}"
        except (error.URLError, socket.timeout, TimeoutError, ConnectionError, OSError):
            last = "connection failure"
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise OllamaResponseError("Ollama returned invalid JSON.") from None
        if attempt < 2:
            time.sleep((2, 5)[attempt])
    raise OllamaConnectionError(f"Ollama request failed after 3 attempts ({last}).")


def _generate(prompt: str, cfg: PipelineConfig, *, model: str | None, num_predict: int,
              schema: dict | None = None, images: list[tuple[bytes, str]] | None = None,
              keep_alive: str | int | None = None) -> str:
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("prompt must be a nonempty string")
    if isinstance(num_predict, bool) or not isinstance(num_predict, int) or num_predict <= 0:
        raise ValueError("num_predict must be a positive integer")
    chosen_model = model or cfg.MODEL
    if not isinstance(chosen_model, str) or not chosen_model.strip() or chosen_model.lower().endswith("-cloud"):
        raise ValueError("model must be a nonempty local tag and must not end in -cloud")
    encoded_images = []
    for image in images or []:
        if (not isinstance(image, tuple) or len(image) != 2
                or not isinstance(image[0], bytes) or not image[0]
                or not isinstance(image[1], str) or not image[1].strip()):
            raise ValueError("images must contain (bytes, MIME type) tuples")
        encoded_images.append(base64.b64encode(image[0]).decode("ascii"))
    payload = {
        "model": chosen_model,
        "prompt": prompt,
        "stream": False,
        "think": False,
        "keep_alive": cfg.OLLAMA_KEEP_ALIVE if keep_alive is None else keep_alive,
        "options": {
            "temperature": 0,
            "seed": 0,
            "num_ctx": cfg.OLLAMA_NUM_CTX,
            "num_predict": num_predict,
        },
    }
    if schema is not None:
        payload["format"] = schema
    if encoded_images:
        payload["images"] = encoded_images
    response = _call(cfg, "POST", "/api/generate", payload)
    if response.get("error"):
        raise OllamaResponseError("Ollama reported a generation error.")
    if response.get("done") is not True or response.get("done_reason") in {"length", "max_tokens"}:
        raise OllamaResponseError("Ollama returned an incomplete or truncated response.")
    text = response.get("response")
    if not isinstance(text, str) or not text.strip():
        raise OllamaResponseError("Ollama returned an empty response.")
    return text


def preflight(cfg: PipelineConfig) -> None:
    version = _call(cfg, "GET", "/api/version").get("version")
    if not isinstance(version, str):
        raise OllamaResponseError("Ollama version response is invalid.")
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:[-+].*)?", version)
    if not match:
        raise OllamaResponseError("Ollama version response is invalid.")
    if tuple(map(int, match.groups())) < MIN_OLLAMA_VERSION:
        raise OllamaError(f"Ollama {'.'.join(map(str, MIN_OLLAMA_VERSION))} or newer is required; found {version}.")
    models = _call(cfg, "GET", "/api/tags").get("models")
    if not isinstance(models, list):
        raise OllamaResponseError("Ollama tags response is invalid.")
    local = {
        item.get("name", item.get("model")): item.get("size")
        for item in models if isinstance(item, dict)
    }
    missing = [tag for tag in (cfg.MODEL,)
               if tag not in local or isinstance(local[tag], bool)
               or not isinstance(local[tag], (int, float)) or local[tag] <= 0]
    if missing:
        commands = "\n".join(f"ollama pull {tag}" for tag in missing)
        raise OllamaModelError(f"Required local Ollama model tag(s) missing:\n{commands}")


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
    raise OllamaResponseError(
        f"Ollama failed to return a valid structured response after {retries} attempts ({last_error}).")


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


def unload(model: str, cfg: PipelineConfig) -> None:
    _validate_config(cfg)
    if not isinstance(model, str) or not model.strip() or model.lower().endswith("-cloud"):
        raise ValueError("model must be a nonempty local tag and must not end in -cloud")
    response = _call(cfg, "POST", "/api/generate", {
        "model": model,
        "stream": False,
        "keep_alive": 0,
    })
    if response.get("error") or response.get("done") is not True:
        raise OllamaResponseError("Ollama did not confirm model unload.")
