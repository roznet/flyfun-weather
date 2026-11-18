/** Debrief report prototype (phase 0, not for merge): the "Along the route"
 *  card of `/debrief-report.html`, built from the Observed tab's own pieces
 *  so it reads and behaves the same:
 *
 *  - the recap ribbon (`mountRecap`) by default;
 *  - the recorded-tick stepper (`tickStepperHtml`): picking a tick, a recap
 *    stretch, an alert marker or a point on the route shows the ribbon as the
 *    app drew it then (`/live/at`), with "Back to recap".
 *
 *  The rest of the page (headline, questions, details) is plain HTML. */

import { fetchLiveAt, fetchLiveTicks, fetchRecap } from './adapters/api-adapter';
import {
  forecastLaneBands,
  recapTimePoints,
  tickForRoutePoint,
} from './helpers/debrief-replay';
import { formatHhmmZ } from './helpers/live-layer';
import { initI18n, t } from './i18n/i18n';
import type { LiveTickEntry } from './store/types';
import { escapeHtml } from './utils';
import { modeBarHtml, mountRecap, tickStepperHtml } from './visualization/observed/recap-view';
import { mountRibbon, stormDetailHtml } from './visualization/observed/ribbon-view';

async function mountDebriefRecap(el: HTMLElement, flightId: string): Promise<void> {
  await initI18n();
  const [recap, ticksRes] = await Promise.all([
    fetchRecap(flightId),
    fetchLiveTicks(flightId).catch(() => ({ ticks: [] as LiveTickEntry[] })),
  ]);
  const ticks = (ticksRes.ticks || []).filter((tk) => tk.tick_at);
  el.innerHTML = '<div class="debrief-stepper"></div><div class="mode-bar" hidden></div>'
    + '<div class="debrief-view"></div><div class="debrief-storm" hidden></div>';
  const stepperEl = el.querySelector<HTMLElement>('.debrief-stepper')!;
  const barEl = el.querySelector<HTMLElement>('.mode-bar')!;
  const viewEl = el.querySelector<HTMLElement>('.debrief-view')!;
  const stormEl = el.querySelector<HTMLElement>('.debrief-storm')!;

  let teardown: (() => void) | null = null;
  let selected: string | null = null;
  let seq = 0;

  const showStorm = (html: string): void => { stormEl.hidden = false; stormEl.innerHTML = html; };

  const drawStepper = (): void => {
    stepperEl.innerHTML = ticks.length ? tickStepperHtml(ticks, selected) : '';
  };

  const showRecap = (): void => {
    selected = null;
    seq++;
    teardown?.();
    barEl.hidden = true;
    stormEl.hidden = true;
    drawStepper();
    const forecast = recap.forecast ?? null;
    const handle = mountRecap(viewEl, {
      recap, forecast, bands: forecastLaneBands(forecast), ticks, showGrid: false,
    }, {
      onTick: (tick) => { void showTick(tick); },
      onRoutePoint: (nm) => {
        const tk = tickForRoutePoint(nm, recapTimePoints(recap), ticks);
        if (tk?.tick_at) void showTick(tk.tick_at);
      },
      onStorm: (storm) => showStorm(stormDetailHtml(storm)),
    }, () => { /* no grid on this page */ });
    teardown = handle.teardown;
  };

  const showTick = async (tick: string): Promise<void> => {
    selected = tick;
    const mine = ++seq;
    drawStepper();
    barEl.hidden = false;
    barEl.innerHTML = modeBarHtml(`Viewing ${formatHhmmZ(tick)}`, t('debrief.backToRecap'));
    try {
      const view = await fetchLiveAt(flightId, tick);
      if (mine !== seq) return;
      teardown?.();
      teardown = null;
      stormEl.hidden = true;
      if (!view.ribbon) {
        viewEl.innerHTML = `<p class="muted">Nothing was drawn along the route at ${escapeHtml(formatHhmmZ(tick))}.</p>`;
        return;
      }
      viewEl.innerHTML = '';
      teardown = mountRibbon(viewEl, view.ribbon, view.storms, {
        onFocus: () => { /* no map on this page */ },
        onStorm: (storm) => showStorm(stormDetailHtml(storm)),
      }, view.route_observations?.airports ?? null, view.glance?.flight?.marker_label ?? null);
    } catch (e) {
      if (mine !== seq) return;
      viewEl.innerHTML = `<p class="err">Could not load ${escapeHtml(formatHhmmZ(tick))}: ${escapeHtml(String(e))}</p>`;
    }
  };

  stepperEl.addEventListener('click', (ev) => {
    const b = (ev.target as Element | null)?.closest<HTMLElement>('[data-tick]');
    if (b?.dataset.tick) void showTick(b.dataset.tick);
  });
  stepperEl.addEventListener('change', (ev) => {
    const sel = ev.target as HTMLSelectElement;
    if (sel.matches('.tick-select') && sel.value) void showTick(sel.value);
  });
  barEl.addEventListener('click', (ev) => {
    if ((ev.target as Element | null)?.closest('[data-testid="mode-bar-back"]')) showRecap();
  });

  showRecap();
}

(window as unknown as { wbMountDebriefRecap: typeof mountDebriefRecap }).wbMountDebriefRecap = mountDebriefRecap;
