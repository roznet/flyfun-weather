"""orjson-first artifact parsing keeps the stdlib's results (#704)."""

from __future__ import annotations

import gzip
import json
import math

from weatherbrief.tasks.artifacts import _loads, _read_json_or_gz


def test_loads_matches_stdlib_on_standard_json():
    obj = {"cross_sections": [{"a": 1.1, "b": [None, True, "é", 1e-7, 12345678901234]}]}
    raw = json.dumps(obj).encode()
    assert _loads(raw) == json.loads(raw)


def test_loads_falls_back_for_nan_written_by_json_dumps():
    # json.dumps writes NaN/Infinity tokens, which orjson rejects; the fallback
    # must read them back as floats exactly as before, not fail or null them.
    raw = json.dumps({"x": float("nan"), "y": float("inf")}).encode()
    out = _loads(raw)
    assert math.isnan(out["x"]) and out["y"] == float("inf")


def test_read_json_or_gz_plain_and_gzipped(tmp_path):
    base = tmp_path / "cross_section.json"
    payload = {"cross_sections": [{"v": 0.1}]}
    base.write_text(json.dumps(payload))
    assert _read_json_or_gz(base) == payload
    base.unlink()
    (tmp_path / "cross_section.json.gz").write_bytes(gzip.compress(json.dumps(payload).encode()))
    assert _read_json_or_gz(base) == payload
    assert _read_json_or_gz(tmp_path / "missing.json") is None
