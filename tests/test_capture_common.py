"""The shared helpers behind scripts/capture/{ios,android,web}.py."""

from __future__ import annotations

import argparse
import json

import pytest

from scripts.capture import common


def test_screens_number_the_same_on_every_platform():
    assert common.file_name("flights") == "01-flights"
    assert common.file_name("cross-section") == "05-cross-section"
    assert common.file_name("observed") == "08-observed"


def test_parse_screens_keeps_canonical_order_and_rejects_unknown():
    assert common.parse_screens(None) == common.SCREENS
    assert common.parse_screens("map, briefing") == ["briefing", "map"]
    with pytest.raises(argparse.ArgumentTypeError):
        common.parse_screens("briefing,radar")


@pytest.mark.parametrize("value", [
    "egtf_lfat-2026-10-30-6c36",
    "https://weather.flyfun.aero/briefing.html?flight=egtf_lfat-2026-10-30-6c36",
    "https://weather.flyfun.aero/briefing.html?flight=egtf_lfat-2026-10-30-6c36&tab=map",
])
def test_flight_id_accepts_a_briefing_url(value):
    assert common.flight_id(value) == "egtf_lfat-2026-10-30-6c36"


def _shots(tmp_path, device, appearance, screens):
    dest = tmp_path / device / appearance
    dest.mkdir(parents=True)
    paths = []
    for screen in screens:
        path = dest / f"{common.file_name(screen)}.png"
        path.write_bytes(b"png")
        paths.append(path)
    return paths


def test_manifest_lists_skips_and_only_fails_on_required_screens(tmp_path, monkeypatch):
    monkeypatch.setattr(common, "git_head", lambda: "abc123")
    shots = _shots(tmp_path, "phone", "light", ["briefing", "map"])
    missing = common.write_manifest(tmp_path, "ios", "f1", {"phone": {"light": shots}},
                                    ["briefing", "skewt", "map", "observed"])
    # Observed is flight-day only: skipped, but not missing.
    assert missing == ["phone/light/skewt"]
    entry = json.loads((tmp_path / "manifest.json").read_text())["captures"]["phone"]["light"]
    assert entry["files"] == ["phone/light/02-briefing.png", "phone/light/07-map.png"]
    assert entry["skipped"] == ["skewt", "observed"]
    assert entry["commit"] == "abc123"


def test_manifest_keeps_other_devices_from_an_earlier_run(tmp_path, monkeypatch):
    monkeypatch.setattr(common, "git_head", lambda: "abc123")
    wide = _shots(tmp_path, "1440x900", "light", ["briefing"])
    common.write_manifest(tmp_path, "web", "f1", {"1440x900": {"light": wide}}, ["briefing"])
    narrow = _shots(tmp_path, "390x844", "dark", ["briefing"])
    common.write_manifest(tmp_path, "web", "f1", {"390x844": {"dark": narrow}}, ["briefing"])
    captures = json.loads((tmp_path / "manifest.json").read_text())["captures"]
    assert set(captures) == {"1440x900", "390x844"}


def test_clear_pngs_drops_only_the_screens_being_recaptured(tmp_path):
    (tmp_path / "03-advisories.png").write_bytes(b"old")
    (tmp_path / "06-skewt.png").write_bytes(b"old")
    common.clear_pngs(tmp_path, ["skewt"])
    assert [p.name for p in tmp_path.iterdir()] == ["03-advisories.png"]


def test_manifest_lists_the_whole_set_after_a_partial_rerun(tmp_path, monkeypatch):
    monkeypatch.setattr(common, "git_head", lambda: "abc123")
    _shots(tmp_path, "phone", "light", ["briefing"])
    (skewt,) = _shots(tmp_path, "phone", "light_rerun", ["skewt"])
    target = tmp_path / "phone" / "light" / skewt.name
    skewt.rename(target)
    missing = common.write_manifest(tmp_path, "ios", "f1", {"phone": {"light": [target]}}, ["skewt"])
    entry = json.loads((tmp_path / "manifest.json").read_text())["captures"]["phone"]["light"]
    assert missing == []
    assert entry["files"] == ["phone/light/02-briefing.png", "phone/light/06-skewt.png"]


def test_flight_argument_is_normalised_by_argparse():
    ap = argparse.ArgumentParser()
    common.add_common_args(ap)
    args = ap.parse_args(["--flight", "https://weather.flyfun.aero/briefing.html?flight=f1-2026&tab=map"])
    assert args.flight == "f1-2026"
