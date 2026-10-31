# Flight captures — screenshots of any flight on iOS, Android and web

Three scripts, one per platform, take the same named screens of **one real flight** from the
real apps, against the local devserver. They are for docs, talks and videos; the store sets
stay on their fixtures (`scripts/ios_screenshots.py`, `app/android/play/listing/`, see
[references/ios-release.md](./references/ios-release.md) §A8 and
[android-play-release.md](./android-play-release.md)).

```
python3 scripts/capture/ios.py     --flight ID [--device "iPad Pro 13-inch (M5)"] [--wait]
python3 scripts/capture/android.py --flight ID [--avd flyfun_pixel10_api37] [--no-build]
python3 scripts/capture/web.py     --flight ID [--size 1440x900] [--scale 2]
   common: [--screens briefing,map,...] [--appearance light --appearance dark] [--out DIR]
→ build/captures/<flight>/<ios|android|web>/<device|avd|WxH>/<light|dark>/NN-<screen>.png
  build/captures/<flight>/<platform>/manifest.json
```

`--flight` takes a bare id or a `briefing.html?flight=ID` link. Run them from the venv
(`source venv/bin/activate`): the token is minted with the checkout's `.env`.

## Before a capture

1. **The devserver over https**: `python3 scripts/ops/devserver.py --https` (the singleton at
   `https://localhost.ro-z.me:8443`; the iOS simulator and the Android emulator both reach it,
   and the web capture uses it too). Any worktree's will do — the captures exercise the apps,
   and the dev DB is shared.
2. **The flight in the dev DB**: `venv/bin/python scripts/ops/import_flight.py <id|URL>` copies
   a prod flight with its packs, live layer and debriefs to the dev user, frozen. A flight
   already in the dev DB (`/api/flights` as the dev user) needs nothing.

Each script checks both first and exits with the command to run when one is missing.

## The screens

Numbered the same everywhere so a doc can say `05-cross-section` and find it in each set. A
screen a run didn't capture is left out and listed under `skipped` in the manifest.

| # | screen | iOS / Android (the app) | web (`briefing.html`) |
|---|---|---|---|
| 01 | flights | the dev user's flight list | `index.html` viewport |
| 02 | briefing | the Advisory tab's summary (iOS switches off Observed, where flight day opens) | viewport at the top |
| 03 | advisories | the Advisories pill of the Advisory tab | the advisories section |
| 04 | discussion | Discussion tab | the Synopsis section |
| 05 | cross-section | Cross-Section tab | viz section, X-Section layout |
| 06 | skewt | iOS: the Sounding sheet; Android: scrolled to the Skew-T below the chart | the Skew-T section |
| 07 | map | Map tab | viz section, Map layout |
| 08 | observed | Observed tab | the "At a glance" section |

`observed` exists only on flight day (the live window): its absence is a skip, not a
failure. Any other screen missing makes the script exit 1 — the shots it did take are still
written. Section shots on the web are the section alone, at the page width; `flights` and
`briefing` are the viewport.

## How each one works

All three sign in as the dev user (`flyfun_common.db.DEV_USER_ID`) with a **20-minute** JWT
minted by `common.mint_token`, a fresh one per device/appearance. The dev `.env` `JWT_SECRET`
is the production secret, so the token is never printed or written to disk; it carries
`scope: review`, which is what lets the apps take it from a bare deep link.

- **iOS** — `FlightCaptureUITests` (UI test target; skipped unless `FLYFUN_CAPTURE=1`, so CI
  and the nightly never run it). The debug app is launched with `-serverEnvironment dev`
  (the devserver) and `FLYFUN_UITEST=1` (tips off), signed in with
  `flyfunweather://auth?token=`, and opens the flight through its universal link
  (`https://weather.flyfun.aero/briefing.html?flight=ID`), falling back to scrolling the list
  to its card. Inputs arrive as `TEST_RUNNER_FLYFUN_CAPTURE_*`; screenshots come back as
  `capture-<screen>` attachments in the result bundle. The simulator is prepared as for the
  store set (`ios_screenshots.prepare`: fresh install, 9:41 status bar, appearance). Through
  `ios_test.run_device`, so one `xcodebuild test` at a time (`--wait` queues). About 2 min
  per device once built.
- **Android** — `FlightCaptureJourney` (androidTest; skipped without its instrumentation
  arguments, so `connectedDebugAndroidTest` never runs it). A debug build on an emulator
  already talks to `https://localhost.ro-z.me:8443` (`ApiClient.DEFAULT_BASE_URL`); the script
  adds `adb reverse tcp:8443`. The journey launches `MainActivity` on the sign-in deep link,
  delivers the briefing link as a new intent (`singleTask`), falls back to scrolling the list,
  and saves `uiAutomation.takeScreenshot()` PNGs to the app's `files/captures/`, which the
  script pulls with `run-as`. Status bar in System UI demo mode (09:41, full signal and
  battery), appearance with `cmd uimode night`; `POST_NOTIFICATIONS` is pre-granted, or the
  permission prompt after sign-in covers the app. Boots the AVD headless when no emulator is
  running and shuts it down after. Gradle's journey runs exclude this class
  (`testInstrumentationRunnerArguments["notClass"]`), and `connectedDebugAndroidTest`
  uninstalls both APKs when it ends — `--no-build` after one says so and stops.
- **Web** — `web/scripts/capture-flight.mjs`, Playwright's Chromium from `web/node_modules`,
  the token in the `flyfun_auth` cookie (passed as an env var, out of `ps`), the guided-tour
  offers marked as seen. `--size` and `--scale` give a phone-width page (`390x844 --scale 3`).

## Limits and care

- **Real data.** Captures show the owner's flights (the list, routes, debriefs): they stay in
  the gitignored `build/`. Pick what goes into a public talk with that in mind; the store
  sets are the ones built on a fictional fixture.
- **What the devserver has is what you get.** A frozen import shows prod's live layer as it
  was at import time; radar and satellite need their feeds on the devserver. Re-import with
  `--overwrite` for a fresher copy.
- **Timing.** Screens are shot after a fixed settle (3 s, the map 5–6 s), not on a "drawn"
  signal: a slow devserver can catch a chart mid-draw. Re-run that screen with `--screens`.
- **Heavy jobs.** The iOS and Android runs each build and drive a simulator/emulator; run
  them one after the other, not together.
