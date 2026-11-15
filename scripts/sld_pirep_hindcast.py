"""Do our SLD signals light up where US pilots report icing aloft? (#1000, #411)

A study script, not product code: nothing imports it. It scores the SLD
signals of the #411 plan **at altitude** against US pilot reports, which
surface METARs cannot do:

- **PIREPs** from the Iowa Environmental Mesonet archive (decoded ICING field,
  flight level, position). Reports with an icing group (NEG included) are kept,
  inside CONUS and at or below ``MAX_ALT_FT``.
- **GFS 0.25°** from NOAA's AWS archive: rain and snow water (RWMR / SNMR) on
  pressure levels, plus the categorical freezing-rain / ice-pellet flags. GFS
  is the same global model we run in Europe and the only one with rain water
  on levels (our ECMWF feed has none).
- **ECMWF open data** from its AWS archive: ``ptype`` + ``tprate`` and the
  13-level profile (t / r / q / gh).

Model columns are sampled at each report's grid cell and its 3×3 neighbourhood
and stored (compressed) in SQLite; the GRIB itself is discarded. Storing the
columns rather than the verdicts means ``report`` can be re-run against
whatever the product code is at the time (e.g. after #411 changes the
warm-nose rule) without downloading again.

Commands::

    pireps   download + parse PIREPs for a date range (idempotent per day)
    fetch    download model fields for every 3-hourly valid time that has
             reports, sample columns (idempotent per model × valid time)
    report   flag rate per signal × truth class (+ chance level)
    status   what is stored, and download throughput so far

Everything lives in one SQLite file, ``--out``/sld_pirep_hindcast.sqlite
(default ``$DATA_DIR/sld_pirep_hindcast``). Nothing is pruned; delete the
directory when the study ends.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import logging
import math
import os
import re
import sqlite3
import sys
import tempfile
import time
import warnings
import zlib
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import requests

logger = logging.getLogger("sld_hindcast")

IEM_PIREPS = "https://mesonet.agron.iastate.edu/cgi-bin/request/gis/pireps.py"
GFS_BASE = "https://noaa-gfs-bdp-pds.s3.amazonaws.com"
ECMWF_BASE = "https://ecmwf-forecasts.s3.eu-central-1.amazonaws.com"

#: CONUS box (lat_min, lat_max, lon_min, lon_max). Alaska and Hawaii are left
#: out: different climates, and Alaska is most of the Canadian-border noise.
CONUS = (24.0, 50.0, -125.0, -66.0)
MAX_ALT_FT = 25000
#: Stored report text is capped: a real PIREP is a few hundred characters.
MAX_REPORT_CHARS = 2000
#: A report is matched to the nearest 3-hourly model valid time.
VALID_STEP_H = 3
MODELS = ("gfs", "ecmwf")
#: Lowest pressure level kept (hPa): nothing above ~FL300 matters for icing.
MIN_LEVEL_HPA = 300

# ── Signal thresholds (the #411 plan's values) ──────────────────────────
#: Supercooled rain: the product's SUPERCOOLED_RAIN_MIN_QR_KG_KG.
QR_MIN_KG_KG = 1e-6
#: Liquid-dominated precipitation, to drop wet-snow false alarms.
LIQUID_FRACTION_MIN = 0.5
#: ECMWF ptype codes: 3 freezing rain, 8 ice pellets, 12 freezing drizzle.
PTYPE_FREEZING = (3, 8, 12)
PTYPE_GATE_MM_H = 0.01
#: "At the report's altitude" = model levels within this many feet.
ALT_WINDOW_FT = 1000.0
#: Report filter: model temperature at the report's altitude in this range.
#: Icing (and SLD) needs sub-zero air; NEG reports in warm air would make
#: every signal look discriminating for free.
SUBZERO_RANGE_C = (-20.0, 0.0)

FT_PER_M = 3.28084
T0_K = 273.15


# ═══════════════════════════════════════════════════════════════════════
# Database
# ═══════════════════════════════════════════════════════════════════════

SCHEMA = """
CREATE TABLE IF NOT EXISTS pireps (
    id INTEGER PRIMARY KEY,
    valid TEXT NOT NULL,          -- ISO UTC, minute precision
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    alt_ft INTEGER NOT NULL,
    aircraft TEXT,
    icing TEXT,
    report TEXT,
    truth TEXT NOT NULL,
    UNIQUE (valid, lat, lon, alt_ft, aircraft)
);
CREATE TABLE IF NOT EXISTS pirep_days (
    day TEXT PRIMARY KEY,
    n_rows INTEGER,
    n_kept INTEGER,
    fetched_at TEXT
);
CREATE TABLE IF NOT EXISTS columns (
    model TEXT NOT NULL,
    valid TEXT NOT NULL,
    iy INTEGER NOT NULL,
    ix INTEGER NOT NULL,
    lat REAL,
    lon REAL,
    payload BLOB NOT NULL,        -- zlib(json): {"levels": {...}, "sfc": {...}}
    PRIMARY KEY (model, valid, iy, ix)
);
CREATE TABLE IF NOT EXISTS fetches (
    model TEXT NOT NULL,
    valid TEXT NOT NULL,
    run TEXT,
    lead_h INTEGER,
    status TEXT NOT NULL,         -- ok | missing | error
    n_bytes INTEGER,
    seconds REAL,
    n_columns INTEGER,
    grid TEXT,                    -- json grid geometry
    error TEXT,
    fetched_at TEXT,
    PRIMARY KEY (model, valid)
);
"""


def open_db(out: Path) -> sqlite3.Connection:
    out.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(out / "sld_pirep_hindcast.sqlite")
    conn.executescript(SCHEMA)
    return conn


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%MZ")


def _parse_iso(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%MZ").replace(tzinfo=UTC)


# ═══════════════════════════════════════════════════════════════════════
# PIREPs
# ═══════════════════════════════════════════════════════════════════════

#: Ordered strongest → weakest SLD evidence. ``report`` prints them in this order.
TRUTH_CLASSES = (
    "fz_remark",        # remarks mention FZRA / FZDZ / SLD / large drops
    "clr_mxd_mod",      # clear or mixed icing, moderate or worse: the SLD proxy
    "clr_mxd_lgt",      # clear or mixed, trace / light
    "rime_mod",         # rime, moderate or worse
    "rime_lgt",         # rime, trace / light
    "icing_untyped",    # icing reported without a type
    "neg",              # no icing reported
)

_FZ_REMARKS = ("FZRA", "FZDZ", "FZ RA", "FZ DZ", " SLD", "/SLD", "LARGE DROP",
               "FREEZING RAIN", "FREEZING DRIZZLE", "FRZ RA", "FRZ DZ", "FRZG RA", "FRZG DZ")

# Words pilots actually use in the /IC group (checked on Jan 2025 reports).
_NEG_WORDS = frozenset({"NEG", "NIL"})
_SEVERE_WORDS = frozenset({"MOD", "MDT", "SEV", "SEVR", "SVR", "HVY", "EXTRM"})
_LIGHT_WORDS = frozenset({"TRACE", "TRC", "TR", "LGT", "LT", "LIGHT", "LGHT"})
_CLEAR_MIXED_WORDS = frozenset({"CLR", "CLEAR", "MX", "MXD", "MIX", "MIXED"})
_RIME_WORDS = frozenset({"RIME", "RME", "RM", "RIM"})


def classify_truth(icing: str | None, report: str | None) -> str | None:
    """Truth class of one PIREP, or None when it carries no icing information.

    Word-based: an icing field with no intensity, type or NEG word (e.g. the
    stray ``ZTLFD-41`` facility codes) is not an icing report.
    """
    ic = (icing or "").strip().upper()
    words = set(re.findall(r"[A-Z]+", ic)) if ic not in ("", "NONE") else set()
    rpt = (report or "").upper()
    negative = bool(words & _NEG_WORDS)
    is_icing = bool(words & (_SEVERE_WORDS | _LIGHT_WORDS | _CLEAR_MIXED_WORDS | _RIME_WORDS))
    # Remarks can only strengthen an icing report: "FZRA" in a NEG report or a
    # report with no icing group is weather seen, not ice on the airframe.
    if is_icing and not negative and any(k in rpt for k in _FZ_REMARKS):
        return "fz_remark"
    if negative:
        return "neg"
    if not is_icing:
        return None
    severe = bool(words & _SEVERE_WORDS)
    if words & _CLEAR_MIXED_WORDS:
        return "clr_mxd_mod" if severe else "clr_mxd_lgt"
    if words & _RIME_WORDS:
        return "rime_mod" if severe else "rime_lgt"
    return "icing_untyped"


@dataclass
class Pirep:
    valid: datetime
    lat: float
    lon: float
    alt_ft: int
    aircraft: str
    icing: str | None
    report: str
    truth: str


def parse_pirep_csv(text: str) -> tuple[int, list[Pirep]]:
    """Parse one IEM CSV download. Returns (rows read, reports kept)."""
    rows = 0
    kept: dict[tuple, Pirep] = {}
    # IEM occasionally carries a garbled report hundreds of KB long (2025-01-14);
    # the default 128 KB field limit would abort the whole day.
    csv.field_size_limit(min(sys.maxsize, 2**31 - 1))
    for row in csv.DictReader(io.StringIO(text)):
        rows += 1
        try:
            valid = datetime.strptime(row["VALID"], "%Y%m%d%H%M").replace(tzinfo=UTC)
            lat, lon = float(row["LAT"]), float(row["LON"])
            alt = int(float(row["FL"]))
        except (KeyError, TypeError, ValueError):
            continue
        if not (CONUS[0] <= lat <= CONUS[1] and CONUS[2] <= lon <= CONUS[3]):
            continue
        if alt <= 0 or alt > MAX_ALT_FT:
            continue
        icing = row.get("ICING")
        truth = classify_truth(icing, row.get("REPORT"))
        if truth is None:
            continue
        aircraft = (row.get("AIRCRAFT") or "").strip()
        # The same report reaches IEM through several products (UBUS01, PRCUS…).
        key = (valid, round(lat, 2), round(lon, 2), alt, aircraft)
        if key in kept:
            continue
        kept[key] = Pirep(valid, lat, lon, alt, aircraft,
                          None if icing in (None, "None") else icing,
                          (row.get("REPORT") or "")[:MAX_REPORT_CHARS], truth)
    return rows, list(kept.values())


def cmd_pireps(conn: sqlite3.Connection, start: date, end: date, force: bool) -> None:
    session = requests.Session()
    day = start
    while day <= end:
        done = conn.execute("SELECT 1 FROM pirep_days WHERE day=?", (day.isoformat(),)).fetchone()
        if done and not force:
            day += timedelta(days=1)
            continue
        sts = datetime.combine(day, datetime.min.time(), UTC)
        params = {"sts": sts.strftime("%Y-%m-%dT%H:%MZ"),
                  "ets": (sts + timedelta(days=1)).strftime("%Y-%m-%dT%H:%MZ"),
                  "fmt": "csv"}
        resp = session.get(IEM_PIREPS, params=params, timeout=120)
        resp.raise_for_status()
        rows, reports = parse_pirep_csv(resp.text)
        with conn:
            for p in reports:
                conn.execute(
                    "INSERT OR REPLACE INTO pireps (valid, lat, lon, alt_ft, aircraft, icing, report, truth)"
                    " VALUES (?,?,?,?,?,?,?,?)",
                    (_iso(p.valid), p.lat, p.lon, p.alt_ft, p.aircraft, p.icing, p.report, p.truth))
            conn.execute("INSERT OR REPLACE INTO pirep_days VALUES (?,?,?,?)",
                         (day.isoformat(), rows, len(reports), _iso(datetime.now(UTC))))
        counts = defaultdict(int)
        for p in reports:
            counts[p.truth] += 1
        logger.info("%s: %d rows, %d icing reports kept (%s)", day, rows, len(reports),
                    ", ".join(f"{k} {counts[k]}" for k in TRUTH_CLASSES if counts[k]))
        day += timedelta(days=1)
        time.sleep(1.0)  # be polite to IEM


# ═══════════════════════════════════════════════════════════════════════
# Model fetch
# ═══════════════════════════════════════════════════════════════════════


def run_for_valid(valid: datetime) -> tuple[datetime, int]:
    """The 6-hourly run and lead (3 or 6 h) used for a 3-hourly valid time.

    Short leads only: the question is whether the signal is right, not how far
    ahead it stays right.
    """
    lead = 3 if valid.hour % 6 == 3 else 6
    return valid - timedelta(hours=lead), lead


def gfs_url(run: datetime, lead: int) -> str:
    return (f"{GFS_BASE}/gfs.{run:%Y%m%d}/{run:%H}/atmos/"
            f"gfs.t{run:%H}z.pgrb2.0p25.f{lead:03d}")


def ecmwf_url(run: datetime, lead: int) -> str:
    stream = "oper" if run.hour in (0, 12) else "scda"
    return (f"{ECMWF_BASE}/{run:%Y%m%d}/{run:%H}z/ifs/0p25/{stream}/"
            f"{run:%Y%m%d%H}0000-{lead}h-{stream}-fc")


_GFS_LEVEL_VARS = frozenset({"RWMR", "SNMR", "TMP", "RH", "HGT"})
_GFS_SFC_VARS = frozenset({"CFRZR", "CICEP", "CRAIN", "CSNOW", "PRATE", "PRES"})
_GFS_2M_VARS = frozenset({"TMP", "RH"})


def plan_gfs(idx_text: str) -> list[tuple[int, int | None]]:
    """Byte ranges (start, end inclusive or None) for the GFS fields we need."""
    from weatherbrief.fetch.grib.gfs_idx import plan_byte_ranges

    ranges = [(r.start, r.end) for r in plan_byte_ranges(
        idx_text, variables=_GFS_LEVEL_VARS, min_level_hpa=MIN_LEVEL_HPA)]
    lines = [ln.split(":") for ln in idx_text.strip().splitlines()]
    offsets = sorted(int(p[1]) for p in lines if len(p) > 1)
    for p in lines:
        if len(p) < 6:
            continue
        var, level, step = p[3], p[4], p[5]
        if "ave" in step or "acc" in step:
            continue  # instantaneous fields only
        if (level == "surface" and var in _GFS_SFC_VARS) or (
                level == "2 m above ground" and var in _GFS_2M_VARS):
            start = int(p[1])
            nxt = [o for o in offsets if o > start]
            ranges.append((start, nxt[0] - 1 if nxt else None))
    return sorted(set(ranges))


_EC_LEVEL_PARAMS = frozenset({"t", "r", "q", "gh"})
_EC_SFC_PARAMS = frozenset({"ptype", "tprate", "2t", "2d", "sp"})


def plan_ecmwf(index_text: str) -> list[tuple[int, int | None]]:
    ranges = []
    for line in index_text.splitlines():
        if not line.strip():
            continue
        d = json.loads(line)
        p = d.get("param")
        if d.get("levtype") == "pl" and p in _EC_LEVEL_PARAMS:
            if int(d.get("levelist", 0)) < MIN_LEVEL_HPA:
                continue
        elif not (d.get("levtype") == "sfc" and p in _EC_SFC_PARAMS):
            continue
        start = int(d["_offset"])
        ranges.append((start, start + int(d["_length"]) - 1))
    return sorted(set(ranges))


def merge_ranges(ranges: list[tuple[int, int | None]], max_gap: int = 0
                 ) -> list[tuple[int, int | None]]:
    """Merge byte ranges that touch (or sit within ``max_gap`` bytes).

    Fewer, larger requests: each request costs ~0.5 s of latency, which on
    98 GFS messages dominates the transfer time.
    """
    merged: list[list] = []
    for start, end in sorted(ranges):
        if merged and merged[-1][1] is not None and start <= merged[-1][1] + 1 + max_gap:
            last_end = merged[-1][1]
            merged[-1][1] = None if end is None else max(last_end, end)
        else:
            merged.append([start, end])
    return [(s, e) for s, e in merged]


def _get_range(session: requests.Session, url: str, rng: tuple[int, int | None]) -> bytes:
    start, end = rng
    header = f"bytes={start}-" if end is None else f"bytes={start}-{end}"
    for attempt in range(4):
        try:
            r = session.get(url, headers={"Range": header}, timeout=120)
            if r.status_code in (200, 206):
                return r.content
            r.raise_for_status()
        except requests.RequestException:
            if attempt == 3:
                raise
            time.sleep(2 ** attempt)
    raise RuntimeError("unreachable")


def download(session: requests.Session, url: str, ranges, workers: int) -> bytes:
    """Fetch the ranges in parallel and return the concatenated GRIB messages."""
    with ThreadPoolExecutor(max_workers=workers) as pool:
        parts = list(pool.map(lambda r: _get_range(session, url, r), ranges))
    return b"".join(parts)


@dataclass(frozen=True)
class Grid:
    lat0: float
    lon0: float
    dlat: float
    dlon: float
    ni: int
    nj: int
    j_positive: bool

    def index(self, lat: float, lon: float) -> tuple[int, int]:
        if self.j_positive:
            iy = round((lat - self.lat0) / self.dlat)
        else:
            iy = round((self.lat0 - lat) / self.dlat)
        ix = round(((lon - self.lon0) % 360.0) / self.dlon) % self.ni
        return iy, ix

    def latlon(self, iy: int, ix: int) -> tuple[float, float]:
        lat = self.lat0 + iy * self.dlat if self.j_positive else self.lat0 - iy * self.dlat
        lon = self.lon0 + ix * self.dlon
        if lon >= 180.0:
            lon -= 360.0
        return round(lat, 3), round(lon, 3)

    def neighbourhood(self, lat: float, lon: float) -> list[tuple[int, int]]:
        iy, ix = self.index(lat, lon)
        return [(iy + a, (ix + b) % self.ni) for a in (-1, 0, 1) for b in (-1, 0, 1)
                if 0 <= iy + a < self.nj]


def _grid_of(gid) -> Grid:
    import eccodes

    return Grid(
        lat0=eccodes.codes_get(gid, "latitudeOfFirstGridPointInDegrees"),
        lon0=eccodes.codes_get(gid, "longitudeOfFirstGridPointInDegrees"),
        dlat=eccodes.codes_get(gid, "jDirectionIncrementInDegrees"),
        dlon=eccodes.codes_get(gid, "iDirectionIncrementInDegrees"),
        ni=eccodes.codes_get(gid, "Ni"),
        nj=eccodes.codes_get(gid, "Nj"),
        j_positive=bool(eccodes.codes_get(gid, "jScansPositively")),
    )


#: eccodes shortName → payload key, for surface-type messages.
_SFC_KEYS = {
    # GFS
    "cfrzr": "cfrzr", "cicep": "cicep", "crain": "crain", "csnow": "csnow",
    "prate": "prate", "sp": "sp", "2t": "t2m", "2r": "rh2m",
    # ECMWF
    "ptype": "ptype", "tprate": "prate", "2d": "td2m",
}
_LEVEL_KEYS = {"t": "t", "r": "r", "q": "q", "gh": "gh", "rwmr": "qr", "snmr": "qs"}


def _round(v: float) -> float:
    return float(f"{v:.4g}")


def sample_grib(data: bytes, points: list[tuple[float, float]]
                ) -> tuple[Grid | None, dict[tuple[int, int], dict]]:
    """Sample every message at each point's 3×3 neighbourhood.

    Returns the grid and {(iy, ix): {"levels": {hPa: {...}}, "sfc": {...}}}.
    Unknown messages are ignored; values under a bitmap become absent.
    """
    import eccodes

    grid: Grid | None = None
    cells: list[tuple[int, int]] = []
    out: dict[tuple[int, int], dict] = {}
    with tempfile.NamedTemporaryFile(suffix=".grib2") as tmp:
        tmp.write(data)
        tmp.flush()
        with open(tmp.name, "rb") as fh:
            while True:
                gid = eccodes.codes_grib_new_from_file(fh)
                if gid is None:
                    break
                try:
                    if grid is None:
                        grid = _grid_of(gid)
                        cells = sorted({c for lat, lon in points for c in grid.neighbourhood(lat, lon)})
                        out = {c: {"levels": {}, "sfc": {}} for c in cells}
                    short = eccodes.codes_get(gid, "shortName")
                    level_type = eccodes.codes_get(gid, "typeOfLevel")
                    level = eccodes.codes_get(gid, "level")
                    values = eccodes.codes_get_values(gid)
                    missing = eccodes.codes_get(gid, "missingValue")
                    bitmap = bool(eccodes.codes_get(gid, "bitmapPresent"))
                    if level_type == "isobaricInhPa":
                        key = _LEVEL_KEYS.get(short)
                        target = "levels"
                    else:
                        key = _SFC_KEYS.get(short)
                        target = "sfc"
                    if key is None:
                        continue
                    for iy, ix in cells:
                        v = float(values[iy * grid.ni + ix])
                        if bitmap and v == missing:
                            continue
                        if target == "levels":
                            out[(iy, ix)]["levels"].setdefault(str(level), {})[key] = _round(v)
                        else:
                            out[(iy, ix)]["sfc"][key] = _round(v)
                finally:
                    eccodes.codes_release(gid)
    return grid, out


def _fetch_one(session, conn, model: str, valid: datetime, points, workers: int) -> None:
    run, lead = run_for_valid(valid)
    url = gfs_url(run, lead) if model == "gfs" else ecmwf_url(run, lead)
    if model == "gfs":
        index_url = url + ".idx"
    else:  # ECMWF open data: <base>.index beside <base>.grib2
        index_url, url = url + ".index", url + ".grib2"
    t0 = time.monotonic()
    row = dict(model=model, valid=_iso(valid), run=_iso(run), lead_h=lead,
               n_bytes=None, seconds=None, n_columns=None, grid=None, error=None)
    try:
        for attempt in range(4):  # S3 answers 503 "slow down" under load
            idx = session.get(index_url, timeout=60)
            if idx.status_code < 500:
                break
            time.sleep(2 ** attempt)
        if idx.status_code in (403, 404):
            row.update(status="missing", error=f"index HTTP {idx.status_code}")
            return
        idx.raise_for_status()
        ranges = plan_gfs(idx.text) if model == "gfs" else plan_ecmwf(idx.text)
        ranges = merge_ranges(ranges, max_gap=64 * 1024)
        data = download(session, url, ranges, workers)
        grid, cols = sample_grib(data, points)
        with conn:
            for (iy, ix), payload in cols.items():
                lat, lon = grid.latlon(iy, ix)
                conn.execute(
                    "INSERT OR REPLACE INTO columns VALUES (?,?,?,?,?,?,?)",
                    (model, _iso(valid), iy, ix, lat, lon,
                     zlib.compress(json.dumps(payload, separators=(",", ":")).encode())))
        row.update(status="ok", n_bytes=len(data), n_columns=len(cols),
                   grid=json.dumps(grid.__dict__) if grid else None)
    except Exception as exc:  # noqa: BLE001 — a study logger records and moves on
        logger.warning("%s %s: %s", model, _iso(valid), exc)
        row.update(status="error", error=str(exc)[:500])
    finally:
        row["seconds"] = round(time.monotonic() - t0, 2)
        with conn:
            conn.execute(
                "INSERT OR REPLACE INTO fetches VALUES (:model,:valid,:run,:lead_h,:status,"
                ":n_bytes,:seconds,:n_columns,:grid,:error,:fetched_at)",
                {**row, "fetched_at": _iso(datetime.now(UTC))})
        if row["status"] == "ok":
            mb = row["n_bytes"] / 1e6
            logger.info("%s %s (run %s +%dh): %.1f MB in %.1f s (%.1f MB/s), %d columns",
                        model, row["valid"], row["run"], lead, mb, row["seconds"],
                        mb / max(row["seconds"], 0.01), row["n_columns"])


def report_points(conn, valid: datetime) -> list[tuple[float, float]]:
    """Positions of the reports matched to this valid time (±half a step)."""
    half = timedelta(hours=VALID_STEP_H / 2)
    rows = conn.execute(
        "SELECT lat, lon FROM pireps WHERE valid >= ? AND valid < ?",
        (_iso(valid - half), _iso(valid + half))).fetchall()
    return [(lat, lon) for lat, lon in rows]


def cmd_fetch(conn, start: date, end: date, models: list[str], workers: int,
              force: bool, limit: int | None) -> None:
    session = requests.Session()
    valid = datetime.combine(start, datetime.min.time(), UTC)
    stop = datetime.combine(end + timedelta(days=1), datetime.min.time(), UTC)
    done = 0
    while valid < stop:
        points = report_points(conn, valid)
        if points:
            for model in models:
                row = conn.execute("SELECT status FROM fetches WHERE model=? AND valid=?",
                                   (model, _iso(valid))).fetchone()
                if row and row[0] in ("ok", "missing") and not force:
                    continue
                _fetch_one(session, conn, model, valid, points, workers)
                done += 1
                if limit is not None and done >= limit:
                    return
        valid += timedelta(hours=VALID_STEP_H)


# ═══════════════════════════════════════════════════════════════════════
# Signals
# ═══════════════════════════════════════════════════════════════════════


@dataclass
class Level:
    p: int
    t_c: float
    rh: float | None
    alt_ft: float
    qr: float | None
    qs: float | None


def column_levels(payload: dict) -> list[Level]:
    """Above-ground levels of a stored column, surface first."""
    sp_hpa = payload["sfc"].get("sp")
    sp_hpa = sp_hpa / 100.0 if sp_hpa else None
    levels = []
    for p_str, v in payload["levels"].items():
        p = int(p_str)
        if "t" not in v or "gh" not in v:
            continue
        if sp_hpa is not None and p > sp_hpa + 5:
            continue  # below the model ground
        levels.append(Level(p, v["t"] - T0_K, v.get("r"), v["gh"] * FT_PER_M,
                            v.get("qr"), v.get("qs")))
    levels.sort(key=lambda lv: -lv.p)
    return levels


def levels_near(levels: list[Level], alt_ft: float) -> list[Level]:
    """Levels within ALT_WINDOW_FT of the altitude, else the bracketing pair."""
    near = [lv for lv in levels if abs(lv.alt_ft - alt_ft) <= ALT_WINDOW_FT]
    if near:
        return near
    below = [lv for lv in levels if lv.alt_ft <= alt_ft]
    above = [lv for lv in levels if lv.alt_ft > alt_ft]
    return ([below[-1]] if below else []) + ([above[0]] if above else [])


def temperature_at(levels: list[Level], alt_ft: float) -> float | None:
    below = [lv for lv in levels if lv.alt_ft <= alt_ft]
    above = [lv for lv in levels if lv.alt_ft > alt_ft]
    if not below or not above:
        return None
    a, b = below[-1], above[0]
    w = (alt_ft - a.alt_ft) / (b.alt_ft - a.alt_ft)
    return a.t_c + w * (b.t_c - a.t_c)


def is_supercooled_rain(lv: Level, liquid_only: bool = False) -> bool:
    if lv.qr is None or lv.t_c >= 0.0 or lv.qr < QR_MIN_KG_KG:
        return False
    if liquid_only and lv.qs is not None:
        return lv.qr / (lv.qr + lv.qs) > LIQUID_FRACTION_MIN
    return True


def column_signals(payload: dict, alt_ft: float) -> dict[str, bool | None]:
    """Cheap per-column signals. None = the model has no input for it."""
    levels = column_levels(payload)
    near = levels_near(levels, alt_ft)
    sfc = payload["sfc"]
    has_qr = any(lv.qr is not None for lv in levels)
    sig: dict[str, bool | None] = {}
    if has_qr:
        sig["scr_alt"] = any(is_supercooled_rain(lv) for lv in near)
        sig["scr_alt_liquid"] = any(is_supercooled_rain(lv, True) for lv in near)
        sig["scr_column"] = any(is_supercooled_rain(lv) for lv in levels)
    else:
        sig["scr_alt"] = sig["scr_alt_liquid"] = sig["scr_column"] = None
    if "cfrzr" in sfc or "cicep" in sfc:
        sig["cat_fzra_pl"] = bool(sfc.get("cfrzr", 0) >= 0.5 or sfc.get("cicep", 0) >= 0.5)
    else:
        sig["cat_fzra_pl"] = None
    if "ptype" in sfc:
        rate_mm_h = (sfc.get("prate") or 0.0) * 3600.0
        code = int(round(sfc["ptype"]))
        sig["ptype_fz"] = code in PTYPE_FREEZING and rate_mm_h >= PTYPE_GATE_MM_H
        sig["ptype_fzdz"] = code == 12 and rate_mm_h >= PTYPE_GATE_MM_H
    else:
        sig["ptype_fz"] = sig["ptype_fzdz"] = None
    return sig


def product_signals(payload: dict, valid: datetime, alt_ft: float, model: str
                    ) -> dict[str, bool | None]:
    """The product's own analysis on one column (what a briefing would compute)."""
    from weatherbrief.analysis.sounding import analyze_sounding
    from weatherbrief.analysis.sounding.precipitation import detect_warm_nose
    from weatherbrief.models import HourlyForecast, PressureLevelData, PrecipPhase

    sfc = payload["sfc"]
    levels = []
    for p_str, v in payload["levels"].items():
        if "t" not in v:
            continue
        levels.append(PressureLevelData(
            pressure_hpa=int(p_str),
            temperature_c=v["t"] - T0_K,
            relative_humidity_pct=v.get("r"),
            geopotential_height_m=v.get("gh"),
            rain_water_kg_kg=v.get("qr"),
            snow_water_kg_kg=v.get("qs"),
        ))
    t2 = sfc.get("t2m")
    td2 = sfc.get("td2m")
    if td2 is None and t2 is not None and sfc.get("rh2m") is not None:
        td2 = _dewpoint_k(t2, sfc["rh2m"])
    rate = sfc.get("prate")
    hourly = HourlyForecast(
        time=valid,
        temperature_2m_c=None if t2 is None else t2 - T0_K,
        dewpoint_2m_c=None if td2 is None else td2 - T0_K,
        surface_pressure_hpa=None if sfc.get("sp") is None else sfc["sp"] / 100.0,
        precipitation_mm=None if rate is None else rate * 3600.0,
    )
    try:
        sa = analyze_sounding(levels, hourly, model_key=model)
    except Exception as exc:  # noqa: BLE001
        logger.debug("analyze_sounding failed: %s", exc)
        sa = None
    if sa is None:
        return {"prod_fzra": None, "prod_warm_nose": None, "prod_sld_alt": None,
                "prod_scr_alt": None}
    precip = sa.precipitation
    fz_risk, _, _, pellets = detect_warm_nose(sa.derived_levels) if sa.derived_levels else (False, None, None, False)
    return {
        "prod_fzra": bool(precip and (precip.freezing_rain_risk or precip.surface_phase in (
            PrecipPhase.FREEZING_RAIN, PrecipPhase.ICE_PELLETS))),
        "prod_warm_nose": bool(fz_risk or pellets),
        "prod_sld_alt": any(z.base_ft <= alt_ft <= z.top_ft for z in sa.sld_zones),
        "prod_scr_alt": any(
            dl.supercooled_rain for dl in sa.derived_levels
            if dl.altitude_ft is not None and abs(dl.altitude_ft - alt_ft) <= ALT_WINDOW_FT),
    }


