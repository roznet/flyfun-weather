# What WeatherNext would need to provide for GA aviation briefing

> Requirements note prepared 2026-09-09 for a WeatherNext contact at Google, in
> response to "what would WeatherNext have to provide to be usable by your app?"
>
> Companion to [ai-weather-models-review.md](./ai-weather-models-review.md),
> which records why no AI model is currently integrated. This doc is the
> constructive half: the specific fields that would change that answer.

## What the app does, in one paragraph

Flyfun Weather produces medium-range (D-7 to D-0) route briefings for
general-aviation flights across Europe. A briefing runs 22 deterministic hazard
evaluators over ~20 points along the route, grades each green/amber/red **per
model**, and aggregates. Everything is altitude-resolved: a light aircraft
flying at FL080 needs to know what is at FL080 and whether climbing or
descending escapes it, not what the column average looks like. Current model
slots are ECMWF IFS (direct GRIB, 25 pressure levels), DWD ICON-EU and ICON-D2,
NOAA GFS and HRRR, plus UK Met Office, Météo-France and GEM via Open-Meteo.

The structural consequence: **a model slot owes a full vertical sounding.** A
surface-only feed cannot occupy a slot, because roughly two thirds of the
evaluators grade on the profile rather than on surface fields.

Field names below use ECMWF short names where one exists, since that is the
common vocabulary between us.

## The single highest-value thing WeatherNext could do

**Extend the station head beyond 2t/2d, to ceiling, visibility and gust.**

WN3 already predicts 2m temperature and dewpoint at 0.05° from a head trained on
in-situ station observations, and the paper shows it beating analysis-based
forecasts against METAR. But a METAR carries more than temperature and dewpoint.
It carries **cloud amount and base height per layer, horizontal visibility, wind
direction and speed, and gust**. Those are exactly the fields that every
physics model produces by parameterization, and exactly the fields those
parameterizations get worst.

Ceiling and visibility are the two numbers that decide whether a GA flight is
legal and safe to depart. Today we estimate them from a mix of model ceiling
diagnostics, dewpoint-depression profiles, and TAF at short range, and we
reconcile the estimates conservatively because we do not fully trust any of
them. A station head trained directly on the METAR ceiling and visibility fields
would be better than anything in our stack, and it is something no physics model
can match. If the answer to "what would make WeatherNext compelling for
aviation" has to be one item, it is this one rather than any of the fields
below.

Everything else in this document is, in effect, "match what ECMWF IFS already
gives us." This item is the only one where WeatherNext could be *better* than
what we have, and it plays to what the model already does well.

---

## Per advisory category

Each section lists what the advisory decides, the variables it consumes, where
we source them today, and what WeatherNext currently delivers.

### 1. Icing

**What it decides:** where supercooled liquid water exists in the layers the
aircraft will actually fly, how severe, whether a climb or descent escapes it,
and separately whether freezing rain is present (the case where the normal
escape, descending into warmer air, is exactly wrong).

| Variable | Needed for | Where we get it today | WeatherNext |
|---|---|---|---|
| `clwc` specific cloud liquid water content, per level | **Primary icing input.** SFIP fuzzy index; liquid-water-content severity bands | ECMWF `clwc` (25 lvl), ICON `qc`, GFS `CLMR`, HRRR `CLMR` | **Absent** |
| `ciwc` specific cloud ice water content, per level | Glaciation factor. High ice with low liquid means a mostly glaciated cloud and much lower icing risk | ECMWF `ciwc`, ICON `qi`, GFS `ICMR`, HRRR `CIMIXR` | **Absent** |
| `cc` fractional cloud cover, per level (0-1) | The in-cloud gate. Icing is only graded where the level is actually in cloud | ECMWF `cc`, ICON `clc` | **Absent** (WN3 has column band amounts only, not per level) |
| `t` per level | Icing temperature curve, peaks near -7 °C | all models | Present |
| `r` or `q` per level | Dewpoint depression, in-cloud proxy | all models | Present as `q` |
| `w` / omega per level | SFIP vertical-motion term | ECMWF, GFS, UKMO, ICON via GRIB | Present |
| `qr` rain water content + `t` | Supercooled rain and freezing-rain detection | ICON-D2 only | **Absent** |
| Freezing level, -10 °C and -20 °C levels | Icing zone boundaries | `deg0l` from ECMWF; else derived from `t` | Derivable only if levels get denser |

