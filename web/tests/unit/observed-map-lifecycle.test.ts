import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { RouteMapRenderer } from '../../ts/visualization/route-map/renderer';
import type { VizRouteData } from '../../ts/visualization/types';
import { makeVizPoint } from './fixtures/viz-point';

// Only Leaflet's browser drawing boundary is replaced. The renderer, overlay,
// fetch/provenance parser, request ownership and display clock all stay real.
const drawing = vi.hoisted(() => {
  class Layer {
    layers: Layer[] = [];
    constructor(readonly kind: string, readonly options: Record<string, unknown> = {}) {}
    addTo(parent: Layer) { parent.addLayer(this); return this; }
    addLayer(layer: Layer) { this.layers.push(layer); return this; }
    clearLayers() { this.layers = []; return this; }
    remove() { this.clearLayers(); }
    fitBounds() {}
  }
  return { Layer, maps: [] as Layer[], images: [] as Layer[] };
});

vi.mock('leaflet', () => ({
  map: () => {
    const map = new drawing.Layer('map');
    drawing.maps.push(map);
    return map;
  },
  tileLayer: () => new drawing.Layer('tiles'),
  layerGroup: () => new drawing.Layer('group'),
  polyline: (_points: unknown, options: Record<string, unknown>) => new drawing.Layer('line', options),
  rectangle: (_bounds: unknown, options: Record<string, unknown>) => new drawing.Layer('rectangle', options),
  circleMarker: (_point: unknown, options: Record<string, unknown>) => new drawing.Layer('flash', options),
  imageOverlay: (url: string, _bounds: unknown, options: Record<string, unknown>) => {
    const image = new drawing.Layer('image', { ...options, url });
    drawing.images.push(image);
    return image;
  },
  latLngBounds: (southwest: number[], northeast: number[]) => ({
    getSouth: () => southwest[0], getWest: () => southwest[1],
    getNorth: () => northeast[0], getEast: () => northeast[1],
  }),
}));

// Custom map labels are ordinary DOM children, outside Leaflet's own panes.
// Leaflet map.remove() must not magically remove those renderer-owned children.
class Element {
  className = '';
  textContent = '';
  innerHTML = '';
  clientWidth = 800;
  clientHeight = 500;
  children: Element[] = [];
  parent: Element | null = null;
  appendChild(child: Element) { child.parent = this; this.children.push(child); return child; }
  remove() {
    if (this.parent) this.parent.children = this.parent.children.filter(child => child !== this);
    this.parent = null;
  }
  querySelector(selector: string) { return this.children.find(child => `.${child.className}` === selector) ?? null; }
}

function route(): VizRouteData {
  return {
    points: [makeVizPoint({ lat: 51, lon: 0 }), makeVizPoint({ lat: 52, lon: 1, distanceNm: 70 })],
    cruiseAltitudeFt: 6000, ceilingAltitudeFt: 18000, flightCeilingFt: 23000,
    totalDistanceNm: 70, waypointMarkers: [], departureTime: '2026-09-05T12:00:00Z',
    flightDurationHours: 1, terrainProfile: null, currentConditions: null,
    fronts: null, nightIntervals: [], sunSide: null, advisoryHighlights: null,
    observed: {
      radiusNm: 20, radiiNm: [5, 10, 20], points: [], summaryLines: [],
      rainRate: null, cloudTops: null, lightning: null,
      reflectivity: {
        source: 'opera_dbzh', label: 'Radar reflectivity', validTime: '2026-09-05T11:30:00Z',
        ageMinutes: 0, windowMinutes: 10, attribution: 'Snapshot producer',
      },
    },
  };
}

function imageResponse(): Response {
  return new Response(new Uint8Array([137, 80, 78, 71]), {
    headers: {
      'Content-Type': 'image/png', 'X-Observed-Valid-Time': '2026-09-05T11:55:00Z',
      'X-Observed-Window-Minutes': '10', 'X-Observed-Attribution': 'Image producer',
    },
  });
}

function visibleLayers(kind: string) {
  const flatten = (layer: InstanceType<typeof drawing.Layer>): InstanceType<typeof drawing.Layer>[] =>
    [layer, ...layer.layers.flatMap(flatten)];
  return drawing.maps.flatMap(flatten).filter(layer => layer.kind === kind);
}

