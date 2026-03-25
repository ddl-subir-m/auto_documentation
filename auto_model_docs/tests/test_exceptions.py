"""Tests for autodoc/core/exceptions.py — custom exception hierarchy."""

import pytest

from autodoc.core.exceptions import (
    AutoDocError,
    BuilderError,
    ConfigurationError,
    GenerationError,
    LLMError,
    SanitizationError,
    ScannerError,
)


# All concrete exception classes to test
EXCEPTION_CLASSES = [
    ScannerError,
    LLMError,
    GenerationError,
    BuilderError,
    ConfigurationError,
    SanitizationError,
]


# ---------------------------------------------------------------------------
# Subclass relationship
# ---------------------------------------------------------------------------


class TestSubclassHierarchy:
    """Every exception class is a subclass of AutoDocError (and Exception)."""

    @pytest.mark.parametrize("exc_cls", EXCEPTION_CLASSES, ids=lambda c: c.__name__)
    def test_is_subclass_of_autodoc_error(self, exc_cls):
        assert issubclass(exc_cls, AutoDocError)

    @pytest.mark.parametrize("exc_cls", EXCEPTION_CLASSES, ids=lambda c: c.__name__)
    def test_is_subclass_of_exception(self, exc_cls):
        assert issubclass(exc_cls, Exception)

    def test_autodoc_error_is_subclass_of_exception(self):
        assert issubclass(AutoDocError, Exception)

    def test_autodoc_error_is_not_subclass_of_runtime_error(self):
        # Verify it inherits directly from Exception, not RuntimeError/ValueError/etc.
        assert not issubclass(AutoDocError, RuntimeError)
        assert not issubclass(AutoDocError, ValueError)


# ---------------------------------------------------------------------------
# Message propagation
# ---------------------------------------------------------------------------


class TestMessagePropagation:
    """Exception messages are accessible via str() and args."""

    @pytest.mark.parametrize("exc_cls", EXCEPTION_CLASSES, ids=lambda c: c.__name__)
    def test_message_in_str(self, exc_cls):
        exc = exc_cls("something went wrong")
        assert str(exc) == "something went wrong"

    @pytest.mark.parametrize("exc_cls", EXCEPTION_CLASSES, ids=lambda c: c.__name__)
    def test_message_in_args(self, exc_cls):
        exc = exc_cls("detail")
        assert exc.args == ("detail",)

    def test_empty_message(self):
        exc = AutoDocError()
        assert str(exc) == ""
        assert exc.args == ()

    def test_multiple_args(self):
        exc = AutoDocError("msg", 42)
        assert exc.args == ("msg", 42)

    @pytest.mark.parametrize("exc_cls", EXCEPTION_CLASSES, ids=lambda c: c.__name__)
    def test_long_message_preserved(self, exc_cls):
        long_msg = "Error: " + "x" * 1000
        exc = exc_cls(long_msg)
        assert str(exc) == long_msg


# ---------------------------------------------------------------------------
# Raise and catch by parent class
# ---------------------------------------------------------------------------


class TestRaiseAndCatch:
    """Each exception can be raised and caught by AutoDocError."""

    @pytest.mark.parametrize("exc_cls", EXCEPTION_CLASSES, ids=lambda c: c.__name__)
    def test_catch_by_autodoc_error(self, exc_cls):
        with pytest.raises(AutoDocError):
            raise exc_cls("test error")

    @pytest.mark.parametrize("exc_cls", EXCEPTION_CLASSES, ids=lambda c: c.__name__)
    def test_catch_by_own_type(self, exc_cls):
        with pytest.raises(exc_cls):
            raise exc_cls("specific error")

    @pytest.mark.parametrize("exc_cls", EXCEPTION_CLASSES, ids=lambda c: c.__name__)
    def test_catch_by_exception(self, exc_cls):
        with pytest.raises(Exception):
            raise exc_cls("generic catch")

    def test_scanner_error_not_caught_by_llm_error(self):
        """Sibling exceptions do not catch each other."""
        with pytest.raises(ScannerError):
            try:
                raise ScannerError("scan failed")
            except LLMError:
                pytest.fail("ScannerError should not be caught by LLMError")

    def test_generation_error_not_caught_by_builder_error(self):
        with pytest.raises(GenerationError):
            try:
                raise GenerationError("gen failed")
            except BuilderError:
                pytest.fail("GenerationError should not be caught by BuilderError")


# ---------------------------------------------------------------------------
# Identity checks
# ---------------------------------------------------------------------------


class TestIdentity:
    """Verify each class is distinct."""

    def test_all_classes_are_distinct(self):
        seen = set()
        for cls in EXCEPTION_CLASSES:
            assert cls not in seen, f"Duplicate class: {cls.__name__}"
            seen.add(cls)

    def test_classes_have_expected_names(self):
        expected_names = {
            "ScannerError",
            "LLMError",
            "GenerationError",
            "BuilderError",
            "ConfigurationError",
            "SanitizationError",
        }
        actual_names = {cls.__name__ for cls in EXCEPTION_CLASSES}
        assert actual_names == expected_names

    @pytest.mark.parametrize("exc_cls", EXCEPTION_CLASSES, ids=lambda c: c.__name__)
    def test_isinstance_of_base(self, exc_cls):
        instance = exc_cls("test")
        assert isinstance(instance, AutoDocError)
        assert isinstance(instance, Exception)
