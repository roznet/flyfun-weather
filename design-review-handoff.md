# Handoff: UX / design review of the FlyFun Weather web app

**Date:** 2026-11-05
**From:** a cloud session (repo `roznet/flyfun-weather`, `main` at `0ca1b06`). It ran in the wrong environment, so this file passes the findings to the main project.
**For:** the coordinator agent. Turn this into issues or plans as you see fit. Nothing was changed in the repo: no code edits, no commits.

---

## 1. What the owner asked for

1. Find products outside aviation that share FlyFun's shape and pick out the best UX and design patterns to learn from. The shape: a list of items whose status changes over time, a dense briefing per item, maps, settings, and uncertainty across several models.
2. The owner then clarified that the aim is **function and understanding, not looks for their own sake**. Pilots must still be able to dig down to the smallest detail. Two things are already attempted but feel clumsy: the advisory → cross-section highlight jump, and the cross-section lens/focus presets (icing, convection…). The owner asked for a hands-on Playwright review with specific advice.

## 2. How the review was done (and its limits)

- Prod (`weather.flyfun.aero`) was blocked by the container's proxy, so the web frontend was built locally (`npm install && npm run build` in `web/`). It was served statically with `python3 -m http.server 8765` from `web/`, and every `/api/*` and `/auth/me` call was mocked in Playwright.
- **Fixture:** `app/fixtures/showcase/showcase.json`, a real prod briefing used for the iOS showcase.
  - Flight `egtf_big_rem_sapre_lsgs-2026-10-12-8bf2`: EGTF → BIG → REM → SAPRE → LSGS, SR22T, cruise 8000 ft, D-1, overall AMBER.
  - Advisories: 2 RED (VFR Feasibility, Airport Weather), 1 AMBER (Sun), 15 GREEN, 2 unavailable (FIKI Icing, Icing Escape).
  - The browser clock was frozen at `2026-10-11T12:30Z` so the briefing counts as upcoming.
- An earlier pass used `web/tests/fixtures/egtf_eglf` (heavily trimmed: 4 route points, 5 levels). That was less useful, but it produced one finding (the departure banner in 10e).
- **Limits. Please re-check these on a real devserver with real data (`scripts/ops/import_flight.py`):**
  - No map background (tile servers blocked).
  - The charts only had ECMWF from the fixture, so Compare mode was not judged on real model disagreement.
  - The synopsis rendered greyed out with a "written for profile Default" banner. "Checking for updates…" stayed on in the rail. Both are probably caused by the mocks (`/api/user/preferences` returned `{}`).
  - The Skew-T was blank for points that have no sounding profile in the fixture (only points 0, 3, 23, 43 and 47 have one).

### Harness to reproduce

Save as `mock2.js`. It needs the global `playwright` package (`NODE_PATH=$(npm root -g) node script.js`):

