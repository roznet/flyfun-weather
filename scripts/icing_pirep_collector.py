"""Icing calibration database: our own ECMWF feed at every US PIREP (#1003).

The live counterpart of the #1000 hindcast (``sld_pirep_hindcast.py``). A
study collector, not product code: nothing imports it. All winter it samples
**our paid ECMWF feed** (the ECPDS delivery: ``a2`` = t/r/u/v/w/gh/clwc/ciwc/cc
on 25 pressure levels, ``a1`` = ptype, cloud cover, cloud base, ...) at every
US pilot report, so the in-cloud icing methods (Ogimet, SFIP, IENG) and the
SLD signals (#411) can be scored on the inputs we use in Europe.

- **PIREPs** from the AWC data API (``/api/data/pirep``, JSON, icing and
  turbulence already decoded), CONUS, at or below FL250. Reports with an
  icing group (NEG included), a turbulence group or a sky group (/SK: cloud
  cover, base, top, for the cloud / IMC scoring) are kept. AWC answers at
  most 400 reports per call, so each hour is one call, split in halves when
  it comes back full. It keeps about a month of history, so a missed hour is
  re-fetched with ``pireps --start/--end``.
- **ECMWF** from the run files already in ``ECMWF_GRIB_DIR`` (the compute
  node's 00Z / 12Z sync): nothing is downloaded. Each report is matched to the
  nearest hourly valid time and sampled from the freshest configured run with
  a lead of 1 to ``--max-lead`` hours (default cycles 00/12, so +1 to +12 h).
  The run and lead are stored with each sample. Adding the 06Z / 18Z runs
  (phase B) is ``--cycles 0,6,12,18``: the freshest run wins, no schema change.

The database is the #1000 schema (``pireps`` / ``columns`` / ``fetches``, model
``ecmwf``, columns on the global 0.25° lattice) plus four tables of its own
(``pirep_details``, ``samples``, ``pirep_hours``, ``runs_seen``), so the
hindcast's ``report`` and scoring run on it unchanged (here with hourly valid
times: ``report`` below passes ``step_h=1``).

Commands::

    collect   hourly from cron: pireps for the last --hours, then sample
    pireps    fetch PIREPs for an hour range (backfill; idempotent per hour)
    sample    sample every report not yet sampled (retries runs not landed yet)
    coverage  step 0 check: sub-grids of one run's a1 / a2 files, and the runs
              on disk with their age (changes nothing)
    status    reports, sample outcomes, leads, runs seen and how long they stayed
    report    the #1000 flag-rate report on this database
    score     the product's icing methods, temperature and sky against the
              reports (``icing_pirep_score.py``)

Cron on the compute node (hourly, after AWC's top-of-hour batch)::

    20 * * * * cd <repo> && venv/bin/python scripts/icing_pirep_collector.py collect

Everything lives in one SQLite file, ``--out``/icing_pirep_collector.sqlite
(default ``$DATA_DIR/icing_pirep_collector``), a few MB a day. No GRIB is kept.
Nothing is pruned: delete the directory when the study ends.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import logging
import os
import sqlite3
import sys
import time
import zlib
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sld_pirep_hindcast as hc  # noqa: E402  (scripts/ is not a package)

logger = logging.getLogger("icing_collector")

AWC_PIREPS = "https://aviationweather.gov/api/data/pirep"
#: AWC returns at most this many reports per call; a full answer is split.
AWC_MAX_ROWS = 400
#: Smallest window (hours) a full answer is split down to.
AWC_MIN_WINDOW_H = 0.125
USER_AGENT = "flyfun-weather icing calibration study"

MODEL = "ecmwf"
DB_NAME = "icing_pirep_collector.sqlite"
#: Columns live on the global 0.25° lattice (the #1000 GFS / ECMWF open-data
#: grid), whatever sub-grid the delivery used, so ``report`` maps reports the
#: same way and a1 / a2 cells line up.
CANONICAL_GRID = hc.Grid(lat0=90.0, lon0=0.0, dlat=0.25, dlon=0.25, ni=1440, nj=721,
                         j_positive=False)
#: Highest pressure level kept is 200 hPa: FL250 is ~375 hPa, the rest is
#: room above the report for cloud tops.
MIN_LEVEL_HPA = 200
#: a1 (surface) fields may be 3-hourly while a2 is hourly: take the nearest
#: a1 step within this many hours and record the offset.
SFC_MAX_OFFSET_H = 1
DEFAULT_CYCLES = (0, 12)
DEFAULT_MAX_LEAD_H = 12
#: How long after its init a run may still land before we stop waiting for
#: it. ECMWF publishes ~8 h after init and the node syncs as it lands; 14 h
#: leaves room for a late delivery or a missed sync without holding reports
#: past a typical 36 h retention.
RUN_WAIT_H = 14
#: A GRIB file younger than this may still be being written.
FILE_SETTLE_S = 120

#: ``samples.status`` values. ``wait`` is never stored: the report is retried.
SAMPLE_STATUSES = (
    "ok",            # a2 column at the report's cell (a1 too when present)
    "sfc_only",      # a1 fields only: the a2 delivery has no sub-grid here
    "outside_grid",  # neither part covers the report
    "step_missing",  # the run is on disk but not the a2 file for this valid hour
    "pruned",        # the run was seen on disk earlier and is gone
    "run_missing",   # the run never showed up before RUN_WAIT_H
    "error",         # decode failed (detail says why); retried every pass until
                     # it decodes or the run is gone (then ``pruned``)
)

EXTRA_SCHEMA = """
CREATE TABLE IF NOT EXISTS pirep_details (
    pirep_id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,         -- awc
    obs_time TEXT NOT NULL,       -- ISO UTC, second precision
    receipt_time TEXT,
    pirep_type TEXT,              -- PIREP | Urgent PIREP | AIREP
    flt_lvl_type TEXT,            -- OTHER | DURC | DURD | ...
    temp_c REAL,
    icing_json TEXT,              -- [{"int","type","base_ft","top_ft"}, ...]
    turb_json TEXT,               -- [{"int","type","freq","base_ft","top_ft"}, ...]
    clouds_json TEXT              -- [{"cover","base_ft","top_ft"}, ...] (the /SK group)
);
CREATE TABLE IF NOT EXISTS samples (
    pirep_id INTEGER NOT NULL,
    model TEXT NOT NULL,
    valid TEXT NOT NULL,          -- the hourly valid time sampled
    run TEXT,
    lead_h INTEGER,
    status TEXT NOT NULL,
    detail TEXT,
    sampled_at TEXT,
    PRIMARY KEY (pirep_id, model)
);
CREATE TABLE IF NOT EXISTS pirep_hours (
    hour TEXT PRIMARY KEY,        -- the window's end, ISO UTC
    n_rows INTEGER,
    n_kept INTEGER,
    n_calls INTEGER,
    fetched_at TEXT
);
CREATE TABLE IF NOT EXISTS runs_seen (
    model TEXT NOT NULL,
    run TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    PRIMARY KEY (model, run)
);
"""


def open_db(out: Path) -> sqlite3.Connection:
    out.mkdir(parents=True, exist_ok=True)
    # status / report open the DB without the collect lock: wait out a long write.
    conn = sqlite3.connect(out / DB_NAME, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")  # status / report can read while cron writes
    conn.executescript(hc.SCHEMA + EXTRA_SCHEMA)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(pirep_details)")}
    if "clouds_json" not in cols:  # databases created before the sky group was kept
        with conn:
            conn.execute("ALTER TABLE pirep_details ADD COLUMN clouds_json TEXT")
    return conn


def _iso(dt: datetime) -> str:
    return hc._iso(dt)


def _floor_hour(dt: datetime) -> datetime:
    return dt.replace(minute=0, second=0, microsecond=0)


def nearest_hour(dt: datetime) -> datetime:
    """The hourly valid time a report is matched to: [H-30 min, H+30 min)."""
    return _floor_hour(dt + timedelta(minutes=30))


# ═══════════════════════════════════════════════════════════════════════
# PIREPs (AWC data API)
# ═══════════════════════════════════════════════════════════════════════

#: Truth class for a report with a turbulence group and no icing group. Not
#: one of the hindcast's TRUTH_CLASSES, so icing scores never count it.
NO_ICING = "no_icing"
#: An icing group the truth classifier can't read (no intensity, type or NEG
#: word). Kept apart from ``no_icing`` so the turbulence set stays clean, and
#: not an icing class either, so the scores skip it.
ICING_UNCLASSIFIED = "icing_unclassified"
#: A report with a sky group (/SK: cover, base, top) and neither an icing nor
#: a turbulence group: kept for the cloud / IMC scoring, skipped by icing scores.
SKY_ONLY = "sky_only"


@dataclass
class AwcReport:
    obs_time: datetime
    lat: float
    lon: float
    alt_ft: int
    aircraft: str
    icing: str | None       # layer 1, "INT TYPE", as the hindcast's ICING field
    report: str
    truth: str
    receipt_time: str | None
    pirep_type: str | None
    flt_lvl_type: str | None
    temp_c: float | None
    icing_layers: list[dict]
    turb_layers: list[dict]
    sky_layers: list[dict]

    @property
    def valid(self) -> datetime:
        """Minute precision, as the hindcast stores it (its UNIQUE key)."""
        return self.obs_time.replace(second=0, microsecond=0)


def _hundreds_ft(v) -> int | None:
    try:
        return None if v in (None, "") else int(float(v) * 100)
    except (TypeError, ValueError):
        return None


def _str(v) -> str:
    return "" if v is None else str(v).strip()


def _icing_intensity(raw: str) -> str:
    # AWC writes a negative report as "NEG" and sometimes "NEGclr".
    return "NEG" if raw.upper().startswith("NEG") else raw


def _layers(row: dict, prefix: str, n: int = 2) -> list[dict]:
    out = []
    for i in range(1, n + 1):
        intensity = _str(row.get(f"{prefix}Int{i}"))
        if not intensity:
            continue
        layer = {"int": _icing_intensity(intensity) if prefix == "icg" else intensity,
                 "type": _str(row.get(f"{prefix}Type{i}")),
                 "base_ft": _hundreds_ft(row.get(f"{prefix}Bas{i}")),
                 "top_ft": _hundreds_ft(row.get(f"{prefix}Top{i}"))}
        if prefix == "tb":
            layer["freq"] = _str(row.get(f"{prefix}Freq{i}"))
        out.append(layer)
    return out


def _ft_or_none(v) -> int | None:
    """AWC's cloud base/top in feet; 0 means not reported."""
    try:
        ft = int(float(v))
    except (TypeError, ValueError):
        return None
    return ft if ft > 0 else None