**Bottom line:** without `clwc` and `ciwc` the icing engine has no primary
input. We do carry a reduced-input variant that substitutes dewpoint depression
and band cloud cover for condensate, and in principle WN3's band amounts could
feed it, but that variant exists to degrade gracefully for a model that is
missing condensate on some levels, not to be a model's entire basis for grading
icing. We would not present it as a real icing assessment. Icing is the single
most consequential hazard for GA in a European winter, so a model slot that
grades it "none" because the fields are absent is worse than no slot: it
produces a falsely reassuring consensus.

### 2. Cloud, ceiling and VMC

**What it decides:** cloud layer geometry along the route (base, top, amount),
whether VFR above or below the layer is possible, and the ceiling at departure,
destination and alternates.

| Variable | Needed for | Where we get it today | WeatherNext |
|---|---|---|---|
| `cc` per level (0-1) | **Layer geometry.** Base, top and amount all derive from the vertical cloud-fraction profile | ECMWF `cc`, ICON `clc` | **Absent** |
| `ceil` cloud ceiling height | Airport ceiling, flight category | ECMWF `ceil`, ICON `ceiling`, GFS/HRRR `HGT`@ceiling | **Absent** |
| `cbh` cloud base height | Lowest base, VFR-below feasibility | ECMWF `cbh`, HRRR `HGT`@cloudBase | **Absent** |
| `lcc` `mcc` `hcc` `tcc` band amounts | Cover consensus, cloud-cover map layer | all models | **Present in WN3** |
| Per-band base / top / top temperature | Cloud-top advisory (can we get on top?) | GFS band geometry | Absent (ECMWF lacks it too) |

**Bottom line:** WN3's `lcc/mcc/hcc/tcc` are the one genuinely new
aviation-adjacent addition over WN2, but they are amounts with no heights.
"5 oktas of medium cloud" does not tell a pilot whether FL100 is on top of it,
in it, or below it, and the whole point of the cloud advisory is to answer that
question. Amounts without heights add a vote to a consensus we already take from
six models; they do not enable anything new.

The ask, in priority order: `cc` on pressure levels first (it yields base, top
and amount together), then `ceil` and `cbh` as surface diagnostics.

### 3. Convection

**What it decides:** thunderstorm risk along the route, and separately whether
the convection is avoidable in VFR (isolated cells you can see and route around)
or embedded in stratiform cloud (which for a GA aircraft means do not go).

| Variable | Needed for | Where we get it today | WeatherNext |
|---|---|---|---|
| CAPE, mixed-layer (100 hPa) and most-unstable | **Primary instability measure** | ECMWF `mlcape100` + `mucape`, ICON `cape_ml`, GFS/HRRR CAPE | **Absent** |
| CIN, with a stated sign convention | Capping. A strong cap suppresses an otherwise scary CAPE | ECMWF `mlcin100`, ICON `cin_ml`, GFS/HRRR CIN | **Absent** |
| Convective precipitation rate | Realization: is the scheme actually firing, or just unstable on paper | ECMWF `cp`, ICON `rain_con` | **Absent** |
| Convective cloud base and top height | Cell depth, whether it is toppable | ECMWF `hcct`, ICON `hbas_con` / `htop_con` | **Absent** |
| K index, Total Totals | Corroborating indices, model-native preferred | ECMWF `kx` / `totalx` | **Absent** |
| Composite reflectivity, echo top, lightning potential, max updraft | Explicit-convection realization on convection-permitting grids | ICON-D2 | **Absent** |
| 0-6 km bulk shear | Organization / storm mode | derived from `u`,`v` | Derivable, resolution permitting |

**Bottom line:** no CAPE at all is the second-largest gap after condensate.

One nuance worth passing on, because it pre-empts the obvious reply. We
deliberately keep each model's **own** CAPE alongside our MetPy re-derivation
from the delivered levels, and we flag when the two diverge by more than
200 J/kg. They diverge often. A model computes CAPE internally over 50 to 140
native levels; re-deriving it from 13 coarse pressure levels is a materially
different number, and at 13 levels the re-derivation is the less trustworthy of
the two. So "you can compute CAPE from `t` and `q`" is not an adequate answer at
the current vertical resolution. We would want the model's own value.

### 4. Turbulence, clear-air turbulence and mountain wave

**What it decides:** where the ride will be rough, in a category of aircraft
where moderate turbulence is a genuine control and airframe concern rather than
a spilled-coffee concern.

