# Briefing surface charts + GRAMET on iOS (#799)

> **Status: shipped** (server + iOS in one PR). The web's Discussion extras —
> DWD / Met Office / Météo-France TEMSI surface charts and GRAMET — on the iOS
> briefing **Discussion** tab. Chart caches and sources: `synoptic-charts.md`.

## Shape

Discussion tab order: **Synoptic Overview → Charts → rest of the digest
(Specific Concerns, Trend, Watch Items) → GRAMET**. The charts illustrate the
synoptic narrative, so they sit right under it; the GRAMET is a reference
image and closes the tab (owner's call after testing on device). The GRAMET
section and its pill appear only when the pack's `has_gramet` is true — no
"not available" placeholder, since most profiles never have one. Charts and GRAMET come from the pack, not the digest, so they render
while the digest is generating, failed, or was never requested
(`DiscussionTabView`; the digest state is an inline row, not a full-page
placeholder).

| Piece | Where |
|---|---|
| Manifest builder + models | `api/_chart_manifest.py` (`build_pack_charts_manifest`, `PackChartsManifest`) |
| On pack meta | `PackMetaResponse.charts`, built in `packs._meta_to_response` |
| Downscaled DWD analysis | `dwd_charts.downscale_analysis` / `resolve_small_analysis_path`; `GET …/dwd-chart/ana?w=1600` |
| GRAMET PNG cache | `GET …/gramet.png` writes `gramet.png` into the pack dir on first render |
| iOS DTO | `Models/API/PackChartsManifest.swift` |
| iOS cache | `BriefingCacheStore` § Shared chart cache; `CachingBriefingRepository` § Charts + GRAMET |
| iOS views | `SurfaceChartsView`, `ChartRouteOverlay` (Canvas), `GrametSectionView` (PDFKit), `Shared/ZoomableScrollView` |

## Decisions

1. **Server-built manifest, not a Swift twin of the web's rules.** iOS renders
   `charts.sources[].charts[]` as given: label, caption, valid/init time, URL,
   overlay key, cache key, `available`, `prefetch`. Gating is the web's
   (`renderDwdCharts`): coverage + horizon + run cycle per source; TEMSI only
   live (non-evicted) options — the manifest reuses `_live_meteofrance_options`.
   The web still reads the flat `*_charts_*` fields; moving it onto the manifest
   is optional later. TEMSI label/offset helpers are ports of `web/ts/utils/temsi.ts`
   — change both together.
2. **Met Office on iOS only with `METOFFICE_CHARTS_PUBLIC`.** The manifest is the
   same for every caller (it is built in `_meta_to_response`, which has no
   request), so admins don't get Met Office on iOS. Accepted in the issue.
3. **Every DWD / Met Office chart id is listed, `available` or not.** A chart the
   server evicted shows "Chart no longer available (valid …)", never vanishes.
4. **Cache keys carry a version token** — `<source>/<run_cycle>/<stem>-<token>`,
   token from the chart's own `meta.json` (`last_modified`, TEMSI `date_run`,
   else `etag`). The issue proposed `<source>/<run_cycle>/<chart_id>`, but the
   bytes under that are *not* immutable: a DWD cycle is named by the **analysis**,
   and its forecast charts are replaced in place when the next ICON run lands
   under the same cycle (`synoptic-charts.md` § DWD forecast chart times); TEMSI
   re-fetches a validity when AEROWEB re-issues it. With the token, the client
   can treat a key as immutable. Cost: a chart whose meta is gone (evicted cycle)
   gets a token-less key, but it is also `available: false` then.
5. **Downscaled DWD analysis** (`ana_w1600.png`, 1600 px wide, 256-colour
   palette): written at refresh beside `ana.png`, created lazily on first request
   for older cycles. Same aspect ratio, so the overlay (served in native 4389×3114
   pixels) maps by one width ratio — iOS simply scales overlay coordinates to the
   size the image is drawn at. Only `?w=1600` on `ana` is accepted (400 otherwise).
