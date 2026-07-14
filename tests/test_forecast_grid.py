"""The forecast-map horizon policy, checked against ECMWF's real delivery.

These tests exist because the grid is not rectangular and the reasons why are
external (a delivery contract and two model horizons), not something the code
can derive. If the delivery changes, these should fail loudly rather than let
the map quietly sample steps that were never delivered.
"""

from weatherbrief.tasks.forecast_grid import (
    COARSE_FROM_DAY,
    COARSE_SAMPLE_HOURS,
    ECMWF_3H_MAX_STEP_H,
    ECMWF_MAX_STEP_H,
    FINE_SAMPLE_HOURS,
    MAP_FORECAST_DAYS,
    MAX_FORECAST_DAY,
    day_hour_pairs,
    forecast_days,
    sample_hours_for_day,
)

# ECMWF operational delivery for the 00Z/12Z runs (delivery_config.json):
# hourly to 90h, 3-hourly to 144h, then 6-hourly, stopping dead at 168h.
ECMWF_DELIVERED_STEPS = (
    list(range(0, 91))
    + list(range(93, 145, 3))
    + [150, 156, 162, 168]
)
ECMWF_MAX_STEP = 168
ICON_EU_MAX_STEP = 120  # cloud-diag GRIB horizon, main cycles


def _steps_for(day: int, init_hour: int) -> list[int]:
    """Lead-time steps the map's sample hours land on, for a given day/run."""
    return [24 * day + h - init_hour for h in sample_hours_for_day(day)]


class TestEcmwfCoverage:
    """Every hour the map offers must be a step ECMWF actually delivers."""

    def test_every_offered_slot_is_delivered(self):
        for init_hour in (0, 12):
            for day in forecast_days():
                for step in _steps_for(day, init_hour):
                    if step <= 0:
                        continue  # before init — legitimately skipped
                    assert step in ECMWF_DELIVERED_STEPS, (
                        f"{init_hour:02d}Z run, D+{day}: step {step}h is offered "
                        f"on the map but ECMWF does not deliver it"
                    )

    def test_far_day_needs_the_coarse_grid(self):
        """The 09Z/15Z slots on D+6 fall between ECMWF's 6-hourly steps.

        This is the whole reason the far day is sampled at three hours: with
        the fine grid, two of five slots would be permanently ECMWF-less.
        """
        undelivered = [
            24 * MAX_FORECAST_DAY + h
            for h in FINE_SAMPLE_HOURS
            if (24 * MAX_FORECAST_DAY + h) not in ECMWF_DELIVERED_STEPS
        ]
        assert undelivered == [153, 159]
        assert all(h not in COARSE_SAMPLE_HOURS for h in (9, 15))

    def test_one_day_further_would_fall_off_the_wall(self):
        """D+7's first slot is past 168h — which is why D+6 is the limit."""
        first_slot = 24 * (MAX_FORECAST_DAY + 1) + min(COARSE_SAMPLE_HOURS)
        assert first_slot > ECMWF_MAX_STEP


class TestModelHorizons:
    def test_icon_stops_inside_its_grib_horizon(self):
        """ICON is fetched only as far as its ceiling GRIB reaches (120h).

        Storing ICON rows past that would put a model on the map with no
        ceiling — absence reading as agreement.
        """
        last_step = max(_steps_for(MAP_FORECAST_DAYS["icon"], init_hour=0))
        assert last_step <= ICON_EU_MAX_STEP

    def test_first_icon_less_day_is_beyond_icon(self):
        """ICON drops out on a clean day boundary, not mid-day."""
        first_dropped_day = MAP_FORECAST_DAYS["icon"] + 1
        assert min(_steps_for(first_dropped_day, init_hour=0)) > ICON_EU_MAX_STEP

    def test_ecmwf_reaches_the_last_offered_day(self):
        assert MAP_FORECAST_DAYS["ecmwf"] == MAX_FORECAST_DAY

    def test_far_days_keep_two_models(self):
        """A day with only one model has no cross-check and must not be offered."""
        for day in forecast_days():
            reaching = [m for m, d in MAP_FORECAST_DAYS.items() if d >= day]
            assert len(reaching) >= 2, f"D+{day} would show {reaching} alone"


class TestConstantsAreDerivedNotCoincidental:
    """MAX_FORECAST_DAY and COARSE_FROM_DAY are both 6 — for different reasons.

    One is ECMWF's 168h wall; the other is where its cadence thins to 6-hourly
    past 144h. They must be derived from those two facts independently, so that
    a change to the delivery contract moves each on its own.
    """

    def test_they_currently_coincide(self):
        assert MAX_FORECAST_DAY == 6
        assert COARSE_FROM_DAY == 6

    def test_the_wall_alone_sets_the_last_day(self):
        """D+6's first slot is inside 168h; D+7's is not."""
        assert 24 * MAX_FORECAST_DAY + min(FINE_SAMPLE_HOURS) <= ECMWF_MAX_STEP_H
        assert 24 * (MAX_FORECAST_DAY + 1) + min(FINE_SAMPLE_HOURS) > ECMWF_MAX_STEP_H

    def test_the_cadence_alone_sets_the_coarse_day(self):
        """The day before goes coarse still fits inside the 3-hourly region."""
        assert 24 * (COARSE_FROM_DAY - 1) + max(FINE_SAMPLE_HOURS) <= ECMWF_3H_MAX_STEP_H
        assert 24 * COARSE_FROM_DAY + max(FINE_SAMPLE_HOURS) > ECMWF_3H_MAX_STEP_H

    def test_the_delivery_facts_match_the_real_step_list(self):
        """Guard the two constants against the actual delivered steps."""
        assert max(ECMWF_DELIVERED_STEPS) == ECMWF_MAX_STEP_H
        three_hourly = [s for s in ECMWF_DELIVERED_STEPS if s <= ECMWF_3H_MAX_STEP_H]
        # Everything past the 3-hourly region is on a 6-hourly step.
        beyond = [s for s in ECMWF_DELIVERED_STEPS if s > ECMWF_3H_MAX_STEP_H]
        assert max(three_hourly) == ECMWF_3H_MAX_STEP_H
        assert all(s % 6 == 0 for s in beyond)


class TestGridShape:
    def test_near_days_are_fine_grained(self):
        for day in range(MAX_FORECAST_DAY):
            assert sample_hours_for_day(day) == FINE_SAMPLE_HOURS

    def test_far_day_is_coarse(self):
        assert sample_hours_for_day(MAX_FORECAST_DAY) == COARSE_SAMPLE_HOURS

    def test_coarse_hours_are_a_subset_of_fine(self):
        assert set(COARSE_SAMPLE_HOURS) <= set(FINE_SAMPLE_HOURS)

    def test_slot_count(self):
        # 6 days x 5 hours + 1 day x 3 hours
        assert len(day_hour_pairs()) == 6 * 5 + 3
