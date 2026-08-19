#!/usr/bin/env python3
"""Drive App Store Connect for an iOS release — everything *except* the submit.

This automates the clicking that sits between `xcodebuild archive` and the
"Submit for Review" button: creating the App Store version, writing the
"What's New" text, uploading the archive, waiting for Apple to finish
processing it, and attaching the resulting build to the version.

**There is deliberately no `submit` subcommand.** Submitting to App Review is
the one irreversible step in the chain — everything this script does can be
edited or undone in App Store Connect afterwards, and submission cannot. The
omission is the safety mechanism: you cannot fat-finger a submission through a
tool that has no code path for it. Press Submit yourself, in the web UI, when
you are happy with what `asc.py status` shows.

Every subcommand is **idempotent** — re-running is the expected way to correct
a mistake:

  - `ensure-version` reuses the existing editable version if there is one
    (renaming it if you changed the marketing version), and only creates one
    when there isn't. It refuses to touch a version that is already in review.
  - `set-notes` overwrites "What's New" in place, as often as you like.
  - `upload` + `attach-build` can be repeated to swap in a new binary; a
    version's build relationship is a pointer, so re-attaching just moves it.

## Credentials (never committed — this is a public repo)

Everything sensitive is read from the environment, i.e. from the gitignored
`.env`. `.env.sample` documents the variable names with empty values.

  ASC_KEY_ID       10-char Key ID of an App Store Connect API key
  ASC_ISSUER_ID    the team's Issuer ID (UUID, shown above the key list)
  ASC_KEY_P8_PATH  path to the downloaded AuthKey_<KEY_ID>.p8, OUTSIDE this repo
                   (optional — defaults to Apple's conventional location,
                   ~/.appstoreconnect/private_keys/AuthKey_<KEY_ID>.p8)
  ASC_APP_ID       optional; skips a bundle-id lookup on every call
  ASC_BUNDLE_ID    optional override (defaults to the app's real bundle id)

Create the key at App Store Connect → Users and Access → Integrations → App
Store Connect API. It needs the **App Manager** role: a Developer-role key can
read but cannot create versions or change metadata. Apple lets you download
the `.p8` exactly once — keep it out of the repo (`*.p8` is gitignored as a
backstop, but the conventional `~/.appstoreconnect/private_keys/` is better).

Unlike `APNS_KEY_P8`, there is no inline/base64 form here. `xcodebuild` requires
a real file path for `-authenticationKeyPath`, and this script only ever runs on
a developer machine — never in the production container — so the file-on-disk
shape is the honest one.

## Usage

    source venv/bin/activate

    # What does App Store Connect think right now?
    python3 scripts/asc.py status

    # Do the whole staging chain in one go (the /archive skill calls this):
    python3 scripts/asc.py stage --version 1.5 --build 11 \\
        --archive "~/Library/Developer/Xcode/Archives/.../flyfun-weather.xcarchive" \\
        --notes-file release-notes/ios-1.5.json

    # Or step by step:
    python3 scripts/asc.py ensure-version --version 1.5
    python3 scripts/asc.py set-notes --notes-file release-notes/ios-1.5.json
    python3 scripts/asc.py upload --archive <path>
    python3 scripts/asc.py wait-build --version 1.5 --build 11
    python3 scripts/asc.py attach-build --version 1.5 --build 11
    python3 scripts/asc.py review-notes --text "flyfunweather://auth?token=..."

Add `--dry-run` to any mutating subcommand to see the request that would be
sent without sending it.
"""

from __future__ import annotations

import argparse
import json
import os
import plistlib
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, NoReturn

import jwt
import requests

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - dotenv is a dev dependency
    load_dotenv = None

API_ROOT = "https://api.appstoreconnect.apple.com/v1"
PLATFORM = "IOS"

# Apple rejects a token whose lifetime exceeds 20 minutes. Stay inside it.
_TOKEN_TTL_SECONDS = 15 * 60

