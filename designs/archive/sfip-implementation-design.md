# SFIP Icing Index — Implementation Design Document

## Purpose

Add a second icing index called **SFIP** (Simplified Forecast Icing Potential) alongside the existing **Ogimet** index. The two indices should be computed independently at each pressure level and both included in the output, so they can be compared. The SFIP is the algorithm family used by Windy.com and operational European met services (IPMA Portugal, UK Met Office) for aviation icing forecasting.

### References

- Belo-Pereira, M. (2015). Comparison of in-flight aircraft icing algorithms based on ECMWF forecasts. *Met. Apps*, 22, 705–715.
- Morcrette, C. et al. (2019). Development and evaluation of in-flight icing index forecast for aviation. *Weather and Forecasting*, 34(3), 731–750.

---

## Algorithm Overview

SFIP uses **fuzzy logic membership functions**. Each input variable is mapped through a function that returns a value between 0.0 and 1.0 representing how favorable that variable is for icing. The membership values are then combined with weights:

```
SFIP = w_T × M_T + w_RH × M_RH + w_CLW × M_CLW + w_VV × M_VV
```

The result is clamped to [0.0, 1.0]. Multiply by 100 for the 0–100 scale used in severity mapping.

There are two operational variants depending on data availability:

| Variant | Variables | When to use |
|---|---|---|
| **SFIP_O** (full) | T, RH, CLW, VV | GFS (has CLWMR from GRIB enrichment) |
| **SFIP_4** (degraded) | T, RH, VV + cloud proxy | Models without CLW (ECMWF, ICON, MétéoFr, UKMO) |

---

## Membership Functions

All membership functions return a value in [0.0, 1.0] unless noted otherwise (VV can return small negative values as a penalty).

### M_T — Temperature Membership

Temperature is the primary driver. The function reflects observed supercooled liquid water distribution from PIREPs: most icing occurs between −2°C and −20°C, with peak probability around −8°C to −12°C.

**Piecewise linear definition (Belo-Pereira 2015 simplified):**

```python
def membership_temperature(t_celsius: float) -> float:
    """
    Temperature membership function for SFIP.
    t_celsius: air temperature at the pressure level in °C.
    Returns: 0.0 to 1.0
    """
    if t_celsius >= 0.0:
        return 0.0
    elif t_celsius >= -2.0:
        # Ramp up from 0 at 0°C to 0.8 at -2°C
        return 0.8 * (-t_celsius) / 2.0
    elif t_celsius >= -20.0:
        # Peak zone: 0.8 at -2°C rising to 1.0 at -5°C, stays 1.0 to -14°C, 
        # then decreases to 0.8 at -20°C
        if t_celsius >= -5.0:
            return 0.8 + 0.2 * (-t_celsius - 2.0) / 3.0
        elif t_celsius >= -14.0:
            return 1.0
        else:
            return 0.8 + 0.2 * (-t_celsius - 20.0) / 6.0
    elif t_celsius >= -25.0:
        # Ramp down from 0.8 at -20°C to 0.0 at -25°C
        return 0.8 * (-t_celsius - 25.0) / 5.0
    else:
        return 0.0
```

**Key points:**
- Returns 0.0 at and above 0°C (no supercooled water possible)
- Peak (1.0) between −5°C and −14°C (matches observed SLW maximum)
- Tapers to 0.0 at −25°C (most water is glaciated below this)
- Smooth ramps at boundaries (this is the key advantage over hard-threshold algorithms)

### M_RH — Relative Humidity Membership

Higher RH means more likely to be in cloud with liquid water. From PIREP verification: 65% of icing events had RH > 97%, only 4% had RH < 70%.

```python
def membership_rh(rh_percent: float) -> float:
    """
    Relative humidity membership function for SFIP.
    rh_percent: relative humidity at the pressure level in %.
    Returns: 0.0 to 1.0
    """
    if rh_percent <= 50.0:
        return 0.0
    elif rh_percent <= 60.0:
        # Small ramp: might be near cloud edge
        return 0.1 * (rh_percent - 50.0) / 10.0
    elif rh_percent <= 90.0:
        # Linear ramp from 0.1 to 0.7
        return 0.1 + 0.6 * (rh_percent - 60.0) / 30.0
    elif rh_percent <= 95.0:
        # Steeper ramp: 0.7 to 0.9
        return 0.7 + 0.2 * (rh_percent - 90.0) / 5.0
    elif rh_percent <= 100.0:
        # Near-saturation: 0.9 to 1.0
        return 0.9 + 0.1 * (rh_percent - 95.0) / 5.0
    else:
        return 1.0
```

