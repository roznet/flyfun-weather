# Current Conditions Along the Route

> Future design — researched and access-tested 2026-08-20; not implemented.

## Intent

Add an explicitly **observed now** layer to D-0 briefings using European radar,
total lightning, and satellite cloud-top products. For each route, summarize
conditions within 5, 10, and 20 NM, display them separately from ETA forecasts,
and retain reproducible derived observations for forecast verification.

The first release is decision support, not tactical storm avoidance. It must not
imply that an observation at briefing time will persist until a later departure,
and it must surface source time, latency, coverage, and confidence.

## Source selection

| Need | Initial source | Product | Resolution / cadence | Notes |
|---|---|---|---|---|
| Radar reflectivity | EUMETNET OPERA ORD through MeteoGate | CIRRUS `DBZH` | 1 km / 5 min | Pan-European rolling 10-minute maximum-reflectivity composite |
| Rain rate | EUMETNET OPERA ORD through MeteoGate | NIMBUS `RATE`, optionally `ACRR` | 2 km / 15 min | Prefer `RATE` for current intensity; `ACRR` for recent accumulation |
| Lightning | EUMETSAT Data Store | MTG LI Level 2 flashes, `EO:EUM:DAT:0691` | 10-minute files, event times inside file | Optical total lightning; not specifically cloud-to-ground strikes |
| Cloud tops | EUMETSAT Data Store | MTG FCI CTTH, `EO:EUM:DAT:0681` | 2 km / 10 min | Height MSL, temperature, pressure, parallax offsets, and quality flags |

OPERA composite products are published as ODIM HDF5 or Cloud-Optimized GeoTIFF
and are CC BY 4.0. Single-site and national products can have exceptions, so the
license in each product's metadata remains authoritative. EUMETSAT core data is
also reusable under its data policy; preserve product attribution in exports.

Do not use RainViewer, Blitzortung, or scraped web tiles as production sources.
Exact cloud-to-ground strike classification would require a separate commercial
agreement such as EUCLID, LINET, or Meteorage. LI is nevertheless a strong free
source for convective activity and route-relative flash rate.

## Access and configuration

### EUMETSAT

The existing `.env` variables are sufficient:

```text
EUMETSAT_CONSUMER_KEY=...
EUMETSAT_CONSUMER_SECRET=...
```

Load them with `python-dotenv`; do not shell-source `.env` because the current
file contains values that are not valid shell syntax. Use `eumdac` to obtain a
token, open the collection, search a narrow UTC sensing-time interval, select the
newest complete product, and stream it to a temporary file before atomic rename.
Never log tokens or either credential.

### MeteoGate / OPERA ORD

Anonymous access works for evaluation but has low query limits. For production:

1. Sign in at <https://devportal.meteogate.eu/> with a supported identity provider.
2. Complete the profile, click **Create API Key**, and store the displayed value
   as a new secret such as `METEOGATE_API_KEY`.
3. Click **Show routes** and verify `eu-eumetnet-weather-radar` is available.
4. Exercise the API in the ORD Swagger UI at
   <https://api.meteogate.eu/eu-eumetnet-weather-radar/docs>. Send the key as the
   `apikey` header (preferred) or `apikey` URL parameter.
5. For support, contact `support.opera@eumetnet.eu`. EUMETNET-member staff can
   request priority status from the MeteoGate helpdesk after registering.

Whitelisting is no longer required. Both catalogue queries and the returned S3
downloads work anonymously; the tested quota was 200 API requests/hour anonymous
versus 2,000/hour with the saved key. The public API base is
`https://api.meteogate.eu/eu-eumetnet-weather-radar`. The open 24-hour S3 cache is
also readable without credentials:

```bash
aws s3 ls s3://openradar-24h/YYYY/MM/DD/OPERA/COMP/ \
  --endpoint-url https://s3.waw3-1.cloudferro.com/ --no-sign-request
```

For the steady-state collector, subscribe to `ORD/eu.eumetnet/#` at
`wss://radar.meteogate.eu:8884/ordmqtt` using `everyone` / `everyone`. Each MQTT
notification contains a direct data link; this is more efficient than catalogue
polling. Use the REST API/S3 cache for startup recovery and gaps.