DEFAULT_BUNDLE_ID = "net.ro-z.flyfun-weather"

# A version in one of these states still accepts metadata and build changes.
# `REJECTED`/`DEVELOPER_REJECTED`/`METADATA_REJECTED`/`INVALID_BINARY` are the
# "came back from review, fix it" states — re-running `stage` against one is a
# normal resubmission flow, so they count as editable.
EDITABLE_STATES = {
    "PREPARE_FOR_SUBMISSION",
    "DEVELOPER_REJECTED",
    "REJECTED",
    "METADATA_REJECTED",
    "INVALID_BINARY",
    "WAITING_FOR_EXPORT_COMPLIANCE",
    "READY_FOR_REVIEW",
}

# A version in one of these is in Apple's hands (or already queued). Editing it
# requires cancelling the submission first — which is a human decision, so we
# stop rather than do it.
IN_FLIGHT_STATES = {
    "WAITING_FOR_REVIEW",
    "IN_REVIEW",
    "PENDING_APPLE_RELEASE",
    "PENDING_DEVELOPER_RELEASE",
    "PROCESSING_FOR_APP_STORE",
    "PROCESSING_FOR_DISTRIBUTION",
    "ACCEPTED",
}


def _fail(msg: str) -> NoReturn:
    sys.exit(f"error: {msg}")


# --- Config ------------------------------------------------------------------


@dataclass(frozen=True)
class AscConfig:
    """App Store Connect API credentials, loaded from the environment."""

    key_id: str
    issuer_id: str
    key_path: Path
    app_id: str | None
    bundle_id: str

    @classmethod
    def from_env(cls) -> "AscConfig":
        key_id = os.environ.get("ASC_KEY_ID")
        issuer_id = os.environ.get("ASC_ISSUER_ID")
        if not key_id or not issuer_id:
            _fail(
                "ASC_KEY_ID and ASC_ISSUER_ID must be set (add them to .env, "
                "which is gitignored). See the module docstring for how to "
                "create an App Store Connect API key."
            )

        raw_path = os.environ.get("ASC_KEY_P8_PATH")
        if raw_path:
            key_path = Path(raw_path).expanduser()
        else:
            # Apple's conventional location — the same one altool and
            # xcodebuild search when you omit an explicit path.
            key_path = (
                Path.home()
                / ".appstoreconnect"
                / "private_keys"
                / f"AuthKey_{key_id}.p8"
            )
        if not key_path.is_file():
            _fail(
                f"private key not found at {key_path}. Set ASC_KEY_P8_PATH in "
                ".env to the AuthKey_*.p8 you downloaded from App Store "
                "Connect — keep it outside this repo."
            )

        return cls(
            key_id=key_id,
            issuer_id=issuer_id,
            key_path=key_path,
            app_id=os.environ.get("ASC_APP_ID") or None,
            bundle_id=os.environ.get("ASC_BUNDLE_ID") or DEFAULT_BUNDLE_ID,
        )


# --- HTTP client -------------------------------------------------------------


