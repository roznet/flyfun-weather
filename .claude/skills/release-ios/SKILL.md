---
name: release-ios
description: Build a signed Xcode archive of the iOS app ready for App Store upload — bumps the version, runs the iPhone + iPad test suites and release pre-flight checks, archives, tags, writes the App Store notes and stages the version on App Store Connect. Invoke with an optional bump type (build / patch / minor / major).
disable-model-invocation: true
---

# Release iOS to the App Store

Build an Xcode archive and stage it on App Store Connect (iOS only). The Android
counterpart is `/release-android`.

Background for every gate below — why both idioms run, the privacy-declaration rationale, the
tagging convention, release-note style, how reviewer sign-in works, and how App Store Connect
staging works — is in `designs/references/ios-release.md` (§A1–§A7). Read the section a step
points you at.

## Arguments

The user may specify a **version bump** (e.g. `/release-ios build`, `/release-ios patch`):

**Version bump** (default: ask the user):
- **build** — only increment `CURRENT_PROJECT_VERSION` (build number), keep `MARKETING_VERSION` unchanged. Use for TestFlight builds or minor fixes.
- **patch** — increment last component of marketing version (1.0 → 1.1) + bump build number
- **minor** — increment middle component (1.0 → 2.0 for two-part, 1.2.3 → 1.3.0) + bump build number
- **major** — increment first component (1.0 → 2.0, 1.2.3 → 2.0.0) + bump build number

If version bump is not specified, ask the user:
> Current version: X.Y (build N). Bump type? [build / patch / minor / major]

## Step 1 — Read current version

Read the current `MARKETING_VERSION` and `CURRENT_PROJECT_VERSION` from `app/flyfun-weather/flyfun-weather.xcodeproj/project.pbxproj`.

Note: there are 6 occurrences each (main target Debug+Release, test target Debug+Release, UI test target Debug+Release). Only update the **main target** entries (the first 2 occurrences of each).

Show the user: "Current version: X.Y (build N)"

## Step 2 — Pre-flight checks

Run these checks and **stop with an error** if any critical ones fail:

### 2-0 — Sync with origin, then pin the commit (do this first)

Every check below — the nightly gate, the test runs, the archive — is evidence only for the
commit it ran against. Two things have broken that link before:

- **A stale checkout.** The gate diffs the nightly's commit against *local* HEAD, so a `main`
  that is behind `origin/main` passes the gate and then archives old code.
- **The checkout moving mid-run.** Another session (e.g. a `/deploy`, or any agent doing
  `git pull` in `main/`) can fast-forward this checkout while the skill runs. On 2026-11-15
  a pull landed *during* `xcodebuild archive`: the binary mixed sources from two commits
  and the bump commit sat on top of code no test had seen.

So, first:

```bash
git fetch -q origin
git status -sb | head -1              # must not say [behind N]; [ahead N] = unpushed work, ask
git rev-parse HEAD origin/main        # should be equal
PINNED_SHA=$(git rev-parse HEAD); echo "$PINNED_SHA"
```

- **Behind `origin/main`:** stop, tell the user, and offer `git pull --ff-only` before any
  check runs. Never archive a commit older than what CI's nightly may have tested.
- **Ahead of `origin/main`:** unpushed commits will ship. Say so and ask: either push first
  (so the nightly can cover them), or accept that the full local passes in 2b must run.

Record `PINNED_SHA` and show it to the user. Steps 3, 6 and 7 check that HEAD is still
`PINNED_SHA` (+ the bump commit); if it moved, every check has to re-run on the new commit.
Ask the user not to deploy or pull in this checkout until the archive is built.

### 2a — API base URL check

Verify that the Release/production build will NOT use localhost. Check `app/flyfun-weather/flyfun-weather/App/AppState.swift`:
- The `#else` branch (non-DEBUG) must point to `https://weather.flyfun.aero` (production)
- The localhost URL (`localhost.ro-z.me:8000`) must only appear inside `#if DEBUG`
- If localhost is in the production path, **stop and warn the user**

### 2b — App tests (unit + UI), iPhone **and** iPad

