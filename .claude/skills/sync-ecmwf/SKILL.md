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

Nothing here is hardcoded, so the recipe works for a fork or a second deployment. Resolve
`<user>@<server>` and `<project-dir>` per `designs/references/deployment-paths.md`, then:

| Placeholder | Resolve from |
|---|---|
| `<server-ecmwf-dir>` | **`HOST_ECMWF_GRIB_DIR`** in the server's `.env` — `ssh <user>@<server> "grep '^HOST_ECMWF_GRIB_DIR=' <project-dir>/.env \| cut -d= -f2"` |
| `$DEST` | Local destination — `ECMWF_GRIB_DIR` in this checkout's `.env` (see step 1) |

> Mind the two variable names: the **server** side is `HOST_ECMWF_GRIB_DIR`, the **local** side
> is `ECMWF_GRIB_DIR`. This recipe touches both ends, so grepping the wrong one on the wrong
> host silently resolves to nothing (or to the local path) and the rsync source comes out empty.

## Steps

### 1. Resolve destination

```bash
DEST=$(grep -E '^ECMWF_GRIB_DIR=' .env | cut -d= -f2-)
if [ -z "$DEST" ]; then
  DEST=~/tmp/ecmwf/data
  echo "note: ECMWF_GRIB_DIR is not set in .env — using default $DEST"
  echo "      uncomment 'ECMWF_GRIB_DIR=$DEST' in .env so the app picks it up"
fi
mkdir -p "$DEST"
```

### 2. Pick the run

If `--run` was provided, use it. Otherwise, list complete sentinels on prod and take the latest:

```bash
ssh <user>@<server> "cd <server-ecmwf-dir> && ls -1 .ready_*z 2>/dev/null | sort | tail -1"
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
  --exclude='*.idx' \
  --include="brg_*_fc_${RUN_TS}_*" \
  --include=".ready_${TAG}" \
  --include="delivery_config.json" \
  --exclude='*' \
  <user>@<server>:<server-ecmwf-dir>/ \
  "$DEST/"
```

Notes on the rsync flags:
- macOS ships rsync 2.6.x — do **not** use `--info=progress2` (unsupported). Use `--stats` and let it print per-file lines.
- `delivery_config.json` is included so `ecmwf_watcher` can re-validate locally.
- The trailing `/` on both source and dest is required for include/exclude semantics.
- **`--exclude='*.idx'` must stay first** (rsync takes the first matching rule, and the `brg_*` include would otherwise pull the indexes too). cfgrib's `.idx` is a pickle that stores the **absolute path** it was built against, and cfgrib only accepts an index whose path matches the GRIB it is opening. Prod's indexes name the server's data path, so locally every one is rejected ("Ignoring index file … incompatible with GRIB file") — and since cfgrib writes indexes with an *exclusive* create, the stale file is never replaced. Result: every decode full-scans the GRIB forever. Measured on the airport-profile panel: **~30–40 s per request with prod indexes present, ~3 s without.** If a sync predating this exclude left indexes behind, delete them: `rm "$DEST"/*.idx`.

### 5. Verify

```bash
ls -1 "$DEST/.ready_${TAG}" 2>/dev/null && cat "$DEST/.ready_${TAG}"
ls -1 "$DEST" | grep -c "_fc_${RUN_TS}_"   # should match files= count in sentinel (no .idx — see above)
du -sh "$DEST"
```

Report:
- Run tag synced
- File count vs sentinel `files=N/M`
- Total local size
- Time taken

### 6. Offer to clean stale runs

After a successful sync, look for older runs in the local cache and offer (with explicit user confirmation) to delete them. Skip this step entirely if `--dry-run` was used.

Discovery — find all sentinels older than 24h, excluding the run we just synced. Note: `rsync -a` preserves the prod mtime, so a re-synced old run can still look stale on disk; the explicit `TAG` exclusion guards against deleting it.

