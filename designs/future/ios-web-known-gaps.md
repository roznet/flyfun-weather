# iOS ↔ Web Known Gaps

Deliberate feature gaps between the web app and the iOS companion app. These are
**decisions**, not bugs — surfaces we chose not to port (yet) and the reasoning,
so a future "should we build this on iOS?" starts from context instead of
rediscovery. When a gap is closed, move it to the bottom under "Closed".

Related: [ios-app-roadmap.md](../ios-app-roadmap.md) (phase plan + open questions),
[ios-app-overview.md](../ios-app-overview.md).

---

## No advisory recalculate on iOS

**Added:** 2026-07-07
**Web location:** `web/ts/managers/advisories-ui.ts` — profile selector rendered
at `:465` (`advisory-profile-select`) and wired at `:548`; altitude-override
slider rendered at `:476` and wired at `:599`; the `onRecalculate` callback is
the option at `:368`, wired to the recalculate button at `:563`. →
`POST /api/flights/{id}/packs/{ts}/advisories/recalculate` (`recalculate_advisories`,
`api/packs.py:3080`, `tasks/advise.py:run_advisories_from_pack`).

**Context — the Refresh-vs-recalculate decision.** Two distinct operations:

- **Refresh** = *get new data* — new model runs, or on D-0 live METAR/TAF/SIGMET
  (the tiered `decide_refresh` gate, `api/packs.py:796`). A user pressing Refresh
  expects the whole briefing, including the AI summary, to reflect the latest
  *weather*. Shared endpoint, identical on web + iOS.
- **Recalculate** = *re-grade the same forecast against changed settings*
  (altitude, profile, icing/cloud/convective method, advisory params,
  aggregation). No new data came from the sky, so the AI digest is intentionally
  **not** regenerated — the `digest.profileMismatch` banner flags it as written
  for the prior settings, with regeneration explicit/opt-in.

We deliberately did **not** fold a recalc mode into the Refresh gate: it would
overload Refresh's meaning and create the false expectation that the AI summary
rewrites itself on an altitude nudge. Instead, recalculate lives **inside the
advisory section**, next to the altitude/profile controls, so its scope is
self-evident ("this recomputes *these*, not the rest").

**The gap.** That in-section recomputation is **web-only**. On web the profile
selector and altitude slider re-grade in place. iOS has **no in-briefing
recalculate control** — it only recomputes advisories via the *edit-flight* flow
(`AddFlightViewModel.regenerateBriefing(for:invalidation:)`,
`app/.../ViewModels/AddFlightViewModel.swift:750`), which reacts to the
`PATCH /api/flights/{id}` `invalidation` hint (`advisories_only` →
`recalculateAdvisories()`). So an iOS user who wants to try a different
altitude/profile *on an existing briefing* can't, short of editing the flight.

**Decision.** Accept the gap for now. The backend endpoint already exists and is
client-agnostic, so this is purely a client-UI gap. Porting it properly is a
**full feature**, not a button: iOS would need the advisory-section controls
(altitude-override slider with the altitude-table delta note, owner-only profile
selector) *and* the recalc/re-render wiring — i.e. the same control suite web
grew incrementally. A bare "Recalculate" button with nothing to change first
would be pointless.

**Revisit / to build when we do:**
- Port the advisory-section controls (altitude slider + profile selector) to the
  iOS briefing view, reusing `recalculateAdvisories()` (already in
  `BriefingRepository`).
- Optional **staleness hint**: a stored `advisory_inputs_hash` on the pack
  (reuse `_load_advisory_profile`'s assembled inputs) lets the button surface
  "settings changed since these advisories were computed" — also closes the
  invisible case where the *profile's contents* were edited elsewhere (touches
  no flight, fires no `invalidation`). Applies to web too.
- Keep Refresh data-only on both clients; do **not** add a `recalc` mode to
  `decide_refresh`.

---

## No profile edit page on iOS

**Added:** 2026-07-07
**Web location:** profile CRUD via `api/profiles.py` (`FlightProfileRow.settings_json`,
`ProfileSettings` schema `:31`) + web profile management UI.

**The gap.** iOS can **select** an existing profile when creating/editing a
flight (`AddFlightViewModel` `selectedProfileId`, applies the profile's
altitude/ceiling), but there is **no page to create or edit a profile's
contents** — name, advisory enabled/params, aggregation, icing/cloud/convective
method, `advisory_models`, `auto_front_detection`. Those settings live in
`FlightProfileRow.settings_json` and are only editable from the web app.

**Decision.** Accept for now. Profile authoring is a lower-frequency, form-heavy
task that fits the larger screen; the iOS app's job today is briefing
consumption + flight setup, and selecting a pre-built profile covers the common
case. This gap compounds the one above: without profile editing *and* without
in-briefing recalculate, an iOS-only user is limited to whatever profiles they
built on web.

**Revisit / to build when we do:**
- A profile list + editor screen driven by the advisory catalog
  (`GET /api/.../advisories/catalog` gives parameter defs), mirroring the web
  form. Naturally pairs with the advisory-recalculate port above.

---

## Closed

_(none yet)_
