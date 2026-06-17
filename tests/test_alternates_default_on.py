"""Guard the graduated default for weather-alternates (#210 → default-on).

The feature was promoted from experimental/opt-out to a normal default-on
feature. Two defaults must stay `True`:

* `BriefingOptions.compute_alternates` — the dataclass default applied when the
  caller does not pass an explicit value.
* The pack-build resolution `profile_settings.get("compute_alternates", True)` —
  a profile with no stored key (new profiles) resolves to on. We assert the
  `dict.get` contract directly since the resolution is inline in the pack
  builder.

The `days_out <= 2` gate is unchanged and is exercised elsewhere.
"""
from __future__ import annotations

from weatherbrief.pipeline import BriefingOptions


def test_pipeline_options_compute_alternates_defaults_on():
    assert BriefingOptions().compute_alternates is True


def test_missing_profile_key_resolves_to_on():
    # NOTE: intent/documentation test only, NOT a regression guard. The real
    # resolution `profile_settings.get("compute_alternates", True)` is inline in
    # api/packs.py with no extractable wrapper, so this only documents the
    # expected dict.get semantics: absent key → on (new default), explicit False
    # → off (opt-out honoured). A revert of the packs.py default would NOT fail
    # here — the BriefingOptions guard above is the real default regression test.
    settings: dict = {}
    assert settings.get("compute_alternates", True) is True
    assert {"compute_alternates": False}.get("compute_alternates", True) is False
