"""Unified LLM client supporting Anthropic and OpenAI."""

import asyncio
import json
import os
import random
from dataclasses import dataclass
from typing import Any, Dict, Optional

from autodoc.core.exceptions import LLMError


@dataclass
class LLMResponse:
    """Response from an LLM completion."""

    content: str
    input_tokens: int
    output_tokens: int
    model: str


class LLMClient:
    """Unified async LLM client supporting Anthropic and OpenAI.

    This client provides a consistent interface for both providers,
    with support for both text completions and structured JSON output.
    """

    def __init__(
        self,
        provider: str = "anthropic",
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        max_retries: int = 3,
        initial_backoff: float = 1.0,
        max_backoff: float = 20.0,
        backoff_jitter: float = 0.2,
    ):
        """Initialize the LLM client.

        Args:
            provider: LLM provider ("anthropic" or "openai").
            model: Model name override. If None, uses provider defaults.
            api_key: API key. If None, reads from environment variable.
            max_retries: Max retries for transient or rate-limit errors.
            initial_backoff: Initial backoff delay in seconds.
            max_backoff: Maximum backoff delay in seconds.
            backoff_jitter: Random jitter factor applied to backoff.
        """
        self.provider = provider.lower()
        self.max_retries = max_retries
        self.initial_backoff = initial_backoff
        self.max_backoff = max_backoff
        self.backoff_jitter = backoff_jitter

        if self.provider == "anthropic":
            from anthropic import AsyncAnthropic

            self.model = model or "claude-sonnet-4-20250514"
            key = api_key or os.environ.get("ANTHROPIC_API_KEY")
            if not key:
                raise LLMError("ANTHROPIC_API_KEY not set")
            self.client = AsyncAnthropic(api_key=key)

        elif self.provider == "openai":
            from openai import AsyncOpenAI

            self.model = model or "gpt-4o"
            key = api_key or os.environ.get("OPENAI_API_KEY")
            if not key:
                raise LLMError("OPENAI_API_KEY not set")
            self.client = AsyncOpenAI(api_key=key)

        else:
            raise LLMError(f"Unknown provider: {provider}")

    async def complete(
        self,
        prompt: str,
        system: str = "You are a technical documentation expert.",
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> LLMResponse:
        """Generate a text completion.

        Args:
            prompt: The user prompt.
            system: System message for the model.
            max_tokens: Maximum tokens to generate.
            temperature: Sampling temperature (0-1).

        Returns:
            LLMResponse with the generated content.

        Raises:
            LLMError: If the API call fails.
        """
        try:
            if self.provider == "anthropic":
                async def _request():
                    return await self.client.messages.create(
                        model=self.model,
                        max_tokens=max_tokens,
                        temperature=temperature,
                        system=system,
                        messages=[{"role": "user", "content": prompt}],
                    )

                response = await self._call_with_retries(_request)
                return LLMResponse(
                    content=response.content[0].text,
                    input_tokens=response.usage.input_tokens,
                    output_tokens=response.usage.output_tokens,
                    model=self.model,
                )

            else:  # OpenAI
                async def _request():
                    return await self.client.chat.completions.create(
                        model=self.model,
                        max_tokens=max_tokens,
                        temperature=temperature,
                        messages=[
                            {"role": "system", "content": system},
                            {"role": "user", "content": prompt},
                        ],
                    )

                response = await self._call_with_retries(_request)
                return LLMResponse(
                    content=response.choices[0].message.content or "",
                    input_tokens=response.usage.prompt_tokens if response.usage else 0,
                    output_tokens=response.usage.completion_tokens if response.usage else 0,
                    model=self.model,
                )

        except Exception as e:
            raise LLMError(self._format_error("LLM completion failed", e)) from e

    async def complete_json(
        self,
        prompt: str,
        schema: Dict[str, Any],
        system: str = "You are a technical documentation expert.",
        max_tokens: int = 4096,
    ) -> Dict[str, Any]:
        """Generate structured JSON output.

        Uses tool calling for Anthropic and response_format for OpenAI
        to ensure valid JSON output matching the schema.

        Args:
            prompt: The user prompt.
            schema: JSON schema defining the expected output structure.
            system: System message for the model.
            max_tokens: Maximum tokens to generate.

        Returns:
            Dictionary matching the provided schema.

        Raises:
            LLMError: If the API call fails or output doesn't match schema.
        """
        try:
            if self.provider == "anthropic":
                async def _request():
                    return await self.client.messages.create(
                        model=self.model,
                        max_tokens=max_tokens,
                        tools=[
                            {
                                "name": "output",
                                "description": "Output structured data matching the schema",
                                "input_schema": schema,
                            }
                        ],
                        tool_choice={"type": "tool", "name": "output"},
                        messages=[{"role": "user", "content": prompt}],
                    )

                response = await self._call_with_retries(_request)

                # Extract tool call result
                for block in response.content:
                    if block.type == "tool_use" and block.name == "output":
                        return block.input

                raise LLMError("No structured output in response")

            else:  # OpenAI
                # Use JSON mode with schema in prompt
                enhanced_prompt = (
                    f"{prompt}\n\nRespond with valid JSON matching this schema:\n"
                    f"{json.dumps(schema, indent=2)}"
                )

                async def _request():
                    return await self.client.chat.completions.create(
                        model=self.model,
                        max_tokens=max_tokens,
                        response_format={"type": "json_object"},
                        messages=[
                            {"role": "system", "content": f"{system}\nAlways respond with valid JSON."},
                            {"role": "user", "content": enhanced_prompt},
                        ],
                    )

                response = await self._call_with_retries(_request)

                content = response.choices[0].message.content
                if not content:
                    raise LLMError("Empty response from OpenAI")

                return json.loads(content)

        except json.JSONDecodeError as e:
            raise LLMError(f"Failed to parse JSON response: {e}") from e
        except LLMError:
            raise
        except Exception as e:
            raise LLMError(self._format_error("LLM JSON completion failed", e)) from e

    async def _call_with_retries(self, request_fn):
        """Call provider request with retries for transient errors."""
        attempt = 0
        backoff = self.initial_backoff

        while True:
            try:
                return await request_fn()
            except Exception as e:
                retryable = self._is_retryable_error(e)
                if not retryable or attempt >= self.max_retries:
                    if attempt:
                        setattr(e, "_autodoc_retry_attempts", attempt)
                    raise

                delay = min(self.max_backoff, backoff)
                if self.backoff_jitter > 0:
                    delay += random.uniform(0, delay * self.backoff_jitter)

                await asyncio.sleep(delay)
                backoff *= 2
                attempt += 1

    def _is_retryable_error(self, error: Exception) -> bool:
        """Return True for transient errors worth retrying."""
        if self._is_rate_limit_error(error):
            return True

        error_name = error.__class__.__name__
        transient_names = {
            "APITimeoutError",
            "APIConnectionError",
            "APIError",
            "OverloadedError",
            "ServiceUnavailableError",
        }
        if error_name in transient_names:
            return True

        status_code = getattr(error, "status_code", None)
        if status_code and int(status_code) in {408, 429, 500, 502, 503, 504}:
            return True

        return False

    def _is_rate_limit_error(self, error: Exception) -> bool:
        """Detect rate-limit errors across providers."""
        status_code = getattr(error, "status_code", None)
        if status_code == 429:
            return True

        error_name = error.__class__.__name__
        if error_name == "RateLimitError":
            return True

        error_text = str(error).lower()
        return "rate_limit" in error_text or "rate limit" in error_text

    def _format_error(self, prefix: str, error: Exception) -> str:
        """Format provider errors with actionable guidance."""
        retries = getattr(error, "_autodoc_retry_attempts", 0)
        retry_suffix = f" after {retries} retries" if retries else ""

        if self._is_rate_limit_error(error):
            return (
                f"{prefix}: rate limit exceeded{retry_suffix}. "
                "Reduce concurrency with --workers (or AUTODOC_PARALLEL_WORKERS) "
                "or retry after a short delay."
            )

        return f"{prefix}: {error}{retry_suffix}"
