"""Log ICON-CH1, AROME, ICON-D2 and ICON-EU surface forecasts at Alpine airports.

A throwaway verification logger, not product code: it answers "does MeteoSwiss
ICON-CH1 beat AROME (and ICON-D2) at Sion and the other Alpine fields?" before
anyone builds a CH1 pipeline. Nothing here is imported by the app.

What it records, per model run (00/06/12/18Z by default) and lead hour
(1..24 by default), at the nearest model cell to each airport:

- **icon-ch1**: MeteoSwiss ICON-CH1-EPS control run (1 km, native triangles),
  single-level files found through the STAC API at data.geo.admin.ch, downloaded
  from the pre-signed CSCS URLs it returns. Files only live 24 h.
- **icon-d2**, **icon-eu**: DWD's new native-grid feed
  ``opendata.dwd.de/weather/nwp/v1/m/<model>/`` (the lat-lon feed ends
  30 Nov 2026, issue #974). Same nearest-cell lookup as CH1.
- **arome**: Météo-France AROME 0.025° SP1/SP2 surface packages through the
  product's own downloader and decoder (``fetch/grib/arome_fetch.py``,
  ``decode_arome_surface_per_point``), bilinear as in the product. AROME's
  surface packages carry no ceiling or visibility.
- **METARs** for the same airports, through the product's verification fetch
  (``tasks/verification.fetch_observations_batch``), so ceiling and visibility
  are parsed exactly as the verification tables parse them.

ECMWF, GFS and ICON as the product sees them are already logged at these
airports by the forecast-map verification cycle (``airport_forecast_snapshots``);
``report`` does not read them yet.

Everything goes into one SQLite file (``--out``, default
``$DATA_DIR/alps_compare`` or ``~/alps_compare``). Downloads are streamed to a
temp file, decoded and deleted; only point values are kept.

Commands::

    python scripts/alps_model_compare.py check          # can this machine reach every source?
    python scripts/alps_model_compare.py grid           # once: nearest cells per model
    python scripts/alps_model_compare.py log            # every 30 min from cron; idempotent
    python scripts/alps_model_compare.py status
    python scripts/alps_model_compare.py report [--days 14]

``log`` walks the cadence runs of the last ``--lookback-h`` hours, fetches what
is published and not yet stored, and retries missing files on the next call, so
cron can call it as often as it likes (a lock file stops overlapping runs).
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import logging
import math
import os
import re
import sqlite3
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

import requests

logger = logging.getLogger("alps_compare")

# ---------------------------------------------------------------------------
# Airports
# ---------------------------------------------------------------------------

#: Alpine fields with METARs inside CH1's usable domain, plus two flat controls
#: (LSZH, LFSB). LOWK is east of AROME's domain: CH1 vs ICON only there.
AIRPORTS: tuple[str, ...] = (
    # Switzerland
    "LSGS", "LSGG", "LSZB", "LSZA", "LSZL", "LSZS", "LSZC", "LSZR", "LSGC",
    "LSZG", "LSMP", "LSME", "LSMM",
    # France
    "LFLS", "LFLB", "LFLP",
    # Austria
    "LOWI", "LOWS", "LOWK",
    # Italy
    "LIPB",
    # Flat controls
    "LSZH", "LFSB",
)

FT_PER_M = 3.28084
KT_PER_MS = 1.0 / 0.514444

# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------

STAC_API = "https://data.geo.admin.ch/api/stac/v1"
CH1_COLLECTION = "ch.meteoschweiz.ogd-forecasting-icon-ch1"
DWD_V1 = "https://opendata.dwd.de/weather/nwp/v1/m"
AWC_METAR = "https://aviationweather.gov/api/data/metar"

#: Single-level variables, by the parameter name both MeteoSwiss and DWD use.
#: Not every model has every one (DWD dropped VIS on the native feed); a
#: missing file is recorded as missing, never as an error.
ICON_VARS: tuple[str, ...] = (
    "CEILING", "VIS", "T_2M", "TD_2M", "U_10M", "V_10M", "VMAX_10M",
    "CLCT", "CLCL", "HBAS_SC",
)


@dataclass(frozen=True)
class IconModel:
    name: str
    max_lead_h: int
    cycle_h: int  # runs every N hours


ICON_MODELS = {
    "icon-ch1": IconModel("icon-ch1", 33, 3),
    "icon-d2": IconModel("icon-d2", 48, 3),
    "icon-eu": IconModel("icon-eu", 78, 3),
}
ALL_MODELS = ("icon-ch1", "icon-d2", "icon-eu", "arome")

USER_AGENT = "flyfun-weather-alps-compare/1"
TIMEOUT = (15, 120)

# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS cells (
    model TEXT NOT NULL, icao TEXT NOT NULL, grid_uuid TEXT, n_cells INTEGER,
    cell INTEGER NOT NULL, cell_lat REAL, cell_lon REAL, dist_km REAL,
    model_elev_m REAL, airport_lat REAL, airport_lon REAL, airport_elev_m REAL,
    PRIMARY KEY (model, icao)
);
CREATE TABLE IF NOT EXISTS forecasts (
    model TEXT NOT NULL, run TEXT NOT NULL, lead_h INTEGER NOT NULL,
    valid TEXT NOT NULL, icao TEXT NOT NULL, var TEXT NOT NULL, value REAL,
    PRIMARY KEY (model, run, lead_h, icao, var)
);
CREATE INDEX IF NOT EXISTS ix_fc_valid ON forecasts (valid, icao);
CREATE TABLE IF NOT EXISTS files (
    model TEXT NOT NULL, run TEXT NOT NULL, lead_h INTEGER NOT NULL,
    var TEXT NOT NULL, status TEXT NOT NULL, bytes INTEGER, seconds REAL,
    at TEXT NOT NULL,
    PRIMARY KEY (model, run, lead_h, var)
);
CREATE TABLE IF NOT EXISTS metars (
    icao TEXT NOT NULL, obs_time TEXT NOT NULL, report_type TEXT, raw TEXT,
    ceiling_ft INTEGER, lowest_base_ft INTEGER, visibility_m INTEGER,
    wind_dir INTEGER, wind_kt INTEGER, gust_kt INTEGER,
    temp_c INTEGER, dewpoint_c INTEGER, category TEXT,
    PRIMARY KEY (icao, obs_time)
);
"""


