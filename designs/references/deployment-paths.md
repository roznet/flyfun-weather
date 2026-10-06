# Resolving deployment paths

Operational docs in this repo use placeholders instead of hardcoded hosts and paths, so the
same runbook works for a fork or a second deployment. **`scripts/ops/hosts.py` resolves them**
— cited by `check-health`, `deploy`, `sync-ecmwf`, `eval-workbench`, `investigateflight` and
`archive`. This doc explains what the values mean and the traps the script is built around.

## How to resolve

Hosts come from `deploy/hosts.json` (gitignored — ssh targets are deployment-private;
`deploy/hosts.example.json` is tracked and documents every field). The script reads it, then
checks each path **on the host itself** in one ssh per host:

```bash
python3 scripts/ops/hosts.py server        # droplet
python3 scripts/ops/hosts.py node <name>   # one compute node (`nodes` for all)
python3 scripts/ops/hosts.py local         # this checkout
python3 scripts/ops/hosts.py all           # everything, one table
python3 scripts/ops/hosts.py server --get HOST_DATA_DIR   # one value, for a command
```

Run it **once at the start** of a task and use the printed values literally wherever a doc
says `<NAME>`. (Each agent Bash call is a fresh shell, so `eval "$(… --env)"` does not carry
over between calls — reading the values off the report does.) Every line is
`ok` / `problem` / `unknown` / `skip` with its evidence; exit 0 = all ok, 1 = a problem,
2 = could not tell. **Only verified values are exported** — a missing path is reported, never
handed back — so a step whose value did not resolve must stop, not guess.

Cloud sessions have no `hosts.json` by design; the script says so (exit 2) and host steps are
simply unavailable there.

## Placeholders

| Placeholder | What it is | From |
|---|---|---|
| `<SERVER_SSH>` | SSH target for the production droplet | `hosts.json` `server.ssh` |
| `<SERVER_PROJECT_DIR>` | The checkout on the server, **relative to the SSH user's home** — every `ssh … "cd <SERVER_PROJECT_DIR>"` assumes that | `server.project_dir` |
| `<HOST_DATA_DIR>` | Host path for the app's data dir (packs, GRIB caches, SRTM, nav.db) | server `.env`, checked |
| `<HOST_ECMWF_GRIB_DIR>` | Host path for ECMWF deliveries | server `.env`, checked |
| `<HOST_SNAPSHOT_INBOX>`, `<HOST_CELLS_INBOX>` | Where compute nodes drop artifacts / observed cells for ingest | server `.env`, checked |
| `<HOST_AIRPORTS_DB>` | The host-side nav.db file (`AIRPORTS_DB`'s basename under `HOST_DATA_DIR`) | derived, checked |
| `<DATA_VOLUME>` | The **mount point** holding all of the above — the disk gauge in health checks | derived from `HOST_DATA_DIR` |
| `<NODE_SSH>`, `<NODE_REPO>`, `<NODE_VENV>`, `<NODE_HEAD>` | Compute-node values | `hosts.py node <name>`, checked on the node |
| `<node.name>`, `<node.branch>`, `lan_only`, `schedule_utc` … | Compute-node inventory fields | `hosts.json` `nodes[]` as written |
| `<LOCAL_DATA_DIR>`, `<LOCAL_AIRPORTS_DB>`, `<LOCAL_ECMWF_GRIB_DIR>`, `<LOCAL_DEV_DB>` | This checkout's paths (`${WORKING_DIR}` expanded) | `hosts.py local` |
| `<shared-infra-dir>` | Directory holding the shared MySQL compose stack on the server | `ssh <SERVER_SSH> "ls -d ~/*/docker-compose.y*ml"` and pick the shared-infra one |
| `<mysql-container>` | Container name of the shared MySQL instance | `deploy/mysql-baseline.json` (gitignored; `.example.json` is tracked) |
| MySQL root password | Root credential for the shared instance | **Never pass it on a command line.** Read it from the container's own env: `docker exec <mysql-container> sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" …'`. |
| `<admin-token>` | Admin session cookie for `/api/admin/metrics` | Supplied by the user at run time. Never read it from disk. |

## The traps the script handles (know them when reading its output)

**1. `<DATA_VOLUME>` is not `HOST_DATA_DIR`.** The data dir sits *inside* the volume, a couple
of levels down. Health checks gauge the **volume** (what fills up; the 62–78 % band); the app
reads and writes the **data dir**. The script derives the mount point rather than assuming a
depth.

**2. `DATA_DIR` in the server's `.env` is a _container_ path.** Compose maps
`${HOST_DATA_DIR}:/app/data`, so inside the container the data is `/app/data`, and
`AIRPORTS_DB` likewise reads as a container path. Over plain `ssh` use the `HOST_*` values;
via `docker exec` use the container path. The script only reads `HOST_*` keys on the server.

**3. An ssh name can reach the wrong machine.** The mac-mini node's ssh name has three times
resolved to the MacBook or to nothing, and the node then sat dozens of commits behind
unnoticed. `hostname` in `hosts.json` makes that a `problem` line; an unreachable `lan_only`
node is `unknown` ("not known to be down"), never `ok`.

**4. Local vs server variable names.** In a dev checkout (and on a compute node) the paths are
plain `DATA_DIR`, `ECMWF_GRIB_DIR`, `AIRPORTS_DB`; the server counterparts are `HOST_*`. The dev
DB the app opens is `{DATA_DIR}/flyfun.db` — `DATABASE_URL` is ignored by the app in dev, so the
script flags a set `DATABASE_URL` as `unknown` (see `worktree-init`).

The expected server shape, for sanity-checking what the script resolved (don't hardcode it):

```
<DATA_VOLUME>/                 ← df target; the 199 GB disk gauge
├── weather/
│   ├── data/                  ← HOST_DATA_DIR   (packs, .cache/grib, .cache/srtm, nav.db)
│   ├── snapshot_inbox/        ← HOST_SNAPSHOT_INBOX
│   └── cells_inbox/           ← HOST_CELLS_INBOX
├── ecmwf/data/                ← HOST_ECMWF_GRIB_DIR
├── mysql/                     ← MySQL data + binlogs
└── forms/, logs/, sandboxes/  ← siblings, not ours
```
