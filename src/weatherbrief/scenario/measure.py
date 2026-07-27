"""Idle-window measurement harness for overnight scenario briefings (issue #225).

Phase 0: establish the *real* time / resource / storage budget for the overnight
"scenario" feature (#226) BEFORE building the scheduler, DB model, or UI. This is
measurement only — it runs ``execute_briefing`` directly with the scenario option
set and reports numbers. No scheduler loop, no DB rows, no UI.

What it measures, per leg-slot and as batch totals:

  * wall-time, **cold-cache vs warm-cache** — the same leg-slots are run twice
    (pass 1 = cold, pass 2 = warm) so the GRIB/forecast cache-reuse "cluster
    effect" that the production design leans on is quantified directly.
  * peak RSS under serial ``DecodePriority.BACKGROUND`` load — read from
    ``resource.ru_maxrss`` (process-lifetime peak), NOT cgroup, which pins near
    the limit from mmap cache (memory note).
  * bytes-on-disk per pack, with the dominant artifacts called out (expect
    ``cross_section.json`` + ``sounding_profiles.json.gz``).
  * Open-Meteo API call count (``BriefingResult.usage.open_meteo_calls``).
  * GRIB on-disk cache hit/miss — tapped non-invasively from the
    ``weatherbrief.fetch.grib.cache`` DEBUG logs (no pipeline changes).

Scenario option set (mirrors the overnight design and the refresh path in
``api/packs.py``): ``generate_llm_digest=False``, ``fetch_gramet=False``,
``generate_skewt=False``, ``enrich_grib=True``. Runs serially (concurrency 1) at
``DecodePriority.BACKGROUND`` to mirror the safest overnight mode.

Usage::

    source venv/bin/activate
    python -m weatherbrief.scenario.measure                 # full run, 2 passes
    python -m weatherbrief.scenario.measure --limit 1       # smoke test, 1 leg
    python -m weatherbrief.scenario.measure --passes 1      # cold only, faster
    python -m weatherbrief.scenario.measure --keep          # leave packs for inspection
    python -m weatherbrief.scenario.measure --clear-grib-cache  # truly-cold disk

Notes:
  * Routes are the real ones from local ``flyfun.db`` (user ``dev-user-001``),
    hard-coded here — phase 0 deliberately avoids a config schema; #226 will add
    ``configs/scenarios.json``.
  * Profiles ("Default" / "IFR FIKI") are carried as labels only. They tune
    advisory *thresholds*, not pipeline timing/RSS/disk, so the budget numbers
    this harness produces are profile-independent. Loading profile→advisory-param
    mapping is lifecycle-layer work deferred to #226.
  * Slots are expressed as relative rules (next Fri/Sat/Sun at HH:MMZ) and
    expanded to absolute UTC at run time, so departures are always in the future
    (live-forecast mode, not historical). A horizon guard skips slots beyond the
    forecast horizon.
"""

from __future__ import annotations

import argparse
import logging
import os
import resource
import shutil
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import perf_counter

logger = logging.getLogger("weatherbrief.scenario.measure")

# --------------------------------------------------------------------------- #
# Curated trips — real routes from local flyfun.db (user dev-user-001).
# ~12 leg-slots/night across 3 trips. Hard-coded for phase 0 (see module docstring).
# --------------------------------------------------------------------------- #

_WEEKDAY = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}


@dataclass(frozen=True)
class Leg:
    """One leg of a scenario trip: a route at an altitude under a profile."""

    role: str            # "out" | "back"
    route: str           # space-separated waypoint codes
    altitude_ft: int
    profile: str         # label only (see module docstring)


@dataclass(frozen=True)
class Slot:
    """A candidate departure: a relative day rule + time, per role."""

    label: str
    day: str             # weekday name, lower-case
    time: str            # "HH:MMZ"


@dataclass(frozen=True)
class Trip:
    id: str
    title: str
    legs: list[Leg]
    # Each entry pairs a slot with the leg role it applies to.
    slots: list[tuple[str, Slot]]   # (role, slot)