def _sky_layers(row: dict) -> list[dict]:
    """The decoded /SK group: [{"cover", "base_ft", "top_ft"}, ...]."""
    out = []
    for c in row.get("clouds") or []:
        if not isinstance(c, dict):
            continue
        cover = _str(c.get("cover"))
        if not cover:
            continue
        out.append({"cover": cover, "base_ft": _ft_or_none(c.get("base")),
                    "top_ft": _ft_or_none(c.get("top"))})
    return out


def parse_awc_report(row: dict) -> AwcReport | None:
    """One AWC JSON report, or None when it is outside the study.

    Kept: inside CONUS, flight level 1-250 (hundreds of feet in ``fltLvl``),
    with an icing group (NEG included), a turbulence group or a sky group.
    The truth class is the hindcast's, from layer 1 of the icing group plus
    the raw report's remarks; turbulence-only reports get ``no_icing`` and
    sky-only ones ``sky_only``.
    """
    try:
        obs = datetime.fromtimestamp(int(row["obsTime"]), UTC)
        lat, lon = float(row["lat"]), float(row["lon"])
        alt = int(float(row["fltLvl"]) * 100)
    except (KeyError, TypeError, ValueError):
        return None
    lat_min, lat_max, lon_min, lon_max = hc.CONUS
    if not (lat_min <= lat <= lat_max and lon_min <= lon <= lon_max):
        return None
    if alt <= 0 or alt > hc.MAX_ALT_FT:
        return None
    icing_layers = _layers(row, "icg")
    turb_layers = _layers(row, "tb")
    sky_layers = _sky_layers(row)
    raw = _str(row.get("rawOb"))[: hc.MAX_REPORT_CHARS]
    icing = None
    truth = None
    if icing_layers:
        icing = f"{icing_layers[0]['int']} {icing_layers[0]['type']}".strip()
        truth = hc.classify_truth(icing, raw)
    if truth is None:
        # An unreadable icing group alone is noise; with a turbulence or sky
        # group the report is still worth its other half.
        if not (turb_layers or sky_layers):
            return None
        if icing_layers:
            truth = ICING_UNCLASSIFIED
        else:
            truth = NO_ICING if turb_layers else SKY_ONLY
    try:
        temp = None if row.get("temp") is None else float(row["temp"])
    except (TypeError, ValueError):
        temp = None
    return AwcReport(obs, lat, lon, alt, _str(row.get("acType")), icing, raw, truth,
                     row.get("receiptTime"), row.get("pirepType"), row.get("fltLvlType"),
                     temp, icing_layers, turb_layers, sky_layers)


