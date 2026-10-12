/**
 * Welcome wizard — shown on first login to collect initial settings.
 *
 * Four steps:
 *   1. Welcome  — brief intro to Flyfun Weather
 *   2. Profile  — select a default profile from system templates
 *   3. Aircraft — two groups (#777):
 *        "Your aircraft"      → POST /aircraft (name, ICAO type, cruise speed,
 *                               service ceiling) as the default aircraft; the
 *                               server replaces the synthesised placeholder.
 *        "Your usual mission" → the selected profile (flight rules, cruise
 *                               altitude, ceiling, models).
 *   4. Tour     — quick overview of key features
 *
 * On completion, marks the selected profile as default and saves setup_completed.
 */

import { updateProfile, fetchSystemTemplates, type ProfileResponse, type ProfileSettings, type SystemTemplate } from '../adapters/profiles-adapter';
import {
  createAircraft, fetchAircraft, updateAircraft,
  type AircraftResponse, type CreateAircraftRequest,
} from '../adapters/aircraft-adapter';
import { attachAircraftTypeTypeahead } from './aircraft-type-typeahead';
import { applyCeilingWarning, parseCeilingInput } from '../utils/ceiling-warning';
import { completeSetup } from '../adapters/preferences-adapter';
import { escapeHtml, initModelCatalog, allModelKeys, defaultModelKeys, modelLabel } from '../utils';
import type { ModelCatalogEntry } from '../utils';
import { t } from '../i18n/i18n';
import { buildDemoTourUrl } from '../tour/demo-config';

type StepId = 'welcome' | 'profile' | 'aircraft' | 'tour';

const STEPS: StepId[] = ['welcome', 'profile', 'aircraft', 'tour'];
const DEFAULT_CRUISE_ALT_FT = 8000;
const DEFAULT_CEILING_FT = 18000;

let backdropEl: HTMLElement | null = null;
let wizardEl: HTMLElement | null = null;
let currentStep = 0;
let allProfiles: ProfileResponse[] = [];
let selectedProfileId: number | null = null;
let systemTemplates: SystemTemplate[] = [];
/** The pilot's real aircraft once the wizard has saved it (or a real default
 *  that already existed). Later saves PUT it instead of POSTing a second one. */
let wizardAircraft: AircraftResponse | null = null;
/** True when wizardAircraft is a real aircraft the pilot had before the wizard:
 *  we then don't touch its IFR flag (that's the pilot's earlier answer). */
let aircraftPreExisted = false;
/** Field values of the aircraft group, kept across Back/Next re-renders. */
interface AircraftDraft { name: string; icao: string; typeName: string; speed: string; ceiling: string }
let aircraftDraft: AircraftDraft = { name: '', icao: '', typeName: '', speed: '', ceiling: '' };

/** Show the welcome wizard. Returns a promise that resolves when the user finishes. */
export async function showWelcomeWizard(
  profile: ProfileResponse,
  modelCatalog: ModelCatalogEntry[],
  profiles?: ProfileResponse[],
): Promise<void> {
  allProfiles = profiles ?? [profile];
  selectedProfileId = profile.id;
  initModelCatalog(modelCatalog);
  currentStep = 0;

  // Load system template descriptions and the user's aircraft (the server
  // always returns at least the synthesised "My aircraft" placeholder).
  const [templates, aircraft] = await Promise.all([
    fetchSystemTemplates().catch(() => [] as SystemTemplate[]),
    fetchAircraft().catch(() => [] as AircraftResponse[]),
  ]);
  systemTemplates = templates;
  const def = aircraft.find(a => a.is_default) ?? null;
  wizardAircraft = def && !def.is_placeholder ? def : null;
  aircraftPreExisted = wizardAircraft != null;
  aircraftDraft = draftFromAircraft(def);

  return new Promise<void>((resolve) => {
    createWizardDOM(resolve);
    renderStep();
  });
}

function getSelectedProfile(): ProfileResponse | null {
  return allProfiles.find(p => p.id === selectedProfileId) ?? allProfiles[0] ?? null;
}

function createWizardDOM(onComplete: () => void): void {
  backdropEl = document.createElement('div');
  backdropEl.className = 'wizard-backdrop';

  wizardEl = document.createElement('div');
  wizardEl.className = 'wizard-modal';

  wizardEl.addEventListener('click', handleProfileCardClick);

  backdropEl.appendChild(wizardEl);
  document.body.appendChild(backdropEl);

  requestAnimationFrame(() => {
    backdropEl?.classList.add('active');
  });

  (wizardEl as any)._onComplete = onComplete;
}

