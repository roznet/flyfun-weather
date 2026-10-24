# First-run setup (welcome wizard)

> The once-per-account setup flow on web, iOS and Android: welcome, default profile, aircraft + usual mission, done. Shown while `setup_completed` is false.

## Why it exists

A new account starts with the seeded system profiles (default **VFR Only**) and the server's
synthesised placeholder aircraft ("My aircraft", `ZZZZ`, `is_placeholder`, #777). Left alone,
an IFR pilot is briefed as VFR only, a FIKI aircraft gets non-FIKI icing grades (icing is
graded for the aircraft, #827), and durations run on default speeds. The wizard asks the
three things that change the briefing, once.

## Surfaces

| Client | Code | Shown when |
|--------|------|------------|
| Web | `web/ts/components/welcome-wizard.ts` | `/auth/me` `setup_completed` false |
| iOS | `Views/Setup/SetupWizardView.swift`, `ViewModels/SetupWizardModel.swift`, `Services/SetupRepository.swift`, `Models/API/SetupModels.swift` | `AppState.checkSetupNeeded` (once per sign-in, `.task(id: jwt)` on the flight list) → full-screen cover |
| Android | `ui/setup/SetupWizardScreen.kt`, `viewmodel/SetupWizardViewModel.kt`, `data/SetupRepository.kt` | `AppStateViewModel.checkSetupNeeded` (launch with a token, `completeLogin`, UI-test session) → replaces the root UI |

All three make the same server calls; the server is unchanged by the mobile port (#900).

## Steps and server calls

1. **Welcome**: copy only (`wizard.welcome*`, `wizard.feature*`, `wizard.betaNotice`).
2. **Profile**: cards for the profiles with a `system_template_key`, described by
   `GET /api/user/profiles/system-templates?locale=…`. Next → `PUT /api/user/profiles/{id}`
   `{is_default: true}`.
3. **Aircraft**, two groups:
   - *Your aircraft* → `POST /api/aircraft` with `is_default: true` (the server replaces a sole
     placeholder and re-points its flights). If a real default already exists it is `PUT`
     instead, and its `is_ifr` is left alone. `is_ifr` = flight rules ≠ `vfr_only`. ICAO type is
     required (`ZZZZ` is the escape).
   - *Your usual mission* → `PUT /api/user/profiles/{id}` `{settings: {flight_rules,
     cruise_altitude_ft, flight_ceiling_ft}}`. The settings merge is sparse, so omitted keys
     (models, advisories) keep the template's values. Warns, never clamps, when the mission
     ceiling is above the service ceiling.
4. **Done**: `POST /api/user/preferences/setup-complete` (204). **Skip** (any step) calls it too,
   so the flow never returns; Settings stays the place to finish.

## Decisions

- **`setup_completed` comes from `/auth/me`, not `/api/user/preferences`.** The preferences
  response does not carry it (the Android `PreferencesResponse.setupCompleted` field is never
  sent). `/auth/me` returns false when the user has no preferences row; the apps treat a
  missing field as "not done", like the web.
- **FIKI pre-tick** follows the profile until the pilot touches the box: template `ifr_fiki`, or
  `advisories.enabled.fiki_icing` (the server's `profile_declares_fiki`, #850). A real aircraft
  that already exists keeps its own answer. The native forms send an explicit `is_fiki`, so the
  server's "omitted → profile" default never reaches them; this pre-tick is how the apps get it.
- **Once per account, not per device.** An account set up on the web never sees the app wizard.
  Existing app users who never completed setup anywhere see it once after the release; it is
  skippable and starts from their real aircraft when they have one.
- **Mobile differences from the web** (kept small during the release): no forecast-model picker
  (the template's models are kept), no demo-tour link, English only (both apps are English
  only), and the apps stop where the web logs and continues: a failed aircraft or mission save
  stays on the aircraft step with an error (Next again PUTs the saved aircraft and retries), and
  a failed load of profiles or aircraft shows Retry and blocks Next past welcome (no duplicate
  default aircraft, no `setup-complete` over profile writes that never ran). Only the
  default-profile PUT is logged and skipped, as on the web.
- **One write at a time.** Each Next holds `isSaving` for its whole step, and Skip is disabled
  while it is set, so a double tap never repeats a write and a save never finishes behind a
  closed wizard. The `/auth/me` check drops a late answer for a changed session (and on Android
  a superseded check is cancelled), so a dismissed wizard does not come back that launch.
- **Demo mode** (Android "Explore Demo Briefings") has no account, so no wizard.

## Testing

- Unit: iOS `SetupWizardModelTests` (stub repository), Android `SetupWizardViewModelTest` (the
  fixture repository, which applies writes like the server).
- Journeys: iOS `SetupUITests`, Android `SetupUITests` (finish with IFR FIKI → aircraft saved
  FIKI; skip → flight list). Launch with `FLYFUN_MOCK_SETUP=1`; without it the fixture account
  has setup done, so no other journey sees the wizard. Fixtures: `app/fixtures/api/setup-*.json`.
- Admin re-run: clear `setup_completed` (`api/admin.py`).
