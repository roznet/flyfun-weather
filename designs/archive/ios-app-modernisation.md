# iOS App Modernisation — UX & Visual Brainstorm

> **Status: SHIPPED (verified 2026-07-11, re-verified 2026-08-15). Historical plan, kept for the "why".**
> Started as a brainstorm 2026-06-23; the whole §8 phase plan executed. Epic `#285` and
> **all phases #286–#293 (P0–P7) are CLOSED.** The §0 baseline, the §4/§6 "DECIDED" items
> **and the §5 parity tables** are a **historical snapshot** — do not read any of them as the
> app's current shape.
> **The durable design has already been folded into the `ios-app-*` docs** — read those, not
> this file, for current truth: `ios-app-overview.md` ("Current Implementation Status" — tab
> set, `FocusIntent`, cross-section layer stack, map overlay, pack chip), `ios-app-ui.md`
> (per-tab as-built layouts), `ios-app-architecture.md` (module map). Live iOS↔web gaps are
> tracked in `designs/future/ios-web-known-gaps.md`. **This file is ready to archive.**
>
> **Where the build diverged from the plan (so you're not misled):** the shipped briefing
> tabs are **Advisory · Discussion · Cross-Section · Map** (+ PIREPs when permitted), NOT the
> §4.1/§6 "Brief · Cross-section · Skew-T · Map". There is **no single "Brief" scroll tab** and
> **Skew-T is not a peer tab** — it's a detail reached from the cross-section (`selectedTab` maps
> `.skewT → .crossSection`; see `SkewTTabView`/`SkewTDetailView`, `BriefingTab` in
> `ViewModels/BriefingViewModel.swift`). Shared state (`activePoint`/`selectedModel`/`selectedPack`/
> `focusIntent`), `cross_check`/`parameters_used` decode, and the advisory-detail endpoint all
> landed as designed.
>
> _Original goal (for context):_ rethink the iPhone/iPad app's UX flow first (frictionless,
> natural drill-down from basic → expert), then dress it in a visual language inspired by
> Flighty / Strava / Unifi. We map **data + actions → flows → UI affordances → visual skin**.

## How to read this doc

Worked in five passes (each a section below):
1. **Reference DNA** — Flighty / Strava / Unifi decoded into *principles*, not "looks".
2. **Surface inventory** — per screen: what users **view**, **do**, **configure** (the raw material).
3. **Affordance palette + placement rule** — the UI vocabulary and *when* to use each.
4. **Flow mapping** — assign data/actions to affordances; the novice→expert depth ladder. (Replaces the 5-tab structure.)
5. **Parity & sync** — cross-section / Skew-T gap vs web + a mechanism to stop future drift.

Open questions collected at the bottom. Nothing here is decided until moved to a real plan.

---

## 0. Baseline — what the app is today (ground truth, 2026-06-23)

Native SwiftUI (`app/flyfun-weather/`), iOS 26.2+, MVVM + Repository, reuses
FlyFunCommon / RZFlight / RZUtils / RZSkewT. See `ios-app-*` design docs.

**The defining UX fact:** a single briefing is **fragmented across 5 bottom tabs** —
`Advisories · Cross-Section · Map · Digest · PIREPs`. One weather story, five
disconnected screens. This is the primary thing the modernisation should challenge.

### As-built surfaces

| Surface | Pattern today | Notes |
|---|---|---|
| Auth | `LoginView` | Apple / Google / dev (simulator) |
| Flight list | `NavigationSplitView` sidebar | route · date · FL · assessment badge · cache dot; pull-to-refresh; aircraft dropdown |
| Add flight | `.sheet` (`AddFlightView`) | FPL paste, waypoints, date/time, FL slider, duration stepper |
| **Edit flight** | **— none —** | editing only on the website (gap) |
| Briefing select | toolbar **menu** (pack history) | D-N labels + assessment badges + cache dot |
| Briefing | `TabView` (5 tabs) | see below |
| → Advisories | grid of expandable cards | per-model badges; tap header to expand → per-model %, catalog desc |
| → Cross-Section | Canvas + sticky layer controls | model menu, method-group dropdowns (cloud/icing/turb/conv), toggle chips (terrain/ref/temp/stability); tap point → slide-up Skew-T + route graph |
| → Map | MapKit | route polyline + waypoint pins + live aircraft icon; **no metric overlay** |
| → Digest | scroll text | assessment badge, watch items, AI sections |
| → PIREPs | list (gated) | expandable rows, severity bars, hazard icons; reporting sheet during flight window |
| Settings | `.sheet` | website link, privacy, sign-out, delete account |
| PIREP report | `.sheet` (flight window) | Form: altitude/icing/turb/cloud/optional/remarks; offline queue |

### Affordance vocabulary already present
Sheets · bottom tabs · toolbar menus · inline-expand cards · toggle chips ·
conditional slide-up (Skew-T) · segmented-ish severity button rows · banners
(refresh/download progress) · split-view sidebar. **The palette exists — it's just
not organised around flow.** Modernisation is mostly *re-composition*, not new primitives.

---

## 1. Reference DNA — Flighty / Strava / Unifi, decoded

We're not copying screens; we're extracting the *principle that makes each work* and
deciding where it applies to a pilot weather tool.

### Flighty — "dense data, made calm and trustworthy"
- **Hero-first hierarchy.** Always surfaces the *one thing that matters now* huge and
  legible; everything else is one tap down. → Briefing should open on a single hero
  assessment, not a tab bar.
- **One scrolling surface per entity.** A flight is *one* page you scroll, with
  sections — not five tabs. Drill-down opens sheets/detail, you don't *navigate away*.
- **Editorial typography + generous whitespace + dark mode.** Big numbers, restrained
  color, color = meaning (severity), never decoration.
- **Live Activities / Dynamic Island, haptics.** The app reaches out at the right
  moment. → maps onto refresh-ready, in-flight tracking, advisory changes.
- *Applies to:* the whole briefing restructure + the "trustworthy premium" skin.

### Strava — "data as narrative & engagement"
- **The activity detail is a story**, not a dump: hero map, then scrubbable charts,
  then stats, then social. → cross-section/route-graph are our "scrubbable charts".
- **History/logbook has identity.** Past flights are worth revisiting → a real flight
  logbook (ties to the existing `debrief` feature: flown/cancelled, condition tags).
- **Achievements / segments turn numbers into meaning.** → e.g. "you flew through the
  conditions the forecast predicted" post-flight verification; streaks of accurate briefings.
- **Shareable recaps.** → a shareable post-flight / briefing card.
- *Applies to:* logbook, post-flight verification/debrief, shareable summary, return-engagement.

### Unifi — "expert telemetry, masterable not dumbed-down"
- **Dense dashboards that stay legible** via strong grid + consistent card language.
- **Drill-in to full technical depth** without leaving the mental model: tap a device →
  live graphs → per-port detail. The novice sees a clean summary; the expert drills to telemetry.
- **Real-time graphs feel alive.** → freshness, model agreement, live tracking.
- *Applies to:* the expert end of progressive depth — cross-section layers, Skew-T side
  panels, per-model compare, the meteorology a pro pilot wants. The drill-down ladder's bottom.

### Synthesis — the through-line
All three take **intimidating dense data → legible, premium, confidence-inspiring.**
That is exactly WeatherBrief's tension (meteorology × novice→ATPL range). The house
style we already hold (memory): *attention-director not go/no-go verdict*; *progressive
depth, layered disclosure, (i) buttons educate*. The references are how to *execute* that
philosophy on iOS.

---

## 1B. Adaptive strategy — iPhone vs iPad (cross-cutting GOAL)

> Equal-priority goal: an **optimised flow for both** iPhone and iPad, with the **same
> visual style**. iPad users want to *see much more at once*; iPhone users want a clean
> drill-down. This is not a port — it's a deliberate per-device composition.

### Core principle: one IA + one visual language, two spatial compositions
Don't build two apps. Build **container-agnostic modules** (Hero, Watch, Advisories,
Cross-section, Route-graph, Map, Skew-T, Digest, PIREP) and let each device arrange them.

- **iPhone = depth through _time_.** One module at a time; the depth ladder
  (glance → expand → sheet → full-screen) is traversed **sequentially** via push/sheet.
  Scarce space ⇒ progressive disclosure is mandatory.
