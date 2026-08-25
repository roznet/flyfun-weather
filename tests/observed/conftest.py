"""Shared fixtures for the observed-conditions tests.

The granules under ``data/`` are synthetic but structurally faithful — see
``make_fixtures.py`` for what each scene is built to exercise.  They are
committed rather than downloaded so the suite stays offline and the
parallax regression has something deterministic to fail against.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from weatherbrief.observed.sampler import SampleStation

DATA_DIR = Path(__file__).parent / "data"

# The station every fixture scene is built around (LFAT, Le Touquet).
STATION = SampleStation("LFAT", 50.517, 1.627)
# A station in the fixture's deliberate no-radar-coverage half.
STATION_NO_COVERAGE = SampleStation("WEST", 50.517, 0.5)


@pytest.fixture
def data_dir() -> Path:
    return DATA_DIR


@pytest.fixture
def dbzh_path() -> Path:
    return DATA_DIR / "opera_dbzh.h5"


@pytest.fixture
def rate_path() -> Path:
    return DATA_DIR / "opera_rate.h5"


@pytest.fixture
def ctth_path() -> Path:
    return DATA_DIR / "ctth.nc"


@pytest.fixture
def li_path() -> Path:
    return DATA_DIR / "li_flashes.nc"
