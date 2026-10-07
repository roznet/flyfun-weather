# Observed cells — detection, motion and self-scoring on a home compute node

> Issue #650, slice 1 of the direction in
> `designs/future/observed-motion-brainstorm.md` §10: **the home node owns the
> field, the droplet owns the route.**  #650 is the loop and the analysis;
> #656 adds a per-frame **display file** pushed to the droplet and drawn on
> the web maps (see "Display file and push" below, and `current-conditions.md`
> for the droplet side).  #688 is the route-geometry slice: the droplet turns
> the display file into storms against the route and the live layer's storm
> rows (`live-observation-layer.md` "Radar storms", meteorology-decisions §41).
> #696 adds **clutter evidence** per core (see "Non-meteorological echoes"
> below, and meteorology-decisions §42).
> Next: the Observed tab (#690), iOS.

## What it is

A long-running loop (`python -m weatherbrief.observed.cells run`) that
archives every OPERA radar, rain-rate and MTG lightning frame (CTTH opt-in) and
writes one **cell catalogue** per radar frame: every rain area and convective
core in Europe with its peak, extent, rain rate, lightning, cloud top, motion,
identity across frames and 30-minute lifecycle trend.  It scores its own
motion against what the radar shows 30 and 60 minutes later.

It runs on the MacBook while the analysis is refined and on the Mac mini as
the live loop.  **Never on the droplet** (memory: ~2.2 GB peak at full grid).

## Layout

```
observed/cells/
  policy.py      every tunable number, versioned (CellPolicy.policy_version)
  detect.py      connected components per tier; footprints as run-length blocks
  motion.py      masked NCC per tile → flow field; cell velocity from its tiles
  velocity.py    smoothed + centroid-track velocity over the lineage history (#662)
  advect.py      motion field from the tiles; straight vs field trajectories (#662)
  lineage.py     identity across frames (advect, overlap, dominant link); trend
  attributes.py  rain rate, lightning, parallax-corrected cloud top per cell
  clutter.py     is this echo weather? isolation + corroborators per core (#696)
  clutter_eval.py the report over archived catalogues (`clutter` CLI, #696)
  catalogue.py   wire format: deterministic gzipped JSON, one per DBZH frame
  display.py     map file per frame (#656): outlines + reduced cells, pushed
  push.py        rsync of new display files to WB_CELLS_PUSH_TARGET (#656)
  webmap.py      `map` CLI: standalone Leaflet review page (the visual reference)
  scoring.py     5 motion variants vs persistence at 30/60 min → cells/scores/<day>.jsonl
  runner.py      archive store, collect + analyse tick, replay, coverage report
  render.py      review PNG (radar, outlines, 30-min arrows, flashes, age/trend)
  archive.py     nightly pack / verify / prune / nas-plan / restore (#658)
  __main__.py    run [--once] | replay | render | status | map | scores | clutter | archive …
```

## One tree everywhere

The same relative paths on every machine; only the root differs, because each
root has exactly one owner and only the owner purges it:

```
<root>/
  opera_dbzh/<stamp>.h5 (+ .json)      raw frames, one directory per source
  opera_rate/…  eumetsat_li/…  [eumetsat_ctth/…]
  cells/
    display/<stamp>.json.gz            map file — built on the home node, pushed (#656)
    catalogues/<day>/<stamp>.json.gz   full analysis (+ <stamp>.failed.json markers)
    scores/<day>.jsonl  runs/<day>.jsonl  state.json
    archive/verified/<day>.json        written by `archive verify` only; gates prune
    archive/staging/…                  tonight's tars, NAS layout; cleared once verified
```

| Machine | Root | Owner, retention |
|---|---|---|
| Droplet | `DATA_DIR/observed` | web app collector: frames 3 h; `cells/display` 24 h (#656) |
| Mac mini | `~/flyfun-data/observed-archive` | cells loop writes; nightly archive job prunes: frames 48 h, analysis 90 days |
| MacBook (dev) | `main/data/observed-archive` | cells loop when testing; the dev server keeps `data/observed` |
| NAS | `/volume1/backup/flyfun/weather/observed-archive` | archive job: frame tars 12 months then event/pinned days only; analysis forever |

`WB_CELLS_ROOT` names the home-node root (required, no default — fails
loudly). It must never be `DATA_DIR/observed`: that store belongs to the web
app's collector, which purges at 3 h — on the MacBook the dev server runs it.
Frame names (`<YYYYMMDD>T<HHMM>`, EUMETSAT products snapped to their slot) are
identical on both sides and byte-identical files (checked 2026-10-04); they are
the shared key that links the analysis to the droplet's own frames
(`test_frame_names_are_the_shared_key`).

`runs/<day>.jsonl` holds one row per processed frame (`analysis_seconds` up to
the display file, `seconds` including push and scoring, `pushed_at`, peak RSS,
bytes, cell counts, what was unavailable) plus `amend` (lightning re-attached,
with its revision and `pushed_at`), `push` (end-of-tick batch), `error` and
`gap` rows.  Frame → push latency is `pushed_at − valid_time`, no log parsing.

## Choices, and why

**Reuse the briefing collector, unforked.**  `collect_once` gained
`lookback` / `max_fetch` passthroughs and `FrameStore` a `retain_all` flag
(`purge` becomes a no-op and never lists the archive directory).  Existing
callers are unchanged.  The root is separate from `DATA_DIR/observed` so a dev
server's collector and this loop never purge or race each other.

**Separate source variable.**  `WB_CELLS_SOURCES` (default
`opera_dbzh,opera_rate,eumetsat_li`), not `WB_OBSERVED_SOURCES`: a worktree's
`.env` is shared with the dev server, whose collector defaults to every source
including CTTH (~7–8 GB/day).  CTTH is opt-in until the EUMETSAT quota with
several consumers is checked.

**The DBZH composite is a 1 km grid (3800 × 4400), RATE 2 km (1900 × 2200).**
Older docs said 2 km for both.  Every distance in the policy is in km and
converted with the grid's own pixel size; RATE maps onto DBZH through the two
affines (same projection, no reprojection).  Analysis is at native 1 km —
small cores matter, and memory is cheap on the home nodes.

**Three tiers, detected and tracked independently:** `rain20` (≥20 dBZ, rain
areas and frontal bands), `core35` and `core41` (convective cores; both
recorded so the archive settles the threshold — 41 matches the live layer's
`RADAR_SIGNIFICANT_DBZ`).  8-connectivity.  A cell touching nodata or the grid
edge is `truncated`.

**Field motion, not object matching.**  128 km tiles, 64 km stride, masked
normalised cross-correlation (Padfield 2012) via `scipy.signal.fftconvolve`:
nodata pixels never take part under any shift, so a coverage edge cannot pull
the estimate.  Gates per tile: coverage ≥ 50 %, echo ≥ 1 % (low on purpose — an
isolated cell is the case that matters), NCC ≥ 0.5, forward/reverse agreement
≤ 1.5 px (the reverse tile is clamped into the grid so edge tiles moving
outward are still checked).  Pair spacing 10 min preferred (DBZH is a rolling
10-minute maximum, so 5-minute pairs share half their window and barely
move), then 15, then 5.  A cell's velocity is the pixel-weighted mean of its
matched tiles; under 50 % support it is `unsupported`, never guessed.

**Lineage: advect, overlap, dominant link.**  Last frame's cells are shifted by
their own velocity before overlapping (a 40 kt cell moves ~6 km in 5 min,
more than many cores).  Link at ≥ 20 % of the smaller area.  A cell inherits
an id only when each side is the other's largest overlap — so the largest
child of a split and the cell that absorbed a merge keep the storm's id and
history, and every other child starts fresh with `parents`.  **Velocity is
withheld on the frame of a split or merge** (`motion.status = "withheld"`).
Ids are `<tier>-<birth stamp>-<label>`: deterministic, so replays agree.

**Trend states: `developing` / `steady` / `decaying` / `mixed` / `new`.**
From the cell's own history over ~30 min (≥ 15 min required): peak ±5 dB and
area ×1.5 / ×0.6.  Opposite signals are `mixed`, never averaged into
`steady` (the first real run had a 59 dBZ core whose area grew ×1.8 while its
peak fell 5.5 dB).  "steady" rather than the issue's "mature" — the
measurement is a trend, not a lifecycle stage.  Provisional thresholds.

**Attributes at their own frame time, never advected.**  RATE = the newest
frame on disk at or before the DBZH time floored to 15 min, up to 30 min older
than the radar (`RATE_MAX_AGE`, #666; on 5-min stamps exactly 30 is the worst
case taken, refreshed to the frame's own slot at the lightning amend) — its time is `inputs.opera_rate` and
the gap `inputs.rate_age_min` (also on the display file); LI slot floored to
10 min; flashes go to the nearest cell pixel within 5 km (the two frames are up
to 10 min apart).  CTTH tops by
corrected position (`lat + delta_latitude`), exactly as the corridor sampler
does.  Every attribute is `None` when its source could not answer, and the
catalogue's `unavailable` list says why.  LI flash positions are taken as
published; whether they carry their own parallax is unverified.

**Never wait for attributes (#666).**  A radar frame is analysed and
published as soon as its DBZH is on disk.  Rain rate is the newest RATE on
disk (above).  Lightning is published as missing and **amended** when the
frame's LI slot lands — see "Latency" below.  Frames are still analysed
oldest first, so a later frame never starts its lineage without its
predecessor.

**Radar first, EUMETSAT after.**  Each tick collects OPERA, analyses (pushing
each live frame as soon as its display file exists), then collects EUMETSAT
(≤ 12 products per sweep; ~20 s per LI product, a CTTH granule ~54 MB),
analyses again if it brought anything, and amends lightning, so EUMETSAT
downloads never hold the newest radar frame back.  Between ticks a **tight
radar poll** checks for the due DBZH every 10 s from +4.0 min after its slot
(`OPERA_DELIVERY_LAG`) until it lands or 2 min pass, then the next tick runs at
once (`wait_for_next_tick`).  Sweeps
(every 15 min) reach back `WB_CELLS_CATCHUP_HOURS` (default 6; the OPERA open
cache keeps 24 h).

**Byte-for-byte catalogues.**  Sorted keys, rounded floats, no wall clock
(timings go to `cells/runs/`), gzip `mtime=0`.  `replay` of the same frames under
the same policy reproduces every file exactly (pinned by a test), into a
separate root it refuses to share with the live one.  Since #666 a *live*
catalogue can differ from its replay where the live loop used an older RATE
frame than replay finds on disk (`inputs.opera_rate` says which); an amended
catalogue otherwise equals the replay's.

**`policy_version` = name + digest of every number.**  It does *not* see code.
A change that alters output without touching a number must bump
`CellPolicy.name` (`cells-1` → `cells-2`), or old and new catalogues will
claim the same version.  Each catalogue also carries `code_revision` (the
checkout's git SHA) so such a slip stays traceable; it is informational —
lineage and scoring match on `policy_version` only, or every deploy would
restart every storm's history.

**A failed frame is retried once, then given up on.**  An exception or an
unreadable DBZH file writes `cells/catalogues/<day>/<stamp>.failed.json` (error,
attempt count) and one `error` row.  The loop retries it once after 30 min
(`FAILURE_RETRY`) so a transient failure — memory, a disk blip — does not cost
the frame; a second failure is final.  Each sweep logs how many frames in the
lookback are marked; `status` lists them; `retry-failed` clears the markers
(or `replay`, which ignores them and survives a failing frame).  A corrupt
catalogue is read as missing (logged), so one bad file cannot fail the
frames that use it as their lineage predecessor.  A
scoring error after the catalogue is written is *not* a failed frame — it is
recorded as `scoring_error` on the run row.  The next frame
then has no predecessor, and its catalogue says so: `unavailable` carries a
`lineage` entry whenever cells are "born" for lack of a predecessor rather
than because the weather started.

**Sweep state.**  `last_tick` means "the loop was alive" (the downtime gap);
`last_sweep` only advances when the catch-up collected without failures; a
failed sweep is retried after 5 minutes (`SWEEP_RETRY`), never on every tick,
so one permanently broken file cannot hammer the providers.

**Self-scoring.**  For each tier and lead (30, 60 min): cells issued at T−L
with an available velocity, advected by v·L (and by each #662 variant), against the tier's cells at T, on
the footprint's own block grid, only where the radar covered the block and
within 100 km of an issued or persisted footprint.  Persistence (same cells,
unmoved) is scored identically — extrapolation is only worth showing where it
beats "it stays put".  Plus median centroid error for cells whose id survived.

## Velocity over the lineage and advection along the field (#662)

Five ways to project a cell forward (`policy.MOTION_VARIANTS`), all recorded
and **all scored on the same cells and the same verification area**:

| variant | what | where |
|---|---|---|
| `raw` | this frame's single-pair vector, straight line (pre-#662 behaviour) | `motion.raw` fields |
| `smoothed` | exp-weighted mean (τ 10 min) of the raw vectors in the last 20 min | `motion.smoothed` |
| `track` | least-squares line through the centroids in the last 20 min (TITAN/SCIT) | `motion.track` |
| `field` | semi-Lagrangian along the tile field, 5-min midpoint steps | rebuilt from `catalogue.flow` |
| `field_anchored` | `field(x) + (smoothed − field(cell))` along the path | idem |

- **History entries** grew to `[t, peak, area, flashes, drow, dcol, row, col,
  break]`; `break` = 1 on a split/merge frame.  4-field entries (pre-#662) read
  as "no vector, no centroid".  Lineage only links under one
  `policy_version`, so in practice old entries never meet new code.
- **Gates hold for every variant**: smoothed/track exist only when the raw
  vector is `available`; an unsupported/withheld frame adds no vector; a
  split/merge opens a fresh window.  Below 2 vectors (smoothed) / 3 centroids
  (track) they **fall back to raw** (`n` = 1) rather than going missing, so
  the scored set is identical; `cells_smoothed` / `cells_track` on each score
  row count the cells where the estimate really differed.
- **The field**: matched tile vectors → normalised convolution on the tile
  lattice (Gaussian σ = 1 lattice step, 64 km) → reaches at most 1 lattice
  step past a matched tile → bilinear between tile centres (missing corners
  renormalised away).  Unsupported → the cell's smoothed vector.  The raw tile
  vectors are stored per catalogue (`flow`, ~4000 rounded numbers, mostly
  null) so scoring and the display rebuild the field without the frames.
- **How (1) and (2) combine — not decided, both scored.**  `field` ignores
  the cell's own vector except as fallback; `field_anchored` keeps the cell's
  smoothed velocity at the cell and takes only the *spatial variation* from
  the field.  The issue proposed the second; the scores pick.  A frame whose
  issued catalogue had no flow field (`field_available: false`) is left out
  of both field variants' medians in `scores`.  Otherwise they would be
  silently scored as the smoothed straight line.
- **Footprints move per block**: displacement at each footprint block's centre
  (≤ 8 km), rounded, applied to its pixels — exactly the old whole-footprint
  integer shift for straight variants.  Scoring batches every cell of a tier
  into one integration (per-cell calls would be ~100k small numpy calls).
- **Verification area is now the union** over all variants + persistence
  (was raw + persistence), so `extrapolation` scores under `cells-2` are not
  directly comparable with the `cells-1` numbers below.
- **Nothing pilot-visible changed**: `CellPolicy.display_motion = "raw"`.  The
  map arrow, `render`, `map` and speed/heading follow it; set it to a variant
  only after `replay` + `scores --root <replay>` show it beating raw on CSI
  and centroid error (issue acceptance).  Lineage advection stays on the raw
  vector regardless.
- **Not measured yet**: the comparison on real frames.  Policy `cells-2`;
  bumping it restarts every storm's lineage once on deploy.

## Non-meteorological echoes (#696)

We detected reflectivity objects and used them as storms. `clutter.py` adds the
missing question — *is this echo weather?* — as **evidence recorded on every
core**, never as a deletion. The decision, the measured numbers and everything
rejected are in `designs/meteorology-decisions.md` §42; what matters here is
the shape.

**One feature carries it: isolation.** A genuine ≥ 41 dBZ core is physically
embedded in its own ≥ 20 dBZ rain; clutter is a bare block in air the radar
looked at and found empty. Over all 11,967 cores of the 17 reference frames,
`ring_rain` (the share of a 2 px outer ring at ≥ 20 dBZ) is ≤ 0.15 for 0.6–1.1 %
of cores and **none** of the 683 corroborated by lightning or a cloud top.
`onset_db`, `robust_drop` and Gabella `continuity` corroborate; `area_perimeter`
and the OPERA quality index are recorded at **zero weight** because measuring
them showed they point the wrong way or carry no signal.

**Four rules, and they are the design:**

1. *Isolation is necessary* — no suspicion without the rain-margin evidence,
   whatever the corroborators say.
2. *Missing evidence is never support* — an uncomputable feature scores zero and
   is named under `unknown`; a ring that is mostly `nodata` yields no isolation
   evidence, so such a cell can never be suspect.
3. *Physical corroboration vetoes* — observed lightning or a cloud top clears
   the cell outright; lightning merely `pending` (#666) is not an observation.
4. *Nothing is deleted* — the block rides on the cell and the droplet decides.

**Only tiers above the rain threshold are assessed** (`assessable`). For
`rain20` itself the ring is below 20 dBZ by construction — that is where the
area ends — so every rain area in Europe would read as isolated.

**The 2 km trap.** The French contribution arrives on a 2 km grid, duplicated
2 × 2 onto the 1 km composite, so every small French cell has hard 1 km edges by
construction and `/how/comment` says some French volumes are pysteps-advected
fills. Hence the ring is 2 px and the continuity window 5 × 5: both reach past
the duplication. A 1 px ring would call half of France clutter.

**Per-cell crops, not full-grid filters.** Cores cover a tiny share of a
3800 × 4400 grid; a whole-grid median or neighbour count would cost seconds a
frame against the ~3 s budget #666 bought. Measured cost of the whole
assessment on a MacBook over the reference frames (~700 assessed cores a
frame): **+0.58 s** median analysis (4.29 → 4.87 s) and **+94 MB** peak RSS
(2235 → 2329 MB), the latter the transient full-grid quality read.

**Size.** The catalogue carries every feature on every assessed core, so it
grows **+11 %** (median 197 → 219 KB gzipped): ~22 KB a frame, ~6 MB a day,
~2.3 GB a year added to the NAS's analysis tree (which keeps catalogues
forever, against ~11 GB/year before). The display file carries the block only
on flagged cells and so grows **+1.2 %** (194 → 196 KB), and the droplet keeps
24 h of it. Both ride inside the existing retention; nothing new is kept.

**Features are rounded before they are scored** (`FEATURE_DIGITS`), not on the
way to the wire. The lightning amend re-scores a published cell from the
features in its catalogue (`rescore`) — lightning is a veto, so a frame whose
flashes land late must lose its suspicion — and rounding first makes that
re-score *identical* to what a replay computes from the raw frame rather than
merely close. That is what keeps "an amended catalogue equals its replay byte
for byte" true.

**No rolling state.** Every feature is per-frame and pixel-keyed, so a replay
reproduces the evidence exactly and a cell id that restarts (the reference core
dropped below minimum area at 00:15Z and took a new id while the echo under it
never moved) cannot hide anything.

**Off switch:** `ClutterPolicy.enabled`. It is a policy number, so a run with
it off carries its own `policy_version` — which is right: those catalogues
answer a different question.

**The report:** `cells clutter --from … --to … [--jsonl rows.jsonl]` over
archived catalogues (`clutter_eval.py`). It prints the flag rate per tier and
`weather_lost` — flagged cells that carry lightning or a cloud top — which
**must be zero**, because those are vetoes: a non-zero count is a leak, not a
calibration result. There is deliberately no positive clutter label; nothing we
have can assert one.

## Display file and push (#656)

**What leaves the home node is only the display file** — no raw frames (the
droplet collects its own, byte-identical), no catalogues, no scores.
`display.build_display` runs in `process_frame` right after the catalogue,
from the same detections, and writes `cells/display/<stamp>.json.gz`
(`DISPLAY_SCHEMA = observed-cells-display/1`):

- `outlines` per tier: masks traced with contourpy into `[[lat, lon], …]`,
  3 decimals; decimated 4×/2× (rain20/cores) on a Europe-sized grid, 2×/1×
  on small ones (`outline_step`).  `webmap.py` imports the same tracer.
- `cells`: every core, plus rain20 areas ≥ `RAIN_MIN_AREA_KM2` (2000 km², the
  prototype's floor).  Fields: `id, tier, lat, lon, area_km2, peak_dbz,
  rate_peak_mm_h, flashes, top_fl, truncated, age_min, event, trend{…},
  motion{status, reason, speed_kt, toward_deg}, arrow` — `arrow` is the
  `[lat, lon]` 30 min ahead, **only** for `motion.status == "available"`,
  by `policy.display_motion` (top-level `motion_variant`, `raw` today; for
  another variant speed/heading are taken from the arrow).
  `webmap.py` reads this same reduced shape (`display_cell`), so the
  prototype and the web overlay cannot drift.
- `times{radar, rate, lightning, cloud_top}` (each input's own time),
  `rate_age_min`, `revision` (0, then 1 once lightning is amended — each its
  own file, `<stamp>.r1.json.gz`; #666), `pending` (`["lightning"]` while it
  is on its way — *not* under `unavailable`, which every client words as
  "X unavailable"; cells then carry `flashes_pending: true`, and
  `rate_as_of` when their rain rate is older than the radar), `unavailable`,
  `policy_version`,
  `code_revision`, `window_minutes`.  Fields are additive under
  `observed-cells-display/1`, so an older droplet still accepts r0.
- `clutter` (#696): `level` / `score` / `reasons` on a cell with evidence
  against it, absent on a clear one (a continent of clean cores pays nothing),
  plus `robust_peak_dbz` on every assessed cell. Additive under
  `observed-cells-display/1`; the droplet's validator is key-agnostic, so an
  older droplet still accepts the file.
- `within` (#688): the id of the cell of the next lower tier that contains
  this one (core41 → core35 → rain20), read off that tier's labels under the
  cell's pixels (`enclosing_cells`); absent when the lower-tier region was
  below its minimum area.  Additive, so no policy bump: catalogues and
  lineage are unchanged.
- **No lightning flashes**: the droplet draws its own LI from
  `/api/observed/flashes`; the per-cell `flashes` count is in the cells.

Deterministic like the catalogue (replay reproduces it byte for byte), and
written **0644** — `mkstemp`'s 0600 would travel through rsync to a different
user on the droplet.  A display failure is a `display_error` on the run row,
never a failed frame (the catalogue, the lineage input, is already written).
Issue estimate for all of Europe: ~150 KB/frame gzipped (not re-measured
in this PR — no real frames in the cloud session).

**Push** (`push.py`): `rsync -t --timeout=60 <files> $WB_CELLS_PUSH_TARGET/`.
A live frame (≤ 15 min old) is pushed the moment its display file is written,
before scoring and before the EUMETSAT downloads (#666); at the end of every
tick a batch push over the lookback is the retry path and takes catch-up
frames (one rsync, not one per frame).  Only each frame's **newest revision**
is a candidate; the sent set lives in `state.json` as display keys (`pushed`:
`<stamp>` or `<stamp>.r<n>`, pruned to the lookback by the batch call;
`last_push`, `push_error`).  A pre-#666 state file reads as "r0 sent".
**Off when unset** — the MacBook never pushes unless asked.  A failure (exit
code, 120 s timeout, rsync missing) is logged and retried next tick; it never
raises.  Explicit file lists, not the directory: the home node keeps every
display file and the droplet purges at 24 h, so a directory rsync would grow
with the archive and re-send purged files.  Plain flags only (macOS ships an
old rsync / openrsync).  SSH auth comes from the node's `~/.ssh/config`.

## Latency (#666)

Measured on the mini before #666 (2026-10-04, 89 frames): DBZH on S3 at
+4.2 min, in the loop at +4.9 (4-min lag + 60 s tick), analysis held for
RATE (+10.1) or LI to +6.1 median / +10.9 p90, 7–8 s compute, push after the
EUMETSAT downloads, droplet ingest every 30 s.  Target ~4.5 min, median and
p90.  What changed, and the budget each step now has:

| step | now | was |
|---|---|---|
| DBZH in hand | +4.2–4.4 (10-s poll from +4.0) | +4.9–5.0 (60-s tick) |
| wait for attributes | none | RATE/LI, up to 15 min |
| compute to display file | target ≤ 3 s (unmeasured on the mini) | 7–8 s |
| whole chain | **target** ~4.5 min, median and p90 (unmeasured) | 6.3 / 11.4 |
| push | right after the display file | after EUMETSAT (~20 s each) |
| droplet ingest | ≤ 5 s | ≤ 30 s |

**Rain rate is not waited for.**  The :00/:05 frames' RATE slot lands at
+10.1, after them; they take the previous one (15–20 min older than the
radar) and say so (`rate_age_min`, `times.rate`, per-cell `rate_as_of`).  An
older RATE is sampled where the cells are *now*, so for a moving cell it is a
mismatch, not just a stale value.  Hence the amend **refreshes it**: RATE
HH:00 (+10.1) lands just before LI HH:00 (+10.5), so r1 almost always carries
the frame's own RATE, at no extra revision (`_refresh_rate`).  Both clients
print "(as of HH:MMZ)" from `rate_as_of`.

**Pending, never "no lightning"** (#666 review).  r0 lists lightning under
`pending`, cells carry `flashes_pending`, and the clients say "lightning
pending" in the badge, popup and list.  The catalogue keeps the plain
`unavailable` entry (the amend keys off it).  Deterministic without a clock:
a frame past the 30-min amend window is past the 25-min stale cut-off on
every map, so a "pending" that never resolves is never drawn.  An unreadable
LI file writes a revision that says "lightning unavailable".

**Lightning: publish on radar, amend when LI lands** (`amend_lightning`, every
tick after the EUMETSAT collection).  For each frame of the last 30 min
(`LI_AMEND_WINDOW`, so a late LI backfill does not rewrite history) whose
catalogue says `lightning: no lightning frame for this slot` and whose LI
slot is now on disk: flash counts (and the rain rate, above) are recomputed on the frame's own
detections (decoded again *outside* the frame cache, so the next radar
frame's predecessor is not evicted), and the catalogue is rewritten with
`flashes`, the cell's own history entry, its `trend` (incl. `d_flashes`) and
`inputs.eumetsat_li`, with a **new display revision** `<stamp>.r1.json.gz`
written *first*: the catalogue still saying "pending" is what makes the next
tick retry, so a crash between the two re-issues r2 instead of losing r1.
An amended catalogue equals its replay byte for byte when the inputs match
(pinned by a test).

**Revisions, not rewrites.**  The droplet serves each display file as
immutable (#660 review), so an amend never changes bytes under an old name:
r0 stays, r1 is a new file, and the listing points at the newest (droplet
side: `cells-overlay.md`).  Bounded at `MAX_DISPLAY_REVISION` = 9; in practice
one (only lightning is amended).

**Lineage / trend consistency — the choice.**  The next frame is usually
analysed before the previous frame's lightning lands, so it copies a history
entry with `flashes: null`.  Successors' published files are **not** patched
(that would cascade a revision through every later frame).  Instead every
frame, when analysed *or amended*, fills `null` history flashes from the
catalogue of the entry's own frame, by (time, id) — a history only ever
holds its own id's earlier entries (`fill_history_flashes`, cached in
`FrameCache`).  So each frame's own amend gives it a complete history and a
real `d_flashes`; between r0 and r1 `d_flashes` is `null` (the trend already
tolerates that).  A no-op in replay.

**Not bumped: `CellPolicy.name` stays `cells-2`.**  Detection, motion and
lineage numbers are unchanged (compute changes are numerically equivalent,
below); the RATE choice is per catalogue in `inputs`.  Bumping would restart
every storm's lineage for no change in the cells.

**Compute.**  `masked_ncc` transforms each of its six inputs once and does
one inverse per product (12 real FFTs instead of `fftconvolve`'s 18; same
`next_fast_len` sizes and crop — bit-identical on a synthetic 2000² pair);
the tile loop runs on a thread pool (`WB_CELLS_FLOW_THREADS`, default 2 —
the only count measured faster; a bad value falls back with a warning),
deterministic because each tile writes its own slot.  Per-cell peaks come
from `np.maximum.at` over the labelled pixels already in hand, not
`ndimage.maximum` (which argsorts the grid).  Scoring runs after the push.
Measured only in a 4-vCPU cloud sandbox on a synthetic 2000 × 2000 pair:
flow 15.0 s → 10.2 s (1 thread) / 7.1 s (2) / **11.2 s (4: oversubscribed)**;
detection 3–4.5× faster.  The mini's numbers are the acceptance test.

## Portability (macOS arm64 now, Linux possible)

numpy / `scipy.ndimage` / `scipy.fft` only — no OpenCV, no source builds, no
process pools (macOS spawns, Linux forks); the flow tiles use a *thread*
pool (pocketfft releases the GIL).  `ru_maxrss` is bytes on macOS and
KiB on Linux (`runner.peak_rss_mb`).  Linear-algebra results may differ in the
last digits between Accelerate and OpenBLAS; the golden test
(`tests/observed/test_cells_golden.py`, `WB_CELLS_GOLDEN_DIR`) compares with
tolerances and exact structure.

## Running it

```
WB_CELLS_ROOT=$PWD/data/observed-archive caffeinate -i \
  ./venv/bin/python -m weatherbrief.observed.cells run
python -m weatherbrief.observed.cells status --hours 24
python -m weatherbrief.observed.cells render --time 2026-10-03T14:05 --out /tmp/c.png --bbox 43,-2,52,10 --scale 2
python -m weatherbrief.observed.cells replay --from 2026-10-03T08:00 --to 2026-10-03T14:00 --out /tmp/replay
python -m weatherbrief.observed.cells clutter --from 2026-10-07T00:00 --to 2026-10-07T01:00 --jsonl /tmp/clutter.jsonl
```

**A laptop sleeps.**  Without `caffeinate -i` (AC power) macOS idle-sleeps the
loop for 10–15 minutes at a time; the first dev run lost most of an hour that
way and it looked like slow downloads.

**Pushing to a dev server** (MacBook): `WB_CELLS_PUSH_TARGET=$PWD/data/cells_inbox`
on the loop, `WB_CELLS_INGEST_ENABLED=1` and `CELLS_INBOX_DIR=$PWD/data/cells_inbox`
on the dev server.  On the mini: `WB_CELLS_PUSH_TARGET=<user>@<droplet>:<HOST_CELLS_INBOX>/`.

**Dead-man:** `WB_CELLS_HEALTHCHECK_URL` (healthchecks.io) is pinged after a
tick that analysed at least one frame, at most every 5 minutes; its silence
covers no radar arriving, a wedged loop and a dead daemon alike.

**Moving to the mini** is configuration, not code: the same command under a
launchd **KeepAlive** daemon (not a calendar job — it must run continuously
and not shift with DST), `WB_CELLS_ROOT` on the mini's disk or the NAS,
EUMETSAT credentials in its environment, `caffeinate`/`pmset` as for the
forecast offload.  The plist belongs in the private config repo with the
other mini daemons.

## Archive and retention (#658)

Decided 2026-10-04.  **The loop never deletes and never talks to the NAS** — a
slow or offline NAS must not stall live analysis.  A nightly job (ops repo:
`digitalocean/tools/backup-observed-archive.sh`, dispatched by the mini's
`flyfun-backup.sh observed-archive`) calls `archive …`, all pure over a root
and a UTC day:

| Step | Command | What it guarantees |
|---|---|---|
| 1 | `archive pending` → `pack --day D` | only days ended ≥ 30 min ago (`PACK_SETTLE`); refuses a day already verified |
| 2 | (job) rsync staging → NAS | staging layout = NAS layout |
| 3 | (job) `sha256sum` on the NAS → `verify --day D --remote-sums F` | every tar must match; then writes `cells/archive/verified/D.json` (holds the manifest) |
| 4 | `prune --execute` | see below; dry run without `--execute` |
| 5 | `nas-plan --manifests … --keep-days …/keep-days.json [--present F]` | prints tar paths; the job deletes them over ssh |

NAS layout: `<source>/<YYYY>/<day>.tar` (plain tar — HDF5/netCDF are already
compressed), `cells/<YYYY>/<day>.tar.gz` (catalogues incl. `.failed.json`,
`scores`, `runs`, `display`), `manifest/<day>.json`, `keep-days.json`
(`{"20260827": "reason"}`, pinned by hand).  Members are relative to the root,
so `restore --day D --from <staging or NAS copy> --to <scratch>` rebuilds a tree
that `replay`/`render`/`map` read unchanged.  Tars are deterministic (sorted,
mtime 0, no owner): re-packing gives the same sha256.

**Manifest:** per tar files/bytes/sha256; every member with its size; frames
present vs expected per source with gap ranges (expected = `WB_CELLS_SOURCES`);
`policy_version` and `code_revision` counts; failed markers; the event block.

**Prune rules.**  Only verified days; never today or yesterday (UTC) whatever
the markers say; a file goes only if the verified manifest lists it **with the
same size** — a frame a late sweep filled in after packing, or a jsonl that
grew, is reported as `kept (not in the verified archive)` and stays (it will
never be archived: a re-pack of a verified day is refused because it would
overwrite the full NAS tar with what is left).  Such files are **kept until
manual action**, with no retention of their own, so the nightly job should
alert when `kept_unarchived` is non-empty.  Manifest sizes are the sizes
written into the tar (fixed at `gettarinfo`), not a later `stat`, so a file
that grew mid-pack fails the size check instead of being pruned.  Frames go per stamp
(`< now − 48 h`); analysis per whole day once it ended 90 days ago.  The
verified day's staging copy is removed on the same pass.  `cells/display`
files (#656) are packed into the day's cells tar.gz (stamp prefix) and pruned
with the analysis at 90 days; `state.json` and markers stay.

**Event days** (`EventPolicy`, `events-1`, provisional): at some frame ≥ 3
`core41` cells with ≥ 10 flashes each, or any cell (any tier) ≥ 55 dBZ with
≥ 50 flashes.  `event: null` (unknown) when there are no catalogues or
lightning was available in < 50 % of frames — `nas-plan` keeps unknowns.  The
thresholds are stored in every manifest and `nas-plan` reads the stored
answer, so changing the policy never reclassifies archived days.  The issue
left N open; 3 was picked low on purpose (a wrong "event" costs ~0.9 GB, a
wrong "not an event" loses the day).

**`nas-plan` fails safe:** older than 365 days AND manifest readable AND
`event: false` AND not in `keep-days.json`.  Missing/corrupt manifest → kept;
missing or corrupt `keep-days.json` → the command fails (a dead mount must not
turn into deleting pinned days); paths are rebuilt from the day, so only
`<source>/<YYYY>/<day>.tar` can ever be printed, never a cells tar.

**Restore + replay is byte-for-byte only from a lineage break.**  A cell's id,
history and trend carry across midnight from the previous day's catalogues;
replaying one restored day starts fresh lineage at 00:00, so cells alive then
get new ids and the first ~30 min differ.  Restore the previous day as well
and replay from a real gap (the start of the archive, or a downtime) for an
exact match (`test_restore_then_replay_reproduces_the_days_catalogues` starts
at 00:00 with no prior day).

Sizes (mini, autumn 2026): DBZH ~650 MB/day, RATE ~130, LI ~80, catalogues
~30 → ~315 GB/year of frame tars, ~11 GB/year of analysis on the NAS.  Mini
steady state: ~2 days of frames (~1.7 GB) + 90 days of analysis (~3 GB).

## Open numbers (to settle from the archive)

Every `ClutterPolicy` threshold (#696): measured over one convective night and
no other regime, and whether the droplet's gate goes on at all.
Core threshold 35 vs 41; tile size and pair spacing; minimum cell areas;
lineage overlap fraction (20 % unmeasured, inherited from #600); trend
thresholds; flash buffer; smoothing window/τ, field σ and reach, and which
motion variant the map shows (#662).  Every one is in `policy.py`.

## Gotchas

- The hot root grows until the nightly archive job runs (see "Archive and
  retention"); if the NAS is down nothing is verified, so nothing is pruned and
  the mini fills at ~0.9 GB/day — the job's free-disk floor is the alarm.
- Display files (#656, ~150 KB/frame, estimate) ride in the day's cells
  tar.gz and are pruned with the analysis (90 d); the droplet keeps 24 h.
- The frame directories are flat (288 DBZH files a day).  Fine for `has()`
  lookups by name; anything that lists them (`FrameStore.list_frames`) will
  slow down as they grow — the loop never lists.
- First frame after a gap has no motion (`no_pair`) and starts fresh lineage.
- A frame filled in late by a sweep is analysed after its successor; its
  successor's lineage is not recomputed.  `replay` is the fix.
- `cells/display/` holds every revision (`<stamp>.json.gz`, `<stamp>.r1.json.gz`);
  the nightly pack globs by stamp prefix, so both travel together.
- A frame whose own LI landed in time but whose predecessor's did not (LI
  out of order) is never amended, so a `null` history entry can survive
  there; `d_flashes` is then `null`, never wrong.
- A suppressed core (#696) still leaves its own bare `rain20` outline: the
  outlines are traced from the tier masks and cannot be filtered per cell, so
  the ribbon can keep a floor-intensity rain band where the storm row went
  away. Known, not yet judged worth machinery.
- `clutter` evidence is only on `core35`/`core41`. Asking for it on `rain20`
  would flag every rain area in Europe (see "Non-meteorological echoes").
- The tight poll's probe is a `collect_opera` with `warn_if_empty=False`
  (one not-yet-published slot is a 404 by design).

## Numbers

Under `cells-1` (before #662). First real run, 2026-10-03 06:00–15:45Z, 71 frames, MacBook (Apple silicon),
`opera_dbzh,opera_rate,eumetsat_li`.  One day of autumn weather — a baseline
for the loop's cost, **not** a calibration of anything.

| | median | max |
|---|---:|---:|
| Seconds per frame (full 1 km grid) | 8.8 | 10.0 |
| Peak RSS | 2.16 GB | 2.16 GB |
| Catalogue (gzip) | 134 KB | 167 KB |
| Cells per frame: rain20 / core35 / core41 | 274 / 313 / 205 | |
| Cells with an available velocity | 635 | 847 |

Self-scoring, median over the run (CSI extrapolation vs persistence; median
centroid error of surviving ids, km):

| tier | lead | CSI extrap | CSI persist | centroid extrap | centroid persist |
|---|---:|---:|---:|---:|---:|
| rain20 | 30 | 0.31 | 0.21 | 4.7 | 14.1 |
| rain20 | 60 | 0.25 | 0.15 | 9.1 | 26.7 |
| core35 | 30 | 0.14 | 0.10 | 4.6 | 9.5 |
| core35 | 60 | 0.08 | 0.06 | 8.5 | 17.7 |
| core41 | 30 | 0.09 | 0.08 | 5.2 | 7.4 |
| core41 | 60 | 0.04 | 0.04 | 10.7 | 12.3 |

Early reading: extrapolation beats persistence everywhere on this day, by a
wide margin for rain areas and barely for 41 dBZ cores at 60 minutes — the
first hint of where a projection horizon (Tier 3) will sit.
