"""OAuth and JWT configuration."""

from __future__ import annotations

import os

from authlib.integrations.starlette_client import OAuth

COOKIE_NAME = "session"

# Insecure default for local dev only — production MUST set JWT_SECRET env var
_DEV_JWT_SECRET = "dev-insecure-jwt-secret-do-not-use-in-production"


def is_dev_mode() -> bool:
    return os.environ.get("ENVIRONMENT", "development") != "production"


def get_jwt_secret() -> str:
    secret = os.environ.get("JWT_SECRET")
    if secret:
        return secret
    if is_dev_mode():
        return _DEV_JWT_SECRET
    raise ValueError("JWT_SECRET environment variable must be set in production")


def create_oauth() -> OAuth:
    """Create and configure the OAuth registry with Google provider."""
    oauth = OAuth()
    oauth.register(
        name="google",
        client_id=os.environ.get("GOOGLE_CLIENT_ID", ""),
        client_secret=os.environ.get("GOOGLE_CLIENT_SECRET", ""),
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile"},
    )
    return oauth
