"""Tests for multi-language sanitizer patterns."""

import pytest

from autodoc.scanning.sanitizer import ContentSanitizer


class TestRSecretPatterns:
    def test_renviron_file_redacted(self):
        sanitizer = ContentSanitizer(extra_sensitive_files=[".Renviron", ".Rprofile"])
        result = sanitizer.sanitize_file_content(
            ".Renviron", "API_KEY=sk-abc123xyz"
        )
        assert "REDACTED" in result.sanitized_content
        assert result.was_modified

    def test_rprofile_file_redacted(self):
        sanitizer = ContentSanitizer(extra_sensitive_files=[".Renviron", ".Rprofile"])
        result = sanitizer.sanitize_file_content(
            ".Rprofile", "options(repos='http://cran.r-project.org')"
        )
        assert "REDACTED" in result.sanitized_content

    def test_normal_r_file_not_redacted(self):
        sanitizer = ContentSanitizer(extra_sensitive_files=[".Renviron", ".Rprofile"])
        result = sanitizer.sanitize_file_content(
            "model.R", "library(caret)\nmodel <- train(y ~ x, data=df)"
        )
        assert "library(caret)" in result.sanitized_content
        assert not result.was_modified


class TestSASSecretPatterns:
    def test_sas_let_password_redacted(self):
        sanitizer = ContentSanitizer(
            extra_patterns=[r"(?i)%let\s+(password|pwd)\s*="]
        )
        code = '%let password=mysecret123;\nPROC LOGISTIC;'
        result = sanitizer.sanitize(code)
        assert "[REDACTED]" in result.sanitized_content
        assert result.was_modified

    def test_sas_libname_user_redacted(self):
        sanitizer = ContentSanitizer(
            extra_patterns=[r"(?i)libname\s+\w+.*\b(user|password)\s*="]
        )
        code = 'libname mylib oracle user=admin password=secret;'
        result = sanitizer.sanitize(code)
        assert "[REDACTED]" in result.sanitized_content

    def test_sas_normal_code_not_redacted(self):
        sanitizer = ContentSanitizer(
            extra_patterns=[r"(?i)%let\s+(password|pwd)\s*="]
        )
        code = 'PROC LOGISTIC data=train;\nMODEL target = x1 x2;\nRUN;'
        result = sanitizer.sanitize(code)
        assert "PROC LOGISTIC" in result.sanitized_content


class TestExistingPythonPatternsStillWork:
    def test_env_file_still_redacted(self):
        sanitizer = ContentSanitizer()
        result = sanitizer.sanitize_file_content(".env", "SECRET=abc")
        assert "REDACTED" in result.sanitized_content

    def test_aws_key_still_redacted(self):
        sanitizer = ContentSanitizer()
        result = sanitizer.sanitize("AKIAIOSFODNN7EXAMPLE")
        assert "[REDACTED]" in result.sanitized_content

    def test_openai_key_still_redacted(self):
        sanitizer = ContentSanitizer()
        result = sanitizer.sanitize("sk-abcdefghijklmnopqrstuvwxyz1234567890")
        assert "[REDACTED]" in result.sanitized_content
