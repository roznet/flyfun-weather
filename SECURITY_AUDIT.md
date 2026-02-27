# Security Audit — WeatherBrief Web App

**Date:** 2026-02-26
**Scope:** Full-stack security review of the WeatherBrief web application — FastAPI backend, SQLAlchemy/SQLite/MySQL database, vanilla TypeScript frontend, file-based artifact storage, and deployment infrastructure.

---

## Executive Summary

WeatherBrief is generally well-architected from a security perspective for its intended use case (small group of trusted pilots). The codebase demonstrates security awareness: path sanitization, HTML escaping, parameterized queries via ORM, encrypted credential storage, proper cookie flags, and comprehensive security headers via Caddy. However, several issues were identified ranging from **medium-severity authorization gaps** to **low-severity hardening opportunities**.

**Critical findings: 0** | **High: 2** | **Medium: 5** | **Low: 7** | **Informational: 4**

> **Fixed:** H1, H2, M2, M4, M5, L1, L2, L3, L4, L5, L6.

---

## HIGH Severity

### H1. Skew-T/Hodograph Path Injection via `icao` and `model` Parameters

**Location:** `src/weatherbrief/api/packs.py:908`, `:968`

```python
skewt_path = pack_dir / "skewt" / f"{icao}_{model}.png"
```

The `icao` and `model` URL path parameters are used **directly** in file path construction without sanitization. While `pack_dir` itself is safely constructed via `safe_path_component()`, the `icao` and `model` values are concatenated into the filename unsanitized.

**Attack:** An attacker could craft a request like:
```
GET /api/flights/{id}/packs/{ts}/skewt/../../etc/passwd/foo
```

FastAPI's path parameter parsing may limit some traversal, but the `model` parameter especially could contain `../` sequences that escape the `skewt/` subdirectory. The path is used both for reading (`FileResponse`) and **writing** (on-demand generation writes to `skewt_path`).

**Impact:** Potential file read outside the pack directory; potential file write to arbitrary locations if the on-demand generation path is triggered.

**Recommendation:** Apply `safe_path_component()` to both `icao` and `model` before using them in path construction, or validate them against a whitelist of known values:

```python
import re
ICAO_PATTERN = re.compile(r"^[A-Z]{4}$")
MODEL_PATTERN = re.compile(r"^[a-z_]+$")

# In the endpoint:
if not ICAO_PATTERN.match(icao.upper()):
    raise HTTPException(400, "Invalid ICAO code")
if not MODEL_PATTERN.match(model):
    raise HTTPException(400, "Invalid model name")
```

---

### H2. Shareable Briefing Links Expose All Flight Data to Any Authenticated User (IDOR)

**Location:** `src/weatherbrief/api/packs.py` (all `_get_pack_dir` callers), `src/weatherbrief/api/flights.py:263-271`

The design doc explicitly states: *"any authenticated user can view any flight's briefings via direct URL."* This is an intentional design choice for a small trusted-user group. However, it creates an **Insecure Direct Object Reference (IDOR)** pattern:

