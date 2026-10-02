# FlyFun Weather — Privacy & Data Practices

*Last updated: 2026-10-02*

This document explains what data the app collects, why, and what I do (and don't do) with it.
FlyFun Weather is a personal project — I'm a single developer, not a company.
The entire codebase is [open source](https://github.com/roznet/flyfun-weather) so you can audit exactly what happens with your data.

---

## Authentication & Identity

### Google Sign-In

When you sign in with Google, the server receives your **email address** and **display name** from Google's OAuth flow. These are stored in the database to identify your account.

### Apple Sign-In

When you sign in with Apple, Apple's **Private Relay** system is used. The server receives a private relay email address — I never see your real email unless you choose to share it. Your display name may also be provided depending on your Apple ID settings. For the Apple part of the sign-in flow Apple acts as an independent data controller; see [Apple's Privacy Policy](https://www.apple.com/legal/privacy/) for how Apple handles that data.

### Email Sign-In Link

You can also sign in with just your email address: the app emails you a one-time sign-in link and code. To deliver and secure it, the server briefly stores your email address and the IP address of the request (and of any attempt to use the link). These records are deleted automatically after 24 hours.

---

## What Data Is Stored

### Account Data

- Email address (or Apple private relay address)
- Display name
- Sign-in method (Google, Apple or email link)
- Account creation and last login timestamps

### Flights & Briefings

- Your saved routes, waypoints, departure times, and flight parameters
- Trips you create (a name, optional notes, and which flights belong to them)
- Flights of other pilots that you follow
- Your aircraft (type and, if you enter it, tail number) and flight profiles
- Generated briefing packs (weather data, advisories, GRAMET cross-sections, Skew-T charts, LLM digests)
- Briefing artifacts are stored as files on the server, organized by user
- Post-flight debriefs you write (how the flight went, free-text notes)

### Pilot Reports (PIREPs)

If you submit a PIREP, the report is stored with its position, altitude, time, conditions and any remarks, linked to your account. Because PIREPs are shared weather observations, they are **anonymized rather than deleted** when you delete your account (see Account Deletion).

### Devices & Connected Apps

- If you use the iOS app with notifications enabled, the device's push-notification token
- API tokens you create, and AI assistants you connect (see AI Assistant Connectors), with when they were created and last used

### Server Logs

Like any web server, requests are logged with the requesting IP address, for security and troubleshooting.

### Preferences

- Flight defaults (cruise altitude, ceiling, preferred models)
- Advisory settings, display preferences, locale
- Autorouter credentials (see dedicated section below)

### Feedback

If you submit feedback on a briefing, the comment and associated flight reference are stored.

### Donations

If you donate, payment is handled entirely by **Stripe** — card details go straight to Stripe and never reach this server. The app records the donation (amount, currency, whether it is recurring, and Stripe's payment reference) linked to your account if you were signed in. Your account email is pre-filled on the Stripe checkout page unless you choose not to.

---

## Briefing Sharing

Briefings are **shareable by direct link** to any authenticated user of the app.
If you share a briefing URL with another pilot, they can view it.
This is intentional — the app is designed for a small trusted community of pilots.
If you don't want a briefing to be viewable by others, you can mark flights as private.

Viewers see your display name (never your email). Your aircraft tail number, debriefs and trip notes are never shown to other users.

---

## Automated Briefing Emails

If you enable auto-refresh on a flight, the app will:

1. Automatically refresh your briefing before departure (based on your preferred schedule)
2. Send you an **email summary** of the updated briefing to your account email

Your email is used **solely** for delivering these briefing notifications and account-related messages (welcome email, etc.).

**I will never use your email for marketing, newsletters, promotions, or share it with any third party.**

### Push Notifications (iOS)

If you allow notifications in the iOS app, briefing updates are delivered through the **Apple Push Notification service**. The notification content — the route (e.g. "EGTF → LFAT"), the trip name for trip updates, and the new assessment — passes through Apple to reach your device. You can turn notifications off in the app or in iOS Settings.

---

## AI Assistant Connectors

You can choose to connect your FlyFun Weather account to an AI assistant such as **ChatGPT** or **Claude** (via the MCP connector). When you do, that assistant can read your flights and briefings and create or refresh flights on your behalf, using access you authorized. What the assistant then does with that data is governed by its provider's own privacy terms (OpenAI or Anthropic), under your account with them — not by this app. You can see and revoke connected assistants and API tokens from Settings.

---

## Autorouter Integration

If you use the Autorouter integration (for GRAMET cross-section data), the app uses **OAuth2 authorization** to connect to your Autorouter account. You are redirected to autorouter.aero to authorize access — your Autorouter password is never shared with or stored by this app.

**What is stored:**
After authorization, an **access token** (valid for approximately one year) is stored encrypted at rest using Fernet symmetric encryption (AES-128-CBC). This token allows the app to fetch GRAMET data on your behalf. No username or password is stored.

You can disconnect your Autorouter account at any time from your settings, which removes the stored token.

---

## Usage Tracking & Cost Transparency

### What Is Tracked

Every briefing refresh logs:

- Number of API calls made (Open-Meteo, GRAMET, LLM)
- LLM model used and token counts (input/output)
- Briefing size and processing time
- Whether the refresh was manual or automatic

### Why

This usage data serves two purposes:

1. **Rate limiting** — to keep costs sustainable and prevent abuse
2. **Cost transparency** — so I can show you (and myself) exactly what the app costs to run

There are **no third-party analytics**, no tracking pixels, no cookies beyond the authentication session cookie. I don't use Google Analytics or any similar service.

### Cost Model

The app tracks the real cost of each briefing (LLM tokens, infrastructure share, storage) and exposes this via a public **transparency endpoint** (`/api/transparency`) that anyone can query. You can also see your own usage and cost breakdown in the app.

---

## Data Retention & Deletion

- **Briefing packs** are trimmed in two stages: 30 days after the flight's departure the heavy files (charts, raw model data) are removed, and after 180 days the briefing is deleted entirely (90 days if you haven't used the app for 30 days). Briefings for flights you debriefed are not deleted (their heavy files are still trimmed), and briefings linked to a PIREP are kept in full.
- **Email sign-in records** (email, IP address) are deleted after 24 hours.
- **In-app usage events** (raw page events) are deleted after 60 days.
- **Account data and flight history** are retained as long as your account exists
- You can delete your account and all data at any time from the app settings (see Account Deletion below)

---

## Hosting & Data Location

- The server is hosted on **DigitalOcean** in the **UK** (London region), which holds an EU data-protection adequacy decision
- All data (database, briefing files, credentials) resides on that server
- No data is replicated to other regions or services beyond what's needed for email delivery

---

## Third-Party Services

The app interacts with these external services during normal operation:

| Service | Data Sent | Purpose |
|---------|-----------|---------|
| **Open-Meteo** | Coordinates, altitudes | Weather forecast data |
| **Autorouter** | OAuth access token + route | GRAMET cross-section images |
| **OpenAI / Anthropic** | Weather + route context (airports, dates, positions); no account identifiers | LLM-generated briefing digest |
| **LangSmith** (when LLM tracing is enabled) | The LLM run trace (same weather/route context) and your digest 👍/👎 rating and any comment | Digest quality monitoring |
| **SMTP / Resend** | Your email + briefing summary | Email delivery |
| **Google / Apple OAuth** | OAuth tokens | Authentication |
| **Stripe** (only if you donate) | Your account email (unless you opt out), account id, amount; card details entered directly with Stripe | Payment processing |
| **Apple Push Notification service** (iOS, if enabled) | Device push token + notification text (route, trip name, assessment) | Briefing notifications |
| **OpenStreetMap / Stadia Maps** (map tiles) | Your browser loads map images directly from them, so they see your IP address and the map area displayed | Background map |

No account identifiers (your name or email) are sent to LLM providers. The context that is sent describes the weather along your route — airports, dates and approximate positions — which carries no personal identifiers. Where LLM-quality tracing is enabled it goes to LangSmith under a data-processing agreement (EU data residency).

---

## Open Source & Auditability

The complete source code is open source. You can verify every claim in this document by reading the code yourself — or ask your favorite AI coding agent to review the code and my claims for you. If you identify any issues, please raise a [GitHub issue](https://github.com/roznet/flyfun-weather/issues) and I will address it. Key areas:

- Authentication: `src/weatherbrief/api/app.py`
- Credential encryption: `flyfun_common.credentials`
- Usage tracking: `src/weatherbrief/api/usage.py`
- Cost tracking: `src/weatherbrief/api/credits.py`
- Email sending: `src/weatherbrief/notify/email.py`
- Account data export: `src/weatherbrief/api/account_export.py`
- Email masking in logs: `src/weatherbrief/privacy.py`

---

## Data Export

You can download a complete, machine-readable (JSON) copy of the personal data held about your account — account details, preferences, flights, briefings, trips, followed flights, debriefs, aircraft, PIREPs, feedback, usage history, devices, API tokens and connected apps, and donations — at any time:

- **Web app:** Settings > Download my data

Encrypted credentials (e.g. your Autorouter token), token values and server-internal values are intentionally excluded for security.

---

## Account Deletion

You can delete your account and all associated data (flights, briefings, preferences, credentials) at any time:

- **iOS app:** Settings > Delete Account
- **Web app:** Settings > Delete Account

This will permanently remove everything linked to your account and cannot be undone. PIREPs you submitted are kept as anonymous weather observations, with the link to you and your aircraft removed.

---

## Contact

If you have questions about your data or want to report a concern, reach out via the [GitHub issue tracker](https://github.com/roznet/flyfun-weather/issues).