```js
const fs=require('fs');
const S=JSON.parse(fs.readFileSync('<repo>/app/fixtures/showcase/showcase.json','utf8'));
const FID=S.flight.id, TS=S.pack.fetch_timestamp;
const unmatched=new Set();
const USER={id:'showcase',email:'pilot@example.com',name:'Pilot',approved:true,is_admin:false,setup_completed:true,llm_digest_enabled:true,synoptic_forecast_map_enabled:true,units_region:'eu'};
async function mock(page,extra){
  await page.clock.install({time:new Date('2026-10-11T12:30:00Z')});
  await page.route(/\/auth\/me/, r=>r.fulfill({json:USER}));
  await page.route(/tile|openstreetmap|cartocdn|arcgis|basemaps/, r=>r.abort());
  await page.route(/\/api\//, route=>{
    const u=new URL(route.request().url()); const p=decodeURIComponent(u.pathname); const j=o=>route.fulfill({json:o});
    if(extra){const r=extra(p,u,route);if(r!==undefined)return r;}
    if(p===`/api/flights/${FID}`) return j(S.flight);
    if(p===`/api/flights`) return j([S.flight]);
    if(p.endsWith('/packs/freshness')) return j({is_fresh:true,reason:'fixture'});
    if(p===`/api/flights/${FID}/packs`) return j([S.pack]);
    if(p===`/api/flights/${FID}/packs/${TS}`) return j(S.pack);
    if(p.endsWith('/snapshot')) return j(S.snapshot);
    if(p.endsWith('/route-analyses')) return j(S.route_analyses);
    let m=p.match(/\/advisories\/([a-z_]+)\/detail$/)||p.match(/\/advisories\/([a-z_]+)$/);
    if(m && S.advisory_details[m[1]]) return j(S.advisory_details[m[1]]);
    if(p.endsWith('/advisories')) return j(S.advisories);
    if(p.endsWith('/elevation')) return j(S.elevation);
    if(p.endsWith('/digest/json')) return j(S.digest);
    m=p.match(/sounding-profile\/(\d+)\//); if(m){const sp=S.sounding_profiles[m[1]]; return sp?j(sp):route.fulfill({status:404,json:{detail:'none'}});}
    if(p.endsWith('/advisories/catalog')) return j(S.advisories.catalog);
    if(p==='/api/user/profiles'||p==='/api/pireps'||p==='/api/aircraft'||p==='/api/user/aircraft') return j([]);
    if(p==='/api/user/preferences') return j({});
    if(p.endsWith('/seen')) return j({ok:true});
    if(p.endsWith('/refresh/status')) return j({status:'idle'});
    if(p==='/api/messages/status'||p==='/api/donations/nudge') return j({});
    if(p==='/api/models') return j([{key:'ecmwf',name:'ECMWF IFS',default:true,max_days:10},{key:'gfs',name:'GFS',default:true,max_days:16},{key:'icon',name:'ICON',default:true,max_days:7}]);
    if(p==='/api/models/config') return j({max_booking_lead_days:14,forecast_horizon_days:7});
    unmatched.add(route.request().method()+' '+p);
    return route.fulfill({status:404,json:{detail:'mock'}});
  });
}
module.exports={mock,FID,unmatched,S};
```

Then `page.goto('http://127.0.0.1:8765/briefing.html?flight='+FID)`, wait about 5 s, and take screenshots. Unmocked endpoints return 404: `route-fronts` is intentional (the page handles it), and `altitude-table` was left unmocked. Two gotchas: `/api/flights/{id}/live` must return 404, not `null` (a `null` crashes `loadLive`), and so must `route-fronts`, not `[]` (an empty array crashes `buildFronts`). Those are mock artifacts, but they also show those two code paths don't handle an empty body defensively.

A useful follow-up: make this a reusable design-review harness (a `/run`-style script or a Playwright project that is not a test), so future UI reviews can run without a backend.

---

## 3. Inspiration: products that share FlyFun's shape (from the first answer)

| Product | Shared shape | Lesson for FlyFun |
|---|---|---|
| **Flighty** | List of upcoming flights whose status changes | The list should answer "what got worse since I last looked": grade trend across refreshes, a "changed" badge, a countdown |
| **Oura / Whoop** | One score made from many inputs | Verdict + contributors sorted by severity, each one line, one tap to detail; greens collapsed but counted |
| **Stripe Dashboard** | Dense object page | Same order every time: status header → key facts → event timeline → raw data. Make briefing *history* a timeline ("D-3 amber convective → D-1 green, models agree") |
| **Sentry / Datadog** | Alert → evidence | Clicking an advisory should drive every view to its evidence (layer, location, time, Skew-T point); crosshair synced across all views |
| **Windy / TradingView** | Many layers, one time axis | Views named after questions instead of many toggles; one time scrubber; saved layouts |
| **Komoot / Strava route builder** | Map + profile | A strip under map/profile coloured by worst hazard per segment (already exists as the Highlight bar, see 4.5) |
| **PredictWind departure planner / Google Flights date grid** | "When should I go" | Grid of slot × airport/sector coloured PROMISING/MARGINAL/UNLIKELY for area outlook and timing scenarios |
| **FiveThirtyEight forecasts / Hopper / NHC cone** | Honest uncertainty | Draw the spread, never average it away. Model × advisory matrix like GitHub CI checks |
| **Apple Weather (Dark Sky lineage)** | Headline + cards | A time-anchored one-sentence headline as the biggest text (nutshell / live highlight); "about this metric" explanations in place |
| **Linear / Raycast / VS Code settings** | Calm density, expert settings | "Modified" markers, per-setting reset, search, live preview of a threshold's effect |
| **Vercel / Perplexity** | Long-running generation | Show the ~2 min briefing build as visible steps |

