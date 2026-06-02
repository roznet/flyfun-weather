"""Regression: digest model-agreement text tolerates None model values (#202 review).

Relaxing ``ModelDivergence.model_values`` to ``dict[str, float | None]`` so packs
round-trip (#202) opened a latent crash in ``_format_model_agreement``: a POOR
divergence with an absent model (``None``) hit ``f"{None:.1f}"`` →
``ValueError``. The formatter now renders absent models as ``N/A``.
"""

from __future__ import annotations

from types import SimpleNamespace

from weatherbrief.digest.text import _format_model_agreement
from weatherbrief.models import AgreementLevel, ModelDivergence


def _snapshot_with_poor_null_divergence():
    div = ModelDivergence(
        variable="temperature_c",
        # icon absent at this point → None; the others diverge widely → POOR.
        model_values={"ecmwf": 4.0, "gfs": 12.0, "icon": None},
        mean=8.0,
        spread=8.0,
        agreement=AgreementLevel.POOR,
    )
    analysis = SimpleNamespace(
        waypoint=SimpleNamespace(icao="LFAT"),
        model_divergence=[div],
    )
    return SimpleNamespace(analyses=[analysis])


def test_format_model_agreement_renders_none_as_na():
    # Must not raise ValueError on the None value.
    lines = _format_model_agreement(_snapshot_with_poor_null_divergence())
    text = "\n".join(lines)
    assert "POOR temperature_c" in text
    assert "icon=N/A" in text
    assert "ecmwf=4.0" in text
    assert "gfs=12.0" in text
