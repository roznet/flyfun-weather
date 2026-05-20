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

---

## 3. GFS cloud diagnostics: window-midpoint interp + RH/condensate gate

**Date:** 2026-05-13
**Status:** Implemented (issue #148, PR #149)
**Context:** Investigation of a 14:00 Z snapshot at pt11 of flight
`lfrq_ercoz_jsy_revtu_tujag_rudmo_egtf-2026-05-17` showed
`nwp_cloud_diagnostics.mid.cover_pct = 100%` at FL180–222 while every
instantaneous signal at the same point disagreed: Open-Meteo bulk
`cloud_cover_mid_pct` = 33 %, GFS pressure-level RH at 500–450 hPa = 11–26 %,
CLMR + ICMR = 0, and GRAMET showed no mid deck. The pilot saw a phantom
layer that no other source supported.

### Background

Two compounding effects in the GFS pgrb2 product:

1. **Averaged cloud cover past f0.** NCEP publishes only the time-averaged
   form of LCDC/MCDC/HCDC (and the matching cloud-band PRES bottoms/tops) for
   forecast hours > 0. The averaging window has length 1/2/3 h depending on
   the step's position in the 3-h reset cycle (1 h at f001/f004/…, 2 h at
   f002/f005/…, 3 h at f003/f006/…/f120); past f120 it's always 3 h. There
   is no instantaneous variant in pgrb2. Our decoder already accepts the
   averaged form (`gfs_idx.py: parse_cloud_diag_idx`) and maps it onto the
   same internal slots as a hypothetical instantaneous (`decode.py:
   _CLOUD_DIAG_FIELD_MAP`).

2. **3-hourly cadence past f120.** GRIB enrichment snaps to native steps
   (… f120, f123, f126, f129 …). Gap hours between those steps used to
   inherit the preceding step's diagnostics via forward-fill.

For the pt11 case the chain was: f132 native step carries the average over
**09–12 Z** (100 % cover), forward-fill propagated that value across 13:00
and 14:00 Z, the next native step f135 (window 12–15 Z, 0 % cover) overwrote
15:00 Z. The 100 % at 14:00 Z was thus an **08–14 Z back-smear** of a window
that had ended two hours earlier.

ICON-EU and ECMWF publish instantaneous cloud cover and are not affected.

### The decision

Three coupled changes, GFS-only:

**A. Window-midpoint linear interpolation** (in
`src/weatherbrief/fetch/grib/fill.py`). When `gfs_init` is provided to
`propagate_all`, GFS cloud diagnostics are interpolated linearly between
native steps in **window-midpoint space** rather than forward-filled.
Each averaged native step's anchor sits at `step - window_length/2`. For
the pt11 case f135's midpoint is 13:30 Z, so 14:00 Z sits past it and
interpolates toward the next anchor's 0 % rather than holding f132's 100 %.

**B. Geometry hold-over with sub-5% drop.** `base_ft`, `top_ft`, and
`top_temp_c` are not numerically interpolatable — interpolating altitudes
between two dissimilar layer geometries would create phantom intermediate
layers. The gap-hour layer reuses the geometry of whichever bracketing
endpoint has the higher cover; when interpolated cover falls below
`_GFS_LAYER_DROP_THRESHOLD_PCT` (5 %) the entire `NWPCloudLayerDiag` is
emptied. Visually a dissipating deck thins toward zero and then drops out.

**C. RH/condensate gate** (`apply_gfs_rh_condensate_gate` in `fill.py`).
After interpolation, for each GFS hourly with both pressure_levels and
diagnostics, each low/mid/high band is rechecked: compute `max(RH)` and
`sum(CLMR + ICMR)` over the pressure levels falling inside `[base_ft,
top_ft]`. If the band has positive cover but `max(RH) < threshold` AND
`sum(CLMR + ICMR) == 0`, the layer is dropped. Per-band thresholds are
conservative starting values:

| Band | RH threshold |
|------|------:|
| low | `_GFS_GATE_RH_LOW_PCT` = 60 % |
| mid | `_GFS_GATE_RH_MID_PCT` = 70 % |
| high | `_GFS_GATE_RH_HIGH_PCT` = 70 % |

