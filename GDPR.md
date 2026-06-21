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
| Feedback contact basis (Art. 6) | ✅ Legitimate interest — pre-ticked, easily-declined contact box on user-initiated feedback |
| Security of processing (Art. 32) | ✅ Implemented (PII log-masking shipped) |
| Data residency (UK, EU-adequate) | ✅ Implemented |
| International transfers (Art. 44–49) | ✅ All transfers under SCC-backed DPAs (Resend, DigitalOcean, Google, Anthropic) or independent controller (Apple); no PII to LLM providers |
| Processor agreements / DPAs (Art. 28) | ✅ All DPAs in force (Resend, DigitalOcean, Google, Anthropic, OpenAI — auto-executed/incorporated); Apple handled as independent controller |
| Our role: controller, not processor | ✅ We are the controller; no one needs a DPA from us (revisit if org/team accounts or cross-app sharing ship) |
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
- **Consent** — sign-in via Google/Apple OAuth is the user's explicit choice of
  provider.
- **Legitimate interest** — minimal usage/cost logging for rate-limiting and cost
  transparency (no profiling); and replying to feedback the user themselves chose
  to send, unless they have declined contact (see §4).
- **Why we think we're fine:** each processing activity maps to a clear basis; none
  rely on opaque or bundled consent.

### 4. Feedback contact (Art. 6(1)(f) legitimate interest) — ✅

- When a user submits feedback they may be contacted about it. The feedback form
  shows a **pre-ticked** "you may contact me about this" checkbox (`contact_ok`,
  default on) that the user can untick to decline; if unticked, the reply path is
  closed and we never contact them.
- Because the box is pre-ticked, we do **not** treat this as Art. 7 opt-in
  consent (a pre-ticked box is not valid consent under Recital 32). Instead the
  basis is **legitimate interest** in responding to a question/report the user
  themselves initiated — narrow, expected, trivially declined, and never used for
  marketing or shared with anyone.

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
  fixed 2026-06-11). *Were it ever enabled,* it is a covered processor: LangChain's
  Terms of Service **incorporate their DPA by reference** (langchain.com/DPA) — accepting
  the ToS accepts the DPA — LangSmith is SOC 2 Type 2 certified and offers **EU data
  residency** to keep traces in the EU. Note that the only content the digest would trace
  is the **anonymized weather context** (no PII), so even enabled this path carries no
  personal data. It remains off by default as a data-minimization measure regardless.
  - *To enable safely:* (1) turn on **EU data residency** in the LangSmith account and
    point `LANGCHAIN_ENDPOINT` at the EU host first, so traces stay in-region; (2) set
    `LANGCHAIN_TRACING_V2=true` + `LANGCHAIN_API_KEY` (env-only — no code change, default
    stays `false`); (3) the digest context (`build_digest_context`,
    `src/weatherbrief/digest/prompt_builder.py`) carries route/date/altitude/weather but
    **no user identifier** (no name, email or `user_id`), so traces are non-identifying.
    Re-evaluate before tracing if a prompt ever starts including user identifiers, names,
    or free-text feedback. With that, enabling tracing is an ops/cost decision, not a
    privacy one.

### 7. Data residency — ✅

- The server is hosted on DigitalOcean in the **UK (London) region**. The UK holds an
  EU data-protection **adequacy decision**, so data stored there is recognised as
  adequately protected for EU/EEA users. Database, briefing files, and credentials all
  reside there. No replication to other regions beyond what email delivery requires.

### 8. International transfers (Art. 44–49) — ✅

- **No personal data is sent to LLM providers** (OpenAI / Anthropic) — only anonymized
  weather context. This is the most sensitive outbound path and it carries no PII.
