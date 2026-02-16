/** Settings page entry point — tabbed preferences with advisory configuration. */

import { fetchCurrentUser } from './adapters/auth-adapter';
import { renderUserInfo, STATUS_DISMISS_MS, initModelCatalog, allModelKeys, defaultModelKeys, modelLabel } from './utils';
import {
  fetchPreferences,
  savePreferences,
  clearAutorouterCreds,
  fetchUsageSummary,
  fetchAdvisoryCatalog,
  fetchModelCatalog,
  type PreferencesResponse,
  type AdvisoryPreferences,
  type AdvisoryCatalogEntry,
  type AdvisoryParameterDef,
  type UsageSummary,
} from './adapters/preferences-adapter';

/** Category display order and labels.
 *  Any categories not listed here will appear at the end under their raw key. */
const CATEGORY_ORDER: [string, string][] = [
  ['icing', 'Icing'],
  ['cloud', 'Cloud'],
  ['turbulence', 'Turbulence'],
  ['convective', 'Convective'],
  ['airport', 'Airport'],
  ['model', 'Forecast'],
];

let catalog: AdvisoryCatalogEntry[] = [];

/** Generate model checkboxes from the model catalog. */
function renderModelCheckboxes(): void {
  const container = document.getElementById('model-checkboxes');
  if (!container) return;
  const defaults = defaultModelKeys();
  container.innerHTML = allModelKeys().map(m => {
    const label = modelLabel(m);
    const checked = defaults.includes(m) ? ' checked' : '';
    return `<label class="checkbox-label"><input type="checkbox" id="model-${m}"${checked}> ${label}</label>`;
  }).join('');
}

async function init(): Promise<void> {
  const user = await fetchCurrentUser();
  if (!user) {
    window.location.href = '/login.html';
    return;
  }
  renderUserInfo(user);

  // Tab switching
  for (const btn of document.querySelectorAll<HTMLButtonElement>('.tab-btn')) {
    btn.addEventListener('click', () => switchTab(btn.dataset.tab!));
  }

  // Load preferences, advisory catalog, and model catalog in parallel
  const [prefs, catalogResult, modelCatalog] = await Promise.all([
    fetchPreferences().catch(err => {
      showStatus(`Failed to load preferences: ${err}`, true);
      return null;
    }),
    fetchAdvisoryCatalog().catch(err => {
      showStatus(`Failed to load advisory catalog: ${err}`, true);
      return [] as AdvisoryCatalogEntry[];
    }),
    fetchModelCatalog().catch(err => {
      showStatus(`Failed to load model catalog: ${err}`, true);
      return [];
    }),
  ]);

  catalog = catalogResult;
  initModelCatalog(modelCatalog);
  renderModelCheckboxes();

  if (prefs) {
    populateForm(prefs);
  }
  renderAdvisorySettings(catalog, prefs?.advisories ?? { enabled: null, params: null });

  // Load usage (non-blocking)
  fetchUsageSummary()
    .then(renderUsage)
    .catch(() => { /* usage section stays hidden */ });

  // Save button
  const form = document.getElementById('settings-form') as HTMLFormElement;
  form?.addEventListener('submit', async (e) => {
    e.preventDefault();
    await handleSave();
  });

  // Clear autorouter credentials
  const clearBtn = document.getElementById('clear-autorouter-btn');
  clearBtn?.addEventListener('click', async () => {
    try {
      await clearAutorouterCreds();
      (document.getElementById('input-ar-username') as HTMLInputElement).value = '';
      (document.getElementById('input-ar-password') as HTMLInputElement).value = '';
      updateAutorouterStatus(false);
      showStatus('Autorouter credentials cleared.');
    } catch (err) {
      showStatus(`Failed to clear credentials: ${err}`, true);
    }
  });
}

function switchTab(tabId: string): void {
  for (const btn of document.querySelectorAll<HTMLButtonElement>('.tab-btn')) {
    btn.classList.toggle('active', btn.dataset.tab === tabId);
  }
  for (const panel of document.querySelectorAll<HTMLElement>('.tab-panel')) {
    panel.classList.toggle('active', panel.id === `tab-${tabId}`);
  }
}

function populateForm(prefs: PreferencesResponse): void {
  const d = prefs.defaults;
  if (d.cruise_altitude_ft != null) {
    (document.getElementById('input-altitude') as HTMLInputElement).value = String(d.cruise_altitude_ft);
  }
  if (d.flight_ceiling_ft != null) {
    (document.getElementById('input-ceiling') as HTMLInputElement).value = String(d.flight_ceiling_ft);
  }

  // Models checkboxes
  const selectedModels = d.models || defaultModelKeys();
  for (const m of allModelKeys()) {
    const cb = document.getElementById(`model-${m}`) as HTMLInputElement;
    if (cb) cb.checked = selectedModels.includes(m);
  }

  updateAutorouterStatus(prefs.has_autorouter_creds);

  // Service toggles
  const grametToggle = document.getElementById('toggle-gramet') as HTMLInputElement;
  const llmToggle = document.getElementById('toggle-llm-digest') as HTMLInputElement;
  if (grametToggle) grametToggle.checked = prefs.gramet_enabled;
  if (llmToggle) llmToggle.checked = prefs.llm_digest_enabled;
}

