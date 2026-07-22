"""ICON-D2 per-level GRIB cache (#469 phase 1).

The ICON-D2 model-level sounding is cached one file per (variable, level)
(``f012_ICON_D2_T_L27.grib2``) instead of one whole-column blob per variable.
That is what keeps a partial download *detectable*: a whole-column key can't
tell a short column from a complete one, so a failed partial write would be
cached as complete and served to every later briefing.

These cover: the cache-key labels, the per-level download primitive, the
prefetch job-planning (fetch only missing levels, honour both legacy layouts),
the decode read-path building per-level path lists, and the decode completeness
check that skips an hour rather than decoding a partial column (#478).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

import weatherbrief.fetch.grib as grib_mod
import weatherbrief.fetch.grib.icon_eu_fetch as icon_fetch_mod
from weatherbrief.fetch.grib import _IconEuContext
from weatherbrief.fetch.grib.cache import cache_key, get_cached, put_cached
from weatherbrief.fetch.grib.icon_eu_fetch import (
    ICON_D2,
    ICON_EU,
    ICON_EU_VARIABLES,
    fetch_icon_eu_per_level,
    icon_model_level_var_label,
    icon_model_level_var_legacy_label,
)


# ---------------------------------------------------------------------------
# Cache-key labels
# ---------------------------------------------------------------------------


def test_per_level_label_and_filename():
    label = icon_model_level_var_label(ICON_D2, "t", 27)
    assert label == "ICON_D2_T_L27"
    assert cache_key(29, label) == "f029_ICON_D2_T_L27.grib2"


def test_legacy_whole_column_label():
    assert icon_model_level_var_legacy_label(ICON_D2, "t") == "ICON_D2_T"
    assert icon_model_level_var_legacy_label(ICON_EU, "qc") == "ICON_EU_QC"


def test_variants_never_collide():
    # Same variable/level, different prefix → distinct cache files.
    assert icon_model_level_var_label(ICON_D2, "t", 40) != icon_model_level_var_label(
        ICON_EU, "t", 40,
    )


def test_only_d2_uses_per_level_cache():
    assert ICON_D2.per_level_cache is True
    assert ICON_EU.per_level_cache is False


# ---------------------------------------------------------------------------
# Per-level download primitive
# ---------------------------------------------------------------------------


def test_fetch_per_level_keys_by_var_and_level(monkeypatch):
    def fake_download(url, session):
        # Encode the level+var so the test can assert correct keying.
        return url.split("/")[-1].encode(), 200

    monkeypatch.setattr(icon_fetch_mod, "_download_one_file", fake_download)

    result = fetch_icon_eu_per_level(
        "20260721", 0, 12, levels=[16, 17], variables=["t", "qc"],
        session=object(), variant=ICON_D2,
    )
    assert set(result.keys()) == {("t", 16), ("t", 17), ("qc", 16), ("qc", 17)}
    # Level number appears in the D2 model-level filename it came from.
    assert b"_16_t." in result[("t", 16)]
    assert b"_17_qc." in result[("qc", 17)]


def test_fetch_per_level_omits_failures(monkeypatch):
    def fake_download(url, session):
        if "_17_" in url:
            return None, 404
        return b"ok", 200

    monkeypatch.setattr(icon_fetch_mod, "_download_one_file", fake_download)

    result = fetch_icon_eu_per_level(
        "20260721", 0, 12, levels=[16, 17], variables=["t"],
        session=object(), variant=ICON_D2,
    )
    assert set(result.keys()) == {("t", 16)}  # 17 failed → omitted for top-up later


# ---------------------------------------------------------------------------
# Prefetch job planning (per-level)
# ---------------------------------------------------------------------------


@pytest.fixture
def d2_prefetch(monkeypatch):
    """Mock the fetch primitives; record which (fhour, var, levels) were pulled."""
    monkeypatch.setenv("GRIB_ICON_PREFETCH_WORKERS", "1")
    state = {"per_level": [], "single": []}

    def fake_per_level(init_date, init_hour, fhour, levels, variables,
                       session=None, max_workers=8, variant=ICON_D2):
        (var,) = variables
        state["per_level"].append((fhour, var, tuple(sorted(levels))))
        return {(var, level): f"{fhour}-{var}-{level}".encode() for level in levels}

    def fake_single(init_date, init_hour, fhours, variables=None,
                    session=None, max_workers=8, variant=ICON_D2):
        (fhour,) = fhours
        state["single"].append((fhour, tuple(variables) if variables else None))
        key = variables[0] if variables else "diag"
        return {fhour: f"{key}-{fhour}".encode()}

    monkeypatch.setattr(icon_fetch_mod, "fetch_icon_eu_per_level", fake_per_level)
    monkeypatch.setattr(icon_fetch_mod, "fetch_icon_eu_single_level", fake_single)
    return state


def _d2_ctx(tmp_path: Path, forecast_hours, levels) -> _IconEuContext:
    return _IconEuContext(
        init_date="20260721", init_hour=0, forecast_hours=forecast_hours,
        run_dir=tmp_path, levels=levels,
        point_lats=[48.0], point_lons=[11.0], session=None, variant=ICON_D2,
    )


def test_prefetch_caches_each_level_separately(tmp_path, d2_prefetch):
    ctx = _d2_ctx(tmp_path, forecast_hours=[12], levels=[16, 17])
    grib_mod._prefetch_icon_eu_data_inner(ctx)

    for var in ICON_EU_VARIABLES:
        for level in (16, 17):
            ck = cache_key(12, icon_model_level_var_label(ICON_D2, var, level))
            assert get_cached(tmp_path, ck) == f"12-{var}-{level}".encode()
    # No whole-column blob is written for a per-level variant.
    assert get_cached(tmp_path, cache_key(12, "ICON_D2_T")) is None


def test_prefetch_tops_up_only_missing_levels(tmp_path, d2_prefetch):
    # Level 16 of "t" already on disk → only level 17 fetched for "t".
    put_cached(tmp_path, cache_key(12, icon_model_level_var_label(ICON_D2, "t", 16)), b"have")
    ctx = _d2_ctx(tmp_path, forecast_hours=[12], levels=[16, 17])
    grib_mod._prefetch_icon_eu_data_inner(ctx)

    t_levels = [lvls for fh, var, lvls in d2_prefetch["per_level"] if var == "t"]
    assert t_levels == [(17,)]
    assert get_cached(tmp_path, cache_key(12, icon_model_level_var_label(ICON_D2, "t", 16))) == b"have"


def test_prefetch_ignores_legacy_whole_column_blob(tmp_path, d2_prefetch):
    """A per-level variant must NOT treat a legacy blob as covering the column.

    Decode refuses those blobs (#478 — their level count is unverifiable), so
    honouring one here would deadlock the hour: nothing per-level gets fetched,
    then decode declines the blob, and the hour is skipped until it ages out.
    """
    put_cached(tmp_path, cache_key(12, "ICON_D2_T"), b"legacy-whole-column")
    ctx = _d2_ctx(tmp_path, forecast_hours=[12], levels=[16, 17])
    grib_mod._prefetch_icon_eu_data_inner(ctx)

    fetched_vars = {var for _, var, _ in d2_prefetch["per_level"]}
    assert fetched_vars == set(ICON_EU_VARIABLES)  # including "t"


def test_prefetch_ignores_legacy_combined_blob(tmp_path, d2_prefetch):
    """Same for the even-older combined blob — same deadlock reasoning."""
    put_cached(tmp_path, cache_key(12, "ICON_D2_QC_QI_P"), b"legacy-combined")
    ctx = _d2_ctx(tmp_path, forecast_hours=[12], levels=[16, 17])
    grib_mod._prefetch_icon_eu_data_inner(ctx)

    fetched_vars = {var for _, var, _ in d2_prefetch["per_level"]}
    assert fetched_vars == set(ICON_EU_VARIABLES)


# ---------------------------------------------------------------------------
# Decode read-path: per-variable path lists
# ---------------------------------------------------------------------------


def _capture_decode_jobs(tmp_path, ctx):
    """Run the decode planner, capturing the parallel-decode job list."""
    from datetime import datetime, timezone

    from weatherbrief.models import ModelSource, RouteCrossSection

    cs = RouteCrossSection(
        model=ModelSource.ICON, route_points=[],
        fetched_at=datetime(2026, 7, 21, tzinfo=timezone.utc), point_forecasts=[],
    )
    captured = {}

    def fake_parallel(job_list):
        captured["jobs"] = job_list
        return []  # no decoded points → planner returns without enriching

    with patch.object(grib_mod, "_dispatch_decode_parallel", side_effect=fake_parallel):
        grib_mod._decode_and_merge_icon_eu(ctx, [cs], [], [])
    return captured.get("jobs", [])


def test_decode_builds_per_level_path_lists(tmp_path):
    # Two levels of every variable cached per-level.
    for var in ICON_EU_VARIABLES:
        for level in (16, 17):
            put_cached(tmp_path, cache_key(12, icon_model_level_var_label(ICON_D2, var, level)), b"x")
    ctx = _d2_ctx(tmp_path, forecast_hours=[12], levels=[16, 17])

    jobs = _capture_decode_jobs(tmp_path, ctx)
    assert len(jobs) == 1
    worker, (var_paths, _lats, _lons) = jobs[0]
    assert worker == "decode_icon_chunked"
    # Every variable resolves to a list of its two per-level files.
    assert set(var_paths.keys()) == set(ICON_EU_VARIABLES)
    for paths in var_paths.values():
        assert len(paths) == 2
        assert all(Path(p).name.startswith("f012_ICON_D2_") for p in paths)


# ---------------------------------------------------------------------------
# Decode completeness (#478) — an incomplete hour is skipped, never decoded
# ---------------------------------------------------------------------------
#
# Every variable is requested over the same full column, so completeness is a
# count. Decoding a partial column would produce a short sounding that reads as
# calm/clear/smooth rather than unavailable — the failure #478 exists to stop.


def test_decode_skips_hour_when_a_variable_is_incomplete(tmp_path, caplog):
    import logging

    # Every variable complete over 3 levels except "u", which is missing one.
    for var in ICON_EU_VARIABLES:
        levels = (16, 17) if var == "u" else (16, 17, 18)
        for level in levels:
            put_cached(
                tmp_path,
                cache_key(12, icon_model_level_var_label(ICON_D2, var, level)),
                b"x",
            )
    ctx = _d2_ctx(tmp_path, forecast_hours=[12], levels=[16, 17, 18])

    with caplog.at_level(logging.WARNING):
        jobs = _capture_decode_jobs(tmp_path, ctx)

    # No decode job at all for that hour — not a partial one.
    assert jobs == []
    assert any("incomplete model-level cache" in r.getMessage() for r in caplog.records)
    # The log names the offending variable and the shortfall, so the operator
    # can tell a fetch failure from a quiet channel.
    assert any("u:2/3" in r.getMessage() for r in caplog.records)


def test_decode_runs_when_every_variable_is_complete(tmp_path):
    """The inverse — completeness must not be over-eager and skip good hours."""
    for var in ICON_EU_VARIABLES:
        for level in (16, 17, 18):
            put_cached(
                tmp_path,
                cache_key(12, icon_model_level_var_label(ICON_D2, var, level)),
                b"x",
            )
    ctx = _d2_ctx(tmp_path, forecast_hours=[12], levels=[16, 17, 18])

    jobs = _capture_decode_jobs(tmp_path, ctx)
    assert len(jobs) == 1
    _worker, (var_paths, _lats, _lons) = jobs[0]
    assert all(len(paths) == 3 for paths in var_paths.values())


# ---------------------------------------------------------------------------
# Completeness loopholes closed after external review of PR #479
# ---------------------------------------------------------------------------


def test_decode_skips_hour_when_a_whole_variable_is_absent_icon_eu(tmp_path, caplog):
    """A missing VARIABLE must skip the hour on whole-column variants too.

    The level-count check only applies to per-level variants, so ICON-EU needs
    its own guard: the GRIB sounding replaces the Open-Meteo pressure levels
    wholesale, so losing u/v alone leaves no wind anywhere -> no Richardson ->
    empty CAT layers -> turbulence reads a false "smooth".
    """
    import logging

    for var in ICON_EU_VARIABLES:
        if var in ("u", "v"):
            continue  # both wind components lost
        put_cached(tmp_path, cache_key(12, f"ICON_EU_{var.upper()}"), b"blob")
    ctx = _IconEuContext(
        init_date="20260722", init_hour=0, forecast_hours=[12], run_dir=tmp_path,
        levels=[35, 36], point_lats=[48.0], point_lons=[11.0],
        session=None, variant=ICON_EU,
    )

    with caplog.at_level(logging.WARNING):
        jobs = _capture_decode_jobs(tmp_path, ctx)

    assert jobs == []
    msgs = " ".join(r.getMessage() for r in caplog.records)
    assert "u:absent" in msgs and "v:absent" in msgs


def test_decode_icon_eu_complete_blob_set_still_decodes(tmp_path):
    """The inverse — a full set of whole-column blobs is still accepted."""
    for var in ICON_EU_VARIABLES:
        put_cached(tmp_path, cache_key(12, f"ICON_EU_{var.upper()}"), b"blob")
    ctx = _IconEuContext(
        init_date="20260722", init_hour=0, forecast_hours=[12], run_dir=tmp_path,
        levels=[35, 36], point_lats=[48.0], point_lons=[11.0],
        session=None, variant=ICON_EU,
    )
    jobs = _capture_decode_jobs(tmp_path, ctx)
    assert len(jobs) == 1


def test_per_level_variant_refuses_legacy_whole_column_blobs(tmp_path):
    """D2 must not trust a legacy blob: the writer concatenated whatever levels
    arrived, so its level count is unverifiable after the fact — the exact
    ambiguity the per-level layout exists to close."""
    for var in ICON_EU_VARIABLES:
        put_cached(tmp_path, cache_key(12, f"ICON_D2_{var.upper()}"), b"blob")
    ctx = _d2_ctx(tmp_path, forecast_hours=[12], levels=[16, 17])

    assert _capture_decode_jobs(tmp_path, ctx) == []


def test_per_level_variant_refuses_legacy_combined_blob(tmp_path):
    """Same for the even-older combined {prefix}_QC_QI_P blob."""
    put_cached(tmp_path, cache_key(12, "ICON_D2_QC_QI_P"), b"combined")
    ctx = _d2_ctx(tmp_path, forecast_hours=[12], levels=[16, 17])

    assert _capture_decode_jobs(tmp_path, ctx) == []


def test_per_variable_fetch_discards_incomplete_column(monkeypatch, caplog):
    """The writer must not cache a partial whole-column blob.

    It is stored under a key that says nothing about how many levels are inside,
    so a partial blob would be served as complete for the rest of the run's TTL.
    """
    import logging

    from weatherbrief.fetch.grib.icon_eu_fetch import fetch_icon_eu_per_variable

    def fake_download(url, session):
        # Level 36 always 404s; everything else succeeds.
        return (None, 404) if url.endswith("_36_t.grib2.bz2") or "_36_" in url else (b"x", 200)

    monkeypatch.setattr(icon_fetch_mod, "_download_one_file", fake_download)

    with caplog.at_level(logging.WARNING):
        result = fetch_icon_eu_per_variable(
            "20260722", 0, 12, levels=[35, 36], variables=["t"],
            session=object(), variant=ICON_EU,
        )

    assert result == {}  # discarded, not a 1-of-2-level blob
    assert any("incomplete column" in r.getMessage() for r in caplog.records)


def test_per_variable_fetch_caches_complete_column(monkeypatch):
    """The inverse — a fully-downloaded column is returned as before."""
    from weatherbrief.fetch.grib.icon_eu_fetch import fetch_icon_eu_per_variable

    monkeypatch.setattr(
        icon_fetch_mod, "_download_one_file", lambda url, session: (b"x", 200),
    )
    result = fetch_icon_eu_per_variable(
        "20260722", 0, 12, levels=[35, 36], variables=["t"],
        session=object(), variant=ICON_EU,
    )
    assert result["t"] == b"xx"
