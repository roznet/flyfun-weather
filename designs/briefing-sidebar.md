# Briefing Sidebar Layout

> Default, fully reversible layout for the briefing page: a fixed
> left rail (route identity, derived glance summary, scroll-spy section nav,
> freshness, controls) beside a scrollable main pane, with per-section focus mode.

## Intent

The classic briefing page is one long scroll. The sidebar layout gives a
persistent at-a-glance rail so a pilot can see the overall assessment + the
non-green advisories without scrolling, and jump directly to any section. It is
now the **default** layout, but stays **fully reversible** — classic is a one-click
opt-out, and nothing about the data path or the main-pane renderers changes.

**The defining design rule: the rail owns no data.** Every renderer
(`briefing-ui.ts` etc.) renders into the main pane exactly as in classic layout.
The rail *derives* its glance summary by reading the already-rendered DOM
(`buildSummary` reads `#assessment-banner` classes + non-green advisory titles)
and *moves* existing nodes (route header, freshness bar) into place. So the
sidebar can be added/removed without touching any other manager.

## Architecture

Single module: `web/ts/managers/sidebar-layout.ts`. Entry point
`initBriefingLayout()` is called once from `briefing-main.ts` after content
renders.

- **Activation**: sidebar is the **default**. `getBriefingLayout()` returns
  `'classic'` only on an explicit opt-out (`?layout=classic` or
  `localStorage['wb_layout'] === 'classic'`); otherwise `'sidebar'`. In classic
  mode `initBriefingLayout` only injects a "Switch to sidebar layout" button (into
  `.toolbar-end-group`) and returns. The sidebar's rail footer carries the reverse
  "Switch to classic layout" button.
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
  in and adds the "Switch to classic layout" button. Moved nodes keep their
  existing (direct or delegated) listeners, which is why nothing else breaks.
- **Section nav**: `NAV_GROUPS` is the whitelist — the nav iterates its groups
  and keeps the keys whose `[data-section="<key>"]` element exists and is not
  `display:none`. So `data-section` alone is necessary but not sufficient
  (`advisories` and `time-scenarios` carry the attribute and are deliberately
  absent from the nav; advisories are surfaced via the glance summary instead).
  `NAV_LABELS` supplies friendly labels and is optional — the fallback is the
  section's `h3` text, then the key. A `MutationObserver` rebuilds the nav as
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
  on the shell so CSS hides all but the chosen section in the main pane.

## Key choices

- **Derive, don't duplicate** — the rail reads rendered DOM instead of
  re-subscribing to the store, so it can never disagree with the main pane and
  adds zero coupling to data managers.
- **Banners stay outside the shell** — freshness/error warnings must survive
  focus mode and are therefore pinned full-width above both panes.
- **The page-header is sticky app-wide, and the rail adds no back-link**
  (issue #543) — `.page-header` is `position: sticky; top: 0; z-index: 50` on
  every page, so the nav is always reachable: in the sidebar layout, in classic,
  and on narrow viewports where the rail is not sticky. A "‹ Flights" breadcrumb
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
- `web/ts/briefing-main.ts` — `initBriefingLayout()` call
- `web/css/style.css` — `.layout-sidebar`, `.briefing-shell`, `.briefing-rail`,
  `.rail-summary`, `.rail-nav`, focus-mode rules
- Sections tagged with `data-section`: `web/briefing.html` (the nav/scroll-spy
  source of truth; `briefing-ui.ts`, `tour/briefing-tour.ts` and
  `eval/label-panel.ts` only read them)
