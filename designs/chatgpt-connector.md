# ChatGPT connector (Custom GPT + OpenAPI Action)

> Native ChatGPT support as the sibling of the Claude MCP connector — same seven
> capabilities, one shared core, two front-doors.

## Why two front-doors

The Claude integration is an **MCP server** (`weatherbrief.mcp.server`, deployed at
`mcp.flyfun.aero/weather`, OAuth 2.1 + DCR). ChatGPT's cleanest path for a
shareable, end-user-facing integration is a **Custom GPT** with an **OpenAPI
Action**: ChatGPT calls a small REST surface directly (server-to-server) and the
builder is configured with a pre-registered OAuth client.

Rather than re-implement the meteorology (all of which is upstream in the
analysis pipeline and served as raw JSON by the REST API), both front-doors are
thin and **share their response shaping + meteorological guardrails**, so Claude
and ChatGPT cannot drift.

```
              ┌──────────────────────────┐
  Claude  ──▶ │ weatherbrief.mcp.server  │ ─┐
   (MCP)      └──────────────────────────┘  │   shared shaping + guardrails
                                            ├─▶ weatherbrief.connectors.views
  ChatGPT ──▶ │ weatherbrief.api.agent    │ ─┘   (summarize_advisories,
 (OpenAPI)    └──────────────────────────┘        convective_detail, advisory_detail,
                                                   summarize_altitude_table,
              both reuse upstream logic            briefing_freshness_status, …)
              (analysis pipeline, REST handlers)
```

## Modules

- **`weatherbrief/connectors/views.py`** `[project]` — pure `dict → dict` shapers,
  the only logic shared between the two connectors. Carries the guardrails:
  cross-check is *context, not a downgrade signal* (#178); the convective
  provenance note (parcel-derived "tops" vs the model's own convective cover).
  Key exports: `summarize_advisories`, `summarize_altitude_table`,
  `advisory_detail`, `convective_detail`, `briefing_freshness_status`,
  `CROSS_CHECK_NOTE`, `CONVECTIVE_NOTE`.

- **`weatherbrief/api/agent.py`** `[project]` — the ChatGPT front-door router,
  mounted at `/agent/v1`. **In-process reuse, no localhost loopback:**
  - *Read* endpoints reuse the same helpers the main REST handlers use
    (`_get_pack_dir`, `list_packs`, `_build_data_status`, `decide_refresh`) and
    read the pack JSON artifacts directly, then apply the shared shapers.
  - *Write* endpoints (`createFlight`, `refreshBriefing`) call the existing
    route handlers directly with **every dependency passed explicitly** — reusing
    all the throttling / gating / background-task machinery with zero
    duplication. (Passing every `Depends`/`Query` param avoids the sentinel
    footgun called out in CLAUDE.md.)

- **`weatherbrief/api/app.py`** — registers `agent_router` (own `/agent/v1`
  prefix, not `/api`) and serves an **isolated OpenAPI** at
  `/agent/v1/openapi.json`: only the seven operations + the OAuth2
  authorization-code security scheme. That tiny document is the artifact pasted
  into the Custom GPT builder, decoupled from the app's large internal API.

## Endpoint ↔ MCP tool parity

| OpenAPI operation (`operation_id`) | Method + path | MCP tool |
|---|---|---|
| `listFlights` | `GET /agent/v1/flights` | `list_flights` |
| `createFlight` | `POST /agent/v1/flights` | `create_flight` |
| `getBriefing` | `GET /agent/v1/flights/{id}/briefing` | `get_briefing` |
| `refreshBriefing` | `POST /agent/v1/flights/{id}/briefing/refresh` | `refresh_briefing` |
| `getAdvisoryDetail` | `GET /agent/v1/flights/{id}/advisories/{advisory_id}` | `get_advisory_detail` |
| `getDigestContext` | `GET /agent/v1/flights/{id}/digest-context` | `get_digest_context` |
| `getAirportWeather` | `GET /agent/v1/airport-weather` | `get_airport_weather` |

The operation **descriptions** (docstrings) mirror the MCP tool docstrings,
including the "drill in before answering / cross-check is not a downgrade signal"
guidance, so the GPT behaves like the Claude connector.

> Note: `getBriefing` reads the **cached** `altitude_table.json` (the cheap GET
> path persisted at refresh, #259) rather than recomputing the sweep; it is
> omitted for packs that predate that artifact.

## Authentication

Reuses the existing OAuth stack (`flyfun_common.oauth`) and the broad **`mcp`
scope** — no new scope, same consent screen as the Claude connector. Endpoints
under `/agent/v1` are not in any `register_scope_paths` allowlist, so `mcp`-scoped
(and unscoped) tokens reach them; a `flights:read`-only token does not.

Unlike the MCP connector (which uses Dynamic Client Registration), **Custom GPT
Actions require a pre-registered confidential client** (you type `client_id` +
`client_secret` into the builder). The AS already supports this — provision the
client through the existing **DCR endpoint** so the secret is hashed the way the
token endpoint verifies it:

OAuth endpoints (from `deploy/weather.flyfun.aero.caddy`):
- authorize: `https://weather.flyfun.aero/oauth/authorize`
- token: `https://weather.flyfun.aero/oauth/token` (`client_secret_post`)
- register (DCR): `https://weather.flyfun.aero/oauth/register`

### Setup steps

1. In ChatGPT (Pro/Team/Enterprise/Edu) **create the Custom GPT**, add an
   **Action**, and import the schema from `https://weather.flyfun.aero/agent/v1/openapi.json`.
2. Set Authentication = **OAuth**, scope `mcp`, token exchange = **POST**.
   Authorization URL / Token URL as above. Save the GPT — ChatGPT now shows its
   **callback URL**: `https://chatgpt.com/aip/g-<gpt-id>/oauth/callback`.
3. **Provision the client** with that callback as the redirect URI, e.g.:
   ```bash
   curl -sX POST https://weather.flyfun.aero/oauth/register \
     -H 'Content-Type: application/json' \
     -d '{"client_name":"FlyFun Weather GPT",
          "redirect_uris":["https://chatgpt.com/aip/g-<gpt-id>/oauth/callback"]}'
   ```
   The response returns `client_id` + `client_secret` (shown once).
4. Paste `client_id` + `client_secret` into the GPT builder's OAuth panel.
5. Open the GPT, run a tool → complete the FlyFun login + `mcp` consent → tools work.

**No CORS change is required**: Actions are called server-to-server by OpenAI, not
from a browser (unlike the Claude MCP connector, whose browser handshake needs the
scoped `claude.ai`/`claude.com` CORS allowlist).

## Testing

- `tests/test_connectors_views.py` — the shared shapers (pure; runnable without
  the heavy deps).
- The MCP suite (`tests/test_mcp_advisory_detail.py`) still exercises the same
  shapers via `weatherbrief.mcp.server` aliases — proof the two front-doors share
  one implementation.
