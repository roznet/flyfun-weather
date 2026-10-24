"""Open-Meteo billed-call weighting and the usage meter (#910 accounting)."""

from __future__ import annotations

import threading
from contextvars import copy_context

import pytest

from weatherbrief.fetch import open_meteo
from weatherbrief.fetch.open_meteo import (
    OpenMeteoClient,
    OpenMeteoUsage,
    open_meteo_meter,
    request_weight,
)


def _vars(n: int) -> str:
    return ",".join(f"v{i}" for i in range(n))


class TestRequestWeight:
    """Pinned to Open-Meteo's own `calculateQueryWeight` and pricing examples."""

    def test_pricing_page_examples(self):
        # "2 weeks of data with 15 weather variables ... 1.5 API calls"
        assert request_weight({"latitude": "1", "hourly": _vars(15), "forecast_days": 14}) == 1.5
        # "4 weeks of data equals 3.0 API calls" (same 15 variables)
        assert request_weight({"latitude": "1", "hourly": _vars(15), "forecast_days": 28}) == 3.0

    def test_small_request_is_one_call(self):
        assert request_weight({"latitude": "1", "hourly": _vars(3), "forecast_days": 7}) == 1.0

    def test_floor_of_one_applies_per_location(self):
        # 3 locations, few variables: each location bills its own minimum.
        assert request_weight({"latitude": "1,2,3", "hourly": _vars(3)}) == 3.0

    def test_time_alone_does_not_inflate_a_light_request(self):
        # max(v/10, v/10 * days/14) floored at 1: 5 vars over 28 days = 1.0.
        assert request_weight({"latitude": "1", "hourly": _vars(5), "forecast_days": 28}) == 1.0

    def test_locations_multiply(self):
        params = {"latitude": ",".join(["1"] * 60), "hourly": _vars(110),
                  "start_date": "2026-10-24", "end_date": "2026-10-25"}
        assert request_weight(params) == 60 * 11.0

    def test_variables_sum_across_sections_and_models(self):
        params = {"latitude": "1", "hourly": _vars(10), "daily": _vars(5),
                  "current": _vars(5), "models": "a,b", "forecast_days": 7}
        assert request_weight(params) == (20 * 2) / 10

    def test_list_params(self):
        assert request_weight({"latitude": [1, 2], "hourly": [f"v{i}" for i in range(20)]}) == 4.0

    def test_date_range_is_inclusive(self):
        base = {"latitude": "1", "hourly": _vars(20)}
        one_day = {**base, "start_date": "2026-10-24", "end_date": "2026-10-24"}
        four_weeks = {**base, "start_date": "2026-10-01", "end_date": "2026-10-28"}
        assert request_weight(one_day) == 2.0
        assert request_weight(four_weeks) == 4.0

    def test_date_range_overrides_forecast_days(self):
        params = {"latitude": "1", "hourly": _vars(20), "forecast_days": 28,
                  "start_date": "2026-10-24", "end_date": "2026-10-24"}
        assert request_weight(params) == 2.0

    def test_unparseable_window_falls_back_instead_of_raising(self):
        # 20 vars over the 7-day default: 2.0. Accounting never breaks a fetch.
        base = {"latitude": "1", "hourly": _vars(20)}
        assert request_weight({**base, "start_date": "2026-10-24", "end_date": "nope"}) == 2.0
        assert request_weight({**base, "forecast_days": "seven"}) == 2.0

    def test_past_days_extend_the_window(self):
        params = {"latitude": "1", "hourly": _vars(10), "forecast_days": 14, "past_days": 14}
        assert request_weight(params) == 2.0


class _Resp:
    def __init__(self, status: int = 200):
        self.status_code = status
        self.headers: dict = {}

    def raise_for_status(self):
        pass

    def json(self):
        return {}


PARAMS = {"latitude": "1,2", "hourly": _vars(30), "forecast_days": 7}  # weight 6.0


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("OPENMETEO_API_KEY", raising=False)
    monkeypatch.setattr(open_meteo.time, "sleep", lambda s: None)

    def make(statuses=(200,)):
        c = OpenMeteoClient()
        queue = list(statuses)
        c.session.get = lambda url, params, timeout: _Resp(queue.pop(0) if queue else 200)
        return c
    return make


class TestClientWeight:
    def test_records_weight_per_request(self, client):
        c = client()
        c.get_json("https://api.open-meteo.com/v1/gfs", PARAMS)
        c.get_json("https://api.open-meteo.com/v1/gfs", PARAMS)
        assert c.usage == OpenMeteoUsage(calls=2, weight=12.0)

    def test_retries_are_billed(self, client):
        c = client(statuses=(503, 200))
        c.get_json("https://api.open-meteo.com/v1/gfs", PARAMS)
        assert c.usage == OpenMeteoUsage(calls=2, weight=12.0)

    def test_weight_exact_under_threads(self, client):
        c = client()
        threads = [
            threading.Thread(target=lambda: [c.get_json("u", PARAMS) for _ in range(50)])
            for _ in range(8)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert c.call_weight == 8 * 50 * 6.0


class TestMeter:
    def test_counts_clients_built_inside(self, client):
        outside = client()
        with open_meteo_meter() as meter:
            inside = client()
            inside.get_json("u", PARAMS)
            outside.get_json("u", PARAMS)
        assert meter.usage == OpenMeteoUsage(calls=1, weight=6.0)

    def test_follows_copy_context_threads(self, client):
        def work():
            client().get_json("u", PARAMS)

        with open_meteo_meter() as meter:
            ctx_threads = [threading.Thread(target=copy_context().run, args=(work,)) for _ in range(3)]
            for t in ctx_threads:
                t.start()
            for t in ctx_threads:
                t.join()
        assert meter.usage == OpenMeteoUsage(calls=3, weight=18.0)

    def test_inner_meter_does_not_leak_to_outer(self, client):
        with open_meteo_meter() as outer:
            with open_meteo_meter() as inner:
                client().get_json("u", PARAMS)
            client().get_json("u", PARAMS)
        assert inner.usage.calls == 1
        assert outer.usage.calls == 1

    def test_no_meter_outside_block(self, client):
        with open_meteo_meter():
            pass
        c = client()
        assert c._meter is None