class Asc:
    """Thin App Store Connect REST client.

    Mints a fresh ES256 token when the cached one nears expiry. Tokens are
    capped at 20 minutes by Apple, and `wait-build` can poll for far longer
    than that, so the refresh is not optional.
    """

    def __init__(self, config: AscConfig, dry_run: bool = False):
        self.config = config
        self.dry_run = dry_run
        self._token: str | None = None
        self._token_exp = 0.0
        self._pem = config.key_path.read_text()
        self._app_id: str | None = config.app_id

    def token(self) -> str:
        now = time.monotonic()
        if self._token and now < self._token_exp:
            return self._token
        issued = datetime.now(timezone.utc)
        self._token = jwt.encode(
            {
                "iss": self.config.issuer_id,
                "iat": issued,
                "exp": issued + timedelta(seconds=_TOKEN_TTL_SECONDS),
                "aud": "appstoreconnect-v1",
            },
            self._pem,
            algorithm="ES256",
            headers={"kid": self.config.key_id, "typ": "JWT"},
        )
        # Refresh a minute early so a slow request can't straddle the expiry.
        self._token_exp = now + _TOKEN_TTL_SECONDS - 60
        return self._token

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = path if path.startswith("http") else f"{API_ROOT}{path}"
        if self.dry_run and method != "GET":
            print(f"[dry-run] {method} {url}")
            if body:
                print(f"[dry-run] {json.dumps(body, indent=2)}")
            return {}

        resp = requests.request(
            method,
            url,
            headers={
                "Authorization": f"Bearer {self.token()}",
                "Content-Type": "application/json",
            },
            params=params,
            json=body,
            timeout=120,
        )
        if resp.status_code == 401:
            _fail(
                "App Store Connect rejected the key (401). Check ASC_KEY_ID / "
                "ASC_ISSUER_ID match the .p8, and that the key is still active."
            )
        if resp.status_code == 403:
            _fail(
                "App Store Connect refused the operation (403). The API key "
                "most likely lacks the App Manager role — a Developer-role key "
                "can read but cannot create versions or edit metadata."
            )
        if not resp.ok:
            detail = resp.text
            try:
                errors = resp.json().get("errors", [])
                detail = "; ".join(
                    f"{e.get('title', '?')}: {e.get('detail', '')}" for e in errors
                ) or resp.text
            except ValueError:
                pass
            _fail(f"{method} {url} -> {resp.status_code}\n  {detail}")
        if resp.status_code == 204 or not resp.content:
            return {}
        return resp.json()

    def get(self, path: str, **params: Any) -> dict[str, Any]:
        return self.request("GET", path, params=params or None)

    # --- App ---

    def app_id(self) -> str:
        if self._app_id:
            return self._app_id
        data = self.get("/apps", **{"filter[bundleId]": self.config.bundle_id})
        apps = data.get("data", [])
        if not apps:
            _fail(
                f"no app found for bundle id {self.config.bundle_id}. Set "
                "ASC_BUNDLE_ID or ASC_APP_ID in .env."
            )
        self._app_id = apps[0]["id"]
        return self._app_id

    # --- Versions ---

    def versions(self, limit: int = 20) -> list[dict[str, Any]]:
        data = self.get(
            f"/apps/{self.app_id()}/appStoreVersions",
            **{"filter[platform]": PLATFORM, "limit": limit},
        )
        return data.get("data", [])

    @staticmethod
    def state_of(version: dict[str, Any]) -> str:
        """Return a version's state.

        Apple introduced `appVersionState` alongside the older `appStoreState`;
        both may be present depending on the API revision serving the request.
        Prefer the newer one and fall back, so this keeps working across the
        deprecation rather than reading `None` and mis-classifying everything
        as editable.
        """
        attrs = version.get("attributes", {})
        return attrs.get("appVersionState") or attrs.get("appStoreState") or "UNKNOWN"

    def editable_version(self) -> dict[str, Any] | None:
        for version in self.versions():
            if self.state_of(version) in EDITABLE_STATES:
                return version
        return None

    def ensure_version(
        self, version_string: str, release_type: str = "MANUAL"
    ) -> dict[str, Any]:
        """Get-or-create the editable App Store version for `version_string`.

        Three cases, all of which the /archive skill can hit legitimately:
        an editable version already exists with this number (reuse it), exists
        with a different number because the bump changed (rename it), or does
        not exist (create it).
        """
        existing = self.editable_version()
        if existing is not None:
            current = existing["attributes"].get("versionString")
            if current == version_string:
                print(
                    f"Version {version_string} already exists and is editable "
                    f"({self.state_of(existing)}) — reusing it."
                )
                return existing
            print(
                f"Editable version is {current}, renaming it to {version_string}."
            )
            self.request(
                "PATCH",
                f"/appStoreVersions/{existing['id']}",
                body={
                    "data": {
                        "id": existing["id"],
                        "type": "appStoreVersions",
                        "attributes": {"versionString": version_string},
                    }
                },
            )
            existing["attributes"]["versionString"] = version_string
            return existing

        # Nothing editable. If something is mid-review, stop — cancelling a
        # submission is a human decision, not something a script should do.
        for version in self.versions():
            state = self.state_of(version)
            if state in IN_FLIGHT_STATES:
                _fail(
                    f"version {version['attributes'].get('versionString')} is "
                    f"{state} — Apple has it. Cancel the submission in App "
                    "Store Connect if you need to change it, then re-run."
                )

        print(f"Creating version {version_string} ({release_type}).")
        created = self.request(
            "POST",
            "/appStoreVersions",
            body={
                "data": {
                    "type": "appStoreVersions",
                    "attributes": {
                        "platform": PLATFORM,
                        "versionString": version_string,
                        "releaseType": release_type,
                    },
                    "relationships": {
                        "app": {"data": {"type": "apps", "id": self.app_id()}}
                    },
                }
            },
        )
        return created.get("data", {})

    # --- What's New ---

    def set_notes(
        self, version_id: str, text: str, locales: list[str] | None = None
    ) -> list[str]:
        """Overwrite `whatsNew` on the version's localizations.

        Localizations are created by Apple for each locale the app already
        supports, so this patches what is there rather than creating rows.
        """
        data = self.get(f"/appStoreVersions/{version_id}/appStoreVersionLocalizations")
        rows = data.get("data", [])
        if not rows:
            _fail("version has no localizations — nothing to write What's New to.")

        updated = []
        for row in rows:
            locale = row["attributes"].get("locale")
            if locales and locale not in locales:
                continue
            self.request(
                "PATCH",
                f"/appStoreVersionLocalizations/{row['id']}",
                body={
                    "data": {
                        "id": row["id"],
                        "type": "appStoreVersionLocalizations",
                        "attributes": {"whatsNew": text},
                    }
                },
            )
            updated.append(locale)
        if not updated:
            _fail(
                f"no localization matched {locales}. Available: "
                + ", ".join(r["attributes"].get("locale", "?") for r in rows)
            )
        return updated

    # --- Builds ---

    def find_build(self, marketing_version: str, build_number: str) -> dict[str, Any] | None:
        """Find the build for (marketing version, build number).

        Build numbers repeat across marketing versions, so filtering on the
        build number alone is not enough — the pre-release version is matched
        client-side to pin the pair.
        """
        data = self.get(
            "/builds",
            **{
                "filter[app]": self.app_id(),
                "filter[version]": build_number,
                "include": "preReleaseVersion",
                "limit": 20,
            },
        )
        included = {
            item["id"]: item
            for item in data.get("included", [])
            if item["type"] == "preReleaseVersions"
        }
        for build in data.get("data", []):
            rel = (
                build.get("relationships", {})
                .get("preReleaseVersion", {})
                .get("data")
            )
            pre = included.get(rel["id"]) if rel else None
            if pre and pre["attributes"].get("version") == marketing_version:
                return build
        return None

    def wait_build(
        self, marketing_version: str, build_number: str, timeout_minutes: int = 60
    ) -> dict[str, Any]:
        """Poll until the build finishes processing on Apple's side.

        Typically 5–30 minutes after upload. The build simply does not exist
        for the first few minutes, so "not found yet" is a normal state here,
        not an error.
        """
        deadline = time.monotonic() + timeout_minutes * 60
        interval = 30
        last = None
        while time.monotonic() < deadline:
            build = self.find_build(marketing_version, build_number)
            if build is not None:
                state = build["attributes"].get("processingState")
                if state != last:
                    print(f"  build {marketing_version} ({build_number}): {state}")
                    last = state
                if state == "VALID":
                    return build
                if state in {"FAILED", "INVALID"}:
                    _fail(
                        f"build {marketing_version} ({build_number}) came back "
                        f"{state}. Check the email from Apple / the Activity "
                        "tab in App Store Connect for the reason."
                    )
            elif last is None:
                print("  build not visible yet (upload still ingesting)…")
                last = "PENDING"
            time.sleep(interval)
        _fail(
            f"timed out after {timeout_minutes} min waiting for build "
            f"{marketing_version} ({build_number}) to become VALID. It may just "
            "be slow — re-run `wait-build` rather than re-uploading."
        )

    def attach_build(self, version_id: str, build_id: str) -> None:
        """Point the version at a build. Re-runnable — it's just a pointer."""
        self.request(
            "PATCH",
            f"/appStoreVersions/{version_id}/relationships/build",
            body={"data": {"type": "builds", "id": build_id}},
        )

    # --- App Review details ---

    def set_review_notes(self, version_id: str, notes: str) -> None:
        """Set the App Review notes (where the reviewer sign-in link goes).

        The review detail may or may not exist yet for a given version, so
        this creates it on first use and patches it thereafter.
        """
        existing = self.get(f"/appStoreVersions/{version_id}/appStoreReviewDetail")
        row = existing.get("data")
        if row:
            self.request(
                "PATCH",
                f"/appStoreReviewDetails/{row['id']}",
                body={
                    "data": {
                        "id": row["id"],
                        "type": "appStoreReviewDetails",
                        "attributes": {"notes": notes},
                    }
                },
            )
        else:
            self.request(
                "POST",
                "/appStoreReviewDetails",
                body={
                    "data": {
                        "type": "appStoreReviewDetails",
                        "attributes": {"notes": notes},
                        "relationships": {
                            "appStoreVersion": {
                                "data": {
                                    "type": "appStoreVersions",
                                    "id": version_id,
                                }
                            }
                        },
                    }
                },
            )