function renderStep(): void {
  if (!wizardEl) return;

  const step = STEPS[currentStep];
  const isFirst = currentStep === 0;
  const isLast = currentStep === STEPS.length - 1;

  const dots = STEPS.map((_, i) =>
    `<span class="wizard-dot${i === currentStep ? ' active' : ''}"></span>`
  ).join('');

  let content = '';
  switch (step) {
    case 'welcome':
      content = renderWelcomeStep();
      break;
    case 'profile':
      content = renderProfileStep();
      break;
    case 'aircraft':
      content = renderAircraftStep();
      break;
    case 'tour':
      content = renderTourStep();
      break;
  }

  const backBtn = isFirst
    ? ''
    : `<button type="button" class="btn btn-secondary wizard-btn-back">${t('wizard.back')}</button>`;
  const nextLabel = isLast ? t('wizard.getStarted') : t('wizard.next');
  const nextClass = isLast ? 'btn btn-primary wizard-btn-finish' : 'btn btn-primary wizard-btn-next';

  wizardEl.innerHTML = `
    <div class="wizard-progress">${dots}</div>
    <div class="wizard-content">${content}</div>
    <div class="wizard-actions">
      ${backBtn}
      <button type="button" class="${nextClass}">${nextLabel}</button>
    </div>
  `;

  wireStepHandlers();
}

function wireStepHandlers(): void {
  if (!wizardEl) return;

  const backBtn = wizardEl.querySelector('.wizard-btn-back');
  const nextBtn = wizardEl.querySelector('.wizard-btn-next');
  const finishBtn = wizardEl.querySelector('.wizard-btn-finish');
  const takeTourBtn = wizardEl.querySelector('.wizard-btn-take-tour') as HTMLAnchorElement | null;

  takeTourBtn?.addEventListener('click', (e) => {
    // Finish setup first so the wizard doesn't re-appear after the tour.
    // Navigation is async, so we suppress the default click and dispatch
    // manually after completeSetup resolves.
    e.preventDefault();
    const href = takeTourBtn.href;
    handleFinish()
      .catch((err) => console.error('Tour setup failed:', err))
      .finally(() => {
        window.location.href = href;
      });
  });

  backBtn?.addEventListener('click', () => {
    if (currentStep > 0) {
      // Leaving the aircraft step backwards keeps what was typed (not saved
      // until Next). The aircraft step itself saved on Next, so going back
      // from the tour step has nothing to collect.
      if (STEPS[currentStep] === 'aircraft') readAircraftDraft();
      currentStep--;
      renderStep();
    }
  });

  nextBtn?.addEventListener('click', async () => {
    if (STEPS[currentStep] === 'profile') {
      await applyProfileSelection();
    }
    if (STEPS[currentStep] === 'aircraft') {
      if (!validateAircraftStep()) return;
      await collectAndSaveAircraftSettings();
    }
    currentStep++;
    renderStep();
  });

  finishBtn?.addEventListener('click', () => handleFinish());

  if (STEPS[currentStep] === 'aircraft') wireAircraftStep();
}

/** Typeahead on the ICAO type + live ceiling-vs-service-ceiling warning. */
function wireAircraftStep(): void {
  if (!wizardEl) return;
  const icaoInput = wizardEl.querySelector('#wiz-ac-icao') as HTMLInputElement | null;
  const dropdown = wizardEl.querySelector('#wiz-ac-type-suggestions') as HTMLElement | null;
  const typeLabel = wizardEl.querySelector('#wiz-ac-type-label') as HTMLElement | null;
  if (icaoInput && dropdown) {
    attachAircraftTypeTypeahead(icaoInput, dropdown, (type) => {
      aircraftDraft.typeName = `${type.manufacturer} ${type.model}`;
      if (typeLabel) typeLabel.textContent = aircraftDraft.typeName;
      setIcaoError(false);
      updateWizardCeilingWarning();
    });
    icaoInput.addEventListener('input', () => {
      // A hand-typed code no longer matches the picked type's name.
      aircraftDraft.typeName = '';
      if (typeLabel) typeLabel.textContent = '';
      if (icaoInput.value.trim()) setIcaoError(false);
    });
  }
  for (const id of ['#wiz-ac-ceiling', '#wiz-ceiling', '#wiz-ac-name', '#wiz-ac-icao']) {
    wizardEl.querySelector(id)?.addEventListener('input', updateWizardCeilingWarning);
  }
  updateWizardCeilingWarning();
}

