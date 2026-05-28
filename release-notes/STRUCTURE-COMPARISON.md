# Release-stream structure: grouped vs per-item

A scan of the last ~60 merged PRs (2026-04-24 → 2026-05-26) to decide how the
user-facing "What's New" / release stream should be structured, and to seed the
initial backfill once the upload mechanism exists.

## PR inventory: user-facing vs internal

The single biggest finding: **most merged PRs are not user-facing.** Of ~60 PRs
in the window, roughly 20 are things a pilot would notice; the rest are
internal (decode internals, verification infra, perf, frontal-detection
research). This alone argues against one-entry-per-PR.

### User-facing (a pilot would care)
- #182 Current Conditions overlay · #170 Route SIGMETs
- #181 Convective DD-vs-model cross-check · #165 Convective regime discrimination · #180 Terrain always-on + cloud fallback
- #175 Region-aware units
- #169 Tiered refresh gating
- #152 Magic-link sign-in
- #163 Climatology maps + leaderboard · #141 / #140 airport rollups
- #162 Guided tour
- #159 Effective cruise altitude consistency
- #153 Import from Autorouter
- #147 Privacy-first analytics · #146 EuroFOX aircraft
- #145 Data Sources & Models table
- #144 Natural clouds · #142 DD/NWP cloud split · #135 source×style axes · #132 surface obscuration · #98 native cloud layers · #143 Skew-T parity
- #122 Airport profile panel on map
- #119 Freshness popover · #109 Freshness marker system
- #118 Progressive briefing render · #117 Shareable links
- #97 Post-flight debrief
- #96 / #95 Synoptic forecast map

### Internal (would be noise as individual entries)
#183 N+1/pagination · #174 NULL flight_category · #172 decode dispatcher ·
#161 422 handler · #160 review cleanup · #156 flight_id widen · #150 cache-rebuild ·
#149 cloud-diag interp · #139/#128/#127 GRIB internals · #138 cgroup bump ·
#136 ECMWF decode pool · #120/#116 fetch parallelism · #107 diagnostics ·
#106 icing gating · #105/#104/#103/#102 perf · #101 Vitest · #99 route processing ·
#94/#93/#91 frontal research · plus the verification/standalone cluster.

> iOS-only PRs (e.g. #184 offline-pack download progress) are **excluded** — the
> What's New stream lives in the web app.

---

## Mode A — Grouped (one entry per theme/release)

~15 entries cover the whole window. Several PRs collapse into one story, and the
~40 internal PRs become a single recurring "behind the scenes" note.

```
★ See current conditions right on your route        23 May   [Feature]
   METAR columns colored by flight category + live SIGMET hazard zones…   (#182, #170)

  Smarter convective and terrain-aware guidance      23 May   [Feature]
   Convective second opinion in the popup/digest; terrain always shown…   (#181, #165, #180)

  Visibility in your local units                     22 May   [Change]
   km in Europe, statute miles in the US, auto by region…                 (#175)

★ Redesigned cloud display                           11 May   [Feature]
   Natural puffy clouds, source×style, surface obscuration, Skew-T parity (#144, #142, #135, #132, #98, #143)

  Behind the scenes: faster, more reliable briefings 23 May   [Change]
   Parallelised fetch/decode, caching, stability, accuracy fixes…         (~40 internal PRs)
   … (15 entries total)
```

(★ = lights the notification dot. See `backfill-grouped.json` for the full text.)

## Mode B — Per-item (one entry per user-facing PR)

Same window, but each PR is its own card. ~25+ entries, and the reading
experience is choppier — closely related changes are scattered, and titles lean
technical:

```
  Add Current Conditions cross-section overlay       23 May   [Feature]   (#182)
  Route SIGMET integration (D-0 real-time)           21 May   [Feature]   (#170)
  Convective DD-vs-model cross-check                  23 May   [Feature]   (#181)
  Convective regime discrimination                    20 May   [Feature]   (#165)
  Terrain always-on + NWP→DD cloud fallback           23 May   [Feature]   (#180)
  Region-aware display units                          22 May   [Change]    (#175)
  Replace hatched clouds with natural puffy clouds    11 May   [Feature]   (#144)
  Split DD/NWP-3D cloud layers on category change     10 May   [Feature]   (#142)
  Orthogonal cloud source × style axes                08 May   [Feature]   (#135)
  Surface obscuration cross-section layer             08 May   [Feature]   (#132)
  Skew-T renders same decks as cross-section          10 May   [Fix]       (#143)
  … 4 separate cloud cards instead of 1 story …
```

---

## Recommendation: **grouped**, with the deploy as the natural unit

The scan makes the case on its own:

1. **Most PRs aren't user-facing.** Per-item forces you to either publish noise
   or hand-prune every deploy. Grouped lets one "behind the scenes" line absorb
   the entire internal cluster.
2. **Related PRs are one story.** The four cloud PRs are *"redesigned cloud
   display"* to a pilot — not four cards. Grouping reads as a changelog, not a
   commit log.
3. **It matches how we ship and how the dot should behave.** A deploy bundles
   several PRs; one release entry per deploy is the natural seam, and you flag the
   whole entry highlight-on or -off. Per-item would multiply dot decisions.

Trade-off: grouped entries need a sentence of editorial framing (which is the
point of the friendly summary), whereas per-item could in theory be
auto-generated from PR titles. But auto-generated titles are exactly the
technical noise we're trying to avoid — so the editorial step is worth it, and
it's the step you've already said you want to validate per deploy.

---

## How this seed maps to the mechanism (not yet built)

- `backfill-grouped.json` is the reusable artifact — a list of
  `{ date, title, body, category, highlight }`, ready for the planned
  `python -m weatherbrief.release import release-notes/backfill-grouped.json`.
- `highlight` in the file expresses **go-forward intent** (which entries are
  dot-worthy). For the **historical backfill** we should import with a
  `--force-no-highlight` flag so existing users don't get a wall of dots for
  past work — only future releases light the dot selectively.
- Go-forward, the deploy skill drafts one new grouped entry per deploy from the
  PRs in `SERVER_SHA..LOCAL_SHA`, you validate, and it's pushed via the same CLI.
