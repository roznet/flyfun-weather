"""Tests for the candidate/decision split in route_sampling.

Builds analytic ``samples`` series (one dict per dense route point) so the
gate logic is exercised independently of the field sampling / smoothing.
"""

from __future__ import annotations

import numpy as np

from weatherbrief.frontal.gates import FrontGateConfig
from weatherbrief.frontal.route_sampling import (
    apply_gate_config,
    decisions_to_crossings,
    generate_front_candidates,
)


def _ramp_samples(
    *,
    theta_lo: float = 280.0,
    theta_hi: float = 290.0,
    gradient: float = 6.0,
    n: int = 12,
    span_km: float = 150.0,
    advection: float = -1.0,
):
    """A straight route with a single TFP zero-crossing at mid-span.

    θe ramps linearly ``theta_lo`` → ``theta_hi`` so the Δθe across a ±75 km
    window centred on the crossing equals ``theta_hi − theta_lo``. TFP is
    positive on the first half and negative on the second so exactly one
    crossing exists at span/2.
    """
    dists = np.linspace(0.0, span_km, n)
    samples = []
    for d in dists:
        frac = d / span_km
        tfp = 1.0 - 2.0 * frac  # +1 → −1, zero at mid-span
        samples.append({
            "lat": 47.0,
            "lon": frac * 2.0,
            "distance_km": float(d),
            "theta_e": theta_lo + frac * (theta_hi - theta_lo),
            "gradient": gradient,
            "neg_laplacian": 0.0,
            "advection": advection,
            "tfp": tfp,
        })
    return samples


class TestGenerateCandidates:
    def test_single_crossing_found(self):
        cands = generate_front_candidates(_ramp_samples())
        assert len(cands) == 1
        c = cands[0]
        assert c.distance_km == 75.0
        # Δθe across ±75 km window = full ramp (290−280) = 10 K
        assert c.delta_theta_e == 10.0
        assert c.gradient == 6.0

    def test_no_crossing_when_tfp_one_signed(self):
        samples = _ramp_samples()
        for s in samples:
            s["tfp"] = 1.0  # never changes sign
        assert generate_front_candidates(samples) == []

    def test_nan_tfp_skipped(self):
        samples = _ramp_samples()
        samples[5]["tfp"] = float("nan")
        samples[6]["tfp"] = float("nan")
        # The crossing straddled those points; with them NaN no candidate forms.
        cands = generate_front_candidates(samples)
        assert all(np.isfinite(c.tfp_before) and np.isfinite(c.tfp_after) for c in cands)


class TestApplyGateConfig:
    def test_same_candidates_different_configs(self):
        """One candidate set, three configs — zero re-sampling, different verdicts."""
        cands = generate_front_candidates(
            _ramp_samples(gradient=5.5, theta_lo=280.0, theta_hi=290.0)
        )
        assert len(cands) == 1

        default = apply_gate_config(cands, FrontGateConfig())  # grad_min 6
        sensitive = apply_gate_config(
            cands, FrontGateConfig(gradient_min=4.0, delta_theta_e_min=3.0)
        )
        strict = apply_gate_config(
            cands, FrontGateConfig(gradient_min=8.0, delta_theta_e_min=7.0)
        )

        assert default[0].accepted is False
        assert default[0].rejected_by == "gradient"
        assert sensitive[0].accepted is True
        assert strict[0].accepted is False
        assert strict[0].rejected_by == "gradient"

    def test_rejected_by_delta_theta_e(self):
        # Strong gradient, weak air-mass jump (Δθe = 2 K) → Δθe gate rejects.
        cands = generate_front_candidates(
            _ramp_samples(gradient=10.0, theta_lo=288.0, theta_hi=290.0)
        )
        decisions = apply_gate_config(cands, FrontGateConfig())  # Δθe_min 5
        assert decisions[0].accepted is False
        assert decisions[0].rejected_by == "delta_theta_e"

    def test_margins_are_signed_slack(self):
        cands = generate_front_candidates(
            _ramp_samples(gradient=7.0, theta_lo=280.0, theta_hi=290.0)
        )
        d = apply_gate_config(cands, FrontGateConfig())[0]  # grad_min 6, Δθe_min 5
        assert d.margins["gradient"] == 1.0          # 7 − 6
        assert d.margins["delta_theta_e"] == 5.0     # |10| − 5
        assert d.accepted is True

    def test_classification_by_advection_sign(self):
        cold = apply_gate_config(
            generate_front_candidates(_ramp_samples(advection=-1.0)),
            FrontGateConfig(),
        )[0]
        warm = apply_gate_config(
            generate_front_candidates(_ramp_samples(advection=+1.0)),
            FrontGateConfig(),
        )[0]
        stationary = apply_gate_config(
            generate_front_candidates(_ramp_samples(advection=0.1)),
            FrontGateConfig(advection_min=0.5),
        )[0]
        assert cold.kind == "cold"
        assert warm.kind == "warm"
        assert stationary.kind == "quasi-stationary"


class TestDecisionsToCrossings:
    def test_only_accepted_become_crossings(self):
        cands = generate_front_candidates(_ramp_samples(gradient=5.5))
        decisions = apply_gate_config(cands, FrontGateConfig())
        assert decisions[0].accepted is False
        assert decisions_to_crossings(decisions, FrontGateConfig().merge_km) == []

    def test_accepted_projects_to_crossing(self):
        cands = generate_front_candidates(_ramp_samples(gradient=7.0))
        decisions = apply_gate_config(cands, FrontGateConfig())
        crossings = decisions_to_crossings(decisions, FrontGateConfig().merge_km)
        assert len(crossings) == 1
        assert crossings[0].kind == "cold"
        assert crossings[0].gradient == 7.0
