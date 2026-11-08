"""Does MeteoSwiss ICON-CH1 beat AROME (and our other models) at Alpine airports?

A study logger, not product code: nothing imports it. It records what the
product does not already record, and joins what it does:

- **icon-ch1** (logged here): MeteoSwiss ICON-CH1-EPS control run, 1 km native
  grid. Files are found through the STAC API at data.geo.admin.ch and downloaded
  from the pre-signed CSCS URLs it returns. They are only kept for 24 h, so
  missed runs are lost. Sampled at the nearest native cell with the product's
  ``icon_grid`` sampler.
- **arome** (logged here): Météo-France AROME 0.025° SP1/SP2 surface packages
  through the product's downloader and decoder. AROME's surface files have no
  ceiling or visibility.
- **METARs** (logged here) through ``tasks/verification.fetch_observations_batch``,
  parsed exactly as the verification tables parse them.
- **ecmwf / gfs / icon** (joined, not downloaded): the forecast-map cycle's
  snapshot rows for the same airports, read from its SQLite artifacts
  (``eu-*.sqlite``, table ``airport_forecast_snapshots``) and copied into this
  study's DB on each ``log`` pass so they outlive the artifacts' 7-day pruning.

Commands::

    check    reachability and format probe (changes nothing)
    grid     once: CH1 grid, nearest cell + model height per airport
    log      every 30 min from cron; idempotent, retries unpublished files
    status   what has been logged
    report   errors vs METAR per model and lead bucket

Everything lives in one SQLite file under ``--out`` (default
``$DATA_DIR/alps_compare``). GRIB files are streamed to a temp file, decoded
and deleted. Nothing is pruned (a few MB a day): delete the directory when the
study ends.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import glob
import logging
import math
import os
import re
import sqlite3
import sys
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import requests

logger = logging.getLogger("alps_compare")

#: Alpine fields with METARs inside CH1's domain, plus two flat controls
#: (LSZH, LFSB). LOWK is east of AROME's domain.
AIRPORTS: tuple[str, ...] = (
    "LSGS", "LSGG", "LSZB", "LSZA", "LSZL", "LSZS", "LSZC", "LSZR", "LSGC",
    "LSZG", "LSMP", "LSME", "LSMM",
    "LFLS", "LFLB", "LFLP",
    "LOWI", "LOWS", "LOWK",
    "LIPB",
    "LSZH", "LFSB",
)

STAC_API = "https://data.geo.admin.ch/api/stac/v1"
CH1_COLLECTION = "ch.meteoschweiz.ogd-forecasting-icon-ch1"
CH1_VARS = ("CEILING", "VIS", "T_2M", "TD_2M", "U_10M", "V_10M", "VMAX_10M", "CLCT", "CLCL")
CH1_MAX_LEAD = 33
#: Hours after init before a run is worth asking for, and before a missing file
#: is given up on (CH1 is complete ~2 h after init; AROME groups land from ~3 h).
PUBLISH_DELAY_H = {"icon-ch1": 1.5, "arome": 3.0}
GIVE_UP_H = {"icon-ch1": 6, "arome": 12}

#: Product snapshot columns copied from the forecast-map artifacts.
PRODUCT_MODELS = ("ecmwf", "gfs", "icon")
PRODUCT_COLS = (
    "temperature_2m_c", "dewpoint_2m_c", "visibility_m", "wind_speed_10m_kt",
    "wind_direction_10m_deg", "wind_gusts_10m_kt", "nwp_ceiling_ft",
)

FT_PER_M = 3.28084
KT_PER_MS = 1.0 / 0.514444
NO_CEILING_M = 9999.0  # the product's "no ceiling" floor (#847)
TIMEOUT = (15, 120)

SCHEMA = """
CREATE TABLE IF NOT EXISTS grid (
    model TEXT, icao TEXT, uuid TEXT, n_cells INTEGER, cell INTEGER,
    dist_km REAL, model_elev_m REAL, field_elev_ft REAL, built_at TEXT,
    PRIMARY KEY (model, icao));
CREATE TABLE IF NOT EXISTS files (
    model TEXT, run TEXT, lead_h INTEGER, var TEXT, status TEXT,
    bytes INTEGER, at TEXT, PRIMARY KEY (model, run, lead_h, var));
CREATE TABLE IF NOT EXISTS forecasts (
    model TEXT, run TEXT, lead_h INTEGER, valid TEXT, icao TEXT, var TEXT, value REAL,
    PRIMARY KEY (model, run, lead_h, icao, var));
CREATE TABLE IF NOT EXISTS product (
    model TEXT, run TEXT, valid TEXT, icao TEXT,
    temperature_2m_c REAL, dewpoint_2m_c REAL, visibility_m REAL,
    wind_speed_10m_kt REAL, wind_direction_10m_deg REAL, wind_gusts_10m_kt REAL,
    nwp_ceiling_ft REAL, PRIMARY KEY (model, run, valid, icao));
CREATE TABLE IF NOT EXISTS metars (
    icao TEXT, obs_time TEXT, report_type TEXT, raw TEXT, ceiling_ft INTEGER,
    visibility_m INTEGER, wind_dir INTEGER, wind_kt INTEGER, gust_kt INTEGER,
    temp_c INTEGER, dewpoint_c INTEGER, PRIMARY KEY (icao, obs_time));