# --- Archive upload ----------------------------------------------------------


def upload_archive(config: AscConfig, archive: Path, dry_run: bool = False) -> None:
    """Export and upload an .xcarchive to App Store Connect via xcodebuild.

    The binary upload is NOT part of the REST API — it goes through
    `xcodebuild -exportArchive` with `destination: upload`, authenticating with
    the same App Store Connect key.

    `manageAppVersionAndBuildNumber: false` matters: left at its default,
    Xcode may silently rewrite the build number during export, and the build
    that lands in App Store Connect then won't match the number `attach-build`
    is looking for.
    """
    if not archive.is_dir():
        _fail(f"archive not found: {archive}")

    options = {
        "method": "app-store-connect",
        "destination": "upload",
        "teamID": os.environ.get("ASC_TEAM_ID", "M7QSSF3624"),
        "uploadSymbols": True,
        "manageAppVersionAndBuildNumber": False,
    }

    # Written to a temp dir, never into the repo — it is a build artifact and
    # this is a public checkout.
    with tempfile.TemporaryDirectory(prefix="asc-export-") as tmp:
        plist_path = Path(tmp) / "exportOptions.plist"
        plist_path.write_bytes(plistlib.dumps(options))

        cmd = [
            "xcodebuild",
            "-exportArchive",
            "-archivePath", str(archive),
            "-exportPath", str(Path(tmp) / "export"),
            "-exportOptionsPlist", str(plist_path),
            "-authenticationKeyPath", str(config.key_path),
            "-authenticationKeyID", config.key_id,
            "-authenticationKeyIssuerID", config.issuer_id,
            "-allowProvisioningUpdates",
        ]
        if dry_run:
            print("[dry-run] " + " ".join(cmd))
            print("[dry-run] exportOptions.plist:")
            print(plistlib.dumps(options).decode())
            return

        print(f"Uploading {archive.name} …  (this takes several minutes)")
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            tail = (result.stdout or "")[-3000:] + (result.stderr or "")[-3000:]
            _fail(f"xcodebuild -exportArchive failed:\n{tail}")
        print("Upload accepted by App Store Connect. Apple now processes the build.")


