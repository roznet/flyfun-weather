# Android Client: Feasibility, Structure, and Phase 1 Scope

> **Status: exploratory. Nothing built. Written 2026-09-11.**
>
> This is a decision document, not a commitment. It answers three questions:
> what a native Android app looks like structurally, how it would sit in this
> repo, and which parts of a Phase 1 are safely delegable to a coding agent
> versus which need a human with Android judgement. Written for a reader with
> **zero Android experience**, so it maps every concept onto the iOS app that
> already exists in `app/flyfun-weather/`.
>
> Decision gates are marked **GATE** and are deliberately placed before the
> expensive parts. Read "Option chosen" and "Phase 1 scope split" first; the
> rest is supporting detail.

## Related docs

- [iOS Architecture](../ios-app-architecture.md): the reference implementation this would mirror
- [iOS Server API](../ios-app-server-api.md): the endpoint contract a third client would consume
- [iOS Overview](../ios-app-overview.md): as-built feature status, the scope baseline
- [iOS/Web known gaps](./ios-web-known-gaps.md): existing two-client drift, which a third client multiplies

---

## 1. The cost picture, measured

Before any structural discussion, the size of the thing being proposed.
Measured 2026-09-11 on `main`:

| Surface | Size |
|---|---|
| iOS app, total Swift | **42,224 lines** across 205 files |
| of which `Views/` | 18,388 lines |
| of which `Views/CrossSection/` | 5,471 lines across 32 files |
| Web client (TypeScript) | 58,264 lines |

Plus three Swift dependencies with **no Android counterpart whatsoever**:

| Package | What it does | Android consequence |
|---|---|---|
| `RZSkewT` (own repo, `roznet/rztskew`) | Skew-T log-P thermodynamics + Canvas rendering, 47 unit tests | Full re-implementation in Kotlin, including the meteorology |
| `FlyFunCommon` (`roznet/flyfun-common`) | Auth service, session, keychain, rolling bearer token | Re-implementation; the OAuth hardening (`state` nonce, `/auth/exchange`) must be reproduced exactly |
| `RZFlight` | Airport data, `KnownAirports`, FMDB access to `airports.db` | Re-implementation over Room or raw SQLite |

**A native Android app is therefore not a port. It is a second complete client
plus three libraries, in an unfamiliar ecosystem.** Realistic order of magnitude
for full parity: 6 to 12 months part-time, and a permanent increase in
maintenance surface.

### The drift multiplier

This repo already contains a skill (`.claude/skills/sync-ios-web/`) whose entire
reason for existing is that two hand-written clients of the same backend drift
apart on hand-copied surfaces (`metrics-catalog.json`, cross-section preset
tables, debrief taxonomy, API DTOs).

Two clients is one pair to audit. Three clients is **three pairs**. The
`sync-ios-web` skill would need to become a three-way audit, and the
`metrics-catalog.json` byte-compare step would need a third copy under
`app/android/app/src/main/assets/`.

This is a real, recurring, permanent cost and it should be priced in before,
not after.

---

## 2. Options considered

| Option | Effort | What you get | What you lose |
|---|---|---|---|
| **A. Full native parity** | 6-12 months part-time | Everything iOS has | Time; three-way drift forever; three Swift libs reimplemented |
| **B. Scoped native Phase 1** ✅ | ~6-10 weeks part-time, much of it delegable | Flight list, advisory tab, digest, push. A real app, on a bounded surface | No cross-section, no map, no Skew-T, no offline bundle |
| **C. Wrapped web (TWA / WebView)** | ~1-2 weeks | "There is an Android app" cheaply. Android supports web push, unlike iOS, so the notification loop closes | No offline pack cache, no background location, no native feel. **The web app currently has no service worker and no manifest**, so it is not PWA-ready: that is prerequisite work |
| **D. Kotlin Multiplatform** | Very high | Shared data layer across both platforms | Requires rewriting the *working* Swift data layer in Kotlin to share it. Right answer from zero, wrong answer from here |

### Option chosen: **B, scoped native Phase 1**

Rationale:

- It bounds the risk. If Phase 1 lands and feels good, extend. If it stalls,
  the loss is weeks, not a year.
