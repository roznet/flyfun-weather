"""The unit-free parcel kernel must agree with MetPy within the agreed tolerances (#704).

``parcel.py`` ports MetPy's parcel chain (LCL, parcel path, LFC/EL, CAPE/CIN,
MU/ML parcels, lifted index, Showalter) to plain floats. The owner-agreed
contract, recorded in designs/meteorology-decisions.md:

- CAPE / CIN (surface, MU, ML): within max(1 %, 5 J/kg)
- LCL / LFC / EL pressure: within 2 hPa
- lifted index, Showalter: within 0.2 degC
- parcel path: within 0.1 degC at every level
- presence agrees: a value MetPy reports as missing (NaN, or by raising) is
  missing from the kernel too, and the other way round.

Observed on real packs the agreement is far tighter (CAPE ~0.02 J/kg on
3,000 J/kg, levels ~0.005 hPa, parcel path ~1e-4 K), so these tests catch a
logic divergence long before the tolerance does.
"""

from __future__ import annotations

import math

import metpy.calc as mpcalc
import numpy as np
import pytest
from metpy.units import units

from weatherbrief.analysis.sounding import parcel as K
from weatherbrief.analysis.sounding import thermodynamics as thermo
from weatherbrief.analysis.sounding.prepare import prepare_profile
from weatherbrief.models.analysis import PressureLevelData

_LEVELS = np.array(
    [1000.0, 975, 950, 925, 900, 850, 800, 750, 700, 650, 600, 550, 500, 400, 300, 250, 200],
)


def _cape_tol(ref):
    return max(0.01 * abs(ref), 5.0)


_TOL = {
    "cape": _cape_tol, "cin": _cape_tol, "mu": _cape_tol, "ml": _cape_tol,
    "lcl": lambda _: 2.0, "lfc": lambda _: 2.0, "el": lambda _: 2.0,
    "li": lambda _: 0.2, "si": lambda _: 0.2,
}


def _value(fn):
    """A value, or None for 'missing' (NaN or raised) — the caller's view of both."""
    try:
        v = fn()
    except Exception:
        return None
    if v is None:
        return None
    v = float(np.asarray(v).reshape(-1)[0]) if np.ndim(v) else float(v)
    return None if math.isnan(v) else v


def _metpy(p, t, td):
    P, T, TD = p * units.hPa, (t - 273.15) * units.degC, (td - 273.15) * units.degC
    out = {"lcl": _value(lambda: mpcalc.lcl(P[0], T[0], TD[0])[0].to("hPa").m)}
    try:
        prof = mpcalc.parcel_profile(P, T[0], TD[0])
    except Exception:
        prof = None
    out["_parcel"] = None if prof is None else prof.to("K").m
    if prof is not None:
        out["lfc"] = _value(lambda: mpcalc.lfc(P, T, TD, parcel_temperature_profile=prof)[0].to("hPa").m)
        out["el"] = _value(lambda: mpcalc.el(P, T, TD, parcel_temperature_profile=prof)[0].to("hPa").m)
        out["cape"] = _value(lambda: mpcalc.cape_cin(P, T, TD, prof)[0].m)
        out["cin"] = _value(lambda: mpcalc.cape_cin(P, T, TD, prof)[1].m)
        out["li"] = _value(lambda: mpcalc.lifted_index(P, T, prof).m)
    out["mu"] = _value(lambda: mpcalc.most_unstable_cape_cin(P, T, TD)[0].m)
    out["ml"] = _value(lambda: mpcalc.mixed_layer_cape_cin(P, T, TD)[0].m)
    out["si"] = _value(lambda: mpcalc.showalter_index(P, T, TD).m)
    return out


def _kernel(p, t, td):
    out = {"lcl": _value(lambda: K._lcl(p[0], t[0], td[0])[0])}
    try:
        prof = K.parcel_profile(p, t[0], td[0])
    except Exception:
        prof = None
    out["_parcel"] = prof
    if prof is not None:
        out["lfc"] = _value(lambda: K.lfc(p, t, td, prof))
        out["el"] = _value(lambda: K.el(p, t, td, prof))
        out["cape"] = _value(lambda: K.cape_cin(p, t, td, prof)[0])
        out["cin"] = _value(lambda: K.cape_cin(p, t, td, prof)[1])
        out["li"] = _value(lambda: K.lifted_index(p, t, prof))
    out["mu"] = _value(lambda: K.most_unstable_cape_cin(p, t, td)[0])
    out["ml"] = _value(lambda: K.mixed_layer_cape_cin(p, t, td)[0])
    out["si"] = _value(lambda: K.showalter_index(p, t, td))
    return out


