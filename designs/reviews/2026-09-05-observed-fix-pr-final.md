## What & why

Correct the interpretation, freshness, persistence and display of observed radar,
lightning and satellite data following the review of PR #584. This remains an
observation display, not a forecast or route-safety assessment.

- **Validity and coverage:** use the FCI status/quality flags and documented
  retrieval-method meanings; remove unsupported multilayer interpretations.
  Preserve positive detections under partial coverage and qualify negative claims.
- **Honest labels:** distinguish geometric feet MSL from pressure flight levels,
  valid-retrieval sample shares from sky-area coverage, and IR effective cloudiness
  from visual opacity. Do not guess an unverified cloudiness scale.
- **Freshness and provenance:** retain immutable UTC observation times/windows in
  saved text; advance per-source display ages without weather polling. Map badges
  use the actual returned image's time, window and attribution.
- **iOS cache durability:** atomically persist same-timestamp realtime refreshes,
  including Siri, while preserving unrelated/unknown snapshot fields and reporting
  persistence failures. Add regression tests for reopen and failure paths.
- **Map correctness:** paint projected, parallax-corrected satellite footprints;
  resolve overlaps using the highest geometric top and its associated temperature;
  bound projection batches. Fix failed-request retries, stale-response races,
  object-URL ownership, flash expiry and map teardown.
- **Usability:** keep the source selector consistent with the rendered fallback,
  preserve explicit None, prevent duplicate refresh requests, and stop long source
  labels, legends and opacity controls overlapping or overflowing on narrow screens.

The full findings, source references and future visualization options are recorded
in Markdown. Design documentation and both bundled help catalogs are updated.

## Issue linkage

Follow-up to merged PR #584; related to observed-conditions work #574. Neither is
automatically closed by this corrective PR. No new tracking issue was created.

## Testing / verification

- Full isolated Python suite: **5,782 passed**, 20 skipped, 23 deselected.
- Targeted observed/API suite: **190 passed**, 6 deselected.
  These Python results are from the initial correction commit; backend code has
  not changed since that run.
- Full web unit suite: **817 passed in 50 files**.
- Full-entrypoint Chrome suite: **10 passed**, covering actual Leaflet/PNG
  decoding, provenance, source selection, refresh recovery, races/cleanup, flash
  aging and long-label layouts at 1280, 390 and 320px. An independent alternate-base
  run also passed all ten tests.
- Application TypeScript check, bundled help-JSON consistency and diff whitespace
  checks passed.
- Independent read-only reviews closed the reported Critical/Important code
  findings within the correction scope. The iOS review is static-only.

Browser checks use controlled HTTP fixtures and an in-memory entrypoint bundle:
no application server, production build, shared database or live-provider requests.
Existing Python and Node warnings are retained in the verification record.

## Deferred validation and limits

- **Mac/iOS compilation, unit and UI execution are deferred at the user's request.**
  Written Swift tests have not been executed; this is not iOS merge/release approval.
- Real-granule checks remain outstanding for FCI status/packing, IR-cloudiness
  scaling, large low-cloud parallax offsets and LI coverage. Chrome fixtures do
  not establish live-weather, Safari/WebKit or actual-device correctness.
- The optional standalone browser-harness typecheck did not pass because the
  repository lacks Node typings. Application TypeScript passes; browser execution
  is verified separately. No dependency was added for that optional probe.
- No animation, nowcasting, motion vectors, radar/satellite storm association or
  encounter prediction is implemented. Those proposals remain future work.

## Compatibility and rollout

- No new dependency, database migration, advisory logic or weather polling.
- Existing JSON keys remain compatible; `X-Observed-Window-Minutes` is additive.
  Satellite footprint rectangles are display approximations, not exact cloud masks.
- Existing saved packs need explicit realtime refresh/recomputation after rollout
  to obtain corrected decoded states and summary wording. Old aggregates cannot
  recover quality evidence that was never retained. No user weather data was deleted.
- These delayed observations support situational awareness, not tactical storm
  avoidance or assurance of a safe route or overflight altitude.

## Review evidence

- `designs/reviews/2026-09-05-pr584-observed-review.md`: complete findings, sources
  and future visualization/motion/association proposals.
- `designs/reviews/2026-09-05-observed-fix-verification.md`: reproduction evidence,
  test commands/results, reviewer dispositions and outstanding validation.
