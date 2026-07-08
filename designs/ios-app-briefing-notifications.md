# iOS App — Briefing Refresh Notifications (APNs)

> Push a notification to the app when a briefing finishes refreshing, so the pilot
> knows to look — and so a Siri-triggered refresh can truthfully say "I'll let you
> know when it's ready."

**Status: PROPOSED — partially foundationed.** A `device_tokens` table already
exists (`src/weatherbrief/db/models.py::DeviceTokenRow`), and the auto-refresh
scheduler already sends an **email** on completion. What's missing: the client
registration flow, the APNs sender, and wiring the send at the refresh-complete
seams. Push notifications are the outstanding item in Phase 2 M2 of the
[roadmap](./ios-app-roadmap.md).

## Related Docs

- [App Intents](./ios-app-intents.md) — the `RefreshBriefingIntent` whose loop this closes
- [Overview](./ios-app-overview.md) — "push notifications for briefing updates" listed as not-built
- [Architecture](./ios-app-architecture.md) — auth, deep links (`flyfunweather://`, universal links)
- [Server API](./ios-app-server-api.md) — endpoints consumed/added
- [Multi-user deployment](./multi-user-deployment.md) — secrets, Resend email precedent

## What already exists (build on, don't duplicate)

| Piece | Where | Reuse |
|---|---|---|
| Device token table + migration | `db/models.py::DeviceTokenRow` (`token`, `environment` `String(16)`, `user_id`); migration `032_pireps_and_device_tokens.py` | Table already created & migrated — just add the endpoint |
| GDPR handling | `api/account_export.py` (token **excluded as a credential**, not exported) + `api/app.py` account-deletion purges rows | Delete/export already correct — nothing to add |
| Single refresh-finalize sink | `api/packs.py::_persist_pack_finalize` — the shared function called by `_finalize_refresh` (scheduler + manual) **and** the streaming path | Emit the push **once** here → covers auto, in-app, and Siri/MCP refreshes in one place |
| Email precedent | `scheduler.py::_try_send_email` (auto-refresh only, ~L452) | Mirror as `notify/push.py`; but hook the shared finalize, since push must ALSO cover manual/Siri (email doesn't) |
| Change detection | `compute_refresh_delta` / worsened-conditions banner (`metar-taf-route-weather`) | Craft the body ("now AMBER — conditions worsened") + gate noisy sends |
| Model-update-aware defer | `scheduler.py` issue #192 email-timing logic | Push inherits the same defer window — do not notify before/twice |

## Design

### Client (iOS)

1. Register for remote notifications; obtain the APNs device token.
2. `POST /api/devices` with `{ token, environment }` (`environment` = `sandbox` |
   `production`, since a TestFlight/dev build's token is APNs-sandbox and must not
   be sent via the prod APNs host). Upsert on the existing `device_tokens` row.
3. Re-register on token change; **unregister on sign-out** (`DELETE /api/devices/{token}`)
   so a signed-out device stops receiving another user's briefings.
4. On tap: read `flight_id` + `timestamp` from the payload and deep-link to the
   updated briefing — reuse the [App Intents](./ios-app-intents.md) `PendingNavigation`
   seam (or the existing `flyfunweather://` / universal-link handler), so tap and
   "open my next FlyFun briefing" land on the same code path.

### Server

- **`notify/push.py`** — token-based APNs (`.p8` key, HTTP/2, `apns-topic` = bundle id).
  One function `send_briefing_push(user_id, flight, meta, *, delta)` mirroring
  `send_briefing_email`. Route to the correct APNs host per stored `environment`.
- **Emit once from the shared finalize** `api/packs.py::_persist_pack_finalize`, guarded
  like `_try_send_email` (log-and-skip on any failure — a push must never break a refresh).
  Every refresh (auto, in-app, Siri/MCP) funnels through this one function, so a single
  hook covers them all — including the `RefreshBriefingIntent` loop. Do **not** copy the
  email's scheduler-only placement (email fires only for auto-refresh; push must be broader).
- **Payload**: `alert` title/body from route + new assessment + `compute_refresh_delta`
  ("EGTF → LFAT: now AMBER, ceiling worsened"); custom data `{ flight_id, timestamp }`;
  optional badge.
- **Send gating** — only on a *new* pack whose assessment changed or worsened (avoid
  "still green" spam). Configurable; default to meaningful-change-only.
- **In-app suppression** — a user watching the SSE progress bar in-app shouldn't also get
  a push. But an in-app manual refresh and a Siri/background refresh are **both**
  `triggered_by="user"` today (`refresh_registry` only knows `user | scheduler`), so the
  server can't yet tell them apart. See the decision in Open Questions — baseline is
  client-side foreground suppression; optional server signal (`triggered_by="intent"` /
  `notify_on_complete`) for precise targeting.
- **Endpoint** `POST /api/devices` (register/upsert) + `DELETE /api/devices/{token}`.

### Notification preferences

Today email-on-completion is **implicit** — it fires whenever a flight's `auto_refresh`
runs (when SMTP is configured), with one timing refinement (`defer_email_for_model_update`).
There is no separate per-flight email flag; "email me when done" ≈ `auto_refresh` on.
This generalizes into an explicit, channel-aware model with a per-flight override.

**Two orthogonal axes + a per-flight override.**

**Global** (Settings → Notifications), stored in `app_prefs_json`:

- **Channels** (*where* — independent, either/both/neither):
  - Email → `notify_email` (default **on** if the account has an email).
  - iOS push → `notify_push` (default **off**; conditional UI — show device state
    ("2 devices") or an install hint; requires ≥1 `device_tokens` row).
- **Scope** (*which refreshes* — single choice) → `notify_scope`:
  - `auto` — only the scheduled near-departure auto-refresh (**default**; reproduces today).
  - `all` — every completion, incl. manual / Siri / MCP.
  - `off` — never, unless enabled per-flight.
- **Content filter** → `notify_change_only` (default **on**): only when the assessment
  changed/worsened (`compute_refresh_delta`), vs every completion.
- **Timing** → migrate the existing `defer_email_for_model_update` into this group,
  channel-agnostic ("wait for an imminent model run before notifying" — applies to push too).
- *(Advanced, optional)* **Quiet hours** — suppress **push** overnight (local); email unaffected.

**Per-flight override** (flight settings — generalizes today's "email me when done"), on `FlightRow`:

- `notify_override`: `default` (follow global) | `on` (notify for **any** completion of this
  flight, even if global is `off`/`auto`) | `mute` (never for this flight). Channels always
  follow the global channel selection.

**Effective decision** (evaluated at `_persist_pack_finalize`):

```
if flight.notify_override == "mute":  stop
elif flight.notify_override == "on":  send            # any completion
else:                                                  # default → global scope
    scope == "all"  → send
    scope == "auto" → send only if triggered_by == "scheduler"
    scope == "off"  → stop
if notify_change_only and not delta.changed:  stop
for each ON channel (email, push):  deliver
    # push additionally suppressed if the app is foregrounded on this flight —
    # a DELIVERY RULE, not a user setting (resolves the earlier ★ question)
```

### UX principles

- **Orthogonal controls, not a matrix.** Pick channels (multi-select) and one scope —
  avoid a combinatorial email-auto / email-all / push-auto / push-all grid. One scope
  applies to all selected channels.
- **Per-flight is an override, not a second settings screen** — a 3-way segmented control
  (Default / Notify / Mute) so the common case (follow global) is zero-thought.
- **Push is conditional UI** — only actionable with a registered device; otherwise show a
  gentle "install the app / enable notifications" hint.
- **Migration preserves today's behavior** — existing users default to Email **on**,
  scope `auto`, change-only **on**, push **off** until a device registers. No surprise.

### Other choices worth offering / deciding

- **★ DECIDED — per-flight notify is independent of auto-refresh.** They are two separate
  controls today and stay separate: the notify override never enables or schedules
  auto-refresh. (A per-flight "notify" is most useful precisely for manual/Siri refreshes
  on a flight whose auto-refresh is off — the Siri-loop case.)
- **Batching/digest** — the scheduler can finish several flights close together; a future
  digest could replace N separate pushes.
- **Per-channel scope** — deliberately NOT in v1 (one scope for all channels); revisit only
  if users ask for "email everything, push only auto".

*(Badge count / cross-surface sync has its own section below.)*

## Badge count & cross-surface sync

**Principle: the badge is a server-*derived* count, never a client-side increment.** Client
counters drift the instant a second surface (web, another device) reads an update or a push
is missed. There is already a precedent to mirror — system-message unseen count
(`api/messages.py`: `messages_last_seen_id` in prefs, server-computed `unseen_count`,
`POST /messages/seen`). We do the same, per-flight.

**State (per user × flight):**
- `last_notified_pack_ts` — timestamp of the most recent notify-qualifying update.
- `last_seen_pack_ts` — advanced when the user opens that flight's briefing on **web or app**.
- Flight is **unseen** iff `last_notified_pack_ts > last_seen_pack_ts`.
- **Badge = count of unseen flights**, computed server-side on demand (like `unseen_count`).

Storage: mirror messages (a `briefing_seen` map in `app_prefs_json`) or a small
`flight_briefing_seen(user_id, flight_id, ts)` table. Flights per user are few; either
works — lean to the table for cleanliness.

**Three mechanisms keep web ↔ app ↔ multi-device in sync:**
1. **Badge in every alert push** — when a notify-qualifying refresh completes, set
   `aps.badge = <current server unseen count>` in the payload, so the visible notification
   already carries the true number.
2. **Silent badge-sync push on state change** — when unseen changes for a reason *other*
   than a new alert (the user reads a flight on the **web**, or on another device), the
   server sends a **silent** push (`content-available: 1`, no alert, `aps.badge: N`) to the
   user's iOS devices so the badge drops to match. *This is what makes "read on web →
   app badge 2 → 1" work.*
3. **Authoritative reconcile on app foreground** — APNs (especially silent pushes) is
   best-effort and coalesced by Apple, never guaranteed. So on `scenePhase == .active` the
   app GETs the current count (`GET /api/flights/badge`, mirroring `/messages/status`) and
   sets the badge directly. This is the correctness backstop.

**Why this is accurate (and the naive version isn't):** the server count is the single
source of truth, push is an optimization, foreground-reconcile is the guarantee. The
annoying-badge failure mode is the opposite — incrementing/decrementing on the client and
trusting push delivery. The worry is well-founded; the ordering above is the fix.

**Definitions to lock:**
- **What clears "unseen"?** Opening the flight's **briefing detail** (web or app) — not
  merely seeing it in the list. Per-flight watermark (any newer pack read clears it), not per-pack.
- **What counts toward the badge?** Only **notify-qualifying** updates (same gate as the
  notification: scope + change-filter + not muted) — mirroring "only highlighted messages
  light the dot".
- **Badge vs push-channel toggle** — reconcile keeps the badge correct even if *alert* push
  is off; decide whether to show a badge when the push channel is disabled (recommend:
  badge follows "a device is registered", independent of alert on/off).

**New surface area:** `POST /api/flights/{id}/seen` (or fold into the existing briefing
GET), `GET /api/flights/badge`, the silent-push path, and the app's foreground reconcile +
mark-seen on briefing open. The **web** calls the same `seen` endpoint on briefing view —
that is what decrements the app badge.

## Sequencing vs App Intents

```
Notification work (this doc)                 App Intents (sibling doc)
──────────────────────────                   ─────────────────────────
device_tokens endpoint + client reg   ┐
notify/push.py + APNs key             ├─►    RefreshBriefingIntent
emit at both refresh-complete seams   ┘      ("…I'll let you know when it's ready")
```

`OpenBriefingIntent` / `OpenFlightListIntent` / `CheckBriefingIntent` do **not**
depend on this doc and can ship first. Only the refresh intent's satisfying
close-the-loop UX needs the push.

## Open Questions / Decisions needed

Resolved by the preferences model above: *notify-when-done vs watching-live* (a push
**delivery rule** — foreground suppression — plus the `all`/`auto` scope), and *dedup with
email* (channels are independent user choices). Remaining:

- **Per-flight notify ⇒ auto-refresh coupling** — see "Other choices" above.
- **APNs provider library** — hand-rolled HTTP/2 + `.p8` JWT, or a small dep that fits the
  async FastAPI stack?
- **APNs key management** — `.p8` key + key id + team id as deployment secrets
  (see [multi-user-deployment](./multi-user-deployment.md) secret handling).
- **Environment routing** — sandbox vs production host per stored `environment`; how to set
  it at build/runtime and keep straight across TestFlight vs App Store.
- **Manual-refresh device scope** — notify only the triggering device, or all the user's
  devices? (All is simpler and consistent with auto-refresh.)
- **Quiet hours** — whether to suppress push overnight in v1 (badge sync is unaffected).
- **Seen storage** — `briefing_seen` map in `app_prefs_json` (mirrors messages) vs a small
  `flight_briefing_seen` table (see Badge section).

## References

- Device token model: `src/weatherbrief/db/models.py::DeviceTokenRow`
- Refresh-complete seams: `src/weatherbrief/scheduler.py`, `src/weatherbrief/api/packs.py`
- Email precedent to mirror: `src/weatherbrief/notify/email.py`
- Change detection: `compute_refresh_delta` (see [metar-taf-route-weather](./metar-taf-route-weather.md))
</content>