"""


# ---------------------------------------------------------------------------
# Plumbing
# ---------------------------------------------------------------------------


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%MZ")


def parse_iso(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%MZ").replace(tzinfo=UTC)


def load_env() -> None:
    with contextlib.suppress(Exception):
        from weatherbrief.envfiles import load_env as _load

        _load()


def open_db(out: Path) -> sqlite3.Connection:
    out.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(out / "alps_compare.sqlite", timeout=60)
    db.execute("PRAGMA journal_mode=WAL")  # report can read while cron logs
    db.executescript(SCHEMA)
    return db


def session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = "flyfun-weather-alps-compare/2"
    return s


def download(sess: requests.Session, url: str, dest: Path) -> int | None:
    """Stream ``url`` to ``dest``: bytes written, or None when not published (404/410).

    Any other HTTP error raises, so a block or an expired URL is never mistaken
    for "not published yet".
    """
    try:
        with sess.get(url, stream=True, timeout=TIMEOUT) as r:
            if r.status_code in (404, 410):
                return None
            r.raise_for_status()
            n = 0
            with open(dest, "wb") as f:
                for chunk in r.iter_content(1 << 20):
                    f.write(chunk)
                    n += len(chunk)
            return n
    except BaseException:
        dest.unlink(missing_ok=True)
        raise


def cadence_runs(now: datetime, every_h: int, lookback_h: int, delay_h: float) -> list[datetime]:
    """Runs at multiples of ``every_h`` UTC, newest first, from ``now - delay_h``
    back to ``now - lookback_h``."""
    t = (now - timedelta(hours=delay_h)).replace(minute=0, second=0, microsecond=0)
    t -= timedelta(hours=t.hour % every_h)
    out = []
    while t >= now - timedelta(hours=lookback_h):
        out.append(t)
        t -= timedelta(hours=every_h)
    return out


def airport_positions() -> dict[str, tuple[float, float, float | None]]:
    """``{icao: (lat, lon, elevation_ft)}`` from euro_aip (``AIRPORTS_DB``)."""
    from weatherbrief.airports import _load_airport_model

    db_path = os.environ.get("AIRPORTS_DB")
    if not db_path:
        sys.exit("AIRPORTS_DB is not set (it comes from the repo .env)")
    model = _load_airport_model(db_path)
    out = {}
    for icao in AIRPORTS:
        ap = model.find_airport_by_code(icao)
        if ap is None:
            logger.warning("%s not in euro_aip, skipped", icao)
            continue
        elev = float(ap.elevation_ft) if ap.elevation_ft is not None else None
        out[icao] = (float(ap.latitude_deg), float(ap.longitude_deg), elev)
    return out


def record_file(db, model, run, lead, var, status, nbytes=None):
    db.execute(
        "INSERT OR REPLACE INTO files VALUES (?,?,?,?,?,?,?)",
        (model, iso(run), lead, var, status, nbytes, iso(datetime.now(UTC))),
    )


def done_files(db, model, run) -> dict[tuple[int, str], str]:
    rows = db.execute(
        "SELECT lead_h, var, status FROM files WHERE model=? AND run=?", (model, iso(run)),
    )
    return {(lead, var): st for lead, var, st in rows}


def store(db, model, run, lead, per_icao: dict[str, dict[str, float]]) -> None:
    valid = iso(run + timedelta(hours=lead))
    db.executemany(
        "INSERT OR REPLACE INTO forecasts VALUES (?,?,?,?,?,?,?)",
        [
            (model, iso(run), lead, valid, icao, var, None if math.isnan(v) else float(v))
            for icao, vals in per_icao.items() for var, v in vals.items()
        ],
    )


# ---------------------------------------------------------------------------
# MeteoSwiss STAC (ICON-CH1)
# ---------------------------------------------------------------------------


def _duration_hours(s: str) -> int | None:
    """``P0DT01H00M00S`` / ``PT3H`` -> hours."""
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:[\d.]+S)?", s or "")
    return None if not m else int(m.group(1) or 0) * 24 + int(m.group(2) or 0)


def stac_search(sess, body: dict, max_pages: int = 10) -> list[dict]:
    """POST ``/search`` and follow ``next`` links (POST or GET style)."""
    feats: list[dict] = []
    method, url, payload = "POST", f"{STAC_API}/search", body
    for _ in range(max_pages):
        r = (sess.post(url, json=payload, timeout=TIMEOUT) if method == "POST"
             else sess.get(url, timeout=TIMEOUT))
        r.raise_for_status()
        obj = r.json()
        feats.extend(obj.get("features", []))
        nxt = next((ln for ln in obj.get("links", []) if ln.get("rel") == "next"), None)
        if not nxt:
            break
        method = nxt.get("method", "GET").upper()
        url, payload = nxt["href"], nxt.get("body", payload)
    return feats


def ch1_assets(sess, run: datetime, variable: str) -> dict[int, str]:
    """``{lead_h: href}`` of the control-run files of ``variable`` for ``run``.

    Tries MeteoSwiss's top-level ``forecast:*`` search fields first; if the
    server rejects them (400) or ignores them (items of other variables come
    back), retries with the standard STAC ``query`` form.
    """
    ref = run.strftime("%Y-%m-%dT%H:%M:%SZ")
    filters = {
        "forecast:reference_datetime": ref,
        "forecast:variable": variable,
        "forecast:perturbed": False,
    }
    feats = None
    with contextlib.suppress(requests.HTTPError):
        feats = stac_search(sess, {"collections": [CH1_COLLECTION], "limit": 100, **filters})
    if feats is None or any(
        f.get("properties", {}).get("forecast:variable") not in (None, variable) for f in feats
    ):
        feats = stac_search(sess, {
            "collections": [CH1_COLLECTION], "limit": 100,
            "query": {k: {"eq": v} for k, v in filters.items()},
        })
    out: dict[int, str] = {}
    for f in feats:
        p = f.get("properties", {})
        if p.get("forecast:perturbed") not in (False, None) or p.get("forecast:variable") not in (None, variable):
            continue
        lead = _duration_hours(p.get("forecast:horizon", ""))
        href = next((a.get("href") for a in f.get("assets", {}).values() if a.get("href")), None)
        if lead is not None and href:
            out[lead] = href
    return out


def ch1_collection_asset(sess, prefix: str) -> str | None:
    r = sess.get(f"{STAC_API}/collections/{CH1_COLLECTION}/assets", timeout=TIMEOUT)
    r.raise_for_status()
    return next((a.get("href") for a in r.json().get("assets", [])
                 if str(a.get("id", "")).startswith(prefix)), None)


def read_native(path: Path):
    """First (control) native-grid message of a file: ``(uuid, values)``."""
    from weatherbrief.fetch.grib.grib_reader import iter_messages

    for msg in iter_messages(path):
        if msg.is_native:
            return msg.uuid, msg.native_values()
    raise ValueError(f"{path.name}: no native-grid message")


def read_constants(path: Path) -> dict[str, tuple[str, object]]:
    """CLAT / CLON / HSURF from MeteoSwiss's horizontal-constants file, by shortName."""
    from weatherbrief.fetch.grib.grib_reader import iter_messages

    names = {"clat": "CLAT", "tlat": "CLAT", "clon": "CLON", "tlon": "CLON", "hsurf": "HSURF", "h": "HSURF"}
    out: dict[str, tuple[str, object]] = {}
    seen = []
    for msg in iter_messages(path):
        seen.append(msg.short_name)
        key = names.get((msg.short_name or "").lower())
        if key and key not in out and msg.is_native:
            out[key] = (msg.uuid, msg.native_values())
    if "CLAT" not in out or "CLON" not in out:
        raise ValueError(f"CLAT/CLON not found; messages: {seen}")
    return out


