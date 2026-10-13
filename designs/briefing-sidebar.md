# Briefing Sidebar Layout

> The briefing page layout: a fixed
> left rail (route identity, derived glance summary, scroll-spy section nav,
> freshness, controls) beside a scrollable main pane, with per-section focus mode.

## Intent

The briefing is one long scroll of sections. The rail gives a persistent
at-a-glance view so a pilot can see the overall assessment + the non-green
advisories without scrolling, and jump directly to any section. It is the only
layout: the old single-column "classic" opt-out was removed (a stale
`?layout=classic` link or `wb_layout` localStorage value is simply ignored).

**The defining design rule: the rail owns no data.** Every renderer
(`briefing-ui.ts` etc.) renders into the main pane, unaware of the rail.
The rail *derives* its glance summary by reading the already-rendered DOM
(`buildSummary` reads `#assessment-banner` classes + non-green advisory titles)
and *moves* existing nodes (route header, freshness bar) into place. So the
sidebar can be added/removed without touching any other manager.

## Architecture

Single module: `web/ts/managers/sidebar-layout.ts`. Entry point
`initBriefingLayout()` is called once from `briefing-main.ts` after content
renders.

- **DOM restructure** (sidebar mode): wrap `.container` content into a
  `.briefing-shell` with `.briefing-rail` (aside) + `.briefing-main` (main). All
  briefing content is moved into MAIN preserving order; the page-header, error
  banner, and stale-pack banner are deliberately **kept above the shell**
  full-width so focus-mode's `display:none` can never hide a freshness/error
  warning.
- **Rail composition** (top→bottom): focus bar, `#briefing-header` (route
  identity), `buildRailControls()` — which **moves** `#display-mode-toggle`
  (Standard/Details) out of the toolbar — `#freshness-bar`, the glance summary,
  the section nav, then `buildRailFooter()`, which **moves** `#history-select`
  in. Moved nodes keep their
  existing (direct or delegated) listeners, which is why nothing else breaks.
- **Section nav**: `NAV_GROUPS` is the whitelist — the nav iterates its groups
  and keeps the keys whose `[data-section="<key>"]` element exists and is not
  `display:none`. So `data-section` alone is necessary but not sufficient
  (`advisories` and `time-scenarios` carry the attribute and are deliberately
  absent from the nav; advisories are surfaced via the glance summary instead).
  `NAV_LABELS` supplies friendly labels and is optional — the fallback is the
  section's `h3` text, then the key. **The main pane lists the nav sections in
  `NAV_GROUPS` order** (Explore → Observations → Discussion → Detail), so
  scroll-spy moves monotonically; `tests/unit/briefing-section-order.test.ts`
  parses `briefing.html` and fails on drift. Banners that are not nav sections
  (`#alt-assessment-banner`, `#refresh-delta-banner` "Since this briefing") sit
  above the first nav section, under the advisories/timing block. A `MutationObserver` rebuilds the nav as
  sections appear/disappear.
- **Scroll-spy**: `setupScrollSpy` keeps the active nav item honest; recompute is
  debounced on scroll/resize.
- **Glance summary** (`buildSummary`): overall assessment chip + non-green
  advisory titles, each a button that scrolls to (and expands) the corresponding
  main-pane element. Rebuilt via `MutationObserver` on `#assessment-banner` and
  `#advisories-wrapper` so it tracks model switch / Standard↔Details toggle /
  recalc.
- **Resizable rail**: `buildResizer` drags `--rail-width` (clamped 260–560 px
  and never past half the shell width; default 320 px in CSS), persisted to
  `localStorage['wb_rail_width']`; double-click resets. Hidden below 980 px,
  where the shell collapses to a single column and the rail stops being sticky.
- **Focus mode**: `enterFocus`/`exitFocus` set `data-focus`/`data-focus-target`
  on the shell so CSS hides all but the chosen section in the main pane. It
  expands a collapsed section (class only — the persisted
  `wb_collapsedSections` is untouched) and dispatches a window `resize` so
  Leaflet re-measures; the Skew-T and cross-section canvases have their own
  `ResizeObserver`s, so a section that is still `display:none` when focused
  renders at the right size once its data lands. Below 980 px a focused main
  pane is ordered before the rail, so the section is first on the page.

## Focusing a section from a link or a framing page

For pages that show one section of a briefing (e.g. a presentation that frames
it), the exported API wraps focus mode — no scrolling to a target, so content
rendering later cannot push the section off-screen, and nothing waits for data
(the sections exist statically in `briefing.html`):

- `?section=<key>` — applied by `initSectionFocus(params)`, called from
  `briefing-main.ts` right after `initBriefingLayout()`. The page opens with
  only that section in the main pane (and the window at the top).