def default_out() -> Path:
    data = os.environ.get("DATA_DIR")
    return Path(data) / "alps_compare" if data else Path.home() / "alps_compare"


def open_db(out: Path) -> sqlite3.Connection:
    out.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(out / "alps_compare.sqlite")
    db.executescript(SCHEMA)
    return db


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


def parse_iso(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


def session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    return s


def download(sess: requests.Session, url: str, dest: Path) -> int | None:
    """Stream ``url`` to ``dest``. Returns bytes, or None on 404/403/410."""
    for attempt in range(3):
        try:
            with sess.get(url, stream=True, timeout=TIMEOUT) as r:
                if r.status_code in (403, 404, 410):
                    return None
                r.raise_for_status()
                n = 0
                with open(dest, "wb") as f:
                    for chunk in r.iter_content(1 << 20):
                        f.write(chunk)
                        n += len(chunk)
                return n
        except requests.RequestException as exc:
            if attempt == 2:
                raise
            logger.info("retry %s: %s", url.split("?")[0], exc)
            time.sleep(2 * (attempt + 1))
    return None


# ---------------------------------------------------------------------------
# GRIB helpers (eccodes, one message at a time)
# ---------------------------------------------------------------------------


def _eccodes():
    # pyproj before eccodes: see grib_reader.py (#911).
    with contextlib.suppress(ImportError):
        import pyproj  # noqa: F401
    import eccodes

    return eccodes


def grib_messages(path: Path):
    """Yield ``(header dict, values ndarray float64 with NaN for missing)``."""
    import numpy as np

    ec = _eccodes()
    with open(path, "rb") as f:
        while True:
            gid = ec.codes_grib_new_from_file(f)
            if gid is None:
                break
            try:
                hdr = {}
                for key in (
                    "shortName", "discipline", "parameterCategory", "parameterNumber",
                    "numberOfValues", "numberOfDataPoints", "uuidOfHGrid", "gridType",
                    "typeOfLevel", "level", "perturbationNumber", "units",
                    "dataDate", "dataTime", "stepRange",
                ):
                    try:
                        hdr[key] = ec.codes_get(gid, key, str)
                    except Exception:
                        hdr[key] = None
                ec.codes_set(gid, "missingValue", 9.999e20)
                vals = np.asarray(ec.codes_get_values(gid), dtype=np.float64)
                vals[vals == 9.999e20] = np.nan
                yield hdr, vals
            finally:
                ec.codes_release(gid)


def first_message(path: Path):
    """The control member's message (perturbationNumber 0 or absent)."""
    chosen = None
    for hdr, vals in grib_messages(path):
        pert = hdr.get("perturbationNumber")
        if pert in (None, "0", "MISSING", "not_found"):
            return hdr, vals
        if chosen is None:
            chosen = (hdr, vals)
    return chosen


def _param(hdr) -> tuple[str, str, str]:
    return (hdr.get("discipline"), hdr.get("parameterCategory"), hdr.get("parameterNumber"))


# CLAT / CLON / HSURF, matched by number so missing local tables don't matter.
_CLAT = {("0", "191", "1")}
_CLON = {("0", "191", "2")}
_HSURF = {("0", "3", "6")}


def nearest_cells(clat, clon, airports: dict[str, tuple[float, float]]):
    """Index of the nearest cell centre to each airport, and its distance (km)."""
    import numpy as np

    lat = np.asarray(clat, dtype=np.float64)
    lon = np.asarray(clon, dtype=np.float64)
    if np.nanmax(np.abs(lat)) <= math.pi + 1e-6:  # radians
        lat, lon = np.degrees(lat), np.degrees(lon)
    out = {}
    la, lo = np.radians(lat), np.radians(lon)
    for icao, (alat, alon) in airports.items():
        p, q = math.radians(alat), math.radians(alon)
        d = np.sin((la - p) / 2) ** 2 + np.cos(la) * math.cos(p) * np.sin((lo - q) / 2) ** 2
        i = int(np.nanargmin(d))
        km = 2 * 6371.0 * math.asin(math.sqrt(float(d[i])))
        out[icao] = (i, float(lat[i]), float(lon[i]), km)
    return out


# ---------------------------------------------------------------------------
# Airports from the euro_aip database (AIRPORTS_DB in .env)
# ---------------------------------------------------------------------------


def load_env() -> None:
    with contextlib.suppress(Exception):
        from weatherbrief.envfiles import load_env as _load

        _load()


def airport_positions(icaos) -> dict[str, tuple[float, float, float | None]]:
    """``{icao: (lat, lon, elevation_m)}`` from euro_aip."""
    from weatherbrief.airports import _load_airport_model

    db_path = os.environ.get("AIRPORTS_DB")
    if not db_path:
        sys.exit("AIRPORTS_DB is not set (it comes from the repo .env)")
    model = _load_airport_model(db_path)
    out = {}
    for icao in icaos:
        ap = model.find_airport_by_code(icao)
        if ap is None:
            logger.warning("%s not in euro_aip, skipped", icao)
            continue
        elev = ap.elevation_ft / FT_PER_M if ap.elevation_ft is not None else None
        out[icao] = (float(ap.latitude_deg), float(ap.longitude_deg), elev)
    return out


# ---------------------------------------------------------------------------
# MeteoSwiss STAC
# ---------------------------------------------------------------------------


def _iso_duration(hours: int) -> str:
    return f"P{hours // 24}DT{hours % 24:02d}H00M00S"


def _duration_hours(s: str) -> int | None:
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:[\d.]+S)?", s or "")
    if not m:
        return None
    return int(m.group(1) or 0) * 24 + int(m.group(2) or 0)


