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
- Settings → a push toggle (`SettingsView`) that requests iOS authorization,
  registers, and writes `notify_push` via `PUT /api/user/preferences`. (#371
  reshaped this section into the Briefing-updates 3-stop + Email + Push described
  below.) Entitlement `aps-environment` + the `remote-notification` background
  mode are added.

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
  dismissed by PUTting it false). `update_preferences` normalizes the same way on
  write — a PUT that would leave no deliverable channel (`notify_push` with zero
  devices doesn't count) reroutes `notify_scope` to `off`, so the invariant holds
  even against a client that doesn't enforce it. Prefs also carry
  `push_device_count` so a device-less web user sees an "install the app" push row.
- **UI shape.** Account › Notifications: a **Briefing updates** 3-stop (Off /
  Assessment changes / Every update) folding `notify_scope` + `notify_change_only`,
  an **Email** toggle, and a device-conditional **Push** toggle. Per-flight: a
  **bell** (Default / Always / Mute → `notify_override`) in the freshness bar
  (web) / briefing toolbar (iOS), whose hint shows what Default resolves to. The
  old per-refresh "Email me when done" checkbox is **retired** — server-side
  presence subsumes it: a manual refresh you walk away from now notifies (the
  poll/stream contact goes stale), and one you watch finish does not. `force_email`
  / `?notify_email=` are gone.
- **UNAVAILABLE is not news (#392).** `notify_briefing_refresh` returns early when
  the new pack's assessment is `UNAVAILABLE` — no push, no email, **no badge**.
  Deliberately placed *ahead* of the WHEN gate, so it also wins over a per-flight
  `notify` override: an unassessable briefing says our data is missing, not that
  the pilot's weather changed. (`UNAVAILABLE` is likewise absent from
  `_ASSESSMENT_RANK`, so it never produces a "worsened" delta either way.)
- **iOS push state is two-valued.** `notify_push` on the server is the *effective*
  pref = local intent (`AppState.wantsPush`, UserDefaults, seeded from the server
  pref) **AND** iOS authorization. `reconcilePushAuthorization()` runs on every
  `.active` and re-syncs `notify_push` both directions, so revoking notifications
  in iOS Settings silences the server and re-granting auto-resumes it without
  re-tapping the toggle. `enablePush()` returns `needsSettings` when iOS already
  denied (it won't re-prompt) while *keeping* the intent.
- **A push also freshens the open briefing.** Both the alert and silent-push
  handlers call `AppState.signalExternalSync(flightId:)` — an observable nudge the
  flight list and any open briefing answer with `syncLatestPack()`. So a foreground
  push updates the visible pack, not just the badge, and push stays "one more sync
  trigger" rather than a special path.

## Related Docs

- [App Intents](./ios-app-intents.md) — the `RefreshBriefingIntent` whose loop this closes
- [Overview](./ios-app-overview.md) — push notifications listed as shipped; the *watch/spatial-match* layer (Phase 3 M2) is what remains unbuilt
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

Register for remote notifications → `POST /api/devices` with `{ token, environment }`
(`sandbox` | `production` — see the routing note below; upserts the `device_tokens` row).
Re-register on token change; **unregister on sign-out** (`DELETE /api/devices/{token}`) so a
signed-out device stops receiving another user's briefings. On tap, read `flight_id` +
`timestamp` from the payload and deep-link through the [App Intents](./ios-app-intents.md)
`PendingNavigation` seam — so a push tap, `flyfunweather://`, a universal link and "open my
next FlyFun briefing" all land on one code path.

### Server

- **`notify/push.py`** — token-based APNs (`.p8` key, HTTP/2, `apns-topic` = bundle id).
  As built: `send_briefing_push(db, user_id, flight, pack, *, delta, badge)` mirroring
  `send_briefing_email`, plus `send_silent_badge_push(db, user_id, badge)` and
  `count_user_devices`. Route to the correct APNs host per stored `environment`.
- **Emit once from the shared post-commit sink** `api/packs.py::_notify_refresh_complete`
  (after each path commits its pack), log-and-skip on any failure — a notification must never
  break a refresh. Every refresh (scheduler, sync `_finalize_refresh`, streaming — so auto,
  in-app, Siri/MCP) funnels through this one function, including the `RefreshBriefingIntent`
  loop. Do **not** re-scatter this the way the old scheduler-only email was placed.
- **Payload**: `alert` title/body from route + new assessment + `compute_refresh_delta`
  (`build_alert_body`); custom data `{ flight_id, timestamp }`; `aps.badge` = server-computed
  unseen count (see Badge section).
- **Send gating** — the WHEN gate above; default is meaningful-change-only
  (`notify_change_only`), to avoid "still green" spam.
- **In-app suppression** — a user watching the SSE progress bar shouldn't also get a banner.
  **Resolved by server-side presence** (#371), the same signal for web and iOS, so this is a
  server decision now rather than a client-only heuristic. iOS
  `UNUserNotificationCenterDelegate.willPresent` foreground banner-suppression remains as a
  harmless delivery backstop (badge still syncs).
- **Endpoint** `POST /api/devices` (register/upsert) + `DELETE /api/devices/{token}`.

### Notification preferences

*Pre-#366, email-on-completion was implicit: it fired whenever a flight's `auto_refresh` ran
(SMTP configured), with no per-flight flag — "email me when done" ≈ `auto_refresh` on. That
is what the model below replaced.*

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
- **Timing** → `defer_email_for_model_update` (still email-named, still in the *service
  toggles* group, not `notify_*`). It was never migrated and doesn't need to be: it shifts
  the **scheduled refresh itself** (`scheduler.py::_user_defers_for_model_update` →
  `_next_due_at`), so every channel inherits it for free. Rename only if it starts confusing
  users.
- *(Advanced, optional)* **Quiet hours** — not built (deferred out of v1); would suppress
  **push** overnight (local) with email unaffected. No `quiet_hours` key exists.

**Per-flight override** (flight settings — generalizes today's "email me when done"), on `FlightRow`:

- `notify_override`: `default` (follow global scope + change filter) | `notify` (**always** —
  notify for **any** completion of this flight, even if global is `off`/`auto` **and even when
  the assessment is unchanged**; the change filter does not apply) | `mute` (never for this
  flight). Channels always follow the global channel selection.

**Effective decision** — `notify/dispatch.py::notify_qualifies` + presence, driven from
`_notify_refresh_complete`. This pseudocode is the final (post-#371) form; an earlier draft
gated email per-`triggered_by`, which presence replaced:

```
# Single WHEN decision — presence AND the shared gate — drives badge + both channels:
if present:  stop                          # user was watching this refresh finish
if flight.notify_override == "mute":  stop
elif flight.notify_override == "notify":  qualifies    # ALWAYS — bypasses scope AND the change filter
else:                                                  # default → global scope + change filter
    scope == "off" → stop
    else (all / legacy auto) → qualifies
    if notify_change_only and not delta.changed:  stop
advance the badge
# HOW (channels) — pure user preference, trigger-agnostic:
push  → deliver if notify_push
email → deliver if notify_email
# (?source= / triggered_by is usage attribution only — it does NOT gate notifications)
```

### UX principles

- **Orthogonal controls, not a matrix** — channels (multi-select) × one scope, never an
  email-auto / email-all / push-auto / push-all grid.
- **Per-flight is an override, not a second settings screen** — 3-way (Default / Notify /
  Mute) so "follow global" is zero-thought.
- **Push is conditional UI** — only actionable with a registered device; otherwise an
  "install the app / enable notifications" hint (`push_device_count` drives this).
- **Defaults preserve pre-#366 behavior** — email **on**, scope `auto` (still the stored
  default; the UI writes only `all`/`off`), change-only **on**, push **off**. No surprise.

### Other choices

- **★ DECIDED — per-flight notify stays a separate control, but auto-refresh seeds a smart
  default.** The data model stays independent (the bell never enables or schedules
  auto-refresh, and per-flight `notify` is useful for manual/Siri refreshes on a flight whose
  auto-refresh is off — the Siri-loop case). But old auto-refresh meant "email me whenever a
  new report is ready", unconditionally, while the new `notify_change_only` default gates on
  assessment change — and a report's *detail* often moves while the assessment holds, so
  those users would perceive it as going silent. Fix: **enabling auto-refresh defaults that
  flight's bell to `notify`** (`api/flights.py::update_auto_refresh`), restoring the
  every-completion ping — which is why `notify` bypasses the change filter. A default, not a
  hard link: the user can drop the bell back afterward, and disabling auto-refresh leaves it.
- **Batching/digest** — the scheduler can finish several flights close together; a future
  digest could replace N separate pushes. Not built.
- **Per-channel scope** — deliberately NOT in v1; revisit only if users ask for "email
  everything, push only auto".

## Badge count & cross-surface sync

**Principle: the badge is a server-*derived* count, never a client-side increment.** Client
counters drift the instant a second surface (web, another device) reads an update or a push
is missed. There is already a precedent to mirror — system-message unseen count
(`api/messages.py`: `messages_last_seen_id` in prefs, server-computed `unseen_count`,
`POST /messages/seen`). We do the same, per-flight.

**State** — table `flight_briefing_seen` (`db/models.py::FlightBriefingSeenRow`, unique on
user×flight), maintained by `notify/badge.py`:
- `last_notified_ts` — pack ts of the most recent notify-qualifying update
  (`record_notify_qualifying`, called from the dispatch gate).
- `last_seen_ts` — set to the flight's **current latest pack ts** when the pilot opens that
  flight's briefing, web or app (`mark_flight_seen`).
- Flight is **unseen** iff `last_notified_ts > last_seen_ts`; **badge = count of unseen
  flights** (each 0 or 1), computed on demand by `compute_badge_count`.

So a flight that refreshes **twice** unopened still counts **once** (we compare the *latest*
notified pack, not each pack), and opening clears it however many packs piled up.

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

Ordering matters: the server count is the single source of truth, push is an optimization,
foreground-reconcile is the guarantee. The annoying-badge failure mode is the opposite —
increment/decrement on the client and trust push delivery.

**Locked semantics:**
- **Count once per flight**, never packs — two unopened refreshes on one flight = 1.
- **Only opening the briefing detail clears it** (web or app), not seeing it in the list.
- **Only notify-qualifying updates light it** — same WHEN gate as the notification, so a
  later unchanged refresh does **not** re-light a flight you already cleared, and an
  UNAVAILABLE pack never lights it at all.
- **Badge is independent of the alert-push toggle** — reconcile keeps it correct even with
  alerts off.

**Surface:** `api/notifications.py` — `GET /api/flights/badge`, `POST /api/flights/{id}/seen`
(both return `BadgeStatus`); the silent-push path; the app's foreground reconcile
(`AppState.reconcileBadge`) + `markBriefingSeen` from `BriefingContainerView`. The **web**
calls the same `seen` endpoint on briefing view — that is what decrements the app badge.

## Relation to App Intents

Both halves have shipped. `RefreshBriefingIntent` is the only intent that depended on this
doc — device registration + `notify/push.py` + the emit from `_notify_refresh_complete` are
what let it say "I'll let you know when it's ready". The read-only intents
(`OpenBriefingIntent` / `OpenFlightListIntent` / `CheckBriefingIntent`) never did.

## Decisions locked (nothing here is still open)

*notify-when-done vs watching-live* → server-side presence; *dedup with email* → channels are
independent user choices. **Locked during #366/#371:** APNs library = **httpx-rolled**;
seen storage = **`flight_briefing_seen` table** (not a prefs blob); device scope = **all of
the user's devices** for every trigger, manual included (simpler, consistent with
auto-refresh); change detection = **assessment/outlook transition vs the prior pack** (a
first briefing counts as changed; a GREEN→AMBER worsening also carries a short delta message
for the push body); quiet hours = **deferred** (not in v1). The reference detail worth
keeping:

- **APNs transport** — APNs is just HTTP/2 + a token (ES256 JWT from the `.p8`, header
  `{kid}`, claims `{iss: team_id, iat}`, cached ~50 min), rolled on `httpx[http2]` + `PyJWT`
  in `notify/push.py`. (`aioapns` was the alternative; **avoid `PyAPNs2`/`apns2`** — built on
  the unmaintained `hyper`.) Send: `POST /3/device/<token>` with `apns-topic: <bundle id>` and
  `apns-push-type: alert` (or `background` for silent badge syncs); on
  `BadDeviceToken`/`Unregistered` the row is deleted — which is also the trigger for the
  last-device email decay above.
- **APNs key management** — a **single `.p8` token-auth key works for BOTH environments**
  (unlike the old cert auth). Store key + key id + team id as deployment secrets (same
  handling as the existing Resend key). **As built**, `ApnsConfig.from_env()` reads:
  `APNS_KEY_P8` (PEM contents; or `APNS_KEY_P8_PATH` for local dev), `APNS_KEY_ID`,
  `APNS_TEAM_ID`, `APNS_BUNDLE_ID` (→ `apns-topic`). Missing config → log-and-skip.

- **Sandbox vs production routing** — per-**token**, decided by the *app build on the device*,
  **not** by which server runs: an Xcode **debug** build → `aps-environment: development` →
  **sandbox** token → sandbox host; **TestFlight and App Store** → `production` → production
  host (note: **TestFlight is production**, a common trap). The token value doesn't reveal its
  environment, so the client reports it at register time (`#if DEBUG` → `sandbox` else
  `production`; or read `aps-environment` from the embedded profile for full precision) and the
  server routes on `device_tokens.environment`. Wrong host → misleading `BadDeviceToken`. Local
  dev: a debug build on a real device → sandbox token → the local dev server (with the `.p8`
  configured) sends to the **sandbox** host — no separate prod credential needed for dev.

## References

- Server: `src/weatherbrief/notify/{dispatch,push,badge}.py`,
  `src/weatherbrief/api/{devices,notifications,preferences}.py`
- Models: `db/models.py::DeviceTokenRow`, `db/models.py::FlightBriefingSeenRow`;
  per-flight `FlightRow.notify_override` (seeded by `api/flights.py::update_auto_refresh`)
- Refresh-complete seams + presence: `api/packs.py::_notify_refresh_complete`,
  `refresh_registry.touch_watch` / `is_watched`; `scheduler.py`
- Tests: `tests/test_briefing_notifications.py`;
  iOS `flyfun-weatherTests/PushNotificationsTests.swift`
- iOS: `Services/PushNotifications.swift`, `App/AppState.swift` (push/badge methods),
  `Views/SettingsView.swift`, `Views/Briefing/BriefingContainerView.swift`
- Change detection: `compute_refresh_delta` (see [metar-taf-route-weather](./metar-taf-route-weather.md))
</content>
