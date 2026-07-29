/** Timeline view of the Data Sources catalog.
 *
 * Same data as the table (`GET /api/data-sources`), rendered as a 36-hour
 * strip so the *schedule* is legible: when each provider's runs initialise,
 * when we expect them to land, and — for the most recent run of each source —
 * when they actually landed.
 *
 * Three things the table can't show:
 *   - **Expected vs realised.** The hollow ring is `init + delivery_offset`
 *     (the registry's expectation); the filled dot is the marker store's
 *     observed `published_at`. The gap between them is the interesting part:
 *     a source drifting consistently early or late means the registry offset
 *     wants recalibrating.
 *   - **Cycle shape.** Sources with a per-cycle horizon (ECMWF 00/12z reach
 *     168h, 06/18z stop at 144h; ICON-EU's intermediate runs are nowcasts)
 *     draw their short runs as thinner bars, so the alternating rhythm of a
 *     feed is visible rather than buried in a dict.
 *   - **Local time.** Arrival times only mean something against the hours
 *     people actually plan in, so the strip carries clock rows — the viewer's
 *     own zone first, then Europe / US East / US West as references.
 *
 * All local-time conversion goes through `Intl.DateTimeFormat` per instant,
 * so daylight saving is handled by construction: a band drawn across a DST
 * boundary bends with it, and no offset is ever hardcoded.
 *
 * Colour encodes **role** (what a source contributes), not model identity —
 * the row label already carries identity, and four roles map onto a palette
 * that stays distinguishable under colour-vision deficiency. Lateness is a
 * separate, reserved status colour so it never collides with a role hue.
 */

import type { DataSourceEntry } from './adapters/data-sources-adapter';
import { escapeHtml } from './utils';
import { t } from './i18n/i18n';

const HOUR_MS = 3_600_000;

/** Hours of history shown to the left of "now". Long enough that every source
 *  — including the 6-hourly ones — has at least one realised run in view. */
const PAST_HOURS = 24;
/** Hours of upcoming expected runs shown to the right of "now". */
const FUTURE_HOURS = 12;

const SPAN_HOURS = PAST_HOURS + FUTURE_HOURS;

/** Reference clocks, in display order. The viewer's own zone is prepended at
 *  render time unless it already resolves to one of these. */
const REFERENCE_ZONES: ReadonlyArray<{ zone: string; labelKey: string }> = [
  { zone: 'Europe/Paris', labelKey: 'dataSources.timeline.zone.europe' },
  { zone: 'America/New_York', labelKey: 'dataSources.timeline.zone.usEast' },
  { zone: 'America/Los_Angeles', labelKey: 'dataSources.timeline.zone.usWest' },
];

/** Local-hour ranges highlighted on every clock row: the windows in which a
 *  pilot typically plans (evening before, morning of). Half-open [from, to). */
const PLANNING_WINDOWS: ReadonlyArray<{ from: number; to: number }> = [
  { from: 6, to: 9 },
  { from: 19, to: 22 },
];

/** Local hours shaded as night. */
const NIGHT_FROM = 22;
const NIGHT_TO = 6;

/** Role → palette slot. Validated for CVD separation in both themes; see the
 *  `--ds-tl-role-*` tokens in style.css. */
const ROLE_SLOT: Record<string, string> = {
  'primary-sounding': '1',
  'cloud-enrichment': '2',
  'surface-base': '3',
  'primary': '4',
};

/** How late a run may land before we call it late rather than on time. The
 *  registry offsets carry deliberate margin, so a few minutes either way is
 *  noise; 20 min is roughly where a real delivery problem starts to show. */
const LATE_TOLERANCE_MIN = 20;

// --- Time helpers ---------------------------------------------------------

/** Hour-of-day in `zone` for the instant `ms`, DST-correct by construction.
 *  Exported for tests. */
export function localHour(ms: number, zone: string): number {
  const parts = new Intl.DateTimeFormat('en-GB', {
    timeZone: zone,
    hour: '2-digit',
    hour12: false,
  }).formatToParts(new Date(ms));
  const h = parts.find((p) => p.type === 'hour')?.value ?? '0';
  // Some ICU builds render midnight as "24" under hour12:false.
  return Number(h) % 24;
}

