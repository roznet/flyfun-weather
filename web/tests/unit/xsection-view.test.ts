/**
 * The shared cross-section view (#819): the state the embed and the MCP App
 * widget both drive. A fake renderer records what draw() hands it.
 */
import { describe, expect, it } from 'vitest';
import manifestJson from '../fixtures/egtf_eglf/route_analyses.json';
import elevationJson from '../fixtures/egtf_eglf/elevation.json';
import {
  layersFromList, layersWithExtra, pickModel, pointIndex, XsectionView, type EmbedData,
} from '../../ts/embed/xsection-view';
import { getDefaultEnabled } from '../../ts/visualization/cross-section/layer-registry';
import type { RouteAnalysesManifest, ElevationProfile } from '../../ts/store/types';

function fakeRenderer() {
  const calls: { setData: unknown[]; enabled: Record<string, boolean>[]; point: number[]; renders: number } = {
    setData: [], enabled: [], point: [], renders: 0,
  };
  return {
    calls,
    setData: (d: unknown) => { calls.setData.push(d); },
    setLayers: (_l: unknown, e: Record<string, boolean>) => { calls.enabled.push(e); },
    setSelectedPointIndex: (i: number) => { calls.point.push(i); },
    render: () => { calls.renders++; },
  };
}

const data: EmbedData = {
  manifest: manifestJson as unknown as RouteAnalysesManifest,
  ceilingFt: 18000,
  snapshot: null,
  elevation: elevationJson as unknown as ElevationProfile,
  fronts: null,
};

describe('helpers', () => {
  it('pointIndex reads numbers, -1 otherwise', () => {
    expect(pointIndex('3')).toBe(3);
    expect(pointIndex(null)).toBe(-1);
    expect(pointIndex('')).toBe(-1);
    expect(pointIndex('x')).toBe(-1);
  });

  it('layersFromList is exact; null keeps the defaults', () => {
    expect(layersFromList(null)).toEqual(getDefaultEnabled());
    const on = Object.entries(layersFromList('cat-bands,terrain')).filter(([, v]) => v).map(([k]) => k);
    expect(on.sort()).toEqual(['cat-bands', 'terrain']);
  });

  it('layersWithExtra adds to the defaults and ignores unknown ids', () => {
    const out = layersWithExtra(['sld-bands', 'nope']);
    expect(out['sld-bands']).toBe(true);
    expect(out.terrain).toBe(true);
    expect('nope' in out).toBe(false);
  });

  it('pickModel: requested, ecmwf, gfs, first', () => {
    expect(pickModel(['ecmwf', 'gfs'], 'gfs')).toBe('gfs');
    expect(pickModel(['ecmwf', 'gfs'], 'icon')).toBe('ecmwf');
    expect(pickModel(['icon', 'gfs'], null)).toBe('gfs');
    expect(pickModel(['icon'], undefined)).toBe('icon');
  });
});

describe('XsectionView', () => {
  it('does nothing before data', () => {
    const r = fakeRenderer();
    const view = new XsectionView(r);
    view.draw();
    expect(r.calls.renders).toBe(0);
    expect(view.setModel('gfs')).toBe(false);
  });

  it('draws with the picked model and the given state', () => {
    const r = fakeRenderer();
    const view = new XsectionView(r, { model: 'nope', point: 2 });
    view.setData(data);
    expect(view.model).toBe('ecmwf');
    view.draw();
    expect(r.calls.renders).toBe(1);
    expect(r.calls.point).toEqual([2]);
    expect(view.viz?.points.length).toBe(data.manifest.analyses.length);
    expect(view.viz?.ceilingAltitudeFt).toBe(18000);
  });

  it('model, layer and point changes reach the renderer', () => {
    const r = fakeRenderer();
    const view = new XsectionView(r);
    view.setData(data);
    expect(view.setModel('gfs')).toBe(true);
    expect(view.setModel('icon')).toBe(false);
    expect(view.model).toBe('gfs');
    view.setLayer('cat-bands', true);
    view.setPoint(4);
    view.draw();
    expect(r.calls.enabled.at(-1)!['cat-bands']).toBe(true);
    expect(r.calls.point.at(-1)).toBe(4);
    expect(view.enabledIds()).toContain('cat-bands');
  });

  it('new data keeps the current model when it has it', () => {
    const view = new XsectionView(fakeRenderer());
    view.setData(data);
    view.setModel('gfs');
    view.setData(data);
    expect(view.model).toBe('gfs');
  });
});
