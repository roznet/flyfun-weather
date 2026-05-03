"""Tests for the digest exception classifier.

Verifies that each Anthropic exception type maps to the right
DigestCode, that messages are user-safe (no leaked SDK state), and
that the catch-all path produces a usable Diagnostic.
"""

from __future__ import annotations

import anthropic
import pytest

from weatherbrief.digest.exceptions import classify_llm_exception
from weatherbrief.models import DigestCode


def _make(exc_cls: type[Exception], **attrs):
    """Construct an exception instance bypassing __init__.

    Anthropic exception classes require a real response object; for
    ``isinstance`` checks in the classifier we just need the class
    identity. ``__new__`` gets us a typed instance without the SDK's
    construction ceremony.
    """
    exc = exc_cls.__new__(exc_cls)
    for k, v in attrs.items():
        setattr(exc, k, v)
    return exc


class TestKnownAnthropicExceptions:
    @pytest.mark.parametrize("exc_cls,expected_code,expected_level", [
        (anthropic.RateLimitError,        DigestCode.ANTHROPIC_RATE_LIMITED,    "warn"),
        (anthropic.APITimeoutError,       DigestCode.ANTHROPIC_TIMEOUT,         "warn"),
        (anthropic.APIConnectionError,    DigestCode.ANTHROPIC_CONNECTION,      "warn"),
        (anthropic.AuthenticationError,   DigestCode.ANTHROPIC_AUTH_ERROR,      "error"),
        (anthropic.PermissionDeniedError, DigestCode.ANTHROPIC_AUTH_ERROR,      "error"),
        (anthropic.BadRequestError,       DigestCode.DIGEST_BAD_REQUEST,        "error"),
        (anthropic.InternalServerError,   DigestCode.ANTHROPIC_INTERNAL_ERROR,  "warn"),
    ])
    def test_classify(self, exc_cls, expected_code, expected_level):
        exc = _make(exc_cls, request_id="req_test_xyz")
        d = classify_llm_exception(exc)
        assert d.code == expected_code
        assert d.level == expected_level
        assert d.stage == "digest"
        assert d.request_id == "req_test_xyz"

    def test_overloaded_529_via_status_code(self):
        # OverloadedError isn't exported at the top level — caught via
        # APIStatusError + status_code==529.
        exc = _make(anthropic.APIStatusError, status_code=529, request_id=None)
        d = classify_llm_exception(exc)
        assert d.code == DigestCode.ANTHROPIC_OVERLOADED
        assert d.level == "warn"


class TestAuthErrorMessage:
    """Auth failures must not expose server-side state in user message."""

    def test_message_does_not_mention_api_key(self):
        for cls in (anthropic.AuthenticationError, anthropic.PermissionDeniedError):
            d = classify_llm_exception(_make(cls))
            # Don't echo "API key rejected" or similar — that's server
            # state leaking through the user-facing message.
            assert "key" not in d.message.lower()
            assert "rejected" not in d.message.lower()
            # And the message MUST not suggest retry (retry won't help).
            assert "try refresh" not in d.message.lower()
            assert "try again" not in d.message.lower()


class TestFallback:
    def test_unknown_exception_falls_through(self):
        d = classify_llm_exception(ValueError("not anthropic at all"))
        assert d.code == DigestCode.DIGEST_UNKNOWN
        assert d.level == "error"
        assert d.stage == "digest"
        # Detail should contain the original exception text
        assert "not anthropic at all" in (d.detail or "")
