/** Settings page entry point — tabbed preferences with profile-based advisory configuration. */

import { fetchCurrentUser } from './adapters/auth-adapter';
import { fetchCostSummary, type CostSummary } from './adapters/credits-adapter';
import { renderUserInfo, escapeHtml, STATUS_DISMISS_MS, initModelCatalog, allModelKeys, defaultModelKeys, modelLabel } from './utils';
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
import {
  fetchProfiles,
  createProfile,
  updateProfile,
  deleteProfile,
  duplicateProfile,
  type ProfileResponse,
  type ProfileSettings,
} from './adapters/profiles-adapter';
import { initTheme } from './theme';
import { initI18n, t, setLocale, getLocale, getDateLocale } from './i18n/i18n';
import { initInfoPopup, showPopupContent } from './components/info-popup';
import { renderAdvisoryPopup } from './helpers/advisory-popup';

/** Category display order and labels.
 *  Any categories not listed here will appear at the end under their raw key. */
const CATEGORY_KEYS: [string, string][] = [
  ['icing', 'settings.cat.icing'],
  ['cloud', 'settings.cat.cloud'],
  ['turbulence', 'settings.cat.turbulence'],
  ['convective', 'settings.cat.convective'],
  ['airport', 'settings.cat.airport'],
  ['model', 'settings.cat.model'],
];

let catalog: AdvisoryCatalogEntry[] = [];
let profiles: ProfileResponse[] = [];
let activeProfileId: number | null = null;

/** Default advisory models: all default briefing models except best_match. */
function defaultAdvisoryModelKeys(): string[] {
  return defaultModelKeys().filter(k => k !== 'best_match');
}

/** Get currently selected briefing model keys from the checkboxes. */
function getSelectedBriefingModels(): string[] {
  const result: string[] = [];
  for (const m of allModelKeys()) {
    const cb = document.getElementById(`model-${m}`) as HTMLInputElement;
    if (cb?.checked) result.push(m);
  }
  return result;
}

/** Generate model checkboxes from the model catalog. */
function renderModelCheckboxes(selectedModels?: string[]): void {
  const container = document.getElementById('model-checkboxes');
  if (!container) return;
  const defaults = selectedModels || defaultModelKeys();
  container.innerHTML = allModelKeys().map(m => {
    const label = modelLabel(m);
    const checked = defaults.includes(m) ? ' checked' : '';
    return `<label class="checkbox-label"><input type="checkbox" id="model-${m}"${checked}> ${label}</label>`;
  }).join('');

  // When briefing models change, re-render advisory model checkboxes
  for (const m of allModelKeys()) {
    const cb = document.getElementById(`model-${m}`) as HTMLInputElement;
    cb?.addEventListener('change', () => {
      renderAdvisoryModelCheckboxes(getSelectedBriefingModels());
    });
  }
}

/** Render advisory model checkboxes (subset of currently selected briefing models). */
function renderAdvisoryModelCheckboxes(
  briefingModels: string[],
  selected?: string[],
): void {
  const container = document.getElementById('advisory-model-checkboxes');
  if (!container) return;

  // Preserve current selections if not explicitly provided
  if (!selected) {
    selected = [];
    for (const cb of container.querySelectorAll<HTMLInputElement>('input[type="checkbox"]')) {
      if (cb.checked && cb.dataset.advModel) selected.push(cb.dataset.advModel);
    }
  }

  // If no explicit selections, use defaults (everything except best_match)
  const effectiveSelected = selected.length > 0
    ? selected
    : defaultAdvisoryModelKeys();

  container.innerHTML = briefingModels.map(m => {
    const label = modelLabel(m);
    const checked = effectiveSelected.includes(m) ? ' checked' : '';
    return `<label class="checkbox-label"><input type="checkbox" data-adv-model="${m}" id="adv-model-${m}"${checked}> ${label}</label>`;
  }).join('');
}

// --- Profile management ---

