"""Where the deployment's data lives: the one place that decides ``DATA_DIR``.

Every reader of the data root (packs, GRIB cache, charts, archives, the dev DB's
neighbours) calls :func:`data_dir`. Nothing else reads ``DATA_DIR`` from the
environment; ``tests/test_data_dir_single_source.py`` keeps it that way.

The rule:

1. The checkout's dotenv files are loaded first (once per process, never
   overriding variables already set), so the answer does not depend on which
   entry point loaded ``.env`` or when. Before this module, ``create_app()``
   loaded ``.env`` only after importing every router, and a module that read
   ``DATA_DIR`` at import time silently got the fallback.
2. ``DATA_DIR`` when set.
3. Otherwise ``<checkout>/data``, never a path relative to whatever directory
   the process was started from. The docker image is an editable install at
   ``/app``, so that is ``/app/data``, which compose also sets as ``DATA_DIR``.
   A non-editable install has no checkout and gets ``./data``.

The result is always absolute. It is resolved on every call rather than
cached: reading one variable is free, and tests (and the odd CLI) set
``DATA_DIR`` after import.
"""

from __future__ import annotations

import os
from pathlib import Path

_env_loaded = False


def _ensure_env_loaded() -> None:
    global _env_loaded
    if _env_loaded:
        return
    _env_loaded = True
    from weatherbrief.envfiles import load_env

    load_env()


def checkout_root() -> Path | None:
    """The source checkout this package runs from, or None for a non-editable install.

    The single definition of "the checkout"; ``envfiles.default_root`` uses it too.
    """
    root = Path(__file__).resolve().parents[2]
    return root if (root / "pyproject.toml").is_file() else None


def data_dir() -> Path:
    """The data root: ``DATA_DIR``, else ``<checkout>/data``; always absolute."""
    _ensure_env_loaded()
    raw = os.environ.get("DATA_DIR")
    if raw:
        return Path(raw).expanduser().absolute()
    root = checkout_root()
    return (root / "data") if root is not None else Path("data").absolute()
