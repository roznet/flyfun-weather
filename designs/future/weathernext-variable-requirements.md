# What WeatherNext would need to provide for GA aviation briefing

> Requirements note prepared 2026-09-09 for a WeatherNext contact at Google, in
> response to "what would WeatherNext have to provide to be usable by your app?"
>
> Companion to [ai-weather-models-review.md](./ai-weather-models-review.md),
> which records why no AI model is currently integrated. This doc is the
> constructive half: the specific fields that would change that answer.

## What the app does

Flyfun Weather produces medium-range (D-7 to D-0) route briefings for
general-aviation flights across Europe. A briefing runs 22 deterministic hazard
evaluators over ~20 points along the route, grades each green/amber/red **per
model**, and aggregates. Current model slots are ECMWF IFS (direct GRIB, 25
pressure levels), DWD ICON-EU and ICON-D2, NOAA GFS and HRRR, plus UK Met
Office, Météo-France and GEM via Open-Meteo.

Everything is altitude-resolved. A light aircraft cruising at FL080 needs to
know what is at FL080, and whether climbing or descending escapes it, not what
the column average looks like. The structural consequence: **a model slot owes a
full vertical sounding.** A surface-only feed cannot occupy a slot, because
roughly two thirds of the evaluators grade on the profile rather than on surface
fields.

The five asks below are in priority order. Field names use ECMWF short names
where one exists, since that is the common vocabulary between us.

---

## Ask 1. More pressure levels, specifically below 700 hPa

**This is first because it gates everything else.** Even if every variable in
asks 2 to 5 appeared tomorrow, they would arrive on a vertical grid too coarse
for the altitudes GA actually flies.

WeatherNext delivers 13 levels: `50, 100, 150, 200, 250, 300, 400, 500, 600,
700, 850, 925, 1000 hPa`. Almost everything a GA aircraft does happens between
the surface and FL100, roughly 1000 to 700 hPa. **WeatherNext puts four levels
in that band.**

| Model | Levels between 1000 and 700 hPa | Spacing |
|---|---|---|
| GFS (Open-Meteo) | 13 | 25 hPa |
| HRRR (direct GRIB) | 13 | 25 hPa |
| ICON-EU (model levels, interpolated) | 13 | 25 hPa |
| ECMWF IFS (direct GRIB) | 7 (1000, 950, 925, 900, 850, 800, 700) | 25-50 hPa |
| **WeatherNext** | **4** (1000, 925, 850, 700) | **75-150 hPa** |

In ISA terms the gaps are: 1000 to 925 is about 2,100 ft, 925 to 850 about
2,300 ft, and **850 to 700 is about 5,100 ft with nothing in between**. That
last gap spans roughly 4,800 ft to 9,900 ft, which is a large share of GA cruise
altitudes in Europe. A pilot asking "what is it like at 7,000 ft?" is asking
about the middle of a void.

**What more levels would unblock, with no new variables at all:**

- **Turbulence and CAT** become computable. See ask 5: the variables are already
  there, the resolution is not.
- **Inversions and boundary-layer structure.** A surface-based inversion two or
  three hundred feet thick is invisible at 75 hPa spacing, and it is exactly
  what produces the nocturnal low-level jet case: calm surface wind with 30+ kt
  just above it, benign on the surface chart and a genuine hazard on approach.
- **Freezing level and the -10 °C / -20 °C levels** become derivable to useful
  accuracy from `t`. At four levels below FL100 the interpolation error on the
  freezing level is comparable to the icing layer's own thickness.
- **Cloud layer edges** (ask 2) get real vertical precision rather than being
  quantised to three or four possible base heights.
- **Every parcel-derived quantity**, CAPE included (ask 3), gets closer to what
  the model computed internally.

**Ask:** at minimum add 950, 900, 800 and 750 hPa, which would take the
sub-700 band from four levels to eight and roughly match ECMWF's operational
delivery to us. Ideally 25 hPa spacing below 700 hPa, matching GFS, HRRR and
ICON. Above 700 hPa the existing 13-level set is adequate for our purposes.

---

## Ask 2. Fractional cloud cover on pressure levels (`cc`)

**What it unblocks:** the cloud cross-section, which is the single most-used view
in the app. A pilot planning a route looks at a distance-versus-altitude
rendering of where the cloud actually is, and decides whether the flight goes
over the top, underneath, or not at all. That picture is drawn from the vertical
cloud-fraction profile.

