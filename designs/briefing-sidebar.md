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
- **Section nav**: built from elements carrying `data-section="<key>"`. `NAV_GROUPS`
  / `NAV_LABELS` define grouping + friendly labels; empty sections (toggled to
  `display:none`) are omitted. A `MutationObserver` rebuilds the nav as sections
  appear/disappear.
- **Scroll-spy**: `setupScrollSpy` keeps the active nav item honest; recompute is
  debounced on scroll/resize.
- **Glance summary** (`buildSummary`): overall assessment chip + non-green
  advisory titles, each a button that scrolls to (and expands) the corresponding
  main-pane element. Rebuilt via `MutationObserver` on `#assessment-banner` and
  `#advisories-wrapper` so it tracks model switch / Standard↔Details toggle /
  recalc.
- **Resizable rail**: `buildResizer` drags `--rail-width` (clamped 260–560 px),
  persisted to `localStorage['wb_rail_width']`; double-click resets.
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
  `data-section` (statically in `web/briefing.html`); the nav is generated from
  those tags, so adding a section to the nav is just adding the attribute (+ a
  `NAV_LABELS` entry, and if it's a new group, a `NAV_GROUPS` entry).

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
  source of truth; `briefing-ui.ts` only reads them)
