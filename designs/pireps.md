# PIREPs

> Crowdsourced pilot weather reports for European airspace — as-built (M0/M1).
> Forward-looking milestones (watches/notifications, model validation) remain in
> [future/pirep-plan.md](./future/pirep-plan.md).

## Intent

Two goals, in this order:

1. **Near-real-time condition sharing** — pilots report what they actually
   encountered along a route.
2. **Longitudinal model validation** — compare NWP forecasts against observed
   conditions. This is the long game and it drives several otherwise-odd
   decisions below (notably retention and anonymise-on-delete).

Targets **European airspace specifically**, because no equivalent to the US PIREP
system exists there. Data is sparse by design at first; value accrues over time.

## Scope decision: Europe only, enforced server-side

- **Europe** — full feature; community PIREPs fill a real gap.
- **US** — do **not** show community PIREPs. Official AWC/ADDS PIREPs are
  authoritative, and mixing community reports in would muddy provenance. Any
  future US support should ingest official PIREPs instead.

`storage/pireps.py::validate_european_bounds` rejects submissions outside the
configured box. This is a server-side gate, not a UI convenience — it is what
keeps the provenance claim true.

## Data model

`PirepRow` is deliberately **flat** — no session or track entity. A PIREP is a
discrete point observation, optionally linked to a flight via `pack_id` and to an
aircraft via `aircraft_id`. Client-generated UUIDs make submission idempotent, so
the iOS offline queue can retry without creating duplicates (see
[ios-app-data-models.md](./ios-app-data-models.md)).

Fields follow the US PIREP structure plus ceiling/tops additions: location + time
(GPS), altitude (FL, GPS-prefilled and editable), aircraft type (from the user
aircraft registry), cloud/ceiling (+ ceiling MSL), cloud tops, icing intensity and
type, turbulence, temperature, wind, remarks.

**`tops_basis` is the non-obvious field.** It qualifies the cloud-tops value:

| Value | Meaning |
|---|---|
| `crossed` | Exact altimeter reading taken through the top |
| `estimated_above` | Visual estimate made from above |
| `below_min` | The value is a **lower bound only** — the pilot never got above the tops |

Treating `below_min` as an exact top is the easy mistake here; it will silently
bias any validation dataset built on tops.

## Permissions and rate limiting

Two flags in `app_prefs_json`, **both defaulting to FALSE** during beta:

- `pirep_can_view` — gates viewing. *Post-rollout intent: this becomes ignored
  and all authenticated users see PIREPs.* It is a beta gate, not a permanent
  privacy control.
- `pirep_can_publish` — gates submitting, and stays permanently so an abusive
  user can be switched off.

Trust model: anyone holding the publish flag is trusted. There is **no
pre-moderation queue** — deliberately, since a queue nobody drains is worse than
none.

Rate limits are enforced server-side (`throttle.py`), and clients should debounce
independently: `pirep_burst_limiter` = 1 per 120 s (single submits only), and
`pirep_daily_limiter` = 50/day. The **batch route charges the daily limiter
`len(items)`**, so "50/day" means 50 PIREPs, not 50 API calls — that asymmetry is
intentional and easy to break when touching the batch path.

Visibility of *linked* PIREPs is resolved separately from the view flag: a PIREP
attached to a pack is only visible to viewers entitled to that flight
(`_visible_linked_pack_ids`, `_assert_can_view_flight` / `_assert_can_view_pack`).

## Presentation rules that are design, not polish

These come from the "attention-director, never a verdict" posture and should not
be optimised away:

- **Staleness is explicit.** Reports older than ~90 min are flagged visually and
  **never quietly hidden** — a hidden stale report reads as "no hazard".
- **Sparse coverage must be visible.** An empty map is not clear skies, and must
  not look like it.
- The situational-awareness disclaimer belongs **in the UI, not buried in ToS**:
  "for situational awareness only — not a substitute for official weather
  briefings".

## Privacy, retention, GDPR

- **Account deletion anonymises; it does not delete.** `user_id` and
  `aircraft_id` are set to NULL and the observation is retained as an anonymous
  record. This is what keeps the validation dataset intact while honouring
  erasure — and it must be stated **at submission time**, not discovered later
  ("stored permanently for weather research; deleting your account anonymises
  your reports").
- **No GPS tracks are stored.** PIREPs are discrete point observations only.
- **PIREP-linked packs are exempt from *all* retention tiers**, not just T2
  (`tasks/retention.py`: `if pack.id in pirep_pack_ids: continue`, before any
  tier logic runs). The forecast a pilot was actually looking at survives
  alongside their report — without that pairing the model-validation goal is
  unreachable. Note this is stricter than the debrief exemption, which only
  waives T2 and still strips heavy artifacts at T1; see [debrief.md](./debrief.md).
  Gotcha: `retention.py` **inlines** the query rather than calling
  `storage/pireps.py::pack_ids_with_pireps`, so that helper is currently dead
  code — change the inlined copy, or the exemption silently won't move.

## Key exports

`PirepRow` (`db/models.py`); `validate_european_bounds`, `create_pirep`,
`get_pirep`, `list_pireps` (`storage/pireps.py`; `pack_ids_with_pireps` exists
but is unused — see retention note above);
`api/pireps.py` router (`POST /api/pireps`, `POST /api/pireps/batch`,
`GET /api/pireps`); `can_view_pireps` / `can_publish_pireps`
(`api/preferences.py`).

## References

- Endpoint contract and spatial queries: [ios-app-server-api.md](./ios-app-server-api.md)
- iOS offline queue: [ios-app-sync-prompting.md](./ios-app-sync-prompting.md)
- Unbuilt milestones (watches, notifications, validation dataset): [future/pirep-plan.md](./future/pirep-plan.md)
