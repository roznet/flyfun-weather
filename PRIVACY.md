# FlyFun Weather — Privacy & Data Practices

*Last updated: 2026-06-20*

This document explains what data the app collects, why, and what I do (and don't do) with it.
FlyFun Weather is a personal project — I'm a single developer, not a company.
The entire codebase is [open source](https://github.com/roznet/flyfun-weather) so you can audit exactly what happens with your data.

---

## Authentication & Identity

### Google Sign-In

When you sign in with Google, the server receives your **email address** and **display name** from Google's OAuth flow. These are stored in the database to identify your account.

### Apple Sign-In

When you sign in with Apple, Apple's **Private Relay** system is used. The server receives a private relay email address — I never see your real email unless you choose to share it. Your display name may also be provided depending on your Apple ID settings.

---

## What Data Is Stored

### Account Data

- Email address (or Apple private relay address)
- Display name
- OAuth provider (Google or Apple)
- Account creation and last login timestamps

### Flights & Briefings

- Your saved routes, waypoints, departure times, and flight parameters
- Generated briefing packs (weather data, advisories, GRAMET cross-sections, Skew-T charts, LLM digests)
- Briefing artifacts are stored as files on the server, organized by user

### Preferences

- Flight defaults (cruise altitude, ceiling, preferred models)
- Advisory settings, display preferences, locale
- Autorouter credentials (see dedicated section below)

### Feedback

If you submit feedback on a briefing, the comment and associated flight reference are stored.

---

## Briefing Sharing

Briefings are **shareable by direct link** to any authenticated user of the app.
If you share a briefing URL with another pilot, they can view it.
This is intentional — the app is designed for a small trusted community of pilots.
If you don't want a briefing to be viewable by others, you can mark flights as private.

---

## Automated Briefing Emails

If you enable auto-refresh on a flight, the app will:

1. Automatically refresh your briefing before departure (based on your preferred schedule)
2. Send you an **email summary** of the updated briefing to your account email

Your email is used **solely** for delivering these briefing notifications and account-related messages (welcome email, etc.).

**I will never use your email for marketing, newsletters, promotions, or share it with any third party.**

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

- **Briefing artifacts** (weather files, charts) are cleaned up automatically after a period to manage disk space
- **Account data and flight history** are retained as long as your account exists
- You can delete your account and all data at any time from the app settings (see Account Deletion below)

---

## Hosting & Data Location

- The server is hosted on **DigitalOcean** in the **EU** region
- All data (database, briefing files, credentials) resides on that server
- No data is replicated to other regions or services beyond what's needed for email delivery

---

## Third-Party Services

The app interacts with these external services during normal operation:

| Service | Data Sent | Purpose |
|---------|-----------|---------|
| **Open-Meteo** | Coordinates, altitudes | Weather forecast data |
| **Autorouter** | OAuth access token + route | GRAMET cross-section images |
| **OpenAI / Anthropic** | Weather data context (no personal info) | LLM-generated briefing digest |
| **SMTP / Resend** | Your email + briefing summary | Email delivery |
| **Google / Apple OAuth** | OAuth tokens | Authentication |

No personal information (name, email, routes) is sent to LLM providers — only anonymized weather data context.

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

You can download a complete, machine-readable (JSON) copy of the personal data held about your account — account details, preferences, flights, briefings, feedback, and usage history — at any time:

- **Web app:** Settings > Download my data

Encrypted credentials (e.g. your Autorouter token) and server-internal values are intentionally excluded for security.

---

## Account Deletion

You can delete your account and all associated data (flights, briefings, preferences, credentials) at any time:

- **iOS app:** Settings > Delete Account
- **Web app:** Settings > Delete Account

This will permanently remove everything linked to your account and cannot be undone.

---

## Contact

If you have questions about your data or want to report a concern, reach out via the [GitHub issue tracker](https://github.com/roznet/flyfun-weather/issues).