# --- Notes loading -----------------------------------------------------------


def load_notes(args: argparse.Namespace) -> str:
    """Resolve the What's New text from --text, --notes-file, or stdin.

    `--notes-file` reads the same `release-notes/ios-<version>.json` the
    /archive skill already drafts, so the App Store text and the site's
    release-stream entry cannot drift apart.
    """
    if args.text:
        return args.text
    if args.notes_file == "-":
        return sys.stdin.read().strip()
    path = Path(args.notes_file)
    if not path.is_file():
        _fail(f"notes file not found: {path}")
    if path.suffix == ".json":
        entries = json.loads(path.read_text())
        if not isinstance(entries, list) or not entries:
            _fail(f"{path} is not a non-empty JSON list of release entries.")
        body = entries[0].get("body")
        if not body:
            _fail(f"{path} first entry has no 'body' field.")
        return body
    return path.read_text().strip()


# --- Reporting ---------------------------------------------------------------


def print_status(asc: Asc) -> None:
    app = asc.get(f"/apps/{asc.app_id()}").get("data", {})
    attrs = app.get("attributes", {})
    print(f"App:      {attrs.get('name')}  ({attrs.get('bundleId')})")
    print(f"App ID:   {asc.app_id()}")
    print()

    versions = asc.versions(limit=5)
    if not versions:
        print("No App Store versions found.")
        return

    print("Recent versions:")
    for version in versions:
        vattrs = version["attributes"]
        state = asc.state_of(version)
        marker = "*" if state in EDITABLE_STATES else " "
        print(f" {marker} {vattrs.get('versionString'):<8} {state}")

    editable = asc.editable_version()
    if editable is None:
        print("\nNo editable version — `ensure-version` would create one.")
        return

    print(f"\nEditable version {editable['attributes'].get('versionString')}:")
    build = asc.get(f"/appStoreVersions/{editable['id']}/build").get("data")
    if build:
        bnum = build.get("attributes", {}).get("version")
        print(f"  build attached:  {bnum}")
    else:
        print("  build attached:  (none)")

    locs = asc.get(
        f"/appStoreVersions/{editable['id']}/appStoreVersionLocalizations"
    ).get("data", [])
    for loc in locs:
        whats_new = (loc["attributes"].get("whatsNew") or "").strip()
        locale = loc["attributes"].get("locale")
        if whats_new:
            first = whats_new.splitlines()[0][:60]
            lines = len(whats_new.splitlines())
            print(f"  what's new [{locale}]: {first}…  ({lines} lines)")
        else:
            print(f"  what's new [{locale}]: (empty)")

    print("\nSubmit for review in App Store Connect when ready — by design, "
          "this script cannot.")


