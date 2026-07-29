/** Settings page entry point — tabbed preferences with profile-based advisory configuration. */

import { fetchCurrentUser, deleteAccount } from './adapters/auth-adapter';
import { fetchMyDonations } from './adapters/donations-adapter';
import { resetMyOnboarding } from './adapters/admin-adapter';
import { redirectToLogin, renderUserInfo, escapeHtml, STATUS_DISMISS_MS, initModelCatalog, allModelKeys, defaultModelKeys, modelLabel } from './utils';
import {
  fetchPreferences,
  savePreferences,
  clearAutorouterCreds,
  unlinkAutorouter,
  fetchUsageSummary,
  fetchAdvisoryCatalog,
  fetchAdvisoryInterview,
  fetchModelCatalog,
  foldBriefingUpdates,
  unfoldBriefingUpdates,
  ENGINE_METHOD_DEFAULTS_FALLBACK,
  type PreferencesResponse,
  type AdvisoryPreferences,
  type AdvisoryCatalogEntry,
  type AdvisoryCategory,
  type AdvisoryParameterDef,
  type EngineMethodDefaults,
  type Interview,
  type UsageSummary,
} from './adapters/preferences-adapter';
import type { InterviewOption } from './types/advisories';
import { fetchFlights, fetchLatestPack, previewAdvisories } from './adapters/api-adapter';
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
import { initInfoPopup, showPopupContent, hideMetricInfo } from './components/info-popup';
import { renderAdvisoryPopup } from './helpers/advisory-popup';
import { buildParamDefaults, pruneAdvisoryParams, pruneEngineMethod } from './helpers/profile-sparsify';
import { clearOffered } from './tour/tour-storage';

/** Advisory id of the experimental front advisory (mirrors FRONTS_ADVISORY_ID in the backend). */
const FRONTS_ADVISORY_ID = 'fronts';

let catalog: AdvisoryCatalogEntry[] = [];
/** Category display order served by the catalog endpoint (#387). Ordering is
 *  backend-owned — there is no client-side CATEGORY_KEYS copy to drift. */
let advisoryCategories: AdvisoryCategory[] = [];
/** Declared engine grading-method defaults served by the catalog endpoint (#403).
 *  The settings form reads its icing/cloud/convective fallbacks from here (not
 *  hardcoded literals) so the UI default cannot drift from the backend, and prunes
 *  a method equal to the default on save. Seeded with the fallback so it is always
 *  concrete before the catalog loads. */
let engineDefaults: EngineMethodDefaults = ENGINE_METHOD_DEFAULTS_FALLBACK;
/** Setup-interview structure (#387, slice 3), fetched lazily on first open. */
let interview: Interview | null = null;
/** Stored interview answers for the active profile ({question_id: option_id}). */
let interviewAnswers: Record<string, string> = {};

/** Live-preview target (#387, slice 4): the user's most recent flight+pack the
 *  draft settings are previewed against. Null until discovered / if none. */
let previewTarget: { flightId: string; ts: string; label: string } | null = null;
let previewTimer: number | undefined;
/** Monotonic ticket so only the most recent preview response is rendered (#390). */
let previewSeq = 0;
/** Cached baseline (active profile's saved settings → per-advisory status).
 *  Refetched only on baseline-changing events, not on every edit (#390 round-4). */
let previewBaselineCache: Record<string, string> | null = null;
let profiles: ProfileResponse[] = [];
let activeProfileId: number | null = null;
let aircraftList: AircraftResponse[] = [];
let editingAircraftId: number | null = null;
let autorouterMode: 'oauth' | 'password' = 'oauth';

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

  // Update the "Default profile" checkbox. Exactly one profile is always the
  // default, so the box is disabled (and stays checked) on the current default —
  // you promote a different profile by checking its box instead.
  const defaultToggle = document.getElementById('toggle-default-profile') as HTMLInputElement;
  if (defaultToggle) {
    defaultToggle.checked = !!activeProfile?.is_default;
    defaultToggle.disabled = !!activeProfile?.is_default;
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
  if (computeAlternatesToggle) computeAlternatesToggle.checked = s.compute_alternates ?? true;  // default-on (graduated)

  // Icing method selector. Absent → the declared backend default (#403), so the
  // form shows the method absence actually grades on, not a hardcoded guess.
  const icingMethodSelect = document.getElementById('input-icing-method') as HTMLSelectElement;
  if (icingMethodSelect) icingMethodSelect.value = s.icing_method ?? engineDefaults.icing_method;

  // Cloud source selector. Absent → the declared backend default (#403). The
  // render style is a client-only viz preference (localStorage), not a profile
  // setting (#410).
  const cloudSourceSelect = document.getElementById('input-cloud-source') as HTMLSelectElement;
  if (cloudSourceSelect) cloudSourceSelect.value = s.cloud_source ?? engineDefaults.cloud_source;

  // Convective method selector
  const convectiveMethodSelect = document.getElementById('input-convective-method') as HTMLSelectElement;
  if (convectiveMethodSelect) convectiveMethodSelect.value = s.convective_method ?? engineDefaults.convective_method;

  // Digest guidance selector
  const guidanceSelect = document.getElementById('input-digest-guidance') as HTMLSelectElement;
  if (guidanceSelect) guidanceSelect.value = s.digest_guidance ?? 'balanced';

  // Advisories
  const advPrefs: AdvisoryPreferences = s.advisories ?? { enabled: null, params: null };
  const aggSelect = document.getElementById('advisory-aggregation') as HTMLSelectElement;
  if (aggSelect) aggSelect.value = advPrefs.aggregation ?? 'majority';
  // Interview answers travel with the profile so the assistant pre-selects them.
  interviewAnswers = { ...(s.interview ?? {}) };
  renderAdvisorySettings(catalog, advPrefs, s.auto_front_detection ?? false);
  // Re-baseline the live preview against this profile's saved settings. Placed
  // here (not in switchProfile) so EVERY entry point that loads a profile's form
  // — switch, new, duplicate, reset-to-template — re-baselines, not just the
  // switcher (#390 round-4). No-op until initAdvisoryPreview discovers a target.
  scheduleAdvisoryPreview(true);
}