- `{type: 'wb-frame:section', section: '<key>' | null}` posted by the parent
  (only when framed, and only when `ev.source === window.parent`) switches the
  focused section without a reload; `section: null` exits focus.
- Valid keys are `FOCUSABLE_SECTIONS`: every `NAV_GROUPS` key plus
  `advisories`. Anything else (unknown key, wrong type, missing field) is
  ignored — by both the param and the message — so neither can do more than
  change which section is shown. A known section that has no content for this
  briefing (still `display:none`) focuses to an empty pane; the framer picks
  sections that exist.
- `focusSection(key)` / `exitSectionFocus()` / `focusedSection()` are the
  in-page API; the rail's "✕ Exit focus" still works when framed.
- **Never persisted**: focus is module state only, so a focus set by a link or
  a frame does not follow the user into normal browsing.
- **`view=` is unchanged** (decision): `?view=skewt|skewt-compare|skewt-static|cross-section`
  (visualization.md, deep-link) still opens the full page and scrolls once —
  the MCP emits `view=skewt` links for pilots, who should land in the whole
  briefing, not a focus they did not ask for. `section=` is the robust path for
  framers. Combined (`?section=skewt&view=skewt-compare`), `view=` picks the
  Skew-T mode and its scroll is skipped while a section is focused.

### Frame protocol (all `wb-frame:*` messages)

The briefing (and every app page) may be framed only by the hosts allowed in
`frame-ancestors` (multi-user-deployment.md). Outgoing messages carry no data,
so they are posted to `'*'`; incoming ones are honoured only from
`window.parent`.

| Message | Direction | Sent / handled in | Meaning |
|---|---|---|---|
| `wb-frame:ready` | page → parent | `utils.renderUserInfo` | auth succeeded, a real page (not login/error) is up |
| `wb-frame:auth-required` | page → parent | `utils.redirectToLogin` | a 401; the parent shows its own sign-in prompt |
| `wb-frame:section` | parent → briefing | `sidebar-layout.initSectionFocus` | `{section: key}` focus that section, `{section: null}` exit focus |

`/embed.html` has its own `wb-embed:*` messages (visualization.md, embed page).

## Key choices

- **Derive, don't duplicate** — the rail reads rendered DOM instead of
  re-subscribing to the store, so it can never disagree with the main pane and
  adds zero coupling to data managers.
- **Banners stay outside the shell** — freshness/error warnings must survive
  focus mode and are therefore pinned full-width above both panes.
- **The page-header is sticky app-wide, and the rail adds no back-link**
  (issue #543) — `.page-header` is `position: sticky; top: 0; z-index: 50` on
  every page, so the nav is always reachable, including on narrow viewports
  where the rail is not sticky. A "‹ Flights" breadcrumb
  in the rail was built and then removed — pinned directly under a pinned nav
  bar that already says *Flights*, it was pure duplication.
  `trackHeaderHeight()` (`web/ts/utils.ts`) publishes its rendered
  height as `--header-h` on `:root`; the rail's sticky `top`/`max-height`, the
  help TOC, and `html { scroll-padding-top }` all read that var instead of
  hard-coding a height. Leaflet maps (panes at z-index 400–1000) get
  `.leaflet-container { z-index: 0 }` so they cannot paint over the header.
- **`data-section` is the contract** — section roots are tagged with
  `data-section` (statically in `web/briefing.html`); scroll-spy, focus mode and
  several other consumers key off it. Putting a section *in the nav* additionally
  requires listing its key in a `NAV_GROUPS` group (labels via `NAV_LABELS` are
  optional). Sections stay out of the nav by simply not being listed.

## Gotchas

- Re-entrancy guard: bails if `.container` already has `.layout-sidebar`.
- All `localStorage` access is wrapped in try/catch (private-mode safe).
- Scroll-spy observer is rebuilt only when nav items change; scroll/resize call
  the existing recompute directly (debounced) to avoid observer churn.

## References
- `web/ts/managers/sidebar-layout.ts` (whole feature)
- `web/ts/briefing-main.ts` — `initBriefingLayout()` + `initSectionFocus(params)`
  calls; `applyDeepLink()` skips its `view=` scroll while focused
- `web/tests/unit/briefing-section-focus.test.ts` (param/message parsing, key
  validation), `web/tests/briefing.spec.ts` (sidebar layout: `?section=`,
  `wb-frame:section`)
- `web/css/style.css` — `.layout-sidebar`, `.briefing-shell`, `.briefing-rail`,
  `.rail-summary`, `.rail-nav`, focus-mode rules
- Sections tagged with `data-section`: `web/briefing.html` (the nav/scroll-spy
  source of truth; `briefing-ui.ts`, `tour/briefing-tour.ts` and
  `eval/label-panel.ts` only read them)
