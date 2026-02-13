/** DOM management for the Briefing report page.
 *
 * Renders all sections: header, assessment, synopsis, GRAMET,
 * model comparison, and Skew-T route view.
 */

import type {
  AltitudeAdvisories,
  FlightResponse,
  ForecastSnapshot,
  PackMeta,
  RouteAnalysesManifest,
  RoutePointAnalysis,
  SoundingAnalysis,
  ThermodynamicIndices,
  WeatherDigest,
  WindComponent,
} from '../store/types';
import * as api from '../adapters/api-adapter';

function $(id: string): HTMLElement {
  return document.getElementById(id)!;
}

// --- Header ---

export function renderHeader(
  flight: FlightResponse | null,
  snapshot: ForecastSnapshot | null,
): void {
  const el = $('briefing-header');
  if (!el || !flight) return;

  // Use snapshot waypoints if available, otherwise derive from route name
  let routeStr: string;
  if (snapshot?.route?.waypoints) {
    routeStr = snapshot.route.waypoints.map((w) => w.icao).join(' \u2192 ');
  } else if (flight.waypoints?.length) {
    routeStr = flight.waypoints.join(' \u2192 ');
  } else {
    routeStr = flight.route_name.replace(/_/g, ' \u2192 ').toUpperCase();
  }

  const date = new Date(flight.target_date + 'T00:00:00Z');
  const dateStr = date.toLocaleDateString('en-GB', {
    weekday: 'short', day: 'numeric', month: 'short', year: 'numeric',
    timeZone: 'UTC',
  });
  const timeStr = `${flight.target_time_utc.toString().padStart(2, '0')}00Z`;
  const alt = flight.cruise_altitude_ft >= 10000
    ? `FL${Math.round(flight.cruise_altitude_ft / 100)}`
    : `${flight.cruise_altitude_ft}ft`;

  el.innerHTML = `
    <span class="route-summary">${routeStr}</span>
    <span class="date-summary">${dateStr} ${timeStr}</span>
    <span class="alt-summary">${alt}</span>
  `;
}

// --- History dropdown ---

export function renderHistoryDropdown(
  packs: PackMeta[],
  currentTimestamp: string | null,
  onSelect: (ts: string) => void,
): void {
  const select = $('history-select') as HTMLSelectElement;
  if (!select) return;

  select.innerHTML = packs.length === 0
    ? '<option>No briefings yet</option>'
    : packs.map((p) => {
        const date = new Date(p.fetch_timestamp);
        const label = `D-${p.days_out} (${date.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', timeZone: 'UTC' })} UTC)`;
        const selected = p.fetch_timestamp === currentTimestamp ? ' selected' : '';
        return `<option value="${p.fetch_timestamp}"${selected}>${label}</option>`;
      }).join('');

  // Wire change event (remove old listener by replacing element)
  const newSelect = select.cloneNode(true) as HTMLSelectElement;
  select.parentNode!.replaceChild(newSelect, select);
  newSelect.addEventListener('change', () => {
    onSelect(newSelect.value);
  });
}

// --- Assessment banner ---

export function renderAssessment(pack: PackMeta | null): void {
  const el = $('assessment-banner');
  if (!el) return;

  if (!pack || !pack.assessment) {
    el.className = 'assessment-banner assessment-none';
    el.textContent = 'No assessment available';
    return;
  }

  const level = pack.assessment.toUpperCase();
  el.className = `assessment-banner assessment-${level.toLowerCase()}`;
  el.innerHTML = `
    <strong>${level}</strong>${pack.assessment_reason ? ` \u2014 ${pack.assessment_reason}` : ''}
  `;
}

// --- Synopsis (structured digest) ---

const DIGEST_SECTIONS: Array<{ key: keyof WeatherDigest; label: string; icon: string }> = [
  { key: 'synoptic', label: 'Synoptic', icon: '\uD83C\uDF0D' },
  { key: 'winds', label: 'Winds', icon: '\uD83D\uDCA8' },
  { key: 'cloud_visibility', label: 'Cloud & Visibility', icon: '\u2601\uFE0F' },
  { key: 'precipitation_convection', label: 'Precipitation & Convection', icon: '\uD83C\uDF27\uFE0F' },
  { key: 'icing', label: 'Icing', icon: '\u2744\uFE0F' },
  { key: 'specific_concerns', label: 'Specific Concerns', icon: '\u26A0\uFE0F' },
  { key: 'model_agreement', label: 'Model Agreement', icon: '\uD83D\uDCCA' },
  { key: 'trend', label: 'Trend', icon: '\uD83D\uDCC8' },
  { key: 'watch_items', label: 'Watch Items', icon: '\uD83D\uDC41\uFE0F' },
];

