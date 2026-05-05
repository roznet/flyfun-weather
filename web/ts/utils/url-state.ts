/** Typed URL state helper.
 *
 * Hydrates page state from `window.location.search` at init, and rewrites
 * the URL via `history.replaceState` whenever the page calls `write()` —
 * giving every page a self-describing URL that can be copied (or
 * shared via a button) as a deep link back to the same configuration.
 *
 * Defaults are elided from the URL so the typical (untouched) view
 * stays at the bare path. Unknown values, or values not in an
 * optional `values` whitelist, fall back to the default — so a stale
 * link from a removed option doesn't render the page broken.
 *
 * Usage:
 *
 *   const state = createUrlState({
 *     tab:    { default: 'forecast' as const, values: ['forecast', 'verification'] },
 *     'fc.day':   { default: 0 },
 *     'fc.hour':  { default: 12 },
 *   });
 *   const init = state.read();          // hydrate state vars from URL
 *   ...
 *   state.write({ tab, 'fc.day': fcDay, 'fc.hour': fcHour });  // sync after change
 */

type Codec<T> = {
  parse: (raw: string) => T | undefined;
  format: (value: T) => string;
};

const stringCodec: Codec<string> = {
  parse: (s) => s,
  format: (v) => v,
};

const intCodec: Codec<number> = {
  parse: (s) => {
    const n = parseInt(s, 10);
    return Number.isFinite(n) ? n : undefined;
  },
  format: (v) => String(v),
};

const floatCodec: Codec<number> = {
  parse: (s) => {
    const n = parseFloat(s);
    return Number.isFinite(n) ? n : undefined;
  },
  format: (v) => String(v),
};

export const urlCodecs = {
  string: stringCodec,
  int: intCodec,
  float: floatCodec,
};

export interface UrlField<T> {
  default: T;
  /** Optional codec; inferred from `default`'s type when omitted. */
  codec?: Codec<T>;
  /** Optional whitelist; unknown values fall back to default on read. */
  values?: readonly T[];
}

export type UrlSchema = Record<string, UrlField<any>>;

export type UrlValues<S extends UrlSchema> = {
  [K in keyof S]: S[K] extends UrlField<infer T> ? T : never;
};

export interface UrlState<S extends UrlSchema> {
  /** Read the current URL and return values, applying defaults for
   *  missing or invalid keys. */
  read(): UrlValues<S>;
  /** Rewrite the URL from a full snapshot. Fields equal to their
   *  default are elided. Uses `history.replaceState` so control
   *  changes don't pile up in browser history. */
  write(values: UrlValues<S>): void;
}

function pickCodec<T>(field: UrlField<T>): Codec<T> {
  if (field.codec) return field.codec;
  if (typeof field.default === 'number') return intCodec as unknown as Codec<T>;
  return stringCodec as unknown as Codec<T>;
}

export function createUrlState<S extends UrlSchema>(schema: S): UrlState<S> {
  const fields = Object.entries(schema) as [keyof S & string, UrlField<any>][];

  const read = (): UrlValues<S> => {
    const params = new URLSearchParams(window.location.search);
    const out = {} as UrlValues<S>;
    for (const [key, field] of fields) {
      const raw = params.get(key);
      let value: any = field.default;
      if (raw != null) {
        const codec = pickCodec(field);
        const parsed = codec.parse(raw);
        if (parsed !== undefined && (!field.values || field.values.includes(parsed))) {
          value = parsed;
        }
      }
      (out as any)[key] = value;
    }
    return out;
  };

  const write = (values: UrlValues<S>): void => {
    const params = new URLSearchParams(window.location.search);
    for (const [key, field] of fields) {
      const v = (values as any)[key];
      if (v === field.default) {
        params.delete(key);
        continue;
      }
      const codec = pickCodec(field);
      params.set(key, codec.format(v));
    }
    const qs = params.toString();
    const newUrl = window.location.pathname + (qs ? `?${qs}` : '') + window.location.hash;
    window.history.replaceState({}, '', newUrl);
  };

  return { read, write };
}