| Variable | Needed for | Where we get it today | WeatherNext |
|---|---|---|---|
| `u`, `v` per level | Vertical wind shear, Richardson number, E-shear index | all models | **Present** |
| `t` per level | Brunt-Väisälä frequency via potential temperature | all models | **Present** |
| `z` / geopotential height per level | Layer thickness Δz, the denominator of every shear term | all models | **Present** |
| `w` / omega per level | Strong updraft/downdraft detection | ECMWF, GFS, UKMO, ICON-GRIB | **Present** |
| `blh` boundary layer height | Separating boundary-layer shear from free-atmosphere CAT | ECMWF `blh` | **Absent** |

**Bottom line: this is the one category WeatherNext could nearly serve today**,
and it is the natural quick win. The blocker is not variables, it is vertical
resolution.

The Richardson-number tiers are scaled by each layer's own thickness. With 13
levels, the 1000-to-925 hPa layer is roughly 2,100 ft thick and 925-to-850
another 2,300 ft, so any shear structure inside those layers is averaged away.
Precisely the altitudes where a GA aircraft flies are the altitudes where the
grid is coarsest. Adding levels below 700 hPa would make this category work with
no new variables at all.

### 5. Surface and airport conditions

**What it decides:** flight category at each airport, crosswind against runway
geometry, density altitude for takeoff performance, low-level wind shear on
approach.

| Variable | Needed for | Where we get it today | WeatherNext |
|---|---|---|---|
| `2t`, `2d` | Density altitude, fog and low-ceiling proxy, LCL estimate | all models | **Present**, and at 0.05° station quality in WN3 |
| `10u`, `10v` | Runway crosswind and headwind components | all models | **Present** |
| `10fg` wind gust | **Crosswind limits for light aircraft are gust-driven, not mean-wind driven.** A 12 kt mean with 28 kt gusts is the no-go, and the mean alone does not show it | ECMWF `fg10`, all Open-Meteo models | **Absent** |
| `vis` horizontal visibility | Flight category, VFR legality | ECMWF `vis`, GFS, ICON, UKMO | **Absent** |
| Ceiling | Flight category | see §2 | **Absent** |
| `sp` surface pressure (not just `msl`) | Anchoring the sounding to the station, density altitude | all models | **Absent** (`msl` only) |

**Bottom line:** gust and visibility are cheap asks with disproportionate value.
Surface pressure rather than mean-sea-level pressure matters more than it
sounds: we anchor each profile on station pressure, and density altitude at a
2,000 ft Alpine strip on a hot day is a real performance question.

### 6. Precipitation and en-route visibility

| Variable | Needed for | Where we get it today | WeatherNext |
|---|---|---|---|
| `tp` total precipitation, hourly | En-route visibility proxy, precipitation extent | all models | **Present**, hourly at 0.1°, IMERG-trained |
| Precipitation type (rain / snow / freezing rain / ice pellets) | Freezing precipitation advisory | Derived from wet-bulb profile and warm-nose detection; ECMWF `ptype` delivered but not yet decoded | **Absent** |
| `sf` snowfall | Surface phase, runway condition | all models | **Absent** |

**Bottom line:** the hourly IMERG- and radar-trained precipitation is genuinely
attractive and is the strongest single-level field WN3 has. Phase is what is
missing, and phase is what turns precipitation from an inconvenience into the
freezing-rain case.

---

## Cross-cutting requirements

These decide whether any of the above is usable, independent of variable
coverage.

### 1. Vertical resolution below 700 hPa

13 levels, with a ~2,100 ft jump from 1000 to 925 hPa, is the constraint that
would still bite even if every variable above appeared tomorrow. Almost
everything a GA aircraft does happens between the surface and FL100, which is
roughly 1000 to 700 hPa. WeatherNext puts four levels in that band: 1000, 925,
850, 700.

For comparison, what we get today in that band: ECMWF direct GRIB gives 1000,
950, 925, 900, 850, 800, 700; ICON-EU model levels interpolate to 25 hPa
spacing; GFS gives 25 hPa spacing.

**Ask:** at minimum add 950, 900, 800 and 750 hPa. Ideally 25 hPa spacing below
700 hPa. This one change would make the turbulence category work immediately and
would materially improve every derived quantity, inversions and boundary-layer
diagnostics in particular.

### 2. Temporal resolution aloft

Upper-air fields are 6-hourly; only single-level fields plus `300/500 hPa t,z`
and `1000 hPa u,v` are hourly. Flights depart at arbitrary times and we grade a
specific flight window, typically two to four hours long.

We enforce a hard internal rule: **we never grade an hour whose fields were not
actually delivered for the model we are claiming.** Silently interpolating and
presenting the result as that model's forecast is a correctness bug we have
specific guards against. So 6-hourly upper air means we can honestly grade four
times a day and must refuse the rest.

**Ask:** hourly upper air, or 3-hourly as a middle ground.