export function renderSynopsis(
  flight: FlightResponse | null,
  pack: PackMeta | null,
  digest: WeatherDigest | null,
): void {
  const el = $('synopsis-section');
  if (!el) return;

  if (!flight || !pack) {
    el.innerHTML = '<p class="muted">No briefing loaded.</p>';
    return;
  }

  if (digest) {
    el.innerHTML = DIGEST_SECTIONS.map(({ key, label, icon }) => {
      const text = digest[key];
      if (!text) return '';
      return `
        <div class="digest-section">
          <h4>${icon} ${label}</h4>
          <p>${escapeHtml(text as string)}</p>
        </div>
      `;
    }).join('');
    return;
  }

  if (pack.has_digest) {
    el.innerHTML = '<p class="muted">Loading digest...</p>';
    fetchAndRenderDigestJson(flight.id, pack.fetch_timestamp, el);
    return;
  }

  el.innerHTML = '<p class="muted">Synopsis not available. Trigger a refresh to generate.</p>';
}

async function fetchAndRenderDigestJson(
  flightId: string, timestamp: string, el: HTMLElement,
): Promise<void> {
  try {
    const url = api.digestJsonUrl(flightId, timestamp);
    const resp = await fetch(url);
    if (!resp.ok) throw new Error(`${resp.status}`);
    const digest: WeatherDigest = await resp.json();

    el.innerHTML = DIGEST_SECTIONS.map(({ key, label, icon }) => {
      const text = digest[key];
      if (!text) return '';
      return `
        <div class="digest-section">
          <h4>${icon} ${label}</h4>
          <p>${escapeHtml(text as string)}</p>
        </div>
      `;
    }).join('');
  } catch {
    el.innerHTML = '<p class="muted">Failed to load digest.</p>';
  }
}

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

// --- GRAMET ---

export function renderGramet(
  flight: FlightResponse | null,
  pack: PackMeta | null,
): void {
  const el = $('gramet-section');
  if (!el) return;

  if (!flight || !pack || !pack.has_gramet) {
    el.innerHTML = '<p class="muted">GRAMET not available for this briefing.</p>';
    return;
  }

  const url = api.grametUrl(flight.id, pack.fetch_timestamp);
  el.innerHTML = `
    <img src="${url}" alt="GRAMET cross-section" class="gramet-img" loading="lazy">
  `;
}

// --- Model Comparison ---

export function renderModelComparison(
  snapshot: ForecastSnapshot | null,
  routeAnalyses?: RouteAnalysesManifest | null,
  selectedPointIndex?: number,
): void {
  const el = $('comparison-section');
  if (!el) return;

  // Route-point mode: single point comparison
  if (routeAnalyses && routeAnalyses.analyses.length > 0) {
    const idx = selectedPointIndex ?? 0;
    const point = routeAnalyses.analyses[idx];
    if (point && point.model_divergence.length > 0) {
      const label = point.waypoint_icao
        ? `${point.waypoint_icao} \u2014 ${point.waypoint_name || ''}`
        : `Point ${point.point_index} (${point.distance_from_origin_nm.toFixed(0)} nm)`;
      el.innerHTML = renderComparisonTable(label, point.model_divergence);
      return;
    }
    el.innerHTML = '<p class="muted">No model comparison data for this point.</p>';
    return;
  }

  // Fallback: stacked waypoint view
  if (!snapshot || snapshot.analyses.length === 0) {
    el.innerHTML = '<p class="muted">No model comparison data available.</p>';
    return;
  }

  el.innerHTML = snapshot.analyses.map((a) => {
    if (a.model_divergence.length === 0) return '';
    return renderComparisonTable(
      `${a.waypoint.icao} \u2014 ${a.waypoint.name}`,
      a.model_divergence,
    );
  }).join('');
}

function renderComparisonTable(
  label: string,
  divergences: Array<{ variable: string; model_values: Record<string, number>; mean: number; spread: number; agreement: string }>,
): string {
  const models = Object.keys(divergences[0]?.model_values || {});
  const headerCells = models.map((m) => `<th>${m.toUpperCase()}</th>`).join('');

  const rows = divergences.map((d) => {
    const valueCells = models.map((m) => {
      const val = d.model_values[m];
      return `<td>${val !== undefined ? val.toFixed(1) : '\u2014'}</td>`;
    }).join('');
    const agreeIcon = d.agreement === 'good' ? '&#10003;'
      : d.agreement === 'moderate' ? '&#9888;' : '&#10007;';
    const agreeClass = `agree-${d.agreement}`;
    return `
      <tr>
        <td class="var-name">${formatVarName(d.variable)}</td>
        ${valueCells}
        <td>${d.spread.toFixed(1)}</td>
        <td class="${agreeClass}">${agreeIcon}</td>
      </tr>
    `;
  }).join('');

  return `
    <div class="comparison-waypoint">
      <h4>${label}</h4>
      <table class="comparison-table">
        <thead>
          <tr>
            <th>Variable</th>
            ${headerCells}
            <th>Spread</th>
            <th>Agree</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
  `;
}

