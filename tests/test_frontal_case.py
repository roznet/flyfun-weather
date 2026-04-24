"""Tests for weatherbrief.frontal.case — multi-level storage + back-compat."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from weatherbrief.frontal.case import (
    load_case,
    save_case_meta,
    save_model_fields,
)


def _sample_fields(n_lat: int = 5, n_lon: int = 7, offset: float = 0.0) -> dict:
    """One timestep of T/Td/theta_e/u/v arrays with a known offset."""
    la, lo = np.meshgrid(
        np.linspace(0, 1, n_lat), np.linspace(0, 1, n_lon), indexing="ij",
    )
    return {
        "T850": (la + offset).astype(np.float32),
        "Td850": (la + 0.5 * lo + offset).astype(np.float32),
        "theta_e": (290 + 10 * la + offset).astype(np.float32),
        "u850": (5 * lo + offset).astype(np.float32),
        "v850": (2 * la + offset).astype(np.float32),
    }


def _write_case(
    case_dir: Path,
    *,
    levels_spec: dict[str, list[int]] | None,
    fields_by_time_per_model: dict[str, list[dict]],
    save_fn_kwargs: dict[str, dict] | None = None,
) -> None:
    """Minimal case-writer for tests. ``levels_spec=None`` omits the field."""
    lat = np.linspace(45.0, 46.0, 5)
    lon = np.linspace(0.0, 1.5, 7)
    models = list(fields_by_time_per_model.keys())

    kwargs: dict = dict(
        case_name=case_dir.name,
        source="test",
        lat=lat, lon=lon,
        models=models,
        init_times={m: 0 for m in models},
    )
    if levels_spec is not None:
        kwargs["levels"] = levels_spec
    save_case_meta(case_dir, **kwargs)

    # Override meta.json if caller wants to simulate legacy (no levels field).
    if levels_spec is None:
        import json
        meta_path = case_dir / "meta.json"
        meta = json.loads(meta_path.read_text())
        meta.pop("levels", None)
        meta_path.write_text(json.dumps(meta, indent=2))

    vt = np.array(
        [np.datetime64("2025-01-01T00") + np.timedelta64(i, "h")
         for i in range(len(next(iter(fields_by_time_per_model.values()))))],
        dtype="datetime64[ns]",
    )
    sf_kwargs = save_fn_kwargs or {}
    for m, f_list in fields_by_time_per_model.items():
        save_model_fields(case_dir, m, f_list, vt, **sf_kwargs.get(m, {}))


# ---------------------------------------------------------------------------
# Back-compat: legacy single-level 850 cases


class TestLegacyBackCompat:
    def test_loads_case_without_levels_field(self, tmp_path):
        """An existing case whose meta.json has no `levels` field loads and
        defaults to [850] — our Storm Ciarán case is in this shape."""
        case = tmp_path / "legacy"
        _write_case(
            case,
            levels_spec=None,
            fields_by_time_per_model={"era5": [_sample_fields()]},
        )
        c = load_case(case)
        assert c.available_levels("era5") == [850]
        assert c.levels == {"era5": [850]}

    def test_legacy_fields_roundtrip(self, tmp_path):
        case = tmp_path / "legacy"
        original = _sample_fields(offset=3.0)
        _write_case(
            case,
            levels_spec=None,
            fields_by_time_per_model={"era5": [original]},
        )
        c = load_case(case)
        out = c.fields("era5", 0)
        assert np.allclose(out["T850"], original["T850"])
        assert np.allclose(out["theta_e"], original["theta_e"])

    def test_legacy_raises_on_non_850_level(self, tmp_path):
        case = tmp_path / "legacy"
        _write_case(
            case,
            levels_spec=None,
            fields_by_time_per_model={"era5": [_sample_fields()]},
        )
        c = load_case(case)
        with pytest.raises(ValueError, match="not in case levels"):
            c.fields("era5", 0, level_hPa=925)


# ---------------------------------------------------------------------------
# Multi-level (Phase B)


class TestMultiLevel:
    def _multi_fields(self, offset: float = 0.0) -> dict:
        """Per-timestep dict keyed by level, each with the full inner field set."""
        return {
            925: _sample_fields(offset=offset + 0.0),
            850: _sample_fields(offset=offset + 10.0),
            700: _sample_fields(offset=offset + 20.0),
        }

    def test_save_multi_level_creates_suffixed_keys(self, tmp_path):
        case = tmp_path / "multi"
        _write_case(
            case,
            levels_spec={"era5": [925, 850, 700]},
            fields_by_time_per_model={"era5": [self._multi_fields()]},
            save_fn_kwargs={"era5": {"levels": [925, 850, 700]}},
        )
        with np.load(case / "raw" / "era5.npz") as npz:
            keys = set(npz.files)
        for L in (925, 850, 700):
            for pfx in ("T", "Td", "theta_e", "u", "v"):
                assert f"{pfx}_{L}" in keys, f"missing {pfx}_{L}"
        # Legacy keys MUST NOT leak in multi-level mode
        assert "T850" not in keys
        assert "theta_e" not in keys

    def test_loads_and_reports_levels(self, tmp_path):
        case = tmp_path / "multi"
        _write_case(
            case,
            levels_spec={"era5": [925, 850, 700]},
            fields_by_time_per_model={"era5": [self._multi_fields()]},
            save_fn_kwargs={"era5": {"levels": [925, 850, 700]}},
        )
        c = load_case(case)
        assert c.available_levels("era5") == [700, 850, 925]
        assert c.levels["era5"] == [700, 850, 925]

    def test_fields_default_level_is_850(self, tmp_path):
        """Without ``level_hPa`` arg, fields() returns the 850 slice."""
        case = tmp_path / "multi"
        fields = self._multi_fields()
        _write_case(
            case,
            levels_spec={"era5": [925, 850, 700]},
            fields_by_time_per_model={"era5": [fields]},
            save_fn_kwargs={"era5": {"levels": [925, 850, 700]}},
        )
        c = load_case(case)
        out = c.fields("era5", 0)
        assert np.allclose(out["T850"], fields[850]["T850"])
        assert np.allclose(out["theta_e"], fields[850]["theta_e"])

    def test_fields_selects_requested_level(self, tmp_path):
        case = tmp_path / "multi"
        fields = self._multi_fields()
        _write_case(
            case,
            levels_spec={"era5": [925, 850, 700]},
            fields_by_time_per_model={"era5": [fields]},
            save_fn_kwargs={"era5": {"levels": [925, 850, 700]}},
        )
        c = load_case(case)
        out_925 = c.fields("era5", 0, level_hPa=925)
        out_700 = c.fields("era5", 0, level_hPa=700)
        # Inner keys are always T850 etc. regardless of actual level (known wart)
        assert np.allclose(out_925["T850"], fields[925]["T850"])
        assert np.allclose(out_700["T850"], fields[700]["T850"])
        # Sanity: different levels give different values (offsets differ)
        assert not np.allclose(out_925["T850"], out_700["T850"])

    def test_fields_all_hours_multi_level(self, tmp_path):
        case = tmp_path / "multi"
        per_time = [self._multi_fields(offset=0.0), self._multi_fields(offset=1.0)]
        _write_case(
            case,
            levels_spec={"era5": [925, 850, 700]},
            fields_by_time_per_model={"era5": per_time},
            save_fn_kwargs={"era5": {"levels": [925, 850, 700]}},
        )
        c = load_case(case)
        all_h = c.fields_all_hours("era5", level_hPa=700)
        assert list(all_h.keys()) == [0, 1]
        assert np.allclose(all_h[0]["T850"], per_time[0][700]["T850"])
        assert np.allclose(all_h[1]["T850"], per_time[1][700]["T850"])

    def test_unknown_level_raises(self, tmp_path):
        case = tmp_path / "multi"
        _write_case(
            case,
            levels_spec={"era5": [925, 850, 700]},
            fields_by_time_per_model={"era5": [self._multi_fields()]},
            save_fn_kwargs={"era5": {"levels": [925, 850, 700]}},
        )
        c = load_case(case)
        with pytest.raises(ValueError, match="not in case levels"):
            c.fields("era5", 0, level_hPa=500)

    def test_single_level_list_uses_legacy_format(self, tmp_path):
        """``levels=[850]`` alone still writes the legacy format — the new
        storage only kicks in when more than one level is requested."""
        case = tmp_path / "single850"
        _write_case(
            case,
            levels_spec={"era5": [850]},
            fields_by_time_per_model={"era5": [_sample_fields()]},
            save_fn_kwargs={"era5": {"levels": [850]}},
        )
        with np.load(case / "raw" / "era5.npz") as npz:
            assert "T850" in npz.files
            assert "T_850" not in npz.files