### 3. Latency

WN3 interim cycles target init +7h10. That is comparable to ECMWF direct
delivery (+6h40) and much slower than ICON-D2 (+2h, 48 h horizon). The hourly
init cadence does improve *worst-case* forecast age, roughly 8 h against ECMWF's
12 h40 just before a new cycle lands, and that is a real benefit at medium
range. But for a flight departing this afternoon, the freshest regional model
still wins, and no amount of init frequency changes that.

### 4. Licensing

Real-time WeatherNext data (under 1 h old, or under 48 h on BigQuery) is
governed by the GDM Real-Time Weather Forecasting *Experimental* Data Terms of
Use; only the historical tail is CC BY 4.0. A briefing product that pilots use
to make go/no-go decisions is squarely the case those terms govern, and
"experimental" terms are difficult to build a safety-adjacent product on.

**Ask:** clarity on a licence that contemplates operational aviation use, or
confirmation that the Maps Platform Weather API is the intended commercial route
for this class of application.

### 5. Access shape

Upper-air fields are currently only on the Requester-Pays GCS Zarr bucket in
`us-east1`. We are a small European application running neither compute nor
storage on GCP, briefing a few hundred route points per flight.

**Ask:** a plain HTTPS point or vertical-profile query, of the shape Open-Meteo
already serves for WN2, is worth considerably more to us than bulk Zarr access.
Pulling a 64-member global ensemble across the Atlantic to sample 20 points is
the wrong shape for this workload.

---

## Ranked ask list

If only a few things are possible, in descending order of value to us:

1. **`clwc` and `ciwc` on pressure levels.** Unlocks the entire icing engine,
   which is the highest-consequence hazard we grade. Nothing else substitutes.
2. **More pressure levels below 700 hPa** (950, 900, 800, 750 at minimum).
   Makes turbulence and CAT work immediately with zero new variables, and
   improves everything else derived from the profile.
3. **`cc` fractional cloud cover on pressure levels.** Yields cloud base, top
   and amount together, and is the in-cloud gate the icing engine needs.
4. **CAPE (mixed-layer and most-unstable) and CIN**, as the model's own values
   rather than something we re-derive.
5. **Extend the station head to ceiling, visibility and gust.** Lower down this
   list only because it is a larger piece of work than the others; in terms of
   what it would be *worth*, it is first, and it is the only item where
   WeatherNext could beat every alternative rather than match them.
6. **Hourly upper air**, or 3-hourly.
7. **`ceil` and `cbh`** as surface diagnostics.
8. **`vis` and `10fg`.** Cheap, and both feed go/no-go decisions directly.
9. **Precipitation type**, and `blh` boundary layer height.

Items 1 to 4 and 7 to 9 are all "match what ECMWF IFS already delivers to us
today." Item 5 is the differentiator.

## What we would do with each level of coverage

- **Items 1 + 2 + 3 + 4:** WeatherNext becomes a full model slot alongside
  ECMWF, ICON and GFS, graded on every advisory, appearing in cross-sections and
  Skew-T diagrams, and contributing to the multi-model consensus. This is the
  outcome where an AI model is genuinely doing aviation work rather than
  supplying background dynamics.
- **Item 5 alone:** WeatherNext becomes the authoritative source for airport
  conditions (flight category, density altitude, crosswind) even with no
  sounding at all, feeding the airport consensus and the alternates engine
  directly. This is a smaller integration and a shorter path to being used in
  production.
- **Neither, as things stand today:** the 64-member ensemble remains interesting
  as a *confidence* signal at D-5 to D-7, where our current confidence measure is
  spread across six deterministic models, which is a proxy for uncertainty
  rather than a measurement of it. That is worth prototyping against Open-Meteo's
  WN2 feed regardless of what happens with the fields above, subject to the
  6-hourly-native caveat in §2.

## For our own reference

Internal cross-references, not intended for the external reader: the icing
engine is `analysis/sounding/{icing,sfip,icing_common}.py`; cloud geometry is
`analysis/sounding/clouds.py`; convection is `analysis/sounding/convective.py`
with grading in `analysis/advisories/convective_grading.py`; the field contracts
are `ThermodynamicIndices`, `NWPCloudDiagnostics`,
`NWPExplicitConvectiveDiagnostics`, `DerivedLevel` and `PressureLevelData` in
`models/analysis.py`. Per-model field attribution is tabulated in
[weather-engine-specs.md](./weather-engine-specs.md); metric-level detail and
the availability matrix are in [analysis-metrics.md](./analysis-metrics.md).
