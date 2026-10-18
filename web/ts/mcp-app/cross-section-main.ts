/**
 * MCP App widget: the route cross-section inside a Claude / ChatGPT chat (#819).
 *
 * The host renders this page (built into one HTML file, `web/dist/
 * mcp-cross-section.html`) in a sandboxed iframe after the model calls
 * `show_cross_section`. That result carries view parameters only; the pack
 * data comes from the app-only `xsection_data` tool, one model at a time.
 * Every model / layer / point change is told to the model, debounced, as a
 * short text context (model-context.ts). Design: designs/mcp-apps.md.
 */

import { App } from '@modelcontextprotocol/ext-apps';
import type { McpUiHostContext } from '@modelcontextprotocol/ext-apps';
import { layersWithExtra, XsectionView } from '../embed/xsection-view';
import { initI18n, t } from '../i18n/i18n';
import { CrossSectionRenderer } from '../visualization/cross-section/renderer';
import { attachInteraction, type InteractionHandle } from '../visualization/cross-section/interaction';
import { getLayerGroups } from '../visualization/cross-section/layer-registry';
import { setActiveTheme } from '../visualization/cross-section/theme';
import { buildModelContext, describePoint, utc } from './model-context';
import { parseViewParams, XsectionLoader, type ToolCallResult, type ViewParams } from './loader';

const CONTEXT_DEBOUNCE_MS = 500;
/** Chart height inline; fullscreen takes the viewport. */
const INLINE_CHART_PX = 320;

const root = document.getElementById('xs-root')!;
const toolbar = document.getElementById('xs-toolbar')!;
const chart = document.getElementById('xs-chart')!;
const info = document.getElementById('xs-info')!;
const status = document.getElementById('xs-status')!;

const app = new App({ name: 'flyfun-weather-cross-section', version: '1.0.0' }, {}, { autoResize: true });
let started = false;
// Settles once messages are loaded in the host's locale (known only after the
// handshake); a tool result that arrives first waits for it.
let i18nDone!: () => void;
const i18nReady = new Promise<void>((resolve) => { i18nDone = resolve; });
let view: XsectionView | null = null;
let loader: XsectionLoader | null = null;
let interaction: InteractionHandle | null = null;
let params: ViewParams | null = null;
let contextTimer: ReturnType<typeof setTimeout> | undefined;

function showStatus(text: string | null): void {
  status.textContent = text ?? '';
  status.hidden = text == null;
}

/** Theme, display mode and size from the host (initial and every change). */
function applyHostContext(ctx: McpUiHostContext | undefined): void {
  if (!ctx) return;
  if (ctx.theme) {
    document.documentElement.dataset.theme = ctx.theme;
    setActiveTheme(ctx.theme === 'dark' ? 'standard' : 'light');
  }
  const fullscreen = ctx.displayMode === 'fullscreen';
  root.classList.toggle('xs-fullscreen', fullscreen);
  const fsBtn = document.getElementById('xs-fullscreen');
  if (fsBtn) {
    fsBtn.hidden = !(app.getHostContext()?.availableDisplayModes ?? []).includes('fullscreen');
    fsBtn.textContent = fullscreen ? t('mcpApp.exitFullscreen') : t('mcpApp.fullscreen');
  }
  const dims = ctx.containerDimensions as { maxHeight?: number } | undefined;
  const cap = !fullscreen && dims?.maxHeight ? Math.max(160, dims.maxHeight - 90) : INLINE_CHART_PX;
  chart.style.height = fullscreen ? '' : `${Math.min(INLINE_CHART_PX, cap)}px`;
  if (view?.data) redraw();
}

function routeLabel(): string {
  const icaos = (view?.data?.manifest.analyses ?? []).map((a) => a.waypoint_icao).filter(Boolean);
  return icaos.join(' → ') || view?.data?.manifest.route_name || '';
}

/** Layer names in panel order, for the model context. */
function enabledLayerNames(): string[] {
  if (!view) return [];
  return panelGroups().flatMap((g) => g.layers.filter((l) => view!.enabled[l.id]).map((l) => t(`viz.layer.${l.id}`)));
}

/** The briefing page's layer groups, minus the advisory highlight (no advisories here). */
function panelGroups() {
  return getLayerGroups().filter((g) => g.group !== 'highlight');
}

function scheduleContext(): void {
  clearTimeout(contextTimer);
  contextTimer = setTimeout(() => {
    if (!view?.data || !params) return;
    const text = buildModelContext({
      route: routeLabel(),
      packTimestamp: params.pack_timestamp,
      model: view.model,
      models: view.models,
      layers: enabledLayerNames(),
      point: describePoint(view.data.manifest, view.point),
    });
    app.updateModelContext({ content: [{ type: 'text', text }] }).catch((err: Error) => {
      console.warn('updateModelContext failed', err);
    });
  }, CONTEXT_DEBOUNCE_MS);
}

function updateInfo(): void {
  if (!view?.data) return;
  const p = describePoint(view.data.manifest, view.point);
  info.textContent = p
    ? `${p.waypoint ?? `${Math.round(p.distanceNm)} NM`} · ${utc(p.validTime)}`
    : t('mcpApp.tapToSelect');
}

