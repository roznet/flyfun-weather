"""CLI for METAR/TAF verification system.

Usage:
    python -m weatherbrief.verify collect [--flight-id ID] [--corridor NM]
    python -m weatherbrief.verify export [--format csv|json] [--output FILE]
    python -m weatherbrief.verify stats [--model MODEL] [--icao ICAO] [--source SOURCE]
    python -m weatherbrief.verify discover [--prefixes LF,ED,...]
    python -m weatherbrief.verify standalone [--once]
    python -m weatherbrief.verify digest [--period 24h|7d] [--send] [--json]
    python -m weatherbrief.verify archive run|backfill|list|verify [--table T]
    python -m weatherbrief.verify prune-raw [--retain-days N] [--apply]
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone

from dotenv import load_dotenv


def _init_db():
    """Initialize database connection."""
    load_dotenv()
    from flyfun_common.db import SessionLocal, init_shared_db, get_engine
    import weatherbrief.db.models  # noqa: F401 — register models

    engine = get_engine()
    if os.environ.get("ENVIRONMENT", "development") == "development":
        init_shared_db(engine)

    return SessionLocal


def cmd_collect(args):
    """Run a collection cycle."""
    SessionLocal = _init_db()
    db = SessionLocal()

    airports_db = os.environ.get("AIRPORTS_DB", "")
    if not airports_db:
        print("ERROR: AIRPORTS_DB environment variable not set", file=sys.stderr)
        sys.exit(1)

    try:
        if args.flight_id:
            # Collect for a specific flight
            from sqlalchemy import select
            from weatherbrief.db.models import FlightRow
            from weatherbrief.tasks.verification import (
                gather_airports,
                fetch_observations_batch,
                store_observations,
            )

            row = db.get(FlightRow, args.flight_id)
            if row is None:
                print(f"ERROR: Flight '{args.flight_id}' not found", file=sys.stderr)
                sys.exit(1)

            icao_to_flights = gather_airports(
                [row], db, airports_db, args.corridor,
            )
            if not icao_to_flights:
                print("No corridor airports found.")
                return

            unique_icaos = sorted(icao_to_flights.keys())
            print(f"Fetching METAR/TAF for {len(unique_icaos)} airports...")

            observations = fetch_observations_batch(unique_icaos, airports_db)
            inserted = store_observations(observations, icao_to_flights, db)
            db.commit()

            print(f"Stored {inserted} new observations from {len(observations)} fetched.")
        else:
            # Full collection cycle
            from weatherbrief.tasks.verification import collect_and_store

            result = collect_and_store(db, airports_db, args.corridor)
            print(f"Flights: {result['flights']}")
            print(f"Airports: {result['airports']}")
            print(f"New observations: {result['observations']}")
            print(f"Finalized: {result['finalized']}")
    finally:
        db.close()


def _export_observations(db, args) -> list[dict]:
    """Rows for ``export --table observations`` (the default)."""
    from sqlalchemy import select
    from weatherbrief.db.models import VerificationObservationRow

    stmt = select(VerificationObservationRow).order_by(
        VerificationObservationRow.observation_time.desc()
    )
    if args.icao:
        stmt = stmt.where(VerificationObservationRow.icao == args.icao.upper())
    if args.limit:
        stmt = stmt.limit(args.limit)

    return [
        {
            "id": r.id,
            "icao": r.icao,
            "observation_time": r.observation_time.isoformat() if r.observation_time else None,
            "collected_at": r.collected_at.isoformat() if r.collected_at else None,
            "flight_category": r.flight_category,
            "ceiling_ft": r.ceiling_ft,
            "visibility_m": r.visibility_m,
            "wind_dir": r.wind_dir,
            "wind_speed_kt": r.wind_speed_kt,
            "wind_gust_kt": r.wind_gust_kt,
            "temperature_c": r.temperature_c,
            "dewpoint_c": r.dewpoint_c,
            "qnh": r.qnh,
            "weather": r.weather,
            "taf_flight_category": r.taf_flight_category,
            "metar_raw": r.metar_raw,
        }
        for r in db.execute(stmt).scalars().all()
    ]


def _export_scores(db, args) -> list[dict]:
    """Rows for ``export --table scores`` — model scores joined to the obs.

    The join is the one every gust analysis does by hand: the forecast gust
    and its flag live on the score, the observed gust and mean wind on the
    observation. Exporting them together means the two conditionings (#491)
    can be computed straight from the CSV.
    """
    from sqlalchemy import select
    from weatherbrief.db.models import (
        VerificationObservationRow,
        VerificationScoreRow,
    )

    s, o = VerificationScoreRow, VerificationObservationRow
    stmt = (
        select(s, o.wind_speed_kt, o.wind_gust_kt, o.wind_dir)
        .join(o, s.observation_id == o.id)
        .order_by(s.observation_time.desc())
    )
    if args.icao:
        stmt = stmt.where(s.icao == args.icao.upper())
    if args.source:
        stmt = stmt.where(s.source == args.source)
    if args.model:
        stmt = stmt.where(s.model == args.model)
    if args.limit:
        stmt = stmt.limit(args.limit)

    return [
        {
            "id": r[0].id,
            "observation_id": r[0].observation_id,
            "icao": r[0].icao,
            "observation_time": r[0].observation_time.isoformat() if r[0].observation_time else None,
            "model": r[0].model,
            "model_init_time": r[0].model_init_time.isoformat() if r[0].model_init_time else None,
            "lead_hours": r[0].lead_hours,
            "days_out": r[0].days_out,
            "source": r[0].source,
            "obs_flight_category": r[0].obs_flight_category,
            "model_flight_category": r[0].model_flight_category,
            "category_match": r[0].category_match,
            "ceiling_delta_ft": r[0].ceiling_delta_ft,
            "visibility_delta_m": r[0].visibility_delta_m,
            "wind_speed_delta_kt": r[0].wind_speed_delta_kt,
            "wind_dir_delta_deg": r[0].wind_dir_delta_deg,
            "model_wind_gust_kt": r[0].model_wind_gust_kt,
            "wind_gust_delta_kt": r[0].wind_gust_delta_kt,
            "model_gust_flag": r[0].model_gust_flag,
            "temperature_delta_c": r[0].temperature_delta_c,
            "obs_wind_advisory": r[0].obs_wind_advisory,
            "model_wind_advisory": r[0].model_wind_advisory,
            "advisory_match": r[0].advisory_match,
            "obs_wind_speed_kt": r[1],
            "obs_wind_gust_kt": r[2],
            "obs_wind_dir": r[3],
        }
        for r in db.execute(stmt).all()
    ]


def _export_taf_scores(db, args) -> list[dict]:
    """Rows for ``export --table taf-scores`` — TAF scores joined to the obs."""
    from sqlalchemy import select
    from weatherbrief.db.models import (
        TafVerificationScoreRow,
        VerificationObservationRow,
    )

    t, o = TafVerificationScoreRow, VerificationObservationRow
    stmt = (
        select(t, o.wind_speed_kt, o.wind_gust_kt, o.taf_wind_speed_kt, o.taf_wind_gust_kt)
        .join(o, t.observation_id == o.id)
        .order_by(t.observation_time.desc())
    )
    if args.icao:
        stmt = stmt.where(t.icao == args.icao.upper())
    if args.source:
        stmt = stmt.where(t.source == args.source)
    if args.limit:
        stmt = stmt.limit(args.limit)

    return [
        {
            "id": r[0].id,
            "observation_id": r[0].observation_id,
            "icao": r[0].icao,
            "observation_time": r[0].observation_time.isoformat() if r[0].observation_time else None,
            "taf_issue_time": r[0].taf_issue_time.isoformat() if r[0].taf_issue_time else None,
            "lead_hours": r[0].lead_hours,
            "source": r[0].source,
            "obs_flight_category": r[0].obs_flight_category,
            "taf_flight_category": r[0].taf_flight_category,
            "category_match": r[0].category_match,
            "ceiling_delta_ft": r[0].ceiling_delta_ft,
            "visibility_delta_m": r[0].visibility_delta_m,
            "wind_speed_delta_kt": r[0].wind_speed_delta_kt,
            "wind_dir_delta_deg": r[0].wind_dir_delta_deg,
            "wind_gust_delta_kt": r[0].wind_gust_delta_kt,
            "obs_wind_advisory": r[0].obs_wind_advisory,
            "taf_wind_advisory": r[0].taf_wind_advisory,
            "advisory_match": r[0].advisory_match,
            "obs_wind_speed_kt": r[1],
            "obs_wind_gust_kt": r[2],
            "taf_wind_speed_kt": r[3],
            "taf_wind_gust_kt": r[4],
        }
        for r in db.execute(stmt).all()
    ]


def cmd_export(args):
    """Export verification data."""
    SessionLocal = _init_db()
    db = SessionLocal()

    table = getattr(args, "table", "observations")
    builders = {
        "observations": _export_observations,
        "scores": _export_scores,
        "taf-scores": _export_taf_scores,
    }

    try:
        records = builders[table](db, args)

        if not records:
            print(f"No {table} found.")
            return

        output = args.output or sys.stdout

        if args.format == "json":
            content = json.dumps(records, indent=2)
            if isinstance(output, str):
                with open(output, "w") as f:
                    f.write(content)
                print(f"Exported {len(records)} {table} to {output}")
            else:
                output.write(content + "\n")
        else:  # csv
            if not records:
                return
            fieldnames = list(records[0].keys())
            buf = io.StringIO() if not isinstance(output, str) else None
            f = open(output, "w", newline="") if isinstance(output, str) else buf
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(records)
            if isinstance(output, str):
                f.close()
                print(f"Exported {len(records)} {table} to {output}")
            else:
                output.write(buf.getvalue())
    finally:
        db.close()


def cmd_score(args):
    """Run scoring for completed flights."""
    SessionLocal = _init_db()
    db = SessionLocal()

    airports_db = os.environ.get("AIRPORTS_DB", "")
    if not airports_db:
        print("ERROR: AIRPORTS_DB environment variable not set", file=sys.stderr)
        sys.exit(1)

    try:
        from weatherbrief.tasks.scoring import score_completed_flights

        result = score_completed_flights(db, airports_db)
        db.commit()

        print(f"Flights scored: {result['flights_scored']}")
        print(f"Model scores:   {result['model_scores']}")
        print(f"TAF scores:     {result['taf_scores']}")
    finally:
        db.close()


def cmd_backfill(args):
    """Re-run scoring (backfill after code changes)."""
    SessionLocal = _init_db()
    db = SessionLocal()

    airports_db = os.environ.get("AIRPORTS_DB", "")
    if not airports_db:
        print("ERROR: AIRPORTS_DB environment variable not set", file=sys.stderr)
        sys.exit(1)

    try:
        from weatherbrief.tasks.scoring import backfill_scores

        result = backfill_scores(db, airports_db, flight_id=args.flight_id)
        db.commit()

        print(f"Flights scored: {result['flights_scored']}")
        print(f"Model scores:   {result['model_scores']}")
        print(f"TAF scores:     {result['taf_scores']}")
    finally:
        db.close()


def cmd_backfill_gust(args):
    """Backfill the gust columns added by migration 083 (#491).

    Two independent halves with very different reach: the TAF gust delta is
    recoverable for all history (both gusts live on the permanent observation
    row), the model gust only for the un-pruned snapshot window. Neither
    rewrites any other scored field.
    """
    SessionLocal = _init_db()
    db = SessionLocal()

    try:
        from weatherbrief.tasks.verification_gust import (
            backfill_model_gust,
            backfill_taf_gust_deltas,
        )

        do_taf = not args.model_only
        do_model = not args.taf_only

        if do_taf:
            n = backfill_taf_gust_deltas(db)
            print(f"TAF gust deltas:  {n}")
        if do_model:
            n = backfill_model_gust(db, days=args.days)
            print(f"Model gust fields: {n} (last {args.days} days)")

        print(
            "Re-roll the affected days to pick the new columns up in the "
            "rollup: python -m weatherbrief.verify rollup-daily-stats --rebuild"
        )
    finally:
        db.close()


def cmd_backfill_report_type(args):
    """Classify pre-091 observations as routine METAR or SPECI (#562).

    ``--dry-run`` surveys without writing and reports the SPECI rate — how much
    convective truth the pre-091 ingest was discarding.
    """
    SessionLocal = _init_db()
    db = SessionLocal()

    try:
        from weatherbrief.tasks.verification_report_type import (
            backfill_report_type,
            survey_report_types,
        )

        survey = survey_report_types(db, batch_size=args.batch_size)
        print("Observation report types:")
        print(survey.render())

        if args.dry_run:
            print("\nDry run — nothing written.")
            return

        pending = survey.classifiable - survey.already_classified
        if pending <= 0:
            print("\nNothing to backfill.")
            return

        n = backfill_report_type(db, batch_size=args.batch_size)
        print(f"\nClassified {n:,} rows.")
        print(
            "Scoring treats NULL and 'METAR' alike, so existing metrics are "
            "unchanged; a future re-score will now skip the SPECIs."
        )
    finally:
        db.close()


def cmd_stats(args):
    """Show verification statistics."""
    SessionLocal = _init_db()
    db = SessionLocal()

    source = getattr(args, 'source', None)

    try:
        from sqlalchemy import func, select
        from weatherbrief.db.models import (
            FlightVerificationMapRow,
            VerificationObservationRow,
            VerificationScoreRow,
        )

        obs_count = db.execute(
            select(func.count(VerificationObservationRow.id))
        ).scalar() or 0

        score_stmt = select(func.count(VerificationScoreRow.id))
        if source:
            score_stmt = score_stmt.where(VerificationScoreRow.source == source)
        score_count = db.execute(score_stmt).scalar() or 0

        airport_count = db.execute(
            select(func.count(func.distinct(VerificationObservationRow.icao)))
        ).scalar() or 0

        label = f"Verification Statistics (source={source})" if source else "Verification Database Statistics"
        print(label)
        print(f"{'─' * 40}")
        print(f"Observations:    {obs_count:>8}")
        print(f"Scores:          {score_count:>8}")
        print(f"Unique airports: {airport_count:>8}")

        if not source or source == "flight":
            flight_count = db.execute(
                select(func.count(func.distinct(FlightVerificationMapRow.flight_id)))
            ).scalar() or 0
            print(f"Flights tracked: {flight_count:>8}")

        if obs_count > 0:
            print(f"\nFlight Category Distribution:")
            cat_counts = db.execute(
                select(
                    VerificationObservationRow.flight_category,
                    func.count(VerificationObservationRow.id),
                )
                .where(VerificationObservationRow.flight_category.isnot(None))
                .group_by(VerificationObservationRow.flight_category)
                .order_by(func.count(VerificationObservationRow.id).desc())
            ).all()
            for cat, count in cat_counts:
                pct = 100 * count / obs_count
                print(f"  {cat:>5}: {count:>6} ({pct:.1f}%)")
    finally:
        db.close()


def cmd_discover(args):
    """Discover METAR-reporting airports for the watchlist."""
    load_dotenv()
    from weatherbrief.tasks.airport_watchlist import (
        DEFAULT_PREFIXES,
        discover_airports,
        get_configs_dir,
        save_watchlist,
    )

    airports_db = os.environ.get("AIRPORTS_DB", "")
    if not airports_db:
        print("ERROR: AIRPORTS_DB environment variable not set", file=sys.stderr)
        sys.exit(1)

    prefixes = args.prefixes.split(",") if args.prefixes else DEFAULT_PREFIXES
    print(f"Discovering airports for prefixes: {', '.join(prefixes)}")

    airports = discover_airports(prefixes, airports_db)
    total = sum(len(v) for v in airports.values())
    print(f"\nFound {total} airports with METARs:")
    for prefix in sorted(airports):
        print(f"  {prefix}: {len(airports[prefix])}")

    configs_dir = get_configs_dir()
    path = save_watchlist(airports, configs_dir)
    print(f"\nSaved to {path}")


def _enter_background_mode() -> None:
    """Lower scheduling priority and prefer this process as the OOM victim.

    Used by scheduler-spawned standalone cycles (issue #236): the cycle
    competes less with interactive requests for CPU, and if the cgroup ever
    OOMs mid-cycle the kernel kills this disposable child instead of the
    uvicorn parent (the OOM killer favours higher oom_score_adj). Both are
    best-effort — a restricted environment just runs at normal priority.
    """
    log = logging.getLogger(__name__)
    try:
        os.nice(10)
    except OSError:
        log.warning("Could not renice background cycle", exc_info=True)
    # oom_score_adj is a Linux-only procfs knob; skip on macOS/other dev hosts
    # rather than logging a spurious FileNotFoundError traceback every cycle.
    if sys.platform.startswith("linux"):
        try:
            with open("/proc/self/oom_score_adj", "w") as f:
                f.write("500")
        except OSError:
            log.warning("Could not set oom_score_adj", exc_info=True)


def cmd_standalone(args):
    """Run a standalone verification cycle."""
    if args.background:
        _enter_background_mode()

    _init_db()
    load_dotenv()

    airports_db = os.environ.get("AIRPORTS_DB", "")
    if not airports_db:
        print("ERROR: AIRPORTS_DB environment variable not set", file=sys.stderr)
        sys.exit(1)

    from weatherbrief.tasks.airport_watchlist import get_configs_dir, load_watchlist_with_coords

    try:
        watchlist = load_watchlist_with_coords(get_configs_dir(), airports_db)
        print(f"Loaded {len(watchlist)} airports from watchlist")
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    from weatherbrief.tasks.standalone_verification import (
        run_post_cycle_tasks,
        run_standalone_cycle,
    )

    from weatherbrief.fetch.grib import shutdown_decode_pool

    try:
        # Only EU is onboarded, so the CLI always runs the EU cycle. Screen the
        # one region a user might plausibly type (--region us) with a clean error
        # rather than the raw ValueError run_standalone_cycle would raise; 'eu',
        # 'all' and the default all run the EU cycle. run_standalone_cycle guards
        # the region itself, so this is a friendlier front door, not the only gate.
        if args.region == "us":
            print(
                "ERROR: --region us is not supported yet — the US watchlist and "
                "model set are not onboarded, so a us cycle would store EU data "
                "mislabeled as us. Use --region eu (the default).",
                file=sys.stderr,
            )
            sys.exit(1)
        result = run_standalone_cycle(
            watchlist, airports_db,
            fetch_forecasts=not args.light,
            score_observations=not args.forecast_only,
            # Pool the ~56K sounding analyses (#448 PR B): the CLI owns a
            # disposable process, so its pool can't contend with the web
            # app's. The scheduler's in-process fallback keeps this False.
            pool_soundings=True,
            region="eu",  # only EU onboarded (validated above + in run_standalone_cycle)
        )
        print(f"\nStandalone verification cycle complete:")
        print(f"  Models fetched: {result.get('models_fetched', 0)}")
        print(f"  Snapshots stored: {result.get('snapshots_stored', 0)}")
        print(f"  Observations stored: {result.get('observations_stored', 0)}")
        print(f"  Scores created: {result.get('scores_created', 0)}")
        print(f"  Duration: {result.get('duration_ms', 0)}ms")

        # Drop the pool BEFORE post-cycle tasks: the cache rebuild never
        # dispatches, so keeping 2+ workers (MetPy/cfgrib imports + accrued
        # RSS, recycling disabled) resident through it would raise peak
        # memory exactly while the rebuild allocates. Graceful here — the
        # workers are idle; the finally below stays as the failure-path net.
        shutdown_decode_pool(wait=True, drain_dispatcher=True)

        if args.with_rollup:
            t_post = time.monotonic()
            run_post_cycle_tasks(airports_db, result["cycle_type"])
            post_ms = int((time.monotonic() - t_post) * 1000)
            # The cycle Duration above excludes post-cycle work, which
            # historically hid a ~40-min cache rebuild (#448) — print the
            # full picture.
            print(f"  Post-cycle tasks (rollup + cache rebuild): {post_ms}ms")
            print(f"  Total: {result.get('duration_ms', 0) + post_ms}ms")

        if args.emit_artifact:
            # --emit-artifact pairs naturally with --forecast-only (a fetch
            # cycle produces fresh snapshots to export). With --light there is
            # no fresh fetch, so we warn rather than hard-fail — exporting the
            # existing DB is a valid (if unusual) re-ship of the last cycle.
            if args.light:
                print("  WARNING: --emit-artifact with --light exports whatever "
                      "snapshots are already in the DB (no fresh fetch).")
            from flyfun_common.db import SessionLocal
            from weatherbrief.tasks.snapshot_artifact import export_snapshots

            emit_db = SessionLocal()
            try:
                manifest = export_snapshots(
                    emit_db, args.emit_artifact,
                    region=args.region,
                    wall_time_ms=result.get("duration_ms", 0),
                )
            finally:
                emit_db.close()
            print(f"\nArtifact written: {args.emit_artifact}")
            print(f"  Region: {manifest.region or 'all'}")
            print(f"  Rows: {manifest.row_count}")
            print(f"  Models: {', '.join(sorted(manifest.models)) or 'none'}")
            print(f"  Checksum: {manifest.checksum[:12]}…")
    finally:
        # Tidy teardown of the child's sounding/decode pool (#448 PR B) —
        # in a finally because the failure path needs it MOST: an uncaught
        # exception would otherwise reach interpreter exit, where
        # concurrent.futures' atexit handler does a *blocking* join of the
        # workers, i.e. exactly the wedged-child hang this teardown exists
        # to avoid. force=True actually KILLs wedged workers (wait=False
        # alone only abandons them, and the atexit join would still hang on
        # a worker stuck in native code) — including orphans from a pool the
        # dispatcher's timeout recovery replaced mid-cycle. The child is
        # disposable: any surviving worker is useless by definition.
        # Idempotent — a no-op on the happy path, where the graceful
        # shutdown above already ran.
        try:
            shutdown_decode_pool(wait=False, drain_dispatcher=True, force=True)
        except Exception:
            pass


def cmd_ingest_artifact(args):
    """Import a portable snapshot artifact into this DB (P3)."""
    _init_db()
    load_dotenv()

    from flyfun_common.db import SessionLocal
    from weatherbrief.tasks.snapshot_artifact import (
        ArtifactValidationError,
        import_snapshots,
    )

    db = SessionLocal()
    try:
        result = import_snapshots(
            db, args.path,
            verify_checksum=not args.no_verify_checksum,
        )
    except ArtifactValidationError as e:
        print(f"ERROR: artifact rejected — {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        db.close()

    m = result.manifest
    print(f"Ingested artifact: {args.path}")
    print(f"  Source host: {m.source_host}  generated: {m.generated_at}")
    print(f"  Region: {m.region or 'all'}  models: {', '.join(sorted(m.models)) or 'none'}")
    print(f"  Rows: total={result.rows_total} inserted={result.rows_inserted} "
          f"skipped(existing)={result.rows_skipped}")


def cmd_rebuild_cache(args):
    """Rebuild the verification/forecast map cache."""
    _init_db()
    load_dotenv()

    airports_db = os.environ.get("AIRPORTS_DB", "")
    if not airports_db:
        print("ERROR: AIRPORTS_DB environment variable not set", file=sys.stderr)
        sys.exit(1)

    from flyfun_common.db import SessionLocal

    from weatherbrief.tasks.cache_builder import rebuild_all

    db = SessionLocal()
    try:
        result = rebuild_all(db, airports_db)
        print("Cache rebuild complete:")
        print(f"  Stats entries: {result['stats']}")
        print(f"  Bias leaderboard entries: {result['bias_leaderboard']}")
        print(f"  Forecast map entries: {result['forecast_map']}")
        print(f"  Duration: {result['duration_ms']}ms")
    finally:
        db.close()


def cmd_rollup_summary(args):
    """Roll up raw observations into airport_monthly_summary / airport_daily_summary."""
    from datetime import date as date_cls, datetime as dt, timezone

    from flyfun_common.db import SessionLocal

    from weatherbrief.tasks.airport_summary import (
        rebuild_all_days,
        rollup_all_complete_days,
        rollup_all_complete_months,
        rollup_day,
        rollup_month,
    )

    _init_db()
    load_dotenv()

    db = SessionLocal()
    try:
        if args.rebuild:
            # Used at deploy time after a migration that adds a column to
            # airport_daily_summary (e.g. migration 057's n_category_changes):
            # existing rows keep the column default until re-rolled.
            n = rebuild_all_days(db)
            db.commit()
            print(f"Re-rolled {n} existing airport-days.")
            return

        if args.all or (not args.month and not args.day):
            n_months = rollup_all_complete_months(db)
            n_days = rollup_all_complete_days(db)
            db.commit()
            print(f"Rolled up {n_months} airport-months and {n_days} airport-days.")
            return

        if args.month:
            try:
                year, month = args.month.split("-")
                month_start = dt(int(year), int(month), 1, tzinfo=timezone.utc)
            except (ValueError, AttributeError):
                print(f"ERROR: invalid --month {args.month!r} (expect YYYY-MM)",
                      file=sys.stderr)
                sys.exit(1)
            n = rollup_month(db, month_start)
            db.commit()
            print(f"Rolled up {n} airport-months for {args.month}.")

        if args.day:
            try:
                d = date_cls.fromisoformat(args.day)
            except ValueError:
                print(f"ERROR: invalid --day {args.day!r} (expect YYYY-MM-DD)",
                      file=sys.stderr)
                sys.exit(1)
            n = rollup_day(db, d)
            db.commit()
            print(f"Rolled up {n} airport-days for {args.day}.")
    finally:
        db.close()


def cmd_rollup_daily_stats(args):
    """Roll up raw verification_scores into verification_daily_stats.

    Used at deploy time to backfill existing standalone data. The scheduler
    keeps this up to date after each cycle, but this command is safe to
    re-run any time (idempotent DELETE+INSERT per day).
    """
    from datetime import date as date_cls

    from flyfun_common.db import SessionLocal

    from weatherbrief.tasks.verification_daily_rollup import (
        rebuild_all_days,
        rollup_all_complete_days,
        rollup_day,
    )

    _init_db()
    load_dotenv()

    db = SessionLocal()
    try:
        if args.day:
            try:
                d = date_cls.fromisoformat(args.day)
            except ValueError:
                print(
                    f"ERROR: invalid --day {args.day!r} (expect YYYY-MM-DD)",
                    file=sys.stderr,
                )
                sys.exit(1)
            n = rollup_day(db, d)
            db.commit()
            print(f"Rolled up {n} groups for {args.day}.")
            return

        if args.rebuild:
            n = rebuild_all_days(db)
            db.commit()
            print(f"Re-rolled {n} groups across existing days.")
            return

        n = rollup_all_complete_days(db)
        db.commit()
        print(f"Rolled up {n} groups across pending days.")
    finally:
        db.close()


def cmd_rollup_monthly_stats(args):
    """Roll completed months into verification_monthly_stats (#522 Phase 4).

    Reads ``verification_daily_stats``, so run ``rollup-daily-stats`` first if
    daily coverage is incomplete — a month is the sum of its days and will
    silently under-report if some of them were never rolled.
    """
    from datetime import datetime as dt

    from flyfun_common.db import SessionLocal

    from weatherbrief.tasks.verification_rollup import (
        prune_daily_stats,
        rebuild_all_months,
        rollup_month,
        run_monthly_rollup,
    )

    _init_db()
    load_dotenv()

    db = SessionLocal()
    try:
        if args.prune_daily is not None:
            n = prune_daily_stats(db, retain_months=args.prune_daily)
            db.commit()
            print(f"Pruned {n} verification_daily_stats rows.")
            return

        if args.month:
            try:
                year, month = args.month.split("-")
                month_start = dt(int(year), int(month), 1, tzinfo=timezone.utc)
            except (ValueError, AttributeError):
                print(f"ERROR: invalid --month {args.month!r} (expect YYYY-MM)",
                      file=sys.stderr)
                sys.exit(1)
            n = rollup_month(db, month_start)
            db.commit()
            print(f"Rolled up {n} rows for {args.month}.")
            return

        if args.rebuild:
            n = rebuild_all_months(db)
            db.commit()
            print(f"Re-rolled {n} rows across existing months.")
            return

        n = run_monthly_rollup(db)
        db.commit()
        print(f"Rolled up {n} rows across pending months.")
    finally:
        db.close()


def cmd_archive(args):
    """Write, backfill, list or verify the Parquet archive (#522 Phase 2)."""
    from flyfun_common.db import SessionLocal

    from weatherbrief.tasks.archive import (
        ARCHIVE_SPECS,
        archive_root,
        list_manifests,
        pending_periods,
        run_archive,
        verify_archives,
    )

    _init_db()
    load_dotenv()

    tables = tuple(args.table) if getattr(args, "table", None) else None
    if tables:
        unknown = [t for t in tables if t not in ARCHIVE_SPECS]
        if unknown:
            print(
                f"ERROR: unknown table(s) {', '.join(unknown)}; "
                f"expected one of {', '.join(ARCHIVE_SPECS)}",
                file=sys.stderr,
            )
            sys.exit(1)

    db = SessionLocal()
    try:
        if args.action in ("run", "backfill"):
            full = args.action == "backfill"
            if args.dry_run:
                print(f"Archive root: {archive_root()}")
                for name in (tables or tuple(ARCHIVE_SPECS)):
                    periods = pending_periods(db, name, full=full)
                    shown = ", ".join(periods[:6])
                    if len(periods) > 6:
                        shown += ", ..."
                    print(f"  {name}: {len(periods)} pending"
                          + (f" ({shown})" if periods else ""))
                return
            result = run_archive(db, full=full, tables=tables)
            db.commit()
            for name, periods in result["archived"].items():
                print(f"  {name}: {len(periods)} archived"
                      + (f" ({periods[0]}..{periods[-1]})" if periods else ""))
            if result["errors"]:
                print(f"ERRORS: {result['errors']} (see log)", file=sys.stderr)
                sys.exit(1)
            return

        if args.action == "list":
            rows = list_manifests(db, tables[0] if tables and len(tables) == 1 else None)
            if not rows:
                print("No archive manifests recorded.")
                return
            print(f"{'table':<14} {'period':<11} {'rows':>10}  file")
            for m in rows:
                print(f"{m.table_name:<14} {m.period:<11} {m.row_count:>10}  {m.file_path}")
            return

        if args.action == "verify":
            report = verify_archives(
                db,
                table_name=tables[0] if tables and len(tables) == 1 else None,
                check_counts=not args.skip_counts,
            )
            bad = [r for r in report if not r["ok"]]
            for r in report:
                mark = "ok " if r["ok"] else "BAD"
                print(f"{mark} {r['table']:<14} {r['period']:<11} "
                      f"{r['rows']:>10}  {r['problem']}")
            print(f"\n{len(report) - len(bad)}/{len(report)} archives verified.")
            if bad:
                sys.exit(1)
    finally:
        db.close()


def cmd_prune_raw(args):
    """Report or apply the raw-verification prune (#522 Phase 3).

    Without ``--apply`` this only reports which months pass both gates — the
    safe way to validate a retention setting before any row is deleted.
    """
    from datetime import timedelta

    from flyfun_common.db import SessionLocal

    from weatherbrief.tasks.retention import prunable_months, prune_raw_observations
    from weatherbrief.tasks.verification_tiering import (
        raw_retention_days,
        raw_retention_disabled,
    )

    _init_db()
    load_dotenv()

    retain = args.retain_days if args.retain_days is not None else raw_retention_days()
    db = SessionLocal()
    try:
        if raw_retention_disabled(retain):
            print(
                f"Raw retention is disabled (retain_days={retain}). "
                "Set VERIFICATION_RAW_RETENTION_DAYS=180 or pass --retain-days."
            )
            return

        cutoff = datetime.now(timezone.utc) - timedelta(days=retain)
        safe, blocked = prunable_months(db, cutoff)
        print(f"retain_days={retain}  cutoff={cutoff.date().isoformat()}")
        print(f"Prunable months: {', '.join(f'{y}-{m:02d}' for y, m in safe) or 'none'}")
        for reason in blocked:
            print(f"  BLOCKED: {reason}")

        if not args.apply:
            print("\nDry run — pass --apply to delete.")
            return

        result = prune_raw_observations(db, retain_days=retain)
        db.commit()
        print(
            f"Pruned obs={result['observations']} scores={result['scores']} "
            f"taf={result['taf_scores']}"
        )
    finally:
        db.close()


def cmd_digest(args):
    """Preview or send the admin digest."""
    from datetime import timedelta

    _init_db()
    from flyfun_common.db import SessionLocal
    from weatherbrief.tasks.admin_digest_stats import get_admin_digest_data

    hours = {"24h": 24, "7d": 168}[args.period]
    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=hours)
    period_label = f"{args.period} — {now.strftime('%Y-%m-%d %H:%M')}Z"
    base_url = os.environ.get("WEATHERBRIEF_BASE_URL", "https://weather.flyfun.aero")

    db = SessionLocal()
    try:
        data = get_admin_digest_data(db, since, now, period_label=period_label, base_url=base_url)

        if args.json:
            print(json.dumps(data.model_dump(mode="json"), indent=2))
        else:
            # Print plain text version
            from weatherbrief.notify.admin_digest_email import _build_plain
            print(_build_plain(data))

        if args.send:
            from weatherbrief.notify.admin_digest_email import send_admin_digest
            from weatherbrief.notify.admin_email import get_admin_emails

            admin_emails = get_admin_emails()
            if not admin_emails:
                print("\nNo ADMIN_EMAILS configured, cannot send.")
                sys.exit(1)
            send_admin_digest(admin_emails, data)
            print(f"\nSent to: {', '.join(admin_emails)}")
    finally:
        db.close()


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s:%(name)s:%(message)s",
    )

    parser = argparse.ArgumentParser(
        prog="weatherbrief.verify",
        description="METAR/TAF verification system",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # collect
    p_collect = subparsers.add_parser("collect", help="Run a collection cycle")
    p_collect.add_argument("--flight-id", help="Collect for a specific flight")
    p_collect.add_argument(
        "--corridor", type=float, default=15.0,
        help="Corridor width in NM (default: 15)",
    )

    # export
    p_export = subparsers.add_parser("export", help="Export verification data")
    p_export.add_argument(
        "--format", choices=["csv", "json"], default="json",
        help="Output format (default: json)",
    )
    p_export.add_argument("--output", "-o", help="Output file (default: stdout)")
    p_export.add_argument("--icao", help="Filter by ICAO code")
    p_export.add_argument("--limit", type=int, help="Limit number of records")
    p_export.add_argument(
        "--table", choices=["observations", "scores", "taf-scores"],
        default="observations",
        help="What to export (default: observations). 'scores' and "
             "'taf-scores' join the observation row, so forecast gust and "
             "observed gust land in the same record.",
    )
    p_export.add_argument(
        "--source", choices=["flight", "standalone"],
        help="Filter scores by source (scores / taf-scores only)",
    )
    p_export.add_argument(
        "--model", help="Filter scores by model, e.g. gfs (scores only)",
    )

    # score
    p_score = subparsers.add_parser("score", help="Score completed flights")

    # backfill
    p_backfill = subparsers.add_parser("backfill", help="Re-run scoring (backfill)")
    p_backfill.add_argument("--flight-id", help="Backfill a specific flight")

    # backfill-gust
    p_backfill_gust = subparsers.add_parser(
        "backfill-gust",
        help="Backfill gust columns on existing scores (#491)",
    )
    gust_scope = p_backfill_gust.add_mutually_exclusive_group()
    gust_scope.add_argument(
        "--taf-only", action="store_true",
        help="Only the TAF gust delta (fully backfillable from stored obs)",
    )
    gust_scope.add_argument(
        "--model-only", action="store_true",
        help="Only the model gust fields (limited to the un-pruned snapshot "
             "window — see --days)",
    )
    p_backfill_gust.add_argument(
        "--days", type=int, default=10,
        help="How far back to look for model gust, in days (default: 10 — "
             "airport_forecast_snapshots are pruned beyond that, so older "
             "scores have no recoverable forecast gust)",
    )

    p_report_type = subparsers.add_parser(
        "backfill-report-type",
        help="Classify observations as METAR or SPECI (migration 091, #562)",
    )
    p_report_type.add_argument(
        "--dry-run", action="store_true",
        help="Survey only — report the SPECI rate without writing",
    )
    p_report_type.add_argument(
        "--batch-size", type=int, default=20_000,
        help="Primary-key window per batch (default 20000)",
    )

    # stats
    p_stats = subparsers.add_parser("stats", help="Show verification statistics")
    p_stats.add_argument(
        "--source", choices=["flight", "standalone"],
        help="Filter by source (default: all)",
    )

    # discover
    p_discover = subparsers.add_parser(
        "discover",
        help="Discover METAR-reporting airports for watchlist",
    )
    p_discover.add_argument(
        "--prefixes",
        help="Comma-separated ICAO prefixes (default: LF,ED,EG,EH,EB,LS,LO)",
    )

    # standalone
    p_standalone = subparsers.add_parser(
        "standalone",
        help="Run a standalone verification cycle",
    )
    p_standalone.add_argument(
        "--once", action="store_true", default=True,
        help="Run a single cycle (default)",
    )
    standalone_mode = p_standalone.add_mutually_exclusive_group()
    standalone_mode.add_argument(
        "--light", action="store_true",
        help="Light cycle: observations + scoring only, skip forecast fetch",
    )
    standalone_mode.add_argument(
        "--forecast-only", action="store_true",
        help="Forecast cycle: fetch + store snapshots only, skip scoring "
             "(what the scheduler's 07/19 UTC fetch loop runs)",
    )
    p_standalone.add_argument(
        "--with-rollup", action="store_true",
        help="After the cycle, run the daily-stats rollup + dashboard cache "
             "rebuild (the scheduler loops' post-cycle work)",
    )
    p_standalone.add_argument(
        "--background", action="store_true",
        help="Renice and raise oom_score_adj — set by the scheduler when it "
             "runs the cycle as an isolated subprocess",
    )
    p_standalone.add_argument(
        "--emit-artifact", metavar="PATH",
        help="After the cycle, export the fresh snapshots to a portable SQLite "
             "artifact at PATH (P2). Off-box compute ships this to a serving "
             "replica, which imports it with `ingest-artifact`.",
    )
    p_standalone.add_argument(
        "--region", choices=["eu", "us", "all"], default="all",
        help="Region scope: filters the emitted artifact and (for the cycle) "
             "tags stored rows. Only 'eu' is onboarded — 'us' errors, 'all'/'eu' "
             "run the EU cycle. Default: all.",
    )

    # ingest-artifact
    p_ingest = subparsers.add_parser(
        "ingest-artifact",
        help="Import a portable snapshot artifact into this DB (P3)",
    )
    p_ingest.add_argument("path", help="Path to the SQLite artifact to import")
    p_ingest.add_argument(
        "--no-verify-checksum", action="store_true",
        help="Skip the content-checksum check (row-count is still validated). "
             "For debugging only — the checksum is what rejects a corrupt "
             "artifact before any row is written.",
    )

    # rebuild-cache
    subparsers.add_parser(
        "rebuild-cache",
        help="Rebuild verification/forecast map cache",
    )

    # rollup-summary
    p_rollup_summary = subparsers.add_parser(
        "rollup-summary",
        help="Roll up obs into airport_monthly/daily_summary tables",
    )
    p_rollup_summary.add_argument(
        "--month",
        help="Roll up a specific month (YYYY-MM). Default: every completed month.",
    )
    p_rollup_summary.add_argument(
        "--day",
        help="Roll up a specific UTC date (YYYY-MM-DD). Default: every completed day.",
    )
    p_rollup_summary.add_argument(
        "--all", action="store_true",
        help="Roll up every completed period for both monthly and daily tables.",
    )
    p_rollup_summary.add_argument(
        "--rebuild", action="store_true",
        help=(
            "Re-roll every existing airport-day from raw (idempotent "
            "DELETE+INSERT). Use after a migration that adds a new "
            "airport_daily_summary column — existing rows keep the default "
            "until re-rolled. Mutually exclusive with --all/--month/--day."
        ),
    )

    # rollup-daily-stats
    p_rollup_daily = subparsers.add_parser(
        "rollup-daily-stats",
        help="Roll up verification_scores into verification_daily_stats",
    )
    p_rollup_daily.add_argument(
        "--day",
        help="Roll up a specific UTC date (YYYY-MM-DD). Default: every pending day.",
    )
    p_rollup_daily.add_argument(
        "--rebuild", action="store_true",
        help="Re-roll every existing date (use after schema/aggregation changes).",
    )

    # rollup-monthly-stats
    p_rollup_monthly = subparsers.add_parser(
        "rollup-monthly-stats",
        help="Roll verification_daily_stats into verification_monthly_stats",
    )
    p_rollup_monthly.add_argument(
        "--month",
        help="Roll a specific month (YYYY-MM). Default: every completed month.",
    )
    p_rollup_monthly.add_argument(
        "--rebuild", action="store_true",
        help="Re-roll every month already in the monthly table.",
    )
    p_rollup_monthly.add_argument(
        "--prune-daily", type=int, default=None, metavar="MONTHS",
        help=(
            "Phase 4 follow-up: delete verification_daily_stats rows older "
            "than MONTHS. Only prunes months that already have a monthly "
            "rollup. Overrides VERIFICATION_DAILY_STATS_RETENTION_MONTHS."
        ),
    )

    # archive
    p_archive = subparsers.add_parser(
        "archive",
        help="Parquet archive of the raw verification tables (#522)",
    )
    p_archive.add_argument(
        "action", choices=["run", "backfill", "list", "verify"],
        help=(
            "run: archive newly-final periods. backfill: walk all completed "
            "history, filling gaps. list: show recorded manifests. "
            "verify: re-check sha256 + row counts."
        ),
    )
    p_archive.add_argument(
        "--table", action="append",
        help=(
            "Limit to one table (repeatable): observations, scores, "
            "taf_scores, snapshots. Default: all."
        ),
    )
    p_archive.add_argument(
        "--dry-run", action="store_true",
        help="run/backfill only: list the periods that would be written.",
    )
    p_archive.add_argument(
        "--skip-counts", action="store_true",
        help="verify only: check files and checksums but not live row counts.",
    )

    # prune-raw
    p_prune = subparsers.add_parser(
        "prune-raw",
        help="Report (or apply) the raw verification prune (#522 Phase 3)",
    )
    p_prune.add_argument(
        "--retain-days", type=int, default=None,
        help="Override VERIFICATION_RAW_RETENTION_DAYS for this run.",
    )
    p_prune.add_argument(
        "--apply", action="store_true",
        help="Actually delete. Without it this only reports what would go.",
    )

    # digest
    p_digest = subparsers.add_parser(
        "digest",
        help="Preview or send the admin digest email",
    )
    p_digest.add_argument(
        "--period", choices=["24h", "7d"], default="24h",
        help="Time period (default: 24h)",
    )
    p_digest.add_argument(
        "--send", action="store_true",
        help="Actually send the email to ADMIN_EMAILS",
    )
    p_digest.add_argument(
        "--json", action="store_true",
        help="Output as JSON instead of plain text",
    )

    args = parser.parse_args()

    if args.command == "collect":
        cmd_collect(args)
    elif args.command == "export":
        cmd_export(args)
    elif args.command == "score":
        cmd_score(args)
    elif args.command == "backfill":
        cmd_backfill(args)
    elif args.command == "backfill-gust":
        cmd_backfill_gust(args)
    elif args.command == "backfill-report-type":
        cmd_backfill_report_type(args)
    elif args.command == "stats":
        cmd_stats(args)
    elif args.command == "discover":
        cmd_discover(args)
    elif args.command == "standalone":
        cmd_standalone(args)
    elif args.command == "ingest-artifact":
        cmd_ingest_artifact(args)
    elif args.command == "rebuild-cache":
        cmd_rebuild_cache(args)
    elif args.command == "rollup-summary":
        cmd_rollup_summary(args)
    elif args.command == "rollup-daily-stats":
        cmd_rollup_daily_stats(args)
    elif args.command == "rollup-monthly-stats":
        cmd_rollup_monthly_stats(args)
    elif args.command == "archive":
        cmd_archive(args)
    elif args.command == "prune-raw":
        cmd_prune_raw(args)
    elif args.command == "digest":
        cmd_digest(args)


if __name__ == "__main__":
    main()
