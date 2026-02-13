"""Tests for JWT token creation and validation."""

from __future__ import annotations

import time

import jwt as pyjwt
import pytest

from weatherbrief.api.jwt_utils import JWT_ALGORITHM, create_token, decode_token

SECRET = "test-secret-key"


class TestCreateToken:
    def test_round_trip(self):
        token = create_token("user-123", "test@example.com", "Test User", SECRET)
        payload = decode_token(token, SECRET)
        assert payload["sub"] == "user-123"
        assert payload["email"] == "test@example.com"
        assert payload["name"] == "Test User"
        assert "exp" in payload
        assert "iat" in payload

    def test_different_secrets_fail(self):
        token = create_token("user-123", "test@example.com", "Test", SECRET)
        with pytest.raises(pyjwt.InvalidSignatureError):
            decode_token(token, "wrong-secret")


class TestDecodeToken:
    def test_expired_token(self):
        # Create a token that's already expired
        payload = {
            "sub": "user-123",
            "email": "test@example.com",
            "name": "Test",
            "iat": time.time() - 3600,
            "exp": time.time() - 1,
        }
        token = pyjwt.encode(payload, SECRET, algorithm=JWT_ALGORITHM)
        with pytest.raises(pyjwt.ExpiredSignatureError):
            decode_token(token, SECRET)

    def test_invalid_token_string(self):
        with pytest.raises(pyjwt.InvalidTokenError):
            decode_token("not-a-jwt", SECRET)

    def test_tampered_payload(self):
        token = create_token("user-123", "test@example.com", "Test", SECRET)
        # Tamper with the payload section
        parts = token.split(".")
        parts[1] = parts[1][::-1]  # reverse the payload
        tampered = ".".join(parts)
        with pytest.raises(pyjwt.InvalidTokenError):
            decode_token(tampered, SECRET)