6. **GRAMET is the PDF on iOS** (`GET …/gramet`, PDFKit: vector zoom). Legacy
   packs that only have a PNG get the PNG (sniffed by `%PDF` magic). Separately
   the web's `gramet.png` is rendered once and cached in the pack dir; retention
   already lists `gramet.png`.

## iOS cache

- **Shared chart cache** under the user scope: `charts/<cacheKey>.<fileExt>`.
  A hit is never revalidated; a second leg or a refresh on the same run never
  re-downloads (unit-tested in `ChartCacheTests`). `flightDirectoryIDs` skips
  `charts/`. Cache paths are server strings, so `..`/empty components are refused.
  Only bytes that start like an image (PNG/GIF/JPEG, `PackChart.looksLikeImage`)
  are written — an error page behind a 200 under an immutable key would be
  served forever. GRAMET likewise caches only `%PDF` or image bytes.
- **Per pack**: GRAMET (`gramet.pdf`/`.png`) and the route overlays
  (`chart-overlay-<source>.json`) — both route-specific. A downloaded pack also
  keeps its own `pack-meta.json`, because the flight-level `pack-meta` only
  holds the last download and the sweep needs every downloaded pack's manifest.
- **Eviction** (`pruneStalePacks`, after pack pruning): mark-and-sweep keeps
  charts referenced by any indexed pack's `pack-meta.json` or any flight dir's
  `latest-pack` / `pack-meta` / `packs` sidecar (so packs viewed but never
  downloaded keep theirs until their flight ages out), then a **50 MB** cap
  removes least-recently-read files (reads touch mtime), both from one walk of
  the directory (`BriefingCacheStore.pruneCharts`). If any of those sidecars
  exists but won't decode, the sweep is **skipped** (logged) — it can't tell
  which charts that pack needs — and only the cap runs. Chart bytes are not
  counted in `CachedPackEntry.totalBytes`.
- **Offline download** prefetches, after the bundle and after registering the
  pack, the charts flagged `prefetch` + their sources' overlays + GRAMET. Never in
  `requiredEndpoints`; every failure (410, network) is logged and skipped.
  Prefetch subset (server-picked, `_mark_prefetch`): DWD analysis + the two
  forecasts bracketing the ETD; Met Office the two nearest the ETD; TEMSI all
  options. Without an ETD (no flight at hand) the source default stands in.
  Everything else is fetched lazily on view and written to the cache.

## Gotchas

- `_meta_to_response` builds the manifest for every pack, pack lists included
  (iOS switches to a history entry as is). Each DWD / Met Office cycle is read
  once per call via `ChartCache.cycle_snapshot` (one directory listing + one
  `meta.json`; an evicted cycle is one failed listing), and `list_flight_packs`
  shares a `cycle_memo` across its packs so a run cycle is read once per request.
- The lazy `?w=1600` downscale for older cycles runs under a process lock, so
  concurrent first requests don't each run the LANCZOS pass.
- Python ports of `temsiTabLabel` / `temsiValidityOffset` live in
  `_chart_manifest.py`; `web/ts/utils/temsi.ts` carries the reciprocal SYNC note.
- Manifest DTOs decode leniently on iOS (missing `in_coverage`/`sources`/
  `available`/`prefetch`/`caption`/`overlay_key` default) so a manifest shape
  change can't fail the whole `PackMetaResponse` decode.
- `departure_time` passed to `_meta_to_response` is the flight's **current**
  ETD, not the one the pack was built for; only captions and prefetch use it.
- The route overlay is drawn by `ChartRouteOverlay` with sizes relative to the
  drawn width (port of `renderRouteSvg`); the zoom viewer lays the chart out at
  full pixel size and scales it, so the route zooms with the image.
- Repository protocol: `chartImage`, `chartOverlay`, `grametDocument` have
  protocol-extension defaults that throw `notFound`, so fixtures/mocks needn't
  implement them; the online and caching repositories do.
- Out of scope: Hewson front polylines on the iOS route map; admin-only DWD
  German text overview.
