# Meteorology Design Decisions

Decisions around weather signal interpretation that have meaningful meteorological
trade-offs. Written so that a domain expert can review the reasoning and suggest
improvements.

---

## 1. Ceiling derivation: DD vs NWP-adjusted cloud layers

**Date:** 2026-04-01
**Status:** Implemented (DD chosen)
**Context:** Discovered via an inconsistency bug where the advisory showed LIFR/IFR
at an airport while the cross-section showed nearly clear skies at the same location.

### Background

The system computes ceiling from two independent sources, then takes the lower
(more conservative):

1. **Sounding-derived ceiling** — analyse the vertical temperature/dewpoint
   profile from the NWP model output. Where the dewpoint depression is small
   enough, flag a cloud layer with a coverage category (FEW/SCT/BKN/OVC).
   The lowest BKN or OVC base becomes the ceiling. When that layer starts at
   the first pressure level (surface), the Lifting Condensation Level (LCL)
   is used as a more physical cloud-base estimate.

2. **NWP cloud diagnostics ceiling** — some models (GFS, ICON) output their own
   cloud base/ceiling from their internal cloud parameterization. This is a
   direct model product, not derived from the profile.

The reconciled ceiling is `min(sounding_ceiling, nwp_ceiling)`.

### The cloud layer pipeline

The sounding analysis produces cloud layers in two stages:

1. **DD cloud layers** — pure dewpoint-depression detection from the vertical
   profile. Coverage (FEW/SCT/BKN/OVC) is assigned based on layer thickness,
   dewpoint depression magnitude, and number of consecutive saturated levels.

2. **NWP-adjusted cloud layers** — the DD layers are modified using the model's
   own bulk cloud cover percentages (`cloud_cover_low_pct`, `cloud_cover_mid_pct`,
   `cloud_cover_high_pct`). This adjustment can both *reduce* and *increase*
   coverage:
   - DD found BKN but model says 16% low cloud → downgraded to FEW
   - DD found SCT but model says 100% low cloud → upgraded to OVC

The cross-section visualization renders the NWP-adjusted layers. The advisory
ceiling uses the DD layers.

### The decision

**`sounding_ceiling_ft` is computed from DD cloud layers (stage 1), not
NWP-adjusted cloud layers (stage 2).**

### Reasoning

1. **Signal independence** — `reconcile_ceiling = min(sounding, nwp)` is designed
   to compare two independent estimates. If the sounding estimate already
   incorporates NWP cloud percentages, the NWP signal is double-counted: once
   in the sounding ceiling (via coverage adjustment), once in the NWP diagnostic
   ceiling. This can produce phantom ceilings that neither pure source supports.

2. **Observed artifact** — in the triggering case (EFNU→EEKE, 2026-04-02), GFS
   at EEKE demonstrated this problem:
   - DD detected SCT at 374ft (not enough moisture for BKN)
   - NWP `cloud_cover_low_pct` = 100% → coverage upgraded to OVC
   - LCL correction applied → ceiling = 1555ft (MVFR)
   - NWP diagnostic ceiling = 6348ft
   - Reconciled = min(1555, 6348) = 1555ft
   - This 1555ft ceiling was a hybrid artifact: DD alone would give 3179ft
     (BKN higher up), NWP alone would give 6348ft. Neither source predicted
     a 1555ft ceiling.

3. **Consistency** — the airport conditions code (`_ceiling_from_sounding()`)
   already used DD cloud layers for its fallback path. Having `sounding_ceiling_ft`
   use the NWP-adjusted version created a divergence where the same sounding
   could report different ceilings depending on code path.

4. **Serialization integrity** — DD cloud layers were not serialized to JSON
   (excluded to save space). On reload, they were reconstructed from the
   NWP-adjusted layers, losing the original DD coverage. This meant
   advisory results could change after a save/load cycle.

### What we decided against

**Using NWP-adjusted cloud layers for `sounding_ceiling_ft`** (the previous
behaviour).

