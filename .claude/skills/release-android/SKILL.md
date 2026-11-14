---
name: release-android
description: Release the Android app to Google Play — pre-flight checks, version bump, signed app bundle, tag and release notes, then upload to a Play track (internal by default) with scripts/play.py. Never publishes to production unless asked. Invoke with an optional bump type (code / patch / minor / major) and track (internal / closed / open).
disable-model-invocation: true
---

# Release Android to Google Play

Build a signed app bundle and put it on a Google Play testing track, the Android
counterpart of `/archive`. Production is a separate, explicit step. Ported from
flyfun-forms' `/release-android`; keep the two in step.

## Arguments

In any order (e.g. `/release-android code`, `/release-android minor closed`):

**Version bump** (default: ask the user). `versionCode` goes up on every one,
since Play refuses a code it has seen before, test tracks included:
- **code**: only `versionCode` + 1, `versionName` unchanged. For a fix to a build already on a testing track.
- **patch / minor / major**: bump `versionName` (1.0 → 1.1, 1.1 → 2.0, 1.2.3 → 1.2.4 / 1.3.0 / 2.0.0) and `versionCode` + 1.

If not given, ask:
> Current version: X.Y (code N). Bump type? [code / patch / minor / major]

**Track** (default `internal`): `internal`, `closed` or `open`. Production is
never a target of this skill's upload; see Step 8.

## Step 1 — Read current version

`versionName` and `versionCode` from `app/android/app/build.gradle.kts`.
Show: "Current version: X.Y (code N) — releasing to {track}".

## Step 2 — Pre-flight checks

Stop with an error if any fail.