| Variable | Needed for | Where we get it today | WeatherNext |
|---|---|---|---|
| `cc` fractional cloud cover, per level (0-1) | **Cloud layer geometry: base, top and amount all derive from this one profile.** Drives the cross-section view, the cloud-top advisory, and the in-cloud gate the icing engine needs | ECMWF `cc` (25 lvl), ICON `clc` (model levels) | **Absent** |
| `ceil` cloud ceiling height | Airport ceiling, flight category | ECMWF `ceil`, ICON `ceiling`, GFS/HRRR `HGT`@ceiling | **Absent** |
| `cbh` cloud base height | Lowest base, VFR-below feasibility | ECMWF `cbh`, HRRR `HGT`@cloudBase | **Absent** |
| `lcc` `mcc` `hcc` `tcc` band amounts | Cover consensus, cloud-cover map layer | all models | **Present in WN3** |
| Per-band base / top / top temperature | Cloud-top advisory, "can we get on top?" | GFS band geometry | Absent (ECMWF lacks it too) |

**Why the WN3 band covers do not substitute.** WN3's `lcc/mcc/hcc/tcc` are the
one genuinely new aviation-adjacent addition over WN2, and they are amounts with
no heights. "Five oktas of medium cloud" does not tell a pilot whether FL100 is
on top of it, in it, or below it, and answering exactly that question is the
point of the cloud advisory. Amounts without heights add another vote to a
consensus we already take from six models; they do not enable a new view.

Note the interaction with ask 1: `cc` on the current 13-level grid would give us
cloud edges quantised to four possible heights below FL100. The two asks
compound, and `cc` on a denser grid is worth considerably more than either alone.

**Ask:** `cc` on pressure levels first, since it yields base, top and amount
together. Then `ceil` and `cbh` as surface diagnostics, which serve the airport
flight-category path where a full profile is overkill.

---

## Ask 3. A native convective signal, not only CAPE

**What it decides:** thunderstorm risk along the route, and separately whether
the convection is avoidable in VFR (isolated cells a pilot can see and route
around) or embedded in stratiform cloud, which for a GA aircraft means do not go.

CAPE and CIN are worth having and we would take them. But this is the ask where
the obvious answer is not the useful one, so it is worth being precise about
what we actually need.

### The calibration problem

CAPE measures *potential*. It does not tell you the atmosphere will convect,
only that it could if something lifts a parcel. Turning a CAPE number into a
fires / does-not-fire signal requires a calibration that is regional, seasonal,
and genuinely hard: European convection produces severe weather at CAPE values
that would be unremarkable over the US plains, so our own thresholds are
European-tuned and CAPE above 2000 J/kg is already exceptional over western
Europe. CIN helps, but a capped sounding that erodes by mid-afternoon and one
that holds all day look similar at 12z.

Our architecture already reflects this. We treat the model's own convective
scheme output as the **realization** channel and CAPE as the **potential**
channel, and we gate one on the other: a deep tower gets held down a severity
level when the model's convective precipitation and convective cover are dry. In
the code, the CAPE-only path is explicitly labelled a fallback and documented as
"parcel CAPE under another name, so it goes through the same
realized-versus-potential gate rather than being trusted as a native firing
signal." A model that gives us CAPE and nothing else lands in that fallback and
is graded conservatively, which is to say: not very usefully.

### What a native signal looks like

In descending order of value, what the models we integrate give us:

| Signal | Kind | Source today |
|---|---|---|
| Composite reflectivity, echo top, lightning potential, max updraft | Explicit convection on a convection-permitting grid. The strongest signal we have | ICON-D2 (2.2 km) |
| Convective precipitation rate | Realization: is the scheme actually firing | ECMWF `cp`, ICON `rain_con` |
| Convective cloud cover, base and top height | Cell presence and depth, whether it is toppable | ECMWF `hcct`, ICON `hbas_con` / `htop_con`, GFS |
| K index, Total Totals | Corroborating indices, model-native preferred over re-derived | ECMWF `kx` / `totalx` |
| CAPE (ML and MU), CIN | Potential. Gated by the above, never trusted alone | ECMWF `mlcape100` / `mucape` / `mlcin100`, ICON `cape_ml` / `cin_ml`, GFS, HRRR |

WeatherNext currently provides **none** of these.

### The opportunity specific to WeatherNext

WeatherNext is a 64-member ensemble. The signal that is hard to calibrate from
deterministic CAPE is trivially expressible from an ensemble: **a calibrated
probability of convective occurrence** at a point and time. "38% of members
produce deep convection here between 14 and 16 UTC" is directly actionable in a
briefing, needs no regional threshold tuning on our side, and is a better
product than anything the deterministic models give us.

