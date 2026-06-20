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
tier + elevated flag: this change. DD-vs-NWP advisory comment: since shipped as
the dedicated `analysis/advisories/dd_nwp_agreement.py` advisory.)
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

---

## 5. GRAMET convective "tower" vs our NWP convective-cover: different GFS fields

**Date:** 2026-05-30
**Status:** Documented as-is (no algorithm change)
**Context:** Pilot review of `lsgs_sapre_djl_somda_vatri_dikol_lfqa-2026-05-31`
(LSGS→LFQA, pack 2026-05-30 12:50 Z) flagged that the GRAMET's convective
tower sits "much earlier in the flight" than where our analysis puts convective
risk. Asked to confirm we did the time/location interpolation right. The doubt
specifically targeted the **NWP convective** signal (the model's own convective
scheme), which the pilot believed GRAMET renders — not the DD/CAPE thermo risk
that drives the route-level "78% of route" advisory (see §4).

### Verification — interpolation is exact, no bug

The GRAMET footer cites `GFS RefTime 2026-05-30 06Z`; our pipeline used the same
run. We re-downloaded the raw GFS GRIB2 (`TCDC@convectiveCloudLayer`, init
2026-05-30 06 Z, f026/f027/f028 = valid 2026-05-31 08/09/10 Z) straight from
NOAA S3 and sampled it independently at the route points.

1. **Time interpolation — exact.** Each point is placed linearly by
   distance-fraction × duration from the departure time. That matches the
   GRAMET's *own* x-axis time labels to the minute (50 nm→08:25, 100→08:50,
   150→09:14, 200→09:39). LSGS 08:00 Z → LFQA 10:00 Z lines up.
2. **Location interpolation — exact.** Route-point NWP fields are sampled
   **directly from the GFS grid at each point's lat/lon** (bilinear, 4-corner) —
   `spatial_interpolation.py` only *gap-fills* `None`s, it does not lerp between
   waypoints. So a value between two waypoints can legitimately exceed both (a
   cell sitting between them). Every stored `convective_cover_pct` reproduced
   the raw GFS to within rounding (e.g. 153 nm: 08 Z 20.5 / 09 Z 57.0 / 10 Z 4.0).
3. **Per-point hour selection — correct.** SOMDA (186 nm) has a transient
   54.7 % convective-cover spike at 08 Z that collapses to 0 % by 09 Z. We reach
   SOMDA at 09:32 (→ forecast hour 10:00) and correctly read ~0 %, **not** the
   08 Z spike — demonstrating the time selection does not smear a transient cell
   onto a later arrival.

### The finding — it's a field/interpretation difference, GFS-internal

The apparent mismatch is not an error; it's two different GFS products that
genuinely disagree on *where* convection is:

- **Our `convective_cover_pct`** comes from GFS `TCDC@convectiveCloudLayer`
  (the convective scheme's cloud fraction). Along this route at flight time it
  is a modest ~19 % at 49–69 nm but **peaks at 53–57 % over 143–163 nm at 09 Z**,
  in a region where parcel CAPE is actually *low* (~160–260 J/kg).
- **The GRAMET's prominent white tower at ~50 nm** sits exactly where GFS has
  **high CAPE (~2066 J/kg at 69 nm)** and a deep cloud column — i.e. it tracks
  parcel buoyancy / deep-cloud depth, **not** the convective-cloud-fraction
  scalar. Our own *thermo/CAPE* convective agrees with the GRAMET here (thermo
  tier reads HIGH at 69 nm, early).

So GRAMET's tower aligns with our CAPE-derived view (early), while our
NWP-cover signal peaks later (~150 nm) because GFS's convective-cloud-cover
diagnostic disagrees with its own CAPE field there.

### Framing — what NWP and DD each mean

The two convective methods are, by definition, two different questions:

- **DD** = derived from the thermodynamic state (CAPE/CIN/LI/shear, cloud from
  RH). "Is the column buoyantly unstable?"
- **NWP** = what the model *natively* tells us about convection — its own
  convective-scheme output (GFS `convective_cover_pct`; ICON/ECMWF convective
  base/top geometry). "Is the model's own scheme producing convective cloud
  here?"

They are *supposed* to be able to disagree. This case is a textbook instance:
GFS's native convective cloud peaks mid-route (~150 nm, low CAPE) while the
buoyancy/deep-cloud view — which the GRAMET tower tracks — peaks early
(~50–69 nm). Surfacing that disagreement is the **product goal**: the app exists
to expose forecast complexity, uncertainty, and model inconsistency, and the
DD/NWP cross-section toggle plus `dd_nwp_agreement` advisory are how a pilot
sees it. So this divergence is the feature working, not a defect.

### Decision

**No code change.** `convective_cover_pct` is a faithful sample of the GFS
convective-cloud-layer field; the time/location placement is exact, so the NWP
signal is correctly located. The divergence from DD is real model
inconsistency, surfaced (not hidden) — exactly what we want.

**Known nuance (cross-ref §4d):** today the NWP convective *risk tier* is itself
derived from CAPE thresholds on every model path (GFS cover branch included) —
`convective_cover_pct` is attached as informational, and the convective base/top
provide geometry, but the **risk level** is CAPE. So the genuinely
model-native signal that exposes this divergence today is the `cover_pct`
diagnostic and the cross-section NWP cloud layer, **not** the NWP risk *level*
(which mostly tracks DD, by §4's design). Making the NWP tier itself
cover/geometry-driven — so "NWP = native" holds end-to-end and
`dd_nwp_agreement`'s convective category (currently near-circular) starts firing
on real divergence — was considered here and **deferred**: the existing cover
diagnostic + DD/NWP display already serve the goal, and a cover→risk mapping
needs PIREP calibration (areal cover ≠ intensity; ICON/ECMWF have geometry but
no cover). Tracked as a possible follow-up, not a correctness fix.

### Implications

- **Don't expect the GRAMET convective tower to match our `convective_cover_pct`
  geographically.** GRAMET's tower is the deep-cloud/CAPE view; our cover scalar
  is the convective-scheme fraction. They are different GFS fields and can peak
  tens of nm apart. (Same lesson as §2's "GRAMET is a black box" for icing.)
- **Bracketing invariant confirmed (incidental).** While investigating, the
  serialized 24 h cross-section was seen to forward-fill a constant convective
  cover past the last fetched native step. This is **benign**: all three GRIB
  paths fetch a step at/after arrival — GFS/ICON via `ceil(dur)+1+extra`
  (`compute_flight_window_hours` / `compute_icon_eu_flight_window_hours`), ECMWF
  via `flight_end + 3 h` margin — so every consumed route point is bracketed by
  real anchors and the forward-filled tail (hours past the flight) is never read
  by analysis. A defensive tidy-up (set trailing *instantaneous* convective
  fields to `None` rather than freeze them, per §3's philosophy) is deferred —
  cosmetic unless a consumer reads the post-arrival tail.

### Real-world validation needed

- Lightning / radar / satellite over the LSGS→LFQA corridor on 2026-05-31:
  did convection fire near the ~50 nm CAPE tower (early), or over the 143–163 nm
  convective-cover band (mid-route, low CAPE), or both? This would tell us which
  GFS field verified and whether the cover scalar is catching real elevated
  convective cloud or a scheme artefact in a low-CAPE column.

### Files (no changes)

Investigation only. Relevant code: `analysis/spatial_interpolation.py`
(gap-fill-only spatial interp), `fetch/grib/decode.py` (bilinear grid sampling),
`fetch/grib/fill.py` (time-axis interp + trailing forward-fill),
`analysis/sounding/convective.py` (`assess_convective_nwp` vs
`assess_convective_thermo`).

---

## 6. Front co-location: realized vs potential convection, and the parcel EL

**Date:** 2026-06-06
**Status:** Implemented (PR #217, #216).
**Context:** The experimental `fronts` advisory red-flagged a stable
high-pressure day (`lsgs_…_egtf-2026-06-07`) with "convective tops to FL272". A
single GFS 925 hPa θe crossing over Alpine terrain was co-located as
*convective* off a `risk_level="moderate"` that was CIN-capped potential CAPE
(CIN −59.5, ML-CAPE 147 J/kg, NWP LI −1), and its `weather_top_ft` was taken as
the **parcel equilibrium level** (27,233 ft) — clearing the deep-convection RED
cutoff (cruise + 15,000 ft) by 233 ft. No convection existed; the EL is high
simply because it's a deep summer troposphere.

### The decision

A front crossing is co-located as **convective only when convection is
*realized***, and the parcel EL is used as a tower-depth proxy **only on that
realized path**. Realized when:
- NWP convective cloud is present (`method != "thermo"`), **or**
- the cap is weak (CIN > −50 J/kg), **or**
- there is positive evidence of usable instability — ML-CAPE ≥ 300 J/kg or a
  lifted index ≤ −2.

A strongly-capped (CIN ≤ −50) thermo risk falls through to the cloud-coverage
category (wet/partly/dry) — and the EL is never used as a realized top — when both
available instability signals confirm the air mass is *not* realizable (ML-CAPE <
300 **and** LI > −2). This mirrors §4's realizable-CAPE logic, one level down: §4
stops surface CAPE over-reading the *route* convective tier; this stops the same
potential CAPE over-reading a *front's* relevance.

### Thresholds (front realized-gate)

- **CIN cap −50 J/kg** — moderate-cap boundary; ECMWF/GFS agree well at this scale.
- **ML-CAPE 300 J/kg** — ESSL "appreciable convection" anchor (same value as §4's
  MOD tier).
- **LI −2** — weak/moderate instability boundary (0…−2 stable/weak; < −2 moderate+).

Tuned on the single LSGS GFS case; revisit if a strong-front case disagrees.

### Two conservative biases (don't silently hide a front)

- **Unknown data → realized.** We downgrade only on *positive* evidence the
  instability is weak. When CIN is the sole available signal (ML-CAPE and LI both
  absent — e.g. ICON emits no lifted index), we keep the crossing convective.
- **Vertical coherence for overflown convection.** A convective crossing seen
  only on a single *below-cruise* θe level (the 925 hPa terrain case) needs a
  second level to RED; a free-atmosphere detection at/above the primary level
  still REDs single-level by depth.

The LI fallback (DD-derived → `nwp_lifted_index`) deliberately crosses the §4
DD/NWP tier separation: here LI is a binary gate signal, not a tier input, so the
mixing is acceptable.

### Where the logic lives (refactor, PR for #216 Fix 3)

The realized/potential decision is a public `convection_realized()` in
`analysis/sounding/convective.py`, reusing `REGIME_CIN_CAP` (−50) and
`REGIME_CAPE_LOW` (300) — the same anchors `classify_regime` uses — rather than
duplicating constants. This keeps it next to the convective tier and shareable.
The two answer different questions and must not be conflated: the **tier**
(`risk_level`) deliberately keeps a moderate cap's risk at its potential level
(WEAK_INSTABILITY only suppresses at CIN < −200), because a moderate-CAPE air mass
is worth flagging on the route; the **realized predicate** uses the stricter −50
cap, because using the parcel EL as a cloud top demands more confidence. Callers
own data extraction (incl. the DD→NWP LI fallback); the module owns logic + thresholds.

### Real-world validation needed

- A strong, genuinely active front (deep CAPE, weak cap) crossing the route at a
  low level: confirm it still REDs and that the EL-as-top proxy isn't suppressed.
- ICON / MeteoFrance partial-coverage points (CIN present, ML-CAPE/LI absent):
  confirm the unknown-data-→-realized bias behaves as intended.

### Files changed

`analysis/advisories/fronts.py` (`_grade_crossing`: overflown-convective
coherence gate), `tasks/fronts.py` (`_colocate`: realized gate via shared
predicate, EL only on realized path), `analysis/sounding/convective.py`
(`convection_realized`). Tests: `tests/test_tasks_fronts.py`,
`tests/analysis/advisories/test_fronts_advisory.py`, `tests/test_convective.py`.


---

## 7. Level-aware terrain masking for front detection

**Date:** 2026-06-06
**Status:** Implemented (PR for #216 Fix 3).
**Context:** Front detection masks grid cells where terrain generates orographic
θe gradients. The mask used a single flat threshold — terrain > **1500 m**
(≈ the 850 hPa surface) — applied to *every* detection level (925 / 850 / 700 hPa).
That is two-sided wrong, because the pressure surfaces sit at very different
heights:

| level | ISA height | flat-1500 m behaviour |
|-------|-----------|------------------------|
| 925 hPa | ~762 m  | **under-masks**: terrain 762–1500 m lets near-ground crossings through |
| 850 hPa | ~1457 m | ≈ correct (the threshold was calibrated here) |
| 700 hPa | ~3012 m | **over-masks**: terrain 1500–3012 m wrongly rejects genuine *free-atmosphere* fronts |

The 700 hPa over-masking is the worse failure — a **false negative** that hides a
real front, against this codebase's "never silently hide a front" bias.

### The decision

Mask a cell at level *P* when terrain reaches *P*'s standard-atmosphere height:
`terrain_m > pressure_hpa_to_altitude_m(P)`. The flat boolean mask is replaced by
a cached **elevation grid** (`terrain_mask_for_level(elevation, level)`), so the
*same* upstream change makes both `fill_terrain` (θe smoothing before gradients)
and the per-crossing terrain gate level-aware with no change to their logic.
925 hPa now masks terrain above ~762 m; 700 hPa only above ~3012 m.

NaN elevation (ocean / no SRTM) is always valid. A `buffer_m` knob can lower the
threshold if a sub-surface margin is ever wanted (default 0 = mask only at/above
the surface).

### Note on the triggering LSGS case

This does **not** change the 2026-06-07 LSGS crossing: it sits over Lake Geneva
(SRTM 370 m), where 925 hPa is *above* ground — a real shallow lake/pre-Alps θe
boundary, already handled by §6 (dry co-location → green). Fix 3 is an independent
detector-correctness improvement, not the fix for that bug. Its value is the
general 925 under-masking / 700 over-masking correction above.

### Migration

The precompute cache (`{DATA_DIR}/hewson/terrain_mask.npz`) now also stores the
elevation grid; a pre-elevation cache is treated as stale and rebuilt on next
precompute. Until rebuilt, the route path falls back to the flat mask (current
behaviour) — graceful, no manual wipe required.

### Real-world validation needed

- A genuine 700 hPa front over the Alps (terrain 1500–3000 m): confirm it now
  surfaces instead of being smoothed/rejected.
- Spot-check that low-level (925) detections over real high terrain (not lakes)
  are suppressed as intended.

### Files changed

`frontal/grid.py` (`build_terrain_elevation`, `terrain_mask_for_level`),
`hewson/precompute.py` (cache elevation, auto-rebuild pre-elevation caches),
`tasks/fronts.py` (per-level mask in the detection loop), `api/hewson_map.py`
(level-aware overlay). Tests: `tests/test_frontal_grid.py`,
`tests/test_tasks_fronts.py`, `tests/test_hewson_precompute.py`.

---

## 8. Review-driven fixes: descent escape, E-Shear units, negative Ri, IENG moisture

**Date:** 2026-06-11
**Status:** Implemented.
**Context:** A full meteorological review of the advisory + cross-section
approach ([meteorology-approach-review-2026-06.md](./meteorology-approach-review-2026-06.md))
surfaced four computation bugs. Each fix changes a calibrated output, so the
reasoning is recorded here.

### (a) Descend-below-icing: max() not min(), terrain floor, freezing-rain guard

`_descend_below_icing` computed `min(freezing_level, lowest icing-cloud base)
− 500` despite its own docstring saying max. **Either** condition alone exits
airframe icing — warm air (below the FZL, even in cloud) or clear air (below
the lowest icing-bearing cloud base, even sub-zero) — so the *higher* of the
two is the least-penalising valid escape; min() over-descended by thousands of
feet in winter profiles. Two guards added:

- **Terrain feasibility.** The per-point terrain elevation (SRTM profile,
  plumbed `pipeline → run_analysis → analyze_all_route_points`) marks an
  escape leaving < 1000 ft AGL as `feasible=False`. The meteorological
  altitude is kept (still true), only flyability is flagged — consistent with
  how `climb_above_icing` treats the service ceiling. The route-level
  `IcingEscapeEvaluator` already did its own terrain check; the per-waypoint
  advisory now agrees with it.
- **Freezing-rain guard.** A model whose precipitation profile sets
  `freezing_rain_risk` (warm nose over a sub-zero surface layer) has NO
  descent escape — "below the freezing level" is the sub-zero layer *under*
  supercooled rain, the worst place available. That model's escape is None;
  when all models flag FZRA the advisory renders with no altitude and
  `feasible=False` instead of a dangerous number.

### (b) E-Shear scale factors converted to the formula's calibration units

The CloudPath formula `E = (5·HWS + VWS² + 42)/4` is calibrated with VWS in
kt/1000 ft and HWS in kt/100 nm. The implementation scaled SI shear by 1e3/1e5
(m/s-per-km, m/s-per-100 km), which **overstated VWS ×1.69 (×2.85 squared) and
understated HWS ×3.6** relative to those units. Scale factors are now exact
conversions (≈592.5, ≈360 000) and `tests/test_e_shear.py` pins kt-unit
profiles to expected severities. Net effect: fewer VWS-driven E-Shear bands,
more weight on horizontal (jet-flank) shear — closer to the index's published
intent. Thresholds (40/80/160) unchanged.

### (c) Negative Richardson number stored; elevated unstable layers are CAT

`compute_stability_indicators` skipped Ri when N² < 0, so statically unstable
layers — classically Ri < 0.25 ⇒ turbulent, and N² < 0 the strongest case —
read as *missing data* and produced no CAT layer. Now stored (negative) and
classified **MODERATE**, with two deliberate caps:

- **MODERATE, not SEVERE**: buoyancy-driven overturning intensity belongs to
  the convective tier; the Ri path only asserts "turbulent layer here".
- **Surface-adjacent layer excluded** (index ≤ 1): a negative-Ri lowest layer
  is the routine daytime superadiabatic surface layer (thermals) — flagging it
  as CAT would paint summer-afternoon noise at the bottom of every
  cross-section.

EDR calibration is unaffected: `richardson_to_d` floors at `RI_FLOOR`, so a
negative Ri maps to the maximum diagnostic — consistent.

### (d) IENG convective term uses the level's real vapor density

`assess_icing_zones_ieng` passed `vapor_density=0.0` to
`_compute_convective_index`, making the cloud-base moisture differential the
maximum possible at every level — a constant convective inflation whenever
CAPE > 100 and `convective_cover_pct` was present. It now passes
`_vapor_density(lv.dewpoint_c)` like Ogimet-DD/NWP do, restoring the
moisture-*decrease* semantics of the Ogimet convective formula and making the
cross-method comparison (§2's table) apples-to-apples.

### Real-world validation needed

- (a): a winter stratus case (FZL well above cloud base) — confirm the new
  escape sits just below the FZL and reads sane against GRAMET; an Alpine case
  where the escape goes infeasible.
- (b): compare E-Shear band frequency before/after on a jet-crossing route;
  the expectation is fewer low-level VWS bands, occasional new jet-flank bands.
- (c): verify elevated negative-Ri CAT layers appear above frontal surfaces
  and NOT at the surface on hot afternoons.

---

## 9. Freezing-precipitation advisory: binary RED on active FZRA/PL, primed-profile AMBER

**Date:** 2026-06-11
**Status:** Implemented (`analysis/advisories/freezing_precip.py`).
**Context:** The review (§8's source) found the deadliest GA icing scenario —
freezing rain below cloud — was computed (`precipitation.py` warm-nose
detection, FZRA/PL surface phase) but surfaced by no advisory, and is
*structurally invisible* to the in-cloud icing methods (all four gate on
`is_in_cloud_layer`; FZRA happens beneath the deck).

### Grading choices

- **Any active FZRA/PL point → RED, no percentage threshold.** Freezing rain
  exceeds every icing certification envelope including FIKI; areal coverage
  doesn't temper it the way it does for in-cloud icing (one transit through a
  freezing-rain shaft is sufficient to be unrecoverable). Ice pellets grade
  identically: PL at the surface *proves* freezing rain in the layer above
  (refreeze completed before the ground).
- **Primed tier (AMBER)** — the freezing-rain profile *shape* (sub-zero
  surface wet-bulb under a warm nose, ≥2 warm levels) without active precip,
  re-checked from `derived_levels` via the now-public `detect_warm_nose`.
  Needed because `assess_precipitation` early-returns on dry hours, so
  `freezing_rain_risk` alone cannot warn about precip-onset timing risk.
  Coverage-gated (`primed_pct_amber`, default 5% ≈ one point) — the shape
  occurs benignly in dry warm-front pre-fields, so a single-point flicker
  shouldn't amber a route, but a corridor of primed profiles should.
- **UNAVAILABLE, never green-by-absence**, when a model has neither a
  precipitation assessment nor derived levels (old packs).

### Companion change

`_descend_below_icing` (§8a) consults the same `freezing_rain_risk`: a model
in FZRA has no descent escape.

### Real-world validation needed

- A verified FZRA event (e.g. METAR FZRA at an en-route airport) — confirm the
  route grades RED and the primed tier ambered the preceding hours.
- Winter inversion days without precip — confirm the 5% primed threshold
  doesn't over-amber routine warm-nose-shaped dry profiles; raise the default
  if it does.

---

## 10. VFR feasibility: climb-out / descent corridor check (vertical column vs cruise line)

**Date:** 2026-06-12
**Status:** Implemented (`analysis/advisories/vfr_feasibility.py`,
`_check_corridor_vfr`).
**Context:** The VFR feasibility advisory combined two checks — departure/arrival
flight category (METAR/TAF ceiling+visibility buckets) and en-route cloud
clearance **at cruise altitude** along the route. A real flight
(EGNE→GAM→OLNEY→EGTF, cruise 8000ft) read **GREEN "VFR conditions throughout"**
on all three models while both GFS and ECMWF showed a BKN/OVC deck around
3800–5350ft — below cruise. Cruise was genuinely clear and both airport ceilings
were just above the 3000ft VFR floor, so neither existing check fired.

### The gap

VFR is a *whole-flight, surface-to-cruise* constraint, not a cruise-line one. The
old model only ever inspected:

1. the airport ceiling **category** (a ground-level ceiling/vis bucket), and
2. cloud **at the cruise altitude** horizontally along the route.

Nothing checked whether the aircraft can physically **climb from the surface up
to cruise, and descend back down, in VMC**. A solid deck sitting between the
field and a clear cruise is invisible to both checks: the ceiling can still grade
VFR (lowest BKN base > 3000ft) while that same BKN/OVC deck blocks the climb-out.

### Decision

Add a third sub-check, folded into the existing worst-of aggregation. Within a
**terminal corridor** (`terminal_corridor_nm`, default **5nm**) of departure and
arrival, scan the full vertical column for a BKN/OVC layer whose **base is below
cruise and top is above field elevation** — a deck the flight must transit on
climb-out or descent. Grading:

- **OVC deck in a corridor → RED.** Overcast is >7/8 by definition — no holes to
  climb or descend through legally VMC. Categorical, like cruise-in-cloud.
- **BKN deck in a corridor → AMBER.** Broken usually has gaps; transit is often
  possible at pilot discretion, so it degrades rather than blocks.
- **SCT and below → no flag.** Scattered is VMC-transitable.

The field-elevation floor (nearest terrain sample from the elevation profile)
prevents a layer buried below the airport from false-triggering. The corridor is
deliberately **terminal-only**: a sub-cruise deck mid-route is irrelevant to VFR
because the aircraft cruises above it — only the climb and descent ends matter.

### Rejected / deferred

- **Replacing the cruise-line check** — no. The two measure different phases
  (cruise vs terminal transit); both are kept and aggregated worst-of.
- **OVC → AMBER (softer)** — rejected. An overcast deck is a hard VMC stop for
  climb/descent; calling it amber would under-warn the exact case that motivated
  this. Chosen explicitly: OVC=red, BKN=amber.
- **Treating SCT as a partial block** — deferred. SCT is legally transitable;
  revisit only if real cases show SCT decks routinely trap VFR climbs.

### Note

This is a deliberate sharpening: a flight that read GREEN can now read RED. On the
motivating flight, GFS and ECMWF both go RED (OVC in the EGNE climb-out; GFS also
BKN in the EGTF descent) while ICON — which shows no significant low cloud —
stays GREEN, preserving the model-disagreement signal.

### Real-world validation needed

- A day with a known low overcast deck under a clear cruise — confirm the route
  grades RED for climb-out and the digest/advisory names the blocking airport.
- A scattered-only terminal deck — confirm it stays GREEN (no over-flagging of
  routine VMC-transitable cumulus).
- Tune `terminal_corridor_nm` if 5nm proves too tight/loose against the real
  climb/descent footprint of GA profiles.

---

## 11. Four advisory additions: terminal convective, en-route precipitation, winds aloft, wave corroboration

**Date:** 2026-06-12
**Status:** Implemented.
**Context:** Follow-ups from the second advisory review (after
[meteorology-approach-review-2026-06.md](./meteorology-approach-review-2026-06.md)
items landed): terminal-area convective dilution, the missing en-route
visibility axis, no aggregation of the computed cruise headwind, and the
speed-only mountain-wind grade.

### (a) Terminal convective folded into Airport Weather, no coverage dilution

The en-route `ConvectiveEvaluator` grades by %-of-route — right for "can I
deviate around it", wrong at the airports where a deviation is not an option:
one MODERATE cell over the destination is ~5% of a 20-point route → GREEN.
`FlightCategoryEvaluator` now grades the worst convective risk within
`conv_radius_nm` (default 25 nm) of each end with **no percentage
threshold**: MODERATE → AMBER, HIGH/EXTREME → RED, no altitude filter (climb
and approach traverse every level). Folded into the existing Airport Weather
advisory (per product decision) rather than a separate card, so the airport
line reads e.g. "Arr LSGS: VFR, convective MODERATE nearby". MARGINAL/LOW
are ignored at the terminal — they amber half of summer otherwise.

### (b) En-route precipitation as the visibility proxy (snow ≫ rain)

No source provides visibility at altitude (parameterized vis is surface-only,
3/7 models), so precipitation phase+intensity — available for **all** models
via the existing per-sounding `PrecipitationAssessment` — is the honest
en-route visibility signal. Grading reflects GA reality: any snow over ≥5%
of the route → AMBER (vis collapses even in light snow showers; surface
phase is column-representative below the melting layer), moderate+ snow over
≥25% → RED, moderate+ rain over ≥30% → AMBER (rain capped at AMBER — it
degrades but rarely prohibits). FZRA/PL count toward extent only; their
severity stays owned by `freezing_precip` (§9) — no double-grading. The
classifier is shared into the VFR feasibility composite **capped at AMBER**:
a pilot VMC-on-top is not directly affected by surface rain, but widespread
snow degrades every descent/divert option. UNAVAILABLE (never green) without
precipitation data.

### (c) Winds aloft / trip impact: informational, TAS as a parameter

`RoutePointAnalysis.wind_components` (cruise headwind per point) fed only the
route graph. New `HeadwindEvaluator` averages it route-wide and estimates the
trip-time delta vs still air. Two deliberate choices:

- **TAS is an advisory parameter** (default 110 kt), not plumbed from the
  aircraft: keeps the advisory recalculable from a saved pack and tunable per
  profile; the headwind numbers are model truth regardless of TAS.
- **GREEN/AMBER-oriented** (AMBER at ≥20 kt mean, RED only ≥40 kt): wind is a
  planning factor, not a hazard — the value is the number, not the colour.
  Altitude-dependent (winds read from the cross-section at the evaluated
  altitude), so the altitude table now shows the wind trade per level.

### (d) Mountain wind: wave-signature corroboration, no fake cross-ridge term

Speed-only grading cannot separate "windy ridge" from "rotor day". The
evaluator now corroborates strong-wind mountain points with two signatures
already computed per sounding: an **inversion overlapping ridge top**
(−1000/+2000 ft band — the stable layer of the classical wave criteria) or an
**OSCILLATING vertical-motion classification** (model resolving the wave).
With a signature present, the RED bar drops from `wind_red_kt` (40) to
`corroborated_red_kt` (default 30). Cross-ridge wind *direction* was
considered and rejected: the elevation profile is 1-D along the route, ridge
orientation is unknown, and inferring it from the along-track terrain
gradient assumes perpendicular crossings — false precision. OSCILLATING
finally feeds an advisory (was computed and unused).

The upper band was tightened from +4000 ft to **+2000 ft** (post-merge review
of #242): classical wave theory places the critical stable layer at or just
above ridge level, and a +4000 ft window false-positively counts mid-level
frontal inversions (e.g. a layer at terrain + 3500 ft) as wave-supporting,
dropping the RED bar to 30 kt with no wave mechanism present. +2000 ft keeps
true summit inversions while excluding elevated frontal features; the Alpine
föhn validation in (d) below will confirm the tightened band still REDs real
wave days at 30–39 kt.

### Real-world validation needed

- (a) A summer afternoon flight with an isolated MODERATE cell at the
  destination ETA — confirm Airport Weather ambers while en-route convective
  stays green; check 25 nm radius against typical cell spacing.
- (b) A winter cold-sector day with scattered snow showers — confirm AMBER
  and that the 5% threshold doesn't flicker on a single mixed-phase point; a
  warm-sector stratiform rain day — confirm rain stays AMBER-bounded.
- (c) A strong jet day — sanity-check the minutes-delta against a flight
  planner for the same route/TAS.
- (d) A documented Alpine föhn/wave day (e.g. strong S flow over the main
  ridge) — confirm RED at 30-39 kt with the inversion signature, and that
  ordinary windy-but-neutral days stay AMBER.

---

## 12. Alternate-requirement TAF interpretation: conservative TEMPO/PROB handling

**Date:** 2026-06-13
**Status:** Implemented (deliberately stricter than the legal minimum)
**Context:** The regulatory alternate-requirement feature (#249, see
[alternate-requirement.md](./alternate-requirement.md)) reads the destination TAF
over the ETA−1h..ETA+1h window. TAFs carry conditional groups (TEMPO, PROB30,
PROB40, PROB.. TEMPO) and the rules for how a *private* pilot may treat them
differ between FAA Part 91 and EASA Part-NCO.

### The legal letter (per FAA Chief Counsel interpretations + EASA AMC)

- **FAA Part 91:** a **TEMPO** deterioration in the arrival window is legally
  binding (disqualifies the alternate); **PROB30/PROB40** lines do *not* legally
  disqualify — legality is read off the main body (steady-state + FM/BECMG).
- **EASA Part-NCO:** a **TEMPO** must be *considered* (assessed by expected
  duration and holding fuel, not an automatic bar); **PROB** lines (probability
  < 50%) can be disregarded as a hard legal barrier provided the main body is
  above minima. No buffer margins beyond the published planning minima.

### Our decision (conservative)

We intentionally do **not** implement the permissive legal minimum. For the
verdict we treat:

- **TEMPO → governing** (a dip below minima makes the field fail) for **both**
  regimes — we do not soften EASA TEMPO to "consider".
- **PROB40 → governing** (counts) — stricter than both regulators, who let a
  Part 91/NCO pilot legally disregard it.
- **PROB30 → advisory only** (surfaced, not counted in the verdict).

**Why.** The page is an attention-director, not a legal go/no-go machine. A
PROB40 TEMPO 0400 FG below minima is a real 40% chance of an un-landable backup;
flagging the field as failing is the safe default, and "legally valid" is not the
same as "operationally wise". The pilot is **not** kept in the dark: the
destination popup shows the steady-state (main body) conditions and lists every
TEMPO/PROB group in the window with how each was treated (`counted` vs advisory),
plus a note that the regulations may legally permit disregarding PROB lines and
assessing a TEMPO by duration/fuel. So the tool is conservative *and* transparent.

This deviation is the user's explicit choice (keep the conservative behaviour
rather than match the legal minimum). PROB30 disregard is the one place we are not
maximally conservative — it matches both the regulators and standard planning
convention, and PROB30 is still surfaced as advisory.

### Minima themselves (not a deviation — these follow the rules)

The minima values *do* follow the regulations: EASA destination trigger is
**NCO.OP.140** (ceiling ≥ DH/MDH + 1000 ft and vis ≥ 5000 m); EASA alternate
selection is **NCO.OP.143** (tiered DH+200/1500 m, DH+400/3000 m, or no-IAP
2000 ft/5000 m); FAA is 14 CFR 91.169 (2000/3 trigger, 600-2 / 800-2 alternate).
The conservatism above is only in the *conditional-group* handling and in
bracketing the unknown plate DH with a high-side proxy range.

---

## 13. Vertical linking of Hewson front lines across pressure levels

**Date:** 2026-06-19
**Status:** Implemented (experimental, gated by `auto_front_detection`); link-gate
calibration pending.
**Context:** Fronts are detected independently at 925/850/700 hPa
(`tasks/fronts.py`, see [hewson-fields-aviation-advisories.md](./future/hewson-fields-aviation-advisories.md)
§7.9.1). A front is a sloping surface, so the same boundary appears at all three
levels, displaced toward the cold air with height. We associate those per-level
crossings into one **chain** to (a) draw a single slanted front line on the
cross-section and (b) know the front's depth (shallow single-level vs. deep).

### Decision

`_link_front_chains` grows chains bottom→top, attaching a crossing to a chain one
level below it only when **all** hold: kind-compatible (cold↔cold / warm↔warm,
quasi wildcards), **same Δθe sign** (same air-mass-contrast direction), and within
a **frontal-slope budget** (925→850 ≈ 100 km, 850→700 ≈ 170 km, ×1.4 for warm
fronts). A **soft coldward prior** breaks ties (prefer the physically-normal slope
back over the cold air) but does not reject anti-slope links.

### Rejected alternatives

- **Distance-only clustering at `merge_km` (the prior `_stamp_vertical_coherence`).**
  Ignored front kind and Δθe sign (could link a cold front to a nearby warm one),
  and 60 km is too tight for warm fronts (shallow slope → 100–200 km between
  levels) so it under-linked them. Kept only its `vertical_levels` output, now
  derived from chain depth.
- **Hard directional (coldward) gate.** Rejected: with only 3 levels and ±50–100 km
  per-level positional uncertainty (§8), plus genuine occlusions that tilt the
  other way, a hard gate would drop real links. Used as a soft tie-break instead.
- **Per-metric θe bands as the cross-section front visual.** The original §7.9 plan;
  never built and not the right primitive for *front lines* (it shades a field, it
  doesn't draw the boundary). The slanted-line layer is what shipped.

### Honesty / open item

The slope budgets and the upright threshold are **physically-motivated defaults,
not validated numbers** — calibration against a known frontal case (Storm Ciarán,
the May-4 fronts) is still pending. With 3 levels the slant is a 2-segment sketch;
the clean version is the Phase D.2 GRIB stencil (detect in the along-route ×
pressure plane, continuity intrinsic, no association heuristic).
