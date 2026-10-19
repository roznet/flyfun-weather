/**
 * MCP App widget pure parts (#819): the model-context text, the view-param
 * parse, and the tools/call loader (mocked callServerTool, cached per model).
 */
import { describe, expect, it, vi } from 'vitest';
import manifestJson from '../fixtures/egtf_eglf/route_analyses.json';
import { buildModelContext, describePoint, utc } from '../../ts/mcp-app/model-context';
import { parseViewParams, resultProblem, XsectionLoader, type ToolCallResult } from '../../ts/mcp-app/loader';
import type { RouteAnalysesManifest } from '../../ts/store/types';

const manifest = manifestJson as unknown as RouteAnalysesManifest;

describe('model context', () => {
  it('utc trims to minutes', () => {
    expect(utc('2026-02-25T16:10:07.255073+00:00')).toBe('2026-02-25 16:10Z');
    expect(utc('2026-02-25T17:00:00')).toBe('2026-02-25 17:00Z');
    expect(utc('garbage')).toBe('garbage');
  });

  it('describes a waypoint and an interpolated point; none for -1', () => {
    const first = describePoint(manifest, 0)!;
    expect(first.waypoint).toBe('EGTF (Fairoaks Airport)');
    expect(first.distanceNm).toBe(0);
    const mid = manifest.analyses.find((a) => !a.waypoint_icao);
    if (mid) expect(describePoint(manifest, mid.point_index)!.waypoint).toBeNull();
    expect(describePoint(manifest, -1)).toBeNull();
    expect(describePoint(manifest, 999)).toBeNull();
  });

  it('builds compact text with the selection and no arrays', () => {
    const text = buildModelContext({
      route: 'EGTF → EGLF',
      packTimestamp: '2026-02-25T16:10:07.255073+00:00',
      model: 'ecmwf',
      models: ['ecmwf', 'gfs'],
      layers: ['NWP Natural', 'Freezing Level'],
      point: describePoint(manifest, 0),
    });
    expect(text).toContain('Route: EGTF → EGLF. Briefing of 2026-02-25 16:10Z.');
    expect(text).toContain('Model shown: ECMWF (pack also has: gfs).');
    expect(text).toContain('Layers on: NWP Natural, Freezing Level.');
    expect(text).toContain('Selected point: index 0, waypoint EGTF (Fairoaks Airport), 0 NM along the route');
    expect(text).not.toMatch(/[[\]{}]/);
    expect(text.length).toBeLessThan(800);
  });

  it('says when nothing is selected', () => {
    const text = buildModelContext({
      route: 'A → B', packTimestamp: 'x', model: 'gfs', models: ['gfs'], layers: [], point: null,
    });
    expect(text).toContain('No route point selected.');
    expect(text).toContain('pack also has: none');
    expect(text).toContain('Layers on: none.');
  });
});

describe('view params', () => {
  const ok: ToolCallResult = {
    structuredContent: {
      flight_id: 'f1', pack_timestamp: 't1', model: 'gfs', layers: ['cat-bands', 3],
      point_index: 2, web_url: 'https://example.test/b',
    },
  };

  it('parses the show_cross_section result', () => {
    expect(parseViewParams(ok)).toEqual({
      flight_id: 'f1', pack_timestamp: 't1', model: 'gfs', layers: ['cat-bands'],
      point_index: 2, web_url: 'https://example.test/b',
    });
  });

  it('throws the server message for errors and statuses', () => {
    expect(() => parseViewParams({ structuredContent: { error: 'Flight x not found.' } })).toThrow('Flight x not found.');
    expect(() => parseViewParams({ structuredContent: { status: 'processing', message: 'Briefing is being generated.' } }))
      .toThrow('Briefing is being generated.');
    expect(() => parseViewParams({ isError: true, content: [{ type: 'text', text: 'boom' }] })).toThrow('boom');
    expect(() => parseViewParams({ structuredContent: {} })).toThrow();
  });

  it('a data payload is not a problem', () => {
    expect(resultProblem({ structuredContent: { route_analyses: {}, model: 'gfs' } })).toBeNull();
  });
});

describe('XsectionLoader', () => {
  const reply = (model: string): ToolCallResult => ({
    structuredContent: { model, route_analyses: manifest, ceiling_ft: 18000, snapshot: null, elevation: null, fronts: null },
  });

  it('calls xsection_data with the pack and caches per model', async () => {
    const call = vi.fn(async (p: { name: string; arguments: Record<string, unknown> }) =>
      reply((p.arguments.model as string) ?? 'ecmwf'));
    const loader = new XsectionLoader(call, 'f1', 't1');
    const first = await loader.load(null);
    expect(first.model).toBe('ecmwf');
    expect(first.data.ceilingFt).toBe(18000);
    expect(call).toHaveBeenLastCalledWith({ name: 'xsection_data', arguments: { flight_id: 'f1', pack_timestamp: 't1' } });
    await loader.load('ecmwf'); // the default's answer is cached under its name
    expect(call).toHaveBeenCalledTimes(1);
    await loader.load('gfs');
    await loader.load('gfs');
    expect(call).toHaveBeenCalledTimes(2);
    expect(call).toHaveBeenLastCalledWith({ name: 'xsection_data', arguments: { flight_id: 'f1', pack_timestamp: 't1', model: 'gfs' } });
  });

  it('rejects on an error result and retries next time', async () => {
    const call = vi.fn()
      .mockResolvedValueOnce({ structuredContent: { error: 'No route analyses for this briefing.' } })
      .mockResolvedValueOnce(reply('gfs'));
    const loader = new XsectionLoader(call, 'f1', 't1');
    await expect(loader.load('gfs')).rejects.toThrow('No route analyses');
    await expect(loader.load('gfs')).resolves.toMatchObject({ model: 'gfs' });
    expect(call).toHaveBeenCalledTimes(2);
  });

  it('rejects a malformed reply with a message', async () => {
    const bad = [
      { structuredContent: { model: 'gfs', route_analyses: {} } },
      { structuredContent: { model: 3, route_analyses: manifest } },
      { structuredContent: { model: 'gfs', route_analyses: { analyses: 'x' } } },
    ];
    for (const r of bad) {
      const loader = new XsectionLoader(vi.fn().mockResolvedValue(r), 'f1', 't1');
      await expect(loader.load('gfs')).rejects.toThrow('No cross-section data');
    }
  });
});
