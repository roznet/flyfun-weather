# iOS release reference

Background for the `archive` skill (`.claude/skills/archive/SKILL.md`). The skill holds the
release procedure; this doc holds the *why* — the reasoning behind each pre-flight gate, the
tagging convention, how reviewer sign-in works, and why the release-stream entry is drafted at
archive but published only at App Store approval.

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

## §A6 — The release-stream entry: drafted at archive, published at approval

Every shipped app version gets one `app_release` entry in the site's What's New stream
(`system_messages`), whose body is the App Store "What's New" text. Before #550 the archive
skill wrote App Store notes and stopped there, so app news lived only in App Store Connect and
five versions shipped without appearing in the stream at all.

**Why the two steps are split.** Archiving happens *before* upload, and Apple review sits
between that and users being able to install. An entry announcing a version nobody can download
is wrong — but the commit range that the notes are derived from is freshest at archive time, and
reconstructing it days later is worse. So: draft at archive (Step 8), publish at approval
(Step 12). Reset the `date` when publishing; the stream is ordered by that field rather than by
insert order, so a stale date would file the entry in the past.

**Title convention:** `iOS {version} — <short headline>`, e.g. `iOS 1.4 — Route SIGMETs,
observations and approach feasibility`. The version is in the title rather than in a separate
field because the stream is unified: an entry sits among server-side features and changes, and
the reader needs to see at a glance that this one is an app version.

**Body is the App Store bullets verbatim.** Two hand-maintained copies of the same notes drift;
copying them keeps the App Store listing and the stream identical by construction.

**Highlighting is a judgement, not a rule.** `highlight` is what lights the unseen dot (the
`/api/messages/status` count includes highlighted rows only). Highlight a release carrying real
user-facing news; leave a bug-fix-only build unhighlighted. Historical backfill
(`release-notes/ios-backfill.json`, 1.0–1.4) is all unhighlighted — importing it must not hand
existing users a wall of dots for work they've already been using.

**The stream is not platform-filtered.** Most entries (a new advisory, a threshold change) reach
app users the moment the server deploys, with no app release involved, so splitting the stream
by platform would be wrong. `app_release` is a *kind* of entry, not a filter. What is
per-client is the rendering: the web adds an install call to action under `app_release` entries
(from a single `APP_STORE_URL`), and the iOS app deliberately omits it.

## §A7 — App Store Connect staging (`scripts/asc.py`)

The release used to end at "the archive is in Organizer" — the pilot then created the version
in App Store Connect, pasted the What's New text, uploaded via Organizer, and pasted the
reviewer link. `scripts/asc.py` does all four over the App Store Connect API.

**There is no `submit` subcommand, and that is the design.** Everything else in the chain is
reversible: a version's metadata can be rewritten, its build swapped, the whole version
deleted. Submitting to App Review is the one step that hands control to Apple. Rather than
guard it with a confirmation prompt — which is one stray "yes" away from firing — the
capability simply does not exist in the tool. The pilot presses Submit in the web UI, having
seen `asc.py status`. Do not add a submit path.

**Credentials never enter the repo.** This is a public checkout. `ASC_KEY_ID` and
`ASC_ISSUER_ID` live in the gitignored `.env` (documented by name, with empty values, in
`.env.sample`); the `.p8` itself lives outside the repo, defaulting to Apple's conventional
`~/.appstoreconnect/private_keys/AuthKey_<KEY_ID>.p8`, which is also where `xcodebuild` and
`altool` look. `*.p8` is gitignored as a backstop. The key needs the **App Manager** role —
a Developer-role key reads fine and then fails every write with a 403.

Unlike `APNS_KEY_P8`, there is no inline/base64 form. That shape exists for APNs because prod
injects the key through docker-compose `env_file`; `asc.py` runs only on a developer machine,
and `xcodebuild -authenticationKeyPath` wants a real file, so a path is the honest interface.

**Idempotency is the whole point.** A release rarely goes right first time — a rejected
binary, a typo in the notes, a bumped build number. Every subcommand is re-runnable:
`ensure-version` reuses the existing editable version (renaming it if the marketing version
moved) instead of failing on "version already exists", `set-notes` overwrites in place, and
attaching a build just moves a pointer. The correct response to a mistake is to re-run, never
to unwind.

**Two failure modes worth knowing:**

- *Version state has two spellings.* Apple added `appVersionState` alongside the older
  `appStoreState` and both may appear depending on which API revision serves the request.
  `Asc.state_of` prefers the new one and falls back. It returns `UNKNOWN` when neither is
  present, and `UNKNOWN` is deliberately absent from `EDITABLE_STATES` — an unparseable state
  must never read as "safe to edit".
- *Xcode can rewrite your build number.* The export options set
  `manageAppVersionAndBuildNumber: false`. Left at its default, Xcode may renumber the build
  during export, and the build that lands in App Store Connect then won't match the number
  `attach-build` searches for — which surfaces much later as a confusing "build not found".

**Build numbers are not unique on their own.** They repeat across marketing versions, so
`find_build` filters on the build number server-side and then matches the pre-release version
client-side to pin the (1.5, 11) pair. Filtering on the build number alone will eventually
attach the wrong binary.
