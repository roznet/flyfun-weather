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
| Device token table | `db/models.py::DeviceTokenRow` (`token`, `environment`, `user_id`, CASCADE on user delete) | Store APNs tokens here; GDPR delete already handled |
| Auto-refresh completion seam | `scheduler.py::_auto_refresh_one` → `_try_send_email` (~L452) | Emit the push at the same point, right beside the email |
| Manual refresh completion | `api/packs.py` refresh path | Second emit seam — the one a Siri/app refresh hits |
| Notification module pattern | `notify/email.py` (Resend/SMTP) | Mirror as `notify/push.py` |
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
- **Emit at both seams**, guarded like `_try_send_email` (log-and-skip on any failure —
  a push must never break a refresh):
  - `scheduler.py::_auto_refresh_one` (auto-refresh) — beside the email send.
  - `api/packs.py` manual refresh completion — so an app/Siri/MCP-triggered refresh
    also notifies. **This is what closes the `RefreshBriefingIntent` loop.**
- **Payload**: `alert` title/body from route + new assessment + `compute_refresh_delta`
  ("EGTF → LFAT: now AMBER, ceiling worsened"); custom data `{ flight_id, timestamp }`;
  optional badge.
- **Send gating** — only on a *new* pack whose assessment changed or worsened (avoid
  "still green" spam). Configurable; default to meaningful-change-only.
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

## Open Questions

- **APNs key management** — `.p8` key + key id + team id as deployment secrets
  (see [multi-user-deployment](./multi-user-deployment.md) secret handling).
- **Environment routing** — sandbox vs production host per token; how to detect at
  build/runtime and keep straight across TestFlight vs App Store.
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
