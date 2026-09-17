"""
Typed configuration contract shared across the pipeline.

Every pipeline module receives a PipelineConfig instead of importing
the config shim, so the shim can be removed once all callers migrate.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field


@dataclass
class PipelineConfig:
    YOUTUBE_API_KEY: str
    VIDEOS: list[dict]
    SESSION_NAME: str
    OUTPUT_DIR: str
    KEEP_LANGUAGES: set[str]
    MIN_COMMENT_LETTERS: int
    MAX_COMMENTS_PER_VIDEO: int
    CODEBOOK_SAMPLE_SIZE: int
    CODEBOOK_SAMPLE_MAX: int
    CLASSIFY_BATCH_SIZE: int
    UNCLASSIFIED_LIMIT: int       # percent, e.g. 30
    REPORT_LANGUAGE: str
    CAMPAIGN_CONTEXT: str | dict
    KEEP_INTERMEDIATE: bool = False
    LLM_BASE_URL: str = "http://127.0.0.1:1234"
    LLM_MODEL: str = "youtube-intelligence"
    LLM_CONTEXT_LENGTH: int = 32768
    LLM_TIMEOUT_SECONDS: int = 600
    # Sent on every model request, e.g. {"Authorization": "Bearer <token>"}.
    LLM_HEADERS: dict[str, str] = field(default_factory=dict)
    # Skip TLS certificate verification for an https LLM_BASE_URL.
    LLM_ALLOW_INSECURE: bool = False


def llm_env(environ: Mapping[str, str]) -> dict:
    """The LLM_* PipelineConfig fields, read from environment variables.

    LLM_HEADERS is a JSON object; LLM_ALLOW_INSECURE is true/1/yes.
    """
    try:
        headers = json.loads(environ.get("LLM_HEADERS") or "{}")
    except json.JSONDecodeError:
        headers = None
    if not isinstance(headers, dict):
        # No value in the message: the variable usually holds a token.
        raise ValueError("LLM_HEADERS must be a JSON object of string values")
    return {
        "LLM_BASE_URL": environ.get("LLM_BASE_URL", "http://127.0.0.1:1234"),
        "LLM_MODEL": environ.get("LLM_MODEL", "youtube-intelligence"),
        "LLM_CONTEXT_LENGTH": int(environ.get("LLM_CONTEXT_LENGTH", "32768")),
        "LLM_TIMEOUT_SECONDS": int(environ.get("LLM_TIMEOUT_SECONDS", "600")),
        "LLM_HEADERS": headers,
        "LLM_ALLOW_INSECURE": environ.get("LLM_ALLOW_INSECURE", "").strip().lower()
                              in ("true", "1", "yes"),
    }