The UI journeys on **both** an iPhone and an iPad must be green for the code being archived,
and a failure on either idiom is a release blocker. They can be proven green two ways —
ask the nightly first, because the full local route costs ~10 min (iPhone ~4.5 + iPad ~5, 2026-10-17).

**First, ask the nightly** (needs `gh`, so run it unsandboxed):
```bash
python3 scripts/ios_ci.py gate
```
It finds the newest nightly on main whose `UI (iPhone)` **and** `UI (iPad)` jobs both ran
and passed, and checks nothing under `app/flyfun-weather` changed since that commit except
version-bump lines. It compares against **local HEAD**, which is why 2-0 syncs first.
It also lists any **newer nightlies that failed**. When the app code is unchanged since
the green run, that means a flaky or CI-only journey, not a reason to block. Name it in the
report, though: when a newer failure is in a suite the release touches, run that suite
locally on both idioms before trusting `COVERED`.

- **`COVERED` (exit 0)** — skip the two full passes. Run only the unit target plus an iPad
  smoke set, one `ios_test.py` call (~5 min): it proves this machine's toolchain — the one
  that builds the archive — builds and launches what CI tested, and the iPad's split view
  and paginated tab bar are where local and CI diverged before.
  ```bash
  T=flyfun-weatherUITests
  python3 scripts/ios_test.py --device "iPad Pro 11-inch (M5)" --only flyfun-weatherTests \
    --only $T/FlightsUITests/testFlightListRendersSeededFlights \
    --only $T/BriefingUITests/testBriefingAdvisoryDrillDown \
    --only $T/BriefingUITests/testRouteSigmetsSectionRenders
  ```
  Report it as "UI covered by nightly <sha> (<run url>); local unit + iPad smoke green".
- **`NOT COVERED` (exit 1)** — it says why (app code changed since, or no two-leg green run
  yet). If the change is pushed, offer the user the choice: dispatch the nightly on main
  (`gh workflow run ios-ui-nightly.yml --ref main`, ~30–45 min on CI, nothing running
  locally; re-run `gate` when it finishes) **or** run the full local passes below.

**Full local passes** — when not covered, or the user asks. Both testable targets
(`flyfun-weatherTests`, `flyfun-weatherUITests`) run together; never skip the UI target here.

**Why both idioms, and why concrete device names: §A1.** In short — a real
`NavigationSplitView` fork means an iPhone pass does not imply an iPad pass, and
`generic/platform=iOS Simulator` builds the tests without running them.

The defaults are the owner's own devices. A name resolves to its newest-iOS simulator (CI's
`pick-device` rule); pass `--device` for others (`xcrun simctl list devices available`).

Use `scripts/ios_test.py`, never a bare `xcodebuild test -quiet | tail`. The script turns off
xcodebuild's on-failure simulator diagnostics (which stalled every red run ~10 min *after*
the last test), caps each test, prints each test as it finishes, and kills xcodebuild with
exit 124 (`STALLED`) if output and the result bundle both go quiet for 5 min. Its closing
summary lists every failure's assertion message.

Run both passes in one call with `run_in_background: true` — a build plus the UI journeys
exceeds the 10-min foreground limit — and wait for the completion notification. The
script runs the devices one after another (each fans its test classes out over simulator
clones; several destinations in one xcodebuild would turn that off) and prints a summary
per device. It exits 3 if another `xcodebuild test` is running (`--wait` to queue).

```bash
python3 scripts/ios_test.py   # defaults: iPhone 18 Pro + iPad Pro 11-inch (M5), each on its newest iOS
```

If tests fail on either idiom, stop and show the failures, noting which device the failure
was on. To iterate on a failure, re-run just those tests with `--only <target/class/test>`
(repeatable). On `STALLED` (124) or a simulator-boot flake, retry once before calling it a
real failure. If every test errors with "cannot find X in scope" for symbols that plainly
exist, the test target linked a stale app module: delete
`~/Library/Developer/Xcode/DerivedData/flyfun-weather-*/Build` for this checkout and re-run.

### 2c — Backend tests

Run the backend test suite:
```bash
source venv/bin/activate && python3 -m pytest tests/ -x -q
```
If tests fail, stop and show the failures. Use timeout of 600000ms.