/** The two ceilings sit in separate groups; warn when the mission ceiling is
 *  above the aircraft's service ceiling (warn only, never clamps). */
function updateWizardCeilingWarning(): void {
  if (!wizardEl) return;
  readAircraftDraft();
  const missionCeiling = parseCeilingInput((wizardEl.querySelector('#wiz-ceiling') as HTMLInputElement | null)?.value);
  const icao = aircraftDraft.icao.toUpperCase();
  applyCeilingWarning(
    wizardEl.querySelector('#wiz-ceiling-warning') as HTMLElement | null,
    missionCeiling,
    {
      tail_number: wizardAircraft?.tail_number ?? null,
      nickname: aircraftDraft.name || null,
      type_name: aircraftDraft.typeName || icao || t('wizard.yourAircraft'),
      ceiling_ft: parseCeilingInput(aircraftDraft.ceiling),
    },
  );
}

function setIcaoError(show: boolean): void {
  const err = wizardEl?.querySelector('#wiz-ac-icao-error') as HTMLElement | null;
  if (err) err.hidden = !show;
  (wizardEl?.querySelector('#wiz-ac-icao') as HTMLInputElement | null)
    ?.classList.toggle('input-error', show);
}

/** The ICAO type is required: the server needs one to create the aircraft,
 *  and leaving it empty would leave the placeholder in place. "ZZZZ" (the ICAO
 *  code for a type without a designator) is the documented escape. */
function validateAircraftStep(): boolean {
  readAircraftDraft();
  if (aircraftDraft.icao) return true;
  setIcaoError(true);
  (wizardEl?.querySelector('#wiz-ac-icao') as HTMLInputElement | null)?.focus();
  return false;
}

function renderWelcomeStep(): string {
  return `
    <div class="wizard-step wizard-welcome">
      <h2>${t('wizard.welcomeTitle')}</h2>
      <p class="wizard-subtitle">
        ${t('wizard.welcomeSubtitle')}
      </p>
      <p class="wizard-beta-notice">
        ${t('wizard.betaNotice')}
      </p>
      <div class="wizard-features">
        <div class="wizard-feature">
          <strong>${t('wizard.featureAiTitle')}</strong>
          <span>${t('wizard.featureAiDesc')}</span>
        </div>
        <div class="wizard-feature">
          <strong>${t('wizard.featureAdvisoriesTitle')}</strong>
          <span>${t('wizard.featureAdvisoriesDesc')}</span>
        </div>
        <div class="wizard-feature">
          <strong>${t('wizard.featureModelsTitle')}</strong>
          <span>${t('wizard.featureModelsDesc')}</span>
        </div>
        <div class="wizard-feature">
          <strong>${t('wizard.featureCrossSectionTitle')}</strong>
          <span>${t('wizard.featureCrossSectionDesc')}</span>
        </div>
        <div class="wizard-feature">
          <strong>${t('wizard.featureSoundingTitle')}</strong>
          <span>${t('wizard.featureSoundingDesc')}</span>
        </div>
      </div>
      <p class="wizard-hint">
        ${t('wizard.setupHint')}
      </p>
    </div>
  `;
}

function renderProfileStep(): string {
  // Build profile cards from system templates matched to user profiles
  const cardsHtml = allProfiles
    .filter(p => p.system_template_key)
    .map(p => {
      const tpl = systemTemplates.find(t => t.key === p.system_template_key);
      const desc = tpl?.description ?? '';
      const isSelected = p.id === selectedProfileId;
      const activeClass = isSelected ? ' wizard-profile-card-active' : '';
      return `
        <div class="wizard-profile-card${activeClass}" data-profile-id="${p.id}">
          <div class="wizard-profile-card-header">
            <strong>${escapeHtml(p.name)}</strong>
            ${isSelected ? '<span class="badge badge-green">Default</span>' : ''}
          </div>
          <p class="wizard-profile-desc">${escapeHtml(desc)}</p>
        </div>
      `;
    })
    .join('');

  // Fallback if no system templates were seeded
  if (!cardsHtml) {
    return `
      <div class="wizard-step">
        <h2>${t('wizard.profileTitle')}</h2>
        <p class="wizard-subtitle">${t('wizard.profileSubtitle')}</p>
        <p class="muted">${t('wizard.profileNone')}</p>
      </div>
    `;
  }

  return `
    <div class="wizard-step wizard-profiles">
      <h2>${t('wizard.profileTitle')}</h2>
      <p class="wizard-subtitle">${t('wizard.profileSubtitle')}</p>
      <p class="wizard-hint">${t('wizard.profileHint')}</p>
      <div class="wizard-profile-cards">${cardsHtml}</div>
    </div>
  `;
}

