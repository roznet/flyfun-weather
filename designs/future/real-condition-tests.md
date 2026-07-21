# Real-Condition Validation Tests

Validation that can only run when nature cooperates. Some forecast behaviour cannot
be tested from fixtures — a melting-layer bright band, a winter graupel shower, a
domain-edge route in real weather — and the conditions may be months away.

**This file exists so those tests survive the wait without an issue sitting open for
half a year.** An issue that cannot close for five months stops being a tracker and
becomes noise; a dated list here keeps the intent, the reasoning, and the pass
criteria intact until the weather turns up.

Related: [meteorology-decisions.md](../meteorology-decisions.md) (the thresholds
under test), [weather-engine-specs.md](../weather-engine-specs.md).

## Lifecycle

1. **Blocked on conditions** → an entry here, tagged with what it needs.
2. **Conditions arrive** → open an issue, run it, link back to the entry.
3. **Done** → move the entry to *Completed* with the result, or delete it if the
   feature it validated is gone.

Keep entries even after a related issue closes. A test whose condition has not
occurred is not obsolete — it is pending. Per [INDEX.md scope](../INDEX.md), do
**not** index this file for MCP discovery; it is planning material, not architecture.

## Finding the condition

Do not wait passively. The single-level GRIB fields are cheap (~1 MB per variable
per forecast hour on the wire — a full 48h domain-wide scan of four variables costs
less than a third of *one* forecast hour of model-level sounding data), so the live
run can be scanned directly for a signature:

```python
lats, lons, dbz  = field(fh, "dbz_ctmax")   # whole domain, 746 x 1215
_,    _,    lpi  = field(fh, "lpi_max")
_,    _,    w    = field(fh, "w_ctmax")
_,    _,    cape = field(fh, "cape_ml")

ok    = np.isfinite(dbz) & np.isfinite(lpi) & np.isfinite(w) & np.isfinite(cape)
strat = ok & (dbz >= 30) & (dbz < 45) & (lpi <= 0.0) & (w < 3.0) & (cape < 100)
```

Then take the densest 1°×1° tile, look up airports in it from `nav.db`, and build a
route through it. This is how the 2026-07-21 bright-band case was found the same day
it was needed, rather than waiting for one to be noticed.

**Two cautions learned from that run:**

- **Scan with the same reduction the pipeline uses.** The predicate above is
  per-cell, but the pipeline takes a **corridor maximum over 10 NM**, so the route
  built from those cells sampled a wider neighbourhood and picked up 50–54 dBZ cores
  nearby — a different case than the one intended. Apply `_d2_corridor_cells(...)`
  to candidate points before accepting them.
- **A model signature is not ground truth.** This finds cases where the *model*
  shows a pattern. That is enough for self-consistency tests (a model contradicting
  its own fields), but for genuine validation pair it with observed radar/lightning.

A generalised, multi-model version of this scanner is a parked idea — it should not
be ICON-specific.

---

## Winter (roughly Nov–Mar)

### ICON-D2 explicit convection — winter graupel-shower control

**Added:** 2026-07-22
**Requires:** A winter graupel-shower day inside the ICON-D2 domain (43.18–58.08°N,
3.94°W–20.34°E). Freezing level low (~2,000–4,000 ft), showery convection.
**Origin:** #462's validation matrix, carried out of #467 so that issue could close.

Run a D2-vs-forced-ICON-EU A/B on a route through the showers and confirm the
explicit track does **not** over-grade. Harness: patch `_d2_corridor_mask_ok` to
force the variant, fetch twice, diff the per-point convective assessments.

**This now tests something different from what it was written for.** Two changes
since:

- **#468 dropped `grau_gsp`** (it is *surface* graupel precipitation, ~always 0 under
  warm-season cores). So this is no longer "does the graupel corroborator behave" —
  that channel is gone.
- **#466 added a bright-band gate**: reflectivity is suppressed to NONE when the
  18 dBZ echo top sits < 10,000 ft above the freezing level *and* no storm-process
  corroborator fired.

The winter question is therefore now the **inverse** of the summer one. With a
freezing level at 2,000 ft instead of 8,000 ft, Δ(echo top − freezing level) is
mechanically much larger for the same physical cloud depth — so a shallow winter
shower could clear the 10,000 ft gate that a deep summer bright band does not.

**Pass:** shallow, non-electrified winter showers are not graded MODERATE+; genuine
wintertime convection still fires.
**Watch specifically:** whether the gate's fixed 10,000 ft delta needs to become
freezing-level-relative, or scale with cloud depth, rather than being absolute.

### Bright-band gate boundary sensitivity

**Added:** 2026-07-22
**Requires:** Accumulated cases spanning a range of freezing levels — winter is the
missing end.
**Origin:** #466/#467; the 10,000 ft threshold did not exist when #467 was written.

The gate bites at the margin. On 2026-07-22's storm case the ESMX 40 nm point flipped
`moderate → none` at Δ = **9,805 ft** — 195 ft under the threshold — with LPI 0.0 and
updraft 3.5 m/s. Defensible (that point had no storm-process evidence at all), but it
shows a 2% move in Δ changes the grade.

**Collect:** Δ, LPI, updraft, dBZ and the outcome for every case run, summer and
winter, until there is enough spread to say whether 10,000 ft is right, whether it
should be relative rather than absolute, and how wide the ambiguous band is.

---

## Completed

### ICON-D2 stratiform bright-band control — 2026-07-21, FAILED then fixed

**Required:** Widespread non-convective rain with melting-layer enhancement.
**Found by:** Scanning the live `20260721 12z` run — 1,996 domain cells in the
35–44 dBZ band with LPI = 0, updraft < 3 m/s, ML-CAPE < 100 J/kg, densest at
50–51°N / 11–12°E. Route: EDDE (Erfurt) → EDQD (Bayreuth), 65 nm, 6,000 ft.

**Result:** the explicit track graded **HIGH at 4 of 8 route points** in rain with
LPI ≈ 0, updraft ≤ 5.5 m/s and ML-CAPE ≤ 242 J/kg — while its own storm-process
fields, the thermo track, ICON-EU and GFS all said no convection. Root cause was the
decision table's ≥50 dBZ row bypassing corroborators; the 35–44 band behaved
correctly. Fixed in #466 (≥50 dBZ now requires |C| ≥ 1, plus the bright-band gate).

**Outstanding:** the fix was verified by replaying the recorded per-point values
through `assess_convective_explicit`. That exercises the decision table but **not**
the full pipeline — decode → payload → freezing-level plumbing → advisory. A live
A/B on a fresh bright-band day would properly close it; kept in #467 while it is
still convective season.
