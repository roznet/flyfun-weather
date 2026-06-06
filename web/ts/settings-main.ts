/** Settings page entry point — tabbed preferences with profile-based advisory configuration. */

import { fetchCurrentUser, deleteAccount } from './adapters/auth-adapter';
import { resetMyOnboarding } from './adapters/admin-adapter';
import { redirectToLogin, renderUserInfo, escapeHtml, STATUS_DISMISS_MS, initModelCatalog, allModelKeys, defaultModelKeys, modelLabel } from './utils';
import {
  fetchPreferences,
  savePreferences,
  clearAutorouterCreds,
  unlinkAutorouter,
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
  resetProfileToTemplate,
  type ProfileResponse,
  type ProfileSettings,
} from './adapters/profiles-adapter';
import {
  fetchAircraft,
  createAircraft,
  updateAircraft,
  deleteAircraft,
  searchAircraftTypes,
  type AircraftResponse,
  type AircraftType,
} from './adapters/aircraft-adapter';
import {
  fetchTokens,
  createToken,
  revokeToken,
  type TokenListItem,
} from './adapters/tokens-adapter';
import { initTheme } from './theme';
import { initI18n, t, setLocale, getLocale, getDateLocale } from './i18n/i18n';
import { setUnitsPreference } from './units';
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

/** Advisory id of the experimental front advisory (mirrors FRONTS_ADVISORY_ID in the backend). */
const FRONTS_ADVISORY_ID = 'fronts';

let catalog: AdvisoryCatalogEntry[] = [];
let profiles: ProfileResponse[] = [];
let activeProfileId: number | null = null;
let aircraftList: AircraftResponse[] = [];
let editingAircraftId: number | null = null;
let autorouterMode: 'oauth' | 'password' = 'oauth';

/** Parse the persisted cloud_method ('soft_nwp', 'square_dd', 'natural_nwp', 'dd', 'nwp', etc.) into source+style. */
function parseCloudMethod(value: string): { source: 'dd' | 'nwp'; style: 'soft' | 'natural' | 'square' } {
  const lower = (value ?? '').toLowerCase();
  // Style-prefixed forms: soft_nwp, soft_dd, square_nwp, square_dd, natural_nwp, natural_dd
  if (lower.startsWith('soft_')) {
    return { style: 'soft', source: lower === 'soft_nwp' ? 'nwp' : 'dd' };
  }
  if (lower.startsWith('square_')) {
    return { style: 'square', source: lower === 'square_nwp' ? 'nwp' : 'dd' };
  }
  if (lower.startsWith('natural_')) {
    return { style: 'natural', source: lower === 'natural_nwp' ? 'nwp' : 'dd' };
  }
  // Legacy bare-source forms ('dd' / 'nwp') used to mean the old hatched
  // layer style. Map them to natural (the replacement rendering for that slot).
  if (lower === 'nwp') return { style: 'natural', source: 'nwp' };
  if (lower === 'dd') return { style: 'natural', source: 'dd' };
  // Unknown → fall back to recommended.
  return { style: 'square', source: 'nwp' };
}

/** Compose source+style back into a cloud_method string for persistence.
 *  Uses `natural_<source>` for the natural style — avoids the bare `dd`/`nwp`
 *  form that the user_migration in api/user_migrations.py rewrites to square_nwp. */