Arguments that favoured this approach:

1. **Implicit false-alarm suppression** — for ECMWF, which lacks NWP cloud
   diagnostic output in our pipeline, there is no second estimate to reconcile
   with. The NWP cloud percentage adjustment was the only check on DD
   over-prediction. In the triggering case, ECMWF at EEKE had:
   - DD: BKN at 364ft (moist boundary layer)
   - `cloud_cover_low_pct` = 16% → NWP adjustment downgraded to FEW
   - Old behaviour: no BKN/OVC → `sounding_ceiling_ft` = None → VFR
   - New behaviour: BKN at 364ft → LCL correction → 920ft → IFR
   - If the 16% cloud cover is correct, the old result (VFR) was closer to
     reality and the new result (IFR) is a false alarm.

2. **More conservative in some cases** — the signal mixing could produce lower
   ceilings than either pure source (as in the GFS 1555ft case), which
   happened to be the cautious choice.

3. **DD over-prediction is a known issue** — a small dewpoint depression can
   reflect stable moist air that is not lifting to form cloud, or where
   turbulent mixing suppresses condensation. The model's cloud parameterization
   accounts for subgrid processes that a simple saturation diagnostic does not.

### Why we chose DD anyway

The old behaviour was accidental — it was conservative in some cases (ECMWF)
but created phantom ceilings in others (GFS). The conservatism was a side effect
of signal mixing, not a deliberate design choice. Better to have clean, predictable
logic and address the gaps explicitly:

- **ECMWF gap**: pending access to ECMWF full-precision GRIB data, which would
  provide cloud diagnostic ceiling as a proper second estimate.
- **Potential enhancement**: use `cloud_cover_low_pct` as an explicit third signal
  in `reconcile_ceiling()` rather than baking it into the sounding estimate.
  For example, if `cloud_cover_low_pct > 80%` and DD found any cloud near the
  surface (even SCT/FEW), that could inform the ceiling independently.

### Real-world validation needed

The triggering flight is EFNU→EEKE on 2026-04-02. Comparing METARs at EEKE
against the model predictions would ground-truth which approach was closer to
reality. Key questions:

- Did EEKE actually have low ceilings around 11Z? (DD prediction)
- Or was it clear/scattered as ECMWF's cloud scheme suggested?
- Did GFS's 100% low cloud verify, or was it a model bias?

### Files changed

- `src/weatherbrief/analysis/sounding/__init__.py` — `sounding_ceiling_ft`
  now computed from `dd_cloud_layers`
- `src/weatherbrief/tasks/artifacts.py` — `dd_cloud_layers` and
  `icing_ogimet_dd_zones` now serialized to `route_analyses.json`
- `src/weatherbrief/models/analysis.py` — comment update reflecting
  serialization change

---

## 2. Ogimet icing zone width: convective contribution at moderate CAPE

**Date:** 2026-04-26
**Status:** Documented as-is (no algorithm change)
**Context:** Pilot review of an LFLW→LEHC briefing flagged Ogimet icing zones
extending ~8500 ft (10000–18580 ft for ECMWF), much wider than what GRAMET
displays for the same conditions. Investigated to confirm the calculation is
correct.

### Background

The Ogimet icing index has two components, blended by CAPE:

```
raw = layered_frac · layered_index(T) + convective_frac · convective_index(T, ρv)
```

- `_compute_layered_index` (icing.py:70): parabola peaking at −7 °C, returns 0
  outside −14 °C ≤ T ≤ 0 °C.
- `_compute_convective_index` (icing.py:78): vapor-density-driven term,
  returns 0 outside −20 °C ≤ T ≤ 0 °C, requires moisture decrease from
  cloud base (`vapor_density_base − vapor_density > 0`).
- `_cape_to_cloud_split` (icing.py:100):
  - CAPE < 100  → layered=1.0, convective=0.0
  - CAPE < 500  → 0.8 / 0.2
  - CAPE < 1500 → **0.5 / 0.5**
  - else        → 0.2 / 0.8