def ch1_point_values(raw: dict[str, float]) -> dict[str, float]:
    """CH1 native names/units -> comparison vars (°C, kt, deg, m, %)."""
    def ok(k):
        return raw.get(k) is not None and not math.isnan(raw[k])

    out: dict[str, float] = {}
    if ok("T_2M"):
        out["t2m_c"] = raw["T_2M"] - 273.15
    if ok("TD_2M"):
        out["td2m_c"] = raw["TD_2M"] - 273.15
    if ok("U_10M") and ok("V_10M"):
        u, v = raw["U_10M"], raw["V_10M"]
        out["wspd_kt"] = math.hypot(u, v) * KT_PER_MS
        out["wdir_deg"] = (math.degrees(math.atan2(-u, -v)) + 360.0) % 360.0
    if ok("VMAX_10M"):
        out["gust_kt"] = raw["VMAX_10M"] * KT_PER_MS
    if ok("VIS"):
        out["vis_m"] = raw["VIS"]
    if ok("CLCT"):
        out["tcc_pct"] = raw["CLCT"]
    if ok("CLCL"):
        out["lcc_pct"] = raw["CLCL"]
    if "CEILING" in raw:
        # As the product decodes ICON: the "no ceiling" fill, a missing cell or
        # a non-positive value all mean no ceiling; heights are metres MSL.
        c = raw["CEILING"]
        out["ceiling_msl_ft"] = (c * FT_PER_M) if (c is not None and not math.isnan(c)
                                                   and 0 < c < NO_CEILING_M) else float("nan")
    return out


def log_ch1_run(db, sess, run: datetime, leads: list[int], tmp: Path, now: datetime) -> dict:
    cells = {icao: (cell, n) for icao, cell, n in db.execute(
        "SELECT icao, cell, n_cells FROM grid WHERE model='icon-ch1'")}
    if not cells:
        raise RuntimeError("no CH1 grid: run `grid` first")
    n_cells = next(iter(cells.values()))[1]
    done = done_files(db, "icon-ch1", run)
    give_up = now - run > timedelta(hours=GIVE_UP_H["icon-ch1"])
    stats = {"ok": 0, "missing": 0, "errors": 0, "bytes": 0}
    touched: set[int] = set()
    try:
        # 'bad' (a decode failure, e.g. a truncated download) is retried like
        # 'missing' until the give-up window: CH1 files live only 24 h.
        retry = (None, "missing") if give_up else (None, "missing", "bad")
        for var in CH1_VARS:
            todo = [ld for ld in leads if done.get((ld, var)) in retry]
            if not todo:
                continue
            hrefs = ch1_assets(sess, run, var)
            for ld in todo:
                href = hrefs.get(ld)
                path = tmp / f"ch1-{var}-{ld}.grib2"
                try:
                    nbytes = download(sess, href, path) if href else None
                except requests.RequestException as exc:
                    # A 403 / expired pre-signed URL / timeout costs this file
                    # only; it stays unrecorded and is retried next pass.
                    logger.warning("icon-ch1 %s %s +%d: download failed: %s", iso(run), var, ld, exc)
                    stats["errors"] += 1
                    continue
                if nbytes is None:
                    record_file(db, "icon-ch1", run, ld, var, "absent" if give_up else "missing")
                    stats["missing"] += 1
                    continue
                try:
                    _uuid, vals = read_native(path)
                except Exception:
                    logger.exception("icon-ch1 %s %s +%d: decode failed", iso(run), var, ld)
                    record_file(db, "icon-ch1", run, ld, var, "bad")
                    db.commit()
                    continue
                finally:
                    path.unlink(missing_ok=True)
                if vals is None or vals.size != n_cells:
                    record_file(db, "icon-ch1", run, ld, var, "grid-mismatch")
                    db.commit()
                    raise RuntimeError(f"icon-ch1 {var}: grid changed, rerun `grid`")
                # Raw value and 'ok' in one commit: a later failure never leaves
                # a file marked done with nothing stored.
                store(db, "icon-ch1", run, ld, {
                    icao: {f"raw_{var}": float(vals[cell])} for icao, (cell, _n) in cells.items()
                })
                record_file(db, "icon-ch1", run, ld, var, "ok", nbytes)
                db.commit()
                touched.add(ld)
                stats["ok"] += 1
                stats["bytes"] += nbytes
    finally:
        # Derived values from everything stored for the lead (wind needs U and
        # V), even when a later file raised.
        for ld in sorted(touched):
            per_icao = {}
            for icao in cells:
                raw = {var[4:]: val for var, val in db.execute(
                    "SELECT var, value FROM forecasts WHERE model='icon-ch1' AND run=? "
                    "AND lead_h=? AND icao=? AND var LIKE 'raw_%'", (iso(run), ld, icao))}
                raw = {k: (float("nan") if v is None else v) for k, v in raw.items()}
                per_icao[icao] = ch1_point_values(raw)
            store(db, "icon-ch1", run, ld, per_icao)
        db.commit()
    return stats


