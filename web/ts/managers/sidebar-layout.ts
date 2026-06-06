/**
 * Optional "sidebar" briefing layout — opt-in and fully reversible.
 *
 * Activated by `?layout=sidebar` or localStorage `wb_layout=sidebar`. The
 * default ('classic') leaves the page exactly as before. In sidebar mode we
 * build a two-column shell and REPARENT existing rendered nodes (route header,
 * assessment, advisories, freshness, history, depth toggle) into a sticky left
 * rail, generate a scroll-spy SECTIONS nav, and add a per-section focus mode.
 *
 * No section rendering logic is touched — this is a shell. Every node we move
 * keeps its existing event listeners (direct or delegated), so refresh, the
 * Standard/Details toggle, collapsibles, history, etc. all keep working.
 */

export type BriefingLayout = 'classic' | 'sidebar';
const STORAGE_KEY = 'wb_layout';
const RAIL_WIDTH_KEY = 'wb_rail_width';
const RAIL_MIN_WIDTH = 260;
const RAIL_MAX_WIDTH = 560;

export function getBriefingLayout(): BriefingLayout {
  const param = new URLSearchParams(location.search).get('layout');
  if (param === 'sidebar' || param === 'classic') return param;
  try {
    if (localStorage.getItem(STORAGE_KEY) === 'sidebar') return 'sidebar';
  } catch { /* ignore */ }
  return 'classic';
}

function setBriefingLayout(layout: BriefingLayout): void {
  try { localStorage.setItem(STORAGE_KEY, layout); } catch { /* ignore */ }
}

// Friendly labels + grouping for the SECTIONS nav. Keys are data-section values.
const NAV_GROUPS: { title: string; keys: string[] }[] = [
  { title: 'Explore', keys: ['cross-section', 'skewt'] },
  { title: 'Observations', keys: ['observations', 'sigmets', 'pireps', 'alternates'] },
  { title: 'Discussion', keys: ['synopsis', 'dwd-charts', 'dwd-overview', 'gramet'] },
  { title: 'Detail', keys: ['sounding', 'comparison'] },
];
const NAV_LABELS: Record<string, string> = {
  'cross-section': 'Cross-section & map',
  'skewt': 'Skew-T soundings',
  'observations': 'METAR / TAF',
  'sigmets': 'SIGMETs',
  'pireps': 'PIREPs',
  'alternates': 'Weather alternates',
  'synopsis': 'Synopsis',
  'dwd-charts': 'Surface pressure & fronts',
  'dwd-overview': 'DWD overview',
  'gramet': 'GRAMET',
  'sounding': 'Sounding analysis',
  'comparison': 'Model comparison',
};

function sectionEl(key: string): HTMLElement | null {
  return document.querySelector(`[data-section="${key}"]`) as HTMLElement | null;
}

function appendIfPresent(parent: HTMLElement, selector: string): void {
  const node = document.querySelector(selector);
  if (node) parent.appendChild(node); // appendChild moves the node
}

function debounce(fn: () => void, ms: number): () => void {
  let h: number | undefined;
  return () => {
    if (h) window.clearTimeout(h);
    h = window.setTimeout(fn, ms);
  };
}

let focusKey: string | null = null;

function enterFocus(shell: HTMLElement, key: string): void {
  const el = sectionEl(key);
  if (!el) return;
  el.classList.remove('collapsed');
  shell.querySelectorAll('[data-focus-target]').forEach((n) => n.removeAttribute('data-focus-target'));
  el.setAttribute('data-focus-target', '');
  shell.setAttribute('data-focus', key);
  focusKey = key;
  updateFocusBar(shell);
  // Let ResizeObserver-driven renderers pick up the new size.
  requestAnimationFrame(() => window.dispatchEvent(new Event('resize')));
}

function exitFocus(shell: HTMLElement): void {
  shell.removeAttribute('data-focus');
  shell.querySelectorAll('[data-focus-target]').forEach((n) => n.removeAttribute('data-focus-target'));
  focusKey = null;
  updateFocusBar(shell);
  requestAnimationFrame(() => window.dispatchEvent(new Event('resize')));
}