function switchProfile(profileId: number): void {
  activeProfileId = profileId;
  renderProfileSelector();
  const profile = profiles.find(p => p.id === profileId);
  if (profile) {
    populateProfileForm(profile);  // also re-baselines the preview
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

async function handleToggleDefault(): Promise<void> {
  const toggle = document.getElementById('toggle-default-profile') as HTMLInputElement;
  if (!activeProfileId || !toggle) return;

  const current = profiles.find(p => p.id === activeProfileId);
  // Only promoting is meaningful — a profile can't un-default itself since one
  // profile is always the default. Unchecking the current default is a no-op.
  if (!toggle.checked || current?.is_default) {
    renderProfileSelector();
    return;
  }

  try {
    await updateProfile(activeProfileId, { is_default: true });
    // The server clears is_default on all other profiles; mirror that locally.
    profiles = profiles.map(p => ({ ...p, is_default: p.id === activeProfileId }));
    renderProfileSelector();
    showStatus(t('settings.defaultProfileSet', { name: current?.name ?? '' }));
  } catch (err) {
    renderProfileSelector();
    showStatus(`Failed to set default profile: ${err}`, true);
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
  set('#label-default-profile', 'settings.defaultProfile');
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
  // Speed label carries an (i) info button child — set the text but keep the button.
  const speedLabel = document.querySelector('label[for="input-speed"]');
  if (speedLabel) {
    const speedInfoBtn = speedLabel.querySelector('.cruise-speed-info-btn');
    speedLabel.textContent = t('page.settings.speed') + ' ';
    if (speedInfoBtn) speedLabel.appendChild(speedInfoBtn);
  }
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
  // Advisory toolbar + engine settings (#387)
  set('#btn-setup-assistant', 'settings.adv.setupAssistant');
  set('#btn-expand-all-advisories', 'settings.adv.expandAll');
  set('#btn-collapse-all-advisories', 'settings.adv.collapseAll');
  set('#engine-settings-title', 'settings.adv.engineSettings');
  set('#engine-settings-hint', 'settings.adv.engineSettingsHint');
  set('label[for="advisory-aggregation"]', 'page.settings.summaryRating');
  set('label[for="input-icing-method"]', 'page.settings.icingMethod');
  set('label[for="input-cloud-source"]', 'page.settings.cloudSource');
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
      return { advisories: [] as AdvisoryCatalogEntry[], categories: [] as AdvisoryCategory[], engine_method_defaults: ENGINE_METHOD_DEFAULTS_FALLBACK };
    }),
    fetchModelCatalog().catch(err => {
      showStatus(t('settings.failedLoad', { what: 'model catalog', error: String(err) }), true);
      return [];
    }),
  ]);

  catalog = catalogResult.advisories;
  advisoryCategories = catalogResult.categories;
  engineDefaults = catalogResult.engine_method_defaults ?? ENGINE_METHOD_DEFAULTS_FALLBACK;
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
  document.getElementById('toggle-default-profile')?.addEventListener('change', handleToggleDefault);
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
    // This flips the fronts advisory's enabled state programmatically (no input
    // event fires), so nudge the preview to reflect it (#390 round-4).
    scheduleAdvisoryPreview();
  });

  // Global expand/collapse all — toggles every per-advisory "Advanced" expander.
  const setAllAdvancedOpen = (open: boolean): void => {
    const settings = document.getElementById('advisory-settings');
    if (!settings) return;
    for (const d of settings.querySelectorAll<HTMLDetailsElement>('details.advisory-advanced')) {
      d.open = open;
    }
  };
  document.getElementById('btn-expand-all-advisories')?.addEventListener('click', () => setAllAdvancedOpen(true));
  document.getElementById('btn-collapse-all-advisories')?.addEventListener('click', () => setAllAdvancedOpen(false));

  // Setup assistant (interview) — opens the guided preset flow.
  document.getElementById('btn-setup-assistant')?.addEventListener('click', () => {
    void openSetupAssistant();
  });

  // Live preview (#387, slice 4): recompute the diff (debounced) whenever the
  // advisory enables/params or aggregation change. Delegated on the container so
  // it survives re-renders.
  // Arrow-wrapped so the DOM Event isn't passed as the `immediate` arg (edits
  // must use the debounce, not fire immediately).
  document.getElementById('advisory-settings')?.addEventListener('input', () => scheduleAdvisoryPreview());
  document.getElementById('advisory-settings')?.addEventListener('change', () => scheduleAdvisoryPreview());
  document.getElementById('advisory-aggregation')?.addEventListener('change', () => scheduleAdvisoryPreview());
  // The engine axes are part of the draft the preview evaluates (collectEngineDraft),
  // so they have to re-trigger it too — they live outside #advisory-settings and
  // are not covered by the delegated listeners above.
  for (const id of ['input-icing-method', 'input-cloud-source',
                    'input-convective-method', 'advisory-model-checkboxes']) {
    document.getElementById(id)?.addEventListener('change', () => scheduleAdvisoryPreview());
  }
  void initAdvisoryPreview();

  // Save button
  const form = document.getElementById('settings-form') as HTMLFormElement;
  form?.addEventListener('submit', async (e) => {
    e.preventDefault();
    await handleSave();
  });

  // Cruise speed (IAS) info popup — wired on both the aircraft and flight-default speed labels
  document.querySelectorAll('.cruise-speed-info-btn').forEach((btn) => {
    btn.addEventListener('click', (e) => {
      e.preventDefault();
      showPopupContent(`
        <h3 style="margin-top:0">${t('settings.cruiseSpeedInfoTitle')}</h3>
        ${t('settings.cruiseSpeedInfoBody')}
      `);
    });
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
  const aircraftLoaded = initAircraftTab();

  // Export my data (GDPR data portability)
  const exportDataBtn = document.getElementById('btn-export-data');
  exportDataBtn?.addEventListener('click', async () => {
    try {
      const resp = await fetch('/api/account/export');
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const blob = await resp.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = 'flyfun-weather-export.json';
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      showStatus(t('settings.exportDataDone'));
    } catch (err) {
      showStatus(t('settings.exportDataFailed', { error: String(err) }), true);
    }
  });

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
    // Dev-only: link to the eval labelling workbench, shown only when it's
    // actually mounted (WEATHERBRIEF_EVAL_WORKBENCH set) — never in prod.
    if (user.eval_workbench_enabled) {
      const evalLink = document.getElementById('eval-workbench-link');
      if (evalLink) evalLink.style.display = '';
    }
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
        clearOffered();
        showStatus('Tour offer reset — open a briefing to see the banner again.');
      } catch (err) {
        showStatus(`Failed to clear tour flag: ${String(err)}`, true);
      }
    });
    document.getElementById('btn-reset-first-time')?.addEventListener('click', async () => {
      try {
        await resetMyOnboarding();
        clearOffered();
        showStatus('First-time experience reset — wizard on Flights, banner on next briefing.');
      } catch (err) {
        showStatus(`Failed to reset first-time flags: ${String(err)}`, true);
      }
    });
  }

  // Deep-links from the flights page info popups (#400): open a specific
  // aircraft or profile straight to its editor.
  void applyDeepLink(aircraftLoaded);
}

/** Honour ?tab=/?profile=<id>/?aircraft=<id> query params: switch to the right
 *  tab and open the target profile or aircraft for editing. */
