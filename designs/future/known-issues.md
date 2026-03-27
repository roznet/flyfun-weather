# Known Issues & Things to Revisit

Items to periodically review. When resolved, move to the bottom under "Resolved".

---

## Bundle endpoint memory usage

**Added:** 2026-03-27
**Location:** `src/weatherbrief/api/packs.py` — `get_bundle()`

The bundle endpoint builds the entire JSON response in memory before gzip-compressing it. For long flights (~60 route points x 3 models = 180 sounding profiles), this involves:

- Loading `cross_section.json` (can be tens of MB) fully into memory
- Calling `_build_sounding_profile()` ~180 times, each doing Pydantic model validation
- Holding the full JSON dict + serialized payload + gzip output simultaneously

For the short flights tested so far (8 points, 472KB uncompressed, 47KB gzip) this is fine. For very long routes the uncompressed bundle could reach 50-60MB, meaning ~100-200MB transient memory per concurrent request.

**Options if this becomes a problem:**
- Stream the gzip output instead of building in memory
- Pre-compute and cache the bundle on disk after each refresh
- Limit concurrency on this endpoint (similar to existing `plot_limiter`)

---

## Resolved

_(none yet)_
