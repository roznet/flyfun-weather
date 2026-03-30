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
tops_basis              ENUM('crossed','estimated_above','below_min') NULL
    -- crossed:         pilot climbed through top, exact altitude from altimeter
    -- estimated_above: pilot is above cloud, estimating top altitude visually
    -- below_min:       pilot is below/in cloud, tops_msl_ft is a lower bound (tops ≥ value)
temp_c                  FLOAT NULL
wind_dir                INT NULL
wind_speed_kt           INT NULL
remarks                 TEXT NULL
aircraft_id             INT NULL            -- FK to user_aircraft(id), see prerequisite below
pack_id                 INT NULL            -- FK to briefing_packs(id), enables prediction vs observation research
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
POST   /api/pireps                  # Submit a single PIREP
POST   /api/pireps/batch            # Submit multiple PIREPs (offline sync)
GET    /api/pireps?flight_id=X      # List PIREPs linked to a flight
GET    /api/pireps?pack_id=X        # List PIREPs linked to a briefing pack
GET    /api/pireps?airport=EGTF     # List PIREPs near airport
GET    /api/pireps?bounds=...&hours=6  # Map viewport query (Phase 5)
GET    /api/pireps?from=...&to=...  # Historical range query (Phase 5)
POST   /api/watches/route           # Register route watch
DELETE /api/watches/route/{id}      # Cancel route watch
POST   /api/watches/airport         # Register airport watch
PUT    /api/device-token            # Upsert APNs device token (call on every app launch)
```

### Flight-linked PIREPs in briefing view

When a PIREP has a `pack_id`, it appears in the flight's briefing view as a **PIREPs** section (alongside advisories, cross-section, etc.). This shows all PIREPs linked to that flight's packs as a chronological list with the same detail card as the standalone viewer. This is the primary way pilots review their own reports and see community reports along their route after a briefing.

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

## Phase 5 — Web App PIREP Viewer

Requires `pirep_can_view` permission. New **PIREPs** tab in the web app briefing view.

### Two views

#### List view (default)

- Chronological list of recent PIREPs (last 6 hours by default)
- Time range selector to browse historical PIREPs — when viewing historical data, age badges are hidden (all data is old by definition)
- Each row shows: time, location (nearest waypoint or lat/lon), altitude, hazard type icons, severity
- Tap/click a row to expand inline detail card showing all fields (icing type, turbulence, cloud tops, temp, wind, remarks, aircraft type, source)

#### Map view

- Leaflet map (reuse existing route map infrastructure)
- **"Fog of war" overlay:** the entire map has a semi-transparent dark overlay by default. Around each PIREP, a circle (radius ~30 km) is cut out of the overlay, revealing the normal map underneath. This makes it visually unambiguous that areas without PIREPs are **unknown**, not clear.
- PIREPs shown as markers within the cleared circles

##### PIREP marker design

**Hazard type** — marker icon/shape:
- Icing: snowflake
- Turbulence: zigzag
- Cloud/ceiling: cloud
- Multiple hazards: stacked or combined icon
- "None" reports (pilot confirmed clear): checkmark — these are valuable data too

**Severity** — marker color:
- None/clear: green
- Trace/light: yellow
- Moderate: orange
- Severe: red

**Age** (live view only, not historical):
- Fresh (<30 min): full opacity, subtle pulse animation
- Recent (30–90 min): reduced opacity (~70%)
- Stale (>90 min): low opacity (~40%) with "STALE" label, grey-tinted

Tap a marker to show the same detail card as the list view.

### API endpoints

```
GET /api/pireps?bounds=sw_lat,sw_lon,ne_lat,ne_lon&hours=6   # map viewport query
GET /api/pireps?from=2026-03-01T00:00Z&to=2026-03-01T12:00Z  # historical range
```

Both require `pirep_can_view` permission.

-----

## Phase 6 — iOS In-Flight Reporting UI

### Trigger condition

User opens app while:

- A flight is active (within planned time window)
- GPS position is along the planned route
- GPS position is in cruise phase (>20nm from departure/arrival airports)

### Reporting card

Non-intrusive card shown below/beside the existing cross-section view. The form is **prediction-guided**: the app reads the forecast pack at the pilot's current GPS position + altitude and shows fields relevant to what's predicted. This keeps the form short and contextual.

**Prediction-guided field selection:**

- Forecast predicts icing → show icing intensity + type fields
- Forecast predicts turbulence → show turbulence field
- Forecast predicts cloud/low ceiling → show in-cloud + ceiling + tops fields
- Always show: altitude, general conditions, remarks

If nothing significant is predicted, the form is minimal — just "Conditions as expected? [Yes] [No]" with [No] expanding to the full field set.

**Example form (icing + cloud predicted):**

```
Current altitude: [8,500 ft]  ← pre-filled from GPS, editable

In cloud?        [ Yes ]  [ No ]  [ Uncertain ]

Icing:           [ None ] [ Trace ] [ Light ] [ Moderate ]

Cloud tops (optional):  [_____] ft  [ Climbed through ] [ Above, est. ] [ Below, at least ]