async function applyDeepLink(aircraftLoaded: Promise<void>): Promise<void> {
  const params = new URLSearchParams(window.location.search);
  const aircraftParam = params.get('aircraft');
  const profileParam = params.get('profile');
  const tabParam = params.get('tab');

  if (aircraftParam) {
    switchTab('aircraft');
    await aircraftLoaded;
    const ac = aircraftList.find(a => String(a.id) === aircraftParam);
    if (ac) {
      editAircraft(ac);
      document.getElementById('aircraft-form-section')
        ?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
    return;
  }

  if (profileParam) {
    switchTab('flight');
    const id = parseInt(profileParam, 10);
    if (!isNaN(id) && profiles.some(p => p.id === id)) switchProfile(id);
    return;
  }

  if (tabParam) switchTab(tabParam);
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

  // Display-currency picker — "auto" or an ISO code (cost/donation display only)
  const currencySelect = document.getElementById('input-display-currency') as HTMLSelectElement;
  if (currencySelect) {
    const dc = (prefs.display_currency || 'auto').toUpperCase();
    const known = Array.from(currencySelect.options).some((o) => o.value === dc);
    currencySelect.value = dc === 'AUTO' || !known ? 'auto' : dc;
  }

  // "Support the service" link is gated on Stripe being configured (global flag).
  const supportSection = document.getElementById('support-section');
  if (supportSection) {
    supportSection.style.display = prefs.donations_enabled ? '' : 'none';
    // Thank existing donors (fire-and-forget; total comes from /donations/me).
    if (prefs.donations_enabled) {
      void fetchMyDonations()
        .then((me) => {
          if (me.total_usd > 0) {
            const thanks = document.getElementById('support-thanks');
            if (thanks) thanks.style.display = '';
          }
        })
        .catch(() => {
          /* non-fatal — just skip the thank-you message */
        });
    }
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

  // Declared unpublished approaches (#510) — rendered from the server's
  // canonical list, so what the field shows after a save is exactly what was
  // stored, not what the user happened to type.
  const declaredInput = document.getElementById(
    'input-declared-approaches',
  ) as HTMLInputElement | null;
  if (declaredInput) {
    declaredInput.value = (prefs.declared_approach_icaos ?? []).join(' ');
  }
  clearDeclaredApproachError();

  renderNotificationSettings(prefs);
}

/**
 * Populate the Account › Notifications section (issue #371).
 *
 * Enforces the channel invariant — you can never disable the last *available*
 * channel, so the silent dead-state ("scope on, no channel → nothing happens")
 * is structurally impossible. The only way to silence is Briefing updates = Off:
 *   - Web-only user (no device): Email is the sole channel, locked on while
 *     Briefing updates ≠ Off.
 *   - User with a device: both channels toggle freely, but turning off the last
 *     enabled one reroutes Briefing updates to Off (the sanctioned way to stop).
 * Push is conditional on a registered device.
 */
function renderNotificationSettings(prefs: PreferencesResponse): void {
  const updatesSel = document.getElementById('notify-updates') as HTMLSelectElement | null;
  const emailToggle = document.getElementById('toggle-notify-email') as HTMLInputElement | null;
  const pushToggle = document.getElementById('toggle-notify-push') as HTMLInputElement | null;
  if (!updatesSel || !emailToggle || !pushToggle) return;

  const hasDevice = (prefs.push_device_count ?? 0) > 0;

  // Localize the static labels via t() (the app has no [data-i18n] applier).
  const setText = (id: string, key: string): void => {
    const el = document.getElementById(id);
    if (el) el.textContent = t(key);
  };
  setText('notify-title', 'settings.notify.title');
  setText('notify-section-hint', 'settings.notify.sectionHint');
  setText('notify-decay-text', 'settings.notify.decayNotice');
  setText('notify-updates-label', 'settings.notify.updates');
  setText('notify-email-label', 'settings.notify.email');
  setText('notify-push-label', 'settings.notify.push');
  const optText: Record<string, string> = {
    off: 'settings.notify.updates.off',
    changes: 'settings.notify.updates.changes',
    every: 'settings.notify.updates.every',
  };
  for (const opt of Array.from(updatesSel.options)) {
    const key = optText[opt.value];
    if (key) opt.textContent = t(key);
  }

  updatesSel.value = foldBriefingUpdates(prefs.notify_scope ?? 'auto', prefs.notify_change_only ?? true);
  emailToggle.checked = prefs.notify_email ?? true;
  pushToggle.checked = (prefs.notify_push ?? false) && hasDevice;
  pushToggle.disabled = !hasDevice;

  // One-time decay notice (email auto-re-enabled after last device removed).
  const decayRow = document.getElementById('notify-decay-notice');
  if (decayRow) decayRow.style.display = prefs.notify_decay_notice ? '' : 'none';

  const setHint = (id: string, key: string): void => {
    const el = document.getElementById(id);
    if (el) el.textContent = t(key);
  };

  const refresh = (): void => {
    const silenced = updatesSel.value === 'off';

    if (!hasDevice) {
      // Email is the only available channel. Lock it on while active; the only
      // way to stop is Briefing updates = Off (which unlocks it).
      if (!silenced) emailToggle.checked = true;
      emailToggle.disabled = !silenced;
    } else {
      emailToggle.disabled = false;
      // Turning off the last enabled channel means "silence me" → reroute to Off.
      if (!silenced && !emailToggle.checked && !pushToggle.checked) {
        updatesSel.value = 'off';
      }
    }

    const nowSilenced = updatesSel.value === 'off';
    setHint(
      'notify-updates-hint',
      nowSilenced
        ? 'settings.notify.updates.hintOff'
        : updatesSel.value === 'every'
          ? 'settings.notify.updates.hintEvery'
          : 'settings.notify.updates.hint',
    );
    setHint('notify-push-hint', hasDevice ? 'settings.notify.push.hint' : 'settings.notify.push.hintNoDevice');
    setHint(
      'notify-email-hint',
      emailToggle.disabled ? 'settings.notify.email.hintLocked' : 'settings.notify.email.hint',
    );
  };

  refresh();
  // Leaving Off with no channel on must enable one, or refresh()'s "both off →
  // reroute to Off" branch would snap the dropdown straight back, stranding the
  // user in a state they can't leave. Mirrors iOS setBriefingUpdates. Email is
  // always available, so it's the least-surprising channel to switch on.
  updatesSel.onchange = (): void => {
    if (updatesSel.value !== 'off' && !emailToggle.checked && !pushToggle.checked) {
      emailToggle.checked = true;
    }
    refresh();
  };
  // Channel toggles keep the reroute-to-Off behaviour (unchecking the last
  // channel means "silence me").
  emailToggle.onchange = refresh;
  pushToggle.onchange = refresh;
}

// --- Advisory settings rendering ---

/** True when the param is a pilot-tier personal-minimum / capability choice,
 *  rendered inline. A missing audience (old server) is treated as advanced so a
 *  param never leaks into the compact view unasked. */
function isPilotParam(param: AdvisoryParameterDef): boolean {
  return param.audience === 'pilot';
}

/** A param is "modified" when its saved value differs from the catalog default. */
function isParamModified(value: number, param: AdvisoryParameterDef): boolean {
  return value !== param.default;
}

/** Ordered category list for rendering. Uses the server-defined order (#387);
 *  falls back to the entries' own (already server-sorted) first-seen order when
 *  the category list is absent (legacy server). */
function orderedCategories(entries: AdvisoryCatalogEntry[]): AdvisoryCategory[] {
  if (advisoryCategories.length > 0) return advisoryCategories;
  const seen = new Set<string>();
  const out: AdvisoryCategory[] = [];
  for (const e of entries) {
    if (!seen.has(e.category)) {
      seen.add(e.category);
      out.push({ key: e.category, diagnostics: false });
    }
  }
  return out;
}

/** Localized category label, falling back to a capitalized raw key. */
function categoryLabel(key: string): string {
  const tk = `settings.cat.${key}`;
  const label = t(tk);
  return label === tk ? key.charAt(0).toUpperCase() + key.slice(1) : label;
}

/** Split severity role out of a threshold param key for visual amber/red
 *  pairing (#387, slice 2 — pairing only, no metadata). `green`/`amber` tokens
 *  are the amber ("warn") trigger; `red` is the ("alert") trigger. Returns the
 *  base quantity (key minus the severity token) and the role. */
function paramSeverity(key: string): { base: string; role: 'warn' | 'alert' | null } {
  let role: 'warn' | 'alert' | null = null;
  const kept: string[] = [];
  for (const part of key.split('_')) {
    if (part === 'red') { role = 'alert'; continue; }
    if (part === 'amber' || part === 'green') { role = role ?? 'warn'; continue; }
    kept.push(part);
  }
  return { base: kept.join('_'), role };
}

/** Render a set of params, pairing amber/red thresholds of the same quantity
 *  onto one visual row ("warn at / alert at"). Pairing is visual only. */
function renderParamGroup(
  advisoryId: string,
  params: AdvisoryParameterDef[],
  userParams: Record<string, number>,
  enabled: boolean,
): string {
  // Index params of this group by (base, role) for pairing.
  const byBaseRole = new Map<string, AdvisoryParameterDef>();
  for (const p of params) {
    const { base, role } = paramSeverity(p.key);
    if (role) byBaseRole.set(`${base}|${role}`, p);
  }
  const consumed = new Set<string>();
  let html = '';
  for (const param of params) {
    if (consumed.has(param.key)) continue;
    const { base, role } = paramSeverity(param.key);
    const partnerRole = role === 'warn' ? 'alert' : role === 'alert' ? 'warn' : null;
    const partner = partnerRole ? byBaseRole.get(`${base}|${partnerRole}`) : undefined;
    if (partner && !consumed.has(partner.key)) {
      // Render as a paired row, warn (amber) first then alert (red).
      const warn = role === 'warn' ? param : partner;
      const alert = role === 'warn' ? partner : param;
      consumed.add(warn.key);
      consumed.add(alert.key);
      html += `<div class="advisory-param-pair">`;
      html += renderParamInput(advisoryId, warn, userParams[warn.key] ?? warn.default, enabled);
      html += `<span class="advisory-param-sep">/</span>`;
      html += renderParamInput(advisoryId, alert, userParams[alert.key] ?? alert.default, enabled);
      html += `</div>`;
    } else {
      html += renderParamInput(advisoryId, param, userParams[param.key] ?? param.default, enabled);
    }
  }
  return html;
}

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

  let html = '';
  for (const cat of orderedCategories(entries)) {
    const catEntries = grouped.get(cat.key);
    if (!catEntries?.length) continue;

    html += `<div class="advisory-category${cat.diagnostics ? ' advisory-category-diagnostics' : ''}">`;
    const label = cat.diagnostics ? t('settings.adv.diagnostics') : categoryLabel(cat.key);
    html += `<div class="advisory-category-title">${escapeHtml(label)}</div>`;

    for (const entry of catEntries) {
      // The experimental front advisory is gated by the Auto Front Detection
      // master (issue #196, model B): the master generates the data + overlays,
      // this toggle independently controls the GREEN/AMBER/RED grade. Default it
      // *on* when the master is on (so it is discoverable), but let the pilot opt
      // out. When the master is off there is no artifact, so the advisory can
      // never surface — disable the row and explain the dependency.
      const isFronts = entry.id === FRONTS_ADVISORY_ID;
      const defaultEnabled = isFronts ? autoFrontDetection : entry.default_enabled;
      // Absent key falls back to default_enabled. The backend MUST resolve the
      // saved map the same way — keep in sync with resolve_enabled_ids()
      // (src/weatherbrief/analysis/advisories/registry.py).
      const isEnabled = enabledMap[entry.id] ?? defaultEnabled;
      const frontsDisabled = isFronts && !autoFrontDetection;
      const userParams = paramsMap[entry.id] ?? {};

      const pilotParams = entry.parameters.filter(isPilotParam);
      const advancedParams = entry.parameters.filter(p => !isPilotParam(p));

      html += `<div class="advisory-setting" data-advisory-row="${entry.id}">`;
      html += `<div class="advisory-header">`;
      html += `<label class="checkbox-label">`;
      html += `<input type="checkbox" data-advisory-id="${entry.id}" ${isEnabled ? 'checked' : ''}${frontsDisabled ? ' disabled' : ''}>`;
      html += ` ${escapeHtml(entry.name)}`;
      html += `<span class="advisory-desc">${escapeHtml(entry.short_description)}</span>`;
      html += `<button type="button" class="metric-info-btn advisory-settings-info-btn" data-advisory-id="${entry.id}" title="Advisory details" aria-label="Advisory details">i</button>`;
      html += `</label>`;
      if (frontsDisabled) {
        html += `<span class="advisory-desc muted">${escapeHtml(t('settings.adv.requiresAutoFront'))}</span>`;
      }
      html += `</div>`;

      // Pilot-tier params render inline (always visible, the compact view).
      if (pilotParams.length > 0) {
        html += `<div class="advisory-params" data-params-for="${entry.id}">`;
        html += renderParamGroup(entry.id, pilotParams, userParams, isEnabled);
        html += `</div>`;
      }

      // Advanced params hide behind a collapsed expander. A modified chip and
      // the per-advisory reset live in the expander summary so a non-default
      // value is never invisibly hidden.
      if (advancedParams.length > 0) {
        const modifiedCount = advancedParams.filter(
          p => isParamModified(userParams[p.key] ?? p.default, p),
        ).length;
        html += `<details class="advisory-advanced" data-advanced-for="${entry.id}">`;
        html += `<summary class="advisory-advanced-summary">`;
        html += `<span class="advisory-advanced-label">${escapeHtml(t('settings.adv.advanced'))} (${advancedParams.length})</span>`;
        html += `<span class="advisory-modified-chip${modifiedCount > 0 ? '' : ' is-hidden'}" data-modified-chip="${entry.id}">${escapeHtml(t('settings.adv.modified', { count: modifiedCount }))}</span>`;
        html += `<button type="button" class="advisory-reset-btn" data-reset-advisory="${entry.id}" title="${escapeHtml(t('settings.adv.resetTitle'))}">${escapeHtml(t('settings.adv.reset'))}</button>`;
        html += `</summary>`;
        html += `<div class="advisory-params" data-advanced-params-for="${entry.id}">`;
        html += renderParamGroup(entry.id, advancedParams, userParams, isEnabled);
        html += `</div>`;
        html += `</details>`;
      }

      html += `</div>`;
    }

    html += `</div>`;
  }

  container.innerHTML = html;

  const catalogMap = new Map(entries.map(e => [e.id, e]));

  // Toggle param disabled state when advisory is toggled (both inline + advanced).
  for (const cb of container.querySelectorAll<HTMLInputElement>('input[data-advisory-id]')) {
    cb.addEventListener('change', () => {
      const id = cb.dataset.advisoryId!;
      const row = container.querySelector(`[data-advisory-row="${id}"]`) as HTMLElement | null;
      if (!row) return;
      for (const input of row.querySelectorAll<HTMLInputElement>('input[data-advisory-param]')) {
        input.disabled = !cb.checked;
      }
    });
  }

  // Live-update the modified chip + default hints as advanced params are edited.
  for (const input of container.querySelectorAll<HTMLInputElement>('input[data-advisory-param]')) {
    input.addEventListener('input', () => {
      const [advId] = input.dataset.advisoryParam!.split(':');
      const entry = catalogMap.get(advId);
      if (entry) refreshAdvisoryDerived(container, entry);
    });
  }

  // Per-advisory reset — scoped to the *advanced* params only. The button lives
  // in the Advanced expander's summary, next to a "k modified" chip that counts
  // advanced params alone, so resetting the whole advisory would silently wipe
  // the inline pilot-tier values (crosswind limits, ceiling/visibility minima)
  // this feature exists to protect — a pilot seeing "0 modified" would click it
  // expecting a no-op (#390 review). Pilot params stay visible and editable
  // inline, each with its own "· default" hint.
  for (const btn of container.querySelectorAll<HTMLButtonElement>('[data-reset-advisory]')) {
    btn.addEventListener('click', (e) => {
      e.preventDefault();
      const id = btn.dataset.resetAdvisory!;
      const entry = catalogMap.get(id);
      if (!entry) return;
      for (const param of entry.parameters) {
        if (isPilotParam(param)) continue;
        const input = container.querySelector<HTMLInputElement>(
          `input[data-advisory-param="${id}:${param.key}"]`,
        );
        if (input) input.value = String(param.default);
      }
      refreshAdvisoryDerived(container, entry);
      scheduleAdvisoryPreview();
    });
  }

  // Info popup for advisory details.
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

  // Initial derived state (default hints hidden/shown for the saved values).
  for (const entry of entries) refreshAdvisoryDerived(container, entry);
}

/** Recompute a row's derived UI in place: per-param "· default" hints and the
 *  Advanced expander's "k modified" chip. Cheap; called on every edit. */
function refreshAdvisoryDerived(container: HTMLElement, entry: AdvisoryCatalogEntry): void {
  let modifiedAdvanced = 0;
  for (const param of entry.parameters) {
    const input = container.querySelector<HTMLInputElement>(
      `input[data-advisory-param="${entry.id}:${param.key}"]`,
    );
    if (!input) continue;
    const val = parseFloat(input.value);
    const modified = !isNaN(val) && isParamModified(val, param);
    const hint = input.parentElement?.querySelector('.param-default') as HTMLElement | null;
    if (hint) hint.classList.toggle('is-hidden', !modified);
    if (modified && !isPilotParam(param)) modifiedAdvanced += 1;
  }
  const chip = container.querySelector<HTMLElement>(`[data-modified-chip="${entry.id}"]`);
  if (chip) {
    chip.textContent = t('settings.adv.modified', { count: modifiedAdvanced });
    chip.classList.toggle('is-hidden', modifiedAdvanced === 0);
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
  const unitStr = param.unit ? `<span class="param-unit">${escapeHtml(param.unit)}</span>` : '';
  // "· default X" shown only when the value differs from the catalog default.
  const modified = isParamModified(value, param);
  const defaultHint = `<span class="param-default${modified ? '' : ' is-hidden'}">${escapeHtml(t('settings.adv.default', { value: param.default }))}</span>`;

  return `<div class="advisory-param">
    <label title="${escapeHtml(param.description)}">${escapeHtml(param.label)}</label>
    <input type="number" data-advisory-param="${advisoryId}:${param.key}"
      value="${value}"${minAttr}${maxAttr}${stepAttr}${disabledAttr}>
    ${unitStr}${defaultHint}
  </div>`;
}

// --- Setup assistant (interview presets, #387 slice 3) ---

/** Open the guided setup assistant — a small modal of identity questions whose
 *  answers patch advisory enabled/params. Pre-selects stored answers; warns
 *  before overwriting an owned key that the pilot manually modified. */
async function openSetupAssistant(): Promise<void> {
  if (!interview) {
    try {
      interview = await fetchAdvisoryInterview();
    } catch (err) {
      showStatus(t('settings.adv.interviewLoadFailed', { error: String(err) }), true);
      return;
    }
  }
  const iv = interview;

  let html = `<div class="interview-modal">`;
  html += `<h3 style="margin-top:0">${escapeHtml(t('settings.adv.interviewTitle'))}</h3>`;
  html += `<p class="muted">${escapeHtml(t('settings.adv.interviewIntro'))}</p>`;
  for (const q of iv.questions) {
    const selected = interviewAnswers[q.id] ?? q.options[0]?.id ?? '';
    html += `<fieldset class="interview-question">`;
    html += `<legend>${escapeHtml(q.title)}</legend>`;
    if (q.help) html += `<p class="muted interview-help">${escapeHtml(q.help)}</p>`;
    for (const opt of q.options) {
      html += `<label class="interview-option">`;
      html += `<input type="radio" name="iv-${escapeHtml(q.id)}" value="${escapeHtml(opt.id)}"${opt.id === selected ? ' checked' : ''}>`;
      html += `<span class="interview-option-label">${escapeHtml(opt.label)}</span>`;
      if (opt.description) html += `<span class="interview-option-desc">${escapeHtml(opt.description)}</span>`;
      html += `</label>`;
    }
    html += `</fieldset>`;
  }
  html += `<div class="interview-actions">`;
  html += `<button type="button" class="btn-secondary" id="iv-cancel">${escapeHtml(t('settings.adv.interviewCancel'))}</button>`;
  html += `<button type="button" class="btn-primary" id="iv-apply">${escapeHtml(t('settings.adv.interviewApply'))}</button>`;
  html += `</div></div>`;

  showPopupContent(html);
  const popup = document.getElementById('metric-info-popup');
  popup?.querySelector('#iv-cancel')?.addEventListener('click', () => hideMetricInfo());
  popup?.querySelector('#iv-apply')?.addEventListener('click', () => applyInterview(iv));
}

/** Apply the selected interview options to the live form. */
function applyInterview(iv: Interview): void {
  const chosen: InterviewOption[] = [];
  const answers: Record<string, string> = {};
  for (const q of iv.questions) {
    const radio = document.querySelector<HTMLInputElement>(`input[name="iv-${q.id}"]:checked`);
    const opt = radio ? q.options.find(o => o.id === radio.value) : undefined;
    if (opt) {
      chosen.push(opt);
      answers[q.id] = opt.id;
    }
  }

  // Read current state and detect owned keys the pilot manually moved off default
  // to a value the chosen options would overwrite — warn before clobbering.
  const prefs = collectAdvisoryPrefs();
  const curEnabled = prefs.enabled ?? {};
  const curParams = prefs.params ?? {};
  const catById = new Map(catalog.map(e => [e.id, e]));
  const conflicts: string[] = [];
  for (const opt of chosen) {
    for (const [id, val] of Object.entries(opt.enabled)) {
      const entry = catById.get(id);
      const def = entry?.default_enabled ?? true;
      const cur = curEnabled[id] ?? def;
      if (cur !== def && cur !== val) conflicts.push(entry?.name ?? id);
    }
    for (const [id, ps] of Object.entries(opt.params)) {
      const entry = catById.get(id);
      for (const [key, val] of Object.entries(ps)) {
        const pdef = entry?.parameters.find(p => p.key === key);
        if (!pdef) continue;
        const cur = curParams[id]?.[key] ?? pdef.default;
        if (cur !== pdef.default && cur !== val) conflicts.push(`${entry?.name ?? id} · ${pdef.label}`);
      }
    }
  }
  if (conflicts.length > 0) {
    if (!confirm(t('settings.adv.interviewOverwrite', { items: conflicts.join(', ') }))) return;
  }

  // Merge patches over collected prefs and re-render so the DOM reflects them.
  prefs.enabled = curEnabled;
  prefs.params = curParams;
  for (const opt of chosen) {
    for (const [id, val] of Object.entries(opt.enabled)) curEnabled[id] = val;
    for (const [id, ps] of Object.entries(opt.params)) {
      curParams[id] = { ...(curParams[id] ?? {}), ...ps };
    }
  }
  interviewAnswers = answers;
  const autoFront = (document.getElementById('toggle-auto-front-detection') as HTMLInputElement)?.checked ?? false;
  renderAdvisorySettings(catalog, prefs, autoFront);
  hideMetricInfo();
  showStatus(t('settings.adv.interviewApplied'));
  scheduleAdvisoryPreview();
}

// --- Live preview against a recent flight (#387, slice 4) ---

/** The engine axes of the settings form — which models vote, and the icing/cloud/
 *  convective grading methods. These change how the *same* weather is graded, so
 *  the live preview has to send the draft values or it silently predicts the
 *  wrong outcome for a Save that also changes them (#390 review). Shared with
 *  `handleSave` so the two can't drift. */
function collectEngineDraft(): {
  advisory_models: string[] | null;
  icing_method: string | null;
  cloud_source: 'dd' | 'nwp' | null;
  convective_method: string | null;
} {
  const advisoryModels: string[] = [];
  const advContainer = document.getElementById('advisory-model-checkboxes');
  if (advContainer) {
    for (const cb of advContainer.querySelectorAll<HTMLInputElement>('input[data-adv-model]')) {
      if (cb.checked) advisoryModels.push(cb.dataset.advModel!);
    }
  }
  // The select offers only 'nwp' / 'dd'; narrow to the ProfileSettings union.
  const cloudSource: 'dd' | 'nwp' =
    (document.getElementById('input-cloud-source') as HTMLSelectElement)?.value === 'dd' ? 'dd' : 'nwp';
  // Empty-select fallbacks are the declared backend defaults (#403), not the old
  // ogimet_dd/thermo literals that disagreed with what the form displayed.
  const icing = (document.getElementById('input-icing-method') as HTMLSelectElement)?.value || engineDefaults.icing_method;
  const convective = (document.getElementById('input-convective-method') as HTMLSelectElement)?.value || engineDefaults.convective_method;
  return {
    // null = "no explicit selection" ⇒ server defaults. Same encoding as the save
    // payload, so preview and save resolve models identically.
    advisory_models: advisoryModels.length > 0 ? advisoryModels : null,
    // Prune a method equal to its declared default → null, i.e. "follow the
    // default" (#403 Part B). Sent as an explicit null (never omitted): the
    // preview endpoint keys on JSON presence, so a null reads as the draft value
    // and grades on the resolved default; on save the server deletes the stored
    // key. Absence resolves to the same default server-side, so this is lossless
    // for grading and lets an already-dense profile shrink.
    icing_method: pruneEngineMethod(icing, engineDefaults.icing_method),
    cloud_source: pruneEngineMethod(cloudSource, engineDefaults.cloud_source) as 'dd' | 'nwp' | null,
    convective_method: pruneEngineMethod(convective, engineDefaults.convective_method),
  };
}

/** Find the user's most recent owned flight with a briefing pack (the pack the
 *  preview evaluates against), then compute the baseline + initial draft diff.
 *  Non-blocking: on any failure the preview panel simply stays hidden. The pack
 *  supplies only the *weather*; the settings compared are always the active
 *  profile's (baseline) vs the live form (draft). */
async function initAdvisoryPreview(): Promise<void> {
  try {
    const { flights } = await fetchFlights();
    const candidates = flights.filter(f => f.role === 'owner' && f.latest_briefing);
    for (const f of candidates) {
      try {
        const pack = await fetchLatestPack(f.id);
        if (pack?.fetch_timestamp && pack.has_advisories !== false) {
          const date = f.departure_time ? f.departure_time.slice(0, 10) : '';
          previewTarget = { flightId: f.id, ts: pack.fetch_timestamp, label: `${f.route_name} · ${date}` };
          break;
        }
      } catch { /* try the next flight */ }
    }
    if (!previewTarget) return;
    void scheduleAdvisoryPreview(true);
  } catch { /* preview stays hidden */ }
}

/** Schedule a preview recompute. Pass ``baselineChanged`` for events that alter
 *  the *saved* settings (init, profile switch/new/duplicate/reset, save): those
 *  run immediately and re-fetch the baseline. Plain edits pass nothing — they
 *  debounce (700ms) and reuse the cached baseline (it hasn't changed). */
function scheduleAdvisoryPreview(baselineChanged = false): void {
  if (!previewTarget) return;
  window.clearTimeout(previewTimer);
  if (baselineChanged) {
    void updatePreview(true);
  } else {
    previewTimer = window.setTimeout(() => void updatePreview(false), 700);
  }
}

/** Recompute the preview and render it, under one monotonic ticket so
 *  out-of-order responses can never render a stale diff (#390): a rapid edit or
 *  profile switch bumps ``previewSeq`` and any older in-flight response is
 *  discarded.
 *
 *  The baseline (active profile's *saved* settings) only changes on
 *  baseline-changing events, so it is cached and re-fetched only when
 *  ``refreshBaseline`` (or the cache is empty) — a debounced edit re-fetches
 *  just the draft, halving the server-side evaluation cost per keystroke (#390
 *  round-4). */
async function updatePreview(refreshBaseline: boolean): Promise<void> {
  if (!previewTarget) return;
  const seq = ++previewSeq;
  const prefs = collectAdvisoryPrefs();
  const needBaseline = refreshBaseline || previewBaselineCache === null;
  // Both halves are scoped to the profile being *edited*, not the profile bound
  // to the flight that supplies the preview weather — for a multi-profile pilot
  // those differ, and the flight's profile may carry a different engine config
  // entirely (#390 review). The baseline sends nothing but the profile, so the
  // server grades it under that profile's saved settings verbatim.
  const profileId = activeProfileId;
  try {
    const [base, draft] = await Promise.all([
      needBaseline
        ? previewAdvisories(previewTarget.flightId, previewTarget.ts, { profile_id: profileId })
        : Promise.resolve(null),
      previewAdvisories(previewTarget.flightId, previewTarget.ts, {
        profile_id: profileId,
        enabled: prefs.enabled,
        params: prefs.params,
        aggregation: prefs.aggregation,
        ...collectEngineDraft(),
      }),
    ]);
    if (seq !== previewSeq) return;  // a newer request superseded this one
    if (base) {
      const m: Record<string, string> = {};
      for (const a of base.manifest.advisories) m[a.advisory_id] = a.aggregate_status;
      previewBaselineCache = m;
    }
    renderPreviewDeltas(previewBaselineCache ?? {}, draft.manifest.advisories);
  } catch { /* leave the last render in place */ }
}

/** Sentinel status for an advisory that is disabled (absent from a manifest —
 *  `evaluate_all` only returns *enabled* advisories, so "off" ≠ any real grade). */
const PREVIEW_OFF = 'off';

/** Render the preview panel: which advisory ratings would change vs saved.
 *
 *  Unions the advisory ids present in the baseline OR the draft so enable/disable
 *  changes surface too — an advisory missing from a manifest is disabled (`off`),
 *  not "no change". Iterating only the draft list (as before) silently dropped
 *  both "just enabled" (undefined baseline) and "just disabled" (absent from
 *  draft) — exactly the Setup-Assistant toggles this feature is meant to preview. */
function renderPreviewDeltas(
  baseline: Record<string, string>,
  advisories: { advisory_id: string; aggregate_status: string }[],
): void {
  const panel = document.getElementById('advisory-preview');
  if (!panel || !previewTarget) return;
  const nameById = new Map(catalog.map(e => [e.id, e.name]));

  const draftById = new Map(advisories.map(a => [a.advisory_id, a.aggregate_status]));
  const ids = new Set<string>([...Object.keys(baseline), ...draftById.keys()]);

  const changes: { name: string; from: string; to: string }[] = [];
  for (const id of ids) {
    const before = baseline[id] ?? PREVIEW_OFF;
    const after = draftById.get(id) ?? PREVIEW_OFF;
    if (before !== after) {
      changes.push({ name: nameById.get(id) ?? id, from: before, to: after });
    }
  }
  changes.sort((a, b) => a.name.localeCompare(b.name));

  let html = `<div class="advisory-preview-head">`;
  html += `<span class="advisory-preview-title">${escapeHtml(t('settings.adv.previewTitle'))}</span>`;
  html += `<span class="advisory-preview-sub">${escapeHtml(t('settings.adv.previewSubtitle', { route: previewTarget.label }))}</span>`;
  html += `</div>`;
  if (changes.length === 0) {
    html += `<p class="muted advisory-preview-none">${escapeHtml(t('settings.adv.previewNoChanges'))}</p>`;
  } else {
    html += `<ul class="advisory-preview-list">`;
    for (const c of changes) {
      html += `<li><span class="advisory-preview-name">${escapeHtml(c.name)}</span> `;
      html += `<span class="adv-status adv-status-${escapeHtml(c.from)}">${escapeHtml(c.from.toUpperCase())}</span>`;
      html += ` → <span class="adv-status adv-status-${escapeHtml(c.to)}">${escapeHtml(c.to.toUpperCase())}</span></li>`;
    }
    html += `</ul>`;
  }
  panel.innerHTML = html;
  panel.classList.remove('is-hidden');
}

/** Collect advisory preferences from the form. */
function collectAdvisoryPrefs(): AdvisoryPreferences {
  const container = document.getElementById('advisory-settings');
  if (!container) return { enabled: null, params: null };

  const enabled: Record<string, boolean> = {};
  const rawParams: Record<string, Record<string, number>> = {};

  // Collect enabled states. Enable flags are deliberately NOT sparsified (#402):
  // `fronts` false equals the catalog default yet is a meaningful override, so the
  // full map is always sent.
  for (const cb of container.querySelectorAll<HTMLInputElement>('input[data-advisory-id]')) {
    const id = cb.dataset.advisoryId!;
    enabled[id] = cb.checked;
  }

  // Collect parameter values, then prune any equal to the catalog default (#403
  // Part B) — absence resolves to the same default server-side, so this is
  // lossless for grading and lets an already-dense profile shrink on save.
  for (const input of container.querySelectorAll<HTMLInputElement>('input[data-advisory-param]')) {
    const [advId, paramKey] = input.dataset.advisoryParam!.split(':');
    const val = parseFloat(input.value);
    if (isNaN(val)) continue;
    if (!rawParams[advId]) rawParams[advId] = {};
    rawParams[advId][paramKey] = val;
  }
  const params = pruneAdvisoryParams(rawParams, buildParamDefaults(catalog));

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

  // Advisory models + grading methods — the same collector the live preview uses,
  // so what was previewed is what gets saved.
  const engine = collectEngineDraft();

  const flightRules = (document.getElementById('input-flight-rules') as HTMLSelectElement)?.value || 'vfr_ifr';
  const grametEnabled = (document.getElementById('toggle-gramet') as HTMLInputElement)?.checked ?? true;
  const llmDigestEnabled = (document.getElementById('toggle-llm-digest') as HTMLInputElement)?.checked ?? true;
  const icingSeverityEnhance = (document.getElementById('toggle-icing-enhance') as HTMLInputElement)?.checked ?? false;
  const autoFrontDetection = (document.getElementById('toggle-auto-front-detection') as HTMLInputElement)?.checked ?? false;
  const computeAlternates = (document.getElementById('toggle-compute-alternates') as HTMLInputElement)?.checked ?? false;
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
    flight_rules: flightRules,
    gramet_enabled: grametEnabled,
    llm_digest_enabled: llmDigestEnabled,
    icing_severity_enhance: icingSeverityEnhance,
    auto_front_detection: autoFrontDetection,
    compute_alternates: computeAlternates,
    ...engine,
    digest_guidance: digestGuidance,
    advisories,
    // Persist interview answers so re-running the assistant pre-selects them (#387).
    interview: interviewAnswers,
  };

  // Account-level settings
  const selectedLocale = (document.getElementById('input-locale') as HTMLSelectElement)?.value || 'en';
  const unitsRegionVal = (document.getElementById('input-units-region') as HTMLSelectElement)?.value;
  const selectedUnitsRegion = unitsRegionVal === 'us' || unitsRegionVal === 'europe' ? unitsRegionVal : 'auto';
  const selectedDisplayCurrency = (document.getElementById('input-display-currency') as HTMLSelectElement)?.value || 'auto';

  try {
    // Save profile settings
    if (activeProfileId) {
      const updated = await updateProfile(activeProfileId, { settings: profileSettings });
      const idx = profiles.findIndex(p => p.id === activeProfileId);
      if (idx >= 0) profiles[idx] = updated;
      // The saved settings just changed — re-baseline the preview so the next
      // edit diffs against what was actually saved (#390 review).
      scheduleAdvisoryPreview(true);
    }

    // Save account-level preferences (locale + optional services + autorouter creds in dev mode)
    const synopticEnabled = (document.getElementById('toggle-synoptic-forecast-map') as HTMLInputElement)?.checked ?? false;
    const deferModelUpdate = (document.getElementById('toggle-defer-model-update') as HTMLInputElement)?.checked ?? false;
    const declaredInput = document.getElementById(
      'input-declared-approaches',
    ) as HTMLInputElement | null;
    const accountUpdate: import('./adapters/preferences-adapter').PreferencesUpdate = {
      locale: selectedLocale,
      units_region: selectedUnitsRegion,
      display_currency: selectedDisplayCurrency,
      synoptic_forecast_map_enabled: synopticEnabled,
      defer_email_for_model_update: deferModelUpdate,
    };
    // Sent verbatim — the server splits, uppercases, dedupes and validates
    // against nav.db, and rejects the whole write if a code is unknown (#510).
    if (declaredInput) {
      accountUpdate.declared_approach_icaos = declaredInput.value;
    }

    // Briefing notifications (issue #371). The 3-stop folds into scope +
    // change-only; channels persist as-is (the invariant is enforced live in
    // the form, so we never save an all-off active state).
    const updatesSel = document.getElementById('notify-updates') as HTMLSelectElement | null;
    const notifyEmailEl = document.getElementById('toggle-notify-email') as HTMLInputElement | null;
    const notifyPushEl = document.getElementById('toggle-notify-push') as HTMLInputElement | null;
    if (updatesSel && notifyEmailEl && notifyPushEl) {
      const folded = unfoldBriefingUpdates(updatesSel.value as 'off' | 'changes' | 'every');
      accountUpdate.notify_scope = folded.notify_scope;
      accountUpdate.notify_change_only = folded.notify_change_only;
      accountUpdate.notify_email = notifyEmailEl.checked;
      // Push can only be on with a registered device (toggle is disabled otherwise).
      accountUpdate.notify_push = notifyPushEl.checked && !notifyPushEl.disabled;
    }
    // Dismiss the one-time decay notice once the user has seen the section.
    const decayRow = document.getElementById('notify-decay-notice');
    if (decayRow && decayRow.style.display !== 'none') {
      accountUpdate.notify_decay_notice = false;
    }
    if (autorouterMode === 'password') {
      const arUsername = (document.getElementById('input-ar-username') as HTMLInputElement)?.value.trim();
      const arPassword = (document.getElementById('input-ar-password') as HTMLInputElement)?.value.trim();
      if (arUsername) accountUpdate.autorouter_username = arUsername;
      if (arPassword) accountUpdate.autorouter_password = arPassword;
    }

    clearDeclaredApproachError();
    const result = await savePreferences(accountUpdate);
    setUnitsPreference(result.units_region);
    // Re-render from the canonical stored list, so the field shows what was
    // actually saved rather than the user's spacing and casing.
    if (declaredInput) {
      declaredInput.value = (result.declared_approach_icaos ?? []).join(' ');
    }
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
    // An unknown airport code is a fixable typo in one field, not a generic
    // save failure — name it at the field so the user can see which code was
    // rejected. #510 requires it never be silently dropped, and a message that
    // only says "save failed" would be exactly that in effect.
    const codes = unknownAirportCodes(err);
    if (codes.length) {
      showDeclaredApproachError(
        t('settings.declaredApproaches.unknown', { codes: codes.join(', ') }),
      );
    }
    showStatus(t('settings.failedSave', { error: String(err) }), true);
  }
}

/** The unknown ICAO codes named by a rejected preferences save, if any. */
function unknownAirportCodes(err: unknown): string[] {
  const detail = (err as { detail?: unknown })?.detail;
  if (!detail || typeof detail !== 'object') return [];
  const d = detail as { error?: unknown; codes?: unknown };
  if (d.error !== 'unknown_airports' || !Array.isArray(d.codes)) return [];
  return d.codes.map(String);
}

function showDeclaredApproachError(message: string): void {
  const el = document.getElementById('declared-approaches-error');
  if (!el) return;
  el.textContent = message;
  el.style.display = '';
  el.classList.add('field-error');
}

function clearDeclaredApproachError(): void {
  const el = document.getElementById('declared-approaches-error');
  if (!el) return;
  el.textContent = '';
  el.style.display = 'none';
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

function initAircraftTab(): Promise<void> {
  // Load aircraft list. Returned so deep-links (?aircraft=<id>) can await it
  // before trying to open the target aircraft's edit form.
  const loaded = fetchAircraft()
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

  return loaded;
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
