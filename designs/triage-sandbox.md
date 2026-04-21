# Triage Sandbox

Runbook for the `triage` system user + scoped source checkout used to invoke
`python -m weatherbrief.triage process` on the droplet.

This closes the C1 finding from the 2026-04-20 security audit: attacker-authored
feedback text is fed into `claude -p` with Read/Grep/Glob enabled. Without
OS-level isolation the LLM's Read tool can reach any absolute path the current
UID can open (`.env`, `~/.aws/credentials`, `/etc/shadow`, ...).

The sandbox enforces three things:

1. **Scoped source tree** — `claude` only sees a sparse checkout (`src/`,
   `web/ts/`, `designs/`, `configs/triage/`, `pyproject.toml`, `README.md`).
2. **Unix user `triage`** — cannot read `.env`, `configs/` at large, any other
   user's home, or anything outside the sandbox dir. Enforced by file perms.
3. **MySQL user `weatherbrief_triage`** — can only read `users`, read/update
   `feedback`, and insert into `cost_ledger`. No DDL, no access to
   `api_tokens`, `briefing_packs`, `user_preferences`, etc.

## Residual risk (accepted)

`ANTHROPIC_API_KEY` is passed to `claude` via env. A prompt-injected LLM can
read its own `/proc/self/environ` and exfiltrate the key into the reply text.
The `scan_for_exfil` regex in `triage/security.py` catches well-formed key
shapes in the output, and admin sends are gated on a clean scan. Because
triage is invoked manually with the operator eyeballing the input, a proxy
pattern that keeps the key out of the sandbox entirely was not adopted.

If triage ever moves to scheduled / unattended invocation, revisit — see the
proxy pattern discussed alongside this control.

---

## One-time droplet setup

All commands below are run on the droplet as a user with sudo (brice).

### 1. System user

```bash
sudo useradd --system --no-create-home --shell /usr/sbin/nologin triage
```

### 2. Sandbox directory

```bash
sudo mkdir -p /mnt/flyfun_data/sandboxes/triage
sudo chown brice:triage /mnt/flyfun_data/sandboxes/triage
sudo chmod 0750 /mnt/flyfun_data/sandboxes/triage
```

### 3. Sparse checkout from the existing local repo

```bash
cd /mnt/flyfun_data/sandboxes/triage
git clone --no-checkout /home/brice/flyfun-weather .
git sparse-checkout init --cone
git sparse-checkout set src web/ts designs configs/triage
# Top-level files come in automatically with sparse-checkout
git checkout main
```

Verify nothing sensitive slipped in:

```bash
ls -la        # no .env, no configs/ except configs/triage/
ls configs/   # should show only: triage
```

### 4. Permissions

```bash
sudo chown -R brice:triage .
sudo find . -type d -exec chmod 0750 {} \;
sudo find . -type f -exec chmod 0640 {} \;
```

### 5. Triage venv (owned by triage)

The venv lives at `/mnt/flyfun_data/sandboxes/triage/venv`, owned by the
`triage` user (so pip can write into it) while the enclosing sandbox
source tree remains read-only to `triage`.

The `weatherbrief` package is installed **non-editable** from a writable
staging copy in `/tmp`, not directly from the sandbox source. Editable
installs and the default `pip install <path>` both try to create
`<path>/src/weatherbrief.egg-info/` during the build, which fails because
the sandbox source tree is read-only to triage by design. Staging into
`/tmp/triage-build` lets the build run in a writable location; the
resulting package is copied into the venv's `site-packages/`, and the
sandbox source tree stays pristine. `claude -p` reads from the sandbox
source tree at runtime, not from the venv.

