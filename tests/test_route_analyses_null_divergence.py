"""Regression: route_analyses.json with null model_divergence must read back (#202).

The artifact is *written* with ``null`` for a model that lacks a divergence
metric at a point (and a null ``mean`` when all models are absent). Before #202
``load_route_analyses`` raised a Pydantic ``ValidationError`` on read-back, which
broke every recompute-from-pack path (advisory recalculate, alt-departure,
scoring, and front recompute).
"""

from __future__ import annotations

import json
from pathlib import Path

from weatherbrief.tasks.artifacts import load_route_analyses


def _pack_with_null_divergence(pack_dir: Path) -> None:
    """Write a minimal route_analyses.json whose one point has a null divergence."""
    manifest = {
        "route_name": "EGKB-LFAT",
        "target_date": "2026-05-31",
        "departure_time": "2026-05-31T06:00:00+00:00",
        "flight_duration_hours": 2.0,
        "total_distance_nm": 160.0,
        "cruise_altitude_ft": 5000,
        "models": ["ecmwf", "gfs", "icon"],
        "analyses": [
            {
                "point_index": 0,
                "lat": 47.0,
                "lon": -1.5,
                "distance_from_origin_nm": 0.0,
                "interpolated_time": "2026-05-31T06:00:00+00:00",
                "forecast_hour": "2026-05-31T06:00:00+00:00",
                "track_deg": 90.0,
                "model_divergence": [
                    {
                        # icon absent at this point → null; mean still numeric.
                        "variable": "temperature_c",
                        "model_values": {"ecmwf": 11.8, "gfs": 12.3, "icon": None},
                        "mean": 12.05,
                        "spread": 0.5,
                        "agreement": "good",
                    },
                    {
                        # all models absent → null mean (the case that crashed).
                        "variable": "cape_jkg",
                        "model_values": {"ecmwf": None, "gfs": None, "icon": None},
                        "mean": None,
                        "spread": 0.0,
                        "agreement": "good",
                    },
                ],
            }
        ],
    }
    (pack_dir / "route_analyses.json").write_text(json.dumps(manifest))


def test_load_route_analyses_tolerates_null_divergence(tmp_path):
    pack_dir = tmp_path / "pack"
    pack_dir.mkdir()
    _pack_with_null_divergence(pack_dir)

    # Must not raise ValidationError.
    manifest = load_route_analyses(pack_dir)

    rpa = manifest.analyses[0]
    by_var = {d.variable: d for d in rpa.model_divergence}
    assert by_var["temperature_c"].model_values["icon"] is None
    assert by_var["temperature_c"].mean == 12.05
    assert by_var["cape_jkg"].mean is None
    assert all(v is None for v in by_var["cape_jkg"].model_values.values())