# ---------------------------------------------------------------------------
# AROME (product downloader + decoder)
# ---------------------------------------------------------------------------


def log_arome_run(db, sess, run: datetime, leads: list[int], tmp: Path, now: datetime,
                  positions: dict) -> dict:
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

    icaos = sorted(positions)
    lats = [positions[i][0] for i in icaos]
    lons = [positions[i][1] for i in icaos]
    date_str, hour = run.strftime("%Y%m%d"), run.hour
    done = done_files(db, "arome", run)
    give_up = now - run > timedelta(hours=GIVE_UP_H["arome"])
    stats = {"ok": 0, "missing": 0, "bytes": 0}

    for group, hours in arome_groups_for_fhours(leads, AROME).items():
        if all(done.get((h, "SURFACE")) not in (None, "missing") for h in hours):
            continue
        codes = [sess.head(arome_package_url(date_str, hour, group, AROME, p), timeout=20).status_code
                 for p in AROME_SURFACE_PACKAGES]
        if any(c not in (200, 404) for c in codes):
            logger.warning("arome %s %s: HTTP %s, skipped this pass", iso(run), group, codes)
            continue
        if any(c == 404 for c in codes):
            for h in hours:
                record_file(db, "arome", run, h, "SURFACE", "absent" if give_up else "missing")
            stats["missing"] += len(hours)
            db.commit()
            continue
        merged = {h: [{} for _ in icaos] for h in hours}
        run_dir = Path(tempfile.mkdtemp(dir=tmp))
        try:
            for pkg in AROME_SURFACE_PACKAGES:
                path = download_arome_group(date_str, hour, group, run_dir, sess, AROME, pkg)
                if path is None:
                    raise RuntimeError(f"arome {group} {pkg}: download failed")
                stats["bytes"] += path.stat().st_size
                for h, pts in decode_arome_surface_per_point(path, hours, lats, lons)["points"].items():
                    for i, raw in enumerate(pts):
                        merged[h][i].update(raw)
                path.unlink(missing_ok=True)
        finally:
            for p in run_dir.glob("*"):
                p.unlink(missing_ok=True)
            with contextlib.suppress(OSError):
                run_dir.rmdir()
        for h in hours:
            per_icao = {}
            for i, icao in enumerate(icaos):
                snap = build_arome_surface_snapshot(merged[h][i])
                vals = {
                    "t2m_c": snap.get("temperature_2m_c"), "td2m_c": snap.get("dewpoint_2m_c"),
                    "wspd_kt": snap.get("wind_speed_10m_kt"), "wdir_deg": snap.get("wind_direction_10m_deg"),
                    "gust_kt": snap.get("wind_gusts_10m_kt"), "tcc_pct": snap.get("cloud_cover_pct"),
                    "lcc_pct": snap.get("cloud_cover_low_pct"),
                }
                per_icao[icao] = {k: v for k, v in vals.items() if v is not None}
            if not any(per_icao.values()):
                logger.error("arome %s +%d: decoder returned no values", iso(run), h)
                record_file(db, "arome", run, h, "SURFACE", "bad")
                continue
            store(db, "arome", run, h, per_icao)
            record_file(db, "arome", run, h, "SURFACE", "ok")
            stats["ok"] += 1
        db.commit()
    return stats


# ---------------------------------------------------------------------------
# METARs and product snapshots
# ---------------------------------------------------------------------------


def log_metars(db) -> int:
    from weatherbrief.tasks.verification import fetch_observations_batch

    obs = fetch_observations_batch(list(AIRPORTS), os.environ["AIRPORTS_DB"])
    db.executemany(
        "INSERT OR IGNORE INTO metars VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        [(o.icao, iso(o.observation_time), o.report_type, o.metar_raw, o.ceiling_ft,
          o.visibility_m, o.wind_dir, o.wind_speed_kt, o.wind_gust_kt,
          o.temperature_c, o.dewpoint_c) for o in obs],
    )
    db.commit()
    return len(obs)