The gate requires at least one observed condensate value inside the band —
otherwise it cannot distinguish "no condensate" from "no data" and leaves
the forecast as-is. Convective and boundary bands are instantaneous in GFS
pgrb2 and not gated.

### Reasoning

1. **Time-truth of the averaged value.** A 3-h-averaged 09–12 Z cover
   carries no information about 14:00 Z. The cleanest way to use it is to
   place it where its time-mean actually applies — the window midpoint —
   and trust the next anchor to constrain the snapshot. Forward-fill silently
   asserted "this 09–12 Z mean still applies 2 hours after the window ended";
   midpoint interp makes the temporal logic explicit.

2. **Geometry interpolation creates noise.** Two adjacent native steps can
   report cloud at very different altitudes (deck rising, deck dissipating,
   different deck altogether). Linearly blending their `base_ft` / `top_ft`
   yields a halfway altitude that neither step supports. The higher-cover
   hold-over biases toward the better-resolved deck and the 5 % drop
   threshold prevents thin phantom layers from outliving their forecast
   support.

3. **The RH/condensate gate is the truth check.** Even with correct timing,
   the averaged cover can be inflated relative to instantaneous reality
   (e.g. when half of the 3-h window had cloud and half was clear). The
   pressure-level RH + CLMR/ICMR at the snapshot hour are instantaneous by
   construction. Requiring **both** sources to agree before declaring a
   cloud-free band keeps the gate conservative — a single moist or
   condensate-bearing level survives.

4. **GFS-only by design.** ICON-EU and ECMWF publish instantaneous cover.
   Applying the same gate there would compete with model physics rather
   than catch a pipeline artifact.

### What we decided against

**Dropping the averaged MCDC/LCDC/HCDC entirely** and re-deriving cloud
cover from instantaneous pressure-level RH + condensate (Sundqvist-style
diagnostic). This is the cleaner long-term end-state — no averaged-window
back-smear is possible if we never read the averaged value — but the change
surface is much larger (new diagnostic, new calibration against
observations, replaces the current GRIB-bulk path used by ceiling and other
downstream consumers). Tracked as a follow-up.

**Step-time linear interp without the midpoint adjustment.** This would
have fixed the forward-fill smear in one direction but still anchored the
3-h-averaged value at the step time, so 14:00 Z would have interpolated
between values that mis-represent their own time bracket. Midpoint
anchoring is the correct fix for averaged data.

### Real-world validation needed

- **Marine stratocumulus calibration of `_GFS_GATE_RH_LOW_PCT`.** Sub-grid
  inversion-trapped sheets can produce real low cover at pressure-level
  RH below 60 %. The conservative 60 % threshold may suppress these. A
  coastal SW UK / Brittany briefing on a stable-PBL day is the canonical
  test case.
- **Negative-control surveillance.** Confirm that strongly-supported decks
  (e.g. ERCOZ low layer 86.9 % @ 4781–10383 ft with RH 80–98 % and non-zero
  CLMR) survive both the interpolation and the gate unchanged.

### Files changed

- `src/weatherbrief/fetch/grib/fill.py` — `_fill_cloud_diagnostics` now
  branches on `gfs_init`: GFS sections use `_interp_gfs_diag_hourly`
  (window-midpoint linear), others fall back to `_fill_diag_hourly`
  (forward-fill). `_fill_cloud_water` similarly branches between
  `_interp_gfs_clw_hourly` (step-time linear) and `_fill_clw_hourly`
  (forward-fill). New `apply_gfs_rh_condensate_gate` runs after
  `propagate_all` and drops phantom layers per the gate rule.
- `src/weatherbrief/fetch/grib/__init__.py` — wires `gfs_init` through
  `propagate_all` and invokes `apply_gfs_rh_condensate_gate` after
  enrichment completes.

---

## 4. Convective risk: regime discrimination, realizable CAPE, and the DD/NWP boundary

**Date:** 2026-05-20
**Status:** Implemented (regime discrimination: PR #165, merged. Realizable-CAPE
tier + elevated flag: this change. DD-vs-NWP advisory comment: planned.)
**Context:** Investigation of `lsgs_odina_srn_adosa_chi_pul_ldlo-2026-05-25`
(Po Valley, 25 May). At pt15 the surface-based CAPE was high (GFS SB/MU 1125,
ECMWF 661 J/kg) and our convective rating read HIGH/MODERATE, yet **all three
models produced zero convective precipitation all day** — GFS CAPE climbed to
~3000 J/kg by 18 Z with no precip. A classic dry, capped, unforced air mass:
high latent instability the models never realize.