def fetch_awc_window(session: requests.Session, end: datetime, hours: float,
                     get=None) -> tuple[list[dict], int]:
    """Every AWC report observed in [end - hours, end]; (rows, calls made).

    A full answer (AWC_MAX_ROWS) may be truncated, so the window is split in
    halves until each answer fits or the window is AWC_MIN_WINDOW_H. Windows
    include both ends, so neighbours overlap by a minute; the caller dedupes.
    """
    get = get or session.get
    lat_min, lat_max, lon_min, lon_max = hc.CONUS
    params = {"format": "json", "bbox": f"{lat_min},{lon_min},{lat_max},{lon_max}",
              "date": end.strftime("%Y%m%d_%H%M"), "age": f"{hours:g}"}
    resp = None
    for attempt in range(4):
        try:
            resp = get(AWC_PIREPS, params=params, timeout=60,
                       headers={"User-Agent": USER_AGENT})
            if resp.status_code < 500 and resp.status_code != 429:
                break
        except requests.RequestException:
            if attempt == 3:
                raise
        if attempt < 3:
            time.sleep(2 ** attempt)
    resp.raise_for_status()
    rows = resp.json() if resp.content.strip() else []
    if len(rows) >= AWC_MAX_ROWS and hours / 2 >= AWC_MIN_WINDOW_H:
        half = hours / 2
        late, n1 = fetch_awc_window(session, end, half, get)
        early, n2 = fetch_awc_window(session, end - timedelta(hours=half), half, get)
        return late + early, 1 + n1 + n2
    if len(rows) >= AWC_MAX_ROWS:
        logger.warning("AWC window %s -%gh still full at %d rows: some reports lost",
                       _iso(end), hours, len(rows))
    return rows, 1


def store_reports(conn: sqlite3.Connection, reports: list[AwcReport]) -> int:
    """Insert new reports (idempotent on the hindcast's UNIQUE key). Returns n new."""
    new = 0
    with conn:
        for r in reports:
            cur = conn.execute(
                "INSERT INTO pireps (valid, lat, lon, alt_ft, aircraft, icing, report, truth)"
                " VALUES (?,?,?,?,?,?,?,?)"
                # First version wins: a later AWC copy of the same report is
                # the re-fetch for late receipts, not a correction.
                " ON CONFLICT (valid, lat, lon, alt_ft, aircraft) DO NOTHING",
                (_iso(r.valid), r.lat, r.lon, r.alt_ft, r.aircraft, r.icing, r.report, r.truth))
            clouds = json.dumps(r.sky_layers) if r.sky_layers else None
            if cur.rowcount == 0:
                # Stored before the sky group was kept: fill it in on a re-fetch.
                if clouds:
                    conn.execute(
                        "UPDATE pirep_details SET clouds_json=? WHERE clouds_json IS NULL AND"
                        " pirep_id=(SELECT id FROM pireps WHERE valid=? AND lat=? AND lon=?"
                        " AND alt_ft=? AND aircraft=?)",
                        (clouds, _iso(r.valid), r.lat, r.lon, r.alt_ft, r.aircraft))
                continue
            new += 1
            conn.execute(
                "INSERT OR REPLACE INTO pirep_details (pirep_id, source, obs_time, receipt_time,"
                " pirep_type, flt_lvl_type, temp_c, icing_json, turb_json, clouds_json)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (cur.lastrowid, "awc", r.obs_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                 r.receipt_time, r.pirep_type, r.flt_lvl_type, r.temp_c,
                 json.dumps(r.icing_layers) if r.icing_layers else None,
                 json.dumps(r.turb_layers) if r.turb_layers else None, clouds))
    return new