function buildFocusBar(shell: HTMLElement): HTMLElement {
  const bar = document.createElement('div');
  bar.className = 'rail-focus-bar';
  bar.style.display = 'none';
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'btn btn-small rail-exit-focus';
  btn.textContent = '✕ Exit focus';
  btn.addEventListener('click', () => exitFocus(shell));
  const label = document.createElement('span');
  label.className = 'rail-focus-label';
  bar.append(btn, label);
  return bar;
}

function updateFocusBar(shell: HTMLElement): void {
  const bar = shell.querySelector('.rail-focus-bar') as HTMLElement | null;
  if (!bar) return;
  if (focusKey) {
    bar.style.display = 'flex';
    const label = bar.querySelector('.rail-focus-label') as HTMLElement;
    label.textContent = NAV_LABELS[focusKey] || focusKey;
  } else {
    bar.style.display = 'none';
  }
}

let spyObserver: IntersectionObserver | null = null;
// Stable handle to the active scroll-spy recompute so scroll/resize can call it
// directly instead of tearing down and rebuilding the observer each time.
let spyRecompute: (() => void) | null = null;

function setupScrollSpy(nav: HTMLElement): void {
  spyObserver?.disconnect();
  const items = Array.from(nav.querySelectorAll('.rail-nav-item')) as HTMLElement[];
  const recompute = (): void => {
    let best: { key: string; top: number } | null = null;
    for (const item of items) {
      const key = item.dataset.navKey!;
      const el = sectionEl(key);
      if (!el) continue;
      const r = el.getBoundingClientRect();
      if (r.bottom > 80 && r.top < window.innerHeight * 0.5) {
        if (!best || r.top < best.top) best = { key, top: r.top };
      }
    }
    items.forEach((i) => {
      const active = !!best && i.dataset.navKey === best.key;
      i.classList.toggle('active', active);
      if (active) i.setAttribute('aria-current', 'true');
      else i.removeAttribute('aria-current');
    });
  };
  spyRecompute = recompute;
  spyObserver = new IntersectionObserver(recompute, { threshold: [0, 1] });
  for (const item of items) {
    const el = sectionEl(item.dataset.navKey!);
    if (el) spyObserver.observe(el);
  }
  recompute();
}

function buildNav(nav: HTMLElement, shell: HTMLElement): void {
  nav.innerHTML = '';
  const title = document.createElement('div');
  title.className = 'rail-nav-title';
  title.textContent = 'Sections';
  nav.appendChild(title);

  for (const group of NAV_GROUPS) {
    const visibleKeys = group.keys.filter((k) => {
      const el = sectionEl(k);
      return el && el.style.display !== 'none';
    });
    if (!visibleKeys.length) continue;

    const gt = document.createElement('div');
    gt.className = 'rail-group-title';
    gt.textContent = group.title;
    nav.appendChild(gt);

    for (const key of visibleKeys) {
      const el = sectionEl(key)!;
      const label = NAV_LABELS[key] || el.querySelector('h3')?.textContent?.trim() || key;
      const item = document.createElement('button');
      item.type = 'button';
      item.className = 'rail-nav-item';
      item.dataset.navKey = key;

      const dot = document.createElement('span');
      dot.className = 'rail-nav-dot';
      const lab = document.createElement('span');
      lab.className = 'rail-nav-label';
      lab.textContent = label;
      const focus = document.createElement('span');
      focus.className = 'rail-nav-focus';
      focus.setAttribute('role', 'button');
      focus.setAttribute('aria-label', `Focus ${label}`);
      focus.title = 'Focus this section';
      focus.textContent = '⛶';
      item.append(dot, lab, focus);

      item.addEventListener('click', (e) => {
        if ((e.target as HTMLElement).closest('.rail-nav-focus')) {
          enterFocus(shell, key);
          return;
        }
        el.classList.remove('collapsed');
        el.scrollIntoView({ behavior: 'smooth', block: 'start' });
      });
      nav.appendChild(item);
    }
  }
  setupScrollSpy(nav);
}

