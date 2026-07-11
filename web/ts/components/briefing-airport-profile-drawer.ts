import { t } from '../i18n/i18n';
import { modelLabel } from '../utils';
import { AirportProfilePanel } from '../visualization/airport-profile-panel';
import { hideMetricInfo } from './info-popup';

export interface BriefingAirportProfileRequest {
  departureIcao: string;
  arrivalIcao: string;
  departureTime: string;
  arrivalTime: string;
  advisoryModel: string;
  availableModels: string[];
}

export const SUPPORTED_AIRPORT_PROFILE_MODELS = ['ecmwf', 'gfs', 'icon'] as const;

type Endpoint = 'departure' | 'arrival';

interface FocusReturnIdentity {
  id: string | null;
  advisoryId: string | null;
  actionKind: string | null;
}

const DEPARTURE_TAB_ID = 'briefing-airport-profile-departure-tab';
const ARRIVAL_TAB_ID = 'briefing-airport-profile-arrival-tab';
const TABPANEL_ID = 'briefing-airport-profile-tabpanel';
const MODAL_PORTAL_SELECTOR = '[data-modal-portal="true"]';
const ACTIVE_MODAL_PORTAL_SELECTOR = `${MODAL_PORTAL_SELECTOR}[data-modal-portal-active="true"]`;

function translatedOrFallback(key: string, fallback: string): string {
  const translated = t(key);
  return translated === key ? fallback : translated;
}

function formatUtcTime(iso: string): string {
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return iso;
  return `${parsed.toISOString().slice(0, 16).replace('T', ' ')}Z`;
}

function focusableElements(root: HTMLElement): HTMLElement[] {
  return Array.from(root.querySelectorAll<HTMLElement>(
    'button:not([disabled]), select:not([disabled]), input:not([disabled]), '
      + 'a[href], [tabindex]:not([tabindex="-1"])',
  )).filter((element) => (
    element.tabIndex >= 0
    && !element.hidden
    && element.getClientRects().length > 0
  ));
}

function containingActiveModalPortal(target: Node): HTMLElement | null {
  const element = target instanceof Element ? target : target.parentElement;
  return element?.closest<HTMLElement>(ACTIVE_MODAL_PORTAL_SELECTOR) ?? null;
}

export class BriefingAirportProfileDrawer {
  private readonly root: HTMLElement;
  private readonly heading: HTMLHeadingElement;
  private readonly departureTab: HTMLButtonElement;
  private readonly arrivalTab: HTMLButtonElement;
  private readonly tabPanel: HTMLDivElement;
  private readonly endpointTime: HTMLTimeElement;
  private readonly fallbackNote: HTMLDivElement;
  private readonly panelHost: HTMLDivElement;
  private panel: AirportProfilePanel | null = null;
  private request: BriefingAirportProfileRequest | null = null;
  private endpoint: Endpoint = 'departure';
  private profileModel: string | null = null;
  private invokingElement: HTMLElement | null = null;
  private invokingElementIdentity: FocusReturnIdentity | null = null;
  private readonly siblingInertStates = new Map<HTMLElement, boolean>();
  private modalIsolationActive = false;
  private bodyObserver: MutationObserver | null = null;
  private readonly documentFocusListener = (event: FocusEvent): void => {
    if (!this.modalIsolationActive || this.root.hidden) return;
    const target = event.target;
    if (target instanceof Node && this.root.contains(target)) return;
    if (target instanceof Node && containingActiveModalPortal(target)) return;
    (this.endpoint === 'departure' ? this.departureTab : this.arrivalTab).focus();
  };
  private readonly documentKeyListener = (event: KeyboardEvent): void => {
    if (!this.modalIsolationActive || this.root.hidden || event.key !== 'Tab') return;
    const target = event.target;
    if (target instanceof Node && containingActiveModalPortal(target)) {
      this.trapModalTab(event);
    }
  };

