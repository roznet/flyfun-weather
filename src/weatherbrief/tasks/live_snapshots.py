"""Per-tick display snapshots of the live layer (#711).

``live.json`` is overwritten every tick and ``live_history.jsonl`` keeps only
raw reports and change/evidence records, so neither the quiet stretches ("no
echo along the route" is a result) nor what the Observed section showed can be
rebuilt afterwards. Each commit therefore appends one line to the per-flight
``live_snapshots.jsonl.gz``: the blocks the Observed section renders, minus the
per-point sample arrays that only feed the map and cross-section overlays.

File format: one gzip member per append (``gzip`` reads concatenated members
as one stream), one JSON object per line.

- ``{"type": "tick", ...}``: written by ``commit_live_update`` under its
  per-flight lock. Carries ``tick_at``, the pack, the radar frame's valid time,
  the ``FlightProgress`` state used, and the display blocks.
- ``{"type": "highlight", "tick_at", "highlight"}``: the Haiku line generated
  after that tick's commit (``patch_highlight`` runs off the tick's critical
  path), merged into the tick's glance on read.

Not stored: ``route_observations`` (the METAR/TAF rows are rebuilt from the
history ``report`` records, :func:`observations_as_of`); only each corridor
airport's geometry rides along (``airports``), because the report records do
not carry it.

Readers: ``GET /live/ticks``, ``GET /live/at`` and the recap
(``tasks/live_recap.py``). Retention: kept 90 days after departure, forever for
kept flights (``tasks/retention.py``).
"""

from __future__ import annotations

import gzip
import json
import logging
import zlib
from datetime import datetime, timezone
from pathlib import Path

from weatherbrief.models.live import TRAIL_EXCLUDE, LiveLayer

logger = logging.getLogger(__name__)

LIVE_SNAPSHOTS_FILE = "live_snapshots.jsonl.gz"
#: Snapshot format version, bumped on an incompatible change.
SNAPSHOT_VERSION = 1
#: Hard cap on the file. Measured ~6 KB a tick gzipped, ~35 ticks a flight
#: day (~200 KB); ↻ presses add lines. Past the cap appends stop (logged
#: once per process per flight): a runaway writer must not fill the disk.
SNAPSHOT_MAX_BYTES = 1_500_000

#: ``observed_conditions`` fields kept whole. The four per-source fields keep
#: their metadata (valid time, window, attribution) without the per-station
#: sample arrays.
_OBSERVED_KEEP = (
    "computed_at", "corridor_nm", "radii_nm", "stations",
    "summary", "summary_entries", "summary_lines", "sources",
)
_OBSERVED_FIELDS = ("reflectivity", "rain_rate", "cloud_tops", "lightning")
#: Per-airport geometry kept so the METAR/TAF rows can be rebuilt.
_AIRPORT_GEOMETRY = (
    "icao", "name", "distance_from_route_nm", "enroute_distance_nm",
    "nearest_waypoint_icao", "eta_hour_offset", "lat", "lon",
)

_capped: set[str] = set()


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt is not None else None


def _observed_display(layer: LiveLayer) -> dict | None:
    oc = layer.observed_conditions
    if oc is None:
        return None
    out = oc.model_dump(mode="json", include=set(_OBSERVED_KEEP))
    for name in _OBSERVED_FIELDS:
        f = getattr(oc, name)
        out[name] = None if f is None else {**f.model_dump(mode="json", exclude={"stations"}), "stations": []}
    return out


def _glance(layer: LiveLayer) -> dict | None:
    if layer.glance is None:
        return None
    # The highlight's gate is the server's regeneration baseline (``/live``
    # strips it too); the carried-forward line itself is kept.
    return layer.glance.model_dump(mode="json", exclude={"highlight": {"gate"}})


def snapshot_record(layer: LiveLayer, *, tick_at: datetime, progress=None) -> dict:
    """The display snapshot of ``layer`` as committed at ``tick_at``."""
    obs = layer.route_observations
    return {
        "type": "tick",
        "v": SNAPSHOT_VERSION,
        "tick_at": tick_at.isoformat(),
        "pack_timestamp": layer.pack_timestamp,
        "pack_dir_name": layer.pack_dir_name,
        # The radar frame the ribbon was binned from: the recap picks segments
        # by this, not by tick_at (they differ by 6-15 min).
        "radar_time": _iso(layer.ribbon.radar_time) if layer.ribbon is not None else None,
        "progress": None if progress is None else {
            "source": progress.source,
            "flown_nm": progress.flown_nm,
            "departed_at": _iso(progress.departed_at),
            "arrival_at": _iso(progress.arrival),
        },
        "live_updated_at": _iso(layer.live_updated_at),
        "observations_updated_at": _iso(layer.observations_updated_at),
        "sigmets_updated_at": _iso(layer.sigmets_updated_at),
        "observed_updated_at": _iso(layer.observed_updated_at),
        "glance": _glance(layer),
        "ribbon": layer.ribbon.model_dump(mode="json") if layer.ribbon is not None else None,
        "storms": layer.storms.model_dump(mode="json") if layer.storms is not None else None,
        "changes": (
            layer.changes.model_dump(mode="json", exclude=TRAIL_EXCLUDE) if layer.changes is not None else None
        ),
        "last_refresh_delta": (
            layer.last_refresh_delta.model_dump(mode="json") if layer.last_refresh_delta is not None else None
        ),
        "route_sigmets": layer.route_sigmets.model_dump(mode="json") if layer.route_sigmets is not None else None,
        "observed_conditions": _observed_display(layer),
        "observations": None if obs is None else {
            "corridor_nm": obs.corridor_nm,
            "fetch_time": _iso(obs.fetch_time),
            "airports": [a.model_dump(mode="json", include=set(_AIRPORT_GEOMETRY)) for a in obs.airports],
        },
    }


