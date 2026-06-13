# Refactor: `nwp_cloud_layers` strictly native NWP

> **STATUS (2026-06-13): SHIPPED & MERGED.** Branch/worktree gone. The
> strict-native refactor is live: `build_nwp_cloud_layers` returns native
> (`nwp_3d`/`grib`) layers or `None` (no synth fallback;
> `clouds.py:507`); Ogimet-NWP/IENG icing short-circuit to `[]` on
> `None`/empty clouds (`icing.py:441,517`); the model field is typed
> `list[EnhancedCloudLayer] | None` (`models/analysis.py:682`) and `None`
> is preserved end-to-end (`__init__.py:466`); the frontend gates
> `nwp-cloud-bands` on `nwpCloudLayers !== null` and `nwp-convective-bg`
> on `hasNwpConvective` (`data-extract.ts:452,474`). Tests:
> `tests/test_nwp_cloud_layers_from_cc.py`, plus `test_icing.py`/`test_clouds.py`.
>
> **Deferred (item 4, by design, not abandoned):** `decode.py` does NOT
> fake ECMWF single-level `top_ft`. ECMWF cloud decks come from per-level
> `cc` (`nwp_3d` source) — wired (`decode.py:39`, `_ECMWF_FRAC_TO_PCT`).
> The open follow-up is the separate ticket: confirm per-level `cc`
> actually produces `nwp_3d` layers in prod (was empty on the test flight).
>
> The durable design knowledge (source taxonomy, no-synth rationale,
> bulk-% as severity-modulator-not-gate) now lives in the docstrings of
> `clouds.py` / `icing.py`. This plan can be archived.

## Why

Today `nwp_cloud_layers` conflates three sources:

1. `nwp_3d` — per-level cloud fraction (ECMWF `cc`, ICON `clc`)
2. `grib` — single-level GRIB diagnostics with explicit base/top (GFS bands)
3. `synthesized` — Open-Meteo bulk `cloud_cover_low/mid/high_pct`, narrowed by DD evidence + inversions

Sources 1 and 2 are real model-native cloud envelopes. Source 3 is a DD layer
narrowed by NWP bulk percentages — semantically a DD-derived hybrid, not an
NWP layer. Treating it as `nwp_cloud_layers` causes:

- **Frontend toggle confusion**: `data-extract.ts` disables the "NWP Layers"
  toggle when `nwp_cloud_layers` is empty, but can't distinguish "model
  predicts clear sky" from "no NWP enrichment exists." Users see the toggle
  greyed out for ECMWF (clear-sky forecast) and assume NWP enrichment is
  broken.
- **Phantom Ogimet-NWP / IENG icing zones** for non-GRIB models: those
  icing variants gate on `nwp_cloud_layers` regardless of source. ICON
  (no GRIB) produces "Ogimet-NWP" icing zones from synthesized envelopes,
  which is misleading — the cross-section can't show a corresponding
  cloud band, so icing appears to float in clear sky.

`tasks/advise.py` and `dd_nwp_agreement.py` are already source-aware
(tag `nwp_synthesized`, exclude synth from agreement checks). The fix is
to push that awareness into the data model so all consumers behave
consistently.

## Decision: option (b) — strict native

`nwp_cloud_layers` returns native (`grib` / `nwp_3d`) layers only, or
`None` when no native source is available. No synth fallback at all.

Rejected option (a) — bulk-% gating in icing — because it would produce
icing zones with no visible cloud band to anchor them, which the user
called out as the kind of inconsistency that erodes trust in the
cross-section.

## Bulk percentages stay useful

`cloud_cover_low/mid/high_pct` keeps its existing role as **severity
modulator** inside the icing functions (modulates the index by cloud
fraction at altitude). It just no longer doubles as a cloud-presence gate.

## Changes

### Backend

1. **`src/weatherbrief/analysis/sounding/clouds.py`**
   - `build_nwp_cloud_layers`: drop the synth fallback. Returns native
     layers (`grib`/`nwp_3d` source) or `None` when neither path produces
     anything. Returns `[]` when GRIB ran but no band met the threshold.
   - Delete `_synthesize_nwp_layers` and any private helpers used only
     by it.

2. **`src/weatherbrief/analysis/sounding/icing.py`**
   - `assess_icing_zones_ogimet_nwp`: short-circuit to `[]` when
     `nwp_cloud_layers` is `None` or empty. Bulk %s remain as severity
     modulator only.
   - `assess_icing_zones_ieng`: same short-circuit.

3. **`src/weatherbrief/analysis/sounding/sfip.py`**
   - SFIP "full" already requires CLW (only present with GRIB), so
     synth-only models naturally fall to "proxy" gating on DD layers.
     No change needed, but verify the `nwp_clouds = nwp_cloud_layers or []`
     fallback at line 338 still does the right thing when callers pass
     `None` (it does — `None or []` is `[]`).