The investigation case: CAPE = 509.6 J/kg → layered/convective split = 0.5/0.5.
At 500 hPa (T = −17.1 °C), `layered_index = 0` (out of the −14..0 range), but
`convective_index > 0` (moisture differential is positive, T = −17 °C is
within the −20..0 convective window). Half-weighted, the level is admitted to
the icing zone, pushing the top from 14012 ft (last layered-eligible level) to
18580 ft. **+4500 ft of zone width entirely from the convective term firing
on a stratiform-looking sounding.**

### Method comparison on the same flight (LFLW, ECMWF, 2026-04-26)

| Method | Width | Convective gate |
|---|---|---|
| Ogimet-DD | 8554 ft | Always on at CAPE > 100 — **no cloud-type or coverage gate** |
| Ogimet-NWP | 8554 ft | Same as DD |
| IENG | 3986 ft | Gated by `nwp_cloud_diagnostics.convective_cover_pct`. ECMWF often has this null → IENG falls back to layered-only |
| SFIP "full" | 8554 ft | Gated by per-level CLW > 0. Model says supercooled liquid is present at −17 °C → level qualifies. |

So three distinct visual widths fall out of three different convective gates,
all algorithmically correct for their respective definitions.

### Decision

**Keep Ogimet (DD and NWP) as-is.** The convective contribution at moderate
CAPE is part of the standard Ogimet formulation as we found it in the
literature; changing it muddies the algorithm provenance. Pilots who want
the narrower view can switch the icing method to IENG or SFIP-proxy in the
cross-section, both of which gate convective contribution more tightly.

### Implications

- **Ogimet zones can extend ~4500 ft above the layered (−14..0) range** when
  CAPE > 100 and there's a moisture gradient through the cloud. This will
  feel "wide" compared to commercial products like GRAMET that likely either
  (a) don't include a convective term, (b) require explicit convective cloud
  presence, or (c) cap the icing band at a narrower T range (e.g. −15..−2 °C).
- **Method-to-method variance is genuine signal**, not bug — the spread from
  3986 ft (IENG) to 8554 ft (Ogimet, SFIP-full) at the same waypoint reflects
  three different definitions of "is there icing here." The user-facing
  takeaway is: pick a method whose conservatism matches the operational
  context.
- **Cross-section visual mismatch with GRAMET is expected.** GRAMET is a
  black-box product with proprietary criteria; we shouldn't be surprised if
  our conservative Ogimet bands look fatter.

### Tightening options (rejected for now)

If real-world validation later shows Ogimet over-predicts:

1. **Mirror IENG's convective gate in Ogimet**: only fire the convective
   component when `convective_cover_pct ≥ threshold` (e.g. 25 %). On flights
   without explicit convective cloud the diagnostic is often null, so this
   would degrade Ogimet to layered-only most of the time. Tightest available
   change.
2. **Tighten convective T range**: cap the convective contribution to a
   warmer band (e.g. −15..−2 °C instead of −20..0 °C). Half-step compromise.
3. **Lower convective weight at moderate CAPE**: change the
   `_cape_to_cloud_split` table so that 500 J/kg gets, say, 0.3 weight
   instead of 0.5. Less invasive than (1) or (2) but harder to defend
   meteorologically.

All three are calibration choices that need PIREP-based validation before
landing. For now we surface multiple methods and let the operator choose.

### Real-world validation needed

PIREPs at altitudes where Ogimet says icing but layered-only does not (e.g.
−15 to −20 °C in cloud) would tell us whether the convective extension is
catching real icing or false-alarming. If most PIREPs at those altitudes
are RIME-only and weak, we'd lean toward option (2). If they're MIXED or
absent, option (1) is justified.

### Files (no changes)

Algorithm code in `src/weatherbrief/analysis/sounding/icing.py` is unchanged.
This entry exists to document the decision so the next reviewer doesn't
re-investigate the same width and conclude it's a bug.