def _curated_trips() -> list[Trip]:
    return [
        Trip(
            id="egtf-lfat-daytrip",
            title="EGTF ↔ LFAT day trip",
            legs=[
                Leg("out", "EGTF BIG LFAT", 8000, "Default"),
                Leg("back", "LFAT EGMD BIG OCK EGTF", 3000, "IFR FIKI"),
            ],
            slots=[
                ("out", Slot("Sat 07Z", "saturday", "07:00Z")),
                ("back", Slot("Sat 14Z", "saturday", "14:00Z")),
                ("out", Slot("Sun 07Z", "sunday", "07:00Z")),
                ("back", Slot("Sun 14Z", "sunday", "14:00Z")),
            ],
        ),
        Trip(
            id="egtf-lsgs-weekend",
            title="EGTF ↔ LSGS weekend",
            legs=[
                Leg("out", "EGTF BIG BILGO ERTIP LSGS", 11000, "Default"),
                Leg("back", "LSGS DJL SOMDA VATRI BILGO XORBI ABB ELDAX WAFFU GWC EGTF",
                    10000, "IFR FIKI"),
            ],
            slots=[
                ("out", Slot("Fri 07Z", "friday", "07:00Z")),
                ("out", Slot("Sat 07Z", "saturday", "07:00Z")),
                ("back", Slot("Sun 07Z", "sunday", "07:00Z")),
                ("back", Slot("Sun 13Z", "sunday", "13:00Z")),
            ],
        ),
        Trip(
            id="egtf-lfmd-weekend",
            title="EGTF ↔ LFMD weekend",
            legs=[
                Leg("out", "EGTF GWC SITET KOVAK DOMOD MTL LFMD", 17000, "Default"),
                Leg("back", "LFMD LFLX EGTF", 8000, "Default"),
            ],
            slots=[
                ("out", Slot("Fri 07Z", "friday", "07:00Z")),
                ("out", Slot("Sat 07Z", "saturday", "07:00Z")),
                ("back", Slot("Sun 07Z", "sunday", "07:00Z")),
                ("back", Slot("Sun 13Z", "sunday", "13:00Z")),
            ],
        ),
    ]


@dataclass(frozen=True)
class LegSlot:
    """A fully-expanded unit of work: one leg at one absolute departure time."""

    trip_id: str
    trip_title: str
    leg: Leg
    slot: Slot
    departure: datetime  # aware UTC

    @property
    def label(self) -> str:
        return f"{self.trip_id} / {self.leg.role} / {self.slot.label}"


def _next_weekday(now: datetime, weekday: str, hh: int, mm: int) -> datetime:
    """Next occurrence (> now) of *weekday* at HH:MM UTC."""
    target = _WEEKDAY[weekday]
    days_ahead = (target - now.weekday()) % 7
    candidate = now.replace(hour=hh, minute=mm, second=0, microsecond=0) + timedelta(days=days_ahead)
    if candidate <= now:
        candidate += timedelta(days=7)
    return candidate


def _parse_zulu(t: str) -> tuple[int, int]:
    hhmm = t.rstrip("Zz")
    hh, mm = hhmm.split(":")
    return int(hh), int(mm)


def expand_leg_slots(trips: list[Trip], now: datetime, horizon_hours: float) -> tuple[list[LegSlot], list[str]]:
    """Expand relative slots to absolute UTC; drop beyond-horizon slots.

    Returns ``(leg_slots, skipped)`` where *skipped* holds human-readable notes
    for slots that fall beyond the forecast horizon (no silent truncation).
    """
    out: list[LegSlot] = []
    skipped: list[str] = []
    horizon = now + timedelta(hours=horizon_hours)
    for trip in trips:
        by_role: dict[str, Leg] = {leg.role: leg for leg in trip.legs}
        for role, slot in trip.slots:
            leg = by_role.get(role)
            if leg is None:
                continue
            hh, mm = _parse_zulu(slot.time)
            dep = _next_weekday(now, slot.day, hh, mm)
            ls = LegSlot(trip.id, trip.title, leg, slot, dep)
            if dep > horizon:
                hours_out = (dep - now).total_seconds() / 3600
                skipped.append(
                    f"{ls.label}: {dep:%Y-%m-%d %H:%MZ} is {hours_out:.0f}h out, "
                    f"beyond {horizon_hours:.0f}h horizon"
                )
                continue
            out.append(ls)
    return out, skipped


