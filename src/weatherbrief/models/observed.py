"""Observed-conditions payload models (issue #574, phase 1).

What a pilot can *see* along the route right now — radar reflectivity and rain
rate (OPERA), total lightning and satellite cloud tops (EUMETSAT) — sampled in
concentric annuli around each corridor station.

Phase 1 **displays observations only**: nothing here carries a verdict, and no
advisory reads it.  The cross-check is visual — ``observed-tops`` renders over
the NWP cloud bands, so "model says FL120, satellite saw FL280" is legible to
the eye without anyone computing it.

Three invariants shape these models, and each is load-bearing:

1. **Absence is three-state, per source.**  ``nodata_px`` (the sensor does not
   look here — 49.4% of the OPERA grid) is never folded into ``undetect_px``
   (the sensor looked and saw nothing).  A consumer that reads a missing value
   as zero is wrong in the first case and right in the second, so the counts
   travel with every value and :attr:`ObservedAnnulus.insufficient_coverage`
   says when the sample must not be asserted at all.
2. **No synthetic common timestamp.**  Each field carries its own frame's
   :attr:`~ObservedField.valid_time` and :attr:`~ObservedField.age_minutes`.
   A DBZH composite is a rolling 10-minute maximum plus delivery lag, so an
   on-screen echo can be ~15 min old — ~30 NM of own-ship at 120 kt.  Nothing
   in the payload lets a client pretend the four sources share an instant.
3. **``quality_method`` is a histogram, not a count.**  ``qm=9`` is the
   multi-layer-suspect flag; collapsing it into one "confidence" number
   destroys exactly the signal the "can I get on top?" question needs.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, computed_field

# Flight-level bins for the cloud-top histogram.  Chosen to match the bands a
# GA pilot actually reasons about: below the typical VFR cruise, the piston
# IFR band, the turbocharged/oxygen band, then jet levels.  Kept as a module
# constant so the client's legend and the server's binning cannot drift.
CLOUD_TOP_FL_BINS: tuple[tuple[str, float, float], ...] = (
    ("FL000-050", 0.0, 50.0),
    ("FL050-150", 50.0, 150.0),
    ("FL150-250", 150.0, 250.0),
    ("FL250-400", 250.0, 400.0),
    ("FL400+", 400.0, float("inf")),
)

# Below this fraction of *looked-at* pixels in an annulus, the sample describes
# too little of the circle to be asserted.  Radar coverage holes are large and
# hard-edged (49.4% of the OPERA grid is nodata), so an annulus straddling a
# coverage boundary can be "clear" on 8% of its area and unknown on the rest.
MIN_COVERAGE_FRACTION = 0.35


class ObservedAttribution(BaseModel):
    """Provenance for one observed field, read from the frame itself.

    Not a constant: the producer varies per frame.  One sampled OPERA
    composite was built by Météo-France rather than centrally by EUMETNET, and
    the ODIM ``how``/``what`` groups say so machine-readably.  We read it
    rather than hard-coding a label that would silently be wrong.
    """

    producer: str | None = None
    license: str | None = None
    url: str | None = None
    # Verbatim attribution line to render on the map overlay, the help-page
    # data-sources table and the PDF.  Built at collection time from the
    # fields above so every surface renders the same string.
    text: str = ""


class ObservedAnnulus(BaseModel):
    """One station × one radius × one field.

    The pixel counts partition the annulus exactly::

        total_px == valid_px + nodata_px
        valid_px == detected_px + undetect_px

    ``detected_px`` counts pixels carrying a real measurement above the
    product's detection floor; ``undetect_px`` counts pixels the sensor
    measured and found empty.  Statistics are computed over ``detected_px``
    only — averaging zeros from ``undetect`` pixels into a rain rate would
    report drizzle where the radar saw a clear sky.
    """

    radius_nm: float
    total_px: int = 0
    valid_px: int = 0
    nodata_px: int = 0
    undetect_px: int = 0
    detected_px: int = 0
    max_value: float | None = None
    mean_value: float | None = None
    p90_value: float | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def coverage_fraction(self) -> float:
        """Fraction of the annulus the sensor actually looked at."""
        if self.total_px <= 0:
            return 0.0
        return self.valid_px / self.total_px

    @computed_field  # type: ignore[prop-decorator]
    @property
    def detected_fraction(self) -> float | None:
        """Fraction of *looked-at* pixels carrying a detection.

        ``None`` when nothing was looked at — deliberately not ``0.0``, which
        a client would render as "clear".
        """
        if self.valid_px <= 0:
            return None
        return self.detected_px / self.valid_px

    @computed_field  # type: ignore[prop-decorator]
    @property
    def insufficient_coverage(self) -> bool:
        """True when the annulus must render as "no coverage", not as clear."""
        return self.coverage_fraction < MIN_COVERAGE_FRACTION


class ObservedTopsAnnulus(ObservedAnnulus):
    """Cloud-top annulus: adds the two histograms the tops question needs.

    ``fl_bins`` is the multi-layer picture CTTH's one-top-per-pixel commitment
    only reveals in aggregate — a cirrus-over-stratus stack shows as a bimodal
    histogram where any single pixel would have picked one layer arbitrarily.

    ``quality_method`` is the per-pixel height-assignment method, kept as a
    full histogram.  ``0`` means *no cloud* — a positive observation, not a
    failed retrieval — and is counted in ``undetect_px``.  ``9`` is the
    multi-layer-suspect flag.
    """

    fl_bins: dict[str, int] = Field(default_factory=dict)
    quality_method: dict[str, int] = Field(default_factory=dict)
    highest_fl: float | None = None


class ObservedFlashAnnulus(BaseModel):
    """Lightning annulus.

    Lightning is a point product, not a grid, so it has no ``nodata`` /
    ``undetect`` split: the instrument sees the whole disc.  Absence of
    flashes in the accumulation window is a real observation.
    """

    radius_nm: float
    flash_count: int = 0
    area_km2: float = 0.0
    window_minutes: float = 0.0
    nearest_flash_nm: float | None = None
    latest_flash_time: datetime | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def flashes_per_1000km2_per_min(self) -> float | None:
        if self.area_km2 <= 0 or self.window_minutes <= 0:
            return None
        return self.flash_count / (self.area_km2 / 1000.0) / self.window_minutes


class ObservedStationRef(BaseModel):
    """A corridor station the sampler measured around.

    Shared across fields so the four sources agree on *where* they sampled
    even though they disagree on *when*.
    """

    id: str
    name: str | None = None
    lat: float
    lon: float
    enroute_distance_nm: float | None = None
    distance_from_route_nm: float | None = None


class ObservedStationSamples(BaseModel):
    """All annuli for one station within one field."""

    station_id: str
    annuli: list[ObservedAnnulus] = Field(default_factory=list)


class ObservedTopsStationSamples(BaseModel):
    station_id: str
    annuli: list[ObservedTopsAnnulus] = Field(default_factory=list)


class ObservedFlashStationSamples(BaseModel):
    station_id: str
    annuli: list[ObservedFlashAnnulus] = Field(default_factory=list)


class ObservedFieldMeta(BaseModel):
    """Frame identity for one observed field.

    Every field carries its own valid time and age.  There is deliberately no
    payload-level "observed at" — see invariant 2 in the module docstring.
    """

    source: str
    quantity: str
    units: str = ""
    valid_time: datetime
    age_minutes: float
    # For products that are an accumulation or a rolling maximum rather than
    # an instant (DBZH is a rolling 10-minute maximum), the width of that
    # window.  ``0`` for instantaneous retrievals.
    window_minutes: float = 0.0
    attribution: ObservedAttribution = Field(default_factory=ObservedAttribution)


class ObservedField(ObservedFieldMeta):
    """A gridded observed field sampled at every station."""

    stations: list[ObservedStationSamples] = Field(default_factory=list)


class ObservedTopsField(ObservedFieldMeta):
    stations: list[ObservedTopsStationSamples] = Field(default_factory=list)


class ObservedFlashField(ObservedFieldMeta):
    stations: list[ObservedFlashStationSamples] = Field(default_factory=list)


class ObservedSummaryEntry(BaseModel):
    """One clause of the "Observed now" readout, with its provenance.

    ``kind`` names the source the clause came from so a client can pair it with
    that source's own frame age — which must never be blended across sources —
    and ``metric_id`` points at the metric-catalog card that explains it, so
    the row can carry the same (i) affordance as everything else on the page.

    ``coverage`` is the one kind that belongs to no single source: it reports
    how much of the corridor the radar could see at all, which qualifies every
    radar clause above it.
    """

    kind: str  # lightning | reflectivity | rain_rate | cloud_tops | coverage
    text: str
    metric_id: str = ""


class ObservedSourceStatus(BaseModel):
    """Why a source is missing, when it is.

    A source that is absent must say so distinctly from a source that is
    present and saw nothing — the same three-state discipline as the pixel
    counts, one level up.
    """

    source: str
    available: bool
    reason: str | None = None
    latest_valid_time: datetime | None = None


class ObservedConditions(BaseModel):
    """Observed conditions along the route corridor (D-0).

    Sits inline on ``briefing.json`` beside ``route_observations``, and is
    recomputed by ``run_realtime_refresh`` so the ↻ button updates it.
    Imagery is never in here — it is served from the frame store.
    """

    computed_at: datetime
    corridor_nm: float
    radii_nm: list[float] = Field(default_factory=list)
    stations: list[ObservedStationRef] = Field(default_factory=list)

    reflectivity: ObservedField | None = None
    rain_rate: ObservedField | None = None
    cloud_tops: ObservedTopsField | None = None
    lightning: ObservedFlashField | None = None

    # Deterministic "Observed now" readout — no LLM.  ``summary_entries`` is
    # the structured form: one clause per source, tagged with which source it
    # came from and which metric-catalog card explains it.  ``summary_lines``
    # is the same content as plain strings for the PDF and the digest, and
    # ``summary`` is those joined into a paragraph.
    #
    # The clauses are deliberately not uniformly shaped ("Radar: peak 38 dBZ…"
    # but "Rain rate to 1.8 mm/h…"), so a client that wants to render them as
    # per-source rows must not recover the source by parsing the prose.  That
    # is what the entries are for.
    summary: str = ""
    summary_entries: list[ObservedSummaryEntry] = Field(default_factory=list)
    summary_lines: list[str] = Field(default_factory=list)
    sources: list[ObservedSourceStatus] = Field(default_factory=list)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def has_any_field(self) -> bool:
        return any(
            f is not None
            for f in (self.reflectivity, self.rain_rate, self.cloud_tops, self.lightning)
        )