function renderProfileSelector(): void {
  const select = document.getElementById('profile-select') as HTMLSelectElement;
  if (!select) return;

  select.innerHTML = profiles.map(p => {
    const defaultTag = p.is_default ? t('flights.form.defaultTag') : '';
    return `<option value="${p.id}"${p.id === activeProfileId ? ' selected' : ''}>${escapeHtml(p.name)}${defaultTag}</option>`;
  }).join('');

  // Update delete button state
  const deleteBtn = document.getElementById('btn-delete-profile') as HTMLButtonElement;
  if (deleteBtn) {
    const activeProfile = profiles.find(p => p.id === activeProfileId);
    deleteBtn.disabled = !!activeProfile?.is_default;
  }
}

function populateProfileForm(profile: ProfileResponse): void {
  const s = profile.settings;

  // Flight defaults
  const flightRulesSelect = document.getElementById('input-flight-rules') as HTMLSelectElement;
  const altInput = document.getElementById('input-altitude') as HTMLInputElement;
  const ceilInput = document.getElementById('input-ceiling') as HTMLInputElement;
  const speedInput = document.getElementById('input-speed') as HTMLInputElement;
  if (flightRulesSelect) flightRulesSelect.value = s.flight_rules ?? 'vfr_ifr';
  if (altInput) altInput.value = String(s.cruise_altitude_ft ?? 8000);
  if (ceilInput) ceilInput.value = String(s.flight_ceiling_ft ?? 18000);
  if (speedInput) speedInput.value = s.speed_kt != null ? String(s.speed_kt) : '';

  // Models
  const selectedModels = s.models || defaultModelKeys();
  renderModelCheckboxes(selectedModels);

  // Advisory model checkboxes
  renderAdvisoryModelCheckboxes(selectedModels, s.advisory_models ?? undefined);

  // Service toggles
  const grametToggle = document.getElementById('toggle-gramet') as HTMLInputElement;
  const llmToggle = document.getElementById('toggle-llm-digest') as HTMLInputElement;
  const icingEnhanceToggle = document.getElementById('toggle-icing-enhance') as HTMLInputElement;
  if (grametToggle) grametToggle.checked = s.gramet_enabled ?? true;
  if (llmToggle) llmToggle.checked = s.llm_digest_enabled ?? true;
  if (icingEnhanceToggle) icingEnhanceToggle.checked = s.icing_severity_enhance ?? false;

  // Icing method selector
  const icingMethodSelect = document.getElementById('input-icing-method') as HTMLSelectElement;
  if (icingMethodSelect) icingMethodSelect.value = s.icing_method ?? 'ogimet_dd';

  // Cloud method selector
  const cloudMethodSelect = document.getElementById('input-cloud-method') as HTMLSelectElement;
  if (cloudMethodSelect) cloudMethodSelect.value = s.cloud_method ?? 'dd';

  // Convective method selector
  const convectiveMethodSelect = document.getElementById('input-convective-method') as HTMLSelectElement;
  if (convectiveMethodSelect) convectiveMethodSelect.value = s.convective_method ?? 'thermo';

  // Advisories
  const advPrefs: AdvisoryPreferences = s.advisories ?? { enabled: null, params: null };
  const aggSelect = document.getElementById('advisory-aggregation') as HTMLSelectElement;
  if (aggSelect) aggSelect.value = advPrefs.aggregation ?? 'majority';
  renderAdvisorySettings(catalog, advPrefs);
}

function switchProfile(profileId: number): void {
  activeProfileId = profileId;
  renderProfileSelector();
  const profile = profiles.find(p => p.id === profileId);
  if (profile) {
    populateProfileForm(profile);
  }
}