## Architecture

```text
EUMETSAT CTTH + LI       OPERA DBZH + RATE
          \               /
          shared 24-hour cache → frame adapters
                            → route-corridor service
                              ├─ D-0 briefing sidecar
                              └─ verification derived rows
```

The key boundary is **fetch once, query many**. A process-wide collector runs
independently of flights. Briefing refreshes never fetch full-disc files; they
read the newest acceptable cached frame and normally spend only local CPU.

Suggested modules (names are provisional): `ingest.py` for discovery/cache,
`sources/{eumetsat,opera}.py` for normalized frames, `corridor.py` for spatial
sampling, `models.py` for Pydantic contracts, and `verification.py` for derived
rows and scoring.

Run the route sampler after `run_route_weather()` in the D-0 pipeline and before
the optional digest. Persist `current_conditions.json` beside the existing pack
artifacts, with a small availability/freshness reference in `briefing.json`.
Extend the existing real-time observation refresh seam rather than regenerating
NWP. Initially add a deterministic **Observed now** section/map layer and digest
context; do not change advisory severity automatically until calibrated.

## Rolling 24-hour cache

Use `DATA_DIR/current_conditions/<source>/<YYYY>/<MM>/<DD>/...` plus a manifest
containing product ID, sensing start/end, ingestion time, URL, size, checksum,
collection/product version, license, and decode status.

- Poll CTTH every 2 minutes and LI every minute; products arrive every 10 minutes.
- Receive OPERA notifications continuously; run a recovery query after startup
  and periodically to detect missed messages.
- Key files by immutable upstream product ID. If present and checksum-valid, do
  not download again. Write `.partial`, validate, then atomically rename.
- Prune files older than 24 hours only after the newest frame is valid. Keep a
  configurable safety margin and disk cap.
- Retain CTTH and LI flash files. Skip AFA initially: flashes are the correct unit
  for counts and the AFA product adds about 0.43 GB/day.
- Preserve derived rows permanently. Raw cache deletion must not delete source
  IDs, checksums, algorithms, or aggregates needed to explain a score.

Measured CTTH size was 57,222,281 bytes per 10-minute file: about 7.9 GB/day and
2.9 TB/year, which is why raw retention should be short. The tested LI flash file
was 390,081 bytes (roughly 55 MB/day at that activity level). The tested full-Europe
OPERA files were 2.66 MB for DBZH and 1.52 MB for RATE: at 5- and 15-minute cadence,
about 0.77 and 0.15 GB/day respectively. A complete 24-hour radar cache is therefore
only about 0.9 GB. Keep compressed ODIM files and read route bounding windows;
decoding the full grids per briefing would waste memory.

## Cadence and freshness

Treat source times independently; never display one synthetic common timestamp.
DBZH is emitted every 5 minutes as a rolling 10-minute maximum, while RATE is
every 15 minutes. CTTH and LI files arrive every 10 minutes; LI preserves individual
flash times and AFA contains 30-second bins inside the file. In the access test,
LI arrived under one minute after period end and CTTH about 11 minutes later.
Expect radar commonly 2–7 minutes old, LI 1–11 minutes old, and CTTH 10–22 minutes
old. The collector polls only for recovery; MQTT/product notifications drive radar.

## Corridor computation

Use the route geodesic polyline, not distance to sparse route points. Densify long
legs to at most 1–2 NM or compute exact closest distance to each geodesic segment,
and return both cross-track distance and along-route station.

Compute exclusive annuli `0–5`, `5–10`, and `10–20 NM` internally. Publish the
pilot-facing nested values `≤5`, `≤10`, and `≤20 NM`; retaining annuli prevents
accidental double counting and supports density comparisons.

For CTTH:

1. Read only the projected bounding window around the route, expanded enough for
   high-cloud parallax displacement (start with 100 km and test).
2. Convert geostationary `x/y` scan angles using the file projection metadata.
3. Apply each pixel's `delta_latitude` and `delta_longitude` **before** testing
   corridor membership. This ordering is a hard invariant.
