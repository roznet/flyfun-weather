/**
 * Welcome wizard — shown on first login to collect initial settings.
 *
 * Three steps:
 *   1. Welcome  — brief intro to Flyfun Weather
 *   2. Aircraft — flight rules, cruise altitude, ceiling, speed
 *   3. Tour    — quick overview of key features
 *
 * On completion, saves the default profile and marks setup_completed.
 */

import { updateProfile, type ProfileResponse, type ProfileSettings } from '../adapters/profiles-adapter';
import { completeSetup } from '../adapters/preferences-adapter';
import { escapeHtml, initModelCatalog, allModelKeys, defaultModelKeys, modelLabel } from '../utils';
import type { ModelCatalogEntry } from '../utils';
import { t } from '../i18n/i18n';

type StepId = 'welcome' | 'aircraft' | 'tour';

const STEPS: StepId[] = ['welcome', 'aircraft', 'tour'];
const DEFAULT_CRUISE_ALT_FT = 8000;
const DEFAULT_CEILING_FT = 18000;

let backdropEl: HTMLElement | null = null;
let wizardEl: HTMLElement | null = null;
let currentStep = 0;
let defaultProfile: ProfileResponse | null = null;

/** Show the welcome wizard. Returns a promise that resolves when the user finishes. */
export async function showWelcomeWizard(
  profile: ProfileResponse,
  modelCatalog: ModelCatalogEntry[],
): Promise<void> {
  defaultProfile = profile;
  initModelCatalog(modelCatalog);
  currentStep = 0;

  return new Promise<void>((resolve) => {
    createWizardDOM(resolve);
    renderStep();
  });
}

function createWizardDOM(onComplete: () => void): void {
  // Backdrop
  backdropEl = document.createElement('div');
  backdropEl.className = 'wizard-backdrop';

  // Wizard container
  wizardEl = document.createElement('div');
  wizardEl.className = 'wizard-modal';

  backdropEl.appendChild(wizardEl);
  document.body.appendChild(backdropEl);

  // Show with a slight delay for CSS transition
  requestAnimationFrame(() => {
    backdropEl?.classList.add('active');
  });

  // Store onComplete for later
  (wizardEl as any)._onComplete = onComplete;
}

function renderStep(): void {
  if (!wizardEl) return;

  const step = STEPS[currentStep];
  const isFirst = currentStep === 0;
  const isLast = currentStep === STEPS.length - 1;

  // Progress indicator
  const dots = STEPS.map((_, i) =>
    `<span class="wizard-dot${i === currentStep ? ' active' : ''}"></span>`
  ).join('');

  let content = '';
  switch (step) {
    case 'welcome':
      content = renderWelcomeStep();
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

  backBtn?.addEventListener('click', async () => {
    if (currentStep > 0) {
      // Save aircraft settings before going back from tour
      if (STEPS[currentStep] === 'tour') {
        await collectAndSaveAircraftSettings();
      }
      currentStep--;
      renderStep();
    }
  });

  nextBtn?.addEventListener('click', async () => {
    if (STEPS[currentStep] === 'aircraft') {
      await collectAndSaveAircraftSettings();
    }
    currentStep++;
    renderStep();
  });

  finishBtn?.addEventListener('click', () => handleFinish());
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

function renderAircraftStep(): string {
  const settings = defaultProfile?.settings;
  const altitude = settings?.cruise_altitude_ft ?? DEFAULT_CRUISE_ALT_FT;
  const ceiling = settings?.flight_ceiling_ft ?? DEFAULT_CEILING_FT;
  const speed = settings?.speed_kt ?? '';
  const rules = settings?.flight_rules ?? 'vfr_ifr';

  // Model checkboxes
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
      <p class="wizard-hint">${t('wizard.aircraftHint')}</p>

      <div class="wizard-form">
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
          <div class="wizard-form-group">
            <label for="wiz-speed">${t('wizard.speed')}</label>
            <input type="number" id="wiz-speed" value="${speed}" placeholder="${t('wizard.speedPlaceholder')}" min="10" max="500" step="1">
          </div>
        </div>

        <div class="wizard-models-section">
          <label>${t('wizard.forecastModels')}</label>
          <p class="muted wizard-model-hint">${t('wizard.modelHint')}</p>
          <div class="wizard-model-checkboxes">${modelHtml}</div>
        </div>
      </div>
    </div>
  `;
}

function renderTourStep(): string {
  return `
    <div class="wizard-step wizard-tour">
      <h2>${t('wizard.tourTitle')}</h2>
      <p class="wizard-subtitle">${t('wizard.tourSubtitle')}</p>

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

/** Collect aircraft settings from the form and save to the default profile. */
async function collectAndSaveAircraftSettings(): Promise<void> {
  if (!wizardEl || !defaultProfile) return;

  const altitude = parseInt((wizardEl.querySelector('#wiz-altitude') as HTMLInputElement)?.value || String(DEFAULT_CRUISE_ALT_FT), 10);
  const ceiling = parseInt((wizardEl.querySelector('#wiz-ceiling') as HTMLInputElement)?.value || String(DEFAULT_CEILING_FT), 10);
  const speedRaw = (wizardEl.querySelector('#wiz-speed') as HTMLInputElement)?.value?.trim();
  const speed = speedRaw ? parseInt(speedRaw, 10) : null;
  const rules = (wizardEl.querySelector('#wiz-flight-rules') as HTMLSelectElement)?.value || 'vfr_ifr';

  const models: string[] = [];
  for (const cb of wizardEl.querySelectorAll<HTMLInputElement>('input[data-model]')) {
    if (cb.checked) models.push(cb.dataset.model!);
  }

  const settings: Partial<ProfileSettings> = {
    cruise_altitude_ft: isNaN(altitude) ? null : altitude,
    flight_ceiling_ft: isNaN(ceiling) ? null : ceiling,
    speed_kt: speed != null && !isNaN(speed) ? speed : null,
    flight_rules: rules,
    models: models.length > 0 ? models : null,
  };

  try {
    const updated = await updateProfile(defaultProfile.id, { settings });
    defaultProfile = updated;
  } catch (err) {
    console.error('Failed to save wizard profile settings:', err);
  }
}

async function handleFinish(): Promise<void> {
  if (!wizardEl || !backdropEl) return;

  const onComplete = (wizardEl as any)._onComplete as () => void;

  // Save settings from current step if on aircraft
  if (STEPS[currentStep] === 'aircraft') {
    await collectAndSaveAircraftSettings();
  }

  // Mark setup as completed
  try {
    await completeSetup();
  } catch (err) {
    console.error('Failed to mark setup complete:', err);
  }

  // Remove wizard
  backdropEl.classList.remove('active');
  setTimeout(() => {
    backdropEl?.remove();
    backdropEl = null;
    wizardEl = null;
  }, 200);

  onComplete();
}
