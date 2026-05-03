"""Tests for the unified Diagnostic model."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

import pytest

from weatherbrief.models import (
    DETAIL_MAX_BYTES,
    Diagnostic,
    DiagnosticPublic,
    DigestCode,
    FetchCode,
)


class TestLegacyRoundTrip:
    """Critical: legacy DB rows must validate without minting fresh IDs."""

    def test_legacy_two_field_dict_validates(self):
        legacy = {"level": "warn", "message": "GFS forecast fetch failed"}
        d = Diagnostic.model_validate(legacy)
        assert d.level == "warn"
        assert d.message == "GFS forecast fetch failed"
        assert d.error_id is None
        assert d.occurred_at is None
        assert d.code is None
        assert d.stage is None

    def test_legacy_round_trip_does_not_mint_uuid(self):
        legacy = {"level": "info", "message": "ICON skipped (range)"}
        d1 = Diagnostic.model_validate(legacy)
        d2 = Diagnostic.model_validate(legacy)
        # Same input should not produce different UUIDs on each parse — would
        # mean every read of the same row generates a "new" diagnostic.
        assert d1.error_id is None
        assert d2.error_id is None

    def test_unknown_extra_fields_ignored(self):
        legacy_with_extras = {
            "level": "warn",
            "message": "old message",
            "future_field_we_added_later": "whatever",
        }
        d = Diagnostic.model_validate(legacy_with_extras)
        assert d.message == "old message"


class TestCreate:
    """Diagnostic.create() mints fresh UUID + timestamp."""

    def test_create_mints_error_id(self):
        d = Diagnostic.create(
            level="warn",
            stage="digest",
            code=DigestCode.ANTHROPIC_INTERNAL_ERROR,
            message="msg",
        )
        assert isinstance(d.error_id, UUID)

    def test_create_mints_occurred_at_utc(self):
        before = datetime.now(timezone.utc)
        d = Diagnostic.create(
            level="warn", stage="digest", code="x", message="y",
        )
        after = datetime.now(timezone.utc)
        assert d.occurred_at is not None
        assert d.occurred_at.tzinfo is not None
        assert before <= d.occurred_at <= after

    def test_create_each_call_unique_id(self):
        d1 = Diagnostic.create(level="warn", stage="s", code="c", message="m")
        d2 = Diagnostic.create(level="warn", stage="s", code="c", message="m")
        assert d1.error_id != d2.error_id

    def test_str_enum_code_serializes(self):
        d = Diagnostic.create(
            level="warn",
            stage="fetch",
            code=FetchCode.MODEL_FETCH_FAILED,
            message="m",
        )
        # StrEnum should serialize as the string value
        dumped = d.model_dump(mode="json")
        assert dumped["code"] == "model_fetch_failed"


class TestDetailCap:
    def test_short_detail_unchanged(self):
        d = Diagnostic.create(
            level="error", stage="digest", code="x", message="m",
            detail="short text",
        )
        assert d.detail == "short text"

    def test_long_detail_truncated(self):
        big = "x" * (DETAIL_MAX_BYTES + 1000)
        d = Diagnostic.create(
            level="error", stage="digest", code="x", message="m", detail=big,
        )
        assert d.detail is not None
        encoded = d.detail.encode("utf-8")
        # Truncated body + suffix marker; the body itself is at most
        # DETAIL_MAX_BYTES.
        body, _, marker = d.detail.partition("\n... [truncated,")
        assert len(body.encode("utf-8")) <= DETAIL_MAX_BYTES
        assert marker.startswith(" ")
        # And the suffix mentions the original size
        assert "bytes total]" in d.detail
        # Total stays bounded (truncation marker is small, fixed-ish)
        assert len(encoded) < DETAIL_MAX_BYTES + 200

    def test_none_detail_stays_none(self):
        d = Diagnostic.create(
            level="warn", stage="s", code="c", message="m", detail=None,
        )
        assert d.detail is None


class TestRedaction:
    def test_bearer_token_redacted(self):
        d = Diagnostic.create(
            level="error", stage="digest", code="x", message="m",
            detail="Authorization: Bearer abc123XYZ_secret-token-value",
        )
        assert "abc123XYZ_secret-token-value" not in d.detail
        assert "<REDACTED>" in d.detail

    def test_api_key_prefix_redacted(self):
        d = Diagnostic.create(
            level="error", stage="digest", code="x", message="m",
            detail="key=sk-ant-abcd1234efgh5678ijkl9012mnop in payload",
        )
        assert "sk-ant-abcd1234efgh5678ijkl9012mnop" not in d.detail
        assert "<REDACTED_API_KEY>" in d.detail

    def test_query_string_token_redacted(self):
        d = Diagnostic.create(
            level="warn", stage="gramet", code="x", message="m",
            detail="GET /api?api_key=mysecret123&other=ok",
        )
        assert "mysecret123" not in d.detail
        assert "<REDACTED>" in d.detail
        assert "other=ok" in d.detail


class TestSerialization:
    def test_dump_includes_new_fields(self):
        d = Diagnostic.create(
            level="warn",
            stage="digest",
            code=DigestCode.ANTHROPIC_OVERLOADED,
            message="hi",
            detail="trace",
            request_id="req_abc",
        )
        dumped = d.model_dump(mode="json")
        assert dumped["level"] == "warn"
        assert dumped["stage"] == "digest"
        assert dumped["code"] == "anthropic_overloaded"
        assert dumped["message"] == "hi"
        assert dumped["detail"] == "trace"
        assert dumped["request_id"] == "req_abc"
        assert "error_id" in dumped
        assert "occurred_at" in dumped

    def test_round_trip_via_json(self):
        d1 = Diagnostic.create(
            level="error", stage="digest",
            code=DigestCode.DIGEST_UNKNOWN, message="m", detail="d",
        )
        dumped = d1.model_dump(mode="json")
        d2 = Diagnostic.model_validate(dumped)
        assert d2.level == d1.level
        assert d2.code == d1.code
        assert d2.error_id == d1.error_id
        # occurred_at preserved through JSON serialization
        assert d2.occurred_at == d1.occurred_at


class TestInvalidLevel:
    def test_unknown_level_rejected(self):
        with pytest.raises(Exception):
            Diagnostic.model_validate({"level": "fatal", "message": "m"})


class TestPublicProjection:
    """DiagnosticPublic must not leak debug fields to API clients."""

    def test_to_public_strips_detail_and_request_id(self):
        d = Diagnostic.create(
            level="warn", stage="digest",
            code=DigestCode.ANTHROPIC_INTERNAL_ERROR,
            message="visible",
            detail="Traceback (most recent call last):\n  File '/app/...'",
            request_id="req_abc123",
        )
        pub = d.to_public()

        # Debug fields gone
        dumped = pub.model_dump(mode="json")
        assert "detail" not in dumped
        assert "request_id" not in dumped

        # User-facing fields preserved
        assert dumped["level"] == "warn"
        assert dumped["message"] == "visible"
        assert dumped["stage"] == "digest"
        assert dumped["code"] == "anthropic_internal_error"

        # error_id IS exposed (user-quotable support id)
        assert "error_id" in dumped
        assert dumped["error_id"] is not None

    def test_to_public_round_trip_via_json(self):
        # Verifies the JSON shape is wire-safe AND survives a model
        # round-trip without re-introducing stripped fields. Earlier
        # version asserted hasattr() on the reconstructed model, which
        # is vacuously true (DiagnosticPublic doesn't declare those
        # fields, so hasattr returns False regardless of input).
        d = Diagnostic.create(
            level="error", stage="digest",
            code=DigestCode.DIGEST_UNKNOWN, message="m",
            detail="secret", request_id="req_x",
        )
        pub_json = d.to_public().model_dump(mode="json")

        # Sanity: payload itself does not carry the stripped fields.
        assert "detail" not in pub_json
        assert "request_id" not in pub_json

        # Round-trip: re-dump after validate must also be clean.
        reconstructed_json = (
            DiagnosticPublic.model_validate(pub_json).model_dump(mode="json")
        )
        assert "detail" not in reconstructed_json
        assert "request_id" not in reconstructed_json
        # And the payload that DID survive matches.
        assert reconstructed_json["code"] == "digest_unknown"
        assert reconstructed_json["message"] == "m"

    def test_legacy_diagnostic_to_public_safe(self):
        # Legacy {level, message} entry survives to_public without errors
        legacy = Diagnostic.model_validate({"level": "warn", "message": "old"})
        pub = legacy.to_public()
        assert pub.level == "warn"
        assert pub.message == "old"
        assert pub.error_id is None
        assert pub.code is None


class TestParseDiagnosticsContainment:
    """The DB-row parser must not crash a whole flight listing on one bad entry."""

    def test_skips_malformed_entry_keeps_valid(self):
        import json

        from weatherbrief.storage.flights import _parse_diagnostics

        raw = json.dumps([
            {"level": "warn", "message": "good entry"},
            {"level": "fatal", "message": "unknown level"},  # malformed
            {"level": "info", "message": "another good entry"},
        ])
        result = _parse_diagnostics(raw)
        assert len(result) == 2
        assert result[0].message == "good entry"
        assert result[1].message == "another good entry"

    def test_empty_and_dict_inputs_safe(self):
        from weatherbrief.storage.flights import _parse_diagnostics

        assert _parse_diagnostics(None) == []
        assert _parse_diagnostics("") == []
        assert _parse_diagnostics("{}") == []  # very-old-row shape
