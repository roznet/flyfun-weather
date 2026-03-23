# Security Audit — WeatherBrief Web App

**Initial audit:** 2026-02-26
**Updated:** 2026-03-23
**Scope:** Full-stack security review of the WeatherBrief web application — FastAPI backend, SQLAlchemy/SQLite/MySQL database, vanilla TypeScript frontend, file-based artifact storage, and deployment infrastructure.

---

## Executive Summary

WeatherBrief is generally well-architected from a security perspective for its intended use case (small group of trusted pilots). The codebase demonstrates security awareness: path sanitization, HTML escaping, parameterized queries via ORM, encrypted credential storage, proper cookie flags, and comprehensive security headers via Caddy. However, several issues were identified ranging from **medium-severity authorization gaps** to **low-severity hardening opportunities**.

Since the initial audit, significant fixes have been applied (H1, H2, M2, M4, M5, L1–L6). The March 2026 re-audit found **5 new medium-severity** and **4 new low-severity** issues, mostly around residual error leakage, fragile authorization defaults, and frontend DOM injection.

**Critical findings: 0** | **High: 2** | **Medium: 10** | **Low: 11** | **Informational: 7**

> **Fixed:** H1, H2, M2, M4, M5, L1, L2, L3, L4, L5, L6.

---

## HIGH Severity

### H1. Skew-T/Hodograph Path Injection via `icao` and `model` Parameters — FIXED

**Location:** `src/weatherbrief/api/packs.py:30-41`

**Fix applied:** `_validate_icao()` and `_validate_model()` now validate all path-sensitive parameters with strict regex patterns (`^[A-Z]{4}$` for ICAO, `^[A-Za-z0-9_-]+$` for model names) before file path construction.

---

### H2. Shareable Briefing Links Expose All Flight Data to Any Authenticated User (IDOR) — FIXED

**Location:** `src/weatherbrief/api/flights.py:533-541`

**Fix applied:** Per-flight `private` flag with `_load_flight_or_404` enforcing visibility. Non-private flights remain intentionally shareable among authenticated users. See M8 for a remaining defensive concern with this function.

---

## MEDIUM Severity

### M1. Admin Check Bypasses Bearer Token Authentication

**Location:** `src/weatherbrief/api/admin.py:44-72`

The `require_admin` dependency only checks JWT cookies — it does not check Bearer API tokens. If admin API token access is ever needed, this creates an inconsistency with `current_user_id` which supports both auth methods.

**Impact:** Low — agents can't access admin endpoints. Currently correct behavior, but undocumented.

**Recommendation:** Document this as intentional, or update `require_admin` to support Bearer tokens if needed.

---

### M2. SSE Streaming Endpoint Manages Its Own DB Session — Potential Session Leak — FIXED

**Location:** `src/weatherbrief/api/packs.py`

**Fix applied:** Session management now uses `try/finally` for guaranteed cleanup.

---

### M3. No CSRF Protection on State-Changing Endpoints — No Action Needed

**Revised Impact:** Low. The combination of `SameSite=lax` on the auth cookie + JSON Content-Type requirement effectively prevents CSRF. The one-click approval link uses HMAC signature verification.

---

### M4. Rate Limiting Only on Open-Meteo, Not on Compute-Heavy Endpoints — FIXED

**Location:** `src/weatherbrief/api/throttle.py`

**Fix applied:** Per-user sliding-window rate limiters added for PDF rendering (10/hour via `pdf_limiter`), plot generation (60/hour via `plot_limiter`), and a server-wide concurrency semaphore (3 concurrent generations via `generation_slot`).

---

### M5. Error Messages Leak Internal Details — PARTIALLY FIXED

**Location:** `src/weatherbrief/api/packs.py`

Most error responses now use the `is_dev_mode()` conditional pattern:
```python
detail=f"Skew-T generation failed: {exc}" if is_dev_mode() else "Skew-T generation failed"
```

**Remaining instances** — see M6 below for two error paths that still leak unconditionally.

---

### M6. Residual Error Detail Leakage in Production (NEW — 2026-03-23)

**Location:** `src/weatherbrief/api/packs.py:674`, `:676`