def stac_search(sess, body: dict, max_pages: int = 20) -> list[dict]:
    """POST /search, following ``next`` links. Returns all features."""
    url = f"{STAC_API}/search"
    feats: list[dict] = []
    payload = dict(body)
    for _ in range(max_pages):
        r = sess.post(url, json=payload, timeout=TIMEOUT)
        r.raise_for_status()
        obj = r.json()
        feats.extend(obj.get("features", []))
        nxt = next((ln for ln in obj.get("links", []) if ln.get("rel") == "next"), None)
        if not nxt:
            break
        if nxt.get("method", "GET").upper() == "POST" and nxt.get("body"):
            url = nxt.get("href", url)
            payload = nxt["body"]
        else:
            r2 = sess.get(nxt["href"], timeout=TIMEOUT)
            r2.raise_for_status()
            obj = r2.json()
            feats.extend(obj.get("features", []))
            break
    return feats


def ch1_assets(sess, run: datetime, variable: str) -> dict[int, str]:
    """``{lead_h: href}`` of the control-run files of ``variable`` for ``run``."""
    ref = run.strftime("%Y-%m-%dT%H:%M:%SZ")
    body = {
        "collections": [CH1_COLLECTION],
        "forecast:reference_datetime": ref,
        "forecast:variable": variable,
        "forecast:perturbed": False,
        "limit": 100,
    }
    try:
        feats = stac_search(sess, body)
    except requests.HTTPError as exc:
        if exc.response is None or exc.response.status_code != 400:
            raise
        # Fallback: the STAC "query" extension form of the same filter.
        feats = stac_search(sess, {
            "collections": [CH1_COLLECTION],
            "limit": 100,
            "query": {
                "forecast:reference_datetime": {"eq": ref},
                "forecast:variable": {"eq": variable},
                "forecast:perturbed": {"eq": False},
            },
        })
    out: dict[int, str] = {}
    for ft in feats:
        props = ft.get("properties", {})
        if props.get("forecast:perturbed") not in (False, "false", None):
            continue
        if props.get("forecast:variable") not in (None, variable):
            continue
        ref_p = props.get("forecast:reference_datetime")
        if ref_p and not ref_p.startswith(ref[:16]):
            continue
        lead = _duration_hours(props.get("forecast:horizon", ""))
        assets = ft.get("assets", {})
        href = next((a.get("href") for a in assets.values() if a.get("href")), None)
        if lead is not None and href:
            out[lead] = href
    return out


def ch1_collection_asset(sess, prefix: str) -> str | None:
    r = sess.get(f"{STAC_API}/collections/{CH1_COLLECTION}/assets", timeout=TIMEOUT)
    r.raise_for_status()
    for a in r.json().get("assets", []):
        if str(a.get("id", "")).startswith(prefix):
            return a.get("href")
    return None


# ---------------------------------------------------------------------------
# DWD native feed
# ---------------------------------------------------------------------------


def dwd_run_token(run: datetime) -> str:
    return quote(run.strftime("%Y-%m-%dT%H:%M"), safe="")


def dwd_step(lead_h: int) -> str:
    return f"PT{lead_h:03d}H00M"


def dwd_url(model: str, param: str, run: datetime, lead_h: int, prefix: str = "") -> str:
    return f"{DWD_V1}/{model}/p/{param}/{prefix}r/{dwd_run_token(run)}/s/{dwd_step(lead_h)}.grib2"


_HREF = re.compile(r'href="([^"?]+/)"')


_PREFIX_CACHE: dict[tuple[str, str], str] = {}


def dwd_prefix(sess, model: str, param: str) -> str:
    """Cached :func:`dwd_level_prefix` (one listing walk per process)."""
    key = (model, param)
    if key not in _PREFIX_CACHE:
        _PREFIX_CACHE[key] = dwd_level_prefix(sess, model, param)
    return _PREFIX_CACHE[key]


def dwd_level_prefix(sess, model: str, param: str) -> str:
    """Path between ``p/<PARAM>/`` and ``r/``: '' for plain single-level files,
    else ``lvt1/<type>/lv1/<level>/`` (first listed), found from the listings."""
    base = f"{DWD_V1}/{model}/p/{param}/"
    prefix = ""
    for _ in range(4):
        r = sess.get(base + prefix, timeout=TIMEOUT)
        if r.status_code != 200:
            return ""
        subs = [h for h in _HREF.findall(r.text) if not h.startswith(("/", "..", "http"))]
        if any(h.startswith("r/") or h == "r/" for h in subs):
            return prefix
        if not subs:
            return prefix
        prefix += subs[0]
    return prefix


# ---------------------------------------------------------------------------
# Value conversion (model native units -> comparison columns)
# ---------------------------------------------------------------------------