[ + More fields ]     ← expands to turbulence, wind, temp, remarks

[ Submit Report ]   [ Skip ]
```

**Key UX principles:**

- Never a modal or pop-up — always opt-in when pilot opens app
- Form is guided by forecast predictions to stay short and relevant
- Show forecast prediction *after* submission, not before (avoids confirmation bias) — the prediction guides which fields appear, but NOT what values are pre-selected
- Skip is one tap, never penalised
- Offline-capable: store locally, sync when connectivity returns

### Altitude input

- Pre-filled from `CLLocation.altitude` (GPS MSL, close enough for layer-level matching)
- Editable number pad if pilot wants to enter indicated altitude
- Record both GPS altitude and reported altitude separately in DB

### Offline sync

PIREPs created without connectivity are stored locally in the iOS app (Core Data or similar) and synced when the network is available. This requires careful handling to avoid stale notifications and duplicates.

#### Client-side

- Each PIREP gets a `client_uuid` (UUID v4) at creation time, stored locally
- Local PIREPs are marked with a `synced` flag (false until server confirms)
- On connectivity restore, the app submits all unsynced PIREPs in `observed_at` order
- If the server returns 409 Conflict (duplicate `client_uuid`), mark as synced and move on
- The app submits all pending PIREPs in a single batch request to `POST /api/pireps/batch`

#### Server-side

- `POST /api/pireps/batch` accepts an array of PIREPs, processes each individually
- Deduplication via `client_uuid` UNIQUE constraint — duplicate inserts return 409, not 500
- For each accepted PIREP, compute `sync_delay = submitted_at - observed_at`

#### Notification rules for late-synced PIREPs

Late-arriving PIREPs are still valuable as data (stored permanently for model validation), but notifications must not mislead:

- **`sync_delay` ≤ 30 minutes:** send notifications normally — weather conditions are still broadly relevant
- **`sync_delay` > 30 minutes:** store the PIREP but **suppress all push notifications** — the weather has likely changed and alerting would be misleading
- **Flight window check:** even for fresh PIREPs, only send notifications to route watches whose `active_from`/`active_to` window is currently active. Don't notify for watches whose flight has already landed.

#### Batch notification coalescing

When multiple PIREPs sync at once (common after landing and reconnecting):

- Group by matching watch (same `watch_id` + `watch_type`)
- If multiple PIREPs match the same watch within a 5-minute processing window, coalesce into a single notification:
  ```json
  {
    "aps": {
      "alert": {
        "title": "3 new PIREPs — EGTF→LSGS route",
        "body": "Moderate icing FL085, Light turbulence FL065, Clear FL045"
      }
    },
    "pirep_ids": [123, 124, 125],
    "flight_id": 456
  }
  ```
- Use the highest severity among the batch for the `min_severity` threshold check — if any PIREP in the batch meets the watch's threshold, send the coalesced notification

-----

## Phase 7 — Post-Flight Debrief

After landing, prompt once with the forecast cross-section overlaid with the route flown.

Simple tap-based assessment per segment:

- **Better than forecast / As forecast / Worse than forecast**
- Flag specific hazards that were notably different

More considered responses than inflight — pilots are relaxed, can think.

-----

## Phase 8 — Model Validation Dataset

PIREPs are pure observation data — no predictions are stored on the PIREP row itself. Instead, each PIREP links to the forecast pack via `pack_id`, and since linked packs are exempt from retention (see Data Retention below), the full forecast data is always available for retrospective comparison.

Validation queries reconstruct predictions by loading the linked pack’s forecast data and interpolating to the PIREP’s lat/lon/altitude/time:

```python
# Pseudocode: reconstruct prediction vs observation for a PIREP
pirep = get_pirep(pirep_id)
pack = load_pack(pirep.pack_id)  # retained indefinitely due to PIREP link
forecast_at_pirep = interpolate_forecast(
    pack.forecasts, pirep.latitude, pirep.longitude,
    pirep.gps_altitude_ft, pirep.observed_at
)
comparison = {
    "observed_icing": pirep.icing_intensity,
    "ecmwf_predicted_icing": forecast_at_pirep["ecmwf"]["icing"],
    "gfs_predicted_icing": forecast_at_pirep["gfs"]["icing"],
    # ... same for cloud, turbulence, etc.
}
```

Over time this builds a dataset of NWP model accuracy at GA-relevant altitudes in European airspace — something met offices don’t currently track. Reconstructing from the full pack (rather than storing snapshots) means validation can be re-run as analysis methods improve.

### Data Retention

PIREPs are **never deleted**. They form a permanent observation dataset for model validation research.

Briefing packs linked to PIREPs are **exempt from retention cleanup**. The existing tiered retention system (`tasks/retention.py`) must check for linked PIREPs before pruning:

- **T1 (strip heavy artifacts):** skip packs that have at least one linked PIREP — the full forecast data (`forecasts.json`, `cross_section.json`) is needed to compare predictions against observations
- **T2 (delete pack entirely):** skip packs with linked PIREPs entirely

Implementation: add a subquery check in the retention task:
```python
# In retention.py, when selecting packs for cleanup:
# Exclude packs that have linked PIREPs
packs_with_pireps = select(PirepRow.pack_id).where(PirepRow.pack_id.isnot(None)).distinct()
query = query.where(BriefingPackRow.id.notin_(packs_with_pireps))
```

This ensures every PIREP retains its full forecast context for retrospective analysis indefinitely.

-----

## PIREP Data Format

Follows US PIREP structure with additions for ceiling/tops:

|Field                 |US PIREP equivalent|Notes                                              |
|----------------------|-------------------|---------------------------------------------------|
|Location + time       |UL + TM            |From GPS                                           |
|Altitude              |FL                 |GPS pre-filled, editable                           |
|Aircraft              |TP                 |From user_aircraft registry (ICAO type + tail number)|
|Cloud/ceiling         |SK                 |Added ceiling MSL field                            |
|Cloud tops            |SK                 |Added tops + basis (crossed/estimated_above/below_min)|
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

## Prerequisite: Aircraft Registry

**Separate feature, implemented before PIREPs.** A user-managed list of aircraft, required so PIREPs can link to an `aircraft_id` with known ICAO type for icing/turbulence intensity interpretation.

### `icao_aircraft_types` (reference table)

```sql
id                      INT PRIMARY KEY AUTO_INCREMENT
icao_code               VARCHAR(4) UNIQUE NOT NULL  -- e.g. SR22, C172, PA28, DA40
manufacturer            VARCHAR(100)                -- e.g. Cirrus, Cessna, Piper, Diamond
model                   VARCHAR(100)                -- e.g. SR22, 172S Skyhawk, Warrior III, DA40 NG
category                ENUM('SEP','MEP','SET','MET','JET') NULL  -- for filtering
```

Pre-populated with common GA types. Searchable by ICAO code, manufacturer, or model name. Source: ICAO DOC 8643 aircraft type designators, filtered to GA-relevant entries.

### `user_aircraft`

```sql
id                      INT PRIMARY KEY AUTO_INCREMENT
user_id                 VARCHAR(64) NOT NULL  -- FK to users(id)
icao_type_id            INT NOT NULL          -- FK to icao_aircraft_types(id)
tail_number             VARCHAR(10) NULL      -- e.g. N12345, G-ABCD, HB-XYZ
nickname                VARCHAR(50) NULL      -- e.g. "Club SR22", "My Archer"
is_default              BOOLEAN DEFAULT FALSE -- default aircraft for new flights/PIREPs
created_at              DATETIME UTC
```

### API endpoints

```
GET    /api/aircraft-types?q=SR2       # Search ICAO types (autocomplete)
GET    /api/aircraft                    # List user's aircraft
POST   /api/aircraft                    # Add aircraft to user's list
PUT    /api/aircraft/{id}               # Update tail number, nickname, default
DELETE /api/aircraft/{id}               # Remove from user's list
```

This is a standalone feature — useful for flight profiles too (link `FlightProfileRow` to a `user_aircraft` entry). Implement as a separate issue/PR before the PIREP work begins.

-----

## Implementation Sequence

### Milestone 0 — Aircraft Registry (prerequisite, separate PR)
1. **Alembic migration** — `icao_aircraft_types` + `user_aircraft` tables
1. **Seed ICAO types** — import GA-relevant subset of DOC 8643
1. **API endpoints** — CRUD + type search
1. **UI** — aircraft picker in flight profile settings

### Milestone 1 — PIREP Publish & View
1. **Alembic migration** — `pireps` table (include `device_tokens` table now so it's ready for Milestone 2)
1. **FastAPI endpoints** — PIREP submit, batch submit, query by flight/pack/bounds/time range
1. **Retention integration** — exempt PIREP-linked packs from T1/T2 cleanup
1. **Web app PIREP viewer** — PIREPs tab with list view, fog-of-war map, detail cards, flight briefing integration
1. **iOS reporting card** — prediction-guided inflight UI with offline sync

### Milestone 2 — Notifications (builds on Milestone 1)
1. **Alembic migration** — `route_watches`, `airport_watches`, `notification_log` tables
1. **Shapely spatial matching** — standalone utility, easily testable
1. **Watch endpoints** — route/airport watch CRUD, device token upsert
1. **APNs p8 setup** — generate key in Apple Developer Portal, implement `notify/apns.py`
1. **Notification trigger** — matching, rate limiting, coalescing, flight window checks

### Milestone 3 — Post-Flight & Validation
1. **Post-flight debrief screen** — segment-level assessment
1. **Model validation tooling** — prediction reconstruction, comparison queries

-----

## Environment Variables to Add

```
APNS_KEY_ID=...           # 10-char key ID from Apple Developer Portal
APNS_TEAM_ID=...          # Your Apple Developer Team ID
APNS_BUNDLE_ID=...        # e.g. aero.flyfun.brief
APNS_P8_KEY_PATH=...      # Path to downloaded .p8 file
```