/**
 * Draggable divider between the rail and the main pane. Dragging sets the
 * `--rail-width` CSS var on the shell (clamped to [MIN, MAX] and never past
 * half the shell width); the chosen width is persisted to localStorage and
 * a double-click resets to the default. Hidden below 980px where the layout
 * collapses to a single column (see CSS media query).
 */
function buildResizer(shell: HTMLElement): HTMLElement {
  const handle = document.createElement('div');
  handle.className = 'briefing-resizer';
  handle.setAttribute('role', 'separator');
  handle.setAttribute('aria-orientation', 'vertical');
  handle.setAttribute('aria-label', 'Resize sidebar');

  let dragging = false;

  const onMove = (e: PointerEvent): void => {
    if (!dragging) return;
    const rect = shell.getBoundingClientRect();
    const max = Math.min(RAIL_MAX_WIDTH, rect.width * 0.5);
    const w = Math.max(RAIL_MIN_WIDTH, Math.min(max, e.clientX - rect.left));
    shell.style.setProperty('--rail-width', `${Math.round(w)}px`);
  };

  const stop = (): void => {
    if (!dragging) return;
    dragging = false;
    document.body.classList.remove('resizing-rail');
    window.removeEventListener('pointermove', onMove);
    window.removeEventListener('pointerup', stop);
    try {
      const w = shell.style.getPropertyValue('--rail-width').trim();
      if (w) localStorage.setItem(RAIL_WIDTH_KEY, w);
    } catch { /* ignore */ }
  };

  handle.addEventListener('pointerdown', (e) => {
    dragging = true;
    document.body.classList.add('resizing-rail');
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', stop);
    e.preventDefault();
  });

  // Double-click restores the default width.
  handle.addEventListener('dblclick', () => {
    shell.style.removeProperty('--rail-width');
    try { localStorage.removeItem(RAIL_WIDTH_KEY); } catch { /* ignore */ }
  });

  return handle;
}

/**
 * Top-of-rail controls: the Standard/Details depth toggle, surfaced at the very
 * top so it's the easiest thing to find and change (per user feedback).
 */
function buildRailControls(): HTMLElement {
  const row = document.createElement('div');
  row.className = 'rail-controls';
  const depth = document.getElementById('display-mode-toggle');
  if (depth) {
    const lab = document.createElement('span');
    lab.className = 'rail-controls-label';
    lab.textContent = 'Detail';
    row.append(lab, depth); // appendChild moves the toggle out of the toolbar
  }
  return row;
}

function buildRailFooter(): HTMLElement {
  const footer = document.createElement('div');
  footer.className = 'rail-footer';

  const hist = document.getElementById('history-select');
  if (hist) {
    const row = document.createElement('div');
    row.className = 'rail-footer-row';
    const lab = document.createElement('span');
    lab.className = 'rail-footer-label';
    lab.textContent = 'Briefing';
    row.append(lab, hist);
    footer.appendChild(row);
  }

  const sw = document.createElement('button');
  sw.type = 'button';
  sw.className = 'btn btn-small layout-switch-btn';
  sw.textContent = 'Switch to classic layout';
  sw.addEventListener('click', () => {
    setBriefingLayout('classic');
    location.reload();
  });
  footer.appendChild(sw);
  return footer;
}

function injectClassicOptIn(): void {
  const group = document.querySelector('.toolbar-end-group');
  if (!group || document.getElementById('layout-optin-btn')) return;
  const btn = document.createElement('button');
  btn.id = 'layout-optin-btn';
  btn.type = 'button';
  btn.className = 'btn btn-small layout-switch-btn';
  btn.textContent = '▦ Try new layout';
  btn.title = 'Preview the experimental sidebar layout';
  btn.addEventListener('click', () => {
    setBriefingLayout('sidebar');
    location.reload();
  });
  group.insertBefore(btn, group.firstChild);
}

/**
 * Entry point — call once after the page's sections and toolbar are wired.
 * No-op (beyond injecting the opt-in button) when layout is 'classic'.
 */