function formatVarName(name: string): string {
  const labels: Record<string, string> = {
    'temperature_c': 'Temp (\u00B0C)',
    'wind_speed_kt': 'Wind (kt)',
    'wind_direction_deg': 'Wind dir (\u00B0)',
    'cloud_cover_pct': 'Cloud (%)',
    'precipitation_mm': 'Precip (mm)',
    'freezing_level_m': 'Freezing (m)',
    'freezing_level_ft': 'Freezing (ft)',
    'cape_surface_jkg': 'CAPE (J/kg)',
    'lcl_altitude_ft': 'LCL (ft)',
    'k_index': 'K-index',
    'total_totals': 'Total Totals',
    'precipitable_water_mm': 'PW (mm)',
    'lifted_index': 'Lifted Index',
    'bulk_shear_0_6km_kt': 'Shear 0-6km (kt)',
    'max_omega_pa_s': 'Max Omega (Pa/s)',
  };
  return labels[name] || name;
}

// --- Sounding Analysis ---

const RISK_COLORS: Record<string, string> = {
  none: '',
  light: 'risk-light',
  low: 'risk-light',
  moderate: 'risk-moderate',
  high: 'risk-high',
  severe: 'risk-severe',
  extreme: 'risk-severe',
};

function riskClass(risk: string): string {
  return RISK_COLORS[risk] || '';
}

function roundAlt(ft: number): number {
  return Math.round(ft / 500) * 500;
}

export function renderSoundingAnalysis(
  snapshot: ForecastSnapshot | null,
  routeAnalyses?: RouteAnalysesManifest | null,
  selectedPointIndex?: number,
): void {
  const el = $('sounding-section');
  if (!el) return;

  // Route-point mode: show single selected point
  if (routeAnalyses && routeAnalyses.analyses.length > 0) {
    const idx = selectedPointIndex ?? 0;
    const point = routeAnalyses.analyses[idx];
    if (point) {
      el.innerHTML = renderSinglePointSounding(point);
      return;
    }
  }

  // Fallback: stacked waypoint view
  if (!snapshot || snapshot.analyses.length === 0) {
    el.innerHTML = '<p class="muted">No sounding analysis available.</p>';
    return;
  }

  const hasSounding = snapshot.analyses.some(
    (a) => a.sounding && Object.keys(a.sounding).length > 0,
  );
  if (!hasSounding) {
    el.innerHTML = '<p class="muted">Sounding analysis not available for this briefing.</p>';
    return;
  }

  el.innerHTML = snapshot.analyses.map((a) => {
    if (!a.sounding || Object.keys(a.sounding).length === 0) return '';
    return `
      <div class="sounding-waypoint">
        <h4>${a.waypoint.icao} \u2014 ${a.waypoint.name}</h4>
        ${renderConvectiveBanner(a.sounding)}
        ${renderVerticalMotion(a.sounding)}
        ${renderAltitudeMarkers(a.sounding)}
        ${renderIcingZones(a.sounding)}
        ${renderEnhancedClouds(a.sounding)}
        ${renderNwpCloudCover(a.sounding)}
        ${renderAltitudeAdvisories(a.altitude_advisories)}
      </div>
    `;
  }).join('');
}