def _append(flight_dir: Path, records: list[dict]) -> bool:
    path = Path(flight_dir) / LIVE_SNAPSHOTS_FILE
    key = str(path)
    size = path.stat().st_size if path.exists() else 0
    if size >= SNAPSHOT_MAX_BYTES:
        if key not in _capped:
            _capped.add(key)
            logger.warning("LIVE_SNAPSHOTS_CAPPED %s at %d bytes — no more ticks recorded", path, size)
        return False
    data = "".join(json.dumps(r, separators=(",", ":")) + "\n" for r in records).encode()
    # One gzip member per append. A failed write is rolled back to the old
    # size; a write cut short by a crash leaves a partial member, which the
    # reader skips by resyncing on the next member's header (_read_lines).
    with open(path, "ab") as f:
        try:
            f.write(gzip.compress(data, compresslevel=6))
            f.flush()
        except Exception:
            f.truncate(size)
            raise
    return True


def append_snapshot(flight_dir: Path | str, layer: LiveLayer, *, tick_at: datetime, progress=None) -> bool:
    """Append this tick's snapshot. Never raises: a snapshot problem must not
    fail the tick or the ↻ press. True if written."""
    try:
        return _append(Path(flight_dir), [snapshot_record(layer, tick_at=tick_at, progress=progress)])
    except Exception:
        logger.warning("Live snapshot write failed for %s — tick kept", flight_dir, exc_info=True)
        return False


def append_highlight(flight_dir: Path | str, highlight, *, tick_at: datetime) -> bool:
    """Record the highlight generated for the tick at ``tick_at`` (#697 runs
    after the commit). Never raises."""
    try:
        return _append(Path(flight_dir), [{
            "type": "highlight",
            "tick_at": tick_at.isoformat(),
            "highlight": highlight.model_dump(mode="json", exclude={"gate"}),
        }])
    except Exception:
        logger.warning("Live snapshot highlight write failed for %s", flight_dir, exc_info=True)
        return False


#: Start of every gzip member this module writes (magic + deflate).
_GZIP_HEADER = b"\x1f\x8b\x08"


def _read_lines(path: Path) -> list[str]:
    """Every complete line of every intact gzip member, in order.

    Members are decompressed one at a time. A corrupt or cut-short member (a
    write interrupted by a crash) is skipped: the read resyncs on the next
    gzip header, so only that member's lines are lost, never the later ticks.
    """
    raw = path.read_bytes()
    out: list[str] = []
    pos = 0
    while pos < len(raw):
        d = zlib.decompressobj(wbits=31)
        try:
            text = d.decompress(raw[pos:])
            ok = d.eof
        except zlib.error:
            ok = False
        if ok:
            out += text.decode(errors="replace").splitlines()
            pos = len(raw) - len(d.unused_data)
            continue
        nxt = raw.find(_GZIP_HEADER, pos + 1)
        logger.warning("Damaged snapshot member in %s at byte %d — skipped", path, pos)
        if nxt < 0:
            break
        pos = nxt
    return out


def load_snapshots(flight_dir: Path | str) -> list[dict]:
    """The flight's tick snapshots, oldest first, each with its generated
    highlight merged in. Empty when none were recorded. Never raises."""
    path = Path(flight_dir) / LIVE_SNAPSHOTS_FILE
    if not path.exists():
        return []
    try:
        lines = _read_lines(path)
    except OSError:
        logger.warning("Unreadable live snapshots %s — ignoring", path, exc_info=True)
        return []
    ticks: list[dict] = []
    by_tick: dict[str, dict] = {}
    for line in lines:
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if not isinstance(rec, dict):
            continue
        if rec.get("type") == "tick":
            ticks.append(rec)
            by_tick[rec.get("tick_at")] = rec
        elif rec.get("type") == "highlight":
            tick = by_tick.get(rec.get("tick_at"))
            if tick is not None and tick.get("glance") is not None:
                tick["glance"]["highlight"] = rec.get("highlight")
    return ticks


