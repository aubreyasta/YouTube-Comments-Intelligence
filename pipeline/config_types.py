"""
Typed configuration contract shared across the pipeline.

Every pipeline module receives a PipelineConfig instead of importing
the config shim, so the shim can be removed once all callers migrate.
"""

from __future__ import annotations

from dataclasses import dataclass


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
