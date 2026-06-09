"""Tests for EDR calibration persistence + readout (issue #221)."""

from __future__ import annotations

import pytest

from weatherbrief.analysis.sounding.edr import DIAGNOSTIC_RICHARDSON, EdrAccumulator
from weatherbrief.db.models import EdrCalibrationAccumulatorRow
from weatherbrief.tasks.edr_calibration import (
    _readout_rows,
    flush_accumulator,
    load_coefficients,
)


@pytest.fixture(autouse=True)
def _clean_edr_table(db_session):
    # flush_accumulator commits (the real table accumulates indefinitely), so
    # committed rows survive db_session's rollback on the shared in-memory
    # engine. Wipe the table before each test for isolation.
    db_session.query(EdrCalibrationAccumulatorRow).delete()
    db_session.commit()
    yield


class _Level:
    def __init__(self, richardson_number=None, altitude_ft=None):
        self.richardson_number = richardson_number
        self.altitude_ft = altitude_ft


def _seed(acc, model="gfs"):
    # Spread of Ri values across one band so variance is non-zero.
    acc.observe_richardson_levels(model, [
        _Level(0.5, 12_000),
        _Level(1.0, 13_000),
        _Level(2.0, 14_000),
        _Level(5.0, 15_000),
    ])


def test_flush_inserts_then_adds_idempotently(db_session):
    acc = EdrAccumulator()
    _seed(acc)
    written = flush_accumulator(db_session, acc)
    assert written >= 1

    rows = db_session.query(EdrCalibrationAccumulatorRow).all()
    by_band = {r.band: r for r in rows}
    assert "10_20kft" in by_band and "all" in by_band
    n_first = by_band["10_20kft"].n
    s1_first = by_band["10_20kft"].sum_ln

    # A second run's moments must ADD to the existing row, not replace it.
    acc2 = EdrAccumulator()
    _seed(acc2)
    flush_accumulator(db_session, acc2)

    row = (
        db_session.query(EdrCalibrationAccumulatorRow)
        .filter_by(model="gfs", diagnostic=DIAGNOSTIC_RICHARDSON, band="10_20kft")
        .one()
    )
    assert row.n == n_first * 2
    assert row.sum_ln == pytest.approx(s1_first * 2)


def test_flush_empty_accumulator_is_noop(db_session):
    assert flush_accumulator(db_session, EdrAccumulator()) == 0
    assert db_session.query(EdrCalibrationAccumulatorRow).count() == 0


def test_load_coefficients_roundtrip(db_session):
    acc = EdrAccumulator()
    _seed(acc)
    flush_accumulator(db_session, acc)

    coeffs = load_coefficients(db_session, "gfs", "10_20kft")
    assert coeffs is not None
    a, b = coeffs
    assert b > 0  # diagnostic increases with turbulence → positive slope

    # Unknown band / missing row → None.
    assert load_coefficients(db_session, "gfs", "not_a_band") is None
    assert load_coefficients(db_session, "icon", "10_20kft") is None


def test_readout_reports_edr_percentiles(db_session):
    acc = EdrAccumulator()
    _seed(acc)
    flush_accumulator(db_session, acc)

    readout = _readout_rows(db_session)
    assert readout
    entry = next(r for r in readout if r["band"] == "10_20kft")
    assert entry["a"] is not None and entry["b"] is not None
    # Percentiles present and monotonically non-decreasing p50 ≤ p90 ≤ p99.
    p50, p90, p99 = entry["edr"]["p50"], entry["edr"]["p90"], entry["edr"]["p99"]
    assert 0.0 <= p50 <= p90 <= p99 <= 1.0