async function handleNewProfile(): Promise<void> {
  const name = prompt(t('settings.enterProfileName'));
  if (!name?.trim()) return;

  try {
    const newProfile = await createProfile(name.trim());
    profiles.push(newProfile);
    activeProfileId = newProfile.id;
    renderProfileSelector();
    populateProfileForm(newProfile);
    showStatus(t('settings.profileCreated', { name: name.trim() }));
  } catch (err) {
    showStatus(`Failed to create profile: ${err}`, true);
  }
}

async function handleDuplicateProfile(): Promise<void> {
  if (!activeProfileId) return;
  const source = profiles.find(p => p.id === activeProfileId);
  const defaultName = source ? `${source.name} (copy)` : 'Copy';
  const name = prompt(t('settings.enterDuplicateName'), defaultName);
  if (!name?.trim()) return;

  try {
    const dup = await duplicateProfile(activeProfileId, name.trim());
    profiles.push(dup);
    activeProfileId = dup.id;
    renderProfileSelector();
    populateProfileForm(dup);
    showStatus(t('settings.profileDuplicated', { name: name.trim() }));
  } catch (err) {
    showStatus(`Failed to duplicate profile: ${err}`, true);
  }
}

async function handleRenameProfile(): Promise<void> {
  if (!activeProfileId) return;
  const current = profiles.find(p => p.id === activeProfileId);
  const name = prompt(t('settings.enterNewName'), current?.name);
  if (!name?.trim() || name.trim() === current?.name) return;

  try {
    const updated = await updateProfile(activeProfileId, { name: name.trim() });
    const idx = profiles.findIndex(p => p.id === activeProfileId);
    if (idx >= 0) profiles[idx] = updated;
    renderProfileSelector();
    showStatus(t('settings.profileRenamed', { name: name.trim() }));
  } catch (err) {
    showStatus(`Failed to rename profile: ${err}`, true);
  }
}

async function handleDeleteProfile(): Promise<void> {
  if (!activeProfileId) return;
  const current = profiles.find(p => p.id === activeProfileId);
  if (current?.is_default) {
    showStatus(t('settings.cannotDeleteDefault'), true);
    return;
  }
  if (!confirm(t('settings.deleteProfileConfirm', { name: current?.name ?? '' }))) return;

  try {
    await deleteProfile(activeProfileId);
    profiles = profiles.filter(p => p.id !== activeProfileId);
    // Switch to the default profile
    const defaultProfile = profiles.find(p => p.is_default) || profiles[0];
    if (defaultProfile) {
      switchProfile(defaultProfile.id);
    }
    showStatus(t('settings.profileDeleted'));
  } catch (err) {
    showStatus(`Failed to delete profile: ${err}`, true);
  }
}

// --- Init ---

