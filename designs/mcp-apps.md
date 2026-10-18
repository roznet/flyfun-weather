# MCP Apps: the in-chat cross-section widget

> The FlyFun Weather MCP server's first MCP App (#819): the interactive route
> cross-section drawn inside a Claude or ChatGPT chat. Scope is the
> cross-section only; the route map, briefing card and live view may reuse
> this plumbing later.

Spec: <https://modelcontextprotocol.io/extensions/apps/overview> (wire spec
`ext-apps/specification/2026-01-26/apps.mdx`). A tool declares
`_meta.ui.resourceUri: "ui://…"`; the host reads that resource (one
self-contained HTML document, `text/html;profile=mcp-app`) and renders it in a
sandboxed cross-origin iframe that talks to the host over postMessage JSON-RPC
(`tools/call` to our server only, `ui/update-model-context`, `ui/open-link`,
`ui/request-display-mode`). Hosts without Apps support show the text result.

## The split: one model tool, one app-only tool, one resource

All in `src/weatherbrief/mcp/server.py`:

| Piece | Who sees it | Carries |
|---|---|---|
| `show_cross_section(flight_id, model?, layers?, point_index?, pack_timestamp?)` | the model (and the host) | text paragraph for the model; `structuredContent` = **view parameters only** (`flight_id`, `pack_timestamp`, `model`, `layers`, `point_index`, `web_url`); `_meta.ui.resourceUri` |
| `xsection_data(flight_id, pack_timestamp, model?)` | the widget only (`visibility: ["app"]`) | the pack data, one model's slice, in `structuredContent`; a one-line text |
| resource `ui://weatherbrief/cross-section.html` | the host | the built widget HTML |

**Why a separate tool, not `_meta.ui` on `get_briefing`:** `get_briefing`'s text
contract serves every host and is called often; a dedicated tool also lets the
model pick the starting view for the question (an icing question → icing layer).
The instructions and `get_briefing`'s docstring say when to call it.

**Why no pack data in `show_cross_section`'s result:** the spec says
`structuredContent` isn't added to model context, but ChatGPT's Apps SDK does
surface it to the model. Pack data there would break #278 (never expose
cross-section / large viz payloads to the model). The data rides only the
app-only tool. `visibility: ["app"]` is audience metadata, not access control:
fastmcp still lists the tool in `tools/list` and the **host** keeps it from the
model; direct calls go through the same bearer-token auth as every tool.

**Errors:** `show_cross_section` returns the same shapes as `get_briefing`
(`_error_result`, or `status: processing|none` from `_resolve_pack`), in both
text and `structuredContent`, with no data; the widget shows the message.

## Payload size

Dev data (141 packs, 60 days): `route_analyses.json` median 1.1 MB, p90 2.9 MB,
max 3.9 MB; the snapshot (`briefing.json`) about the same; elevation ≤ 140 KB.
Host limits on tool results through the bridge are undocumented.

