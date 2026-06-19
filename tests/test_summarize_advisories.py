"""Unit tests for ``summarize_advisories`` — the flights-list card summary.

Covers the contract the flights-list card depends on (issue #276): RED/AMBER
counts, severity-ordered top chips (RED before AMBER), the 3-item cap, and
exclusion of GREEN / UNAVAILABLE advisories.
"""

from __future__ import annotations

from weatherbrief.models import (
    AdvisoryCatalogEntry,
    AdvisoryStatus,
    RouteAdvisoriesManifest,
    RouteAdvisoryResult,
)
from weatherbrief.tasks.advise import summarize_advisories


def _adv(advisory_id: str, status: AdvisoryStatus) -> RouteAdvisoryResult:
    return RouteAdvisoryResult(advisory_id=advisory_id, aggregate_status=status)


def _catalog(*ids_and_names: tuple[str, str]) -> list[AdvisoryCatalogEntry]:
    return [
        AdvisoryCatalogEntry(
            id=i,
            name=n,
            short_description="",
            description="",
            category="test",
        )
        for i, n in ids_and_names
    ]


def test_all_green_yields_empty_summary():
    manifest = RouteAdvisoriesManifest(
        advisories=[_adv("a", AdvisoryStatus.GREEN), _adv("b", AdvisoryStatus.GREEN)],
        catalog=_catalog(("a", "A"), ("b", "B")),
    )
    summary = summarize_advisories(manifest)
    assert summary.red == 0
    assert summary.amber == 0
    assert summary.top == []


def test_mixed_red_amber_ordering_and_counts():
    """RED entries come before AMBER in ``top``; counts reflect totals."""
    manifest = RouteAdvisoriesManifest(
        advisories=[
            _adv("conv", AdvisoryStatus.AMBER),
            _adv("ice", AdvisoryStatus.RED),
            _adv("wind", AdvisoryStatus.AMBER),
        ],
        catalog=_catalog(("conv", "Convection"), ("ice", "Icing"), ("wind", "Winds")),
    )
    summary = summarize_advisories(manifest)
    assert summary.red == 1
    assert summary.amber == 2
    # RED first regardless of manifest order, then AMBER.
    assert [(c.status, c.name) for c in summary.top] == [
        ("RED", "Icing"),
        ("AMBER", "Convection"),
        ("AMBER", "Winds"),
    ]


def test_top_capped_at_three():
    manifest = RouteAdvisoriesManifest(
        advisories=[
            _adv("r1", AdvisoryStatus.RED),
            _adv("r2", AdvisoryStatus.RED),
            _adv("a1", AdvisoryStatus.AMBER),
            _adv("a2", AdvisoryStatus.AMBER),
        ],
        catalog=_catalog(("r1", "R1"), ("r2", "R2"), ("a1", "A1"), ("a2", "A2")),
    )
    summary = summarize_advisories(manifest)
    # Counts are not capped...
    assert summary.red == 2
    assert summary.amber == 2
    # ...but the chip list is, keeping the most-severe first.
    assert len(summary.top) == 3
    assert [(c.status, c.name) for c in summary.top] == [
        ("RED", "R1"),
        ("RED", "R2"),
        ("AMBER", "A1"),
    ]


def test_unavailable_excluded():
    manifest = RouteAdvisoriesManifest(
        advisories=[
            _adv("u", AdvisoryStatus.UNAVAILABLE),
            _adv("g", AdvisoryStatus.GREEN),
            _adv("r", AdvisoryStatus.RED),
        ],
        catalog=_catalog(("u", "Unavail"), ("g", "Green"), ("r", "Red one")),
    )
    summary = summarize_advisories(manifest)
    assert summary.red == 1
    assert summary.amber == 0
    assert [(c.status, c.name) for c in summary.top] == [("RED", "Red one")]


def test_missing_catalog_entry_falls_back_to_id():
    """A chip whose advisory_id isn't in the catalog uses the id as the name."""
    manifest = RouteAdvisoriesManifest(
        advisories=[_adv("orphan", AdvisoryStatus.RED)],
        catalog=_catalog(),
    )
    summary = summarize_advisories(manifest)
    assert summary.top[0].name == "orphan"