/** Translate static HTML elements on the settings page. */
function translateStaticElements(): void {
  const set = (sel: string, key: string) => {
    const el = document.querySelector(sel);
    if (el) el.textContent = t(key);
  };
  const setHtml = (sel: string, key: string) => {
    const el = document.querySelector(sel);
    if (el) el.innerHTML = t(key);
  };
  set('h1', 'page.settings.title');
  set('button[type="submit"].btn-primary', 'page.settings.save');
  set('a.btn-secondary[href="/"]', 'page.settings.backToFlights');
  set('.tab-btn[data-tab="flight"]', 'page.settings.tabProfiles');
  set('.tab-btn[data-tab="services"]', 'page.settings.tabAccount');
  // Account tab
  set('#tab-services .section:nth-child(1) h3', 'page.settings.language');
  set('#tab-services .section:nth-child(1) .section-hint', 'page.settings.languageHint');
  set('label[for="input-locale"]', 'page.settings.displayLanguage');
  set('#tab-services .section:nth-child(2) h3', 'page.settings.autorouterTitle');
  set('label[for="input-ar-username"]', 'page.settings.username');
  set('label[for="input-ar-password"]', 'page.settings.password');
  set('#clear-autorouter-btn', 'page.settings.clear');
  set('#usage-section h3', 'page.settings.usageTitle');
  set('#credits-section h3', 'page.settings.creditsTitle');
  // Flight tab
  set('.profile-section h3', 'page.settings.flightProfiles');
  set('.profile-section .section-hint', 'page.settings.profilesHint');
  set('label[for="profile-select"]', 'page.settings.activeProfile');
  set('#btn-new-profile', 'page.settings.new');
  set('#btn-duplicate-profile', 'page.settings.duplicate');
  set('#btn-rename-profile', 'page.settings.rename');
  set('#btn-delete-profile', 'page.settings.delete');
  // Flight defaults section
  const sections = document.querySelectorAll('#tab-flight > .section');
  if (sections[1]) {
    const h3 = sections[1].querySelector('h3');
    if (h3) h3.textContent = t('page.settings.flightDefaults');
    const hint = sections[1].querySelector('.section-hint');
    if (hint) hint.textContent = t('page.settings.flightDefaultsHint');
  }
  set('label[for="input-flight-rules"]', 'page.settings.flightRules');
  set('label[for="input-altitude"]', 'page.settings.cruiseAltitude');
  set('label[for="input-ceiling"]', 'page.settings.flightCeiling');
  set('label[for="input-speed"]', 'page.settings.speed');
  // Forecast models section
  if (sections[2]) {
    const h3 = sections[2].querySelector('h3');
    if (h3) h3.textContent = t('page.settings.forecastModels');
    const hint = sections[2].querySelector('.section-hint');
    if (hint) hint.textContent = t('page.settings.forecastModelsHint');
  }
  // Advisories section
  if (sections[3]) {
    const h3 = sections[3].querySelector('h3');
    if (h3) h3.textContent = t('page.settings.advisories');
    const hint = sections[3].querySelector('.section-hint');
    if (hint) hint.textContent = t('page.settings.advisoriesHint');
  }
  set('label[for="advisory-aggregation"]', 'page.settings.summaryRating');
  set('label[for="input-icing-method"]', 'page.settings.icingMethod');
  set('label[for="input-cloud-method"]', 'page.settings.cloudMethod');
  set('label[for="input-convective-method"]', 'page.settings.convectiveMethod');
  // Translate select options for flight rules
  const frSelect = document.getElementById('input-flight-rules') as HTMLSelectElement;
  if (frSelect) {
    for (const opt of frSelect.options) {
      if (opt.value === 'vfr_ifr') opt.textContent = t('page.settings.vfrIfr');
      if (opt.value === 'vfr_only') opt.textContent = t('page.settings.vfrOnly');
    }
  }
  // Translate aggregation options
  const aggSelect = document.getElementById('advisory-aggregation') as HTMLSelectElement;
  if (aggSelect) {
    for (const opt of aggSelect.options) {
      if (opt.value === 'worst') opt.textContent = t('page.settings.worstModel');
      if (opt.value === 'majority') opt.textContent = t('page.settings.majorityModel');
    }
  }
}

