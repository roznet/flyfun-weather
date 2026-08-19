# Aircraft ⇄ Profile consolidation

Status: **shelved, not implemented.** Proposed 2026-07-12, revised after a
read-only audit of production, slices consolidated 2026-07-14 (five → two, §6).
**#396 / #397 / #398 were all closed `NOT_PLANNED` on 2026-08-01 with no closing
rationale** — a bulk close, not a decision recorded anywhere. Re-verified against
code 2026-08-15: **none of the data-model work landed.** No
`ensure_default_aircraft`, no `load_aircraft_context`, no `needs_review` column,
no migration; `resolve_cruise_speed_ias` still falls back to the profile
(`atmo.py:34`); the wizard still writes `speed_kt` into the profile; settings
still says "Flight Defaults".

Read this doc as **an analysis worth keeping** — the §1/§1a prod audit is the
part with lasting value, and it is what kills the naive "the ceiling is a
duplicate, delete one" instinct that this design keeps getting re-proposed as.
Before reviving it, re-run the audit: the numbers are from 2026-07-12 and predate
migrations 078/079 (sparse profile settings, #402) and the #387 interview work.

**What landed anyway, adjacent to the plan** (so don't re-plan it): the
*vocabulary* is already in the UI ahead of the model — `settings.html:72` says
**"Service Ceiling"** and the aircraft (i) popup states the ownership rule
verbatim (`en.json: flights.form.aircraftInfoBody`); S2's "(i) popup" bullet is
**done** (`alert()` gone, aircraft + profile popups with "Edit in Settings →"
deep links, `flights-main.ts:254-260, 301-335`); iOS moved on its own
(`applyProfile` now applies the ceiling too, default aircraft auto-selected,
in-app aircraft-create form with ICAO-type typeahead,
`AddFlightViewModel.swift:646-712`); the flight-creation tour (#400) **shipped**
(`web/ts/tour/flights-tour.ts`). ⚠️ The interview's `flying_type` question was
**renamed `flight_rules`** (`interview.py:57`), so that string now means *three*
things — interview question id, profile settings key, advisory category.

Still unlanded and still true: the ceiling-overwrite handler
(`flights-main.ts:1064-1069`), the hard-coded English zero-aircraft hint
(`:1038`), `is_ifr`/`is_fiki` read by nothing outside the aircraft CRUD API,
`speed_kt` written by the wizard (`welcome-wizard.ts:405-435`).

---

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
(`welcome-wizard.ts:405-435`, `collectAndSaveAircraftSettings`) and never calls
`POST /aircraft`. Users finish setup owning zero aircraft, so the
flight-creation pane shows a hint instead of a populated dropdown
(`flights-main.ts:1031`). The profile absorbed the aircraft's job.

### 1a. The finding that changed the design

434 users have multiple profiles carrying **different ceilings**. That looks
alarming — until you split it: **274 are just the seeded system templates**
(VFR Only 10 000 / IFR Conservative 18 000 / IFR FIKI 25 000), never touched.
**160 users actually authored a ceiling**, and only **26** both authored one *and*
fly more than one profile.

Then look at *what those users wrote* — e.g. `VFR Only 10 000 | IFR Conservative
15 000 | IFR FIKI 25 000` (and 12 000 / 25 000 in the middle slot for two other
users). Same pilot, same aeroplane, three ceilings, varying by **mission**.

That is not a service ceiling. **`profile.flight_ceiling_ft` and
`aircraft.ceiling_ft` are not duplicates** — they are two different quantities
with confusingly similar names:

- **`aircraft.ceiling_ft` = service ceiling.** What the airframe can physically
  reach. A hard cap.
- **`profile.flight_ceiling_ft` = mission ceiling.** How high I will actually
  plan and analyse *this kind of trip* — driven by oxygen, rules, currency,
  comfort. A choice.

The relationship is not "one replaces the other", it is **mission ≤ service**.
That is *why* half the users who set both have them "disagreeing": they are
answering two different questions. The first draft of this doc read that
disagreement as evidence of duplication; it is evidence of the opposite.
Corroboration: only **17%** of flights override the profile's ceiling (versus
45% for cruise altitude) — it is sticky and doing real work, not a throwaway
prefill. Same logic for cruise altitude, which has no aircraft counterpart and
which the templates vary by mission (5 500 / 8 000 / 10 000) for one aeroplane.

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

No new columns — the existing ones finally get used. `cruise_speed_kt` becomes
the only source of cruise IAS (profile fallback removed); `ceiling_ft` is
relabelled **service ceiling** everywhere and stays nullable (unknown is an
honest answer and simply disables the mission≤service check); `is_ifr`/`is_fiki`
are wired into the engine (§4.3); `is_default` gains teeth — exactly one per
user (§4.1). *(The `typical_cruise_altitude_ft` column of the first draft is
**dropped**: cruise altitude is a mission choice and stays on the profile.)*

### Profile (`flight_profiles.settings_json`)

**Remove** `speed_kt`, the only true duplicate. **Keep and relabel**
`flight_ceiling_ft` → "mission ceiling"; keep `cruise_altitude_ft`,
`flight_rules` and everything else unchanged. `configs/system_profiles.json` is
untouched — its per-mission altitudes/ceilings (5 500/8 000/10 000 and
10 000/18 000/25 000, still there today) are exactly the behaviour we preserve.

### Interview

Drops the first two questions — `flight_rules` (formerly `flying_type`) and
`icing_equipage` — both are aircraft facts (*"Is your aircraft FIKI-certified?"*).
**Zero profiles had interview answers as of the 2026-07-12 audit**, so this
deleted no user data *then*; re-check before acting. Keeps `minimums`, a real
preference.

## 4. Resolution rules

### 4.1 Every user has a default aircraft

`ensure_default_aircraft(db, user_id)` in `storage/aircraft.py`, mirroring
`ensure_default_profile` (`storage/flights.py:835`).

`flights.aircraft_id` stays **nullable** with `SET NULL` on delete; a null
resolves *lazily* to the user's default aircraft at read time
(`load_aircraft_context`), exactly as `load_profile_context` already resolves a
null `profile_id` (`api/profiles.py:402`). Safe deletes, no NOT NULL constraint.

- `create_flight` attaches `req.aircraft_id or ensure_default_aircraft(...)`, so
  the 72% of flights with no aircraft — and every MCP/ChatGPT flight, which
  cannot send one at all — get one.
- The `aircraft_id = 0` detach sentinel (`api/flights.py:2177-2179`, in the
  update path) is removed.

### 4.2 Speed comes from the aircraft; altitude and mission ceiling from the profile

```
cruise_speed_ias   = aircraft.cruise_speed_kt          (live, per briefing)
cruise_altitude_ft = req ?? profile.cruise_altitude_ft ?? 8000
flight_ceiling_ft  = req ?? profile.flight_ceiling_ft  ?? 18000    # mission ceiling
```

`resolve_cruise_speed_ias` loses its profile fallback and becomes aircraft-only.

**Delete the ceiling-overwrite handler at `flights-main.ts:1064-1069`** — where
selecting an aircraft overwrites the ceiling input with the *service* ceiling.
That single line is the code that conflated the two concepts, and it is the most
likely origin of the 39 users whose two ceilings disagree.

The aircraft's service ceiling instead **validates**: if mission ceiling >
service ceiling, warn in the flight pane and in settings ("*this profile analyses
to 18 000 ft, but N123AB's service ceiling is 14 000 ft*"). Warn, never silently
clamp.

Existing flights are untouched either way: they snapshot `cruise_altitude_ft` and
`flight_ceiling_ft` onto their own row at creation (and hash both into the flight
ID — `_flight_id` / `"ceil"`, `api/flights.py:257-267`).

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
Three guards, all required: onboarding asks explicitly (S2, §6); the migration
backfills `is_ifr` from `flight_rules != 'vfr_only'` (§5); a profile/aircraft
mismatch is *shown*, not silently applied (S2, §6).

Note `profile.flight_rules` is **almost inert today** — its only consumer is one
line of the digest prompt (`digest/prompt_builder.py:111-123` →
`PILOT CAPABILITY: …`). No evaluator reads it. (Beware the **three-way name
collision**: `"flight_rules"` is also an advisory **category** — the settings
group holding `vfr_feasibility` + `ifr_feasibility`,
`analysis/advisories/registry.py:33,56` — *and*, since #387, the id of the
interview's first question, which used to be `flying_type`
(`analysis/advisories/interview.py:57`). Three unrelated meanings, one string.)
What actually gates the IFR advisory today is the advisory `enabled` map,
written by that interview question. The derived `effective_rules` takes over
that job, which is what makes retiring the question a consequence of the design
rather than a separate deletion.

**FIKI.** `aircraft.is_fiki` selects which icing advisory is *eligible* — FIKI ⇒
`fiki_icing`, otherwise ⇒ `icing_escape` — threaded onto `RouteContext`. This is
the one genuine behaviour change in the plan. An explicit per-profile
`enabled: false` **still wins**, the same "explicit opt-out beats the master" rule
already used for `auto_front_detection` ⇄ `fronts` (#196 model B).

## 5. Migration

One Alembic revision (`batch_alter_table`, SQLite + MySQL). Essentially a data
migration — the only DDL is the `needs_review` boolean of §5a. (Latest revision
on main is 088, so it lands after that.)

1. **Synthesise a default aircraft for the 487 users who have none**, from their
   default profile:
   - `cruise_speed_kt ← profile.speed_kt` (real data)
   - `is_ifr ← profile.flight_rules != 'vfr_only'` (real data — load-bearing, see §4.3)
   - `is_fiki ← false`
   - `ceiling_ft ← NULL`
   - `icao_type = 'ZZZZ'`, `nickname = 'My aircraft'`, `is_default = true`
2. **Strip `speed_kt`** from every `settings_json`. Nothing else in the profile is
   touched. (The #402 sparsify — migration 079,
   `analysis/advisories/profile_sparsify.py` — landed since and swept advisory
   params, the legacy `cloud_method` and engine methods only; top-level
   `speed_kt` is untouched by it, so this step still has work to do. Reuse its
   dry-run-report pattern.)
3. Users who already own aircraft (94) are left completely alone.

⚠️ **Do not backfill `is_fiki` from the advisory `enabled` map.** 433 users have
`fiki_icing: true` and 439 have it `false` — but those are *template-seeded*
defaults persisted on save, not user declarations. The only trustworthy equipage
signal in prod is `aircraft.is_fiki` (32 users), and for everyone else the honest
answer is "unknown ⇒ not FIKI", which is also the conservative one (it selects
`icing_escape`, the more cautious advisory). Onboarding then asks for real.

`ceiling_ft = NULL` and `icao_type = 'ZZZZ'` are deliberately *honest gaps*, not
invented data: they disable the mission≤service check until the pilot fills them
in, and they drive the review prompt (§5a).

### 5a. The "review your aircraft" prompt

The migration also sets **`user_aircraft.needs_review = true`** on the 487
synthesised rows (one new boolean — the plan's only DDL). On next web login those
users get a small modal: *"FlyFun now tracks your aeroplane separately from your
flying preferences. Here's what we carried over — please check it."* Saving clears
the flag; the 94 users who already own aircraft never see it.

**It completes, it does not gate.** The aircraft already exists, so nothing is
broken — flights, scheduled refreshes and MCP calls keep working the moment the
migration lands. So the modal is *dismissible but re-shown* until reviewed: soft
and persistent, not soft and forgettable. Hard-blocking a returning pilot before
they can read their weather is a bad trade for data we already guess reasonably.

Fields, prefilled from what we actually know: cruise speed (`profile.speed_kt`,
real) and IFR-equipped (`flight_rules != 'vfr_only'`, real and load-bearing,
§4.3); name `"My aircraft"` as a placeholder; FIKI unticked (unknown ⇒
conservative). **ICAO type** (typeahead) and **service ceiling** are left empty —
unknown, deliberately not invented, and the main ask of the modal.

**No cruise altitude and no mission ceiling on this form** — they are profile
(mission) fields, not aircraft fields (§1a). Putting them here would rebuild the
exact confusion this work removes.

Why a flag and not the `icao_type == 'ZZZZ'` placeholder as the marker: a pilot
whose type genuinely isn't in the catalog could save ZZZZ deliberately and be
trapped in an unclearable prompt.

iOS and MCP users are unaffected until they visit the web app; their synthesised
aircraft works regardless. The equivalent iOS prompt rides along in the same
client slice (S2, §6).

## 6. Slices

Two, split along the only seam that matters: **what the server decides** vs **what
a human is shown**. (Replaces an earlier five-slice split, #397–#401, which cut
across that seam: the agent surfaces were server-side Python stranded in a client
slice, and the settings rename, the wizard and iOS were three issues restating
one rule on three screens. #399/#401 were closed `COMPLETED` 2026-07-14 as
*merged into* S1/S2, not as shipped code.) **All the issues are closed now —
#396/#397/#398 `NOT_PLANNED` on 2026-08-01 — so reviving this means opening fresh
ones; the seam below is still the right split.**

**S1 — backend, #397 (closed, unbuilt).** Migration (`needs_review` column;
synthesise a default aircraft for the 487 users with none; strip `speed_kt` from
`settings_json`); `ensure_default_aircraft` + `load_aircraft_context`;
aircraft-only speed resolver; `effective_rules` derivation; `is_fiki` on
`RouteContext`; interview loses its `flight_rules` and `icing_equipage`
questions; mission ≤ service validation, exposed as a **warning signal for the
client to render** — never a silent clamp.

*Agent surfaces ride here*, because they are Python in the same files: optional
`aircraft_id` on MCP/ChatGPT `create_flight` (correctness is already covered by
the lazy default — this is about *choice*), and the aircraft surfaced on
`get_briefing` / `list_flights` so an agent can tell which aeroplane a flight was
analysed for.

Tests: resolution precedence, lazy-default path, agent creation attaching a
default *and* honouring an explicit one, all four (`is_ifr` × `flight_rules`)
combinations, explicit-opt-out-beats-equipage, and a regression test per equipage
combination.

**S2 — clients (web + iOS), #398 (closed, mostly unbuilt).** One issue, because it is one idea — *aircraft
= what the machine can do; profile = what you intend to do with it; never
silently clamp or overwrite* — stated on several screens, in the same words. The
wizard's aircraft form and the review modal are literally the same field list and
**must share one component**; that shared component is the structural reason this
isn't three issues.

- *Settings:* rename "Flight Defaults" → **Mission defaults** (cruise altitude,
  mission ceiling, flight rules), dropping speed. Relabel the aircraft's ceiling
  as **service ceiling**. Render the mission>service and IFR-mismatch warnings
  that S1 exposes.
- *Flight-creation pane:* **delete the ceiling-overwrite handler**
  (`flights-main.ts:1064-1069`) — the few lines that conflate the two ceilings,
  and the likely origin of the 39 disagreeing users. i18n the hard-coded English
  zero-aircraft hint (`flights-main.ts:1038`). ✅ *Done already, independently:*
  the aircraft/profile (i) popups now use `showPopupContent` with
  "Edit in Settings →" deep links.
- *Onboarding wizard:* step 3 becomes a *real* aircraft — name + ICAO type
  (typeahead against the existing `GET /aircraft/types`), cruise speed, service
  ceiling, IFR + FIKI → `POST /aircraft` with `is_default: true`. No cruise
  altitude, no mission ceiling on that form. The IFR/FIKI questions move here from
  the interview, where they never belonged. Step 2 gains **a few critical pilot
  minimums** — #387 makes this nearly free, since the catalog now tags params
  `audience: "pilot"`: `airport_wind.crosswind_red_kt`,
  `flight_category.amber_ceiling_ft`, `flight_category.amber_vis_sm`, with "tune
  the rest in Settings".
- *Review modal* for the `needs_review` users — §5a. Completes, does not gate.
- *iOS:* aircraft required in `AddFlightViewModel` (S1 guarantees one exists).
  The client has since moved on its own — `applyProfile` now applies cruise
  altitude **and** the ceiling, the default aircraft is auto-selected on create,
  and there is an in-app aircraft-create form with ICAO-type typeahead
  (`AddFlightViewModel.swift:646-712`). What is still owed: the aircraft should
  prefill **nothing** — it *warns* (mission>service, IFR mismatch) and supplies
  speed and equipage. Decode `needs_review` and point those users at the web
  modal. Mirror the labels.

**Flight-creation tour, #400 — SHIPPED** (2026-07-17, `web/ts/tour/flights-tour.ts`,
`tour-storage.ts` generalised to per-tour keys). It was never consolidation work:
it adds no field, moves no data and renames nothing. Noted here only because it
*describes* the aircraft/profile model — if the model changes, its step copy
needs a pass.

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