def cmd_pireps(conn, session, start: datetime, end: datetime, force: bool, get=None) -> int:
    """Fetch hour windows ending start+1h .. end (floored to the hour)."""
    hour = _floor_hour(start) + timedelta(hours=1)
    stop = _floor_hour(end)
    total_new = 0
    while hour <= stop:
        done = conn.execute("SELECT 1 FROM pirep_hours WHERE hour=?", (_iso(hour),)).fetchone()
        if done and not force:
            hour += timedelta(hours=1)
            continue
        rows, calls = fetch_awc_window(session, hour, 1.0, get)
        reports = [r for r in (parse_awc_report(x) for x in rows) if r is not None]
        new = store_reports(conn, reports)
        with conn:
            conn.execute("INSERT OR REPLACE INTO pirep_hours VALUES (?,?,?,?,?)",
                         (_iso(hour), len(rows), len(reports), calls, _iso(datetime.now(UTC))))
        counts = Counter(r.truth for r in reports)
        logger.info("PIREPs to %s: %d rows, %d kept, %d new (%s)", _iso(hour), len(rows),
                    len(reports), new, ", ".join(f"{k} {v}" for k, v in counts.most_common()))
        total_new += new
        hour += timedelta(hours=1)
    return total_new


# ═══════════════════════════════════════════════════════════════════════
# ECMWF runs on disk
# ═══════════════════════════════════════════════════════════════════════


@dataclass
class RunFiles:
    """The a1 / a2 files of the runs on disk: {run: {part: {valid: path}}}.

    ``unsettled`` holds the (run, part, valid) of files present but younger
    than FILE_SETTLE_S (being written or re-synced): left out of ``files``.
    """
    files: dict[datetime, dict[str, dict[datetime, Path]]]
    unsettled: set = field(default_factory=set)

    @classmethod
    def scan(cls, grib_dir: Path, now_ts: float | None = None) -> RunFiles:
        from weatherbrief.fetch.grib.ecmwf_fetch import scan_ecmwf_files

        now_ts = now_ts if now_ts is not None else time.time()
        files: dict = defaultdict(lambda: defaultdict(dict))
        unsettled: set = set()
        for f in scan_ecmwf_files(grib_dir):
            if f.feed not in ("a1", "a2"):
                continue
            try:
                if now_ts - f.path.stat().st_mtime < FILE_SETTLE_S:
                    unsettled.add((f.base_time, f.feed, f.valid_time))
                    continue  # may still be being written
            except OSError:
                continue
            files[f.base_time][f.feed][f.valid_time] = f.path
        return cls({k: dict(v) for k, v in files.items()}, unsettled)

    def runs(self) -> set[datetime]:
        return set(self.files)

    def a2(self, run: datetime, valid: datetime) -> Path | None:
        return self.files.get(run, {}).get("a2", {}).get(valid)

    def a1(self, run: datetime, valid: datetime) -> tuple[Path | None, int | None]:
        """The a1 file nearest the valid time (ties: the earlier), and its offset."""
        steps = self.files.get(run, {}).get("a1", {})
        for off in sorted(range(-SFC_MAX_OFFSET_H, SFC_MAX_OFFSET_H + 1), key=lambda o: (abs(o), o)):
            p = steps.get(valid + timedelta(hours=off))
            if p is not None:
                return p, off
        return None, None


def candidate_runs(valid: datetime, cycles: tuple[int, ...], max_lead: int) -> list[datetime]:
    """Runs of the configured cycles with a lead of 1..max_lead h, freshest first."""
    out = []
    for lead in range(1, max_lead + 1):
        run = valid - timedelta(hours=lead)
        if run.hour in cycles:
            out.append(run)
    return out


def choose_run(valid: datetime, cycles: tuple[int, ...], max_lead: int, disk: RunFiles,
               seen: set[datetime], now: datetime) -> tuple[str, datetime | None]:
    """(status, run): ``ok`` with the run to sample, ``wait``, or a final status.

    The freshest candidate run wins. While it can still land (init +
    RUN_WAIT_H) we wait for it rather than take an older one; once it is past
    that, the next candidate is tried, and the reason the freshest failed is
    what gets recorded if none works.
    """
    reason = "run_missing"
    for i, run in enumerate(candidate_runs(valid, cycles, max_lead)):
        if run in disk.files:
            if disk.a2(run, valid) is not None:
                return "ok", run
            if now < run + timedelta(hours=RUN_WAIT_H):
                return "wait", None  # the run is still arriving
            status = "step_missing"
        elif now < run + timedelta(hours=RUN_WAIT_H):
            return "wait", None
        else:
            status = "pruned" if run in seen else "run_missing"
        if i == 0:
            reason = status
    return reason, None


