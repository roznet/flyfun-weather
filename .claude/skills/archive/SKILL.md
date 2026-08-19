---
name: archive
description: Build a signed Xcode archive of the iOS app ready for App Store upload — bumps the version, runs the iPhone + iPad test suites and release pre-flight checks, archives, tags, and drafts release notes. Invoke with an optional bump type (build / patch / minor / major).
disable-model-invocation: true
---

# Archive for App Store

Build an Xcode archive ready for App Store upload (iOS only).

Background for every gate below — why both idioms run, the privacy-declaration rationale, the
tagging convention, release-note style, how reviewer sign-in works, and how App Store Connect
staging works — is in `designs/references/ios-release.md` (§A1–§A7). Read the section a step
points you at.

## Arguments

The user may specify a **version bump** (e.g. `/archive build`, `/archive patch`):

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

### 2a — API base URL check

Verify that the Release/production build will NOT use localhost. Check `app/flyfun-weather/flyfun-weather/App/AppState.swift`:
- The `#else` branch (non-DEBUG) must point to `https://weather.flyfun.aero` (production)
- The localhost URL (`localhost.ro-z.me:8000`) must only appear inside `#if DEBUG`
- If localhost is in the production path, **stop and warn the user**

### 2b — App tests (unit + UI), iPhone **and** iPad

Run the Xcode test suite on **both** an iPhone and an iPad simulator. Both testable targets
(`flyfun-weatherTests`, `flyfun-weatherUITests`) run together — never skip the UI target to
save time, and a failure on either idiom is a release blocker.

**Why both idioms, and why concrete device names: §A1.** In short — a real
`NavigationSplitView` fork means an iPhone pass does not imply an iPad pass, and
`generic/platform=iOS Simulator` builds the tests without running them.

Pick models the machine actually has (`xcrun simctl list devices available`) and substitute
the closest current iPhone / iPad if these names aren't present.

**iPhone pass:**
```bash
xcodebuild test \
  -project app/flyfun-weather/flyfun-weather.xcodeproj \
  -scheme flyfun-weather \
  -destination "platform=iOS Simulator,name=iPhone 17 Pro" \
  -quiet \
  2>&1 | tail -30
```

**iPad pass:**
```bash
xcodebuild test \
  -project app/flyfun-weather/flyfun-weather.xcodeproj \
  -scheme flyfun-weather \
  -destination "platform=iOS Simulator,name=iPad Pro 11-inch (M5)" \
  -quiet \
  2>&1 | tail -30
```

If tests fail on either idiom, stop and show the failures, noting which device the failure
was on. Each pass boots a simulator and drives the app, so use a timeout of 600000ms (10 min)
**per pass**. On a simulator-boot flake, retry once before calling it a real failure.

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
  CODE_SIGN_STYLE=Automatic \
  | tail -20
