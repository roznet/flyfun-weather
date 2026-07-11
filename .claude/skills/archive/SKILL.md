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

### 2b — App tests (unit + UI), iPhone **and** iPad

Run the Xcode test suite on **both** an iPhone and an iPad simulator. This is a
universal app (`TARGETED_DEVICE_FAMILY = "1,2,7"` — iPhone + iPad + visionOS),
and the flight-list/add-flight flows the UI tests walk cross a real idiom fork:
`FlightListView` is a `NavigationSplitView` that collapses to the list on iPhone
(compact) but to the detail pane behind a "Show Sidebar" toggle on iPad portrait
(regular). Other surfaces branch on `horizontalSizeClass` too (`RouteMapView`
dual metrics, `BriefingContainerView`, `SkewTDetailView`, `AirportConditionsView`).
So "iPhone passes" does **not** imply "iPad passes" — and the UI test's
iPad-portrait branch in `revealFlightList()` (the "Show Sidebar" tap) is *only*
exercised when the suite actually runs on an iPad. iPhone-only leaves that path
untested and free to rot.

The `flyfun-weather` scheme's Test action includes **both** testable targets,
neither skipped, so each run executes them together:
- `flyfun-weatherTests` — unit tests
- `flyfun-weatherUITests` — **UI tests** (e.g. `testFlightListRendersSeededFlights`,
  `testAddFlightValidationAndCreate`)

The UI tests are an important part of this preflight check — they exercise the
real app launch + flight-list/add-flight flows, which is exactly what we ship.
Do not skip or disable the UI test target to make the build go faster; if a UI
test fails on **either** idiom, treat it as a release blocker like any other
test. UI tests are slower and more sensitive to simulator state than unit tests,
so use a generous timeout (see below) and, if they flake on simulator boot,
retry once before concluding there's a real failure.

