# Meteorology Change Review Checklist

Use this checklist for any PR that changes weather equations, units, thresholds,
missing-data handling, severity mapping, aggregation, provenance, or weather
visualization thresholds.

- [ ] A fresh model/session reviewed the authoritative design docs and full diff.
- [ ] The reviewer independently checked equations and units.
- [ ] Missing data cannot appear as clear/GREEN.
- [ ] Percentages and extents use evaluated evidence domains; missing inputs do not dilute hazards.
- [ ] Backend and visual thresholds agree.
- [ ] Method, compound-tie, and representative-model attribution are correct.
- [ ] Any calibration change cites literature, an independent oracle, or observations.
- [ ] Deferred calibration findings have explicit follow-up issues.
- [ ] The review links exact reproductions, regression names, commands/counts, and known gate limitations.
- [ ] User-visible changes include screenshots, or the PR explains why screenshots are not applicable.