function renderAircraftStep(): string {
  const profile = getSelectedProfile();
  const settings = profile?.settings;
  const altitude = settings?.cruise_altitude_ft ?? DEFAULT_CRUISE_ALT_FT;
  const ceiling = settings?.flight_ceiling_ft ?? DEFAULT_CEILING_FT;
  const rules = settings?.flight_rules ?? 'vfr_ifr';
  const d = aircraftDraft;

  const defaults = settings?.models ?? defaultModelKeys();
  const modelHtml = allModelKeys().map(m => {
    const label = escapeHtml(modelLabel(m));
    const checked = defaults.includes(m) ? ' checked' : '';
    return `<label class="checkbox-label"><input type="checkbox" data-model="${escapeHtml(m)}"${checked}> ${label}</label>`;
  }).join('');

  return `
    <div class="wizard-step wizard-aircraft">
      <h2>${t('wizard.aircraftTitle')}</h2>
      <p class="wizard-subtitle">${t('wizard.aircraftSubtitle')}</p>

      <div class="wizard-form">
        <fieldset class="wizard-group">
          <legend>${t('wizard.aircraftGroup')}</legend>
          <p class="muted wizard-group-hint">${t('wizard.aircraftGroupHint')}</p>
          <div class="wizard-form-row">
            <div class="wizard-form-group">
              <label for="wiz-ac-name">${t('wizard.aircraftName')}</label>
              <input type="text" id="wiz-ac-name" value="${escapeHtml(d.name)}" placeholder="${escapeHtml(t('wizard.aircraftNamePlaceholder'))}" maxlength="50">
            </div>
            <div class="wizard-form-group wizard-typeahead">
              <label for="wiz-ac-icao">${t('wizard.icaoType')}</label>
              <input type="text" id="wiz-ac-icao" value="${escapeHtml(d.icao)}" placeholder="${escapeHtml(t('wizard.icaoTypePlaceholder'))}" maxlength="4" autocomplete="off" required>
              <div id="wiz-ac-type-suggestions" class="autocomplete-dropdown" style="display:none"></div>
              <span id="wiz-ac-type-label" class="muted wizard-type-label">${escapeHtml(d.typeName)}</span>
              <span id="wiz-ac-icao-error" class="wizard-field-error" hidden>${t('wizard.icaoTypeRequired')}</span>
            </div>
          </div>
          <div class="wizard-form-row">
            <div class="wizard-form-group">
              <label for="wiz-ac-speed">${t('wizard.speed')}</label>
              <input type="number" id="wiz-ac-speed" value="${escapeHtml(d.speed)}" placeholder="${escapeHtml(t('wizard.speedPlaceholder'))}" min="10" max="500" step="1">
            </div>
            <div class="wizard-form-group">
              <label for="wiz-ac-ceiling">${t('wizard.serviceCeiling')}</label>
              <input type="number" id="wiz-ac-ceiling" value="${escapeHtml(d.ceiling)}" placeholder="${escapeHtml(t('wizard.serviceCeilingPlaceholder'))}" min="1000" max="60000" step="500">
            </div>
          </div>
        </fieldset>

        <fieldset class="wizard-group">
          <legend>${t('wizard.missionGroup')}</legend>
          <p class="muted wizard-group-hint">${t('wizard.missionGroupHint')}</p>
          <div class="wizard-form-row">
            <div class="wizard-form-group">
              <label for="wiz-flight-rules">${t('wizard.flightRules')}</label>
              <select id="wiz-flight-rules" class="input-select">
                <option value="vfr_ifr"${rules === 'vfr_ifr' ? ' selected' : ''}>${t('wizard.vfrIfr')}</option>
                <option value="vfr_only"${rules === 'vfr_only' ? ' selected' : ''}>${t('wizard.vfrOnly')}</option>
              </select>
            </div>
          </div>
          <div class="wizard-form-row">
            <div class="wizard-form-group">
              <label for="wiz-altitude">${t('wizard.cruiseAltitude')}</label>
              <input type="number" id="wiz-altitude" value="${altitude}" min="1000" max="45000" step="500">
            </div>
            <div class="wizard-form-group">
              <label for="wiz-ceiling">${t('wizard.flightCeiling')}</label>
              <input type="number" id="wiz-ceiling" value="${ceiling}" min="1000" max="45000" step="500">
            </div>
          </div>
          <p id="wiz-ceiling-warning" class="ceiling-warning" role="status" hidden></p>

          <div class="wizard-models-section">
            <label>${t('wizard.forecastModels')}</label>
            <p class="muted wizard-model-hint">${t('wizard.modelHint')}</p>
            <div class="wizard-model-checkboxes">${modelHtml}</div>
          </div>
        </fieldset>
      </div>
      <p class="wizard-hint">${t('wizard.aircraftHint')}</p>
    </div>
  `;
}