- `connectors/views.py::slice_route_analyses(manifest, model)`: per point, keep
  only `model` in `sounding` (~80% of a point), `wind_components`,
  `cruise_temperature_c` and each `model_divergence[].model_values`
  (`agreement` stays: the renderer reads the worst agreement across models);
  drop `altitude_advisories` (the cross-section never reads it, only the
  briefing page's point panel). `models` stays whole for the model switch. On
  the fixture the slice is < 60% of the input; on real multi-model packs the
  sounding share makes it far smaller.
- `slice_snapshot`: only `route_observations`, `route_sigmets`,
  `observed_conditions` (`XSECTION_SNAPSHOT_KEYS`, what `draw()` reads).
- Changing model calls `xsection_data` again; the widget caches per model
  (`mcp-app/loader.ts`).
- One INFO line per call: `xsection_data flight=… model=… bytes=…` (the JSON
  size of the payload), so prod tells us the real numbers.
- **Memory (MCP container limit 256M):** artifacts are fetched one at a time and
  sliced at once (`del manifest` before the snapshot), the slicer is shallow
  (shares untouched sub-objects), and the payload is returned as
  `ToolResult(structured_content=…)` with a one-line text, not fastmcp's default
  of a second full JSON copy in `content`.

**Fallback if a host chokes (not built):** a short-lived, flight-scoped signed
URL the widget fetches directly, plus `csp.connectDomains:
["https://weather.flyfun.aero"]` on the resource. Build it only if the byte log
or a host failure says so.

## The widget (web)

- `web/ts/embed/xsection-view.ts`: `XsectionView` holds model / layers / point
  and `draw()` (extractVizData → renderer), shared with `embed-main.ts` (REST
  loader + `wb-embed:*` protocol, unchanged). One draw path, two loaders.
- `web/ts/mcp-app/cross-section-main.ts`: `App` from
  `@modelcontextprotocol/ext-apps` (pinned exact, 2.0.3). Handlers are set
  before `connect()` (one-shot events can fire right after the handshake).
  Reads view params from `ontoolresult`, loads via `callServerTool('xsection_data')`,
  draws, attaches the briefing page's `attachInteraction` (tap/click selects a
  point, hover tooltip). Toolbar: model select, a Layers panel (the briefing's
  `getLayerGroups()` minus `highlight`, no advisories here), selected-point
  label, "Open full briefing" (`openLink(web_url)`), Fullscreen (only when the
  host's `availableDisplayModes` has it). Inline chart 320 px, capped by the
  host's `containerDimensions.maxHeight`; `autoResize` reports the size.
- **Layers:** `show_cross_section(layers=…)` switches layers on **on top of the
  briefing defaults** (`layersWithExtra`), unlike the embed's exact list, so the
  model can't hand the pilot a bare chart. Ids are validated against
  `XSECTION_LAYER_IDS` (a test checks each still exists in the web registry);
  unknown ids are dropped and named in the text.
- **Model context** (`mcp-app/model-context.ts`): on any model/layer/point
  change, debounced 500 ms, `updateModelContext` with a short plain-text block:
  route, briefing time, model shown (+ the others in the pack), layer names on,
  selected point (index, waypoint or NM along, planned time, model forecast
  hour). No arrays, no soundings. Each call replaces the previous (spec
  semantics). It ends by saying the widget shows the briefing data and never
  re-grades.
- **Host context:** `theme` dark → cross-section `standard` theme, light →
  `light`, plus `data-theme` on `<html>` for the toolbar CSS; `locale` (BCP 47)
  → `initI18n(locale)`; later `host-context-changed` re-applies.

### Build

`web/scripts/build-mcp-app.mjs` (esbuild JS API, `write: false`, IIFE, minified,
no splitting so the locale JSON is inlined) writes **one** file,
`web/dist/mcp-cross-section.html`, from `web/mcp-app/cross-section.html` +
`cross-section.css`, inlining JS (with `</script` escaped) and CSS.
`npm run build` / `npm run dev` include it (`build:mcp-app`, `dev:mcp-app`
watches the TS graph, the template and the CSS). `web/dist/` is gitignored like
the other bundles. The Docker image builds it in the node stage and the MCP
container (same image) reads it.

Size: ~1 MB (zod pulled in by the ext-apps SDK ~440 KB, the four locale files
~290 KB, the cross-section ~80 KB). Sent once per widget render; the server
reads the file once per process (`_widget_html`, a stub HTML when missing so
the tool still works in an unbuilt test env, not cached).

### Sandbox gotchas

- `localStorage` throws in a sandbox without `allow-same-origin`:
  `i18n.ts` (`detectLocale`, `setLocale`) now guards it; `units.ts` already did.
  Anything new on the widget's import graph must not touch storage unguarded.
- No `/css/style.css`: the widget CSS copies the colour tokens and the
  `.viz-tooltip` rules it needs. A new cross-section class that relies on
  style.css will be unstyled in the widget.
- No external fonts, scripts or connections: CSP stays default
  (`connect-src 'none'`), so the resource declares no `csp`.
- **No `domain`:** Claude derives a per-connector sandbox origin
  (`{sha256(connector URL)[:32]}.claudemcpcontent.com`); declaring one is only
  needed for origin-allowlisted APIs or the app's own OAuth, must match the exact
  connector URL, and we have two (`mcp.flyfun.aero/weather`,
  `weather.flyfun.aero/mcp`). A wrong value stops the widget rendering.
- zod under the default CSP: the ext-apps `App` sets zod `jitless` itself.

## Debugging

- `fastmcp dev apps <spec>` (needs `fastmcp[apps]`) starts the server and a
  local AppBridge host. Point the real server at a devserver with
  `WEATHERBRIEF_API_URL` + `WEATHERBRIEF_TOKEN` (stdio/env-token path in
  `_extract_token`). Its UI loads the MCP SDK from esm.sh, so it fails in a
  sandbox whose proxy blocks that host.
- Without it: a scratch page hosting `AppBridge` from
  `@modelcontextprotocol/ext-apps/app-bridge` in a `sandbox="allow-scripts"`
  iframe, answering `oncalltool` from the Python tool functions. Connect the
  bridge **before** loading the iframe's document, or the widget's
  `ui/initialize` is lost.
- Prod: grep the MCP container log for `xsection_data` to see payload bytes.

## Tests

- `tests/test_mcp_cross_section.py`: tool meta (`resourceUri`, app-only
  visibility), resource mime/meta and stub, view-params-only result (no pack
  data in text or `structuredContent`), error shapes, per-model slicing + byte
  log, layer ids exist in the web registry.
- `web/tests/unit/xsection-view.test.ts` (shared view state),
  `web/tests/unit/mcp-app.test.ts` (model-context text, view-param parse,
  loader with a mocked `callServerTool`).
