/** Info-popup content for the experimental cross-section "Fronts" layer.
 *
 * Fronts are a hard concept, so this is layered: a plain-language explanation
 * first, then how relevance is judged, then the actual Hewson maths for advanced
 * users — plus references and an AI-discussion seed. Returned as an HTML string
 * for showPopupContent() (components/info-popup.ts), reusing the same popup CSS
 * classes as the metric / synoptic-Hewson popups. English-only, matching the
 * other rich info catalogs (data/hewson-metrics-catalog.ts, metrics-helper.ts).
 */

function escapeAttr(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/"/g, '&quot;');
}

const NAME = 'Front detection (experimental)';

// The AI-discussion subject is the *meteorological method*, not our feature
// label — "(experimental)" is about our code, irrelevant to the chat. Naming
// Hewson + TFP + θe points the model straight at the right technique. This
// fills the "Tell me more about {metric} …" slot in info-popup.ts.
const AI_TOPIC =
  'the Hewson Thermal Front Parameter (TFP) method for locating fronts from '
  + 'equivalent potential temperature (θe)';

// Fits the "… In particular, {llm_prompt}." continuation.
const LLM_PROMPT =
  'explain in practical flying terms what crossing such a front means for a '
  + 'light aircraft; how to read cold vs warm vs quasi-stationary crossings; '
  + 'why ECMWF, GFS and ICON can disagree about the same boundary; and the '
  + 'method’s blind spots — orographic θe gradients over mountains that '
  + 'masquerade as fronts, dry boundaries that carry no weather, and that an '
  + '850 hPa θe field cannot see low cloud or fog';

export function renderFrontsInfo(): string {
  return `
    <div class="popup-header">
      <h3>${NAME}</h3>
      <p class="popup-vibe">Where your route crosses the boundary between two different air masses — and whether that boundary actually carries weather.</p>
    </div>
    <div class="popup-body">

      <div class="popup-section">
        <h4>In plain terms</h4>
        <p>A front is the edge between two air masses — say warm, humid air ahead and cooler, drier air behind. Crossing one you might meet a wind shift, a band of cloud or showers, some turbulence, or — if the air is unstable — towering build-ups. This overlay marks, for each model, the point along your route where your track crosses such a boundary.</p>
      </div>

      <div class="popup-section">
        <h4>Reading the markers</h4>
        <p>A vertical line on the cross-section sits at the crossing point along the route, drawn at the level nearest your cruise. Its colour is the type:</p>
        <ul>
          <li><strong>Cold</strong> — colder air advancing; often a sharper, showery, gusty transition that clears behind.</li>
          <li><strong>Warm</strong> — warmer, moister air overrunning; lowering cloud and more persistent rain ahead of it.</li>
          <li><strong>Quasi-stationary</strong> — little movement; weather lingers near the boundary.</li>
        </ul>
      </div>

      <div class="popup-section">
        <h4>How we judge whether it matters</h4>
        <p>Not every air-mass boundary brings weather, so a bare crossing isn’t graded on its own. We read the same model’s cloud and convection <em>at the crossing</em>, shown in the cross-section beneath the line:</p>
        <ul>
          <li><strong>Dry</strong> boundary (clear air) → essentially just a wind shift.</li>
          <li><strong>Wet</strong> boundary (cloud / precipitation) → expect a band of IMC, rain, and shear.</li>
          <li><strong>Convective</strong> boundary → build-ups that can tower <em>far above</em> the boundary, so a front you overfly can still matter.</li>
        </ul>
        <p>We also check the boundary <strong>persists</strong> over a few hours and whether the models <strong>agree</strong>: a flickering, single-model boundary sitting over high terrain is usually an orographic artifact, not a real front.</p>
      </div>

      <div class="popup-section">
        <h4>How it’s calculated <span class="popup-unit">(for the curious)</span></h4>
        <p>This is the <strong>Hewson (1998) objective-front</strong> method restricted to your route. It works on <strong>equivalent potential temperature θe</strong> — a single quantity that bundles temperature and humidity, so it tracks air masses better than temperature alone.</p>
        <ul>
          <li>The <strong>front axis</strong> is the zero line of the <strong>Thermal Front Parameter (TFP)</strong> — the locus where the θe gradient is sharpest (a <em>position</em> indicator, not a tendency).</li>
          <li>A crossing is kept only if both the gradient magnitude <code>|∇θe|</code> <em>and</em> the air-mass jump <code>Δθe</code> across it are strong enough — weak or dry gradients, and gradient “cols”, are rejected.</li>
          <li>Cold vs warm comes from the <strong>advection</strong> sign <code>−V·∇θe</code>; intensity (significant / classical / sharp) from <code>|∇θe|</code>.</li>
          <li>Fields are precomputed at <strong>925 / 850 / 700 hPa</strong> from ECMWF, GFS and ICON, then sampled along your route <em>at each waypoint’s ETA</em> — so a moving front is placed where and when you actually meet it.</li>
        </ul>
      </div>

      <div class="popup-section popup-limitations">
        <h4>Limitations</h4>
        <ul>
          <li>A <strong>free-atmosphere</strong> signal: an 850 hPa θe field does not see low stratus or fog — that stays METAR/TAF territory.</li>
          <li>Position ±50–100 km, timing ±1–2 h; treat it qualitatively.</li>
          <li>Models disagree, and <strong>orographic</strong> θe gradients over mountains can masquerade as quasi-stationary “fronts”.</li>
          <li><strong>Advisory only</strong> — not an official SIGWX / SIGMET product.</li>
        </ul>
      </div>

      <div class="popup-section popup-learn-more">
        <a href="https://en.wikipedia.org/wiki/Weather_front" target="_blank" rel="noopener noreferrer">Weather fronts on Wikipedia ↗</a>
        &nbsp;·&nbsp;
        <a href="https://en.wikipedia.org/wiki/Equivalent_potential_temperature" target="_blank" rel="noopener noreferrer">Equivalent potential temperature ↗</a>
        <p style="margin:0.4rem 0 0;font-size:0.85em;opacity:0.8">Method: Hewson, T. D. (1998), “Objective fronts”, <em>Meteorological Applications</em> 5(1), 37–65. There is no dedicated Wikipedia page for the Hewson method; the links above cover the underlying concepts.</p>
      </div>

      <div class="popup-section popup-discuss-ai" data-metric-name="${escapeAttr(AI_TOPIC)}" data-llm-prompt="${escapeAttr(LLM_PROMPT)}">
        <div class="popup-discuss-header">
          <span class="popup-discuss-label">Discuss with AI</span>
          <span class="popup-discuss-hint">A prompt is copied to clipboard — just paste it in the new chat</span>
        </div>
        <div class="popup-discuss-buttons">
          <a href="https://claude.ai/new" target="_blank" rel="noopener noreferrer" class="popup-ai-btn popup-ai-claude" data-ai="claude">Claude</a>
          <a href="https://chatgpt.com/" target="_blank" rel="noopener noreferrer" class="popup-ai-btn popup-ai-chatgpt" data-ai="chatgpt">ChatGPT</a>
          <a href="https://gemini.google.com/app" target="_blank" rel="noopener noreferrer" class="popup-ai-btn popup-ai-gemini" data-ai="gemini">Gemini</a>
        </div>
        <div class="popup-discuss-toast" hidden>Prompt copied! Paste it into the chat.</div>
      </div>
    </div>
  `;
}
