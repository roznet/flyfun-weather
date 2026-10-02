"""Write the iOS UI-test fixtures for the live-layer scenarios.

Replays each scenario (tests/fixtures/live_scenarios/) through the production
live layer and writes the chosen ticks as ``/live`` response bodies under
app/flyfun-weather/flyfun-weatherUITests/LiveScenarios/<scenario>_<HHMM>.json.
The UI test feeds each one to the mock app (FLYFUN_MOCK_LIVE_JSON) and checks
every change renders at its tier. tests/test_live_scenarios.py fails when these
files drift from what the server would produce — rerun this script then.

Usage:
    PYTHONPATH=tests python scripts/export_live_scenario_ios.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "tests"))

from live_scenario_replay import IOS_TICKS, ios_tick_files, load_scenario, replay  # noqa: E402


def main() -> int:
    for name, at in IOS_TICKS.items():
        with tempfile.TemporaryDirectory() as d:
            files = ios_tick_files(name, replay(load_scenario(name), Path(d) / "u" / "flight"), at)
        for path, text in files.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
            print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