/** Offset of `zone` from UTC, in hours, at the instant `ms`. Exported for tests. */
export function zoneOffsetHours(ms: number, zone: string): number {
  const dtf = new Intl.DateTimeFormat('en-US', {
    timeZone: zone,
    hour12: false,
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit',
  });
  const p: Record<string, string> = {};
  for (const part of dtf.formatToParts(new Date(ms))) {
    if (part.type !== 'literal') p[part.type] = part.value;
  }
  const asUtc = Date.UTC(
    Number(p.year), Number(p.month) - 1, Number(p.day),
    Number(p.hour) % 24, Number(p.minute),
  );
  // Round to the minute: the source instant may carry seconds we dropped.
  return (asUtc - Math.floor(ms / 60000) * 60000) / HOUR_MS;
}

/** `UTC+2` / `UTC-7` / `UTC` for `zone` at `ms`. */
function offsetLabel(ms: number, zone: string): string {
  const off = zoneOffsetHours(ms, zone);
  if (off === 0) return 'UTC';
  const sign = off > 0 ? '+' : '−';
  const abs = Math.abs(off);
  const whole = Math.floor(abs);
  const mins = Math.round((abs - whole) * 60);
  return `UTC${sign}${whole}${mins ? `:${String(mins).padStart(2, '0')}` : ''}`;
}

/** `HH:MM` in UTC. */
function hhmm(ms: number): string {
  const d = new Date(ms);
  return `${String(d.getUTCHours()).padStart(2, '0')}:${String(d.getUTCMinutes()).padStart(2, '0')}`;
}

/** `HH:MMZ` in UTC. */
function zulu(ms: number): string {
  return `${hhmm(ms)}Z`;
}

/** Signed minute delta rendered as `12 min early` / `46 min late` / `on time`. */
function deltaLabel(deltaMin: number): string {
  const abs = Math.abs(Math.round(deltaMin));
  if (abs < 1) return t('dataSources.timeline.onTime');
  const unit = abs >= 90
    ? t('dataSources.timeline.hoursShort', { n: (abs / 60).toFixed(1) })
    : t('dataSources.timeline.minsShort', { n: String(abs) });
  return deltaMin > 0
    ? t('dataSources.timeline.late', { d: unit })
    : t('dataSources.timeline.early', { d: unit });
}

// --- Run enumeration ------------------------------------------------------

export interface Run {
  /** Cycle hour (UTC) this run belongs to. */
  cycle: number;
  /** Run initialisation instant. */
  init: number;
  /** Expected delivery: `init + delivery_offset[cycle]`. */
  expected: number;
  /** Observed delivery, when this is the source's latest run and the marker
   *  store recorded a publish wallclock. `null` otherwise — including for
   *  every run in the future. */
  actual: number | null;
  /** Forecast horizon of this cycle, hours. */
  horizonH: number;
  /** Whether this cycle stops short of the source's longest horizon. */
  short: boolean;
}

/** Every run of `entry` whose init-or-delivery falls inside `[t0, t1]`.
 *  Exported for tests. */
