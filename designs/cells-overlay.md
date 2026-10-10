# Cell overlay — droplet ingest, endpoints and the web maps (#656)

> The home node analyses radar cells (`observed-cells.md`) and pushes one
> small **display file** per radar frame.  This doc is everything after the
> push: the droplet's inbox → store → API, and the shared Leaflet overlay
> drawn on the forecast map's "Now" tab and the briefing route map.
> Experimental: thresholds are provisional (`CellPolicy`); the overlay
> directs attention, it never gives a verdict.

## Flow

```
home node  cells/display/<stamp>.json.gz ──rsync──▶ CELLS_INBOX_DIR (HOST_CELLS_INBOX)
droplet    run_cells_ingest_loop (5 s): validate → DATA_DIR/observed/cells/display/  (24 h)
API        GET /api/observed/cells/frames            frames newest first (+ key of newest revision) + stale state
           GET /api/observed/cells/{key}.json[?south&west&north&east]   key = <stamp>.r<n>
web        visualization/cells-overlay.ts            one renderer, two maps (Now tab, route map)
```

Code: `observed/cells_display.py` (validate, `ingest`, `DisplayStore`,
`frames_status`, `filter_bbox`), `scheduler.run_cells_ingest_loop`,
`api/observed.py` (cells section).

## Choices, and why