**Key points:**
- Returns 0.0 below 50% (definitely clear air)
- Accelerates above 90% (high confidence of cloud presence)
- The 60–90% range is the uncertain zone where CLW becomes the discriminator

### M_CLW — Cloud Liquid Water Membership

**This is only available for GFS** via the CLWMR field from GRIB enrichment. This is the most important membership function for avoiding false alarms — it directly detects cloud liquid water presence.

```python
def membership_clw(clw: float) -> float:
    """
    Cloud liquid water content membership function for SFIP.
    clw: cloud liquid water mixing ratio at the pressure level.
         Expected units: g/kg (if your CLWMR is in kg/kg, multiply by 1000 first).
    Returns: 0.0 to 1.0
    """
    if clw <= 0.0:
        return 0.0
    elif clw <= 0.01:
        # Trace amounts: low confidence
        return 0.3 * clw / 0.01
    elif clw <= 0.05:
        # Moderate: ramp to 0.7
        return 0.3 + 0.4 * (clw - 0.01) / 0.04
    elif clw <= 0.1:
        # Significant: ramp to 0.9
        return 0.7 + 0.2 * (clw - 0.05) / 0.05
    elif clw <= 0.2:
        # High: ramp to 1.0
        return 0.9 + 0.1 * (clw - 0.1) / 0.1
    else:
        return 1.0
```

**Key points:**
- Returns 0.0 when CLW = 0 (no cloud liquid water = no icing, regardless of T and RH)
- This is what prevents over-forecasting — T and RH can both look favorable but if there's no actual liquid water, there's no icing
- CLW > 0.1 g/kg is significant; > 0.2 g/kg is high
- **Unit check critical**: GFS CLWMR in GRIB is typically kg/kg. If so, multiply by 1000 to get g/kg before passing to this function. Verify against your GRIB enrichment pipeline.

### M_CLW_proxy — Cloud Proxy for Models Without CLW

For ECMWF, ICON, MétéoFr, UKMO — use dewpoint depression and cloud cover as a proxy:

```python
def membership_clw_proxy(
    dewpoint_depression: float,
    rh_percent: float, 
    cloud_cover_at_band: float
) -> float:
    """
    Proxy for CLW membership when actual CLW is not available.
    dewpoint_depression: T - Td in °C at the pressure level
    rh_percent: relative humidity at the level in %
    cloud_cover_at_band: NWP cloud cover % for the ICAO band this level falls in
                         (low/mid/high depending on altitude)
    Returns: 0.0 to 1.0
    """
    # Sounding-based cloud detection
    if dewpoint_depression > 5.0:
        sounding_score = 0.0
    elif dewpoint_depression > 3.0:
        sounding_score = 0.2 * (5.0 - dewpoint_depression) / 2.0
    elif dewpoint_depression > 1.0:
        sounding_score = 0.2 + 0.5 * (3.0 - dewpoint_depression) / 2.0
    else:
        # DD < 1°C: very likely in cloud
        sounding_score = 0.7 + 0.3 * min(1.0, rh_percent / 100.0)

    # NWP cloud cover factor
    if cloud_cover_at_band > 80:
        nwp_score = 1.0
    elif cloud_cover_at_band > 50:
        nwp_score = 0.5 + 0.5 * (cloud_cover_at_band - 50.0) / 30.0
    elif cloud_cover_at_band > 20:
        nwp_score = 0.2 * (cloud_cover_at_band - 20.0) / 30.0
    else:
        nwp_score = 0.0

    # Combine: trust whichever is higher (conservative for safety)
    return max(sounding_score, nwp_score * 0.8)
```

**Key points:**
- This is a degraded substitute — it will not be as precise as actual CLW
- The SFIP output for non-GFS models should be flagged/annotated as `sfip_variant: "proxy"` vs `sfip_variant: "full"` so the UI or consumer can distinguish confidence levels
- Uses the same DD < 3°C threshold the existing Ogimet index uses, but with a continuous score rather than binary

### M_VV — Vertical Velocity Membership

Upward motion (negative ω in Pa/s) enhances supercooled water production. 74% of icing PIREPs occur in rising air. This acts as a modifier — it boosts or penalizes the index.

