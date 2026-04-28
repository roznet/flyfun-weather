---
name: sync-ecmwf
description: Rsync the latest complete ECMWF GRIB run from the production server into the local ECMWF_GRIB_DIR so refresh can run with ECMWF enrichment locally
---

# Sync ECMWF GRIB run from production

Pull the latest complete ECMWF run from `weather.flyfun.aero` into the local `ECMWF_GRIB_DIR` so `refresh_briefing` can apply ECMWF GRIB enrichment in dev. One run is ~2 GB / ~230 files and takes ~4 min over a typical residential link.

## Inputs

- `--run YYYYMMDD_HHz` (optional) — pick a specific run instead of the latest complete one (e.g. `20260426_00z`).
- `--force` (optional) — re-rsync even if the local sentinel for the same run already exists.
- `--dry-run` (optional) — show what would transfer, do not write.

## Constants

- Prod host: `brice@161.35.35.15`
- Prod ECMWF dir: `/mnt/flyfun_data/ecmwf/data`
- Local destination: value of `ECMWF_GRIB_DIR` in `.env`. If not set / commented out, default to `/Users/brice/tmp/ecmwf/data` and remind the user to uncomment the line in `.env` so the backend picks it up.

## Steps

### 1. Resolve destination

```bash
DEST=$(grep -E '^ECMWF_GRIB_DIR=' .env | cut -d= -f2-)
if [ -z "$DEST" ]; then
  DEST=/Users/brice/tmp/ecmwf/data
  echo "note: ECMWF_GRIB_DIR is not set in .env — using default $DEST"
  echo "      uncomment 'ECMWF_GRIB_DIR=$DEST' in .env so the app picks it up"
fi
mkdir -p "$DEST"
```

### 2. Pick the run

If `--run` was provided, use it. Otherwise, list complete sentinels on prod and take the latest:

```bash
ssh brice@161.35.35.15 "cd /mnt/flyfun_data/ecmwf/data && ls -1 .ready_*z 2>/dev/null | sort | tail -1"
```

The sentinel filename is `.ready_YYYYMMDD_HHz` (no `.partial` suffix — partial runs are skipped). Strip the `.ready_` prefix to get the run tag (e.g. `20260426_00z`), then convert to the GRIB filename run timestamp:

- run tag `20260426_00z` → run timestamp `20260426T000000Z`
- run tag `20260425_18z` → run timestamp `20260425T180000Z`

Print the resolved sentinel content (`ssh ... "cat .ready_<tag>"`) so the user sees `files=N/M` and `base_time=`.

### 3. Skip if already synced

```bash
if [ -f "$DEST/.ready_<tag>" ] && [ "$FORCE" != "1" ]; then
  echo "Run <tag> already present locally — pass --force to re-rsync"
  exit 0
fi
```

### 4. Rsync the run

Anchor the include on `_fc_<RUN_TIMESTAMP>_` so we only get files **whose run init** matches — without this, the older runs whose *valid* time happens to match this hour leak in (verified during initial test: 26 spurious `scda_fc` files came over).

```bash
RUN_TS=20260426T000000Z   # convert from run tag
TAG=20260426_00z

rsync -av --stats ${DRY_RUN:+-n} \
  --include="brg_*_fc_${RUN_TS}_*" \
  --include=".ready_${TAG}" \
  --include="delivery_config.json" \
  --exclude='*' \
  brice@161.35.35.15:/mnt/flyfun_data/ecmwf/data/ \
  "$DEST/"
```

Notes on the rsync flags:
- macOS ships rsync 2.6.x — do **not** use `--info=progress2` (unsupported). Use `--stats` and let it print per-file lines.
- `delivery_config.json` is included so `ecmwf_watcher` can re-validate locally.
- The trailing `/` on both source and dest is required for include/exclude semantics.

### 5. Verify

```bash
ls -1 "$DEST/.ready_${TAG}" 2>/dev/null && cat "$DEST/.ready_${TAG}"
ls -1 "$DEST" | grep -c "_fc_${RUN_TS}_"   # should match files= count in sentinel × 2 (data + .idx)
du -sh "$DEST"
```

Report:
- Run tag synced
- File count vs sentinel `files=N/M`
- Total local size
- Time taken

### 6. Suggest next step

After a successful sync, suggest:

```
ECMWF_GRIB_DIR is now populated with run <tag>.
You can now refresh a briefing locally and it should pick up ECMWF enrichment.
For an existing pack, look in fetch_meta.json for an 'ECMWF GRIB enrichment applied' diagnostic line.
```

## Gotchas

- **Pattern anchoring**: `brg_*_<RUN_TS>_*` matches both the init time and the valid time positions. Always include `_fc_` before the run timestamp to anchor correctly.
- **`.partial` sentinels**: written when ECPDS delivery times out. The skill ignores them by default — partial runs should not be promoted to dev unless the user passes `--run` explicitly with a `.partial` tag.
- **Disk usage**: the skill does NOT clean old runs locally. Periodically `rm` older `_fc_<old_run>_*` and matching `.ready_*` files if `/Users/brice/tmp/ecmwf/data` is filling up.
- **Network**: ~9 MB/s observed in initial test — droplet uplink, not local — so 2 GB takes ~4 min. If it's noticeably slower, check `ssh brice@161.35.35.15 'iftop'` or just wait.
- **rsync exit 24**: "some files vanished" — happens when the watcher deletes a file mid-transfer. Not fatal for our purposes as long as the run files all came through; verify with the file-count check in step 5.