- **Any authenticated user** can access any other user's flight details, briefing snapshots, Skew-T images, GRAMET data, advisories, elevation profiles, reports (HTML/PDF), and digest data.
- Any authenticated user can trigger email sending for any flight (the email goes to the logged-in user's address, but the data is from any flight).
- Flight IDs are predictable: `{route_name}-{date}-{4char_hash}`.

**Impact:** A user could enumerate and access all other users' flight plans, routes, weather briefings, and trip schedules. For a small trusted group this may be acceptable, but it breaks the principle of least privilege.

**Recommendation:** If sharing is intentional, document the security implications clearly for users. Consider adding:
1. An explicit "share" action that generates a share token
2. A per-flight visibility setting (private/shared)
3. At minimum, avoid exposing `user_id` in the `FlightResponse` model for flights the viewer doesn't own

---

## MEDIUM Severity

### M1. Admin Check Bypasses Bearer Token Authentication

**Location:** `src/weatherbrief/api/admin.py:44-72`

The `require_admin` dependency only checks JWT cookies — it does not check Bearer API tokens:

```python
def require_admin(request: Request) -> str:
    if is_dev_mode():
        return DEV_USER_ID
    token = request.cookies.get(COOKIE_NAME)  # Only checks cookies
    # ... no Bearer token check
```

If an API token user (agent) somehow has an admin-level email, they cannot use admin endpoints. This is arguably correct (agents shouldn't be admins), but it creates an inconsistency with the main `current_user_id` dependency which supports both auth methods.

**Impact:** Low — agents can't access admin endpoints even if they should. However, the inconsistency could cause confusion and bugs if admin API token access is ever needed.

**Recommendation:** If admin access via API tokens is ever needed, `require_admin` should be updated to also check Bearer tokens. Otherwise, document this as intentional.

---

### M2. SSE Streaming Endpoint Manages Its Own DB Session — Potential Session Leak

**Location:** `src/weatherbrief/api/packs.py:536-594`

The `refresh_briefing_stream` endpoint creates its own `SessionLocal()` session outside FastAPI's dependency injection:

```python
db = SessionLocal()
try:
    # ... use db ...
except Exception:
    db.close()
    raise
db.close()
```

If an exception occurs between session creation and the explicit close (e.g., in `_load_owned_flight`), the session could leak. The pipeline thread also creates its own session which is properly handled.

**Impact:** Under error conditions, database connections could leak, eventually exhausting the pool.

**Recommendation:** Use a `try/finally` block to guarantee session cleanup:

```python
db = SessionLocal()
try:
    flight = _load_owned_flight(db, flight_id, user_id)
    # ...
finally:
    db.close()
```

---

### M3. No CSRF Protection on State-Changing POST/DELETE/PUT Endpoints

**Location:** All API mutation endpoints

The session cookie (`wb_auth`) is set with `SameSite=lax`, which protects against CSRF for top-level navigations. However, `SameSite=lax` **does NOT protect** against:
- POST requests from forms on third-party sites (forms can submit cross-site POSTs)
- Actually, `SameSite=lax` **does** block cross-site POST cookies — only same-site and top-level GET navigations are allowed

Wait — `SameSite=lax` cookies **are** sent on top-level GET navigations from other sites but **not** on cross-site POST/PUT/DELETE requests. So this is actually well-protected for the JSON API endpoints (which require `Content-Type: application/json`).

**Revised Impact:** Low. The combination of `SameSite=lax` + JSON Content-Type requirement effectively prevents CSRF for the API endpoints. The one-click approval link (`GET /api/admin/approve/{id}`) is a state-changing GET, but it uses HMAC signature verification which provides equivalent CSRF protection.

**Recommendation:** No immediate action needed. The current protections are adequate.

---

### M4. Rate Limiting Only on Open-Meteo, Not on Compute-Heavy Endpoints

**Location:** `src/weatherbrief/api/usage.py:96-114`

Rate limiting is enforced per-user-per-day on:
- Open-Meteo API calls (50/day)
- GRAMET calls (20/day) — graceful skip
- LLM digest calls (20/day) — graceful skip

However, there is **no rate limiting** on:
- **Advisory recalculation** (`POST .../advisories/recalculate`) — CPU-intensive
- **Report generation** (`GET .../report.pdf`) — WeasyPrint PDF rendering is very CPU-intensive
- **Skew-T on-demand generation** — matplotlib rendering is CPU-intensive
- **Observation refresh** (`POST .../observations/refresh`) — external API calls to METAR/TAF sources

**Impact:** An authenticated user could DoS the server by repeatedly requesting PDF report generation or Skew-T rendering, which are CPU/memory intensive operations.

**Recommendation:** Add rate limiting or caching for compute-heavy endpoints:
1. Cache generated PDFs on disk (like Skew-T images already are)
2. Add a simple per-user rate limit for heavy operations (e.g., 10 PDF generations/hour)
3. Consider adding request timeouts for matplotlib/WeasyPrint operations

---

### M5. Error Messages Leak Internal Details in Dev Mode

**Location:** `src/weatherbrief/api/packs.py:514`

```python
detail = f"Briefing fetch failed: {exc}" if is_dev_mode() else "Briefing fetch failed"
```

This pattern is correctly applied here (dev-only details), but some endpoints leak exception details unconditionally:

- `packs.py:955`: `detail=f"Skew-T generation failed: {exc}"` — always exposed
- `packs.py:999`: `detail=f"Hodograph generation failed: {exc}"` — always exposed
- `packs.py:1446`: `detail=f"Email send failed: {exc}"` — always exposed (could leak SMTP config)

**Impact:** Internal error details (stack traces, file paths, SMTP configuration) could be exposed to users in production.

**Recommendation:** Apply the same dev-mode conditional pattern to all error detail messages.

---

## LOW Severity

### L1. JWT Secret Derivation in Dev Mode Is Weak but Intentional

**Location:** `src/weatherbrief/api/auth_config.py:12`, `encryption.py:26`

Dev mode uses a hardcoded JWT secret (`dev-insecure-jwt-secret-do-not-use-in-production`) and derives the Fernet encryption key from it. This is clearly marked and intentional for local development.

**Risk:** If `ENVIRONMENT` is accidentally not set to `production` on the server, the hardcoded secret would be used.

**Recommendation:** Add a startup check in production that validates `JWT_SECRET` is set and is not the dev default:

```python
if not is_dev_mode():
    secret = os.environ.get("JWT_SECRET", "")
    if not secret or secret == _DEV_JWT_SECRET:
        raise ValueError("Production requires a unique JWT_SECRET")
```

---

### L2. CSP Blocks Leaflet JS but Allows Leaflet CSS via unpkg

**Location:** `deploy/weather.flyfun.aero.caddy:8`, `web/briefing.html:8`

The Caddy CSP header restricts scripts to `'self'`:
```
script-src 'self'
```

But `briefing.html` loads Leaflet CSS from `unpkg.com`:
```html
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" ...>
```

The CSS load will be blocked by the CSP (`style-src 'self' 'unsafe-inline'` doesn't include `unpkg.com`). This means the Leaflet map likely has broken styling in production.

**Recommendation:** Either:
1. Add `https://unpkg.com` to the `style-src` directive in the CSP
2. Bundle the Leaflet CSS locally (preferred — eliminates external dependency)

Additionally, check if Leaflet JS is loaded from a CDN (which would be blocked by `script-src 'self'`).

---

### L3. `innerHTML` Usage Is Mostly Safe but Has Some Unescaped Paths

**Location:** Various TypeScript files

The codebase has a proper `escapeHtml()` utility and uses it in many places. However, some `innerHTML` assignments interpolate values without escaping:

- `briefing-ui.ts:87`: `fetch_timestamp` values used in `<option value="...">`  — from server data, low risk
- `briefing-ui.ts:162`, `:203`, `:207`: Dynamic labels from server — from trusted server data
- `flights-ui.ts:39`: Flight data rendered with mix of escaped and unescaped fields

Since all data comes from the server (not direct user input) and the CSP blocks inline scripts, the XSS risk is very low. But defense-in-depth suggests escaping all interpolated values.

**Recommendation:** Audit all `innerHTML` assignments and ensure every interpolated value goes through `escapeHtml()`, even for server-sourced data.

---

### L4. Thread Pool Executor Is Unbounded for On-Demand Generation

**Location:** `src/weatherbrief/api/packs.py:520`

```python
_refresh_executor = ThreadPoolExecutor(max_workers=2)
```

The refresh executor is properly bounded. However, on-demand Skew-T and hodograph generation runs synchronously in the request thread (not in the executor). Multiple concurrent requests for uncached Skew-T images could consume all worker threads.

**Recommendation:** Either run on-demand generation through the executor pool, or add a semaphore to limit concurrent generations.

---

### L5. No Input Length Validation on Several String Fields

**Location:** Various API request models

- `CreateFlightRequest.route_name`: No max length (stored in VARCHAR(256))
- `CreateAgentRequest.name`: No max length (stored in VARCHAR(256))
- `FeedbackRequest.flight_id`: No max length (stored in VARCHAR(256))
- `ProfileSettings.flight_rules`: No validation against allowed values

**Impact:** Very low — SQLAlchemy will truncate or error on oversized values. But explicit validation provides better error messages and prevents potential abuse.

**Recommendation:** Add `max_length` constraints to Pydantic models and validate enum-like fields against allowed values.

---

### L6. `Content-Disposition` Header in PDF Download Uses Unsanitized Route Name

**Location:** `src/weatherbrief/api/packs.py:1402-1407`

```python
route_slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", flight.route_name or "-".join(flight.waypoints))
filename = f"briefing_{route_slug}_{flight.target_date}_d{meta.days_out}.pdf"
return Response(
    content=pdf_bytes,
    headers={"Content-Disposition": f'attachment; filename="{filename}"'},
)
```

The regex sanitization is good, but the filename could still be very long. Some browsers have issues with extremely long Content-Disposition filenames.

**Recommendation:** Truncate the filename to a reasonable length (e.g., 100 characters).

---

### L7. Observation Refresh Has No Ownership Check on Flight

**Location:** `src/weatherbrief/api/packs.py:736-749`

```python
flight = _load_owned_flight(db, flight_id, user_id)
```

Actually, this **does** check ownership via `_load_owned_flight`. This is correct. No issue here upon closer inspection.

---

## INFORMATIONAL

### I1. SQLAlchemy ORM Prevents SQL Injection

All database queries use SQLAlchemy's ORM and parameterized queries. No raw SQL is used anywhere in the codebase. **SQL injection is not a concern.**

### I2. Path Traversal in File Storage Is Well-Mitigated

The `safe_path_component()` function in `storage/flights.py` properly strips all path separators and traversal sequences. It's consistently used in `pack_dir_for()` for `user_id`, `flight_id`, and `timestamp`. The only gap is the Skew-T/hodograph endpoints (H1 above).

### I3. Cookie Security Is Properly Configured

- `httponly=True` — prevents JavaScript access
- `samesite="lax"` — prevents CSRF for non-GET requests
- `secure=True` in production — prevents transmission over HTTP
- 7-day expiry — reasonable session lifetime
- JWT includes `exp` claim — properly verified on decode

### I4. Caddy Security Headers Are Comprehensive

The Caddy config includes:
- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: DENY`
- `Strict-Transport-Security` with `includeSubDomains; preload`
- `Content-Security-Policy` with restrictive defaults
- `Referrer-Policy: strict-origin-when-cross-origin`
- `Permissions-Policy` disabling camera, mic, geolocation
- Server header removed

---

## Summary of Recommendations (Priority Order)

| # | Severity | Finding | Fix Effort |
|---|----------|---------|-----------|
| H1 | High | Skew-T path injection via unsanitized `icao`/`model` | Small — add validation |
| H2 | High | All flights visible to all authenticated users (IDOR) | Medium — add visibility controls |
| M4 | Medium | No rate limiting on CPU-heavy endpoints | Medium — add caching + limits |
| M5 | Medium | Error messages leak internal details | Small — conditional error details |
| M2 | Medium | SSE session leak potential | Small — add try/finally |
| L1 | Low | Validate JWT_SECRET in production startup | Small |
| L2 | Low | CSP vs Leaflet CSS mismatch | Small — bundle locally |
| L3 | Low | innerHTML without escaping in some places | Small — audit and add escapeHtml |
| L4 | Low | Unbounded on-demand image generation | Small — add semaphore |
| L5 | Low | Missing input length validation | Small — add constraints |
| L6 | Low | Long Content-Disposition filenames | Trivial |

---

## What's Done Well

1. **ORM-only database access** — no SQL injection surface
2. **Path sanitization** via `safe_path_component()` — consistently applied
3. **Fernet encryption** for autorouter credentials at rest
4. **Proper JWT implementation** — HS256, expiry, httponly cookies
5. **API token security** — SHA-256 hashed, revocable, expiring
6. **HTML escaping** — `escapeHtml()` utility used throughout frontend, `html.escape()` in email templates
7. **Production hardening** — docs disabled, CORS locked down, security headers
8. **Non-root Docker container** (UID 2000)
9. **Approval workflow** with HMAC-signed one-click links
10. **Auto-reload credits** prevent negative balance abuse
