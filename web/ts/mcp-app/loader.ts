/**
 * Pack data for the MCP App widget over `tools/call` (the sandbox has no
 * cookies and `connect-src 'none'`, so the REST adapters can't run). One
 * `xsection_data` call per model, cached: switching back is free.
 */

import type { EmbedData } from '../embed/xsection-view';

/** The slice of a tools/call result the widget reads. */
export interface ToolCallResult {
  isError?: boolean;
  structuredContent?: Record<string, unknown>;
  content?: Array<{ type: string; text?: string }>;
}

export type CallServerTool = (params: { name: string; arguments: Record<string, unknown> }) => Promise<ToolCallResult>;

/** What `show_cross_section` puts in structuredContent: view parameters only. */
export interface ViewParams {
  flight_id: string;
  pack_timestamp: string;
  model: string | null;
  layers: string[];
  point_index: number | null;
  web_url: string;
}

/** A result's error message (tool error, `error`, or a non-ready `status`), or null. */
export function resultProblem(result: ToolCallResult): string | null {
  const sc = result.structuredContent;
  if (sc && typeof sc.error === 'string') return sc.error;
  if (sc && typeof sc.message === 'string' && typeof sc.status === 'string') return sc.message;
  if (result.isError) {
    return result.content?.find((c) => c.type === 'text')?.text ?? 'The tool call failed.';
  }
  return null;
}

/** The view parameters from a show_cross_section result; throws its message otherwise. */
export function parseViewParams(result: ToolCallResult): ViewParams {
  const problem = resultProblem(result);
  if (problem) throw new Error(problem);
  const sc = result.structuredContent ?? {};
  if (typeof sc.flight_id !== 'string' || typeof sc.pack_timestamp !== 'string') {
    throw new Error('The tool result carries no flight to show.');
  }
  return {
    flight_id: sc.flight_id,
    pack_timestamp: sc.pack_timestamp,
    model: typeof sc.model === 'string' ? sc.model : null,
    layers: Array.isArray(sc.layers) ? sc.layers.filter((l): l is string => typeof l === 'string') : [],
    point_index: typeof sc.point_index === 'number' ? sc.point_index : null,
    web_url: typeof sc.web_url === 'string' ? sc.web_url : '',
  };
}

export class XsectionLoader {
  private readonly cache = new Map<string, Promise<{ model: string; data: EmbedData }>>();

  constructor(
    private readonly call: CallServerTool,
    private readonly flightId: string,
    private readonly packTimestamp: string,
  ) {}

  /** One model's data; null asks the server for its default (ECMWF, GFS, first). */
  load(model: string | null): Promise<{ model: string; data: EmbedData }> {
    const key = model ?? '';
    let hit = this.cache.get(key);
    if (!hit) {
      hit = this.fetch(model);
      this.cache.set(key, hit);
      // A failed call isn't cached, so the next switch retries it.
      hit.catch(() => this.cache.delete(key));
      // The default resolves to a named model: cache it under that name too.
      if (model == null) hit.then((r) => { if (!this.cache.has(r.model)) this.cache.set(r.model, hit!); }, () => {});
    }
    return hit;
  }

  private async fetch(model: string | null): Promise<{ model: string; data: EmbedData }> {
    const args: Record<string, unknown> = { flight_id: this.flightId, pack_timestamp: this.packTimestamp };
    if (model) args.model = model;
    const result = await this.call({ name: 'xsection_data', arguments: args });
    const problem = resultProblem(result);
    if (problem) throw new Error(problem);
    const sc = result.structuredContent as Record<string, any> | undefined;
    if (!sc?.route_analyses) throw new Error('No cross-section data in the reply.');
    return {
      model: sc.model,
      data: {
        manifest: sc.route_analyses,
        ceilingFt: sc.ceiling_ft ?? undefined,
        snapshot: sc.snapshot ?? null,
        elevation: sc.elevation ?? null,
        fronts: sc.fronts ?? null,
      },
    };
  }
}