// --- Advisory settings rendering ---

function renderAdvisorySettings(
  entries: AdvisoryCatalogEntry[],
  userAdvisories: AdvisoryPreferences,
): void {
  const container = document.getElementById('advisory-settings');
  if (!container) return;

  // Group by category
  const grouped = new Map<string, AdvisoryCatalogEntry[]>();
  for (const entry of entries) {
    const list = grouped.get(entry.category) || [];
    list.push(entry);
    grouped.set(entry.category, list);
  }

  const enabledMap = userAdvisories.enabled ?? {};
  const paramsMap = userAdvisories.params ?? {};

  // Build ordered list of categories: known order first, then any extras
  const knownKeys = new Set(CATEGORY_ORDER.map(([k]) => k));
  const allCategories: [string, string][] = [...CATEGORY_ORDER];
  for (const catKey of grouped.keys()) {
    if (!knownKeys.has(catKey)) {
      // Unknown category — capitalize key as label
      allCategories.push([catKey, catKey.charAt(0).toUpperCase() + catKey.slice(1)]);
    }
  }

  let html = '';
  for (const [catKey, catLabel] of allCategories) {
    const catEntries = grouped.get(catKey);
    if (!catEntries?.length) continue;

    html += `<div class="advisory-category">`;
    html += `<div class="advisory-category-title">${catLabel}</div>`;

    for (const entry of catEntries) {
      const isEnabled = enabledMap[entry.id] ?? entry.default_enabled;
      const userParams = paramsMap[entry.id] ?? {};

      html += `<div class="advisory-setting">`;
      html += `<div class="advisory-header">`;
      html += `<label class="checkbox-label">`;
      html += `<input type="checkbox" data-advisory-id="${entry.id}" ${isEnabled ? 'checked' : ''}>`;
      html += ` ${entry.name}`;
      html += `<span class="advisory-desc">${entry.short_description}</span>`;
      html += `</label>`;
      html += `</div>`;

      if (entry.parameters.length > 0) {
        html += `<div class="advisory-params" data-params-for="${entry.id}">`;
        for (const param of entry.parameters) {
          const value = userParams[param.key] ?? param.default;
          html += renderParamInput(entry.id, param, value, isEnabled);
        }
        html += `</div>`;
      }

      html += `</div>`;
    }

    html += `</div>`;
  }

  container.innerHTML = html;

  // Toggle param visibility when advisory is toggled
  for (const cb of container.querySelectorAll<HTMLInputElement>('input[data-advisory-id]')) {
    cb.addEventListener('change', () => {
      const paramsDiv = container.querySelector(`[data-params-for="${cb.dataset.advisoryId}"]`) as HTMLElement;
      if (paramsDiv) {
        for (const input of paramsDiv.querySelectorAll<HTMLInputElement>('input')) {
          input.disabled = !cb.checked;
        }
      }
    });
  }
}

function renderParamInput(
  advisoryId: string,
  param: AdvisoryParameterDef,
  value: number,
  enabled: boolean,
): string {
  const minAttr = param.min != null ? ` min="${param.min}"` : '';
  const maxAttr = param.max != null ? ` max="${param.max}"` : '';
  const stepAttr = param.step != null ? ` step="${param.step}"` : '';
  const disabledAttr = enabled ? '' : ' disabled';
  const unitStr = param.unit ? `<span class="param-unit">${param.unit}</span>` : '';

  return `<div class="advisory-param">
    <label title="${param.description}">${param.label}</label>
    <input type="number" data-advisory-param="${advisoryId}:${param.key}"
      value="${value}"${minAttr}${maxAttr}${stepAttr}${disabledAttr}>
    ${unitStr}
  </div>`;
}

/** Collect advisory preferences from the form. */
function collectAdvisoryPrefs(): AdvisoryPreferences {
  const container = document.getElementById('advisory-settings');
  if (!container) return { enabled: null, params: null };

  const enabled: Record<string, boolean> = {};
  const params: Record<string, Record<string, number>> = {};

  // Collect enabled states
  for (const cb of container.querySelectorAll<HTMLInputElement>('input[data-advisory-id]')) {
    const id = cb.dataset.advisoryId!;
    enabled[id] = cb.checked;
  }

  // Collect parameter values
  for (const input of container.querySelectorAll<HTMLInputElement>('input[data-advisory-param]')) {
    const [advId, paramKey] = input.dataset.advisoryParam!.split(':');
    const val = parseFloat(input.value);
    if (!isNaN(val)) {
      if (!params[advId]) params[advId] = {};
      params[advId][paramKey] = val;
    }
  }

  return { enabled, params };
}

