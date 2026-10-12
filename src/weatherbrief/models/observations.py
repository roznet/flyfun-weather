"""METAR/TAF observation models for route weather integration."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, computed_field

from weatherbrief.models.observed import ObservedConditions
from weatherbrief.models.runway_wind import RunwayWindPicture


_COMPASS8 = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")


class ObsProxy(BaseModel):
    """A nearby station standing in for a field with no METAR or TAF (#767).

    Set on the *field's* row (``AirportObservation.metar_proxy`` /
    ``taf_proxy``); the station's own row keeps its report under its own ICAO.
    Chosen by ``analysis/obs_proxy.resolve_obs_proxy``.
    """

    icao: str
    distance_nm: float
    # True bearing from the field to the station, so "8 NM NE" reads from
    # where the pilot is.
    bearing_deg: int | None = None
    # Station elevation minus field elevation (ft). None when either is unknown
    # (only a curated override can pick such a station).
    elevation_diff_ft: int | None = None
    source: str = "auto"  # "auto" | "override"

    @property
    def where(self) -> str:
        """"8 NM NE" (distance rounded, 8-point compass)."""
        text = f"{self.distance_nm:.0f} NM"
        if self.bearing_deg is not None:
            text += f" {_COMPASS8[int(((self.bearing_deg % 360) + 22.5) // 45) % 8]}"
        return text


class AirportObservation(BaseModel):
    """METAR/TAF data for one airport along the route."""

    icao: str
    name: str | None = None
    distance_from_route_nm: float
    enroute_distance_nm: float | None = None
    nearest_waypoint_icao: str
    # Airport position (#688: a station's CB/TCU joins the radar storm near
    # it). None for reports fetched before the field, or an airport the
    # database cannot place.
    lat: float | None = None
    lon: float | None = None
    metar_raw: str | None = None
    metar_time: datetime | None = None
    metar_flight_category: str | None = None  # "VFR"/"MVFR"/"IFR"/"LIFR"
    metar_ceiling_ft: int | None = None
    metar_visibility_m: int | None = None
    metar_wind_dir: int | None = None
    metar_wind_speed_kt: int | None = None
    metar_wind_gust_kt: int | None = None
    # The METAR's dddVddd variable range (#758). A VRB wind is a speed with
    # no direction; calm is 0 kt. None on packs built before #758.
    metar_wind_variable_from: int | None = None
    metar_wind_variable_to: int | None = None
    metar_weather: list[str] = Field(default_factory=list)
    metar_temperature_c: int | None = None
    metar_dewpoint_c: int | None = None
    metar_qnh: float | None = None
    # "METAR" or "SPECI" for the latest report, plus the category and time of
    # the report before it (within the 3 h fetch window). The live layer's
    # significance classifier (#637) needs them for hysteresis: a category
    # crossing counts once two consecutive reports agree, or on a SPECI.
    # None on packs built before #637.
    metar_report_type: str | None = None
    metar_previous_flight_category: str | None = None
    metar_previous_time: datetime | None = None
    taf_raw: str | None = None
    # Issue time of the TAF the at-ETA reading came from (None before #637).
    taf_issue_time: datetime | None = None
    # Validity window of the TAF, and whether it contains this airport's ETA.
    # aviationweather.gov returns the latest TAF however old, so False is common
    # at fields that issue TAFs only in opening hours: the raw text is kept and
    # every at-ETA field stays empty. None on packs built before #610.
    taf_valid_from: datetime | None = None
    taf_valid_to: datetime | None = None
    taf_valid_at_eta: bool | None = None
    # Worse of the prevailing and temporary categories at ETA.
    taf_flight_category_at_eta: str | None = None
    # What sets taf_flight_category_at_eta: the temporary group's label when it
    # is worse ("TEMPO", "PROB30 TEMPO"), else the latest BECMG/FM applied.
    taf_trend_type: str | None = None
    # Main body with completed BECMG / started FM groups applied.
    taf_prevailing_category_at_eta: str | None = None
    # Worst TEMPO/PROB group at ETA — set only when worse than prevailing.
    taf_temporary_category_at_eta: str | None = None
    taf_temporary_type: str | None = None
    # TS, FG, FZ*, SN, GR… and CB/TCU in the prevailing or temporary conditions.
    taf_significant_weather: list[str] = Field(default_factory=list)
    taf_wind_dir: int | None = None
    taf_wind_speed_kt: int | None = None
    taf_wind_gust_kt: int | None = None
    taf_wind_variable_from: int | None = None  # #758, as metar_wind_variable_*
    taf_wind_variable_to: int | None = None
    taf_applicable_lines: list[int] = Field(default_factory=list)
    metar_wind_advisory: str | None = None
    metar_best_runway_id: str | None = None
    metar_crosswind_kt: float | None = None
    metar_headwind_kt: float | None = None
    taf_wind_advisory: str | None = None
    taf_best_runway_id: str | None = None
    taf_crosswind_kt: float | None = None
    taf_headwind_kt: float | None = None
    has_metar: bool = False
    has_taf: bool = False
    eta_hour_offset: int | None = None  # rounded hours after departure
    # Runways + the METAR and TAF-at-ETA winds on them (#758), for the
    # runway + wind widget. Built for every corridor airport so any future
    # placement (table row, alternate) needs no server change. None when the
    # airport has no runway data, the computation failed, or on packs built
    # before #758.
    runway_wind: RunwayWindPicture | None = None
    # Stand-in stations when this field has no METAR / no TAF of its own
    # (#767). Provenance only: the station's report stays on its own row, and
    # ``RouteObservations.terminal`` is how a consumer reads it for the field.
    # ``runway_wind`` then lays the station's winds over *this* field's
    # runways. None for a field with its own report, and on older packs.
    metar_proxy: ObsProxy | None = None
    taf_proxy: ObsProxy | None = None

    @computed_field
    @property
    def proxy_caption(self) -> str | None:
        """The gray line clients show under a proxied terminal (#767):
        "using EGLL as proxy · 8 NM NE", or "METAR EGLF (4 NM N) · TAF EGLL
        (12 NM NE)" when the two differ ("TAF EGLK" for the field's own,
        "TAF none" for neither). Server-built so web, iOS and the agents show
        one string. None when the field reports itself."""
        m, t = self.metar_proxy, self.taf_proxy
        if m is None and t is None:
            return None
        if m is not None and t is not None and m.icao == t.icao:
            return f"using {m.icao} as proxy · {m.where}"

        def name(proxy: ObsProxy | None, own: bool) -> str:
            if proxy is not None:
                return f"{proxy.icao} ({proxy.where})"
            return self.icao if own else "none"

        return (
            f"METAR {name(m, self.has_metar or bool(self.metar_raw))}"
            f" · TAF {name(t, self.has_taf or bool(self.taf_raw))}"
        )

    def taf_at_eta_line(self) -> str:
        """One-line TAF reading at ETA, shared by the LLM context and the text digest.

        ``TAF at ETA [MVFR], TEMPO [IFR] (TSRA CB)`` — prevailing category, the
        worse temporary group if any, significant weather. An expired TAF says
        so with its validity, so the digest cannot quote it as current.
        """
        if self.taf_valid_at_eta is False:
            window = ""
            if self.taf_valid_from is not None and self.taf_valid_to is not None:
                window = (
                    f" (latest TAF valid {self.taf_valid_from:%d/%H}Z"
                    f"-{self.taf_valid_to:%d/%H}Z)"
                )
            return f"TAF: none valid at ETA{window}"
        if self.taf_valid_at_eta is None:
            # Packs built before #610 carry only the single-group reading.
            category = f" [{self.taf_flight_category_at_eta}]" if self.taf_flight_category_at_eta else ""
            trend = f" ({self.taf_trend_type})" if self.taf_trend_type else ""
            return f"TAF at ETA{category}{trend}"

        line = "TAF at ETA"
        if self.taf_prevailing_category_at_eta:
            line += f" [{self.taf_prevailing_category_at_eta}]"
        if self.taf_temporary_category_at_eta:
            line += f", {self.taf_temporary_type or 'TEMPO'} [{self.taf_temporary_category_at_eta}]"
        if self.taf_significant_weather:
            line += f" ({' '.join(self.taf_significant_weather)})"
        return line


class ObservationComparison(BaseModel):
    """Comparison of one airport's observations vs nearest model prediction."""

    icao: str
    obs_category: str | None = None
    model_category: str | None = None
    category_match: str  # "CONFIRMING" / "SIGNIFICANT" / "CONFLICTING"
    ceiling_delta_ft: int | None = None
    visibility_delta_m: float | None = None
    wind_speed_delta_kt: float | None = None
    model_wind_dir: float | None = None
    model_wind_speed_kt: float | None = None
    model_wind_gust_kt: float | None = None
    model_wind_advisory: str | None = None
    model_best_runway_id: str | None = None
    model_crosswind_kt: float | None = None
    wind_advisory_match: str | None = None
    detail: str = ""