- **2a — Server URL.** In `app/android/app/src/main/kotlin/aero/flyfun/weather/data/ApiClient.kt`, `DEFAULT_BASE_URL` picks `localhost.ro-z.me` only under `BuildConfig.DEBUG && isRunningOnEmulator()` and is `https://weather.flyfun.aero` otherwise; no other non-test Kotlin source hard-codes `localhost`.
- **2b — Tests.** From `app/android`: `./gradlew test :app:testDebugUnitTest` (the four modules' JVM tests, what CI runs). There are no instrumented tests yet.
- **2c — Uncommitted changes.** `git status --porcelain` is empty; ask to commit or stash otherwise.
- **2d — Branch.** On `main`; warn and ask if not.
- **2e — Upload key.** `app/android/keystore.properties` exists (a symlink to `~/.googleplaystore/flyfun-weather-keystore.properties`; forms' key is a different file). Without it the bundle is unsigned and Play rejects it. Never print its contents.
- **2f — Play state.** If the service account key exists (`~/.googleplaystore/private_keys/play-service-account.json`, or `PLAY_SERVICE_ACCOUNT_JSON`), run `venv/bin/python3 scripts/play.py status` and show it: the current release on each track, and whether production has ever had a release (if not, uploads must be `--status draft`, Google's rule for an unpublished app). `pip install -e '.[release]'` once if it says google-auth is missing.

## Step 3 — Bump version

Edit `versionCode` / `versionName` in `app/android/app/build.gradle.kts`.

## Step 4 — Build the signed bundle

From `app/android`:
```bash
./gradlew clean :app:bundleRelease
```
Then verify `app/build/outputs/bundle/release/app-release.aab`:
```bash
jarsigner -verify app/build/outputs/bundle/release/app-release.aab   # "jar verified."
keytool -printcert -jarfile app/build/outputs/bundle/release/app-release.aab | grep SHA256
```
The SHA-256 must be the upload key's (`91:D5:8A:76:…:67:CC`, also shown in Play Console → App integrity). A different certificate means the wrong key (forms' key starts `64:6A`); stop.

Offer a quick check on the emulator first (`./gradlew :app:assembleRelease`, uninstall the debug build, install `app-release.apk`): sign-in and one briefing. A release build on the emulator talks to production (`weather.flyfun.aero`), not the devserver. R8 is off, so release and debug code are the same, but signing and the release manifest are not.

## Step 5 — Commit version bump

```
Bump Android version to X.Y (code N) for Play release
```
Do not push unless the user asks.

## Step 6 — Tag

`android/{versionName}`, never the code, as for iOS (`ios/1.9`).
- New `versionName` (patch/minor/major): `git tag android/{version}`
- Code-only bump: move it, `git tag -f android/{version}` (push with `--force`)

## Step 7 — Release notes

Previous tag: `git tag -l 'android/*' --sort=-version:refname`. If there is none
(the first release), cover the app as a whole.

From `git log {previous}..android/{version} --oneline -- app/android src`, write
user-facing notes:
- Android changes, plus server changes users of the Android app will notice
- Plain language, no hashes or jargon; skip tests, CI, docs and refactors
- en-GB, `- ` bullets, **at most 500 characters** (Play's limit; `play.py` refuses more)
- If the matching iOS notes (`release-notes/ios-*.json`, the `body` field) describe the same features, reuse their wording

Show them and **wait for approval**. Save to `release-notes/android-{version}.txt`
exactly as approved (plain text, unlike the iOS JSON). A translation, if the
user wants one, goes beside it as `release-notes/android-{version}.{lang}.txt`
(e.g. `.fr-FR.txt`).

For a version going to **production**, also draft the What's New entry now
(published later, in Step 8b): `release-notes/android-{version}.json`, a JSON
list of one entry in the shape `python -m weatherbrief.release import` accepts:

```json
[
  {
    "date": "YYYY-MM-DD",
    "title": "Android {version}: <short headline>",
    "category": "android_release",
    "highlight": false,
    "body": "<the approved .txt, verbatim>"
  }
]
```

Body is the Play notes verbatim, so the store listing and the stream can't
drift. Highlight (it lights the unseen dot for everyone) only a release with
real user-facing news; ask the user. Testing-track builds (internal, closed)
get no entry: nobody outside the testers can install them. Commit:
```
Release notes for android {version}
```
Push the tag once the user confirms.

## Step 8 — Upload to Play

`scripts/play.py` (docstring has the details) uploads the bundle, sets the
track's release with the notes and commits the Play edit. Offer `--dry-run` first:
it makes the changes in an edit, has Google validate them and discards it.

```bash
venv/bin/python3 scripts/play.py upload --track {track}
```
Add `--status draft` only for an app that has never had a production release
(not needed for flyfun-weather any more: 1.0 reached production on 2026-11-12). The
first bundle of a new app goes through the API too (forms 1.0 did); the app
itself must exist in Play Console first ("Package not found" otherwise).

If the service account key is missing, this step is unavailable: say so, point
at the credentials section of `scripts/play.py`, and fall back to uploading
`app-release.aab` in Play Console → Testing → {track} → Create release.

**Production only when the user asks**, and as a staged rollout:
```bash
venv/bin/python3 scripts/play.py promote --from closed --to production --rollout 0.2 --yes-production
venv/bin/python3 scripts/play.py rollout --fraction 0.5 --yes-production   # later
venv/bin/python3 scripts/play.py rollout --complete --yes-production       # everyone
venv/bin/python3 scripts/play.py rollout --halt --yes-production           # stop
```
`--yes-production` is the user's go-ahead, the counterpart of pressing Submit
for the App Store; never add it on your own initiative.

## Step 8b — Publish the What's New entry (production only)

Only once the production release is live on Google Play (rollout started, so
users can install it), never at upload: an entry announcing a version nobody can
download is wrong. Same split as iOS (`designs/references/ios-release.md` §A6).

1. Set the entry's `date` in `release-notes/android-{version}.json` to **today**,
   so it interleaves where users could first install it.
2. Check prod knows the category: `android_release` arrived with #985. If prod
   predates it, `import` rejects the entry; deploy first.
3. Publish (`-` reads stdin, because `release-notes/` is not in the container
   image). `<SERVER_SSH>` comes from `python3 scripts/ops/hosts.py server`:

```bash
ssh <SERVER_SSH> "docker exec -i weatherbrief python -m weatherbrief.release import -" \
  < release-notes/android-{version}.json
ssh <SERVER_SSH> "docker exec weatherbrief python -m weatherbrief.release list" | head
```

The web's What's New renders a "Get it on Google Play" button under an
`android_release` entry; the apps show the entry without it.

## Step 9 — Report

- Pre-flight results
- Version and code, the bundle path, its signing certificate check
- The tag created or moved
- The release notes and their file (and the drafted What's New entry, published or not)
- What `play.py` did (track, release name, status) and the final `play.py status`; or, if Step 8 was skipped, the manual Console route
- Testers on internal/closed get the update from the Play Store within minutes to hours
- Remind the user to push the version bump and release-notes commits
