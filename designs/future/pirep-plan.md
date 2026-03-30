# PIREP Collection & Model Validation — Implementation Plan

## Overview

Add crowdsourced PIREP (pilot weather report) collection to flyfun-weather, with two goals:

1. **Near-real-time condition sharing** — pilots share observed conditions along their route
1. **Longitudinal model validation** — compare NWP forecasts against what pilots actually encountered

This is an open-source, community-driven feature with no commercial entity behind it. Data is sparse by design initially — the value builds over time.

-----

## Design Principles

- **Zero friction in the cockpit** — no pop-ups, no forms; the app pre-populates from the forecast, pilot confirms or corrects
- **Pilot opens app → report is already there** — contextual, not interruptive
- **Avoid confirmation bias** — show predictions *after* pilot responds, not before
- **Staleness is explicit** — stale reports are flagged aggressively, never displayed as current
- **Open source, MIT licensed** — no corporation, community-owned data

-----

## Phase 1 — Database Schema

Add 4 new tables via Alembic migration. No change to existing MySQL setup.

### `pireps`

```sql
id                      INT PRIMARY KEY AUTO_INCREMENT
client_uuid             VARCHAR(36) UNIQUE NULL  -- client-generated UUID for offline dedup
submitted_at            DATETIME UTC        -- when report was filed
observed_at             DATETIME UTC        -- when conditions were observed (may differ if offline)
latitude                FLOAT
longitude               FLOAT
gps_altitude_ft         INT                 -- from CLLocation.altitude
reported_altitude_ft    INT NULL            -- pilot-corrected if different
in_cloud                BOOLEAN NULL
icing_intensity         ENUM('none','trace','light','moderate','severe') NULL
icing_type              ENUM('rime','clear','mixed') NULL
turbulence_intensity    ENUM('none','light','moderate','severe') NULL
ceiling_msl_ft          INT NULL
tops_msl_ft             INT NULL
tops_observed_type      ENUM('observed','estimated') NULL   -- above vs below cloud
temp_c                  FLOAT NULL
wind_dir                INT NULL
wind_speed_kt           INT NULL
remarks                 TEXT NULL
aircraft_type           VARCHAR(10) NULL    -- e.g. SR22, C172 — affects intensity interpretation
pack_id                 INT NULL            -- FK to briefing_packs(id)
predicted_icing         JSON NULL           -- snapshot of model prediction at this point
predicted_cloud         JSON NULL           -- snapshot of model prediction at this point
source                  ENUM('manual','inflight','postflight') DEFAULT 'manual'
user_id                 VARCHAR(64) NULL    -- FK to users(id), nullable for anonymous
```

### `device_tokens`

```sql
id                      INT PRIMARY KEY AUTO_INCREMENT
user_id                 VARCHAR(64) NOT NULL  -- FK to users(id)
token                   VARCHAR(200) NOT NULL
environment             ENUM('sandbox','production') NOT NULL
updated_at              DATETIME UTC
UNIQUE KEY (token)      -- a given token belongs to exactly one user/environment
INDEX (user_id)         -- look up all tokens for a user
```

One user can have multiple devices (iPhone + iPad), each with its own token. A device in development uses `sandbox`, production builds use `production`. Notifications are sent to **all** active tokens for the user, each routed to the correct APNs endpoint.

### `route_watches`

```sql
id                      INT PRIMARY KEY AUTO_INCREMENT
user_id                 VARCHAR(64) NOT NULL  -- FK to users(id)
flight_id               VARCHAR(256) NOT NULL -- FK to flights(id)
route_waypoints         JSON                -- cached GPS coordinates from existing conversion function
corridor_km             INT DEFAULT 50
active_from             DATETIME UTC
active_to               DATETIME UTC
min_severity            ENUM('any','light','moderate','severe') DEFAULT 'light'
created_at              DATETIME UTC
```

### `airport_watches`