4. **`src/weatherbrief/fetch/grib/decode.py`** — **deferred from this PR**.
   - Originally planned to set `low.top_ft` so that the GRIB single-level
     path could produce layers for ECMWF.
   - On closer look, ECMWF single-level products only ship band *bases*
     (`ceil`, `cbh`) and bulk cover fractions (`lcc`/`mcc`/`hcc`/`tcc`)
     — no per-band tops. The only way to populate `top_ft` is to fake
     it to the ICAO band ceiling, which is exactly the "no visual
     anchor" pathology we just removed from the icing path. Doing so
     in `_build_grib_layers` would re-introduce the same problem we
     just fixed.
   - The proper ECMWF cloud-layer path is per-level `cc`
     (`nwp_3d` source). `ecmwf_fetch.py` already requests `cc` and
     `decode.py` already maps it to `cloud_area_fraction_pct` per
     pressure level. The pipeline appears correctly wired but the
     test flight produced empty `nwp_cloud_layers` for ECMWF —
     either the threshold (12.5%) wasn't met or the merge has a
     gap. **Follow-up**: investigate why per-level `cc` isn't
     producing `nwp_3d` layers in production (separate ticket).

5. **`src/weatherbrief/analysis/sounding/__init__.py`**
   - Update `nwp_cloud_layers or []` calls (lines 363, 380, 391) — most
     can stay as-is because `or []` already coerces `None` correctly.
     Verify no caller relies on `nwp_cloud_layers` being non-None.

6. **`src/weatherbrief/api/packs.py:2081`** — JSON serialization. Make sure
   `None` round-trips as `null` (don't coerce to `[]`). Pydantic does this
   correctly by default; the change in the source-of-truth model is
   already typed `list[...] | None`, so this should just work.

### Frontend

7. **`web/ts/visualization/data-extract.ts:212-226`**
   - Pass `nwpCloudLayers` through with `null` preserved (allow
     `EnhancedCloudLayer[] | null` instead of forcing `[]`).
   - Gate `nwp-cloud-bands` on `data.points.some((p) => p.nwpCloudLayers !== null)`.
   - `icing-ogimet-nwp-bands` continues to gate on
     `icingOgimetNwpZones.length > 0` — empty zones now correctly mean
     "no native NWP, can't compute" rather than "synth produced nothing."
   - `nwp-convective-bg` should gate on `convective_nwp !== null` (currently
     checks `nwpConvectiveBaseFt !== null`, which treats "computed, no
     convection" as missing). Pass through the raw `convective_nwp`
     presence as a boolean if the data adapter doesn't already.

### Tests

8. Update `tests/test_*` for:
   - `build_nwp_cloud_layers` returning `None` instead of synthesized
     layers when no GRIB / no `cc`. Existing tests that assert synth
     behavior need to either move to a different signal or be deleted.
   - Ogimet-NWP / IENG icing returning `[]` for non-GRIB inputs.
   - ECMWF diagnostic builder producing non-empty `top_ft` for the
     low band when `ceiling_m` is present.

### Frontend tests

9. `tests/playwright/*` — verify the cross-section toggle for "NWP
   Layers" is enabled for GRIB-enriched models even when `[]`, and
   disabled for non-GRIB models. (Existing flight fixtures may need
   updating once the data shape changes.)

## Out of scope

- DD layer suppression when model bulk % = 0% — discussed but
  rejected for this PR (separate ticket; needs threshold design).
- Migrating `icing_ogimet_nwp_zones` field type from `list` to
  `Optional[list]` — not needed once `nwp_cloud_layers` is `None`-aware,
  because the icing functions short-circuit upstream.

## Verification

- Re-run the briefing for the test flight
  `egtf_gwc_sfd_..._lsgs-2026-05-01-d281` and confirm:
  - ECMWF `nwp_cloud_layers` is `[]` at most points (clear sky) and
    populated where the model has clouds (currently only LSGS at 13.5%).
  - ICON `nwp_cloud_layers` is `null` everywhere (no GRIB).
  - GFS `nwp_cloud_layers` populated with `source="grib"` (unchanged).
  - Cross-section toggle for "NWP Layers" is enabled for ECMWF + GFS,
    disabled for ICON.
  - No phantom Ogimet-NWP icing zones on ICON.
- Compare digest output against current production for a sample
  flight to ensure no regression in advisories.

## Branch + PR

- Branch: `nwp-cloud-layers-refactor`
- Worktree: `/Users/brice/Developer/public/flyfun-weather/nwp-cloud-layers-refactor`
- PR title: `refactor(clouds): nwp_cloud_layers strictly native (drop synth fallback)`
- Reviewer focus: the Ogimet-NWP / IENG behavior change for non-GRIB
  models, and the ECMWF diagnostic builder fix.