```python
def membership_vv(omega_pa_s: float | None) -> float:
    """
    Vertical velocity membership function for SFIP.
    omega_pa_s: pressure vertical velocity in Pa/s. 
                Negative = ascent (favorable for icing).
                Positive = subsidence (unfavorable).
                None if not available (ICON, MétéoFr).
    Returns: -0.3 to +0.5 (NOTE: can be negative — acts as modifier)
    """
    if omega_pa_s is None:
        return 0.0  # neutral when unavailable

    if omega_pa_s < -5.0:
        # Very strong ascent: big boost
        return 0.5
    elif omega_pa_s < -2.0:
        # Strong ascent
        return 0.3 + 0.2 * (-omega_pa_s - 2.0) / 3.0
    elif omega_pa_s < -0.5:
        # Moderate ascent
        return 0.1 + 0.2 * (-omega_pa_s - 0.5) / 1.5
    elif omega_pa_s < 0.5:
        # Near-neutral
        return 0.0
    elif omega_pa_s < 2.0:
        # Moderate subsidence: penalty
        return -0.15 * (omega_pa_s - 0.5) / 1.5
    else:
        # Strong subsidence: clouds dissipating
        return -0.3
```

**Key points:**
- Unlike the other membership functions, this returns negative values for subsidence (penalty)
- The weight on VV is small (see below), so the penalty/boost is modest
- Returns 0.0 (neutral) when ω is unavailable, so the index still works without it

---

## Combining: Weights and Final Formula

### SFIP_O (full, with CLW — GFS only)

```python
SFIP = 0.35 * M_T + 0.15 * M_RH + 0.35 * M_CLW + 0.15 * M_VV
```

CLW and T share dominance. RH and VV are supporting factors.

### SFIP_4 (without CLW — other models)

```python
SFIP = 0.40 * M_T + 0.25 * M_RH + 0.25 * M_CLW_proxy + 0.10 * M_VV
```

Without real CLW, T becomes more dominant and the proxy gets moderate weight.

### Clamping and Scaling

```python
sfip_raw = max(0.0, min(1.0, sfip_value))  # clamp to [0, 1]
sfip_100 = sfip_raw * 100.0                 # scale to 0–100 for severity mapping
```

---

## Severity Mapping

Map the 0–100 SFIP value to severity labels. Two mappings are provided:

### Windy-Compatible Mapping (from Belo-Pereira 2015 / WAFS)

This matches Windy's published scale:

```python
def sfip_severity_windy(sfip_100: float) -> str:
    if sfip_100 < 25:
        return "NONE"
    elif sfip_100 < 30:
        return "TRACE"
    elif sfip_100 < 35:
        return "LIGHT"
    elif sfip_100 < 55:
        return "MODERATE"
    else:
        return "HEAVY"
```

### Alternative GA-Tuned Mapping

The Windy thresholds may be conservative for GA use (small bands for trace/light). An alternative with wider bands:

```python
def sfip_severity_ga(sfip_100: float) -> str:
    if sfip_100 < 15:
        return "NONE"
    elif sfip_100 < 30:
        return "LIGHT"
    elif sfip_100 < 55:
        return "MODERATE"
    else:
        return "SEVERE"
```

**Decision for implementer**: include both mappings or pick one. The Windy mapping is useful for comparison; the GA mapping aligns better with the existing Ogimet severity levels (NONE/LIGHT/MODERATE/SEVERE). Suggest storing the raw `sfip_100` value and letting the presentation layer choose the mapping.

---

## Ice Water Content: Using ICMR

Since the GRIB enrichment also provides **ICMR** (ice mixing ratio), it can be used as a supplementary check:

```python
def glaciation_factor(clw: float, icmr: float) -> float:
    """
    Reduce icing potential when cloud is heavily glaciated.
    Both in same units (g/kg).
    Returns: multiplier 0.0 to 1.0
    """
    total = clw + icmr
    if total <= 0:
        return 0.0
    liquid_fraction = clw / total
    # Mostly ice = low icing risk (ice crystals don't adhere like SLW)
    return liquid_fraction
```

Apply as: `M_CLW_adjusted = M_CLW * glaciation_factor(clw, icmr)`

This is physically meaningful: a cloud at −15°C with high ICMR but low CLWMR is mostly glaciated and presents less icing risk than the same temperature with high CLWMR. This goes beyond what the standard SFIP does and would be a useful enhancement.

---

## Per-Level Computation

The SFIP should be computed at **every pressure level** independently, just like the Ogimet index. The implementation should follow the same pattern as the existing icing computation.

### Input per level

| Field | Required | Source | Notes |
|---|---|---|---|
| `temperature` (°C) | Yes | API / level data | All models |
| `relative_humidity` (%) | Yes | API / level data | All models |
| `dewpoint_depression` (°C) | For proxy | Derived (T - Td) | All models |
| `cloud_liquid_water` (g/kg) | GFS only | GRIB enrichment CLWMR | Check units — may need ×1000 if kg/kg |
| `ice_mixing_ratio` (g/kg) | GFS only | GRIB enrichment ICMR | For glaciation factor |
| `omega` (Pa/s) | When available | API / level data | GFS, ECMWF, UKMO; None for ICON, MétéoFr |
| `cloud_cover_at_band` (%) | For proxy | Surface API | Map level to ICAO band, use cloud_cover_low/mid/high |