```sql
id                      INT PRIMARY KEY AUTO_INCREMENT
user_id                 VARCHAR(64) NOT NULL  -- FK to users(id)
airport_icao            VARCHAR(4)
radius_km               INT DEFAULT 30
min_severity            ENUM('any','light','moderate','severe') DEFAULT 'moderate'
quiet_hours_start       INT NULL            -- hour UTC e.g. 22
quiet_hours_end         INT NULL            -- hour UTC e.g. 6
```

### `notification_log`

```sql
id                      INT PRIMARY KEY AUTO_INCREMENT
user_id                 VARCHAR(64) NOT NULL  -- FK to users(id), recipient
pirep_id                INT NOT NULL          -- FK to pireps(id)
watch_type              ENUM('route','airport')
watch_id                INT
sent_at                 DATETIME UTC
apns_status             VARCHAR(50)
```

### PIREP Permissions

PIREP viewing and publishing are independently controlled per user via the existing `user_preferences` table:

```sql
-- Added to user_preferences JSON or as columns:
pirep_can_view          BOOLEAN DEFAULT FALSE   -- can see other pilots' PIREPs on map/cross-section
pirep_can_publish       BOOLEAN DEFAULT FALSE   -- can submit PIREPs
```

Admin can enable/disable each capability independently per user. This allows:
- **View-only users** — see community PIREPs but can't submit (e.g., student pilots, dispatchers)
- **Publish-only users** — submit reports but don't yet have the viewer (phased rollout)
- **Full access** — both view and publish
- **Disabled** — neither (default for new users until feature is generally available)

The API enforces these permissions:
- `POST /api/pireps` requires `pirep_can_publish`
- `GET /api/pireps` requires `pirep_can_view`
- Admin endpoints can bulk-enable for approved users

### Rate Limiting

PIREP submissions are rate-limited to prevent abuse and notification flooding:

- **Per-user:** max 1 PIREP per 2 minutes (inflight reports are periodic, not continuous)
- **Per-user daily cap:** max 50 PIREPs per 24h (generous for long flights, catches runaway clients)
- **Severe reports:** PIREPs with `moderate`/`severe` icing or turbulence from accounts less than 7 days old are held for admin review before triggering notifications
- Enforced server-side; client should also debounce to avoid wasted requests

-----

## Phase 2 — Spatial Matching (Python/Shapely)

No PostGIS needed. Shapely handles corridor queries at expected volumes (<<100 active watches).

```python
# src/weatherbrief/pirep/matching.py

from shapely.geometry import LineString, Point
import json, math

def degrees_per_km(lat: float) -> float:
    """Approximate degrees-of-longitude per km at given latitude.

    Note: this only corrects the longitude axis. The latitude axis is always
    ~111 km/degree. Using the same factor for Shapely's isotropic buffer()
    produces a slightly elliptical corridor in real-world distance — acceptable
    at mid-latitudes and typical corridor widths (≤50 km), but would need a
    proper geodesic buffer (e.g. pyproj) for polar or very wide corridors.
    """
    cos_lat = math.cos(math.radians(lat))
    if cos_lat < 1e-6:
        cos_lat = 1e-6  # clamp near poles
    return 1.0 / (111.32 * cos_lat)

def find_matching_route_watches(pirep_lat, pirep_lon, active_watches):
    point = Point(pirep_lon, pirep_lat)
    matches = []
    for watch in active_watches:
        waypoints = json.loads(watch.route_waypoints)
        line = LineString([(lon, lat) for lat, lon in waypoints])
        # Buffer in degrees, corrected for longitude at PIREP latitude.
        # Approximation — see degrees_per_km docstring.
        buffer_deg = degrees_per_km(pirep_lat) * watch.corridor_km
        if line.buffer(buffer_deg).contains(point):
            matches.append(watch)
    return matches

def find_matching_airport_watches(pirep_lat, pirep_lon, airport_watches):
    point = Point(pirep_lon, pirep_lat)
    matches = []
    for watch in airport_watches:
        airport_point = Point(watch.airport_lon, watch.airport_lat)
        buffer_deg = degrees_per_km(pirep_lat) * watch.radius_km
        if airport_point.buffer(buffer_deg).contains(point):
            matches.append(watch)
    return matches
```

