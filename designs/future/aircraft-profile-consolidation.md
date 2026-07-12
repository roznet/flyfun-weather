# Aircraft ⇄ Profile consolidation

Status: **proposed** (2026-07-12), revised after a read-only audit of production.

The app looks like it asks for the same facts twice. A prod audit showed that is
only *half* true — one field is a genuine duplicate, one is a genuine
duplication of concept, and two are **different concepts wearing confusingly
similar names**. This doc fixes the ownership rule, makes the aircraft entity
real (84% of users don't have one), and folds in the UX work that depends on it.

---

## 1. What the production data actually says

Read-only audit, 2026-07-12 (581 users, 1 283 flights, 103 aircraft):

| finding | number |
|---|---|
| users with **zero** aircraft | **487 / 581 (84%)** |
| flights with **no** `aircraft_id` | **922 / 1 283 (72%)** |
| users where aircraft + profile **both** set a ceiling, and they **disagree** | 39 / 81 |
| users where aircraft + profile both set a speed, and they disagree | 21 / 64 |
| profiles with saved setup-interview answers | **0** |
| aircraft with real equipage recorded | 83 `is_ifr`, 33 `is_fiki` — **read by nothing** |

**The aircraft entity is unused because nothing populates it.** The onboarding
wizard's step is *called* "aircraft" but creates none — it writes flight rules,
cruise altitude, ceiling and speed into the *profile's* settings JSON
(`welcome-wizard.ts:407-436`) and never calls `POST /aircraft`. Users finish
setup owning zero aircraft, so the flight-creation pane hides its aircraft
dropdown (`flights-main.ts:953`). The profile absorbed the aircraft's job.

### 1a. The finding that changed the design

434 users have multiple profiles carrying **different ceilings**. That looks
alarming — until you split it: **274 are just the seeded system templates**
(VFR Only 10 000 / IFR Conservative 18 000 / IFR FIKI 25 000), never touched.
**160 users actually authored a ceiling**, and only **26** both authored one *and*
fly more than one profile.

Then look at *what those users wrote*:

```
user 903fcde9:  VFR Only 10 000 | IFR Conservative 15 000 | IFR FIKI 25 000
user 7436d9fe:  VFR Only 10 000 | IFR Conservative 12 000 | IFR FIKI 25 000
user 62fabc8a:  VFR Only 10 000 | IFR Conservative 25 000 | IFR FIKI 25 000
```

Same pilot. Same aeroplane. Three ceilings, varying by **mission**.

That is not a service ceiling. **`profile.flight_ceiling_ft` and
`aircraft.ceiling_ft` are not duplicates** — they are two different quantities
with confusingly similar names:

- **`aircraft.ceiling_ft` = service ceiling.** What the airframe can physically
  reach. A hard cap.
- **`profile.flight_ceiling_ft` = mission ceiling.** How high I will actually
  plan and analyse *this kind of trip* — driven by oxygen, rules, currency,
  comfort. A choice.

The relationship is not "one replaces the other", it is **mission ≤ service**.
That is *why* half the users who set both have them "disagreeing": they are not
confused, they are answering two different questions. The original version of
this doc read that disagreement as evidence of duplication. It is evidence of
the opposite.

Corroboration: only **17%** of flights override the profile's ceiling (versus
45% for cruise altitude). The per-profile ceiling is sticky and doing real work
— it is not a throwaway prefill.

The same logic applies to cruise altitude, which has no aircraft counterpart at
all and which the templates deliberately vary by mission (5 500 / 8 000 /
10 000) for the same aeroplane.

## 2. The ownership rule

> **Aircraft = what the machine can do. Profile = what I intend to do with it.**

Ask of each field: *does this change when I swap aeroplanes, or when I change my
mind about the mission?*

| field | home | why |
|---|---|---|
| ICAO type, name, tail | **aircraft** | identity |
| `cruise_speed_kt` | **aircraft** | **the one genuine duplicate.** Same meaning in both places; the aircraft already wins in code (`atmo.py:34`). Delete `profile.speed_kt` |
| `ceiling_ft` → **service ceiling** | **aircraft** | physical cap. Currently web-only; gains a real job (§4.3) |
| `is_ifr`, `is_fiki` | **aircraft** | **genuine duplication of concept** — the columns are dead while the interview asks the same questions. Aircraft becomes the source of truth |
| `cruise_altitude_ft` | **profile** | mission choice; no aircraft counterpart; templates vary it for the same plane |
| `flight_ceiling_ft` → **mission ceiling** | **profile** | *not* a duplicate of the service ceiling. 160 users author it; 26 rely on it across profiles |
| `flight_rules` | **profile** | intent, not capability (§4.3) |
| minimums, advisory params, engine methods, models, digest | **profile** | judgment |

**So the profile's "Flight Defaults" section is not deleted — it is renamed to
what it always was.** It becomes **Mission defaults** (cruise altitude, mission
ceiling, flight rules) and loses exactly one field: speed. The confusion was
real, but it was a *naming* problem wearing a data-model costume.

## 3. Target data model

### Aircraft (`user_aircraft`)

No new columns. The existing ones finally get used:

- `cruise_speed_kt` — the only source of cruise IAS (profile fallback removed).
- `ceiling_ft` — relabelled **service ceiling** in every UI. Nullable: unknown is
  an honest answer, and it simply disables the mission≤service check.
- `is_ifr`, `is_fiki` — wired into the engine (§4.3).
- `is_default` — gains teeth: every user is guaranteed exactly one (§4.1).

*(The `typical_cruise_altitude_ft` column proposed in the first draft is
**dropped**. Cruise altitude is a mission choice and stays on the profile.)*

### Profile (`flight_profiles.settings_json`)

- **Remove** `speed_kt` — the only true duplicate.
- **Keep and relabel** `flight_ceiling_ft` → "mission ceiling", `cruise_altitude_ft`.
- **Keep** `flight_rules` and everything else unchanged.
- `configs/system_profiles.json` is untouched — its per-mission altitudes and
  ceilings are exactly the behaviour we are preserving.

### Interview

Drops `flying_type` and `icing_equipage` — both are aircraft facts (*"Is your
aircraft FIKI-certified?"*). **Zero profiles have interview answers in prod**, so
this deletes no user data. Keeps `minimums`, a real preference.

## 4. Resolution rules

### 4.1 Every user has a default aircraft

`ensure_default_aircraft(db, user_id)` in `storage/aircraft.py`, mirroring
`ensure_default_profile` (`storage/flights.py:793`).

`flights.aircraft_id` stays **nullable** with `SET NULL` on delete; a null
resolves *lazily* to the user's default aircraft at read time
(`load_aircraft_context`), exactly as `load_profile_context` already resolves a
null `profile_id` (`api/profiles.py:392`). Safe deletes, no NOT NULL constraint.

- `create_flight` attaches `req.aircraft_id or ensure_default_aircraft(...)`, so
  the 72% of flights with no aircraft — and every MCP/ChatGPT flight, which
  cannot send one at all — get one.
- The `aircraft_id = 0` detach sentinel (`api/flights.py:1871`) is removed.

### 4.2 Speed comes from the aircraft; altitude and mission ceiling from the profile

```
cruise_speed_ias   = aircraft.cruise_speed_kt          (live, per briefing)
cruise_altitude_ft = req ?? profile.cruise_altitude_ft ?? 8000
flight_ceiling_ft  = req ?? profile.flight_ceiling_ft  ?? 18000    # mission ceiling
```

`resolve_cruise_speed_ias` loses its profile fallback and becomes aircraft-only.

**Delete the ceiling-overwrite handler at `flights-main.ts:989`** — where
selecting an aircraft overwrites the ceiling input with the *service* ceiling.
That single line is the code that conflated the two concepts, and it is the most
likely origin of the 39 users whose two ceilings disagree.

The aircraft's service ceiling instead **validates**: if mission ceiling >
service ceiling, warn in the flight pane and in settings ("*this profile analyses
to 18 000 ft, but N123AB's service ceiling is 14 000 ft*"). Warn, never silently
clamp.

Existing flights are untouched either way: they snapshot `cruise_altitude_ft` and
`flight_ceiling_ft` onto their own row at creation (and hash both into the flight
ID, `api/flights.py:236`).

### 4.3 Equipage drives the engine

**IFR — capability AND intent, and never silently.** `aircraft.is_ifr` = *can this
machine fly IFR*; `profile.flight_rules` = *do I want this trip analysed as IFR*.
An IFR-capable plane flown under a VFR-only profile is a normal, deliberate
combination, and interpretation is a job only the profile can do (a future digest
system prompt may read advisories differently under VFR-only rules).

```
effective_rules = vfr_only if not aircraft.is_ifr else profile.flight_rules
```

- IFR plane + VFR-only profile → VFR (intent restricts — the common case).
- VFR-only plane + IFR profile → VFR (capability restricts — grading IFR
  feasibility for a plane that cannot fly IFR is meaningless).

⚠️ **The AND must never be silent.** `is_ifr` defaults to `False` and is written
by nothing today, so a naive AND would quietly downgrade the IFR profile of every
pilot who never ticked the box — `ifr_feasibility` would just stop appearing.
Three guards, all required: onboarding asks explicitly (S3); the migration
backfills `is_ifr` from `flight_rules != 'vfr_only'` (§5); a profile/aircraft
mismatch is *shown*, not silently applied (S2).

Note `profile.flight_rules` is **almost inert today** — its only consumer is one
line of the digest prompt (`prompt_builder.py:89` → `PILOT CAPABILITY: …`). No
evaluator reads it. (Beware: `"flight_rules"` is *also* an advisory **category**
name — the settings group holding `vfr_feasibility` + `ifr_feasibility`,
`registry.py:54`. Same string, unrelated meaning.) What actually gates the IFR
advisory today is the advisory `enabled` map, written by the interview's
`flying_type` question. The derived `effective_rules` takes over that job, which
is what makes retiring `flying_type` a consequence of the design rather than a
separate deletion.

**FIKI.** `aircraft.is_fiki` selects which icing advisory is *eligible* — FIKI ⇒
`fiki_icing`, otherwise ⇒ `icing_escape` — threaded onto `RouteContext`. This is
the one genuine behaviour change in the plan. An explicit per-profile
`enabled: false` **still wins**, the same "explicit opt-out beats the master" rule
already used for `auto_front_detection` ⇄ `fronts` (#196 model B).

## 5. Migration

One Alembic revision (`batch_alter_table`, SQLite + MySQL). **No schema change** —
it is a data migration.

1. **Synthesise a default aircraft for the 487 users who have none**, from their
   default profile:
   - `cruise_speed_kt ← profile.speed_kt` (real data)
   - `is_ifr ← profile.flight_rules != 'vfr_only'` (real data — load-bearing, see §4.3)
   - `is_fiki ← false`
   - `ceiling_ft ← NULL`
   - `icao_type = 'ZZZZ'`, `nickname = 'My aircraft'`, `is_default = true`
2. **Strip `speed_kt`** from every `settings_json`. Nothing else in the profile is
   touched.
3. Users who already own aircraft (94) are left completely alone.

⚠️ **Do not backfill `is_fiki` from the advisory `enabled` map.** 433 users have
`fiki_icing: true` and 439 have it `false` — but those are *template-seeded*
defaults persisted on save, not user declarations. The only trustworthy equipage
signal in prod is `aircraft.is_fiki` (32 users), and for everyone else the honest
answer is "unknown ⇒ not FIKI", which is also the conservative one (it selects
`icing_escape`, the more cautious advisory). Onboarding then asks for real.

`ceiling_ft = NULL` and `icao_type = 'ZZZZ'` are deliberately *honest gaps*, not
invented data: they disable the mission≤service check until the pilot fills them
in, and they drive the naming nudge (§7).

## 6. Slices

**S1 — data model + resolution (backend), #397.** Migration; `ensure_default_aircraft`
+ `load_aircraft_context`; aircraft-only speed resolver; `effective_rules`
derivation; `is_fiki` on `RouteContext`; interview loses two questions; mission ≤
service validation. Tests: resolution precedence, lazy-default path, MCP/agent
creation attaching a default, explicit-opt-out-beats-equipage, and a regression
test per equipage combination.

**S2 — settings + flight-creation pane (web), #398.** Rename "Flight Defaults" →
**Mission defaults** (cruise altitude, mission ceiling, flight rules); drop speed
from it. Relabel the aircraft's ceiling as **service ceiling**. **Delete the
ceiling-overwrite handler** (`flights-main.ts:989`). Show the mission>service and
IFR-mismatch warnings. On the create pane: replace the aircraft (i)'s raw
**`alert()`** (`flights-main.ts:259` — `initInfoPopup()` is already live there, it
just isn't used) with `showPopupContent`, add a matching **profile (i)**, and give
both an **"Edit in Settings →"** deep link. i18n the hard-coded English
zero-aircraft hint.

**S3 — onboarding wizard, #399.** Step 3 becomes a *real* aircraft: name + ICAO
type (typeahead against the existing `GET /aircraft/types`), cruise speed, service
ceiling, IFR + FIKI → `POST /aircraft` with `is_default: true`. The IFR/FIKI
questions move here from the interview, where they never belonged. Step 2 gains
**a few critical pilot minimums** — and #387 makes this nearly free, since the
catalog now tags params `audience: "pilot"`: `airport_wind.crosswind_red_kt`,
`flight_category.amber_ceiling_ft`, `flight_category.amber_vis_sm`, with "tune the
rest in Settings".

**S4 — flight-creation tour, #400.** `driver.js` is already a dependency and
`briefing-tour.ts` is a clean `DriveStep[]`; this is a new step array plus
generalising `tour-storage.ts` (today one hard-coded `wb_tour_offered` key →
per-tour keys). Steps: aircraft+profile ("configuration that decides how the
flight is analysed; keep several for different purposes") → route entry + FPL /
Autorouter import → **Interpret** (see how the app understood your route, on a
map, before committing) → date/time/altitude/duration → **Flexibility** (explore
alternative departure times — *and* be honest that it is heavy server work).

**S5 — clients, #401.** iOS: aircraft required in `AddFlightViewModel`; today
`applyProfile` sets only cruise altitude and selecting an aircraft changes
nothing. MCP/ChatGPT: optional aircraft on `create_flight`, defaulting to the
user's default.

## 7. Risks

- **The `is_fiki` → icing rewiring is the only behaviour change with teeth.**
  Everything else is a move or a rename. Needs the explicit-opt-out rule and a
  regression test per equipage combination.
- **The `is_ifr` AND has a silent-downgrade trap** — see §4.3. The backfill from
  `flight_rules` is load-bearing, not a nicety.
- **The migration rewrites `settings_json` on prod MySQL.** Small tables, but it
  is a data migration — dry-run against a prod dump first.
- **`ZZZZ` + null service ceiling** are visible gaps for 487 backfilled users
  until they name their aircraft. Mitigate with a one-time nudge on the flights
  page, not a blocking modal.
- **Renaming, not deleting, is the point.** Resist the temptation to "simplify"
  by folding mission ceiling into service ceiling later — the prod data
  (§1a) says pilots use both.