  constructor() {
    const root = document.createElement('aside');
    root.className = 'briefing-airport-profile-drawer';
    root.hidden = true;
    root.setAttribute('role', 'dialog');
    root.setAttribute('aria-modal', 'true');
    root.setAttribute('aria-labelledby', 'briefing-airport-profile-title');
    root.innerHTML = `
      <div class="briefing-airport-profile-shell">
        <header class="briefing-airport-profile-header">
          <h2 id="briefing-airport-profile-title"></h2>
          <button type="button" class="briefing-airport-profile-close">&times;</button>
        </header>
        <div class="briefing-airport-endpoints" role="tablist"></div>
        <div class="briefing-airport-profile-tabpanel" role="tabpanel" id="${TABPANEL_ID}">
          <time class="briefing-airport-endpoint-time"></time>
          <div class="briefing-airport-fallback-note" hidden></div>
          <div class="briefing-airport-profile-panel-host"></div>
        </div>
      </div>
    `;
    document.body.appendChild(root);
    this.root = root;
    this.heading = root.querySelector('h2') as HTMLHeadingElement;
    this.tabPanel = root.querySelector('.briefing-airport-profile-tabpanel') as HTMLDivElement;
    this.endpointTime = root.querySelector('.briefing-airport-endpoint-time') as HTMLTimeElement;
    this.fallbackNote = root.querySelector('.briefing-airport-fallback-note') as HTMLDivElement;
    this.panelHost = root.querySelector('.briefing-airport-profile-panel-host') as HTMLDivElement;

    const tabs = root.querySelector('.briefing-airport-endpoints') as HTMLDivElement;
    this.departureTab = document.createElement('button');
    this.departureTab.type = 'button';
    this.departureTab.className = 'briefing-airport-endpoint-tab';
    this.departureTab.id = DEPARTURE_TAB_ID;
    this.departureTab.setAttribute('role', 'tab');
    this.departureTab.setAttribute('aria-controls', TABPANEL_ID);
    this.arrivalTab = document.createElement('button');
    this.arrivalTab.type = 'button';
    this.arrivalTab.className = 'briefing-airport-endpoint-tab';
    this.arrivalTab.id = ARRIVAL_TAB_ID;
    this.arrivalTab.setAttribute('role', 'tab');
    this.arrivalTab.setAttribute('aria-controls', TABPANEL_ID);
    tabs.append(this.departureTab, this.arrivalTab);

    const closeButton = root.querySelector(
      '.briefing-airport-profile-close',
    ) as HTMLButtonElement;
    const closeLabel = t('advisories.focusClose');
    closeButton.setAttribute('aria-label', closeLabel);
    closeButton.title = closeLabel;
    closeButton.addEventListener('click', () => this.close());
    this.departureTab.addEventListener('click', () => this.selectEndpoint('departure'));
    this.arrivalTab.addEventListener('click', () => this.selectEndpoint('arrival'));
    root.addEventListener('keydown', (event) => this.onKeyDown(event));
  }

  open(request: BriefingAirportProfileRequest): void {
    const openingFromClosed = this.root.hidden;
    this.destroyPanel();
    this.request = request;
    this.endpoint = 'departure';
    if (openingFromClosed) this.captureInvokingElement();
    const exactModelSupported = SUPPORTED_AIRPORT_PROFILE_MODELS.includes(
      request.advisoryModel as typeof SUPPORTED_AIRPORT_PROFILE_MODELS[number],
    ) && request.availableModels.includes(request.advisoryModel);
    // Prefer the exact supported advisory model; otherwise the declared
    // ecmwf→gfs→icon order determines the visible fallback.
    this.profileModel = exactModelSupported
      ? request.advisoryModel
      : SUPPORTED_AIRPORT_PROFILE_MODELS.find(
          model => request.availableModels.includes(model),
        ) ?? null;

    this.heading.textContent = translatedOrFallback(
      'advisories.airportProfileTitle',
      'Airport profile',
    );
    this.departureTab.textContent = `${translatedOrFallback(
      'advisories.airportProfileDeparture',
      'Departure',
    )} ${request.departureIcao}`;
    this.arrivalTab.textContent = `${translatedOrFallback(
      'advisories.airportProfileArrival',
      'Arrival',
    )} ${request.arrivalIcao}`;

    this.renderFallbackNote();

    this.root.hidden = false;
    if (openingFromClosed) this.activateModalIsolation();
    if (this.profileModel) {
      this.panel = new AirportProfilePanel({
        container: this.panelHost,
        initialModel: this.profileModel,
        onClose: () => this.close(),
        onModelChange: (model) => {
          this.profileModel = model;
          this.renderFallbackNote();
        },
      });
    }
    this.selectEndpoint('departure');
    this.departureTab.focus();
  }