def _artifact_iso(value) -> str | None:
    """An artifact datetime (ISO string, naive = UTC) -> this DB's ISO minutes."""
    if value is None:
        return None
    dt = datetime.fromisoformat(str(value))
    return iso(dt if dt.tzinfo else dt.replace(tzinfo=UTC))


def copy_product_snapshots(db, snapshots_dir: str) -> int:
    """Copy our airports' ECMWF/GFS/ICON rows from the forecast-map artifacts."""
    n = 0
    marks = ",".join("?" * len(AIRPORTS))
    models = ",".join("?" * len(PRODUCT_MODELS))
    for path in sorted(glob.glob(os.path.join(snapshots_dir, "eu-*.sqlite"))):
        try:
            src = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            rows = src.execute(
                f"SELECT model, model_init_time, forecast_hour, icao, {', '.join(PRODUCT_COLS)} "
                f"FROM airport_forecast_snapshots WHERE icao IN ({marks}) AND model IN ({models})",
                (*AIRPORTS, *PRODUCT_MODELS),
            ).fetchall()
            src.close()
        except sqlite3.Error as exc:
            logger.warning("snapshot artifact %s unreadable: %s", path, exc)
            continue
        cur = db.executemany(
            "INSERT OR IGNORE INTO product VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [(m, _artifact_iso(run), _artifact_iso(valid), icao, *vals)
             for m, run, valid, icao, *vals in rows],
        )
        n += cur.rowcount
    db.commit()
    return n


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_check(args) -> int:
    """Probe every source; print what it sees. Changes nothing."""
    import numpy as np

    sess = session()
    ok = True

    def line(name, good, detail=""):
        nonlocal ok
        ok &= bool(good)
        print(f"[{'OK ' if good else 'FAIL'}] {name}  {detail}")

    now = datetime.now(UTC)
    try:
        r = sess.get(f"{STAC_API}/collections/{CH1_COLLECTION}", timeout=TIMEOUT)
        line("STAC collection", r.ok, f"HTTP {r.status_code}")
    except Exception as exc:  # noqa: BLE001
        line("STAC collection", False, repr(exc))

    run = hrefs = None
    for cand in cadence_runs(now, 3, 24, 2.5):
        try:
            hrefs = ch1_assets(sess, cand, "T_2M")
        except Exception as exc:  # noqa: BLE001
            line("STAC search", False, repr(exc))
            break
        if hrefs:
            run = cand
            line("STAC search", True, f"run {iso(run)}: T_2M control leads {min(hrefs)}..{max(hrefs)}")
            break
    else:
        line("STAC search", False, "no control T_2M file in the last 24 h")

    with tempfile.TemporaryDirectory() as td:
        if run:
            for var in ("T_2M", "CEILING", "VIS"):
                h = hrefs if var == "T_2M" else ch1_assets(sess, run, var)
                if not h:
                    line(f"CH1 {var}", False, "not listed")
                    continue
                p = Path(td) / f"{var}.grib2"
                try:
                    t0 = time.monotonic()
                    n = download(sess, h[min(h, key=lambda x: abs(x - 1))], p)
                    uuid, v = read_native(p)
                    f = v[np.isfinite(v)]
                    q = np.percentile(f, [0, 10, 50, 90, 100]) if f.size else []
                    line(f"CH1 {var}", True,
                         f"{n / 1e6:.1f} MB in {time.monotonic() - t0:.1f} s, {v.size} cells, uuid {uuid}, "
                         f"missing {np.mean(~np.isfinite(v)):.1%}, <=0 {np.mean(f <= 0):.1%}, "
                         f">=9999 {np.mean(f >= 9999):.1%}, pct0/10/50/90/100 {' '.join(f'{x:.0f}' for x in q)}")
                except Exception as exc:  # noqa: BLE001
                    line(f"CH1 {var}", False, repr(exc))
        try:
            href = ch1_collection_asset(sess, "horizontal_constants")
            p = Path(td) / "hc.grib2"
            download(sess, href, p)
            consts = read_constants(p)
            line("CH1 horizontal_constants", True, f"{sorted(consts)} uuid {consts['CLAT'][0]}")
        except Exception as exc:  # noqa: BLE001
            line("CH1 horizontal_constants", False, repr(exc))

    try:
        from weatherbrief.fetch.grib.arome_fetch import AROME, arome_package_url

        found = next((c for c in cadence_runs(now, 3, 30, 3.0) if sess.head(
            arome_package_url(c.strftime("%Y%m%d"), c.hour, "00H06H", AROME, "SP1"),
            timeout=20).status_code == 200), None)
        line("AROME mirror", found is not None, f"latest {iso(found)}" if found else "nothing in 30 h")
    except Exception as exc:  # noqa: BLE001
        line("AROME mirror", False, repr(exc))

    if args.snapshots_dir:
        files = sorted(glob.glob(os.path.join(args.snapshots_dir, "eu-*.sqlite")))
        line("product snapshot artifacts", bool(files),
             f"{len(files)} files, newest {os.path.basename(files[-1]) if files else '-'}")
    else:
        line("product snapshot artifacts", False, "--snapshots-dir / ALPS_SNAPSHOTS_DIR not set")

    line("AIRPORTS_DB", bool(os.environ.get("AIRPORTS_DB")), os.environ.get("AIRPORTS_DB", ""))
    print("\nall OK" if ok else "\nsome checks failed")
    return 0 if ok else 1