def has_snapshots(flight_dir: Path | str) -> bool:
    return (Path(flight_dir) / LIVE_SNAPSHOTS_FILE).exists()


def snapshots_capped(flight_dir: Path | str) -> bool:
    """The file reached ``SNAPSHOT_MAX_BYTES``: ticks after it were not
    recorded, so a recap must not read the rest as "not reached"."""
    path = Path(flight_dir) / LIVE_SNAPSHOTS_FILE
    return path.exists() and path.stat().st_size >= SNAPSHOT_MAX_BYTES


# --- The METAR/TAF rows as of a tick ------------------------------------------


def parse_utc(value) -> datetime | None:
    """An ISO string from a snapshot or history record as aware UTC, or None."""
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _seen_by(rec: dict, at: datetime) -> bool:
    t = parse_utc(rec.get("tick_at"))
    return t is not None and t <= at


def observations_as_of(history: list[dict], snapshot: dict, progress=None):
    """The route METAR/TAF rows as they stood at ``snapshot``'s tick.

    Rebuilt from the history ``report`` records seen by then (latest METAR and
    latest TAF per airport) on the corridor airports the snapshot lists, the
    way ``route_weather.run_route_weather`` fills them. The TAF is read at each
    airport's ETA from ``progress`` (the recap's time base), or left unread
    without one. Not rebuilt: the runway/wind picture and the wind advisories,
    which need the airports database (the runway dials are hidden, #758).
    None when the snapshot lists no airports.
    """
    from euro_aip.briefing.weather.parser import WeatherParser

    from weatherbrief.models.observations import AirportObservation, RouteObservations
    from weatherbrief.tasks.route_weather import _apply_taf_at_eta, _worst_category

    block = snapshot.get("observations")
    at = parse_utc(snapshot.get("tick_at"))
    if not block or at is None:
        return None
    metars: dict[str, list[dict]] = {}
    tafs: dict[str, dict] = {}
    for r in history:
        if r.get("type") != "report" or not _seen_by(r, at):
            continue
        icao = (r.get("icao") or "").upper()
        if r.get("kind") == "metar" and r.get("raw"):
            metars.setdefault(icao, []).append(r)
        elif r.get("kind") == "taf" and r.get("raw"):
            prev = tafs.get(icao)
            if prev is None or (r.get("issued_at") or "") >= (prev.get("issued_at") or ""):
                tafs[icao] = r

    airports: list[AirportObservation] = []
    for geo in block.get("airports") or []:
        try:
            obs = AirportObservation.model_validate(geo)
        except Exception:
            continue
        icao = obs.icao.upper()
        reports = sorted(metars.get(icao, []), key=lambda r: r.get("observed_at") or "")
        if reports:
            latest = reports[-1]
            rep = WeatherParser.parse_metar(latest["raw"], source="live_history",
                                            reference=parse_utc(latest.get("observed_at")))
            obs.has_metar = True
            obs.metar_raw = latest["raw"]
            obs.metar_time = parse_utc(latest.get("observed_at"))
            obs.metar_report_type = latest.get("report_type")
            obs.metar_flight_category = latest.get("flight_category") or (
                rep.flight_category.value if rep is not None and rep.flight_category is not None else None
            )
            if rep is not None:
                obs.metar_ceiling_ft = rep.ceiling_ft
                obs.metar_visibility_m = rep.visibility_meters
                obs.metar_wind_dir = rep.wind_direction
                obs.metar_wind_speed_kt = rep.wind_speed
                obs.metar_wind_gust_kt = rep.wind_gust
                obs.metar_wind_variable_from = rep.wind_variable_from
                obs.metar_wind_variable_to = rep.wind_variable_to
                obs.metar_weather = list(rep.weather_conditions)
                obs.metar_temperature_c = rep.temperature
                obs.metar_dewpoint_c = rep.dewpoint
                obs.metar_qnh = rep.altimeter
            earlier = [r for r in reports[:-1] if (r.get("observed_at") or "") < (latest.get("observed_at") or "")]
            if earlier:
                obs.metar_previous_flight_category = earlier[-1].get("flight_category")
                obs.metar_previous_time = parse_utc(earlier[-1].get("observed_at"))
        taf_rec = tafs.get(icao)
        if taf_rec is not None:
            obs.has_taf = True
            obs.taf_raw = taf_rec["raw"]
            obs.taf_issue_time = parse_utc(taf_rec.get("issued_at"))
            eta = progress.eta(obs.enroute_distance_nm) if progress is not None else None
            taf = WeatherParser.parse_taf(taf_rec["raw"], source="live_history",
                                          reference=parse_utc(taf_rec.get("issued_at")) or at)
            if taf is not None and eta is not None:
                try:
                    _apply_taf_at_eta(obs, taf, eta)
                except Exception:
                    logger.debug("TAF at ETA failed for %s", icao, exc_info=True)
        airports.append(obs)

    metar_cats = [a.metar_flight_category for a in airports if a.metar_flight_category]
    taf_cats = [a.taf_flight_category_at_eta for a in airports if a.taf_flight_category_at_eta]
    return RouteObservations(
        corridor_nm=block.get("corridor_nm") or 0.0,
        fetch_time=parse_utc(block.get("fetch_time")) or at,
        airports_found=len(airports),
        airports_with_metar=sum(1 for a in airports if a.has_metar),
        airports_with_taf=sum(1 for a in airports if a.has_taf and a.taf_valid_at_eta),
        airports=airports,
        worst_metar_category=_worst_category(metar_cats),
        worst_taf_category=_worst_category(taf_cats),
        phenomena_along_route=sorted({w for a in airports for w in a.metar_weather}),
    )


