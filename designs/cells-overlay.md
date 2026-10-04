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
droplet    run_cells_ingest_loop (30 s): validate → DATA_DIR/observed/cells/display/  (24 h)
API        GET /api/observed/cells/frames            stamps newest first + stale state
           GET /api/observed/cells/{stamp}.json[?south&west&north&east]
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
nothing else rotates it); the host dir must be group `weatherdata` (2002),
mode 2775.  The issue's later comment fixed the store path
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

**Retention 24 h, by valid time** (agreed on the issue, 2026-10-04):
~150 KB × 288 ≈ 43 MB.  Longer than radar's 3 h, so a future loop over past
overlays is not cut short.  `received_at` is the store file's mtime (the
ingest time).

**Stale after 25 min of the overlay's own age** (`STALE_AFTER`) — a
departure from the issue's "~15 min".  The node publishes a frame ~5–10 min
after its valid time (up to ~15+ if lightning is late, `ATTRIBUTE_WAIT`), and
a new one every 5 min, so a healthy feed is routinely 10–15 min old: a
15-min threshold would flicker "unavailable" on a working feed.  With 25, a
stopped loop reads unavailable ~10–15 min after its last push, which is the
acceptance criterion.  One constant; the listing ships it
(`stale_after_minutes`) so clients do not hard-code it.

**Disabled is an answer, not a 404.**  `GET /cells/frames` returns
`{"enabled": false, …}` without `WB_CELLS_INGEST_ENABLED`, so the Cells
toggle can say "not available on this server".  The per-stamp file is 404
when disabled, 410 when purged/absent/invalid.  The cells gate is independent
of `WB_OBSERVED_ENABLED`.

**bbox filter keeps whole shapes.**  A cell is kept when its centroid is in
the box; an outline when its own extent overlaps the box — a rain band
crossing the route is drawn whole, not cut at the box edge.  Per-stamp
responses are `immutable` (like the #652 tiles), the bbox is in the URL.

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
  `cellsBadge`, popup / legend wording.  Withheld motion reads
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
- Not done: route geometry (off-track distance, abeam point, closest approach
  vs ETA), iOS, a time slider over past overlays.

## Gotchas

- The ingest loop only starts when the flag is set at boot; a missing inbox
  dir is logged once and waited for (dev servers without a mount).
- The droplet never re-derives anything from a display file: arrows, trends
  and motion status are the node's.  A wrong arrow is a node bug.
- No migration: file store only.
