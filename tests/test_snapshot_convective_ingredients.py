"""Convective ingredients reach the snapshot row (#565).

The failure this guards against is not a crash: it is a column that exists, is
wired end to end, and is NULL on every row. `sounding_lifted_index` was exactly
that for years — written from `snapshot_fields`, copied by `_store_snapshots`,
with a column on the table, and None on 110,801 of 110,801 rows of a sampled
archive day because a swallowed TypeError ate it upstream.

So these tests assert on *values produced by the real analysis path*, not on
plumbing with hand-made inputs.
"""

from __future__ import annotations

import pytest

from weatherbrief.analysis.sounding.snapshot_fields import (
    compute_snapshot_sounding_fields,
)
from weatherbrief.db.models import AirportForecastSnapshotRow
from weatherbrief.models.analysis import HourlyForecast, PressureLevelData

# A moist, unstable-ish column with 500 hPa present — enough for MetPy to
# produce a parcel, LFC/EL and a lifted index.
_LEVELS = [
    (1000, 22, 85), (925, 17, 80), (850, 12, 75), (700, 4, 65),
    (600, -3, 60), (500, -12, 55), (400, -24, 45), (300, -39, 35),
    (250, -49, 30), (200, -55, 25), (150, -58, 20), (100, -60, 15),
]


def _hourly(**overrides) -> HourlyForecast:
    levels = [
        PressureLevelData(
            pressure_hpa=hpa, temperature_c=t, relative_humidity_pct=rh,
            wind_speed_kt=25, wind_direction_deg=250,
        )
        for hpa, t, rh in _LEVELS
    ]
    defaults = dict(
        time="2026-08-16T12:00:00Z",
        temperature_2m_c=22.0,
        dewpoint_2m_c=18.0,
        pressure_levels=levels,
        cape_jkg=1200.0,
        convective_inhibition_jkg=-25.0,
    )
    defaults.update(overrides)
    return HourlyForecast(**defaults)


class TestFieldsAreProducedByTheRealPath:
    def test_sounding_lifted_index_is_populated(self):
        """The exact column that was NULL on every row ever written."""
        fields = compute_snapshot_sounding_fields(_hourly(), "gfs")
        assert fields.get("sounding_lifted_index") is not None

    def test_lfc_and_el_are_populated(self):
        fields = compute_snapshot_sounding_fields(_hourly(), "gfs")
        assert fields.get("sounding_lfc_ft") is not None
        assert fields.get("sounding_el_ft") is not None

    @pytest.mark.parametrize(
        "model_key,expected",
        [("gfs", "sb"), ("ecmwf", "mu"), ("icon", "ml")],
    )
    def test_cape_type_records_the_parcel(self, model_key, expected):
        """Without this, `cape_jkg` is three different quantities in one column."""
        fields = compute_snapshot_sounding_fields(_hourly(), model_key)
        assert fields.get("nwp_cape_type") == expected

    def test_model_cin_is_carried_through(self):
        fields = compute_snapshot_sounding_fields(_hourly(), "gfs")
        assert fields.get("nwp_cin_jkg") == pytest.approx(-25.0)

    def test_no_native_diagnostics_means_no_convective_method(self):
        """Absence is the honest signal, and it is load-bearing.

        In the standalone cycle the sounding analysis runs inside the
        Open-Meteo fetch, *before* `_enrich_with_grib` supplies GFS/ICON cloud
        diagnostics — so those two models have no native convective assessment
        at snapshot time and `nwp_conv_method` is NULL. A reader must take that
        as "not graded natively here", not as "no convection". ECMWF takes the
        GRIB-first path and does carry diagnostics.
        """
        fields = compute_snapshot_sounding_fields(_hourly(), "gfs")
        assert fields.get("nwp_conv_method") is None

    def test_native_diagnostics_produce_a_convective_method(self):
        """The label is what makes the other convective columns interpretable."""
        from weatherbrief.models.analysis import NWPCloudDiagnostics

        diag = NWPCloudDiagnostics(
            convective_cover_pct=45.0,
            convective_base_ft=3500.0,
            convective_top_ft=28000.0,
            convective_precip_mm_h=2.5,
            ml_cape_jkg=1450.0,
        )
        fields = compute_snapshot_sounding_fields(
            _hourly(nwp_cloud_diagnostics=diag), "ecmwf",
        )

        assert fields.get("nwp_conv_method") is not None
        assert fields.get("nwp_conv_cover_pct") == pytest.approx(45.0)
        assert fields.get("nwp_conv_top_ft") == pytest.approx(28000.0)
        assert fields.get("nwp_conv_precip_mm_h") == pytest.approx(2.5)
        assert fields.get("nwp_ml_cape_jkg") == pytest.approx(1450.0)

    def test_no_bulk_shear_column_is_emitted(self):
        """`compute_indices_extended` is skipped by the lite path, so a bulk
        shear column would be NULL on every standalone row."""
        fields = compute_snapshot_sounding_fields(_hourly(), "gfs")
        assert not any("shear" in k for k in fields)