Visual principles:
- Colour means severity, nothing else.
- Tabular numerals and three or four text sizes.
- Three levels of disclosure everywhere: headline → contributors → evidence.
- Dark mode designed in from the start, not inverted.
- Don't copy the reassuring softness of consumer apps: amber and red must stay unmissable.
- Share components between web and iOS, given the existing `sync-ios-web` drift skill.

---

## 4. Findings from the hands-on review (ranked by impact on understanding)

### What already works (keep it)
- The advisory 📈 jump: it scrolls to the cross-section, sets Lens = the matching preset, turns on Highlight, and draws a red/amber/green extent bar along the distance axis.
- The advisory info modal: a plain-language definition plus a table of the actual threshold parameters.
- Full Details: per-model badges on each card, and per-model rows in the Departure/Arrival boxes.
- Hover sync between cross-section, route graph and map.
- Settings: per-advisory "Advanced (n)" + Reset.

### 4.1 The page opens with controls, not the decision
- At 1440 px there are about 12 controls above the verdict: Refresh, Feedback, download, email, help, share, Auto-refresh, the Notifications select (plus a hint), Private, and the 👍/👎 "Was this summary accurate?". Then the tour banner. Then the AMBER sentence.
- At phone width the rail stacks first: header, detail toggle, AMBER chip, watch list, 12 section-nav items, briefing select. Then the toolbar. The verdict sentence is about 2 screens down.
- **Proposal:** put flight settings (notify, private, auto-refresh, share, export, email) in one "Flight ⋯" menu or the rail footer. Move 👍/👎 next to the digest it rates. Below 980 px, put the verdict before the rail. `designs/briefing-sidebar.md` already orders a focused section first on narrow screens, so there's a precedent.

### 4.2 Advisories are grouped by evaluator, not by problem
- Both REDs come from one cause: low stratus at EGTF. GFS's AMBER on IFR Feasibility and VMC at Cruise is that same cloud again.
- For a "VFR + IFR capable" aircraft, with a verdict saying "IFR flight is workable", the two red cards look worse than the verdict.
- **Proposal:** a decision header with the verdict plus a one-line **IFR: workable / VFR: no** answer. Below it, issues grouped by place or phase of flight, each listing the advisories it drives, e.g. "Departure EGTF: LIFR stratus, OVC in climb-out → VFR Feasibility R, Airport Weather R, IFR Feasibility (GFS only) A".

### 4.3 "Unknown" is filed under "ALL CLEAR" (safety-relevant)
- FIKI Icing and Icing Escape (non-FIKI) render as "? No data" cards inside the ALL CLEAR grid.
- The rail lists the same two under "What to watch" with no status dot and no explanation.
- **Proposal:** a separate grey "Not assessed" tier with the reason (disabled in profile / no data from model / not applicable to this aircraft). Never show it under "clear". Settings show FIKI Icing unchecked by default, so for FIKI the reason may be the profile; Icing Escape is enabled and still has no data. Check both.