```

The archive path under `~/Library/Developer/Xcode/Archives/` makes it appear in Xcode Organizer automatically.

This may take a few minutes. Run with a generous timeout (600000ms).

## Step 6 — Verify archive

Check that the archive was created and verify the embedded version:
```bash
/usr/libexec/PlistBuddy -c "Print :ApplicationProperties:CFBundleShortVersionString" "$ARCHIVE_PATH/Info.plist"
/usr/libexec/PlistBuddy -c "Print :ApplicationProperties:CFBundleVersion" "$ARCHIVE_PATH/Info.plist"
```

## Step 7 — Commit version bump

Stage and commit the version bump to `project.pbxproj`:
```
Bump version to X.Y (build N) for App Store release
```

Do NOT push unless the user asks.

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

Find the **previous version** tag:
```bash
git tag -l "ios/*" --sort=-version:refname
```

Generate a user-facing "What's New" summary from commits between the previous version and HEAD:
```bash
git log {previous_tag}..HEAD --oneline
```

Write concise, user-facing release notes — grouped bullets, plain language, no internal
changes, 5–8 bullets max. Full style guide: §A4.

Show the release notes to the user for review before pushing tags.

### Draft the release-stream entry (only with explicit confirmation)

The same notes also belong in the site's What's New stream, as an `app_release` entry. Draft
the file now — the commit range is freshest at archive time — but **do not publish it here**:
Apple review sits between this archive and users being able to install, so publishing happens
at approval (Step 12). Why the split, and how to pick the title / highlight flag: **§A6**.

Draft `release-notes/ios-{version}.json` in the shape `python -m weatherbrief.release import`
accepts — a JSON **list** of one entry:

```json
[
  {
    "date": "YYYY-MM-DD",
    "title": "iOS {version} — <short headline>",
    "category": "app_release",
    "highlight": false,
    "body": "- First App Store bullet\n- Second App Store bullet"
  }
]
```

- `date` — leave as the archive date for now; **reset it to the approval date in Step 12**, so
  the entry interleaves chronologically at the point users could actually install it.
- `title` — `iOS {version} — <short headline>` (e.g. `iOS 1.5 — Route SIGMETs and observations`).
- `body` — the App Store bullets **verbatim**, so the two surfaces can't drift.
- `highlight` — a per-release judgement, not automatic. Propose `true` only for a release
  carrying real user-facing news; leave a bug-fix-only build unhighlighted.

**Confirm (HARD STOP):** show the proposed title, highlight yes/no, and full body — then **end
the turn.** Only a readable "yes" counts. Apply any edits the user makes and re-show if they're
substantial. **Write nothing unless the user says yes.**

Validate the written file against a dev database before moving on (never against prod):

```bash
source venv/bin/activate && python3 -m weatherbrief.release import \
  release-notes/ios-{version}.json --dry-run
```

Commit the file on its own (the version bump already landed in Step 7):

```
Draft the iOS {version} release-stream entry

Published at App Store approval, not here — Apple review sits between this
archive and users being able to install.
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

Then stage everything in one call. `--notes-file` reads the entry drafted in Step 8, so the
App Store text and the site's release-stream entry cannot drift:

```bash
source venv/bin/activate && python3 scripts/asc.py stage \
  --version {marketing_version} \
  --build {build_number} \
  --archive "{archive_path}" \
  --notes-file release-notes/ios-{version}.json \
  --review-notes "flyfunweather://auth?token=…"
```

Notes on behaviour — all three are normal, not errors:

- **Re-running is safe.** `stage` reuses an existing editable version, renames it if the
  marketing version changed, and overwrites What's New in place. Re-run it to correct a
  mistake or to push a second binary rather than trying to undo anything.
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
- If a release-stream entry was drafted: its path, and that it is **not published yet** —
  publish it when Apple approves the build (Step 12)

## Step 12 — Publish the release-stream entry (at App Store approval)

**Not part of the archive run.** This step happens later, when Apple approves the build and
users can actually install it — invoke the skill again (or just follow this step) then. Why the
split: **§A6**.

Pre-flight:

1. **Confirm the build is live** on the App Store — approved *and* released, not merely
   "Pending Developer Release". If it isn't, stop: the entry would announce a version nobody
   can download.
2. Set the entry's `date` to **today** (the release date), not the archive date, so it
   interleaves chronologically where users could first install it.
3. Check the category is deployed: `app_release` reached prod in `44284321`. If prod predates
   that commit, `import` rejects the unknown category — deploy first.

Publish (`-` reads stdin, because `release-notes/` is not in the container image):

```bash
ssh <user>@<server> "docker exec -i weatherbrief python -m weatherbrief.release import -" \
  < release-notes/ios-{version}.json
```

Confirm it landed, and that the iOS app sees it:

```bash
ssh <user>@<server> "docker exec weatherbrief python -m weatherbrief.release list" | head
curl -s https://weather.flyfun.aero/api/messages | head -c 400
```

The entry now appears in the web help page's What's New tab **and** the app's What's New view
(More → What's New). The web renders an install call to action under it; the app deliberately
does not — a reader already inside the app has nothing to install.

If this is the first `app_release` entry on prod, also import the historical backfill so the
stream reads as a complete app history rather than starting mid-way:

```bash
ssh <user>@<server> "docker exec -i weatherbrief python -m weatherbrief.release import - \
  --force-no-highlight" < release-notes/ios-backfill.json
```