# --- CLI ---------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--dry-run",
            action="store_true",
            help="print the requests that would be sent, without sending them",
        )

    sub.add_parser("status", help="show the current App Store Connect state")

    p = sub.add_parser("ensure-version", help="get-or-create the editable version")
    p.add_argument("--version", required=True, help="marketing version, e.g. 1.5")
    p.add_argument(
        "--release-type",
        default="MANUAL",
        choices=["MANUAL", "AFTER_APPROVAL", "SCHEDULED"],
        help="MANUAL (default) = you press Release after approval",
    )
    add_common(p)

    p = sub.add_parser("set-notes", help="write the What's New text")
    p.add_argument("--notes-file", default="-", help="release-notes/ios-X.Y.json, or - for stdin")
    p.add_argument("--text", help="literal text (overrides --notes-file)")
    p.add_argument("--locale", action="append", help="restrict to locale(s), repeatable")
    add_common(p)

    p = sub.add_parser("upload", help="export + upload an .xcarchive")
    p.add_argument("--archive", required=True, help="path to the .xcarchive")
    add_common(p)

    p = sub.add_parser("wait-build", help="poll until the build is VALID")
    p.add_argument("--version", required=True)
    p.add_argument("--build", required=True)
    p.add_argument("--timeout", type=int, default=60, help="minutes (default 60)")

    p = sub.add_parser("attach-build", help="attach a processed build to the version")
    p.add_argument("--version", required=True)
    p.add_argument("--build", required=True)
    add_common(p)

    p = sub.add_parser("review-notes", help="set the App Review notes")
    p.add_argument("--text", required=True, help="notes text (reviewer sign-in link)")
    add_common(p)

    p = sub.add_parser(
        "stage",
        help="ensure-version + set-notes + upload + wait + attach (no submit)",
    )
    p.add_argument("--version", required=True)
    p.add_argument("--build", required=True)
    p.add_argument("--archive", help="skip the upload if omitted")
    p.add_argument("--notes-file", default="-")
    p.add_argument("--text", help="literal What's New text (overrides --notes-file)")
    p.add_argument("--locale", action="append")
    p.add_argument("--review-notes", help="App Review notes (reviewer sign-in link)")
    p.add_argument(
        "--release-type",
        default="MANUAL",
        choices=["MANUAL", "AFTER_APPROVAL", "SCHEDULED"],
    )
    p.add_argument("--timeout", type=int, default=60)
    add_common(p)

    return parser


