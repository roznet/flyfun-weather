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
