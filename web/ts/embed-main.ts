/**
 * Embeddable cross-section: one flight's pack, full-bleed, no app chrome.
 *
 *   /embed.html?flight=<id>[&pack=<ts>][&model=<m>][&layers=a,b,c][&point=<i>][&theme=<id>]
 *
 * For pages that frame a briefing view (talks, help) and want to drive it:
 * the parent posts `{type: 'wb-embed:layers', layers: [...]}` (exactly these
 * layers on) or `{type: 'wb-embed:point', index}` / `{type: 'wb-embed:model',
 * model}`; the embed answers `{type: 'wb-embed:ready', models, layers}` once
 * the data is drawn. Read-only: it shows what the viewer could already open
 * on the briefing page, under the same auth. A parent should also handle
 * `wb-frame:auth-required` (utils.redirectToLogin, on a 401); the embed never
 * calls renderUserInfo, so it sends `wb-embed:ready`, not `wb-frame:ready`.
 */

import {
  fetchElevationProfile, fetchFlight, fetchLatestPack, fetchPack, fetchRouteAnalyses,
  fetchRouteFronts, fetchSnapshot,
} from './adapters/api-adapter';
import type { ElevationProfile, ForecastSnapshot, RouteAnalysesManifest } from './store/types';
import type { RouteFrontsManifest } from './types/fronts';
import { initI18n } from './i18n/i18n';
import { extractVizData } from './visualization/data-extract';
import { CrossSectionRenderer } from './visualization/cross-section/renderer';
import { getAllLayers, getDefaultEnabled } from './visualization/cross-section/layer-registry';
import { setActiveTheme, THEMES, type ThemeId } from './visualization/cross-section/theme';

interface EmbedData {
  manifest: RouteAnalysesManifest;
  ceilingFt: number | undefined;
  snapshot: ForecastSnapshot | null;
  elevation: ElevationProfile | null;
  fronts: RouteFrontsManifest | null;
}

const params = new URLSearchParams(location.search);
const container = document.getElementById('embed-xsection')!;
const status = document.getElementById('embed-status')!;
const theme = params.get('theme');
if (theme && theme in THEMES) setActiveTheme(theme as ThemeId);
const renderer = new CrossSectionRenderer(container);

let data: EmbedData | null = null;
let model = params.get('model') ?? '';
let enabled = layersFromList(params.get('layers'));
let point = pointIndex(params.get('point'));

/** A route-point index, or -1 (none) for anything that isn't a number. */
function pointIndex(v: unknown): number {
  if (v == null || v === '') return -1;
  const n = Number(v);
  return Number.isFinite(n) ? n : -1;
}

/** Exactly the listed layers on; null (no list) keeps the briefing defaults. */
function layersFromList(list: string | string[] | null): Record<string, boolean> {
  if (list == null) return getDefaultEnabled();
  const on = new Set(Array.isArray(list) ? list : list.split(',').filter(Boolean));
  const out: Record<string, boolean> = {};
  for (const layer of getAllLayers()) out[layer.id] = on.has(layer.id);
  return out;
}

function draw(): void {
  if (!data) return;
  const viz = extractVizData(data.manifest, model, data.ceilingFt, data.elevation, {
    routeObservations: data.snapshot?.route_observations,
    routeSigmets: data.snapshot?.route_sigmets,
    routeFronts: data.fronts,
    observedConditions: data.snapshot?.observed_conditions,
  });
  renderer.setData(viz);
  renderer.setLayers(getAllLayers(), enabled);
  renderer.setSelectedPointIndex(point);
  renderer.render();
}

/** To the framing page. '*' on purpose: the messages carry only model and
 *  layer ids or an error string, and frame-ancestors already limits who can
 *  frame this page (as utils.ts's wb-frame:* messages). */
function post(msg: object): void {
  if (window.parent !== window) window.parent.postMessage(msg, '*');
}

window.addEventListener('message', (ev: MessageEvent) => {
  // Only a framing page drives the view (unframed, parent === window).
  if (window.parent === window || ev.source !== window.parent) return;
  const msg = ev.data;
  if (!msg || typeof msg !== 'object' || typeof msg.type !== 'string') return;
  if (msg.type === 'wb-embed:layers') enabled = layersFromList(msg.layers ?? null);
  else if (msg.type === 'wb-embed:point') point = pointIndex(msg.index);
  else if (msg.type === 'wb-embed:model' && data?.manifest.models.includes(msg.model)) model = msg.model;
  else return;
  draw();
});

async function load(): Promise<void> {
  const flight = params.get('flight');
  if (!flight) throw new Error('missing ?flight=');
  await initI18n();
  const ts = params.get('pack');
  const pack = ts ? await fetchPack(flight, ts) : await fetchLatestPack(flight);
  const at = pack.fetch_timestamp;
  const [manifest, snapshot, elevation, fronts, flightRow] = await Promise.allSettled([
    fetchRouteAnalyses(flight, at),
    fetchSnapshot(flight, at),
    fetchElevationProfile(flight, at),
    fetchRouteFronts(flight, at),
    fetchFlight(flight),
  ]);
  if (manifest.status !== 'fulfilled') throw new Error('no route analyses for this pack');
  const value = <T,>(r: PromiseSettledResult<T>): T | null => (r.status === 'fulfilled' ? r.value : null);
  data = {
    manifest: manifest.value,
    ceilingFt: value(flightRow)?.flight_ceiling_ft,
    snapshot: value(snapshot),
    elevation: value(elevation),
    fronts: value(fronts),
  };
  const models = data.manifest.models;
  if (!models.includes(model)) {
    model = models.includes('ecmwf') ? 'ecmwf' : models.includes('gfs') ? 'gfs' : models[0];
  }
  status.remove();
  draw();
  post({ type: 'wb-embed:ready', models, model, layers: getAllLayers().map((l) => l.id) });
}

load().catch((err: Error) => {
  status.textContent = err.message;
  post({ type: 'wb-embed:error', message: err.message });
});