That framing plays to what the model already is, rather than asking it to
reproduce a parameterization scheme it does not have. If a native convective
diagnostic is difficult, an ensemble convective probability may be both easier
for you and more useful to us.

**Ask, in order:** a convective occurrence probability from the ensemble;
failing that, convective precipitation rate plus convective cloud top, which is
the minimum realization channel; and CAPE (ML and MU) plus CIN alongside either,
as the model's own values rather than something we re-derive. On re-derivation:
we deliberately keep each model's own CAPE beside our re-derived value and flag
divergence above 200 J/kg, because a model computing CAPE over 50 to 140 native
levels and us re-deriving it from 13 coarse pressure levels are materially
different numbers, and at 13 levels ours is the less trustworthy one.

---

## Ask 4. Condensate on pressure levels, for icing

**What it decides:** where supercooled liquid water sits in the layers the
aircraft will actually fly, how severe it is, whether a climb or descent escapes
it, and separately whether freezing rain is present, which is the case where the
normal escape (descend into warmer air) is exactly wrong.

| Variable | Needed for | Where we get it today | WeatherNext |
|---|---|---|---|
| `clwc` specific cloud liquid water content, per level | **Primary icing input.** SFIP fuzzy index; liquid-water-content severity bands | ECMWF `clwc` (25 lvl), ICON `qc`, GFS `CLMR`, HRRR `CLMR` | **Absent** |
| `ciwc` specific cloud ice water content, per level | Glaciation factor. High ice with low liquid means a mostly glaciated cloud and much lower risk | ECMWF `ciwc`, ICON `qi`, GFS `ICMR`, HRRR `CIMIXR` | **Absent** |
| `cc` per level | The in-cloud gate. Icing is graded only where the level is in cloud (see ask 2) | ECMWF `cc`, ICON `clc` | **Absent** |
| `t` per level | Icing temperature curve, peaks near -7 °C | all models | Present |
| `r` or `q` per level | Dewpoint depression, in-cloud proxy | all models | Present as `q` |
| `w` omega, per level | SFIP vertical-motion term | ECMWF, GFS, UKMO, ICON via GRIB | Present |
| `qr` rain water content, with `t` | Supercooled rain, freezing-rain detection | ICON-D2 only | **Absent** |
| Freezing level, -10 °C and -20 °C levels | Icing zone boundaries | ECMWF `deg0l`, else derived from `t` | Needs ask 1 |

**Bottom line.** Without `clwc` and `ciwc` the icing engine has no primary
input. We do carry a reduced-input variant that substitutes dewpoint depression
and band cloud cover for condensate, and in principle WN3's band amounts could
feed it, but that variant exists to degrade gracefully for a model missing
condensate on some levels, not to be a model's entire basis for grading icing.
We would not present its output as a real icing assessment.

Icing is the highest-consequence hazard for GA in a European winter, and it is
the reason the honest answer to "can WeatherNext be a model slot today" is no. A
slot that grades icing "none" because the fields are absent is worse than no
slot: it produces a falsely reassuring consensus.

This ask sits below asks 1 to 3 only because it is the largest change to the
model's output set, not because it matters least. If condensate is achievable it
moves to the top.

---

## Ask 5. Turbulence: the fields are already there

This category is included to make a point rather than to ask for variables:
**WeatherNext could nearly serve it today.**

| Variable | Needed for | Where we get it today | WeatherNext |
|---|---|---|---|
| `u`, `v` per level | Vertical wind shear, Richardson number, E-shear index | all models | **Present** |
| `t` per level | Brunt-Väisälä frequency via potential temperature | all models | **Present** |
| `z` geopotential height, per level | Layer thickness Δz, the denominator of every shear term | all models | **Present** |
| `w` omega, per level | Strong updraft and downdraft detection | ECMWF, GFS, UKMO, ICON via GRIB | **Present** |
| `blh` boundary layer height | Separating boundary-layer shear from free-atmosphere CAT | ECMWF `blh` | **Absent** |

The blocker is not variables, it is ask 1. Our Richardson-number tiers are
scaled by each layer's own thickness, so with 2,100 ft and 5,100 ft layers the
shear inside them is averaged away, and precisely the altitudes where a GA
aircraft flies are the altitudes where the grid is coarsest.