- It teaches the ecosystem on real code rather than a tutorial.
- The Phase 1 surface (flight list, advisory, digest, push) is the part with
  the **best verification story**, see §7. The excluded parts (cross-section
  Canvas, 620-annotation map, Skew-T) are precisely the ones with no automated
  oracle.
- Option D is explicitly rejected rather than deferred: its payoff is sharing a
  data layer that, on iOS, already exists and works.

Option C stays on the table as a fallback and is **not** mutually exclusive
with B: a TWA could ship first while native Phase 1 is built. If chosen, the
prerequisite is a service worker and web manifest in `web/`, which benefits the
web client regardless.

---

## 3. Android project structure

Android uses Gradle, not a project file. Canonical modern layout:

```
app/android/                              # repo root of the Android project
├── settings.gradle.kts                   # declares modules + dependency repos
├── build.gradle.kts                      # root: plugin versions only
├── gradle.properties
├── gradle/
│   ├── libs.versions.toml                # version catalog: pins every dependency
│   └── wrapper/
│       ├── gradle-wrapper.jar            # COMMITTED, pins the Gradle version
│       └── gradle-wrapper.properties
├── gradlew, gradlew.bat                  # COMMITTED wrapper scripts
└── app/                                  # the application module
    ├── build.gradle.kts
    └── src/
        ├── main/
        │   ├── AndroidManifest.xml
        │   ├── kotlin/aero/flyfun/weather/
        │   │   ├── FlyFunApp.kt, MainActivity.kt
        │   │   ├── data/                 # APIClient, repositories, stores
        │   │   ├── model/                # DTOs + domain types
        │   │   ├── ui/                   # Compose screens, mirrors Views/
        │   │   └── viewmodel/
        │   ├── res/                      # icons, strings, themes
        │   └── assets/                   # raw files, e.g. metrics-catalog.json
        ├── test/                         # JVM unit tests, NO emulator
        └── androidTest/                  # instrumented/UI tests, needs emulator
```

### Mapping to the iOS app you already have

| iOS | Android | Note |
|---|---|---|
| `flyfun-weather.xcodeproj` | `settings.gradle.kts` + `build.gradle.kts` | Plain text and genuinely mergeable, unlike `project.pbxproj` |
| Xcode target | Gradle module | |
| `flyfun-weather/` | `app/src/main/kotlin/` | |
| `flyfun-weatherTests/` (hermetic, **gated** in `ios.yml`) | `src/test/` | JVM, hermetic, fast. The gateable half |
| `flyfun-weatherUITests/` (simulator, **nightly** in `ios-ui-nightly.yml`) | `src/androidTest/` | Emulator, slow, flaky. The nightly half |
| `Package.resolved` | `gradle/libs.versions.toml` | Hand-written rather than generated |
| SPM | Gradle + Maven Central | |
| `Resources/` | `res/` (platform assets) + `assets/` (raw files) | `metrics-catalog.json` goes in `assets/` |

**The unit/UI split this repo already codified for iOS maps one-to-one onto
Android.** The reasoning in the header comments of `.github/workflows/ios.yml`
and `ios-ui-nightly.yml` transfers verbatim: gate the hermetic target, run the
device-driving target nightly, never let a launch hiccup fail a PR.

---

## 4. Stack decisions

**Kotlin, not Java.** Java on Android is legacy; new APIs, samples and docs are
Kotlin-first. **Jetpack Compose**, not XML layouts: it is the direct SwiftUI
analogue (declarative, `@Composable` ≈ `View`, state-driven recomposition).