### Output per level

| Field | Type | Description |
|---|---|---|
| `sfip_raw` | float | 0.0 to 1.0, the raw SFIP value |
| `sfip_100` | float | 0 to 100, scaled for severity mapping |
| `sfip_severity` | str | "NONE" / "TRACE" / "LIGHT" / "MODERATE" / "HEAVY" (Windy mapping) |
| `sfip_variant` | str | "full" (has CLW) or "proxy" (using DD/cloud proxy) |

### Gating: Only assess levels where icing is physically possible

Same approach as the existing Ogimet index:

```python
# Skip levels where icing is impossible
if temperature >= 0.0:
    sfip_raw = 0.0  # above freezing
elif temperature < -25.0:
    sfip_raw = 0.0  # too cold, fully glaciated
else:
    # Compute SFIP normally
    ...
```

This avoids wasting computation on levels deep in the stratosphere or near the surface on warm days.

---

## Integration Notes

### Relationship to Existing Ogimet Index

- **Do not replace Ogimet** — add SFIP alongside it
- Both should be computed for the same levels
- Both should appear in the output data structures so they can be compared
- The existing icing assessment (type classification: clear/mixed/rime from temperature bands) applies to both indices — it's independent of severity calculation method

### Suggested code organization

The new SFIP computation could either:
1. Live in the existing `sounding/icing.py` as additional functions alongside the Ogimet computation
2. Live in a new file `sounding/sfip.py` that is called from the same orchestration point

Either approach works. The key is that both indices are computed in the same pass over the level data and both end up in the output.

### Model-specific behavior summary

| Model | SFIP Variant | M_CLW Source | M_VV Source | Glaciation Factor |
|---|---|---|---|---|
| GFS | SFIP_O (full) | CLWMR from GRIB | ω from API | Yes (ICMR from GRIB) |
| ECMWF | SFIP_4 (proxy) | DD + cloud cover proxy | ω from API | No |
| ICON | SFIP_4 (proxy) | DD + cloud cover proxy | None (ω unavailable) | No |
| MétéoFr | SFIP_4 (proxy) | DD + cloud cover proxy | None (ω unavailable) | No |
| UKMO | SFIP_4 (proxy) | DD + cloud cover proxy | ω from API | No |

### CLWMR unit verification

**Critical**: before implementing, verify the units of CLWMR in the GRIB enrichment data:
- GFS GRIB2 native unit for CLWMR is **kg/kg**
- The membership function above expects **g/kg**
- If CLWMR is in kg/kg, multiply by 1000 before passing to `membership_clw()`
- Typical values: 0.0001 to 0.001 kg/kg = 0.1 to 1.0 g/kg in cloud
- If the values at cloud levels are in the range 0.0001–0.001, they're in kg/kg
- If they're in the range 0.1–1.0, they're already in g/kg

---

## Example Computation

A level at 700 hPa, GFS model:

```
T = -8°C, RH = 96%, CLWMR = 0.15 g/kg, ICMR = 0.02 g/kg, ω = -1.5 Pa/s

M_T(-8)    = 1.0     (in peak zone -5 to -14°C)
M_RH(96)   = 0.92    (near saturation)
M_CLW(0.15) = 0.95   (significant liquid water)
glaciation  = 0.15 / (0.15 + 0.02) = 0.88
M_CLW_adj  = 0.95 × 0.88 = 0.84
M_VV(-1.5) = 0.23    (moderate ascent boost)

SFIP_O = 0.35 × 1.0 + 0.15 × 0.92 + 0.35 × 0.84 + 0.15 × 0.23
       = 0.35 + 0.138 + 0.294 + 0.035
       = 0.817

sfip_100 = 81.7
severity = "HEAVY" (> 55)
```

Compare: Ogimet layered index at −8°C = `100 × 8 × 6 / 49` = 97.96 → would also be high severity.

---

## Summary of What to Implement

1. **Five membership functions**: `membership_temperature()`, `membership_rh()`, `membership_clw()`, `membership_clw_proxy()`, `membership_vv()`
2. **One helper**: `glaciation_factor()` (GFS only, uses ICMR)
3. **Two combining formulas**: SFIP_O (with CLW) and SFIP_4 (with proxy), selected per model
4. **Severity mapping**: `sfip_severity_windy()` (and optionally `sfip_severity_ga()`)
5. **Per-level computation** following the same pattern as Ogimet, with temperature gating
6. **Output fields**: `sfip_raw`, `sfip_100`, `sfip_severity`, `sfip_variant` alongside existing Ogimet fields