function composeCloudMethod(source: string, style: string): string {
  return `${style}_${source}`;                   // 'soft_nwp', 'square_dd', 'natural_nwp', etc.
}

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
  const activeProfile = profiles.find(p => p.id === activeProfileId);
  const deleteBtn = document.getElementById('btn-delete-profile') as HTMLButtonElement;
  if (deleteBtn) {
    deleteBtn.disabled = !!activeProfile?.is_default;
  }

  // Show/hide reset-to-template button
  const resetBtn = document.getElementById('btn-reset-profile') as HTMLButtonElement;
  if (resetBtn) {
    resetBtn.style.display = activeProfile?.system_template_key ? '' : 'none';
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
  const autoFrontToggle = document.getElementById('toggle-auto-front-detection') as HTMLInputElement;
  if (autoFrontToggle) autoFrontToggle.checked = s.auto_front_detection ?? false;
  const computeAlternatesToggle = document.getElementById('toggle-compute-alternates') as HTMLInputElement;
  if (computeAlternatesToggle) computeAlternatesToggle.checked = s.compute_alternates ?? false;

  // Icing method selector
  const icingMethodSelect = document.getElementById('input-icing-method') as HTMLSelectElement;
  if (icingMethodSelect) icingMethodSelect.value = s.icing_method ?? 'ogimet_nwp';

  // Cloud source + style selectors (composed into s.cloud_method like 'soft_nwp', 'square_dd', etc.).
  const cloudSourceSelect = document.getElementById('input-cloud-source') as HTMLSelectElement;
  const cloudStyleSelect = document.getElementById('input-cloud-style') as HTMLSelectElement;
  const { source: cloudSource, style: cloudStyle } = parseCloudMethod(s.cloud_method ?? 'square_nwp');
  if (cloudSourceSelect) cloudSourceSelect.value = cloudSource;
  if (cloudStyleSelect) cloudStyleSelect.value = cloudStyle;

  // Convective method selector
  const convectiveMethodSelect = document.getElementById('input-convective-method') as HTMLSelectElement;
  if (convectiveMethodSelect) convectiveMethodSelect.value = s.convective_method ?? 'nwp';

  // Digest guidance selector
  const guidanceSelect = document.getElementById('input-digest-guidance') as HTMLSelectElement;
  if (guidanceSelect) guidanceSelect.value = s.digest_guidance ?? 'balanced';

  // Advisories
  const advPrefs: AdvisoryPreferences = s.advisories ?? { enabled: null, params: null };
  const aggSelect = document.getElementById('advisory-aggregation') as HTMLSelectElement;
  if (aggSelect) aggSelect.value = advPrefs.aggregation ?? 'majority';
  renderAdvisorySettings(catalog, advPrefs, s.auto_front_detection ?? false);
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

async function handleResetProfile(): Promise<void> {
  if (!activeProfileId) return;
  const current = profiles.find(p => p.id === activeProfileId);
  if (!current?.system_template_key) return;
  if (!confirm(t('settings.resetConfirm', { name: current.name }))) return;

  try {
    const updated = await resetProfileToTemplate(activeProfileId);
    const idx = profiles.findIndex(p => p.id === activeProfileId);
    if (idx >= 0) profiles[idx] = updated;
    populateProfileForm(updated);
    showStatus(t('settings.profileReset'));
  } catch (err) {
    showStatus(`Failed to reset profile: ${err}`, true);
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
  set('#autorouter-section h3', 'page.settings.autorouterTitle');
  set('#ar-link-btn', 'page.settings.arLink');
  set('#ar-unlink-btn', 'page.settings.arUnlink');
  set('label[for="input-ar-username"]', 'page.settings.username');
  set('label[for="input-ar-password"]', 'page.settings.password');
  set('#clear-autorouter-btn', 'page.settings.clear');
  set('#usage-section h3', 'page.settings.usageTitle');
  // Danger zone
  set('.danger-zone h3', 'page.settings.deleteAccountTitle');
  set('.danger-zone .section-hint', 'page.settings.deleteAccountHint');
  set('#btn-delete-account', 'page.settings.deleteAccount');
  // Flight tab
  set('.profile-section h3', 'page.settings.flightProfiles');
  set('.profile-section .section-hint', 'page.settings.profilesHint');
  set('label[for="profile-select"]', 'page.settings.activeProfile');
  set('#btn-new-profile', 'page.settings.new');
  set('#btn-duplicate-profile', 'page.settings.duplicate');
  set('#btn-rename-profile', 'page.settings.rename');
  set('#btn-delete-profile', 'page.settings.delete');
  set('#btn-reset-profile', 'settings.resetToTemplate');
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
  set('label[for="input-cloud-source"]', 'page.settings.cloudSource');
  set('label[for="input-cloud-style"]', 'page.settings.cloudStyle');
  set('label[for="input-convective-method"]', 'page.settings.convectiveMethod');
  // Translate select options for flight rules
  const frSelect = document.getElementById('input-flight-rules') as HTMLSelectElement;
  if (frSelect) {
    for (const opt of frSelect.options) {
      if (opt.value === 'vfr_ifr') opt.textContent = t('page.settings.vfrIfr');
      if (opt.value === 'vfr_only') opt.textContent = t('page.settings.vfrOnly');
    }
  }
  // Translate digest guidance label (preserves info button)
  const guidanceLabel = document.querySelector('label[for="input-digest-guidance"]');
  if (guidanceLabel) {
    const btn = guidanceLabel.querySelector('button');
    const span = document.createTextNode(t('page.settings.digestGuidance') + ' ');
    guidanceLabel.textContent = '';
    guidanceLabel.appendChild(span);
    if (btn) guidanceLabel.appendChild(btn);
  }
  // Translate digest guidance options
  const guidanceSelect = document.getElementById('input-digest-guidance') as HTMLSelectElement;
  if (guidanceSelect) {
    for (const opt of guidanceSelect.options) {
      if (opt.value === 'conservative') opt.textContent = t('page.settings.guidanceConservative');
      if (opt.value === 'balanced') opt.textContent = t('page.settings.guidanceBalanced');
      if (opt.value === 'tolerant') opt.textContent = t('page.settings.guidanceTolerant');
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
    redirectToLogin();
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

  // Show success message if returning from OAuth linking
  const params = new URLSearchParams(window.location.search);
  if (params.get('autorouter') === 'linked') {
    showStatus(t('settings.arLinkedSuccess'));
    // Clean up URL
    window.history.replaceState({}, '', window.location.pathname);
    // Switch to account tab to show the connected status
    switchTab('services');
  }

  // Load usage and tokens (non-blocking).
  // Cost summary is intentionally not loaded here — a new cost section is
  // planned and the previous Credits panel was removed.
  fetchUsageSummary()
    .then(renderUsage)
    .catch(() => { /* usage section stays hidden */ });
  initTokenSection();

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
  document.getElementById('btn-reset-profile')?.addEventListener('click', handleResetProfile);

  // Auto Front Detection master ⇄ front advisory row: re-render the advisory
  // list when the master flips so the front advisory enables/defaults live
  // (issue #196, model B). Re-render from the *current* form state to preserve
  // other unsaved edits; drop the fronts entry so it re-defaults to the master.
  document.getElementById('toggle-auto-front-detection')?.addEventListener('change', (e) => {
    const masterOn = (e.target as HTMLInputElement).checked;
    const current = collectAdvisoryPrefs();
    if (current.enabled) delete current.enabled[FRONTS_ADVISORY_ID];
    renderAdvisorySettings(catalog, current, masterOn);
  });

  // Save button
  const form = document.getElementById('settings-form') as HTMLFormElement;
  form?.addEventListener('submit', async (e) => {
    e.preventDefault();
    await handleSave();
  });

  // Digest guidance info popup
  const guidanceInfoBtn = document.querySelector('.digest-guidance-info-btn');
  guidanceInfoBtn?.addEventListener('click', async (e) => {
    e.preventDefault();
    const guidanceSelect = document.getElementById('input-digest-guidance') as HTMLSelectElement;
    const key = guidanceSelect?.value || 'balanced';
    try {
      const { fetchDigestGuidanceText } = await import('./adapters/profiles-adapter');
      const text = await fetchDigestGuidanceText(key);
      showPopupContent(`
        <h3 style="margin-top:0">AI Assessment Guidance: ${escapeHtml(key.charAt(0).toUpperCase() + key.slice(1))}</h3>
        <pre style="white-space:pre-wrap; font-size:0.9em; line-height:1.5; max-height:60vh; overflow-y:auto;">${escapeHtml(text)}</pre>
      `);
    } catch (err) {
      showPopupContent(`<p>Failed to load guidance text: ${escapeHtml(String(err))}</p>`);
    }
  });

  // Clear autorouter credentials (password mode)
  const clearBtn = document.getElementById('clear-autorouter-btn');
  clearBtn?.addEventListener('click', async () => {
    try {
      await clearAutorouterCreds();
      (document.getElementById('input-ar-username') as HTMLInputElement).value = '';
      (document.getElementById('input-ar-password') as HTMLInputElement).value = '';
      updateAutorouterStatus(false, 'password');
      showStatus(t('settings.credentialsCleared'));
    } catch (err) {
      showStatus(t('settings.failedClearCreds', { error: String(err) }), true);
    }
  });

  // Unlink autorouter (OAuth mode)
  const unlinkBtn = document.getElementById('ar-unlink-btn');
  unlinkBtn?.addEventListener('click', async () => {
    try {
      await unlinkAutorouter();
      updateAutorouterStatus(false, 'oauth');
      showStatus(t('settings.arDisconnected'));
    } catch (err) {
      showStatus(t('settings.failedClearCreds', { error: String(err) }), true);
    }
  });

  // Aircraft tab
  initAircraftTab();

  // Delete account
  const deleteAccountBtn = document.getElementById('btn-delete-account');
  deleteAccountBtn?.addEventListener('click', async () => {
    if (!confirm(t('settings.deleteAccountConfirm'))) return;
    if (!confirm(t('settings.deleteAccountDoubleConfirm'))) return;
    try {
      await deleteAccount();
    } catch (err) {
      showStatus(t('settings.deleteAccountFailed', { error: String(err) }), true);
    }
  });

  // Admin-only tools: replay first-time experience.
  if (user.is_admin) {
    const adminSection = document.getElementById('admin-tools-section');
    if (adminSection) adminSection.style.display = '';
    document.getElementById('btn-reset-onboarding')?.addEventListener('click', async () => {
      try {
        await resetMyOnboarding();
        showStatus('Welcome wizard reset — open Flights to replay it.');
      } catch (err) {
        showStatus(`Failed to reset onboarding: ${String(err)}`, true);
      }
    });
    document.getElementById('btn-reset-tour-offer')?.addEventListener('click', () => {
      try {
        localStorage.removeItem('wb_tour_offered');
        showStatus('Tour offer reset — open a briefing to see the banner again.');
      } catch (err) {
        showStatus(`Failed to clear tour flag: ${String(err)}`, true);
      }
    });
    document.getElementById('btn-reset-first-time')?.addEventListener('click', async () => {
      try {
        await resetMyOnboarding();
        localStorage.removeItem('wb_tour_offered');
        showStatus('First-time experience reset — wizard on Flights, banner on next briefing.');
      } catch (err) {
        showStatus(`Failed to reset first-time flags: ${String(err)}`, true);
      }
    });
  }
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
  // Show the correct autorouter controls based on mode
  autorouterMode = prefs.autorouter_mode;
  const oauthControls = document.getElementById('ar-oauth-controls');
  const passwordControls = document.getElementById('ar-password-controls');
  if (prefs.autorouter_mode === 'oauth') {
    if (oauthControls) oauthControls.style.display = '';
    if (passwordControls) passwordControls.style.display = 'none';
  } else {
    if (oauthControls) oauthControls.style.display = 'none';
    if (passwordControls) passwordControls.style.display = '';
  }
  updateAutorouterStatus(prefs.has_autorouter_creds, prefs.autorouter_mode);

  // Locale picker — reflect server-stored preference
  const localeSelect = document.getElementById('input-locale') as HTMLSelectElement;
  if (localeSelect) {
    localeSelect.value = prefs.locale || getLocale();
  }

  // Units region picker — reflect server-stored preference (auto/europe/us)
  const unitsRegionSelect = document.getElementById('input-units-region') as HTMLSelectElement;
  if (unitsRegionSelect) {
    const ur = prefs.units_region;
    unitsRegionSelect.value = ur === 'us' || ur === 'europe' ? ur : 'auto';
  }

  // Account-level optional services
  const synopticToggle = document.getElementById('toggle-synoptic-forecast-map') as HTMLInputElement;
  if (synopticToggle) {
    synopticToggle.checked = prefs.synoptic_forecast_map_enabled ?? false;
  }
  const deferToggle = document.getElementById('toggle-defer-model-update') as HTMLInputElement;
  if (deferToggle) {
    deferToggle.checked = prefs.defer_email_for_model_update ?? false;
  }
}

// --- Advisory settings rendering ---

function renderAdvisorySettings(
  entries: AdvisoryCatalogEntry[],
  userAdvisories: AdvisoryPreferences,
  autoFrontDetection: boolean = false,
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
      // The experimental front advisory is gated by the Auto Front Detection
      // master (issue #196, model B): the master generates the data + overlays,
      // this toggle independently controls the GREEN/AMBER/RED grade. Default it
      // *on* when the master is on (so it is discoverable), but let the pilot opt
      // out. When the master is off there is no artifact, so the advisory can
      // never surface — disable the row and explain the dependency.
      const isFronts = entry.id === FRONTS_ADVISORY_ID;
      const defaultEnabled = isFronts ? autoFrontDetection : entry.default_enabled;
      const isEnabled = enabledMap[entry.id] ?? defaultEnabled;
      const frontsDisabled = isFronts && !autoFrontDetection;
      const userParams = paramsMap[entry.id] ?? {};

      html += `<div class="advisory-setting">`;
      html += `<div class="advisory-header">`;
      html += `<label class="checkbox-label">`;
      html += `<input type="checkbox" data-advisory-id="${entry.id}" ${isEnabled ? 'checked' : ''}${frontsDisabled ? ' disabled' : ''}>`;
      html += ` ${entry.name}`;
      html += `<span class="advisory-desc">${entry.short_description}</span>`;
      html += `<button class="metric-info-btn advisory-settings-info-btn" data-advisory-id="${entry.id}" title="Advisory details" aria-label="Advisory details">i</button>`;
      html += `</label>`;
      if (frontsDisabled) {
        html += `<span class="advisory-desc muted">Requires Auto Front Detection (enable it above).</span>`;
      }
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
  const autoFrontDetection = (document.getElementById('toggle-auto-front-detection') as HTMLInputElement)?.checked ?? false;
  const computeAlternates = (document.getElementById('toggle-compute-alternates') as HTMLInputElement)?.checked ?? false;
  const icingMethod = (document.getElementById('input-icing-method') as HTMLSelectElement)?.value || 'ogimet_dd';
  const cloudSourceValue = (document.getElementById('input-cloud-source') as HTMLSelectElement)?.value || 'nwp';
  const cloudStyleValue = (document.getElementById('input-cloud-style') as HTMLSelectElement)?.value || 'square';
  const cloudMethod = composeCloudMethod(cloudSourceValue, cloudStyleValue);
  const convectiveMethod = (document.getElementById('input-convective-method') as HTMLSelectElement)?.value || 'thermo';
  const digestGuidance = (document.getElementById('input-digest-guidance') as HTMLSelectElement)?.value || 'balanced';
  const advisories = collectAdvisoryPrefs();
  // The front advisory enable is only meaningful when the master is on; when it
  // is off the checkbox is disabled and reads false. Don't persist that stale
  // false (issue #196, model B) — drop it so the advisory re-defaults to on if
  // the master is later enabled.
  if (!autoFrontDetection && advisories.enabled) {
    delete advisories.enabled[FRONTS_ADVISORY_ID];
  }

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
    auto_front_detection: autoFrontDetection,
    compute_alternates: computeAlternates,
    icing_method: icingMethod,
    cloud_method: cloudMethod,
    convective_method: convectiveMethod,
    digest_guidance: digestGuidance,
    advisories,
  };

  // Account-level settings
  const selectedLocale = (document.getElementById('input-locale') as HTMLSelectElement)?.value || 'en';
  const unitsRegionVal = (document.getElementById('input-units-region') as HTMLSelectElement)?.value;
  const selectedUnitsRegion = unitsRegionVal === 'us' || unitsRegionVal === 'europe' ? unitsRegionVal : 'auto';

  try {
    // Save profile settings
    if (activeProfileId) {
      const updated = await updateProfile(activeProfileId, { settings: profileSettings });
      const idx = profiles.findIndex(p => p.id === activeProfileId);
      if (idx >= 0) profiles[idx] = updated;
    }

    // Save account-level preferences (locale + optional services + autorouter creds in dev mode)
    const synopticEnabled = (document.getElementById('toggle-synoptic-forecast-map') as HTMLInputElement)?.checked ?? false;
    const deferModelUpdate = (document.getElementById('toggle-defer-model-update') as HTMLInputElement)?.checked ?? false;
    const accountUpdate: import('./adapters/preferences-adapter').PreferencesUpdate = {
      locale: selectedLocale,
      units_region: selectedUnitsRegion,
      synoptic_forecast_map_enabled: synopticEnabled,
      defer_email_for_model_update: deferModelUpdate,
    };
    if (autorouterMode === 'password') {
      const arUsername = (document.getElementById('input-ar-username') as HTMLInputElement)?.value.trim();
      const arPassword = (document.getElementById('input-ar-password') as HTMLInputElement)?.value.trim();
      if (arUsername) accountUpdate.autorouter_username = arUsername;
      if (arPassword) accountUpdate.autorouter_password = arPassword;
    }

    const result = await savePreferences(accountUpdate);
    setUnitsPreference(result.units_region);
    updateAutorouterStatus(result.has_autorouter_creds, autorouterMode);
    if (autorouterMode === 'password') {
      const arPwdInput = document.getElementById('input-ar-password') as HTMLInputElement;
      if (arPwdInput?.value) arPwdInput.value = '';
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

function updateAutorouterStatus(hasCreds: boolean, mode: 'oauth' | 'password' = 'oauth'): void {
  const badge = document.getElementById('ar-status-badge');
  if (!badge) return;
  if (hasCreds) {
    badge.textContent = mode === 'oauth' ? t('settings.arConnected') : t('settings.configured');
    badge.className = 'badge badge-green';
  } else {
    badge.textContent = t('settings.notSet');
    badge.className = 'badge badge-none';
  }
  if (mode === 'oauth') {
    const linkBtn = document.getElementById('ar-link-btn') as HTMLElement;
    const unlinkBtn = document.getElementById('ar-unlink-btn') as HTMLButtonElement;
    if (linkBtn) linkBtn.style.display = hasCreds ? 'none' : '';
    if (unlinkBtn) unlinkBtn.style.display = hasCreds ? 'inline-block' : 'none';
  } else {
    const clearBtn = document.getElementById('clear-autorouter-btn') as HTMLButtonElement;
    if (clearBtn) clearBtn.style.display = hasCreds ? 'inline-block' : 'none';
  }
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

// --- Aircraft tab ---

function initAircraftTab(): void {
  // Load aircraft list
  fetchAircraft()
    .then(list => {
      aircraftList = list;
      renderAircraftList();
    })
    .catch(() => { /* aircraft section stays empty */ });

  document.getElementById('btn-add-aircraft')?.addEventListener('click', () => {
    editingAircraftId = null;
    showAircraftForm('Add Aircraft');
    clearAircraftForm();
  });
  document.getElementById('btn-save-aircraft')?.addEventListener('click', saveAircraftForm);
  document.getElementById('btn-cancel-aircraft')?.addEventListener('click', hideAircraftForm);

  // ICAO type autocomplete
  const icaoInput = document.getElementById('ac-icao-type') as HTMLInputElement;
  let debounceTimer: ReturnType<typeof setTimeout> | null = null;
  icaoInput?.addEventListener('input', () => {
    if (debounceTimer) clearTimeout(debounceTimer);
    debounceTimer = setTimeout(() => {
      const q = icaoInput.value.trim();
      if (q.length >= 1) {
        searchAircraftTypes(q).then(showTypeSuggestions).catch(() => hideTypeSuggestions());
      } else {
        hideTypeSuggestions();
      }
    }, 200);
  });
  icaoInput?.addEventListener('blur', () => {
    // Delay hiding to allow click on suggestion
    setTimeout(hideTypeSuggestions, 200);
  });
}

function renderAircraftList(): void {
  const container = document.getElementById('aircraft-list');
  if (!container) return;

  if (aircraftList.length === 0) {
    container.innerHTML = '<p class="muted">No aircraft added yet.</p>';
    return;
  }

  container.innerHTML = aircraftList.map(ac => {
    const badges: string[] = [];
    if (ac.is_default) badges.push('<span class="badge badge-green">Default</span>');
    if (ac.is_ifr) badges.push('<span class="badge badge-none">IFR</span>');
    if (ac.is_fiki) badges.push('<span class="badge badge-none">FIKI</span>');

    const details: string[] = [];
    if (ac.cruise_speed_kt) details.push(`${ac.cruise_speed_kt} kt`);
    if (ac.ceiling_ft) details.push(`FL${Math.round(ac.ceiling_ft / 100)}`);

    const tailDisplay = ac.tail_number ? `<strong>${escapeHtml(ac.tail_number)}</strong> — ` : '';
    const nicknameDisplay = ac.nickname ? ` <span class="muted">(${escapeHtml(ac.nickname)})</span>` : '';

    return `
      <div class="aircraft-card" data-id="${ac.id}">
        <div class="aircraft-card-main">
          <div class="aircraft-card-info">
            ${tailDisplay}${escapeHtml(ac.type_name)}${nicknameDisplay}
            ${badges.join(' ')}
          </div>
          <div class="aircraft-card-details muted">${details.join(' · ')}</div>
        </div>
        <div class="aircraft-card-actions">
          <button type="button" class="btn btn-secondary btn-sm ac-edit-btn" data-id="${ac.id}">Edit</button>
          <button type="button" class="btn btn-danger btn-sm ac-delete-btn" data-id="${ac.id}">Delete</button>
        </div>
      </div>`;
  }).join('');

  // Bind edit/delete buttons
  for (const btn of container.querySelectorAll<HTMLButtonElement>('.ac-edit-btn')) {
    btn.addEventListener('click', () => {
      const ac = aircraftList.find(a => a.id === Number(btn.dataset.id));
      if (ac) editAircraft(ac);
    });
  }
  for (const btn of container.querySelectorAll<HTMLButtonElement>('.ac-delete-btn')) {
    btn.addEventListener('click', async () => {
      const id = Number(btn.dataset.id);
      const ac = aircraftList.find(a => a.id === id);
      if (!ac || !confirm(`Delete ${ac.tail_number || ac.type_name}?`)) return;
      try {
        await deleteAircraft(id);
        aircraftList = aircraftList.filter(a => a.id !== id);
        renderAircraftList();
        showStatus('Aircraft deleted');
      } catch (err) {
        showStatus(`Failed to delete: ${err}`, true);
      }
    });
  }
}

function editAircraft(ac: AircraftResponse): void {
  editingAircraftId = ac.id;
  showAircraftForm('Edit Aircraft');

  (document.getElementById('ac-icao-type') as HTMLInputElement).value = ac.icao_type;
  (document.getElementById('ac-tail') as HTMLInputElement).value = ac.tail_number || '';
  (document.getElementById('ac-nickname') as HTMLInputElement).value = ac.nickname || '';
  (document.getElementById('ac-speed') as HTMLInputElement).value = ac.cruise_speed_kt?.toString() || '';
  (document.getElementById('ac-ceiling') as HTMLInputElement).value = ac.ceiling_ft?.toString() || '';
  (document.getElementById('ac-ifr') as HTMLInputElement).checked = ac.is_ifr;
  (document.getElementById('ac-fiki') as HTMLInputElement).checked = ac.is_fiki;
  (document.getElementById('ac-default') as HTMLInputElement).checked = ac.is_default;

  const label = document.getElementById('ac-type-label');
  if (label) label.textContent = ac.type_name;
}

function showAircraftForm(title: string): void {
  const section = document.getElementById('aircraft-form-section');
  const titleEl = document.getElementById('aircraft-form-title');
  if (section) section.style.display = '';
  if (titleEl) titleEl.textContent = title;
}

function hideAircraftForm(): void {
  const section = document.getElementById('aircraft-form-section');
  if (section) section.style.display = 'none';
  editingAircraftId = null;
}

function clearAircraftForm(): void {
  (document.getElementById('ac-icao-type') as HTMLInputElement).value = '';
  (document.getElementById('ac-tail') as HTMLInputElement).value = '';
  (document.getElementById('ac-nickname') as HTMLInputElement).value = '';
  (document.getElementById('ac-speed') as HTMLInputElement).value = '';
  (document.getElementById('ac-ceiling') as HTMLInputElement).value = '';
  (document.getElementById('ac-ifr') as HTMLInputElement).checked = false;
  (document.getElementById('ac-fiki') as HTMLInputElement).checked = false;
  (document.getElementById('ac-default') as HTMLInputElement).checked = false;
  const label = document.getElementById('ac-type-label');
  if (label) label.innerHTML = '&nbsp;';
}

async function saveAircraftForm(): Promise<void> {
  const icaoType = (document.getElementById('ac-icao-type') as HTMLInputElement).value.trim().toUpperCase();
  if (!icaoType) {
    showStatus('ICAO type is required', true);
    return;
  }

  const data = {
    icao_type: icaoType,
    tail_number: (document.getElementById('ac-tail') as HTMLInputElement).value.trim() || null,
    nickname: (document.getElementById('ac-nickname') as HTMLInputElement).value.trim() || null,
    cruise_speed_kt: parseInt((document.getElementById('ac-speed') as HTMLInputElement).value) || null,
    ceiling_ft: parseInt((document.getElementById('ac-ceiling') as HTMLInputElement).value) || null,
    is_ifr: (document.getElementById('ac-ifr') as HTMLInputElement).checked,
    is_fiki: (document.getElementById('ac-fiki') as HTMLInputElement).checked,
    is_default: (document.getElementById('ac-default') as HTMLInputElement).checked,
  };

  try {
    if (editingAircraftId) {
      const updated = await updateAircraft(editingAircraftId, data);
      aircraftList = aircraftList.map(a => a.id === updated.id ? updated : a);
      // If this became default, clear default from others
      if (updated.is_default) {
        aircraftList = aircraftList.map(a => a.id === updated.id ? a : { ...a, is_default: false });
      }
      showStatus('Aircraft updated');
    } else {
      const created = await createAircraft(data);
      if (created.is_default) {
        aircraftList = aircraftList.map(a => ({ ...a, is_default: false }));
      }
      aircraftList.push(created);
      showStatus('Aircraft added');
    }
    renderAircraftList();
    hideAircraftForm();
  } catch (err) {
    showStatus(`Failed to save: ${err}`, true);
  }
}

function showTypeSuggestions(types: AircraftType[]): void {
  const dropdown = document.getElementById('ac-type-suggestions');
  if (!dropdown || types.length === 0) {
    hideTypeSuggestions();
    return;
  }

  dropdown.innerHTML = types.slice(0, 10).map(t =>
    `<div class="autocomplete-item" data-icao="${escapeHtml(t.icao)}">
      <strong>${escapeHtml(t.icao)}</strong> — ${escapeHtml(t.manufacturer)} ${escapeHtml(t.model)}
      ${t.category ? `<span class="muted">(${escapeHtml(t.category)})</span>` : ''}
    </div>`
  ).join('');
  dropdown.style.display = 'block';

  for (const item of dropdown.querySelectorAll<HTMLElement>('.autocomplete-item')) {
    item.addEventListener('mousedown', (e) => {
      e.preventDefault(); // prevent blur
      const icao = item.dataset.icao || '';
      (document.getElementById('ac-icao-type') as HTMLInputElement).value = icao;
      const type = types.find(t => t.icao === icao);
      const label = document.getElementById('ac-type-label');
      if (label && type) label.textContent = `${type.manufacturer} ${type.model}`;
      hideTypeSuggestions();
    });
  }
}

function hideTypeSuggestions(): void {
  const dropdown = document.getElementById('ac-type-suggestions');
  if (dropdown) dropdown.style.display = 'none';
}

// --- MCP token management ---

function initTokenSection(): void {
  refreshTokenList();

  document.getElementById('btn-create-token')?.addEventListener('click', async () => {
    const nameInput = document.getElementById('mcp-token-name') as HTMLInputElement;
    const name = nameInput.value.trim();
    if (!name) {
      showStatus('Enter a name for the token', true);
      return;
    }

    const revealEl = document.getElementById('mcp-token-reveal')!;
    const valueEl = document.getElementById('mcp-token-value')!;
    revealEl.style.display = 'none';

    try {
      const result = await createToken(name);
      nameInput.value = '';
      valueEl.textContent = result.token;
      revealEl.style.display = '';

      refreshTokenList();
    } catch (err) {
      showStatus(`${err}`, true);
    }
  });

  document.getElementById('btn-copy-token')?.addEventListener('click', () => {
    const valueEl = document.getElementById('mcp-token-value')!;
    navigator.clipboard.writeText(valueEl.textContent || '').then(() => {
      showStatus('Token copied to clipboard');
    });
  });
}

async function refreshTokenList(): Promise<void> {
  const container = document.getElementById('mcp-token-list');
  if (!container) return;

  const tokens = await fetchTokens();
  if (tokens.length === 0) {
    container.innerHTML = '<p class="muted">No tokens yet.</p>';
    return;
  }

  const locale = getDateLocale();
  const rows = tokens.map(tok => {
    const created = new Date(tok.created_at).toLocaleDateString(locale, {
      day: 'numeric', month: 'short', year: 'numeric',
    });
    const lastUsed = tok.last_used_at
      ? new Date(tok.last_used_at).toLocaleDateString(locale, {
          day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit',
        })
      : 'Never';
    return `<tr>
      <td>${escapeHtml(tok.name)}</td>
      <td class="muted">${created}</td>
      <td class="muted">${lastUsed}</td>
      <td><button type="button" class="btn btn-danger btn-sm tok-revoke-btn" data-id="${tok.id}" data-name="${escapeHtml(tok.name)}">Revoke</button></td>
    </tr>`;
  }).join('');

  container.innerHTML = `
    <table class="token-table">
      <thead><tr><th>Name</th><th>Created</th><th>Last Used</th><th></th></tr></thead>
      <tbody>${rows}</tbody>
    </table>`;

  for (const btn of container.querySelectorAll<HTMLButtonElement>('.tok-revoke-btn')) {
    btn.addEventListener('click', async () => {
      const id = Number(btn.dataset.id);
      const name = btn.dataset.name || 'this token';
      if (!confirm(`Revoke "${name}"? Any MCP client using it will stop working.`)) return;
      try {
        await revokeToken(id);
        refreshTokenList();
        // Hide the reveal box if visible (in case they just created and are revoking)
        const revealEl = document.getElementById('mcp-token-reveal');
        if (revealEl) revealEl.style.display = 'none';
        showStatus('Token revoked');
      } catch (err) {
        showStatus(`${err}`, true);
      }
    });
  }
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}