4. Exclude fill/failed pixels; separate high-confidence from low-confidence tops.
5. Report max, p90/p95, high-confidence max, coldest top, valid-pixel count,
   coverage/confidence, and first/last affected route station. Heights are MSL.

For LI, use flash latitude/longitude and event time directly. For each corridor
report flashes in the last 10, 30, and 60 minutes, rate, area-normalized density,
nearest distance/time, and trend. Never label this as a ground-strike count.

For radar, report maximum DBZH, route fraction above calibrated thresholds,
maximum/percentile rain rate, nearest echo, and contiguous affected segments.
Later derive an observed convective object by associating radar echo, LI flashes,
and cold/high CTTH pixels. Show both overall cloud tops and tops associated with
electrical/radar activity; one top per CTTH pixel cannot describe multilayer cloud.

## Access tests performed 2026-08-20

### EUMETSAT

The repository virtualenv (`eumdac 3.1.1`, xarray/netCDF4, Satpy, pyproj) and the
credentials in `.env` successfully authenticated and downloaded current products.
No credential value was printed.

| Product | Sensing interval (UTC) | Ingested | Download |
|---|---|---|---|
| CTTH `0681` | 11:40–11:50 | 12:01:33 | 57,222,281 B |
| LI flashes `0691` | 11:50:04–12:00:04 | 12:00:42 | 390,081 B |
| LI AFA `0687` | 11:50–12:00 | 12:00:52 | 3,179,008 B |

The CTTH frame was `5568 × 5568`, nominal/NRT, with complete/timeliness indicators
at 100%. The flash file contained 13,408 full-disc flashes. Download, catalogue,
and authentication completed in under 20 seconds; after files were local, sampling
both airports took roughly 0.5–0.8 seconds.

Parallax-corrected spot checks:

| Airport | Corridor | CTTH result | LI flashes in preceding 10 min |
|---|---:|---|---:|
| LFMN | ≤5 NM | max 8,673 m / FL285 approx.; 4 valid pixels | 0 |
| LFMN | ≤10 NM | max 9,612 m / FL315 approx.; coldest 230.25 K | 0 |
| LFMN | ≤20 NM | max 11,328 m / FL372 approx.; coldest 220.26 K | 236 |
| EGLL | ≤5 NM | median 623 m; max 1,855 m; low confidence | 0 |
| EGLL | ≤10 NM | median 1,519 m; max 2,894 m; low confidence | 0 |
| EGLL | ≤20 NM | median 1,900 m; max 4,521 m; 13 high-confidence pixels | 0 |

The nearest LFMN flash was 13.3 NM away at 11:51:33 UTC; the nearest EGLL flash
was 28.2 NM away. These results match the intended discriminating case: deep
convection near Nice and predominantly low cloud near Heathrow. An earlier query
without parallax correction was materially misleading, validating the invariant.

### MeteoGate OPERA

The saved `METEOGATE_API_KEY` authenticated using the `apikey` header without being
logged. Anonymous catalogue access and unkeyed S3 downloads were also successful.
Two catalogue requests plus both downloads completed in about 4.4 seconds.

| Product | Valid time (UTC) | Grid | Whole-Europe download |
|---|---|---:|---:|
| CIRRUS DBZH | 12:20:01–12:30:00 | `4400 × 3800`, 1 km | 2,656,176 B |
| NIMBUS RATE | 12:15:00 | `2200 × 1900`, 2 km | 1,520,398 B |

| Airport | Corridor | Max DBZH | Max RATE | Operational reading |
|---|---:|---:|---:|---|
| EGLL | ≤5 NM | 21.0 dBZ | 0.27 mm/h | Field pixel dry; weak echo starts ~4.4 NM away |
| EGLL | ≤10 NM | 40.5 dBZ | 9.16 mm/h | Moderate/heavy shower east, ~7–10 NM away |
| EGLL | ≤20 NM | 58.5 dBZ | 57.79 mm/h | Strong cells ~11–14 NM away, not over field |
| LFMN | ≤5 NM | 56.0 dBZ | 61.21 mm/h | Embedded storm; ≥45 dBZ within 0.6 NM |
| LFMN | ≤10 NM | 60.5 dBZ | 153.76 mm/h | Extensive intense convection |
| LFMN | ≤20 NM | 62.0 dBZ | 344.24 mm/h | Extreme single-pixel RATE; use p95/coverage too |