# --- One tick, in the /live shape ---------------------------------------------


def find_tick(snapshots: list[dict], tick: datetime) -> dict | None:
    """The snapshot committed at ``tick``, else the last one before it (a
    client mapping a route point to a time asks for "as of"). None when the
    flight has no snapshot at or before ``tick``."""
    best = None
    for s in snapshots:
        t = parse_utc(s.get("tick_at"))
        if t is None or t > tick:
            continue
        if best is None or t >= parse_utc(best.get("tick_at")):
            best = s
    return best


def tick_view(flight_dir: Path | str, snapshot: dict, history: list[dict] | None = None) -> dict:
    """``snapshot`` as the fields of a ``GET /live`` response: the stored
    blocks, the change trails as of that tick, and the METAR/TAF rows rebuilt
    from the history (:func:`observations_as_of`)."""
    from weatherbrief.models.live import LiveChanges
    from weatherbrief.tasks.artifacts import load_briefing
    from weatherbrief.tasks.live_layer import flight_progress, load_live_history
    from weatherbrief.tasks.live_trail import route_context, trails_for_pack

    flight_dir = Path(flight_dir)
    tick_at = parse_utc(snapshot.get("tick_at"))
    name = snapshot.get("pack_dir_name")
    # No name would make this the flight dir itself: never read a briefing there.
    pack_dir = flight_dir / name if name else None
    briefing = (load_briefing(pack_dir) if pack_dir is not None and pack_dir.is_dir() else None) or {}
    route, departure = route_context(briefing)
    progress = flight_progress(route, departure, tick_at) if tick_at is not None else None
    history = history if history is not None else load_live_history(flight_dir)
    try:
        observations = observations_as_of(history, snapshot, progress)
    except Exception:
        logger.warning("METAR/TAF rebuild failed for %s at %s", flight_dir, tick_at, exc_info=True)
        observations = None
    try:
        changes = LiveChanges.model_validate(snapshot["changes"]) if snapshot.get("changes") else None
    except Exception:
        logger.warning("Snapshot changes unreadable for %s at %s — served without", flight_dir, tick_at)
        changes = None
    return {
        "pack_timestamp": snapshot.get("pack_timestamp"),
        "live_updated_at": snapshot.get("live_updated_at"),
        "route_observations": observations,
        "observations_updated_at": snapshot.get("observations_updated_at"),
        "route_sigmets": snapshot.get("route_sigmets"),
        "sigmets_updated_at": snapshot.get("sigmets_updated_at"),
        "observed_conditions": snapshot.get("observed_conditions"),
        "observed_updated_at": snapshot.get("observed_updated_at"),
        "storms": snapshot.get("storms"),
        "glance": snapshot.get("glance"),
        "ribbon": snapshot.get("ribbon"),
        # Trails as /live served them at that tick (#669): the history up to it.
        "changes": (
            trails_for_pack(pack_dir, changes, now=tick_at, briefing_data=briefing)
            if pack_dir is not None else changes
        ),
        "last_refresh_delta": snapshot.get("last_refresh_delta"),
        "tick_at": snapshot.get("tick_at"),
        "radar_time": snapshot.get("radar_time"),
        "progress": snapshot.get("progress"),
    }


def tick_index(snapshots: list[dict]) -> list[dict]:
    """``GET /live/ticks``: each tick's time, radar frame and position."""
    return [
        {
            "tick_at": s.get("tick_at"),
            "radar_time": s.get("radar_time"),
            "pack_timestamp": s.get("pack_timestamp"),
            "flown_nm": (s.get("progress") or {}).get("flown_nm"),
            "progress_source": (s.get("progress") or {}).get("source"),
            "alert_count": ((s.get("changes") or {}).get("alert_count")),
        }
        for s in snapshots
    ]
