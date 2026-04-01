Review target: $ARGUMENTS

If $ARGUMENTS is a GitHub PR URL or repo/pull/number:
- Use `gh` to fetch the PR diff and description
- Focus review on changed lines only
- Post the review as a PR comment using `gh pr comment`

Otherwise:
- Interpret $ARGUMENTS as a description of what to review
- Use git log, git diff, or file reads as appropriate to 
  find the relevant changes

Then apply the standard review criteria:

Before starting the review, read the following for context:
- CLAUDE.md at the repo root for coding standards
- Any design documents in /designs/ folder relevant to files being changed
- use the designs documents for module intent and architecture

Then perform a code review of the current PR focusing on:
- Bugs and logic errors
- CLAUDE.md violations
- Deviations from the documented architecture/design intent from the designs documents
- Swift/iOS best practices (memory management, concurrency)
- TypeScript: type safety, avoid `any`, async/await correctness
- Python: type hints, error handling, async patterns if applicable
- Code and logic duplication, opportunity for optimisation and consolidation
- Check for simplicity, maintainability and extensibility

If the PR touches weather analysis code (formulas, thresholds, sounding pipeline,
layer building, advisory evaluators, or data model changes in the analysis path):

- **Meteorological validity**: Do the formulas, thresholds, and assumptions hold
  from an aviation weather / thermodynamics standpoint? Are the physical units
  consistent? Are threshold values reasonable for the NWP resolution we use?
  Flag anything that contradicts standard meteorological practice or could
  produce misleading results for a pilot.

- **Cross-metric impact**: Does the same pattern, formula, or data structure
  exist in a sibling module or parallel metric? (e.g., Ri-based CAT layers
  and E-Shear layers share the same grouping logic; icing has Ogimet, SFIP,
  and IENG variants.) If the change fixes or improves one, check whether the
  same fix should be applied to the others. List any sibling modules that
  were reviewed and whether they need a matching change.

- **Cross-model robustness**: The pipeline processes multiple NWP models
  (GFS, ECMWF, ICON, UKMO, MétéoFrance) which differ in available fields,
  pressure level spacing, and variable naming. For any change, check:
  - Is the logic at the right abstraction level to apply to all models,
    or is it model-specific when it shouldn't be (or vice versa)?
  - If it relies on an input field (omega, cloud water, geopotential),
    does it fail gracefully when that field is absent for some models?
  - Could differences in vertical resolution (e.g., GFS 25hPa vs UKMO
    50hPa spacing) cause the same logic to behave very differently?
  - Are thresholds calibrated for one model's characteristics but applied
    to all?

If the PR touches web frontend code (HTML, TypeScript, CSS):

- **CSP compliance**: Read `deploy/weather.flyfun.aero.caddy` for the active
  Content-Security-Policy. Check whether any new or changed code would be
  blocked by the policy:
  - New external script/stylesheet loads (CDNs) → blocked by `script-src`/`style-src`
  - New `fetch()`/XHR to external domains → blocked by `connect-src`
  - New images from external domains (not tile servers) → blocked by `img-src`
  - New inline scripts in HTML without `'unsafe-inline'` in `script-src`
  - New `<form action="...">` to unlisted domains → blocked by `form-action`
  - New iframes → blocked by `frame-ancestors`/`default-src`
  If a violation is found, flag it and suggest either updating the CSP or
  refactoring to stay within the current policy.

Do NOT flag:
- Style issues not covered by CLAUDE.md
- Minor suggestions or nits
- Pre-existing issues not touched by this PR

Be concise. High confidence issues only.

If no issues found, post a brief approval comment.