function redraw(): void {
  if (!view) return;
  view.draw();
  if (view.viz) interaction?.update(view.viz);
  updateInfo();
}

function onChange(): void {
  redraw();
  scheduleContext();
}

function buildToolbar(): void {
  const v = view!;
  toolbar.innerHTML = '';

  const modelSelect = document.createElement('select');
  modelSelect.setAttribute('aria-label', t('mcpApp.model'));
  for (const m of v.models) modelSelect.add(new Option(m.toUpperCase(), m, false, m === v.model));
  modelSelect.addEventListener('change', () => switchModel(modelSelect.value, modelSelect));
  toolbar.append(modelSelect);

  const layersBtn = document.createElement('button');
  layersBtn.type = 'button';
  layersBtn.textContent = t('mcpApp.layers');
  layersBtn.setAttribute('aria-expanded', 'false');
  const panel = document.getElementById('xs-layers')!;
  layersBtn.addEventListener('click', () => {
    panel.hidden = !panel.hidden;
    layersBtn.setAttribute('aria-expanded', String(!panel.hidden));
  });
  toolbar.append(layersBtn);
  buildLayerPanel(panel);

  toolbar.append(info);

  const spacer = document.createElement('span');
  spacer.className = 'xs-spacer';
  toolbar.append(spacer);

  if (params?.web_url) {
    const open = document.createElement('button');
    open.type = 'button';
    open.textContent = t('mcpApp.openBriefing');
    open.addEventListener('click', () => {
      app.openLink({ url: params!.web_url }).catch((err: Error) => console.warn('openLink failed', err));
    });
    toolbar.append(open);
  }

  const fs = document.createElement('button');
  fs.type = 'button';
  fs.id = 'xs-fullscreen';
  fs.addEventListener('click', async () => {
    const mode = app.getHostContext()?.displayMode === 'fullscreen' ? 'inline' : 'fullscreen';
    try {
      const res = await app.requestDisplayMode({ mode });
      applyHostContext({ ...app.getHostContext(), displayMode: res.mode });
    } catch (err) {
      console.warn('requestDisplayMode failed', err);
    }
  });
  toolbar.append(fs);
}

function buildLayerPanel(panel: HTMLElement): void {
  panel.innerHTML = '';
  for (const group of panelGroups()) {
    const fieldset = document.createElement('fieldset');
    const legend = document.createElement('legend');
    legend.textContent = group.label;
    fieldset.append(legend);
    for (const layer of group.layers) {
      const label = document.createElement('label');
      const box = document.createElement('input');
      box.type = 'checkbox';
      box.checked = !!view!.enabled[layer.id];
      box.addEventListener('change', () => {
        view!.setLayer(layer.id, box.checked);
        onChange();
      });
      label.append(box, ` ${t(`viz.layer.${layer.id}`)}`);
      fieldset.append(label);
    }
    panel.append(fieldset);
  }
}

async function switchModel(model: string, select: HTMLSelectElement): Promise<void> {
  if (!view || !loader || model === view.model) return;
  select.disabled = true;
  showStatus(t('mcpApp.loading'));
  try {
    const { model: got, data } = await loader.load(model);
    view.setData(data);
    view.setModel(got);
    showStatus(null);
    onChange();
  } catch (err) {
    select.value = view.model;
    showStatus((err as Error).message);
  } finally {
    select.disabled = false;
  }
}

async function start(result: ToolCallResult): Promise<void> {
  if (started) return;
  started = true;
  try {
    params = parseViewParams(result);
  } catch (err) {
    showStatus((err as Error).message);
    return;
  }
  await i18nReady;
  showStatus(t('mcpApp.loading'));
  loader = new XsectionLoader((p) => app.callServerTool(p) as Promise<ToolCallResult>, params.flight_id, params.pack_timestamp);
  try {
    const { model, data } = await loader.load(params.model);
    const renderer = new CrossSectionRenderer(chart);
    view = new XsectionView(renderer, {
      model,
      enabled: layersWithExtra(params.layers),
      point: params.point_index ?? -1,
    });
    view.setData(data);
    view.setModel(model);
    buildToolbar();
    applyHostContext(app.getHostContext());
    view.draw();
    interaction = attachInteraction(renderer, view.viz!, {
      onSelectPoint: (index) => {
        view!.setPoint(index);
        onChange();
      },
    });
    showStatus(null);
    updateInfo();
    scheduleContext();
  } catch (err) {
    showStatus((err as Error).message);
  }
}

// Handlers before connect(): the host may send these right after the handshake.
app.ontoolresult = (result) => { void start(result as ToolCallResult); };
app.ontoolcancelled = () => { if (!started) showStatus(t('mcpApp.cancelled')); };
app.onhostcontextchanged = (ctx) => applyHostContext({ ...app.getHostContext(), ...ctx });

app.connect()
  .then(async () => {
    const ctx = app.getHostContext();
    await initI18n(ctx?.locale);
    i18nDone();
    applyHostContext(ctx);
    if (!started) showStatus(t('mcpApp.loading'));
  })
  .catch((err: Error) => {
    showStatus(`Could not connect to the chat host: ${err.message}`);
    void initI18n().then(i18nDone);
  });
