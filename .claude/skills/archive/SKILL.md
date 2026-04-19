# Archive for App Store

Build an Xcode archive ready for App Store upload (iOS only).

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

### 2b — App tests

Run the Xcode test suite:
```bash
xcodebuild test \
  -project app/flyfun-weather/flyfun-weather.xcodeproj \
  -scheme flyfun-weather \
  -destination "platform=iOS Simulator,name=iPhone 17 Pro" \
  -quiet \
  2>&1 | tail -30
```
If tests fail, stop and show the failures. Use timeout of 300000ms.

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

Verify required usage descriptions are present in `app/flyfun-weather/flyfun-weather/SupportingFiles/Info.plist`:
- `NSLocationWhenInUseUsageDescription` (for flight tracking)

Verify `PrivacyInfo.xcprivacy` exists at `app/flyfun-weather/flyfun-weather/PrivacyInfo.xcprivacy`.

If any are missing, stop and warn.

### 2h — Local package overrides

Check `app/flyfun-weather/flyfun-weather.xcodeproj/project.pbxproj` for absolute local package paths (e.g. `/Users/brice/Developer/public/rzskewt`). These break builds on other machines and must be reverted to remote SPM references before archiving. **Stop and warn the user** if found.

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

Tags track the **marketing version** only. The pattern is `ios/{marketing_version}`:
- Example: `ios/1.0`, `ios/1.1`

**Key rules:**
- Tags correspond to `MARKETING_VERSION`, never to `CURRENT_PROJECT_VERSION` (build number)
- Build-only bumps (`/archive build`) do NOT create a new tag — they move the existing tag for that version
- Only patch/minor/major bumps create a genuinely new tag

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

Write concise, user-facing release notes:
- Group related changes into bullet points
- Use plain language (no commit hashes, no technical jargon)
- Focus on features and fixes the user cares about
- Skip internal changes (tests, CI, refactoring, version bumps, doc syncs)
- Keep it to 5-8 bullet points max

Show the release notes to the user for review before pushing tags.

## Step 9 — Report

Tell the user:
- Pre-flight check results summary
- Archive created at the path
- Version and build number in the archive
- The tag that was created
- The release notes for the App Store
- It should now appear in **Xcode → Window → Organizer**
- From there they can **Distribute App** → **App Store Connect** to upload
- Remind them to push the version bump commit when ready
