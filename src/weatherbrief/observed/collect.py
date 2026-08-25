"""Observed-frame collector — the only component that touches the network.

Everything downstream reads the local frame store, which is what makes "zero
network fetches inside a briefing request" true rather than aspirational.

Two very different acquisition shapes:

* **OPERA** keys are fully deterministic — ``s3://openradar-24h/YYYY/MM/DD/
  OPERA/COMP/OPERA@YYYYMMDDTHHMM@0@{DBZH,RATE}.h5`` — so the collector
  computes the frame times it should have and fetches the ones it is missing.
  No listing, no crawl, and a 404 is simply "not published yet".
* **EUMETSAT** products are discovered through ``eumdac`` over a time window
  and arrive zipped, so the collector searches, downloads and unwraps.

Both paths share the same discipline: fetch newest-first, never re-fetch a
frame the store already has, write the payload before the sidecar, and purge
on every tick so a stalled consumer cannot fill the disk.

The droplet's inbound transfer is free (DigitalOcean bills egress only), which
is what makes the ~246 GB/month of 10-minute CTTH affordable.  It is still
worth keeping the CTTH pull off the GRIB decode cycle, where inbound peaks at
~34× the mean.
"""

from __future__ import annotations

import io
import logging
import os
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import requests

from .ctth import CTTH_COLLECTION, LI_COLLECTION
from .frames import (
    ALL_SOURCES,
    SOURCE_EUMETSAT_CTTH,
    SOURCE_OPERA_DBZH,
    SOURCE_OPERA_RATE,
    SOURCE_SPECS,
    FrameStore,
)
from .opera import OPERA_S3_BUCKET, opera_key

logger = logging.getLogger(__name__)

OPERA_BASE_URL = f"https://{OPERA_S3_BUCKET}.s3.amazonaws.com"

# Observed delivery lag from a frame's nominal time to its appearance in the
# cache.  Fetching before this has elapsed just spends a 404.
OPERA_DELIVERY_LAG = timedelta(minutes=4)
EUMETSAT_DELIVERY_LAG = timedelta(minutes=5)

# How far back a single tick will reach to backfill.  Deliberately short: an
# hour-old radar frame answers nothing a pilot is asking, and a long backfill
# after an outage would hammer the provider to fill a store that immediately
# purges most of it.
DEFAULT_LOOKBACK = timedelta(minutes=30)

_REQUEST_TIMEOUT = 60


@dataclass
class CollectResult:
    """Outcome of one source's collection tick."""

    source: str
    fetched: int = 0
    skipped: int = 0
    missing: int = 0
    failed: int = 0
    purged: int = 0
    bytes_in: int = 0
    latest_valid_time: datetime | None = None
    errors: list[str] = field(default_factory=list)


def observed_enabled() -> bool:
    """Master gate.  Off unless explicitly enabled for the deployment."""
    return os.environ.get("WB_OBSERVED_ENABLED", "").strip() in ("1", "true", "yes")


def enabled_sources() -> tuple[str, ...]:
    """Sources this deployment collects.

    ``WB_OBSERVED_SOURCES`` narrows the set — useful when EUMETSAT credentials
    are absent but radar is wanted, which is a perfectly good half of the
    feature rather than a broken one.
    """
    raw = os.environ.get("WB_OBSERVED_SOURCES", "").strip()
    if not raw:
        return ALL_SOURCES
    wanted = tuple(s.strip() for s in raw.split(",") if s.strip())
    unknown = [s for s in wanted if s not in SOURCE_SPECS]
    if unknown:
        raise ValueError(f"Unknown observed sources in WB_OBSERVED_SOURCES: {unknown}")
    return wanted


