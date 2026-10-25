"""GET retry for the GRIB downloaders (NOAA S3, DWD opendata)."""

from __future__ import annotations

import pytest
import requests

from weatherbrief.fetch.grib import http_retry
from weatherbrief.fetch.grib.hrrr_fetch import HrrrIncompleteArtifact, iter_hrrr_range_chunks


class _Resp:
    def __init__(self, status: int, body: bytes = b"", body_error: Exception | None = None):
        self.status_code = status
        self._body = body
        self._body_error = body_error

    @property
    def content(self) -> bytes:
        if self._body_error is not None:
            raise self._body_error
        return self._body


class _Session:
    """Plays back a script of responses / exceptions, one per GET."""

    def __init__(self, script):
        self.script = list(script)
        self.calls: list[dict] = []

    def get(self, url, headers=None, timeout=None):
        self.calls.append({"url": url, "headers": headers})
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def test_success_first_try():
    s = _Session([_Resp(206, b"abc")])
    resp = http_retry.get_with_retry(s, "https://x/f", timeout=1, headers={"Range": "bytes=0-2"})
    assert resp.content == b"abc"
    assert len(s.calls) == 1
    assert s.calls[0]["headers"] == {"Range": "bytes=0-2"}


@pytest.mark.parametrize("failure", [
    requests.ReadTimeout("slow"),
    requests.ConnectionError("reset"),
    _Resp(503),
    _Resp(429),
    # Headers arrived, then the body stalled: requests raises while reading it.
    _Resp(206, body_error=requests.exceptions.ChunkedEncodingError("cut short")),
])
def test_transient_failure_then_success(failure):
    s = _Session([failure, _Resp(206, b"ok")])
    resp = http_retry.get_with_retry(s, "https://x/f", timeout=1)
    assert resp.status_code == 206
    assert resp.content == b"ok"
    assert len(s.calls) == 2


def test_404_is_an_answer_not_retried():
    s = _Session([_Resp(404)])
    assert http_retry.get_with_retry(s, "https://x/f", timeout=1).status_code == 404
    assert len(s.calls) == 1


def test_network_failure_every_attempt_raises_last():
    s = _Session([requests.ReadTimeout("1"), requests.ReadTimeout("2"), requests.ReadTimeout("3")])
    with pytest.raises(requests.ReadTimeout, match="3"):
        http_retry.get_with_retry(s, "https://x/f", timeout=1)
    assert len(s.calls) == 3


def test_retryable_status_every_attempt_returns_last():
    s = _Session([_Resp(503), _Resp(503), _Resp(502)])
    assert http_retry.get_with_retry(s, "https://x/f", timeout=1).status_code == 502
    assert len(s.calls) == 3


def test_range_stream_survives_one_timeout():
    """One slow span no longer drops the artifact (#913 GFS, HRRR)."""
    s = _Session([_Resp(206, b"aa"), requests.ReadTimeout("slow"), _Resp(206, b"bb")])
    chunks = list(iter_hrrr_range_chunks("https://x/f", [(0, 1), (2, 3)], s))
    assert b"".join(chunks) == b"aabb"


def test_range_stream_still_refuses_a_persistent_failure():
    s = _Session([_Resp(206, b"aa")] + [requests.ReadTimeout("slow")] * 3)
    with pytest.raises(HrrrIncompleteArtifact):
        list(iter_hrrr_range_chunks("https://x/f", [(0, 1), (2, 3)], s))
