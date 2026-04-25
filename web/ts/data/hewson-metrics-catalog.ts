/** Hewson synoptic-field metrics catalog — pilot-facing explanations, with
 * per-level thresholds and operational interpretation.
 *
 * Source of truth for the (i) info popovers on the Synoptic Forecast tab.
 * Kept separate from web/ts/data/metrics-catalog.json because each Hewson
 * metric has level-specific thresholds (925 vs 850 vs 700 hPa) that don't
 * fit the single-table schema used by the cross-section / Skew-T metrics.
 *
 * Numerical thresholds and physical reasoning come from § 2 + § 4 of
 * designs/future/hewson-fields-aviation-advisories.md, with corrections
 * from a 2026-04-25 Claude Desktop review:
 *   - θe value bands recalibrated (UK summer day at 850 ≈ 305-315 K, NOT
 *     tropical); tropical threshold raised to ~335 K at 850
 *   - |∇θe| thresholds aligned with Hewson 1998 — classical front at
 *     850 hPa is 4-6 K/100 km, not 8+
 *   - TFP rewritten as a position indicator (zero crossing = front line),
 *     not a tendency indicator — earlier text conflated spatial position
 *     with temporal evolution
 *   - Icing claim qualified to the 0 to -15 °C overrunning layer
 *   - Occlusion / stationary boundary pattern called out
 *   - ∂θe/∂t reframed around the ∂θe/∂t vs −V·∇θe decomposition (the
 *     diagnostic value is in the disagreement)
 */

import type { HewsonMetric } from '../visualization/hewson-colormaps';

export interface HewsonLevelThresholds {
  /** Pressure level in hPa. */
  level: number;
  /** Pilot-facing altitude label (e.g. "5,000 ft"). */
  altitude_label: string;
  /** Optional contextual note for this level. */
  note?: string;
  /** Threshold rows specific to this level. */
  rows: Array<{
    range: string;        // e.g. "< 2", "2–4", "> 8"
    label: string;        // e.g. "Benign", "Classical front"
    risk: 'none' | 'low' | 'moderate' | 'high' | 'severe';
    meaning: string;
  }>;
}

export interface HewsonCatalogEntry {
  name: string;
  unit: string;
  vibe: string;                // Italic subtitle — short, evocative
  what_it_is: string;          // Definition (HTML allowed for <em>, <code>, <br>)
  what_map_shows: string;      // What the user is looking at on screen
  /** Optional set of per-level threshold tables. */
  level_thresholds?: HewsonLevelThresholds[];
  /** Optional flat threshold table (when not level-dependent). */
  flat_thresholds?: Array<{
    range: string; label: string; risk: 'none' | 'low' | 'moderate' | 'high' | 'severe'; meaning: string;
  }>;
  /** Multi-level usage paragraph (HTML — list / paragraphs allowed). */
  multi_level_usage?: string;
  /** Operational notes — pilot rules, sign conventions, caveats. */
  pilot_notes?: string;
  /** Where this field can mislead. */
  limitations?: string;
  wikipedia?: string;
  /** "explain how/why ..." continuation pasted into the AI prompt. */
  llm_prompt?: string;
}