# --------------------------------------------------------------------------- #
# Resource measurement helpers.
# --------------------------------------------------------------------------- #

def _peak_rss_mb() -> float:
    """Process-lifetime peak RSS in MB (resource.ru_maxrss, unit-normalized).

    ru_maxrss is in KB on Linux, bytes on macOS.

    Local to the scenario harness. The pipeline carried a twin of this until
    #506, where it fed a per-request "peak" — something a monotonic lifetime
    mark cannot describe. Here the lifetime figure IS the question: one
    high-water for the whole measurement run. So it stays.
    """
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return rss / (1024 * 1024) if sys.platform == "darwin" else rss / 1024


class GribCacheCounter(logging.Handler):
    """Counts GRIB on-disk cache hits/writes from the cache module's DEBUG logs.

    Non-invasive: attaches to ``weatherbrief.fetch.grib.cache`` and tallies the
    existing ``"Cache hit: ..."`` and ``"Cached: ..."`` (write == miss-then-fetch)
    messages. No pipeline code changes.
    """

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.hits = 0
        self.writes = 0

    def emit(self, record: logging.LogRecord) -> None:
        msg = record.getMessage()
        if msg.startswith("Cache hit:"):
            self.hits += 1
        elif msg.startswith("Cached:"):
            self.writes += 1

    def reset(self) -> None:
        self.hits = 0
        self.writes = 0


def _dir_size_breakdown(path: Path) -> tuple[int, list[tuple[str, int]]]:
    """Total bytes under *path* and a descending (filename, bytes) breakdown."""
    files: list[tuple[str, int]] = []
    total = 0
    if path.exists():
        for f in path.rglob("*"):
            if f.is_file():
                size = f.stat().st_size
                total += size
                files.append((f.name, size))
    files.sort(key=lambda t: t[1], reverse=True)
    return total, files


# --------------------------------------------------------------------------- #
# Per-leg run.
# --------------------------------------------------------------------------- #

@dataclass
class LegMeasurement:
    leg_slot: LegSlot
    cold_seconds: float | None = None
    warm_seconds: float | None = None
    disk_bytes: int = 0
    top_artifacts: list[tuple[str, int]] = field(default_factory=list)
    open_meteo_calls: int = 0
    grib_cache_hits: int = 0
    grib_cache_writes: int = 0
    rss_after_mb: float = 0.0
    grib_init_times: dict[str, int] = field(default_factory=dict)
    waypoints_resolved: int = 0
    waypoints_rejected: int = 0
    error: str | None = None        # cold-pass failure
    warm_error: str | None = None   # warm-pass failure (cold succeeded)


def _build_options(leg_slot: LegSlot, out_dir: Path, airports_db: str | None):
    from weatherbrief.pipeline import BriefingOptions

    return BriefingOptions(
        enrich_grib=True,         # the point — full route + cross-section + advisories
        fetch_gramet=False,       # scenario option set
        generate_skewt=False,
        generate_llm_digest=False,
        output_dir=out_dir,
        user_id="dev-user-001",
        airports_db_path=airports_db,
        profile_name=leg_slot.leg.profile,  # label only
    )


def _run_one(leg_slot: LegSlot, out_dir: Path, airports_db: str | None,
             cache_counter: GribCacheCounter):
    """Run execute_briefing once for *leg_slot*, writing into *out_dir*.

    Returns ``(elapsed_seconds, result)`` or raises.
    """
    from weatherbrief.airports import resolve_waypoints
    from weatherbrief.models import RouteConfig
    from weatherbrief.pipeline import execute_briefing

    codes = leg_slot.leg.route.split()
    waypoint_objs, rejected = resolve_waypoints(codes, airports_db)
    route = RouteConfig(
        name=leg_slot.leg.route,
        waypoints=waypoint_objs,
        cruise_altitude_ft=leg_slot.leg.altitude_ft,
    )
    options = _build_options(leg_slot, out_dir, airports_db)

    cache_counter.reset()
    t0 = perf_counter()
    result = execute_briefing(route=route, departure_time=leg_slot.departure, options=options)
    elapsed = perf_counter() - t0
    return elapsed, result, len(waypoint_objs), len(rejected)