export function initBriefingLayout(): void {
  if (getBriefingLayout() !== 'sidebar') {
    injectClassicOptIn();
    return;
  }

  const container = document.querySelector('.container') as HTMLElement | null;
  if (!container || container.classList.contains('layout-sidebar')) return;
  container.classList.add('layout-sidebar');

  const shell = document.createElement('div');
  shell.className = 'briefing-shell';
  const rail = document.createElement('aside');
  rail.className = 'briefing-rail';
  const main = document.createElement('main');
  main.className = 'briefing-main';
  shell.append(rail, main);

  // Draggable divider so the rail width can be tuned (clamped, persisted).
  shell.appendChild(buildResizer(shell));
  try {
    const w = localStorage.getItem(RAIL_WIDTH_KEY);
    if (w) shell.style.setProperty('--rail-width', w);
  } catch { /* ignore */ }

  const pageHeader = container.querySelector('.page-header');
  const loadingOverlay = document.getElementById('loading-overlay');
  if (pageHeader && pageHeader.nextSibling) {
    container.insertBefore(shell, pageHeader.nextSibling);
  } else {
    container.appendChild(shell);
  }

  // Move all briefing content into MAIN (preserve order), skipping the
  // page-header, the shell itself, and the loading overlay. Error and
  // stale-data banners are kept out of MAIN too so the focus-mode
  // `display:none` rule can never suppress a freshness/error warning.
  const keep = new Set<Node | null>([
    pageHeader,
    shell,
    loadingOverlay,
    document.getElementById('error-message'),
    document.getElementById('stale-pack-banner'),
  ]);
  Array.from(container.childNodes).forEach((node) => {
    if (keep.has(node)) return;
    main.appendChild(node);
  });

  // Pin the error/stale banners full-width at the very top, above both panes,
  // so a freshness/error warning is always visible (and outside focus-mode).
  const staleBanner = document.getElementById('stale-pack-banner');
  const errorBanner = document.getElementById('error-message');
  if (staleBanner) container.insertBefore(staleBanner, shell);
  if (errorBanner) container.insertBefore(errorBanner, shell);

  // Build the rail. The assessment + advisories stay in MAIN (rendered as
  // before); the rail carries the route identity, a derived glance summary,
  // section nav, freshness, and the footer controls.
  rail.appendChild(buildFocusBar(shell));
  appendIfPresent(rail, '#briefing-header'); // route identity

  // Depth toggle + freshness sit at the very top: the toggle is the control
  // users reach for most, and the freshness bar carries the live "refreshing…"
  // progress, which is key information that must be visible without scrolling.
  rail.appendChild(buildRailControls());
  appendIfPresent(rail, '#freshness-bar');

  const summary = document.createElement('div');
  summary.className = 'rail-summary';
  rail.appendChild(summary);

  const nav = document.createElement('nav');
  nav.className = 'rail-nav';
  rail.appendChild(nav);

  rail.appendChild(buildRailFooter());

  buildNav(nav, shell);
  buildSummary(summary);

  // Rebuild nav as sections appear/disappear (empty ones toggle display:none).
  const navMo = new MutationObserver(debounce(() => buildNav(nav, shell), 200));
  navMo.observe(main, { attributes: true, attributeFilter: ['style'], subtree: true, childList: true });

  // Rebuild the glance summary whenever the assessment or advisories re-render
  // (initial load, model switch, Standard/Details toggle, recalc).
  const sumMo = new MutationObserver(debounce(() => buildSummary(summary), 150));
  const banner = document.getElementById('assessment-banner');
  const advWrapper = document.getElementById('advisories-wrapper');
  if (banner) sumMo.observe(banner, { childList: true, characterData: true, subtree: true, attributes: true, attributeFilter: ['class'] });
  if (advWrapper) sumMo.observe(advWrapper, { childList: true, subtree: true, attributes: true, attributeFilter: ['style'] });

  // Keep the active nav item honest during scroll/resize. Call the existing
  // recompute directly (debounced) — the observer is only rebuilt when the
  // nav items themselves change (via buildNav → setupScrollSpy).
  const onScroll = debounce(() => spyRecompute?.(), 50);
  window.addEventListener('scroll', onScroll, { passive: true });
  window.addEventListener('resize', onScroll, { passive: true });
}