export const HEWSON_CATALOG: Record<HewsonMetric, HewsonCatalogEntry> = {
  // -------------------------------------------------------------------------
  theta_e: {
    name: 'θe — equivalent potential temperature',
    unit: 'K',
    vibe: "An air mass's thermodynamic fingerprint",
    what_it_is: `
      <p>The temperature an air parcel would have if you took it through this thought experiment:</p>
      <ol>
        <li>Lift it dry-adiabatically (~1 °C / 100 m of cooling) until it hits 100 % humidity — the LCL (lifting condensation level).</li>
        <li>Keep lifting it <em>moist</em>-adiabatically — much slower cooling, since condensation releases latent heat — until <strong>all</strong> its water vapour has condensed out and fallen as precipitation.</li>
        <li>Then lower it dry-adiabatically (it's bone-dry now) back to a reference pressure of 1000 hPa.</li>
      </ol>
      <p>The number combines <strong>temperature, moisture, and pressure</strong> into one quantity that is conserved under both dry and moist adiabatic processes. Two parcels with the same θe are the <em>same air mass</em> thermodynamically, even if one is currently warm-and-dry and the other cold-and-saturated. That's why θe is the right variable for tracking "tropical vs polar", "maritime vs continental".</p>
    `,
    what_map_shows: `A horizontal slice of θe at the chosen level. Smooth where one air mass dominates; gradients (color "ribbons") reveal frontal zones, dry intrusions, and moisture tongues — even ones the surface chart never draws.`,
    level_thresholds: [
      {
        level: 925, altitude_label: '~2,500 ft',
        rows: [
          { range: '< 290', label: 'Polar / arctic', risk: 'low', meaning: 'Cold continental or arctic origin. Showers + in-cloud icing in winter.' },
          { range: '295–330', label: 'Mid-latitude', risk: 'none', meaning: 'Ordinary European boundary-layer air across the year.' },
          { range: '> 335', label: 'Tropical / subtropical', risk: 'high', meaning: 'Warm humid airmass — convection-prone.' },
        ],
      },
      {
        level: 850, altitude_label: '~5,000 ft',
        note: 'The classical synoptic level. A normal UK summer afternoon sits around 305–315 K — not tropical. Tropical / convection-prone air starts around 335 K here.',
        rows: [
          { range: '< 285', label: 'Polar', risk: 'low', meaning: 'Cold polar / arctic. Showers, in-cloud icing.' },
          { range: '290–325', label: 'Mid-latitude', risk: 'none', meaning: 'Ordinary mid-latitude air. UK summer ≈ 305–315 K.' },
          { range: '> 335', label: 'Tropical / subtropical', risk: 'high', meaning: 'Operationally convection-prone — Mediterranean / Saharan / tropical maritime origin.' },
        ],
      },
      {
        level: 700, altitude_label: '~10,000 ft',
        rows: [
          { range: '< 275', label: 'Polar', risk: 'low', meaning: 'Cold mid-tropospheric air aloft.' },
          { range: '280–310', label: 'Mid-latitude', risk: 'none', meaning: 'Free-troposphere European air.' },
          { range: '> 320', label: 'Tropical / subtropical', risk: 'high', meaning: 'Tropical mid-troposphere — leads warm advection at lower levels by 6–24 h.' },
        ],
      },
    ],
    multi_level_usage: `
      <ul>
        <li><strong>925</strong> for boundary-layer weather (fog, sea-breeze fronts, low ceilings, cold pools)</li>
        <li><strong>850</strong> to track the actual frontal system bringing the weather</li>
        <li><strong>700</strong> to anticipate what's coming next — upper-level patterns lead the surface by 6–24 h</li>
      </ul>
      <p>A 15–20 K change between waypoints at any level = you're crossing between air masses, drawn front or not.</p>
      <p><strong>Note on convective instability.</strong> When θe decreases with height (∂θe/∂z &lt; 0), the column is "convectively unstable" — lifting it produces buoyancy. This map doesn't show vertical structure; you'd need multi-level θe at the same point. Compare 925 vs 850 vs 700 mentally on the same column to spot it.</p>
    `,
    limitations: `θe at one level can't see vertical structure (need multiple levels). It's also free-atmosphere weather — local boundary-layer phenomena (fog at 1,500 ft, low stratus) won't appear in θe at 850.`,
    wikipedia: 'https://en.wikipedia.org/wiki/Equivalent_potential_temperature',
    llm_prompt: 'explain how θe (equivalent potential temperature) is conserved through dry and moist adiabatic processes, why it works as an air-mass tracer for VFR/IFR planning, and how comparing θe at 925, 850 and 700 hPa over the same point reveals air-mass character and convective potential',
  },

  // -------------------------------------------------------------------------
  gradient: {
    name: '|∇θe| — boundary intensity',
    unit: 'K / 100 km',
    vibe: 'How fast the air mass changes as you fly across',
    what_it_is: `<p>The magnitude of the spatial rate-of-change of θe — the textbook scalar for <strong>frontal zone intensity</strong>. A strong gradient is the operational definition of a front.</p>`,
    what_map_shows: `Always positive (it's a magnitude). Large values trace the elongated zones where two different air masses meet — drawn fronts, dry-line edges, sea-breeze boundaries, pre-frontal moisture tongues.`,
    level_thresholds: [
      {
        level: 925, altitude_label: '~2,500 ft',
        note: 'Surface effects (coastlines, terrain edges) generate strong but shallow gradients that aren\'t true fronts — Med vs Atlantic in summer often hits 6–8 K/100 km without a real front.',
        rows: [
          { range: '< 2', label: 'Negligible', risk: 'none', meaning: 'Uniform boundary-layer air.' },
          { range: '2–4', label: 'Weak gradient', risk: 'low', meaning: 'Minor wind shift, scattered low cloud.' },
          { range: '6–8', label: 'Classical front', risk: 'moderate', meaning: 'Cloud band, possible precipitation. Could also be coast/terrain.' },
          { range: '> 8', label: 'Strong front', risk: 'high', meaning: 'Well-defined surface signature.' },
          { range: '> 12', label: 'Very sharp', risk: 'severe', meaning: 'Sharp surface boundary. Expect SIGMETs in the area.' },
        ],
      },
      {
        level: 850, altitude_label: '~5,000 ft',
        note: 'The Hewson reference level. Literature thresholds for "classical front" with typical analysis-grade smoothing are 4–6 K/100 km; that lines up with this table.',
        rows: [
          { range: '< 1', label: 'Negligible', risk: 'none', meaning: 'Uniform air mass.' },
          { range: '1–3', label: 'Weak gradient', risk: 'low', meaning: 'Minor cloud band.' },
          { range: '4–6', label: 'Classical front', risk: 'moderate', meaning: 'Stratus / nimbostratus, precipitation, wind shift, low-level turbulence.' },
          { range: '> 6', label: 'Strong / sharp', risk: 'high', meaning: 'Well-organized front, shear with altitude.' },
          { range: '> 9', label: 'Very sharp', risk: 'severe', meaning: 'Pronounced front. Expect SIGMETs in the area.' },
        ],
      },
      {
        level: 700, altitude_label: '~10,000 ft',
        note: 'Mixing is more efficient up here — true frontal gradients are weaker than at 850.',
        rows: [
          { range: '< 1', label: 'Negligible', risk: 'none', meaning: 'Smooth mid-troposphere.' },
          { range: '1–2', label: 'Weak gradient', risk: 'low', meaning: 'Minor baroclinic zone.' },
          { range: '3–5', label: 'Classical front', risk: 'moderate', meaning: 'Lifted front or jet-entrance zone.' },
          { range: '> 5', label: 'Strong baroclinic', risk: 'high', meaning: 'Strong upper-level forcing, often leads the surface.' },
          { range: '> 7', label: 'Very sharp', risk: 'severe', meaning: 'Intense baroclinic zone aloft.' },
        ],
      },
    ],
    multi_level_usage: `
      <ul>
        <li>Signal at <strong>all three</strong> levels = deep, well-organized front. Classical frontal weather: cloud, precip, shear.</li>
        <li>Signal <strong>only at 925</strong> = shallow / coastal effect — local weather impact only.</li>
        <li>Signal at <strong>700 ahead of 850/925</strong> = upper-level / lifted front, leads the surface; scattered showers possible but not a full frontal passage.</li>
      </ul>
    `,
    limitations: `These thresholds are at our 0.25° grid with the smoothing in compute_hewson_diagnostics. Higher-resolution / less-smoothed data can produce stronger raw values for the same physical front.`,
    wikipedia: 'https://en.wikipedia.org/wiki/Weather_front',
    llm_prompt: 'explain how the magnitude of the θe gradient identifies frontal zones in synoptic analysis, why Hewson 1998 placed the "classical front" threshold at 4–6 K/100 km at 850 hPa, and how grid resolution and smoothing scale affect the raw values pilots see on a forecast map',
  },

  // -------------------------------------------------------------------------
  neg_laplacian: {
    name: '−∇²θe — sharpness',
    unit: 'K / (100 km)²',
    vibe: 'Squall line vs gentle ramp',
    what_it_is: `<p>The negative Laplacian of θe — its second spatial derivative. Tells you whether the gradient is <strong>concentrated</strong> (positive — narrow front) or <strong>spread out</strong> (≈ 0, or negative — broad ramp). Positive values mark where the gradient peaks locally.</p>`,
    what_map_shows: `Diverging signal. Positive (red) = narrow-band fronts. Near-zero = smooth ramps. Negative (blue) = inside a broad transition.`,
    flat_thresholds: [
      { range: '> 2', label: 'Sharp', risk: 'high', meaning: 'Narrow-band precipitation, quick wind shift over a few nm.' },
      { range: '0–2', label: 'Mild peak', risk: 'low', meaning: 'Discernible front edge, transition not abrupt.' },
      { range: '≈ 0', label: 'Smooth', risk: 'none', meaning: 'If there\'s weather, spread over 100+ km.' },
      { range: '< 0', label: 'Plateau', risk: 'none', meaning: 'You are inside a broad transition, not at its edge.' },
    ],
    pilot_notes: `<p>High −∇²θe combined with high |∇θe| = sharp <em>and</em> strong front — least friendly for VFR. (Note: "expect SIGMETs in the area" is the right framing — SIGMETs are issued for specific phenomena, not gradient magnitudes.)</p>`,
    limitations: `At 925 the field is noisy from coastline / terrain artefacts — interpret cautiously. At 850 and 700 it's the cleanest "is this front sharp?" signal.`,
    wikipedia: 'https://en.wikipedia.org/wiki/Frontogenesis',
    llm_prompt: 'explain how the negative Laplacian of θe distinguishes sharp narrow-band fronts (squall-line type) from broad gradient zones, and how this maps to Hewson\'s frontogenesis framework where second derivatives identify zones of locally maximum gradient',
  },

  // -------------------------------------------------------------------------
  tfp: {
    name: 'TFP — thermal front parameter',
    unit: 'K / (100 km)²',
    vibe: 'Where exactly is the front line drawn?',
    what_it_is: `
      <p>Hewson's locator for the front <em>line</em>. Mathematically: the gradient of |∇θe| projected onto the direction of ∇θe. The function reaches its <strong>maximum</strong> on the warm-side boundary of the frontal zone, <strong>zero-crosses</strong> (positive → negative as you move warm→cold) at the front line itself, and reaches its <strong>minimum</strong> on the cold-side boundary.</p>
      <p>So TFP is a <strong>position indicator</strong>, not a tendency indicator. Whether conditions are deteriorating or improving for you depends on which way the front is moving — that's an advection question. Use −V·∇θe and ∂θe/∂t for tendency; use TFP to see <em>where the front line is</em>.</p>
    `,
    what_map_shows: `Diverging field. The <strong>zero contour</strong> (going + → −) is the analyzed front line; the sign tells you which side of the frontal zone you're on.`,
    flat_thresholds: [
      { range: 'TFP > 0', label: 'Warm-air side', risk: 'low', meaning: 'You are between the front line and the warm-side boundary of the frontal zone.' },
      { range: 'TFP ≈ 0', label: 'Front line', risk: 'high', meaning: 'You are crossing the front itself — peak gradient in the zone.' },
      { range: 'TFP < 0', label: 'Cold-air side', risk: 'low', meaning: 'You are between the front line and the cold-side boundary.' },
    ],
    pilot_notes: `<p>Combined with wind direction and the −V·∇θe map, TFP tells you whether the front is moving toward you or away. Two pilots in the same TFP-positive area can experience very different weather depending on whether they're flying with or against the front's motion.</p>`,
    limitations: `TFP at <strong>850</strong> is canonical (Hewson's original work). At <strong>925</strong> it is noisier (bumpy boundary-layer θe). At <strong>700</strong> the field is smoother but the front is <em>aloft</em> — surface position can be displaced 50–150 km from the 700 zero-contour because fronts tilt with height (warm fronts slope forward, cold fronts slope backward).`,
    llm_prompt: 'explain the Thermal Front Parameter (TFP) from Hewson 1998 — its mathematical definition as the directional derivative of |∇θe| along the gradient, what its zero crossing represents, why it is a position indicator rather than a tendency indicator, and how forecasters use the locating equation alongside it',
  },

  // -------------------------------------------------------------------------
  advection: {
    name: '−V·∇θe — warm/cold advection',
    unit: 'K / h',
    vibe: 'What kind of air is on its way',
    what_it_is: `
      <p>The rate of change of θe at a fixed point caused by horizontal wind alone. <strong>Sign convention:</strong> −V·∇θe &gt; 0 means wind is blowing from high-θe air toward low-θe air → warm air is being brought in (warm advection). The "−" in the formula gives this intuitive sign.</p>
      <p>This is the most flight-relevant Hewson field — sign and magnitude both matter.</p>
    `,
    what_map_shows: `Diverging field; <strong>red = warm advection, blue = cold advection</strong>. Hot spots are leading edges of advancing air masses.`,
    flat_thresholds: [
      { range: '> 2 K/h', label: 'Rapid warm', risk: 'severe', meaning: 'Frontal passage within 1–2 h. Warm sector arriving fast.' },
      { range: '1 to 2 K/h', label: 'Warm advection', risk: 'high', meaning: 'Significant tendency — change over 3–6 h. Ceilings dropping, icing rising.' },
      { range: '−1 to 1 K/h', label: 'Negligible', risk: 'low', meaning: 'Quasi-stationary regime. Watch for occlusions if |∇θe| is high.' },
      { range: '−2 to −1 K/h', label: 'Cold advection', risk: 'moderate', meaning: 'Polar air arriving — improving visibility, gusty unstable showers.' },
      { range: '< −2 K/h', label: 'Rapid cold', risk: 'high', meaning: 'Cold front passage within 1–2 h. Wind shift, possible squalls.' },
    ],
    pilot_notes: `
      <p><strong>Warm advection (red, &gt; 0):</strong></p>
      <ul>
        <li>Moist warm air overrunning colder air → stratiform cloud, persistent rain</li>
        <li>Ceilings <em>lower over time</em>; visibility reduces</li>
        <li><strong>Icing risk rises in the 0 to −15 °C layer</strong> where warm moist air overruns sub-freezing air — typically 3,000–10,000 ft for a classical warm front. Above the warm sector (FL180+) you may be in cirrus with no significant icing.</li>
        <li>Wind typically backs (rotates CCW) with height</li>
        <li><em>Rule:</em> "depart before the warm front arrives"</li>
      </ul>
      <p><strong>Cold advection (blue, &lt; 0):</strong></p>
      <ul>
        <li>Polar / drier air replacing warmer air → improving visibility, unstable showery weather</li>
        <li><strong>Icing risk drops</strong> as moisture clears</li>
        <li>Gusty winds, mechanical turbulence</li>
        <li>Wind typically veers (rotates CW) with height</li>
        <li><em>Rule:</em> "wait for the cold front to clear, then VMC"</li>
      </ul>
      <p><strong>Pattern to watch for: occluded / stationary boundaries.</strong> If |∇θe| is high but −V·∇θe is <strong>near zero</strong>, the front isn't moving — suspect an <strong>occlusion</strong> or stationary boundary (common over the UK and Alps). Conditions don't follow either warm-front or cold-front rules cleanly.</p>
    `,
    multi_level_usage: `
      <ul>
        <li><strong>925</strong> advection: what's hitting the surface <em>now</em> — ceilings, visibility, low-level winds.</li>
        <li><strong>850</strong> advection: classical "front pattern" — best 6–12 h indicator.</li>
        <li><strong>700</strong> advection: leads the surface by 6–24 h. Strong 700 without 850 yet = "incoming, decide now."</li>
      </ul>
      <p>Useful pattern: warm advection at 700 + 850 with cold advection at 925 = warm front aloft over cold pool → high icing, persistent IFR.</p>
    `,
    wikipedia: 'https://en.wikipedia.org/wiki/Advection',
    llm_prompt: 'explain how horizontal advection of θe (−V·∇θe) predicts ceilings, visibility and icing trends ahead of warm and cold fronts, why the sign convention works the way it does (positive = warm advection), and how to recognise occluded or stationary boundaries from a high-gradient / low-advection signature',
  },

  // -------------------------------------------------------------------------
  tendency: {
    name: '∂θe/∂t — local tendency',
    unit: 'K / h',
    vibe: 'What is actually happening here, vs what advection predicts',
    what_it_is: `
      <p>The <strong>observed</strong> rate of change of θe at a fixed point. The full decomposition is:</p>
      <p style="font-family:monospace;text-align:center">∂θe/∂t = −V·∇θe + (diabatic + vertical terms)</p>
      <p>The first term is what advection alone predicts. The rest captures <strong>diurnal heating/cooling, latent heat release in convection, and radiative cooling</strong>. The interesting case is when ∂θe/∂t and −V·∇θe <em>disagree</em> — that tells you the air mass itself is being modified, not just being moved.</p>
    `,
    what_map_shows: `Diverging — where θe is rising or falling. Computed by central differences across the snapshot's 3 h timesteps.`,
    flat_thresholds: [
      { range: '> 1 K/h', label: 'Rising fast', risk: 'high', meaning: 'Warm sector arriving, or daytime heating in the boundary layer.' },
      { range: '0 to 1 K/h', label: 'Rising', risk: 'low', meaning: 'Slow warming. Cross-check with advection.' },
      { range: '≈ 0', label: 'Stable', risk: 'none', meaning: 'Current pattern persists.' },
      { range: '−1 to 0 K/h', label: 'Falling', risk: 'low', meaning: 'Slow cooling.' },
      { range: '< −1 K/h', label: 'Falling fast', risk: 'high', meaning: 'Post-frontal clearing, evening cooling, or cold pool spreading.' },
    ],
    pilot_notes: `
      <p>Compare ∂θe/∂t to −V·∇θe at the same point:</p>
      <ul>
        <li><strong>Tendency &gt; advection</strong>: diabatic warming on top of advection. Daytime CAPE building under a warm-advection regime → thunderstorm risk <em>amplified</em> beyond what advection alone suggests.</li>
        <li><strong>Tendency &lt; advection</strong>: cooling beyond what advection brings — radiative cooling at night, evaporation from precip, cold pools spreading.</li>
        <li><strong>Tendency ≈ advection</strong>: pure advective regime. The air is being moved, not modified.</li>
      </ul>
    `,
    limitations: `At <strong>925</strong> the diurnal cycle is large — expect ±1–2 K/h just from sun/night, even without synoptic change. At <strong>850 and 700</strong>, virtually all signal is genuine air-mass change.`,
    llm_prompt: 'explain how the local tendency ∂θe/∂t differs from the advection term −V·∇θe in the full θe budget equation, how their disagreement reveals diabatic processes (daytime heating, latent heat release in convection, radiative cooling), and what operational situations make this decomposition useful for thunderstorm and overnight-cooling forecasting',
  },
};
