"""Fernet encryption for storing sensitive credentials at rest."""

from __future__ import annotations

import base64
import hashlib
import os

from cryptography.fernet import Fernet


def _get_fernet_key() -> bytes:
    """Get the Fernet encryption key.

    Uses CREDENTIAL_ENCRYPTION_KEY env var if set.
    In dev mode, derives a key from JWT_SECRET as a fallback.
    """
    explicit_key = os.environ.get("CREDENTIAL_ENCRYPTION_KEY")
    if explicit_key:
        return explicit_key.encode()

    from weatherbrief.api.auth_config import get_jwt_secret, is_dev_mode

    if is_dev_mode():
        # Derive a stable Fernet key from the JWT secret
        digest = hashlib.sha256(get_jwt_secret().encode()).digest()
        return base64.urlsafe_b64encode(digest)

    raise ValueError(
        "CREDENTIAL_ENCRYPTION_KEY must be set in production. "
        "Generate with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
    )


def encrypt(plaintext: str) -> str:
    """Encrypt a plaintext string, returning a Fernet token as a string."""
    f = Fernet(_get_fernet_key())
    return f.encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    """Decrypt a Fernet token string back to plaintext."""
    f = Fernet(_get_fernet_key())
    return f.decrypt(ciphertext.encode()).decode()
