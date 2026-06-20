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
| Security of processing (Art. 32) | ✅ Implemented (PII log-masking shipped) |
| Data residency (UK, EU-adequate) | ✅ Implemented |
| International transfers (Art. 44–49) | 🟡 Email covered via Resend SCCs (auto-executed); confirm other processors |
| Processor agreements / DPAs (Art. 28) | 🟡 Resend DPA in force; confirm remaining processors |
| Right to data portability (Art. 20) | ✅ Implemented (self-service JSON export) |
| Breach notification process (Art. 33–34) | ✅ Implemented (runbook in `SECURITY.md`) |
| Records of processing (Art. 30) | 🟡 Likely exempt (small scale) — this doc serves as informal record |
| DPO / EU representative | 🟡 Believe not required (small scale, no large-scale special-category processing) |

✅ implemented · 🟡 partial / needs confirmation · ❌ outstanding

### Applicable law & supervisory authority

The controller is established in the **United Kingdom**, so the governing regime is the
**UK GDPR + Data Protection Act 2018** and the lead supervisory authority is the
**Information Commissioner's Office (ICO)**. UK GDPR is the retained version of the EU
GDPR — the article numbers and obligations referenced throughout this document are
materially identical. Because the app also serves pilots in the EU/EEA, **EU GDPR
applies in parallel** by territorial scope (Art. 3(2)) for those users.

*International transfers note:* email delivery (Resend) transfers data to the US. As a UK
controller these are UK→US transfers covered by the **UK International Data Transfer
Addendum** to the SCCs; the same path serves EU users under the EU SCCs. See §8.

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

### 6. Security of processing (Art. 32) — ✅

- Autorouter access tokens are encrypted at rest (Fernet / AES-128-CBC); no
  passwords are stored.
- OAuth-based authentication; session cookie scoped to `.flyfun.aero`.
- A standing security review is maintained in [`SECURITY_AUDIT.md`](./SECURITY_AUDIT.md).
- **Email addresses are no longer logged in the clear.** A `mask_email` helper
  (`src/weatherbrief/privacy.py`) reduces every ops log line to
  `b***@gmail.com`, applied across the email, admin, scheduler, feedback and
  packs paths (SECURITY_AUDIT L-new-8, fixed 2026-06-20).
- **LangSmith tracing** defaults to `false` with a privacy comment so filling in
  an API key can't silently export prompt content (SECURITY_AUDIT 2026-06-L5,
  fixed 2026-06-11).

### 7. Data residency — ✅

- The server is hosted on DigitalOcean in the **UK (London) region**. The UK holds an
  EU data-protection **adequacy decision**, so data stored there is recognised as
  adequately protected for EU/EEA users. Database, briefing files, and credentials all
  reside there. No replication to other regions beyond what email delivery requires.

### 8. International transfers (Art. 44–49) — 🟡

- **No personal data is sent to LLM providers** (OpenAI / Anthropic) — only anonymized
  weather context. This is the most sensitive outbound path and it carries no PII.
- **Email (Resend / SMTP)** does transfer the user's email address to a US processor.
  Resend publishes a DPA that incorporates **Standard Contractual Clauses** for
  international transfer (UK→US via the UK IDTA Addendum, EU→US via the EU SCCs; see
  https://resend.com/security/gdpr), which is a valid transfer mechanism.
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

### 10. Right to data portability (Art. 20) — ✅

- A self-service **"Download my data"** button in web Settings calls
  `GET /api/account/export` and downloads a single JSON document containing the
  user's account, preferences, flights (with briefing-pack metadata and
  debriefs), aircraft, profiles, usage records, PIREPs, feedback and device
  registrations.
- The export mirrors the deletion inventory (`_on_delete_user`) so the data we
  *return* matches the data we *hold*. Secrets and server-internal fields
  (encrypted credentials, push-token values, integrity HMACs, AI-triage
  internals, file paths, OAuth `provider_sub`) are deliberately excluded.
- Code: `src/weatherbrief/api/account_export.py`; tests in
  `tests/test_account_export.py`. Shipped 2026-06-20.

### 11. Breach notification (Art. 33–34) — ✅

- A documented process is maintained in [`SECURITY.md`](./SECURITY.md): record →
  contain & assess → notify the **ICO within 72 hours** of becoming aware (unless the
  breach is unlikely to be risky) → notify affected users without undue delay if the
  breach is **high risk**, in plain language. All breaches are documented per Art. 33(5).
- `SECURITY.md` also provides a **private reporting channel** (GitHub's "Report a
  vulnerability") so a suspected breach can reach the maintainer without public
  disclosure — this shortens time-to-awareness, which is when the 72-hour clock starts.
- If a breach affects EU/EEA users, the relevant EU authority would be notified in
  addition to the ICO (Art. 3(2)).

### 12. Records of processing (Art. 30) — 🟡

- Art. 30 has a small-organization exemption that likely applies. This document, plus
  `PRIVACY.md`, serves as an informal record of processing activities in the meantime.

### 13. DPO / UK & EU representative — 🟡

- We believe a Data Protection Officer is **not required**: processing is small-scale
  and does not involve large-scale or systematic special-category data.
- As a UK-established controller the **ICO** is the supervisory authority. An EU
  representative (Art. 27) could in principle be needed for offering services to EU
  residents, but the Art. 27 exemption for occasional, low-risk processing is likely to
  apply at this scale.
- **To revisit** if the user base or data scope grows materially.

---

## Outstanding items (action list)

1. ✅ **Data export endpoint** (Art. 20) — shipped 2026-06-20
   (`src/weatherbrief/api/account_export.py` + Settings "Download my data").
2. **Confirm processor DPAs accepted** — Resend ✅ (auto-executed on signup; download
   copy for records). Still confirm: DigitalOcean, Google, Apple, OpenAI/Anthropic.
   *Admin, mostly checkbox.*
3. ✅ **Stop logging email PII at INFO** — `mask_email` applied across all ops log
   sites (`SECURITY_AUDIT.md` L-new-8), shipped 2026-06-20.
4. ✅ **LangSmith tracing default** — `LANGCHAIN_TRACING_V2` defaults to `false`
   (`SECURITY_AUDIT.md` 2026-06-L5), fixed 2026-06-11.
5. ✅ **Breach-notification runbook** (Art. 33–34) — documented in `SECURITY.md`
   (UK GDPR / ICO, 72-hour process), shipped 2026-06-20.
6. **Review PIREP anonymization** when that feature is built (Art. 17). *Future.*

---

## Our current public answer

> FlyFun Weather is privacy-by-design: UK-hosted (EU-adequate), data-minimizing, with a published
> privacy policy and full self-service account deletion. No personal data is sent to
> LLM providers — only anonymized weather context. We have not undergone a formal
> legal review, but we have implemented everything we understand GDPR to require and
> are openly tracking the remaining items (notably a data-export feature) in this
> document. The codebase is open source so anyone can verify these claims.
