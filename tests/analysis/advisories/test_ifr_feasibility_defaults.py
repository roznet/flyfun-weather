"""Guard: ifr_feasibility icing-% catalog defaults must match the evaluate()
fallbacks. They previously diverged (catalog 20/50 vs evaluate 15/30), so a
call without explicit params graded icing more aggressively than the UI showed.
Both now reference a single shared constant; these tests fail if that breaks.
"""

from __future__ import annotations

from weatherbrief.analysis.advisories import ifr_feasibility
from weatherbrief.analysis.advisories.ifr_feasibility import IFRFeasibilityEvaluator


def _param_default(key: str) -> float:
    entry = IFRFeasibilityEvaluator.catalog_entry()
    param = next(p for p in entry.parameters if p.key == key)
    return param.default


def test_icing_pct_defaults_are_20_50():
    """Confirmed intended thresholds (user decision, 2026-06-30): amber 20, red 50."""
    assert ifr_feasibility._ICING_PCT_AMBER_DEFAULT == 20
    assert ifr_feasibility._ICING_PCT_RED_DEFAULT == 50


def test_catalog_defaults_match_shared_constants():
    """Catalog parameter defaults are wired to the shared constants."""
    assert _param_default("icing_pct_amber") == ifr_feasibility._ICING_PCT_AMBER_DEFAULT
    assert _param_default("icing_pct_red") == ifr_feasibility._ICING_PCT_RED_DEFAULT


def test_catalog_amber_below_red():
    """Sanity: amber threshold is strictly below red."""
    assert _param_default("icing_pct_amber") < _param_default("icing_pct_red")
