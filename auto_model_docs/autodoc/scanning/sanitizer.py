"""Content sanitization to remove secrets before sending to LLM."""

import re
from dataclasses import dataclass, field
from typing import List, Optional, Pattern, Set


@dataclass
class SanitizationResult:
    """Result of sanitizing content."""

    sanitized_content: str
    redactions: List[str] = field(default_factory=list)
    was_modified: bool = False


class ContentSanitizer:
    """Sanitizes content by redacting secrets and sensitive information.

    This is a critical security component that ensures no secrets,
    credentials, or sensitive data are sent to the LLM API.
    """

    # Regex patterns to detect secrets
    SECRET_PATTERNS = [
        # API Keys (generic)
        r'(?i)(api[_-]?key|apikey|api_secret)\s*[=:]\s*["\']?[\w\-]{20,}["\']?',
        # Secret tokens
        r'(?i)(secret|token|bearer)\s*[=:]\s*["\']?[\w\-]{20,}["\']?',
        # Passwords
        r'(?i)(password|passwd|pwd)\s*[=:]\s*["\']?[^\s"\']{8,}["\']?',
        # AWS credentials
        r'(?i)(aws[_-]?access[_-]?key[_-]?id?|aws[_-]?secret[_-]?access[_-]?key)\s*[=:]\s*["\']?[\w\-]+["\']?',
        # AWS Access Key IDs (AKIA...)
        r'AKIA[0-9A-Z]{16}',
        # Database connection strings
        r'(?i)(postgres|mysql|mongodb|redis|mssql)://[^\s]+',
        # Private keys
        r'-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----',
        # Internal IP addresses
        r'\b(?:10|172\.(?:1[6-9]|2\d|3[01])|192\.168)\.\d{1,3}\.\d{1,3}\b',
        # GitHub tokens
        r'gh[pousr]_[A-Za-z0-9_]{36,}',
        # Slack tokens
        r'xox[baprs]-[0-9a-zA-Z]{10,}',
        # Anthropic API keys
        r'sk-ant-[a-zA-Z0-9\-_]{20,}',
        # OpenAI API keys
        r'sk-[a-zA-Z0-9]{20,}',
    ]

    # Keywords that often precede sensitive values
    SENSITIVE_KEYWORDS: Set[str] = {
        "credential",
        "credentials",
        "secret",
        "private",
        "password",
        "ssn",
        "social_security",
        "credit_card",
        "api_key",
        "access_token",
        "refresh_token",
        "auth_token",
        "bearer",
        "private_key",
    }

    def __init__(self, max_length: int = 50000, extra_patterns: Optional[List[str]] = None,
                 extra_sensitive_files: Optional[List[str]] = None):
        """Initialize the sanitizer.

        Args:
            max_length: Maximum content length. Content longer than this
                       will be truncated.
            extra_patterns: Additional regex patterns for language-specific secrets.
            extra_sensitive_files: Additional file names/patterns to redact entirely
                (e.g., [".Renviron", ".Rprofile"] for R projects).
        """
        self.max_length = max_length
        all_patterns = list(self.SECRET_PATTERNS)
        if extra_patterns:
            all_patterns.extend(extra_patterns)
        self._compiled_patterns: List[Pattern] = [
            re.compile(p, re.MULTILINE) for p in all_patterns
        ]
        self._extra_sensitive_files: List[str] = extra_sensitive_files or []

    def sanitize(self, content: str) -> SanitizationResult:
        """Sanitize content by redacting secrets.

        Args:
            content: The content to sanitize.

        Returns:
            SanitizationResult with sanitized content and redaction log.
        """
        redactions: List[str] = []
        sanitized = content

        # Apply pattern-based redaction
        for i, pattern in enumerate(self._compiled_patterns):
            matches = pattern.findall(sanitized)
            if matches:
                match_count = len(matches) if isinstance(matches[0], str) else len(matches)
                redactions.append(f"Pattern {i + 1}: {match_count} match(es) redacted")
                sanitized = pattern.sub("[REDACTED]", sanitized)

        # Apply keyword-based redaction
        for keyword in self.SENSITIVE_KEYWORDS:
            # Match keyword followed by = or : and a value
            kw_pattern = re.compile(
                rf'\b\w*{re.escape(keyword)}\w*\s*[=:]\s*["\']?[^"\'\s\n]+["\']?',
                re.IGNORECASE,
            )
            if kw_pattern.search(sanitized):
                redactions.append(f"Keyword '{keyword}' redacted")
                sanitized = kw_pattern.sub(f"[{keyword.upper()}_REDACTED]", sanitized)

        # Truncate if needed
        if len(sanitized) > self.max_length:
            original_length = len(sanitized)
            sanitized = sanitized[: self.max_length]
            sanitized += f"\n\n... [TRUNCATED: {original_length - self.max_length} characters removed]"
            redactions.append(
                f"Truncated from {original_length} to {self.max_length} characters"
            )

        return SanitizationResult(
            sanitized_content=sanitized,
            redactions=redactions,
            was_modified=len(redactions) > 0,
        )

    def sanitize_file_content(self, filepath: str, content: str) -> SanitizationResult:
        """Sanitize file content with additional file-specific checks.

        Args:
            filepath: Path to the file (for context).
            content: File content to sanitize.

        Returns:
            SanitizationResult with sanitized content.
        """
        # Skip certain file types entirely
        sensitive_files = {".env", ".pem", ".key", "credentials", "secrets"}
        sensitive_files.update(s.lower() for s in self._extra_sensitive_files)
        for sensitive in sensitive_files:
            if sensitive in filepath.lower():
                return SanitizationResult(
                    sanitized_content=f"[FILE CONTENTS REDACTED: {filepath}]",
                    redactions=[f"Entire file redacted: {filepath}"],
                    was_modified=True,
                )

        return self.sanitize(content)
