"""Error bodies a route may return: class name plus first line, no tracebacks.

Provider SDKs (litellm, botocore, urllib) raise with multi-line messages that
carry request URLs, local file paths and sometimes the raw HTTP body. Several
routes used to return ``str(exc)`` verbatim, so a missing DeepSeek key on
staging (2026-09-22 audit) answered ``500`` with a litellm stack fragment
including ``/app/.venv/lib/python3.11/site-packages/...``. Every route that
catches a broad ``Exception`` goes through :func:`clean_error_message` and
:func:`error_status` so the body is one line and the status says what kind of
failure it was.
"""

from __future__ import annotations

import re

# Absolute POSIX/Windows paths and ``File "..."`` traceback frames.
_PATH_RE = re.compile(r'(File\s+")?(?:/[\w.\-@]+){2,}(?:\.py)?(?:",\s*line\s*\d+)?|[A-Za-z]:\\[^\s"]+')
_WS_RE = re.compile(r"\s+")

# Phrases the provider layer uses when a key is absent, plus the SDK exception
# names for a rejected/absent credential. Matched case-insensitively on the
# exception class name and its first line.
_MISSING_KEY_MARKERS = (
    "api key not configured",
    "no api key configured",
    "is not connected",
    "api_key",
    "api key",
    "authenticationerror",
    "authentication_error",
    "invalid_api_key",
    "missing credentials",
    "unable to locate credentials",
)


def first_line(text: object) -> str:
    """The first non-empty line of ``text``, whitespace-collapsed, paths removed."""
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if line:
            line = _PATH_RE.sub("<path>", line)
            return _WS_RE.sub(" ", line).strip()
    return ""


def clean_error_message(exc: BaseException | str, *, max_len: int = 300) -> str:
    """``ClassName: first line`` for an exception, or the cleaned first line of a string."""
    if isinstance(exc, BaseException):
        head = first_line(exc)
        name = type(exc).__name__
        message = f"{name}: {head}" if head else name
    else:
        message = first_line(exc)
    return message[:max_len]


def is_missing_key_error(exc: BaseException | str) -> bool:
    """True when the failure is an absent or rejected provider credential."""
    if isinstance(exc, BaseException):
        haystack = f"{type(exc).__name__} {first_line(exc)}".lower()
    else:
        haystack = first_line(exc).lower()
    return any(marker in haystack for marker in _MISSING_KEY_MARKERS)


def error_status(exc: BaseException, *, default: int = 500) -> int:
    """HTTP status for an exception a route caught: 503 for a missing provider key."""
    if is_missing_key_error(exc):
        return 503
    return default
