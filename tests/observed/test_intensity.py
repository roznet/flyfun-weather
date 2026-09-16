"""The intensity ladder behind the "Observed now" words.

The tests that matter here are not the boundary checks — those are arithmetic.
They are the two invariants that stop the feature from quietly going wrong:
the reflectivity and rain-rate ladders must keep agreeing with each other, and
the word must never appear without the number that produced it.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

import pytest

from weatherbrief.models.observed import (
    ObservedAnnulus,
    ObservedConditions,
    ObservedField,
    ObservedStationRef,
    ObservedStationSamples,
)
from weatherbrief.observed.intensity import (
    DBZ_BANDS,
    RATE_BANDS,
    EchoIntensity,
    classify_dbz,
    classify_rate,
)
from weatherbrief.observed.summary import build_summary_entries

NOW = datetime(2026, 8, 25, 14, 10, tzinfo=timezone.utc)
FRAME = datetime(2026, 8, 25, 14, 5, tzinfo=timezone.utc)


# --- the ladders -----------------------------------------------------------


@pytest.mark.parametrize(
    "dbz,expected",
    [
        (None, None),
        (5.0, None),       # a real detection, below every published level
        (17.9, None),
        (18.0, EchoIntensity.LIGHT),
        (29.9, EchoIntensity.LIGHT),
        (30.0, EchoIntensity.MODERATE),
        (40.9, EchoIntensity.MODERATE),
        (41.0, EchoIntensity.HEAVY),
        (45.9, EchoIntensity.HEAVY),
        (46.0, EchoIntensity.VERY_HEAVY),
        (49.9, EchoIntensity.VERY_HEAVY),
        (50.0, EchoIntensity.EXTREME),
        (72.0, EchoIntensity.EXTREME),
    ],
)
def test_dbz_bands_are_the_vip_levels(dbz, expected):
    assert classify_dbz(dbz) is expected


@pytest.mark.parametrize(
    "rate,expected",
    [
        (None, None),
        (0.0, None),
        (0.49, None),
        (0.5, EchoIntensity.LIGHT),
        (2.5, EchoIntensity.MODERATE),
        (10.0, EchoIntensity.HEAVY),
        (30.0, EchoIntensity.VERY_HEAVY),
        (50.0, EchoIntensity.EXTREME),
    ],
)
def test_rate_bands(rate, expected):
    assert classify_rate(rate) is expected


def test_below_the_scale_is_not_the_same_as_unknown():
    """A 12 dBZ return is a measurement, and no level names it.

    Returning LIGHT for it would promote clutter into a named class; raising
    would break a render path.  None is the third answer, and the summary
    renders it by dropping the word and keeping the number.
    """
    assert classify_dbz(12.0) is None
    assert classify_dbz(None) is None


# --- the two ladders must not drift apart ----------------------------------


def _marshall_palmer_mm_h(dbz: float) -> float:
    """Rain rate for a reflectivity under Z = 200 R^1.6."""
    return (10 ** (dbz / 10) / 200) ** (1 / 1.6)


def test_rate_bands_are_the_z_r_equivalents_of_the_dbz_bands():
    """The whole point of the ladder: one cell, one word, either layer.

    Before this the two ramps were independent tables and disagreed by a
    factor of 2-3 at the top — a 45 dBZ core was "heavy" on the reflectivity
    legend and off the scale entirely on the rain-rate one.  Tolerance is 2
    dBZ, which is the rounding applied when the mm/h boundaries were picked
    (13.3 -> 10, 27.3 -> 30, 48.6 -> 50) and far inside the factor-of-two
    spread between Z-R relations.
    """
    assert len(DBZ_BANDS) == len(RATE_BANDS)
    for (dbz_floor, dbz_class), (rate_floor, rate_class) in zip(DBZ_BANDS, RATE_BANDS):
        assert dbz_class is rate_class, "the two ladders name different classes"
        equivalent_dbz = 10 * math.log10(200 * rate_floor**1.6)
        assert abs(equivalent_dbz - dbz_floor) <= 2.0, (
            f"{rate_floor} mm/h is {equivalent_dbz:.1f} dBZ, not {dbz_floor} — "
            "one ladder moved without the other"
        )


def test_a_cell_gets_the_same_word_from_either_product():
    """Sampled across the range, not just at the boundaries."""
    for dbz in (20.0, 25.0, 33.0, 38.0, 43.0, 48.0, 55.0):
        by_reflectivity = classify_dbz(dbz)
        by_rate = classify_rate(_marshall_palmer_mm_h(dbz))
        assert by_reflectivity is by_rate, (
            f"{dbz} dBZ reads {by_reflectivity} but its "
            f"{_marshall_palmer_mm_h(dbz):.1f} mm/h equivalent reads {by_rate}"
        )


# --- the word never replaces the number ------------------------------------


def _field(source: str, units: str, max_value: float, age: float) -> ObservedField:
    return ObservedField(
        source=source,
        quantity=source,
        units=units,
        valid_time=FRAME,
        age_minutes=age,
        window_minutes=10.0,
        stations=[
            ObservedStationSamples(
                station_id="P000",
                annuli=[
                    ObservedAnnulus(
                        radius_nm=20.0,
                        total_px=100,
                        valid_px=90,
                        nodata_px=10,
                        undetect_px=60,
                        detected_px=30,
                        max_value=max_value,
                    )
                ],
            )
        ],
    )


def _conditions(dbz: float | None = None, rate: float | None = None) -> ObservedConditions:
    return ObservedConditions(
        computed_at=NOW,
        corridor_nm=20.0,
        radii_nm=[20.0],
        stations=[ObservedStationRef(id="P000", name="LFAT", lat=50.5, lon=1.6)],
        reflectivity=_field("opera_dbzh", "dBZ", dbz, 8.0) if dbz is not None else None,
        rain_rate=_field("opera_rate", "mm/h", rate, 12.0) if rate is not None else None,
    )


def test_the_measurement_survives_the_gloss():
    """Word AND number, always — the word is a gloss, not a replacement."""
    entries = {e.kind: e for e in build_summary_entries(_conditions(dbz=44.0, rate=18.0))}
    assert "heavy echo" in entries["reflectivity"].text
    assert "44 dBZ" in entries["reflectivity"].text
    assert "(heavy)" in entries["rain_rate"].text
    assert "18.0 mm/h" in entries["rain_rate"].text


def test_both_clauses_agree_on_one_cell():
    """44 dBZ and its 18 mm/h equivalent must not read as different weather."""
    entries = {e.kind: e for e in build_summary_entries(_conditions(dbz=44.0, rate=18.0))}
    assert entries["reflectivity"].category == entries["rain_rate"].category == "heavy"


def test_category_is_machine_readable_and_scoped():
    """Clients style rows off `category`; prose parsing is explicitly barred.

    Only the two intensity-bearing clauses set it, so an empty category is a
    reliable "this row has no class" rather than a value a client must guess at.
    """
    entries = build_summary_entries(_conditions(dbz=33.0, rate=3.1))
    by_kind = {e.kind: e.category for e in entries}
    assert by_kind["reflectivity"] == "moderate"
    assert by_kind["rain_rate"] == "moderate"
    assert by_kind.get("coverage", "") == ""


def test_a_faint_echo_keeps_its_number_and_loses_its_word():
    """Below VIP 1 there is no class, and the summary must not invent one."""
    entries = {e.kind: e for e in build_summary_entries(_conditions(dbz=16.0, rate=0.3))}
    # 16 dBZ is under ECHO_MENTION_DBZ, so the clause is the no-echo form.
    assert "no echo above" in entries["reflectivity"].text
    assert entries["reflectivity"].category == ""
    # 0.3 mm/h is a real rate below the scale: number, no word.
    assert "0.3 mm/h" in entries["rain_rate"].text
    assert "(" in entries["rain_rate"].text  # the age parenthetical, not a class
    assert entries["rain_rate"].category == ""
    for word in ("light", "moderate", "heavy", "extreme"):
        assert word not in entries["rain_rate"].text


def test_the_summary_never_says_rain():
    """OPERA carries no phase, and the same dBZ is a third of the water as snow."""
    entries = build_summary_entries(_conditions(dbz=44.0, rate=18.0))
    text = " ".join(e.text for e in entries).lower()
    assert "rain" not in text, "phase asserted from a product that has none"