describe('RouteMapRenderer observed lifecycle', () => {
  let container: Element;
  let renderer: RouteMapRenderer;
  let page: EventTarget;
  let doc: EventTarget;

  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-09-05T12:00:00Z'));
    drawing.maps.length = 0;
    drawing.images.length = 0;
    page = new EventTarget();
    doc = Object.assign(new EventTarget(), {
      visibilityState: 'visible', documentElement: { dataset: { theme: 'light' } },
      createElement: () => new Element(),
    });
    vi.stubGlobal('window', page);
    vi.stubGlobal('document', doc);
    container = new Element();
    renderer = new RouteMapRenderer(container as unknown as HTMLElement);
    renderer.setData(route());
    renderer.setObservedSource('opera_dbzh');
  });

  afterEach(() => {
    renderer.destroy();
    vi.useRealTimers();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it('removes renderer-owned observed labels and stops their clock when destroyed', () => {
    // Catches the missing observedLegendEl.remove() in renderer.destroy().
    vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>(() => {})));
    renderer.setObservedLegends(new Map([['opera_dbzh', {
      source: 'opera_dbzh', label: 'Radar reflectivity', units: 'dBZ',
      legend: [{ value: 0, color: '#00ff00' }, { value: 60, color: '#ff0000' }],
    }]]));
    renderer.render();
    expect(container.querySelector('.map-observed-legend')?.innerHTML).toContain('Radar reflectivity');
    expect(container.querySelector('.map-observed-badge')?.textContent).toContain('loading');
    expect(vi.getTimerCount()).toBeGreaterThan(0);

    renderer.destroy();

    expect.soft(container.querySelector('.map-observed-legend')).toBeNull();
    expect.soft(container.querySelector('.map-observed-badge')).toBeNull();
    expect.soft(vi.getTimerCount()).toBe(0);
    expect(renderer.getMap()).toBeNull();
  });

  it('discards pending image completion after destruction without creating URLs or repainting', async () => {
    // Losing request disposal would create an unowned object URL on late success.
    let finish!: (response: Response) => void;
    const fetchImage = vi.fn((_url: string) => new Promise<Response>(resolve => { finish = resolve; }));
    vi.stubGlobal('fetch', fetchImage);
    const createUrl = vi.spyOn(URL, 'createObjectURL');
    renderer.render();
    expect(fetchImage).toHaveBeenCalledTimes(1);
    expect(String(fetchImage.mock.calls[0][0])).toContain('/api/observed/overlay/opera_dbzh.png?');
    renderer.destroy();

    finish(imageResponse());
    await vi.advanceTimersByTimeAsync(0);
    await vi.advanceTimersByTimeAsync(120000);
    doc.dispatchEvent(new Event('visibilitychange'));
    page.dispatchEvent(new Event('pageshow'));

    expect(createUrl).not.toHaveBeenCalled();
    expect(drawing.images).toHaveLength(0);
    expect(container.children).toHaveLength(0);
    expect(visibleLayers('rectangle')).toHaveLength(0);
    expect(fetchImage).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);
  });

  it('ages and expires lightning locally while keeping the loaded raster untouched', async () => {
    // A missing renderer clock subscription leaves old markers visible; calling
    // full renderObserved() on each tick would reconstruct the raster instead.
    const fetchImage = vi.fn(async () => imageResponse());
    vi.stubGlobal('fetch', fetchImage);
    renderer.setObservedOpacity(0.4);
    renderer.render();
    await vi.advanceTimersByTimeAsync(0);
    expect(visibleLayers('image')).toHaveLength(1);
    expect(visibleLayers('image')[0].options.opacity).toBe(0.4);
    expect(container.querySelector('.map-observed-badge')?.textContent).toContain('5 min old');
    expect(container.querySelector('.map-observed-badge')?.textContent).toContain('Image producer');
    expect(container.querySelector('.map-observed-badge')?.textContent).not.toContain('11:30Z');

    renderer.setObservedFlashes([{ lat: 51.5, lon: 0.5, time: '2026-09-05T11:01:30Z' }]);
    renderer.refreshObserved();
    const raster = visibleLayers('image')[0];
    const imageConstructions = drawing.images.length;
    expect(visibleLayers('flash')).toHaveLength(1);
    expect(visibleLayers('flash')[0].options.fillOpacity).toBeCloseTo(0.0225);

    await vi.advanceTimersByTimeAsync(60000);
    expect(visibleLayers('flash')).toHaveLength(1);
    expect(visibleLayers('flash')[0].options.fillOpacity).toBeCloseTo(0.0075);
    expect(container.querySelector('.map-observed-badge')?.textContent).toContain('6 min old');
    await vi.advanceTimersByTimeAsync(60000);
    expect(visibleLayers('flash')).toHaveLength(0);
    expect(container.querySelector('.map-observed-badge')?.textContent).toContain('7 min old');
    expect(visibleLayers('image')[0]).toBe(raster);
    expect(drawing.images).toHaveLength(imageConstructions);
    expect(fetchImage).toHaveBeenCalledTimes(1);
  });
});