def _dewpoint_k(t_k: float, rh_pct: float) -> float:
    """Magnus dewpoint (K) from temperature (K) and RH (%)."""
    t_c = t_k - T0_K
    rh = max(min(rh_pct, 100.0), 1.0)
    g = math.log(rh / 100.0) + 17.62 * t_c / (243.12 + t_c)
    return 243.12 * g / (17.62 - g) + T0_K


# ═══════════════════════════════════════════════════════════════════════
# Report
# ═══════════════════════════════════════════════════════════════════════

SIGNALS = {
    "gfs": ("scr_alt", "scr_alt_liquid", "scr_column", "cat_fzra_pl",
            "prod_scr_alt", "prod_sld_alt", "prod_fzra", "prod_warm_nose"),
    "ecmwf": ("ptype_fz", "ptype_fzdz", "prod_sld_alt", "prod_fzra", "prod_warm_nose"),
}
#: Signals scored over the 3×3 neighbourhood (any cell) rather than the nearest one.
NEIGHBOURHOOD = ("scr_alt", "scr_alt_liquid", "scr_column", "cat_fzra_pl", "ptype_fz", "ptype_fzdz")


def _load_columns(conn, model: str, valid: str) -> tuple[Grid | None, dict]:
    g = conn.execute("SELECT grid FROM fetches WHERE model=? AND valid=? AND status='ok'",
                     (model, valid)).fetchone()
    if not g or not g[0]:
        return None, {}
    grid = Grid(**json.loads(g[0]))
    cols = {(iy, ix): json.loads(zlib.decompress(blob))
            for iy, ix, blob in conn.execute(
                "SELECT iy, ix, payload FROM columns WHERE model=? AND valid=?", (model, valid))}
    return grid, cols