# ═══════════════════════════════════════════════════════════════════════
# GRIB sampling
# ═══════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class MsgGrid:
    """A regular lat/lon (sub-)grid, as one GRIB message describes it."""
    lat0: float
    lon0: float
    dlat: float
    dlon: float
    ni: int
    nj: int
    j_positive: bool

    def flat_index(self, lat: float, lon: float) -> int | None:
        """Index of the nearest grid point in the message's values, or None outside."""
        j = (lat - self.lat0) / self.dlat if self.j_positive else (self.lat0 - lat) / self.dlat
        jr = round(j)
        if not 0 <= jr < self.nj or abs(j - jr) > 0.5 + 1e-6:
            return None
        ir = round(((lon - self.lon0) % 360.0) / self.dlon)
        if self.ni * self.dlon >= 360.0 - 1e-6:
            ir %= self.ni  # global in longitude: wrap
        elif not 0 <= ir < self.ni:
            return None
        return jr * self.ni + ir


#: a1 shortNames renamed to the hindcast's payload keys so its signals work.
_SFC_RENAME = {"2t": "t2m", "2d": "td2m", "tprate": "prate", "2r": "rh2m"}


def _msg_grid(gid) -> MsgGrid | None:
    import eccodes

    if eccodes.codes_get(gid, "gridType") != "regular_ll":
        return None
    return MsgGrid(
        lat0=eccodes.codes_get(gid, "latitudeOfFirstGridPointInDegrees"),
        lon0=eccodes.codes_get(gid, "longitudeOfFirstGridPointInDegrees"),
        dlat=eccodes.codes_get(gid, "jDirectionIncrementInDegrees"),
        dlon=eccodes.codes_get(gid, "iDirectionIncrementInDegrees"),
        ni=eccodes.codes_get(gid, "Ni"),
        nj=eccodes.codes_get(gid, "Nj"),
        j_positive=bool(eccodes.codes_get(gid, "jScansPositively")),
    )


def sample_file(path: Path, cells: list[tuple[int, int]], target: str,
                out: dict[tuple[int, int], dict]) -> dict[str, int]:
    """Add every field of a GRIB file at the canonical cells into ``out``.

    ``target`` is ``levels`` (a2: by pressure level, MIN_LEVEL_HPA and below)
    or ``sfc`` (a1). Sub-grids are first-wins per cell and key, like the
    product's decoder. Returns {"messages", "sub_grids"} for the log.
    """
    import eccodes

    latlons = {c: CANONICAL_GRID.latlon(*c) for c in cells}
    index_cache: dict[MsgGrid, dict] = {}
    n_msg = 0
    with open(path, "rb") as fh:
        while True:
            gid = eccodes.codes_grib_new_from_file(fh)
            if gid is None:
                break
            try:
                n_msg += 1
                grid = _msg_grid(gid)
                if grid is None:
                    continue
                if grid not in index_cache:
                    index_cache[grid] = {c: grid.flat_index(*ll) for c, ll in latlons.items()}
                where = {c: i for c, i in index_cache[grid].items() if i is not None}
                if not where:
                    continue
                short = eccodes.codes_get(gid, "shortName")
                if not short or short == "unknown":
                    short = f"p{eccodes.codes_get(gid, 'paramId')}"
                if target == "levels":
                    if eccodes.codes_get(gid, "typeOfLevel") != "isobaricInhPa":
                        continue
                    level = int(eccodes.codes_get(gid, "level"))
                    if level < MIN_LEVEL_HPA:
                        continue
                else:
                    short = _SFC_RENAME.get(short, short)
                values = eccodes.codes_get_values(gid)
                missing = eccodes.codes_get(gid, "missingValue")
                bitmap = bool(eccodes.codes_get(gid, "bitmapPresent"))
                for cell, i in where.items():
                    v = float(values[i])
                    if bitmap and v == missing:
                        continue
                    payload = out.setdefault(cell, {"levels": {}, "sfc": {}})
                    if target == "levels":
                        payload["levels"].setdefault(str(level), {}).setdefault(short, hc._round(v))
                    else:
                        payload["sfc"].setdefault(short, hc._round(v))
            finally:
                eccodes.codes_release(gid)
    return {"messages": n_msg, "sub_grids": len(index_cache)}


def _cells_for(points: list[tuple[float, float]]) -> list[tuple[int, int]]:
    return sorted({c for lat, lon in points for c in CANONICAL_GRID.neighbourhood(lat, lon)})