export function runsInWindow(entry: DataSourceEntry, t0: number, t1: number): Run[] {
  if (!entry.cycles.length) return [];

  const horizons = entry.cycles.map((c) => entry.horizon_hours[String(c)] ?? 0);
  const maxHorizon = Math.max(...horizons);

  const latestInit = entry.latest_init ? Date.parse(entry.latest_init) : NaN;
  const publishedAt = entry.published_at ? Date.parse(entry.published_at) : NaN;

  const runs: Run[] = [];
  // A run initialised up to two days back can still deliver inside the window
  // (GEM's expected offset is 8h, and the window itself is 36h wide).
  const dayStart = new Date(t0);
  dayStart.setUTCHours(0, 0, 0, 0);

  for (let dayOffset = -2; dayOffset <= 2; dayOffset += 1) {
    for (const cycle of entry.cycles) {
      const init = Date.UTC(
        dayStart.getUTCFullYear(), dayStart.getUTCMonth(),
        dayStart.getUTCDate() + dayOffset, cycle,
      );
      const offsetH = entry.delivery_offset_hours[String(cycle)] ?? 0;
      const expected = init + offsetH * HOUR_MS;
      // Keep a run if any part of the init→expected span is visible.
      if (expected < t0 || init > t1) continue;

      const horizonH = entry.horizon_hours[String(cycle)] ?? maxHorizon;
      const isLatest = Number.isFinite(latestInit) && init === latestInit;
      runs.push({
        cycle,
        init,
        expected,
        actual: isLatest && Number.isFinite(publishedAt) ? publishedAt : null,
        horizonH,
        short: horizonH < maxHorizon,
      });
    }
  }
  runs.sort((a, b) => a.init - b.init);
  return runs;
}

// --- DOM helpers ----------------------------------------------------------

function div(cls: string, parent?: HTMLElement): HTMLDivElement {
  const el = document.createElement('div');
  el.className = cls;
  if (parent) parent.appendChild(el);
  return el;
}

/** Position a mark by percentage across the plot. */
function place(el: HTMLElement, leftPct: number, widthPct?: number): void {
  el.style.left = `${leftPct}%`;
  if (widthPct !== undefined) el.style.width = `${Math.max(widthPct, 0)}%`;
}

// --- Render ---------------------------------------------------------------

interface Scale {
  t0: number;
  t1: number;
  /** Instant → percentage across the plot. */
  pct(ms: number): number;
}

function makeScale(t0: number, t1: number): Scale {
  const span = t1 - t0;
  return { t0, t1, pct: (ms) => ((ms - t0) / span) * 100 };
}

/** One label + plot row. Returns the plot element for callers to fill. */
function addRow(
  track: HTMLElement,
  cls: string,
  name: string,
  sub: string,
): HTMLDivElement {
  const row = div(`ds-tl-row ${cls}`, track);
  const label = div('ds-tl-label', row);
  const strong = document.createElement('b');
  strong.textContent = name;
  label.appendChild(strong);
  if (sub) {
    const em = document.createElement('i');
    em.textContent = sub;
    label.appendChild(em);
  }
  return div('ds-tl-plot', row);
}

/** Hour gridlines + the now line, drawn behind every row's marks. */
function addBackdrop(plot: HTMLElement, scale: Scale, now: number): void {
  const bg = div('ds-tl-bg', plot);
  const start = Math.ceil(scale.t0 / HOUR_MS) * HOUR_MS;
  for (let ms = start; ms <= scale.t1; ms += 3 * HOUR_MS) {
    const isSynoptic = new Date(ms).getUTCHours() % 6 === 0;
    const line = div(`ds-tl-guide${isSynoptic ? ' synoptic' : ''}`, bg);
    place(line, scale.pct(ms));
  }
  const nowLine = div('ds-tl-now', bg);
  place(nowLine, scale.pct(now));
}

function renderRuler(track: HTMLElement, scale: Scale, now: number): void {
  const plot = addRow(track, 'ds-tl-ruler', 'UTC', t('dataSources.timeline.zulu'));
  addBackdrop(plot, scale, now);

  const start = Math.ceil(scale.t0 / HOUR_MS) * HOUR_MS;
  for (let ms = start; ms <= scale.t1; ms += HOUR_MS) {
    const hour = new Date(ms).getUTCHours();
    const isSynoptic = hour % 6 === 0;
    const tick = div(`ds-tl-tick${isSynoptic ? ' synoptic' : ''}`, plot);
    place(tick, scale.pct(ms));
    if (hour % 3 === 0) {
      // A centred label at either extreme is half-clipped by the plot edge;
      // anchor those two to the inside instead.
      const p = scale.pct(ms);
      const edge = p < 2 ? ' edge-start' : p > 98 ? ' edge-end' : '';
      const lbl = div(`ds-tl-hour${isSynoptic ? ' synoptic' : ''}${edge}`, plot);
      lbl.textContent = String(hour).padStart(2, '0');
      place(lbl, p);
    }
    if (hour === 0) {
      const day = div('ds-tl-daymark', plot);
      day.textContent = new Intl.DateTimeFormat(undefined, {
        timeZone: 'UTC', day: 'numeric', month: 'short',
      }).format(new Date(ms));
      place(day, scale.pct(ms));
    }
  }

  const nowChip = div('ds-tl-nowchip', plot);
  nowChip.textContent = t('dataSources.timeline.now');
  place(nowChip, scale.pct(now));
}