Use concrete device names (not `generic/platform=iOS Simulator`, which can
build-for-testing but won't actually *run* the UI tests). The iPad pass uses
portrait, the idiom where the split-view fork lives. Pick a model the machine
has installed — list with `xcrun simctl list devices available` and substitute
the closest current iPhone / iPad if these exact names aren't present.

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

If tests fail on either idiom, stop and show the failures, noting which device
the failure was on. Because each run boots a simulator and drives the app, and
this now runs twice, use a timeout of 600000ms (10 min) **per pass**.

> Note: running both idioms is the standard for the archive preflight (infrequent,
> ships universal). For the day-to-day dev inner loop, an iPhone-only run is an
> acceptable fast gate — but the archive must run both.

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

Verify required keys are present in `app/flyfun-weather/flyfun-weather/SupportingFiles/Info.plist`:
- `NSLocationWhenInUseUsageDescription` (for flight tracking)
- `ITSAppUsesNonExemptEncryption` set to `false` (declares HTTPS-only usage so App Store Connect doesn't demand annual export compliance docs)

Verify `PrivacyInfo.xcprivacy` exists at `app/flyfun-weather/flyfun-weather/PrivacyInfo.xcprivacy` and declares at minimum the data types the web privacy page (`web/privacy.html`) claims are collected:
- `NSPrivacyCollectedDataTypeEmailAddress`
- `NSPrivacyCollectedDataTypeName`
- `NSPrivacyCollectedDataTypeUserID`
- `NSPrivacyCollectedDataTypePreciseLocation` (must be `Linked=true` — PIREPs upload location tied to the user on the server)
- `NSPrivacyCollectedDataTypeOtherUserContent` (flight routes, waypoints, PIREP notes, feedback)
- `NSPrivacyCollectedDataTypeProductInteraction` (API call counts, LLM token usage)

If any required key/declaration is missing, stop and warn. Divergence between `PrivacyInfo.xcprivacy` and `web/privacy.html` is a common reviewer nit, so they must stay in sync.

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

## Step 10 — App Store reviewer sign-in token (ask first)

Do **not** mint this by default. After reporting, ask the user:

> Are you submitting this build to the App Store for review? If so, the App
> Review notes need a working sign-in deep link, and the previous reviewer
> token expires 7–60 days after it was minted — so it may be stale. Mint a
> fresh one?

Only if they say yes, mint it:

```bash
source venv/bin/activate && python3 scripts/mint_reviewer_token.py --days 60
```

Then give the user the printed `flyfunweather://auth?token=…` line to paste into
**App Store Connect → App Review Information → Notes** (alongside the "tap this
link on the device / simulator to sign in" instruction).

**Background so you can explain it if asked:**
- The reviewer signs in through the app's `flyfunweather://auth?token=<jwt>`
  deep link. The JWT is self-contained — auth verifies the signature only, it
  does **not** hit the DB — so the token can be minted from a dev checkout as
  long as it's signed with the production `JWT_SECRET` (the dev `.env`
  `JWT_SECRET` *is* the prod secret).
- The token is bound to a dedicated **"Sign in with Apple" test account**
  (private-relay email). The account already exists in the prod DB; the script
  only issues a session token for it, it does not create the user.
- The standard `flyfun_common.auth.create_token` helper only issues **7-day**
  tokens — too short for a review cycle — which is why this dedicated script
  exists with a configurable (default 60-day) expiry.
- **Nothing sensitive is committed.** The script reads everything from `.env`
  (gitignored): `JWT_SECRET` (the only real secret) plus the reviewer identity
  `REVIEWER_USER_ID` / `REVIEWER_EMAIL` / `REVIEWER_NAME`. If a fresh checkout
  is missing these, the script exits with a message naming the missing var —
  re-add them to `.env` (they are not in the repo by design).

## Step 11 — Close iOS-shipped issues (ask first)

iOS-only issues ship through the **App Store**, not through a server deploy — so
the `/deploy` skill's close step (which comments "Deployed to
https://weather.flyfun.aero") **never touches them**. Nothing else closes them
either. Left alone, they stay open forever even after the feature ships. This
step is the App-Store analogue of `/deploy`'s "Close Addresses issues" step.

> **Important, mirrors the deploy gate:** we only close an issue once the work is
> actually *live for users*. For a server deploy that's the moment the health
> check returns 200. For iOS the equivalent is **App Store approval**, not
> submission. Because approval lands async (hours–days later, out of band from
> this skill), the default here is to **comment now and defer the close**, then
> close on the next run once approval is confirmed. Only close-on-submission if
> the user explicitly asks (they sometimes do — it keeps the tracker tidy).

Enumerate the issues referenced by the iOS work in this build, then confirm with
the user before touching anything:

```bash
# Range = previous ios/* tag → HEAD (same range as the release notes in Step 8).
PREV_TAG=$(git tag -l "ios/*" --sort=-version:refname | sed -n '2p')  # 1p is the tag just created
# Collect issue numbers from BOTH commit messages and the PRs merged in the range.
# Match the deploy skill's keyword whitelist so we don't close passing references.
git log "${PREV_TAG}..HEAD" --pretty='%B' \
  | grep -oiE '(addresses|refs?|references|related to|closes?|closed|fix(es|ed)?|resolves?|resolved)[[:space:]]+(issue[[:space:]]+)?#[0-9]+' \
  | grep -oE '#[0-9]+' | tr -d '#' | sort -un
```

Bare `#N` references (no keyword) are intentionally **not** matched — same rule
as the deploy skill — so passing "see #50 for context" mentions don't get closed.
If a recent PR used a bare `#N` and you know it should close, name it explicitly
to the user rather than loosening the regex.

For each candidate issue, present it to the user with its title and the PR/commit
that shipped it, and ask which to act on. Then:

- **Default (defer):** comment and leave open —
  ```bash
  gh issue comment "$issue" --body "Shipped in iOS build ${BUILD} (v${VERSION}), submitted to App Store review. Will close once Apple approves."
  ```
  Note these in the report so the **next** archive run (or a manual pass after
  approval) closes them.
- **Close-on-submission (only if the user asks):**
  ```bash
  gh issue comment "$issue" --body "Shipped in iOS build ${BUILD} (v${VERSION})."
  gh issue close "$issue"
  ```
- Issue already **CLOSED** (e.g. its web/server half closed it via `/deploy`):
  comment only, don't reopen.

> **Root-cause note (why this step exists):** PRs that ship iOS features must
> still carry a close-intent keyword (`Closes #N` / `Addresses #N`) in the body
> **and** commit message — see the PR template. But even a correctly-keyworded
> iOS PR must **not** auto-close at merge (the feature isn't live until Apple
> approves), which is exactly why iOS work should use `Addresses #N` (close on
> ship), never `Closes #N` (close on merge). This step is where that close
> finally happens.