### 4.4 Green takes the space and the model-disagreement signal is hidden
- 15 green cards take most of the advisory block (about 2 screens on a phone).
- "15 GREEN (3 MIXED)" is cryptic. "Mixed" means green overall with a non-green model, which is the most interesting signal, but the per-model badges only appear in Full Details.
- **Proposal:** show greens as compact one-line rows, and give every card a small three-dot model strip even in Compact.

### 4.5 The evidence jump loses its context
- The trigger is a small 📈 emoji. Airport Weather (`flight_category`) and Sun have no jump button at all.
- After the jump the card is off-screen and the only context is an italic caption ("VMC picture: clouds & obscuration vs cruise and airports"). There's no "Showing: VFR Feasibility RED, 142 nm IMC, OVC in climb-out · ← back to advisories".
- Jumping from VMC at Cruise and then clicking **Compare** opened Compare on layer **Ogimet-DD** (an icing method). The advisory context was dropped.
- Under the Icing lens, the hover tooltip showed **Sun** data (elevation and azimuth) and no icing, temperature or freezing level values.
- **Proposal:** make "evidence mode" a real state. Keep a context bar pinned above the chart with the advisory name, the grade, per-model dots, the extent, ‹ › to step through non-green advisories, and an exit. Mark the worst point or segment on the chart. Make the tooltip show values for the active lens. Keep the lens and advisory across X-Section / Compare / Split / Map.

### 4.6 An empty chart doesn't say whether it's clear or broken
- The Icing lens on ECMWF draws nothing and gives no message.
- With the Icing lens, the route graph (Freezing Level / Ceiling NWP) drew empty axes from -0.2 to 1.2 "ft" with no line. This could be the fixture, but either way the UI can't tell "no data" from "zero".
- **Proposal:** an explicit message on the chart: "No icing at any level along the route (ECMWF)" / "Freezing level: no data for this model".

### 4.7 The cross-section's look and controls
- The Standard theme fills the sky with saturated mid blue, so clouds are pale blue/grey blocks on blue (low contrast). In dark mode the panel stays bright blue (glaring).
- Six controls sit above the chart: mode tabs, Model, Theme (+ 👁), Lens Focus, Emulate. Then seven layer pills. "Theme", "Lens Focus" and "Emulate" are three overlapping ideas.
- **Proposal:** a neutral sky by default, so colour is only spent on weather and hazard, plus a proper dark variant. Make Lens the main control as visible chips (Clouds · Icing · Convective · Turbulence · VFR · IFR, plus Basic/Learn). Move Theme and Emulate into a ⚙ popover. Keep the layer pills and their per-layer method menus for experts.

### 4.8 The best explanation sits at the bottom
- The digest's **Specific Concerns** and **Watch Items** are the clearest reasoning in the app (e.g. the 45 dBZ echo SE of EGTF; observed LIFR at EGVO/EGKK/EGKB/EGUB vs model MVFR). They render after the cross-section, Skew-T, a 22-row METAR/TAF table and alternates, and only in Full Details.
- The section order is grouped by data type (Explore → Observations → Discussion → Detail) and is locked by `tests/unit/briefing-section-order.test.ts`.
- **Proposal:** order the page for a decision: verdict → issues + watch items → evidence (cross-section, Skew-T) → verification (METAR/TAF, alternates) → deep detail (sounding analysis, model comparison). Watch items could become a checklist tied to the live layer. This changes `NAV_GROUPS`, so it is a design decision for the owner.

### 4.9 Tables hide their headline
- METAR/TAF: a full-width orange bar says "Observation/model conflicts detected" but not where. The key fact (four airports observed LIFR vs model MVFR near departure) has to be found row by row. The wind columns are all "G" and take half the width.
- **Proposal:** one summary line naming the conflicts, rows that agree collapsed by default, and wind columns hidden when they're all green.
- Alternates: good content, but heavy with disclaimer text. Consider one line plus an info icon.

