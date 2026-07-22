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

1. **Averaged cloud cover past f0.** NCEP publishes the time-averaged form of
   LCDC/MCDC/HCDC for forecast hours > 0, and publishes the matching
   cloud-band PRES bottoms/tops and TMP cloud-top temperatures *only* in
   averaged form. Because the geometry has no instantaneous variant, the cover
   must use the averaged form too, so the two share the same statistical
   processing and temporal alignment (`gfs_idx.py: _PREFER_AVERAGED_PAIRS`);
   `decode.py: _CLOUD_DIAG_FIELD_MAP` maps both onto the same internal slots.

   **The averaging window resets at every multiple of 6 and grows to the next
   reset** — `0-1`, `0-2`, … `0-6`, then `6-7` … `6-12`, and so on — so
   `window_length = fhour - 6*((fhour-1)//6)`, i.e. 1–6 h depending on step
   position. Past f120 the 3-hourly output cadence makes the widths alternate
   3 / 6 (`120-123`, `120-126`, `126-129`, `126-132`), and that pattern holds
   to the end of the run (`174-180`, `234-240`, `378-384`).

   > **Corrected 2026-07-22 (#480).** This section previously described a
   > repeating 1/2/3-hour cycle capped at 3 h past f120. That model was an
   > assumption, never checked against NCEP metadata, and it was wrong. The
   > values above are read directly from live `.idx` files. The error made
   > `_gfs_window_length_hours` misplace window midpoints by up to 1.5 h.

2. **3-hourly cadence past f120.** GRIB enrichment snaps to native steps
   (… f120, f123, f126, f129 …). Gap hours between those steps used to
   inherit the preceding step's diagnostics via forward-fill.

For the pt11 case the chain was: f132 native step carries the average over
**06–12 Z** (window `126-132`, 100 % cover), forward-fill propagated that
value across 13:00 and 14:00 Z, the next native step f135 (window `132-135`
= 12–15 Z, 0 % cover) overwrote 15:00 Z. The 100 % at 14:00 Z was thus a
back-smear of a window that had ended two hours earlier.

Note the two neighbouring windows are **not** the same width — f132 spans 6 h
because the window resets at f132 (a multiple of 6), while f135 spans 3 h.
Midpoint spacing is therefore uneven, which is why the anchor has to be
computed from the real window rather than assumed. (The original write-up of
this incident said f132 covered 09–12 Z, following the incorrect uniform-3 h
model corrected above.)

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
the forecast as-is. Convective cover is instantaneous in GFS pgrb2;
boundary-layer cover is published averaged-only and so is window-midpoint
aligned like low/mid/high (#441). Neither the convective nor the boundary
band is gated.

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
**Superseded in part by §18 (2026-07-16, #442):** reasoning 2's safety asymmetry
stays *inside the per-model DD thermo tier*, but its **advisory-level**
consequence — flooring the advisory *colour* to the DD tier — is being replaced
by an NWP-native grade with a DD-trigger AMBER cap (DD alone can no longer floor
the colour to red). The thermo tier itself is unchanged.
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
- **Open-Meteo `showers` (convective-only precip)** — *is* fetched and stored
  (`HourlyForecast.showers_mm`, `fetch/open_meteo.py`, `fetch/variables.py`);
  used in precipitation phase, and as the uniform cross-model realized-convection
  signal for the convective character advisory (§14). (Earlier text said "not
  fetched" — corrected 2026-06-24.)
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

**Known nuance (cross-ref §4d) — RESOLVED in §14 (2026-06-23, #283):** when this
was written the NWP convective *risk tier* was itself derived from CAPE
thresholds on every model path (GFS cover branch included) —
`convective_cover_pct` was attached as informational, and the convective
base/top provided geometry, but the **risk level** was CAPE. So the genuinely
model-native signal that exposed this divergence was the `cover_pct` diagnostic
and the cross-section NWP cloud layer, **not** the NWP risk *level* (which
mostly tracked DD, by §4's design). Making the NWP tier itself
cover/geometry-driven — so "NWP = native" holds end-to-end and
`dd_nwp_agreement`'s convective category (then near-circular) starts firing on
real divergence — was **deferred** here. **§14 implements it** (tower-top primary
scale + cover modifier, CAPE fallback only for models with no native scheme).

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

---

## 14. NWP convective track made model-native (tower-top driven, not CAPE)

**Date:** 2026-06-23
**Status:** Implemented Phase 1 + Phase 2 (#283). Phase 2 adds the
realized-convection firing gate, native-corroboration modifiers, the inline
cross-check re-key, the dd_nwp_agreement reconciliation, and decoding of the
ECMWF a1 native fields + ICON mixed-layer CAPE/CIN. Remaining decode gaps
(ICON `rain_con`, GFS `CPRAT`) are noted at the end — the analysis already
consumes them when present and the firing gate is missing-data-safe without them.
(ICON `rain_con` is now decoded — it lands under cfgrib shortName `crr`, not
`rain_con`; commit `60f7036b`.)
**Superseded in part by §18 (2026-07-16, #442):** the advisory-level "grade
floors at the DD (thermo) tier" behaviour introduced with this model-native track
is being replaced by an NWP-native grade + DD-trigger AMBER cap. The firing gate,
native corroboration, and inline cross-check described here are unchanged.
**Context:** §5 documented, and §4d's "two independent tracks" framing assumed,
that the NWP convective track is the model's *own* convective scheme. But
`assess_convective_nwp` set its **risk level from CAPE** on every path (GFS
cover / ICON hybrid / ECMWF hcct), identical to the DD thermo tier. The
model-native fields (GFS `convective_cover_pct`, ICON/ECMWF `convective_top_ft`)
were only *attached* as geometry/context — they never drove the risk. So
`convective_nwp.risk_level ≈ convective_thermo.risk_level` almost everywhere,
`dd_nwp_agreement`'s convective category (`_risk_distance >= 2`) was effectively
dead, and the "two independent assessments" were near-circular.

### The decision

`assess_convective_nwp` now derives its risk **from the model's own convective
scheme**, not CAPE, for every GRIB model that exposes native fields:

- **Primary scale — convective tower top → severity.** `convective_top_ft`
  (the one native field common to GFS, ECMWF and ICON; resolution-robust, and it
  separates shallow Cu from a mature Cb) maps to a tier via
  `_CONV_TOP_FL_THRESHOLDS` (FL380 EXTREME / FL280 HIGH / FL200 MODERATE /
  FL120 LOW / present-but-shallow MARGINAL).
- **Cover modifier (GFS).** When `convective_cover_pct` is present, numerous
  cover (≥35%) bumps the top-derived tier up one level, **capped at HIGH** —
  areal cover alone never implies EXTREME (that needs a ≥FL380 tower). When a
  model reports cover but *no* top, cover sets a depth-unknown tier capped at
  MODERATE.
- **Quiet scheme → NONE.** A native model with no convective top and no
  meaningful cover (e.g. ECMWF at a capped morning point — hcct sentinel, no
  convective cloud) reads **NONE**. It is returned as a real assessment (not
  `None`) so `dd_nwp_agreement` can compare it against a HIGH DD track.
- **CAPE fallback (`method="nwp_cape_fallback"`)** only when the diagnostics
  carry *no* native cloud content at all (a defensive/synthetic case — the
  GRIB builders return `None`, not an empty diag, in production; Open-Meteo-only
  AROME/UKMO/MF have no diag → `None` → no NWP track). The distinct method lets
  `dd_nwp_agreement` skip the now-circular DD-vs-NWP comparison, and
  `convection_realized` treats it like `thermo` (CAPE under another name, so it
  goes through the realized-vs-potential gate, not "native → realized").
- **Existing strong-CIN suppression kept** (CIN < −200 → one level down). Phase
  1 partial handling of a capped tower; the precip-based firing gate is Phase 2.
- The `method` strings consumed downstream (`"nwp"` / `"nwp_hybrid"` /
  `"nwp_lcl_top"`, plus front co-location's `method != "thermo"`) are preserved,
  and `base_ft` / `top_ft` / `cover_pct` are populated exactly as before.

### Guardrail — NWP-quiet must never downgrade DD (safety asymmetry)

`convective_method` defaults to `"nwp"`, so the aggregate convective advisory
(`analysis/advisories/convective.py`) grades on the now-native NWP track. A quiet
NWP track at a capped loaded-gun point (where models under-fire — §4 reasoning 2)
must **not** suppress a DD HIGH. The aggregate therefore floors the graded risk
at the DD (thermo) tier: `graded_risk = max(active, convective_thermo)`. The two
tracks stay independent — the divergence is **surfaced** (cross-check note +
`dd_nwp_agreement`), never blended into the DD tier (§4d). When the active track
is DD this is a no-op. The per-model thermo tier is untouched (DD stays pure).

### Reasoning

1. **Tower-top is the honest, resolution-robust native scale.** Precip *rate*
   thresholds are resolution-dependent (0.8 mm/h at ECMWF ~9 km ≈ 3–5 mm/h at
   AROME 1.3 km); tower top is instantaneous (no accumulation differencing) and
   comparable across models, so it is the primary scale and precip is reserved
   as a yes/no firing gate (Phase 2).
2. **Independence restores the diagnostic.** With NWP native, the Reims cases
   verify: GFS Sun (cover 46.8%, top FL332) → HIGH while ECMWF morning is capped
   → NONE, so the convective cross-check fires on the real divergence DD can't
   see (the inline `convective_cross_check`, not `dd_nwp_agreement` — see Phase 2).
3. **Safety asymmetry over purity for the *grade*.** Under-warning a capped
   loaded gun is worse than over-warning, so the aggregate floors at DD; the
   native view still drives the cross-section and the cross-check.

### Caveats / calibration (v1, not final)

- Thresholds reproduce every Reims case checked but are **a defensible v1** —
  tune against a labelled set via the eval-digest corpus replay.
- ICON-EU native fields vanish beyond 120 h (ECMWF 168 h, GFS 384 h); a model in
  fallback purely due to horizon has `nwp_diagnostics = None` → no NWP track,
  same as a non-native model.

### Phase 2 — firing gate, modifiers, and the inline cross-check re-key

**Firing gate (`_apply_firing_gate`).** A MODERATE+ tower is only kept there
when the model's own scheme *realized* convection — `convective_precip_mm_h >
0.1` OR `convective_cover_pct > 15`. A deep-but-dry tower (the capped / elevated
case) is held down one level. This is the native-side mirror of §6's parcel-EL
over-read fix. Crucially it is **missing-data-safe**: a not-realized tower is
held down only on *positive* dry evidence (precip ~0 or cover ≤ threshold), never
on absent data — so a model that simply doesn't emit precip (ECMWF before `cp`
lands, or the first ICON window hour with no predecessor step to de-accumulate
against) keeps its tower-top tier rather than being wrongly suppressed (safety
asymmetry). (ICON gained a `convective_precip_mm_h` signal from `rain_con` in
#421 — see "Remaining decode gaps" below.)

**Native corroboration.** A *realized* MODERATE+ cell whose own model-native
indices are strong (`k_index > 35`, `total_totals > 50`, or conv precip >
0.5 mm/h) is bumped up one level, capped at HIGH (only a ≥FL380 tower yields
EXTREME). These are the model's NATIVE kx/totalx/precip on
`NWPCloudDiagnostics`, not the DD-derived indices — the NWP track stays
independent. CIN suppression now prefers the model's own `ml_cin` when present.

**Inline cross-check re-key (the follow-up's primary ask).**
`convective_cross_check` (consumed by the convective advisory's per-point
`cross_check` note — *details-only, never grades*) previously keyed
`model_active` on bare convective-geometry presence, which would over-fire on
shallow Cu now that any tower is decoded. It is re-keyed to the native **firing**
signal: precip > the gate, cover ≥ 25%, or a tower ≥ FL200 ("active"); no precip
AND low/no cover AND no deep tower ("quiet"); the gap is intentionally neither.
This makes the two directions fire on the real Reims cases — Sat ECMWF (capped,
dry) → `dd_not_corroborated`; Sun GFS (cover 46.8%, FL332, DD marginal) →
`model_active_dd_quiet`.

**dd_nwp_agreement reconciliation (avoid double-reporting).** With the NWP
convective risk now native, the `dd_nwp_agreement` convective category
(`_risk_distance ≥ 2`) would report the *same* divergence as the inline
cross-check. Per the follow-up's preferred option, the **convective category is
removed from `dd_nwp_agreement`** (it stays focused on freezing-level + cloud
overlap); the richer, convective-specific inline cross-check is the single source
of truth. Documented in `designs/advisories.md`.

**Decoding.** ECMWF a1 delivers `cp`/`kx`/`totalx`/`mlcape100`/`mlcin100`
already (no extra download): `kx`/`totalx`/`mlcape100`/`mlcin100` are surfaced
instantaneously in `build_ecmwf_cloud_diagnostics`; `cp` is accumulated since
init, so its mm/h rate is computed by step-difference in the ECMWF merge loop
(mirroring `tp`/`sf`) and injected onto the diagnostics. ICON adds the
instantaneous `cape_ml`/`cin_ml` single-level products. New `NWPCloudDiagnostics`
fields are forward-filled automatically (fill.py `model_copy`) and added to the
spatial-interp `_lerp_diagnostics`.

### Remaining decode gaps (small, low-risk)

- **ICON `rain_con`** (convective rain) — ✅ RESOLVED (#421). Accumulated since
  init (kg/m² ≡ mm, already mm so **no** ×1000, unlike ECMWF `cp`); the mm/h rate
  is de-accumulated in the ICON cloud-diag merge loop, mirroring the ECMWF `cp`
  path. ICON has no ±margin (fetched on-demand exactly on window hours), so the
  merge prepends one leading single-level step (`icon_eu_previous_step`) to give
  the first window hour a predecessor to difference against. The firing gate and
  native corroboration now evaluate ICON towers. `SNOW_CON` (convective snow)
  stays deferred — omitting it is safe by construction (positive-dry-evidence gate).
  Validation caveat: the DWD product/URL was verified live against the opendata
  directory listing (per the issue), and the de-accumulation logic is covered by
  unit + mocked-decode integration tests. The cfgrib **decoded shortName** (the
  `rain_con` key the field map relies on) has not yet been confirmed against a
  real DWD GRIB pull — worth a one-time live-decode check when convenient, though
  it follows the same lowercased-shortName convention as the sibling single-level
  fields (`ceiling`, `clcl`, …) that already decode correctly in production.
- **GFS `CPRAT`/`ACPCP`** (convective precip): GFS always emits convective
  *cover*, which already drives the firing gate, so GFS precip is redundant for
  the gate. Deferred (the `.idx` byte-range + shortName needs validation).

### Caveat

The firing-gate / corroboration thresholds and the cross-check bands are a
**defensible v1**, not calibrated numbers — wire into the eval-digest corpus
replay. The convective-precip rate is resolution-dependent (per the issue's
gotcha), which is exactly why tower top is the primary scale and precip is only
a yes/no firing gate.

### Files changed (Phase 1 + Phase 2)

- `src/weatherbrief/analysis/sounding/convective.py` — native risk
  (`_CONV_TOP_FL_THRESHOLDS`, `_CONV_COVER_PCT_THRESHOLDS`, `_up_one`,
  `_native_convective_risk`, `_nwp_cape_fallback_risk`,
  `_has_native_cloud_content`), rewritten `assess_convective_nwp`,
  `convection_realized` fallback handling.
- `src/weatherbrief/analysis/advisories/convective.py` — DD-floor guardrail.
- `src/weatherbrief/analysis/advisories/dd_nwp_agreement.py` — skip the
  CAPE-fallback path in the convective comparison.
- `src/weatherbrief/models/analysis.py` — `method` doc (new values).
- `tests/test_convective.py` — native-top tiering, cover modifier + cap,
  cover-only scale, CIN suppression, quiet-native NONE, CAPE-fallback path, and
  the Reims regression anchors.

### Phase 3 — `cp` fires the NWP track without a tower; character prefers native `cp`

**Date:** 2026-06-26
**Status:** Implemented.
**Context:** EGTF→BIG→LFAT→LFQA 2026-06-27 (D-1). Over the Channel, ECMWF's own
scheme is precipitating convectively — `cp` peaks **4.26 mm/h at ~52 nm**, with
0.4–1.2 mm/h either side — matching what Windy shows. But the NWP track read
**NONE** the whole way across, and the convective-character advisory read ECMWF
**GREEN**. Two Phase-1/2 assumptions broke here:

1. **"No tower top ⇒ quiet scheme ⇒ NONE."** Phase 1 made `convective_top_ft`
   (`hcct`) the primary native scale on the premise that a firing scheme emits a
   tower top. ECMWF violates it: `hcct` is sentinel/absent across the entire
   Channel *even where `cp` = 4 mm/h*, and ECMWF has no convective-cover field.
   So `assess_convective_nwp` fell through to the quiet branch → NONE, with the
   `cp` we already decode sitting unused on the same diagnostics object. The
   Phase-2 firing gate only ever *holds a tower down*; there was no symmetric
   path to *lift NONE up* when `cp` fires without geometry. This is the marine /
   **elevated** convection case — the surface parcel has zero CAPE (DD quiet),
   so neither track saw it.
2. **Character "realized" read Open-Meteo `showers`, which is structurally 0.0
   for ECMWF IFS.** `showers_at_point()` returns Open-Meteo's convective-only
   precip, which Open-Meteo does not populate for ECMWF (verified: 0.00 at every
   point while total precip was 4.38 mm/h). So no ECMWF point could ever be
   "realized" by precip → GREEN.

**The decisions:**

- **`cp` is a first-class native firing signal, not just a binary gate.** When a
  native model has no tower top and no cover fraction but `convective_precip_mm_h
  > 0.1`, derive the NWP risk from a **convective-precip-rate ladder**
  (`_CONV_PRECIP_MM_H_THRESHOLDS`: ≥2.0 → MODERATE, ≥0.5 → LOW, ≥0.1 →
  MARGINAL), method `"nwp_precip"`. **Tower top stays primary whenever present** —
  this is the geometry-absent fallback only. Depth is unknown from rate alone, so
  the ladder is **capped at MODERATE** (same rationale as the cover-only scale),
  and a precip-derived tier skips the firing-gate hold-down and the precip
  corroboration (which would double-count `cp`).
  - *Why a ladder and not a binary floor:* the user explicitly chose to revisit
    the Phase-1 "precip is yes/no only" stance. Rate still carries real intensity
    information; the MODERATE cap and the resolution caveat below keep it honest.
  - *Resolution caveat (unchanged from Phase-1 reasoning 1):* convective-precip
    rate is resolution-dependent. These thresholds are calibrated for
    synoptic-scale GRIB (ECMWF ~9–25 km); a convection-permitting model whose
    `cp` gets wired later needs its own ladder. Defensible v1 — tune against the
    eval-digest corpus.
- **Convective-character "realized" prefers GRIB-native `cp` over Open-Meteo
  `showers`** for any model carrying `nwp_cloud_diagnostics`. A native value of
  `0.0` is a real "not firing" reading and is used as-is; only absent native
  diagnostics (non-GRIB models — AROME/UKMO/MF) fall back to the Open-Meteo
  `showers` cross-section field. This is the "ECMWF should use the GRIB variable,
  not Open-Meteo" principle (the documented intended direction for ECMWF surface
  fields, weather-engine-specs §Future-1).

**Effect on the EGTF→LFQA case:** ECMWF convective character GREEN → **AMBER
"Scattered cells" (20% of route)** under the shipped default; the NWP track now
reads MODERATE over the Channel `cp` cores, so the inline cross-check and the
per-model NWP cross-section reflect the firing the model actually forecasts. The
**graded severity colour is unchanged** over the Channel — §14's DD-floor
(`max(native, thermo)`) already graded it MODERATE off the elevated MU-CAPE — so
this is a narrative / character / track-independence fix, not a colour change
there. Safety asymmetry preserved: `cp` can only *add* a firing signal, never
downgrade a DD red.

**Files changed (Phase 3):**
- `src/weatherbrief/analysis/sounding/convective.py` —
  `_CONV_PRECIP_MM_H_THRESHOLDS`, `_risk_from_conv_precip`, `"nwp_precip"` branch
  in `assess_convective_nwp`.
- `src/weatherbrief/analysis/advisories/convective_character.py` — realized
  signal prefers native `convective_precip_mm_h` over Open-Meteo `showers`.
- `tests/test_convective.py` — precip-rate tiering, the ECMWF Channel regression
  (`cp` fires when `hcct` absent), and tower-top-stays-primary guard.

## 15. Convective character: a VFR-avoidability axis separate from severity

**Date:** 2026-06-24
**Status:** Implemented (issue #294). Thresholds are physically-motivated
defaults — PIREP/radar calibration pending.
**Context:** A RED convective advisory was narrating as "VFR impractical" even on
days of *isolated* cells in otherwise-clear air. Two real cases anchor it:
EDQT→EDDS 2026-06-16 (graded RED on 82–100% route coverage; reality was "few but
nasty" cells the pilot circumnavigated VFR), and `lsgs_…_dikol_lfqa-2026-05-31`
(anticyclone, no fronts, VFR observed, RED convective). The over-warning's root
cause: the convective advisory's "% of route affected" measures *environment
favorability* (CAPE ≥ MODERATE per point), not realized cell coverage — on a
loaded-gun day that is ~the whole route while realized cells are few.

### The decision

Add a **second, orthogonal axis** — convective *character* (VFR avoidability) —
computed per model and surfaced as its own graded advisory (`convective_character`).
**Severity still owns the colour**: the existing `convective` advisory is
unchanged, and a big cell still grades RED there regardless of character. The two
axes are deliberately kept separate (mirrors §4d DD/NWP separation): severity =
how bad a cell is (CAPE + shear + modifiers); character = whether you can operate
around it.

`ConvectiveCharacter`: NONE / ISOLATED / SCATTERED / WIDESPREAD / EMBEDDED /
ORGANIZED. Advisory colour: NONE→GREEN, ISOLATED/SCATTERED→AMBER,
WIDESPREAD/ORGANIZED/EMBEDDED→RED.

### Classifier — coverage-first (the key ordering)

Per `classify_convective_character` (`analysis/sounding/convective.py`):

1. **EMBEDDED** if a majority of convective points sit under a BKN/OVC deck
   (can't see the cells to avoid them).
2. **Realized-coverage band** from the % of route points with *realized*
   convection — `showers_mm` (uniform across all models), GFS `convective_cover_pct`,
   or ICON/ECMWF convective geometry. ≤15% isolated, ≤40% scattered, else widespread.
3. **K-index / Total Totals** (numerous-storm potential) nudge the band up one
   step, never down.
4. **Forcing** (front co-located / synoptic ascent / strong shear) only relabels a
   *widespread* band as ORGANIZED.

**Why coverage-first, not forcing-first:** EDQT had a trough axis and 28–38 kt
shear (forcing present) yet was avoidable because a cap held it to a few realized
cells. Forcing-first would have mislabelled it ORGANIZED→RED. Coverage-first keeps
it ISOLATED→AMBER, matching ground truth; a genuine squall line still goes RED
because it has *widespread realized showers*. Shear stays a *severity* signal
(it makes cells nasty, not numerous).

### Below-base clearance — an altitude *modifier*, not a band driver (#298)

The classifier above is altitude-agnostic — coverage sets the band. #298 adds a
below-base avoidability note **after** the band is decided, in the *evaluator*
(`_below_base_geometry`/`_format_below_base`), leaving `classify_convective_character`
untouched. It is **annotate-only**: it never changes the colour, and fires **only
on ISOLATED/SCATTERED** (the bands that are already "avoidable"). This mirrors the
severity-side overfly filter (`top_clearance_ft`, default 2000 ft) but from below,
reusing the same 2000 ft default as a tunable `base_clearance_ft`.

**The asymmetry (deliberate):** an above-tops buffer is a genuine vertical out —
you *overfly* in clear air. A below-base buffer is only "*more circumnavigable*"
(see-and-avoid under the cells), **never** a vertical out: precip shafts, gust
fronts/downbursts, and lowering bases/vis all live below cloud base. So below-base
relieves downward pressure on the note's wording but is not symmetric to overflying.

**The VMC gate (the load-bearing correctness check):** "below the bases" only buys
see-and-avoid if the layer you'd actually fly — cruise up to the cell base — is
genuinely VMC. `_vmc_below_base` returns False when a **BKN/OVC** cloud layer
overlaps that band (you'd be descending *into* cloud — the embedded case from
below). Only BKN/OVC breaks VMC (FEW/SCT is see-and-avoid-compatible); bulk
low/mid cover is **not** used here because at cumuliform bases it reflects the
cells' own cu and would over-suppress a genuinely clear sub-base layer.

**Tower-not-resolved → no softening.** When a realized cell's model-native base is
`None` — the `nwp_precip` ghost column (firing `cp`, no diagnosed tower), the
`nwp_lcl_top` path without an LCL, the CAPE fallback, or non-GRIB models — the
geometry is unmeasurable, so the note degrades to an honest *"cell depth
unresolved — below-base clearance not assessable"* rather than claiming a
clearance. **Precedence is safety-first**: within-layer (cruise inside a cell) >
deck-below-cells (IMC) > unresolved > the positive clear/marginal notes, so a
softer phrase can never mask a worse geometry, and a single unresolved or
non-VMC cell on the route suppresses the "more avoidable lower" hint.

### Signals, and the per-model asymmetry

- **`showers_mm`** is the uniform realized-convection signal (fetched for every
  model — corrected a stale doc note in §4 claiming it wasn't).
- **K-index / Total Totals** are coverage/numerosity indices, MetPy-derived for
  all models. ECMWF additionally delivers **native `kx`/`totalx`** (a1 GRIB, full
  IFS resolution); these are decoded into `nwp_k_index`/`nwp_total_totals` and
  **preferred over the MetPy values for ECMWF** (kept as a separate NWP signal,
  not folded into the severity tier). `kx` is delivered in Kelvin, so it is
  normalized to °C via `_k_index_to_c` (§14 / #283 established the unit) before
  the K≥40 nudge — feeding it raw would fire the nudge unconditionally; Total
  Totals is offset-immune. ICON publishes no native K/TT (its 28-level
  full-sounding replacement makes MetPy K/TT trustworthy anyway).

### Digest wiring + guardrail

The advisory flows into the digest context automatically; `briefer_v1.md` instructs
the LLM to let character drive *how it describes* convection (circumnavigable VFR
for isolated/scattered; impractical for widespread/organized/embedded) without
changing the colour. A conservative deterministic backstop
(`check_convective_vfr_consistency`) flags only a same-sentence convective-term +
absolute-VFR-impractical-term co-occurrence when the character advisory is AMBER —
narrow on purpose so it doesn't fire when VFR is impractical for cloud/airport
reasons.

### Real-world validation needed

- EDQT→EDDS 2026-06-16 should read ISOLATED/SCATTERED (AMBER); a documented
  squall-line/MCS day should read WIDESPREAD/ORGANIZED (RED); an embedded-CB-in-
  stratiform day EMBEDDED (RED). Tune `isolated_max_pct` / `scattered_max_pct` /
  `embed_pct` / `organized_shear_kt` and the `showers` point threshold against
  radar/lightning/PIREPs.
- ECMWF native `kx` is delivered in Kelvin (per §14 / #283) and is normalized to
  °C via `_k_index_to_c` before use; `totalx` is offset-immune and used as-is.
  (The earlier "pass-through, no conversion" assumption was corrected when #283
  and #294 merged.)

### Files changed

`models/analysis.py` (`ConvectiveCharacter`, `HourlyForecast.nwp_k_index/`
`nwp_total_totals`, `ThermodynamicIndices.nwp_k_index/nwp_total_totals`),
`analysis/sounding/convective.py` (`classify_convective_character`, `ConvCharPoint`),
`analysis/sounding/__init__.py` (copy native K/TT onto indices),
`analysis/advisories/convective_character.py` (new evaluator), `_helpers.py`
(`showers_at_point`), `advisories/strings.py`, `digest/guardrails.py`,
`configs/weather_digest/prompts/briefer_v1.md`, `fetch/grib/decode.py` +
`__init__.py` + `fill.py` (ECMWF kx/totalx decode + plumb). Tests:
`tests/test_convective.py`, `tests/test_digest_assertions.py`,
`tests/test_ecmwf_sample.py`.

**#298 below-base clearance (this iteration):** `ConvCharPoint` gains
`convective_base_ft`/`convective_top_ft`/`vmc_below_base`;
`analysis/advisories/convective_character.py` gains `_vmc_below_base`,
`_below_base_geometry`, `_format_below_base`, the `base_clearance_ft` param, and
the annotate-only wiring; `advisories/strings.py` adds 5 keys (en/fr/de/es).
Tests in `tests/test_convective.py` (`_below_base_geometry` / `_vmc_below_base`).
`classify_convective_character` is deliberately **untouched** (annotate-only).

---

## 16. Digest colour may step down for ISOLATED/SCATTERED convection

**Date:** 2026-06-26
**Status:** Implemented (digest prompt `briefer_v2.md`). LLM-briefer behaviour
change only — the deterministic `convective` and `convective_character`
advisories are unchanged. This is the *consumption* decision §15 deferred.
**Context:** §15 added the avoidability axis but deliberately left **severity
owning the overall colour** — `briefer_v1.md` said the character advisory "never
changes the overall GREEN/AMBER/RED". Pilot debriefs kept reporting the residual
over-warn on isolated-cell days, e.g. `edds_norfe…edkl` 2026-06-21: *"The
redflagging for VFR flights is strange. There have been some thunderstorms, also
heavy ones, but isolated and easily to circumnavigate. Nice VFR flight."* Two
compounding causes: (a) packs generated before §15 carried no character advisory
at all; (b) even with it present, the prompt forbade it from moving the colour,
and the `conservative` guidance preset independently reads "a single RED aggregate
advisory is a strong signal toward RED".

### The decision

In the digest prompt the two convective axes are **weighed together for the
overall colour**:

- Activity RED + Character **ISOLATED / SCATTERED** → overall **AMBER** on
  convection alone (a highly-localised, avoidable hazard), **unless another
  advisory is independently RED** (VFR Feasibility, Cloud Tops, Icing) — then the
  RED is attributed to that actual cause, not to convection.
- Character **WIDESPREAD / ORGANIZED / EMBEDDED** → overall **RED** on convection
  stands.
- **Fallback when no character advisory is present** (older packs, or a model
  without one): if CAPE-derived risk is RED/HIGH but the models' own convective
  cover is ~0% / flagged "not corroborated" in the per-model cross-check, treat
  as isolated and uncertain-to-trigger → AMBER.

The deterministic advisories are untouched; only the LLM's colour synthesis
changes.

### Validation (A/B on real prod packs, sonnet-4-6 @ T=0)

Regenerated each pack's context with current code (so the character advisory is
present), held it constant, and replayed through v1 vs v2:

- **16-pack "TS=better" debrief cohort** (pilots reported convection milder than
  forecast): v1→v2 flipped **7 RED→AMBER with zero spurious moves** (no upgrades,
  no non-convective downgrades). A further ~6 were already corrected by the mere
  presence of the character advisory (they pre-dated §15).
- **Safety — big systems held RED:** every sampled EMBEDDED pack (`klit_klnk`
  2026-04-24 d3/d4, `lfqa_djl_lsgl_lipv` 2026-06-02 d1) stayed **RED** under v2;
  the one WIDESPREAD pack was already AMBER and stayed AMBER. The
  WIDESPREAD/ORGANIZED/EMBEDDED→RED branch is intact.
- Sample skew: of 16 sampled convective-RED packs, ~11 grade isolated/scattered,
  ~4 widespread/embedded — the change only relaxes the isolated/scattered
  majority.

### Known weakness / follow-up

A `ifr_feasibility` (or other feasibility advisory) that is *itself*
convection-derived can still anchor the colour at RED — observed once
(`edds_norfe…edkl` d2 held RED while its five sibling leads flipped). The guard
should clarify that a feasibility advisory whose own driver is the convection
does not count as the "independent" RED. Deferred (needs its own re-validation).

### Rejected options

- **Make character lower the deterministic `convective` colour** (in the advisory
  layer): rejected — the advisory chip RED ("dangerous convection on route") is
  correct as a hazard signal; the over-conservatism is in the *narrative
  synthesis*, so the fix belongs in the digest, preserving §15's
  severity/character separation.
- **Relax the `conservative` guidance preset instead:** rejected — would broaden
  far beyond convection; the convective exception is specific and lives better as
  a base-prompt rule.

### Files

`configs/weather_digest/prompts/briefer_v2.md` (new active prompt; `briefer_v1.md`
kept for rollback/diff), `configs/weather_digest/{default,openai}.json` (repointed
`briefer` → v2). Builds on §15 (`analysis/sounding/convective.py`
`classify_convective_character`).

---

## 17. Wet-bulb precipitation phase boundaries realigned to the melting-physics convention

**Date:** 2026-07-01
**Status:** Implemented.
**Context:** The full validation review (testing-accuracy-review.md session 2) found
the per-level wet-bulb phase classifier (`precipitation.py
_level_phase_from_wet_bulb`) used bands shifted several degrees cold of the
literature: `Tw < −5 °C → SNOW`, `−5 ≤ Tw < 0 → MIXED`, `Tw ≥ 0 → RAIN`. Both
edges were physically wrong: falling snow does not melt at sub-zero wet-bulb
(evaporative cooling holds the hydrometeor at Tw, so there is no "partial
melting" at Tw −3 °C), and snow routinely survives to Tw ≈ +1 °C, so labelling
Tw +0.5 as pure RAIN dropped real wet-snow situations out of the snow band.

### The decision

One convention for both the per-level classifier and the surface fallback
(which already used it): **melting begins at Tw > 0 °C and completes near
Tw ≈ +1.3 °C** (Matsuo & Sasyo 1981; the same wet-bulb convention used in
common NWP precipitation-type post-processing).

- `Tw < 0.0 °C → SNOW`
- `0.0 ≤ Tw ≤ 1.3 °C → MIXED` (melting band / wet snow / sleet)
- `Tw > 1.3 °C → RAIN`

Constants `_TW_SNOW_MAX_C = 0.0` / `_TW_RAIN_MIN_C = 1.3`; the surface-phase
fallback in `_determine_surface_phase` now calls the shared classifier (its
values were already 0/1.3 — the aloft classifier was the outlier).

### Exposure honestly stated

The en-route precipitation advisory (§11b) was **mostly shielded**: it grades on
`surface_phase`, whose primary path is the model's own rain/snow split, and
whose Tw fallback already used 0/1.3. Also `enroute_precip._SNOW_PHASES`
includes MIXED, so the mislabelled −5..0 band still counted toward snow extent.
The real leaks were:
1. **Wet snow (Tw 0..+1.3) read RAIN** in the per-level classification — under-
   warn in the one band where snow is stickiest (airframe adhesion), affecting
   any consumer of `precipitation_zones` / `DerivedLevel.precip_phase`.
2. **The digest narrative** — zones flow into the LLM prompt
   (`digest/prompt_builder.py`, `digest/text.py`), so a pure-snow column at
   Tw −3 was narrated as "mixed" (which suggests freezing-rain-adjacent
   concerns the profile does not support).

### Direction of change

Strictly conservative for the snow/mixed hazard band: everything previously
SNOW or MIXED stays in `_SNOW_PHASES`; the 0..+1.3 band moves RAIN → MIXED
(more warning); −5..0 relabels MIXED → SNOW (more honest, same grading).

### Real-world validation needed

- A marginal wet-snow day (surface Tw +0.5..+1) — confirm the snow/mixed extent
  now ambers where METARs report SN/RASN while the old code read plain rain.
- The ice-fraction path (GRIB CLWMR/ICMR) takes precedence where available and
  is unchanged; spot-check a mixed-phase GRIB case for zone-boundary agreement
  between the two paths.

### Files changed

`src/weatherbrief/analysis/sounding/precipitation.py`
(`_level_phase_from_wet_bulb`, `_determine_surface_phase`),
`tests/test_precipitation.py` (exact-boundary pins at 0.0 and 1.3, fallback
consistency test).

---

## 18. Convective advisory colour: NWP-native grade with a DD-trigger AMBER cap (removing the full DD floor)

**Date:** 2026-07-16
**Status:** Implemented (#442). Supersedes the §4/§14 advisory-level DD-floor
behaviour.
**Context:** Follow-up to the ICON `rain_con` firing-gate fix (decoded under
cfgrib shortName `crr`, not `rain_con`, commit `60f7036b`). That fix is what
finally makes ICON's model-native convective tier trustworthy enough to grade on
directly — the NWP scheme can now be *held down* by realized-dry evidence
(`convective_precip_mm_h ≤ 0.1`) instead of every deep ICON tower riding at full
tier forever. With all three models now carrying a realization signal, the
advisory-level DD floor became the dominant remaining false-alarm source.

### The problem with the DD floor

The convective advisory grades each model at
`graded_risk = max(NWP-scheme tier, DD-thermo tier)` (`convective.py`,
`floored_by_thermo`). The DD/CAPE thermo track is conservative in practice (§4
documents the realizable-CAPE work that already tempers it, but it still
over-reads on loaded-but-unforced air masses). Because the floor takes the
`max`, a point where the model's own convective scheme is **quiet** can still be
floored to **RED** on DD alone — the exact loaded-gun false alarm §4 reasoning-2
deliberately accepted as the safe direction. In the field that asymmetry fires
too often: a capped Continental summer sounding reads red on CAPE the models
never realize.

Dropping DD entirely is not acceptable either: a genuine "DD loaded, NWP green"
divergence must not vanish silently.

### The decision

Grade the colour from the **NWP-native tier only** (no full floor), with two
rules layered on top:

> **(1) DD-trigger amber.** If the NWP tier is **green** (below `min_risk`) **and**
> the DD-vs-NWP cross-check returns `dd_not_corroborated` (DD MODERATE+ while the
> model's own scheme is quiet — no convective precip above the firing gate,
> low/no cover, no deep tower), **upgrade that point to AMBER only** — never RED —
> with `reason_code = "dd_trigger"`, tier capped at MODERATE (so a DD HIGH never
> renders "peak HIGH" under an amber colour).
>
> **(2) MODERATE+ amber floor.** Any MODERATE+ point that reaches cruise (real NWP
> MODERATE or a dd_trigger) forces the advisory to **at least AMBER**. The
> per-model colour otherwise runs through the existing coverage thresholds
> (`affected_pct_amber` 20 %, `affected_pct_red` 50 %) on the LOW-floor extent —
> but those were calibrated **with** the old DD floor inflating that extent.
> Without the floor, an isolated-but-real MODERATE tower (8 % of route) or a
> dd_trigger amber would fall below 20 % and read GREEN despite a "MODERATE+ peak
> MODERATE" headline — colour contradicting text, and the divergence note
> unsurfaced. The floor keeps MODERATE convection a *watch*.

Colour provenance after this change:

- **RED** — only from the model's own NWP track: a **HIGH** tower anywhere, or its
  own **MODERATE+ coverage crossing the red threshold** (≥ 50 %). A `dd_trigger`
  point is explicitly excluded from the red-coverage count, so **DD alone can
  never produce a red** — it can only raise green→amber.
- **AMBER** — any MODERATE+ reaching cruise (real or dd_trigger), or LOW-floor
  coverage over the amber threshold.
- **GREEN** — everything else (including convection topping out below cruise).

The dd_trigger amber is bound to the *exact* condition that emits the
`convective_cross_check` note (`convective.py::convective_cross_check`,
`dd_not_corroborated`). Colour, note, and reason are therefore one condition and
cannot diverge — "why amber?" is answered verbatim by the surfaced note.

### Known limitation (calibration item)

A **shallow NWP MODERATE + deep DD HIGH** point falls in a gap: the NWP is not
green (so `dd_trigger` does not fire) yet its own tower tops below cruise (so the
below-cruise filter greens it), and the cross-check's "neither active nor quiet"
band (tower FL120–200, cover 10–25 %) stays silent — so the DD's deeper reach is
not surfaced even as amber. Rare configuration; flagged for the calibration pass
rather than special-cased now (test:
`test_shallow_nwp_moderate_below_cruise_greens_despite_deep_dd`).

### Truth table (motivating flight: EGTF→LFAT→LFQA 2026-07-17)

| Model | NWP tier | DD tier | Floor (today) | NWP-native + DD-cap (#442) |
|-------|----------|---------|---------------|----------------------------|
| ECMWF | HIGH (`cp` 0.37 mm/h — wet) | LOW | RED | **RED** — own scheme fires a realized tower |
| ICON  | MODERATE (`crr` realized) | MODERATE | AMBER | **AMBER** — NWP tier |
| GFS   | quiet (cover ~3%, no tower) | MODERATE (CAPE 348) | AMBER (DD floor) | **AMBER** — reason = "DD MODERATE not corroborated" |

The colours match the old floor here (verified reproducing this pack), but the
*provenance* changes: GFS is now amber via a named `dd_trigger` + the surfaced
DD-vs-NWP note rather than an anonymous floor, and the loaded-gun path can no
longer escalate to red on DD alone. Note the **MODERATE+ amber floor** is what
keeps ICON and GFS at AMBER — without it, dropping the DD floor shrank the
LOW-floor coverage below the 20 % amber threshold and both fell to GREEN despite
their "MODERATE+ peak" headline (the wrinkle that rule fixes).

### Reasoning

1. **Colour should mean what it says.** The app directs attention, it is not a
   go/no-go verdict. RED = "expect it" belongs to a corroborated storm (the
   model's own scheme firing HIGH); AMBER = "watch for it" fits a loaded-but-
   uncorroborated environment. Mapping DD-only concern to amber aligns the colour
   with its meaning and with the progressive-depth philosophy.
2. **Two false-alarm cuts at once.** No DD-driven reds, and — because the trigger
   is MODERATE+ via the cross-check — DD LOW stops flooring anything at all.
3. **DD is demoted, not silenced.** Its voice survives as the amber + the
   cross-check note in both the advisory and the digest (the digest already emits
   `convective_cross_check` unconditionally, `prompt_builder.py`), so the "model
   not realizing it" signal still reaches the pilot and the LLM.
4. **Single source of truth preserved.** The DD-vs-NWP convective comparison
   already lives only on the convective advisory's inline `cross_check` (the
   `dd_nwp_agreement` advisory dropped convective at #283 — see advisories.md).
   Binding the amber to that same check keeps one code path.

### What this reverses / supersedes

- **§4 reasoning 2 ("loaded gun on potential, safety asymmetry")** — that
  asymmetry stays *inside the per-model DD thermo tier* (a capped gun is still
  scored on potential CAPE, never softened by ML). What changes is the
  **advisory-level** consequence: a loaded DD tier no longer floors the *colour*
  to red when the model scheme is quiet — it caps at amber. The DD tier itself is
  unchanged.
- **§14 / advisories.md "grade floors at the DD (thermo) tier"** — replaced by the
  NWP-native grade + DD-trigger amber cap described here.

### Consequences (accepted)

- **NWP-amber + DD-HIGH stays amber.** The upgrade rule only lifts *green* NWP, so
  the sole route to red is NWP HIGH itself. Consistent with the invariant.
- **A genuinely dangerous all-models-under-firing sounding grades amber, not
  red.** This is the real trade: we bet amber gives sufficient attention, backed
  by the named cross-check note. Revisit if verification shows missed corroborated
  events under an all-quiet-NWP regime.
- **Green-with-divergence must still surface the note.** Today the inline note is
  built only for *affected* points; the DD-trigger amber makes the point affected,
  so the note surfaces. Guard this in tests.

### Implementation (#442)

- `analysis/advisories/convective.py`:
  - Replaced the `floored_by_thermo` block (`graded_risk = max(NWP, DD)`) with
    `graded_risk = conv.risk_level` (NWP-native).
  - Added the DD-trigger branch inside the below-`min_risk` (green NWP) path:
    `xc.direction == "dd_not_corroborated"` → `graded_risk = MODERATE`,
    `reason_code = "dd_trigger"`, and a `dd_trigger_count` tally.
  - In the status computation: exclude `dd_trigger_count` from the RED-coverage
    test (DD never reds), then floor a MODERATE+ point (`affected_mod > 0`) at
    AMBER (the coverage-recalibration wrinkle above).
- `reason_code` token `thermo_floor` → `dd_trigger` (`models/advisories.py`;
  no TS switch depends on the value).
- `convective_character` — unchanged (already NWP-cells-only, orthogonal to
  severity, §15).
- Downstream surfaces (cross-section RED/AMBER cutouts §373, digest assessment,
  iOS, MCP) inherit from the single grade — no per-surface change.
- Tests: `test_reason_codes.py` (dd_trigger amber, LOW-NWP-not-floored),
  `test_evaluators.py` (below-cruise via thermo EL; shallow-NWP limitation),
  `test_method_provenance.py` (method stays `nwp`). Full suite green.
- Deferred: optional `dd_amber_min_risk` param (default MODERATE) for eval-corpus
  tuning; the shallow-NWP+deep-DD gap above.

### Cross-check surfacing (follow-up, same PR family)

The per-model `cross_check` note (advisory + digest) was reworked twice as a
follow-up:

1. **Plain, layer-named copy.** Dropped "corroborated"/"DD"/"NWP scheme" for the
   cross-section toggle names — *"Thermo Convective shows MODERATE instability,
   but the model's own NWP Convective forecast is quiet"* — so a pilot can pull up
   exactly those two overlays. Web header → "Convective signals disagree" + a
   muted tappable `ℹ` summary-card tag when a note is present.
2. **Driver-anchored, ≥2-tier gate.** The old route-wide "dominant divergence"
   scan surfaced a note about *any* stretch where the signals differed — which
   read as contradicting the grade (e.g. "ICON red" next to "ICON's NWP quiet",
   where the quiet stretch was a *different* 9 nm than the red-driving towers).
   Now the note compares `convective_nwp` vs `convective_thermo` **at the
   grade-driving (peak) point only**, and fires **only on a ≥2-tier gap**
   (same-or-one-off = normal method spread). It names the driver: NWP-higher →
   "NWP Convective drives this — Thermo Convective shows only {DD}"; DD-higher
   (the `dd_trigger` case) → "Thermo Convective … but the NWP forecast is quiet
   here". This makes the note *explain* the grade instead of contradicting it,
   and reuses the peak/highlight tracking already computed for `peak_dist_nm`.

### Real-world validation needed

- Replay the eval-digest corpus old-vs-new (same config) and confirm the red→amber
  moves are all loaded-gun/quiet-NWP cases, not corroborated events being
  under-graded.
- Watch for any all-quiet-NWP sounding that *did* produce observed convection
  (lightning/METAR-TS) — that is the case the removed floor was protecting, and
  the one that would argue for a narrower cap.

## 19. ICON-D2 explicit-convection track: reflectivity-driven firing with corroborated severity

**Date:** 2026-07-21
**Status:** Implemented (#462, building on the #456/#461 D2 slot).
**Context:** ICON-D2 is convection-permitting — it runs **no deep-convection
parameterization**, so the diagnostics the icon slot's NWP convective track
grades on elsewhere (`hbas_con`/`htop_con` geometry, `rain_con` realization)
either 404 on the D2 feed or silently change meaning. #461 deliberately shipped
those fields **unfetched** on D2 (missing-data semantics); until this entry, a
D2-sourced icon slot therefore had *no* model-native convective signal at all.
Deep convection in D2 lives in explicit storm fields — simulated reflectivity,
echo top, lightning potential, updrafts — a different *kind* of signal,
carried end-to-end as its own track (`NWPExplicitConvectiveDiagnostics` payload,
`assess_convective_explicit`, `method="nwp_explicit"`) and never blended into
the parameterized concepts.

### The decision — v1 firing/severity table

**Superseded by the 2026-07-21 amendment at the end of this section (#466/#467):
the corroborator algebra is now LPI-primary, CAPE/UH are narrative-only, the
≥ 50 dBZ row no longer bypasses corroboration, and a bright-band gate was added.
The v1 table is retained below as the historical baseline the amendment argues
against.**

The firing signal is `dbz_ctmax` (column-max simulated reflectivity, max over
the previous hour), reduced to a **corridor maximum** over a ~10 NM route
buffer. The corroborator set C (each channel counts 1 when **complete AND over
threshold**): `lpi_max ≥ 1 J/kg` (≥ 5 counts 2) · `w_ctmax ≥ 10 m/s` ·
`cape_ml ≥ 500 J/kg`. (A fourth corroborator, `grau_gsp ≥ 0.5 mm`, shipped in
#462 but was dropped in #468 — see the update block below.)

| Corridor `dbz_ctmax` | \|C\| = 0 | \|C\| = 1 | \|C\| ≥ 2 |
|---|---|---|---|
| < 35 dBZ | no fire | no fire | no fire |
| 35–44 dBZ | **no fire** — "echo present, likely stratiform/melting-band" note | MARGINAL | MODERATE |
| 45–49 dBZ | MODERATE | MODERATE | HIGH |
| ≥ 50 dBZ | HIGH | HIGH | HIGH |

These are **calibration starting points, not physical constants** — revisit
against the #462 validation cases (2026-06-27 EGTF→LFAT→LFQA, 2026-06-21
LFMD→EGTF hits; stratiform bright-band quiet controls). `|uh_max| ≥ 25 m²/s²`
is a rotation/character **note only** in v1 —
HRRR updraft-helicity thresholds are NOT portable (2–8 km layer here vs
HRRR's 2–5 km).

### Hard rules carried into code (each with a test)

1. **Echo top is not a cloud top.** The 18 dBZ echo top sits *below* the
   physical storm top (anvil ice reflects weakly). The explicit assessment sets
   `top_ft=None` unconditionally, so the overfly-clearance filter
   (`top_ft + clearance ≤ cruise`) structurally cannot consume it — it would
   err in the dangerous direction ("safe to overfly" under a higher anvil).
   The value travels only as the dedicated `echo_top_18dbz_ft` detail field;
   D2 cells render with unresolved vertical geometry (ghost column).
   The Pa→ft conversion stays on **one datum per hour**: log-pressure over the
   hour's own geopotential column, extrapolated along the nearest two levels
   when the echo sits above the aviation slice, rather than switching to ISA
   pressure altitude mid-field (adopted from the parallel PR #465
   implementation of this issue). A metre-datum column and ISA disagree by
   hundreds of feet, so flipping between them across route points would make
   echo tops incomparable. ISA remains the fallback only when the hour carries
   fewer than two levels with heights.
2. **Never linearly interpolate dBZ** (logarithmic). Corridor-max extraction at
   decode replaces per-point bilinear sampling entirely for these fields, and
   they are registered as explicit SKIPs in both the time-axis fill and the
   spatial interpolator.
3. **Interval maxima attach to `(H−1, H]`.** The hourly echo top is
   *constructed*: min pressure across exactly the four 15-min `min_pres`
   windows ending at H−45 … H (three live in file f(H−1) — the sole reason
   D2 sets `needs_predecessor_step` since `grau_gsp` was dropped in #468).
   A missing quarter
   degrades `echo_top_complete` — never a partial min presented as the hourly
   value. No hold-over fill: a 1-hour maximum from a failed hour has no
   covering interval (contrast the ECMWF gust precedent, whose window spans
   the gap), so a missing hour stays honestly unavailable.
4. **Graupel ≠ hail** — wording everywhere is "graupel / strong mixed-phase
   core", including the `_severity_modifiers` freezing-level+CAPE heuristic,
   which is rephrased on the explicit track (its "hail risk" string is a
   parameterized-era proxy; D2's mixed-phase signal does not discriminate
   hail). This wording rule stays even though the `grau_gsp` corroborator
   itself was dropped in #468 (below): the freezing-level+CAPE modifier is
   thermodynamic, not tied to any graupel channel.
5. **None ≠ 0 (#421), per tier.** Completeness means "valid unmasked corridor
   cells decoded", not "file downloaded": an all-masked corridor is
   UNAVAILABLE; a −150 dBZ encoder floor is genuinely QUIET (normalized to
   `None` + `detection_complete=True`). `detection_complete=False` produces
   **no assessment at all** (`convective_nwp=None` +
   `convective_explicit_unavailable=True`) — never an NWP NONE ("scheme
   quiet"), never a "quiet scheme" reading in cross-checks, never a CAPE-only
   fallback presented as D2's explicit verdict. To make that reachable, the
   enrichment **always attaches a payload** for every in-window D2 hour —
   with all channels None and every completeness flag False when nothing
   decoded — because payload *presence* is the mode signal: a payload-less
   hour would silently fall back to the parameterized path, which on D2
   (scheme fields unfetched, #461) structurally reads as a quiet scheme,
   turning a one-hour fetch hiccup into fake-quiet (PR #463 review). Grading then falls back to the
   thermo track, truthfully badged. Incomplete corroborator channels never
   move the tier in either direction — |C| counts only complete channels, and
   a zero on one channel never suppresses positive evidence on another.
6. **No CIN suppression of a simulated echo.** The model already convected;
   penalising the echo on surface/ML CIN would be circular — same reasoning as
   the `nwp_precip` path (§14).

### Rejected alternatives

- **Mapping `hbas_sc`/`htop_sc` (shallow scheme) into convective_base/top** —
  would show a benign fair-weather-cumulus top during a real storm (#461).
- **Feeding D2 `rain_con` into `convective_precip_mm_h`** — near-zero even in
  severe explicit storms; would read "quiet" exactly when D2 sees a storm.
- **Per-point bilinear sampling of the storm fields** — a cell between 10 NM
  route points (or displaced a few km by timing error) vanishes, making the
  2.2 km model *less* likely to fire than coarse models. Corridor extrema
  instead; recorded as such in the payload docstring.
- **Reflectivity alone (no corroborators)** — 35–44 dBZ is reachable by
  stratiform rain and melting-band bright-band; the quiet controls exist
  precisely because "fires at least as early as EU" alone rewards false
  alarms. Hence the corroborated middle rows and the C0 no-fire band.
- **dbz_cmax (instantaneous) as the core field** — a brief core between hourly
  samples would be missed; the hour-max `dbz_ctmax` is the firing signal
  (dbz_cmax left unfetched in v1).
- **`tcond10_mx`** — deferred from v1 (no payload field); the "10" is the
  −10 °C isotherm, not 10 g/kg — revisit with calibration. Now the leading
  candidate to *replace* the dropped `grau_gsp` corroborator (#468 below):
  column condensate above the −10 °C isotherm is the mixed-phase-core column
  property, and unlike surface graupel it co-locates with the reflectivity
  cores.
- **Sharing the cloud-diag cache blob + V2→V3 key bump** (the issue's sketch)
  — the explicit fields live in **per-variable blobs** with their own key
  (`ICON_D2_EXPL_<VAR>_V1`) instead: the decoder must know which physical
  field it is reading without trusting eccodes shortNames, several files are
  multi-message sub-hourly (message-level `stepRange` selection, never a
  cfgrib blob merge), and the cloud-diag blob's content is then unchanged so
  no bump is needed — warm caches stay schema-correct.

### Aggregation — explicitly NOT decided here

A lone D2 explicit cell against quiet coarse models still aggregates GREEN
under majority rules. The proposed "high-confidence D2 explicit convection
floors the aggregate at AMBER" changes aggregation semantics → decide inside
the #442 grade framework with its own entry. The per-model D2 tier is fully
visible in the per-model breakdown regardless, and the explicit DD-vs-model
cross-check (`_explicit_cross_check`) surfaces "ICON-D2 explicitly develops a
cell; thermodynamics quiet" divergences per point.

### Domain-gate hardening (carried from #461 review)

The delivered D2 regular-lat-lon files are `regular_ll` with no rotated-pole
metadata and ~17 % bitmap-masked corner cells. A route can pass the #461 bbox
gate yet clip masked cells. The gate now also requires the route's **entire
corridor buffer** to lie in valid cells, tested against a cached validity mask
built once from a delivered message's bitmap (testing the actual product was
preferred over hardcoding DWD's rotated-pole constants). Mask unavailable →
fail-open to the bbox gate (behaviour then no worse than #461 — masked corners
decode as unavailable, not wrong); corridor clips masked cells → the whole
slot falls back to ICON-EU, same all-or-nothing rule as the bbox.

A corridor can be clipped **two** ways, and both now read UNAVAILABLE: by
bitmap-masked cells inside the buffer, and by the **outer grid boundary**. The
second one was initially missed (external #462 review of PR #463) — the index
window is built with `searchsorted`, which silently truncates at the array
bounds, so a point within one corridor radius of the domain edge reduced over
a partial buffer and still reported `detection_complete=True`. It also
defeated the mask gate outright: the gate compares valid-cell against
all-cell counts inside the buffer, and at the boundary both gathers clip
identically, so they can never disagree. `_d2_corridor_window` now requires
the whole buffer to lie inside the grid extent before either check runs.

### Update (#468): `grau_gsp` dropped from the corroborator set

**Date:** 2026-07-21
**Status:** Implemented (#468, amends the §19 v1 table above).
**Context:** The first live run of the explicit-convection track against real
DWD data (#467) showed `grau_gsp` reads ~always 0 on a warm-season route
corridor.

`grau_gsp` (shortName `tgrp`, paramId 231040) is **surface graupel
precipitation** — snow pellets that reach the ground — a `stepType=accum`
SURFACE field, not the **column** mixed-phase-core property #462 specified it
as. Nearly every deep convective core is graupel-laden aloft, but surface
graupel needs the pellets to survive the fall without melting, which in
warm-season convection they almost never do. Measured over the ESMX→EKRK
corridor (same valid time, three independent runs): hundreds of ≥45 dBZ cells
with LPI to 89 J/kg and updrafts to 23 m/s, and **exactly zero** surface
graupel at those cells. Where the field *is* nonzero it is tiny and orographic
(0.05 % of the domain, median 0.017 mm, clustered at 45.9–47.5°N over the
Alps) — a terrain confound, not a corridor-storm signal.

Consequences of leaving it in:
- The graupel corroborator could never realistically fire on a route corridor,
  so `|C|` effectively topped out at 3 (LPI ≤ 2, updraft, ML-CAPE) not 5, and
  where it *could* fire it was Alpine-biased.
- `strength_complete` was misleading: a valid `0.0` satisfied it, so the
  payload reported a healthy corroborator tier built partly on a channel that
  carries no usable information here.
- The error direction is toward **under-warning**, concentrated in the 35–44
  dBZ band where corroborators decide NONE / MARGINAL / MODERATE. At ≥ 50 dBZ
  nothing changes (dBZ alone gives HIGH), which is why the live ESMX→EKRK case
  still graded correctly and this stayed invisible to the tests (synthetic
  fixtures supplied their own graupel values).

**Decision.** Drop `grau_gsp` from `ICON_D2_EXPLICIT_CONV_VARIABLES` and from
the corroborator set; redefine `strength_complete` as `lpi_max + w_ctmax` both
valid. This removes a provably wrong input rather than recalibrating — but it
does alter `|C|`, so it lands as its own logged decision (related: #466
corroborator recalibration, #467 validation matrix). The whole `grau_gsp`
fetch / per-cell de-accumulation / decode machinery (`grau_gsp_cells`,
`icon_d2_hourly_graupel_mm`, `_d2_corridor_cell_map`, the `graupel_hour_mm`
payload field) is removed with it, so the tidy diff-then-reduce de-accumulation
described in the old rule 4 no longer exists. Bandwidth was **not** the
argument: `grau_gsp` is ~1.3 % of a D2 run's download.

**Follow-up (not done here):** evaluate `tcond10_mx` (column condensate above
the −10 °C isotherm) as the replacement corroborator — it is the column
mixed-phase quantity #462 wanted and will co-locate with the reflectivity
cores. It must clear the same three-run corridor test above before being wired
in, so it is deferred pending calibration.

### Amendment (2026-07-21, #466/#467): LPI-primary corroboration + bright-band gate

**Status:** Implemented. Supersedes the v1 firing/severity table above.
**Composes with #468 (immediately above), which landed first.** The two were
developed in parallel and agree: #468 removed `grau_gsp` outright as a provably
dead input, and this amendment independently demoted it from an independent vote
to LPI-embedded detail. Net result — the vote set is `lpi_max` (primary) with
`w_ctmax` as its **only** substitute, so the substitute path tops out at
|C| = 1 and **|C| ≥ 2 is reachable only via LPI ≥ 5**. The `|C| ≥ 2` column of
the v2 table is therefore an *electrification* column in practice, which is
deliberate: LPI is the one channel that actually integrates the mixed-phase
updraft the tier is trying to name.
**Context:** The v1 table shipped with #462 as calibration *starting points*
requiring validation before it could be trusted (its own "revisit against the
validation cases" caveat). Two independent pressures then landed together:

1. **The corroborator argument from the parallel PR #464 implementation** (the
   #466 issue body). DWD's LPI (Lynn & Yair) is essentially `∫ ε w² dz` with ε
   weighted by mixed-phase (graupel/ice) content in the charging zone — it
   *already integrates* updraft and graupel. Counting `lpi_max`, `w_ctmax` and
   `grau_gsp` as three independent votes triple-counts one mixed-phase updraft,
   so a moderately-electrified (or bright-band) cell can reach |C| ≥ 2 on
   physically one signal. And `cape_ml` is *environment*, not storm process: it
   duplicates the independent thermo track and let a 35–44 dBZ stratiform band
   upgrade to MARGINAL on ML-CAPE alone — the exact false alarm the quiet
   controls exist to catch.
2. **The #467 forward validation.** The #462 retrospective cases proved
   unrecoverable (DWD open-data keeps only the current D2 run; saved packs from
   those dates predate the D2 slot), so validation became *forward*. A live
   stratiform control (**EDDE→EDQD, 2026-07-21**, widespread non-convective
   rain) ran the D2-vs-forced-EU A/B and **the v1 table false-alarmed HIGH at 4
   of 8 route points** — 50–54 dBZ column-max with LPI ≈ 0, updraft ≤ 5.5 m/s
   and ML-CAPE ≤ 242 J/kg, while every other signal (its own LPI/updraft/CAPE,
   the thermo track, ICON-EU, GFS) said no convection. The 35–44 band this issue
   was *originally* about behaved correctly (43 and 36 dBZ, |C| = 0 → none). The
   hole was the **≥ 50 dBZ row bypassing corroborators entirely.**

### The decision — v2 table

| Corridor `dbz_ctmax` | \|C\| = 0 | \|C\| = 1 | \|C\| ≥ 2 |
|---|---|---|---|
| < 35 dBZ | no fire | no fire | no fire |
| 35–44 dBZ | no fire (stratiform note) | MARGINAL | MODERATE |
| 45–49 dBZ | MODERATE | MODERATE | HIGH |
| ≥ 50 dBZ | **MODERATE** | HIGH | HIGH |

Three coupled changes (`analysis/sounding/convective.py`):

**A. LPI-primary corroborator algebra** (`_explicit_corroborators`). |C| now
counts **storm-process** signal only:
- **LPI is the primary channel.** When present (complete), it votes alone:
  `≥ 5` → 2, `≥ 1` → 1. `w_ctmax` is then *narrative detail, not additive* — it
  is an ingredient LPI already integrates.
- **`w_ctmax` substitutes only when LPI does not vote** — LPI missing *or*
  present-but-below-floor. The substitute (`w ≥ 10 m/s` → 1) counts only when
  its own channel is complete-and-over-threshold. It is the only substitute:
  `grau_gsp`, the other v1 candidate, was removed in #468.
- **CAPE and UH are narrative-only, never votes.**

  *Completeness invariant (issue #466 note; rule 5 unchanged).* The substitution
  is **identical** whether LPI is missing or present-and-quiet, so an incomplete
  LPI channel can never *silently promote* w into counting beyond what a
  present-but-quiet LPI would give. A masked substitute channel (None) still
  contributes nothing.

**B. ≥ 50 dBZ no longer self-certifies.** `dbz_ctmax` is a **column max**, and a
strong melting-layer bright band in heavy stratiform rain reaches 50+ dBZ with
no convection in the column. |C| = 0 now caps the row at **MODERATE** (was HIGH);
|C| ≥ 1 restores HIGH.

**C. Bright-band gate** (`assess_convective_explicit`). When the 18 dBZ echo top
sits **< 10,000 ft above the freezing level** *and* |C| = 0 (no storm-process
corroboration), the high reflectivity is treated as melting-layer bright band,
not convection, and the fire is suppressed to **NONE** at any dBZ. Physically a
bright band sits *at* the melting layer, so its echo top is only a few kft above
the 0 °C level, whereas a real convective core carries hydrometeors far higher.
#467 measured the discriminator cleanly: **6.5–7.8 kft** in the stratiform
control vs **24–27 kft** in the ESMX real storms — a wide margin around the
10 kft threshold. This finally gives `echo_top_18dbz_ft` a real job while
preserving rule 1 (it never becomes `top_ft`, so it still cannot reach the
overfly-clearance filter). Freezing level is taken from the sounding's own 0 °C
crossing, falling back to the model-native / NWP-diagnostic freezing level.

Under B+C the EDDE→EDQD ≥ 50 dBZ false-alarm points go HIGH → MODERATE (table) →
NONE (gate); the 35–44 rows are unchanged; and the ESMX real hit (LPI 89, updraft
23, Δ ≈ 25 kft) stays HIGH. Both directions are pinned as regression tests in
`tests/test_convective_explicit.py` (`TestExplicitValidationMatrix`,
`TestExplicitBrightBandGate`, `TestExplicitLpiPrimaryAlgebra`).

### Rejected alternatives

- **Keep the v1 four-way independent |C|** (`lpi + w + graupel + cape`). Rejected:
  triple-counts the mixed-phase updraft LPI already integrates, and lets CAPE
  (environment) upgrade a stratiform band — the mechanism behind the #467 false
  alarm on the corroborated rows.
- **A straight swap to LPI-primary at #462 implementation time** (what PR #464
  proposed inline). Rejected then: the v1 table survived two external review
  rounds, so the firing algebra could not change without running the validation
  matrix under both schemes. That is why this landed as its own issue with a
  both-directions acceptance bar, not as a silent edit — the recalibration is
  now *earned* by the #467 run rather than asserted.
- **An unconditional bright-band gate** (suppress at Δ < 10 kft regardless of
  |C|), as the #467 comment framed it ("independent of the corroborator
  algebra"). Rejected in favour of gating on |C| = 0: a suppressor that can hide
  a *corroborated* cell is a dangerous false negative, against this codebase's
  under-warning-is-worse asymmetry and its "never silently hide a storm" bias.
  A shallow echo top and a firing LPI are physically contradictory (electrification
  needs ice aloft), so the two rarely conflict; when they do, the positive
  electrification evidence wins. On the #467 cases the outcome is identical
  either way (the false-alarm points are all |C| = 0), so no validated behaviour
  is lost by the safer gating.
- **A lower / higher Δ threshold than 10 kft.** 10 kft sits in the wide gap
  between the two observed populations (7.8 vs 24.4 kft). The one residual
  ambiguous case is the ESMX 45–49 dBZ |C| = 0 points already flagged as
  suspicious in #467 (Δ ≈ 9.8 / 12.6 kft): the 9.8 kft point is now suppressed,
  the 12.6 kft one stays MODERATE (the 45–49 |C| = 0 row). That is acceptable —
  MODERATE, not HIGH, for a 47.8 dBZ echo with LPI 0 and a 3.5 m/s updraft — and
  the threshold is a calibration knob to revisit as more forward cases land.

### Known interactions / follow-ups

- **Graupel is gone, not merely dead (#468, resolved).** This bullet originally
  read "`grau_gsp` freezes bitwise from ~f024, so the corroborator is
  effectively always 0" and treated it as a bug to fix. The #468 investigation
  landed a different and firmer diagnosis: `grau_gsp` is *surface* graupel
  precipitation, so it is ~always 0 under warm-season corridor cores by
  construction — nothing to fix, and it was dropped outright (see the #468
  section above). The LPI-primary scheme is unaffected either way: LPI carries
  the storm-process vote when present, and graupel only ever mattered as a
  substitute when LPI is absent/quiet, where its being 0 simply means `w` must
  carry it. No v2 grade on the validated cases changes. The consequence that
  *does* survive is the |C| ≥ 2 ceiling noted at the top of this amendment. If
  `tcond10_mx` is later wired in as the column replacement (#468 follow-up), it
  enters as a second substitute and restores a non-LPI route to |C| = 2 — that
  is a recalibration to validate, not a drop-in.
- **Aggregation is still not decided here** (unchanged from v1): a lone D2
  explicit cell against quiet coarse models still aggregates by majority — the
  "floor the aggregate at AMBER" question belongs in the #442 grade framework.
- **Still-outstanding #467 controls**: a winter graupel-shower day (needs the
  season) and a second/third convective hit before the thresholds are treated as
  fully calibrated rather than validated on one case each way.