class RouteObservations(BaseModel):
    """Complete METAR/TAF picture along the route."""

    corridor_nm: float
    fetch_time: datetime
    airports_found: int
    airports_with_metar: int
    airports_with_taf: int
    airports: list[AirportObservation] = Field(default_factory=list)
    comparisons: list[ObservationComparison] = Field(default_factory=list)
    worst_metar_category: str | None = None
    worst_taf_category: str | None = None
    has_conflicts: bool = False
    phenomena_along_route: list[str] = Field(default_factory=list)

    def airport(self, icao: str | None) -> AirportObservation | None:
        """The row filed under ``icao`` (case-insensitive), or None."""
        if not icao:
            return None
        icao = icao.upper()
        return next((a for a in self.airports if a.icao.upper() == icao), None)

    def terminal(
        self,
        icao: str | None,
        *,
        metar_station: str | None = None,
        taf_station: str | None = None,
    ) -> "TerminalObservation | None":
        """The weather a terminal (departure, destination) is flown on (#767).

        The field's own METAR/TAF, or its proxy's when ``metar_proxy`` /
        ``taf_proxy`` names one. Every terminal consumer reads through this
        instead of looking the field up by ICAO, so a proxied field is never
        blank on one surface and filled on another. ``metar_station`` /
        ``taf_station`` force the station read (the significance classifier
        compares a station with *itself* across a proxy switch). None when the
        field is not in the corridor list at all.
        """
        field = self.airport(icao)
        if field is None:
            return None
        metar_proxy, taf_proxy = field.metar_proxy, field.taf_proxy
        if metar_station is not None and metar_station.upper() != field.icao.upper():
            if metar_proxy is None or metar_proxy.icao.upper() != metar_station.upper():
                metar_proxy = ObsProxy(icao=metar_station.upper(), distance_nm=0.0)
        elif metar_station is not None:
            metar_proxy = None
        if taf_station is not None and taf_station.upper() != field.icao.upper():
            if taf_proxy is None or taf_proxy.icao.upper() != taf_station.upper():
                taf_proxy = ObsProxy(icao=taf_station.upper(), distance_nm=0.0)
        elif taf_station is not None:
            taf_proxy = None
        metar_row = self.airport(metar_proxy.icao) if metar_proxy else field
        taf_row = self.airport(taf_proxy.icao) if taf_proxy else field
        return TerminalObservation(
            field=field,
            metar_row=metar_row,
            taf_row=taf_row,
            metar_proxy=metar_proxy,
            taf_proxy=taf_proxy,
        )