class TestEveryEmittedFieldHasAColumn:
    """A field with no column is silently dropped by `_store_snapshots`.

    `nwp_k_index`, `nwp_total_totals` and `surface_pressure_hpa` were computed
    by the ECMWF builder and discarded for exactly this reason.
    """

    def test_all_emitted_keys_map_to_snapshot_columns(self):
        fields = compute_snapshot_sounding_fields(_hourly(), "ecmwf")
        columns = {c.name for c in AirportForecastSnapshotRow.__table__.columns}
        orphans = sorted(set(fields) - columns)
        assert not orphans, f"emitted but no column to store them in: {orphans}"


class TestPersistenceWhitelistCoversTheColumns:
    """`_store_snapshots` copies an explicit key list; a new column that is not
    added there is NULL forever, with nothing failing."""

    def test_whitelist_covers_every_convective_column(self):
        import re
        from pathlib import Path
        import weatherbrief.tasks.standalone_verification as sv

        src = Path(sv.__file__).read_text()
        body = src[src.index("def _store_snapshots"):]
        body = body[:body.index("    snapshot_insert_ignore(db, rows)")]
        copied = set(re.findall(r'"(\w+)":\s*snap', body))

        required = {
            "nwp_conv_method", "nwp_conv_cover_pct", "nwp_conv_base_ft",
            "nwp_conv_top_ft", "nwp_conv_precip_mm_h", "nwp_ml_cape_jkg",
            "nwp_cape_type", "nwp_cin_jkg", "nwp_lifted_index",
            "nwp_k_index", "nwp_total_totals",
            "sounding_lfc_ft", "sounding_el_ft",
        }
        assert required <= copied, f"never persisted: {sorted(required - copied)}"


class TestAdditionsCannotBlankExistingFields:
    """A missing attribute must cost one field, not all of them.

    `compute_snapshot_sounding_fields` is fail-silent by contract: any
    exception returns `{}` and the row keeps only its surface data. So an
    unguarded `sounding.indices.<new_field>` does not degrade gracefully — it
    blanks `sounding_ceiling_ft`, `freezing_level_ft`, CAPE/CIN and the
    convective risk for that row as well. Every added read therefore goes
    through `getattr(..., None)`.
    """

    def test_missing_new_index_attribute_keeps_the_established_fields(
        self, monkeypatch,
    ):
        from types import SimpleNamespace

        import weatherbrief.analysis.sounding as sounding_mod

        real = sounding_mod.analyze_sounding_lite

        def _lite_without_new_fields(levels, hourly=None, model_key=None, **kw):
            result = real(levels, hourly, model_key, **kw)
            if result is None or result.indices is None:
                return result
            # An `indices` that predates #565: the established fields exist,
            # the new ones do not.
            result.indices = SimpleNamespace(
                sounding_ceiling_ft=1200.0,
                freezing_level_ft=9000.0,
                cape_surface_jkg=800.0,
                cin_surface_jkg=-30.0,
                lifted_index=-3.0,
            )
            return result

        monkeypatch.setattr(
            sounding_mod, "analyze_sounding_lite", _lite_without_new_fields,
        )

        fields = compute_snapshot_sounding_fields(_hourly(), "gfs")

        assert fields.get("sounding_ceiling_ft") == pytest.approx(1200.0), (
            "an unguarded read of a new index field blanked every sounding "
            "column via the fail-silent handler"
        )
        assert fields.get("sounding_cape_jkg") == pytest.approx(800.0)
        assert fields.get("sounding_lifted_index") == pytest.approx(-3.0)
        assert fields.get("sounding_lfc_ft") is None