### Background — why surface CAPE over-reads

Our convective tier was scored on `effective_cape = max(SB, MU, ML)` against
European-calibrated thresholds (50 LOW / 300 MOD / 1000 HIGH / 2000 EXTREME),
modulated only by a strong-CIN suppression at CIN < −200. Since MU ≥ SB ≥ ML,
`max()` is effectively MU-CAPE — the *most optimistic* parcel. It ignores three
things the model's own convective scheme accounts for: the diurnal/forcing
*trigger*, the *cap* (CIN), and dry-mid-level *entrainment*. So a moist shallow
boundary layer under a dry mid-troposphere (SB high, mid-levels 20–30 % RH)
reads HIGH even though the column won't convect.

### The three CAPE parcels (and the Europe point)

- **SB** (surface-based): lift the surface parcel. Sensitive to a thin moist skin.
- **ML** (mixed-layer, lowest ~100 hPa): lift the layer mean — what a well-mixed
  daytime boundary layer actually realizes. The **Europe-preferred** measure for
  surface-based convection (ESSL/ESTOFEX), and European severe convection occurs
  at much lower CAPE than the US (our thresholds already reflect this).
- **MU** (most-unstable, max-θe parcel in lowest ~300 hPa): captures **elevated**
  convection (instability above the boundary layer). MU ≥ SB always; MU = SB ⇒
  surface-rooted.

For aviation at cruise both matter: ML answers "will surface storms initiate",
MU answers "is there an unstable layer aloft I could meet at altitude".

### The decisions