### 2d — Uncommitted changes

Run `git status` — warn the user if there are uncommitted changes beyond the version bump that's about to happen.

### 2e — Git branch check

Verify we're on `main` branch. Warn (but don't block) if on a different branch.

### 2f — Debug-only code check

Search for common debug patterns that shouldn't ship:
- `#if DEBUG` blocks that contain API URLs or feature flags — verify they have proper `#else` branches
- Any `print(` or `NSLog(` in SwiftUI views (these are noisy in production) — warn but don't block
- Any `TODO` or `FIXME` comments — warn but don't block

### 2g — Info.plist privacy descriptions

Required in `app/flyfun-weather/flyfun-weather/SupportingFiles/Info.plist`:
`NSLocationWhenInUseUsageDescription`, and `ITSAppUsesNonExemptEncryption` set to `false`.

Required in `app/flyfun-weather/flyfun-weather/PrivacyInfo.xcprivacy` — at minimum the types
the web privacy page (`web/privacy.html`) claims are collected:
`EmailAddress`, `Name`, `UserID`, `PreciseLocation` (**must be `Linked=true`**),
`OtherUserContent`, `ProductInteraction` (each prefixed `NSPrivacyCollectedDataType`).

If any is missing, stop and warn. **These two files must stay in sync — divergence is a common
App Review nit.** What each declaration covers and why: §A2.

### 2h — Local package overrides

Check `app/flyfun-weather/flyfun-weather.xcodeproj/project.pbxproj` for absolute local package paths — anything under `/Users/` pointing at a sibling checkout (e.g. a local `rzskewt`). These break builds on other machines and must be reverted to remote SPM references before archiving. **Stop and warn the user** if found:

```bash
grep -n '/Users/' app/flyfun-weather/flyfun-weather.xcodeproj/project.pbxproj || echo "no local package overrides"
```

Report all checks as a checklist to the user before proceeding.

## Step 3 — Bump version

Always increment `CURRENT_PROJECT_VERSION` by 1.

For `MARKETING_VERSION`, apply the bump type:
- **build**: no change to marketing version
- **patch**: increment the last component (1.0 → 1.1, 1.2.3 → 1.2.4)
- **minor**: increment middle component, reset last (1.0 → 2.0 for two-part, 1.2.3 → 1.3.0)
- **major**: increment first component, reset rest (1.0 → 2.0, 1.2.3 → 2.0.0)

Update only the **main target** entries (first 2 of each) in `project.pbxproj` using the Edit tool with `replace_all`. Be careful to distinguish main target vs test target entries by checking surrounding context.

Show the user: "Bumped to X.Y (build N)"

Then **commit it immediately, before building**, after checking HEAD is still the pinned
commit. The build stamps the git commit into the app (`BuildStamp.plist`, via
`app/flyfun-weather/scripts/stamp-build-info.sh`). An uncommitted bump stamps
`<sha>-dirty`, which names no real commit and can't be checked in Step 6.

```bash
[ "$(git rev-parse HEAD)" = "$PINNED_SHA" ] || echo "HEAD MOVED since pre-flight — stop"
git add app/flyfun-weather/flyfun-weather.xcodeproj/project.pbxproj
git commit -m "Bump version to X.Y (build N) for App Store release"
```

If HEAD moved, stop: tell the user what came in (`git log --oneline $PINNED_SHA..HEAD`)
and re-run the pre-flight on the new commit. Do NOT push unless the user asks.

## Step 4 — Clean build folder

```bash
xcodebuild clean \
  -project app/flyfun-weather/flyfun-weather.xcodeproj \
  -scheme flyfun-weather \
  -configuration Release
```

## Step 5 — Build archive

```bash
xcodebuild archive \
  -project app/flyfun-weather/flyfun-weather.xcodeproj \
  -scheme flyfun-weather \
  -configuration Release \
  -destination "generic/platform=iOS" \
  -archivePath ~/Library/Developer/Xcode/Archives/$(date +%Y-%m-%d)/flyfun-weather\ $(date +%d-%m-%Y,\ %H.%M).xcarchive \
  -allowProvisioningUpdates \
  CODE_SIGN_STYLE=Automatic \
  | tail -20
```

