/** Climatology tab — orchestrates dataset dropdown, sub-metric picker,
 *  map renders, and the volatility leaderboard.
 *
 * State machine per tab:
 *   month  (str)            — YYYY-MM from the picker
 *   dataset (DatasetKey)    — top-level dropdown (Category / Phenomena / Wind / Volatility)
 *   sub    (string|null)    — sub-metric for datasets that have one
 *
 * Render flow: dataset/sub change → fetch envelope → ``map.setScale(scale, popup)``
 * → ``map.setData(airports)``. Map class is dataset-agnostic.
 */

import {
  fetchCategoryMap, fetchPhenomenaMap, fetchWindMap,
  fetchVolatilityMap, fetchLeaderboard,
  type CategoryMapResponse, type ClimatologyMapEnvelope,
  type LeaderboardResponse, type WindMetric,
} from '../adapters/climatology-adapter';
import {
  ClimatologyMap, legendRows, pctColor,
  type MapAirport, type Scale,
} from './climatology-map';
import {
  CategoryDataset, PhenomenaDataset, WindDataset, VolatilityDataset,
  DATASET_LABEL,
  type CategoryKey, type DatasetKey, type PhenomenaKey, type WindKey,
} from './climatology-datasets';
import { $ } from '../utils';

type SubTab = 'map' | 'top';

const MONTH_LABEL = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                     'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

interface MonthOption { value: string; label: string; isMtd: boolean }

function buildMonthOptions(months: number, now: Date): MonthOption[] {
  const out: MonthOption[] = [];
  const y = now.getUTCFullYear();
  const m = now.getUTCMonth();
  for (let i = 0; i < months; i++) {
    const d = new Date(Date.UTC(y, m - i, 1));
    const isMtd = i === 0;
    const value = `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, '0')}`;
    const label = `${MONTH_LABEL[d.getUTCMonth()]} ${d.getUTCFullYear()}` +
                  (isMtd ? ' (to date)' : '');
    out.push({ value, label, isMtd });
  }
  return out;
}

export class ClimatologyTab {
  private mapView: ClimatologyMap | null = null;
  private month = '';
  private monthIsMtd = false;
  private dataset: DatasetKey = 'category';
  private sub: string = 'vfr';   // typed per-dataset; coerced at use site
  private currentSub: SubTab = 'map';
  private initialised = false;
  private inFlight = false;
  /** Set when reload is called while a fetch is in flight; triggers a
   *  re-run after the current one finishes so the picker state and the
   *  displayed data don't desync from rapid clicks. */
  private reloadDirty = false;
  private leaderboardLoaded = false;

  init(): void {
    if (this.initialised) {
      this.mapView?.invalidateSize();
      return;
    }
    this.initialised = true;

    this.populateMonthPicker();
    this.populateDatasetPicker();
    this.renderSubPicker();   // populates "Color by" for default dataset
    this.mapView = new ClimatologyMap('clim-map-container');
    this.wireControls();
    this.reload();
  }

  show(): void {
    if (this.initialised) {
      setTimeout(() => this.mapView?.invalidateSize(), 50);
    } else {
      setTimeout(() => this.init(), 50);
    }
  }

  // --- Pickers -------------------------------------------------------------

  private populateMonthPicker(): void {
    const sel = $('clim-month-picker') as HTMLSelectElement | null;
    if (!sel) return;
    const opts = buildMonthOptions(13, new Date());
    sel.innerHTML = opts.map((o) => `<option value="${o.value}">${o.label}</option>`).join('');
    this.month = opts[0].value;
    this.monthIsMtd = opts[0].isMtd;
    sel.value = this.month;
  }

  private populateDatasetPicker(): void {
    const sel = $('clim-dataset-picker') as HTMLSelectElement | null;
    if (!sel) return;
    sel.innerHTML = (Object.keys(DATASET_LABEL) as DatasetKey[])
      .map((k) => `<option value="${k}">${DATASET_LABEL[k]}</option>`)
      .join('');
    sel.value = this.dataset;
  }