function renderConvectiveBanner(soundings: Record<string, SoundingAnalysis>): string {
  const models = Object.keys(soundings);
  const hasConvective = models.some(
    (m) => soundings[m].convective && soundings[m].convective!.risk_level !== 'none',
  );
  if (!hasConvective) return '';

  const headerCells = models.map((m) => `<th>${m.toUpperCase()}</th>`).join('');

  const rowSpecs: Array<{ label: string; render: (m: string) => string }> = [
    {
      label: 'Risk',
      render: (m) => {
        const c = soundings[m].convective;
        if (!c || c.risk_level === 'none') return '<td>\u2014</td>';
        return `<td class="${riskClass(c.risk_level)}">${c.risk_level.toUpperCase()}</td>`;
      },
    },
    {
      label: 'CAPE (J/kg)',
      render: (m) => {
        const v = soundings[m].convective?.cape_jkg;
        return `<td>${v != null ? v.toFixed(0) : '\u2014'}</td>`;
      },
    },
    {
      label: 'Lifted Index',
      render: (m) => {
        const v = soundings[m].convective?.lifted_index;
        return `<td>${v != null ? v.toFixed(1) : '\u2014'}</td>`;
      },
    },
    {
      label: 'K-index',
      render: (m) => {
        const v = soundings[m].convective?.k_index;
        return `<td>${v != null ? v.toFixed(0) : '\u2014'}</td>`;
      },
    },
    {
      label: 'Modifiers',
      render: (m) => {
        const mods = soundings[m].convective?.severe_modifiers;
        if (!mods || mods.length === 0) return '<td>\u2014</td>';
        return `<td>${escapeHtml(mods.join(', '))}</td>`;
      },
    },
  ];

  const rows = rowSpecs.map(({ label, render }) => {
    const cells = models.map(render).join('');
    return `<tr><td class="var-name">${label}</td>${cells}</tr>`;
  }).join('');

  return `
    <div class="convective-section">
      <h5>Convective</h5>
      <table class="band-table">
        <thead><tr><th></th>${headerCells}</tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
  `;
}

function formatClassification(cls: string): string {
  const labels: Record<string, string> = {
    quiescent: 'Quiescent',
    synoptic_ascent: 'Synoptic Ascent',
    synoptic_subsidence: 'Synoptic Subsidence',
    convective: 'Convective',
    oscillating: 'Oscillating',
    unavailable: 'N/A',
  };
  return labels[cls] || cls;
}

function renderVerticalMotion(soundings: Record<string, SoundingAnalysis>): string {
  const models = Object.keys(soundings);
  const hasVerticalMotion = models.some(
    (m) => soundings[m].vertical_motion && soundings[m].vertical_motion!.classification !== 'unavailable',
  );
  if (!hasVerticalMotion) return '';

  const headerCells = models.map((m) => `<th>${m.toUpperCase()}</th>`).join('');

  // Summary rows
  const rowSpecs: Array<{ label: string; render: (m: string) => string }> = [
    {
      label: 'Classification',
      render: (m) => {
        const vm = soundings[m].vertical_motion;
        if (!vm || vm.classification === 'unavailable') return '<td class="muted">N/A</td>';
        const cls = vm.classification === 'convective' ? 'risk-severe' : '';
        return `<td class="${cls}">${formatClassification(vm.classification)}</td>`;
      },
    },
    {
      label: 'Max W (ft/min)',
      render: (m) => {
        const vm = soundings[m].vertical_motion;
        if (!vm || vm.max_w_fpm == null) return '<td>\u2014</td>';
        const sign = vm.max_w_fpm > 0 ? '+' : '';
        const alt = vm.max_w_level_ft != null ? ` @ ${vm.max_w_level_ft.toFixed(0)}ft` : '';
        return `<td>${sign}${vm.max_w_fpm.toFixed(0)}${alt}</td>`;
      },
    },
  ];

  // Add contamination row only if any model flags it
  const hasContamination = models.some(
    (m) => soundings[m].vertical_motion?.convective_contamination,
  );
  if (hasContamination) {
    rowSpecs.push({
      label: 'Contamination',
      render: (m) => {
        const vm = soundings[m].vertical_motion;
        if (!vm) return '<td>\u2014</td>';
        return vm.convective_contamination
          ? '<td class="risk-moderate">Mid-level convective</td>'
          : '<td>None</td>';
      },
    });
  }

  const summaryRows = rowSpecs.map(({ label, render }) => {
    const cells = models.map(render).join('');
    return `<tr><td class="var-name">${label}</td>${cells}</tr>`;
  }).join('');

  // CAT risk layers section
  let catSection = '';
  const hasCat = models.some(
    (m) => (soundings[m].vertical_motion?.cat_risk_layers?.length ?? 0) > 0,
  );
  if (hasCat) {
    const allAlts = new Set<number>();
    for (const m of models) {
      const layers = soundings[m].vertical_motion?.cat_risk_layers || [];
      for (const l of layers) {
        allAlts.add(roundAlt(l.base_ft));
        allAlts.add(roundAlt(l.top_ft));
      }
    }
    const sortedAlts = [...allAlts].sort((a, b) => b - a);

    if (sortedAlts.length >= 2) {
      const catRows = sortedAlts.slice(0, -1).map((alt, i) => {
        const nextAlt = sortedAlts[i + 1];
        const midpoint = (alt + nextAlt) / 2;

        let anyHit = false;
        const cells = models.map((m) => {
          const layer = (soundings[m].vertical_motion?.cat_risk_layers || []).find(
            (l) => l.base_ft <= midpoint && l.top_ft >= midpoint,
          );
          if (!layer) return '<td>\u2014</td>';
          anyHit = true;
          const ri = layer.richardson_number != null ? ` Ri=${layer.richardson_number.toFixed(2)}` : '';
          return `<td class="${riskClass(layer.risk)}">${layer.risk.toUpperCase()}${ri}</td>`;
        }).join('');

        if (!anyHit) return '';
        return `<tr><td class="var-name">${nextAlt}-${alt}ft</td>${cells}</tr>`;
      }).join('');

      catSection = `
        <h6>CAT Risk Layers</h6>
        <table class="band-table">
          <thead><tr><th>Altitude</th>${headerCells}</tr></thead>
          <tbody>${catRows}</tbody>
        </table>
      `;
    }
  }

  return `
    <div class="vertical-motion-section">
      <h5>Vertical Motion</h5>
      <table class="band-table">
        <thead><tr><th></th>${headerCells}</tr></thead>
        <tbody>${summaryRows}</tbody>
      </table>
      ${catSection}
    </div>
  `;
}