-----

## Phase 3 — FastAPI Endpoints

Add to existing API structure under `src/weatherbrief/api/`.

```
POST   /api/pireps                  # Submit a PIREP
GET    /api/pireps?flight_id=X      # List PIREPs for a route (with age filter)
GET    /api/pireps?airport=EGTF     # List PIREPs near airport
POST   /api/watches/route           # Register route watch
DELETE /api/watches/route/{id}      # Cancel route watch
POST   /api/watches/airport         # Register airport watch
PUT    /api/device-token            # Upsert APNs device token (call on every app launch)
```

### Device token upsert (call on every app launch)

```python
# Always upsert by token — tokens change silently, and a user may have
# multiple devices (iPhone + iPad), each with its own token.
async def upsert_device_token(user_id: str, token: str, environment: str):
    await db.execute("""
        INSERT INTO device_tokens (user_id, token, environment, updated_at)
        VALUES (:user_id, :token, :environment, NOW())
        ON DUPLICATE KEY UPDATE
            user_id = :user_id,
            environment = :environment,
            updated_at = NOW()
    """, ...)
    # Prune stale tokens for this user not seen in 90 days
    await db.execute("""
        DELETE FROM device_tokens
        WHERE user_id = :user_id AND updated_at < NOW() - INTERVAL 90 DAY
    """, ...)
```

-----

## Phase 4 — APNs Integration

New file: `src/weatherbrief/notify/apns.py` alongside existing `notify/` email infrastructure.

### Use p8 key (not p12 certificates)

- Generate once in Apple Developer Portal → Keys → APNs
- Never expires (unlike p12 which requires annual renewal)
- One key covers all App IDs under your team
- Works for both sandbox and production — just different endpoints

### Environment routing

```python
APNS_ENDPOINTS = {
    'sandbox':    'https://api.sandbox.push.apple.com',
    'production': 'https://api.push.apple.com',
}

async def send_push(device_token: str, environment: str, payload: dict):
    endpoint = APNS_ENDPOINTS[environment]
    # Sign JWT with p8 key, POST to endpoint
    # Handle 410 Gone = invalid token, remove from DB
    ...
```

### Notification trigger flow

```
PIREP submitted (user must have pirep_can_publish permission)
  → check submission rate limit (1 per 2 min, 50 per day)
  → if severe report from new account (<7 days): queue for admin review
  → deduplicate by client_uuid if present
  → find_matching_route_watches()
  → find_matching_airport_watches()
  → for each match:
      check severity >= min_severity
      check not in quiet hours (airport watches)
      check not sent same watch in last 30 min (rate limit)
      look up device token from device_tokens table via watch.user_id
      batch if multiple PIREPs pending for same watch
      send via APNs to correct endpoint (from device_tokens.environment)
      log to notification_log
```

### Notification payload

```json
{
  "aps": {
    "alert": {
      "title": "New PIREP — EGTF→LSGS route",
      "body": "Moderate icing reported at FL085 near DIJON — 8 min ago"
    },
    "sound": "default"
  },
  "pirep_id": 123,
  "flight_id": 456
}
```

-----

## Phase 5 — iOS In-Flight Reporting UI

### Trigger condition

User opens app while:

- A flight is active (within planned time window)
- GPS position is along the planned route
- GPS position is in cruise phase (>20nm from departure/arrival airports)

### Reporting card

Non-intrusive card shown below/beside the existing cross-section view. Pre-populated from forecast pack at current GPS position + altitude.

**Fields shown:**

```
Current altitude: [8,500 ft]  ← pre-filled from GPS, editable

In cloud?        [ Yes ]  [ No ]  [ Uncertain ]

Icing:           [ None ] [ Trace ] [ Light ] [ Moderate ]
                  ↑ pre-selected from forecast, pilot confirms/corrects

Turbulence:      [ None ] [ Light ] [ Moderate ]

Cloud tops (optional):  [_____] ft  [ Observed above ] [ Estimated ]

[ Submit Report ]   [ Skip ]
```