_METAR_ADVISORY = ("metar_wind_advisory", "metar_best_runway_id", "metar_crosswind_kt", "metar_headwind_kt")
_TAF_ADVISORY = ("taf_wind_advisory", "taf_best_runway_id", "taf_crosswind_kt", "taf_headwind_kt")
# Provenance stays the field's, never the station's.
_PROVENANCE = ("metar_proxy", "taf_proxy")


class TerminalObservation(BaseModel):
    """A terminal's effective METAR/TAF and where each comes from (#767).

    Not serialized: built on demand by :meth:`RouteObservations.terminal`.
    ``metar_row`` / ``taf_row`` are the rows the reports live on (the field
    itself, or the proxy station's own row; None when that station is not in
    the corridor list this time).
    """

    field: AirportObservation
    metar_row: AirportObservation | None = None
    taf_row: AirportObservation | None = None
    metar_proxy: ObsProxy | None = None
    taf_proxy: ObsProxy | None = None

    @property
    def icao(self) -> str:
        return self.field.icao

    @property
    def proxied(self) -> bool:
        return self.metar_proxy is not None or self.taf_proxy is not None

    @property
    def metar_station(self) -> str | None:
        """ICAO the METAR comes from, None when there is no METAR."""
        row = self.metar_row
        if row is None or not (row.has_metar or row.metar_raw):
            return None
        return row.icao

    @property
    def taf_station(self) -> str | None:
        row = self.taf_row
        if row is None or not (row.has_taf or row.taf_raw):
            return None
        return row.icao

    @property
    def label(self) -> str:
        """"EGTF (via EGLL)" — the field, and the station when it is proxied.
        Every message about a proxied terminal names both (#767 decision 8)."""
        via = []
        for proxy in (self.metar_proxy, self.taf_proxy):
            if proxy is not None and proxy.icao not in via:
                via.append(proxy.icao)
        return f"{self.icao} (via {', '.join(via)})" if via else self.icao

    @property
    def caption(self) -> str | None:
        """The gray line under the terminal (``AirportObservation.proxy_caption``)."""
        return self.field.proxy_caption

    @property
    def proxy_note(self) -> str | None:
        """For the digests: "EGTF has no METAR or TAF; EGLL (8 NM NE,
        elevation +3 ft) used as proxy". None when unproxied."""
        m, t = self.metar_proxy, self.taf_proxy
        if m is None and t is None:
            return None

        def station(p: ObsProxy) -> str:
            elev = (
                f", elevation {p.elevation_diff_ft:+d} ft" if p.elevation_diff_ft is not None else ""
            )
            return f"{p.icao} ({p.where}{elev})"

        if m is not None and t is not None and m.icao == t.icao:
            return f"{self.icao} has no METAR or TAF; {station(m)} used as proxy"
        notes = []
        if m is not None:
            notes.append(f"{self.icao} has no METAR; {station(m)} used as METAR proxy")
        if t is not None:
            notes.append(f"{self.icao} has no TAF; {station(t)} used as TAF proxy")
        return "; ".join(notes)

    def effective(self) -> AirportObservation:
        """The field's row with the station's METAR and TAF fields laid in.

        For *policy* only (significance, glance categories): it carries the
        station's raw text so convective tags read the same, so never render
        its ``metar_raw`` as the field's — show the station's own row. The
        wind advisories are recomputed on the **field's** runways (the
        picture on ``field.runway_wind``), since that is where the pilot lands.
        """
        if not self.proxied:
            return self.field
        update: dict = {}
        if self.metar_proxy is not None:
            update.update(_fields(self.metar_row, "metar_", _METAR_ADVISORY))
            update["has_metar"] = bool(self.metar_row and self.metar_row.has_metar)
            update.update(_field_advisory(self.field, update, "metar"))
        if self.taf_proxy is not None:
            update.update(_fields(self.taf_row, "taf_", _TAF_ADVISORY))
            update["has_taf"] = bool(self.taf_row and self.taf_row.has_taf)
            update.update(_field_advisory(self.field, update, "taf"))
        return self.field.model_copy(update=update)