def score(conn, models: list[str], start: date | None, end: date | None,
          subzero: bool, with_product: bool, step_h: int = VALID_STEP_H) -> dict:
    """{model: {signal: {truth: [flagged, n]}}} plus the n of reports scored.

    ``step_h`` is the spacing of the stored valid times (3 here; 1 for the
    hourly #1003 collector, which shares this schema). Reports whose truth is
    not an icing class (the collector's turbulence-only reports) are skipped.
    """
    results: dict = {m: defaultdict(lambda: defaultdict(lambda: [0, 0])) for m in models}
    scored = {m: 0 for m in models}
    skipped = {m: defaultdict(int) for m in models}
    half = timedelta(hours=step_h / 2)
    for model in models:
        valids = [v for (v,) in conn.execute(
            "SELECT valid FROM fetches WHERE model=? AND status='ok' ORDER BY valid", (model,))]
        for valid_s in valids:
            valid = _parse_iso(valid_s)
            if start and valid.date() < start or end and valid.date() > end:
                continue
            grid, cols = _load_columns(conn, model, valid_s)
            if grid is None:
                continue
            product_cache: dict = {}
            for lat, lon, alt, truth in conn.execute(
                    "SELECT lat, lon, alt_ft, truth FROM pireps WHERE valid >= ? AND valid < ?",
                    (_iso(valid - half), _iso(valid + half))):
                if truth not in TRUTH_CLASSES:
                    continue
                centre = grid.index(lat, lon)
                if centre not in cols:
                    skipped[model]["no_column"] += 1
                    continue
                t_alt = temperature_at(column_levels(cols[centre]), alt)
                if subzero and (t_alt is None or not SUBZERO_RANGE_C[0] <= t_alt <= SUBZERO_RANGE_C[1]):
                    skipped[model]["not_subzero"] += 1
                    continue
                sig = column_signals(cols[centre], alt)
                for cell in grid.neighbourhood(lat, lon):
                    if cell == centre or cell not in cols:
                        continue
                    other = column_signals(cols[cell], alt)
                    for k in NEIGHBOURHOOD:
                        if other.get(k):
                            sig[k] = True
                if with_product:
                    key = (centre, alt // 500)
                    if key not in product_cache:
                        product_cache[key] = product_signals(cols[centre], valid, alt, model)
                    sig.update(product_cache[key])
                scored[model] += 1
                for name in SIGNALS[model]:
                    v = sig.get(name)
                    if v is None:
                        continue
                    for bucket in (truth, "all"):
                        cell_ = results[model][name][bucket]
                        cell_[0] += int(bool(v))
                        cell_[1] += 1
    return {"results": results, "scored": scored, "skipped": skipped}


def format_report(res: dict, models: list[str]) -> str:
    cols = TRUTH_CLASSES + ("all",)
    out = []
    for model in models:
        out.append(f"\n{model.upper()}: {res['scored'][model]} reports scored"
                   + (f" (skipped: {dict(res['skipped'][model])})" if res['skipped'][model] else ""))
        header = f"{'signal':<16}" + "".join(f"{c:>15}" for c in cols)
        out.append(header)
        n_row = f"{'n reports':<16}"
        any_sig = next(iter(res["results"][model].values()), None)
        for c in cols:
            n = any_sig[c][1] if any_sig else 0
            n_row += f"{n:>15}"
        out.append(n_row)
        for name in SIGNALS[model]:
            row = f"{name:<16}"
            for c in cols:
                flagged, n = res["results"][model][name][c]
                row += f"{(f'{100 * flagged / n:.1f}%' if n else '-'):>15}"
            out.append(row)
    out.append("\nCells: % of reports in that class where the signal was set. "
               "'all' is the chance level. A useful signal is well above 'all' on "
               "fz_remark / clr_mxd_mod and well below it on neg.")
    return "\n".join(out)


def cmd_status(conn) -> None:
    n_days, n_rep = conn.execute("SELECT COUNT(*), COALESCE(SUM(n_kept),0) FROM pirep_days").fetchone()
    print(f"PIREP days: {n_days}, reports kept: {n_rep}")
    for truth, n in conn.execute("SELECT truth, COUNT(*) FROM pireps GROUP BY truth ORDER BY 2 DESC"):
        print(f"  {truth:<15} {n}")
    for model, status, n, mb, secs in conn.execute(
            "SELECT model, status, COUNT(*), COALESCE(SUM(n_bytes),0)/1e6, COALESCE(SUM(seconds),0)"
            " FROM fetches GROUP BY model, status ORDER BY model, status"):
        rate = f", {mb / secs:.1f} MB/s" if secs and mb else ""
        print(f"{model:<6} {status:<8} {n:>5} valid times, {mb:,.0f} MB in {secs:,.0f} s{rate}")
    n_cols, size = conn.execute("SELECT COUNT(*), COALESCE(SUM(LENGTH(payload)),0) FROM columns").fetchone()
    print(f"columns stored: {n_cols} ({size / 1e6:.1f} MB compressed)")


def estimate_full(conn, start: date, end: date) -> None:
    """Extrapolate download volume and time for a date range from what ran so far."""
    days = (end - start).days + 1
    valid_times = days * 24 // VALID_STEP_H
    for model, n, mb, secs in conn.execute(
            "SELECT model, COUNT(*), SUM(n_bytes)/1e6, SUM(seconds) FROM fetches"
            " WHERE status='ok' GROUP BY model"):
        per_mb, per_s = mb / n, secs / n
        print(f"{model}: {per_mb:.0f} MB and {per_s:.0f} s per valid time → "
              f"{valid_times} valid times ≈ {per_mb * valid_times / 1e3:,.0f} GB, "
              f"{per_s * valid_times / 3600:,.1f} h at this connection's rate")


# ═══════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════


def _default_out() -> Path:
    data = os.environ.get("DATA_DIR")
    if not data:
        try:
            from dotenv import dotenv_values
            data = dotenv_values(".env").get("DATA_DIR")
        except ImportError:
            data = None
    return Path(data or "data") / "sld_pirep_hindcast"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", type=Path, default=None, help="study directory")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def _dates(p):
        p.add_argument("--start", type=date.fromisoformat, required=True)
        p.add_argument("--end", type=date.fromisoformat, required=True)

    p = sub.add_parser("pireps")
    _dates(p)
    p.add_argument("--force", action="store_true")
    p = sub.add_parser("fetch")
    _dates(p)
    p.add_argument("--models", default=",".join(MODELS))
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--force", action="store_true")
    p.add_argument("--limit", type=int, default=None, help="stop after N model × valid fetches")
    p = sub.add_parser("report")
    p.add_argument("--start", type=date.fromisoformat)
    p.add_argument("--end", type=date.fromisoformat)
    p.add_argument("--models", default=",".join(MODELS))
    p.add_argument("--all-temperatures", action="store_true",
                   help="keep reports whose model temperature is outside -20…0 °C")
    p.add_argument("--no-product", action="store_true", help="skip the product analysis (fast)")
    sub.add_parser("status")
    p = sub.add_parser("estimate")
    _dates(p)

    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    for noisy in ("urllib3", "metpy", "weatherbrief"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    # MetPy warns on every truncated column (index levels above our 300 hPa cut).
    warnings.filterwarnings("ignore", message="Interpolation point out of data bounds")
    conn = open_db(args.out or _default_out())

    if args.cmd == "pireps":
        cmd_pireps(conn, args.start, args.end, args.force)
    elif args.cmd == "fetch":
        cmd_fetch(conn, args.start, args.end, args.models.split(","), args.workers,
                  args.force, args.limit)
    elif args.cmd == "report":
        models = args.models.split(",")
        res = score(conn, models, args.start, args.end,
                    subzero=not args.all_temperatures, with_product=not args.no_product)
        print(format_report(res, models))
    elif args.cmd == "status":
        cmd_status(conn)
    elif args.cmd == "estimate":
        estimate_full(conn, args.start, args.end)
    return 0


if __name__ == "__main__":
    sys.exit(main())
