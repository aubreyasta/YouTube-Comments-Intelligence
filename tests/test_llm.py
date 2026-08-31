"""Offline checks for LM Studio LLM boundary.

Covers text generation, structured output with validation, image handling,
retry logic, error handling, and config validation. Patches urllib and sleep;
no network, no model calls.

Run: python tests/test_llm.py
"""

import base64
import json
import os
import sys
import time
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline import llm
from pipeline.config_types import PipelineConfig


def make_cfg():
    return PipelineConfig(
        YOUTUBE_API_KEY="test",
        LLM_BASE_URL="http://127.0.0.1:1234",
        LLM_MODEL="youtube-intelligence",
        LLM_CONTEXT_LENGTH=32768,
        LLM_TIMEOUT_SECONDS=600,
        VIDEOS=[],
        SESSION_NAME="test",
        OUTPUT_DIR="output",
        KEEP_LANGUAGES={"en"},
        MIN_COMMENT_LETTERS=4,
        MAX_COMMENTS_PER_VIDEO=100,
        CODEBOOK_SAMPLE_SIZE=10,
        CODEBOOK_SAMPLE_MAX=50,
        CLASSIFY_BATCH_SIZE=25,
        UNCLASSIFIED_LIMIT=30,
        REPORT_LANGUAGE="English",
        CAMPAIGN_CONTEXT="",
    )


def mock_response(data, status=200):
    """Create a mock urllib response."""
    mock = MagicMock()
    mock.read.return_value = json.dumps(data).encode("utf-8")
    mock.status = status
    mock.__enter__ = lambda s: s
    mock.__exit__ = MagicMock(return_value=False)
    return mock


def test_text_generation():
    """Plain text generation with no schema."""
    cfg = make_cfg()
    response_data = {
        "choices": [{"message": {"content": "Hello world"}, "finish_reason": "stop"}]
    }

    with patch("urllib.request.urlopen", return_value=mock_response(response_data)):
        result = llm.ask("Say hello", cfg, num_predict=100)

    assert result == "Hello world"
    print("  ok  text generation returns plain content")


def test_structured_output_with_schema():
    """Structured output sends response_format and validates JSON."""
    cfg = make_cfg()
    schema = {
        "type": "object",
        "properties": {"answer": {"type": "string"}},
        "required": ["answer"],
    }
    response_data = {
        "choices": [
            {"message": {"content": '{"answer": "42"}'}, "finish_reason": "stop"}
        ]
    }

    with patch("urllib.request.urlopen", return_value=mock_response(response_data)) as mock_urlopen:
        result = llm.ask_json("Question", cfg, schema=schema, num_predict=200)

    assert result == {"answer": "42"}

    # Verify the request payload
    call_args = mock_urlopen.call_args
    request_obj = call_args[0][0]
    payload = json.loads(request_obj.data.decode("utf-8"))
    assert "response_format" in payload
    assert payload["response_format"]["type"] == "json_schema"
    assert payload["response_format"]["json_schema"]["name"] == "response"
    assert payload["response_format"]["json_schema"]["strict"] is True
    assert payload["response_format"]["json_schema"]["schema"] == schema
    print("  ok  structured output sends response_format and validates JSON")


def test_structured_output_uses_reasoning_content_when_content_is_empty():
    """LM Studio may place a thinking model's structured result in reasoning_content."""
    cfg = make_cfg()
    response_data = {
        "choices": [{
            "message": {
                "content": "",
                "reasoning_content": '{"answer": "42"}',
            },
            "finish_reason": "stop",
        }]
    }

    with patch("urllib.request.urlopen", return_value=mock_response(response_data)):
        result = llm.ask_json("Question", cfg, schema={"type": "object"})

    assert result == {"answer": "42"}
    print("  ok  structured output accepts LM Studio reasoning_content fallback")