function renderClock(
  track: HTMLElement,
  scale: Scale,
  now: number,
  zone: string,
  label: string,
): void {
  const plot = addRow(track, 'ds-tl-clock', label, `${offsetLabel(now, zone)}`);
  addBackdrop(plot, scale, now);

  // Walk the window hour by hour and merge contiguous hours that share a
  // classification. Doing it per instant rather than per fixed offset is what
  // makes the bands DST-correct across a transition.
  const start = Math.floor(scale.t0 / HOUR_MS) * HOUR_MS;
  type Kind = 'night' | 'plan' | null;
  const kindAt = (ms: number): Kind => {
    const h = localHour(ms, zone);
    if (PLANNING_WINDOWS.some((w) => h >= w.from && h < w.to)) return 'plan';
    if (h >= NIGHT_FROM || h < NIGHT_TO) return 'night';
    return null;
  };

  let runStart = start;
  let runKind = kindAt(start);
  const flush = (end: number) => {
    if (runKind && end > runStart) {
      const band = div(`ds-tl-band ${runKind}`, plot);
      place(band, scale.pct(runStart), scale.pct(end) - scale.pct(runStart));
      // No in-band caption: at this scale a three-hour band is ~40px wide and
      // any label inside it collides with the hour readout. The legend names
      // the shading once instead.
      band.title = runKind === 'plan'
        ? t('dataSources.timeline.planning')
        : t('dataSources.timeline.night');
    }
  };
  for (let ms = start + HOUR_MS; ms <= scale.t1 + HOUR_MS; ms += HOUR_MS) {
    const k = kindAt(ms);
    if (k !== runKind) {
      flush(ms);
      runStart = ms;
      runKind = k;
    }
  }
  flush(scale.t1);

  // Local hour readout every three hours, aligned to the UTC ruler ticks.
  const tickStart = Math.ceil(scale.t0 / HOUR_MS) * HOUR_MS;
  for (let ms = tickStart; ms <= scale.t1; ms += 3 * HOUR_MS) {
    const lbl = div('ds-tl-localhour', plot);
    lbl.textContent = String(localHour(ms, zone)).padStart(2, '0');
    place(lbl, scale.pct(ms));
  }
}

function runTooltip(entry: DataSourceEntry, run: Run): string {
  const lines: string[] = [];
  lines.push(`<s>${escapeHtml(entry.model_label)} · ${escapeHtml(entry.provider_label)}</s>`);
  lines.push(`<b>${t('dataSources.timeline.tip.init')}</b> ${zulu(run.init)}`);
  lines.push(`<b>${t('dataSources.timeline.tip.expected')}</b> ${zulu(run.expected)}`);
  if (run.actual !== null) {
    const deltaMin = (run.actual - run.expected) / 60000;
    lines.push(
      `<b>${t('dataSources.timeline.tip.actual')}</b> ${zulu(run.actual)}`
      + ` <em>(${escapeHtml(deltaLabel(deltaMin))})</em>`,
    );
  } else if (run.expected > Date.now()) {
    lines.push(`<em>${t('dataSources.timeline.tip.upcoming')}</em>`);
  }
  lines.push(
    `<b>${t('dataSources.timeline.tip.horizon')}</b> ${run.horizonH}h`
    + (run.short ? ` · ${t('dataSources.timeline.shortRun')}` : ''),
  );
  return lines.join('<br>');
}

