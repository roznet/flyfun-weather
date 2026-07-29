/** The Data Sources panel: a Table / Timeline switch over one catalog fetch.
 *
 * The two views answer different questions about the same rows — the table is
 * "what is each source and where is it now?", the timeline is "when does each
 * source land, and did it land when we expected?". Timeline is the default
 * because the schedule is the thing people come to this tab to reason about;
 * the table stays one click away and is still the right view for looking up a
 * resolution or level count.
 *
 * Both views render from a single `loadCatalog()` result, so switching costs
 * no network round-trip.
 */

import { loadCatalog, mountDataSourcesTable } from './data-sources-table';
import { mountDataSourcesTimeline } from './data-sources-timeline';
import { escapeHtml } from './utils';
import { t } from './i18n/i18n';

export type DataSourcesView = 'timeline' | 'table';

const STORAGE_KEY = 'wb_dataSourcesView';
const DEFAULT_VIEW: DataSourcesView = 'timeline';

function storedView(): DataSourcesView {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw === 'table' || raw === 'timeline') return raw;
  } catch {
    // Private browsing / storage disabled — fall through to the default.
  }
  return DEFAULT_VIEW;
}

function storeView(view: DataSourcesView): void {
  try {
    localStorage.setItem(STORAGE_KEY, view);
  } catch {
    // Not being able to remember the choice is not worth failing the render.
  }
}

/** Mount the switcher plus whichever view is selected into `host`. */
export async function mountDataSourcesView(host: HTMLElement): Promise<void> {
  host.innerHTML = `<p class="muted">${escapeHtml(t('dataSources.loading'))}</p>`;

  let resp;
  try {
    resp = await loadCatalog();
  } catch {
    host.innerHTML = `<p class="muted">${escapeHtml(t('dataSources.loadFailed'))}</p>`;
    return;
  }

  host.innerHTML = `
    <div class="ds-view-toggle" role="tablist" aria-label="${escapeHtml(t('dataSources.view.label'))}">
      <button type="button" class="btn-toggle" role="tab" data-view="timeline">${escapeHtml(t('dataSources.view.timeline'))}</button>
      <button type="button" class="btn-toggle" role="tab" data-view="table">${escapeHtml(t('dataSources.view.table'))}</button>
    </div>
    <div class="ds-view-body"></div>
  `;

  const body = host.querySelector<HTMLElement>('.ds-view-body');
  const buttons = Array.from(host.querySelectorAll<HTMLButtonElement>('.ds-view-toggle .btn-toggle'));
  if (!body) return;

  const apply = (view: DataSourcesView): void => {
    for (const btn of buttons) {
      const active = btn.dataset.view === view;
      btn.classList.toggle('active', active);
      btn.setAttribute('aria-selected', active ? 'true' : 'false');
    }
    if (view === 'timeline') {
      mountDataSourcesTimeline(body, resp.sources, resp.generated_at);
    } else {
      // Re-uses the cached catalog, so this is a render, not a fetch.
      void mountDataSourcesTable(body, 'full');
    }
  };

  for (const btn of buttons) {
    btn.addEventListener('click', () => {
      const view = btn.dataset.view === 'table' ? 'table' : 'timeline';
      storeView(view);
      apply(view);
    });
  }

  apply(storedView());
}
