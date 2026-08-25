# iOS ↔ Web Known Gaps

Deliberate feature gaps between the web app and the iOS companion app. These are
**decisions**, not bugs — surfaces we chose not to port (yet) and the reasoning,
so a future "should we build this on iOS?" starts from context instead of
rediscovery. When a gap is closed, move it to the bottom under "Closed".

Related: [ios-app-roadmap.md](../ios-app-roadmap.md) (phase plan + open questions),
[ios-app-overview.md](../ios-app-overview.md).

---

## No observed conditions on iOS

**Added:** 2026-08-25
**Web location:** the `observed-tops` / `observed-surface` cross-section layers
(`web/ts/visualization/cross-section/layers/observed-*.ts`), the map overlay
(`web/ts/visualization/route-map/observed-overlay.ts`), the two route-graph
metrics, and the "Observed now" briefing section
(`renderObservedConditions` in `web/ts/managers/briefing-ui.ts`). Payload:
`observed_conditions` on `briefing.json`; imagery from `/api/observed`.

**Context.** Issue #574 scopes the iOS `/observed` endpoint out of phase 1
explicitly. The sampled payload already rides inline on `briefing.json`, so an
iOS client gets it for free the moment it decodes the field — it is the
*imagery* that has no iOS story yet, and that is where the work is: the
overlay is a server-rendered plate-carrée PNG sized to a corridor bbox, which
means a per-request render rather than a cacheable tile, and the offline pack
would either have to bundle a frame that is stale by definition or leave the
layer blank in the air.

**Why the gap is safe today.** The Swift decoders ignore unknown keys, so
adding `observed_conditions` to the snapshot breaks nothing. An iOS build that
wants the numbers can decode them without any server change.

**When to close it.** Together with phase 2, not before: phase 1 deliberately
computes no verdict, and the cross-check it offers is *visual* — cloud tops
drawn over the NWP cloud bands. Porting a visual cross-check to a second
renderer costs the same work twice and produces two things to keep in step
(`sync-ios-web`). Once phase 2 computes `echo_match` / `intensity_match` as
data, iOS can show the result without reimplementing the picture.

---

## No advisory recalculate on iOS

**Added:** 2026-07-07
**Web location:** `web/ts/managers/advisories-ui.ts` — profile selector rendered
at `:465` (`advisory-profile-select`) and wired at `:548`; altitude-override
slider rendered at `:476` and wired at `:599`; the `onRecalculate` callback is
the option at `:368`, wired to the recalculate button at `:563`. →
`POST /api/flights/{id}/packs/{ts}/advisories/recalculate` (`recalculate_advisories`
in `api/packs.py`, → `tasks/advise.py:run_advisories_from_pack`). Sibling endpoint
`advisories/preview` grades *draft* settings with `persist=False` for the settings
page — recalculate reads the **saved** profile and **writes** into the pack, so the
two are not interchangeable.

**Context — the Refresh-vs-recalculate decision.** Two distinct operations:

- **Refresh** = *get new data* — new model runs, or on D-0 live METAR/TAF/SIGMET
  (the tiered `decide_refresh` gate in `api/packs.py`). A user pressing Refresh
  expects the whole briefing, including the AI summary, to reflect the latest
  *weather*. Shared endpoint, identical on web + iOS.
- **Recalculate** = *re-grade the same forecast against changed settings*
  (altitude, profile, icing/cloud/convective method, advisory params,
  aggregation). No new data came from the sky, so the AI digest is intentionally
  **not** regenerated — a banner flags it as written for the prior settings,
  with regeneration explicit/opt-in.

  **Both clients now carry that staleness banner** (this part is no longer a
  gap): web has `digest.profileMismatch` and `digest.altitudeMismatch`
  (`buildDigestAltitudeWarning`, `web/ts/managers/briefing-ui.ts`), iOS has the
  altitude one in `Views/Briefing/DigestAltitudeWarning.swift`. Read its header
  comment before touching either — the warning must compare the flight altitude
  against `SnapshotResponse.route` (i.e. `briefing.json`, the one artifact
  recalculate leaves alone), *not* `AdvisoriesResponse.cruiseAltitudeFt`, which
  recalculate rewrites and which would make the warning silently dead. iOS has
  no profile-mismatch counterpart yet — it cannot change profile in-briefing
  (the gap below), so the case can only arise via an edit elsewhere.

We deliberately did **not** fold a recalc mode into the Refresh gate: it would
overload Refresh's meaning and create the false expectation that the AI summary
rewrites itself on an altitude nudge. Instead, recalculate lives **inside the
advisory section**, next to the altitude/profile controls, so its scope is
self-evident ("this recomputes *these*, not the rest").

**The gap.** That in-section recomputation is **web-only**. On web the profile
selector and altitude slider re-grade in place. iOS has **no in-briefing
recalculate control** — it only recomputes advisories via the *edit-flight* flow
(`AddFlightViewModel.regenerateBriefing(for:invalidation:)`,
`app/.../ViewModels/AddFlightViewModel.swift`), which reacts to the
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
method, `advisory_models`, `auto_front_detection`, plus the newer
`digest_guidance`, `flight_rules` and the setup-`interview` answers (#387).
Those settings live in `FlightProfileRow.settings_json` and are only editable
from the web app. The axis list keeps growing, which raises the porting cost
each time — see `ProfileSettings` in `api/profiles.py` for the current set.

**Decision.** Accept for now. Profile authoring is a lower-frequency, form-heavy
task that fits the larger screen; the iOS app's job today is briefing
consumption + flight setup, and selecting a pre-built profile covers the common
case. This gap compounds the one above: without profile editing *and* without
in-briefing recalculate, an iOS-only user is limited to whatever profiles they
built on web.

**Revisit / to build when we do:**
- A profile list + editor screen driven by the advisory catalog
  (`GET /api/user/preferences/advisories/catalog` gives parameter defs),
  mirroring the web form, with `advisories/preview` for the "what would change"
  pane. Naturally pairs with the advisory-recalculate port above.

---

## Closed

_(none yet)_