```bash
# 1. Pre-create the venv dir owned by triage (parent is not triage-writable by design)
sudo install -d -o triage -g triage -m 0755 /mnt/flyfun_data/sandboxes/triage/venv

# 2. Build the venv and upgrade pip
sudo -u triage python3 -m venv /mnt/flyfun_data/sandboxes/triage/venv
sudo -u triage /mnt/flyfun_data/sandboxes/triage/venv/bin/pip install --upgrade pip

# 3. Stage the package in /tmp and install from there
sudo -u triage bash -c '
  rm -rf /tmp/triage-build &&
  mkdir /tmp/triage-build &&
  cp -r /mnt/flyfun_data/sandboxes/triage/src /tmp/triage-build/ &&
  cp /mnt/flyfun_data/sandboxes/triage/pyproject.toml /tmp/triage-build/ &&
  /mnt/flyfun_data/sandboxes/triage/venv/bin/pip install /tmp/triage-build &&
  rm -rf /tmp/triage-build
'
```

The `/home/triage/.cache/pip is not writable` warning from pip is cosmetic —
triage has no home dir, so downloads are not cached. If you refresh the
venv often and want to silence it, set `PIP_CACHE_DIR=/tmp/triage-pip-cache`
and pre-create that dir as triage.

### 6. Scoped MySQL user

The SQL lives in the shared-infra repo at
`digitalocean/shared-infra/init-scripts/04-create-weatherbrief-triage-user.sql`
(same directory as the existing weatherbrief, wordpress, and flyfunboarding
DB bootstraps). Edit the placeholder password first, then run once against
the live shared-mysql:

```bash
cd ~/digitalocean/shared-infra
# Edit init-scripts/04-create-weatherbrief-triage-user.sql and replace
# CHANGE_ME with a strong password.
docker compose exec -T mysql mysql -uroot -p \
    < init-scripts/04-create-weatherbrief-triage-user.sql
```

The grants (SELECT on `users`, SELECT+UPDATE on `feedback`, SELECT+INSERT
on `cost_ledger`) are the minimum `triage/process.py` needs. No DDL, no
access to `api_tokens`, `briefing_packs`, `user_preferences`, `oauth_*`,
etc.

Verify from the host:

```bash
mysql -u weatherbrief_triage -p -h 127.0.0.1 -P 3306 weatherbrief \
    -e "SELECT COUNT(*) FROM feedback;"
```

Expected: a row count — **not** `ERROR 1045 (28000): Access denied`.

### 7. Env file

The triage env lives at `/mnt/flyfun_data/sandboxes/triage/.env` — right
inside the sandbox. `triage/__main__.py` calls `load_dotenv()`, which
auto-loads it when the CLI is invoked with CWD at the sandbox root, so
the wrapper script doesn't need to source anything.

```bash
sudo -u triage tee /mnt/flyfun_data/sandboxes/triage/.env > /dev/null <<'EOF'
ANTHROPIC_API_KEY=sk-ant-...
DATABASE_URL=mysql+pymysql://weatherbrief_triage:<pw>@127.0.0.1:3306/weatherbrief
LOG_LEVEL=INFO
EOF
sudo chown triage:triage /mnt/flyfun_data/sandboxes/triage/.env
sudo chmod 0400 /mnt/flyfun_data/sandboxes/triage/.env
```

Security posture: the `.env` is readable only by the `triage` user, 0400.
A prompt-injected LLM running as `triage` can still read it (same-UID
access to `/proc/self/environ` and to the file itself) — that is the
accepted residual risk documented at the top of this file. Moving the
file to `/etc/` or elsewhere does not change that risk, so co-locating
it with the sandbox is preferred for operational simplicity.

The file is already in the main repo's `.gitignore`, so the sparse
checkout won't fight it — `git pull` leaves it alone, `git status` in
the sandbox shows nothing.

### 8. Wrapper script

`/usr/local/bin/triage-run`:

```bash
#!/bin/bash
set -euo pipefail
cd /mnt/flyfun_data/sandboxes/triage
exec sudo -u triage --preserve-env= \
    env $(grep -v '^#' /etc/triage/env | xargs) \
    /mnt/flyfun_data/sandboxes/triage/venv/bin/python \
    -m weatherbrief.triage "$@"
```

```bash
sudo chmod 0755 /usr/local/bin/triage-run
```

### 9. Sudoers rule (optional, makes invocation quieter)

```
# /etc/sudoers.d/triage
brice ALL=(triage) NOPASSWD: /mnt/flyfun_data/sandboxes/triage/venv/bin/python
```

