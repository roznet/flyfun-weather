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

```bash
sudo -u triage python3 -m venv /mnt/flyfun_data/sandboxes/triage/venv
# Give triage write on its own venv dir
sudo chown -R triage:triage /mnt/flyfun_data/sandboxes/triage/venv
sudo -u triage /mnt/flyfun_data/sandboxes/triage/venv/bin/pip install \
    -e /mnt/flyfun_data/sandboxes/triage
```

### 6. Scoped MySQL user

Run as MySQL root (e.g. `docker compose exec shared-mysql mysql -uroot -p`):

```sql
CREATE USER 'weatherbrief_triage'@'127.0.0.1' IDENTIFIED BY '<strong-password>';
GRANT SELECT                 ON weatherbrief.users       TO 'weatherbrief_triage'@'127.0.0.1';
GRANT SELECT, UPDATE         ON weatherbrief.feedback    TO 'weatherbrief_triage'@'127.0.0.1';
GRANT SELECT, INSERT         ON weatherbrief.cost_ledger TO 'weatherbrief_triage'@'127.0.0.1';
FLUSH PRIVILEGES;
```

Adjust the host part (`'127.0.0.1'`) to match how the triage user connects —
if you reach MySQL via the compose-published port on 127.0.0.1, this is
correct; if via a docker bridge IP, use that CIDR.

### 7. Env file

Place the triage env at `/etc/triage/env`:

```bash
sudo mkdir -p /etc/triage
sudo tee /etc/triage/env > /dev/null <<'EOF'
ANTHROPIC_API_KEY=sk-ant-...
DATABASE_URL=mysql+pymysql://weatherbrief_triage:<pw>@127.0.0.1:<port>/weatherbrief
LOG_LEVEL=INFO
EOF
sudo chown triage:triage /etc/triage/env
sudo chmod 0400 /etc/triage/env
```

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

### Refreshing the sandbox

When code changes on main and you want the triage LLM to see it:

```bash
cd /mnt/flyfun_data/sandboxes/triage
sudo -u brice git pull --ff-only
# if pyproject changed:
sudo -u triage venv/bin/pip install -e .
```

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
