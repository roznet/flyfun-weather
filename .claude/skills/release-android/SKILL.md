---
name: release-android
description: Release the Android app to Google Play — pre-flight checks, version bump, signed app bundle, tag and release notes, then upload with scripts/play.py to either the closed testing track only (testers) or closed then production (everyone). Invoke with an optional bump type (code / patch / minor / major) and target (testers / production).
disable-model-invocation: true
---

# Release Android to Google Play

Build a signed app bundle and put it on Google Play, the Android counterpart of
`/archive`. Ported from flyfun-forms' `/release-android`; keep the two in step.

## Arguments

In any order (e.g. `/release-android code testers`, `/release-android minor production`):

**Version bump** (default: ask the user). `versionCode` goes up on every one,
since Play refuses a code it has seen before, test tracks included:
- **code**: only `versionCode` + 1, `versionName` unchanged. For a fix to a build already on a testing track.
- **patch / minor / major**: bump `versionName` (1.0 → 1.1, 1.1 → 2.0, 1.2.3 → 1.2.4 / 1.3.0 / 2.0.0) and `versionCode` + 1.

**Target** (default: ask the user):
- **testers**: upload to the `closed` track only. Closed testers get it; everyone else stays on the current production build.
- **production**: upload to `closed`, then promote that same release to production, to everyone (`--full`). Only a staged rollout if the user asks for one (see Step 8).

If either is missing, ask in one question:
> Current version: X.Y (code N), production on A.B (code M). Bump type? [code / patch / minor / major] — Target? [testers / production]

Choosing **production** is the user's go-ahead for `--yes-production`; never
add it otherwise.

## How Play releases work (tell the user if they ask)

- A track (internal, closed, open, production) holds a release: a bundle plus
  its notes. Promoting copies a release from one track to another. Nobody is
  "moved": every device installs the **highest versionCode among the tracks it
  is eligible for** (production for everyone, closed too for opted-in testers).
  So testers on closed get a production release that outranks closed, and
  closed-only builds reach testers ahead of everyone else.
- `play.py` commits each edit **with "Send for review"** (unless `--no-review`).
  Play Console → Publishing overview then shows "Changes in review", each line
  reading e.g. "Production 1.1 (10) — Start full rollout": that is the change
  under review, not a button to press.
- After approval (hours, sometimes a day) it goes live by itself, unless
  **Managed publishing** is on in Publishing overview, in which case it waits
  under "Changes ready to publish" for a Publish click. Users then get it via
  Play auto-update, typically within a day.

## Step 1 — Read current version

`versionName` and `versionCode` from `app/android/app/build.gradle.kts`.
Show: "Current version: X.Y (code N) — releasing to {target}".

## Step 2 — Pre-flight checks

Stop with an error if any fail.