def icon_point_values(raw: dict[str, float]) -> dict[str, float]:
    """Native ICON names/units -> stored vars. Keeps raw metres for heights."""
    out: dict[str, float] = {}

    def ok(v):
        return v is not None and not (isinstance(v, float) and math.isnan(v))

    if ok(raw.get("T_2M")):
        out["t2m_c"] = raw["T_2M"] - 273.15
    if ok(raw.get("TD_2M")):
        out["td2m_c"] = raw["TD_2M"] - 273.15
    u, v = raw.get("U_10M"), raw.get("V_10M")
    if ok(u) and ok(v):
        out["wspd_kt"] = math.hypot(u, v) * KT_PER_MS
        out["wdir_deg"] = (math.degrees(math.atan2(-u, -v)) + 360.0) % 360.0
    if ok(raw.get("VMAX_10M")):
        out["gust_kt"] = raw["VMAX_10M"] * KT_PER_MS
    if ok(raw.get("VIS")):
        out["vis_m"] = raw["VIS"]
    if ok(raw.get("CEILING")):
        out["ceiling_raw_m"] = raw["CEILING"]
    if ok(raw.get("HBAS_SC")):
        out["hbas_sc_raw_m"] = raw["HBAS_SC"]
    if ok(raw.get("CLCT")):
        out["tcc_pct"] = raw["CLCT"]
    if ok(raw.get("CLCL")):
        out["lcc_pct"] = raw["CLCL"]
    return out


# ---------------------------------------------------------------------------
# Runs to fetch
# ---------------------------------------------------------------------------


def cadence_runs(now: datetime, every_h: int, lookback_h: int, delay_h: float) -> list[datetime]:
    """Runs at multiples of ``every_h`` from ``now - lookback_h`` up to ``now - delay_h``."""
    newest = now - timedelta(hours=delay_h)
    t = newest.replace(minute=0, second=0, microsecond=0)
    t -= timedelta(hours=t.hour % every_h)
    out = []
    while t >= now - timedelta(hours=lookback_h):
        out.append(t)
        t -= timedelta(hours=every_h)
    return out


def done_files(db, model: str, run: datetime) -> dict[tuple[int, str], str]:
    rows = db.execute(
        "SELECT lead_h, var, status FROM files WHERE model=? AND run=?", (model, iso(run)),
    ).fetchall()
    return {(lead, var): st for lead, var, st in rows}


def record_file(db, model, run, lead, var, status, nbytes=None, secs=None):
    db.execute(
        "INSERT OR REPLACE INTO files VALUES (?,?,?,?,?,?,?,?)",
        (model, iso(run), lead, var, status, nbytes, secs, iso(datetime.now(timezone.utc))),
    )


def store_values(db, model, run, lead, per_icao: dict[str, dict[str, float]]):
    valid = iso(run + timedelta(hours=lead))
    rows = [
        (model, iso(run), lead, valid, icao, var, float(v))
        for icao, vals in per_icao.items() for var, v in vals.items()
    ]
    db.executemany("INSERT OR REPLACE INTO forecasts VALUES (?,?,?,?,?,?,?)", rows)


def load_cells(db, model) -> dict[str, tuple[int, int]]:
    rows = db.execute("SELECT icao, cell, n_cells FROM cells WHERE model=?", (model,)).fetchall()
    return {icao: (cell, n) for icao, cell, n in rows}


# ---------------------------------------------------------------------------
# ICON (CH1 / D2 / EU) logging
# ---------------------------------------------------------------------------

# A file still missing this long after the run is given up on (recorded as
# 'missing' and not retried). Before that, 'missing' is retried every call.
GIVE_UP_AFTER_H = {"icon-ch1": 6, "icon-d2": 5, "icon-eu": 7, "arome": 12}


def log_icon_run(db, sess, model: str, run: datetime, leads: list[int], tmp: Path,
                 now: datetime) -> dict:
    cells = load_cells(db, model)
    if not cells:
        logger.warning("%s: no cells, run `grid` first", model)
        return {}
    n_expected = next(iter(cells.values()))[1]
    done = done_files(db, model, run)
    give_up = now - run > timedelta(hours=GIVE_UP_AFTER_H[model])
    stats = {"ok": 0, "missing": 0, "bytes": 0}

    # Per variable: CH1 needs one STAC search for all leads of a variable.
    raw_by_lead: dict[int, dict[str, dict[str, float]]] = {}
    for var in ICON_VARS:
        todo = [ld for ld in leads if done.get((ld, var)) not in ("ok", "absent")]
        if not todo:
            continue
        if model == "icon-ch1":
            try:
                hrefs = ch1_assets(sess, run, var)
            except requests.RequestException as exc:
                logger.warning("%s %s %s: STAC search failed: %s", model, iso(run), var, exc)
                continue
            if not hrefs and give_up:
                for ld in todo:
                    record_file(db, model, run, ld, var, "absent")
                continue
        else:
            hrefs = None
        for ld in todo:
            url = (
                hrefs.get(ld) if hrefs is not None
                else dwd_url(model, var, run, ld, dwd_prefix(sess, model, var))
            )
            if url is None:
                record_file(db, model, run, ld, var, "absent" if give_up else "missing")
                stats["missing"] += 1
                continue
            path = tmp / f"{model}-{var}-{ld}.grib2"
            t0 = time.monotonic()
            try:
                nbytes = download(sess, url, path)
            except requests.RequestException as exc:
                logger.warning("%s %s %s +%d: %s", model, iso(run), var, ld, exc)
                continue
            if nbytes is None:
                record_file(db, model, run, ld, var, "absent" if give_up else "missing")
                stats["missing"] += 1
                continue
            try:
                msg = first_message(path)
                if msg is None:
                    record_file(db, model, run, ld, var, "bad")
                    continue
                hdr, vals = msg
                if vals.size != n_expected:
                    logger.error(
                        "%s %s: %d values, grid has %d (grid changed? rerun `grid`)",
                        model, var, vals.size, n_expected,
                    )
                    record_file(db, model, run, ld, var, "grid-mismatch")
                    continue
                for icao, (cell, _n) in cells.items():
                    raw_by_lead.setdefault(ld, {}).setdefault(icao, {})[var] = float(vals[cell])
            finally:
                path.unlink(missing_ok=True)
            record_file(db, model, run, ld, var, "ok", nbytes, time.monotonic() - t0)
            stats["ok"] += 1
            stats["bytes"] += nbytes
        db.commit()

    # Wind needs U and V from the same lead; read back the raw parts stored so
    # far so a retry that fetches only V still produces the wind.
    for ld, per_icao in raw_by_lead.items():
        merged = {}
        for icao, raw in per_icao.items():
            prev = _stored_raw(db, model, run, ld, icao)
            prev.update(raw)
            merged[icao] = {**icon_point_values(prev), **{f"raw_{k}": v for k, v in prev.items()}}
        store_values(db, model, run, ld, merged)
    db.commit()
    return stats


