"""Geographic domain of each observed source — "is there radar here?"."""

from __future__ import annotations

import pytest

from weatherbrief.observed.coverage import (
    RADAR_SOURCES,
    covering_sources,
    covers,
    has_radar,
)
from weatherbrief.observed.frames import (
    ALL_SOURCES,
    SOURCE_EUMETSAT_CTTH,
    SOURCE_EUMETSAT_LI,
    SOURCE_OPERA_DBZH,
)

LONDON = (51.48, -0.46)
WARSAW = (52.17, 20.97)
REYKJAVIK = (64.13, -21.94)
NORDKAPP = (71.0, 25.8)
NEW_YORK = (40.64, -73.78)
PHOENIX = (33.43, -112.01)


@pytest.mark.parametrize("point", [LONDON, WARSAW])
def test_europe_has_every_source(point):
    assert set(covering_sources(*point)) == set(ALL_SOURCES)
    assert has_radar(*point)


@pytest.mark.parametrize("point", [NEW_YORK, PHOENIX])
def test_the_us_has_no_observed_source(point):
    assert covering_sources(*point) == ()
    assert not has_radar(*point)


@pytest.mark.parametrize("point", [REYKJAVIK, NORDKAPP])
def test_the_satellite_disc_reaches_the_far_north_of_europe(point):
    assert covers(SOURCE_EUMETSAT_CTTH, *point)[0]
    assert covers(SOURCE_EUMETSAT_LI, *point)[0]


def test_every_source_declares_a_domain():
    # A source without one covers nothing, and would silently never sample.
    for source in ALL_SOURCES:
        assert covers(source, *LONDON)[0], source


def test_covers_is_vectorised():
    lats, lons = zip(LONDON, PHOENIX, WARSAW)
    assert covers(SOURCE_OPERA_DBZH, lats, lons).tolist() == [True, False, True]


def test_unknown_source_covers_nothing():
    assert not covers("mrms_future", *LONDON)[0]


def test_radar_sources_are_the_opera_pair():
    assert SOURCE_OPERA_DBZH in RADAR_SOURCES
    assert SOURCE_EUMETSAT_CTTH not in RADAR_SOURCES
