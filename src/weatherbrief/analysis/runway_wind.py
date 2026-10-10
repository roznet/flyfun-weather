"""Build the runway + wind picture (#758) for one airport.

Pure: runways and wind samples in, :class:`RunwayWindPicture` out. No new
trigonometry here — the per-end components are euro_aip's
``WeatherAnalyzer.wind_components`` (signed crosswind, side, gust components,
worst case over a ``dddVddd`` range), and the best end and its advisory tier
are ``compute_wind_advisory``'s, the function that fills the METAR/TAF table.
One picker, so the dial and the table cannot name different runways.

Headings and winds are both true; see ``models/runway_wind.py``.
"""

from __future__ import annotations

from datetime import datetime

from weatherbrief.models.airport_conditions import RunwayEnd
from weatherbrief.models.runway_wind import (
    EndComponents,
    RunwayInfo,
    RunwayWindPicture,
    WindAtAirport,
    WindSample,
)


def wind_sample(
    source: str,
    time: datetime | None,
    direction: int | None,
    speed_kt: int | None,
    gust_kt: int | None,
    variable_from: int | None = None,
    variable_to: int | None = None,
) -> WindSample | None:
    """A :class:`WindSample` from reported fields, or None when there is no wind.

    No speed means no wind was reported — never "calm" (#758: missing data is
    stated, not drawn as calm). Calm is a reported 0 kt; VRB is a speed with
    no direction.
    """
    if speed_kt is None:
        return None
    calm = speed_kt == 0
    variable = not calm and direction is None
    return WindSample(
        source=source,
        time=time,
        direction_true=None if variable else direction,
        speed_kt=speed_kt,
        gust_kt=gust_kt,
        variable=variable,
        variable_from=None if calm else variable_from,
        variable_to=None if calm else variable_to,
        calm=calm,
    )


def runway_ends_of(runways: list[RunwayInfo]) -> list[RunwayEnd]:
    """The flat end list ``compute_wind_advisory`` picks from, in runway order.

    ``airports.get_runway_ends`` is this projection of ``get_runways``, so the
    order (and with it the tie-break between equal ends) is the table's.
    """
    return [
        RunwayEnd(id=end.ident, heading_deg=end.heading_true)
        for rwy in runways
        for end in rwy.ends
    ]


def end_components(sample: WindSample, runways: list[RunwayInfo]) -> list[EndComponents]:
    """euro_aip's components of ``sample`` on every runway end."""
    from euro_aip.briefing.weather.analysis import WeatherAnalyzer
    from euro_aip.briefing.weather.models import WeatherReport

    report = WeatherReport(
        wind_direction=sample.direction_true,
        wind_speed=sample.speed_kt,
        wind_gust=sample.gust_kt,
        wind_variable_from=sample.variable_from,
        wind_variable_to=sample.variable_to,
    )
    out: list[EndComponents] = []
    for end in runway_ends_of(runways):
        wc = WeatherAnalyzer.wind_components(report, end.heading_deg, end.id)
        if wc is None:
            continue
        crosswind = float(wc.crosswind)
        out.append(EndComponents(
            ident=end.id,
            headwind_kt=float(wc.headwind),
            crosswind_kt=crosswind,
            # euro_aip names a side even for a dead-on wind; a pure
            # head/tailwind has none.
            side=wc.crosswind_direction if crosswind != 0 else "",
            gust_headwind_kt=wc.gust_headwind,
            gust_crosswind_kt=wc.gust_crosswind,
            max_crosswind_kt=float(
                wc.max_crosswind if wc.max_crosswind is not None else abs(crosswind)
            ),
        ))
    return out


def wind_at_airport(sample: WindSample, runways: list[RunwayInfo]) -> WindAtAirport:
    """One sample over the runways, with the table's best end and advisory."""
    from weatherbrief.tasks.route_weather import compute_wind_advisory

    advisory, best_end, _, _ = compute_wind_advisory(
        sample.direction_true, sample.speed_kt, sample.gust_kt, runway_ends_of(runways),
    )
    return WindAtAirport(
        wind=sample,
        ends=end_components(sample, runways),
        best_end=best_end,
        advisory=advisory,
    )


def build_runway_wind_picture(
    icao: str,
    runways: list[RunwayInfo],
    winds: list[WindSample | None],
) -> RunwayWindPicture:
    """The picture for one airport. ``None`` samples (nothing reported) are dropped."""
    return RunwayWindPicture(
        icao=icao,
        runways=runways,
        winds=[wind_at_airport(s, runways) for s in winds if s is not None],
    )


def picture_for_observation(obs, runways: list[RunwayInfo], eta: datetime | None) -> RunwayWindPicture:
    """The picture for an ``AirportObservation``: its METAR now and TAF at ``eta``.

    The TAF sample is only taken when the TAF is valid at the ETA (an expired
    TAF leaves every at-ETA field empty, #610).
    """
    metar = wind_sample(
        "metar", obs.metar_time, obs.metar_wind_dir, obs.metar_wind_speed_kt,
        obs.metar_wind_gust_kt, obs.metar_wind_variable_from, obs.metar_wind_variable_to,
    )
    taf = None
    if obs.taf_valid_at_eta is not False:
        taf = wind_sample(
            "taf", eta, obs.taf_wind_dir, obs.taf_wind_speed_kt,
            obs.taf_wind_gust_kt, obs.taf_wind_variable_from, obs.taf_wind_variable_to,
        )
    return build_runway_wind_picture(obs.icao, runways, [metar, taf])