// --- Save ---

async function handleSave(): Promise<void> {
  const altitude = parseInt((document.getElementById('input-altitude') as HTMLInputElement).value, 10);
  const ceiling = parseInt((document.getElementById('input-ceiling') as HTMLInputElement).value, 10);

  const models: string[] = [];
  for (const m of allModelKeys()) {
    const cb = document.getElementById(`model-${m}`) as HTMLInputElement;
    if (cb?.checked) models.push(m);
  }
  if (models.length === 0) {
    showStatus('Select at least one forecast model.', true);
    return;
  }

  const arUsername = (document.getElementById('input-ar-username') as HTMLInputElement).value.trim();
  const arPassword = (document.getElementById('input-ar-password') as HTMLInputElement).value.trim();

  const advisories = collectAdvisoryPrefs();

  const grametEnabled = (document.getElementById('toggle-gramet') as HTMLInputElement)?.checked ?? true;
  const llmDigestEnabled = (document.getElementById('toggle-llm-digest') as HTMLInputElement)?.checked ?? true;

  try {
    const result = await savePreferences({
      defaults: {
        cruise_altitude_ft: isNaN(altitude) ? null : altitude,
        flight_ceiling_ft: isNaN(ceiling) ? null : ceiling,
        models,
      },
      advisories,
      autorouter_username: arUsername || undefined,
      autorouter_password: arPassword || undefined,
      gramet_enabled: grametEnabled,
      llm_digest_enabled: llmDigestEnabled,
    });
    updateAutorouterStatus(result.has_autorouter_creds);
    // Clear password field after successful save
    if (arPassword) {
      (document.getElementById('input-ar-password') as HTMLInputElement).value = '';
    }
    showStatus('Preferences saved.');
  } catch (err) {
    showStatus(`Failed to save: ${err}`, true);
  }
}

// --- Autorouter status ---

function updateAutorouterStatus(hasCreds: boolean): void {
  const badge = document.getElementById('ar-status-badge');
  if (!badge) return;
  if (hasCreds) {
    badge.textContent = 'Configured';
    badge.className = 'badge badge-green';
  } else {
    badge.textContent = 'Not set';
    badge.className = 'badge badge-none';
  }
  const clearBtn = document.getElementById('clear-autorouter-btn') as HTMLButtonElement;
  if (clearBtn) clearBtn.style.display = hasCreds ? 'inline-block' : 'none';
}

// --- Status messages ---

function showStatus(message: string, isError = false): void {
  const el = document.getElementById('status-message');
  if (!el) return;
  el.textContent = message;
  el.classList.add('visible');
  el.className = isError ? 'status-error' : 'status-success';
  if (!isError) {
    setTimeout(() => { el.classList.remove('visible'); }, STATUS_DISMISS_MS);
  }
}

// --- Usage rendering ---

function renderUsage(usage: UsageSummary): void {
  const section = document.getElementById('usage-section');
  if (!section) return;
  section.classList.remove('hidden-section');

  const todayGrid = document.getElementById('usage-today-grid');
  if (todayGrid) {
    todayGrid.innerHTML = [
      renderUsageBar('Briefings', usage.today.briefings, null),
      renderUsageBar('Open-Meteo', usage.today.open_meteo.used, usage.today.open_meteo.limit),
      renderUsageBar('GRAMET', usage.today.gramet.used, usage.today.gramet.limit),
      renderUsageBar('AI Digest', usage.today.llm_digest.used, usage.today.llm_digest.limit),
    ].join('');
  }

  const monthSummary = document.getElementById('usage-month-summary');
  if (monthSummary) {
    const KILO = 1000;
    const tokens = usage.month.total_tokens >= KILO
      ? `~${Math.round(usage.month.total_tokens / KILO)}K tokens`
      : `${usage.month.total_tokens} tokens`;
    monthSummary.textContent =
      `${usage.month.briefings} briefings / ${usage.month.gramet} GRAMET / ` +
      `${usage.month.llm_digest} AI digests / ${tokens}`;
  }
}

function renderUsageBar(label: string, used: number, limit: number | null): string {
  if (limit !== null) {
    const pct = Math.min(100, Math.round((used / limit) * 100));
    const cls = pct >= 90 ? 'danger' : pct >= 70 ? 'warn' : '';
    return `
      <div class="usage-row">
        <span class="usage-label">${label}</span>
        <div class="usage-bar-track">
          <div class="usage-bar-fill ${cls}" style="width:${pct}%"></div>
        </div>
        <span class="usage-count">${used} / ${limit}</span>
      </div>`;
  }
  return `
    <div class="usage-row">
      <span class="usage-label">${label}</span>
      <span class="usage-count" style="flex:1;">${used}</span>
    </div>`;
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}
