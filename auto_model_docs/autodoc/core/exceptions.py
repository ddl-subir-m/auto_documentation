"""Custom exceptions for Auto Model Documentation."""


class AutoDocError(Exception):
    """Base exception for all autodoc errors."""

    pass


class ScannerError(AutoDocError):
    """Error during code or artifact scanning."""

    pass


class LLMError(AutoDocError):
    """Error from LLM API calls."""

    pass


class GenerationError(AutoDocError):
    """Error during content generation."""

    pass


class BuilderError(AutoDocError):
    """Error during document building."""

    pass


class ConfigurationError(AutoDocError):
    """Error in configuration or settings."""

    pass


class SanitizationError(AutoDocError):
    """Error during content sanitization."""

    pass
