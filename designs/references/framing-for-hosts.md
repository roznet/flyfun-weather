# Framing flyfun-weather in another page (hosts guide)

For a page **outside** this repo that shows live weather.flyfun.aero views of a
real flight in an `<iframe>`: the talks site (`~/Developer/private/flyfun-talks`),
help pages, anything similar. This is the contract such a host can rely on: what
it can frame, which URL parameters and `postMessage`s it can use, and the traps.
Implementation detail lives in `briefing-sidebar.md` (focus mode, frame protocol)
and `visualization.md` (deep links, embed page). This doc is not in `INDEX.md`
on purpose: it is addressed to the host's agent, not to work on this repo.

"Framed" changes nothing about what is drawn: the iframe shows the production
page exactly as a signed-in user would see it at that URL (same data, same
styling, nothing highlighted). The parameters only choose **what** is shown.

## Two surfaces

| | `/embed.html` | `/briefing.html` + `section=` |
|---|---|---|
| Shows | the cross-section only | any one briefing section |
| Look | full-bleed, no app chrome | add `chrome=none` for full-bleed |
| Drive it live | layers, route point, model | which section is shown |
| Weight | light, loads only cross-section data | the whole briefing page |

Use `/embed.html` to build a cross-section step by step (layer by layer, move
the point). Use the briefing for everything else (METAR/TAF, SIGMETs, Skew-T,
GRAMET, alternates…), or for a cross-section exactly as the briefing shows it
(with its toolbar and the route map).

## `/embed.html` (cross-section)

```
/embed.html?flight=<id>[&pack=<ts>][&model=<m>][&layers=a,b,c][&point=<i>][&theme=<id>]
```

- `pack` — a pack `fetch_timestamp`. Omit it for the latest pack. Pin it for a
  talk that must keep showing the same forecast.
- `model` — e.g. `ecmwf`, `gfs`, `icon`. Unknown or absent → ecmwf, else gfs, else the first one.
- `layers` — **exact**: only these layers on. Absent → briefing defaults. The
  valid ids come back in `wb-embed:ready` (below). Don't hard-code a list.
- `point` — route-point index to mark. `-1` or absent → none.
- `theme` — `standard`, `high-contrast`, `gramet`, `light`.

Messages (host → embed, honoured only from `window.parent`; each redraws):

| Message | Effect |
|---|---|
| `{type: 'wb-embed:layers', layers: ['clouds', …]}` | exactly these layers on (`layers: null` → defaults) |
| `{type: 'wb-embed:point', index: 7}` | mark that route point (`-1` → none) |
| `{type: 'wb-embed:model', model: 'icon'}` | switch model (ignored if the pack lacks it) |

Embed → host:

| Message | When |
|---|---|
| `{type: 'wb-embed:ready', models, model, layers}` | data drawn. `models` = the pack's models, `model` = the selected one, `layers` = every layer id |
| `{type: 'wb-embed:error', message}` | load failed (no flight, no route analyses for the pack…) |
| `{type: 'wb-frame:auth-required'}` | a 401, see Auth |

Wait for `wb-embed:ready` before sending messages.

## `/briefing.html` focused on one section

```
/briefing.html?flight=<id>[&pack=<ts>]&section=<key>[&chrome=none][&model=<m>][&point=<i>][&view=…][&preset=<id>|&advisory=<id>]
```

- `section` — opens with only that section in the main pane. Nothing depends on
  scrolling, so content that loads later can't push it away. A collapsed section
  is expanded. Unknown keys are ignored (the full briefing shows). Keys:

  | Group | Keys |
  |---|---|
  | Explore | `cross-section` (cross-section + map), `skewt` |
  | Observations | `observed-glance`, `observed-ribbon`, `observed` (radar & lightning), `observations` (METAR/TAF), `sigmets`, `pireps`, `alternates` |
  | Discussion | `synopsis` (the AI digest), `dwd-charts`, `dwd-overview`, `gramet` |
  | Detail | `sounding`, `comparison` |
  | Not in the nav | `advisories` |

- `chrome=none` — hides the site header, the left rail (briefing header,
  freshness, controls) and the resizer; the section takes the full width, like
  an embed. The stale-data and error banners still show (on purpose). Without
  the rail there is no "Exit focus" button; the host drives focus by message.
- `model`, `point` (the analysis `point_index`), `preset` / `advisory` (the
  cross-section lens: `preset` ids are the keys of `ADVISORY_PRESETS`, e.g. `basic`,
  `icing`, `clouds`; `advisory` is an advisory id, mapped by `ADVISORY_TO_PRESET`;
  both in `web/ts/visualization/cross-section/advisory-presets.ts`) — the
  same deep link the MCP builds (`visualization.md`, deep-link).
- `view=skewt|skewt-compare|skewt-static` — with `section=skewt`, picks the Skew-T mode.

Messages:

| Message | Direction | Effect |
|---|---|---|
| `{type: 'wb-frame:section', section: '<key>'}` | host → briefing | show that section instead, no reload |
| `{type: 'wb-frame:section', section: null}` | host → briefing | back to the full briefing |
| `{type: 'wb-frame:ready'}` | briefing → host | signed in, a real page is up (not a login or error page) |
| `{type: 'wb-frame:auth-required'}` | briefing → host | a 401, see Auth |

Unknown keys, wrong types and messages from anything but `window.parent` are
ignored. A message can only change which section is shown. Focus is never
saved: the same user opening the briefing normally gets the full page.

Not available yet (ask in this repo if a talk needs it): driving the briefing's
route point, model or Skew-T mode by message after load. Today they are set
once, by URL.

## Auth, framing, retention

- **Auth**: the frame uses the viewer's own weather.flyfun.aero session cookie.
  talks.flyfun.aero is the same site (flyfun.aero), so the cookie is sent. The
  viewer must be able to open that flight: theirs, or one shared with them. On a
  401 the page posts `wb-frame:auth-required`. The host shows its own
  "sign in at weather.flyfun.aero" prompt (or its snapshot fallback); the frame
  can't complete a Google sign-in itself.
- **Who may frame**: CSP `frame-ancestors 'self' https://talks.flyfun.aero` on
  the whole site (`deploy/weather.flyfun.aero.caddy`; the private config repo
  is the source of truth). A new host domain needs a change there first.
- **Retention**: after 30 days, tier T1 strips Skew-T, GRAMET and
  `cross_section.json` from a flight's packs. The cross-section itself still
  renders (from `route_analyses.json`). A flight a talk relies on must be
  pinned (`scripts/ops/pin_flight.py`; the talks repo has `tools/pins.py <talk> --fix`).

## Traps

- **Empty sections**: a section the flight has no data for (METAR/TAF on a past
  flight, alternates when there are none, a stripped Skew-T) stays hidden, and
  focusing it shows an empty pane. With `chrome=none` that is a **blank frame**,
  and no message says so. Check each slide's section against the real flight,
  pinned pack included.
- **Latest vs pinned pack**: without `pack=` the frame shows the newest pack,
  which changes as the flight refreshes and stops after the flight.
  Observation sections (METAR/TAF, SIGMETs, radar) show what was fetched for
  that pack.
- **Size**: the briefing is a responsive page. Below 980 px wide it switches to
  one column. Give the frame a desktop-like size (≥ 1000 px wide) for the
  layout the screenshots show.
- **Origin when posting**: post to the frame with the weather origin,
  `frame.contentWindow.postMessage(msg, 'https://weather.flyfun.aero')`. Its
  replies are posted to `'*'` (they carry only ids), so check
  `ev.origin === 'https://weather.flyfun.aero'` on receipt.
