# iOS release reference

Background for the `archive` skill (`.claude/skills/archive/SKILL.md`). The skill holds the
release procedure; this doc holds the *why* — the reasoning behind each pre-flight gate, the
tagging convention, and how reviewer sign-in works.

Read the section the skill points you at. The skill is complete on its own for a normal
archive.

## §A1 — Why both iPhone and iPad must run

`flyfun-weather` is a universal app (`TARGETED_DEVICE_FAMILY = "1,2,7"` — iPhone + iPad +
visionOS), and the flows the UI tests walk cross a real idiom fork. **"iPhone passes" does not
imply "iPad passes."**

`FlightListView` is a `NavigationSplitView` that collapses to the *list* on iPhone (compact)
but to the *detail pane*, behind a "Show Sidebar" toggle, on iPad portrait (regular). Other
surfaces branch on `horizontalSizeClass` too: `RouteMapView` dual metrics,
`BriefingContainerView`, `SkewTDetailView`, `AirportConditionsView`.

Concretely: the UI test's iPad-portrait branch in `revealFlightList()` — the "Show Sidebar"
tap — is *only* exercised when the suite actually runs on an iPad. An iPhone-only run leaves
that path untested and free to rot.

The iPad pass uses **portrait**, the idiom where the split-view fork lives.

**Both testable targets run together.** The `flyfun-weather` scheme's Test action includes
`flyfun-weatherTests` (unit) and `flyfun-weatherUITests` (UI), neither skipped. The UI tests
exercise the real app launch and the flight-list / add-flight flows — exactly what ships — so
do **not** skip or disable the UI target to make the build faster. A UI failure on *either*
idiom is a release blocker like any other test.

UI tests are slower and more sensitive to simulator state than unit tests. Use a generous
timeout, and if they flake on simulator boot, retry once before concluding there's a real
failure.

**Use concrete device names.** `generic/platform=iOS Simulator` can build-for-testing but
won't actually *run* the UI tests. List what's installed with
`xcrun simctl list devices available` and substitute the closest current iPhone / iPad if the
names in the skill aren't present.

> Scope note: running both idioms is the standard for the **archive** pre-flight — infrequent,
> and it ships universal. For the day-to-day dev inner loop an iPhone-only run is an acceptable
> fast gate.

## §A2 — Privacy declarations must match the web page

`PrivacyInfo.xcprivacy` and the web privacy page (`web/privacy.html`) must declare the same
data types. **Divergence between them is a common App Review nit**, so they are kept in sync
deliberately rather than incidentally.

Why each declared type is there:

| Type | Why |
|---|---|
| `NSPrivacyCollectedDataTypeEmailAddress` | Account identity |
| `NSPrivacyCollectedDataTypeName` | Account identity |
| `NSPrivacyCollectedDataTypeUserID` | Account identity |
| `NSPrivacyCollectedDataTypePreciseLocation` | PIREPs upload location tied to the user on the server — must be `Linked=true` |
| `NSPrivacyCollectedDataTypeOtherUserContent` | Flight routes, waypoints, PIREP notes, feedback |
| `NSPrivacyCollectedDataTypeProductInteraction` | API call counts, LLM token usage |

`ITSAppUsesNonExemptEncryption = false` in `Info.plist` declares HTTPS-only usage, which stops
App Store Connect demanding annual export-compliance documentation.

## §A3 — Tagging convention

Tags track the **marketing version only**, pattern `ios/{marketing_version}` — e.g. `ios/1.0`,
`ios/1.1`.

- Tags correspond to `MARKETING_VERSION`, **never** to `CURRENT_PROJECT_VERSION` (build number).
- A build-only bump does **not** create a new tag — it *moves* the existing tag for that
  version, so the tag always points at the latest build of that marketing version.
- Only patch / minor / major bumps create a genuinely new tag.

That's why the skill force-pushes on a build-only bump and plain-pushes on a version bump: the
force is the intended "move the pointer", not an accident.

## §A4 — Release-note style

Write for a pilot, not a changelog reader:

- Group related changes into bullets; don't mirror commits one-for-one.
- Plain language — no commit hashes, no technical jargon.
- Focus on features and fixes a user notices.
- Skip internal changes: tests, CI, refactoring, version bumps, doc syncs.
- 5–8 bullets maximum.

## §A5 — How reviewer sign-in works

App Review needs a working sign-in, which is delivered as a deep link in the review notes.

- The reviewer signs in through the app's `flyfunweather://auth?token=<jwt>` deep link. The JWT
  is **self-contained** — auth verifies the signature only, it does not hit the DB — so the
  token can be minted from a dev checkout as long as it's signed with the production
  `JWT_SECRET`.
- The token is bound to a dedicated **"Sign in with Apple" test account** (private-relay
  email). That account already exists in the production DB; the script only issues a session
  token for it, it does not create the user.
- The standard `flyfun_common.auth.create_token` helper issues **7-day** tokens — too short for
  a review cycle. That's why a dedicated script exists with a configurable (default 60-day)
  expiry.
- **Tokens expire**, 7–60 days after minting, so the one in the current review notes may well
  be stale by the next submission. Mint fresh rather than assuming.

**Nothing sensitive is committed.** The script reads everything from `.env` (gitignored):
`JWT_SECRET` plus the reviewer identity `REVIEWER_USER_ID` / `REVIEWER_EMAIL` /
`REVIEWER_NAME`. A fresh checkout missing these gets an error naming the missing variable —
re-add them to `.env`; they are absent from the repo by design.

> Note that the dev `.env` `JWT_SECRET` is the same value as production's. That's what makes
> local minting work, and it also means a compromised dev checkout can forge production
> tokens. Worth knowing when deciding where that file lives.