```bash
JUST_SYNCED_TAG="<TAG>"   # e.g. 20260510_00z

# Sentinels older than 24h (mtime > 1 day), excluding the just-synced one and any .partial
OLD_SENTINELS=$(find "$DEST" -maxdepth 1 -name '.ready_*z' -mtime +1 -type f 2>/dev/null \
  | grep -v "/.ready_${JUST_SYNCED_TAG}$" || true)
```

If `OLD_SENTINELS` is empty, print `No stale runs (>24h) to clean.` and continue to step 7.

Otherwise, for each old sentinel, build a per-run summary:

```bash
for sentinel in $OLD_SENTINELS; do
  tag=$(basename "$sentinel" | sed 's/^\.ready_//')
  date_part=${tag%_*}
  hour_part=${tag#*_}; hour_part=${hour_part%z}
  RUN_TS="${date_part}T${hour_part}0000Z"

  age_h=$(( ($(date +%s) - $(stat -f %m "$sentinel")) / 3600 ))
  file_count=$(ls -1 "$DEST"/brg_*_fc_${RUN_TS}_* 2>/dev/null | wc -l | tr -d ' ')
  size=$(du -ch "$sentinel" "$DEST"/brg_*_fc_${RUN_TS}_* 2>/dev/null | tail -1 | cut -f1)

  echo "  $tag — ${age_h}h old, $file_count files, $size"
done
```

Present the list to the user and **pause for explicit confirmation** (do not delete anything yet). For example:

```
Stale ECMWF runs in ~/tmp/ecmwf/data:
  20260509_00z — 30h old, 232 files, 2.0G
  20260508_12z — 42h old, 232 files, 2.0G
Delete these (sentinels + matching brg_*_fc_<run>_* files)? [y/N]
```

Only on an explicit "yes" from the user, run the deletion per stale tag:

```bash
for sentinel in $OLD_SENTINELS; do
  tag=$(basename "$sentinel" | sed 's/^\.ready_//')
  date_part=${tag%_*}
  hour_part=${tag#*_}; hour_part=${hour_part%z}
  RUN_TS="${date_part}T${hour_part}0000Z"

  rm -f "$DEST"/brg_*_fc_${RUN_TS}_* "$sentinel"
  echo "Removed $tag"
done
du -sh "$DEST"
```

Notes:
- Only `.ready_*z` is in scope here — `.partial` sentinels and orphan grib files (no matching sentinel) are out of scope; surface them to the user but do not auto-delete.
- Never delete without confirmation, even if disk looks tight. The user can always re-sync from prod.

### 7. Suggest next step

After a successful sync, suggest:

```
ECMWF_GRIB_DIR is now populated with run <tag>.
You can now refresh a briefing locally and it should pick up ECMWF enrichment.
For an existing pack, look in fetch_meta.json for an 'ECMWF GRIB enrichment applied' diagnostic line.
```

## Gotchas

- **Pattern anchoring**: `brg_*_<RUN_TS>_*` matches both the init time and the valid time positions. Always include `_fc_` before the run timestamp to anchor correctly.
- **`.partial` sentinels**: written when ECPDS delivery times out. The skill ignores them by default — partial runs should not be promoted to dev unless the user passes `--run` explicitly with a `.partial` tag.
- **Disk usage**: step 6 offers to clean runs whose sentinel is >24h old, but only with explicit user confirmation and never the run just synced. Orphan grib files (no matching `.ready_*` sentinel) are reported but not auto-deleted — clean them by hand if needed.
- **Network**: ~9 MB/s observed in initial test — droplet uplink, not local — so 2 GB takes ~4 min. If it's noticeably slower, check `ssh <user>@<server> 'iftop'` or just wait.
- **rsync exit 24**: "some files vanished" — happens when the watcher deletes a file mid-transfer. Not fatal for our purposes as long as the run files all came through; verify with the file-count check in step 5.
