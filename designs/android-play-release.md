# Android App: Google Play Release

> How the Android app reaches Google Play: `scripts/play.py` + `/release-android`,
> the upload key, and what only the owner can do in Play Console. Ported from
> flyfun-forms (its `designs/future/android-play-release.md`, `scripts/play.py`,
> `/release-android`), which shipped 1.0 through the same tooling on 2026-10-17.

## Tooling

- **`scripts/play.py`** drives the Google Play Developer API (v3) the way
  `scripts/asc.py` drives App Store Connect: `status`, `upload` (bundle + release
  notes onto a track), `promote`, `rollout`, `listing` (text and images from
  `app/android/play/listing/`), `testers`. Every write is a Play *edit*;
  `--dry-run` has Google validate the edit, then discards it. Production refuses
  to run without `--yes-production` (the counterpart of `asc.py` having no
  `submit`), and a new production release must say `--rollout <fraction>` or
  `--full`. The file is the forms script with a different default package —
  fix a bug in one, port it to the other.
- **`/release-android`** (`.claude/skills/release-android/`) is the `/archive`
  counterpart: pre-flight, bump, `bundleRelease`, certificate check, tag
  `android/X.Y`, release notes `release-notes/android-X.Y.txt` (≤500 chars, plain
  text — Play's limit), upload to `internal` by default.
- **The first bundle goes through the API too.** Only creating the app needs the
  Console. Until the app's first production release, releases must be
  `--status draft` (Google's rule for an unpublished app).

## Credentials (outside the repo — public repo)

| What | Where |
|---|---|
| Service account (one for every FlyFun app) | `~/.googleplaystore/private_keys/play-service-account.json`, or `PLAY_SERVICE_ACCOUNT_JSON`. Needs this app granted in Play Console → Users and permissions |
| Upload key | `~/.googleplaystore/private_keys/flyfun-weather-upload.jks`, alias `upload`, certificate SHA-256 `91:D5:8A:76:69:4C:99:5F:6A:48:A5:43:DD:9F:EE:3E:20:24:03:C2:B4:C2:85:41:B9:6F:10:FC:3B:09:67:CC` |
| Its passwords | `~/.googleplaystore/flyfun-weather-keystore.properties`, symlinked as `app/android/keystore.properties` (gitignored) |
| Python deps | `pip install -e '.[release]'` (google-auth) |

Forms has its own upload key (`flyfun-forms-upload.jks`, `keystore.properties`);
the two apps never share one. **Back the upload key and its properties file
up** — a lost upload key can be reset through Play support, but that takes days.

`app/android/app/build.gradle.kts` signs `bundleRelease` with the key when
`keystore.properties` is present; without it the bundle is unsigned and Play
rejects it. Play App Signing (mandatory) holds the real app signing key; the
upload key only proves uploads come from us.

## Versions

Android versions independently of iOS, as forms does: Play's first release is
**1.0 (code 1)**. `versionCode` goes up on every upload, test tracks included,
and can never go down. Tags are `android/<versionName>`.

## App Links

`deploy/weather.flyfun.aero.caddy` has `/.well-known/assetlinks.json` commented
out. Once the app exists in Play Console, take the **app signing** key's SHA-256
from Setup → App integrity (Google's key, not the upload key above), uncomment
the block with it, and deploy **before** shipping a production build — otherwise
`/briefing.html`, `/s/…`, `/t/…` and `/auth/callback` links open in the browser.
OAuth sign-in itself uses the `flyfunweather://` scheme and does not depend on it.

## Owner checklist (Play Console)

1. Create the app (done 2026-10-19): name "FlyFun Weather", free. The package
   name comes from the first upload (1.0 (1), internal, draft) and is now fixed
   as `aero.flyfun.weather`. The default language is en-US, so `default/` and
   the release notes land there.
2. Users and permissions → invite the service account with release and store
   listing access to this app. `play.py status` then answers instead of
   "Package not found".
3. **Closed test, 12 testers × 14 days** (personal account rule, per app). Reuse
   forms' tester Google Group: `play.py testers --track closed --group …`.
4. App content forms — drafted below.
5. Store listing: `app/android/play/listing/default/` (title, short and full
   description, `icon.png` 512² from the iOS `AppLogo.png`, `feature-graphic.png`
   1024×500, `phone/*.png`), pushed with `play.py listing --images`. Screenshots
   are the release build on the `flyfun_pixel10_api37` emulator in Demo Mode
   (fixture data only). The demo fixture's cross-section and Observed tab are
   too sparse to show, so those two are missing; no tablet set yet.

## App content (drafted answers)

- **Privacy policy:** `https://weather.flyfun.aero/privacy.html`.
- **Account deletion:** in-app at Settings → Delete Account (`DELETE /auth/account`,
  also clears the local briefing cache and PIREP queue); web URL
  `https://weather.flyfun.aero/privacy.html#account-deletion` (the web app has
  the same Settings action).
- **App access:** briefings need sign-in. Give reviewers a `scope: review` token
  deep link (`scripts/mint_reviewer_token.py`, as for App Review), or point them
  at "Explore Demo Briefings" on the sign-in screen, which works without an account.
- **Foreground service (location):** `FlightTrackingService` is
  `foregroundServiceType="location"`, started only by the pilot from the Observed
  tab to place the aircraft on the route in flight. The declaration needs a short
  video of starting tracking. No `ACCESS_BACKGROUND_LOCATION`, so no background
  location declaration.
- **Ads:** none. **Target audience:** 18+. **Content rating (IARC):** utility,
  no user interaction features beyond pilot reports → expect Everyone / PEGI 3.
- **Data safety:**
  - *Collected:* email address and name (account management, app functionality);
    precise location, only while the pilot runs flight tracking, sent to the
    server and kept with the flight (app functionality); other user content —
    flights, aircraft, debriefs, PIREPs, feedback (app functionality).
    Push device token once FCM is wired (device or other IDs).
  - *Shared:* none (LLM and weather providers act as service providers).
  - *Encrypted in transit:* yes. *Deletion:* yes, in-app plus the web URL.

## Not yet done

- FCM client (`firebase-messaging`, `google-services.json`): push is inactive on
  Android; adding it changes the Data safety answers.
- R8 (`isMinifyEnabled`) off: enable only with a full drive of the release build.
- Cross-section, Observed and tablet screenshots (needs a richer demo fixture).
