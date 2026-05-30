# Phase Roadmap (historical)

> Development changelog of completed implementation phases, extracted from `architecture.md` (it is history, not design context or live planning). Live/known-issue items live in `designs/future/`.

## Phases

| Phase | Status | Summary |
|-------|--------|---------|
| 1 | Done | Open-Meteo fetch, wind/icing/cloud analysis, JSON snapshots, text digest |
| 2 | Done | Route rework (YAML, per-waypoint track), GRAMET, Skew-T plots |
| 3 | Done | DWD text forecasts, LLM digest (LangGraph + structured output) |
| 4a | Done | MetPy sounding analysis: thermodynamic indices, enhanced clouds/icing/convective, altitude band comparison |
| 4b | Done | Vertical motion + CAT turbulence: omega profiles, Richardson number, Brunt-Vaisala frequency |
| 4c | Planned | Ensemble & remaining model comparison refinement |
| 5 | Done | Web UI, API, PDF report, email delivery |
| 6.1 | Done | Docker + DB + Deploy: SQLAlchemy storage, Alembic migrations, Docker packaging |
| 6.2 | Done | Auth: Google OAuth, JWT sessions, user-scoped data, approval workflow |
| 6.3 | Done | Preferences: per-user settings, Fernet-encrypted autorouter credentials |
| 6.4 | Done | Usage tracking, daily rate limits, admin page, shareable briefing links |
| 7.1 | Done | Interactive cross-section visualization: canvas renderer, 8 layer types, hover/click interaction |
| 7.2 | Done | SRTM terrain elevation profile along route (90m resolution, 0.5nm spacing) |
| 7.3 | Done | Model freshness checking: smart refresh skips unchanged models |
| 7.4 | Done | Enhanced Skew-T: CAPE/CIN shading, hodograph, indices panel, overlays |
| 7.5 | Done | Metric explanations UI: catalog-driven info popups, tiered display, threshold scales |
| 7.6 | Done | Inversion detection, Ogimet icing index, GRAMET PDF, convective risk visualization |
| 7.7 | Done | Legacy routes.yaml removal, collapsible sections, admin force refresh |
| 7.8 | Done | NWP cloud bands, terrain draw-order fix, layer legends, "Discuss with AI" buttons |
| 8.1 | Done | Route advisory system: 13 evaluators, registry, user-tunable parameters, recalculation, frontend dashboard |
| 8.2 | Done | Extended pressure levels (25 for GFS/ECMWF) + GRIB2 enrichment (CLWMR/ICMR from GFS S3), LWC-based icing |
| 9.1 | Done | Flight parameter profiles: named templates for altitude/models/advisories, profile CRUD API, settings UI |
| 9.2 | Done | Unified atmospheric profile table, icing severity toggle, Windy meteogram links |
| 10.1 | Done | Route graph: canvas chart below cross-section for scalar metrics (wind, temp, precip, CAPE, freezing level) |
| 10.2 | Done | Route-aware text forecasts: NWS AFD (US) and DWD (Europe) integrated into LLM digest |
| 10.3 | Done | METAR/TAF route weather: D-0 observations, obs-vs-model comparison, TAF highlighting, wind advisories |
| 10.4 | Done | API token authentication for bot/agent users (admin-managed, `wb_` prefix, SHA-256 hashed) |
| 11.1 | Done | Cost attribution: per-briefing cost computation, credit balance, auto-reload, ledger, admin config, transparency endpoint |
| 11.2 | Done | User feedback: submission with categories, admin email notifications, admin feedback listing |
| 11.3 | Done | Refresh registry: prevent duplicate refreshes, per-flight status tracking, SSE progress streaming |
| 11.4 | Done | Route map visualization: Leaflet geographic view with 14-metric coloring, altitude slider, hover sync |
| 11.5 | Done | Admin user costs page: per-user cost attribution dashboard, cost distribution chart, transaction ledger |
| 11.6 | Done | UX: centralized navigation banner, consistent nav across all pages |
| 11.7 | Done | First-login workflow: welcome wizard (intro, aircraft defaults, guided tour), setup_completed tracking |
| 11.8 | Done | Dark/light/system theme: CSS custom properties, FOUC prevention, theme-aware canvases, map tile switching, image inversion |
| 11.9 | Done | Auto-refresh scheduler: background polling, freshness check, pre-flight lead time, email notification |
| 11.10 | Done | Flight privacy: private flag hides flights from shared briefing links |
| 11.11 | Done | Compact/full display mode: compact hides sounding analysis, model comparison, secondary advisories |
| 12.1 | Done | Cross-section theme system: switchable themes (standard, high-contrast), theme preview, cloud hatch patterns, theme-aware legends |
| 12.2 | Done | Auth consolidation: OAuth, JWT, DB engine, encryption moved to flyfun-common; cross-subdomain SSO via `flyfun_auth` cookie |
| 12.3 | Done | Cost unification: drop credits abstraction, USD everywhere, shared cost_ledger via flyfun-common |
| 12.4 | Done | Admin hub: cross-app Systems tab via flyfun-common's create_hub_router |
| 12.5 | Done | Feedback workflow: status tracking (pending/ready/replied/ignored), AI triage, admin reply email |
| 12.6 | Done | Security hardening: HMAC pack integrity, audit logging (admin actions, pack access) |
| 12.7 | Done | Account deletion: cascade delete flights/profiles/artifacts, auth callback |
| 12.8 | Done | Email: Resend API provider with SMTP fallback |
| 12.9 | Done | Convective advisory altitude-awareness, ceiling route graph metrics, route map width variation |
| 12.10 | Done | GRIB2 memory optimization: two-phase sequential decode, chunked ICON-EU processing |
| 12.11 | Done | Memory hardening for long routes: per-fhour cleanup in all GRIB enrichment loops, post-advisory release of `cross_sections` in `pipeline.py` (already on disk, not needed downstream), container limit raised to 3 GB. Per-request memory curve logged at end of `execute_briefing()` (`Memory curve: start=… fetch=… analyze=… advisories=… post_clear=… end=… MB; peak=… (+N this request)`) for ongoing observability |