def _fields(row: AirportObservation | None, prefix: str, skip: tuple[str, ...]) -> dict:
    """``row``'s report fields (``metar_*`` / ``taf_*``), or all empty."""
    blank = AirportObservation(icao="", distance_from_route_nm=0.0, nearest_waypoint_icao="")
    src = row if row is not None else blank
    return {
        name: getattr(src, name)
        for name in AirportObservation.model_fields
        if name.startswith(prefix) and name not in skip and name not in _PROVENANCE
    }


def _field_advisory(field: AirportObservation, report: dict, kind: str) -> dict:
    """The ``kind`` (metar/taf) wind advisory of ``report``'s wind on the
    field's runways, or all None when the field has no runway data."""
    keys = _METAR_ADVISORY if kind == "metar" else _TAF_ADVISORY
    empty = dict.fromkeys(keys)
    runways = field.runway_wind.runways if field.runway_wind is not None else []
    if not runways:
        return empty
    from weatherbrief.analysis.runway_wind import runway_ends_of
    from weatherbrief.tasks.route_weather import compute_wind_advisory

    adv, rwy, xw, hw = compute_wind_advisory(
        report.get(f"{kind}_wind_dir"), report.get(f"{kind}_wind_speed_kt"),
        report.get(f"{kind}_wind_gust_kt"), runway_ends_of(runways),
    )
    if adv is None:
        return empty
    return dict(zip(keys, (adv, rwy, xw, hw)))