function renderAltitudeMarkers(soundings: Record<string, SoundingAnalysis>): string {
  const models = Object.keys(soundings);
  const hasIndices = models.some((m) => soundings[m].indices != null);
  if (!hasIndices) return '';

  const headerCells = models.map((m) => `<th>${m.toUpperCase()}</th>`).join('');

  const rowSpecs: Array<{ key: keyof ThermodynamicIndices; label: string }> = [
    { key: 'freezing_level_ft', label: '0\u00B0C' },
    { key: 'minus10c_level_ft', label: '-10\u00B0C' },
    { key: 'minus20c_level_ft', label: '-20\u00B0C' },
    { key: 'lcl_altitude_ft', label: 'LCL' },
  ];

  const rows = rowSpecs.map(({ key, label }) => {
    const cells = models.map((m) => {
      const v = soundings[m].indices?.[key] as number | null;
      return `<td>${v != null ? v.toFixed(0) + 'ft' : '\u2014'}</td>`;
    }).join('');
    return `<tr><td class="var-name">${label}</td>${cells}</tr>`;
  }).join('');

  return `
    <div class="altitude-markers">
      <h5>Key Altitudes</h5>
      <table class="band-table">
        <thead><tr><th></th>${headerCells}</tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
  `;
}

function renderIcingZones(soundings: Record<string, SoundingAnalysis>): string {
  const models = Object.keys(soundings);
  const hasIcing = models.some((m) => soundings[m].icing_zones.length > 0);
  if (!hasIcing) return '';

  // Collect all boundary altitudes across models, rounded to 500ft
  const allAlts = new Set<number>();
  for (const m of models) {
    for (const z of soundings[m].icing_zones) {
      allAlts.add(roundAlt(z.base_ft));
      allAlts.add(roundAlt(z.top_ft));
    }
  }
  const sortedAlts = [...allAlts].sort((a, b) => b - a); // top-down
  if (sortedAlts.length < 2) return '';

  const headerCells = models.map((m) => `<th>${m.toUpperCase()}</th>`).join('');

  const rows = sortedAlts.slice(0, -1).map((alt, i) => {
    const nextAlt = sortedAlts[i + 1];
    const midpoint = (alt + nextAlt) / 2;

    let anyHit = false;
    const cells = models.map((m) => {
      const zone = soundings[m].icing_zones.find(
        (z) => z.base_ft <= midpoint && z.top_ft >= midpoint,
      );
      if (!zone) return '<td>\u2014</td>';
      anyHit = true;
      const sld = zone.sld_risk ? ' SLD' : '';
      const tw = zone.mean_wet_bulb_c != null ? ` Tw=${zone.mean_wet_bulb_c.toFixed(0)}\u00B0C` : '';
      return `<td class="${riskClass(zone.risk)}">${zone.risk.toUpperCase()} ${zone.icing_type}${tw}${sld}</td>`;
    }).join('');

    if (!anyHit) return '';
    return `<tr><td class="var-name">${nextAlt}-${alt}ft</td>${cells}</tr>`;
  }).join('');

  return `
    <div class="icing-zones">
      <h5>Icing Zones</h5>
      <table class="band-table">
        <thead><tr><th>Altitude</th>${headerCells}</tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
  `;
}