Two error handlers in the briefing refresh endpoint still expose exception details unconditionally in production:

```python
except ImportError as exc:
    raise HTTPException(status_code=503, detail=f"Missing dependency: {exc}")
except ValueError as exc:
    raise HTTPException(status_code=400, detail=str(exc))
```

The `ImportError` message reveals which Python packages are installed/missing. The `ValueError` message could expose internal validation logic or data shapes.

**Impact:** Information disclosure — internal implementation details exposed to any authenticated user who triggers a refresh error.

**Recommendation:** Apply the same dev-mode conditional pattern:
```python
except ImportError as exc:
    logger.warning("Refresh failed (missing dependency): %s", exc)
    detail = f"Missing dependency: {exc}" if is_dev_mode() else "Service temporarily unavailable"
    raise HTTPException(status_code=503, detail=detail)
except ValueError as exc:
    detail = str(exc) if is_dev_mode() else "Invalid request"
    raise HTTPException(status_code=400, detail=detail)
```

Additionally, `packs.py:1943` has the same pattern for the email endpoint:
```python
except ValueError as exc:
    raise HTTPException(status_code=400, detail=str(exc))
```

---

### M7. SessionMiddleware Reuses JWT Secret (NEW — 2026-03-23)

**Location:** `src/weatherbrief/api/app.py:129-134`

```python
app.add_middleware(
    SessionMiddleware,
    secret_key=get_jwt_secret(),    # Same secret as JWT tokens
    same_site="none",
    https_only=not is_dev_mode(),
)
```

Two concerns:

1. **Secret reuse:** The Starlette `SessionMiddleware` and JWT authentication share the same secret (`get_jwt_secret()`). If one system is compromised (e.g., session cookie forgery), the other is also compromised. Cryptographic best practice is to use separate keys for separate purposes.

2. **`SameSite=none`:** The session cookie uses `SameSite=none`, which is the most permissive setting. The comment explains this is needed for Apple OAuth's `response_mode=form_post`. This is acceptable for the OAuth state cookie, but broadens the attack surface compared to `lax`.

**Impact:** Medium — shared secret increases blast radius of a key compromise. The `SameSite=none` setting is a necessary trade-off for Apple OAuth.

**Recommendation:**
1. Use a separate secret for `SessionMiddleware`:
   ```python
   session_secret = os.environ.get("SESSION_SECRET", get_jwt_secret() + "-session")
   ```
2. Document why `SameSite=none` is required (Apple OAuth) so it isn't accidentally changed.

---

### M8. Fragile Authorization Default in `_load_flight_or_404` (NEW — 2026-03-23)

**Location:** `src/weatherbrief/api/flights.py:533-541`

```python
def _load_flight_or_404(db: Session, flight_id: str, *, viewer_id: str | None = None) -> Flight:
    """Load a flight by ID. Returns 404 if not found or private and not owned by viewer."""
    try:
        flight = load_flight(db, flight_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Flight '{flight_id}' not found")
    if flight.private and viewer_id is not None and flight.user_id != viewer_id:
        raise HTTPException(status_code=404, detail=f"Flight '{flight_id}' not found")
    return flight
```

The `viewer_id` parameter defaults to `None`. When `None` is passed, the private-flight check is **silently skipped** — private flights become visible. This is a "fail-open" design.

Currently, the only internal caller that omits `viewer_id` is `_load_owned_flight()` (line 546), which immediately enforces ownership. So there is **no active vulnerability today**. However, the fail-open default means any future caller that forgets to pass `viewer_id` will unintentionally bypass the privacy check.

**Impact:** No current exploit, but high risk of future regression. A single missed `viewer_id=user_id` in a new endpoint would silently expose private flights.

**Recommendation:** Invert the default to fail-closed:
```python
def _load_flight_or_404(db: Session, flight_id: str, *, viewer_id: str) -> Flight:
    # viewer_id is now required — callers must explicitly provide it
```
Or if truly optional access is needed (e.g., internal background tasks), use a separate function name like `_load_flight_internal()` to make the bypass explicit.

---

### M9. Unhandled JSON Parse Errors on Disk Artifact Files (NEW — 2026-03-23)