  close(): void {
    if (this.root.hidden) return;
    this.destroyPanel();
    this.root.hidden = true;
    this.request = null;
    const returnTarget = this.resolveInvokingElement();
    this.clearInvokingElement();
    this.deactivateModalIsolation();
    returnTarget?.focus();
  }

  destroy(): void {
    this.destroyPanel();
    this.request = null;
    this.clearInvokingElement();
    this.deactivateModalIsolation();
    this.root.remove();
  }

  private captureInvokingElement(): void {
    const active = document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null;
    this.invokingElement = active;
    this.invokingElementIdentity = active
      ? {
          id: active.id || null,
          advisoryId: active.dataset.advisoryId ?? null,
          actionKind: active.dataset.actionKind ?? null,
        }
      : null;
  }

  private resolveInvokingElement(): HTMLElement | null {
    if (this.invokingElement?.isConnected) return this.invokingElement;
    const identity = this.invokingElementIdentity;
    if (!identity) return null;
    if (identity.id) {
      const byId = document.getElementById(identity.id);
      if (byId instanceof HTMLElement) return byId;
    }
    if (!identity.advisoryId) return null;
    return Array.from(document.querySelectorAll<HTMLElement>('[data-advisory-id]'))
      .find(element => (
        element.dataset.advisoryId === identity.advisoryId
        && (identity.actionKind === null
          || element.dataset.actionKind === identity.actionKind)
      )) ?? null;
  }

  private clearInvokingElement(): void {
    this.invokingElement = null;
    this.invokingElementIdentity = null;
  }

  private activateModalIsolation(): void {
    if (this.modalIsolationActive) return;
    this.modalIsolationActive = true;
    this.siblingInertStates.clear();
    for (const child of Array.from(document.body.children)) {
      this.isolateBodySibling(child);
    }
    this.bodyObserver = new MutationObserver((records) => {
      if (!this.modalIsolationActive) return;
      for (const record of records) {
        for (const added of Array.from(record.addedNodes)) {
          if (added.parentNode === document.body) this.isolateBodySibling(added);
        }
      }
    });
    this.bodyObserver.observe(document.body, { childList: true });
    document.addEventListener('focusin', this.documentFocusListener, true);
    document.addEventListener('keydown', this.documentKeyListener, true);
  }

  private isolateBodySibling(node: Node): void {
    if (node === this.root || !(node instanceof HTMLElement)) return;
    if (node.matches(MODAL_PORTAL_SELECTOR)) return;
    if (!this.siblingInertStates.has(node)) {
      this.siblingInertStates.set(node, node.inert);
    }
    node.inert = true;
  }

  private deactivateModalIsolation(): void {
    if (!this.modalIsolationActive) return;
    this.modalIsolationActive = false;
    this.bodyObserver?.disconnect();
    this.bodyObserver = null;
    document.removeEventListener('focusin', this.documentFocusListener, true);
    document.removeEventListener('keydown', this.documentKeyListener, true);
    for (const [sibling, wasInert] of this.siblingInertStates) {
      sibling.inert = wasInert;
    }
    this.siblingInertStates.clear();
  }