class SigmetAlongRoute(BaseModel):
    """One SIGMET intersecting the route corridor (flat, serializable).

    Retains the polygon outline, the affected enroute span and the vertical
    band so a later cross-section/map overlay of the impacted area can be
    built without re-fetching.
    """

    fir_id: str
    fir_name: str | None = None
    hazard: str | None = None  # TURB / ICE / TS / MTW / VA ...
    qualifier: str | None = None  # SEV / EMBD / ISOL ...
    base_ft: int | None = None
    top_ft: int | None = None
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    direction: str | None = None  # movement, e.g. "NE" (None if stationary)
    speed_kt: int | None = None
    raw_text: str = ""
    # Route-intersection metadata from RouteSigmetService.
    matched_firs: list[str] = Field(default_factory=list)
    min_distance_nm: float | None = None
    enroute_distance_from_nm: float | None = None
    enroute_distance_to_nm: float | None = None
    # Polygon outline as (lon, lat) vertices — kept for a future
    # cross-section/map overlay of the affected area.
    coords: list[tuple[float, float]] = Field(default_factory=list)


#: What every surface says for a block whose fetch failed (#734): the empty
#: list means "not checked", never "none". Web (i18n ``sigmets.unavailable``)
#: and iOS (``RouteSigmetsView``) carry their own copy of this wording.
SIGMETS_UNAVAILABLE_TEXT = (
    'SIGMETs unavailable at briefing time (source did not respond). '
    'Not checked, not "none": check SIGMETs before departure.'
)


class RouteSigmets(BaseModel):
    """SIGMETs affecting the route corridor (D-0 real-time info)."""

    corridor_nm: float
    fetch_time: datetime
    altitude_low_ft: int | None = None
    altitude_high_ft: int | None = None
    time_window_from: datetime | None = None
    time_window_to: datetime | None = None
    route_firs: list[str] = Field(default_factory=list)
    sigmets: list[SigmetAlongRoute] = Field(default_factory=list)
    # The SIGMET source's base query succeeded (#686). False: ``sigmets`` is
    # empty because the fetch failed, not because none are listed.
    fetch_ok: bool = True
    # When the source was successfully queried (now, then each lookahead step
    # up to the first failure), so a missing pending SIGMET can be told
    # cancelled from out of reach (euro_aip ``isigmet_covers``). None: unknown
    # (older packs, sources that do not report it).
    queried_at: list[datetime] | None = None

    @computed_field
    @property
    def count(self) -> int:
        return len(self.sigmets)

    @computed_field
    @property
    def hazards(self) -> list[str]:
        """Sorted union of hazard types across the matched SIGMETs."""
        return sorted({s.hazard for s in self.sigmets if s.hazard})

    @computed_field
    @property
    def has_severe(self) -> bool:
        return any(
            s.qualifier and s.qualifier.upper() == "SEV" for s in self.sigmets
        )


class RefreshDelta(BaseModel):
    """What got *worse* between the previous real-time state and this refresh.

    Deterministic (no LLM): drives the "conditions worsened since last update"
    banner. Improvements are intentionally not reported — the banner only warns.
    ``messages`` use language-neutral aviation shorthand (ICAO codes, flight
    categories, FIR/SIGMET ids) so they need no per-locale translation.
    """

    worsened: bool = False
    messages: list[str] = Field(default_factory=list)
    computed_at: datetime | None = None


class RealtimeRefreshResult(BaseModel):
    """Combined output of the cheap D-0 real-time refresh seam: fresh
    METAR/TAF observations plus route SIGMETs (issue #167 seam, #168 SIGMET)
    and re-sampled observed conditions (#574).

    ``observed`` re-reads locally-held radar/lightning/satellite frames rather
    than fetching, which is what lets it ride along on the cheap path.  It is
    ``None`` where the observed collector is not enabled.
    """

    observations: RouteObservations
    sigmets: RouteSigmets | None = None
    delta: RefreshDelta | None = None
    observed: ObservedConditions | None = None
    # Set when the refresh was persisted to the flight's live layer (#637):
    # the stored layer's timestamp and its changes since the briefing.
    live_updated_at: datetime | None = None
    changes: "LiveChanges | None" = None


from weatherbrief.models.live import LiveChanges  # noqa: E402  (cycle: live imports this module)

RealtimeRefreshResult.model_rebuild()