  /** Repopulate the "Color by" row based on the active dataset. */
  private renderSubPicker(): void {
    const host = $('clim-metric-picker');
    if (!host) return;
    const opts = this.subOptionsForActiveDataset();
    if (opts.length === 0) {
      host.innerHTML = '<span style="color:var(--text-muted); font-size:0.75rem">(single metric)</span>';
      this.sub = '';
      return;
    }
    // Default sub: first eligible option (skips MTD-disabled ones if current month is MTD).
    const firstEligible = opts.find((o) => !this.isSubDisabled(o.value)) ?? opts[0];
    this.sub = firstEligible.value;
    host.innerHTML = opts.map((o) => {
      const disabled = this.isSubDisabled(o.value);
      const active = o.value === this.sub ? ' active' : '';
      const title = disabled ? ' title="Available for completed months only"' : '';
      return `<button class="btn-toggle${active}" data-sub="${o.value}"${disabled ? ' disabled' : ''}${title}>${o.label}</button>`;
    }).join('');
    this.wireSubButtons();
  }

  private subOptionsForActiveDataset(): { value: string; label: string }[] {
    switch (this.dataset) {
      case 'category':   return CategoryDataset.subOptions.map((o) => ({ value: o.value, label: o.label }));
      case 'phenomena':  return PhenomenaDataset.subOptions.map((o) => ({ value: o.value, label: o.label }));
      case 'wind':       return WindDataset.subOptions.map((o) => ({ value: o.value, label: o.label }));
      case 'volatility': return VolatilityDataset.subOptions;
    }
  }

  private isSubDisabled(sub: string): boolean {
    if (this.dataset === 'wind' && this.monthIsMtd) {
      const opt = WindDataset.subOptions.find((o) => o.value === sub);
      return !!opt && !opt.mtd;
    }
    return false;
  }

  // --- Wiring --------------------------------------------------------------

  private wireSubButtons(): void {
    document.querySelectorAll<HTMLButtonElement>('#clim-metric-picker .btn-toggle')
      .forEach((btn) => {
        btn.addEventListener('click', () => {
          if (btn.hasAttribute('disabled')) return;
          document.querySelectorAll('#clim-metric-picker .btn-toggle')
            .forEach((b) => b.classList.remove('active'));
          btn.classList.add('active');
          this.sub = btn.dataset.sub!;
          this.invalidateLeaderboard();
          this.reload();
        });
      });
  }

  private invalidateLeaderboard(): void {
    this.leaderboardLoaded = false;
    // If the user is currently viewing the leaderboard sub-tab, refresh it
    // immediately so dataset/sub/month changes are reflected. Otherwise
    // the next switch to that sub-tab will fetch.
    if (this.currentSub === 'top') this.loadLeaderboard();
  }

  private wireControls(): void {
    const monthSel = $('clim-month-picker') as HTMLSelectElement | null;
    monthSel?.addEventListener('change', () => {
      this.month = monthSel.value;
      const now = new Date();
      const cur = `${now.getUTCFullYear()}-${String(now.getUTCMonth() + 1).padStart(2, '0')}`;
      this.monthIsMtd = this.month === cur;
      this.renderSubPicker();
      this.invalidateLeaderboard();
      this.reload();
    });

    const dsSel = $('clim-dataset-picker') as HTMLSelectElement | null;
    dsSel?.addEventListener('change', () => {
      this.dataset = dsSel.value as DatasetKey;
      this.renderSubPicker();
      this.invalidateLeaderboard();
      this.reload();
    });

    document.querySelectorAll<HTMLButtonElement>('#clim-subtabs .tab-btn')
      .forEach((btn) => {
        btn.addEventListener('click', () => {
          this.switchSubTab(btn.dataset.subtab as SubTab);
        });
      });
  }

  private switchSubTab(sub: SubTab): void {
    this.currentSub = sub;
    document.querySelectorAll('#clim-subtabs .tab-btn').forEach((b) => {
      b.classList.toggle('active', (b as HTMLElement).dataset.subtab === sub);
    });
    document.querySelectorAll('.clim-subpanel').forEach((p) => {
      p.classList.toggle('active', p.id === `clim-subpanel-${sub}`);
    });
    if (sub === 'map') {
      setTimeout(() => this.mapView?.invalidateSize(), 50);
    } else if (sub === 'top' && !this.leaderboardLoaded) {
      this.loadLeaderboard();
    }
  }

