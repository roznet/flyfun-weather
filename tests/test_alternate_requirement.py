"""Tests for the regulatory alternate-requirement assessment (issue #249).

Pure-function tests first (no euro_aip / no I/O), then a wiring smoke test that
exercises the NWP fallback path end-to-end without euro_aip installed.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from weatherbrief.analysis.alternate_requirement import (
    _GNSS,
    _NONPRECISION,
    _PRECISION,
    TrendView,
    band_qualification,
    band_trigger,
    build_window,
    combine_qual,
    combine_trigger,
    compute_easa_qual,
    compute_easa_trigger,
    compute_faa_qual,
    compute_faa_trigger,
    easa_alt_ceiling_band,
    easa_alt_vis,
    no_forecast_window,
    nwp_window,
    proxy_for_approach,
)
from weatherbrief.models.alternate_requirement import BandVerdict, TriggerVerdict
from weatherbrief.models.alternates import AlternateAirport, RouteAlternates
from weatherbrief.tasks.alternate_requirement import (
    _taf_instant_trends,
    run_alternate_requirement,
)
from weatherbrief.units import M_PER_SM


# --- Verdict logic -----------------------------------------------------------

class TestVerdictLogic:
    def test_qualification_likely_marginal_unlikely(self):
        # higher forecast is better; band [600, 1100]
        assert band_qualification(1200, 600, 1100) == BandVerdict.LIKELY
        assert band_qualification(1100, 600, 1100) == BandVerdict.LIKELY  # >= hi
        assert band_qualification(700, 600, 1100) == BandVerdict.MARGINAL
        assert band_qualification(599, 600, 1100) == BandVerdict.UNLIKELY
        assert band_qualification(600, 600, 1100) == BandVerdict.MARGINAL  # == lo

    def test_qualification_missing_fails(self):
        assert band_qualification(None, 600, 1100) == BandVerdict.UNLIKELY

    def test_trigger_mirrors_qualification(self):
        # lower forecast forces an alternate; band [600, 1100]
        assert band_trigger(1200, 600, 1100) == TriggerVerdict.NOT_REQUIRED
        assert band_trigger(700, 600, 1100) == TriggerVerdict.MARGINAL
        assert band_trigger(599, 600, 1100) == TriggerVerdict.REQUIRED

    def test_trigger_missing_requires(self):
        assert band_trigger(None, 600, 1100) == TriggerVerdict.REQUIRED

    def test_faa_collapsed_band_never_marginal(self):
        # req_lo == req_hi → only the two extremes
        assert band_qualification(800, 600, 600) == BandVerdict.LIKELY
        assert band_qualification(599, 600, 600) == BandVerdict.UNLIKELY
        for f in (601, 600.0, 599.9, 700, 599):
            assert band_qualification(f, 600, 600) != BandVerdict.MARGINAL
            assert band_trigger(f, 2000, 2000) != TriggerVerdict.MARGINAL

    def test_combine_worst_of_criteria(self):
        assert combine_qual([BandVerdict.LIKELY, BandVerdict.MARGINAL]) == BandVerdict.MARGINAL
        assert combine_qual([BandVerdict.MARGINAL, BandVerdict.UNLIKELY]) == BandVerdict.UNLIKELY
        assert combine_qual([BandVerdict.LIKELY, BandVerdict.LIKELY]) == BandVerdict.LIKELY
        assert combine_trigger(
            [TriggerVerdict.NOT_REQUIRED, TriggerVerdict.MARGINAL]
        ) == TriggerVerdict.MARGINAL
        assert combine_trigger(
            [TriggerVerdict.MARGINAL, TriggerVerdict.REQUIRED]
        ) == TriggerVerdict.REQUIRED


# --- EASA bands per approach class -------------------------------------------

class TestEasaBands:
    # NCO.OP.143 alternate minima: ILS (DH<250) → DH+200 / 1500 m;
    # RNP & non-precision (DH>=250) → DH+400 / 3000 m.
    def test_precision_ceiling_band(self):
        # ILS DH 200–300 → ceiling 400–500
        assert easa_alt_ceiling_band(_PRECISION) == (400.0, 500.0)

    def test_gnss_ceiling_band(self):
        # RNP/RNAV DH 250–600, +400 → ceiling 650–1000
        assert easa_alt_ceiling_band(_GNSS) == (650.0, 1000.0)

    def test_nonprecision_ceiling_band(self):
        # VOR/NDB DH 400–900, +400 → ceiling 800–1300
        assert easa_alt_ceiling_band(_NONPRECISION) == (800.0, 1300.0)

    def test_vis_is_fixed_per_tier(self):
        assert easa_alt_vis(_PRECISION) == 1500.0  # ILS / DH<250
        assert easa_alt_vis(_GNSS) == 3000.0  # DH>=250
        assert easa_alt_vis(_NONPRECISION) == 3000.0


# --- Approach proxy selection / conservative rules ---------------------------

class TestProxySelection:
    def test_known_classes(self):
        assert proxy_for_approach("ILS") is _PRECISION
        assert proxy_for_approach("RNP") is _GNSS
        assert proxy_for_approach("RNAV") is _GNSS
        assert proxy_for_approach("VOR") is _NONPRECISION
        assert proxy_for_approach("ndb") is _NONPRECISION  # case-insensitive

    def test_unknown_class_is_nonprecision(self):
        assert proxy_for_approach("TACAN") is _NONPRECISION
        assert proxy_for_approach("") is _NONPRECISION
        assert proxy_for_approach(None, has_iap=True) is _NONPRECISION

    def test_no_iap_is_none(self):
        assert proxy_for_approach("ILS", has_iap=False) is None
        assert proxy_for_approach(None, has_iap=False) is None


# --- FAA trigger -------------------------------------------------------------

class TestFaaTrigger:
    def test_below_ceiling_threshold_required(self):
        # 1900 ft / 5 SM → ceiling forces it
        trig = compute_faa_trigger(nwp_window(1900, 5 * M_PER_SM))
        assert trig.status == TriggerVerdict.REQUIRED

    def test_above_thresholds_not_required(self):
        trig = compute_faa_trigger(nwp_window(2100, 4 * M_PER_SM))
        assert trig.status == TriggerVerdict.NOT_REQUIRED

    def test_low_visibility_required(self):
        # 3000 ft but vis 2 SM → required
        trig = compute_faa_trigger(nwp_window(3000, 2 * M_PER_SM))
        assert trig.status == TriggerVerdict.REQUIRED

    def test_never_marginal(self):
        for ceil, vis_sm in [(2000, 3), (1999, 2.9), (5000, 10)]:
            trig = compute_faa_trigger(nwp_window(ceil, vis_sm * M_PER_SM))
            assert trig.status in (TriggerVerdict.REQUIRED, TriggerVerdict.NOT_REQUIRED)

    def test_no_forecast_required(self):
        trig = compute_faa_trigger(no_forecast_window())
        assert trig.status == TriggerVerdict.REQUIRED
        assert trig.source == "none"
        assert trig.reason == "no forecast available"


# --- EASA trigger (destination, three-state) ---------------------------------

class TestEasaTrigger:
    # NCO.OP.140: not required only if ceiling ≥ DH+1000 AND vis ≥ 5000 m.
    # _NONPRECISION dh [400,900] → ceiling band [1400, 2400]; vis fixed 5000.
    # _PRECISION (ILS) dh [200,300] → ceiling band [1200, 1300].
    def test_above_band_not_required(self):
        trig = compute_easa_trigger(nwp_window(2500, 6000), _NONPRECISION)
        assert trig.status == TriggerVerdict.NOT_REQUIRED

    def test_inside_band_marginal(self):
        # ceiling 1800 in 1400–2400 → marginal; vis 6000 ≥ 5000 → not-required
        trig = compute_easa_trigger(nwp_window(1800, 6000), _NONPRECISION)
        assert trig.ceiling.verdict == TriggerVerdict.MARGINAL.value
        assert trig.status == TriggerVerdict.MARGINAL

    def test_below_band_required(self):
        trig = compute_easa_trigger(nwp_window(1300, 6000), _NONPRECISION)
        assert trig.status == TriggerVerdict.REQUIRED

    def test_visibility_below_5km_required(self):
        # ceiling clears (2500 ≥ 2400); vis 4000 < 5000 (fixed bar) → required
        trig = compute_easa_trigger(nwp_window(2500, 4000), _NONPRECISION)
        assert trig.visibility.verdict == TriggerVerdict.REQUIRED.value
        assert trig.status == TriggerVerdict.REQUIRED

    def test_ifr_precision_field_requires_alternate(self):
        # Regression (ESMQ): an ILS field, IFR ceiling 960 ft, good vis. DH+1000
        # band is 1200–1300, so 960 < 1200 → REQUIRED (was wrongly NOT_REQUIRED
        # when the trigger used the DH+200 selection minima).
        trig = compute_easa_trigger(nwp_window(960, 26000), _PRECISION)
        assert trig.status == TriggerVerdict.REQUIRED
        assert trig.ceiling.required_min == 1200
        assert trig.ceiling.required_max == 1300

    def test_no_iap_requires_vmc(self):
        # proxy None (no IAP) → must be VMC: ceiling ≥ 1500 ft / vis ≥ 5000 m.
        trig = compute_easa_trigger(nwp_window(1600, 6000), None)
        assert trig.status == TriggerVerdict.NOT_REQUIRED
        trig = compute_easa_trigger(nwp_window(1200, 6000), None)
        assert trig.status == TriggerVerdict.REQUIRED  # collapsed band, never marginal

    def test_no_forecast_required(self):
        trig = compute_easa_trigger(no_forecast_window(), _NONPRECISION)
        assert trig.status == TriggerVerdict.REQUIRED
        assert trig.source == "none"

    def test_worked_reason_shows_breakeven_dh(self):
        # EGTE-style: ILS, ceiling 1597 → break-even DH = 1597 − 1000 = 597 ft,
        # above the typical ILS minima range (200–300) → "alternate unlikely
        # required". The prose must surface the inverted bar, not "comfortably
        # above minima".
        trig = compute_easa_trigger(nwp_window(1597, 22840), _PRECISION, approach_label="ILS")
        assert trig.status == TriggerVerdict.NOT_REQUIRED
        r = trig.reason
        assert "ILS" in r
        assert "597" in r  # break-even DH = ceiling − 1000
        assert "above" in r
        assert "alternate unlikely required" in r
        assert "comfortably above minima" not in r
        # Actionable plate-minimum check replaces the (model estimate) suffix; the
        # conclusion sits on its own line.
        assert "check the relevant plate minimum ≤ 597 ft" in r
        assert "(model estimate)" not in r
        assert "\nalternate unlikely required" in r

    def test_worked_reason_below_band_names_required(self):
        # ILS ceiling 960 → break-even DH = −40 (clamped to 0), below the ILS
        # minima range → "alternate required".
        trig = compute_easa_trigger(nwp_window(960, 26000), _PRECISION, approach_label="ILS")
        assert trig.status == TriggerVerdict.REQUIRED
        assert "below" in trig.reason
        assert "alternate required" in trig.reason

    def test_worked_reason_no_iap_mentions_vmc(self):
        trig = compute_easa_trigger(nwp_window(1600, 6000), None)
        assert "no instrument approach" in trig.reason
        assert "VMC" in trig.reason

    def test_no_forecast_reason_unchanged(self):
        # The worked reason override must not touch the no-forecast branch.
        trig = compute_easa_trigger(no_forecast_window(), _PRECISION, approach_label="ILS")
        assert trig.reason == "no forecast available"

    def test_trigger_ceiling_basis_uses_1000_margin(self):
        # The trigger basis must use the +1000 ft NCO.OP.140 margin, NOT the
        # +200/+400 selection margin (the regime trap). Present even no-forecast.
        easa = compute_easa_trigger(nwp_window(1597, 22840), _PRECISION, approach_label="ILS")
        assert easa.ceiling_basis == "ILS: est DH 200–300 ft + 1000 ft margin (NCO.OP.140)"
        noiap = compute_easa_trigger(nwp_window(1600, 6000), None)
        assert "VMC proxy" in noiap.ceiling_basis
        nofc = compute_easa_trigger(no_forecast_window(), _PRECISION, approach_label="ILS")
        assert "1000 ft margin" in nofc.ceiling_basis
        faa = compute_faa_trigger(nwp_window(1597, 22840))
        assert faa.ceiling_basis == "fixed 2000 ft / 3 SM trigger (14 CFR 91.169)"


# --- FAA alternate minima (per candidate) ------------------------------------

class TestFaaAlternateMinima:
    def test_ils_precision_600_2(self):
        # ILS: ceiling >= 600 AND vis >= 2 SM
        q = compute_faa_qual(650, 2.5 * M_PER_SM, "ILS", has_iap=True)
        assert q.verdict == BandVerdict.LIKELY
        q = compute_faa_qual(550, 2.5 * M_PER_SM, "ILS", has_iap=True)
        assert q.verdict == BandVerdict.UNLIKELY  # 550 < 600

    def test_nonprecision_800_2(self):
        # anything-but-ILS uses 800-2
        q = compute_faa_qual(750, 2.5 * M_PER_SM, "RNAV", has_iap=True)
        assert q.verdict == BandVerdict.UNLIKELY  # 750 < 800
        q = compute_faa_qual(850, 2.5 * M_PER_SM, "RNAV", has_iap=True)
        assert q.verdict == BandVerdict.LIKELY

    def test_lpv_lands_in_nonprecision(self):
        # No confirmable LPV in FAA precision branch → 800-2
        q = compute_faa_qual(750, 2.5 * M_PER_SM, "RNP", has_iap=True)
        assert q.verdict == BandVerdict.UNLIKELY

    def test_no_iap_vfr_proxy(self):
        # no IAP: ceiling >= 1000 AND vis >= 3 SM
        q = compute_faa_qual(1200, 3.5 * M_PER_SM, None, has_iap=False)
        assert q.verdict == BandVerdict.LIKELY
        q = compute_faa_qual(900, 3.5 * M_PER_SM, None, has_iap=False)
        assert q.verdict == BandVerdict.UNLIKELY
        assert "VFR only" in q.reason

    def test_faa_qual_never_marginal(self):
        q = compute_faa_qual(801, 2.01 * M_PER_SM, "VOR", has_iap=True)
        assert q.verdict != BandVerdict.MARGINAL


# --- EASA alternate minima (per candidate) -----------------------------------

class TestEasaAlternateMinima:
    # NCO.OP.143: VOR (DH>=250 tier) → ceiling DH+400 band [800, 1300], vis 3000 m.
    # ILS (DH<250 tier) → ceiling [400, 500], vis 1500 m. No IAP → 2000 ft / 5 km.
    def test_marginal_in_band(self):
        q = compute_easa_qual(1000, 5000, "VOR", has_iap=True)  # 1000 in 800–1300
        assert q.ceiling.verdict == BandVerdict.MARGINAL.value

    def test_likely_clears_worst_case(self):
        q = compute_easa_qual(1400, 5000, "VOR", has_iap=True)  # >=1300 and vis>=3000
        assert q.verdict == BandVerdict.LIKELY

    def test_unlikely_fails_best_case(self):
        q = compute_easa_qual(700, 5000, "VOR", has_iap=True)
        assert q.verdict == BandVerdict.UNLIKELY  # 700 < 800

    def test_ils_low_dh_tier_uses_1500m_vis(self):
        # ILS: ceiling 600 clears [400,500] (Likely); vis 1500 is the bar.
        q = compute_easa_qual(600, 1500, "ILS", has_iap=True)
        assert q.verdict == BandVerdict.LIKELY
        q2 = compute_easa_qual(600, 1400, "ILS", has_iap=True)  # vis 1400 < 1500
        assert q2.visibility.verdict == BandVerdict.UNLIKELY.value

    def test_combine_worst_axis(self):
        # ceiling likely, vis unlikely → unlikely overall (VOR vis bar 3000 m)
        q = compute_easa_qual(1400, 1000, "VOR", has_iap=True)  # vis 1000 < 3000
        assert q.visibility.verdict == BandVerdict.UNLIKELY.value
        assert q.verdict == BandVerdict.UNLIKELY

    def test_no_iap_requires_2000_5km(self):
        # NCO.OP.143 no-IAP: ceiling 2000 ft / vis 5000 m.
        q = compute_easa_qual(2200, 6000, None, has_iap=False)
        assert "VFR only" in q.reason
        assert q.verdict == BandVerdict.LIKELY
        q2 = compute_easa_qual(1200, 6000, None, has_iap=False)  # 1200 < 2000
        assert q2.verdict == BandVerdict.UNLIKELY

    def test_ceiling_basis_surfaces_est_dh_and_margin(self):
        # The provenance string explains the band: approach class · est DH +
        # alternate margin (RNP → +400 ft; ILS → +200 ft).
        q = compute_easa_qual(4675, 23660, "RNP", has_iap=True)
        assert q.ceiling_basis == "RNP: est DH 250–600 ft + 400 ft alternate margin (NCO.OP.143)"
        ils = compute_easa_qual(1200, 2000, "ILS", has_iap=True)
        assert "ILS: est DH 200–300 ft + 200 ft alternate margin" in ils.ceiling_basis
        noiap = compute_easa_qual(2200, 6000, None, has_iap=False)
        assert "VFR proxy" in noiap.ceiling_basis

    def test_faa_ceiling_basis_is_fixed_regulatory(self):
        assert "fixed 600-2" in compute_faa_qual(650, 2.5 * M_PER_SM, "ILS", has_iap=True).ceiling_basis
        assert "fixed 800-2" in compute_faa_qual(850, 2.5 * M_PER_SM, "RNP", has_iap=True).ceiling_basis


# --- Conservative rules ------------------------------------------------------

class TestConservativeRules:
    def test_missing_ceiling_in_nwp_is_clear_not_fail(self):
        # NWP None ceiling = no layer = clear (good), vis high
        q = compute_easa_qual(None, 6000, "ILS", has_iap=True)
        assert q.ceiling.verdict == BandVerdict.LIKELY.value

    def test_missing_visibility_fails(self):
        q = compute_easa_qual(1200, None, "ILS", has_iap=True)
        assert q.visibility.verdict == BandVerdict.UNLIKELY.value
        assert q.verdict == BandVerdict.UNLIKELY

    def test_unknown_approach_uses_nonprecision_band(self):
        # ILS band would clear 1000 ft (band 400–500) Likely; non-precision band
        # (800–1300) makes it Marginal — confirm the unknown class is demanding.
        q = compute_easa_qual(1000, 5000, "WEIRD", has_iap=True)
        assert q.ceiling.verdict == BandVerdict.MARGINAL.value


# --- Window builder ----------------------------------------------------------

class TestWindowBuilder:
    def test_cavok_is_clear(self):
        w = build_window([[TrendView(cavok=True)]])
        assert w.ceiling_ft is None  # no ceiling constraint
        assert w.visibility_m >= 9999
        assert w.has_forecast

    def test_no_ceiling_layer_is_good(self):
        # no BKN/OVC but vis reported — ceiling None (good), vis kept
        w = build_window([[TrendView(ceiling_ft=None, visibility_m=8000)]])
        assert w.ceiling_ft is None
        assert w.visibility_m == 8000

    def test_multi_group_worst_case(self):
        base = TrendView(ceiling_ft=3000, visibility_m=9999)
        tempo = TrendView(ceiling_ft=800, visibility_m=3000, trend_type="TEMPO")
        w = build_window([[base, tempo]])
        assert w.ceiling_ft == 800
        assert w.visibility_m == 3000
        assert w.triggered_by_tempo

    def test_tempo_flag_cleared_by_later_equally_bad_prevailing(self):
        # #250 round-3: a TEMPO drives the worst ceiling at one instant, then a
        # later prevailing instant is equally bad. The window value is owed to
        # the prevailing condition, so the TEMPO tag must NOT stale-persist.
        inst_tempo = [
            TrendView(ceiling_ft=3000, visibility_m=9999),
            TrendView(ceiling_ft=300, visibility_m=5000, trend_type="TEMPO"),
        ]
        inst_prevailing = [TrendView(ceiling_ft=300, visibility_m=5000)]
        w = build_window([inst_tempo, inst_prevailing])
        assert w.ceiling_ft == 300
        assert not w.triggered_by_tempo  # prevailing is equally bad → not temporary

    def test_tempo_flag_set_when_tempo_is_strictly_worst(self):
        # Counterpart: when the prevailing line never reaches the TEMPO low, the
        # binding value is genuinely temporary and the tag stays.
        inst_tempo = [
            TrendView(ceiling_ft=3000, visibility_m=9999),
            TrendView(ceiling_ft=300, visibility_m=5000, trend_type="TEMPO"),
        ]
        inst_prevailing = [TrendView(ceiling_ft=1500, visibility_m=9999)]
        w = build_window([inst_tempo, inst_prevailing])
        assert w.ceiling_ft == 300
        assert w.triggered_by_tempo

    def test_fm_supersedes_base(self):
        # base is worse but an applicable FM improves it → prevailing = FM
        base = TrendView(ceiling_ft=500, visibility_m=9999)
        fm = TrendView(
            ceiling_ft=3000, visibility_m=9999, trend_type="FM",
            validity_start=datetime(2000, 1, 1, 12),
        )
        w = build_window([[base, fm]])
        assert w.ceiling_ft == 3000  # base 500 superseded
        assert not w.triggered_by_tempo

    def test_prob30_disregarded_by_default(self):
        base = TrendView(ceiling_ft=3000, visibility_m=9999)
        prob30 = TrendView(ceiling_ft=400, visibility_m=2000, trend_type="TEMPO", probability=30)
        w = build_window([[base, prob30]])
        assert w.ceiling_ft == 3000  # PROB30 ignored
        w2 = build_window([[base, prob30]], include_prob30=True)
        assert w2.ceiling_ft == 400  # honoured when opted-in

    def test_prob40_honoured(self):
        base = TrendView(ceiling_ft=3000, visibility_m=9999)
        prob40 = TrendView(ceiling_ft=400, visibility_m=2000, trend_type="TEMPO", probability=40)
        w = build_window([[base, prob40]])
        assert w.ceiling_ft == 400
        assert w.triggered_by_tempo

    def test_worst_across_instants(self):
        i1 = [TrendView(ceiling_ft=2000, visibility_m=8000)]
        i2 = [TrendView(ceiling_ft=900, visibility_m=5000)]
        w = build_window([i1, i2])
        assert w.ceiling_ft == 900
        assert w.visibility_m == 5000

    def test_empty_window_no_forecast(self):
        w = build_window([])
        assert not w.has_forecast
        assert w.source == "none"

    def test_nwp_fallback_path(self):
        w = nwp_window(1500, 6000)
        assert w.source == "nwp"
        assert w.has_forecast
        assert not w.triggered_by_tempo
        assert w.ceiling_ft == 1500


# --- TAF → instant trends extraction (injected applicable_trends) ------------

class TestTafExtraction:
    def test_base_plus_applicable_trends(self):
        taf = SimpleNamespace(
            ceiling_ft=3000, visibility_meters=9999, cavok=False,
            trend_type=None, probability=None, validity_start=None,
        )
        tempo = SimpleNamespace(
            ceiling_ft=600, visibility_meters=3000, cavok=False,
            trend_type="TEMPO", probability=40,
            validity_start=SimpleNamespace(day=1, hour=12),
        )

        def fake_applicable(_taf, _t):
            return [tempo]

        eta = datetime(2026, 6, 13, 12, tzinfo=timezone.utc)
        sample_times = [eta + timedelta(minutes=m) for m in (-60, 0, 60)]
        instants = _taf_instant_trends(taf, sample_times, applicable_trends_fn=fake_applicable)
        assert len(instants) == 3
        # each instant: base + tempo
        assert all(len(group) == 2 for group in instants)
        w = build_window(instants, source="taf")
        assert w.ceiling_ft == 600  # tempo worsens
        assert w.triggered_by_tempo

    def test_base_group_with_stray_probability_not_temporary(self):
        # #250 review: a parent WeatherReport with a stray `probability` must NOT
        # cause the base view to be classed temporary (which would drop its
        # ceiling to clear / its vis to fail and set a false TEMPO tag).
        taf = SimpleNamespace(
            ceiling_ft=500, visibility_meters=8000, cavok=False,
            trend_type=None, probability=30, validity_start=None,
        )
        eta = datetime(2026, 6, 13, 12, tzinfo=timezone.utc)
        instants = _taf_instant_trends(taf, [eta], applicable_trends_fn=lambda _t, _s: [])
        w = build_window(instants, source="taf")
        assert w.ceiling_ft == 500  # real ceiling kept (not optimistically clear)
        assert w.visibility_m == 8000  # real vis kept (not pessimistically None)
        assert not w.triggered_by_tempo

    def test_month_straddling_taf_orders_fm_after_base(self):
        # #250 review: base valid from day 29, FM from day 01 (next month). At an
        # ETA on day 01, the FM must supersede the base (rollover-safe ordering),
        # so its worse ceiling wins — not silently dropped by a fixed-Jan anchor.
        base = SimpleNamespace(
            ceiling_ft=3000, visibility_meters=9999, cavok=False,
            trend_type=None, probability=None,
            validity_start=SimpleNamespace(day=29, hour=6),
        )
        fm = SimpleNamespace(
            ceiling_ft=800, visibility_meters=9999, cavok=False,
            trend_type="FM", probability=None,
            validity_start=SimpleNamespace(day=1, hour=0),
        )
        eta = datetime(2026, 7, 1, 6, tzinfo=timezone.utc)
        instants = _taf_instant_trends(taf=base, sample_times=[eta], ref=eta,
                                       applicable_trends_fn=lambda _t, _s: [fm])
        w = build_window(instants, source="taf")
        assert w.ceiling_ft == 800  # FM superseded the base, not the reverse
        assert not w.triggered_by_tempo  # FM is prevailing, not a temporary group


# --- Wiring smoke test (NWP path, no euro_aip) -------------------------------

def _make_snapshot(dest_icao: str, alternates: RouteAlternates):
    route = SimpleNamespace(destination=SimpleNamespace(icao=dest_icao))
    return SimpleNamespace(route=route, alternates=alternates, route_observations=None)


class TestWiringSmoke:
    def test_nwp_path_populates_triggers_and_quals(self):
        cand_good = AlternateAirport(
            icao="EGAA", lat=54.0, lon=-6.0, distance_from_dest_nm=30, position="after",
            flight_category="VFR", ceiling_ft=4000, visibility_m=9999,
            has_instrument_approach=True, best_approach_type="ILS",
        )
        cand_marginal = AlternateAirport(
            icao="EGAC", lat=54.6, lon=-5.9, distance_from_dest_nm=10, position="after",
            flight_category="MVFR", ceiling_ft=1000, visibility_m=5000,
            has_instrument_approach=True, best_approach_type="VOR",
        )
        alt = RouteAlternates(
            destination_icao="EIDW", destination_category="MVFR",
            destination_ceiling_ft=1500, destination_visibility_m=6000,
            corridor_nm=40, radius_nm=50,
            eta=datetime(2026, 6, 13, 12, tzinfo=timezone.utc),
            alternates=[cand_good, cand_marginal],
        )
        snap = _make_snapshot("EIDW", alt)

        # bogus db path → approach lookup degrades to (None, False) gracefully
        run_alternate_requirement(snap, "/nonexistent/nav.db")

        req = alt.alternate_requirement
        assert req is not None
        assert req.faa.source == "nwp"
        # 1500 ft / ~3.7 SM → FAA ceiling < 2000 → required
        assert req.faa.status == TriggerVerdict.REQUIRED
        # EASA NCO.OP.140: no destination IAP (bogus DB) → VMC proxy 1500 ft /
        # 5000 m; 1500 ft / 6000 m clears both → not required.
        assert req.easa.status == TriggerVerdict.NOT_REQUIRED
        # caveats present
        assert any("planning guidance" in c.lower() for c in req.caveats)

        # per-candidate quals populated
        assert cand_good.faa.verdict == BandVerdict.LIKELY  # ILS, 4000/9999
        assert cand_good.easa.verdict == BandVerdict.LIKELY
        assert cand_marginal.easa.verdict == BandVerdict.MARGINAL  # 1000 in 800–1300
        # No route_observations (D-2/D-1) → candidates qualify on NWP consensus.
        assert cand_good.faa.source == "nwp"
        assert cand_good.easa.source == "nwp"

    def test_no_alternates_is_noop(self):
        snap = SimpleNamespace(alternates=None)
        run_alternate_requirement(snap, "/nonexistent/nav.db")  # must not raise


# --- Candidate TAF path (D-0): reuse route-corridor TAFs, fetch the gaps ------

def _obs(airports):
    """Minimal route_observations stand-in: only .airports is read."""
    return SimpleNamespace(airports=airports)


class TestCandidateTafQualification:
    """A TAF covering a candidate's ETA overrides its NWP-consensus qualification."""

    def _alt_with_candidate(self, cand):
        return RouteAlternates(
            destination_icao="EIDW", destination_category="VFR",
            destination_ceiling_ft=4000, destination_visibility_m=9999,
            corridor_nm=40, radius_nm=50,
            eta=datetime(2026, 6, 13, 12, tzinfo=timezone.utc),
            alternates=[cand],
        )

    def test_reused_taf_overrides_nwp_consensus(self):
        # NWP consensus says LIFR (would be UNLIKELY even for ILS minima), but a
        # TAF covering the ETA reports CAVOK → the candidate qualifies on the TAF.
        cand = AlternateAirport(
            icao="EGAC", lat=54.6, lon=-5.9, distance_from_dest_nm=10, position="after",
            flight_category="LIFR", ceiling_ft=100, visibility_m=400,
            has_instrument_approach=True, best_approach_type="ILS",
        )
        alt = self._alt_with_candidate(cand)
        obs = _obs([SimpleNamespace(
            icao="EGAC", taf_raw="TAF EGAC 130600Z 1306/1406 28010KT CAVOK",
        )])
        snap = SimpleNamespace(
            route=SimpleNamespace(destination=SimpleNamespace(icao="EIDW")),
            alternates=alt, route_observations=obs,
        )

        run_alternate_requirement(snap, "/nonexistent/nav.db")

        assert cand.faa.source == "taf"
        assert cand.easa.source == "taf"
        # CAVOK clears the fixed/estimated minima → LIKELY, not the NWP UNLIKELY.
        assert cand.faa.verdict == BandVerdict.LIKELY
        assert cand.easa.verdict == BandVerdict.LIKELY

    def test_gap_candidate_taf_is_fetched(self, monkeypatch):
        # Candidate is NOT in route_observations → the gap-fetch path supplies
        # its TAF. We stub the network fetch to keep the test offline.
        cand = AlternateAirport(
            icao="EGAA", lat=54.0, lon=-6.0, distance_from_dest_nm=30, position="after",
            flight_category="LIFR", ceiling_ft=100, visibility_m=400,
            has_instrument_approach=True, best_approach_type="ILS",
        )
        alt = self._alt_with_candidate(cand)
        # route_observations present (D-0) but without this candidate.
        obs = _obs([SimpleNamespace(icao="EISOMEWHERE", taf_raw="TAF ...")])
        snap = SimpleNamespace(
            route=SimpleNamespace(destination=SimpleNamespace(icao="EIDW")),
            alternates=alt, route_observations=obs,
        )

        import weatherbrief.tasks.alternate_requirement as mod
        called = {}

        def fake_fetch(icaos, db_path):
            called["icaos"] = list(icaos)
            return {"EGAA": "TAF EGAA 130600Z 1306/1406 28010KT CAVOK"}

        monkeypatch.setattr(mod, "_fetch_candidate_tafs", fake_fetch)

        run_alternate_requirement(snap, "/nonexistent/nav.db")

        assert called["icaos"] == ["EGAA"]  # only the uncovered candidate
        assert cand.faa.source == "taf"
        assert cand.faa.verdict == BandVerdict.LIKELY

    def test_no_taf_for_candidate_keeps_nwp(self, monkeypatch):
        # route_observations present but no TAF for the candidate and gap-fetch
        # finds none → NWP consensus is kept (source stays "nwp").
        cand = AlternateAirport(
            icao="EGAC", lat=54.6, lon=-5.9, distance_from_dest_nm=10, position="after",
            flight_category="VFR", ceiling_ft=4000, visibility_m=9999,
            has_instrument_approach=True, best_approach_type="ILS",
        )
        alt = self._alt_with_candidate(cand)
        obs = _obs([SimpleNamespace(icao="EGAC", taf_raw=None)])
        snap = SimpleNamespace(
            route=SimpleNamespace(destination=SimpleNamespace(icao="EIDW")),
            alternates=alt, route_observations=obs,
        )

        import weatherbrief.tasks.alternate_requirement as mod
        monkeypatch.setattr(mod, "_fetch_candidate_tafs", lambda icaos, db: {})

        run_alternate_requirement(snap, "/nonexistent/nav.db")

        assert cand.faa.source == "nwp"
        assert cand.faa.verdict == BandVerdict.LIKELY  # VFR clears on NWP too

    def test_candidate_taf_window_rejects_empty_parse(self):
        from weatherbrief.tasks.alternate_requirement import _candidate_taf_window
        eta = datetime(2026, 6, 13, 12, tzinfo=timezone.utc)
        # An unparseable body yields neither ceiling nor visibility → no override.
        assert _candidate_taf_window("not a taf", eta) is None
        assert _candidate_taf_window("", eta) is None
        assert _candidate_taf_window(None, eta) is None


