# Aircraft ⇄ Profile consolidation

Status: **proposed** (2026-07-12). Supersedes the profile's "Flight Defaults" section.

The app asks for the same facts in two places, and the two places disagree. This
doc fixes the ownership rule, makes the aircraft entity real, and folds the
resulting UX work (flight-creation pane, onboarding, a flight-creation tour) into
one sequence.

---

## 1. The problem, with evidence

The overlap is not a tidy duplicate — each colliding field resolves in a
*different direction*, and the aircraft's most distinctive columns are dead.

| fact | aircraft | profile | who actually wins today |
|---|---|---|---|
| cruise speed (IAS) | `cruise_speed_kt` | `speed_kt` | **aircraft** — the one real resolver, `atmo.py:34` `resolve_cruise_speed_ias` |
| ceiling | `ceiling_ft` | `flight_ceiling_ft` | **profile** server-side (`api/flights.py:756`); the aircraft's value is applied by exactly one web `change` handler (`flights-main.ts:989`) and is invisible to iOS, MCP, ChatGPT, and `update_flight` |
| cruise altitude | — | `cruise_altitude_ft` | profile (no aircraft counterpart) |
| IFR equipped | `is_ifr` | `flight_rules` + `interview.flying_type` | **nothing reads `is_ifr`** — it renders a settings badge and never enters the pipeline |
| FIKI equipped | `is_fiki` | `interview.icing_equipage` | **nothing reads `is_fiki`** — the interview answer drives the icing advisories instead |

Three things follow.

**The aircraft entity is half-built.** Only `cruise_speed_kt` reaches the engine.
`ceiling_ft` is web-only; `is_ifr` / `is_fiki` are decorative.