def test_structured_output_validator_retry():
    """ask_json retries on validation failure and succeeds on retry."""
    cfg = make_cfg()
    call_count = [0]

    def mock_urlopen_side_effect(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            # First call: invalid JSON
            return mock_response(
                {"choices": [{"message": {"content": "not json"}, "finish_reason": "stop"}]}
            )
        else:
            # Second call: valid JSON
            return mock_response(
                {"choices": [{"message": {"content": '{"ok": true}'}, "finish_reason": "stop"}]}
            )

    def validator(value):
        if "ok" not in value:
            raise ValueError("missing ok")
        return value

    with patch("urllib.request.urlopen", side_effect=mock_urlopen_side_effect):
        with patch("time.sleep"):
            result = llm.ask_json(
                "Test", cfg, schema={"type": "object"}, validation=validator, retries=3
            )

    assert result == {"ok": True}
    assert call_count[0] == 2
    print("  ok  structured output retries on validation failure")


def test_image_generation():
    """Image generation sends content array with image_url data URL."""
    cfg = make_cfg()
    image_bytes = b"fake image data"
    mime_type = "image/png"
    response_data = {
        "choices": [{"message": {"content": '{"description":"I see an image"}'},
                     "finish_reason": "stop"}]
    }
    schema = {
        "type": "object",
        "properties": {"description": {"type": "string"}},
        "required": ["description"],
    }

    with patch("urllib.request.urlopen", return_value=mock_response(response_data)) as mock_urlopen:
        result = llm.ask_json(
            "Describe this", cfg, schema=schema,
            images=[(image_bytes, mime_type)], num_predict=100
        )

    assert result == {"description": "I see an image"}

    # Verify the request payload
    call_args = mock_urlopen.call_args
    request_obj = call_args[0][0]
    payload = json.loads(request_obj.data.decode("utf-8"))

    messages = payload["messages"]
    assert len(messages) == 1
    assert messages[0]["role"] == "user"
    content = messages[0]["content"]
    assert isinstance(content, list)
    assert len(content) == 2

    # Text part
    assert content[0]["type"] == "text"
    assert content[0]["text"] == "Describe this"

    # Image part
    assert content[1]["type"] == "image_url"
    image_url = content[1]["image_url"]["url"]
    assert image_url.startswith("data:image/png;base64,")
    encoded = base64.b64encode(image_bytes).decode("ascii")
    assert image_url == f"data:image/png;base64,{encoded}"
    print("  ok  image generation sends content array with data URL")


def test_retry_on_connection_error():
    """Retries exactly 3 times on connection errors."""
    cfg = make_cfg()
    call_count = [0]

    def mock_urlopen_side_effect(*args, **kwargs):
        call_count[0] += 1
        raise ConnectionError("Connection refused")

    with patch("urllib.request.urlopen", side_effect=mock_urlopen_side_effect):
        with patch("time.sleep"):
            try:
                llm.ask("Test", cfg, num_predict=100)
                assert False, "Should have raised LMStudioConnectionError"
            except llm.LMStudioConnectionError as e:
                assert "3 attempts" in str(e)

    assert call_count[0] == 3
    print("  ok  retries exactly 3 times on connection errors")


def test_retry_on_429():
    """Retries on HTTP 429."""
    cfg = make_cfg()
    call_count = [0]

    def mock_urlopen_side_effect(*args, **kwargs):
        call_count[0] += 1
        from urllib.error import HTTPError
        raise HTTPError("http://test", 429, "Too Many Requests", {}, None)

    with patch("urllib.request.urlopen", side_effect=mock_urlopen_side_effect):
        with patch("time.sleep"):
            try:
                llm.ask("Test", cfg, num_predict=100)
                assert False, "Should have raised LMStudioConnectionError"
            except llm.LMStudioConnectionError:
                pass

    assert call_count[0] == 3
    print("  ok  retries on HTTP 429")


def test_retry_on_5xx():
    """Retries on HTTP 500/502/503/504."""
    cfg = make_cfg()

    for status in [500, 502, 503, 504]:
        call_count = [0]

        def mock_urlopen_side_effect(*args, **kwargs):
            call_count[0] += 1
            from urllib.error import HTTPError
            raise HTTPError("http://test", status, "Server Error", {}, None)

        with patch("urllib.request.urlopen", side_effect=mock_urlopen_side_effect):
            with patch("time.sleep"):
                try:
                    llm.ask("Test", cfg, num_predict=100)
                    assert False, "Should have raised LMStudioConnectionError"
                except llm.LMStudioConnectionError:
                    pass

        assert call_count[0] == 3, f"Failed for status {status}"

    print("  ok  retries on HTTP 500/502/503/504")


def test_no_retry_on_400():
    """Does not retry on HTTP 400."""
    cfg = make_cfg()
    call_count = [0]

    def mock_urlopen_side_effect(*args, **kwargs):
        call_count[0] += 1
        from urllib.error import HTTPError
        raise HTTPError("http://test", 400, "Bad Request", {}, None)

    with patch("urllib.request.urlopen", side_effect=mock_urlopen_side_effect):
        with patch("time.sleep"):
            try:
                llm.ask("Test", cfg, num_predict=100)
                assert False, "Should have raised LMStudioModelError"
            except llm.LMStudioModelError:
                pass

    assert call_count[0] == 1
    print("  ok  does not retry on HTTP 400")


def test_no_retry_on_404():
    """Does not retry on HTTP 404."""
    cfg = make_cfg()
    call_count = [0]

    def mock_urlopen_side_effect(*args, **kwargs):
        call_count[0] += 1
        from urllib.error import HTTPError
        raise HTTPError("http://test", 404, "Not Found", {}, None)

    with patch("urllib.request.urlopen", side_effect=mock_urlopen_side_effect):
        with patch("time.sleep"):
            try:
                llm.ask("Test", cfg, num_predict=100)
                assert False, "Should have raised LMStudioModelError"
            except llm.LMStudioModelError:
                pass

    assert call_count[0] == 1
    print("  ok  does not retry on HTTP 404")


def test_invalid_json_no_retry():
    """Invalid JSON raises LMStudioResponseError immediately."""
    cfg = make_cfg()
    call_count = [0]

    def mock_urlopen_side_effect(*args, **kwargs):
        call_count[0] += 1
        mock = MagicMock()
        mock.read.return_value = b"not json"
        mock.__enter__ = lambda s: s
        mock.__exit__ = MagicMock(return_value=False)
        return mock

    with patch("urllib.request.urlopen", side_effect=mock_urlopen_side_effect):
        with patch("time.sleep"):
            try:
                llm.ask("Test", cfg, num_predict=100)
                assert False, "Should have raised LMStudioResponseError"
            except llm.LMStudioResponseError as e:
                assert "invalid JSON" in str(e)

    assert call_count[0] == 1
    print("  ok  invalid JSON raises LMStudioResponseError immediately")


def test_non_object_response():
    """Non-object JSON raises LMStudioResponseError."""
    cfg = make_cfg()

    with patch("urllib.request.urlopen", return_value=mock_response([1, 2, 3])):
        with patch("time.sleep"):
            try:
                llm.ask("Test", cfg, num_predict=100)
                assert False, "Should have raised LMStudioResponseError"
            except llm.LMStudioResponseError as e:
                assert "non-object" in str(e)

    print("  ok  non-object response raises LMStudioResponseError")


def test_empty_choices():
    """Empty choices array raises LMStudioResponseError."""
    cfg = make_cfg()

    with patch("urllib.request.urlopen", return_value=mock_response({"choices": []})):
        with patch("time.sleep"):
            try:
                llm.ask("Test", cfg, num_predict=100)
                assert False, "Should have raised LMStudioResponseError"
            except llm.LMStudioResponseError as e:
                assert "no choices" in str(e)

    print("  ok  empty choices raises LMStudioResponseError")


def test_truncated_response():
    """finish_reason 'length' or 'max_tokens' raises LMStudioResponseError."""
    cfg = make_cfg()

    for finish_reason in ["length", "max_tokens"]:
        response_data = {
            "choices": [{"message": {"content": "partial"}, "finish_reason": finish_reason}]
        }

        with patch("urllib.request.urlopen", return_value=mock_response(response_data)):
            with patch("time.sleep"):
                try:
                    llm.ask("Test", cfg, num_predict=100)
                    assert False, "Should have raised LMStudioResponseError"
                except llm.LMStudioResponseError as e:
                    assert "truncated" in str(e)

    print("  ok  truncated response raises LMStudioResponseError")


def test_empty_content():
    """Empty message content raises LMStudioResponseError."""
    cfg = make_cfg()
    response_data = {
        "choices": [{"message": {"content": ""}, "finish_reason": "stop"}]
    }

    with patch("urllib.request.urlopen", return_value=mock_response(response_data)):
        with patch("time.sleep"):
            try:
                llm.ask("Test", cfg, num_predict=100)
                assert False, "Should have raised LMStudioResponseError"
            except llm.LMStudioResponseError as e:
                assert "empty" in str(e)

    print("  ok  empty content raises LMStudioResponseError")


def test_preflight_model_not_found():
    """preflight raises LMStudioModelError when model is not in inventory."""
    cfg = make_cfg()
    response_data = {
        "models": [
            {"key": "other-model", "capabilities": {"vision": True}}
        ]
    }

    with patch("urllib.request.urlopen", return_value=mock_response(response_data)):
        try:
            llm.preflight(cfg)
            assert False, "Should have raised LMStudioModelError"
        except llm.LMStudioModelError as e:
            assert "not found" in str(e)

    print("  ok  preflight raises LMStudioModelError when model not found")


def test_preflight_vision_not_supported():
    """preflight raises LMStudioModelError when model lacks vision."""
    cfg = make_cfg()
    response_data = {
        "models": [
            {"key": "youtube-intelligence", "capabilities": {"vision": False}}
        ]
    }

    with patch("urllib.request.urlopen", return_value=mock_response(response_data)):
        try:
            llm.preflight(cfg)
            assert False, "Should have raised LMStudioModelError"
        except llm.LMStudioModelError as e:
            assert "vision" in str(e)

    print("  ok  preflight raises LMStudioModelError when vision not supported")


def test_preflight_success():
    """preflight succeeds when model is found with vision capability."""
    cfg = make_cfg()
    response_data = {
        "models": [
            {"key": "youtube-intelligence", "capabilities": {"vision": True}}
        ]
    }

    with patch("urllib.request.urlopen", return_value=mock_response(response_data)):
        llm.preflight(cfg)  # Should not raise

    print("  ok  preflight succeeds with valid model and vision")


def test_config_validation_loopback_only():
    """LLM_BASE_URL must be loopback only."""
    cfg = make_cfg()
    cfg.LLM_BASE_URL = "http://example.com:1234"

    try:
        llm._validate_config(cfg)
        assert False, "Should have raised ValueError"
    except ValueError as e:
        assert "loopback" in str(e)

    print("  ok  LLM_BASE_URL must be loopback only")


def test_config_validation_nonempty_model():
    """LLM_MODEL must be nonempty."""
    cfg = make_cfg()
    cfg.LLM_MODEL = ""

    try:
        llm._validate_config(cfg)
        assert False, "Should have raised ValueError"
    except ValueError as e:
        assert "nonempty" in str(e)

    print("  ok  LLM_MODEL must be nonempty")


def test_config_validation_positive_context():
    """LLM_CONTEXT_LENGTH must be positive."""
    cfg = make_cfg()
    cfg.LLM_CONTEXT_LENGTH = 0

    try:
        llm._validate_config(cfg)
        assert False, "Should have raised ValueError"
    except ValueError as e:
        assert "positive" in str(e)

    print("  ok  LLM_CONTEXT_LENGTH must be positive")


def test_config_validation_positive_timeout():
    """LLM_TIMEOUT_SECONDS must be positive."""
    cfg = make_cfg()
    cfg.LLM_TIMEOUT_SECONDS = -1

    try:
        llm._validate_config(cfg)
        assert False, "Should have raised ValueError"
    except ValueError as e:
        assert "positive" in str(e)

    print("  ok  LLM_TIMEOUT_SECONDS must be positive")


if __name__ == "__main__":
    tests = [
        test_text_generation,
        test_structured_output_with_schema,
        test_structured_output_uses_reasoning_content_when_content_is_empty,
        test_structured_output_validator_retry,
        test_image_generation,
        test_retry_on_connection_error,
        test_retry_on_429,
        test_retry_on_5xx,
        test_no_retry_on_400,
        test_no_retry_on_404,
        test_invalid_json_no_retry,
        test_non_object_response,
        test_empty_choices,
        test_truncated_response,
        test_empty_content,
        test_preflight_model_not_found,
        test_preflight_vision_not_supported,
        test_preflight_success,
        test_config_validation_loopback_only,
        test_config_validation_nonempty_model,
        test_config_validation_positive_context,
        test_config_validation_positive_timeout,
    ]

    failed = 0
    for test in tests:
        try:
            test()
        except AssertionError as exc:
            print(f"  FAIL {test.__name__}: {exc}")
            failed += 1
        except Exception as exc:
            print(f"  ERROR {test.__name__}: {type(exc).__name__}: {exc}")
            failed += 1

    if failed:
        print(f"\nFAIL ({failed}/{len(tests)} failed)")
        sys.exit(1)
    print(f"\nPASS ({len(tests)}/{len(tests)})")