def _stored_raw(db, model, run, lead, icao) -> dict[str, float]:
    rows = db.execute(
        "SELECT var, value FROM forecasts WHERE model=? AND run=? AND lead_h=? AND icao=? "
        "AND var LIKE 'raw_%'",
        (model, iso(run), lead, icao),
    ).fetchall()
    return {var[4:]: val for var, val in rows}


# ---------------------------------------------------------------------------
# AROME logging (product downloader + decoder)
# ---------------------------------------------------------------------------


def log_arome_run(db, sess, run: datetime, leads: list[int], tmp: Path, now: datetime) -> dict:
    from weatherbrief.fetch.grib.arome_fetch import (
        AROME,
        AROME_SURFACE_PACKAGES,
        arome_groups_for_fhours,
        arome_package_url,
        download_arome_group,
    )
    from weatherbrief.fetch.grib.decode import (
        build_arome_surface_snapshot,
        decode_arome_surface_per_point,
    )

    positions = {k: (v[0], v[1]) for k, v in airport_positions(AIRPORTS).items()}
    icaos = sorted(positions)
    lats = [positions[i][0] for i in icaos]
    lons = [positions[i][1] for i in icaos]
    date_str, hour = run.strftime("%Y%m%d"), run.hour
    done = done_files(db, "arome", run)
    give_up = now - run > timedelta(hours=GIVE_UP_AFTER_H["arome"])
    stats = {"ok": 0, "missing": 0, "bytes": 0}

    for group, hours in arome_groups_for_fhours(leads, AROME).items():
        if all(done.get((h, "SURFACE")) in ("ok", "absent") for h in hours):
            continue
        run_dir = Path(tempfile.mkdtemp(dir=tmp))
        try:
            merged: dict[int, list[dict]] = {h: [{} for _ in icaos] for h in hours}
            complete = True
            for pkg in AROME_SURFACE_PACKAGES:
                head = sess.head(arome_package_url(date_str, hour, group, AROME, pkg), timeout=20)
                if head.status_code != 200:
                    complete = False
                    break
                t0 = time.monotonic()
                path = download_arome_group(date_str, hour, group, run_dir, sess, AROME, pkg)
                if path is None:
                    complete = False
                    break
                stats["bytes"] += path.stat().st_size
                dec = decode_arome_surface_per_point(path, hours, lats, lons)
                for h, pts in dec["points"].items():
                    for i, raw in enumerate(pts):
                        merged[h][i].update(raw)
                logger.info("arome %s %s %s: %.0f s", iso(run), group, pkg, time.monotonic() - t0)
                path.unlink(missing_ok=True)
            if not complete:
                for h in hours:
                    record_file(db, "arome", run, h, "SURFACE", "absent" if give_up else "missing")
                stats["missing"] += len(hours)
                db.commit()
                continue
            for h in hours:
                per_icao = {}
                for i, icao in enumerate(icaos):
                    snap = build_arome_surface_snapshot(merged[h][i])
                    vals = {
                        "t2m_c": snap.get("temperature_2m_c"),
                        "td2m_c": snap.get("dewpoint_2m_c"),
                        "wspd_kt": snap.get("wind_speed_10m_kt"),
                        "wdir_deg": snap.get("wind_direction_10m_deg"),
                        "gust_kt": snap.get("wind_gusts_10m_kt"),
                        "tcc_pct": snap.get("cloud_cover_pct"),
                        "lcc_pct": snap.get("cloud_cover_low_pct"),
                    }
                    per_icao[icao] = {k: v for k, v in vals.items() if v is not None}
                store_values(db, "arome", run, h, per_icao)
                record_file(db, "arome", run, h, "SURFACE", "ok")
                stats["ok"] += 1
            db.commit()
        finally:
            for p in run_dir.glob("*"):
                p.unlink(missing_ok=True)
            with contextlib.suppress(OSError):
                run_dir.rmdir()
    return stats


# ---------------------------------------------------------------------------
# METARs
# ---------------------------------------------------------------------------


