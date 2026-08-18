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
    OLLAMA_BASE_URL: str = "http://127.0.0.1:11434"
    MODEL: str = "qwen3.5:4b"
    OLLAMA_NUM_CTX: int = 32768
    OLLAMA_TIMEOUT_SECONDS: int = 600
    OLLAMA_KEEP_ALIVE: str = "10m"