**Location:** `src/weatherbrief/api/packs.py:1579`, `:1590`, `:1684`, `:1694`, `:1773`, `:1784`

```python
ra_data = json.loads(ra_path.read_text())
cs_data = json.loads(cs_path.read_text())
```

Six `json.loads()` calls read JSON artifacts from disk without error handling. If a file is corrupted (partial write, disk error, interrupted pipeline), `json.JSONDecodeError` propagates as an unhandled 500 error. Depending on the exception message, this could leak file paths and partial file contents in the response.

**Impact:** Unhandled exceptions cause 500 errors with potentially informative stack traces. Corrupted files become unrecoverable without manual intervention.

**Recommendation:** Wrap disk JSON reads in try/except:
```python
try:
    ra_data = json.loads(ra_path.read_text())
except (json.JSONDecodeError, OSError) as exc:
    logger.warning("Corrupted artifact %s: %s", ra_path, exc)
    raise HTTPException(status_code=500, detail="Briefing data corrupted — try refreshing")
```

---

### M10. No Rate Limiting on Admin Approval Endpoint (NEW — 2026-03-23)

**Location:** `src/weatherbrief/api/admin.py:425-472`

```python
@router.get("/approve/{user_id}", response_class=HTMLResponse)
def one_click_approve(request: Request, user_id: str, ts: str, sig: str, ...):
```

The one-click approval endpoint:
- Is publicly accessible (no login required — auth is via HMAC signature)
- Has no rate limiting per IP
- Accepts a `user_id` path parameter that could be enumerated
- Returns different HTTP status codes for "invalid signature" (403), "expired" (410), and "user not found" (404), enabling user enumeration

The HMAC validation is strong (SHA-256 with full secret), making brute-force impractical. However, the endpoint could be used for user ID enumeration by observing response codes, and lack of rate limiting means automated scanning is possible.

**Impact:** Low-medium — user ID enumeration is possible. HMAC brute-force is impractical but the endpoint is unbounded.

**Recommendation:**
1. Return the same error code (403) for all failure cases to prevent enumeration
2. Add per-IP rate limiting (e.g., 10 requests/minute)
3. Consider adding a CAPTCHA or requiring authentication for non-HMAC paths

---

## LOW Severity

### L1. JWT Secret Derivation in Dev Mode Is Weak but Intentional — FIXED

**Fix applied:** Startup validation added.

---

### L2. CSP Blocks Leaflet CSS via unpkg — FIXED

**Fix applied:** Leaflet CSS bundled locally.

---

### L3. `innerHTML` Usage Is Mostly Safe but Has Some Unescaped Paths — FIXED

**Fix applied:** `escapeHtml()` consistently applied to interpolated values in major UI files.

---

### L4. Thread Pool / Concurrency for On-Demand Generation — FIXED

**Fix applied:** Server-wide `generation_slot()` semaphore (3 concurrent) added in `throttle.py`.

---

### L5. No Input Length Validation on Several String Fields — FIXED

**Fix applied:** `max_length` constraints added to Pydantic models (`CreateFlightRequest.route_name`, `CreateAgentRequest.name`, `FeedbackRequest` fields).

---

### L6. `Content-Disposition` Header in PDF Download Uses Long Route Name — FIXED

**Fix applied:** Filename truncated.

---

### L7. Observation Refresh Has No Ownership Check — Not an Issue

`_load_owned_flight` correctly checks ownership. No fix needed.

---

### L8. `innerHTML` with Unescaped Error Messages in Admin UI (NEW — 2026-03-23)

**Location:** `web/ts/admin-main.ts:111`, `:435`, `:524`

```typescript
container.innerHTML = `<p style="color:#dc3545;...">Failed to load feedback: ${err}</p>`;
container.innerHTML = `<p style="color:#dc3545;...">Failed to load metrics: ${err}</p>`;
tbody.innerHTML = `<tr><td ...>Failed to load: ${err}</td></tr>`;
```

Error messages from failed API calls are inserted into the DOM via `innerHTML` without escaping. While `err` is typically a browser-generated error string or an HTTP status message, a malicious server response or MITM attacker could craft an error message containing HTML/JavaScript.

