Review target: $ARGUMENTS

If $ARGUMENTS is a GitHub PR URL or repo/pull/number:

- Use gh to fetch the PR diff and description
- Focus review on changed lines only
- Post the review as a PR comment using gh pr comment

Otherwise:

- Interpret $ARGUMENTS as a description of what to review
- Use git log, git diff, or file reads as appropriate to find the relevant changes

---

## Context loading

Before starting the review, read the following:

- CLAUDE.md at the repo root for coding standards
- Any design documents in /designs/ folder relevant to files being changed - use these for module intent and architecture

---

## Single-pass completeness requirement

**Do one complete pass. Find every issue. Report them all.**

Do not hold issues back for a follow-up round. If there are 10 real issues, report all 10 now. The goal is one complete review that can be acted on in one go, not an iterative dialogue.

Group output by severity:

- **Critical** - bugs, logic errors, data corruption, security issues: must fix before merge
- **Important** - architecture violations, design deviations, correctness problems: should fix
- **Minor** - best practices, missed optimisations, low-effort improvements: fix if low effort

Within each group, order by file/module for readability.

---

## Review criteria

Apply all of the following in the single pass:

**General**
- Bugs and logic errors
- CLAUDE.md violations
- Deviations from documented architecture/design intent in /designs/
- Code and logic duplication, opportunities for consolidation
- Simplicity, maintainability and extensibility

**Swift/iOS**
- Memory management (retain cycles, weak/unowned correctness)
- Concurrency (actor isolation, data races, MainActor usage)
- iOS best practices

**TypeScript**
- Type safety, avoid `any`
- async/await correctness

**Python**
- Type hints
- Error handling
- Async patterns if applicable

---

## Weather analysis code

If the PR touches weather analysis code (formulas, thresholds, sounding pipeline, layer building, advisory evaluators, or data model changes in the analysis path), also check:

**Meteorological validity**
Do the formulas, thresholds, and assumptions hold from an aviation weather / thermodynamics standpoint? Are units consistent? Are threshold values reasonable for the NWP resolution used? Flag anything that contradicts standard meteorological practice or could produce misleading results for a pilot.

**Cross-metric impact**
Does the same pattern, formula, or data structure exist in a sibling module or parallel metric? (e.g., Ri-based CAT layers and E-Shear layers share grouping logic; icing has Ogimet, SFIP, and IENG variants.) If the change fixes or improves one, check whether the same fix applies to the others. List sibling modules reviewed and whether they need a matching change.

**Cross-model robustness**
The pipeline processes GFS, ECMWF, ICON, UKMO, and MeteoFrance, which differ in available fields, pressure level spacing, and variable naming. For any change, check:

- Is the logic at the right abstraction level for all models, or is it model-specific when it shouldn't be (or vice versa)?
- If it relies on an input field (omega, cloud water, geopotential), does it fail gracefully when that field is absent for some models?
- Could differences in vertical resolution (e.g., GFS 25hPa vs UKMO 50hPa spacing) cause the same logic to behave very differently across models?
- Are thresholds calibrated for one model's characteristics but applied to all?

---

## Web frontend code

If the PR touches web frontend code (HTML, TypeScript, CSS), also check:

**CSP compliance**
Read deploy/weather.flyfun.aero.caddy for the active Content-Security-Policy. Check whether any new or changed code would be blocked:

- New external script/stylesheet loads (CDNs) - blocked by script-src/style-src
- New fetch()/XHR to external domains - blocked by connect-src
- New images from external domains (not tile servers) - blocked by img-src
- New inline scripts in HTML without 'unsafe-inline' in script-src
- New `<form action="...">` to unlisted domains - blocked by form-action
- New iframes - blocked by frame-ancestors/default-src

If a violation is found, flag it and suggest either updating the CSP or refactoring to stay within the current policy.

---

## Do NOT flag

- Style issues not covered by CLAUDE.md
- Nits or minor suggestions below the Minor threshold
- Pre-existing issues not touched by this PR

If no issues found, post a brief approval comment.