`-allowProvisioningUpdates` lets Xcode fetch or create the profiles itself. That matters on a
new Mac, where the first archive otherwise fails for lack of a profile. The upload in Step 10
re-signs for distribution with the ASC API key (cloud-managed signing), so a local Apple
Distribution certificate isn't needed. If the export fails on signing anyway, a single
Organizer → Distribute App run on the archive sets up what's missing.

The archive path under `~/Library/Developer/Xcode/Archives/` makes it appear in Xcode Organizer automatically.

This may take a few minutes. Run with a generous timeout (600000ms).

## Step 6 — Verify archive

Check that the archive was created and verify the embedded version:
```bash
/usr/libexec/PlistBuddy -c "Print :ApplicationProperties:CFBundleShortVersionString" "$ARCHIVE_PATH/Info.plist"
/usr/libexec/PlistBuddy -c "Print :ApplicationProperties:CFBundleVersion" "$ARCHIVE_PATH/Info.plist"
```

Then check the binary was built from the bump commit, and only from it:

```bash
plutil -extract GitCommit raw "$ARCHIVE_PATH/Products/Applications/flyfun-weather.app/BuildStamp.plist"
git rev-parse --short HEAD
[ "$(git rev-parse HEAD~1)" = "$PINNED_SHA" ] && echo "parent is pinned commit"
```

The stamp must equal `git rev-parse --short HEAD` exactly, with **no `-dirty`**, and HEAD's
parent must be `PINNED_SHA`. A mismatch means the checkout changed during or after the
build. Delete the archive, re-run the pre-flight on the new commit, and rebuild.

One exception: `-dirty` with HEAD unmoved and `git status --porcelain -- app/flyfun-weather`
clean. The bump commit and the `project.pbxproj` write landed in the same second, git's index
marks that entry "racily clean", and the sandboxed stamp script cannot re-read the file to
confirm (seen 2026-11-15, build 28). Run `git status` once to refresh the index, delete the
archive and rebuild; the stamp comes out clean.

## Step 7 — Confirm the pin held

The bump was committed in Step 3. Before tagging, run `git log --oneline -3` and check
nothing landed on top of the bump commit. If anything did, the tag would point at
untested code: stop and ask.

## Step 8 — Tag and release notes

### Tagging convention

Tags track the **marketing version** only: `ios/{marketing_version}` (e.g. `ios/1.0`). A
build-only bump *moves* the existing tag rather than creating one; only patch/minor/major
bumps create a new tag. Full convention and why the force-push is intentional: §A3.

**When the tag already exists** (build-only bump):
```bash
git tag -f ios/{version}
git push origin ios/{version} --force
```

**When the tag is new** (patch/minor/major bump):
```bash
git tag ios/{version}
git push origin ios/{version}
```

### Generate release notes

Find the **previous version** tag — for a build-only bump that is the tag *before* the
current marketing version, since the current one's notes cover the whole version:
```bash
git tag -l "ios/*" --sort=-version:refname
```

Generate a user-facing "What's New" summary from commits between the previous version and HEAD:
```bash
git log {previous_tag}..HEAD --oneline
```

Write concise, user-facing release notes — grouped bullets, plain language, no internal
changes, 5–8 bullets max. Full style guide: §A4.

Show the notes and **wait for approval** before pushing tags. Save them to
`release-notes/ios-{version}.txt` exactly as approved (plain text, `- ` bullets). Step 10
passes that file to `asc.py`, so what is committed is what the store shows.