function renderEnhancedClouds(soundings: Record<string, SoundingAnalysis>): string {
  const models = Object.keys(soundings);
  const hasClouds = models.some((m) => soundings[m].cloud_layers.length > 0);
  if (!hasClouds) return '';

  // Collect all boundary altitudes across models, rounded to 500ft
  const allAlts = new Set<number>();
  for (const m of models) {
    for (const cl of soundings[m].cloud_layers) {
      allAlts.add(roundAlt(cl.base_ft));
      allAlts.add(roundAlt(cl.top_ft));
    }
  }
  const sortedAlts = [...allAlts].sort((a, b) => b - a);
  if (sortedAlts.length < 2) return '';

  const headerCells = models.map((m) => `<th>${m.toUpperCase()}</th>`).join('');

  const rows = sortedAlts.slice(0, -1).map((alt, i) => {
    const nextAlt = sortedAlts[i + 1];
    const midpoint = (alt + nextAlt) / 2;

    let anyHit = false;
    const cells = models.map((m) => {
      const layer = soundings[m].cloud_layers.find(
        (cl) => cl.base_ft <= midpoint && cl.top_ft >= midpoint,
      );
      if (!layer) return '<td>\u2014</td>';
      anyHit = true;
      const t = layer.mean_temperature_c != null ? ` T=${layer.mean_temperature_c.toFixed(0)}\u00B0C` : '';
      return `<td>${layer.coverage.toUpperCase()}${t}</td>`;
    }).join('');

    if (!anyHit) return '';
    return `<tr><td class="var-name">${nextAlt}-${alt}ft</td>${cells}</tr>`;
  }).join('');

  return `
    <div class="enhanced-clouds">
      <h5>Cloud Layers</h5>
      <table class="band-table">
        <thead><tr><th>Altitude</th>${headerCells}</tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
  `;
}

function renderNwpCloudCover(soundings: Record<string, SoundingAnalysis>): string {
  const models = Object.keys(soundings);
  const hasNwp = models.some((m) =>
    soundings[m].cloud_cover_low_pct != null ||
    soundings[m].cloud_cover_mid_pct != null ||
    soundings[m].cloud_cover_high_pct != null,
  );
  if (!hasNwp) return '';

  const headerCells = models.map((m) => `<th>${m.toUpperCase()}</th>`).join('');

  const rowSpecs: Array<{ key: 'cloud_cover_high_pct' | 'cloud_cover_mid_pct' | 'cloud_cover_low_pct'; label: string }> = [
    { key: 'cloud_cover_high_pct', label: 'High' },
    { key: 'cloud_cover_mid_pct', label: 'Mid' },
    { key: 'cloud_cover_low_pct', label: 'Low' },
  ];

  const rows = rowSpecs.map(({ key, label }) => {
    const cells = models.map((m) => {
      const v = soundings[m][key];
      return `<td>${v != null ? v.toFixed(0) + '%' : '\u2014'}</td>`;
    }).join('');
    return `<tr><td class="var-name">${label}</td>${cells}</tr>`;
  }).join('');

  return `
    <div class="nwp-cloud-cover">
      <h5>NWP Cloud Cover</h5>
      <table class="band-table">
        <thead><tr><th></th>${headerCells}</tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
  `;
}