(Tighten the command path further if you want.)

---

## Ongoing operations

### Refreshing the sandbox source

When code changes on main and you want the triage LLM to see it:

```bash
cd /mnt/flyfun_data/sandboxes/triage
git pull --ff-only

# After pull, reset perms on any newly-added files (clone/pull leaves them brice:brice).
# Exclude .env from the file chmod — it must stay 0400 triage:triage.
sudo chown -R brice:triage .
sudo chown triage:triage .env
sudo find . -type d -exec chmod 0750 {} \;
sudo find . -type f ! -name '.env' -exec chmod 0640 {} \;
sudo chmod 0400 .env
```

Note: updating the sandbox source tree is enough for `claude -p` to see
new code, because it reads files at runtime. The triage venv's installed
copy of `weatherbrief` only runs the DB-side glue (`triage/process.py`,
`triage/prompt.py`, `triage/security.py`, `db/models.py`); if you edit
those, re-run the staging install (next section).

### Refreshing the venv

The venv pins `flyfun-common` and `euro-aip` at whatever was latest on
PyPI at install time. For new minor/bug-fix releases of those packages,
upgrade in place:

```bash
sudo -u triage /mnt/flyfun_data/sandboxes/triage/venv/bin/pip install \
    --upgrade flyfun-common euro-aip
```

For anything else (new dep in `pyproject.toml`, major bump of an existing
dep, or a change to code in `src/weatherbrief/triage/` or `src/weatherbrief/db/models.py`):
re-run the staging install, which rebuilds the `weatherbrief` package in
the venv from current sandbox source and lets pip re-resolve deps:

```bash
sudo -u triage bash -c '
  rm -rf /tmp/triage-build &&
  mkdir /tmp/triage-build &&
  cp -r /mnt/flyfun_data/sandboxes/triage/src /tmp/triage-build/ &&
  cp /mnt/flyfun_data/sandboxes/triage/pyproject.toml /tmp/triage-build/ &&
  /mnt/flyfun_data/sandboxes/triage/venv/bin/pip install --upgrade /tmp/triage-build &&
  rm -rf /tmp/triage-build
'
```

To fully rebuild from scratch (e.g. after a Python minor version bump on
the host), delete `/mnt/flyfun_data/sandboxes/triage/venv/` and redo
section 5.

### Running triage

```bash
triage-run status
triage-run process --id <feedback_id>
triage-run process -n 5
```

### Verifying the sandbox

From inside the triage user context, these should all fail:

```bash
sudo -u triage cat /home/brice/flyfun-weather/.env       # Permission denied
sudo -u triage cat /etc/shadow                           # Permission denied
sudo -u triage ls /mnt/flyfun_data/sandboxes/triage/configs/
# shows: triage (only)
```

The code tripwire in `triage/process.py::_assert_sandboxed()` additionally
refuses to run as any user other than `triage` unless `TRIAGE_ALLOW_UNSAFE=1`
is set — protects against "oops, ran `python -m weatherbrief.triage` from my
laptop checkout".

---

## What's deliberately NOT in the sandbox

| Path | Why excluded |
|------|--------------|
| `.env`, `.env.*` | Production secrets |
| `configs/` (except `configs/triage/`) | Digest / verification configs may carry keys or sensitive prompts |
| `flyfun-common/` editable source | Not needed to triage bug reports; keeps auth internals out of LLM view |
| `.git/hooks/`, `.github/workflows/` | Not useful for the triage task |
| `/home/brice/` | Developer's dotfiles, SSH keys, etc. |
| Any other `/etc/...` secret files | Only `/etc/triage/env` is readable by triage user |

## Related

- `src/weatherbrief/triage/process.py` — the CLI worker, with
  `_assert_sandboxed()` tripwire.
- `src/weatherbrief/triage/security.py` — `scan_for_exfil()` and
  `sanitize_for_untrusted_block()`.
- `configs/triage/triage_prompt_v1.md` — prompt template with the
  untrusted-input block.
- `SECURITY_AUDIT.md` §C1 — originating finding.
