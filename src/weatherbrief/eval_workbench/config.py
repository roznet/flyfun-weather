"""Runtime gate + identity for the dev-only golden-labeling workbench.

These helpers are the single source of truth for "is the workbench on?" and for
the reserved flight-id namespace the virtual-flight resolver keys on. Keep this
module import-light (stdlib only) so the guard checks added to hot core paths
(``_load_flight_or_404``, ``load_pack_meta``, ``list_packs``) cost nothing in
production, where the feature is off.
"""

from __future__ import annotations

import os
from pathlib import Path

# A briefing opened with flight id ``eval-<corpus_id>`` is resolved from the
# file corpus instead of the database. The prefix is deliberately not a valid
# saved-flight slug shape, so it can never collide with a real flight.
EVAL_FLIGHT_PREFIX = "eval-"

_TRUTHY = {"1", "true", "yes", "on"}

DEFAULT_CORPUS_DIR = Path("tests/eval_data/corpus")

# The two areas of the eval set. ``staging`` is the scratch triage area where
# freshly-pulled briefings land (gitignored); ``corpus`` is the curated,
# committed set. Promotion moves a labelled pack from staging into corpus.
AREAS = ("staging", "corpus")


def eval_workbench_enabled() -> bool:
    """True when the workbench is enabled (dev only).

    Gated by ``WEATHERBRIEF_EVAL_WORKBENCH``. Unset/falsey in production, so the
    router is never mounted and the resolver guards are dead code there.
    """
    return os.getenv("WEATHERBRIEF_EVAL_WORKBENCH", "").strip().lower() in _TRUTHY


def eval_corpus_dir() -> Path:
    """Directory holding the pulled corpus packs + golden labels.

    Override with ``EVAL_CORPUS_DIR`` (absolute path recommended in a worktree,
    where there is no local ``data/``). Defaults to ``tests/eval_data/corpus``.
    """
    raw = os.getenv("EVAL_CORPUS_DIR", "").strip()
    return Path(raw).expanduser() if raw else DEFAULT_CORPUS_DIR


def eval_staging_dir() -> Path:
    """Directory holding the scratch staging packs (gitignored).

    Override with ``EVAL_STAGING_DIR``; otherwise derived as the ``staging``
    sibling of the corpus dir (the eval-repo layout: ``<repo>/corpus`` +
    ``<repo>/staging``).
    """
    raw = os.getenv("EVAL_STAGING_DIR", "").strip()
    if raw:
        return Path(raw).expanduser()
    return eval_corpus_dir().parent / "staging"


def area_root(area: str) -> Path:
    """Root directory for an eval area ("staging" | "corpus")."""
    if area == "staging":
        return eval_staging_dir()
    if area == "corpus":
        return eval_corpus_dir()
    raise ValueError(f"unknown eval area: {area!r} (expected one of {AREAS})")


def is_eval_flight_id(flight_id: str | None) -> bool:
    """True when ``flight_id`` is in the reserved eval namespace."""
    return isinstance(flight_id, str) and flight_id.startswith(EVAL_FLIGHT_PREFIX)


def corpus_id_from_flight_id(flight_id: str) -> str:
    """``eval-<corpus_id>`` -> ``<corpus_id>``."""
    return flight_id[len(EVAL_FLIGHT_PREFIX):]


def eval_flight_id(corpus_id: str) -> str:
    """``<corpus_id>`` -> ``eval-<corpus_id>`` (the synthetic flight id)."""
    return f"{EVAL_FLIGHT_PREFIX}{corpus_id}"
