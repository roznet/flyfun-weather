# Shared app fixtures

Test data both mobile apps use, kept once here (#895).

- `api/` — one mock API response per endpoint, served in mock mode (`FLYFUN_MOCK=1`) by
  `FixtureBriefingRepository` on iOS (via `FixtureFiles.swift`, Debug builds only) and on
  Android (via `FixtureFiles.kt`, all builds: the login screen's demo mode uses them). Decoded
  through each app's production decoder, so a DTO change that breaks them fails loudly. Editing
  a file changes both platforms' UI journeys.
- `live-scenarios/` — `/live` ticks of a real flight morning, written by
  `scripts/export_live_scenario_ios.py` (`tests/test_live_scenarios.py` fails if they drift
  from the server). Read by the iOS live-scenario journey and the web unit tests.
- `showcase/showcase.json` — the App Store screenshot briefing, written by
  `scripts/export_showcase_fixture_ios.py`.

Design notes: `designs/ios-app-ui.md` (UI-test fixtures) and
`designs/android-app-architecture.md` (UI Tests & Mock Launch).
