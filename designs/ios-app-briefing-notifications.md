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

### User preference

Add a push on/off toggle, alongside the existing email preference
(`defer_email_for_model_update` / `UserPreferencesStore`). Respect it at send time.
Consider quiet-hours later.

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

- **★ Notify-when-done vs watching-live** — avoid a redundant push when the user triggered
  the refresh from inside the app. `triggered_by` is only `user | scheduler` today, so a
  Siri refresh is indistinguishable from an in-app one. **Recommend:** client-side
  foreground suppression as the baseline (robust, no server change); optionally add a
  `triggered_by="intent"` / `notify_on_complete` signal later for server-side precision.
- **APNs provider library** — hand-rolled HTTP/2 + `.p8` JWT, or a small dep? Note the
  server is Python/FastAPI; pick something that fits the async stack.
- **APNs key management** — `.p8` key + key id + team id as deployment secrets
  (see [multi-user-deployment](./multi-user-deployment.md) secret handling).
- **Environment routing** — sandbox vs production host per stored `environment`; how to
  set it at build/runtime and keep straight across TestFlight vs App Store.
- **Dedup with email** — users who get both email and push on the same refresh: fine,
  or offer "one or the other"?
- **Manual-refresh scope** — notify only the triggering device, or all the user's
  devices? (All is simpler and consistent with auto-refresh.)
- **Throttling / quiet hours** — cap sends per flight per day; suppress overnight?

## References

- Device token model: `src/weatherbrief/db/models.py::DeviceTokenRow`
- Refresh-complete seams: `src/weatherbrief/scheduler.py`, `src/weatherbrief/api/packs.py`
- Email precedent to mirror: `src/weatherbrief/notify/email.py`
- Change detection: `compute_refresh_delta` (see [metar-taf-route-weather](./metar-taf-route-weather.md))
</content>