- **Email (Resend / SMTP)** does transfer the user's email address to a US processor.
  Resend publishes a DPA that incorporates **Standard Contractual Clauses** for
  international transfer (UK→US via the UK IDTA Addendum, EU→US via the EU SCCs; see
  https://resend.com/security/gdpr), which is a valid transfer mechanism.
  Resend's DPA is **pre-signed by Resend and considered fully executed on account
  signup** — no separate acceptance step is required, so it is already in force for us.
  A copy of the DPA can be downloaded from the Resend dashboard for our records.
- **OAuth sign-in (Google / Apple)** involves US-based providers. **Google** transfers
  are covered by the SCCs incorporated into its API/Cloud DPA (see §9). **Apple** acts as
  an independent controller and is responsible for its own transfer mechanisms for the
  authentication it performs; we receive only a minimal identifier + email (see §9). The
  iOS Apple path verifies the token locally and sends Apple no user data.
- **Why we think we're fine:** the PII leaving the EU goes to processors with SCC-backed
  DPAs already in force (Resend, Google), or to an independent controller (Apple) handling
  its own compliance; no PII goes to the LLM providers. Hosting (DigitalOcean) is in the
  UK (§7) and additionally covered by its auto-accepted DPA (§9).

### 9. Processor agreements / DPAs (Art. 28) — ✅

- Processors we rely on: DigitalOcean (hosting), Resend/SMTP (email), OpenAI &
  Anthropic (LLM digest — no PII sent). For sign-in, **Google** acts as our processor
  and **Apple** acts as an independent controller (see below).
- **Resend:** DPA is pre-signed and auto-executed on signup — **in force**. ✅
- **DigitalOcean (hosting):** DigitalOcean's DPA is **automatically accepted by agreeing
  to their Terms of Service — no separate signature is required**, so it is already in
  force for us. A copy can be downloaded from
  [DigitalOcean's DPA page](https://www.digitalocean.com/legal/data-processing-agreement)
  for our records. Hosting is in the UK (London) region (see §7). ✅
- **Google Sign-In (processor):** Google processes the OAuth identity exchange on our
  behalf, so a DPA applies. Google does not have us sign a separate document — the DPA
  is **automatically incorporated into the terms we already accepted**: the
  [Google API Services / OAuth terms](https://developers.google.com/terms) for the
  Sign-In APIs we use, and the
  [Google Cloud Data Processing Addendum](https://cloud.google.com/terms/data-processing-addendum)
  where Cloud/Firebase services apply. We rely on the standard OIDC sign-in (not
  Firebase). We reference these as the processing arrangement in our records; nothing
  further needs to be signed. ✅
- **Sign in with Apple (independent controller):** Apple's position is that it is an
  **independent controller** for its part of the authentication, not our processor, so
  Apple **does not offer a developer DPA** — there is no document to obtain. Apple's role
  is instead governed by the
  [Apple Developer Program License Agreement](https://developer.apple.com/support/downloads/terms/apple-developer-program/Apple-Developer-Program-License-Agreement-20250107.pdf).
  The correct treatment (per the ICO/EDPB controller-to-controller model) is to document
  the arrangement rather than chase a DPA: we receive only a stable user identifier
  (`sub`), an email (often an Apple private-relay address), and a display name on first
  login; users should consult
  [Apple's privacy policy](https://www.apple.com/legal/privacy/) for Apple's own
  handling. This is recorded in `PRIVACY.md` and in §2/§8 here. ✅
- **Processing footprint is minimal.** For both providers the backend only validates the
  sign-in and stores `sub` + email + name — no profiling, no ongoing API access to the
  provider. The **iOS Sign in with Apple path verifies the signed identity token locally
  against Apple's public keys** (`appleid.apple.com/auth/keys`) and never sends user data
  to Apple, which shrinks the footprint further.
- **Anthropic (LLM digest — no PII sent):** Anthropic's DPA was **updated effective
  2026-01-01 and is now automatically incorporated into the Commercial Terms of Service**
  — accepting the Commercial Terms as an API customer accepts the DPA with no separate
  signature flow, so it is already in force for us. The document can be viewed/downloaded
  for our records via Anthropic's Help Center
  (https://privacy.claude.com/en/articles/7996862-how-do-i-view-and-sign-your-data-processing-addendum-dpa).
  *RoPA note:* Anthropic's infrastructure spans **AWS, Google Cloud and Azure**, so no
  single cloud's regional guarantees can be assumed — recorded here for the processing
  record. In any case **no personal data is sent to Anthropic** (only anonymized weather
  context), so this path carries no PII. ✅
- **OpenAI (LLM digest — no PII sent):** OpenAI's DPA is **incorporated into the
  business/API terms and becomes effective when the API is used under that agreement** —
  no separately negotiated DPA is required. The
  [OpenAI Data Processing Addendum](https://openai.com/policies/data-processing-addendum)
  can be downloaded for our records. As with Anthropic, **no personal data is sent to
  OpenAI** — only anonymized weather context. ✅
- **Why we think we're fine:** every processor is now covered by a DPA already in force,
  auto-executed or auto-incorporated via the terms we accepted (Resend, DigitalOcean,
  Google, Anthropic, OpenAI); Apple is correctly handled as an independent controller with
  the relationship documented. No outstanding processor agreements remain.

### 9a. Our role: controller, not processor — ✅

- A DPA is a **controller → processor** contract: someone needs one *from us* only if we
  process personal data **on their behalf**, under their instructions. Today that never
  happens, so **no one needs a DPA from FlyFun Weather.**
- **Individual pilots are data subjects, not controllers.** We decide what is collected
  and why (we build and run the app), so we are the **controller** of their account data.
  A data subject is owed a privacy notice and their rights — never a DPA. The DPAs in §9
  therefore all flow the *other* way: those are *our* processors.
- **We do not process any third party's data on their instructions**, so we sit in the
  controller seat throughout the current direct-to-consumer app.
- **Triggers that would change this** (we would then need to *offer* a short standard DPA,
  or characterise the relationship deliberately) — flagged here for future review:
  1. **Organizational / team accounts** — if a flight school, club or operator ever
     manages *its members'/employees'* data through FlyFun, that organization becomes the
     controller and we become its **processor** → it would be entitled to a DPA from us.
  2. **Flight sharing / cross-app interchange** (planned) — when personal data flows
     *between apps*, the likely model is **independent controllers** (each app its own
     controller, as with Apple above) or **joint controllers** (Art. 26 — an arrangement,
     not a DPA). To be decided and documented when built, not assumed.
  3. **Third-party services using the API/MCP on behalf of *their* users** — today the
     API/MCP acts for the *user themselves* via their own token (no processor
     relationship); a service acting for *its* users instead could create one.
- **Why we think we're fine:** in the current model we are the controller, we publish
  privacy notices, and we owe no DPAs. The first time a *business* uses FlyFun to handle
  *other people's* data is the trigger to revisit this section.

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
2. ✅ **Processor DPAs accepted** — all in force, auto-executed or auto-incorporated via
   accepted terms: Resend, DigitalOcean, Google, Anthropic, OpenAI. Apple is handled as an
   independent controller (no DPA exists; documented in `PRIVACY.md` + §9). Copies of each
   DPA are downloadable from the respective dashboards for our records.
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
