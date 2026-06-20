# FlyFun Weather — GDPR Considerations

*Last updated: 2026-06-20*

## Statement

**This document has not been through a formal legal review.** FlyFun Weather is a
personal, open-source project run by a single developer, not a company. We are not
lawyers, and nothing here is legal advice or a certification of compliance.

What this document *is*: an honest, good-faith record of everything we understand
GDPR to require, what we have implemented to meet it, where we believe we are fine
and why, and what we know is still outstanding. The entire codebase is
[open source](https://github.com/roznet/flyfun-weather), so every claim below can be
verified against the code. See also [`PRIVACY.md`](./PRIVACY.md) for the user-facing
privacy notice and [`SECURITY_AUDIT.md`](./SECURITY_AUDIT.md) for the security review.

If you spot something we've missed or got wrong, please open a
[GitHub issue](https://github.com/roznet/flyfun-weather/issues).

---

## Summary

| Area | Status |
|------|--------|
| Privacy notice / transparency (Art. 13–14) | ✅ Implemented |
| Data minimization (Art. 5) | ✅ Implemented |
| Right to erasure (Art. 17) | ✅ Implemented |
| Lawful basis (Art. 6) | ✅ Believe fine |
| Consent handling (Art. 7) | ✅ Implemented (contact consent) |
| Security of processing (Art. 32) | 🟡 Mostly — two known fixes outstanding |
| EU data residency | ✅ Implemented |
| International transfers (Art. 44–49) | 🟡 Email covered via Resend SCCs (auto-executed); confirm other processors |
| Processor agreements / DPAs (Art. 28) | 🟡 Resend DPA in force; confirm remaining processors |
| Right to data portability (Art. 20) | ❌ Outstanding (no data export yet) |
| Breach notification process (Art. 33–34) | ❌ Outstanding (no documented process) |
| Records of processing (Art. 30) | 🟡 Likely exempt (small scale) — this doc serves as informal record |
| DPO / EU representative | 🟡 Believe not required (small scale, no large-scale special-category processing) |

✅ implemented · 🟡 partial / needs confirmation · ❌ outstanding

---

## What we have considered

### 1. Transparency & privacy notice (Art. 13–14) — ✅

- A plain-language privacy policy is published at [`PRIVACY.md`](./PRIVACY.md) and
  served in-app at `web/privacy.html`.
- It lists what data is collected, why, where it is stored, the third parties
  involved, and the user's rights.
- **Why we think we're fine:** the notice is specific, current, and accessible from
  the app.

### 2. Data minimization (Art. 5) — ✅

- We store only: email (or Apple private-relay address), display name, OAuth
  provider, account timestamps, the user's own flights/briefings, preferences, and
  optional feedback comments.
- **No third-party analytics, no tracking pixels, no advertising cookies** — only an
  authentication session cookie (`flyfun_auth`).
- **Why we think we're fine:** we collect only what the service functionally needs;
  no profiling, no marketing data.

### 3. Lawful basis (Art. 6) — ✅ (believe fine)

- **Contract** — processing flights/briefings is necessary to provide the service the
  user signed up for.
- **Consent** — sign-in via Google/Apple OAuth; explicit opt-in consent flag
  (`contact_ok`) before we reply to feedback.
- **Legitimate interest** — minimal usage/cost logging for rate-limiting and cost
  transparency (no profiling).
- **Why we think we're fine:** each processing activity maps to a clear basis; none
  rely on opaque or bundled consent.

### 4. Consent handling (Art. 7) — ✅

- Feedback replies are gated on an explicit `contact_ok` consent checkbox; a bare
  thumbs-up does not silently opt the user into being contacted.
- **Why we think we're fine:** consent is specific, unbundled, and opt-in.

### 5. Right to erasure (Art. 17) — ✅

- "Delete Account" is available in both the web app (Settings) and iOS app, and
  permanently removes the account and all associated data (flights, briefings,
  preferences, credentials).
- **Why we think we're fine:** erasure is self-service, complete, and irreversible.
- *Note:* the planned PIREP feature will **anonymize** (null out `user_id`/`aircraft_id`)
  rather than delete shared observation records, retaining them as anonymous data.
  Anonymization is a recognized approach, but this is a future feature — flagged here
  so it is reviewed when built.

### 6. Security of processing (Art. 32) — 🟡

- Autorouter access tokens are encrypted at rest (Fernet / AES-128-CBC); no
  passwords are stored.
- OAuth-based authentication; session cookie scoped to `.flyfun.aero`.
- A standing security review is maintained in [`SECURITY_AUDIT.md`](./SECURITY_AUDIT.md).
- **Outstanding (GDPR-adjacent), see below:** email addresses logged at INFO level;
  LangSmith tracing default-on risk.

### 7. EU data residency — ✅

- The server is hosted on DigitalOcean in an **EU region**. Database, briefing files,
  and credentials all reside there. No replication to other regions beyond what email
  delivery requires.

### 8. International transfers (Art. 44–49) — 🟡

- **No personal data is sent to LLM providers** (OpenAI / Anthropic) — only anonymized
  weather context. This is the most sensitive outbound path and it carries no PII.
- **Email (Resend / SMTP)** does transfer the user's email address to a US processor.
  Resend publishes a DPA that incorporates **Standard Contractual Clauses** for EU→US
  transfer (see https://resend.com/security/gdpr), which is a valid transfer mechanism.
  Resend's DPA is **pre-signed by Resend and considered fully executed on account
  signup** — no separate acceptance step is required, so it is already in force for us.
  A copy of the DPA can be downloaded from the Resend dashboard for our records.
- **Why we think we're fine:** the only PII leaving the EU goes to a processor with an
  SCC-backed DPA already in force.

### 9. Processor agreements / DPAs (Art. 28) — 🟡

- Processors we rely on: DigitalOcean (hosting), Resend/SMTP (email), Google & Apple
  (OAuth), OpenAI & Anthropic (LLM digest — no PII sent).
- Each offers a standard DPA.
- **Resend:** DPA is pre-signed and auto-executed on signup — **in force**. ✅
- **To confirm:** that the remaining processors' standard DPAs are accepted/in force,
  so we can say "DPA in place" for each rather than "DPA available."

### 10. Right to data portability (Art. 20) — ❌ Outstanding

- We support deletion but do **not** yet offer a "download my data" export.
- **Plan:** add a data-export endpoint that returns the user's account data, flights,
  preferences, and feedback in a machine-readable format (e.g. JSON).

### 11. Breach notification (Art. 33–34) — ❌ Outstanding

- No documented breach-detection/notification process exists.
- **Plan:** write a short runbook covering how a suspected breach is identified,
  assessed, and (if required) reported within 72 hours.

### 12. Records of processing (Art. 30) — 🟡

- Art. 30 has a small-organization exemption that likely applies. This document, plus
  `PRIVACY.md`, serves as an informal record of processing activities in the meantime.

### 13. DPO / EU representative — 🟡

- We believe a Data Protection Officer is **not required**: processing is small-scale
  and does not involve large-scale or systematic special-category data.
- **To revisit** if the user base or data scope grows materially.

---

## Outstanding items (action list)

1. **Data export endpoint** (Art. 20) — implement "download my data" (JSON). *Code, on us.*
2. **Confirm processor DPAs accepted** — Resend ✅ (auto-executed on signup; download
   copy for records). Still confirm: DigitalOcean, Google, Apple, OpenAI/Anthropic.
   *Admin, mostly checkbox.*
3. **Stop logging email PII at INFO** — route email addresses to a privacy logger or
   use hashed/user-id references in ops logs (`SECURITY_AUDIT.md` L-new-8). *Code, on us.*
4. **LangSmith tracing default** — ensure `LANGCHAIN_TRACING_V2` defaults to `false` so
   prompt content (potential PII context) isn't exported without an explicit consent
   surface (`SECURITY_AUDIT.md` 2026-06-L5). *Code/config, on us.*
5. **Breach-notification runbook** (Art. 33–34) — short documented process. *Docs, on us.*
6. **Review PIREP anonymization** when that feature is built (Art. 17). *Future.*

---

## Our current public answer

> FlyFun Weather is privacy-by-design: EU-hosted, data-minimizing, with a published
> privacy policy and full self-service account deletion. No personal data is sent to
> LLM providers — only anonymized weather context. We have not undergone a formal
> legal review, but we have implemented everything we understand GDPR to require and
> are openly tracking the remaining items (notably a data-export feature) in this
> document. The codebase is open source so anyone can verify these claims.