| iOS choice | Android equivalent | Confidence |
|---|---|---|
| SwiftUI | Jetpack Compose + Material 3 | Settled |
| `@Observable` + MVVM | `androidx.lifecycle.ViewModel` + `StateFlow` | Settled |
| `async/await` | Kotlin coroutines + `Flow` | Settled |
| `URLSession`, SSE via `URLSession.bytes` | Ktor client (native SSE support) | Settled. Retrofit/OkHttp is the alternative but SSE is clumsier |
| `Codable` | `kotlinx.serialization` | Settled |
| `UserDefaults` | Jetpack DataStore | Settled |
| JSON-on-disk cache stores | Same shape, `java.io.File` + kotlinx.serialization | Settled. The file-based design ports directly |
| `AirportDatabase` (FMDB, downloaded SQLite) | Room, or raw `SQLiteOpenHelper` | Settled. The downloaded `airports.db` works unchanged |
| SwiftUI `Canvas` | Compose `Canvas` | Settled. Both immediate-mode; cross-section renderer maps over reasonably |
| MapKit / `MKMapView` | Google Maps Compose **or** MapLibre | **GATE, see below** |
| Swift Charts | Vico (third party) | **Gap.** No first-party equivalent |
| APNs | Firebase Cloud Messaging | Settled, but requires server work (§6) |
| Sign in with Apple + Google | Credential Manager (Google); Apple via web flow | Settled |
| App Intents / Siri / Spotlight | App Actions / `ShortcutManager` | **Weak.** Do not expect parity |

### GATE: maps

Not a Phase 1 decision (Phase 1 excludes maps), but it is the single largest
downstream risk, so record it now:

- **Google Maps Compose** needs an API key and a billing account on file
  (generous free tier, but a card is required).
- **MapLibre** is free but you build the tile source and style pipeline
  yourself.