/** Seed the aircraft group from the user's default aircraft. A placeholder
 *  only contributes its speed (carried over from the old profile speed); its
 *  "My aircraft" name and ZZZZ type are stand-ins, not the pilot's answers. */
function draftFromAircraft(ac: AircraftResponse | null): AircraftDraft {
  if (!ac) return { name: '', icao: '', typeName: '', speed: '', ceiling: '' };
  const speed = ac.cruise_speed_kt != null ? String(ac.cruise_speed_kt) : '';
  if (ac.is_placeholder) return { name: '', icao: '', typeName: '', speed, ceiling: '' };
  return {
    name: ac.nickname ?? '',
    icao: ac.icao_type,
    typeName: ac.type_name !== ac.icao_type ? ac.type_name : '',
    speed,
    ceiling: ac.ceiling_ft != null ? String(ac.ceiling_ft) : '',
  };
}

/** Copy the aircraft group's inputs into aircraftDraft (no-op off the step). */
function readAircraftDraft(): void {
  if (!wizardEl) return;
  const val = (id: string) => (wizardEl!.querySelector(id) as HTMLInputElement | null)?.value?.trim();
  const icao = val('#wiz-ac-icao');
  if (icao === undefined) return;  // aircraft step not rendered
  aircraftDraft = {
    ...aircraftDraft,
    name: val('#wiz-ac-name') ?? '',
    icao: icao.toUpperCase(),
    speed: val('#wiz-ac-speed') ?? '',
    ceiling: val('#wiz-ac-ceiling') ?? '',
  };
}

function renderTourStep(): string {
  const demoUrl = buildDemoTourUrl();
  const ctaHtml = demoUrl
    ? `<div class="wizard-tour-cta">
         <a class="btn btn-primary wizard-btn-take-tour" href="${escapeHtml(demoUrl)}">
           ${t('wizard.startTourBtn')}
         </a>
         <p class="wizard-hint">${t('wizard.startTourHint')}</p>
       </div>`
    : '';

  return `
    <div class="wizard-step wizard-tour">
      <h2>${t('wizard.tourTitle')}</h2>
      <p class="wizard-subtitle">${t('wizard.tourSubtitle')}</p>

      ${ctaHtml}

      <div class="wizard-tour-items">
        <div class="wizard-tour-item">
          <div class="wizard-tour-number">1</div>
          <div class="wizard-tour-text">
            <strong>${t('wizard.step1Title')}</strong>
            <span>${t('wizard.step1Desc')}</span>
          </div>
        </div>
        <div class="wizard-tour-item">
          <div class="wizard-tour-number">2</div>
          <div class="wizard-tour-text">
            <strong>${t('wizard.step2Title')}</strong>
            <span>${t('wizard.step2Desc')}</span>
          </div>
        </div>
        <div class="wizard-tour-item">
          <div class="wizard-tour-number">3</div>
          <div class="wizard-tour-text">
            <strong>${t('wizard.step3Title')}</strong>
            <span>${t('wizard.step3Desc')}</span>
            <span class="wizard-tour-hint">${t('wizard.step3Hint')}</span>
          </div>
        </div>
        <div class="wizard-tour-item">
          <div class="wizard-tour-number">4</div>
          <div class="wizard-tour-text">
            <strong>${t('wizard.step4Title')}</strong>
            <span>${t('wizard.step4Desc')}</span>
          </div>
        </div>
        <div class="wizard-tour-item">
          <div class="wizard-tour-number">5</div>
          <div class="wizard-tour-text">
            <strong>${t('wizard.step5Title')}</strong>
            <span>${t('wizard.step5Desc')}</span>
          </div>
        </div>
      </div>

      <p class="wizard-hint">
        ${t('wizard.tourHint')}
      </p>
    </div>
  `;
}