function renderSource(
  track: HTMLElement,
  scale: Scale,
  now: number,
  entry: DataSourceEntry,
  isFirstOfModel: boolean,
  tip: HTMLElement,
): void {
  const slot = ROLE_SLOT[entry.role] ?? '4';
  const plot = addRow(
    track,
    `ds-tl-source${isFirstOfModel ? ' ds-tl-group' : ''}`,
    entry.model_label,
    entry.provider_label,
  );
  plot.style.setProperty('--ds-tl-hue', `var(--ds-tl-role-${slot})`);
  addBackdrop(plot, scale, now);

  // Delivery offsets are routinely longer than the gap between cycles (ECMWF
  // waits 6h40m for a run every 6h), so consecutive runs genuinely overlap in
  // time. Drawn on one line they fuse into a single continuous band and the
  // per-run rhythm — the whole point of the row — disappears. Alternating two
  // lanes keeps every run's start and end readable.
  let lane = 0;
  for (const run of runsInWindow(entry, scale.t0, scale.t1)) {
    const laneCls = lane % 2 === 0 ? ' lane-a' : ' lane-b';
    lane += 1;
    const future = run.expected > now;
    const x0 = Math.max(scale.pct(run.init), 0);
    const x1 = Math.min(scale.pct(run.expected), 100);

    const bar = div(
      `ds-tl-run${laneCls}${run.short ? ' short' : ''}${future ? ' future' : ''}`,
      plot,
    );
    place(bar, x0, x1 - x0);
    bar.tabIndex = 0;
    bar.setAttribute('role', 'img');
    bar.setAttribute(
      'aria-label',
      `${entry.model_label} ${entry.provider_label}, `
      + `${t('dataSources.timeline.tip.init')} ${zulu(run.init)}, `
      + `${t('dataSources.timeline.tip.expected')} ${zulu(run.expected)}`
      + (run.actual !== null
        ? `, ${t('dataSources.timeline.tip.actual')} ${zulu(run.actual)}`
        : ''),
    );

    // Init tick at the left end of the bar.
    if (run.init >= scale.t0) {
      const tick = div(`ds-tl-init${future ? ' future' : ''}`, plot);
      place(tick, scale.pct(run.init));
    }
    // Hollow ring at the expected delivery.
    if (run.expected <= scale.t1) {
      const ring = div(`ds-tl-expected${laneCls}${future ? ' future' : ''}`, plot);
      place(ring, scale.pct(run.expected));
    }

    // Realised delivery: filled dot, plus a connector spanning the gap so the
    // drift reads as a length rather than as two dots to compare by eye.
    if (run.actual !== null && run.actual >= scale.t0 && run.actual <= scale.t1) {
      const deltaMin = (run.actual - run.expected) / 60000;
      const late = deltaMin > LATE_TOLERANCE_MIN;
      const from = Math.min(scale.pct(run.expected), scale.pct(run.actual));
      const to = Math.max(scale.pct(run.expected), scale.pct(run.actual));
      if (to - from > 0.05) {
        const link = div(`ds-tl-drift${laneCls}${late ? ' late' : ''}`, plot);
        place(link, from, to - from);
      }
      const dot = div(`ds-tl-actual${laneCls}${late ? ' late' : ''}`, plot);
      place(dot, scale.pct(run.actual));

      // Sits above the lanes on its own line with a surface backing: at this
      // density it would otherwise be struck through by the next run's bar.
      const chip = div(`ds-tl-delta${late ? ' late' : ''}`, plot);
      chip.textContent = deltaLabel(deltaMin);
      place(chip, scale.pct(run.actual));
    }

    const show = () => {
      tip.innerHTML = runTooltip(entry, run);
      tip.classList.add('visible');
      const scroller = tip.parentElement as HTMLElement;
      const scrollRect = scroller.getBoundingClientRect();
      const barRect = bar.getBoundingClientRect();
      const left = barRect.left - scrollRect.left + scroller.scrollLeft;
      const maxLeft = scroller.scrollLeft + scrollRect.width - tip.offsetWidth - 8;
      tip.style.left = `${Math.max(8, Math.min(left, maxLeft))}px`;
      tip.style.top = `${barRect.top - scrollRect.top + scroller.scrollTop - tip.offsetHeight - 8}px`;
    };
    const hide = () => tip.classList.remove('visible');
    bar.addEventListener('mouseenter', show);
    bar.addEventListener('focus', show);
    bar.addEventListener('mouseleave', hide);
    bar.addEventListener('blur', hide);
  }
}

