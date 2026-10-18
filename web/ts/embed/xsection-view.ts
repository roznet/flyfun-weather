/**
 * One flight's cross-section with its view state (model, layers, selected
 * point), shared by the framed embed (`embed-main.ts`, REST + `wb-embed:*`)
 * and the MCP App widget (`mcp-app/cross-section-main.ts`, tools/call). The
 * loaders differ; the state and `draw()` live here once.
 */

import type { ElevationProfile, ForecastSnapshot, RouteAnalysesManifest } from '../store/types';
import type { RouteFrontsManifest } from '../types/fronts';
import type { VizRouteData } from '../visualization/types';
import { extractVizData } from '../visualization/data-extract';
import type { CrossSectionRenderer } from '../visualization/cross-section/renderer';
import { getAllLayers, getDefaultEnabled } from '../visualization/cross-section/layer-registry';

/** Everything `draw()` needs; the snapshot only for its three observed keys. */
export interface EmbedData {
  manifest: RouteAnalysesManifest;
  ceilingFt: number | undefined;
  snapshot: Pick<ForecastSnapshot, 'route_observations' | 'route_sigmets' | 'observed_conditions'> | null;
  elevation: ElevationProfile | null;
  fronts: RouteFrontsManifest | null;
}

/** The renderer surface the view drives (an interface so tests can fake it). */
export type XsectionRenderer = Pick<
  CrossSectionRenderer, 'setData' | 'setLayers' | 'setSelectedPointIndex' | 'render'
>;

/** A route-point index, or -1 (none) for anything that isn't a number. */
export function pointIndex(v: unknown): number {
  if (v == null || v === '') return -1;
  const n = Number(v);
  return Number.isFinite(n) ? n : -1;
}

/** Exactly the listed layers on; null (no list) keeps the briefing defaults. */
export function layersFromList(list: string | string[] | null): Record<string, boolean> {
  if (list == null) return getDefaultEnabled();
  const on = new Set(Array.isArray(list) ? list : list.split(',').filter(Boolean));
  const out: Record<string, boolean> = {};
  for (const layer of getAllLayers()) out[layer.id] = on.has(layer.id);
  return out;
}

/** The briefing defaults with the listed layers switched on as well. */
export function layersWithExtra(extra: readonly string[] | null | undefined): Record<string, boolean> {
  const out = getDefaultEnabled();
  for (const id of extra ?? []) if (id in out) out[id] = true;
  return out;
}

/** The requested model when the pack has it, else ECMWF, GFS, the first. */
export function pickModel(models: readonly string[], requested: string | null | undefined): string {
  if (requested && models.includes(requested)) return requested;
  return models.includes('ecmwf') ? 'ecmwf' : models.includes('gfs') ? 'gfs' : models[0];
}

export class XsectionView {
  data: EmbedData | null = null;
  model: string;
  enabled: Record<string, boolean>;
  point: number;
  /** The last extracted viz data (null before the first draw). */
  viz: VizRouteData | null = null;

  constructor(
    private readonly renderer: XsectionRenderer,
    init: { model?: string | null; enabled?: Record<string, boolean>; point?: number } = {},
  ) {
    this.model = init.model ?? '';
    this.enabled = init.enabled ?? getDefaultEnabled();
    this.point = init.point ?? -1;
  }

  get models(): string[] {
    return this.data?.manifest.models ?? [];
  }

  /** New data; keeps the current model when the data has it, else picks one. */
  setData(data: EmbedData): void {
    this.data = data;
    this.model = pickModel(data.manifest.models, this.model);
  }

  /** Switch model; false (and no change) when the pack doesn't have it. */
  setModel(model: string): boolean {
    if (!this.models.includes(model)) return false;
    this.model = model;
    return true;
  }

  setEnabled(enabled: Record<string, boolean>): void {
    this.enabled = enabled;
  }

  setLayer(id: string, on: boolean): void {
    this.enabled = { ...this.enabled, [id]: on };
  }

  setPoint(index: number): void {
    this.point = index;
  }

  /** The ids switched on, in registry order. */
  enabledIds(): string[] {
    return getAllLayers().filter((l) => this.enabled[l.id]).map((l) => l.id);
  }

  draw(): void {
    const data = this.data;
    if (!data) return;
    const viz = extractVizData(data.manifest, this.model, data.ceilingFt, data.elevation, {
      routeObservations: data.snapshot?.route_observations,
      routeSigmets: data.snapshot?.route_sigmets,
      routeFronts: data.fronts,
      observedConditions: data.snapshot?.observed_conditions,
    });
    this.viz = viz;
    this.renderer.setData(viz);
    this.renderer.setLayers(getAllLayers(), this.enabled);
    this.renderer.setSelectedPointIndex(this.point);
    this.renderer.render();
  }
}
