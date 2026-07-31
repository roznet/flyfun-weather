"""The declared-approach containment guardrail (issue #510, guardrail 1).

A pilot's declaration that they have an unpublished/self-briefed approach is a
personal operational assertion. It carries **no regulatory weight**: a
self-briefed approach cannot make a field qualify as a filed alternate under
EASA NCO.OP.143, and cannot relieve the NCO.OP.140 trigger. If it ever set
``has_instrument_approach`` in the shared airport path, the alternates table and
the FAA/EASA verdicts would start making regulatory claims out of it.

Containment is structural today: ``tasks/alternates.py`` runs its own
``procedures_query.approaches()`` against euro_aip and never calls
``get_runway_approaches``, the one function that knows about declarations. These
tests pin that structure, because the failure they guard against is silent — a
wrongly-qualified alternate looks exactly like a correctly-qualified one.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from weatherbrief.airports import get_runway_approaches
from weatherbrief.models.airport_conditions import AirportApproaches

SRC = Path(__file__).resolve().parents[1] / "src" / "weatherbrief"

# The modules that turn approach availability into a regulatory statement.
REGULATORY_MODULES = [
    SRC / "tasks" / "alternates.py",
    SRC / "tasks" / "alternate_requirement.py",
    SRC / "analysis" / "alternate_requirement.py",
]

# Names that only exist to carry a declaration. Any of them appearing in a
# module above means the declaration has reached the regulatory path.
DECLARATION_NAMES = (
    "declared_approach_icaos",
    "load_declared_approaches",
    "has_declared_approach",
    "SOURCE_USER_DECLARED",
    "user_declared",
    "get_runway_approaches",
)


@pytest.mark.parametrize("path", REGULATORY_MODULES, ids=lambda p: p.name)
def test_regulatory_modules_never_see_a_declaration(path):
    """No alternate-minima module may reference the declaration machinery."""
    source = path.read_text()
    for name in DECLARATION_NAMES:
        assert name not in source, (
            f"{path.name} references {name!r}. A user-declared unpublished "
            "approach must never reach the alternates or alternate-requirement "
            "path — see #510 guardrail 1."
        )


def test_alternates_resolves_approaches_independently():
    """Alternates ask euro_aip directly, not the declaration-aware collector.

    This is what makes the containment structural rather than a convention: the
    two questions ("can the pilot get in?" and "does this field qualify as a
    filed alternate?") are answered from different code, so wiring a
    declaration into one cannot leak into the other.
    """
    source = (SRC / "tasks" / "alternates.py").read_text()
    assert "procedures_query.approaches()" in source


def test_only_the_advisory_evaluator_consumes_the_declaration():
    """Enumerate the consumers, so a new one has to be a deliberate act."""
    consumers = {
        p.relative_to(SRC).as_posix()
        for p in SRC.rglob("*.py")
        if "has_declared_approach" in p.read_text()
    }
    assert consumers == {
        "models/airport_conditions.py",              # defines it
        "analysis/advisories/approach_feasibility.py",  # the sole consumer
    }, consumers


def test_declaration_does_not_survive_into_the_published_view(monkeypatch):
    """``published`` is the list every minima/alignment reader must use.

    ``AlternateAirport.best_approach_type`` and the qualification proxies are
    all keyed off an approach *class*; a declaration has none, so its presence
    must not change what a published-only reader sees.
    """
    class _Airport:
        ident = "EGTF"
        iso_country = "GB"
        runways: list = []
        procedures: list = []

        class procedures_query:  # noqa: N801 - mirrors the euro_aip attribute
            @staticmethod
            def approaches():
                class _Empty:
                    @staticmethod
                    def all():
                        return []
                return _Empty()

    class _CoveredGbField:
        """Establishes that GB is surveyed, so EGTF's own empty procedure list
        reads as "no published approach" (the #510 premise) rather than as a
        dataset coverage gap."""
        iso_country = "GB"

    class _Model:
        class airports:
            @staticmethod
            def get(icao):
                return _Airport() if icao == "EGTF" else None

            @staticmethod
            def with_procedures():
                return [_CoveredGbField()]

    monkeypatch.setattr(
        "weatherbrief.airports._load_airport_model", lambda _p: _Model(),
    )
    declared: AirportApproaches = get_runway_approaches(
        ["EGTF"], "nav.db", ["EGTF"],
    )["EGTF"]

    assert declared.has_iap is True            # the advisory sees it…
    assert declared.has_published_iap is False  # …and nothing else does
    assert declared.published == []
    assert declared.served_runway_ids == set()