**Key UX principles:**

- Never a modal or pop-up — always opt-in when pilot opens app
- Show forecast prediction *after* submission, not before (avoids confirmation bias)
- Skip is one tap, never penalised
- Offline-capable: store locally, sync when connectivity returns

### Altitude input

- Pre-filled from `CLLocation.altitude` (GPS MSL, close enough for layer-level matching)
- Editable number pad if pilot wants to enter indicated altitude
- Record both GPS altitude and reported altitude separately in DB

-----

## Phase 6 — Post-Flight Debrief

After landing, prompt once with the forecast cross-section overlaid with the route flown.

Simple tap-based assessment per segment:

- **Better than forecast / As forecast / Worse than forecast**
- Flag specific hazards that were notably different

More considered responses than inflight — pilots are relaxed, can think.

-----

## Phase 7 — Model Validation Dataset

Each PIREP automatically links to the forecast pack via `pack_id`, enabling retrospective comparison:

```python
# Query: how accurate was ECMWF icing prediction vs pilot reports?
SELECT
    p.observed_at,
    p.icing_intensity AS reported,
    JSON_EXTRACT(p.predicted_icing, '$.ecmwf.intensity') AS ecmwf_predicted,
    JSON_EXTRACT(p.predicted_icing, '$.gfs.intensity') AS gfs_predicted
FROM pireps p
WHERE p.pack_id IS NOT NULL
  AND p.icing_intensity IS NOT NULL
```

Over time this builds a dataset of NWP model accuracy at GA-relevant altitudes in European airspace — something met offices don’t currently track.


## PIREP Data Format

Follows US PIREP structure with additions for ceiling/tops:

|Field                 |US PIREP equivalent|Notes                                              |
|----------------------|-------------------|---------------------------------------------------|
|Location + time       |UL + TM            |From GPS                                           |
|Altitude              |FL                 |GPS pre-filled, editable                           |
|Aircraft type         |TP                 |From user profile                                  |
|Cloud/ceiling         |SK                 |Added ceiling MSL field                            |
|Cloud tops            |SK                 |Added tops + observed/estimated flag               |
|Icing intensity + type|IC                 |NONE/TRACE/LIGHT/MODERATE/SEVERE + RIME/CLEAR/MIXED|
|Turbulence            |TB                 |NONE/LIGHT/MODERATE/SEVERE                         |
|Temperature           |TA                 |From OAT if available                              |
|Wind                  |WV                 |Optional                                           |
|Remarks               |RM                 |Free text, optional                                |

-----

## Liability & Legal Notes

- MIT licensed, no warranty
- Disclaimer must be prominent in UI, not buried in ToS
- “For situational awareness only — not a substitute for official weather briefings”
- Stale reports (>90 min) flagged visually, not quietly hidden
- Sparse coverage must be explicit — empty map ≠ clear skies
- Consult aviation law specialist before public launch (AOPA Legal Services Plan as starting point, but check scope covers product liability not just certificate defence)

-----

## Implementation Sequence

1. **Alembic migration** — 5 new tables (pireps, device_tokens, route_watches, airport_watches, notification_log), no existing schema changes
1. **Shapely spatial matching** — standalone utility, easily testable
1. **FastAPI endpoints** — PIREP submit + watch registration
1. **APNs p8 setup** — generate key in Apple Developer Portal, implement `notify/apns.py`
1. **iOS reporting card** — inflight contextual UI
1. **Post-flight debrief screen**

-----

## Environment Variables to Add

```
APNS_KEY_ID=...           # 10-char key ID from Apple Developer Portal
APNS_TEAM_ID=...          # Your Apple Developer Team ID
APNS_BUNDLE_ID=...        # e.g. aero.flyfun.brief
APNS_P8_KEY_PATH=...      # Path to downloaded .p8 file
```