**Inbox and store are two directories.**  Each has one writer: the node's
ssh user writes the inbox, the app user (uid 2000) owns the store and is the
only one that deletes from it ("one owner per root").  The inbox is mounted
read-write because the app *moves* files out (that is how it stays empty —
nothing else rotates it); the host dir is `dockerapp:dockerapp` (2000),
mode 2770, exactly like the snapshot inbox — the ssh user (`brice`) is in
group `dockerapp`, not in `weatherdata` (2002), so a 2002 inbox refuses the
push.  Prod: `/mnt/flyfun_data/weather/cells_inbox`.  The issue's later comment fixed the store path
(`DATA_DIR/observed/cells/display`, same relative path as the node's) — the
inbox is the issue body's `HOST_SNAPSHOT_INBOX` pattern.

**Validated at ingest and again at read.**  Ingest checks gzip+JSON, ≤ 5 MB
compressed / 40 MB raw, `schema == observed-cells-display/1`,
`policy_version` shaped `<name>+<hex digest>`, `valid_time` equal to the
filename's stamp, `cells` list / `outlines` dict.  Bad files go to
`inbox/rejected/` (logged once, kept for a look); rsync's `.<name>.XXXX`
temp files are never touched.  Read re-validates (cached by path+mtime+size,
8 entries) so a hand-copied file cannot reach a browser unchecked.
Any `policy_version` is accepted — the overlay shows what the node ran.
**A revision is written once**: an identical re-push is dropped, different bytes
for a stored revision go to `rejected/` (each revision's response is
`immutable`, so the first copy must stay the only one). `rejected/` is pruned
after 24 h.

**Revisions (#666).**  The node publishes a frame before its lightning lands
and re-issues it as `<stamp>.r1.json.gz` when it does (`observed-cells.md`,
"Latency").  Chosen over a separate lightning file (one more fetch and overlay
rule on both clients) and over a short-lived per-stamp response (loses the
caching guarantee).  The store keeps every revision (r0 is the pre-#666 name,
`<stamp>.json.gz`; the file must say `revision` = its name's, absent = 0); the
listing has one entry per frame with `key` = `<stamp>.r<n>` of the newest,
`revision`, `received_at` of that revision and `first_received_at` of r0.
`GET /cells/<stamp>.r<n>.json` serves exactly that revision, `immutable`
(410 if not stored).  A **bare** `GET /cells/<stamp>.json` — clients from
before #666 — serves the newest revision with `no-cache`.  Clients key their
display caches by URL and take the URL from the listing's `key`, so once the
listing shows r1 no client can serve r0 for that stamp; an older client's
in-memory cache can hold r0 until its listing refresh, by design.
**Deploy the droplet before the node**: a pre-#666 droplet leaves
`.r1` files in the inbox untouched (not stamp-named) — harmless, but they
pile up there until it is upgraded.

**Retention 24 h, by valid time** (agreed on the issue, 2026-10-04):
~150 KB × 288 ≈ 43 MB.  Longer than radar's 3 h, so a future loop over past
overlays is not cut short.  `received_at` is the store file's mtime (the
ingest time). The inbox file's mtime, which `rsync -t` carries over from the
node, is when the node wrote the frame: ingest keeps it in memory as
`computed_at(stamp, revision)` for the live tick's latency row (#751, see
live-observation-layer.md). Not stored in the display file, which a replay
must reproduce byte for byte.

**Stale after 25 min of the overlay's own age** (`STALE_AFTER`) — a
departure from the issue's "~15 min".  Set when the node published ~5–15 min
after valid time; since #666 it targets ~4.5 min, so a healthy feed is ~5–10
min old.  Kept at 25 (not tightened in #666) so a provider hiccup of a frame
or two does not blank the overlay; a stopped loop reads unavailable ~15–20
min after its last push.  One constant; the listing ships it
(`stale_after_minutes`) so clients do not hard-code it.

**Ingest every 5 s** (#666; was 30 s, up to 30 s of a ~4.5-min budget): the
inbox scan is a listing of a handful of names.  The purges, which list the
whole 24-h store, run once a minute.

**Disabled is an answer, not a 404.**  `GET /cells/frames` returns
`{"enabled": false, …}` without `WB_CELLS_INGEST_ENABLED`, so the Cells
toggle can say "not available on this server".  The per-stamp file is 404
when disabled, 410 when purged/absent/invalid.  The cells gate is independent
of `WB_OBSERVED_ENABLED`.

**bbox filter keeps whole shapes.**  A cell is kept when its centroid is in
the box; an outline when its own extent overlaps the box — a rain band
crossing the route is drawn whole, not cut at the box edge.  Per-stamp
responses are `immutable` (like the #652 tiles), the bbox is in the URL.
The box is capped at 80° (the flash ceiling), not imagery's 25°, so a long
route's corridor never loses its cells.

**Time alignment (current-conditions invariant #4).**  Radar tiles and the
overlay are separate frames.  The client draws the overlay whose stamp equals
the drawn radar frame's stamp, else the newest one *at or before* it, and the
badge gets its own line with the overlay's own time.  Never a shared "as of".

## Web: one renderer, two maps

- `visualization/cells-overlay-core.ts` (pure, unit-tested): types, trend
  colours (developing `#d7263d`, decaying `#1b6ca8`, steady `#7a7a7a`, mixed
  `#f18f01`, new white — evolution, not safety; unknown states grey),
  outline styles (core35 white, core41 black, rain20 grey), `matchCellFrame`
  (the stamp rule above + the stale threshold from the listing),
  `cellsBadge`, popup / legend wording, `frameKey` (the URL key: newest
  revision, else the bare stamp on an older server); the popup's
  rain rate reads "as of HH:MMZ" from the cell's `rate_as_of`.  The badge
  never says "no X": `pending` inputs read "lightning pending" (#666 publishes
  before lightning lands), `unavailable` ones "X unavailable"; a cell with
  `flashes_pending` reads "lightning pending", not "–".  Withheld motion reads
  "split/merge this frame", unsupported "too little of the cell in matched
  tiles"; `truncated` reads "partly outside radar coverage".
- `visualization/cells-overlay.ts`: `CellsLayer` (fetch listing with a 60 s
  TTL, fetch the display file — cached by URL, immutable — and draw into its
  own pane `cellsPane`, z 450, above tiles and the route).  Arrows (magenta,
  30 min) only for `motion.status == "available"` and only for cores.
  Redraws only when the URL or the rain toggle changes, so the route map's
  altitude-drag re-renders keep an open popup.
- **"Now" tab** (`forecast-page.md`): the whole of Europe, no bbox — the
  server passes the stored gzip through (`Content-Encoding: gzip`, ~150 KB
  instead of ~1 MB raw JSON).  Cells on by default there.
- **Route map**: a "Cells" checkbox next to Satellite, offered whenever the
  observed layers are (the briefing carries observed conditions).  **Off by
  default** (`vizSettings.observedCells`) — experimental on a safety
  product.  Requests the corridor box widened by 50 NM
  (`CELLS_MARGIN_NM`); pairs with the radar frame on screen only when the
  drawn layer is reflectivity, else takes the newest.  Its line is appended
  to the observed badge; a collapsible legend sits top-right.  Re-checked on
  the existing 2-min visible-tab tick so a feed going stale is noticed
  without a re-render.
- Route geometry (off-track distance, abeam point, closest approach vs ETA)
  is computed server-side in the live tick since #688 (`LiveLayer.storms`);
  the maps do not use it yet (#690). Not done: lightning flashes on iOS, a time
  slider over past overlays.

## iOS (#661)

Port of the same rules: `CellsOverlay` (Views/Map/CellsOverlay.swift — match,
badge, words, colours; tested in `CellsOverlayTests`), DTOs in
`Models/API/ObservedCells.swift`, fetching in `RouteCellsModel` (60 s listing
TTL, 6-entry display cache by path, token-guarded so an overtaken refresh is
dropped). Two instances per briefing: the route map's own (Cells toggle, off
by default, pairs with the drawn reflectivity frame) and the Observed tab's
(`BriefingViewModel.cellsModel`, newest current overlay — no radar drawn
there). Kept apart on purpose: one model polled with two stamps could flip the
map to a frame that is not the radar under it. The box is the widest sampled
corridor + 50 NM — not the cross-section's corridor pick — so both ask for the
same box. `CellDisplay` decodes leniently: envelope fields optional, `cells`
element by element (a malformed cell is dropped, not the overlay). The list
names a cell's position by the nearest route waypoint ("20 NM NE of LFPN"), a
label only: off-track/abeam geometry stays the planned server-side step.
`requestDataURL`, not `requestData`, so the bbox query survives.
Paths use `CellFrame.displayKey` (#666, the newest revision; the bare stamp
on an older server), so the 6-entry path cache never holds a superseded
revision.  Same wording as the web: "lightning pending" / "X unavailable" in
the badge, `lightningText` / `rateAsOfText` in the callout, "lightning
pending" in the Observed tab list.

## Suspect echoes (#696)

The node marks cores that do not look like weather (`observed-cells.md`,
meteorology-decisions §42) and the display file carries the `clutter` block on
them. The droplet does two things with it:

- **Serves it unchanged.** `/api/observed/cells/{stamp}` is the raw file; the
  validator is key-agnostic so the additive field needs no schema bump, and the
  overlay is free to draw or dim such a cell. The web and iOS renderers do not
  read it yet.
- **Optionally keeps it out of the route products** —
  `cells_display.clutter_suppress_enabled()` / `WB_CELLS_CLUTTER_SUPPRESS`,
  **off by default**, consumed by `storms.operational_cells`
  (`live-observation-layer.md`), and by `route_bands`, which also skips the
  `rain20` rings listed in `suspect_outlines` (#702; `cells_display.suspect_outlines`
  reads them, `validate` refuses an index that points nowhere, `filter_bbox`
  re-maps them). Suppression is not a clear-sky claim.

## Gotchas

- The ingest loop only starts when the flag is set at boot; a missing inbox
  dir is logged once and waited for (dev servers without a mount).
- The droplet never re-derives anything from a display file: arrows, trends
  and motion status are the node's.  A wrong arrow is a node bug.
- No migration: file store only.
