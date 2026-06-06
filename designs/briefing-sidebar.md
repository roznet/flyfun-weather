# Briefing Sidebar Layout

> Opt-in, fully reversible alternative layout for the briefing page: a fixed
> left rail (route identity, derived glance summary, scroll-spy section nav,
> freshness, controls) beside a scrollable main pane, with per-section focus mode.

## Intent

The classic briefing page is one long scroll. The sidebar layout gives a
persistent at-a-glance rail so a pilot can see the overall assessment + the
non-green advisories without scrolling, and jump directly to any section. It is
**opt-in and reversible** — a power-user affordance, not a redesign. Nothing about
the data path or the main-pane renderers changes.

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

- **Activation**: `getBriefingLayout()` returns `'sidebar'` if `?layout=sidebar`
  or `localStorage['wb_layout'] === 'sidebar'`, else `'classic'`. In classic mode
  `initBriefingLayout` only injects an opt-in toggle button and returns.
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
- **`data-section` is the contract** — section renderers tag their root with
  `data-section`; the nav is generated from those tags, so adding a section to
  the nav is just adding the attribute (+ a `NAV_LABELS` entry).

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
- Renderers that tag `data-section`: `web/ts/managers/briefing-ui.ts`