### 4.10 Copy and data bugs (concrete, small)
- a. **GitHub issue numbers in user-facing copy.** Examples: "(#335)" in a VFR Feasibility parameter description (`src/weatherbrief/analysis/advisories/vfr_feasibility.py`, shown in the info modal); "(#827)" and "(#283)" in other advisory strings. Grep the advisory param/description strings for `#\d{3,4}`.
- b. **Unformatted ceiling.** The Arrival LSGS box shows `ceil 58337.5ft` (should be "no ceiling"/CLR, or rounded). Other boxes show `ceil 52640ft`, `ceil 64035ft` in Full Details.
- c. **Implausible altitude.** Sounding Analysis (Full Details, point 11) shows "Max W (ft/min) -40 @ 155239ft". Looks like a units or index bug.
- d. **Unexplained contradiction.** Sounding Analysis at point 11 lists "Low-level wind shear SEVERE 456–2612ft" while the Low-Level Wind Shear advisory is GREEN. It may be correct (one route point vs the terminal evaluator), but it needs an explanation or a different label.
- e. **Banner that compares times without dates.** With the trimmed fixture: "Pack departure 1700Z differs from flight 1700Z. Refresh to regenerate". Only the date differed. Include the date when the dates differ.

### 4.11 Web layout overflows on phones (390 px, iPhone 14 emulation)
- Briefing page scroll width is **646 px**. The culprits: `#refresh-btn` / `.toolbar-icon-group` / `.toolbar-end-group` (398 px), `.route-graph-selectors` (496 px), `.skewt-overlay-controls` (467 px), `.skewt-panel-selector` (448 px), `.band-table.obs-table` (619–674 px).
- Settings page scroll width is **509 px**, from `.advisory-param-pair` rows (up to 679 px).
- iOS is probably the main phone channel, but these are cheap CSS fixes (wrap, scroll containers for tables).

### 4.12 Flights list (`index.html`)
- The New Flight form is always open and takes about 40% of the first screen.
- Each card has Briefing / Edit / **Delete** (red outline) side by side, so Delete has almost the same weight as the main action.
- Cards show the current grade and the top advisories, but no trend, no time-to-departure, and no "changed since you looked".
- **Proposal (Flighty-style row):** route · "in 26 h" · grade with dots for the last N refreshes · "changed" badge · first clause of `assessment_reason`. The row itself opens the briefing; Edit/Delete go in a ⋯ menu. New Flight becomes a button or drawer.

### 4.13 Settings (`settings.html`)
- Reset shows on every advisory, so you can't see what differs from the default. Add "modified" markers.
- No search across about 20 advisories × Advanced params.
- A single "Save Settings" button at the top of a very long page.
- No preview of a threshold's effect.
- **Quick win:** the advisory info modal already lists the thresholds ("Cloud clearance 1000 ft"). Make them editable there ("change for this profile → Recalculate"), so thresholds live next to the evidence they affect. Recalculation without refetch already exists.

---

## 5. Suggested priorities

1. **Decision header** (4.1, 4.2, 4.4, 4.8): verdict + IFR/VFR answer + issues grouped by cause with model dots + watch items. Mostly rearranging data already computed. Mirror it on iOS.
2. **Explicit unknown and empty states** (4.3, 4.6). Small, and it matters for safety and trust.
3. **Evidence mode** (4.5): context bar, ‹ ›, worst-point marker, lens-aware tooltip, context kept across modes.
4. **Cross-section defaults** (4.7): neutral sky, a real dark theme, Lens as the main control, Theme/Emulate in a popover.
5. **Bug batch** (4.10, 4.11): small, independent, good `to-start` issues.
6. **Flights list and settings** (4.12, 4.13).

Items 4.10a–e and 4.11 are concrete enough to file as issues with `--label to-start`. Items 1, 3 and 4 need a plan or design-doc pass first (`briefing-sidebar.md`, `visualization.md`, `ios-app-ui.md`). The section-order change in 4.8 is the owner's call because a unit test deliberately locks the current order.
