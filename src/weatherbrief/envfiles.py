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
    """The nearest directory above this package holding a dotenv file.

    Same answer as `load_dotenv()`'s `find_dotenv()` for an editable install (the
    checkout root); None for an installed copy (docker image), where compose provides
    the environment instead.
    """
    for d in Path(__file__).resolve().parents:
        if any((d / name).is_file() for name in ENV_FILES):
            return d
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