  // --- Status / legend -----------------------------------------------------

  private setStatus(text: string, kind: 'info' | 'error' = 'info'): void {
    const el = $('clim-status');
    if (!el) return;
    el.textContent = text;
    el.className = 'map-info' + (kind === 'error' ? ' map-info-error' : '');
  }

  private renderLegend(scale: Scale): void {
    const el = $('clim-legend');
    if (!el) return;
    const rows = legendRows(scale).map(
      (s) => `<div class="legend-item">
        <span class="legend-dot" style="background:${s.color}"></span>${s.label}
      </div>`,
    ).join('');
    el.innerHTML = `
      <div class="map-legend-title">${scale.title}</div>
      ${rows}
      <div class="legend-item" style="margin-top:0.25rem; opacity:0.7">
        <span class="legend-dot" style="background:#888"></span>no data
      </div>
    `;
  }

  // --- Render --------------------------------------------------------------

  private async reload(): Promise<void> {
    if (this.inFlight) {
      this.reloadDirty = true;
      return;
    }
    this.inFlight = true;
    this.setStatus('Loading…');
    try {
      const { scale, popup, airports, header } = await this.fetchActive();
      this.renderLegend(scale);
      this.mapView?.setScale(scale, popup);
      this.mapView?.setData(airports);
      this.setStatus(header);
    } catch (err) {
      this.setStatus(`Failed to load: ${(err as Error).message}`, 'error');
    } finally {
      this.inFlight = false;
      if (this.reloadDirty) {
        this.reloadDirty = false;
        this.reload();
      }
    }
  }

  private async fetchActive(): Promise<{
    scale: Scale; popup: (a: MapAirport) => string;
    airports: MapAirport[]; header: string;
  }> {
    switch (this.dataset) {
      case 'category': {
        const data: CategoryMapResponse = await fetchCategoryMap(this.month);
        const sub = (this.sub || 'vfr') as CategoryKey;
        const scale = CategoryDataset.scaleFor(sub);
        const airports: MapAirport[] = data.airports.map((a) => {
          const v = a[`pct_${sub}` as 'pct_vfr'];
          return { ...a, value: v === undefined ? null : v };
        });
        return { scale, popup: CategoryDataset.popup, airports,
                 header: headerFromCategory(data) };
      }
      case 'phenomena': {
        const sub = (this.sub || 'ts') as PhenomenaKey;
        const data = await fetchPhenomenaMap(this.month, sub);
        return { scale: PhenomenaDataset.scaleFor(sub),
                 popup: PhenomenaDataset.popup,
                 airports: data.airports as MapAirport[],
                 header: headerFromEnvelope(data) };
      }
      case 'wind': {
        const sub = (this.sub || 'gust') as WindKey;
        const data = await fetchWindMap(this.month, sub as WindMetric);
        // Inject _sub so the popup knows which label to print.
        const airports: MapAirport[] = data.airports.map(
          (a) => ({ ...a, _sub: sub }),
        );
        return { scale: WindDataset.scaleFor(sub),
                 popup: WindDataset.popup, airports,
                 header: headerFromEnvelope(data) };
      }
      case 'volatility': {
        const data = await fetchVolatilityMap(this.month);
        return { scale: VolatilityDataset.scaleFor(),
                 popup: VolatilityDataset.popup,
                 airports: data.airports as MapAirport[],
                 header: headerFromEnvelope(data) };
      }
    }
  }

  // --- Leaderboard (Top airports sub-tab) ----------------------------------

  private async loadLeaderboard(): Promise<void> {
    const host = $('clim-leaderboard');
    if (!host) return;
    host.innerHTML = '<div class="map-info" style="padding:1rem">Loading…</div>';
    try {
      const subParam = this.dataset === 'volatility' ? null : (this.sub || null);
      const data = await fetchLeaderboard(this.month, this.dataset, subParam, 20);
      const scale = this.activeScale();
      host.innerHTML = renderLeaderboardHtml(data, scale);
      this.leaderboardLoaded = true;
    } catch (err) {
      host.innerHTML = `<div class="map-info map-info-error" style="padding:1rem">
        Failed: ${(err as Error).message}</div>`;
    }
  }