def log_metars(db, icaos) -> int:
    from weatherbrief.tasks.verification import fetch_observations_batch

    obs = fetch_observations_batch(list(icaos), os.environ["AIRPORTS_DB"])
    rows = [
        (
            o.icao, iso(o.observation_time), o.report_type, o.metar_raw,
            o.ceiling_ft, o.lowest_cloud_base_ft, o.visibility_m,
            o.wind_dir, o.wind_speed_kt, o.wind_gust_kt,
            o.temperature_c, o.dewpoint_c, o.flight_category,
        )
        for o in obs
    ]
    db.executemany("INSERT OR IGNORE INTO metars VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    db.commit()
    return len(rows)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_check(args) -> int:
    """Reachability and format probe. Prints what it sees; changes nothing."""
    sess = session()
    ok = True

    def line(name, good, detail=""):
        nonlocal ok
        ok &= bool(good)
        print(f"[{'OK ' if good else 'FAIL'}] {name}  {detail}")

    # 1. STAC collection + search
    try:
        r = sess.get(f"{STAC_API}/collections/{CH1_COLLECTION}", timeout=TIMEOUT)
        ext = r.json().get("extent", {}).get("temporal", {}).get("interval") if r.ok else None
        line("STAC collection", r.ok, f"HTTP {r.status_code} temporal={ext}")
    except Exception as exc:
        line("STAC collection", False, repr(exc))

    now = datetime.now(timezone.utc)
    href = None
    for run in cadence_runs(now, 3, 24, 2.5):
        try:
            hrefs = ch1_assets(sess, run, "T_2M")
        except Exception as exc:
            line("STAC search", False, repr(exc))
            break
        if hrefs:
            line("STAC search", True, f"run {iso(run)}: T_2M ctrl leads {min(hrefs)}..{max(hrefs)} ({len(hrefs)})")
            href = hrefs[min(hrefs, key=lambda h: abs(h - 1))]
            break
    else:
        line("STAC search", False, "no control T_2M file in the last 24 h (format? see --verbose)")
        if args.verbose:
            body = {"collections": [CH1_COLLECTION], "limit": 2}
            with contextlib.suppress(Exception):
                print(json.dumps(stac_search(sess, body, max_pages=1)[:2], indent=1)[:3000])

    # 2. CSCS download (pre-signed URL) + decode
    if href:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "t2m.grib2"
            try:
                t0 = time.monotonic()
                n = download(sess, href, p)
                dt = time.monotonic() - t0
                line("CSCS download", n is not None, f"{(n or 0) / 1e6:.1f} MB in {dt:.1f} s")
                if n:
                    hdr, vals = first_message(p)
                    line("CH1 decode", vals.size > 0,
                         f"{vals.size} values, grid={hdr.get('gridType')} uuid={hdr.get('uuidOfHGrid')} "
                         f"short={hdr.get('shortName')} range={float(min(vals)):.1f}..{float(max(vals)):.1f}")
            except Exception as exc:
                line("CSCS download/decode", False, repr(exc))
    try:
        hc = ch1_collection_asset(sess, "horizontal_constants")
        line("CH1 horizontal_constants asset", bool(hc), (hc or "")[:90])
    except Exception as exc:
        line("CH1 horizontal_constants asset", False, repr(exc))

    # 3. DWD native feed
    for model in ("icon-d2", "icon-eu"):
        found = None
        for run in cadence_runs(now, 3, 24, 2.0):
            url = dwd_url(model, "T_2M", run, 1, dwd_prefix(sess, model, "T_2M"))
            try:
                r = sess.head(url, timeout=20)
            except Exception as exc:
                line(f"DWD {model}", False, repr(exc))
                break
            if r.status_code == 200:
                found = (run, url)
                break
        if found:
            line(f"DWD {model}", True, f"latest run {iso(found[0])} {found[1]}")
        else:
            line(f"DWD {model}", False, "no T_2M +1h file in 24 h (URL layout?)")
    for model in ("icon-d2", "icon-eu"):
        for param in ("CLAT", "HSURF", "T_2M", "CEILING", "HBAS_SC"):
            pre = dwd_prefix(sess, model, param)
            line(f"DWD {model} {param} listing", True, f"prefix='{pre}'")

    # 4. AROME mirror
    try:
        from weatherbrief.fetch.grib.arome_fetch import AROME, arome_package_url

        good = None
        for run in cadence_runs(now, 3, 30, 3.0):
            url = arome_package_url(run.strftime("%Y%m%d"), run.hour, "00H06H", AROME, "SP1")
            if sess.head(url, timeout=20).status_code == 200:
                good = (run, url)
                break
        line("AROME mirror", bool(good), f"latest {iso(good[0])}" if good else "no SP1 00H06H in 30 h")
    except Exception as exc:
        line("AROME mirror", False, repr(exc))

    # 5. METARs
    try:
        r = sess.get(AWC_METAR, params={"ids": "LSGS,LSZH", "format": "raw"}, timeout=20)
        line("aviationweather.gov", r.ok and "LSGS" in r.text, r.text.strip().splitlines()[0][:80] if r.ok else f"HTTP {r.status_code}")
    except Exception as exc:
        line("aviationweather.gov", False, repr(exc))

    # 6. eccodes
    try:
        ec = _eccodes()
        line("eccodes", True, ec.codes_get_api_version())
    except Exception as exc:
        line("eccodes", False, repr(exc))

    print("\nall reachable" if ok else "\nsome checks failed: paste this output in the thread")
    return 0 if ok else 1


def _grid_from_file(path: Path):
    """Return (clat, clon, hsurf, uuid, n) from a file holding CLAT/CLON/HSURF."""
    clat = clon = hsurf = None
    uuid = None
    for hdr, vals in grib_messages(path):
        key = _param(hdr)
        short = (hdr.get("shortName") or "").lower()
        if key in _CLAT or short in ("clat", "tlat"):
            if clat is None:
                clat, uuid = vals, hdr.get("uuidOfHGrid")
        elif key in _CLON or short in ("clon", "tlon"):
            clon = clon if clon is not None else vals
        elif key in _HSURF or short in ("hsurf", "h"):
            hsurf = hsurf if hsurf is not None else vals
    return clat, clon, hsurf, uuid


def cmd_grid(args) -> int:
    """Nearest cell per airport for each ICON model (one-off; rerun if a grid changes)."""
    load_env()
    sess = session()
    db = open_db(args.out)
    pos = airport_positions(AIRPORTS)
    latlon = {k: (v[0], v[1]) for k, v in pos.items()}
    now = datetime.now(timezone.utc)

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        for model in args.models:
            if model == "arome":
                continue
            clat = clon = hsurf = None
            uuid = None
            if model == "icon-ch1":
                href = ch1_collection_asset(sess, "horizontal_constants")
                if not href:
                    logger.error("icon-ch1: no horizontal_constants asset")
                    continue
                p = tmp / "hc.grib2"
                download(sess, href, p)
                clat, clon, hsurf, uuid = _grid_from_file(p)
            else:
                for run in cadence_runs(now, 3, 24, 2.0):
                    parts = {}
                    for param in ("CLAT", "CLON", "HSURF"):
                        pre = dwd_prefix(sess, model, param)
                        p = tmp / f"{model}-{param}.grib2"
                        if download(sess, dwd_url(model, param, run, 0, pre), p):
                            parts[param] = first_message(p)
                    if "CLAT" in parts and "CLON" in parts:
                        clat, uuid = parts["CLAT"][1], parts["CLAT"][0].get("uuidOfHGrid")
                        clon = parts["CLON"][1]
                        hsurf = parts["HSURF"][1] if "HSURF" in parts else None
                        break
            if clat is None or clon is None:
                logger.error("%s: CLAT/CLON not found", model)
                continue
            n = int(clat.size)
            near = nearest_cells(clat, clon, latlon)
            db.execute("DELETE FROM cells WHERE model=?", (model,))
            for icao, (cell, la, lo, km) in near.items():
                elev = float(hsurf[cell]) if hsurf is not None and hsurf.size == n else None
                db.execute(
                    "INSERT INTO cells VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (model, icao, uuid, n, cell, la, lo, km, elev, pos[icao][0], pos[icao][1], pos[icao][2]),
                )
                print(f"{model:8s} {icao}  cell {cell:8d}  {km:5.2f} km  model {elev if elev is None else round(elev)} m"
                      f"  airport {pos[icao][2] if pos[icao][2] is None else round(pos[icao][2])} m")
            db.commit()
            # D2's native grid has a bitmap: a cell far from the airport means the
            # airport is outside the domain.
            far = [i for i, v in near.items() if v[3] > 5.0]
            if far:
                logger.warning("%s: nearest cell > 5 km for %s (outside domain?)", model, ", ".join(far))
    return 0


@contextlib.contextmanager
def lock(out: Path):
    out.mkdir(parents=True, exist_ok=True)
    fh = open(out / ".lock", "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        logger.info("another log run holds the lock; exiting")
        sys.exit(0)
    try:
        yield
    finally:
        fcntl.flock(fh, fcntl.LOCK_UN)
        fh.close()


def cmd_log(args) -> int:
    load_env()
    with lock(args.out):
        db = open_db(args.out)
        sess = session()
        now = datetime.now(timezone.utc)
        leads = list(range(args.min_lead, args.max_lead + 1))
        t_all = time.monotonic()
        with tempfile.TemporaryDirectory(dir=args.tmp) as td:
            tmp = Path(td)
            for model in args.models:
                delay = {"icon-ch1": 1.5, "icon-d2": 1.0, "icon-eu": 2.5, "arome": 3.0}[model]
                for run in cadence_runs(now, args.every, args.lookback_h, delay):
                    t0 = time.monotonic()
                    try:
                        if model == "arome":
                            st = log_arome_run(db, sess, run, leads, tmp, now)
                        else:
                            mleads = [ld for ld in leads if ld <= ICON_MODELS[model].max_lead_h]
                            st = log_icon_run(db, sess, model, run, mleads, tmp, now)
                    except Exception:
                        logger.exception("%s %s failed", model, iso(run))
                        continue
                    if st and (st.get("ok") or st.get("missing")):
                        logger.info(
                            "%s %s: %d ok, %d missing, %.0f MB, %.0f s",
                            model, iso(run), st["ok"], st["missing"], st["bytes"] / 1e6,
                            time.monotonic() - t0,
                        )
        try:
            n = log_metars(db, AIRPORTS)
            logger.info("metars: %d reports seen", n)
        except Exception:
            logger.exception("metar fetch failed")
        logger.info("log pass done in %.0f s", time.monotonic() - t_all)
    return 0


def cmd_status(args) -> int:
    db = open_db(args.out)
    print("cells:", dict(db.execute("SELECT model, COUNT(*) FROM cells GROUP BY model").fetchall()))
    print("\nruns (files ok / missing / absent, MB):")
    for row in db.execute(
        "SELECT model, run, SUM(status='ok'), SUM(status='missing'), SUM(status='absent'), "
        "ROUND(SUM(COALESCE(bytes,0))/1e6) FROM files GROUP BY model, run ORDER BY run DESC, model LIMIT 40"
    ):
        print("  ", *row)
    print("\nmetars:", db.execute("SELECT COUNT(*), MIN(obs_time), MAX(obs_time) FROM metars").fetchone())
    return 0


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def _category(ceiling_ft, vis_m) -> str | None:
    """VFR/MVFR/IFR/LIFR from ceiling (ft AGL, None = none) and visibility (m).

    Metric visibility >= 8000 m is VFR (meteorology-decisions §48). Returns None
    when both are unknown.
    """
    if ceiling_ft is None and vis_m is None:
        return None
    c = ceiling_ft if ceiling_ft is not None else 1e9
    v = vis_m if vis_m is not None else 1e9
    if c < 500 or v < 1600:
        return "LIFR"
    if c < 1000 or v < 5000:
        return "IFR"
    if c <= 3000 or v < 8000:
        return "MVFR"
    return "VFR"


def cmd_report(args) -> int:
    """Per model, lead bucket and airport: errors against the nearest METAR."""
    db = open_db(args.out)
    since = iso(datetime.now(timezone.utc) - timedelta(days=args.days))
    metars: dict[tuple[str, str], tuple] = {}
    for row in db.execute(
        "SELECT icao, obs_time, ceiling_ft, visibility_m, wind_kt, gust_kt, temp_c, dewpoint_c "
        "FROM metars WHERE obs_time >= ? AND (report_type IS NULL OR report_type='METAR')",
        (since,),
    ):
        t = parse_iso(row[1])
        # Routine METARs at HH:20/HH:50: take the one within 30 min of the hour.
        hour = (t + timedelta(minutes=30)).replace(minute=0)
        key = (row[0], iso(hour))
        if key not in metars or abs((t - hour).total_seconds()) < metars[key][0]:
            metars[key] = (abs((t - hour).total_seconds()), *row[2:])

    elev = {icao: e for icao, e in db.execute(
        "SELECT DISTINCT icao, airport_elev_m FROM cells")}
    model_elev = {(m, i): e for m, i, e in db.execute("SELECT model, icao, model_elev_m FROM cells")}

    fc: dict[tuple, dict[str, float]] = {}
    for model, run, lead, valid, icao, var, val in db.execute(
        "SELECT model, run, lead_h, valid, icao, var, value FROM forecasts WHERE valid >= ?",
        (since,),
    ):
        fc.setdefault((model, run, lead, valid, icao), {})[var] = val

    buckets = [(1, 6), (7, 12), (13, 24), (25, 48)]
    stats: dict[tuple, dict[str, list]] = {}
    for (model, _run, lead, valid, icao), v in fc.items():
        obs = metars.get((icao, valid))
        if obs is None:
            continue
        _, o_ceil, o_vis, o_wind, o_gust, o_t, o_td = obs
        b = next((f"{a}-{z}h" for a, z in buckets if a <= lead <= z), None)
        if b is None:
            continue
        s = stats.setdefault((model, b), {})
        if "t2m_c" in v and o_t is not None:
            s.setdefault("T", []).append(v["t2m_c"] - o_t)
        if "td2m_c" in v and o_td is not None:
            s.setdefault("Td", []).append(v["td2m_c"] - o_td)
        if "wspd_kt" in v and o_wind is not None:
            s.setdefault("wind", []).append(v["wspd_kt"] - o_wind)
        # Ceiling: ICON CEILING treated as metres MSL (as the product does);
        # AGL = raw - airport elevation. No ceiling field => "none" above 9999 m.
        if "ceiling_raw_m" in v or "vis_m" in v:
            raw = v.get("ceiling_raw_m")
            ap = elev.get(icao) or model_elev.get((model, icao)) or 0.0
            c_ft = None
            if raw is not None and raw < 9999.0:
                c_ft = max(0.0, (raw - (ap if args.ceiling_datum == "msl" else 0.0)) * FT_PER_M)
            m_cat = _category(c_ft, v.get("vis_m"))
            o_cat = _category(o_ceil, o_vis)
            if m_cat and o_cat:
                s.setdefault("cat_hit", []).append(1.0 if m_cat == o_cat else 0.0)
                o_low = o_cat in ("IFR", "LIFR")
                m_low = m_cat in ("IFR", "LIFR")
                s.setdefault("ifr_obs", []).append(1.0 if o_low else 0.0)
                if o_low:
                    s.setdefault("ifr_pod", []).append(1.0 if m_low else 0.0)
                if m_low:
                    s.setdefault("ifr_far", []).append(0.0 if o_low else 1.0)

    def mae(xs):
        return sum(abs(x) for x in xs) / len(xs) if xs else float("nan")

    def bias(xs):
        return sum(xs) / len(xs) if xs else float("nan")

    def mean(xs):
        return sum(xs) / len(xs) if xs else float("nan")

    print(f"Since {since}. MAE / bias against the METAR within 30 min of the valid hour.")
    print("cat = flight-category hit rate (ceiling+vis models only); IFR POD/FAR = IFR-or-worse detection.\n")
    hdr = f"{'model':9s} {'lead':6s} {'n':>5s}  {'T mae/bias':>12s}  {'Td mae/bias':>12s}  {'wind mae/bias':>13s}  {'cat':>5s} {'IFR obs':>7s} {'POD':>5s} {'FAR':>5s}"
    print(hdr)
    for (model, b) in sorted(stats):
        s = stats[(model, b)]
        n = len(s.get("T", []))
        print(
            f"{model:9s} {b:6s} {n:5d}  "
            f"{mae(s.get('T', [])):5.1f}/{bias(s.get('T', [])):+5.1f}  "
            f"{mae(s.get('Td', [])):5.1f}/{bias(s.get('Td', [])):+5.1f}  "
            f"{mae(s.get('wind', [])):6.1f}/{bias(s.get('wind', [])):+5.1f}  "
            f"{mean(s.get('cat_hit', [])):5.2f} {len([x for x in s.get('ifr_obs', []) if x]):7d} "
            f"{mean(s.get('ifr_pod', [])):5.2f} {mean(s.get('ifr_far', [])):5.2f}"
        )
    return 0


# ---------------------------------------------------------------------------


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", type=Path, default=None, help="data dir (default $DATA_DIR/alps_compare)")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check")
    g = sub.add_parser("grid")
    g.add_argument("--models", nargs="+", default=["icon-ch1", "icon-d2", "icon-eu"])
    lg = sub.add_parser("log")
    lg.add_argument("--models", nargs="+", default=list(ALL_MODELS), choices=ALL_MODELS)
    lg.add_argument("--every", type=int, default=6, help="log runs at multiples of N hours UTC")
    lg.add_argument("--min-lead", type=int, default=1)
    lg.add_argument("--max-lead", type=int, default=24)
    lg.add_argument("--lookback-h", type=int, default=20, help="CH1 files live 24 h")
    lg.add_argument("--tmp", default=None, help="temp dir for downloads")
    sub.add_parser("status")
    rp = sub.add_parser("report")
    rp.add_argument("--days", type=int, default=14)
    rp.add_argument("--ceiling-datum", choices=("msl", "agl"), default="msl")
    args = ap.parse_args(argv)
    if args.out is None:
        load_env()
        args.out = default_out()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    return {
        "check": cmd_check, "grid": cmd_grid, "log": cmd_log,
        "status": cmd_status, "report": cmd_report,
    }[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