def cmd_grid(args) -> int:
    """Nearest CH1 cell, model height and distance for each airport (one-off)."""
    from weatherbrief.fetch.grib.icon_grid import IconNativeGrid

    db = open_db(args.out)
    sess = session()
    positions = airport_positions()
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "hc.grib2"
        href = ch1_collection_asset(sess, "horizontal_constants")
        if not href or download(sess, href, p) is None:
            sys.exit("CH1 horizontal_constants not available")
        consts = read_constants(p)
    uuid, clat = consts["CLAT"]
    grid = IconNativeGrid(uuid, clat, consts["CLON"][1], default_method="nearest")
    hsurf = consts.get("HSURF", (None, None))[1]
    icaos = sorted(positions)
    nw = grid.nearest_weights([positions[i][0] for i in icaos], [positions[i][1] for i in icaos])
    built = iso(datetime.now(UTC))
    db.execute("DELETE FROM grid WHERE model='icon-ch1'")
    for row, k in enumerate(nw.inb_idx):
        icao, cell = icaos[int(k)], int(nw.idx[row, 0])
        lat, lon, elev_ft = positions[icao]
        dist = 2 * 6371.0 * math.asin(min(1.0, math.sqrt(
            math.sin(math.radians(grid.clat[cell] - lat) / 2) ** 2
            + math.cos(math.radians(lat)) * math.cos(math.radians(grid.clat[cell]))
            * math.sin(math.radians(grid.clon[cell] - lon) / 2) ** 2)))
        model_m = float(hsurf[cell]) if hsurf is not None else None
        db.execute("INSERT INTO grid VALUES (?,?,?,?,?,?,?,?,?)",
                   ("icon-ch1", icao, uuid, grid.n_cells, cell, dist, model_m, elev_ft, built))
        field_m = elev_ft / FT_PER_M if elev_ft is not None else None
        def m(x):
            return "-" if x is None else round(x)

        dz = model_m - field_m if model_m is not None and field_m is not None else None
        print(f"{icao}  cell {cell:8d}  {dist:4.2f} km  model {m(model_m)} m  "
              f"field {m(field_m)} m  dz {m(dz)} m")
    outside = set(icaos) - {icaos[int(k)] for k in nw.inb_idx}
    if outside:
        print("outside the CH1 grid:", ", ".join(sorted(outside)))
    db.commit()
    return 0