# --------------------------------------------------------------------------- #
# Reporting.
# --------------------------------------------------------------------------- #

def _human_bytes(n: int) -> str:
    f = float(n)
    for unit in ("B", "KB", "MB"):
        if f < 1024:
            return f"{f:.0f}{unit}" if unit == "B" else f"{f:.1f}{unit}"
        f /= 1024
    return f"{f:.1f}GB"


def render_report(measurements: list[LegMeasurement], skipped: list[str],
                  passes: int, started_at: datetime, total_wall: float) -> str:
    lines: list[str] = []
    lines.append("# Scenario briefing — idle-window measurement (issue #225)")
    lines.append("")
    lines.append(f"- Run started: {started_at:%Y-%m-%d %H:%M:%SZ}")
    lines.append(f"- Host: {sys.platform}, {os.cpu_count()} CPUs")
    lines.append(f"- Passes: {passes} (1=cold, 2=warm cache reuse)")
    lines.append(f"- Decode priority: BACKGROUND, serial (concurrency 1)")
    lines.append(f"- Options: enrich_grib=True, llm_digest=False, gramet=False, skewt=False")
    lines.append(f"- Peak RSS (process lifetime): **{_peak_rss_mb():.0f} MB**")
    lines.append(f"- Total harness wall-time: **{total_wall:.0f}s** ({total_wall/60:.1f} min)")
    lines.append("")

    # Per-leg table.
    lines.append("## Per leg-slot")
    lines.append("")
    hdr = ("| Leg-slot | Route | WPs | Cold s | Warm s | Δ% | Disk | OM calls | GRIB hit/wr (cold) |")
    sep = ("|---|---|---:|---:|---:|---:|---:|---:|---:|")
    lines.append(hdr)
    lines.append(sep)
    cold_sum = warm_sum = disk_sum = om_sum = 0.0
    for m in measurements:
        if m.error:
            lines.append(f"| {m.leg_slot.label} | {m.leg_slot.leg.route} | — | ERROR | — | — | — | — | {m.error[:40]} |")
            continue
        cold = m.cold_seconds or 0.0
        warm = m.warm_seconds
        delta = ""
        if warm is not None and cold:
            delta = f"{(warm - cold) / cold * 100:+.0f}%"
        cold_sum += cold
        warm_sum += warm or 0.0
        disk_sum += m.disk_bytes
        om_sum += m.open_meteo_calls
        wps = f"{m.waypoints_resolved}" + (f"(-{m.waypoints_rejected})" if m.waypoints_rejected else "")
        warm_str = "ERR" if m.warm_error else ("—" if warm is None else f"{warm:.0f}")
        lines.append(
            f"| {m.leg_slot.label} | `{m.leg_slot.leg.route}` | {wps} "
            f"| {cold:.0f} | {warm_str} | {delta or '—'} "
            f"| {_human_bytes(m.disk_bytes)} | {m.open_meteo_calls} "
            f"| {m.grib_cache_hits}/{m.grib_cache_writes} |"
        )
    n_ok = sum(1 for m in measurements if not m.error)
    warm_total_str = f"{warm_sum:.0f}" if passes >= 2 else "—"
    lines.append(
        f"| **TOTAL ({n_ok} legs)** |  |  | **{cold_sum:.0f}** | **{warm_total_str}** |  "
        f"| **{_human_bytes(int(disk_sum))}** | **{om_sum:.0f}** |  |"
    )
    lines.append("")

    # Cold vs warm batch summary.
    if passes >= 2 and n_ok:
        reuse = (cold_sum - warm_sum) / cold_sum * 100 if cold_sum else 0
        lines.append("## Cold vs warm batch")
        lines.append("")
        lines.append(f"- Cold batch wall-time: **{cold_sum:.0f}s** ({cold_sum/60:.1f} min)")
        lines.append(f"- Warm batch wall-time: **{warm_sum:.0f}s** ({warm_sum/60:.1f} min)")
        lines.append(f"- Cache-reuse speedup (cluster effect): **{reuse:.0f}%** faster warm")
        lines.append("")

    # Disk breakdown — which artifacts dominate (aggregate across legs).
    lines.append("## Disk — dominant artifacts (aggregate)")
    lines.append("")
    agg: dict[str, int] = {}
    for m in measurements:
        for name, size in m.top_artifacts:
            agg[name] = agg.get(name, 0) + size
    top = sorted(agg.items(), key=lambda t: t[1], reverse=True)[:12]
    if top:
        lines.append("| Artifact | Total bytes (all packs) |")
        lines.append("|---|---:|")
        for name, size in top:
            lines.append(f"| `{name}` | {_human_bytes(size)} |")
    avg_disk = disk_sum / n_ok if n_ok else 0
    lines.append("")
    lines.append(f"- Avg disk per pack: **{_human_bytes(int(avg_disk))}**")
    lines.append("")

    # GRIB init times observed (which run scenarios rode).
    inits: dict[str, int] = {}
    for m in measurements:
        inits.update(m.grib_init_times)
    if inits:
        lines.append("## GRIB init times observed (which run scenarios rode)")
        lines.append("")
        for model, ts in sorted(inits.items()):
            try:
                dt = datetime.fromtimestamp(ts, tz=timezone.utc)
                lines.append(f"- {model}: {dt:%Y-%m-%d %H:%MZ}")
            except (ValueError, OSError, OverflowError):
                lines.append(f"- {model}: {ts}")
        lines.append("")

    if skipped:
        lines.append("## Skipped (beyond forecast horizon)")
        lines.append("")
        for s in skipped:
            lines.append(f"- {s}")
        lines.append("")

    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Main.
