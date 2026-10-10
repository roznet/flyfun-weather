---
name: sync-ecmwf
description: Rsync the latest complete ECMWF GRIB run from the production server into the local ECMWF_GRIB_DIR so refresh can run with ECMWF enrichment locally
---

# Sync ECMWF GRIB run from production

Pull the latest complete ECMWF run from the droplet into the local `ECMWF_GRIB_DIR` so
`refresh_briefing` can apply ECMWF GRIB enrichment in dev. One run is ~2 GB / ~230 files,
~4 min at the droplet's ~9 MB/s uplink.

## Run it

```bash
python3 scripts/ops/sync_ecmwf.py [--run YYYYMMDD_HHz] [--force] [--dry-run]
```

It resolves both ends itself — the server's `HOST_ECMWF_GRIB_DIR` via `scripts/ops/hosts.py`,
the local `ECMWF_GRIB_DIR` from this checkout's `.env` (default `~/tmp/ecmwf/data`, with a
note to set it) — then:
- picks `--run` or the newest **complete** sentinel `.ready_<tag>` (never `.partial`; pass a
  `.partial` tag with `--run` explicitly only if the user wants a partial run) and prints it
  (`files=N/M`, `base_time`);
- skips a run already synced (`--force` re-syncs);
- rsyncs only that run's files, the sentinel and `delivery_config.json`;
- checks the local file count against the sentinel's `files=N` — a mismatch is a `problem`;
- warns on leftover `*.idx` and lists stale runs.

Relay: run tag, file count vs sentinel, size, time. Exit 2 = the droplet couldn't be reached.

## Stale runs — delete only with the user's yes

The report lists every local run whose sentinel is older than 24 h (never the one just
synced), with age, files and size. Show the list and **ask**; never delete because disk looks
tight — the user can always re-sync. On an explicit yes:

```bash
python3 scripts/ops/sync_ecmwf.py --delete-stale <tag> [<tag> ...]
```

It removes the sentinel and that run's `brg_*_fc_<run>_*` files. Orphan GRIBs (no sentinel)
and `.partial` runs are reported but never deleted by the script — by hand if wanted.

## Next step

```
ECMWF_GRIB_DIR is now populated with run <tag>. Refresh a briefing locally; its
fetch_meta.json should show an 'ECMWF GRIB enrichment applied' diagnostic line.
```

## Gotchas (why the script does what it does)

- **Anchor on the run init.** GRIB names carry the init *and* the valid time, so
  `brg_*_<RUN_TS>_*` also matches older runs whose valid time is that hour (26 spurious
  `scda_fc` files in the first test). The filter is `brg_*_fc_<RUN_TS>_*`.
- **`*.idx` excluded first.** rsync takes the first matching rule, so the exclude must precede
  the `brg_*` include. cfgrib's `.idx` stores the absolute path it was built against; the
  server's are rejected locally ("Ignoring index file … incompatible"), and since cfgrib
  writes indexes with an exclusive create they are never replaced — every decode full-scans,
  ~30–40 s per request instead of ~3 s. If the report warns about `*.idx`, delete them.
- macOS rsync 2.6: no `--info=progress2`; the script uses `--stats`.
- rsync exit 24 ("some files vanished") happens when the watcher deletes a file mid-transfer;
  not fatal — the file-count check decides.