function renderLegend(host: HTMLElement): void {
  const legend = div('ds-tl-legend', host);
  // Same keys the table's role badges use — one label set, not two.
  const roleKeys: Array<[string, string]> = [
    ['1', 'dataSources.role.primary_sounding'],
    ['2', 'dataSources.role.cloud_enrichment'],
    ['3', 'dataSources.role.surface_base'],
    ['4', 'dataSources.role.primary'],
  ];
  for (const [slot, key] of roleKeys) {
    const item = div('ds-tl-legend-item', legend);
    const sw = div('ds-tl-sw', item);
    sw.style.background = `var(--ds-tl-role-${slot})`;
    item.appendChild(document.createTextNode(t(key)));
  }
  const marks: Array<[string, string]> = [
    ['ds-tl-sw-expected', 'dataSources.timeline.legend.expected'],
    ['ds-tl-sw-actual', 'dataSources.timeline.legend.actual'],
    ['ds-tl-sw-late', 'dataSources.timeline.legend.late'],
    ['ds-tl-sw-short', 'dataSources.timeline.legend.short'],
    ['ds-tl-sw-plan', 'dataSources.timeline.legend.planning'],
  ];
  for (const [cls, key] of marks) {
    const item = div('ds-tl-legend-item', legend);
    div(`ds-tl-sw ${cls}`, item);
    item.appendChild(document.createTextNode(t(key)));
  }
}

/** Render the timeline into `host`, replacing its contents. */
export function mountDataSourcesTimeline(
  host: HTMLElement,
  sources: DataSourceEntry[],
  generatedAt: string,
): void {
  host.innerHTML = '';

  const intro = div('muted data-sources-intro', host);
  intro.textContent = t('dataSources.timeline.intro');

  const now = Date.now();
  const t0 = Math.floor(now / HOUR_MS) * HOUR_MS - PAST_HOURS * HOUR_MS;
  const scale = makeScale(t0, t0 + SPAN_HOURS * HOUR_MS);

  const scroller = div('ds-tl-scroll', host);
  const track = div('ds-tl-track', scroller);
  const tip = div('ds-tl-tip', scroller);

  renderRuler(track, scale, now);

  // Viewer's own zone first — the strip should answer "when is *my* data
  // fresh?" before it answers the same question for a reference city.
  const viewerZone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  const zones: Array<{ zone: string; label: string }> = [];
  // Skip the viewer row when it would draw the same clock twice — matching by
  // *current offset*, not zone id, because Europe/Zurich and Europe/Paris are
  // different zones that read identically on this strip.
  const viewerOffset = viewerZone ? zoneOffsetHours(now, viewerZone) : null;
  const duplicatesReference = viewerOffset !== null
    && REFERENCE_ZONES.some((r) => zoneOffsetHours(now, r.zone) === viewerOffset);
  if (viewerZone && !duplicatesReference) {
    zones.push({ zone: viewerZone, label: t('dataSources.timeline.zone.you') });
  }
  for (const ref of REFERENCE_ZONES) {
    zones.push({ zone: ref.zone, label: t(ref.labelKey) });
  }
  for (const z of zones) {
    try {
      renderClock(track, scale, now, z.zone, z.label);
    } catch {
      // An unknown IANA zone (stale browser data) must not take the view down.
    }
  }

  const seenModels = new Set<string>();
  for (const entry of sources) {
    const isFirst = !seenModels.has(entry.model);
    seenModels.add(entry.model);
    renderSource(track, scale, now, entry, isFirst, tip);
  }

  renderLegend(host);

  const footer = div('muted data-sources-footer', host);
  footer.textContent = t('dataSources.timeline.footer', {
    ts: new Date(generatedAt).toISOString().slice(11, 16) + 'Z',
  });
}