class TestConditionalExtraction:
    """_extract_conditionals: descriptive TEMPO/PROB list with the counted flag."""

    def _vt(self, day, hour):
        return SimpleNamespace(day=day, hour=hour)

    def test_classifies_and_counts(self):
        from weatherbrief.tasks.alternate_requirement import _extract_conditionals
        eta = datetime(2026, 6, 14, 12, tzinfo=timezone.utc)
        taf = SimpleNamespace(trends=[
            # FM is main body, not conditional → excluded
            SimpleNamespace(trend_type="FM", probability=None, ceiling_ft=3000,
                            visibility_meters=9999, cavok=False,
                            validity_start=self._vt(14, 9), validity_end=None),
            SimpleNamespace(trend_type="TEMPO", probability=None, ceiling_ft=400,
                            visibility_meters=3000, cavok=False,
                            validity_start=self._vt(14, 11), validity_end=self._vt(14, 13)),
            SimpleNamespace(trend_type="TEMPO", probability=30, ceiling_ft=200,
                            visibility_meters=800, cavok=False,
                            validity_start=self._vt(14, 11), validity_end=self._vt(14, 13)),
            SimpleNamespace(trend_type="TEMPO", probability=40, ceiling_ft=300,
                            visibility_meters=1200, cavok=False,
                            validity_start=self._vt(14, 11), validity_end=self._vt(14, 13)),
            # outside the ETA window (next day) → excluded
            SimpleNamespace(trend_type="TEMPO", probability=None, ceiling_ft=100,
                            visibility_meters=500, cavok=False,
                            validity_start=self._vt(15, 6), validity_end=self._vt(15, 9)),
        ])
        conds = _extract_conditionals(taf, eta)
        kinds = {(c.kind, c.counted) for c in conds}
        assert ("TEMPO", True) in kinds
        assert ("PROB30 TEMPO", False) in kinds   # PROB30 noted, not counted
        assert ("PROB40 TEMPO", True) in kinds
        assert len(conds) == 3                      # FM + out-of-window excluded
        tempo = next(c for c in conds if c.kind == "TEMPO")
        assert tempo.validity == "1411/1413"