function renderAltitudeAdvisories(adv: AltitudeAdvisories | null): string {
  if (!adv) return '';

  const parts: string[] = [];

  // Cruise icing badge
  if (adv.cruise_in_icing) {
    parts.push(
      `<div class="cruise-icing-banner ${riskClass(adv.cruise_icing_risk)}">` +
      `Cruise in icing: ${adv.cruise_icing_risk.toUpperCase()}</div>`,
    );
  }

  // Per-model vertical regimes as columns
  const models = Object.keys(adv.regimes);
  if (models.length > 0) {
    const headerCells = models.map((m) => `<th>${m.toUpperCase()}</th>`).join('');

    // Collect all unique altitudes to build rows
    const allAlts = new Set<number>();
    for (const regimes of Object.values(adv.regimes)) {
      for (const r of regimes) {
        allAlts.add(r.floor_ft);
        allAlts.add(r.ceiling_ft);
      }
    }
    const sortedAlts = [...allAlts].sort((a, b) => b - a); // top-down

    // Build regime rows: for each altitude pair, show each model's regime
    const rows = sortedAlts.slice(0, -1).map((alt, i) => {
      const nextAlt = sortedAlts[i + 1];
      const midpoint = (alt + nextAlt) / 2;

      const cells = models.map((m) => {
        const regime = adv.regimes[m].find(
          (r) => r.floor_ft <= midpoint && r.ceiling_ft >= midpoint,
        );
        if (!regime) return '<td>\u2014</td>';
        const cls = regime.icing_risk !== 'none' ? riskClass(regime.icing_risk) : '';
        return `<td class="${cls}">${escapeHtml(regime.label)}</td>`;
      }).join('');

      return `<tr><td class="var-name">${nextAlt.toFixed(0)}-${alt.toFixed(0)}ft</td>${cells}</tr>`;
    }).join('');

    parts.push(`
      <div class="regime-table-wrap">
        <h5>Vertical Profile</h5>
        <table class="band-table">
          <thead><tr><th>Altitude</th>${headerCells}</tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
    `);
  }

  // Advisories as table
  if (adv.advisories.length > 0) {
    const advModels = Object.keys(adv.regimes);
    const advHeaderCells = advModels.map((m) => `<th>${m.toUpperCase()}</th>`).join('');

    const advisoryRows = adv.advisories.map((a) => {
      const isDescentToZero = a.advisory_type === 'descend_below_icing' && a.altitude_ft === 0;
      const infeasibleBadge = !a.feasible ? ' <span class="advisory-badge">INFEASIBLE</span>' : '';

      let label: string;
      if (isDescentToZero) {
        label = 'Unable to descend below freezing' + infeasibleBadge;
      } else {
        label = escapeHtml(a.reason) + infeasibleBadge;
      }

      const cells = advModels.map((m) => {
        const v = a.per_model_ft[m];
        if (v == null) return '<td>\u2014</td>';
        if (a.advisory_type === 'descend_below_icing' && v === 0) {
          return '<td>SFC</td>';
        }
        return `<td>${v.toFixed(0)}ft</td>`;
      }).join('');

      const rowCls = !a.feasible ? ' class="advisory-infeasible"' : '';
      return `<tr${rowCls}><td class="var-name">${label}</td>${cells}</tr>`;
    }).join('');

    parts.push(`
      <div class="advisories-section">
        <h5>Advisories</h5>
        <table class="band-table">
          <thead><tr><th></th>${advHeaderCells}</tr></thead>
          <tbody>${advisoryRows}</tbody>
        </table>
      </div>
    `);
  }

  return parts.join('');
}

// --- Route Slider ---

export function renderRouteSlider(
  ra: RouteAnalysesManifest | null,
  selectedIndex: number,
  onSelect: (index: number) => void,
): void {
  const section = $('route-slider-section');
  const container = $('route-slider-container');
  if (!section || !container) return;

  if (!ra || ra.analyses.length === 0) {
    section.style.display = 'none';
    return;
  }

  section.style.display = '';
  const analyses = ra.analyses;
  const maxIdx = analyses.length - 1;
  const current = analyses[selectedIndex] || analyses[0];
  const totalDist = ra.total_distance_nm;

  // Build waypoint labels for the track
  const waypointLabels = analyses
    .filter((a) => a.waypoint_icao)
    .map((a) => {
      const pct = totalDist > 0 ? (a.distance_from_origin_nm / totalDist) * 100 : 0;
      return `<span class="slider-waypoint-label" style="left: ${pct}%">${a.waypoint_icao}</span>`;
    })
    .join('');

  // Format time
  const time = new Date(current.interpolated_time);
  const timeStr = time.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', timeZone: 'UTC' }) + 'Z';

  // Wind info for first model
  const modelKeys = Object.keys(current.wind_components);
  let windInfo = '';
  if (modelKeys.length > 0) {
    const wc: WindComponent = current.wind_components[modelKeys[0]];
    const hdTail = wc.headwind_kt >= 0 ? `HD ${wc.headwind_kt.toFixed(0)}` : `TL ${(-wc.headwind_kt).toFixed(0)}`;
    windInfo = `Wind ${wc.wind_direction_deg.toFixed(0)}\u00B0/${wc.wind_speed_kt.toFixed(0)}kt (${hdTail})`;
  }

  const pointLabel = current.waypoint_icao
    ? `${current.waypoint_icao} \u2014 ${current.waypoint_name || ''}`
    : `${current.lat.toFixed(2)}\u00B0N ${Math.abs(current.lon).toFixed(2)}\u00B0${current.lon >= 0 ? 'E' : 'W'}`;

  container.innerHTML = `
    <div class="route-slider-info">
      <span class="slider-point-label">${pointLabel}</span>
      <span class="slider-distance">${current.distance_from_origin_nm.toFixed(0)} nm</span>
      <span class="slider-time">${timeStr}</span>
      <span class="slider-wind">${windInfo}</span>
    </div>
    <div class="route-slider-track">
      <input type="range" id="route-slider" min="0" max="${maxIdx}" value="${selectedIndex}" class="route-slider-input">
      <div class="slider-waypoint-labels">${waypointLabels}</div>
    </div>
    <div class="slider-endpoints">
      <span>${analyses[0].waypoint_icao || 'Origin'}</span>
      <span>${analyses[maxIdx].waypoint_icao || 'Destination'}</span>
    </div>
  `;

  const slider = document.getElementById('route-slider') as HTMLInputElement;
  if (slider) {
    slider.addEventListener('input', () => {
      onSelect(parseInt(slider.value, 10));
    });
  }
}

