# FlyFun Weather — Privacy & Data Practices

*Last updated: 2026-03-23*

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

## Autorouter Credentials

If you use the Autorouter integration (for GRAMET cross-section data), the app stores your Autorouter username and password.

**How they are stored:**
- Credentials are **encrypted at rest** using Fernet symmetric encryption (AES-128-CBC)
- The encryption key is stored separately from the database

**Honest disclosure:**
Anyone with access to the server (i.e., me) could technically decrypt these credentials, since the encryption key lives on the same server. This means you need to **trust me** not to look at or misuse them. I have no intention of doing so and never will, but I want to be upfront about the technical reality.

You can delete your Autorouter credentials at any time from your settings.

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

## Pricing Philosophy

FlyFun Weather is intended to **remain free**. I built it for myself and fellow pilots, not as a business.

If the app grows to a point where costs become significant (one can dream), I plan to:

- Set up a **voluntary donation** system
- Maintain **full transparency** on actual costs using the usage data described above
- Never gate features behind a paywall

I will not monetize your data, sell your information, or introduce advertising.

---

## Data Retention & Deletion

- **Briefing artifacts** (weather files, charts) are cleaned up automatically after a period to manage disk space
- **Account data and flight history** are retained as long as your account exists
- If you want your account and all associated data deleted, contact me and I will remove everything

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
| **Autorouter** | Your credentials + route | GRAMET cross-section images |
| **OpenAI / Anthropic** | Weather data context (no personal info) | LLM-generated briefing digest |
| **SMTP / Resend** | Your email + briefing summary | Email delivery |
| **Google / Apple OAuth** | OAuth tokens | Authentication |

No personal information (name, email, routes) is sent to LLM providers — only anonymized weather data context.

---

## Open Source & Auditability

The complete source code is open source. You can verify every claim in this document by reading the code yourself — or ask your favorite AI coding agent to review the code and my claims for you. Key areas:

- Authentication: `src/weatherbrief/api/app.py`
- Credential encryption: `flyfun_common.credentials`
- Usage tracking: `src/weatherbrief/api/usage.py`
- Cost tracking: `src/weatherbrief/api/credits.py`
- Email sending: `src/weatherbrief/notify/email.py`

---

## Account Deletion

You can delete your account and all associated data (flights, briefings, preferences, credentials) directly from the app settings. This will permanently remove everything linked to your account.

---

## Contact

If you have questions about your data or want to report a concern, reach out to me directly.
