"""Shared-vector parity test for the Python consensus.

Pins ``analysis.airport_consensus.consensus`` to the reference behaviour the web
forecast map used to compute client-side. That client consensus was retired in
#419 — the web now reads the server-baked ``consensus`` / ``consensus_majority``
blocks straight off the payload — so this file is the guardrail that the server
still produces exactly what the client used to, field-by-field, including the
newly-baked ``convective_risk`` and ``cloud_cover_pct``. Driven off
``tests/fixtures/consensus_vectors.json``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from weatherbrief.analysis.airport_consensus import consensus

_VECTORS = json.loads(
    (Path(__file__).parent / "fixtures" / "consensus_vectors.json").read_text()
)


@pytest.mark.parametrize("case", _VECTORS["cases"], ids=lambda c: c["name"])
@pytest.mark.parametrize("mode", ["worst", "majority"])
def test_consensus_matches_shared_vector(case, mode):
    per_model = {m: dict(fields) for m, fields in case["models"].items()}
    result = consensus(per_model, mode=mode)
    expected = case["expected"][mode]
    assert result["flight_category"] == expected["flight_category"]
    assert result["ceiling_ft"] == pytest.approx(expected["ceiling_ft"])
    assert result["visibility_m"] == pytest.approx(expected["visibility_m"])
    assert result["wind_speed_kt"] == pytest.approx(expected["wind_speed_kt"])
    assert result["crosswind_kt"] == pytest.approx(expected["crosswind_kt"])
    assert result["headwind_kt"] == pytest.approx(expected["headwind_kt"])
    assert result["convective_risk"] == expected["convective_risk"]
    assert result["cloud_cover_pct"] == pytest.approx(expected["cloud_cover_pct"])


# Freezing level (#841): not in the retired client's vectors, so pinned here.
def test_freezing_level_lowest_in_worst_median_of_pool_in_majority():
    per_model = {
        "gfs": {"flight_category": "VFR", "freezing_level_ft": 5000.0},
        "icon": {"flight_category": "VFR", "freezing_level_ft": 7000.0},
        "ecmwf": {"flight_category": "IFR", "freezing_level_ft": 2000.0},
        "meteofrance": {"flight_category": "VFR", "freezing_level_ft": None},
    }
    assert consensus(per_model, mode="worst")["freezing_level_ft"] == 2000.0
    # Majority: the VFR pool only, None ignored.
    assert consensus(per_model, mode="majority")["freezing_level_ft"] == 6000.0


def test_snap_to_dict_and_map_row_adapter_carry_freezing_level():
    from types import SimpleNamespace

    from weatherbrief.analysis.airport_consensus import snap_to_dict
    from weatherbrief.tasks.map_queries import _row_to_snapshot_dict

    row = SimpleNamespace(model="gfs", freezing_level_ft=4321.0)
    assert snap_to_dict(_row_to_snapshot_dict(row))["freezing_level_ft"] == 4321.0


def test_snap_to_dict_carries_dewpoint_and_tolerates_its_absence():
    # #948: the forecast-map card shows Temp / Dew. Rows that predate the
    # column (or archives without it) give None, never a KeyError.
    from types import SimpleNamespace

    from weatherbrief.analysis.airport_consensus import snap_to_dict
    from weatherbrief.tasks.map_queries import _row_to_snapshot_dict

    row = SimpleNamespace(model="gfs", temperature_2m_c=4.0, dewpoint_2m_c=1.5)
    d = snap_to_dict(_row_to_snapshot_dict(row))
    assert d["temperature_c"] == 4.0
    assert d["dewpoint_c"] == 1.5
    assert snap_to_dict({"model": "gfs"})["dewpoint_c"] is None