// --- Route-point sounding (single point) ---

function renderSinglePointSounding(point: RoutePointAnalysis): string {
  if (!point.sounding || Object.keys(point.sounding).length === 0) {
    return '<p class="muted">No sounding data for this point.</p>';
  }

  const label = point.waypoint_icao
    ? `${point.waypoint_icao} \u2014 ${point.waypoint_name || ''}`
    : `Point ${point.point_index} (${point.distance_from_origin_nm.toFixed(0)} nm)`;

  return `
    <div class="sounding-waypoint">
      <h4>${label}</h4>
      ${renderConvectiveBanner(point.sounding)}
      ${renderVerticalMotion(point.sounding)}
      ${renderAltitudeMarkers(point.sounding)}
      ${renderIcingZones(point.sounding)}
      ${renderEnhancedClouds(point.sounding)}
      ${renderNwpCloudCover(point.sounding)}
      ${renderAltitudeAdvisories(point.altitude_advisories)}
    </div>
  `;
}

// --- Skew-T ---

export function renderSkewTs(
  flight: FlightResponse | null,
  pack: PackMeta | null,
  snapshot: ForecastSnapshot | null,
  selectedModel: string,
  routeAnalyses?: RouteAnalysesManifest | null,
  selectedPointIndex?: number,
): void {
  const el = $('skewt-section');
  if (!el) return;

  if (!flight || !pack) {
    el.innerHTML = '<p class="muted">Skew-T diagrams not available.</p>';
    return;
  }

  // Route-point mode: single Skew-T via on-demand endpoint
  if (routeAnalyses && routeAnalyses.analyses.length > 0) {
    const idx = selectedPointIndex ?? 0;
    const point = routeAnalyses.analyses[idx];
    if (point) {
      const label = point.waypoint_icao || `Point ${point.point_index}`;
      const url = api.routeSkewtUrl(flight.id, pack.fetch_timestamp, point.point_index, selectedModel);
      el.innerHTML = `
        <div class="skewt-gallery">
          <div class="skewt-card skewt-card-large">
            <h4>${label} \u2014 ${selectedModel.toUpperCase()}</h4>
            <img src="${url}" alt="Skew-T ${label} ${selectedModel}"
                 class="skewt-img" loading="lazy"
                 onerror="this.parentElement.classList.add('skewt-unavailable')">
            <div class="skewt-fallback">Not available</div>
          </div>
        </div>
      `;
      return;
    }
  }

  // Fallback: waypoint gallery
  if (!pack.has_skewt || !snapshot) {
    el.innerHTML = '<p class="muted">Skew-T diagrams not available.</p>';
    return;
  }

  const waypoints = snapshot.route.waypoints;
  el.innerHTML = `
    <div class="skewt-gallery">
      ${waypoints.map((wp) => {
        const url = api.skewtUrl(flight.id, pack.fetch_timestamp, wp.icao, selectedModel);
        return `
          <div class="skewt-card">
            <h4>${wp.icao}</h4>
            <img src="${url}" alt="Skew-T ${wp.icao} ${selectedModel}"
                 class="skewt-img" loading="lazy"
                 onerror="this.parentElement.classList.add('skewt-unavailable')">
            <div class="skewt-fallback">Not available</div>
          </div>
        `;
      }).join('')}
    </div>
  `;
}

// --- Loading / Error ---

export function renderLoading(loading: boolean): void {
  const el = $('loading-overlay');
  if (el) el.style.display = loading ? 'flex' : 'none';
}

export function renderRefreshing(refreshing: boolean): void {
  const btn = $('refresh-btn') as HTMLButtonElement;
  if (btn) {
    btn.disabled = refreshing;
    btn.textContent = refreshing ? 'Refreshing...' : 'Refresh';
  }
}

export function renderEmailing(emailing: boolean): void {
  const btn = $('email-btn') as HTMLButtonElement;
  if (btn) {
    btn.disabled = emailing;
    btn.textContent = emailing ? 'Sending...' : 'Send Email';
  }
}

export function renderError(error: string | null): void {
  const el = $('error-message');
  if (el) {
    el.textContent = error || '';
    el.style.display = error ? 'block' : 'none';
  }
}
