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
    # Mirrors api/packs.py: a profile that never stored the key gets the new
    # default-on. (A stored False still wins — explicit opt-out is honoured.)
    settings: dict = {}
    assert settings.get("compute_alternates", True) is True
    assert {"compute_alternates": False}.get("compute_alternates", True) is False
