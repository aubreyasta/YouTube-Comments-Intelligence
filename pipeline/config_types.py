"""
Typed configuration contract shared across the pipeline.

Every pipeline module receives a PipelineConfig instead of importing
the config shim, so the shim can be removed once all callers migrate.
"""

from __future__ import annotations

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
    EMOTION_MODEL: str
    SENTIMENT_MODEL: str
    REPORT_LANGUAGE: str
    CAMPAIGN_CONTEXT: str | dict
    KEY_VISUALS: dict = field(default_factory=dict)
    KEEP_INTERMEDIATE: bool = False
    OLLAMA_BASE_URL: str = "http://127.0.0.1:11434"
    TEXT_MODEL: str = "qwen3:14b-q4_K_M"
    VISION_MODEL: str = "qwen3-vl:8b-instruct-q4_K_M"
    OLLAMA_TEXT_NUM_CTX: int = 32768
    OLLAMA_VISION_NUM_CTX: int = 8192
    OLLAMA_TIMEOUT_SECONDS: int = 600
    OLLAMA_KEEP_ALIVE: str = "10m"