**The onboarding wizard's "aircraft" step creates no aircraft.** It writes flight
rules, cruise altitude, ceiling and speed into the *profile's* settings JSON
(`welcome-wizard.ts:407-436`) and never calls `POST /aircraft`. A user who
completes setup therefore owns **zero** aircraft — which is why the
flight-creation pane hides its aircraft dropdown behind a (not even i18n'd) "you
can add aircraft presets in Settings" hint (`flights-main.ts:953`). The profile
absorbed the aircraft's job because the aircraft was never populated.

**The setup interview asks about the aircraft and stores the answer on the
profile.** Of its three questions (`analysis/advisories/interview.py`), two are
plainly aircraft facts — *"Is your aircraft FIKI-certified?"* (`icing_equipage`,
which flips `fiki_icing` / `icing_escape`) and *"instrument-rated **and
equipped**"* (`flying_type`, which flips `ifr_feasibility`). Only `minimums`
(crosswind limits, ceiling/visibility floors) is genuinely a pilot preference.

## 2. The ownership rule

> **Aircraft = what the machine can do. Profile = how conservative you want the
> analysis to be.**

Every field lands by asking "does this change when I swap planes, or when I
change my mind about risk?"

**Aircraft owns** ICAO type, name, cruise speed, service ceiling, typical cruise
altitude, IFR-equipped, FIKI-equipped.

**Profile owns** personal minimums and advisory parameters, engine methods,
forecast models, digest guidance, aggregation — and `flight_rules`, which stays
because an IFR-equipped aircraft is still flown VFR-only by a VFR pilot. It
becomes a *mission* choice, constrained by the aircraft (see §4.3).

Cruise altitude is the one genuinely contested field: it correlates with the
plane (a C172 does not cruise at FL280) *and* with the mission (the
`ifr_conservative` template picks 8000, `vfr_only` picks 5500). It goes to the
aircraft, because swapping planes should change it and changing risk appetite
should not. Cost, accepted: the system profile templates stop nudging altitude.

## 3. Target data model

### Aircraft (`user_aircraft`)

Existing: `icao_type`, `tail_number`, `nickname`, `is_ifr`, `is_fiki`,
`cruise_speed_kt`, `ceiling_ft`, `is_default`.

**Add** `typical_cruise_altitude_ft INT NULL`.

`is_default` gains teeth: every user is guaranteed exactly one default aircraft
(§4.1).

### Profile (`flight_profiles.settings_json`)

**Remove** `cruise_altitude_ft`, `flight_ceiling_ft`, `speed_kt` — the whole
"Flight Defaults" section.

**Keep** `flight_rules`, `models`, `advisory_models`, `icing_method`,
`cloud_method`, `convective_method`, `icing_severity_enhance`,
`auto_front_detection`, `compute_alternates`, `gramet_enabled`,
`llm_digest_enabled`, `digest_guidance`, `advisories`, `interview`.

`configs/system_profiles.json` drops the two altitude keys from all three
templates; `flight_rules` stays.

### Interview

Drops `flying_type` and `icing_equipage`. Both become aircraft fields, asked once
at onboarding on the aircraft step. The interview keeps `minimums` — a real
preference — and remains the place to re-run presets later.

## 4. Resolution rules

### 4.1 Every user has a default aircraft

`ensure_default_aircraft(db, user_id)` in `storage/aircraft.py`, mirroring the
existing `ensure_default_profile` (`storage/flights.py:793`).

Resolution mirrors the profile pattern exactly: `flights.aircraft_id` stays
**nullable** with `SET NULL` on delete, and a null resolves *lazily* to the
user's default aircraft at read time (`load_aircraft_context`), the way
`load_profile_context` already resolves a null `profile_id`
(`api/profiles.py:392`). That keeps deletes safe without a NOT NULL constraint.

- `create_flight` attaches `req.aircraft_id or ensure_default_aircraft(...)`, so
  MCP- and ChatGPT-created flights — which send no aircraft at all today — get
  one automatically.
- The `aircraft_id = 0` detach sentinel in `PATCH /flights/{id}`
  (`api/flights.py:1871`) is removed. An aircraft is no longer detachable.
- Web and iOS make the selector required and always populated.

### 4.2 Flight creation reads performance from the aircraft

```
cruise_altitude_ft = req ?? aircraft.typical_cruise_altitude_ft ?? 8000
flight_ceiling_ft  = req ?? aircraft.ceiling_ft               ?? 18000
cruise_speed_ias   = aircraft.cruise_speed_kt                 (live, per briefing)
```

`update_flight` re-applies these when `aircraft_id` changes — which finally makes
the stale comment at `api/flights.py:1711` ("switch aircraft (applies
speed/ceiling defaults)") true. `resolve_cruise_speed_ias` loses its profile
fallback and becomes aircraft-only.

Existing flights are unaffected: they snapshot `cruise_altitude_ft` /
`flight_ceiling_ft` onto their own row at creation (and hash both into the flight
ID, `api/flights.py:236`), so retiring the profile defaults cannot disturb them.

### 4.3 Equipage drives the engine

The aircraft's flags stop being decorative:

- **Effective flight rules** = `vfr_only` if `not aircraft.is_ifr` else
  `profile.flight_rules`. An IFR profile flown in a VFR-only aircraft is graded
  VFR — the capability floor wins over the preference. This feeds
  `BriefingOptions.flight_rules` (`api/packs.py:1113`), so the digest prompt and
  the `vfr_feasibility` / `ifr_feasibility` advisories follow it for free.
- **Icing advisories** derive from `aircraft.is_fiki` rather than a saved
  `enabled` map: FIKI ⇒ `fiki_icing`, otherwise ⇒ `icing_escape`. Threading
  `is_fiki` onto `RouteContext` is the one genuine engine change in this plan and
  the piece to design most carefully — it must not silently re-enable an advisory
  a pilot explicitly turned off. Proposal: aircraft equipage selects *which* of
  the two icing advisories is eligible; an explicit per-profile `enabled: false`
  still wins (the same "explicit opt-out beats the master" rule already used for
  `auto_front_detection` ⇄ `fronts`, issue #196 model B).

## 5. Migration

One Alembic revision (`batch_alter_table`, SQLite + MySQL):

1. Add `user_aircraft.typical_cruise_altitude_ft`.
2. **Backfill an aircraft for every user who has none**, from their default
   profile: `cruise_speed_kt ← speed_kt`, `ceiling_ft ← flight_ceiling_ft`,
   `typical_cruise_altitude_ft ← cruise_altitude_ft`,
   `is_ifr ← flight_rules != 'vfr_only'`,
   `is_fiki ← interview.icing_equipage == 'fiki'`, `is_default = true`,
   `icao_type = 'ZZZZ'` (the ICAO "type not otherwise specified" code — the
   onboarding nudge asks them to name it properly).
3. For users who *do* have aircraft, fill any null
   `typical_cruise_altitude_ft` from their default profile.
4. Strip `cruise_altitude_ft`, `flight_ceiling_ft`, `speed_kt` from every
   `settings_json`, and from `configs/system_profiles.json`.

Reversible: the downgrade re-derives the three profile keys from the default
aircraft.

## 6. Slices

**S1 — data model + resolution (backend).** Migration, `ensure_default_aircraft`,
`load_aircraft_context`, creation/update resolution, aircraft-only speed
resolver, effective-flight-rules derivation, `is_fiki` on `RouteContext`,
interview loses two questions. Tests: resolution precedence, the lazy-default
path, MCP/agent creation attaching a default, explicit-opt-out-beats-equipage.

**S2 — settings + flight-creation pane (web).** Remove the profile's Flight
Defaults section; the aircraft editor gains typical cruise altitude. On the
create pane: replace the aircraft (i)'s raw **`alert()`** (`flights-main.ts:259`
— `initInfoPopup()` is already live on that page, it just isn't used) with
`showPopupContent`, add a matching profile (i), and give both an **"Edit in
Settings →"** deep link. Both popups explain that these configure *how the flight
is analysed* and that you can keep several for different purposes. i18n the
zero-aircraft hint (which the default-aircraft guarantee should make unreachable
anyway).

**S3 — onboarding wizard.** Step 3 becomes a *real* aircraft: name + ICAO type
(typeahead against the existing `GET /aircraft/types` catalog), cruise speed,
service ceiling, typical cruise altitude, IFR + FIKI toggles → `POST /aircraft`
with `is_default: true`. Step 2 (profile) gains **a few critical pilot minimums**
— and #387 makes this nearly free: the catalog now tags params
`audience: "pilot"`, so the wizard renders them generically instead of
hard-coding. The critical few: `airport_wind.crosswind_red_kt`,
`flight_category.amber_ceiling_ft`, `flight_category.amber_vis_sm`, with "you can
tune the rest, and re-run the setup assistant, in Settings".

**S4 — flight-creation tour.** `driver.js` is already a dependency and the
briefing tour is a clean `DriveStep[]` (`web/ts/tour/briefing-tour.ts`), so this
is a new step array plus generalising `tour-storage.ts` (today a single
hard-coded `wb_tour_offered` key → per-tour keys). Steps, mapping onto existing
element ids:

1. **Aircraft + profile** (`#input-aircraft`, `#input-profile`) — "this is the
   configuration that decides how the flight is analysed; keep several for
   different purposes."
2. **Route** (`#input-waypoints`, `#btn-paste-fpl`, `#btn-import-autorouter`) —
   airports or standard waypoints, import from FPL or Autorouter.
3. **Interpret** (`#btn-preview-route`) — shows how the app *understood* the
   route, on a map, before you commit.
4. **Date / time / altitude / duration** — auto-estimated from the aircraft.
5. **Flexibility** (`#input-flexibility`) — explore alternative departure times,
   *and* be honest that it is a lot of server work, so use it when you mean it.

Own localStorage key, own `?tour=` trigger, own help icon on `index.html`,
`tour.flights.*` i18n keys across all four locales.

**S5 — clients.** iOS: aircraft required in `AddFlightViewModel`, `applyAircraft`
sets altitude/ceiling (today `applyProfile` sets only cruise altitude and
aircraft selection changes nothing). MCP/ChatGPT: expose an optional aircraft on
`create_flight`, defaulting to the user's default.

## 7. Risks

- **The `is_fiki` → icing advisory rewiring is the one behaviour change with
  teeth.** Everything else is a move; this changes which advisory fires. Needs
  the explicit-opt-out rule above, and a regression test per equipage.
- **Migration rewrites `settings_json` on prod MySQL.** Small tables, but it is a
  data migration, not just a schema one — dry-run against a prod dump first.
- **The `ZZZZ` placeholder type** is a visible wart for backfilled users until
  they name their aircraft. Mitigate with a one-time nudge on the flights page.
- **Existing flights keep their snapshotted altitude/ceiling** — correct, but it
  means a user who "fixes" their aircraft won't see old flights change. That is
  the intended behaviour (flights are immutable records), worth saying in the
  (i) copy.