def expected_frame_times(
    source: str,
    now: datetime,
    *,
    lookback: timedelta = DEFAULT_LOOKBACK,
    delivery_lag: timedelta | None = None,
) -> list[datetime]:
    """Frame times that should exist by ``now``, newest first.

    The cadence is a fixed interval anchored on the hour, not a cycle with a
    forecast horizon — which is exactly why the freshness registry needed an
    interval schedule kind before this could be surfaced (see
    :mod:`weatherbrief.fetch.freshness.registry`).
    """
    spec = SOURCE_SPECS[source]
    lag = delivery_lag if delivery_lag is not None else _default_lag(source)
    interval_minutes = int(spec.interval.total_seconds() // 60)
    if interval_minutes <= 0:
        raise ValueError(f"source {source} has a non-positive interval")

    latest = now - lag
    anchor = latest.replace(second=0, microsecond=0)
    anchor -= timedelta(minutes=anchor.minute % interval_minutes)
    # Measured back from the newest *available* frame, not from wallclock: a
    # lookback shorter than the delivery lag would otherwise return nothing at
    # all, and "backfill the last half hour of frames" is the intent.
    oldest = anchor - lookback

    times: list[datetime] = []
    step = 0
    while True:
        candidate = anchor - timedelta(minutes=interval_minutes * step)
        if candidate < oldest:
            break
        times.append(candidate)
        step += 1
    return times


def _default_lag(source: str) -> timedelta:
    if source in (SOURCE_OPERA_DBZH, SOURCE_OPERA_RATE):
        return OPERA_DELIVERY_LAG
    return EUMETSAT_DELIVERY_LAG


# --- OPERA -----------------------------------------------------------------


def collect_opera(
    source: str,
    store: FrameStore,
    *,
    now: datetime | None = None,
    lookback: timedelta = DEFAULT_LOOKBACK,
    max_fetch: int = 4,
    session: requests.Session | None = None,
) -> CollectResult:
    """Fetch any missing OPERA composites for ``source``."""
    from . import opera

    spec = SOURCE_SPECS[source]
    now = now or datetime.now(timezone.utc)
    result = CollectResult(source=source)
    sess = session or requests.Session()

    for valid_time in expected_frame_times(source, now, lookback=lookback):
        if result.fetched >= max_fetch:
            break
        if store.has(source, valid_time):
            result.skipped += 1
            result.latest_valid_time = result.latest_valid_time or valid_time
            continue
        url = f"{OPERA_BASE_URL}/{opera_key(valid_time, spec.quantity)}"
        try:
            response = sess.get(url, timeout=_REQUEST_TIMEOUT)
        except requests.RequestException as exc:
            result.failed += 1
            result.errors.append(f"{valid_time:%H:%M} {exc}")
            continue
        if response.status_code == 404:
            # Not published yet, or this cadence slot simply does not exist.
            result.missing += 1
            continue
        if response.status_code != 200:
            result.failed += 1
            result.errors.append(f"{valid_time:%H:%M} HTTP {response.status_code}")
            continue

        payload = response.content
        path = store.write_payload(source, valid_time, payload)
        try:
            meta = opera.read_metadata(path, spec.quantity)
        except Exception as exc:
            logger.warning("Unreadable OPERA frame %s: %s", path, exc)
            path.unlink(missing_ok=True)
            result.failed += 1
            result.errors.append(f"{valid_time:%H:%M} unreadable: {exc}")
            continue
        meta["url"] = url
        store.write_sidecar(source, valid_time, meta)
        result.fetched += 1
        result.bytes_in += len(payload)
        if result.latest_valid_time is None or valid_time > result.latest_valid_time:
            result.latest_valid_time = valid_time

    result.purged = store.purge(source, now=now)
    return result


# --- EUMETSAT --------------------------------------------------------------


def eumetsat_credentials() -> tuple[str, str] | None:
    key = os.environ.get("EUMETSAT_CONSUMER_KEY", "").strip()
    secret = os.environ.get("EUMETSAT_CONSUMER_SECRET", "").strip()
    if not key or not secret:
        return None
    return key, secret


def _collection_id(source: str) -> str:
    return CTTH_COLLECTION if source == SOURCE_EUMETSAT_CTTH else LI_COLLECTION


def _open_datastore():
    import eumdac

    credentials = eumetsat_credentials()
    if credentials is None:
        raise RuntimeError(
            "EUMETSAT_CONSUMER_KEY / EUMETSAT_CONSUMER_SECRET are not set"
        )
    return eumdac.DataStore(eumdac.AccessToken(credentials))


def _unwrap_product(raw: bytes) -> bytes:
    """Return the netCDF payload from a EUMETSAT product download.

    Products arrive as a zip holding the granule alongside quicklook PNGs and
    EOPMetadata.xml.  We keep only the netCDF: the quicklooks are a quarter of
    the bytes and nothing reads them.
    """
    if not raw[:2] == b"PK":
        return raw
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        names = [n for n in archive.namelist() if n.endswith(".nc")]
        if not names:
            raise ValueError("EUMETSAT product zip contains no .nc granule")
        return archive.read(names[0])


def _product_time(product) -> datetime | None:
    for attr in ("sensing_end", "end", "sensing_start", "start"):
        value = getattr(product, attr, None)
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return None


def collect_eumetsat(
    source: str,
    store: FrameStore,
    *,
    now: datetime | None = None,
    lookback: timedelta = DEFAULT_LOOKBACK,
    max_fetch: int = 2,
    datastore=None,
) -> CollectResult:
    """Search, download and unwrap EUMETSAT granules for ``source``."""
    from . import ctth, lightning

    spec = SOURCE_SPECS[source]
    now = now or datetime.now(timezone.utc)
    result = CollectResult(source=source)

    try:
        datastore = datastore or _open_datastore()
        collection = datastore.get_collection(_collection_id(source))
        products = list(
            collection.search(
                dtstart=now - lookback - spec.interval,
                dtend=now,
            )
        )
    except Exception as exc:
        result.failed += 1
        result.errors.append(str(exc))
        logger.warning("EUMETSAT search failed for %s: %s", source, exc)
        result.purged = store.purge(source, now=now)
        return result

    # Newest first: a briefing wants the freshest frame, and the fetch budget
    # should be spent there rather than on backfill.
    dated = [(t, p) for p, t in ((p, _product_time(p)) for p in products) if t]
    dated.sort(key=lambda item: item[0], reverse=True)

    for product_time, product in dated:
        if result.fetched >= max_fetch:
            break
        valid_time = _snap_to_interval(product_time, spec.interval)
        if store.has(source, valid_time):
            result.skipped += 1
            continue
        try:
            with product.open() as handle:
                raw = handle.read()
            payload = _unwrap_product(raw)
        except Exception as exc:
            result.failed += 1
            result.errors.append(f"{valid_time:%H:%M} {exc}")
            logger.warning("EUMETSAT download failed for %s: %s", source, exc)
            continue

        path = store.write_payload(source, valid_time, payload)
        try:
            reader = ctth if source == SOURCE_EUMETSAT_CTTH else lightning
            meta = reader.read_metadata(path)
        except Exception as exc:
            logger.warning("Unreadable EUMETSAT granule %s: %s", path, exc)
            path.unlink(missing_ok=True)
            result.failed += 1
            result.errors.append(f"{valid_time:%H:%M} unreadable: {exc}")
            continue
        meta["product_id"] = str(product)
        store.write_sidecar(source, valid_time, meta)
        result.fetched += 1
        result.bytes_in += len(payload)
        if result.latest_valid_time is None or valid_time > result.latest_valid_time:
            result.latest_valid_time = valid_time

    result.purged = store.purge(source, now=now)
    return result


def _snap_to_interval(when: datetime, interval: timedelta) -> datetime:
    """Round a product's sensing time down to its cadence slot.

    The store keys frames by slot so ``has()`` is a filename check.  Sensing
    times carry seconds and occasional sub-minute jitter; without snapping,
    the same granule would be re-fetched under a new name every tick.
    """
    minutes = int(interval.total_seconds() // 60) or 1
    snapped = when.astimezone(timezone.utc).replace(second=0, microsecond=0)
    return snapped - timedelta(minutes=snapped.minute % minutes)


# --- Tick ------------------------------------------------------------------


def collect_once(
    store: FrameStore | None = None,
    *,
    now: datetime | None = None,
    sources: tuple[str, ...] | None = None,
) -> list[CollectResult]:
    """One collection tick across every enabled source.

    A source that fails does not stop the others: half the observed picture is
    worth more than none of it, and the payload says which half is missing.
    """
    store = store or FrameStore()
    now = now or datetime.now(timezone.utc)
    results: list[CollectResult] = []
    for source in sources if sources is not None else enabled_sources():
        try:
            if source in (SOURCE_OPERA_DBZH, SOURCE_OPERA_RATE):
                results.append(collect_opera(source, store, now=now))
            else:
                results.append(collect_eumetsat(source, store, now=now))
        except Exception as exc:
            logger.warning("Observed collection failed for %s", source, exc_info=True)
            results.append(CollectResult(source=source, failed=1, errors=[str(exc)]))
    return results


def due_sources(
    store: FrameStore,
    *,
    now: datetime | None = None,
    sources: tuple[str, ...] | None = None,
) -> list[str]:
    """Sources whose next frame should already have been published.

    Lets the scheduler tick every minute while only reaching out for a source
    when its cadence says a new frame exists — a 5-minute radar stream and a
    10-minute satellite one share one loop without either polling blind.
    """
    now = now or datetime.now(timezone.utc)
    due: list[str] = []
    for source in sources if sources is not None else enabled_sources():
        expected = expected_frame_times(source, now)
        if not expected:
            continue
        if not store.has(source, expected[0]):
            due.append(source)
    return due
