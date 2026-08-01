#!/usr/bin/env bash
# Download golden-decode HRRR wrfprs samples (#457) into tests/data/hrrr_samples/.
#
# Byte-ranges the f01 file of a recent 00z HRRR run on the public NOAA S3
# bucket into two GRIB2 files (message order preserved):
#
#   hrrr_sounding_f01.grib2  TMP/DPT/RH/UGRD/VGRD at 925/850/700/500 mb
#                            + PRES:surface
#   hrrr_diag_f01.grib2      HGT:cloud ceiling, LCDC/MCDC/HCDC/TCDC,
#                            CAPE/CIN:180-0 mb above ground,
#                            REFC:entire atmosphere
#
# The samples are gitignored (large binaries); tests/test_hrrr_golden.py
# skips cleanly when they are absent. Re-run this script to refresh them.
#
# Requires: curl, awk, grep. Network access (the TESTS never hit network —
# they read only the local files this script produces).
#
# Env overrides:
#   HRRR_RUN_DATE=YYYYMMDD  pin a specific 00z run (default: most recent
#                           00z run whose f01 idx answers 200, up to 3 days back)

set -euo pipefail

S3_BASE="https://noaa-hrrr-bdp-pds.s3.amazonaws.com"
INIT_HOUR="00"
FHOUR="01"
OUT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/tests/data/hrrr_samples"

SOUNDING_RE='^(TMP|DPT|RH|UGRD|VGRD):(925|850|700|500) mb$|^PRES:surface$'
DIAG_RE='^HGT:cloud ceiling$|^LCDC:low cloud layer$|^MCDC:middle cloud layer$|^HCDC:high cloud layer$|^TCDC:entire atmosphere$|^(CAPE|CIN):180-0 mb above ground$|^REFC:entire atmosphere$'

mkdir -p "$OUT_DIR"
cd "$OUT_DIR"

# --- Pick the run: most recent 00z whose f01 idx exists ---------------------
if [ -n "${HRRR_RUN_DATE:-}" ]; then
    RUN_DATE="$HRRR_RUN_DATE"
else
    RUN_DATE=""
    for days_back in 0 1 2; do
        candidate="$(date -u -d "today - ${days_back} days" +%Y%m%d)"
        idx_url="${S3_BASE}/hrrr.${candidate}/conus/hrrr.t${INIT_HOUR}z.wrfprsf${FHOUR}.grib2.idx"
        if curl -sf --head --max-time 15 "$idx_url" > /dev/null; then
            RUN_DATE="$candidate"
            break
        fi
    done
    if [ -z "$RUN_DATE" ]; then
        echo "ERROR: no 00z HRRR run found in the last 3 days on $S3_BASE" >&2
        exit 1
    fi
fi

GRIB2_URL="${S3_BASE}/hrrr.${RUN_DATE}/conus/hrrr.t${INIT_HOUR}z.wrfprsf${FHOUR}.grib2"
IDX_URL="${GRIB2_URL}.idx"
IDX_FILE="hrrr.t${INIT_HOUR}z.wrfprsf${FHOUR}.grib2.idx"

echo "Run:  hrrr.${RUN_DATE} t${INIT_HOUR}z f${FHOUR}"
echo "Idx:  $IDX_URL"
curl -sf --retry 3 --max-time 60 "$IDX_URL" -o "$IDX_FILE"

# --- Byte ranges -------------------------------------------------------------
# idx line format: msg_num:byte_offset:d=...:VAR:LEVEL:fcst:
# A message spans [its offset, next message's offset - 1]; the last message
# in the file gets an open-ended range (none of ours is last, but stay safe).
ranges_for() {
    awk -F: -v re="$1" '
        { off[NR] = $2; key[NR] = $4 ":" $5 }
        END {
            for (i = 1; i <= NR; i++) {
                if (key[i] ~ re) {
                    if (i < NR) printf "%d-%d\n", off[i], off[i + 1] - 1
                    else printf "%d-\n", off[i]
                }
            }
        }
    ' "$IDX_FILE"
}

download_subset() {
    local name="$1" re="$2" out="$3"
    local ranges count=0
    ranges="$(ranges_for "$re")"
    if [ -z "$ranges" ]; then
        echo "ERROR: no idx lines matched $name pattern" >&2
        exit 1
    fi
    : > "$out"
    while IFS= read -r range; do
        [ -z "$range" ] && continue
        curl -sf --retry 3 --max-time 120 -r "$range" "$GRIB2_URL" >> "$out"
        count=$((count + 1))
    done <<< "$ranges"
    echo "$name: $count messages -> $out ($(stat -c %s "$out") bytes)"
}

download_subset "sounding" "$SOUNDING_RE" "hrrr_sounding_f${FHOUR}.grib2"
download_subset "diag" "$DIAG_RE" "hrrr_diag_f${FHOUR}.grib2"

cat > SOURCE.txt <<EOF
# HRRR golden-decode samples (#457) — fetched $(date -u +%Y-%m-%dT%H:%M:%SZ)
run=hrrr.${RUN_DATE} cycle=t${INIT_HOUR}z fhour=f${FHOUR}
grib2_url=${GRIB2_URL}
files=hrrr_sounding_f${FHOUR}.grib2 hrrr_diag_f${FHOUR}.grib2
EOF

echo "Done. Samples in $OUT_DIR (run ${RUN_DATE} t${INIT_HOUR}z f${FHOUR})."
