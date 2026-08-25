"""Build the ``ObservedConditions`` payload for one route.

The briefing call site.  Reads the newest local frame for each source, samples
it around the route's own cross-section points, and assembles the inline
payload that sits on ``briefing.json`` beside ``route_observations``.

Two properties this module is responsible for:

* **No network.**  Nothing here fetches; a source with no recent frame simply
  reports why.  The collector is the only thing that talks to a provider.
* **No shared clock.**  Each field carries its own frame's valid time and age.
  A source whose newest frame is older than its display window is dropped
  rather than presented as current — an hour-old radar picture is not "what is
  there now", and there is no honest way to say so with one timestamp across
  four streams.

Stations are the route's interpolated cross-section points, so the sampled
values land on the same X axis the cross-section and route graph already use.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from weatherbrief.models.analysis import RouteConfig
from weatherbrief.models.observed import (
    ObservedConditions,
    ObservedField,
    ObservedFlashField,
    ObservedFlashStationSamples,
    ObservedSourceStatus,
    ObservedStationRef,
    ObservedStationSamples,
    ObservedTopsField,
    ObservedTopsStationSamples,
)

from .frames import (
    SOURCE_EUMETSAT_CTTH,
    SOURCE_EUMETSAT_LI,
    SOURCE_OPERA_DBZH,
    SOURCE_OPERA_RATE,
    SOURCE_SPECS,
    FrameStore,
    StoredFrame,
)
from .grid import compute_window, nm_to_km
from .sampler import DEFAULT_RADII_NM, SampleStation, sample, sample_flashes
from .summary import build_summary

logger = logging.getLogger(__name__)

# Spacing of the sampled stations along the route.  Matches the cross-section's
# default point spacing so an observed value and the model column above it
# describe the same place.
DEFAULT_SPACING_NM = 10.0

# Radar composites are ground-projected, so there is no parallax to reach for;
# a couple of pixels of slack just keeps a disc from clipping the window edge.
RADAR_WINDOW_PAD_KM = 5.0


def build_stations(
    route: RouteConfig, spacing_nm: float = DEFAULT_SPACING_NM
) -> tuple[list[ObservedStationRef], list[SampleStation]]:
    """Route cross-section points as sampler stations.

    IDs are positional (``P000``, ``P001``, …) rather than ICAO: a round-trip
    route visits the same airport twice, and the payload keys samples by
    station id.
    """
    from weatherbrief.fetch.route_points import interpolate_route

    refs: list[ObservedStationRef] = []
    stations: list[SampleStation] = []
    for index, point in enumerate(interpolate_route(route, spacing_nm)):
        station_id = f"P{index:03d}"
        refs.append(
            ObservedStationRef(
                id=station_id,
                name=point.waypoint_icao or point.waypoint_name,
                lat=point.lat,
                lon=point.lon,
                enroute_distance_nm=point.distance_from_origin_nm,
                distance_from_route_nm=0.0,
            )
        )
        stations.append(SampleStation(station_id, point.lat, point.lon))
    return refs, stations


def _usable_frame(
    store: FrameStore, source: str, now: datetime
) -> tuple[StoredFrame | None, str | None]:
    """Newest frame for ``source``, or the reason there is none to show."""
    spec = SOURCE_SPECS[source]
    frames = store.list_frames(source)
    if not frames:
        return None, "no frames collected"
    newest = frames[0]
    age = timedelta(minutes=newest.age_minutes(now))
    if age > spec.max_display_age:
        return None, (
            f"newest frame is {age.total_seconds() / 60:.0f} min old "
            f"(limit {spec.max_display_age.total_seconds() / 60:.0f} min)"
        )
    return newest, None


def _grid_field(
    stored: StoredFrame,
    source: str,
    stations: list[SampleStation],
    radii_nm: tuple[float, ...],
    now: datetime,
):
    """Sample one gridded source and return its populated field model."""
    from . import ctth, opera

    spec = SOURCE_SPECS[source]
    max_radius_km = nm_to_km(max(radii_nm))
    lats = [s.lat for s in stations]
    lons = [s.lon for s in stations]

    if source in (SOURCE_OPERA_DBZH, SOURCE_OPERA_RATE):
        grid = opera.read_grid(stored.path)
        window = compute_window(
            grid, lats, lons, radius_km=max_radius_km, pad_km=RADAR_WINDOW_PAD_KM
        )
        frame = opera.read_window(
            stored.path, spec.quantity, window, source=source, units=spec.units
        )
    else:
        import netCDF4

        with netCDF4.Dataset(str(stored.path)) as dataset:
            grid = ctth.read_grid(dataset)
        window = compute_window(
            grid,
            lats,
            lons,
            radius_km=max_radius_km,
            # Parallax first: the pixels that belong over these stations sit
            # tens of km away in the imagery, so the read must reach them.
            # Scaled to the route's own latitude — the 75 km figure is a 50°N
            # measurement, and a Scandinavian route needs twice that or its
            # high cloud is silently truncated.
            pad_km=ctth.parallax_pad_km(max(abs(lat) for lat in lats)),
            # Granule chunks are full-width strips; narrowing columns costs a
            # partial-chunk decompression and saves nothing.
            full_width=True,
        )
        frame = ctth.read_window(stored.path, window, source=source)

    samples = sample(frame, window, stations, radii_nm)
    common = dict(
        source=source,
        quantity=frame.quantity,
        units=frame.units,
        valid_time=frame.valid_time,
        age_minutes=round(frame.age_minutes(now), 1),
        window_minutes=frame.window_minutes,
        attribution=frame.attribution,
    )
    if source == SOURCE_EUMETSAT_CTTH:
        return ObservedTopsField(
            **common,
            stations=[
                ObservedTopsStationSamples(station_id=sid, annuli=annuli)
                for sid, annuli in samples.items()
            ],
        )
    return ObservedField(
        **common,
        stations=[
            ObservedStationSamples(station_id=sid, annuli=annuli)
            for sid, annuli in samples.items()
        ],
    )


def _lightning_field(
    stored: StoredFrame,
    stations: list[SampleStation],
    radii_nm: tuple[float, ...],
    now: datetime,
) -> ObservedFlashField:
    from . import lightning

    spec = SOURCE_SPECS[SOURCE_EUMETSAT_LI]
    frame = lightning.read_flashes(
        stored.path, source=SOURCE_EUMETSAT_LI, window_minutes=spec.window_minutes
    )
    samples = sample_flashes(frame, stations, radii_nm)
    return ObservedFlashField(
        source=SOURCE_EUMETSAT_LI,
        quantity="flash",
        units="count",
        valid_time=frame.valid_time,
        age_minutes=round(frame.age_minutes(now), 1),
        window_minutes=frame.window_minutes,
        attribution=frame.attribution,
        stations=[
            ObservedFlashStationSamples(station_id=sid, annuli=annuli)
            for sid, annuli in samples.items()
        ],
    )


def build_observed_conditions(
    route: RouteConfig,
    *,
    store: FrameStore | None = None,
    radii_nm: tuple[float, ...] = DEFAULT_RADII_NM,
    spacing_nm: float = DEFAULT_SPACING_NM,
    now: datetime | None = None,
    sources: tuple[str, ...] | None = None,
) -> ObservedConditions:
    """Sample every available observed source along ``route``.

    Always returns a payload, even when nothing is available: the ``sources``
    list then says which stream is missing and why, which is information a
    pilot needs (a briefing with no radar is not a briefing with no weather).
    """
    store = store or FrameStore()
    now = now or datetime.now(timezone.utc)
    refs, stations = build_stations(route, spacing_nm)

    wanted = sources if sources is not None else tuple(SOURCE_SPECS)
    statuses: list[ObservedSourceStatus] = []
    fields: dict[str, object] = {}

    for source in wanted:
        stored, reason = _usable_frame(store, source, now)
        if stored is None:
            statuses.append(
                ObservedSourceStatus(source=source, available=False, reason=reason)
            )
            continue
        try:
            if source == SOURCE_EUMETSAT_LI:
                fields[source] = _lightning_field(stored, stations, radii_nm, now)
            else:
                fields[source] = _grid_field(stored, source, stations, radii_nm, now)
        except Exception as exc:
            # A malformed frame must not take the other three sources with it.
            logger.warning("Observed sampling failed for %s", source, exc_info=True)
            statuses.append(
                ObservedSourceStatus(
                    source=source,
                    available=False,
                    reason=f"frame unreadable: {exc}",
                    latest_valid_time=stored.valid_time,
                )
            )
            continue
        statuses.append(
            ObservedSourceStatus(
                source=source, available=True, latest_valid_time=stored.valid_time
            )
        )

    conditions = ObservedConditions(
        computed_at=now,
        corridor_nm=max(radii_nm) if radii_nm else 0.0,
        radii_nm=list(radii_nm),
        stations=refs,
        reflectivity=fields.get(SOURCE_OPERA_DBZH),
        rain_rate=fields.get(SOURCE_OPERA_RATE),
        cloud_tops=fields.get(SOURCE_EUMETSAT_CTTH),
        lightning=fields.get(SOURCE_EUMETSAT_LI),
        sources=statuses,
    )
    conditions.summary_lines = build_summary(conditions)
    conditions.summary = " ".join(conditions.summary_lines)
    return conditions