**(a) Regime discrimination (PR #165).** Classify from potential CAPE + CIN:
THERMAL (<300), WEAK_INSTABILITY (300–800), ACTIVE (≥800, cap weaker than −50),
LOADED_GUN (≥800, CIN ≤ −50). LOADED_GUN is held down one level unless 700 hPa
ω shows large-scale ascent (≤ −0.1 Pa/s) that could erode the cap; an
unassessable cap (no ω) is *not* downgraded.

**(b) Realizable-CAPE tier, scoped to ACTIVE only (this change).** The ACTIVE
regime is scored on **ML-CAPE** (realizable), floored at **one level below** the
potential tier, and **not** tempered when ω₇₀₀ shows ascent (ascent can realize
the latent CAPE). LOADED_GUN keeps scoring on **potential** — the cap, not poor
mixing, holds it, and a capped gun must never be dismissed on low ML (that is
the dangerous case). WEAK_INSTABILITY / THERMAL keep the potential tier with the
generic strong-CIN suppression.

**(c) Elevated-convection flag (this change).** `elevated_convection` fires when
MU − SB ≥ 200 J/kg and MU ≥ 300 — the most-unstable parcel sits above the
surface. It is an **additive warning** (driver string + bool), independent of
the surface tier, surfaced regardless of regime.

**(d) DD stays pure; DD-vs-NWP comparison lives in the advisory (planned).** The
thermo tier uses only **DD-derived** quantities (parcel CAPE variants, cloud
from RH) and **raw model state** (RH, ω — the inputs the DD track derives from).
Model-native *parameterized* diagnostics — `nwp_cape_jkg`, convective-cloud
cover — are deliberately **kept out of the tier**. The DD-vs-NWP divergence
(e.g. our parcel CAPE ≫ the model's own CAPE: GFS 1125 vs 470, ECMWF 661 vs 96)
is a real "model not realizing it" signal, but it is surfaced as a **comment in
the advisory layer** (`dd_nwp_agreement`), not blended into the per-model thermo
tier. Mixing the two tracks would blur a line the codebase intentionally draws.

### Reasoning

1. **ML is the honest realizable measure**; on the motivating case it drops GFS
   pt15 from HIGH (potential 1125) to MODERATE (ML 563), matching the model's
   zero precip. The SB→ML collapse *is* the dry/shallow-moisture story.
2. **Loaded gun on potential, safety asymmetry.** Under-warning (calling a real
   loaded gun "fine") is worse than over-warning. Hence: one-level cap on any
   downgrade, ascent-protection, ML absent ⇒ stay conservative, and loaded-gun
   never softened by ML.
3. **Scope to ACTIVE, not all regimes (Option B over A).** Because ML ≪ SB
   almost everywhere, ML-scoring *all* non-loaded-gun regimes pulled the whole
   route down ~one level (the one-level floor dominated, since ML alone is
   usually too low to set the tier). That is a sweeping recalibration disguised
   as a fix. Scoping to ACTIVE targets the surface-CAPE false-HIGH directly and
   warns more elsewhere — consistent with the safety asymmetry.
4. **Keeping nwp_cape out of the tier** preserves two independent derivations so
   their disagreement remains a usable diagnostic; folding it in would
   double-count and erase the signal `dd_nwp_agreement` exists to expose.

### Architecture note (necessary enabler)

MU-CAPE and ML-CAPE were computed in `compute_indices_extended` (heavy pass),
which runs *after* the convective assessment in `analyze_sounding_lite`. So the
tier saw `ml = mu = None` and silently scored on SB. Moved both into
`compute_indices_core` so the single convective call — used by **both** the
briefing pipeline and standalone verification — sees them. Consequence: the
convective tier now genuinely uses `max(SB, MU, ML)` as always intended (a few
low-CAPE points tick up where MU > SB), and standalone verification scores the
same logic the briefing uses.

### What we decided against

- **NWP-divergence gate inside the thermo tier** — rejected; mixes DD and NWP
  (decision d). The comparison belongs in the advisory.
- **Pure ML for the whole tier** — rejected; erases loaded-gun warnings (ML is
  low precisely when a cap is holding back high potential).
- **ML-tempering across all regimes (Option A)** — rejected; route-wide softening
  (reasoning 3). Revisit only as part of a deliberate threshold recalibration.

### Deferred / calibration items

- **`OMEGA_SUBSIDENCE = 0.05 Pa/s` likely too sensitive** — European anticyclonic
  summer subsidence is routinely 0.05–0.15; consider 0.10 to match the ascent
  threshold's "clear signal" spirit. Fold into the calibration pass.
- **CAPE=800 hard regime boundary** — 1 J/kg can flip a tier; the deferred
  continuous 0–1 score + calibration breakpoints is the right home.
- **Explicit mid-level-RH entrainment suppressor** — currently the SB→ML gap
  carries the dry-column story; a direct mean-600–850-hPa-RH annotation (threaded
  like ω) would name the mechanism. Deferred.
- **Open-Meteo `showers` (convective-only precip)** — not currently fetched;
  would give a precip-based corroboration signal cleaner than total QPF.
- **Cross-model NWP-consensus override** — belongs in the advisory aggregation
  layer (1 model HIGH while others dry → temper/annotate the aggregate).

### Real-world validation needed

- Did the Po Valley corridor (ODINA–ADOSA) actually stay convection-free on
  2026-05-25? Lightning/radar/METAR-TS would tell us whether ML-MODERATE beats
  SB-HIGH, or whether afternoon cells fired after all (which would argue for
  keeping more of the potential tier under weak forcing).
- Tune `ELEVATED_MU_SB_EXCESS` (200 J/kg) and the one-level floor against PIREPs /
  observed elevated convection.

### Files changed

- `src/weatherbrief/analysis/sounding/convective.py` — `_realizable_risk`
  (ACTIVE-only ML tier + one-level floor + ascent-protection),
  `_elevated_instability`, regime-scoped scoring in `assess_convective_thermo`.
- `src/weatherbrief/analysis/sounding/thermodynamics.py` — MU/ML-CAPE moved from
  `compute_indices_extended` to `compute_indices_core`.
- `src/weatherbrief/models/analysis.py` — `ConvectiveRegime` (+`label`),
  `regime`/`drivers`/`suppressors`/`elevated_convection` on `ConvectiveAssessment`.
- `src/weatherbrief/digest/{prompt_builder,text}.py` — render regime label +
  drivers/suppressors.
- `tests/test_convective.py` — regime, realizable-tier, floor, ascent, elevated,
  and loaded-gun-not-hidden coverage.
