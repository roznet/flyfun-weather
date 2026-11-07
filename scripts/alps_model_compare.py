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
temp file, decoded and deleted; only point values are kept, with no pruning
(roughly 150k rows a day, some 10 MB: delete the file when the study ends).

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


ICON_MODELS = {
    "icon-ch1": IconModel("icon-ch1", 33),
    "icon-d2": IconModel("icon-d2", 48),
    "icon-eu": IconModel("icon-eu", 78),
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
CREATE TABLE IF NOT EXISTS grid_meta (model TEXT PRIMARY KEY, built_at TEXT NOT NULL);
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
    db = sqlite3.connect(out / "alps_compare.sqlite", timeout=60)
    db.execute("PRAGMA journal_mode=WAL")  # report can read while cron logs
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


def download(sess: requests.Session, url: str, dest: Path) -> int | str | None:
    """Stream ``url`` to ``dest``. Returns bytes, None on 404/410, "forbidden" on 403."""
    for attempt in range(3):
        try:
            with sess.get(url, stream=True, timeout=TIMEOUT) as r:
                if r.status_code == 403:
                    return "forbidden"
                if r.status_code in (404, 410):
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
    # Radians vs degrees: every grid here spans > 3 deg of latitude away from
    # the equator, so a max |lat| <= pi can only be radians.
    if np.nanmax(np.abs(lat)) <= math.pi + 1e-6:
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


_POSITIONS: dict[tuple, dict] = {}


def airport_positions(icaos) -> dict[str, tuple[float, float, float | None]]:
    """``{icao: (lat, lon, elevation_m)}`` from euro_aip (cached per process)."""
    key = tuple(icaos)
    if key not in _POSITIONS:
        _POSITIONS[key] = _airport_positions(icaos)
    return _POSITIONS[key]


def _airport_positions(icaos) -> dict[str, tuple[float, float, float | None]]:
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
            continue
        # GET-style pagination: follow the links until there is no ``next``.
        href = nxt["href"]
        for _ in range(max_pages):
            r2 = sess.get(href, timeout=TIMEOUT)
            r2.raise_for_status()
            obj = r2.json()
            feats.extend(obj.get("features", []))
            nxt = next((ln for ln in obj.get("links", []) if ln.get("rel") == "next"), None)
            if not nxt:
                break
            href = nxt["href"]
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
        feats = stac_search(sess, body, max_pages=2)
    except requests.HTTPError as exc:
        if exc.response is None or exc.response.status_code != 400:
            raise
        feats = None
    # On a 400, or when the server ignored the unknown top-level keys and
    # answered 200 with unfiltered items, use the STAC "query" extension form.
    if feats is None or any(
        ft.get("properties", {}).get("forecast:variable") not in (None, variable)
        for ft in feats
    ):
        if feats is not None:
            logger.info("STAC ignored top-level forecast:* filters; using `query`")
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


class ListingError(RuntimeError):
    """A DWD directory listing could not be read (try again next pass)."""


def dwd_prefix(sess, model: str, param: str) -> str:
    """Cached :func:`dwd_level_prefix` (one listing walk per process).

    A failed listing raises :class:`ListingError` and is not cached, so one
    transient error never pins a wrong URL.
    """
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
            raise ListingError(f"HTTP {r.status_code} listing {base + prefix}")
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
    if ok(raw.get("CEILING")) and raw["CEILING"] > 0:
        # <= 0 is not a real ceiling height in either datum; leave it unknown
        # rather than read it as a 0 ft (LIFR) ceiling. >= 9999 m is the
        # product's "no ceiling" fill (#847), handled in `report`.
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
    touched: set[int] = set()
    try:
        for var in ICON_VARS:
            final = ("ok", "absent", "bad", "forbidden") if give_up else ("ok", "absent")
            todo = [ld for ld in leads if done.get((ld, var)) not in final]
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
                try:
                    dwd_prefix(sess, model, var)
                except (ListingError, requests.RequestException) as exc:
                    logger.warning("%s %s: %s; skipped this pass", model, var, exc)
                    continue
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
                    path.unlink(missing_ok=True)
                    logger.warning("%s %s %s +%d: %s", model, iso(run), var, ld, exc)
                    continue
                if isinstance(nbytes, str):  # 403: expired pre-signed URL or a block
                    logger.warning("%s %s %s +%d: HTTP 403", model, iso(run), var, ld)
                    record_file(db, model, run, ld, var, "forbidden")
                    stats["missing"] += 1
                    continue
                if nbytes is None:
                    record_file(db, model, run, ld, var, "absent" if give_up else "missing")
                    stats["missing"] += 1
                    continue
                try:
                    msg = first_message(path)
                except Exception:  # noqa: BLE001 - one bad file must not lose the run
                    logger.exception("%s %s %s +%d: decode failed", model, iso(run), var, ld)
                    record_file(db, model, run, ld, var, "bad")
                    db.commit()
                    continue
                finally:
                    path.unlink(missing_ok=True)
                if msg is None:
                    record_file(db, model, run, ld, var, "bad")
                    continue
                _hdr, vals = msg
                if vals.size != n_expected:
                    logger.error(
                        "%s %s: %d values, grid has %d (grid changed? rerun `grid`)",
                        model, var, vals.size, n_expected,
                    )
                    # A grid change is permanent: stop this model until `grid` is rerun.
                    record_file(db, model, run, ld, var, "grid-mismatch")
                    db.commit()
                    raise RuntimeError(f"{model}: grid changed, rerun `grid`")
                # The raw value and the 'ok' record land in the same commit, so a
                # later failure can never leave a file marked done with no values.
                store_values(db, model, run, ld, {
                    icao: {f"raw_{var}": float(vals[cell])} for icao, (cell, _n) in cells.items()
                })
                record_file(db, model, run, ld, var, "ok", nbytes, time.monotonic() - t0)
                db.commit()
                touched.add(ld)
                stats["ok"] += 1
                stats["bytes"] += nbytes
    finally:
        # Runs even when a later file raised, so every lead already marked
        # 'ok' gets its derived values (the next pass would skip it).
        # Derived values (wind needs U and V of the same lead) are rebuilt from
        # everything stored for the lead, so a retry that fetches only V still
        # produces the wind.
        for ld in sorted(touched):
            store_values(db, model, run, ld, {
                icao: icon_point_values(_stored_raw(db, model, run, ld, icao)) for icao in cells
            })
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
        final = ("ok", "absent", "bad") if give_up else ("ok", "absent")
        if all(done.get((h, "SURFACE")) in final for h in hours):
            continue
        run_dir = Path(tempfile.mkdtemp(dir=tmp))
        try:
            merged: dict[int, list[dict]] = {h: [{} for _ in icaos] for h in hours}
            # Both packages must be published before either is downloaded, so a
            # late SP2 never costs a wasted SP1 download on every retry.
            try:
                codes = [
                    sess.head(arome_package_url(date_str, hour, group, AROME, pkg), timeout=20)
                    .status_code
                    for pkg in AROME_SURFACE_PACKAGES
                ]
            except requests.RequestException as exc:
                logger.warning("arome %s %s: availability probe failed: %s", iso(run), group, exc)
                continue
            if any(c not in (200, 404) for c in codes):
                # A server error is not "not published": skip, never give up on it.
                logger.warning("arome %s %s: probe HTTP %s; skipped", iso(run), group, codes)
                continue
            complete = all(c == 200 for c in codes)
            for pkg in AROME_SURFACE_PACKAGES if complete else ():
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
                if not any(per_icao.values()):
                    logger.error("arome %s +%d: decoder returned no values", iso(run), h)
                    record_file(db, "arome", run, h, "SURFACE", "bad")
                    continue
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
    ceil_run = None
    for run in cadence_runs(now, 3, 24, 2.5):
        try:
            hrefs = ch1_assets(sess, run, "T_2M")
        except Exception as exc:
            line("STAC search", False, repr(exc))
            break
        if hrefs:
            line("STAC search", True, f"run {iso(run)}: T_2M ctrl leads {min(hrefs)}..{max(hrefs)} ({len(hrefs)})")
            href = hrefs[min(hrefs, key=lambda h: abs(h - 1))]
            ceil_run = run
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
                got = isinstance(n, int)
                line("CSCS download", got, f"{n / 1e6:.1f} MB in {dt:.1f} s" if got else f"result={n}")
                if got:
                    import numpy as np

                    hdr, vals = first_message(p)
                    line("CH1 decode", vals.size > 0,
                         f"{vals.size} values, grid={hdr.get('gridType')} uuid={hdr.get('uuidOfHGrid')} "
                         f"short={hdr.get('shortName')} range={np.nanmin(vals):.1f}..{np.nanmax(vals):.1f}")
            except Exception as exc:
                line("CSCS download/decode", False, repr(exc))
    # CEILING encoding: what "no ceiling" looks like decides how `report` reads it.
    if href:
        try:
            ch = ch1_assets(sess, ceil_run, "CEILING")
            if ch:
                with tempfile.TemporaryDirectory() as td:
                    p = Path(td) / "ceiling.grib2"
                    download(sess, ch[min(ch)], p)
                    _ceiling_summary("CH1 CEILING", first_message(p), line)
            else:
                line("CH1 CEILING", False, "no CEILING control file listed")
        except Exception as exc:  # noqa: BLE001
            line("CH1 CEILING", False, repr(exc))
    try:
        hc = ch1_collection_asset(sess, "horizontal_constants")
        line("CH1 horizontal_constants asset", bool(hc), (hc or "")[:90])
    except Exception as exc:
        line("CH1 horizontal_constants asset", False, repr(exc))

    # 3. DWD native feed
    for model in ("icon-d2", "icon-eu"):
        found = None
        for run in cadence_runs(now, 3, 24, 2.0):
            try:
                url = dwd_url(model, "T_2M", run, 1, dwd_prefix(sess, model, "T_2M"))
                r = sess.head(url, timeout=20)
            except Exception as exc:
                line(f"DWD {model}", False, repr(exc))
                break
            if r.status_code == 200:
                found = (run, url)
                break
        if found:
            line(f"DWD {model}", True, f"latest run {iso(found[0])} {found[1]}")
            with tempfile.TemporaryDirectory() as td:
                p = Path(td) / "ceiling.grib2"
                url = dwd_url(model, "CEILING", found[0], 1, dwd_prefix(sess, model, "CEILING"))
                if isinstance(download(sess, url, p), int):
                    _ceiling_summary(f"DWD {model} CEILING", first_message(p), line)
                else:
                    line(f"DWD {model} CEILING", False, url)
        else:
            line(f"DWD {model}", False, "no T_2M +1h file in 24 h (URL layout?)")
    for model in ("icon-d2", "icon-eu"):
        for param in ("CLAT", "HSURF", "T_2M", "CEILING", "HBAS_SC"):
            try:
                pre = dwd_prefix(sess, model, param)
                line(f"DWD {model} {param} listing", True, f"prefix='{pre}'")
            except Exception as exc:  # noqa: BLE001
                line(f"DWD {model} {param} listing", False, repr(exc))

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


def _ceiling_summary(name, msg, line) -> None:
    """Print the CEILING value distribution: fill values and datum hints."""
    import numpy as np

    if msg is None:
        line(name, False, "no message")
        return
    hdr, vals = msg
    nonfinite = float(np.mean(~np.isfinite(vals))) if vals.size else 0.0
    v = vals[np.isfinite(vals)]
    if v.size == 0:
        line(name, False, "all missing")
        return
    q = np.percentile(v, [0, 1, 10, 50, 90, 99, 100])
    line(
        name, True,
        f"units={hdr.get('units')} level={hdr.get('typeOfLevel')} missing: {nonfinite:.1%}  "
        f"<=0: {np.mean(v <= 0):.1%}  >=9999: {np.mean(v >= 9999):.1%}  "
        f"pct0/1/10/50/90/99/100 = {' '.join(f'{x:.0f}' for x in q)}",
    )


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
                got = download(sess, href, p)
                if not isinstance(got, int):
                    logger.error("icon-ch1: horizontal_constants download failed (%s)", got)
                    continue
                clat, clon, hsurf, uuid = _grid_from_file(p)
            else:
                for run in cadence_runs(now, 3, 24, 2.0):
                    parts = {}
                    for param in ("CLAT", "CLON", "HSURF"):
                        try:
                            pre = dwd_prefix(sess, model, param)
                        except (ListingError, requests.RequestException) as exc:
                            logger.warning("%s %s: %s", model, param, exc)
                            continue
                        p = tmp / f"{model}-{param}.grib2"
                        if isinstance(download(sess, dwd_url(model, param, run, 0, pre), p), int):
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
            db.execute(
                "INSERT OR REPLACE INTO grid_meta VALUES (?, ?)",
                (model, iso(datetime.now(timezone.utc))),
            )
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


def _grid_stale(db, model: str) -> bool:
    """True when a grid-mismatch was recorded after the model's cells were built."""
    row = db.execute(
        "SELECT MAX(at) FROM files WHERE model=? AND status='grid-mismatch'", (model,),
    ).fetchone()
    built = db.execute("SELECT built_at FROM grid_meta WHERE model=?", (model,)).fetchone()
    return bool(row and row[0]) and (built is None or row[0] > built[0])


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
    if not os.environ.get("AIRPORTS_DB"):
        sys.exit("AIRPORTS_DB is not set (it comes from the repo .env); METARs need it")
    with lock(args.out):
        db = open_db(args.out)
        sess = session()
        now = datetime.now(timezone.utc)
        leads = list(range(args.min_lead, args.max_lead + 1))
        t_all = time.monotonic()
        failed = False
        with tempfile.TemporaryDirectory(dir=args.tmp) as td:
            tmp = Path(td)
            for model in args.models:
                if model != "arome" and _grid_stale(db, model):
                    logger.error("%s: grid changed since `grid` ran; rerun `grid`. Skipped.", model)
                    failed = True
                    continue
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
                        failed = True
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
            failed = True
        logger.info("log pass done in %.0f s%s", time.monotonic() - t_all, " (with errors)" if failed else "")
    return 1 if failed else 0


def cmd_status(args) -> int:
    db = open_db(args.out)
    print("cells:", dict(db.execute("SELECT model, COUNT(*) FROM cells GROUP BY model").fetchall()))
    print("\nruns (files ok / missing / absent / bad / forbidden / grid-mismatch, MB):")
    for row in db.execute(
        "SELECT model, run, SUM(status='ok'), SUM(status='missing'), SUM(status='absent'), "
        "SUM(status='bad'), SUM(status='forbidden'), SUM(status='grid-mismatch'), "
        "ROUND(SUM(COALESCE(bytes,0))/1e6) FROM files GROUP BY model, run ORDER BY run DESC, model LIMIT 40"
    ):
        print("  ", *row)
    print("\nmetars:", db.execute("SELECT COUNT(*), MIN(obs_time), MAX(obs_time) FROM metars").fetchone())
    return 0


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def _category(ceiling_ft, vis_m) -> str:
    """The product's flight category (``classify_flight_category``), so model and
    METAR are categorised exactly as in the verification tables. Missing ceiling
    or visibility counts as unrestricted on that axis, as there."""
    from weatherbrief.analysis.airport_conditions import classify_flight_category

    return classify_flight_category(ceiling_ft, visibility_m=vis_m).value


def _dz(model_m, airport_m) -> float | None:
    """Model cell height minus field elevation (m), or None if either is unknown."""
    if model_m is None or airport_m is None:
        return None
    return model_m - airport_m


def _score_ceiling(s, v, icao, o_ceil, o_vis, elev, datum) -> None:
    """Add the ceiling/category samples of one forecast hour to ``s``."""
    # Ceiling: only where the model has a CEILING value for this hour
    # (AROME has none). ICON CEILING is read as metres MSL, as the product
    # does (--ceiling-datum agl to test the other reading); >= 9999 m is
    # the "no ceiling" fill (#847).
    # A NaN cell (stored as NULL) or a non-positive value is an unknown
    # ceiling, not a clear sky: skip the sample rather than score it VFR.
    raw_c = v.get("raw_CEILING")
    if raw_c is None or raw_c <= 0:
        return
    raw = v.get("ceiling_raw_m")
    ap = elev.get(icao)
    if ap is None and datum == "msl":
        return  # no field elevation: an MSL ceiling can't be turned into AGL
    c_ft = None
    if raw is not None and raw < 9999.0:
        c_ft = max(0.0, (raw - (ap if datum == "msl" else 0.0)) * FT_PER_M)
    # Like for like: ceiling-only category on both sides for every model;
    # the full ceiling+visibility category only where the model has VIS
    # (DWD dropped it on the native feed, so CH1 only in practice).
    variants = [("ceil", _category(c_ft, None), _category(o_ceil, None))]
    if "vis_m" in v:
        variants.append(("full", _category(c_ft, v["vis_m"]), _category(o_ceil, o_vis)))
    for tag, m_cat, o_cat in variants:
        s.setdefault(f"{tag}_hit", []).append(1.0 if m_cat == o_cat else 0.0)
        o_low = o_cat in ("IFR", "LIFR")
        m_low = m_cat in ("IFR", "LIFR")
        if o_low:
            s.setdefault(f"{tag}_pod", []).append(1.0 if m_low else 0.0)
        if m_low:
            s.setdefault(f"{tag}_far", []).append(0.0 if o_low else 1.0)


def cmd_report(args) -> int:
    """Per model, lead bucket and airport: errors against the nearest METAR."""
    db = open_db(args.out)
    since = iso(datetime.now(timezone.utc) - timedelta(days=args.days))
    metars: dict[tuple[str, str], tuple] = {}
    for row in db.execute(
        "SELECT icao, obs_time, ceiling_ft, visibility_m, wind_kt, gust_kt, temp_c, dewpoint_c, wind_dir "
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
        "SELECT model, run, lead_h, valid, icao, var, value FROM forecasts WHERE valid >= ? "
        "AND (var NOT LIKE 'raw_%' OR var = 'raw_CEILING')",
        (since,),
    ):
        fc.setdefault((model, run, lead, valid, icao), {})[var] = val

    buckets = [(1, 6), (7, 12), (13, 24), (25, 48)]
    stats: dict[tuple, dict[str, list]] = {}
    for (model, _run, lead, valid, icao), v in fc.items():
        obs = metars.get((icao, valid))
        if obs is None:
            continue
        _, o_ceil, o_vis, o_wind, o_gust, o_t, o_td, o_wdir = obs
        b = next((f"{a}-{z}h" for a, z in buckets if a <= lead <= z), None)
        if b is None:
            continue
        s = stats.setdefault((model, b), {})
        if "t2m_c" in v and o_t is not None:
            s.setdefault("T", []).append(v["t2m_c"] - o_t)
            # Terrain: the nearest cell can sit hundreds of metres off the
            # field. Tcorr moves the model T to the field elevation at the
            # standard 6.5 K/km (ICON only: AROME has no stored cell height).
            dz = _dz(model_elev.get((model, icao)), elev.get(icao))
            if dz is not None:
                s.setdefault("Tcorr", []).append(v["t2m_c"] + 0.0065 * dz - o_t)
                s.setdefault("dz", []).append(dz)
        if "td2m_c" in v and o_td is not None:
            s.setdefault("Td", []).append(v["td2m_c"] - o_td)
        if "wspd_kt" in v and o_wind is not None:
            s.setdefault("wind", []).append(v["wspd_kt"] - o_wind)
        # Gust error only where the METAR reports a gust group. Where it does
        # not (gusts < 10 kt above the mean), count a "false gust" when the
        # model gust is >= 10 kt above the observed mean wind.
        if "gust_kt" in v and o_gust is not None:
            s.setdefault("gust", []).append(v["gust_kt"] - o_gust)
        elif "gust_kt" in v and o_wind is not None:
            s.setdefault("false_gust", []).append(1.0 if v["gust_kt"] - o_wind >= 10 else 0.0)
        # Direction error (circular), only when both winds are >= 5 kt.
        if (
            "wdir_deg" in v and o_wdir is not None and o_wind is not None
            and o_wind >= 5 and v.get("wspd_kt", 0) >= 5
        ):
            d = abs((v["wdir_deg"] - o_wdir + 180.0) % 360.0 - 180.0)
            s.setdefault("wdir", []).append(d)
        _score_ceiling(s, v, icao, o_ceil, o_vis, elev, args.ceiling_datum)

    def mae(xs):
        return sum(abs(x) for x in xs) / len(xs) if xs else float("nan")

    def bias(xs):
        return sum(xs) / len(xs) if xs else float("nan")

    mean = bias

    def ns(xs):
        return f"{mean(xs):4.2f}({len(xs)})" if xs else "   -   "

    print(f"Since {since}. MAE / bias against the METAR within 30 min of the valid hour (n per column).")
    print("ceil = category from ceiling alone, every model with a CEILING field;")
    print("full = ceiling + visibility, models with VIS only. hit = category hit rate,")
    print("POD/FAR = IFR-or-worse detection / false alarms, (n) = sample size.")
    print("gust = error where the METAR has a gust group; falsegust = share of no-gust METARs")
    print("where the model gust is >= 10 kt above the observed mean; dir mae in degrees, winds >= 5 kt.")
    print("ICON models: nearest native cell; AROME: bilinear on 0.025 deg (as in the product).")
    print("Tcorr = T moved to field elevation at 6.5 K/km (diagnostic only: wrong under valley")
    print("inversions); |dz| = mean |cell height - field| (m), ICON only.")
    print("Routine METARs only, as the verification tables score (SPECIs bias towards bad weather).\n")
    print(
        f"{'model':9s} {'lead':6s} {'T mae/bias (n)':>18s} {'Tcorr':>11s}  {'|dz|':>5s}  {'Td mae/bias':>12s} {'wind mae/bias':>13s}  {'gust mae/bias':>13s}  {'falsegust':>9s} {'dir mae':>9s}  "
        f"{'ceil hit':>9s} {'POD':>9s} {'FAR':>9s}  {'full hit':>9s} {'POD':>9s} {'FAR':>9s}"
    )
    for (model, b) in sorted(stats):
        s = stats[(model, b)]
        t = s.get("T", [])
        print(
            f"{model:9s} {b:6s} {mae(t):5.1f}/{bias(t):+5.1f} ({len(t):4d})  "
            f"{mae(s.get('Tcorr', [])):5.1f}/{bias(s.get('Tcorr', [])):+5.1f}  "
            f"{mae(s.get('dz', [])):5.0f}  "
            f"{mae(s.get('Td', [])):5.1f}/{bias(s.get('Td', [])):+5.1f}  "
            f"{mae(s.get('wind', [])):6.1f}/{bias(s.get('wind', [])):+5.1f}  "
            f"{mae(s.get('gust', [])):6.1f}/{bias(s.get('gust', [])):+5.1f}  "
            f"{ns(s.get('false_gust', [])):>9s} {mae(s.get('wdir', [])):5.0f}({len(s.get('wdir', []))})  "
            f"{ns(s.get('ceil_hit', [])):>9s} {ns(s.get('ceil_pod', [])):>9s} {ns(s.get('ceil_far', [])):>9s}  "
            f"{ns(s.get('full_hit', [])):>9s} {ns(s.get('full_pod', [])):>9s} {ns(s.get('full_far', [])):>9s}"
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
