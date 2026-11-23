"""Derived visibility selection per model (#979 phase 3)."""

import pytest

from weatherbrief.analysis.visibility.methods import VisibilityInputs, vis_rh_icon
from weatherbrief.analysis.visibility.selection import (
    DERIVED_VISIBILITY_METHOD,
    derived_visibility_m,
    derived_visibility_method,
)


def test_icon_uses_rh_icon():
    assert derived_visibility_method("icon") == "rh_icon"


def test_models_with_their_own_visibility_have_no_method():
    for model in ("ecmwf", "gfs", "ukmo", "meteofrance"):
        assert derived_visibility_method(model) is None
        assert derived_visibility_m(model, {"temperature_2m_c": 5.0, "dewpoint_2m_c": 5.0}) is None


def test_saturated_air_reads_fog():
    # RH 100 %: the bottom of the rh_icon curve.
    assert derived_visibility_m("icon", {"temperature_2m_c": 5.0, "dewpoint_2m_c": 5.0}) == 400.0


def test_dry_air_reads_the_cap():
    assert derived_visibility_m("icon", {"temperature_2m_c": 10.0, "dewpoint_2m_c": 2.0}) == 20000.0


def test_matches_the_scored_method():
    fields = {"temperature_2m_c": 8.0, "dewpoint_2m_c": 7.5}
    expected = vis_rh_icon(VisibilityInputs(temperature_2m_c=8.0, dewpoint_2m_c=7.5))
    assert derived_visibility_m("icon", fields) == pytest.approx(round(expected))


def test_missing_inputs_stay_unknown():
    assert derived_visibility_m("icon", {"temperature_2m_c": 8.0}) is None
    assert derived_visibility_m("icon", {}) is None


def test_a_native_value_is_never_passed_through():
    # Even if a caller hands in a visibility, the method ignores it.
    out = derived_visibility_m(
        "icon", {"temperature_2m_c": 10.0, "dewpoint_2m_c": 2.0, "visibility_m": 300.0},
    )
    assert out == 20000.0


def test_every_selected_method_exists():
    from weatherbrief.analysis.visibility.methods import VISIBILITY_FUNCS

    for method in DERIVED_VISIBILITY_METHOD.values():
        assert method in VISIBILITY_FUNCS and method != "native"