def main() -> None:
    args = build_parser().parse_args()

    if load_dotenv is not None:
        load_dotenv(Path(__file__).resolve().parent.parent / ".env")

    config = AscConfig.from_env()
    asc = Asc(config, dry_run=getattr(args, "dry_run", False))

    if args.command == "status":
        print_status(asc)
        return

    if args.command == "ensure-version":
        version = asc.ensure_version(args.version, args.release_type)
        print(f"Version id: {version.get('id')}")
        return

    if args.command == "set-notes":
        editable = asc.editable_version()
        if editable is None:
            _fail("no editable version — run `ensure-version` first.")
        locales = asc.set_notes(editable["id"], load_notes(args), args.locale)
        print(f"What's New written to: {', '.join(locales)}")
        return

    if args.command == "upload":
        upload_archive(config, Path(args.archive).expanduser(), asc.dry_run)
        return

    if args.command == "wait-build":
        build = asc.wait_build(args.version, args.build, args.timeout)
        print(f"Build {args.version} ({args.build}) is VALID — id {build['id']}")
        return

    if args.command == "attach-build":
        editable = asc.editable_version()
        if editable is None:
            _fail("no editable version — run `ensure-version` first.")
        build = asc.find_build(args.version, args.build)
        if build is None:
            _fail(
                f"build {args.version} ({args.build}) not found. Has it finished "
                "processing? Try `wait-build`."
            )
        asc.attach_build(editable["id"], build["id"])
        print(f"Attached build {args.version} ({args.build}) to the version.")
        return

    if args.command == "review-notes":
        editable = asc.editable_version()
        if editable is None:
            _fail("no editable version — run `ensure-version` first.")
        asc.set_review_notes(editable["id"], args.text)
        print("App Review notes updated.")
        return

    if args.command == "stage":
        version = asc.ensure_version(args.version, args.release_type)
        version_id = version.get("id")

        notes = load_notes(args)
        locales = asc.set_notes(version_id, notes, args.locale)
        print(f"What's New written to: {', '.join(locales)}")

        if args.review_notes:
            asc.set_review_notes(version_id, args.review_notes)
            print("App Review notes updated.")

        if args.archive:
            upload_archive(config, Path(args.archive).expanduser(), asc.dry_run)

        if asc.dry_run:
            print("[dry-run] skipping build wait + attach")
            return

        print(f"Waiting for build {args.version} ({args.build}) to process…")
        build = asc.wait_build(args.version, args.build, args.timeout)
        asc.attach_build(version_id, build["id"])
        print(f"Attached build {args.version} ({args.build}).")

        print()
        print_status(asc)
        return


if __name__ == "__main__":
    main()
