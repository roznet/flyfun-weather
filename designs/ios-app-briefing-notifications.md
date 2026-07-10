# iOS App — Briefing Refresh Notifications (APNs)

> Push a notification to the app when a briefing finishes refreshing, so the pilot
> knows to look — and so a Siri-triggered refresh can truthfully say "I'll let you
> know when it's ready."

**Status: IMPLEMENTED (#366) — server + iOS client.** The full
server side is built and tested (`tests/test_briefing_notifications.py`):

- `notify/push.py` — token-based APNs sender (httpx HTTP/2 + PyJWT ES256,
  cached ~50 min), per-token sandbox/production routing, dead-token pruning.
- `notify/dispatch.py` — the single notification gate + channel dispatch, emitted
  once from the shared post-commit sink `api/packs.py::_notify_refresh_complete`
  (called by each refresh path *after* it commits the pack) so it covers
  auto / in-app / Siri / MCP without notifying about a pack that could still roll
  back. Email moved here from the scheduler.
- `notify/badge.py` + `api/notifications.py` — server-derived cross-surface badge
  (`flight_briefing_seen` table), `GET /api/flights/badge`,
  `POST /api/flights/{id}/seen` (+ silent badge-sync push on web read).
- `api/devices.py` — `POST /api/devices` (upsert), `DELETE /api/devices/{token}`.
- Preferences (`app_prefs_json`): `notify_email` / `notify_push` / `notify_scope`
  / `notify_change_only`; per-flight `FlightRow.notify_override`. Migration `075`.

**iOS client (built on #364's app-shell):**

- `Services/PushNotifications.swift` — `AppDelegate` (`@UIApplicationDelegateAdaptor`)
  + `UNUserNotificationCenterDelegate`: registers the APNs token, uploads it via
  `POST /api/devices`, foreground-suppresses banners, handles the silent
  badge-sync push, and deep-links a tapped push through the **existing
  `PendingNavigation` seam** (same path as `onOpenURL` / App Intents). Pure
  `PushSupport` helpers (hex, payload→nav) are unit-tested
  (`PushNotificationsTests.swift`).
- `AppState` — `uploadDeviceToken` / `unregisterPushDevice` (on sign-out) /
  `reconcileBadge` / `markBriefingSeen` / `requestPushAuthorizationAndRegister`.
  Badge reconcile + token refresh run in the existing `scenePhase == .active`
  block; mark-seen fires when a briefing opens (`BriefingContainerView`).
- Settings → a "Briefing Push Alerts" toggle (`SettingsView`) that requests iOS
  authorization, registers, and writes `notify_push` via
  `PUT /api/user/preferences`. Entitlement `aps-environment` + the
  `remote-notification` background mode are added.

Deployment must set the APNs secrets (see "APNs key management" below).

**Preferences UI + semantics (#371) — server + web + iOS.** The `notify_*` prefs
are now fully controllable on both clients, with the semantics tightened:

- **Clean WHEN/HOW split with unified presence** (`notify/dispatch.py`). One
  channel-agnostic **WHEN** decision — `notify_qualifies` (scope + override +
  change-only) **AND not `present`** — gates the badge and both channels. If it
  passes, **HOW** is pure user preference: deliver on each enabled channel
  (email/push), independent of who/what/where triggered the refresh. This is the
  invariant that lets "push only, never email — even when I refresh on the web"
  work: the channel choice knows nothing about the trigger.
- **Presence** = "was the user actively watching this refresh finish?" Recorded
  server-side in the refresh registry (`touch_watch`/`is_watched`, `WATCH_PRESENCE_TTL`
  = 30s) from any UI holding the SSE stream (keepalive) or polling
  `/packs/refresh/status`. The **same signal for web and iOS** (both share those
  endpoints), and robust to the stream→poll handoff on navigate-away-and-back
  (the 3s poll keeps it fresh; TTL exceeds the 15s SSE keepalive). The flight-list
  poll (`/refresh/active`) deliberately does NOT count — being on the list ≠
  viewing the briefing. Computed once in `_notify_refresh_complete`, uniform
  across all three refresh paths (scheduler / sync / streaming).
- **`?source=`** (`user` | `siri` | `mcp`; unknown → `user`, `scheduler` is
  internal-only) rides on `BriefingUsage.triggered_by` for **usage attribution
  only** — it no longer affects notifications (presence replaced the old
  per-channel email rule). Legacy `scope="auto"` is read as "on"; the UI only
  ever writes `all` or `off`.
- **Channel invariant.** Channels never express "off": the only way to silence is
  scope/override. The web + iOS Settings enforce this live (email locks on when
  it's the sole available channel; turning off the last channel reroutes to
  Briefing updates = Off). The **decay** fail-safe is server-side
  (`preferences.apply_last_device_decay`, fired on BOTH device-loss paths —
  `devices.unregister_device` on explicit sign-out, and `notify/push.py::_dispatch`
  when an APNs send prunes the last dead token, the common
  app-deleted-without-sign-out case): losing the *last* device while
  `notify_email` is off **and scope ≠ off** re-enables email and
  raises a one-time `notify_decay_notice` (surfaced in the prefs response,
  dismissed by PUTting it false). Prefs also carry `push_device_count` so a
  device-less web user sees an "install the app" push row.
- **UI shape.** Account › Notifications: a **Briefing updates** 3-stop (Off /
  Assessment changes / Every update) folding `notify_scope` + `notify_change_only`,
  an **Email** toggle, and a device-conditional **Push** toggle. Per-flight: a
  **bell** (Default / Always / Mute → `notify_override`) in the freshness bar
  (web) / briefing toolbar (iOS), whose hint shows what Default resolves to. The
  old per-refresh "Email me when done" checkbox is **retired** — server-side
  presence subsumes it: a manual refresh you walk away from now notifies (the
  poll/stream contact goes stale), and one you watch finish does not. `force_email`
  / `?notify_email=` are gone.

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
| Single post-commit notify sink | `api/packs.py::_notify_refresh_complete` — called by each refresh path (scheduler, sync `_finalize_refresh`, streaming) *after* it commits the pack | Emit the push **once** here → covers auto, in-app, and Siri/MCP refreshes in one place, after commit so we never notify about a pack that rolls back |
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
- **Emit once from the shared post-commit sink** `api/packs.py::_notify_refresh_complete` (after each path commits its pack), guarded
  like `_try_send_email` (log-and-skip on any failure — a push must never break a refresh).
  Every refresh (auto, in-app, Siri/MCP) funnels through this one function, so a single
  hook covers them all — including the `RefreshBriefingIntent` loop. Do **not** copy the
  email's scheduler-only placement (email fires only for auto-refresh; push must be broader).
- **Payload**: `alert` title/body from route + new assessment + `compute_refresh_delta`
  ("EGTF → LFAT: now AMBER, ceiling worsened"); custom data `{ flight_id, timestamp }`;
  `aps.badge` = server-computed unseen count (see Badge section).
- **Send gating** — only on a *new* pack whose assessment changed or worsened (avoid
  "still green" spam). Configurable; default to meaningful-change-only.
- **In-app suppression** — a user watching the SSE progress bar in-app shouldn't also get a
  banner. **Resolved by server-side presence** (#371): the notification decision suppresses
  entirely when the user is *watching this refresh finish*, detected from watch-contact in
  `refresh_registry` (SSE keepalive or `/packs/refresh/status` poll; see the presence bullet
  above) — the same signal for web and iOS, so "distinguish 'I'm watching this' from 'tell me
  when done'" is a server decision now, not a client-only heuristic. iOS
  `UNUserNotificationCenterDelegate.willPresent` foreground banner-suppression remains as a
  harmless delivery backstop (badge still syncs).
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
- **Scope** (*which refreshes* — single choice) → `notify_scope`. **As shipped (#371)**
  the scope + change-only pair is presented as one **Briefing updates** 3-stop and the
  manual-vs-automatic line moved to the per-channel email rule, so scope collapses to
  on/off:
  - `off` — never, unless a flight is set to Always per-flight.
  - `all` — notifications on (the 3-stop's Assessment-changes / Every-update stops both
    write `all`, differing only by `notify_change_only`).
  - `auto` — **legacy** (pre-#371 default); read as "on", never written by the UI.
- **Content filter** → `notify_change_only` (default **on**): only when the assessment
  changed/worsened (`compute_refresh_delta`), vs every completion.
- **Timing** → migrate the existing `defer_email_for_model_update` into this group,
  channel-agnostic ("wait for an imminent model run before notifying" — applies to push too).
- *(Advanced, optional)* **Quiet hours** — suppress **push** overnight (local); email unaffected.

**Per-flight override** (flight settings — generalizes today's "email me when done"), on `FlightRow`:

- `notify_override`: `default` (follow global) | `on` (notify for **any** completion of this
  flight, even if global is `off`/`auto`) | `mute` (never for this flight). Channels always
  follow the global channel selection.

**Effective decision** (evaluated in `notify/dispatch.py`, driven from `_notify_refresh_complete`):

```
# Shared gate (notify_qualifies) — drives the badge and both channels:
if flight.notify_override == "mute":  stop
elif flight.notify_override == "notify":  qualifies    # any completion
else:                                                  # default → global scope
    scope == "off" → stop
    else (all / legacy auto) → qualifies
if notify_change_only and not delta.changed:  stop
advance the badge
# Per-channel trigger rule (#371):
push  → deliver on every qualifying refresh
email → deliver only if triggered_by is non-user-present (skip a bare "user")
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

**State (per user × flight) — a flight counts at most once:**
- `latest_pack_ts` — the flight's newest pack (already known server-side).
- `last_notified_pack_ts` — pack ts of the most recent notify-qualifying update.
- `last_seen_pack_ts` — set to the flight's **current `latest_pack_ts`** when the user opens
  that flight's briefing (web or app).
- Flight is **unseen** iff `last_notified_pack_ts > last_seen_pack_ts`.
- **Badge = count of unseen flights** (each flight 0 or 1), computed server-side on demand
  (like `unseen_count`).

This is exactly the intended semantics: if a flight refreshes **twice** and neither is
opened it still counts **once** (we compare the *latest* notified pack, not each pack);
opening the briefing marks the current latest pack seen and clears the flight no matter how
many packs piled up; older unopened packs never add to the count.

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
- **Count once per flight** — the badge counts *flights with an unseen latest update*,
  never packs. Two unopened refreshes on one flight = 1.
- **What clears "unseen"?** Opening that flight's **briefing detail** (web or app) — not
  merely seeing it in the list. Opening sets `last_seen_pack_ts = latest_pack_ts`, so the
  flight clears even if several packs accumulated and only the newest was viewed.
- **What counts toward the badge?** Only **notify-qualifying** updates (same gate as the
  notification: scope + change-filter + not muted) — mirroring "only highlighted messages
  light the dot". A later non-qualifying refresh (e.g. unchanged) does **not** re-light a
  flight you already cleared.
- **Badge vs push-channel toggle** — reconcile keeps the badge correct even if *alert* push
  is off; recommend the badge follows "a device is registered", independent of alert on/off.

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
emit from _notify_refresh_complete    ┘    ("…I'll let you know when it's ready")
```

`OpenBriefingIntent` / `OpenFlightListIntent` / `CheckBriefingIntent` do **not**
depend on this doc and can ship first. Only the refresh intent's satisfying
close-the-loop UX needs the push.

## Open Questions / Decisions needed

Resolved by the preferences model above: *notify-when-done vs watching-live* (a push
**delivery rule** — foreground suppression — plus the `all`/`auto` scope), and *dedup with
email* (channels are independent user choices). Remaining:

- **APNs provider library** — APNs is just HTTP/2 + a token (ES256 JWT from the `.p8`, header
  `{kid}`, claims `{iss: team_id, iat}`, cached ~50 min). Options: **(a) roll it on `httpx`**
  (already a dep; add `httpx[http2]` + `PyJWT` — ~120 lines, full control over retries and
  response→token-cleanup, no bit-rot risk); **(b) `aioapns`** (async, maintained, token auth,
  handles HTTP/2). **Avoid `PyAPNs2`/`apns2`** (built on the unmaintained `hyper`). Recommend
  (a). Send: `POST /3/device/<token>` with `apns-topic: <bundle id>` and `apns-push-type:
  alert` (or `background` for silent badge syncs); on `BadDeviceToken`/`Unregistered` delete
  the row.
- **APNs key management** — a **single `.p8` token-auth key works for BOTH environments**
  (unlike the old cert auth). Store key + key id + team id as deployment secrets (same
  handling as the existing Resend key). **As built**, `ApnsConfig.from_env()` reads:
  `APNS_KEY_P8` (PEM contents; or `APNS_KEY_P8_PATH` for local dev), `APNS_KEY_ID`,
  `APNS_TEAM_ID`, `APNS_BUNDLE_ID` (→ `apns-topic`). Missing config → log-and-skip.

**Decisions locked during #366 (server half):** APNs library = **httpx-rolled**
(chosen); seen storage = **`flight_briefing_seen` table** (not a prefs blob);
device scope = **all of the user's devices** (simpler, consistent with
auto-refresh); change detection = **assessment/outlook transition vs the prior
pack** (a first briefing counts as changed; a GREEN→AMBER worsening also carries
a short delta message for the push body); quiet hours = **deferred** (not in v1).
- **Sandbox vs production routing** — per-**token**, decided by the *app build on the device*,
  **not** by which server runs: an Xcode **debug** build → `aps-environment: development` →
  **sandbox** token → sandbox host; **TestFlight and App Store** → `production` → production
  host (note: **TestFlight is production**, a common trap). The token value doesn't reveal its
  environment, so the client reports it at register time (`#if DEBUG` → `sandbox` else
  `production`; or read `aps-environment` from the embedded profile for full precision) and the
  server routes on `device_tokens.environment`. Wrong host → misleading `BadDeviceToken`. Local
  dev: a debug build on a real device → sandbox token → the local dev server (with the `.p8`
  configured) sends to the **sandbox** host — no separate prod credential needed for dev.
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