def cmd_log(args) -> int:
    if not os.environ.get("AIRPORTS_DB"):
        sys.exit("AIRPORTS_DB is not set (it comes from the repo .env)")
    args.out.mkdir(parents=True, exist_ok=True)
    with open(args.out / ".lock", "w") as lockf:
        try:
            fcntl.flock(lockf, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            logger.info("another log pass is running")
            return 0
        db = open_db(args.out)
        sess = session()
        now = datetime.now(UTC)
        leads = list(range(1, args.max_lead + 1))
        failed = False
        positions = None
        with tempfile.TemporaryDirectory() as td:
            for model in args.models:
                for run in cadence_runs(now, args.every, args.lookback_h, PUBLISH_DELAY_H[model]):
                    t0 = time.monotonic()
                    try:
                        if model == "icon-ch1":
                            st = log_ch1_run(db, sess, run, [ld for ld in leads if ld <= CH1_MAX_LEAD], Path(td), now)
                        else:
                            positions = positions or {k: v[:2] for k, v in airport_positions().items()}
                            st = log_arome_run(db, sess, run, leads, Path(td), now, positions)
                    except Exception:
                        logger.exception("%s %s failed", model, iso(run))
                        failed = True
                        continue
                    if st.get("errors"):
                        failed = True
                    if st["ok"] or st["missing"] or st.get("errors"):
                        logger.info("%s %s: %d ok, %d missing, %d errors, %.0f MB, %.0f s", model, iso(run),
                                    st["ok"], st["missing"], st.get("errors", 0), st["bytes"] / 1e6,
                                    time.monotonic() - t0)
        for name, step in (("metars", lambda: log_metars(db)),
                           ("product snapshots", lambda: copy_product_snapshots(db, args.snapshots_dir)
                            if args.snapshots_dir else 0)):
            try:
                logger.info("%s: %d rows", name, step())
            except Exception:
                logger.exception("%s failed", name)
                failed = True
    return 1 if failed else 0


def cmd_status(args) -> int:
    db = open_db(args.out)
    print("CH1 grid airports:", db.execute("SELECT COUNT(*) FROM grid").fetchone()[0])
    print("\nmodel  run  ok missing absent bad grid-mismatch  MB")
    for row in db.execute(
        "SELECT model, run, SUM(status='ok'), SUM(status='missing'), SUM(status='absent'), "
        "SUM(status='bad'), SUM(status='grid-mismatch'), ROUND(SUM(COALESCE(bytes,0))/1e6) "
        "FROM files GROUP BY model, run ORDER BY run DESC, model LIMIT 30"
    ):
        print("  ", *row)
    print("\nproduct rows:", dict(db.execute("SELECT model, COUNT(*) FROM product GROUP BY model").fetchall()))
    print("metars:", db.execute("SELECT COUNT(*), MIN(obs_time), MAX(obs_time) FROM metars").fetchone())
    return 0


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

BUCKETS = ((1, 6), (7, 12), (13, 24), (25, 48))


def field_elevations(db) -> dict[str, float]:
    """``{icao: field elevation ft}``: euro_aip first, the CH1 ``grid`` table as
    fallback. Warns for any study airport left without one, since its MSL model
    ceilings would then be scored unconverted against AGL METAR ceilings."""
    elev = {icao: e for icao, e in db.execute(
        "SELECT icao, field_elev_ft FROM grid WHERE model='icon-ch1'") if e is not None}
    if os.environ.get("AIRPORTS_DB"):
        try:
            elev.update({icao: p[2] for icao, p in airport_positions().items() if p[2] is not None})
        except Exception as exc:  # noqa: BLE001
            logger.warning("euro_aip elevations unavailable (%s); using the grid table", exc)
    missing = sorted(set(AIRPORTS) - set(elev))
    if missing:
        logger.warning("no field elevation for %s: their MSL ceilings are scored unconverted",
                       ", ".join(missing))
    return elev


def _forecast_rows(db, since: str, elev: dict[str, float]):
    """Yield ``(model, lead_h, valid, icao, vals)`` for every source, on one schema.

    ``vals`` keys: t2m_c, td2m_c, wspd_kt, wdir_deg, gust_kt, vis_m,
    ceiling_agl_ft, has_ceiling (the model publishes a ceiling at all).
    """
    from weatherbrief.analysis.airport_conditions import to_agl_ceiling
    from weatherbrief.tasks.scoring import model_cloud_base_agl

    rows: dict[tuple, dict] = {}
    for model, run, lead, valid, icao, var, val in db.execute(
        "SELECT model, run, lead_h, valid, icao, var, value FROM forecasts "
        "WHERE valid >= ? AND var NOT LIKE 'raw_%'", (since,)):
        rows.setdefault((model, lead, valid, icao), {})[var] = val
    for (model, lead, valid, icao), v in rows.items():
        if "ceiling_msl_ft" in v:
            c = v.pop("ceiling_msl_ft")
            v["ceiling_agl_ft"] = to_agl_ceiling(c, elev.get(icao), source_is_agl=False) if c is not None else None
            v["has_ceiling"] = True
        yield model, lead, valid, icao, v

    # The product stores "no ceiling" as NULL, but a model whose artifacts never
    # carry the column is NULL everywhere too. Only a model with some non-NULL
    # ceiling in the window is scored on ceiling; for it, NULL means clear.
    with_ceiling = {m for (m,) in db.execute(
        "SELECT DISTINCT model FROM product WHERE valid >= ? AND nwp_ceiling_ft IS NOT NULL", (since,))}
    for model, run, valid, icao, t, td, vis, ws, wd, wg, ceil in db.execute(
            "SELECT * FROM product WHERE valid >= ?", (since,)):
        lead = round((parse_iso(valid) - parse_iso(run)).total_seconds() / 3600)
        yield model, lead, valid, icao, {
            "t2m_c": t, "td2m_c": td, "vis_m": vis, "wspd_kt": ws, "wdir_deg": wd, "gust_kt": wg,
            "ceiling_agl_ft": model_cloud_base_agl(ceil, model, elev.get(icao)),
            "has_ceiling": model in with_ceiling,
        }


def cmd_report(args) -> int:
    """Errors vs the routine METAR nearest each valid hour, per model and lead bucket."""
    from weatherbrief.analysis.airport_conditions import classify_flight_category

    def cat(ceiling_ft, vis_m):
        return classify_flight_category(ceiling_ft, visibility_m=vis_m).value

    db = open_db(args.out)
    since = iso(datetime.now(UTC) - timedelta(days=args.days))
    # Routine METARs only, as the verification tables score; the one within
    # 30 min of each hour.
    metars: dict[tuple[str, str], tuple] = {}
    for icao, t_s, *obs in db.execute(
        "SELECT icao, obs_time, ceiling_ft, visibility_m, wind_kt, gust_kt, wind_dir, temp_c, dewpoint_c "
        "FROM metars WHERE obs_time >= ? AND (report_type IS NULL OR report_type='METAR')", (since,)):
        t = parse_iso(t_s)
        hour = (t + timedelta(minutes=30)).replace(minute=0)
        off = abs((t - hour).total_seconds())
        key = (icao, iso(hour))
        if key not in metars or off < metars[key][0]:
            metars[key] = (off, *obs)
    elev = field_elevations(db)
    dz = {icao: (m - elev[icao] / FT_PER_M) for icao, m in db.execute(
        "SELECT icao, model_elev_m FROM grid WHERE model='icon-ch1'")
        if m is not None and icao in elev}

    airports = set(args.airports) if args.airports else None
    samples = []
    for model, lead, valid, icao, v in _forecast_rows(db, since, elev):
        if airports is not None and icao not in airports:
            continue
        obs = metars.get((icao, valid))
        b = next((f"{a}-{z}h" for a, z in BUCKETS if a <= lead <= z), None)
        if obs is None or b is None:
            continue
        samples.append((model, lead, valid, icao, b, v, obs))
    if args.matched:
        # Common sample: only (airport, valid hour, lead) keys every listed
        # model has, so the rows compare the same cases.
        keys: dict[str, set] = {m: set() for m in args.matched}
        for model, lead, valid, icao, *_ in samples:
            if model in keys:
                keys[model].add((icao, valid, lead))
        common = set.intersection(*keys.values())
        samples = [s for s in samples if s[0] in keys and (s[3], s[2], s[1]) in common]

    stats: dict[tuple, dict[str, list]] = {}
    for model, lead, valid, icao, b, v, obs in samples:
        _, o_ceil, o_vis, o_wind, o_gust, o_wdir, o_t, o_td = obs
        s = stats.setdefault((model, b), {})

        def add(key, value, s=s):
            s.setdefault(key, []).append(value)

        if v.get("t2m_c") is not None and o_t is not None:
            add("T", v["t2m_c"] - o_t)
            if model == "icon-ch1" and icao in dz:
                add("Tcorr", v["t2m_c"] + 0.0065 * dz[icao] - o_t)
        if v.get("td2m_c") is not None and o_td is not None:
            add("Td", v["td2m_c"] - o_td)
        if v.get("wspd_kt") is not None and o_wind is not None:
            add("wind", v["wspd_kt"] - o_wind)
            if (v.get("wdir_deg") is not None and o_wdir is not None
                    and o_wind >= 5 and v["wspd_kt"] >= 5):
                add("dir", abs((v["wdir_deg"] - o_wdir + 180) % 360 - 180))
        if v.get("gust_kt") is not None and o_gust is not None:
            add("gust", v["gust_kt"] - o_gust)
        if v.get("has_ceiling"):
            for tag, mv, ov in (("ceil", None, None), ("full", v.get("vis_m"), o_vis)):
                if tag == "full" and mv is None:
                    continue  # no model visibility: no like-for-like full category
                m_cat, o_cat = cat(v.get("ceiling_agl_ft"), mv), cat(o_ceil, ov)
                add(f"{tag}_hit", float(m_cat == o_cat))
                m_low, o_low = m_cat in ("IFR", "LIFR"), o_cat in ("IFR", "LIFR")
                if o_low:
                    add(f"{tag}_pod", float(m_low))
                if m_low:
                    add(f"{tag}_far", float(not o_low))

    def mae(xs):
        return f"{sum(abs(x) for x in xs) / len(xs):5.1f}" if xs else "    -"

    def bias(xs):
        return f"{sum(xs) / len(xs):+5.1f}" if xs else "    -"

    def rate(xs):
        return f"{sum(xs) / len(xs):4.2f}({len(xs)})" if xs else "   -"

    print(f"Since {since}; routine METAR within 30 min of the valid hour.")
    print(f"Airports: {', '.join(sorted(airports)) if airports else 'all ' + str(len(AIRPORTS))}; "
          + (f"matched sample across {', '.join(args.matched)}." if args.matched
             else "unmatched: each row has its own runs, leads and hours (use --matched to compare)."))
    print("T/Td in C, wind in kt, dir in deg (both winds >= 5 kt).")
    print("gust*: only hours whose METAR has a gust group, so the bias is positive by construction.")
    print("Tcorr: CH1 T moved to field elevation at 6.5 K/km (diagnostic; wrong under inversions).")
    print("ceil: category from ceiling alone; full: ceiling + visibility, models with visibility only.")
    print("hit = exact category agreement, dominated by VFR hours; POD/FAR (IFR+LIFR) separate the models.")
    print("Ceilings AGL as the product converts them (ICON-family MSL, ECMWF AGL). (n) = samples.")
    print(f"25-48h: product models only (CH1/AROME are logged to --max-lead, CH1 stops at {CH1_MAX_LEAD} h).\n")
    print(f"{'model':9s} {'lead':6s} {'n':>5s} {'T mae/bias':>11s} {'Tcorr':>11s} {'Td mae/bias':>11s} "
          f"{'wind':>11s} {'gust*':>11s} {'dir':>5s}  {'ceil hit':>9s} {'POD':>9s} {'FAR':>9s}  "
          f"{'full hit':>9s} {'POD':>9s} {'FAR':>9s}")
    for (model, b) in sorted(stats):
        s = stats[(model, b)]
        g = s.get
        print(f"{model:9s} {b:6s} {len(g('T', [])):5d} {mae(g('T', []))}/{bias(g('T', []))} "
              f"{mae(g('Tcorr', []))}/{bias(g('Tcorr', []))} {mae(g('Td', []))}/{bias(g('Td', []))} "
              f"{mae(g('wind', []))}/{bias(g('wind', []))} {mae(g('gust', []))}/{bias(g('gust', []))} "
              f"{mae(g('dir', []))}  {rate(g('ceil_hit', [])):>9s} {rate(g('ceil_pod', [])):>9s} "
              f"{rate(g('ceil_far', [])):>9s}  {rate(g('full_hit', [])):>9s} {rate(g('full_pod', [])):>9s} "
              f"{rate(g('full_far', [])):>9s}")
    return 0


def main(argv=None) -> int:
    load_env()
    data = os.environ.get("DATA_DIR")
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", type=Path,
                    default=Path(data) / "alps_compare" if data else Path.home() / "alps_compare")
    ap.add_argument("--snapshots-dir", default=os.environ.get("ALPS_SNAPSHOTS_DIR"),
                    help="directory of the forecast-map eu-*.sqlite artifacts")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check")
    sub.add_parser("grid")
    lg = sub.add_parser("log")
    lg.add_argument("--models", nargs="+", default=["icon-ch1", "arome"], choices=["icon-ch1", "arome"])
    lg.add_argument("--every", type=int, default=6, help="log runs at multiples of N hours UTC")
    lg.add_argument("--max-lead", type=int, default=24)
    lg.add_argument("--lookback-h", type=int, default=20, help="CH1 files live 24 h")
    sub.add_parser("status")
    rp = sub.add_parser("report")
    rp.add_argument("--days", type=int, default=14)
    rp.add_argument("--airports", nargs="+", metavar="ICAO",
                    help="score only these airports (e.g. the flat controls LSZH LFSB)")
    rp.add_argument("--matched", nargs="+", metavar="MODEL",
                    help="score only (airport, hour, lead) cases all these models have, e.g. icon-ch1 arome")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    return {"check": cmd_check, "grid": cmd_grid, "log": cmd_log,
            "status": cmd_status, "report": cmd_report}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