def _assert_parity(p, t, td, label=""):
    ref, new = _metpy(p, t, td), _kernel(p, t, td)
    pr, pn = ref.pop("_parcel"), new.pop("_parcel")
    assert (pr is None) == (pn is None), f"{label}: parcel presence"
    if pr is not None:
        assert pr.shape == pn.shape, label
        np.testing.assert_array_equal(np.isnan(pr), np.isnan(pn), err_msg=label)
        ok = ~np.isnan(pr)
        assert np.max(np.abs(pr[ok] - pn[ok]), initial=0.0) <= 0.1, f"{label}: parcel path"
    for key, tol in _TOL.items():
        r, v = ref.get(key), new.get(key)
        assert (r is None) == (v is None), f"{label} {key}: MetPy {r} vs kernel {v}"
        if r is not None:
            assert abs(r - v) <= tol(r), f"{label} {key}: MetPy {r} vs kernel {v}"


def _sounding(rng):
    """A random but physically shaped sounding: surface 700-1030 hPa, any regime."""
    p0 = rng.uniform(700, 1030)
    p = np.concatenate(([p0], _LEVELS[_LEVELS < p0 - 5]))
    t0 = rng.uniform(-15, 40) + 273.15
    t = np.empty_like(p)
    t[0] = t0
    for i in range(1, len(p)):
        # Lapse per ln-p step: from inversions to superadiabatic near the ground.
        lapse = rng.uniform(-3, 11) if p[i] > p0 - 150 else rng.uniform(3, 9.5)
        dz_km = 29.3 * (t[i - 1]) * math.log(p[i - 1] / p[i]) / 1000.0
        t[i] = t[i - 1] - lapse * dz_km
    dd = rng.uniform(0, 4) + rng.uniform(0, 1, len(p)) * rng.uniform(0, 25)
    dd[0] = rng.choice([0.0, rng.uniform(0, 3), rng.uniform(3, 25)])
    return p, t, t - dd


@pytest.mark.parametrize("seed", range(4))
def test_random_soundings_match_metpy(seed):
    rng = np.random.default_rng(704 + seed)
    for i in range(40):
        p, t, td = _sounding(rng)
        _assert_parity(p, t, td, label=f"seed {seed} #{i}")


def _std(t_sfc_c, lapse_c_per_km, rh_dd, p0=1000.0, top=200.0):
    p = _LEVELS[(_LEVELS <= p0) & (_LEVELS >= top)]
    if p[0] != p0:
        p = np.concatenate(([p0], p))
    z_km = 7.0 * np.log(p0 / p)
    t = t_sfc_c + 273.15 - lapse_c_per_km * z_km
    return p, t, t - rh_dd


EDGE_CASES = {
    # Stable, dry: no LFC, CAPE 0.
    "stable_no_lfc": _std(5.0, 4.0, 15.0),
    # Hot, moist, steep lapse: large CAPE, EL well aloft.
    "deep_convective": _std(32.0, 7.5, 2.0),
    # Unstable to the top: parcel still warmer at 200 hPa, so no EL.
    "el_above_top": _std(35.0, 8.5, 1.0, top=300.0),
    # Saturated surface (T == Td): LCL at the surface.
    "saturated_surface": _std(18.0, 6.5, 0.0),
    # High terrain: surface below 850 hPa, so Showalter has no 850 hPa parcel.
    "high_terrain": _std(20.0, 7.0, 6.0, p0=780.0),
    # Profile stops below 500 hPa: no lifted index.
    "short_profile": _std(25.0, 7.0, 3.0, top=600.0),
}


def _superadiabatic():
    p, t, td = _std(30.0, 7.0, 4.0)
    t = t.copy()
    t[0] += 4.0  # 4 K hotter at the ground than the column above it supports
    return p, t, t - np.where(np.arange(len(t)) == 0, 8.0, 4.0)


def _elevated():
    # Cool, dry surface under a warm moist layer at 850-700: MU differs from SB.
    p, t, td = _std(10.0, 5.0, 12.0)
    t = t.copy(); td = td.copy()
    mid = (p <= 850) & (p >= 700)
    t[mid] += 6.0
    td[mid] = t[mid] - 0.5
    return p, t, td