/** Handle profile card click — defined as named function so it can be removed on cleanup. */
function handleProfileCardClick(e: Event): void {
  const card = (e.target as HTMLElement).closest('.wizard-profile-card') as HTMLElement | null;
  if (!card || !wizardEl) return;
  const profileId = parseInt(card.dataset.profileId!, 10);
  if (isNaN(profileId)) return;

  selectedProfileId = profileId;

  // Update active state on all cards
  for (const c of wizardEl.querySelectorAll('.wizard-profile-card')) {
    const el = c as HTMLElement;
    const isActive = parseInt(el.dataset.profileId!, 10) === profileId;
    el.classList.toggle('wizard-profile-card-active', isActive);
    const badge = el.querySelector('.badge');
    if (badge) badge.remove();
    if (isActive) {
      el.querySelector('.wizard-profile-card-header')?.insertAdjacentHTML(
        'beforeend',
        ' <span class="badge badge-green">Default</span>'
      );
    }
  }
}

/** Set the selected profile as default (via API). */
async function applyProfileSelection(): Promise<void> {
  if (selectedProfileId == null) return;
  try {
    await updateProfile(selectedProfileId, { is_default: true });
  } catch (err) {
    console.error('Failed to set default profile:', err);
  }
}

/** Save the aircraft step: the aircraft group to the default aircraft
 *  (POST, or PUT once the wizard has one), the mission group to the selected
 *  profile. Only called while the aircraft step is rendered. */
async function collectAndSaveAircraftSettings(): Promise<void> {
  if (!wizardEl) return;
  const profile = getSelectedProfile();

  const altitude = parseInt((wizardEl.querySelector('#wiz-altitude') as HTMLInputElement)?.value || String(DEFAULT_CRUISE_ALT_FT), 10);
  const ceiling = parseInt((wizardEl.querySelector('#wiz-ceiling') as HTMLInputElement)?.value || String(DEFAULT_CEILING_FT), 10);
  const rules = (wizardEl.querySelector('#wiz-flight-rules') as HTMLSelectElement)?.value || 'vfr_ifr';

  const models: string[] = [];
  for (const cb of wizardEl.querySelectorAll<HTMLInputElement>('input[data-model]')) {
    if (cb.checked) models.push(cb.dataset.model!);
  }

  readAircraftDraft();
  await saveWizardAircraft(rules);

  if (!profile) return;
  // Profile = the mission. No speed: it lives on the aircraft (#777).
  const settings: Partial<ProfileSettings> = {
    cruise_altitude_ft: isNaN(altitude) ? null : altitude,
    flight_ceiling_ft: isNaN(ceiling) ? null : ceiling,
    flight_rules: rules,
    models: models.length > 0 ? models : null,
  };

  try {
    await updateProfile(profile.id, { settings });
  } catch (err) {
    console.error('Failed to save wizard profile settings:', err);
  }
}

/** POST the pilot's aircraft as the default (the server replaces the
 *  synthesised placeholder and re-points its flights), or PUT it when the
 *  wizard already holds a real one (Back → Next, or a pre-existing default). */
async function saveWizardAircraft(rules: string): Promise<void> {
  const d = aircraftDraft;
  if (!d.icao) return;  // validated before we get here
  const speed = parseInt(d.speed, 10);
  const ceiling = parseInt(d.ceiling, 10);
  const req: CreateAircraftRequest = {
    icao_type: d.icao,
    nickname: d.name || null,
    cruise_speed_kt: Number.isFinite(speed) && speed > 0 ? speed : null,
    ceiling_ft: Number.isFinite(ceiling) && ceiling > 0 ? ceiling : null,
    // Mirrors the placeholder rule: IFR-capable unless the pilot flies VFR only.
    ...(aircraftPreExisted ? {} : { is_ifr: rules !== 'vfr_only' }),
    is_default: true,
  };
  try {
    wizardAircraft = wizardAircraft
      ? await updateAircraft(wizardAircraft.id, req)
      : await createAircraft(req);
  } catch (err) {
    console.error('Failed to save wizard aircraft:', err);
  }
}

async function handleFinish(): Promise<void> {
  if (!wizardEl || !backdropEl) return;

  const onComplete = (wizardEl as any)._onComplete as () => void;

  if (STEPS[currentStep] === 'aircraft') {
    await collectAndSaveAircraftSettings();
  }

  try {
    await completeSetup();
  } catch (err) {
    console.error('Failed to mark setup complete:', err);
  }

  wizardEl.removeEventListener('click', handleProfileCardClick);

  backdropEl.classList.remove('active');
  setTimeout(() => {
    backdropEl?.remove();
    backdropEl = null;
    wizardEl = null;
  }, 200);

  onComplete();
}