def _record(conn, ids: list[int], valid: datetime, run: datetime | None, status: str,
            detail: str | None = None, statuses: dict[int, str] | None = None) -> None:
    now = _iso(datetime.now(UTC))
    lead = None if run is None else int((valid - run).total_seconds() // 3600)
    with conn:
        for pid in ids:
            conn.execute(
                "INSERT OR REPLACE INTO samples VALUES (?,?,?,?,?,?,?,?)",
                (pid, MODEL, _iso(valid), None if run is None else _iso(run), lead,
                 (statuses or {}).get(pid, status), detail, now))


def sample_valid(conn, disk: RunFiles, valid: datetime, run: datetime,
                 reports: list[tuple[int, float, float]]) -> Counter:
    """Sample one valid hour's reports from one run and store columns + samples."""
    t0 = time.monotonic()
    a2 = disk.a2(run, valid)
    a1, sfc_off = disk.a1(run, valid)
    cells = _cells_for([(lat, lon) for _, lat, lon in reports])
    cols: dict[tuple[int, int], dict] = {}
    n_bytes = 0
    try:
        info = sample_file(a2, cells, "levels", cols)
        n_bytes += a2.stat().st_size
        if a1 is not None:
            sample_file(a1, cells, "sfc", cols)
            n_bytes += a1.stat().st_size
    except Exception as exc:  # noqa: BLE001 — a study collector records and moves on
        logger.warning("%s run %s: %s", _iso(valid), _iso(run), exc)
        _record(conn, [r[0] for r in reports], valid, run, "error", str(exc)[:500])
        return Counter(error=len(reports))
    statuses: dict[int, str] = {}
    for pid, lat, lon in reports:
        payload = cols.get(CANONICAL_GRID.index(lat, lon))
        if payload and payload["levels"]:
            statuses[pid] = "ok"
        elif payload and payload["sfc"]:
            statuses[pid] = "sfc_only"
        else:
            statuses[pid] = "outside_grid"
    with conn:
        for (iy, ix), payload in cols.items():
            if sfc_off and payload["sfc"]:
                payload["sfc_offset_h"] = sfc_off
            lat, lon = CANONICAL_GRID.latlon(iy, ix)
            conn.execute("INSERT OR REPLACE INTO columns VALUES (?,?,?,?,?,?,?)",
                         (MODEL, _iso(valid), iy, ix, lat, lon,
                          zlib.compress(json.dumps(payload, separators=(",", ":")).encode())))
        n_cols = conn.execute("SELECT COUNT(*) FROM columns WHERE model=? AND valid=?",
                              (MODEL, _iso(valid))).fetchone()[0]
        lead = int((valid - run).total_seconds() // 3600)
        if n_cols:
            conn.execute(
                "INSERT OR REPLACE INTO fetches VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (MODEL, _iso(valid), _iso(run), lead, "ok", n_bytes,
                 round(time.monotonic() - t0, 2), n_cols,
                 json.dumps(CANONICAL_GRID.__dict__), None, _iso(datetime.now(UTC))))
    detail = None if a1 is not None else "no a1 file within ±1 h"
    _record(conn, list(statuses), valid, run, "ok", detail, statuses)
    counts = Counter(statuses.values())
    logger.info("%s from %s +%dh: %d reports (%s), %d cells, %d a2 messages on %d sub-grids",
                _iso(valid), _iso(run), lead, len(reports), dict(counts), len(cols),
                info["messages"], info["sub_grids"])
    return counts


def update_runs_seen(conn, disk: RunFiles, now: datetime) -> set[datetime]:
    with conn:
        for run in disk.runs():
            conn.execute(
                "INSERT INTO runs_seen VALUES (?,?,?,?)"
                " ON CONFLICT (model, run) DO UPDATE SET last_seen=excluded.last_seen",
                (MODEL, _iso(run), _iso(now), _iso(now)))
    return {hc._parse_iso(r) for (r,) in conn.execute(
        "SELECT run FROM runs_seen WHERE model=?", (MODEL,))}


def cmd_sample(conn, grib_dir: Path, cycles: tuple[int, ...], max_lead: int,
               now: datetime | None = None,
               disk: RunFiles | None = None) -> Counter:
    now = now or datetime.now(UTC)
    disk = disk if disk is not None else RunFiles.scan(grib_dir)
    seen = update_runs_seen(conn, disk, now)
    # A decode error is often a file caught mid-sync: retry it every pass. It
    # ends as ``ok`` or, once the run is gone, ``pruned`` / ``run_missing``.
    with conn:
        conn.execute("DELETE FROM samples WHERE model=? AND status='error'", (MODEL,))
    pending: dict[datetime, list[tuple[int, float, float]]] = defaultdict(list)
    for pid, valid_s, lat, lon in conn.execute(
            "SELECT p.id, p.valid, p.lat, p.lon FROM pireps p LEFT JOIN samples s"
            " ON s.pirep_id = p.id AND s.model = ? WHERE s.pirep_id IS NULL", (MODEL,)):
        pending[nearest_hour(hc._parse_iso(valid_s))].append((pid, lat, lon))
    used = {hc._parse_iso(v): hc._parse_iso(r) for v, r in conn.execute(
        "SELECT valid, run FROM fetches WHERE model=? AND status='ok'", (MODEL,))}
    totals: Counter = Counter()
    for valid in sorted(pending):
        reports = pending[valid]
        if valid in used:
            # Later reports for an hour already sampled stay on the same run, so
            # one valid time's columns never mix runs (``fetches`` holds one run).
            run = used[valid]
            if disk.a2(run, valid) is not None:
                status = "ok"
            elif (run, "a2", valid) in disk.unsettled:
                # The file is there but being re-synced (inside the settle
                # window): try next pass.
                status = "wait"
            else:
                # Gone. The node prunes a run step by step once the next run
                # lands, so other steps of the run may well still be on disk.
                status = "pruned"
        else:
            status, run = choose_run(valid, cycles, max_lead, disk, seen, now)
        if status == "wait":
            totals["waiting"] += len(reports)
            continue
        if status != "ok":
            _record(conn, [r[0] for r in reports], valid, used.get(valid), status)
            totals[status] += len(reports)
            continue
        totals.update(sample_valid(conn, disk, valid, run, reports))
    logger.info("sample: %s", dict(totals) or "nothing pending")
    return totals


# ═══════════════════════════════════════════════════════════════════════
# Step 0 check, status, report
# ═══════════════════════════════════════════════════════════════════════


def describe_grids(path: Path) -> list[tuple[MsgGrid, int, list[str]]]:
    """(grid, n messages, a few shortNames) per distinct sub-grid of one file."""
    import eccodes

    seen: dict[MsgGrid, list] = {}
    with open(path, "rb") as fh:
        while True:
            gid = eccodes.codes_grib_new_from_file(fh)
            if gid is None:
                break
            try:
                g = _msg_grid(gid)
                if g is None:
                    continue
                entry = seen.setdefault(g, [0, []])
                entry[0] += 1
                name = eccodes.codes_get(gid, "shortName")
                if name not in entry[1]:
                    entry[1].append(name)
            finally:
                eccodes.codes_release(gid)
    return [(g, n, names) for g, (n, names) in seen.items()]


def _grid_box(g: MsgGrid) -> str:
    lat1 = g.lat0 + (g.nj - 1) * g.dlat * (1 if g.j_positive else -1)
    lon0 = g.lon0 - 360 if g.lon0 > 180 else g.lon0
    lon1 = lon0 + (g.ni - 1) * g.dlon
    return (f"lat {min(g.lat0, lat1):.2f}..{max(g.lat0, lat1):.2f}, "
            f"lon {lon0:.2f}..{lon1:.2f} ({g.dlat:g}° × {g.dlon:g}°, {g.ni}×{g.nj})")


def conus_coverage(g: MsgGrid) -> float:
    """Share of a 1° lattice over the CONUS box that the grid covers."""
    lat_min, lat_max, lon_min, lon_max = hc.CONUS
    pts = [(la, lo) for la in range(int(lat_min), int(lat_max) + 1)
           for lo in range(int(lon_min), int(lon_max) + 1)]
    return sum(g.flat_index(la, lo) is not None for la, lo in pts) / len(pts)


def cmd_coverage(grib_dir: Path, run_tag: str | None) -> None:
    disk = RunFiles.scan(grib_dir, now_ts=float("inf"))  # list every file
    if not disk.files:
        print(f"no a1 / a2 files in {grib_dir}")
        return
    now = datetime.now(UTC)
    print(f"Runs on disk in {grib_dir} (age = now - init):")
    for run in sorted(disk.files):
        parts = disk.files[run]
        print(f"  {run:%Y%m%d_%Hz}  age {(now - run).total_seconds() / 3600:5.1f} h  "
              + "  ".join(f"{p}: {len(parts.get(p, {}))} steps" for p in ("a1", "a2")))
    run = (datetime.strptime(run_tag, "%Y%m%d_%Hz").replace(tzinfo=UTC) if run_tag
           else max(disk.files))
    for part in ("a1", "a2"):
        steps = disk.files.get(run, {}).get(part, {})
        if not steps:
            print(f"\n{part}: no files for {run:%Y%m%d_%Hz}")
            continue
        valid = sorted(steps)[min(6, len(steps) - 1)]
        path = steps[valid]
        print(f"\n{part} sub-grids in {path.name}:")
        for g, n, names in describe_grids(path):
            print(f"  {_grid_box(g)}: {n} messages, CONUS covered {100 * conus_coverage(g):.0f}%"
                  f" [{', '.join(names[:12])}{', ...' if len(names) > 12 else ''}]")


def cmd_status(conn) -> None:
    n, days = conn.execute(
        "SELECT COUNT(*), COUNT(DISTINCT substr(valid, 1, 10)) FROM pireps").fetchone()
    print(f"PIREPs: {n} over {days} days")
    for truth, k in conn.execute("SELECT truth, COUNT(*) FROM pireps GROUP BY truth ORDER BY 2 DESC"):
        print(f"  {truth:<15} {k}")
    n_turb = conn.execute("SELECT COUNT(*) FROM pirep_details WHERE turb_json IS NOT NULL").fetchone()[0]
    print(f"  with a turbulence group: {n_turb}")
    n_sky = conn.execute("SELECT COUNT(*) FROM pirep_details WHERE clouds_json IS NOT NULL").fetchone()[0]
    print(f"  with a sky group: {n_sky}")
    last = conn.execute("SELECT MAX(hour), MAX(fetched_at) FROM pirep_hours").fetchone()
    print(f"last AWC hour {last[0]} (fetched {last[1]})")
    print("\nSamples:")
    for status, k in conn.execute(
            "SELECT status, COUNT(*) FROM samples WHERE model=? GROUP BY status ORDER BY 2 DESC", (MODEL,)):
        print(f"  {status:<13} {k}")
    waiting = conn.execute(
        "SELECT COUNT(*) FROM pireps p LEFT JOIN samples s ON s.pirep_id=p.id AND s.model=?"
        " WHERE s.pirep_id IS NULL", (MODEL,)).fetchone()[0]
    print(f"  not yet sampled {waiting}")
    leads = conn.execute("SELECT lead_h, COUNT(*) FROM samples WHERE model=? AND status='ok'"
                         " GROUP BY lead_h ORDER BY lead_h", (MODEL,)).fetchall()
    if leads:
        print("  ok by lead: " + ", ".join(f"+{h}h {k}" for h, k in leads))
    n_cols, size = conn.execute("SELECT COUNT(*), COALESCE(SUM(LENGTH(payload)),0) FROM columns").fetchone()
    print(f"columns stored: {n_cols} ({size / 1e6:.1f} MB compressed)")
    runs = conn.execute("SELECT run, first_seen, last_seen FROM runs_seen WHERE model=? ORDER BY run",
                        (MODEL,)).fetchall()
    if runs:
        newest_seen = max(b for _, _, b in runs)
        stays = [(hc._parse_iso(b) - hc._parse_iso(r)).total_seconds() / 3600
                 for r, _, b in runs if b < newest_seen]  # gone from disk since
        print(f"runs seen: {len(runs)}, newest {runs[-1][0]}"
              + (f"; older runs last seen {min(stays):.0f}-{max(stays):.0f} h after init" if stays else ""))


def cmd_report(conn, start: date | None, end: date | None, all_temps: bool, product: bool) -> None:
    res = hc.score(conn, [MODEL], start, end, subzero=not all_temps, with_product=product, step_h=1)
    print(hc.format_report(res, [MODEL]))


def cmd_score(conn, start: date | None, end: date | None) -> None:
    import warnings

    import icing_pirep_score as score

    product_log = logging.getLogger("weatherbrief")
    level = product_log.level
    product_log.setLevel(logging.ERROR)  # one line per odd sounding would bury the tables
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # MetPy on every sounding
            data = score.collect(conn, start, end)
    finally:
        product_log.setLevel(level)
    print(score.format_score(data))


# ═══════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════


def _env(name: str) -> str | None:
    val = os.environ.get(name)
    if val:
        return val
    try:
        from dotenv import dotenv_values
        return dotenv_values(".env").get(name)
    except ImportError:
        return None


def _default_out() -> Path:
    return Path(_env("DATA_DIR") or "data") / "icing_pirep_collector"


def _default_grib_dir() -> Path:
    from weatherbrief.fetch.grib.ecmwf_fetch import DEFAULT_ECMWF_GRIB_DIR
    return Path(_env("ECMWF_GRIB_DIR") or DEFAULT_ECMWF_GRIB_DIR).expanduser()


def _hour(s: str) -> datetime:
    """YYYY-MM-DD or YYYY-MM-DDTHH, UTC."""
    fmt = "%Y-%m-%dT%H" if "T" in s else "%Y-%m-%d"
    return datetime.strptime(s, fmt).replace(tzinfo=UTC)


@contextlib.contextmanager
def _lock(out: Path):
    """One writer at a time: a slow decode must not overlap the next cron run."""
    out.mkdir(parents=True, exist_ok=True)
    lock = out / ".lock"
    with open(lock, "a") as fh:  # "a": a waiting run must not touch the holder's mtime
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            os.utime(lock)  # mtime = when the holder took the lock
        except BlockingIOError:
            age = time.time() - lock.stat().st_mtime
            logger.warning("another run has held %s for %.0f min; skipping", lock, age / 60)
            yield False
            return
        yield True


def lock_state(out: Path) -> str:
    """One line for ``status``: is a collect run holding the lock, and since when."""
    lock = out / ".lock"
    if not lock.exists():
        return "lock: free"
    with open(lock, "a") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            age = (time.time() - lock.stat().st_mtime) / 60
            return f"lock: HELD for {age:.0f} min" + (" (stuck? a collect run takes minutes)" if age > 50 else "")
        fcntl.flock(fh, fcntl.LOCK_UN)
    return "lock: free"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", type=Path, default=None, help="study directory")
    ap.add_argument("--grib-dir", type=Path, default=None, help="ECMWF delivery dir (ECMWF_GRIB_DIR)")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def _sampling(p):
        p.add_argument("--cycles", default=",".join(map(str, DEFAULT_CYCLES)),
                       help="run init hours to sample from (phase B: 0,6,12,18)")
        p.add_argument("--max-lead", type=int, default=DEFAULT_MAX_LEAD_H)

    p = sub.add_parser("collect")
    p.add_argument("--hours", type=int, default=3,
                   help="re-fetch this many past hours (late receipts are common)")
    _sampling(p)
    p = sub.add_parser("pireps")
    p.add_argument("--start", type=_hour, required=True)
    p.add_argument("--end", type=_hour, required=True)
    p.add_argument("--force", action="store_true")
    p = sub.add_parser("sample")
    _sampling(p)
    p = sub.add_parser("coverage")
    p.add_argument("--run", default=None, help="YYYYMMDD_HHz (default: newest on disk)")
    sub.add_parser("status")
    p = sub.add_parser("report")
    p.add_argument("--start", type=date.fromisoformat)
    p.add_argument("--end", type=date.fromisoformat)
    p.add_argument("--all-temperatures", action="store_true")
    p.add_argument("--no-product", action="store_true")
    p = sub.add_parser("score")
    p.add_argument("--start", type=date.fromisoformat)
    p.add_argument("--end", type=date.fromisoformat)

    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    for noisy in ("urllib3", "metpy", "weatherbrief"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    out = args.out or _default_out()
    grib_dir = args.grib_dir or _default_grib_dir()

    if args.cmd == "coverage":
        cmd_coverage(grib_dir, args.run)
        return 0
    if args.cmd in ("status", "report", "score"):
        conn = open_db(out)
        if args.cmd == "status":
            cmd_status(conn)
            print(lock_state(out))
        elif args.cmd == "score":
            cmd_score(conn, args.start, args.end)
        else:
            cmd_report(conn, args.start, args.end, args.all_temperatures, not args.no_product)
        return 0

    with _lock(out) as ok:
        if not ok:
            return 0
        conn = open_db(out)
        session = requests.Session()
        if args.cmd == "pireps":
            cmd_pireps(conn, session, args.start, args.end, args.force)
            return 0
        cycles = tuple(int(c) for c in args.cycles.split(","))
        if args.cmd == "collect":
            now = datetime.now(UTC)
            try:
                cmd_pireps(conn, session, now - timedelta(hours=args.hours), now, force=True)
            except requests.RequestException as exc:
                # AWC down: still sample what is pending; the next run re-fetches.
                logger.warning("AWC fetch failed: %s", exc)
        cmd_sample(conn, grib_dir, cycles, args.max_lead)
    return 0


if __name__ == "__main__":
    sys.exit(main())
