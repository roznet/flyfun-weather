"""Tests for the candidate/decision split in route_sampling.

Builds analytic ``samples`` series (one dict per dense route point) so the
gate logic is exercised independently of the field sampling / smoothing.
"""

from __future__ import annotations

import numpy as np

from weatherbrief.frontal.gates import FrontGateConfig
from weatherbrief.frontal.route_sampling import (
    FrontCandidate,
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


class TestAnomalyTerrainGates:
    """On-track anomaly + high-terrain gates (issue: route detector was gated
    more loosely than the 2-D map / off-track paths, letting orographic θe
    gradients through). Same machinery as :func:`_gate_vertex`."""

    LAT = np.arange(45.0, 50.0, 0.25)
    LON = np.arange(2.0, 9.0, 0.25)

    @staticmethod
    def _cand(lat, lon, gradient):
        # delta_theta_e well above the 5 K gate so only anomaly/terrain decide.
        return FrontCandidate(
            lat=lat, lon=lon, distance_km=100.0, gradient=gradient,
            neg_laplacian=0.0, advection=-1.0, tfp_before=1.0, tfp_after=-1.0,
            delta_theta_e=10.0, airmass_window_km=75.0,
        )

    def _background(self):
        # Broad persistent (orographic) high-gradient region east of 5.5° lon.
        bg = np.full((self.LAT.size, self.LON.size), 2.0)
        bg[:, self.LON >= 5.5] = 8.0
        return bg

    def _terrain(self):
        # Valid everywhere except a broad high-terrain band south of 46.5° lat.
        tm = np.ones((self.LAT.size, self.LON.size), dtype=bool)
        tm[self.LAT <= 46.5, :] = False
        return tm

    def test_persistent_gradient_rejected_by_anomaly(self):
        # grad 9 over background 8 → anomaly 1 < anomaly_min 2 → rejected.
        cfg = FrontGateConfig(level_hPa=925)
        d = apply_gate_config(
            [self._cand(48.0, 6.5, 9.0)], cfg,
            background=self._background(), lat_axis=self.LAT, lon_axis=self.LON,
        )[0]
        assert d.accepted is False
        assert d.rejected_by == "anomaly"

    def test_transient_front_passes_anomaly(self):
        # grad 9 over background 2 → anomaly 7 → accepted.
        cfg = FrontGateConfig(level_hPa=925)
        d = apply_gate_config(
            [self._cand(48.0, 3.5, 9.0)], cfg,
            background=self._background(), lat_axis=self.LAT, lon_axis=self.LON,
        )[0]
        assert d.accepted is True

    def test_high_terrain_rejected(self):
        cfg = FrontGateConfig(level_hPa=925)
        d = apply_gate_config(
            [self._cand(45.5, 3.5, 12.0)], cfg,
            terrain_mask=self._terrain(), lat_axis=self.LAT, lon_axis=self.LON,
        )[0]
        assert d.accepted is False
        assert d.rejected_by == "terrain"

    def test_terrain_label_precedes_anomaly(self):
        # A cell both on high terrain AND in the persistent-background region:
        # terrain is the harder categorical rule, so the trace reads "terrain".
        cfg = FrontGateConfig(level_hPa=925)
        d = apply_gate_config(
            [self._cand(45.5, 6.5, 9.0)], cfg,  # lat<46.5 (terrain) & lon>5.5 (bg 8)
            background=self._background(), terrain_mask=self._terrain(),
            lat_axis=self.LAT, lon_axis=self.LON,
        )[0]
        assert d.rejected_by == "terrain"

    def test_gradient_gate_precedes_anomaly_and_terrain(self):
        # A sub-threshold gradient is rejected by "gradient" first, regardless.
        cfg = FrontGateConfig(level_hPa=925)
        d = apply_gate_config(
            [self._cand(45.5, 6.5, 4.0)], cfg,
            background=self._background(), terrain_mask=self._terrain(),
            lat_axis=self.LAT, lon_axis=self.LON,
        )[0]
        assert d.rejected_by == "gradient"

    def test_no_inputs_is_legacy_magnitude_only(self):
        # Without background/terrain/axes the gate is the old gradient+Δθe only,
        # so a persistent-region candidate that the new gates would drop passes.
        cfg = FrontGateConfig(level_hPa=925)
        d = apply_gate_config([self._cand(48.0, 6.5, 9.0)], cfg)[0]
        assert d.accepted is True
        assert "anomaly" in d.margins  # margin still reported (NaN), no gate

    def test_anomaly_disabled_by_config_flag(self):
        cfg = FrontGateConfig(level_hPa=925, use_anomaly_filter=False)
        d = apply_gate_config(
            [self._cand(48.0, 6.5, 9.0)], cfg,
            background=self._background(), lat_axis=self.LAT, lon_axis=self.LON,
        )[0]
        assert d.accepted is True  # anomaly gate skipped when flag off

    def test_terrain_gate_fires_independently_of_anomaly_flag(self):
        # The terrain gate is not tied to use_anomaly_filter — a high-terrain
        # candidate is rejected even with the anomaly filter off.
        cfg = FrontGateConfig(level_hPa=925, use_anomaly_filter=False)
        d = apply_gate_config(
            [self._cand(45.5, 3.5, 12.0)], cfg,
            terrain_mask=self._terrain(), lat_axis=self.LAT, lon_axis=self.LON,
        )[0]
        assert d.accepted is False
        assert d.rejected_by == "terrain"
