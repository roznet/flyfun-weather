"""Warm paths must not drown the warning channel with publication-lag 404s.

DWD publishes a run progressively, so the airport-profile precache runs ahead
of the frontier by design and asks for files that only exist minutes later.
Those wholesale 404s were 68% of every WARNING the app emitted (~670/day),
burying the 313 that meant something.

``expect_missing`` lets a caller say "absent is routine here". The severity is
keyed on the *caller*, never on the status alone: a wholesale 404 on a briefing
path is a real signal — it is what a DWD URL-scheme change would look like —
and must keep warning. A mixed or non-404 failure set stays a warning even on a
warm path.
"""

from __future__ import annotations

import logging

import pytest

from weatherbrief.fetch.grib import icon_eu_fetch as icon_fetch_mod
from weatherbrief.fetch.grib.icon_eu_fetch import (
    _missing_is_expected,
    fetch_icon_eu_per_level,
    fetch_icon_eu_per_variable,
    fetch_icon_eu_single_level,
)

_LOGGER = "weatherbrief.fetch.grib.icon_eu_fetch"


def _warnings(caplog) -> list[str]:
    return [
        r.getMessage() for r in caplog.records
        if r.name == _LOGGER and r.levelno >= logging.WARNING
    ]


# ---------------------------------------------------------------------------
# The predicate
# ---------------------------------------------------------------------------


class TestMissingIsExpected:

    def test_all_404_and_opted_in_is_expected(self):
        assert _missing_is_expected({404: 40}, True) is True

    def test_all_404_without_opting_in_still_warns(self):
        """The briefing path never opts in, so its 404s stay loud."""
        assert _missing_is_expected({404: 40}, False) is False

    @pytest.mark.parametrize("failures", [
        {404: 39, 500: 1},
        {500: 40},
        {404: 39, "timeout": 1},
        {"connection_error": 5},
    ])
    def test_non_404_failures_are_never_quiet(self, failures):
        """A real upstream error is not publication lag, opted in or not."""
        assert _missing_is_expected(failures, True) is False

    def test_no_failures_is_not_expected_missing(self):
        assert _missing_is_expected({}, True) is False


# ---------------------------------------------------------------------------
# The three batch fetchers
# ---------------------------------------------------------------------------


class TestSingleLevelSeverity:

    def _all_404(self, monkeypatch):
        monkeypatch.setattr(
            icon_fetch_mod, "_download_one_file", lambda url, session: (None, 404),
        )

    def test_quiet_when_opted_in(self, monkeypatch, caplog):
        self._all_404(monkeypatch)
        with caplog.at_level(logging.DEBUG, logger=_LOGGER):
            fetch_icon_eu_single_level(
                "20260826", 0, [80], session=object(), expect_missing=True,
            )
        assert _warnings(caplog) == []
        assert any("all" in r.getMessage() and r.levelno == logging.DEBUG
                   for r in caplog.records), "should still be traceable at DEBUG"

    def test_warns_by_default(self, monkeypatch, caplog):
        self._all_404(monkeypatch)
        with caplog.at_level(logging.DEBUG, logger=_LOGGER):
            fetch_icon_eu_single_level("20260826", 0, [80], session=object())
        assert len(_warnings(caplog)) == 1

    def test_warns_on_mixed_failures_even_when_opted_in(self, monkeypatch, caplog):
        calls = {"n": 0}

        def fake(url, session):
            calls["n"] += 1
            return (None, 500) if calls["n"] == 1 else (None, 404)

        monkeypatch.setattr(icon_fetch_mod, "_download_one_file", fake)
        with caplog.at_level(logging.DEBUG, logger=_LOGGER):
            fetch_icon_eu_single_level(
                "20260826", 0, [80], session=object(), expect_missing=True,
            )
        assert len(_warnings(caplog)) == 1


class TestPerVariableSeverity:

    def test_quiet_when_opted_in(self, monkeypatch, caplog):
        monkeypatch.setattr(
            icon_fetch_mod, "_download_one_file", lambda url, session: (None, 404),
        )
        with caplog.at_level(logging.DEBUG, logger=_LOGGER):
            fetch_icon_eu_per_variable(
                "20260826", 0, 80, levels=[10, 11], variables=["t"],
                session=object(), expect_missing=True,
            )
        assert _warnings(caplog) == []

    def test_warns_by_default(self, monkeypatch, caplog):
        monkeypatch.setattr(
            icon_fetch_mod, "_download_one_file", lambda url, session: (None, 404),
        )
        with caplog.at_level(logging.DEBUG, logger=_LOGGER):
            fetch_icon_eu_per_variable(
                "20260826", 0, 80, levels=[10, 11], variables=["t"],
                session=object(),
            )
        assert len(_warnings(caplog)) == 1

    def test_incomplete_column_is_quiet_when_opted_in(self, monkeypatch, caplog):
        """Half a column is the same "still landing" case as none of it."""
        monkeypatch.setattr(
            icon_fetch_mod, "_download_one_file",
            lambda url, session: (b"ok", 200) if "_10_" in url else (None, 404),
        )
        with caplog.at_level(logging.DEBUG, logger=_LOGGER):
            fetch_icon_eu_per_variable(
                "20260826", 0, 80, levels=[10, 11], variables=["t"],
                session=object(), expect_missing=True,
            )
        assert _warnings(caplog) == []

    def test_incomplete_column_warns_on_a_real_error(self, monkeypatch, caplog):
        monkeypatch.setattr(
            icon_fetch_mod, "_download_one_file",
            lambda url, session: (b"ok", 200) if "_10_" in url else (None, 500),
        )
        with caplog.at_level(logging.DEBUG, logger=_LOGGER):
            fetch_icon_eu_per_variable(
                "20260826", 0, 80, levels=[10, 11], variables=["t"],
                session=object(), expect_missing=True,
            )
        assert len(_warnings(caplog)) == 1


class TestPerLevelSeverity:

    def test_quiet_when_opted_in(self, monkeypatch, caplog):
        monkeypatch.setattr(
            icon_fetch_mod, "_download_one_file", lambda url, session: (None, 404),
        )
        with caplog.at_level(logging.DEBUG, logger=_LOGGER):
            fetch_icon_eu_per_level(
                "20260826", 0, 80, levels=[10], variables=["t"],
                session=object(), expect_missing=True,
            )
        assert _warnings(caplog) == []

    def test_warns_by_default(self, monkeypatch, caplog):
        monkeypatch.setattr(
            icon_fetch_mod, "_download_one_file", lambda url, session: (None, 404),
        )
        with caplog.at_level(logging.DEBUG, logger=_LOGGER):
            fetch_icon_eu_per_level(
                "20260826", 0, 80, levels=[10], variables=["t"],
                session=object(),
            )
        assert len(_warnings(caplog)) == 1
