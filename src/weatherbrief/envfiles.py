"""Load a checkout's dotenv files: `.env` (config), then `.env.secrets` (secrets).

Config and secrets live in separate files so the plain config can be read and edited
freely, while `.env.secrets` is the sops-managed file nobody opens by hand (the owner's
config repo encrypts it; syncfiles decrypts it on the host that uses it). Each key
should live in exactly one of the two files.

Precedence matches `load_dotenv()`'s default: variables already in the environment win
(docker compose, the Mac mini runners that `source` both files, a test's monkeypatch),
then `.env`, then `.env.secrets`. A missing file is skipped silently: CI and the triage
sandbox have neither, and an older checkout may have only `.env`.

`dotenv.load_dotenv` is looked up on the module at call time, never imported by name,
so the no-op stub in `tests/conftest.py` and `mock.patch("dotenv.load_dotenv")` apply.
"""
from __future__ import annotations

from pathlib import Path

import dotenv

ENV_FILES = (".env", ".env.secrets")


def default_root() -> Path | None:
    """The checkout root (`weatherbrief.paths.checkout_root`) when it holds a dotenv file.

    One definition of "the checkout" for both the dotenv files and the data root's
    fallback. None when there is no checkout or it has neither file: CI, and the
    docker image, whose compose file provides the environment instead.
    """
    from weatherbrief.paths import checkout_root

    root = checkout_root()
    if root is not None and any((root / name).is_file() for name in ENV_FILES):
        return root
    return None


def env_paths(root: Path | str | None = None) -> list[Path]:
    """The dotenv files `load_env(root)` would read, in load order (existing ones only)."""
    base = Path(root) if root is not None else default_root()
    if base is None:
        return []
    return [base / name for name in ENV_FILES if (base / name).is_file()]


def load_env(root: Path | str | None = None, *, override: bool = False) -> bool:
    """Load `<root>/.env` then `<root>/.env.secrets` into `os.environ`.

    `root` defaults to the checkout this package runs from. With `override=False`
    (the default) values already set in the environment are kept. Returns True if any
    variable was set.
    """
    loaded = False
    for path in env_paths(root):
        loaded = bool(dotenv.load_dotenv(path, override=override)) or loaded
    return loaded