- **iPad = depth through _space_.** Multiple modules visible at once; the ladder becomes
  **spatial adjacency** (master–detail–inspector). The user "sees more" because rungs sit
  side-by-side, not because content differs. This is Unifi's dashboard vs its mobile drill-down.

### This reframes the tabs question
"Single-scroll vs tabs" is mostly an **iPhone-scarcity** problem. On iPad the answer is
almost certainly a **multi-pane split-view workspace** (already begun — `NavigationSplitView`),
*not* a tab bar. So we can settle the iPhone briefing structure somewhat independently,
knowing iPad expands the same modules into panes.

### Affordance translation (same intent, device-appropriate surface)
| Intent | iPhone | iPad |
|---|---|---|
| pick flight | push from logbook | persistent **sidebar** (col 1) |
| briefing overview | single scroll | **center pane** (col 2) |
| config (layers / model) | half/full **bottom sheet** | trailing **`.inspector`** panel |
| detail (Skew-T, advisory detail) | full-screen **push** | **third column** / detail pane |
| compare models | sequential switch | **side-by-side** simultaneously |
| scrub value | peek sheet | inline + inspector readout |
| heavy viz (cross-section) | full-screen on tap | **always-on center**, never hidden |

### Canonical layouts
- **iPad briefing = 3-column workspace** (Unifi):
  `Flights │ Briefing + cross-section (+ route graph) │ Inspector(layers / Skew-T / advisory detail)`.
  Expert sees cross-section + route graph + Skew-T + compare **at once**.
- **iPhone briefing = drill-through stack** (Flighty): single scroll → full-screen viz →
  bottom-sheet config.

### Implementation discipline (so it stays ONE app)
- Modules are **container-agnostic** views — no assumption about sheet vs panel vs column.
- Drive layout off **`horizontalSizeClass` + width breakpoints**, `NavigationSplitView`,
  `.inspector`, adaptive `LazyVGrid`, `ViewThatFits` — **not** `if iPad` device checks.
- **Shared design tokens** (color / type / spacing / card language) across both; only the
  *arrangement* adapts. Visual identity is identical by construction.
- **iPad is a width _spectrum_, not a point:** Split View, Slide Over, Stage Manager mean
  the center pane can be iPhone-narrow at runtime. Design to breakpoints so a module
  gracefully collapses from "panel" to "sheet" when its container narrows.

---

## 1C. Visual language (skin) [DIRECTION SET 2026-06-23]

**Borrow Flighty's _structure/hierarchy_, keep our own _color identity_** — i.e. NOT
Flighty's dark-first. Match the **web app's light + dark** tone so the two clients feel like
one product. Detailed type/spacing pass deferred, but the palette is fixed now:

| Token | Light | Dark | Use |
|---|---|---|---|
| bg | `#f8f9fa` | `#121218` | page background |
| surface | `#ffffff` | `#1e1e2a` | cards / sheets |
| text | `#1a1a2e` | `#e4e4e8` | primary text |
| text-muted | `#6c757d` | `#9ca3af` | secondary |
| border | `#dee2e6` | `#2d2d3a` | hairlines |
| primary | `#2563eb` | `#60a5fa` | accent / interactive |
| green (OK) | `#198754` | `#34d399` | assessment GREEN |
| amber | `#cc8800` | `#fbbf24` | assessment AMBER |
| red | `#dc3545` | `#f87171` | assessment RED / danger |
| LIFR | `#8e24aa` | `#c084fc` | LIFR category |

- **Both modes ship** (follow system, with manual override — web has `[data-theme]`).
- Severity color = meaning, never decoration (consistent with cockpit constraints + house style).
- Map these to a single SwiftUI **token set** (Color assets / a `Theme` struct) so §1B's
  "shared design tokens" promise is concrete. Source of truth: `web/css/style.css` `:root` /
  `[data-theme="dark"]`.
- The cross-section/Skew-T have their *own* viz themes (`visualization.md`) — separate from
  this app chrome palette; keep that distinction.

**Typography / spacing / motion [SKIN PASS 2026-06-23 — the Flighty premium feel]:**
- **Type:** system **SF Pro** for text (Dynamic Type, standard text styles). **Big, bold hero
  numbers** are the signature — the assessment + "what-changed" reads large and confident. Use
  **tabular / monospaced digits** for all *data* readouts (scrub strip, route-graph values, Skew-T
  side panel, freshness times) so columns align and numbers don't jitter.
- **Hierarchy:** one loud thing per surface (the hero), everything else quiet — generous whitespace,
  restrained weights. Color carries meaning (severity), never decoration; chrome stays neutral
  (text / text-muted / border), primary blue only for interactive.
- **Spacing:** 4/8 pt grid; card padding 16, section spacing 24; consistent rhythm everywhere.
- **Cards / elevation:** `surface` fill, ~12–16 pt corner radius, the §1C `shadow` token (subtle in
  light, soft glow in dark); hairline `border` where shadow reads too heavy.
