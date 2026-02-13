"""Tests for Fernet encryption utilities."""

from __future__ import annotations

import pytest


class TestEncryption:
    """Test encrypt/decrypt round-trip and key derivation."""

    def test_round_trip(self, monkeypatch):
        """Encrypt then decrypt returns original plaintext."""
        monkeypatch.setenv("ENVIRONMENT", "development")
        monkeypatch.delenv("CREDENTIAL_ENCRYPTION_KEY", raising=False)

        from weatherbrief.api.encryption import decrypt, encrypt

        plaintext = '{"username": "alice", "password": "s3cret!"}'
        ciphertext = encrypt(plaintext)
        assert ciphertext != plaintext
        assert decrypt(ciphertext) == plaintext

    def test_explicit_key(self, monkeypatch):
        """Uses CREDENTIAL_ENCRYPTION_KEY when set."""
        from cryptography.fernet import Fernet

        key = Fernet.generate_key().decode()
        monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", key)

        from weatherbrief.api.encryption import decrypt, encrypt

        plaintext = "test-data"
        ciphertext = encrypt(plaintext)
        assert decrypt(ciphertext) == plaintext

    def test_production_requires_explicit_key(self, monkeypatch):
        """In production, missing CREDENTIAL_ENCRYPTION_KEY raises ValueError."""
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("JWT_SECRET", "some-jwt-secret")
        monkeypatch.delenv("CREDENTIAL_ENCRYPTION_KEY", raising=False)

        from weatherbrief.api.encryption import encrypt

        with pytest.raises(ValueError, match="CREDENTIAL_ENCRYPTION_KEY must be set"):
            encrypt("test")

    def test_dev_derives_from_jwt_secret(self, monkeypatch):
        """In dev mode, key is derived from JWT_SECRET."""
        monkeypatch.setenv("ENVIRONMENT", "development")
        monkeypatch.delenv("CREDENTIAL_ENCRYPTION_KEY", raising=False)
        monkeypatch.setenv("JWT_SECRET", "my-dev-secret")

        from weatherbrief.api.encryption import decrypt, encrypt

        ct = encrypt("hello")
        assert decrypt(ct) == "hello"

    def test_wrong_key_fails(self, monkeypatch):
        """Decrypting with a different key raises an error."""
        from cryptography.fernet import Fernet, InvalidToken

        key1 = Fernet.generate_key().decode()
        key2 = Fernet.generate_key().decode()

        monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", key1)
        from weatherbrief.api.encryption import encrypt

        ciphertext = encrypt("secret")

        monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", key2)
        from weatherbrief.api.encryption import decrypt

        with pytest.raises(InvalidToken):
            decrypt(ciphertext)