- **2a — Server URL.** In `app/android/app/src/main/kotlin/aero/flyfun/weather/data/ApiClient.kt`, `DEFAULT_BASE_URL` picks `localhost.ro-z.me` only under `BuildConfig.DEBUG && isRunningOnEmulator()` and is `https://weather.flyfun.aero` otherwise; no other non-test Kotlin source hard-codes `localhost` (the `LocalhostRozMeDns` helper in flyfun-common, used only for a localhost URL, is fine).
- **2b — Tests.** From `app/android`: `./gradlew test :app:testDebugUnitTest` (the four modules' JVM tests, what CI runs). There are no instrumented tests yet.
- **2c — Uncommitted changes.** Nothing uncommitted under `app/android`; ask to commit or stash otherwise. Unrelated dirty files elsewhere can stay: stage only this skill's files, by path.
- **2d — Branch.** On `main`; warn and ask if not.
- **2e — Upload key.** `app/android/keystore.properties` exists (a symlink to `~/.googleplaystore/flyfun-weather-keystore.properties`; forms' key is a different file). Without it the bundle is unsigned and Play rejects it. Never print its contents.
- **2f — Play state.** If the service account key exists (`~/.googleplaystore/private_keys/play-service-account.json`, or `PLAY_SERVICE_ACCOUNT_JSON`), run `venv/bin/python3 scripts/play.py status` and show it: the current release on each track. `pip install -e '.[release]'` once if it says google-auth is missing.

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
The SHA-256 must be the upload key's (`91:D5:8A:76:…:67:CC`, also shown in Play Console → App integrity). A different certificate means the wrong key (forms' key starts `64:6A`); stop. jarsigner's self-signed / no-timestamp / JarInputStream warnings are normal for an AAB.

Offer a quick check on the emulator first (`./gradlew :app:assembleRelease`, uninstall the debug build, install `app-release.apk`): sign-in and one briefing. A release build on the emulator talks to production (`weather.flyfun.aero`), not the devserver. R8 is off, so release and debug code are the same, but signing and the release manifest are not.

## Step 5 — Commit version bump

```
Bump Android version to X.Y (code N) for Play release
```

## Step 6 — Tag

`android/{versionName}`, never the code, as for iOS (`ios/1.9`).
- New `versionName` (patch/minor/major): `git tag android/{version}`
- Code-only bump: move it, `git tag -f android/{version}` (push with `--force`)

## Step 7 — Release notes

Previous tag: `git tag -l 'android/*' --sort=-version:refname`. If there is none
(the first release), cover the app as a whole. (`android/1.0` points at code 7,
not the code-9 build that shipped; for 1.1 the baseline was the code-9 bump
commit `e0241b7a`. From 1.1 on, tags match what shipped.)

From `git log {previous}..HEAD --oneline -- app/android src`, write
user-facing notes:
- Android changes, plus server changes users of the Android app will notice
- Plain language, no hashes or jargon; skip tests, CI, docs and refactors
- en-GB, `- ` bullets, **at most 500 characters** (Play's limit; `play.py` refuses more; check with `wc -m`)
- If the matching iOS notes describe the same features, reuse their wording

Show them and **wait for approval**. Save to `release-notes/android-{version}.txt`
exactly as approved (plain text). A translation, if the user wants one, goes
beside it as `release-notes/android-{version}.{lang}.txt` (e.g. `.fr-FR.txt`).

The notes go to the **Play Store only**: no web What's New entry
(`android_release`) for app releases, testing or production. Commit:
```
Release notes for android {version}
```

## Step 8 — Upload to Play

`scripts/play.py` (docstring has the details) uploads the bundle, sets the
track's release with the notes and commits the Play edit, sending it for
review. Offer `--dry-run` on the first command if the user wants it: it makes
the changes in an edit, has Google validate them and discards it.

**testers** and **production** both start with:
```bash
venv/bin/python3 scripts/play.py upload --track closed
```

**production** then promotes that release:
```bash
venv/bin/python3 scripts/play.py promote --from closed --to production --full --yes-production
```

While the user base is small, production goes to everyone (`--full`). If the
user asks for a staged rollout instead:
```bash
venv/bin/python3 scripts/play.py promote --from closed --to production --rollout 0.2 --yes-production
venv/bin/python3 scripts/play.py rollout --fraction 0.5 --yes-production   # later
venv/bin/python3 scripts/play.py rollout --complete --yes-production       # everyone
venv/bin/python3 scripts/play.py rollout --halt --yes-production           # stop
```

To promote an earlier **testers** release later (the build already on closed),
run only the promote command; no new build or version bump is needed.

If the service account key is missing, this step is unavailable: say so, point
at the credentials section of `scripts/play.py`, and fall back to Play Console
→ Testing → Closed testing → Create release (upload `app-release.aab`), then
Promote release → Production.

## Step 9 — Push

Push the commits and the tag once the user confirms:
```bash
git push && git push origin android/{version}
```
(`gh`/`git push` needs the sandbox disabled.) Another session may have pushed
`main` already; check `git log origin/main..HEAD` first.

## Step 10 — Report

- Pre-flight results
- Version and code, the bundle path, its signing certificate check
- The tag created or moved, and whether it is pushed
- The release notes and their file
- What `play.py` did (tracks, release name, status) and the final `play.py status`; or, if Step 8 was skipped, the manual Console route
- What happens next: "Changes in review" in Publishing overview, live by itself after approval (unless managed publishing is on); testers get closed builds within minutes to hours of approval