EDGE_CASES["superadiabatic_surface"] = _superadiabatic()
EDGE_CASES["elevated_instability"] = _elevated()


@pytest.mark.parametrize("name", sorted(EDGE_CASES))
def test_edge_cases_match_metpy(name):
    p, t, td = EDGE_CASES[name]
    _assert_parity(np.asarray(p, float), np.asarray(t, float), np.asarray(td, float), label=name)


def test_edge_cases_exercise_what_they_claim():
    """Guard the fixtures: each named case really is that regime under MetPy."""
    stable = _metpy(*EDGE_CASES["stable_no_lfc"])
    assert stable["lfc"] is None and stable["cape"] == 0.0
    assert _metpy(*EDGE_CASES["deep_convective"])["cape"] > 1000
    assert _metpy(*EDGE_CASES["el_above_top"])["el"] is None
    assert _metpy(*EDGE_CASES["high_terrain"])["si"] is None
    assert _metpy(*EDGE_CASES["short_profile"])["li"] is None
    elevated = _metpy(*EDGE_CASES["elevated_instability"])
    assert elevated["mu"] > elevated["cape"]


def _profile(p, t, td):
    levels = [
        PressureLevelData(
            pressure_hpa=int(round(pp)), temperature_c=float(tt - 273.15),
            dewpoint_c=float(dd - 273.15),
        )
        for pp, tt, dd in zip(p, t, td)
    ]
    return prepare_profile(levels)


def test_compute_indices_core_kernel_vs_metpy_switch(monkeypatch):
    """The production entry point agrees with WB_SOUNDING_KERNEL=metpy within tolerance."""
    for name in ("deep_convective", "elevated_instability", "stable_no_lfc", "short_profile"):
        prof = _profile(*EDGE_CASES[name])
        monkeypatch.setenv("WB_SOUNDING_KERNEL", "metpy")
        ref = thermo.compute_indices_core(prof)
        monkeypatch.delenv("WB_SOUNDING_KERNEL")
        new = thermo.compute_indices_core(prof)
        a, b = ref.indices, new.indices
        for field, tol in [
            ("cape_surface_jkg", _cape_tol), ("cin_surface_jkg", _cape_tol),
            ("cape_most_unstable_jkg", _cape_tol), ("cape_mixed_layer_jkg", _cape_tol),
            ("lcl_pressure_hpa", lambda _: 2.0), ("lfc_pressure_hpa", lambda _: 2.0),
            ("el_pressure_hpa", lambda _: 2.0), ("lifted_index", lambda _: 0.2),
        ]:
            r, v = getattr(a, field), getattr(b, field)
            assert (r is None) == (v is None), f"{name} {field}"
            if r is not None and not math.isnan(r):
                assert abs(r - v) <= tol(r), f"{name} {field}: {r} vs {v}"
        assert len(ref.parcel_path) == len(new.parcel_path)
        for x, y in zip(ref.parcel_path, new.parcel_path):
            assert x.pressure_hpa == y.pressure_hpa
            assert abs(x.temperature_c - y.temperature_c) <= 0.1


def test_metpy_switch_uses_metpy(monkeypatch):
    monkeypatch.setenv("WB_SOUNDING_KERNEL", "metpy")
    assert isinstance(thermo._parcel_backend(_profile(*EDGE_CASES["deep_convective"])), thermo._MetPyParcel)
    monkeypatch.delenv("WB_SOUNDING_KERNEL")
    assert isinstance(thermo._parcel_backend(_profile(*EDGE_CASES["deep_convective"])), thermo._KernelParcel)


def test_kernel_failure_falls_back_to_metpy(monkeypatch):
    """A kernel exception yields MetPy's value for that index, never a silent None."""
    prof = _profile(*EDGE_CASES["deep_convective"])
    monkeypatch.setenv("WB_SOUNDING_KERNEL", "metpy")
    ref = thermo.compute_indices_core(prof).indices
    monkeypatch.delenv("WB_SOUNDING_KERNEL")

    def boom(*_a, **_k):
        raise RuntimeError("kernel broken")

    monkeypatch.setattr(K, "cape_cin", boom)
    out = thermo.compute_indices_core(prof).indices
    assert out.cape_surface_jkg == ref.cape_surface_jkg
    assert out.cin_surface_jkg == ref.cin_surface_jkg