At LFMN the 5 NM DBZH p95 was 54.5 dBZ and 28% of available pixels exceeded
45 dBZ. At EGLL no pixel within 5 NM exceeded 35 dBZ; the 20 NM maximum shows why
every headline needs nearest distance, direction, p95, and affected-area fraction.
The DBZH `qi_total` layer was zero for most detected pixels, unlike RATE quality
(about 0.91–1.0); verify its producer semantics before treating zero as low quality.

## Historical verification

Do not force spatial observations into airport METAR rows. Write a separate
partitioned Parquet stream under `DATA_DIR/archive/current_conditions/`, following
the tiered pattern in `metar-taf-accuracy.md`; keep daily/monthly aggregates in the
database for dashboards.

Each derived row needs route/forecast identity, valid and observed times, source
product IDs and versions, retrieval latency, corridor algorithm/version, width and
annulus, quality/coverage, metrics, checksum lineage, and license attribution.
Known active routes can be derived at ingestion; later experiments can re-download
archive products by ID rather than storing multi-terabyte full-disc history.

Initial scores should cover precipitation occurrence/intensity against RATE/DBZH,
convective forecast hits/misses against radar + LI, predicted vs observed top,
nearest-hazard distance, and onset/clearance timing. Keep forecast issue time and
lead time immutable to prevent hindsight leakage.

## Tests and acceptance criteria

- Unit-test geostationary projection round trips and prove parallax is applied
  before route distance.
- Test exact segment distance, stationing, nested/exclusive bands, boundaries at
  5/10/20 NM, and duplicate-free counts.
- Test fill values, failed retrieval, low-confidence cloud, stale/missing frames,
  partial coverage, atomic writes, idempotency, recovery, and 24-hour pruning.
- Commit small extracted netCDF/ODIM fixtures, not full-disc source files.
- Preserve a derived 2026-08-20 LFMN/EGLL regression fixture with tolerances around
  the values above; the test must fail if parallax is removed.
- Mock catalogue/API responses for CI and put live-provider smoke tests behind an
  explicit environment flag.
- Target cached route sampling under 2 seconds and zero network downloads in a
  briefing request. Expose ingest latency, last-success age, cache bytes, download
  failures, and route-query duration as operational metrics.

## Delivery sequence

1. Implement the OPERA MQTT/S3 collector and reproduce the measured access fixture.
2. Implement the shared EUMETSAT collector, cache, adapters, corridor library, and
   CLI output; reproduce the LFMN/EGLL fixture.
3. Add the D-0 sidecar/API/UI with explicit source time and observed-vs-forecast
   separation; add deterministic digest context only.
4. Add OPERA ingestion and radar/lightning/satellite association.
5. Add durable verification rows, rollups, scoring, and archive backfill.
6. Calibrate thresholds before allowing current conditions to affect advisories.

## Existing patterns and references

- `designs/future/satellite-cloud-top-validation.md` — CTTH projection, parallax,
  quality codes, and earlier route experiments; do not duplicate that decoder.
- `scripts/fetch_mtg_ctth_route.py`, `scripts/analyze_mtg_ctth_route.py`,
  `scripts/print_ctth_route_table.py`, `scripts/ctth_route_histogram.py` — reusable
  research code to promote into tested library components.
- `designs/metar-taf-route-weather.md` — D-0 pipeline and real-time refresh seam.
- `designs/metar-taf-accuracy.md` — verification ingestion and tiered retention.
- `designs/freshness-markers.md` and `designs/time-alignment-audit.md` — freshness
  and UTC conventions.
- EUMETNET ORD documentation: <https://eumetnet.github.io/openradardata-documentation/>
- ORD access guide: <https://eumetnet.github.io/openradardata-documentation/2-ORD-API-discovering-and-accessing-data/>
- MeteoGate access guide: <https://eumetnet.github.io/meteogate-documentation/2-discovering-and-accessing-data/>
- EUMETSAT Data Store: <https://data.eumetsat.int/>