/** Scroll to a main-pane element, expanding its collapsible section and flashing it. */
function scrollToEl(el: HTMLElement): void {
  const section = el.closest('.section.collapsible') as HTMLElement | null;
  if (section) section.classList.remove('collapsed');
  el.scrollIntoView({ behavior: 'smooth', block: 'start' });
  el.classList.add('rail-flash');
  window.setTimeout(() => el.classList.remove('rail-flash'), 1300);
}

function readAssessmentLevel(banner: HTMLElement | null): 'green' | 'amber' | 'red' | null {
  if (!banner) return null;
  for (const lvl of ['red', 'amber', 'green'] as const) {
    if (banner.classList.contains(`assessment-${lvl}`)) return lvl;
  }
  return null;
}

/**
 * Derive the rail glance summary from the already-rendered main content:
 *  - an overall amber/green/red chip → scrolls to the full assessment
 *  - the titles of the non-green advisories → scroll to that advisory card
 * Nothing is moved; this mirrors what's shown in the main pane.
 */
function buildSummary(container: HTMLElement): void {
  container.innerHTML = '';

  // Overall assessment chip
  const banner = document.getElementById('assessment-banner');
  const level = readAssessmentLevel(banner);
  const overall = document.createElement('button');
  overall.type = 'button';
  overall.className = level ? `rail-overall rail-overall-${level}` : 'rail-overall';
  const levelText = banner?.querySelector('strong')?.textContent?.trim() || (level ? level.toUpperCase() : '—');
  const dot = document.createElement('span');
  dot.className = 'rail-overall-dot';
  const lvl = document.createElement('span');
  lvl.className = 'rail-overall-level';
  lvl.textContent = levelText;
  const hint = document.createElement('span');
  hint.className = 'rail-overall-hint';
  hint.textContent = 'summary ›';
  overall.append(dot, lvl, hint);
  overall.addEventListener('click', () => { if (banner) scrollToEl(banner); });
  container.appendChild(overall);

  // What to watch: non-green advisory titles
  const cards = Array.from(document.querySelectorAll('#advisories-section .advisory-card')) as HTMLElement[];
  if (!cards.length) return;

  const watch = document.createElement('div');
  watch.className = 'rail-watch';
  const wt = document.createElement('div');
  wt.className = 'rail-watch-title';
  wt.textContent = 'What to watch';
  watch.appendChild(wt);

  let nonGreen = 0;
  let green = 0;
  for (const card of cards) {
    const status = card.classList.contains('advisory-red') ? 'red'
      : card.classList.contains('advisory-amber') ? 'amber'
        : card.classList.contains('advisory-green') ? 'green' : 'other';
    if (status === 'green') { green++; continue; }
    nonGreen++;
    const name = card.querySelector('.advisory-name')?.textContent?.trim() || card.dataset.advisory || '';
    const item = document.createElement('button');
    item.type = 'button';
    item.className = `rail-watch-item sev-${status}`;
    const d = document.createElement('span');
    d.className = 'rail-watch-dot';
    const n = document.createElement('span');
    n.className = 'rail-watch-name';
    n.textContent = name;
    item.append(d, n);
    item.addEventListener('click', () => scrollToEl(card));
    watch.appendChild(item);
  }

  if (nonGreen === 0) {
    const allclear = document.createElement('div');
    allclear.className = 'rail-watch-allclear';
    allclear.textContent = green > 0 ? `✓ All clear (${green})` : '✓ All clear';
    watch.appendChild(allclear);
  } else if (green > 0) {
    const clear = document.createElement('div');
    clear.className = 'rail-watch-clear';
    clear.textContent = `✓ ${green} clear`;
    watch.appendChild(clear);
  }
  container.appendChild(watch);
}