Either way, the forecast map renders **~620 annotations**. That is exactly the
scale that pushed the iOS app off the SwiftUI `Map` API onto `MKMapView`
(#428). Assume naive Compose markers will hit the same wall and budget for a
custom marker layer.

### GATE: minimum SDK

iOS targets 26.2 with an explicit "no legacy burden" stance. The Android
equivalent decision is `minSdk`. Suggest **API 31 (Android 12)** or higher:
it keeps Compose modern and avoids a long tail of compatibility shims, at the
cost of excluding older devices. Confirm against whatever device mix matters
before writing the manifest.

---

## 5. Repo integration, and the trap

`app/android/` alongside `app/flyfun-weather/` works, but **three things must be
fixed first**:

### 5.1 `ios.yml` triggers on `app/**`, fix before adding anything

```yaml
# .github/workflows/ios.yml
    paths: &paths
      - 'app/**'                 # <- becomes wrong the moment app/android/ exists
      - '.github/workflows/ios.yml'
```

Today `app/` is synonymous with "the iOS app". The moment `app/android/` exists,
**every Android-only commit spins a `macos-26` runner and rebuilds the iOS
app.** Narrow the filter to `app/flyfun-weather/**` first. Same for
`ios-ui-nightly.yml`.

This is cheap to do now and annoying to notice later, so it is listed as step
zero in §8.

### 5.2 `tests.yml` `paths-ignore` comment goes stale

```yaml
    paths-ignore: &ignored
      - 'app/**'           # iOS/iPadOS app — has its own toolchain (xcodebuild)
```

The *behaviour* stays correct (Android also has its own toolchain), but the
comment becomes misleading. Update it to say "native clients, each with its own
toolchain".

### 5.3 Naming

Recommendation: **keep `app/flyfun-weather/` where it is** and add
`app/android/`. Asymmetric, but renaming the iOS directory churns every path in
`project.pbxproj`, the two workflows, `CLAUDE.md`, the `sync-ios-web` skill and
several design docs for pure cosmetics.

If symmetry matters, do the rename (`app/ios/` + `app/android/`) as a **separate
commit before any Android code lands**, never mixed in.

### 5.4 Three-way drift

Add `app/android/app/src/main/assets/metrics-catalog.json` to the `sync-ios-web`
skill's surface list and rename the skill (`sync-clients`?). Do this when the
first copied surface lands, not later.

---

## 6. Server-side work (outside `app/`)

Phase 1 cannot ship without these. They are Python work in `src/weatherbrief/`,
not Kotlin, and they are **fully testable by the existing pytest suite**, which
makes them good early delegable work.

| Area | Current state | Android needs |
|---|---|---|
| Push | `notify/push.py` is APNs-only (token-based, httpx HTTP/2 + PyJWT) | FCM transport alongside APNs; `dispatch.py` picks by platform |
| Device registry | `api/devices.py`, `device_tokens` table | A `platform` column (`ios` / `android`), plus an Alembic migration. **See `designs/migrations.md`: `batch_alter_table` is mandatory** |
| Auth | `?platform=ios`, callback scheme `flyfunweather://` | An Android equivalent: App Links (preferred) or a custom scheme. The `state` nonce + `/auth/exchange` hardening must be reproduced exactly, see `flyfun-common/designs/oauth-deeplink-hardening.md` |
| Apple Sign In | `POST /auth/apple/token`, bundle ID in `APPLE_APP_IDS` | Not applicable on Android; Google only |

### DTO generation

FastAPI serves an OpenAPI schema, but **`openapi_url` is disabled outside dev
mode** (`api/app.py`, `docs_kwargs`). So schema-driven Kotlin DTO generation
must run against a **dev server**, not production. Worth knowing before someone
tries to point a generator at `weather.flyfun.aero`.

---

## 7. Build the oracle before the app

This is the most important section in the document.

An agent loop is only as good as its feedback signal. The parts of this project
an agent can build unattended are exactly the parts where **a test can tell it
the truth**. The parts it will get plausibly, confidently wrong are the ones
where only a human can judge.

So the verification apparatus is not a follow-up task. It is step one.

| Order | Build this | Gives an oracle for |
|---|---|---|
| 1 | `./gradlew testDebugUnitTest` green in CI on `ubuntu-latest`, **with the vacuous-pass guard already written into `ios.yml`** (a filter matching zero tests must fail, not silently pass) | Data layer, DTOs, repositories, stores |
| 2 | **Screenshot testing (Roborazzi or Paparazzi)** | Compose layout. Runs on the JVM, no emulator. Turns "does this screen look right" from a human-only question into a diffable artifact |
| 3 | Round-trip serialization tests against captured real API responses | DTO correctness against the live contract |
| 4 | `androidTest` journeys on a nightly emulator workflow (`reactivecircus/android-emulator-runner`) | End-to-end flows. Nightly, **not** gated, exactly per `ios-ui-nightly.yml`'s reasoning |

Step 2 is the highest-leverage item on this list and the easiest to skip. Skip
it and every Compose screen becomes human-review-only, which is what makes
UI work slow and undelegable.

**CI note, and it is good news:** Android unit tests run on `ubuntu-latest` with
no emulator. Unlike the iOS split, the gating workflow is fast and free, and
the agent can run the full hermetic suite locally in seconds.

---

## 8. Phase 1 scope split

**Phase 1 target:** sign in, see your flights, open a briefing, read the
advisory verdict and the LLM discussion, get a push when a refresh finishes.
Nothing else.

Column meanings:

- **Est.**: rough Kotlin line count. Estimates, not measurements.
- **Oracle**: what tells us it is correct. This drives the next column.
- **Owner**: `agent` = safely delegable given the oracle exists;
  `human` = needs Android judgement or visual assessment;
  `pair` = agent drafts, human reviews with a device in hand.

### 8.0 Prerequisites (do these first, in order)

| # | Task | Est. | Oracle | Owner |
|---|---|---|---|---|
| 0a | Narrow `ios.yml` + `ios-ui-nightly.yml` path filters to `app/flyfun-weather/**` | ~4 lines | CI still fires correctly on an iOS-only commit | agent |
| 0b | Server: `platform` column on `device_tokens` + Alembic migration | ~60 py | pytest; migration up/down on SQLite **and** MySQL | agent |
| 0c | Server: FCM transport in `notify/push.py`, dispatch by platform | ~150 py | pytest with a mocked FCM endpoint | agent |
| 0d | Server: Android auth callback (App Links), `?platform=android` | ~80 py | pytest; manual end-to-end once the app exists | pair |

### 8.1 Scaffolding and infrastructure

| # | Task | Est. | Oracle | Owner |
|---|---|---|---|---|
| 1a | Gradle project, version catalog, wrapper, `minSdk` decision | ~200 | `./gradlew assembleDebug` succeeds | agent |
| 1b | `.github/workflows/android.yml`, `ubuntu-latest`, unit tests, **vacuous-pass guard** | ~80 | Workflow green; deliberately broken test goes red | agent |
| 1c | Roborazzi screenshot-test harness wired in | ~120 | A known-good screen produces a stable golden image | agent |
| 1d | App shell: `MainActivity`, theme, navigation skeleton | ~300 | Compiles, launches, screenshot golden | pair |

### 8.2 Data layer (the well-verified part)

| # | Task | Est. | Oracle | Owner |
|---|---|---|---|---|
| 2a | DTOs for Phase 1 endpoints, `kotlinx.serialization`, generated from dev-server OpenAPI | ~1,200 | Round-trip tests against captured real responses | **agent** |
| 2b | `ApiClient` over Ktor: bearer token, rolling refresh, error mapping | ~400 | Unit tests with `MockEngine` | **agent** |
| 2c | `BriefingRepository` interface + `OnlineBriefingRepository` (Phase 1 subset only) | ~350 | Unit tests | **agent** |
| 2d | `CachingBriefingRepository` + on-disk JSON pack cache | ~450 | Unit tests over a temp dir | **agent** |
| 2e | `FixtureBriefingRepository` (canned fixtures, mirrors the iOS `FLYFUN_MOCK=1` pattern) | ~500 | It *is* test infrastructure | **agent** |
| 2f | DataStore preferences store | ~200 | Unit tests | **agent** |
| 2g | `NetworkMonitor` (connectivity state) | ~120 | Unit tests + one manual airplane-mode check | pair |

**§8.2 is ~3,200 lines, essentially all agent-suitable, because every row has a
real oracle.** This is the single biggest delegable block in Phase 1 and the
reason Option B is attractive.

### 8.3 Auth

| # | Task | Est. | Oracle | Owner |
|---|---|---|---|---|
| 3a | Google sign-in via Credential Manager | ~300 | Manual. No meaningful automated test | **human** |
| 3b | Token exchange (`state` nonce → `/auth/exchange`), keychain-equivalent storage (EncryptedSharedPreferences) | ~250 | Unit-testable logic; the OS integration is not | pair |
| 3c | App Links deep-link handling for the OAuth callback | ~150 | Manual; `adb shell am start` can drive it | **human** |

Auth is small in lines and large in frustration. It is OS-integration work with
device-specific failure modes, a security-sensitive protocol that must match
`oauth-deeplink-hardening.md` exactly, and almost no automated oracle. **Budget
disproportionate human time here.**

### 8.4 UI (Compose)

| # | Task | Est. | Oracle | Owner |
|---|---|---|---|---|
| 4a | Flight list screen: sections (future/recent/past), unseen dot, pull-to-refresh | ~700 | Screenshot goldens + ViewModel unit tests | pair |
| 4b | `FlightListViewModel` (grouping, badge, refresh state) | ~350 | Unit tests. Port `FlightGroupingTests.swift` logic | **agent** |
| 4c | Briefing container + two tabs (Advisory, Discussion) | ~400 | Screenshot goldens | pair |
| 4d | Advisory tab: hero verdict, advisory grid, GREEN collapse strip | ~900 | Screenshot goldens; severity is server-computed so no logic risk | pair |
| 4e | Discussion tab: LLM digest rendering, markdown-lite | ~450 | Screenshot goldens + parser unit tests | pair |
| 4f | Theme, typography, colour tokens, **cockpit legibility** | ~300 | Human eye. No oracle exists | **human** |
| 4g | Error, empty, offline and loading states across all screens | ~400 | Screenshot goldens per state | pair |

Note 4d: advisory severity and all weather maths are **server-computed**
(`sync-ios-web` explicitly scopes them out of drift audits). The client only
renders. That removes the entire class of "the agent got the meteorology wrong"
risk from Phase 1, which is a significant part of why this scope was chosen.

### 8.5 Push

| # | Task | Est. | Oracle | Owner |
|---|---|---|---|---|
| 5a | FCM integration, token upload, `FirebaseMessagingService` | ~300 | Manual, on a real device | **human** |
| 5b | Foreground suppression, badge sync, tap → deep link (`PendingNavigation` equivalent) | ~250 | Unit-testable routing logic; delivery is not | pair |
| 5c | Firebase project setup, `google-services.json`, signing config | config only | It works or it does not | **human** |

### 8.6 Rough totals

| Owner | Est. Kotlin | Share |
|---|---|---|
| **agent** (real oracle, delegable) | ~3,900 | **~40%** |
| **pair** (agent drafts, human reviews on device) | ~4,200 | ~43% |
| **human** (judgement or OS integration) | ~1,700 | ~17% |
| **Total Phase 1** | **~9,800 Kotlin** + ~290 Python | |

Against 42,224 lines of Swift for the full iOS app, Phase 1 is roughly **a
quarter of the surface** and deliberately the quarter with the best oracles.

---

## 9. Explicitly out of scope for Phase 1

Recorded so that scope creep is a visible decision rather than a drift:

- **Cross-section Canvas renderer.** 5,471 lines on iOS. Correctness is "does
  this look like the atmosphere a pilot expects", which is a meteorological
  judgement with no automated oracle.
- **Forecast map and route map.** ~620 annotations, an unresolved
  Google-Maps-vs-MapLibre gate, and a known performance cliff.
- **Skew-T.** Requires porting `RZSkewT` including its thermodynamics.
- **Offline bundle, auto-download, eviction.**
- **PIREPs** (submit, offline queue, list).
- **Live flight tracking.** Background location on Android is materially harder
  than on iOS: foreground services, per-OEM battery killers, a permission
  model that changed across several releases.
- **Debrief, sharing, timing scenarios, alternates, What's New, help catalog.**
- **App Actions / Shortcuts** (the App Intents equivalent).

If Phase 1 succeeds, the natural Phase 2 is PIREPs plus the offline bundle
(both have good oracles), and the natural Phase 3 is the cross-section (poor
oracle, high value, do it with a human at the wheel).

---

## 10. Decision gates

| Gate | Question | Decide before |
|---|---|---|
| G1 | Native Phase 1 at all, or ship a TWA/PWA wrapper first? | Any Kotlin is written |
| G2 | `minSdk` floor | The manifest exists |
| G3 | Directory naming: keep `app/flyfun-weather/`, or rename to `app/ios/`? | Any Android file lands |
| G4 | Firebase account, project and billing posture for FCM | §8.0c |
| G5 | Google Maps (API key + billing) vs MapLibre | **Phase 2**, not Phase 1 |
| G6 | Does `sync-ios-web` become `sync-clients` (three-way)? | The first copied surface lands |

G1 is the real one. Everything below it is mechanical.

---

## 11. Why the "one prompt builds a compiler" demos do not transfer directly

Recorded because it will come up again, and because it explains the shape of
§8's ownership column.

Those demos are favourable on four axes that this project is not:

1. **The spec is public and precise.** A Scheme subset has a published grammar
   and thousands of reference implementations. "An Android client for FlyFun
   Weather" has requirements living in 42k lines of Swift and in what European
   GA pilots need in a cockpit.
2. **Verification is free, instant and truthful.** The compiler compiles or it
   does not. Compose layout quality has no such signal, which is exactly why §7
   comes before §8.
3. **Nothing outside the artifact matters.** No production backend, no live
   users, no auth, no AIRAC cycle, no existing client whose conventions must be
   matched.
4. **The bar is demo-grade.** "It built a compiler" means it compiles the twelve
   examples, not that it has usable error messages.

Cost is **not** the limiting factor. A long autonomous run is tens of dollars,
which against months of evenings is noise. The limiting factor is the oracle.

Where this repo is *better* than average for agent work: the design docs,
`CLAUDE.md` conventions, the code-review skill, and the existing iOS app as a
reference implementation together constitute a specification and a verification
apparatus. That is the thing the flashy demos manufacture artificially, and
here it already exists.

---

## 12. References

- `app/flyfun-weather/`: the iOS reference implementation
- `.github/workflows/ios.yml`: the gated hermetic unit job, including the
  vacuous-pass guard worth copying verbatim
- `.github/workflows/ios-ui-nightly.yml`: the non-gated device-driving job and
  the reasoning for the split
- `.claude/skills/sync-ios-web/SKILL.md`: the hand-copied surfaces that would
  become three-way
- `designs/migrations.md`: `batch_alter_table` is mandatory for §8.0b
- `designs/ios-app-server-api.md`: the endpoint contract
- `flyfun-common/designs/oauth-deeplink-hardening.md`: the auth protocol §8.3b
  must reproduce