  /** Color scale currently in effect — used to dot leaderboard rows. */
  private activeScale(): Scale | null {
    switch (this.dataset) {
      case 'category':   return CategoryDataset.scaleFor((this.sub || 'vfr') as CategoryKey);
      case 'phenomena':  return PhenomenaDataset.scaleFor((this.sub || 'ts') as PhenomenaKey);
      case 'wind':       return WindDataset.scaleFor((this.sub || 'gust') as WindKey);
      case 'volatility': return VolatilityDataset.scaleFor();
    }
  }
}

// --- Local helpers ---------------------------------------------------------

function headerFromCategory(data: CategoryMapResponse): string {
  const plotted = data.airports.length;
  const withData = data.airports.filter((a) => a.n_obs > 0).length;
  // Low-confidence airports (high NULL-category share) count as "with data"
  // but render faded and are leaderboard-omitted — call them out separately.
  const lowConf = data.airports.filter((a) => a.low_confidence).length;
  const note = data.is_mtd
    ? `Month to date · ${data.as_of_date ?? 'no completed days yet'}`
    : 'Completed month';
  const lowConfNote = lowConf > 0 ? `, ${lowConf} low-confidence` : '';
  return `${note} · ${plotted} airports plotted (${withData} with data${lowConfNote})`;
}

function headerFromEnvelope(data: ClimatologyMapEnvelope): string {
  const plotted = data.airports.length;
  const withData = data.airports.filter((a) => a.value !== null).length;
  const note = data.is_mtd
    ? `Month to date · ${data.as_of_date ?? 'no completed days yet'}`
    : 'Completed month';
  return `${note} · ${plotted} plotted (${withData} with data)`;
}

function formatValue(value: number | null, unit: string): string {
  if (value === null) return '—';
  if (unit === '%') return `${value.toFixed(1)}%`;
  if (unit === 'kt') return `${value.toFixed(0)} kt`;
  return value.toFixed(3);  // ratio
}

function renderLeaderboardHtml(data: LeaderboardResponse, scale: Scale | null): string {
  // For the category dataset the gate + displayed count are categorizable
  // obs (rows whose flight_category is non-NULL), not raw METAR count — label
  // them "usable" so a lower number than the user's METAR provider is clear.
  const obsLabel = data.dataset === 'category' ? 'Usable obs' : 'Obs';
  const obsNoun = data.dataset === 'category' ? 'usable observations' : 'observations';
  if (data.rows.length === 0) {
    return `<div class="map-info" style="padding:1rem">
      No airports meet the minimum ${obsNoun} count
      (${data.min_n_obs} required) for this period.
    </div>`;
  }
  const rows = data.rows.map((r, i) => {
    const color = (r.value !== null && scale) ? pctColor(r.value, scale) : '#888';
    return `
      <tr>
        <td style="text-align:right; padding-right:0.5rem; color:var(--text-muted)">${i + 1}.</td>
        <td>
          <span class="legend-dot" style="background:${color}; display:inline-block; margin-right:0.4rem; vertical-align:middle"></span>
          <b>${r.icao}</b>
        </td>
        <td style="text-align:right">${formatValue(r.value, data.unit)}</td>
        <td style="text-align:right; color:var(--text-muted)">${r.n_obs.toLocaleString()}</td>
      </tr>
    `;
  }).join('');
  const monthNote = data.is_mtd
    ? `${data.month} (to ${data.as_of_date ?? '—'})`
    : data.month;
  return `
    <div class="clim-leaderboard-wrap">
      <h3 style="margin-top:0">Top airports by ${data.label} · ${monthNote}</h3>
      <p style="color:var(--text-muted); font-size:0.8rem; margin:0.25rem 0 0.75rem">
        Ranked highest first. Minimum ${data.min_n_obs} ${obsNoun}.
        Change Dataset / Color by on the Map tab to rank by a different metric.
      </p>
      <table class="clim-leaderboard-table">
        <thead>
          <tr>
            <th></th><th>Airport</th>
            <th style="text-align:right">${data.label}</th>
            <th style="text-align:right">${obsLabel}</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
  `;
}
