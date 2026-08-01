#!/usr/bin/env bash
# Fetch the golden-decode HRRR sample for tests/test_hrrr_sample.py (#457).
#
# Those tests exercise the Lambert-grid decode against REAL data — the
# projection against the file's own 2-D lat/lon arrays, the CIMIXR-decodes-as-
# "unknown" mapping, grid-relative wind rotation, the diagnostics field map.
# None of that can be checked against a synthetic fixture: the whole point is
# that the product's actual grid definition and variable naming are the oracle.
#
# The sample is gitignored (~16 MB of GRIB2) and the tests skip cleanly when it
# is absent, so CI without it stays green. Before this script the sample had to
# be assembled by hand from a docstring, which meant in practice it existed on
# one machine. Re-run this to (re)create it. (Idea taken from PR #518.)
#
# It byte-ranges a single wrfprs forecast hour into ONE file, message order
# preserved, containing exactly what the tests read:
#
#   the sounding at 850/700/500 mb   TMP DPT RH UGRD VGRD VVEL HGT
#                                    CLMR CLWMR CIMIXR
#   the diagnostics set              LCDC/MCDC/HCDC/TCDC,
#                                    HGT:cloud ceiling|cloud base,
#                                    CAPE/CIN:90-0 mb above ground
#
# THREE pressure levels, not one, and that is load-bearing: with a single level
# cfgrib exposes `isobaricInhPa` as a scalar coordinate rather than a dimension,
# the 3-D branch of _decode_pressure_vars_from_datasets never runs, and the
# sounding decodes to {}. A one-level sample makes the golden tests fail in a
# way that says nothing about the decoder. Three levels also let the tests see
# a real vertical axis, as the 40-level production fetch does.
#
# Both spellings of cloud liquid water are requested because the live product
# publishes CLMR while NCO's inventory documents CLWMR — same defensive pairing
# as HRRR_SOUNDING_VARIABLES in hrrr_fetch.py. Whichever exists is fetched.
#
# Requires: curl, awk. Network access — the TESTS never hit the network, they
# read only the local file this script produces.
#
# Env overrides:
#   HRRR_RUN_DATE=YYYYMMDD   pin a run (default: most recent whose idx is up)
#   HRRR_INIT_HOUR=HH        default 12
#   HRRR_FHOUR=FF            default 06

set -euo pipefail

S3_BASE="https://noaa-hrrr-bdp-pds.s3.amazonaws.com"
INIT_HOUR="${HRRR_INIT_HOUR:-12}"
FHOUR="${HRRR_FHOUR:-06}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="$REPO_ROOT/tests/data/hrrr_samples"
# Fixed name: test_hrrr_sample.py globs the directory and takes the first
# match, so a stable filename keeps re-runs idempotent instead of piling up
# stale samples that would silently win the sort.
OUT_FILE="hrrr_sample.grib2"

# Matched against "VAR:LEVEL" from the idx line. Keep in step with
# HRRR_SOUNDING_VARIABLES / HRRR_CLOUD_DIAG_VARIABLES in hrrr_fetch.py.
WANTED_RE='^(TMP|DPT|RH|UGRD|VGRD|VVEL|HGT|CLMR|CLWMR|CIMIXR):(850|700|500) mb$|^(LCDC:low cloud layer|MCDC:middle cloud layer|HCDC:high cloud layer|TCDC:entire atmosphere|HGT:cloud ceiling|HGT:cloud base|CAPE:90-0 mb above ground|CIN:90-0 mb above ground)$'

# --- Portability -------------------------------------------------------------
# macOS ships BSD date (-v), Linux GNU date (-d). Probe rather than guess.
date_days_ago() {
    if date -u -v-0d +%Y%m%d >/dev/null 2>&1; then
        date -u -v-"$1"d +%Y%m%d
    else
        date -u -d "$1 days ago" +%Y%m%d
    fi
}
# `wc -c` instead of stat(1), whose size flag differs (-c %s vs -f %z).
file_size() { wc -c < "$1" | tr -d ' '; }

# --- Pick the run ------------------------------------------------------------
if [ -n "${HRRR_RUN_DATE:-}" ]; then
    RUN_DATE="$HRRR_RUN_DATE"
else
    RUN_DATE=""
    for days_back in 0 1 2; do
        candidate="$(date_days_ago "$days_back")"
        probe="${S3_BASE}/hrrr.${candidate}/conus/hrrr.t${INIT_HOUR}z.wrfprsf${FHOUR}.grib2.idx"
        if curl -sfI --max-time 15 "$probe" > /dev/null 2>&1; then
            RUN_DATE="$candidate"
            break
        fi
    done
    if [ -z "$RUN_DATE" ]; then
        echo "ERROR: no t${INIT_HOUR}z f${FHOUR} HRRR run found in the last 3 days" >&2
        echo "       (bucket: $S3_BASE — check network, or pin HRRR_RUN_DATE)" >&2
        exit 1
    fi
fi

GRIB2_URL="${S3_BASE}/hrrr.${RUN_DATE}/conus/hrrr.t${INIT_HOUR}z.wrfprsf${FHOUR}.grib2"
mkdir -p "$OUT_DIR"
cd "$OUT_DIR"
IDX_FILE=".hrrr_sample.idx"

echo "Run: hrrr.${RUN_DATE} t${INIT_HOUR}z f${FHOUR}"
curl -sf --retry 3 --max-time 60 "${GRIB2_URL}.idx" -o "$IDX_FILE"

# --- Byte ranges -------------------------------------------------------------
# idx line format: msg_num:byte_offset:d=YYYYMMDDHH:VAR:LEVEL:step:
# A message spans [its offset, next message's offset - 1]; the last message in
# the file has no successor, so it gets an open-ended range. Comment lines (the
# committed excerpt has a header) have no numeric offset and are skipped.
ranges="$(
    awk -F: -v re="$WANTED_RE" '
        $2 ~ /^[0-9]+$/ { n++; off[n] = $2; key[n] = $4 ":" $5 }
        END {
            for (i = 1; i <= n; i++)
                if (key[i] ~ re) {
                    if (i < n) printf "%d-%d\n", off[i], off[i + 1] - 1
                    else        printf "%d-\n", off[i]
                }
        }
    ' "$IDX_FILE"
)"

if [ -z "$ranges" ]; then
    echo "ERROR: no idx lines matched the wanted variable set" >&2
    echo "       The product may have renamed a field — compare against" >&2
    echo "       tests/fixtures/hrrr_idx_excerpt.txt and hrrr_fetch.py." >&2
    exit 1
fi

: > "$OUT_FILE"
count=0
while IFS= read -r range; do
    [ -z "$range" ] && continue
    curl -sf --retry 3 --max-time 120 -r "$range" "$GRIB2_URL" >> "$OUT_FILE"
    count=$((count + 1))
done <<< "$ranges"

cat > SOURCE.txt <<EOF
# HRRR golden-decode sample (#457) — fetched $(date -u +%Y-%m-%dT%H:%M:%SZ)
# Regenerate with scripts/download_hrrr_samples.sh
run=hrrr.${RUN_DATE} cycle=t${INIT_HOUR}z fhour=f${FHOUR}
grib2_url=${GRIB2_URL}
messages=${count}
EOF

rm -f "$IDX_FILE"
echo "Wrote ${count} messages -> ${OUT_DIR}/${OUT_FILE} ($(file_size "$OUT_FILE") bytes)"
echo "Run: pytest tests/test_hrrr_sample.py"