The CSP (`script-src 'self'`) would block inline script execution, but HTML injection (phishing content, fake login forms) would still work.

**Impact:** Low — requires MITM or malicious server response, and CSP blocks script execution. Admin-only pages further limit exposure.

**Recommendation:** Use `escapeHtml()` or `textContent` instead of `innerHTML` for error messages:
```typescript
container.textContent = `Failed to load feedback: ${err}`;
```

---

### L9. ProfileSettings Has Unvalidated Enum-Like Fields (NEW — 2026-03-23)

**Location:** `src/weatherbrief/api/profiles.py:29-44`

```python
class ProfileSettings(BaseModel):
    icing_method: str | None = None    # "ogimet_dd", "ogimet_nwp", or "sfip_nwp"
    cloud_method: str | None = None    # "dd" or "nwp"
    convective_method: str | None = None  # "thermo" or "nwp"
    flight_rules: str | None = None    # "vfr_only" or "vfr_ifr"
    advisories: dict | None = None     # {enabled: {}, params: {}}
```

Several fields have documented valid values (in comments) but no Pydantic validators. Users can store arbitrary strings that may cause unexpected behavior in downstream processing. The `advisories` field accepts an untyped `dict` — no depth or key validation.

**Impact:** Low — invalid values would likely cause runtime errors in analysis code rather than security issues. But deeply nested `advisories` dicts could cause performance issues.

**Recommendation:** Add `Literal` type constraints or `@field_validator` for enum fields:
```python
from typing import Literal
flight_rules: Literal["vfr_only", "vfr_ifr"] | None = None
icing_method: Literal["ogimet_dd", "ogimet_nwp", "sfip_nwp"] | None = None
```
Type the `advisories` field using `AdvisoryPreferences` instead of bare `dict`.

---

### L10. Missing `max_length` on Profile Name Fields (NEW — 2026-03-23)

**Location:** `src/weatherbrief/api/profiles.py:58-76`

```python
class CreateProfileRequest(BaseModel):
    name: str                       # No max_length
    settings: ProfileSettings | None = None

class DuplicateProfileRequest(BaseModel):
    name: str                       # No max_length
```

Profile names have no length constraint in the Pydantic model. While the database column likely has a max length, the error message from a DB truncation/rejection is less user-friendly than a Pydantic validation error.

**Recommendation:** Add `Field(max_length=256)` to profile name fields.

---

### L11. Missing Altitude Range Validation (NEW — 2026-03-23)

**Location:** `src/weatherbrief/api/flights.py:39-40`, `src/weatherbrief/api/profiles.py:32-33`

```python
cruise_altitude_ft: int | None = None
flight_ceiling_ft: int | None = None
```

No range validation on altitude fields. Values could be negative, zero, or unreasonably large (e.g., 999,999 ft). While unlikely to cause security issues, extreme values could cause unexpected behavior in weather analysis calculations (pressure level lookups, icing analysis at negative altitudes, etc.).

**Recommendation:** Add range validators:
```python
@field_validator("cruise_altitude_ft", "flight_ceiling_ft")
@classmethod
def validate_altitude(cls, v):
    if v is not None and not (0 <= v <= 60000):
        raise ValueError("Altitude must be 0–60,000 ft")
    return v
```

---

## INFORMATIONAL

### I1. SQLAlchemy ORM Prevents SQL Injection

All database queries use SQLAlchemy's ORM and parameterized queries. No raw SQL is used anywhere in the codebase. **SQL injection is not a concern.**

### I2. Path Traversal in File Storage Is Well-Mitigated

The `safe_path_component()` function in `storage/flights.py` uses a strict whitelist regex (`[^a-zA-Z0-9._-]` → `_`). It's consistently used in `pack_dir_for()` for `user_id`, `flight_id`, and `timestamp`. H1 gap now fixed with `_validate_icao()` and `_validate_model()`.

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

### I5. CORS Wildcard with Credentials in Dev Mode (NEW — 2026-03-23)

**Location:** `src/weatherbrief/api/app.py:136-143`

```python
if is_dev_mode():
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        ...
    )
```