**This is the cheapest concrete win in this document.** Granting ask 1 turns a
category from unusable to usable with no new prognostic variables, which makes
it the natural first step if one is wanted.

---

## Surface fields, not only pressure levels

Everything above asks for something on the vertical grid. Two surface fields
would be immediately useful on their own, need no 3D output at all, and are the
two numbers a pilot actually reads first.

### Ceiling

The height of the lowest broken or overcast layer above the field. Together with
visibility it sets the **flight category**, which is the single go/no-go number
in a briefing: whether a VFR pilot may legally depart or arrive, and whether an
IFR approach is likely to get in.

We estimate it today from a mix of model ceiling diagnostics, the
dewpoint-depression profile, and TAF at short range, and we reconcile those
estimates conservatively because we do not fully trust any one of them. A direct
`ceil` would replace that reconciliation with a number.

This also appears in ask 2, for a different reason. There it falls out of the 3D
cloud field and serves the en-route cross-section. Here it is wanted as a plain
surface diagnostic at the airport, and it would be worth having on its own even
if the 3D field never arrives.

### Visibility

Decides VFR legality directly, and with ceiling completes the flight category.
It is also the field most likely to be the reason a flight does not happen on a
European winter morning, when the ceiling is fine and the visibility is 1,200 m
in mist.

### The rest, briefly

| Variable | Needed for | Where we get it today | WeatherNext |
|---|---|---|---|
| `ceil` cloud ceiling height | **Flight category, the go/no-go number** | ECMWF `ceil`, ICON `ceiling`, GFS and HRRR `HGT`@ceiling | **Absent** |
| `vis` horizontal visibility | **Flight category, VFR legality** | ECMWF `vis`, GFS, ICON, UKMO | **Absent** |
| `10fg` wind gust | Crosswind limits for light aircraft are gust-driven, not mean-wind driven. A 12 kt mean with 28 kt gusts is the no-go, and the mean alone does not show it | ECMWF `fg10`, all Open-Meteo models | **Absent** |
| `sp` surface pressure, not just `msl` | Anchoring the sounding to the station, density altitude | all models | **Absent** |
| Precipitation type (rain / snow / freezing rain / ice pellets) | Freezing-precipitation advisory | Derived from wet-bulb profile and warm-nose detection | **Absent** |
| `tp` total precipitation, hourly | En-route visibility proxy, precipitation extent | all models | **Present**, IMERG-trained |

Surface pressure rather than mean-sea-level pressure matters more than it
sounds: we anchor each profile on station pressure, and density altitude at a
2,000 ft Alpine strip on a hot day is a real performance question.

---

## A separate opportunity: extend the station head

Everything above is, in effect, "match what ECMWF IFS already gives us." This
one is different, and it is the only item where WeatherNext could be *better*
than every alternative rather than match them.

WN3 already predicts 2 m temperature and dewpoint at 0.05° from a head trained
on in-situ station observations, and the paper shows it beating analysis-based
forecasts against METAR. But a METAR carries more than temperature and dewpoint.
It carries **cloud amount and base height per layer, horizontal visibility, wind
direction and speed, and gust**. Those are exactly the fields every physics
model produces by parameterization, and exactly the fields those
parameterizations get worst.

Ceiling and visibility are the two numbers that decide whether a GA flight is
legal and safe to depart. Today we estimate them from a mix of model ceiling
diagnostics, dewpoint-depression profiles and TAF at short range, and we
reconcile those estimates conservatively because we do not fully trust any one
of them. A station head trained directly on the METAR ceiling and visibility
fields would be better than anything in our stack.

It sits outside the ranked asks because it is a larger piece of work than any of
them and it serves a different surface (airports rather than the en-route
profile). In terms of what it would be *worth*, it is arguably first.

## For our own reference

Internal cross-references, not intended for the external reader: the icing
engine is `analysis/sounding/{icing,sfip,icing_common}.py`; cloud geometry is
`analysis/sounding/clouds.py`; the convective realization gate is
`convection_realized` and `_apply_firing_gate` in
`analysis/sounding/convective.py`, with grading in
`analysis/advisories/convective_grading.py`; the field contracts are
`ThermodynamicIndices`, `NWPCloudDiagnostics`,
`NWPExplicitConvectiveDiagnostics`, `DerivedLevel` and `PressureLevelData` in
`models/analysis.py`. Per-model field attribution is tabulated in
[weather-engine-specs.md](./weather-engine-specs.md); metric-level detail and
the availability matrix are in [analysis-metrics.md](./analysis-metrics.md).
