"""Tests for autodoc.llm.client -- LLMClient class."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from autodoc.core.exceptions import LLMError
from autodoc.llm.client import LLMClient, LLMResponse


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_client(provider="anthropic", **kwargs):
    """Build an LLMClient while mocking the underlying SDK constructor.

    Because LLMClient imports AsyncAnthropic / AsyncOpenAI locally inside
    __init__, we patch the classes at the *library package* level so the
    local ``from anthropic import AsyncAnthropic`` picks up the mock.
    """
    defaults = dict(
        model="test-model",
        api_key="sk-test-key",
        max_retries=3,
        initial_backoff=0.001,   # tiny backoff so tests don't sleep
        max_backoff=0.01,
        timeout_seconds=5.0,
    )
    defaults.update(kwargs)

    if provider.lower() == "anthropic":
        with patch("anthropic.AsyncAnthropic") as mock_cls:
            mock_cls.return_value = MagicMock()
            client = LLMClient(provider=provider, **defaults)
            client._mock_sdk = mock_cls.return_value
            return client
    else:
        with patch("openai.AsyncOpenAI") as mock_cls:
            mock_cls.return_value = MagicMock()
            client = LLMClient(provider=provider, **defaults)
            client._mock_sdk = mock_cls.return_value
            return client


def _api_error(status_code, cls_name="APIError"):
    """Create a fake exception with a status_code attribute and a class name."""
    exc_cls = type(cls_name, (Exception,), {})
    exc = exc_cls(f"HTTP {status_code}")
    exc.status_code = status_code
    return exc


# ===========================================================================
# Initialization
# ===========================================================================


class TestLLMClientInit:
    """LLMClient.__init__ tests."""

    def test_anthropic_defaults(self):
        """Anthropic provider sets expected model default and creates client."""
        with patch("anthropic.AsyncAnthropic") as mock_cls:
            mock_cls.return_value = MagicMock()
            client = LLMClient(provider="anthropic", api_key="sk-test")
        assert client.provider == "anthropic"
        assert client.model == "claude-sonnet-4-20250514"
        assert client.max_retries == 3

    def test_openai_defaults(self):
        """OpenAI provider sets expected model default and creates client."""
        with patch("openai.AsyncOpenAI") as mock_cls:
            mock_cls.return_value = MagicMock()
            client = LLMClient(provider="openai", api_key="sk-test")
        assert client.provider == "openai"
        assert client.model == "gpt-4o"

    def test_custom_model_override(self):
        """Explicit model name overrides default."""
        client = _make_client(provider="anthropic", model="custom-model-v1")
        assert client.model == "custom-model-v1"

    def test_unknown_provider_raises(self):
        """Unknown provider raises LLMError."""
        with pytest.raises(LLMError, match="Unknown provider"):
            LLMClient(provider="deepmind", api_key="sk-test")

    def test_missing_anthropic_key_raises(self):
        """Missing API key for Anthropic raises LLMError."""
        with patch.dict("os.environ", {}, clear=True):
            with patch("anthropic.AsyncAnthropic"):
                with pytest.raises(LLMError, match="ANTHROPIC_API_KEY not set"):
                    LLMClient(provider="anthropic")

    def test_missing_openai_key_raises(self):
        """Missing API key for OpenAI raises LLMError."""
        with patch.dict("os.environ", {}, clear=True):
            with patch("openai.AsyncOpenAI"):
                with pytest.raises(LLMError, match="OPENAI_API_KEY not set"):
                    LLMClient(provider="openai")

    def test_provider_case_insensitive(self):
        """Provider name is case-insensitive."""
        client = _make_client(provider="Anthropic")
        assert client.provider == "anthropic"


# ===========================================================================
# _is_retryable_error
# ===========================================================================


class TestIsRetryableError:
    """Tests for LLMClient._is_retryable_error."""

    @pytest.fixture
    def client(self):
        return _make_client()

    @pytest.mark.parametrize("status_code", [408, 429, 500, 502, 503, 504])
    def test_transient_status_codes(self, client, status_code):
        """HTTP status codes known to be transient should be retryable."""
        err = _api_error(status_code)
        assert client._is_retryable_error(err) is True

    @pytest.mark.parametrize("status_code", [400, 401, 403, 404])
    def test_permanent_status_codes(self, client, status_code):
        """Permanent client-error status codes should NOT be retryable."""
        err = _api_error(status_code, cls_name="BadRequestError")
        assert client._is_retryable_error(err) is False

    @pytest.mark.parametrize("cls_name", [
        "APITimeoutError", "APIConnectionError", "APIError",
        "OverloadedError", "ServiceUnavailableError",
    ])
    def test_transient_class_names(self, client, cls_name):
        """Errors whose class names match known transient types are retryable."""
        exc_cls = type(cls_name, (Exception,), {})
        exc = exc_cls("transient")
        assert client._is_retryable_error(exc) is True

    def test_rate_limit_error_class_name(self, client):
        """RateLimitError class name is retryable (via _is_rate_limit_error)."""
        exc_cls = type("RateLimitError", (Exception,), {})
        exc = exc_cls("rate limited")
        assert client._is_retryable_error(exc) is True

    def test_plain_exception_not_retryable(self, client):
        """A plain Exception with no status code is not retryable."""
        assert client._is_retryable_error(ValueError("oops")) is False


# ===========================================================================
# _is_rate_limit_error
# ===========================================================================


class TestIsRateLimitError:
    """Tests for LLMClient._is_rate_limit_error."""

    @pytest.fixture
    def client(self):
        return _make_client()

    def test_status_429(self, client):
        """HTTP 429 detected as rate limit."""
        err = _api_error(429)
        assert client._is_rate_limit_error(err) is True

    def test_rate_limit_class_name(self, client):
        """RateLimitError class name detected."""
        exc_cls = type("RateLimitError", (Exception,), {})
        exc = exc_cls("rate limited")
        assert client._is_rate_limit_error(exc) is True

    def test_rate_limit_in_message_underscore(self, client):
        """String 'rate_limit' in error message detected."""
        exc = Exception("error: rate_limit exceeded for tokens")
        assert client._is_rate_limit_error(exc) is True

    def test_rate_limit_in_message_space(self, client):
        """String 'rate limit' in error message detected."""
        exc = Exception("You have hit the rate limit, please wait.")
        assert client._is_rate_limit_error(exc) is True

    def test_non_rate_limit_error(self, client):
        """Ordinary error not flagged as rate-limit."""
        exc = Exception("authentication failed")
        exc.status_code = 401
        assert client._is_rate_limit_error(exc) is False


# ===========================================================================
# _format_error
# ===========================================================================


class TestFormatError:
    """Tests for LLMClient._format_error."""

    @pytest.fixture
    def client(self):
        return _make_client()

    def test_basic_format(self, client):
        """Basic formatting includes prefix and error text."""
        exc = Exception("connection reset")
        msg = client._format_error("Call failed", exc)
        assert msg == "Call failed: connection reset"

    def test_retry_context_included(self, client):
        """When retry attempts are tagged, they appear in the message."""
        exc = Exception("server error")
        exc._autodoc_retry_attempts = 3
        msg = client._format_error("LLM call", exc)
        assert "after 3 retries" in msg

    def test_rate_limit_suggestion(self, client):
        """Rate-limit errors include guidance about reducing concurrency."""
        exc = Exception("rate_limit exceeded")
        exc.status_code = 429
        msg = client._format_error("Request", exc)
        assert "rate limit exceeded" in msg
        assert "--generation-workers" in msg

    def test_rate_limit_with_retries(self, client):
        """Rate-limit message includes retry count when present."""
        exc = Exception("rate_limit exceeded")
        exc.status_code = 429
        exc._autodoc_retry_attempts = 2
        msg = client._format_error("Request", exc)
        assert "after 2 retries" in msg
        assert "rate limit exceeded" in msg


# ===========================================================================
# _call_with_retries
# ===========================================================================


class TestCallWithRetries:
    """Tests for LLMClient._call_with_retries."""

    @pytest.fixture
    def client(self):
        return _make_client(max_retries=2, initial_backoff=0.001, max_backoff=0.002)

    @pytest.mark.asyncio
    async def test_success_first_try(self, client):
        """Successful request on first attempt returns immediately."""
        request_fn = AsyncMock(return_value="ok")
        result = await client._call_with_retries(request_fn)
        assert result == "ok"
        assert request_fn.await_count == 1

    @pytest.mark.asyncio
    async def test_success_after_retry(self, client):
        """Transient failure followed by success returns the successful result."""
        transient_err = _api_error(502)
        request_fn = AsyncMock(side_effect=[transient_err, "recovered"])
        result = await client._call_with_retries(request_fn)
        assert result == "recovered"
        assert request_fn.await_count == 2

    @pytest.mark.asyncio
    async def test_timeout_raises_llm_error(self):
        """asyncio.TimeoutError after exhausting retries raises LLMError."""
        client = _make_client(
            max_retries=1, initial_backoff=0.001, max_backoff=0.002,
            timeout_seconds=0.001,
        )

        async def slow_fn():
            await asyncio.sleep(10)

        with pytest.raises(LLMError, match="timed out"):
            await client._call_with_retries(slow_fn)

    @pytest.mark.asyncio
    async def test_exhausts_all_retries(self, client):
        """Non-stop transient errors exhaust retries and propagate the exception."""
        transient_err = _api_error(503)
        request_fn = AsyncMock(side_effect=transient_err)
        with pytest.raises(Exception, match="HTTP 503"):
            await client._call_with_retries(request_fn)
        # max_retries=2, so total attempts = 1 initial + 2 retries = 3
        assert request_fn.await_count == 3

    @pytest.mark.asyncio
    async def test_permanent_error_not_retried(self, client):
        """Permanent errors are raised immediately without retries."""
        perm_err = _api_error(401, cls_name="AuthenticationError")
        request_fn = AsyncMock(side_effect=perm_err)
        with pytest.raises(Exception, match="HTTP 401"):
            await client._call_with_retries(request_fn)
        assert request_fn.await_count == 1


# ===========================================================================
# complete_json -- Anthropic provider
# ===========================================================================


class TestCompleteJsonAnthropic:
    """Tests for complete_json() with the Anthropic provider."""

    @pytest.fixture
    def client(self):
        return _make_client(provider="anthropic")

    @pytest.mark.asyncio
    async def test_returns_tool_use_input(self, client):
        """complete_json extracts the tool_use block input from Anthropic response."""
        expected_data = {"summary": "A great model", "score": 0.95}

        tool_block = SimpleNamespace(
            type="tool_use", name="output", input=expected_data
        )
        response = SimpleNamespace(
            content=[tool_block],
            usage=SimpleNamespace(input_tokens=100, output_tokens=50),
        )
        client.client.messages.create = AsyncMock(return_value=response)

        schema = {"type": "object", "properties": {"summary": {"type": "string"}}}
        result = await client.complete_json("Describe the model", schema=schema)

        assert result == expected_data

    @pytest.mark.asyncio
    async def test_no_tool_use_raises(self, client):
        """If no tool_use block is found, LLMError is raised."""
        text_block = SimpleNamespace(type="text", text="no JSON here")
        response = SimpleNamespace(
            content=[text_block],
            usage=SimpleNamespace(input_tokens=10, output_tokens=5),
        )
        client.client.messages.create = AsyncMock(return_value=response)

        with pytest.raises(LLMError, match="No structured output"):
            await client.complete_json("prompt", schema={"type": "object"})


# ===========================================================================
# complete_json -- OpenAI provider
# ===========================================================================


class TestCompleteJsonOpenAI:
    """Tests for complete_json() with the OpenAI provider."""

    @pytest.fixture
    def client(self):
        return _make_client(provider="openai")

    @pytest.mark.asyncio
    async def test_returns_parsed_json(self, client):
        """complete_json parses JSON from OpenAI response content."""
        expected_data = {"summary": "All good", "metrics": [1, 2, 3]}
        message = SimpleNamespace(content=json.dumps(expected_data))
        choice = SimpleNamespace(message=message)
        response = SimpleNamespace(
            choices=[choice],
            usage=SimpleNamespace(prompt_tokens=80, completion_tokens=40),
        )
        client.client.chat.completions.create = AsyncMock(return_value=response)

        result = await client.complete_json("prompt", schema={"type": "object"})
        assert result == expected_data

    @pytest.mark.asyncio
    async def test_empty_response_raises(self, client):
        """Empty content from OpenAI raises LLMError."""
        message = SimpleNamespace(content="")
        choice = SimpleNamespace(message=message)
        response = SimpleNamespace(
            choices=[choice],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=0),
        )
        client.client.chat.completions.create = AsyncMock(return_value=response)

        with pytest.raises(LLMError, match="Empty response"):
            await client.complete_json("prompt", schema={"type": "object"})

    @pytest.mark.asyncio
    async def test_malformed_json_raises(self, client):
        """Malformed JSON from OpenAI raises LLMError."""
        message = SimpleNamespace(content="{ not valid json }")
        choice = SimpleNamespace(message=message)
        response = SimpleNamespace(
            choices=[choice],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
        )
        client.client.chat.completions.create = AsyncMock(return_value=response)

        with pytest.raises(LLMError, match="Failed to parse JSON"):
            await client.complete_json("prompt", schema={"type": "object"})