# --------------------------------------------------------------------------- #

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scenario briefing measurement harness (#225)")
    parser.add_argument("--passes", type=int, default=2, choices=(1, 2),
                        help="1=cold only; 2=cold then warm (default, measures cache reuse)")
    parser.add_argument("--limit", type=int, default=None,
                        help="Only run the first N leg-slots (smoke test)")
    parser.add_argument("--keep", action="store_true",
                        help="Leave throwaway packs on disk for inspection")
    parser.add_argument("--clear-grib-cache", action="store_true",
                        help="Delete data/.cache/grib before the cold pass (truly-cold disk)")
    parser.add_argument("--horizon-hours", type=float, default=168.0,
                        help="Forecast horizon for the slot guard (default 168h = ECMWF 00/12Z)")
    parser.add_argument("--report", type=str, default=None,
                        help="Write the markdown report to this path (also printed to stdout)")
    parser.add_argument("-v", "--verbose", action="store_true", help="DEBUG logging")
    args = parser.parse_args(argv)

    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # Keep third-party noise down unless -v.
    if not args.verbose:
        logging.getLogger("weatherbrief.fetch.grib.cache").setLevel(logging.DEBUG)  # need DEBUG for counter
        logging.getLogger("urllib3").setLevel(logging.WARNING)

    from weatherbrief.fetch.grib import DecodePriority, set_decode_priority

    # Mirror the safest overnight mode: BACKGROUND priority, serial.
    set_decode_priority(DecodePriority.BACKGROUND)

    data_dir = Path(os.environ.get("DATA_DIR", "data"))
    airports_db = os.environ.get("AIRPORTS_DB")
    if not airports_db or not Path(airports_db).exists():
        logger.error("AIRPORTS_DB not set or missing (%s). Set it in .env.", airports_db)
        return 2

    if args.clear_grib_cache:
        grib_cache = data_dir / ".cache" / "grib"
        if grib_cache.exists():
            logger.warning("Clearing GRIB on-disk cache: %s", grib_cache)
            shutil.rmtree(grib_cache)

    # GRIB cache hit/write counter — attach to the cache logger.
    cache_counter = GribCacheCounter()
    cache_logger = logging.getLogger("weatherbrief.fetch.grib.cache")
    cache_logger.addHandler(cache_counter)

    now = datetime.now(timezone.utc)
    trips = _curated_trips()
    leg_slots, skipped = expand_leg_slots(trips, now, args.horizon_hours)
    if args.limit is not None:
        leg_slots = leg_slots[: args.limit]

    logger.info("Expanded %d leg-slots (%d skipped beyond horizon)", len(leg_slots), len(skipped))
    for s in skipped:
        logger.info("SKIP %s", s)

    # Throwaway output root under DATA_DIR.
    run_tag = now.strftime("%Y%m%dT%H%M%SZ")
    scratch_root = data_dir / "scenario_measure" / run_tag
    scratch_root.mkdir(parents=True, exist_ok=True)
    logger.info("Writing throwaway packs under %s", scratch_root)

    measurements = [LegMeasurement(leg_slot=ls) for ls in leg_slots]
    started = now
    harness_t0 = perf_counter()

    # Pass 1 — cold.
    for i, m in enumerate(measurements):
        ls = m.leg_slot
        out_dir = scratch_root / "cold" / f"{i:02d}_{ls.trip_id}_{ls.leg.role}_{ls.slot.day}"
        logger.info("[cold %d/%d] %s  dep=%s", i + 1, len(measurements), ls.label, ls.departure.isoformat())
        try:
            elapsed, result, n_wp, n_rej = _run_one(ls, out_dir, airports_db, cache_counter)
            m.cold_seconds = elapsed
            m.open_meteo_calls = result.usage.open_meteo_calls
            m.grib_cache_hits = cache_counter.hits
            m.grib_cache_writes = cache_counter.writes
            m.grib_init_times = dict(result.grib_init_times)
            m.waypoints_resolved = n_wp
            m.waypoints_rejected = n_rej
            m.rss_after_mb = _peak_rss_mb()
            disk, files = _dir_size_breakdown(out_dir)
            m.disk_bytes = disk
            m.top_artifacts = files[:8]
            logger.info("[cold %d/%d] done in %.0fs  disk=%s  om=%d  grib_hit/wr=%d/%d  peakRSS=%.0fMB",
                        i + 1, len(measurements), elapsed, _human_bytes(disk),
                        m.open_meteo_calls, m.grib_cache_hits, m.grib_cache_writes, m.rss_after_mb)
        except Exception as exc:  # noqa: BLE001 — measurement harness, keep going
            logger.exception("[cold %d/%d] FAILED: %s", i + 1, len(measurements), ls.label)
            m.error = f"{type(exc).__name__}: {exc}"

    # Pass 2 — warm (same leg-slots, cache now populated).
    if args.passes >= 2:
        for i, m in enumerate(measurements):
            if m.error:
                continue
            ls = m.leg_slot
            out_dir = scratch_root / "warm" / f"{i:02d}_{ls.trip_id}_{ls.leg.role}_{ls.slot.day}"
            logger.info("[warm %d/%d] %s", i + 1, len(measurements), ls.label)
            try:
                elapsed, result, _n_wp, _n_rej = _run_one(ls, out_dir, airports_db, cache_counter)
                m.warm_seconds = elapsed
                logger.info("[warm %d/%d] done in %.0fs  grib_hit/wr=%d/%d",
                            i + 1, len(measurements), elapsed, cache_counter.hits, cache_counter.writes)
            except Exception as exc:  # noqa: BLE001
                logger.exception("[warm %d/%d] FAILED: %s", i + 1, len(measurements), ls.label)
                m.warm_error = f"{type(exc).__name__}: {exc}"

    total_wall = perf_counter() - harness_t0
    cache_logger.removeHandler(cache_counter)

    report = render_report(measurements, skipped, args.passes, started, total_wall)
    print("\n" + report)

    if args.report:
        Path(args.report).write_text(report)
        logger.info("Report written to %s", args.report)

    if not args.keep:
        logger.info("Cleaning up throwaway packs under %s (use --keep to retain)", scratch_root)
        shutil.rmtree(scratch_root, ignore_errors=True)
    else:
        logger.info("Packs retained under %s", scratch_root)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