- **Motion & haptics (where Flighty's polish lives):** purposeful micro-interactions — haptic on
  severity change, refresh complete, scrub snap-to-point; smooth sheet-detent + tab transitions;
  never gratuitous.
- **Iconography:** SF Symbols, consistent weight; severity via color, not icon shape alone.
- **Accessibility:** Dynamic Type + high-contrast honored (cockpit constraint) in both modes.

## 1D. Offline-first (NON-NEGOTIABLE — must survive the redesign)

A pilot with no signal in the cockpit must still reach their briefing. This already works and
**must not regress**:
- `CachingBriefingRepository` multi-tier fallback (online → cached flights.json → per-pack data).
- Logbook: per-flight **cache dot**, non-cached rows **disabled offline**, "serving cached" flag.
- Per-pack **explicit download** with byte-level progress; cached-pack badges in history picker.
- Sign-out blocked while offline (would strand auth).

**Redesign requirement:** offline/cached state is **cross-cutting chrome**, visible in BOTH
the logbook AND the briefing (a global "offline / showing cached @ <time>" affordance), not
just a green dot easily lost in a prettier layout. Treat it as a first-class status, like freshness.

## 1E. Web ↔ iOS parity by construction (cross-cutting GOAL)

> **Goal:** make it *structurally easy* to keep web + iOS consistent — in what a card
> displays and in functionality — so the normal workflow (build analysis in backend → add to
> web (primary) → sync to iOS) becomes mechanical, not archaeological. Web stays primary;
> iOS becomes first-class enough to be **someone's main app**, not a second-class viewer.

### The seam (the one rule that makes this work)
> **"What to display" is shared (backend / generated contract). "How to display it on this
> device" is per-client (mirror-structured).**

A shared *rendering* engine is rejected (§5 — 6–12mo, overkill). So sync is surgical: share the
data + the display *intent*; duplicate only the rendering, and make the duplicate findable.

| Shared (one source of truth) | Per-client (mirror-structured, duplicated) |
|---|---|
| data contracts (schema/OpenAPI) | rendering / drawing code |
| catalogs & registries (advisories, layers, metrics) | layout & navigation |
| presentation shaping (card content, sections, summaries, ordering, severity mapping) | gestures / interaction |
| copy / i18n strings | device-adaptive composition (§1B) |

### Five mechanisms (dependency order)
1. **Schema-first contracts + codegen** — generate Swift `Codable` from the same source as the
   web types. New backend field → both decode for free. Kills the "iOS drops `cross_check` /
   extended sounding fields" bug class at the root (§4.6, §5.2).
2. **Backend catalogs/registries = source of truth for any "list of displayable things."** The
   advisory *catalog* already works this way; extend to the cross-section layer registry (§4.5,
   §5.3) and route-graph metric registry. Add an item once → both clients inherit it.
3. **Shared presentation shaping** (`connectors/views.py` pattern, generalized) — non-trivial
   "what goes in the card / what's a section / how it's summarized" → backend pure functions both
   clients consume, not re-derived in TS *and* Swift. (= the "consistent card" ask.) Already the
   plan for advisory detail (§4.6) and digest decomposition (§4.1).
4. **Structural isomorphism** — Swift modules mirror web module names/folders 1:1 so the render
   counterpart is obvious. Partial today (`Views/CrossSection/Layers/` ↔
   `visualization/cross-section/layers/`); formalize + keep a web↔iOS module mapping table.
5. **Living parity map + sync recipe** — generalize the §5 parity table into a checklist; document
   a "when you add X to web, do Y on iOS" recipe so the sync step is mechanical.

### Realistic stance (don't over-promise)
- **NEW analysis:** design the contract + shaping shared from the start (cheap when greenfield).
- **EXISTING surfaces:** extract shared shaping *opportunistically when we touch them* — web's
  TS managers won't be rewritten wholesale. §4.6 (advisory detail) is the template: touch it,
  add the REST shaping, both clients converge.
- **Always:** mirror structure + update the parity map, even when rendering stays duplicated.
- *Boundary discipline:* "what to show" creeping into the client is how drift starts — keep
  display *intent* on the shared side, device *expression* on the client side.

**This principle already shows up in:** §4.1 (digest decomposition), §4.5 (config from a shared
layer registry), §4.6 (advisory shaping over REST), §5 (cross-section/Skew-T sync). Those are
not separate ideas — they're all this one. Every surface design below should carry a
**"shared vs per-client" split**.

---

## 2. Surface inventory — data · actions · config

> For each surface, enumerate the raw material so we can assign affordances in §4.
> `view` = what they read · `do` = actions taken · `config` = what they tune.
> **(TODO: fill / refine collaboratively — this is the working table.)**

### 2.1 Flight list / logbook
- **view:** route, date/time, FL, overall assessment, freshness/cache state, aircraft
- **do:** select, create, **edit (missing today)**, delete, refresh, duplicate/move (web has it)
- **config:** sort/filter (future/recent/past — cf. `debrief` 3-section list), units region
- *Strava lens:* this is the **logbook**. Future vs flown-history split; identity for past flights.

### 2.2 Create / edit flight
- **view:** parsed route preview, sun/leg summary
- **do:** paste FPL, edit waypoints, set date/time/FL/duration, pick aircraft, save
- **config:** advanced (alternates compute, front detection pref, icing/cloud method prefs)
- *Gap:* native **edit** doesn't exist. Decide: build native vs keep web-only.

### 2.3 Briefing — hero / assessment
- **view:** overall traffic-light + reason, watch items, freshness, "what changed since last refresh"
- **do:** refresh, switch pack (D-N history), start/stop tracking, open detail facets
- **config:** —
- *Flighty lens:* this is the **hero**. Becomes the top of the single briefing surface.

### 2.4 Briefing — advisories
- **view:** per-category severity, per-model split, aggregate detail, catalog explanation, cross-check note
- **do:** expand a category, drill to per-model/per-point detail (cf. MCP `get_advisory_detail`)
- **config:** which categories shown? severity thresholds (pref)
- *Note:* per-model split + cross-check is a **hook**, full reconciliation is deeper — mirror the MCP depth model.

### 2.5 Briefing — cross-section (heaviest config surface)
- **view:** vertical weather along route (≤25 layers), terrain, temp/stability lines, live aircraft
- **do:** tap point → Skew-T, hover/scrub, (web: zoom/pan, compare, windy link)
- **config:** model select · method groups (cloud/icing/turb/conv) · toggle layers · theme/preset · compare models
- *Unifi lens:* the expert drill-down floor. Config is heavy → belongs in a **bottom sheet**, not always-on chrome.

### 2.6 Briefing — Skew-T
- **view:** T/Td/parcel, CAPE/CIN, overlays (cloud/icing/inversion/convective), 14 side-panel variables (web)
- **do:** pick point, (web: pick side-panel vars, toggle overlays, compare models)
- **config:** side-panel variable selection, overlay toggles
- *Gap:* iOS = thin RZSkewT, 0/14 variables. See §5.

### 2.7 Briefing — map
- **view:** route, waypoints, live position; (web: per-segment metric coloring, hazard zones)
- **do:** pan/zoom, tap waypoint → conditions
- **config:** metric overlay select (web has 13), altitude slider
- *Gap:* iOS map is barebones.

### 2.8 Briefing — digest (AI)
- **structure:** `DigestResponse` = `assessment`+`assessment_reason` · `synoptic` ·
  `winds`/`icing`/`turbulence`/`precipitation`/`visibility`/`trend`/`model_agreement`/
  `recommendations` · `watch_items`. Long-range packs (D-N beyond GRIB horizon) use
  `LongRangeDigest` (`outlook`/`outlook_reason` — softer scale, NOT GREEN/AMBER/RED).
- **view:** the above, **decomposed by role** across the Brief (§4.1), not one block.
  Note "Synopsis" (web label) == the `synoptic` field → rendered **last**.
- **do:** read; (future: ask follow-up / drill into an advisory it references)
- **config:** —
- *Handle the `digestPending` state* (digest generates async after refresh) — show
  "Generating summary…" without blocking the rest of the Brief.

### 2.9 PIREP / in-flight
- **view:** PIREP feed, last report, live tracking state
- **do:** file report (tap-only), start/stop flight
- **config:** —
- *Largely fit-for-purpose already; cockpit constraints doc governs.*

---

## 3. Affordance palette + placement rule

The "things to play between." Each with a **when-to-use** keyed on
**frequency × complexity × glanceability**.

| Affordance | Best for | Rule of thumb |
|---|---|---|
| **Hero block (top of scroll)** | the one thing that matters now | exactly one per surface; always visible, no interaction to see it |
| **Single scrolling surface w/ sections** | one entity's full story | replaces multi-tab fragmentation of *one* briefing |
| **Segmented control** | 2–4 peer views, frequent switching, cheap | model select, layout mode; not for >4 |
| **Tab bar (app-level)** | top-level *destinations* (≠ facets of one entity) | reserve for Logbook / Briefing / Map / Profile-style top nav, not briefing internals |
| **Expanding button → menu** | discrete pick from a list, infrequent | pack history, aircraft, theme/preset |
| **Toggle chips (scroll row)** | independent on/off layers, frequent, glanceable | cross-section layer toggles |
| **Bottom sheet — peek detent** | persistent context + quick action | "tap point" summary; mini-controls |
| **Bottom sheet — half detent** | moderate config without losing the viz | cross-section layer/model config over the chart |
| **Bottom sheet — full detent** | heavy config or secondary content | full layer catalog, Skew-T variable picker |
| **Full-screen modal/push** | immersive single task | Skew-T detail, full-screen cross-section, add/edit flight |
| **Inline expand (disclosure)** | progressive detail in a list | advisory card → per-model; PIREP row |
| **Hide behind "Advanced"** | expert-only, rarely touched | thresholds, method overrides, experimental prefs |
| **Live Activity / Dynamic Island** | glance while away/in-flight | refresh-ready, tracking, advisory change |

**Placement heuristic:** *frequent + simple + glanceable* → always-on (hero, chips,
segmented). *Frequent + complex* → half-sheet over the content. *Rare + complex* →
full-sheet / Advanced. *Expert-only* → behind a disclosure so novices never see it.
The progressive-depth ladder = **glance → expand → sheet → full-screen.**

**Device note:** every row above has an iPad equivalent — see §1B's translation table.
The rule of thumb is *iPhone hides via time (sheet/push), iPad reveals via space
(inspector/column)*. Same affordance intent, different surface.

---

## 4. Flow mapping — proposed north star

> The redesign hypothesis. **To debate, not adopt yet.**

### 4.0 App structure — two modes [DECIDED 2026-06-23]

The app has **two distinct modes**, a master→detail push (no app-level tab bar):

```
MODE A — Flight management            MODE B — Briefing viewing
┌─────────────────────────┐   push   ┌──────────────────────────────┐
│ Flight list (logbook)   │  ──────▶ │ Tabbed briefing:             │
│  → create flight        │          │  Brief · Cross-section ·     │
│  → edit flight (native!) │  ◀────── │  Skew-T · Map (PIREPs later) │
│  → select briefing      │   back   │  (the §4.1 hybrid)           │
└─────────────────────────┘          └──────────────────────────────┘
```
- **Native edit-flight is in scope** (mode A) — today it's web-only; that gap closes.
- **iPad (§1B):** mode A = sidebar (col 1); mode B = center + inspector (cols 2–3). The two
  modes are *columns side-by-side* on iPad, *push/pop* on iPhone. Same modules.
- **Map is its own tab**, peer to Cross-section — NOT a segmented toggle inside cross-section.
  Rationale: cross-section's chrome budget is already spent on its **config button** (§4.5);
  don't also crowd it with a map switch. On iPad both are options of the col-2 content selector.

### 4.1 The tabs debate → hybrid (iPhone) [DECISION LEANING: keep tabs, regrouped]

The briefing is **both** a narrative (read digest → advisory → cross-section the first time)
**and** a set of instruments a pilot flips between non-linearly (advisory → cross-section →
Skew-T → back to advisory). Two honest sides:

**For tabs (the instruments view):** O(1) random access to any section, persistent, in the
thumb zone, spatial memory ("Skew-T is always bottom-right"). Canonical iOS for
frequently-switched peer destinations. The investigation loop *is* non-linear → tabs serve it.

**Against tabs (the narrative view):** a flat bar presents everything as equal peers and loses
the natural first-read order; and it always costs bottom real-estate — worst exactly where
space matters most (cross-section).

**Rejected alternative — "button → drop-down nav menu":** strictly worse for *frequent*
switching (+1 tap latency every time, hides destinations, no "you are here", often out of
thumb zone). A top **segmented control** also loses (top-anchored = away from thumb).
Both only win when navigation is *rare* — which it isn't here.

**Synthesis — regroup 5 flat tabs → ~3–4 stronger ones, keeping fast switching where it matters:**
- **"Brief" tab = a Flighty-style scroll** (folds the narrative; encodes read-me-first order).
  **Order DECIDED 2026-06-23:**
  ```
  Brief (single scroll)
  ├─ HERO:      traffic-light + reason + "what changed" + freshness/offline status
  ├─ DIGEST:    AI hazard narrative (collapsible)  ← read first after the light
  ├─ WATCH:     chips for the few things to look at → deep-link to instrument
  ├─ AIRPORTS:  dep / arr condition cards (VFR/MVFR/IFR/LIFR, wind, best rwy)
  ├─ ADVISORIES: category cards → inline expand → "Details" sheet (per-model)
  └─ SYNOPSIS:  big-picture synoptic overview  ← last (context, not quick-read)
  ```
  **Key move — decompose the digest by role** (don't render `DigestResponse` as one block):
  `assessment`+`assessment_reason` → HERO · `watch_items` → WATCH chips · hazard fields
  (`winds`/`icing`/`turbulence`/`precipitation`/`visibility`/`trend`/`model_agreement`/
  `recommendations`) → DIGEST section · `synoptic` ("Synoptic Overview") → **SYNOPSIS, last**
  (it's currently rendered *first* in the web digest — we deliberately move it to the bottom).
- **Heavy instruments stay peer tabs:** **Cross-section** (+ route graph), **Skew-T**, **Map**.
  **PIREPs deferred on iPhone** (iPad / later — §4.0). Fast non-linear access preserved.
- Net: guided first-read **and** instrument-style switching. **iPhone = 4 tabs**
  (Brief · Cross-section · Skew-T · Map); iPad adds PIREPs + compare in the workspace.

**Reclaim the space cost (so the main knock against tabs mostly evaporates):**
- **iOS 26 tab-bar minimize-on-scroll** (verify API `.tabBarMinimizeBehavior`) — bar tucks as
  you scroll into the Brief, returns on scroll-up. Real-estate back in the reading sections.
- **Immersive focus mode for cross-section** (your landscape idea, generalized): cross-section
  is a *wide* artifact (distance × altitude) → **landscape = full-bleed, no chrome**; add
  **tap-to-toggle-chrome** (Photos-style) for portrait. On iPad this is moot — cross-section is
  the always-on center pane (§1B); iPhone landscape-focus reaches toward that same feeling.

**Inside the heavy tabs**, config still lives in **bottom sheets** (half = layers/model,
full = catalog/variables) on iPhone, → **`.inspector` panel** on iPad (§1B). Expert lives
there (Unifi); novice never opens it.

**App-level vs briefing-level:** "tabs" here = *briefing-internal* (iPhone). The app-level
question (Logbook as a separate destination, push-from-logbook vs a top app tab bar) is
separate and still open — see §6 Q1. On iPad both dissolve into the multi-pane workspace anyway.

### 4.2 Progressive-depth ladder (worked example: icing)
1. **Glance** — hero shows AMBER, watch-chip "Icing FL060–080".
2. **Expand** — advisory card: per-model badges + one-line reason.
3. **Sheet** — "Details": per-model %, cross-check note, catalog explanation, (i) educate.
4. **Full-screen** — cross-section with icing layers; tap point → Skew-T with icing overlay + indices.
Same ladder shape for every category. Novice stops at 1–2; pro goes to 4.

### 4.3 Cross-section config flow (the heaviest)
- Default: clean GRAMET-aligned view, **no chrome**.
- Tap **"Layers"** (FAB or toolbar) → **half-sheet**: model segmented control + method-group
  pickers + toggle chips. Viz stays visible above → instant feedback (Unifi drill-in feel).
- **"More layers / Compare / Theme"** → full-sheet (rare/expert).
- Scrub the chart → **peek-sheet** shows values at the cursor point; tap → push Skew-T.

### 4.4 Flight management — logbook · create · edit [DECIDED: simple-first]

**Scope DECIDED: utility logbook (A.1 = a)** — keep the flow simple initially; no achievements /
verification / sharing yet (revisit later).

**Logbook** (mode A; iPad = sidebar, col 1):
- Three sections (reuse `debrief`'s structure): **Future · Recent · Past**.
- Row: route · date/time · FL · assessment badge · cache dot (§1D); tap → briefing.
- Pull-to-refresh; **+** → create. Offline: non-cached rows disabled (§1D).
- Past flights link to their briefing snapshot + the existing **debrief** (flown/cancelled +
  condition tags) if present — **no** new identity/verification/recap surfaces yet.

**Create flight** (sheet / form):
- FPL paste **or** manual waypoints · date/time · cruise FL (slider) · duration · aircraft picker.
- Create **auto-triggers briefing generation** (existing behaviour) → lands on the Brief tab
  (`digestPending` until the digest settles, §2.8).

**Edit flight [DECIDED: native; reuses the create form]:**
- Same form, pre-filled (one form, two modes — create/edit). Resolves §6 Q2.
- **Re-briefing cost confirm:** saving a change to route/time/FL/duration/aircraft (all affect the
  forecast) prompts *"This will regenerate the briefing. Continue?"* then regenerates — never a
  silent recompute. Online-only (mode-A default).

### 4.5 Cross-section configuration panel — the dense surface [needs its own design]

The hardest single surface: "quick, complicated and dense." It must serve a novice who
just wants a sensible default **and** a pro who wants the full method/layer matrix — without
either feeling the other's complexity. Governing idea: **progressive disclosure applied to
config itself.** 90% of users pick a preset and never go deeper.

**Surface:** iPhone = **half-sheet** over the live chart (chart stays visible → instant
feedback, Unifi drill-in); expands to full detent. iPad = **`.inspector`** trailing panel
(persistent). Opened by a **"Layers" pill** in the cross-section chrome (also in landscape focus).
**Model selector is NOT in here [DECIDED]** — it's a **persistent control in the cross-section
chrome** (model-switching is more frequent than layer config; comparing one view across models
is core). **The panel doubles as the LEGEND [DECIDED]:** every row carries the swatch/color that
matches the chart (reusing shared `scales.ts` color fns) — config + legend in one surface, killing
the missing-legend gap.

**Layered structure — compact-by-default** (web's "compact mode": one row per *concern*, not the
full matrix):
```
Cross-section config            (half-sheet ⇄ full ; or iPad inspector)
┌──────────────────────────────────────────────┐
│ PRESET   [GRAMET] [Windy] [ForeFlight] [Custom]│  ← 90% stop here (model lives in chart chrome)
│ ☁ Clouds      ● Soft NWP ▾   ⓘ   ▢swatch      │  ← per-concern row + (i) + chart-matching swatch
│ ❄ Icing       ● Ogimet-NWP ▾ ⓘ   ▢            │
│ 💨 Turbulence  ● CAT (Ri) ▾   ⓘ   ▢            │
│ ⛈ Convection  ● NWP ▾        ⓘ   ▢            │
│ Reference  [Terrain][0°C][Cruise][Inv]…        │  ← independent toggle chips (swatched)
├──────────────── More ▾ ───────────────────────┤
│ cloud STYLE (soft/natural/square) · −10/−20°C  │  ← appearance / expert — rarely touched
│ LCL/LFC/EL · THEME (std/hi-contrast/gramet)    │  ← themes KEPT but BURIED [DECIDED]
│ Compare (iPad only — §4.7)                      │
└──────────────────────────────────────────────┘
```
**Principles / DECIDED:**
- **Preset collapses everything** — sets every method/toggle + theme + cloud style; touching any
  control flips PRESET → "Custom". Novice never opens "More".
- **Compact-by-default:** one row per concern showing the active method (menu to change); full
  method list + cloud *style* live under "More". **Cloud source×style** (6 combos) tamed by
  treating **style as preset-driven appearance** — compact row = on/off + source; style only in "More".
  *(Rendering emulation of the improved natural + square styles is a real renderer task — §5.4.)*
- **Two idioms:** method groups = pick-one method (menu) + on/off; reference lines = toggle chips.
  Mirrors the data model; avoids web checkbox-soup.
- **(i) educates** on every method (house-style teaching → layer-info + legend meaning).
- **Themes kept but buried** under "More" [DECIDED] — distinct from app light/dark (§1C), tied to
  preset by default.
- **Live preview** — never a modal that hides the chart.
- **Persists to SHARED user prefs [DECIDED §1E]:** writes the same `service_toggles` /
  preferred-method prefs the web uses → **syncs web↔iOS** (set icing method on phone → it's on the
  laptop). Shared, not per-device.
- **Parity hook:** the method/layer rows ARE the §5 gap — **data-drive them from the shared layer
  registry** (§5.3/§1E) so adding a layer is one backend row both clients inherit. *This panel and
  the sync strategy are the same problem.*

### 4.6 Advisory detail ladder — the "why is it RED?" surface [needs its own design + BACKEND]

The heart of the product: the **attention-director, not go/no-go verdict** philosophy made
visual. The advisory carries four depth layers (all already curated in `connectors/views.py`,
the *same* shaping MCP + ChatGPT consume):
- **A card:** aggregate status + detail + per-model badges + neutral flags
  `cross_check_present` / `per_model_present`.
- **B generic detail** (`advisory_detail`): per-model {status, detail, affected_pct,
  affected_nm, **cross_check**} + **`parameters_used`** (thresholds that fired) + catalog desc.
- **C convective reconciliation** (`convective_detail`): per-model `assessment_method`,
  `method_counts`, `thermo.peak {cape, el_top_ft, risk_level, waypoint_icao, valid_time, eta}`,
  `nwp.max_cover_pct`. *The "RED under blue sky" story, with peak location + ETA.*
- **D per-point** sounding (route-analyses) — not visualized today; the Rung-4 deep-link target.

**CARDINAL RULE — cross-check is an EXPLAINER, never an ALERT.** Straight from the code's
own `CROSS_CHECK_NOTE` ("display-only context, not a downgrade signal"): render it in
**neutral/info chrome (primary blue / muted), NEVER amber or red.** The UI explains *why it's
RED*; it must never offer "but maybe ignore it." "Models disagree" is context, not a warning.
This is the single most important visual decision in the app.

**The ladder (convective = worked example):**
```
RUNG 1 — Card (Brief scroll / glance)
  ❄︎ Convective  🔴 GFS 🔴 ICON 🟡 ECMWF        "Why it's RED ›"   ← CTA only when AMBER/RED
                                                                    (GREEN cards stay calm)
RUNG 2 — Inline expand (tap card)
  per-model rows: model · status · detail · 30nm/55nm (55%)   (tap a model badge → that model)
  ⓘ explainer (BLUE): "RED on high CAPE — models show little cloud. Here's why ›"
                                                ← pre-empts the doubt BEFORE the pilot thinks "wrong"
RUNG 3 — Detail sheet (iPhone half/full) / inspector panel (iPad §1B)
  WHY THIS GRADE
    Graded by: thermodynamic (CAPE) — GFS/ICON; model scheme quiet
    Peak: CAPE 850 J/kg · tops ~FL270 · near LFMD · ETA 14:20
    Model convective cover: ~0% (blue sky)
    plain-language: "air primed for tall convection even with a clear model cloud field —
      expected timing/resolution lag, not a contradiction. Surfaced so you watch it."
    (i) educate: what's CAPE? · what are 'tops'/EL? · DD vs model scheme?
  WHAT FIRED IT  ← parameters_used vs measured: "HIGH→instant RED; 55% affected (amber@20/red@50)"
RUNG 4 — "Show on cross-section ›"  → deep-link to convective layer at the peak
         (uses cross_check peak distance_nm + valid_time to position cursor)
```

**Generic (non-convective) advisories:** no `convective_detail`, so Rung 3 = per-model detail +
`parameters_used` vs the measured value in `detail` prose ("55% affected; amber threshold 20%")
+ catalog description. Same ladder shape; the convective case is just the richest fill.

**iPhone vs iPad:** iPhone — card → inline expand → "Why?" → detail **sheet** → Rung-4 switches
tab. iPad — card in Brief (col 2) → detail opens in **inspector** (col 3); Rung-4 swaps the
center pane to cross-section focused. (§1B.)

**BACKEND PREREQUISITES (this is not UI-only):**
1. **Add `cross_check` to the iOS `ModelAdvisoryResult` Codable** — present in API, not decoded today.
2. **Surface `parameters_used` + catalog params on iOS** — decoded today, never shown.
3. **Expose Layer C over HTTP.** `convective_detail` is **MCP-only** today → iOS cannot build the
   CAPE-vs-cover story. Add a thin REST endpoint (`…/advisories/{id}/detail`) that returns
   `advisory_detail` + (for convective) `convective_detail` by **reusing the existing pure
   functions in `connectors/views.py`** — ONE source of truth across web/iOS/MCP/ChatGPT.
   *(Same principle as §5.3 shared-registry: share the shaping, don't re-derive per client.)*

### 4.7 Cross-section interaction — the instrument surface [needs its own design]

The densest surface (config already in §4.5; this is the *live behavior*). The web is **four
synchronized views** — cross-section + route-graph (X-aligned, 150px below) + route-map + Skew-T —
sharing `VizRouteData` and syncing via **hover** (crosshair + rich floating tooltip from the
14-entry `tooltip-formatters` registry) and **click** (select point → Skew-T, altitude links back).
The whole model is **mouse-hover-centric** → the core redesign job is reinventing it for touch.

**Three things change for iOS:**

1. **Hover → touch gestures [DECIDED: model (a)].** No hover on a phone.
   - **Tap / drag = scrub** — moves the crosshair to that distance; a 2D cursor (distance ×
     altitude) drives the readout strip (distance/time + every enabled layer the cursor altitude
     intersects). **Tap-to-Skew-T was rejected** (not natural).
   - **"Sounding ›" on the readout strip deep-links to the Skew-T tab** for the current scrub
     point (§4 tab set). No tap/drag ambiguity.
   - Scrub continuously sets the **shared active point** (see below).

   **Shared "active point" state [DECIDED].** A single briefing-level `activePoint`
   (= nearest route point to the scrub cursor) + `selectedModel`, shared across cross-section,
   route-graph, Skew-T, (and map highlight). Scrubbing updates it; "Sounding ›" opens the Skew-T
   *for it*; any **visible** Skew-T reflects it **live** (iPad inspector animates as you scrub;
   iPhone half-detent sheet updates the lower Skew-T as you scrub the cross-section above).
   *Subtlety:* the **readout is continuous** (interpolated at the cursor) but the **Skew-T snaps to
   the nearest route point** (soundings are discrete) — hence `activePoint` = nearest point, not the
   raw cursor x.

2. **Floating tooltip → fixed scrub-readout strip.** A tooltip under the finger is hand-occluded.
   Move the per-layer rows to a **persistent strip above the chart**, updating live while scrubbing.
   *The key touch innovation.* **§1E win:** the `tooltip-formatters` *registry* (which layers, which
   fields, swatch color key) becomes a **shared contract**; iOS renders the same rows → adding a
   layer's readout stays one entry for both platforms. (Render strings stay per-client/i18n.)

3. **Web's 4 layout modes collapse into the tab structure:** map = its own tab (§4.0); "split" =
   native on iPad multi-pane (better as cross-section + **Skew-T** side-by-side than web's
   cross-section+map); **compare = a mode toggle inside the config panel** (§4.5), not a layout.

**Skew-T placement [DECISION UPDATED 2026-06-23: its own tab on iPhone].** Reconsidered from
"drill" → **peer tab**: the Skew-T is already rich/nice and deserves **full-screen** (a half-sheet
cramps it); a tab is far more **discoverable** than a "Sounding ›" button; 4 tabs is HIG-fine.
So **iPhone = 4 tabs: Brief · Cross-section · Skew-T · Map** (PIREPs still deferred).
- **iPhone:** Skew-T is a full-screen peer tab. Defaults to a sensible point (last `activePoint`,
  else departure/worst point); top route strip + **‹ prev/next ›** to pick the point. The shared
  `activePoint` carries context: scrub the cross-section → switch to Skew-T → it shows that point
  (the requested sync). The cross-section readout's **"Sounding ›" deep-links to the Skew-T tab**
  for the scrub point (no longer a sheet).
- **iPad:** unchanged — Skew-T in the **inspector** (col 3), live-linked to the cross-section
  (col 2) as you scrub (the simultaneous both-visible view, where there's room).
- **Trade-off:** iPhone loses the *simultaneous* cross-section+Skew-T view (a cramped half-sheet
  anyway). Clean split instead: **readout strip = quick glance, Skew-T tab = deep study.**
  Reversible to the drill/sheet model if testing disagrees.
- *(Skew-T variable parity — the 0/14 side-panel gap — is §5; this is just where it lives.)*

**Route graph [DECIDED].** X-aligned below the cross-section; **default visible** (collapsible)
on iPhone, always-on in the iPad center pane. Driven by the **shared 12-metric registry**
(§1E mech 2) → Swift Charts renders from the same metric list the web canvas does (all 12 propagate
for free). **iPhone = single metric** (legibility on a ~150px strip) with an "add 2nd" affordance;
**iPad = dual Y-axis** (room). Metric pickers inline below the graph (frequent exploration), not in
a sheet. **One cursor [DECIDED]:** shares the cross-section scrub cursor → the readout strip shows
**both** per-layer values *and* the route-graph metric value(s) at the cursor (unified, not two
tooltips — the payoff of X-alignment).

**Compare mode [DECIDED: iPad-first, deferred on iPhone].** Absent on iOS today; the Unifi "expert"
feature. iPad has room for consensus rendering; **iPhone compare is deferred** (later parity item).
Reuses the consensus-alpha spec + comparable-layer list as shared contract.

**Landscape immersive focus mode** (the §4.1 idea, here in full): rotate → full-bleed cross-section,
chrome hidden, minimal scrub strip + floating Layers pill. Cross-section is inherently *wide*
(distance × altitude) so landscape gives it the right aspect ratio. Tap toggles chrome in portrait
(Photos-style). iPad: moot (always wide; cross-section is the always-on center pane).

**Deep-link focus intent** (from advisory Rung-4, §4.6): a shared **anchor contract** — `{model,
layer_preset, point_index/distance_nm, valid_time, altitude_ft}` — so "Show on cross-section" opens
already scrubbed to the convective peak with the right layer on. Connective tissue across tabs.

**§1E shared vs per-client split for the cross-section:**
| Shared (contract / registry / spec) | Per-client (render) |
|---|---|
| `VizRouteData` + extended sounding-profile contract (incl. the fields iOS drops today) | SwiftUI Canvas drawing |
| layer registry (§4.5), route-graph 12-metric registry, compare layer list + consensus-alpha | gesture model (scrub/tap vs hover/click) |
| `tooltip-formatters` registry (which layers/fields/swatch) | scrub-readout strip vs floating tooltip |
| `scales.ts` color functions, cross-section theme defs | landscape immersive mode (iOS-only) |
| focus-intent anchor contract (deep-link) | iPad inspector-linked Skew-T composition |

### 4.8 Skew-T feature pass [needs PACKAGE work in RZSkewT + app wiring]

Now that Skew-T is a first-class tab (§4.7), spec the "few more features." Audit finding: the
RZSkewT package core is solid (T/Td/parcel/CAPE-CIN/wind barbs/LCL-LFC-EL markers/indices) but
**the app passes cloud/icing/inversion overlay bands that the package never draws**, and the app
**decodes only 6 per-level fields, dropping the extended ones** (RH, θe, lapse rate, Ri, ω, CC,
CLW, ICE, icing indices) — the same §5/§1E drop bug. Per the house rule (*enhance the library, not
the wrapper*), rendering features land **in RZSkewT** (`~/Developer/public/rzskewt`); the
*what-to-show* specs **mirror the web registry** (§1E) so they don't drift.

**Priority tiers:**
- **Tier 0 — App wiring [cheap, foundational].** Decode the extended per-level fields (the §1E
  codegen fix — unblocks side panel + cross-section readout + advisory detail too); convert LCL
  alt→pressure, pass −10/−20°C markers + lifted index. No visible change yet; data now available.
- **Tier 1 — Overlay bands [PACKAGE, highest ROI].** RZSkewT renders the cloud/icing/inversion
  bands it *already receives* + the convective LFC→EL zone. Brings overlay parity. iPhone + iPad.
- **Tier 2 — Interactivity [PACKAGE].** tap/drag → crosshair at nearest pressure level + readout
  (T/Td/DD/RH/wind/HW-XW/θe/icing) + **linked cursor** with the cross-section (live on iPad,
  active-point on iPhone). Touch-native sounding inspect.
- **Tier 3 — Side-panel variables [PACKAGE].** the 14-variable registry (mirrors web
  `VARIABLE_REGISTRY`). **iPhone = single selectable side-variable strip** (its value vs the
  tap-readout = seeing the *profile* shape, e.g. RH vs altitude; consistent w/ route-graph single-
  metric call); **iPad = full dual-axis side panel.**
- **Tier 4 — Compare mode [PACKAGE, iPad-first].** multi-model T/Td overlay; **deferred on iPhone**
  (consistent with §4.7 compare).

**DECIDED:**
- **Skew-T overlays follow the cross-section's active methods** (pick Ogimet-NWP icing once → both
  the cross-section bands and Skew-T icing overlay use it; shared config, no double-configuration).
  Web lists this "method sync" as *future*; we bake it in from the start.
- **iPhone side panel = single variable; iPad = dual-axis full panel.**

**§1E split:** *shared* = `SoundingProfileResponse` contract (incl. dropped fields), overlay list,
14-variable registry, indices set, server-side thermodynamics (all physics stays in Python/MetPy —
client renders only). *Per-client* = RZSkewT Canvas rendering, touch gestures, iPhone-single vs
iPad-dual side panel. **Package work benefits any RZSkewT consumer**, not just this app.

### 4.9 Map tab [needs metric-overlay pass; barebones today]

The map earns its tab by being **geographic** — *where* along the route a hazard sits, on real
terrain — complementary to the cross-section's distance×altitude view (used less often, so keep it
focused, not gold-plated). Today: route line + pins + live aircraft. The web (Leaflet) has the real
set; iOS (MapKit) is a **parallel renderer**, not a port.

**Pass:**
- **Tier 1 — Segment metric overlay [core upgrade].** Color + width the route segments by a chosen
  metric (per-segment MapKit polylines), driven by the **shared `MAP_METRICS` registry +
  `scales.ts` color fns** (§1E — all 13 metrics propagate free). One metric drives *both* color and
  width (`getColor`/`getWidth`) — e.g. low ceiling renders thick = dangerous.
- **Tier 2 — Altitude slider** for level-dependent metrics (icing/CAT/SFIP/cloud/temp at FL;
  0→ceiling, FL labels, debounced) — shown only when the metric is alt-dependent.
- **Tier 3 — Waypoint tap → conditions callout** (VFR/MVFR, wind, ceiling, best rwy) — reuses
  airport-conditions data.
- **Legend:** compact floating gradient chip for the active metric. **Live aircraft** + marker: keep.

**DECIDED:**
- **iPhone = single map metric** (color+width from one); **iPad = separate color + width metrics**
  (web `mapColorMetric` + `mapWidthMetric`). Same single-vs-dual logic as the route graph.
- **Native MapKit dark mode** (follows app light/dark §1C) — *not* the web CartoDB tile-swap.
- **Sync via shared `activePoint`:** tap waypoint/segment → sets `activePoint` → cross-section/Skew-T
  reflect on switch. The §4.6/§4.7 **focus intent** extends to the map: `{metric, altitude,
  highlight point}` so "show convective" opens the map on the right metric + level.
- **Scope:** overlay + slider + legend + waypoint conditions. **SIGMET/hazard-zone shading = later.**

**§1E split:** *shared* = `MAP_METRICS` registry (getValue/getColor/getWidth), `scales.ts` color
fns, `computeSegmentStyles` logic, alt-dependent helpers. *Per-client* = MapKit polyline/annotation
rendering, native dark mode, slider/legend/callout UI.

### 4.10 Connective chrome — making 4 tabs feel like one briefing

Answers the original worry (tabs fragment one briefing). **Core idea: a briefing is ONE object with
shared state; the tabs are views onto it.** Four shared-state objects stitch them together:
- **`selectedPack`** — which D-N briefing is shown.
- **`selectedModel` [DECIDED: shared across the 3 instrument tabs]** — one model for cross-section +
  Skew-T + map (switch once, all follow; matches web's single viz model). Brief still shows
  all-models advisories.
- **`activePoint`** — scrub/selected route point (§4.7).
- **`focusIntent`** — deep-link payload `{model, layer/metric, point, altitude, valid_time}` (§4.6/§4.7/§4.9).

**Persistent top header** (above the bottom tab bar → stays across all 4 tabs):
```
┌─ BRIEFING HEADER (persistent: Brief·Cross-section·Skew-T·Map) ────────┐
│ LFAT → LFMD · 15 Mar · FL080        [pack D-1 ▾] [↻ refresh] [▶ Track]│
│ ⓘ fresh 2h ago · GFS 12Z…    ·    ☁︎ showing cached @14:20 (offline)  │
├──────────── SSE refresh banner (stage · %, when active) ─────────────┤
│                        (tab content)                                  │
└──────────── tab bar: Brief · Cross-section · Skew-T · Map ───────────┘
```
- **Identity** route·date·FL · **Freshness chip** ("GFS 12Z, ECMWF 00Z", expands to per-source (i) —
  `freshness-markers`/`data-status` backend).
- **Offline/cached status (§1D, FIRST-CLASS)** — global "showing cached @time (offline)" + per-pack
  download button live here, not buried.
- **Pack selector** — D-N history menu (assessment badge + cache dot); iPhone = header menu,
  iPad = flights sidebar (col 1) doubles as pack history.
- **Refresh + SSE** — gated by freshness (#167); SSE streams stage/% to the banner; on completion
  computes the **"what changed" delta** (`compute_refresh_delta`) feeding the Brief hero (§4.1).
- **Tracking** — start/stop (flight window) → live aircraft on cross-section + map.
- *Model selector control* itself lives in the instrument chrome (§4.5) but drives this shared state.

**Connective deep-link routing** (the tissue) — one `focusIntent`, consumed by the target tab:
- Brief advisory "Show on cross-section / map" → set `focusIntent` → switch tab → apply (layer/metric
  on, scrubbed to peak point/altitude/time). · Cross-section "Sounding ›" → Skew-T tab for
  `activePoint`. · Watch chip → its instrument. · Tap a waypoint anywhere → `activePoint`.
- **iPad:** deep-links drive the *adjacent pane* (cross-section col 2 → Skew-T inspector) — panes
  just update, **no navigation at all**. This is why the iPad workspace feels more cohesive than tabs.

**§1E split:** *shared* = pack metadata (D-N, assessment, init times), SSE refresh protocol/stages,
`compute_refresh_delta`, freshness-marker computation. *Per-client* = header layout, SSE banner UI,
offline indicator, native nav chrome.

---

## 5. Cross-section / Skew-T parity & sync (the "keep in sync going forward" ask)

### 5.1 Parity vs web — SNAPSHOT 2026-06-23, MOSTLY CLOSED SINCE
> **Do not read this table as current.** Phases 1/3/4/6 closed most of it. As of 2026-08-15
> the app ships: square **and** re-ported natural cloud bands (`Layers/CloudBandsNatural.swift`),
> LCL/LFC/EL, the route graph (`Views/RouteGraph/`, Swift Charts), the map metric overlay
> (`Views/Map/MapMetrics.swift`), cross-section themes + presets + legend
> (`CrossSectionTheme.swift` / `Layers/CrossSectionPresets.swift`), and the Skew-T side-panel
> variable set (`SkewTVariableCatalog.swift`). **Still genuinely absent** (deferred by design,
> §4.7/§4.8): multi-model **compare mode**, and the IENG/SLD, E-shear, fronts and
> night/obscuration cross-section layers (`CrossSectionPresets` keeps e.g. `sld-bands` as a
> parity id and drops it at apply time). Track those in `designs/future/ios-web-known-gaps.md`.

| Subsystem | iOS coverage (2026-06-23) | Missing then |
|---|---|---|
| Cross-section layers | 16/25 (~64%) | **square clouds (missing) + natural needs re-port to the improved algo (§5.4)**, IENG/SLD icing, E-shear turb, fronts, night/obscuration, current-conditions |
| Temp/stability lines | 3/6 | LCL, LFC, EL (**server data already present — cheap win**) |
| Compare mode | ✗ | multi-model overlay (web-only) |
| Route graph | ✗ | 12 scalar metrics (web-only) |
| Themes / presets / legend | ✗ | GRAMET-only on iOS |
| Map metric overlay | ✗ | web has 13 metrics; iOS = route+pins |
| Skew-T side-panel variables | 0/14 | RZSkewT shows only T/Td/wind |
| Skew-T overlays | 3/5 | convective zone, compare |

### 5.2 Why it drifts
Both clients consume the **same server JSON**, but **rendering is 100% duplicated**
(TS vs Swift). A new web layer = hand-port to Swift + App Store cycle. **No automation.**
Worse: iOS `SoundingProfileLevel` decode **drops** the extended fields (RH/θe/Ri/icing
indices…) the web uses for side panels — *the data already arrives and is thrown away.*
**[FIXED by Phase 0 / #286 — the extended fields decode and feed `SkewTVariableCatalog`;
`cross_check` and `parameters_used` reach the advisory detail view.]** The *structural*
anti-drift work (schema codegen, backend-served layer/metric registries) was **not** done —
rendering is still hand-ported, so the drift mechanism below is still live.

### 5.3 Sync strategy options (decide later)
- **A — Shared schema → codegen (low effort, high leverage).** One source-of-truth schema
  for `VizRouteData` + `SoundingProfileLevel`; generate Swift Codable (quicktype/openapi).
  New fields auto-reach iOS. **First step regardless of UI direction.** Also: just *decode*
  the extended sounding fields iOS already receives.
- **B — Shared rendering engine (high effort).** Skia/WASM core both platforms call. Real
  single-source-of-truth for rendering; 6–12mo. Probably overkill.
- **C — WebView for the heavy viz (medium).** Native shell + offline-cached web cross-section/
  Skew-T in a `WKWebView`. Full feature set instantly, zero render duplication; cost = mobile
  webview feel + offline complexity. Tension with the "premium native" goal.
- **Cheap wins now (independent of A/B/C):** LCL/LFC/EL lines, decode extended sounding fields,
  Skew-T overlay/variable subset. Documented so the next person sees the gaps.

**Working stance:** do **A** (schema/codegen + decode dropped fields) regardless; decide
B vs C only for the deep-viz parity question after the UX north star settles.

### 5.4 Cloud rendering styles — emulate the web's improved natural + square [DONE — Phase 1 / #287]
> Shipped in `Views/CrossSection/Layers/CloudBandsNatural.swift` (natural + square in one
> port, web layer ids `square-cloud-bands` / `square-nwp-cloud-bands` preserved).

The web's `cloud-bands-factory` got materially better — **natural** (flat-bottom puffs, bumpy
quadratic-Bézier tops, coverage as *horizontal fill-fraction*: SCT gaps / BKN touching / OVC blanket,
deterministic per-band hash for stable shapes, global-x anchoring for coherent tiling) and **square**
(solid cells, opacity from cover%) both look good now. iOS today has **soft + an older natural** and
is **missing square entirely**.
- **Action:** port both styles into the iOS `CrossSectionRenderer` so the app matches the web look;
  re-port natural to the improved algorithm. (Beyond just "soft works" — this is render *quality*.)
- **§1E split:** the rendering knobs are a **shared spec** — `DEFAULT_NATURAL_CONFIG` (fill-fraction
  per coverage class, puff/hump width, jitter, min-band-width, feather) + the square
  opacity-from-cover% mapping — so clouds look *identical* across web/iOS; only the SwiftUI Canvas
  drawing is per-client. Mirror the same `{source, style}` factory shape in Swift.
- Style surfaces as the §4.5 "More" appearance control (preset default: soft for GRAMET, square for
  ForeFlight); §5.4 is the *rendering* behind that control.

---

## 6. Open questions / decisions to make

1. **Briefing-internal tabs (iPhone):** DECIDED (§4.1) — keep tabs, set =
   iPhone = **Brief · Cross-section · Skew-T · Map** (4 tabs; Skew-T promoted to its own tab
   2026-06-23; PIREPs + compare deferred on iPhone → iPad/later); Map is its own tab (§4.0);
   minimize-on-scroll + landscape focus mode for cross-section. *(verify iOS 26
   `.tabBarMinimizeBehavior` API.)*
1b. **App-level nav:** DECIDED (§4.0) — two modes, master→detail push (logbook → tabbed
   briefing), no app-level tab bar. iPad = columns side-by-side.
2. **Native edit-flight:** DECIDED (§4.0/§4.4) — native; **reuses the create form** (pre-filled);
   saving a forecast-affecting change shows a **re-briefing cost confirm**, then regenerates.
3. **Heavy viz:** DECIDED — **native** (native cross-section/Skew-T/map designed throughout),
   kept in sync via §1E shared contracts/registries + RZSkewT package work (§4.7–§4.9). WebView
   rejected (fights the premium-native goal).
4. **Engagement scope:** DECIDED — **(a) simple utility logbook** (future/recent/past + briefing
   snapshots + existing debrief). Achievements / verification / sharing deferred. (§4.4)
5. **iPad workspace:** DECIDED — **3 panes for the briefing** (flights │ cross-section+route-graph │
   inspector), **2 panes elsewhere** (logbook │ detail); **inspector demotes to a sheet below
   compact width** (Stage Manager / narrow Split View). (§1B)
6. **Visual skin:** DECIDED (§1C) — light+dark web palette + the typography/spacing/motion pass
   (SF Pro, big bold hero numbers, tabular data digits, 4/8 grid, purposeful haptics). Flighty
   hierarchy, our color identity.
7. **In-flight mode:** DECIDED — **(a) status quo**: tracking stays inside existing tabs (live
   aircraft on cross-section + map); no dedicated full-screen mode or Live Activity for now.

---

## 7. Working method / next steps

- This doc is the shared canvas; iterate section by section.
- Suggested order: lock §1 principles → fill §2 inventory with real user priorities →
  agree §3 placement rule → debate §4 north star → then §6 decisions → then a skin pass →
  then carve into implementable plans (each its own `designs/` plan + GH issues).
- **Every surface design carries a §1E "shared vs per-client" split** (what's a backend
  contract/registry/shaping vs what's iOS render) and notes any parity-map update.
- **Foundational first step = Phase 0 (§8):** iOS-only — decode the extended sounding fields +
  advisory `cross_check` the server *already sends* (the §5.2 drop bug). **No backend.** Unblocks
  the Skew-T side panel, cross-section readout, and advisory detail at once. (Schema codegen /
  backend-served registries are a *later* anti-drift refinement, not a blocker.)

### References
- `ios-app-overview.md`, `ios-app-ui.md`, `ios-app-architecture.md`, `ios-app-data-models.md`
- `visualization.md`, `skewt-canvas.md`, `route-graph.md`, `briefing-sidebar.md` (web parity)
- `advisories.md`, `digest.md`, `debrief.md` (feature depth to surface)
- Memory: *progressive depth for all expertise*, *attention-director not go/no-go*

---

## 8. Implementation sequencing & tracking

**Model: supervised autonomy in vertical chunks.** Not "one agent loose until done" — native-UI
*feel* can't be agent-self-verified, and there are cross-stack/cross-repo seams. Each phase is a
runnable slice an agent can largely implement, ending in a human checkpoint (build + sim +
screenshots + eyeball feel + `/code-review`). The **web implementation is the porting reference**
for most surfaces (high success rate — the agent translates, it doesn't invent).

### Phases (dependency-ordered)

| # | Chunk | Backend? | Deps | Definition of done |
|---|---|:---:|---|---|
| **0** | **Data plumbing (iOS-only)** — decode dropped `SoundingProfileLevel` extended fields; add `cross_check` to `ModelAdvisoryResult`; surface `parameters_used` | none | — | fixture-JSON decode tests pass; fields reach the view layer |
| **1** | **Cross-section render parity** — cloud styles (§5.4 natural re-port + square), missing layers (scoped), LCL/LFC/EL lines | none | 0 | matches web for a reference flight (screenshot compare) |
| **2** | **Backbone** — 4-tab restructure + shared state (`activePoint`/`selectedModel`/`focusIntent`/`selectedPack`) + connective header (§4.10) + skin tokens (§1C) | none | 0 | 4 tabs navigate, shared state wired, header persists, tokens applied; builds+runs |
| **3** | **Cross-section interaction** — scrub + unified readout strip, config sheet (§4.5), route graph (Swift Charts §4.7), landscape focus | none | 1,2 | scrub + "Sounding ›" deep-link + config all work on sim |
| **4** | **Skew-T pass** — RZSkewT package: overlay-band render, interactivity, side panel (iPhone single / iPad dual); overlays-follow-cross-section (§4.8) | none | 0,2 | Skew-T tab matches web overlays + variable(s); RZSkewT PR merged + version bumped |
| **5** | **Brief + advisory detail** — digest decomposition + hero/watch/airports (§4.1), advisory ladder (§4.6); **+ the one backend endpoint** (`convective_detail` over REST, reusing `connectors/views.py`) | **yes (small)** | 0,2 | "why it's RED" story renders; endpoint has tests |
| **6** | **Map tab** — segment metric overlay + altitude slider + legend + waypoint conditions (§4.9) | none | 0,2 | matches web map metrics |
| **7** | **Flight management** — logbook (future/recent/past), create, native edit + re-briefing confirm (§4.4) | none | 2 | create→briefing; edit→regenerate works |

- **Only backend touch in the whole plan: Phase 5's one small REST endpoint** (afternoon-sized,
  reuses existing pure functions). Everything else is iOS app + the RZSkewT package.
- **Most autonomy-friendly:** 0, 1, 6, 7 (testable / web-reference-matchable). **Supervise closest:**
  2 (re-architects nav). Phase 1 is nav-independent → can run in parallel with 2.

### Tracking — hybrid (doc = design, GitHub = execution)

Don't duplicate the design into issues. **This doc stays the design of record** (the why, the §1E
splits, parity); **GitHub tracks execution** and references doc §s.

**Created 2026-06-23:** epic `roznet/flyfun-weather#285`; phases #286 (P0) · #287 (P1) · #288 (P2) ·
#289 (P3) · #290 (P4) · #291 (P5) · #292 (P6) · #293 (P7); RZSkewT package `roznet/rzskewt#1` (↔ #290).
- **1 epic / tracking issue** ("iOS app modernisation") — links this doc + the phase checklist.
- **1 issue per phase (0–7)** — scope + DoD + link to the relevant §; task-level **checklists inside**
  each; promote a checkbox to its own sub-issue only when chunky (RZSkewT package work, Phase-5 endpoint).
- Project conventions: `Addresses #N` in PR **and** commit (close-on-deploy); `/code-review` per chunk.