async function init(): Promise<void> {
  await initI18n();
  translateStaticElements();
  const user = await fetchCurrentUser();
  if (!user) {
    window.location.href = '/login.html';
    return;
  }
  initTheme();
  initInfoPopup();
  renderUserInfo(user, 'settings');

  // Tab switching
  for (const btn of document.querySelectorAll<HTMLButtonElement>('.tab-btn')) {
    btn.addEventListener('click', () => switchTab(btn.dataset.tab!));
  }

  // Load profiles, preferences, advisory catalog, and model catalog in parallel
  const [profilesResult, prefs, catalogResult, modelCatalog] = await Promise.all([
    fetchProfiles().catch(err => {
      showStatus(t('settings.failedLoad', { what: 'profiles', error: String(err) }), true);
      return [] as ProfileResponse[];
    }),
    fetchPreferences().catch(err => {
      showStatus(t('settings.failedLoad', { what: 'preferences', error: String(err) }), true);
      return null;
    }),
    fetchAdvisoryCatalog().catch(err => {
      showStatus(t('settings.failedLoad', { what: 'advisory catalog', error: String(err) }), true);
      return [] as AdvisoryCatalogEntry[];
    }),
    fetchModelCatalog().catch(err => {
      showStatus(t('settings.failedLoad', { what: 'model catalog', error: String(err) }), true);
      return [];
    }),
  ]);

  catalog = catalogResult;
  profiles = profilesResult;
  initModelCatalog(modelCatalog);

  // Set active profile to default
  const defaultProfile = profiles.find(p => p.is_default) || profiles[0];
  if (defaultProfile) {
    activeProfileId = defaultProfile.id;
    renderProfileSelector();
    populateProfileForm(defaultProfile);
  } else {
    renderModelCheckboxes();
    renderAdvisorySettings(catalog, { enabled: null, params: null });
  }

  // Populate account-level settings
  if (prefs) {
    populateAccountForm(prefs);
  }

  // Load usage and credits (non-blocking)
  fetchUsageSummary()
    .then(renderUsage)
    .catch(() => { /* usage section stays hidden */ });
  fetchCostSummary()
    .then(renderCosts)
    .catch(() => { /* costs section stays hidden */ });

  // Profile controls
  const profileSelect = document.getElementById('profile-select') as HTMLSelectElement;
  profileSelect?.addEventListener('change', () => {
    const id = parseInt(profileSelect.value, 10);
    if (!isNaN(id)) switchProfile(id);
  });
  document.getElementById('btn-new-profile')?.addEventListener('click', handleNewProfile);
  document.getElementById('btn-duplicate-profile')?.addEventListener('click', handleDuplicateProfile);
  document.getElementById('btn-rename-profile')?.addEventListener('click', handleRenameProfile);
  document.getElementById('btn-delete-profile')?.addEventListener('click', handleDeleteProfile);

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
      showStatus(t('settings.credentialsCleared'));
    } catch (err) {
      showStatus(t('settings.failedClearCreds', { error: String(err) }), true);
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

function populateAccountForm(prefs: PreferencesResponse): void {
  updateAutorouterStatus(prefs.has_autorouter_creds);

  // Locale picker — reflect server-stored preference
  const localeSelect = document.getElementById('input-locale') as HTMLSelectElement;
  if (localeSelect) {
    localeSelect.value = prefs.locale || getLocale();
  }
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
  const knownKeys = new Set(CATEGORY_KEYS.map(([k]) => k));
  const allCategories: [string, string][] = CATEGORY_KEYS.map(([k, tKey]) => [k, t(tKey)]);
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
      html += `<button class="metric-info-btn advisory-settings-info-btn" data-advisory-id="${entry.id}" title="Advisory details" aria-label="Advisory details">i</button>`;
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

  // Info popup for advisory details
  const catalogMap = new Map(entries.map(e => [e.id, e]));
  for (const btn of container.querySelectorAll<HTMLButtonElement>('.advisory-settings-info-btn')) {
    btn.addEventListener('click', (e) => {
      e.preventDefault();
      const advId = btn.dataset.advisoryId!;
      const entry = catalogMap.get(advId);
      if (!entry) return;
      const currentParams = collectParamsFor(container, advId);
      showPopupContent(renderAdvisoryPopup(entry, currentParams));
    });
  }
}

/** Read current parameter values from the settings form for a single advisory. */
function collectParamsFor(container: HTMLElement, advisoryId: string): Record<string, number> {
  const params: Record<string, number> = {};
  const prefix = `${advisoryId}:`;
  for (const input of container.querySelectorAll<HTMLInputElement>('input[data-advisory-param]')) {
    const key = input.dataset.advisoryParam!;
    if (key.startsWith(prefix)) {
      const paramKey = key.slice(prefix.length);
      const val = parseFloat(input.value);
      if (!isNaN(val)) params[paramKey] = val;
    }
  }
  return params;
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

  const aggSelect = document.getElementById('advisory-aggregation') as HTMLSelectElement;
  const aggregation = (aggSelect?.value === 'majority' ? 'majority' : 'worst') as 'worst' | 'majority';

  return { enabled, params, aggregation };
}

// --- Save ---

async function handleSave(): Promise<void> {
  // Collect profile settings from the form
  const altitude = parseInt((document.getElementById('input-altitude') as HTMLInputElement).value, 10);
  const ceiling = parseInt((document.getElementById('input-ceiling') as HTMLInputElement).value, 10);
  const speedRaw = (document.getElementById('input-speed') as HTMLInputElement).value.trim();
  const speed = speedRaw ? parseInt(speedRaw, 10) : null;

  const models: string[] = [];
  for (const m of allModelKeys()) {
    const cb = document.getElementById(`model-${m}`) as HTMLInputElement;
    if (cb?.checked) models.push(m);
  }
  if (models.length === 0) {
    showStatus(t('settings.selectOneModel'), true);
    return;
  }

  // Collect advisory models
  const advisoryModels: string[] = [];
  const advContainer = document.getElementById('advisory-model-checkboxes');
  if (advContainer) {
    for (const cb of advContainer.querySelectorAll<HTMLInputElement>('input[data-adv-model]')) {
      if (cb.checked) advisoryModels.push(cb.dataset.advModel!);
    }
  }

  const flightRules = (document.getElementById('input-flight-rules') as HTMLSelectElement)?.value || 'vfr_ifr';
  const grametEnabled = (document.getElementById('toggle-gramet') as HTMLInputElement)?.checked ?? true;
  const llmDigestEnabled = (document.getElementById('toggle-llm-digest') as HTMLInputElement)?.checked ?? true;
  const icingSeverityEnhance = (document.getElementById('toggle-icing-enhance') as HTMLInputElement)?.checked ?? false;
  const icingMethod = (document.getElementById('input-icing-method') as HTMLSelectElement)?.value || 'ogimet_dd';
  const cloudMethod = (document.getElementById('input-cloud-method') as HTMLSelectElement)?.value || 'dd';
  const convectiveMethod = (document.getElementById('input-convective-method') as HTMLSelectElement)?.value || 'thermo';
  const advisories = collectAdvisoryPrefs();

  // Build profile settings
  const profileSettings: Partial<ProfileSettings> = {
    cruise_altitude_ft: isNaN(altitude) ? null : altitude,
    flight_ceiling_ft: isNaN(ceiling) ? null : ceiling,
    speed_kt: speed != null && !isNaN(speed) ? speed : null,
    models,
    advisory_models: advisoryModels.length > 0 ? advisoryModels : null,
    flight_rules: flightRules,
    gramet_enabled: grametEnabled,
    llm_digest_enabled: llmDigestEnabled,
    icing_severity_enhance: icingSeverityEnhance,
    icing_method: icingMethod,
    cloud_method: cloudMethod,
    convective_method: convectiveMethod,
    advisories,
  };

  // Account-level settings
  const arUsername = (document.getElementById('input-ar-username') as HTMLInputElement).value.trim();
  const arPassword = (document.getElementById('input-ar-password') as HTMLInputElement).value.trim();
  const selectedLocale = (document.getElementById('input-locale') as HTMLSelectElement)?.value || 'en';

  try {
    // Save profile settings
    if (activeProfileId) {
      const updated = await updateProfile(activeProfileId, { settings: profileSettings });
      const idx = profiles.findIndex(p => p.id === activeProfileId);
      if (idx >= 0) profiles[idx] = updated;
    }

    // Save account-level preferences (locale + autorouter creds)
    const accountUpdate: import('./adapters/preferences-adapter').PreferencesUpdate = {
      locale: selectedLocale,
    };
    if (arUsername) accountUpdate.autorouter_username = arUsername;
    if (arPassword) accountUpdate.autorouter_password = arPassword;

    const result = await savePreferences(accountUpdate);
    updateAutorouterStatus(result.has_autorouter_creds);
    if (arPassword) {
      (document.getElementById('input-ar-password') as HTMLInputElement).value = '';
    }

    // Apply locale change to UI (triggers reload of translation strings)
    if (selectedLocale !== getLocale()) {
      await setLocale(selectedLocale as any);
      // Reload page to apply translations to all static HTML
      window.location.reload();
      return;
    }

    showStatus(t('settings.saved'));
  } catch (err) {
    showStatus(t('settings.failedSave', { error: String(err) }), true);
  }
}

// --- Autorouter status ---

function updateAutorouterStatus(hasCreds: boolean): void {
  const badge = document.getElementById('ar-status-badge');
  if (!badge) return;
  if (hasCreds) {
    badge.textContent = t('settings.configured');
    badge.className = 'badge badge-green';
  } else {
    badge.textContent = t('settings.notSet');
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
  el.className = isError ? 'status-error visible' : 'status-success visible';
  if (!isError) {
    setTimeout(() => { el.classList.remove('visible'); }, STATUS_DISMISS_MS);
  }
}

// --- Credits rendering ---

function renderCosts(costs: CostSummary): void {
  const section = document.getElementById('credits-section');
  if (!section) return;
  section.classList.remove('hidden-section');

  // Cost summary
  const balanceEl = document.getElementById('credits-balance');
  if (balanceEl) {
    balanceEl.innerHTML = `
      <div class="credits-summary">
        <span class="muted credits-detail">
          ${t('settings.costThisWeek', { cost: '$' + costs.cost_this_week_usd.toFixed(2) })}
          &middot; ${t('settings.costThisMonth', { cost: '$' + costs.cost_this_month_usd.toFixed(2) })}
          &middot; ${t('settings.costTotal', { cost: '$' + costs.total_cost_usd.toFixed(2) })}
          (${costs.total_briefings} briefings)
        </span>
      </div>`;
  }

  // Recent transactions
  const txEl = document.getElementById('credits-transactions');
  if (txEl && costs.recent_transactions.length > 0) {
    const rows = costs.recent_transactions
      .filter(tx => tx.cost_usd > 0)
      .slice(0, 10)
      .map(tx => {
        const ts = new Date(tx.timestamp).toLocaleDateString(getDateLocale(), {
          day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit',
        });
        return `<tr>
          <td class="muted">${ts}</td>
          <td>$${tx.cost_usd.toFixed(4)}</td>
          <td class="muted">${tx.category}</td>
        </tr>`;
      }).join('');
    txEl.innerHTML = `
      <h4 class="subsection-heading">${t('settings.recentTransactions')}</h4>
      <table class="credits-table">
        <thead><tr><th>${t('settings.txDate')}</th><th>${t('settings.txCost')}</th><th>${t('settings.txType')}</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>`;
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
      renderUsageBar(t('usage.briefings'), usage.today.briefings, null),
      renderUsageBar(t('usage.openMeteo'), usage.today.open_meteo.used, usage.today.open_meteo.limit),
      renderUsageBar(t('usage.gramet'), usage.today.gramet.used, usage.today.gramet.limit),
      renderUsageBar(t('usage.aiDigest'), usage.today.llm_digest.used, usage.today.llm_digest.limit),
    ].join('');
  }

  const monthSummary = document.getElementById('usage-month-summary');
  if (monthSummary) {
    const KILO = 1000;
    const tokens = usage.month.total_tokens >= KILO
      ? `~${Math.round(usage.month.total_tokens / KILO)}K tokens`
      : `${usage.month.total_tokens} tokens`;
    monthSummary.textContent = t('usage.monthSummary', {
      briefings: String(usage.month.briefings),
      gramet: String(usage.month.gramet),
      digests: String(usage.month.llm_digest),
      tokens,
    });
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
