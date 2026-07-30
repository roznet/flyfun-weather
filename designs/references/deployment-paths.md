# Resolving deployment paths

Operational docs in this repo use placeholders instead of hardcoded hosts and paths, so the
same runbook works for a fork or a second deployment. This is the single source of truth for
what each placeholder means and how to resolve it — cited by `check-health`, `deploy`,
`sync-ecmwf`, `eval-workbench` and `investigateflight`.

Resolve once at the start of a session and reuse; don't re-derive per command.

## Placeholders

| Placeholder | What it is | How to resolve |
|---|---|---|
| `<user>@<server>` | SSH target for the production droplet | Your local Claude config or `~/.ssh/config`. Never written into this repo. |
| `<project-dir>` | The checkout on the server, **relative to the SSH user's home** | `ssh <user>@<server> "ls -d ~/*/.git" ` — or just try `flyfun-weather`, the conventional name. Every `ssh ... "cd <project-dir>"` in these docs assumes home-relative. |
| `HOST_DATA_DIR` | Host path for the app's data dir (packs, GRIB caches, SRTM, nav.db) | `ssh <user>@<server> "grep '^HOST_DATA_DIR=' <project-dir>/.env \| cut -d= -f2"` |
| `HOST_ECMWF_GRIB_DIR` | Host path for ECMWF deliveries | same grep, `^HOST_ECMWF_GRIB_DIR=` |
| `HOST_SNAPSHOT_INBOX` | Where a compute node drops its artifact for ingest | same grep, `^HOST_SNAPSHOT_INBOX=` |
| `<data-volume>` | The **mount point** holding all of the above — the disk gauge in health checks | **Derive it, don't guess** (see below). It is a *parent* of `HOST_DATA_DIR`, not equal to it. |
| `<shared-infra-dir>` | Directory holding the shared MySQL compose stack on the server | `ssh <user>@<server> "ls -d ~/*/docker-compose.y*ml"` and pick the shared-infra one |
| `<node.ssh>`, `<node.repo>`, `<node.venv>`, `<node.branch>` | Compute-node fields | Entries in `deploy/compute-nodes.json` (gitignored — ssh targets are deployment-private). `deploy/compute-nodes.example.json` is tracked and documents every field. |
| `<admin-token>` | Admin session cookie for `/api/admin/metrics` | Supplied by the user at run time. Never read it from disk. |

## The two traps

**1. `<data-volume>` is not `HOST_DATA_DIR`.** The data dir sits *inside* the volume, typically
a couple of levels down. Health checks gauge the **volume** (that's what fills up and what the
62–78 % band refers to); the app reads and writes the **data dir**. Using one where the other
belongs either measures the wrong filesystem or walks the wrong tree.

Derive the mount point rather than assuming a depth:

```bash
ssh <user>@<server> "df -P '<HOST_DATA_DIR>' | tail -1 | awk '{print \$6}'"
```

**2. `DATA_DIR` in the server's `.env` is a _container_ path, not a host path.** The compose
file maps `${HOST_DATA_DIR}:/app/data`, so inside the container the same data is `/app/data`,
and `AIRPORTS_DB` likewise reads as a container path. Anything you run over plain `ssh` needs
the `HOST_*` value; anything you run via `docker exec` needs the container path. Grepping for
`DATA_DIR` without the `HOST_` prefix silently returns the wrong one — always anchor the grep
with `^HOST_`.

## Resolve everything in one go

```bash
SERVER=<user>@<server>
PROJECT_DIR=flyfun-weather          # home-relative on the server

# Pull all three HOST_* values into the local shell
eval "$(ssh "$SERVER" "grep -E '^HOST_(DATA_DIR|ECMWF_GRIB_DIR|SNAPSHOT_INBOX)=' $PROJECT_DIR/.env")"

# Derive the volume mount point from the data dir
DATA_VOLUME=$(ssh "$SERVER" "df -P '$HOST_DATA_DIR' | tail -1 | awk '{print \$6}'")

printf 'volume=%s\ndata=%s\necmwf=%s\ninbox=%s\n' \
  "$DATA_VOLUME" "$HOST_DATA_DIR" "$HOST_ECMWF_GRIB_DIR" "$HOST_SNAPSHOT_INBOX"
```

The expected shape — a single large volume with the app's data, ECMWF deliveries, MySQL and
sundry siblings underneath it:

```
<data-volume>/                 ← df target; the 199 GB disk gauge
├── weather/
│   ├── data/                  ← HOST_DATA_DIR   (packs, .cache/grib, .cache/srtm, nav.db)
│   └── snapshot_inbox/        ← HOST_SNAPSHOT_INBOX
├── ecmwf/data/                ← HOST_ECMWF_GRIB_DIR
├── mysql/                     ← MySQL data + binlogs
└── forms/, logs/, sandboxes/  ← siblings, not ours
```

Don't hardcode that tree either — it's the current deployment's shape, and the `HOST_*` vars
are what make a fork work without editing these docs. Use it to sanity-check that what you
resolved looks right.

## Local dev

In a dev checkout the equivalents come from this repo's own `.env` and are ordinary host
paths — `DATA_DIR` (no `HOST_` prefix, because there's no container indirection),
`ECMWF_GRIB_DIR`, `AIRPORTS_DB`. Note `ECMWF_GRIB_DIR` is the *local* variable; its server
counterpart is `HOST_ECMWF_GRIB_DIR`. They are easy to confuse when writing a sync recipe that
touches both ends.
