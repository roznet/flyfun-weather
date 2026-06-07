/**
 * WGS84 lon/lat <-> chart-pixel projection for the DWD + Met Office surface
 * charts, ported from the Python `lonlat_to_chart_pixel` (pyproj polar-stereo
 * + 8-coeff homography) so the synoptic Hewson overlay can paint onto a chart
 * basemap.
 *
 * The projection is the *ellipsoidal* north polar stereographic (Snyder, USGS
 * PP 1395, §21), matching pyproj's default WGS84 ellipsoid — the homography
 * coefficients in `chart-projection-constants.ts` were fit to pyproj's exact
 * projected metres, so a spherical approximation would drift off the isobars.
 *
 * Equivalence with the Python path is enforced by
 * `web/tests/unit/chart-projection.test.ts` against an auto-generated fixture.
 */

import {
  CHART_PROJECTIONS,
  WGS84,
  type ChartProjectionKey,
} from './chart-projection-constants';

const DEG = Math.PI / 180;
const E = Math.sqrt(WGS84.e2);

export interface PixelPoint {
  x: number;
  y: number;
}

export interface LonLat {
  lon: number;
  lat: number;
}

/** Snyder eq. 15-9: isometric-latitude parameter `t` for the north polar aspect. */
function tFromLat(latRad: number): number {
  const sinPhi = Math.sin(latRad);
  return (
    Math.tan(Math.PI / 4 - latRad / 2) /
    Math.pow((1 - E * sinPhi) / (1 + E * sinPhi), E / 2)
  );
}

/** Scale factor mapping `t` -> polar radius rho, derived once per projection. */
function rhoScale(latTsDeg: number): number {
  const a = WGS84.a;
  if (latTsDeg >= 90) {
    // Standard parallel at the pole (Snyder eq. 21-33 form).
    return (
      (2 * a) /
      Math.sqrt(
        Math.pow(1 + E, 1 + E) * Math.pow(1 - E, 1 - E),
      )
    );
  }
  const phiC = latTsDeg * DEG;
  const sinC = Math.sin(phiC);
  const mc = Math.cos(phiC) / Math.sqrt(1 - WGS84.e2 * sinC * sinC);
  const tc = tFromLat(phiC);
  return (a * mc) / tc; // rho = scale * t
}

export interface ChartProjection {
  /** Forward: WGS84 lon/lat (deg) -> native chart pixel. */
  forward(lat: number, lon: number): PixelPoint;
  /** Inverse: native chart pixel -> WGS84 lon/lat (deg). */
  inverse(x: number, y: number): LonLat;
  /** Native chart pixel size [width, height]. */
  nativeSize: readonly [number, number];
}

/**
 * Build the forward+inverse projection for a chart type. The returned object
 * caches the per-projection scale so the hot path (per grid cell, 4 corners)
 * is just a handful of transcendental calls.
 */
export function makeChartProjection(key: ChartProjectionKey): ChartProjection {
  const c = CHART_PROJECTIONS[key];
  const lon0 = c.lon_0 * DEG;
  const scale = rhoScale(c.lat_ts);
  const [a, b, cc, d, e, f, g, h] = c.homography;

  function forward(lat: number, lon: number): PixelPoint {
    const lam = lon * DEG - lon0;
    const rho = scale * tFromLat(lat * DEG);
    // pyproj north polar aspect: x = rho*sin(lam), y = -rho*cos(lam).
    const px = rho * Math.sin(lam);
    const py = -rho * Math.cos(lam);
    const denom = g * px + h * py + 1;
    return {
      x: (a * px + b * py + cc) / denom,
      y: (d * px + e * py + f) / denom,
    };
  }

  function inverse(x: number, y: number): LonLat {
    // Invert the homography: solve the 2x2 system for (px, py) in metres.
    //   x = (a*px + b*py + cc) / w,  y = (d*px + e*py + f) / w,  w = g*px + h*py + 1
    const a11 = a - x * g;
    const a12 = b - x * h;
    const a21 = d - y * g;
    const a22 = e - y * h;
    const b1 = x - cc;
    const b2 = y - f;
    const det = a11 * a22 - a12 * a21;
    const px = (b1 * a22 - b2 * a12) / det;
    const py = (a11 * b2 - a21 * b1) / det;

    // Invert the polar stereographic.
    const rho = Math.hypot(px, py);
    const lam = Math.atan2(px, -py); // since px = rho*sin(lam), -py = rho*cos(lam)
    const lon = (lam + lon0) / DEG;

    // t from rho, then iterate latitude (Snyder eq. 7-9 fixed point).
    const t = rho / scale;
    let lat = Math.PI / 2 - 2 * Math.atan(t);
    for (let i = 0; i < 12; i++) {
      const sinPhi = Math.sin(lat);
      const next =
        Math.PI / 2 -
        2 *
          Math.atan(
            t * Math.pow((1 - E * sinPhi) / (1 + E * sinPhi), E / 2),
          );
      if (Math.abs(next - lat) < 1e-12) {
        lat = next;
        break;
      }
      lat = next;
    }
    return { lon: normalizeLon(lon), lat: lat / DEG };
  }

  return { forward, inverse, nativeSize: c.native_size };
}

function normalizeLon(lon: number): number {
  let v = lon;
  while (v > 180) v -= 360;
  while (v < -180) v += 360;
  return v;
}