  private selectEndpoint(endpoint: Endpoint): void {
    if (!this.request) return;
    this.endpoint = endpoint;
    const isDeparture = endpoint === 'departure';
    this.departureTab.setAttribute('aria-selected', String(isDeparture));
    this.departureTab.tabIndex = isDeparture ? 0 : -1;
    this.arrivalTab.setAttribute('aria-selected', String(!isDeparture));
    this.arrivalTab.tabIndex = isDeparture ? -1 : 0;
    this.tabPanel.setAttribute(
      'aria-labelledby',
      isDeparture ? DEPARTURE_TAB_ID : ARRIVAL_TAB_ID,
    );
    const time = isDeparture ? this.request.departureTime : this.request.arrivalTime;
    const icao = isDeparture ? this.request.departureIcao : this.request.arrivalIcao;
    this.endpointTime.dateTime = time;
    this.endpointTime.textContent = formatUtcTime(time);
    this.panel?.load({ icao, startHour: time });
  }

  private destroyPanel(): void {
    hideMetricInfo();
    this.panel?.destroy();
    this.panel = null;
    this.panelHost.innerHTML = '';
  }

  private renderFallbackNote(): void {
    const request = this.request;
    const fallback = request !== null
      && this.profileModel !== null
      && (!SUPPORTED_AIRPORT_PROFILE_MODELS.includes(
        request.advisoryModel as typeof SUPPORTED_AIRPORT_PROFILE_MODELS[number],
      ) || !request.availableModels.includes(request.advisoryModel));
    this.fallbackNote.hidden = !fallback;
    this.fallbackNote.textContent = fallback && request && this.profileModel
      ? translatedOrFallback(
          'advisories.airportProfileFallback',
          `${modelLabel(request.advisoryModel)} is unavailable for airport profiles; using ${modelLabel(this.profileModel)}.`,
        ).replace('{advisoryModel}', modelLabel(request.advisoryModel))
          .replace('{profileModel}', modelLabel(this.profileModel))
      : '';
  }

  private onKeyDown(event: KeyboardEvent): void {
    if (this.root.hidden) return;
    if (event.key === 'Escape') {
      event.preventDefault();
      this.close();
      return;
    }
    const eventTab = event.target === this.departureTab
      ? this.departureTab
      : event.target === this.arrivalTab
        ? this.arrivalTab
        : null;
    if (eventTab) {
      let endpoint: Endpoint | null = null;
      if (event.key === 'ArrowRight') {
        endpoint = eventTab === this.departureTab ? 'arrival' : 'departure';
      } else if (event.key === 'ArrowLeft') {
        endpoint = eventTab === this.departureTab ? 'arrival' : 'departure';
      } else if (event.key === 'Home') {
        endpoint = 'departure';
      } else if (event.key === 'End') {
        endpoint = 'arrival';
      }
      if (endpoint) {
        event.preventDefault();
        this.selectEndpoint(endpoint);
        (endpoint === 'departure' ? this.departureTab : this.arrivalTab).focus();
        return;
      }
    }
    if (event.key !== 'Tab') return;
    this.trapModalTab(event);
  }

  private trapModalTab(event: KeyboardEvent): void {
    const activePortals = Array.from(
      document.querySelectorAll<HTMLElement>(ACTIVE_MODAL_PORTAL_SELECTOR),
    );
    const focusable = [
      ...focusableElements(this.root),
      ...activePortals.flatMap(portal => focusableElements(portal)),
    ];
    if (focusable.length === 0) {
      event.preventDefault();
      return;
    }
    event.preventDefault();
    const currentIndex = document.activeElement instanceof HTMLElement
      ? focusable.indexOf(document.activeElement)
      : -1;
    const nextIndex = event.shiftKey
      ? (currentIndex <= 0 ? focusable.length - 1 : currentIndex - 1)
      : (currentIndex < 0 || currentIndex === focusable.length - 1 ? 0 : currentIndex + 1);
    focusable[nextIndex].focus();
  }
}