`allow_origins=["*"]` combined with `allow_credentials=True` is a CORS misconfiguration. However, browsers will **not** honor `Access-Control-Allow-Origin: *` when credentials are included — they require an explicit origin. So this is effectively broken CORS in dev mode rather than a security vulnerability.

No production risk since the middleware is only added in dev mode. But it means dev-mode cross-origin requests with cookies may fail silently.

**Recommendation:** Use explicit origins in dev mode (e.g., `["http://localhost:3000", "http://localhost:8000"]`).

### I6. No External SSRF Risk (NEW — 2026-03-23)

All outbound HTTP calls in the fetch layer use hardcoded URLs:
- Open-Meteo API (`elevation.py`, `open_meteo.py`)
- DWD text forecasts (`dwd_text.py`)
- Model status checks (`model_status.py`)

No user-supplied URLs are used in outbound requests. **SSRF is not a concern.**

### I7. Email Injection Is Well-Mitigated (NEW — 2026-03-23)

Email templates in `notify/admin_email.py` properly use `html.escape()` for all user-supplied values (name, email, user_id). Email addresses come from Google OAuth (validated by Google) or admin input. SMTP headers use `MIMEMultipart` which handles encoding safely. **Email injection is not a concern.**

---

## Summary of Recommendations (Priority Order)

| # | Severity | Finding | Status | Fix Effort |
|---|----------|---------|--------|-----------|
| H1 | High | Skew-T path injection via unsanitized `icao`/`model` | **FIXED** | — |
| H2 | High | All flights visible to all authenticated users (IDOR) | **FIXED** | — |
| M6 | Medium | Residual error detail leakage in production | **OPEN** | Small — add dev-mode check |
| M7 | Medium | SessionMiddleware reuses JWT secret | **OPEN** | Small — separate secret |
| M8 | Medium | Fragile fail-open default in `_load_flight_or_404` | **OPEN** | Small — make viewer_id required |
| M9 | Medium | Unhandled JSON parse errors on disk artifacts | **OPEN** | Small — add try/except |
| M10 | Medium | No rate limiting on admin approval endpoint | **OPEN** | Small — add IP rate limit |
| M4 | Medium | No rate limiting on CPU-heavy endpoints | **FIXED** | — |
| M5 | Medium | Error messages leak internal details | **FIXED** | — |
| M2 | Medium | SSE session leak potential | **FIXED** | — |
| L8 | Low | innerHTML with unescaped errors in admin UI | **OPEN** | Small |
| L9 | Low | Unvalidated enum-like profile fields | **OPEN** | Small |
| L10 | Low | Missing max_length on profile names | **OPEN** | Trivial |
| L11 | Low | Missing altitude range validation | **OPEN** | Small |
| L1 | Low | Validate JWT_SECRET in production startup | **FIXED** | — |
| L2 | Low | CSP vs Leaflet CSS mismatch | **FIXED** | — |
| L3 | Low | innerHTML without escaping in some places | **FIXED** | — |
| L4 | Low | Unbounded on-demand image generation | **FIXED** | — |
| L5 | Low | Missing input length validation | **FIXED** | — |
| L6 | Low | Long Content-Disposition filenames | **FIXED** | — |

---

## What's Done Well

1. **ORM-only database access** — no SQL injection surface
2. **Path sanitization** via `safe_path_component()` — consistently applied
3. **Input validation** via `_validate_icao()` and `_validate_model()` for path-sensitive params
4. **Fernet encryption** for autorouter credentials at rest
5. **Proper JWT implementation** — HS256, expiry, httponly cookies
6. **API token security** — SHA-256 hashed, revocable, expiring
7. **HTML escaping** — `escapeHtml()` utility used throughout frontend, `html.escape()` in email templates
8. **Production hardening** — docs disabled, CORS locked down, security headers
9. **Non-root Docker container** (UID 2000)
10. **Approval workflow** with HMAC-signed one-click links
11. **Auto-reload credits** prevent negative balance abuse
12. **Rate limiting + concurrency control** on CPU-heavy endpoints (throttle.py)
13. **No SSRF surface** — all outbound URLs hardcoded
14. **Email injection prevention** — proper escaping and MIME handling
