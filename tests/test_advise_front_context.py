"""Tests for ``_front_context`` — the two-control gating of the experimental
front advisory (issue #196, model B).

``auto_front_detection`` (master) generates the ``route_fronts.json`` artifact and
the overlays; the per-profile ``fronts`` advisory toggle independently controls
whether the GREEN/AMBER/RED grade surfaces. The artifact's presence enables the
advisory *by default*, but an explicit ``fronts: false`` opt-out is honored.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from weatherbrief.analysis.advisories.fronts import FRONTS_ADVISORY_ID
from weatherbrief.models import RouteFrontsManifest
from weatherbrief.tasks import advise


def _manifest() -> RouteFrontsManifest:
    return RouteFrontsManifest(
        generated_at=datetime(2026, 5, 31, tzinfo=timezone.utc),
        primary_level_hPa=850, levels=[850], models=["gfs"], per_model={},
    )


@pytest.fixture()
def with_artifact(monkeypatch):
    """Pretend ``route_fronts.json`` is on disk (i.e. the master was on)."""
    manifest = _manifest()
    monkeypatch.setattr(
        "weatherbrief.tasks.artifacts.load_route_fronts", lambda _pack: manifest,
    )
    return manifest


def test_no_pack_dir_is_noop():
    fronts, enabled = advise._front_context(None, {"icing"})
    assert fronts is None
    assert enabled == {"icing"}


def test_missing_artifact_is_noop(monkeypatch):
    monkeypatch.setattr(
        "weatherbrief.tasks.artifacts.load_route_fronts", lambda _pack: None,
    )
    fronts, enabled = advise._front_context(Path("/x"), {"icing"})
    assert fronts is None
    assert enabled == {"icing"}


def test_artifact_present_injects_by_default(with_artifact):
    """Master on + no explicit advisory choice → advisory surfaces."""
    fronts, enabled = advise._front_context(Path("/x"), {"icing"})
    assert fronts is with_artifact
    assert FRONTS_ADVISORY_ID in enabled
    assert "icing" in enabled  # other advisories preserved


def test_explicit_enable_injects(with_artifact):
    fronts, enabled = advise._front_context(
        Path("/x"), {"icing"}, advisory_enabled={FRONTS_ADVISORY_ID: True},
    )
    assert FRONTS_ADVISORY_ID in enabled


def test_explicit_disable_suppresses_advisory_but_keeps_data(with_artifact):
    """Master on + explicit ``fronts: false`` → overlays (data) stay, grade hidden."""
    fronts, enabled = advise._front_context(
        Path("/x"), {"icing"}, advisory_enabled={FRONTS_ADVISORY_ID: False},
    )
    assert fronts is with_artifact  # data still available for overlays
    assert FRONTS_ADVISORY_ID not in enabled
    assert enabled == {"icing"}


def test_unrelated_disable_still_injects(with_artifact):
    """An explicit disable of a *different* advisory must not suppress fronts."""
    _fronts, enabled = advise._front_context(
        Path("/x"), {"icing"}, advisory_enabled={"convective": False},
    )
    assert FRONTS_ADVISORY_ID in enabled


def test_none_enabled_ids_materializes_defaults(with_artifact):
    """When enabled_ids is None we expand the default catalog set + fronts,
    rather than collapsing to a single-element set that would disable others."""
    _fronts, enabled = advise._front_context(Path("/x"), None)
    assert FRONTS_ADVISORY_ID in enabled
    # At least one other default-enabled advisory should be present.
    assert len(enabled) > 1