The notes go to the **App Store only**: no `app_release` entry in the site's What's New
stream (owner's call, 2026-11-15, same as `/release-android`; see §A6). Commit:

```
Release notes for iOS {version}
```

## Step 9 — App Store reviewer sign-in token (ask first)

This runs **before** staging, because the token's deep link is pushed to App Store Connect as
part of Step 10 rather than pasted by hand.

Do **not** mint this by default. Ask the user:

> Are you submitting this build to the App Store for review? If so, the App
> Review notes need a working sign-in deep link, and the previous reviewer
> token expires 7–60 days after it was minted — so it may be stale. Mint a
> fresh one?

Only if they say yes, mint it:

```bash
source venv/bin/activate && python3 scripts/mint_reviewer_token.py --days 60
```

Keep the printed `flyfunweather://auth?token=…` line — Step 10 passes it to `--review-notes`.
If the user declines, skip `--review-notes` and leave whatever is already on the version.

How the token works, why a dedicated script exists, and what it reads from `.env`: **§A5** —
read it before explaining any of this to the user. If the script errors, it names the missing
`.env` variable; those are absent from the repo by design.

## Step 10 — Stage on App Store Connect

`scripts/asc.py` does the version creation, "What's New", upload, and build attach over the
App Store Connect API — the steps that used to be manual Organizer + copy-paste work. **It
cannot submit for review, by design** (there is no such subcommand); the pilot presses Submit.
Rationale and the credential setup: **§A7**.

If `ASC_KEY_ID` / `ASC_ISSUER_ID` are not in `.env`, this step is unavailable — say so, point
at §A7, and fall back to the manual route in Step 11.

First show the user what App Store Connect currently thinks:

```bash
source venv/bin/activate && python3 scripts/asc.py status
```

**Store screenshots are opt-in — ask, don't default.** Most releases don't need new ones,
and recapture (~10 min) plus upload (~10 min) roughly doubles staging. Ask the user (ask it
alongside the Step 9 token question, so it costs no extra round-trip):

> Update the App Store screenshots for this release? (recapture ~10 min + upload ~10 min)

Only on yes, recapture — it exits at once when no screen changed since the last capture;
otherwise run it with `run_in_background: true`. Why the sizes and how "changed" is
decided: **§A8**.

```bash
python3 scripts/ios_screenshots.py --if-changed --wait
```

If it captured, show the user a few of the new PNGs before staging. Then stage everything in
one call. `--notes-file` reads the notes saved in Step 8. Pass `--screenshots` **only if the user said yes**
— it uploads the sets that differ from the store, so a stale local capture left over from
an earlier release would otherwise be pushed anyway:

```bash
source venv/bin/activate && python3 scripts/asc.py stage \
  --version {marketing_version} \
  --build {build_number} \
  --archive "{archive_path}" \
  --notes-file release-notes/ios-{version}.txt \
  --screenshots build/appstore-screenshots \
  --review-notes "flyfunweather://auth?token=…"
```

(Drop `--screenshots` when the user declined screenshots, and `--review-notes` when they
declined the token.)

Notes on behaviour — all four are normal, not errors:

- **Re-running is safe.** `stage` reuses an existing editable version, renames it if the
  marketing version changed, and overwrites What's New in place. Re-run it to correct a
  mistake or to push a second binary rather than trying to undo anything.
- **It releases on approval.** The version is set to "Automatically release this version"
  (`AFTER_APPROVAL`, enforced on reuse too), so Submit for Review is the only gate. Pass
  `--release-type MANUAL` only if the user asks to hold a release. Rationale: §A7.
- **It waits for Apple.** After upload the build sits in processing for ~5–30 minutes before
  it can be attached. Use a timeout of 600000ms (10 min) and, if it's still going, re-run
  `python3 scripts/asc.py wait-build --version X.Y --build N` — **do not re-upload**.
- **It stops at in-review versions.** If a version is already `WAITING_FOR_REVIEW` or
  `IN_REVIEW`, it refuses rather than editing. Cancelling a submission is the user's call.

Add `--dry-run` first if the user wants to see the calls before anything is sent.

## Step 11 — Report

Tell the user:
- Pre-flight check results summary
- Archive created at the path
- Version and build number in the archive
- The tag that was created
- The release notes for the App Store
- **If Step 10 ran:** the version is staged on App Store Connect with notes and build
  attached — they review it in the web UI and press **Submit for Review** themselves. Show
  the final `asc.py status` output.
- **If Step 10 was skipped** (no API key configured): the archive appears in **Xcode → Window
  → Organizer**, and from there **Distribute App** → **App Store Connect** uploads it; the
  What's New text and reviewer link then have to be pasted in by hand.
- Remind them to push the version bump commit when ready
