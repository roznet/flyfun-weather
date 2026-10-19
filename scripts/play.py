#!/usr/bin/env python3
"""Drive Google Play Console for an Android release — the repeatable part.

The Android counterpart of `scripts/asc.py`: uploading the signed `.aab` to a
track, writing release notes, moving a release between tracks, steering a
staged rollout, pushing the store listing (text and images) and pointing a
testing track at its Google Group of testers.

What it cannot do, because the Play Developer API has no endpoint for it:
create the app, fill in the App content declarations (data safety, content
rating, target audience, ads, app access), choose the store category, and apply
for production access after the closed test. Those stay in the web Console.
The first bundle can go through the API: FlyFun Forms 1.0 (1) did, on 2026-10-17.

Adapted from flyfun-forms' `scripts/play.py`; the two differ only in the default
package name. Fix a bug in one, port it to the other.

## How changes land

Every write goes through a Play *edit*, a transaction: nothing is visible until
the edit is committed. That makes `--dry-run` cheap and honest — it opens a
real edit, makes every change, asks Google to validate it, then deletes the
edit instead of committing. An `upload` dry run skips the bundle, so it can
only check the credentials, the track and the release notes.

## Production is opt-in

Writing to the production track publishes to users once Google's review
passes. Every command refuses to touch production without `--yes-production`,
the counterpart of `asc.py` having no `submit`: the owner types it, on purpose.
A new production release also needs `--rollout <fraction>` (staged) or `--full`.

Releases on an app that has never been published must be drafts (Google's
rule); pass `--status draft` until the first production release is out, then
roll those drafts out in the Console.

## Credentials (never committed — this is a public repo)

A Google Cloud service account with the Google Play Android Developer API
enabled, invited in Play Console → Users and permissions with release and
store listing access to this app. Its JSON key lives outside the repo:

  PLAY_SERVICE_ACCOUNT_JSON  path to the key (default
                             ~/.googleplaystore/private_keys/play-service-account.json)
  PLAY_PACKAGE_NAME          optional override (default aero.flyfun.weather)

The upload key that signs the bundle is separate: `app/android/keystore.properties`
(gitignored), a symlink to `~/.googleplaystore/keystore.properties`.

## Usage

    source venv/bin/activate
    pip install -e '.[release]'   # once: google-auth

    python3 scripts/play.py status

    # Upload the bundle Gradle built, with release-notes/android-<version>.txt:
    python3 scripts/play.py upload --track internal

    # Move what internal testers have to closed testing, then production:
    python3 scripts/play.py promote --from internal --to closed
    python3 scripts/play.py promote --from closed --to production --rollout 0.2 --yes-production
    python3 scripts/play.py rollout --fraction 0.5 --yes-production
    python3 scripts/play.py rollout --complete --yes-production
    python3 scripts/play.py rollout --halt --yes-production

    # Store listing from app/android/play/listing/, images too:
    python3 scripts/play.py listing --images

    # Who can install a testing track:
    python3 scripts/play.py testers --track closed --group flyfun-testers@googlegroups.com

Add `--dry-run` to any command that writes.

## Release notes

`release-notes/android-<versionName>.txt` is the default language's text (at
most 500 characters, Play's limit). A translation goes beside it as
`android-<versionName>.<lang>.txt`, e.g. `android-1.0.fr-FR.txt`.

## Listing files

`app/android/play/listing/<dir>/` holds `title.txt` (≤30), `short_description.txt`
(≤80) and `full_description.txt` (≤4000). `<dir>` is a Play language code
(`fr-FR`), or `default` for the app's default language, whatever it is. Images
go in the `default` directory only, and other languages show them:
`icon.png` (512×512), `feature-graphic.png` (1024×500), `phone/*.png`,
`tablet/*.png` (10-inch), each set replaced whole on upload.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, NoReturn

import requests

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - dotenv is a dev dependency
    load_dotenv = None

REPO = Path(__file__).resolve().parent.parent
ANDROID = REPO / "app" / "android"
GRADLE_FILE = ANDROID / "app" / "build.gradle.kts"
DEFAULT_AAB = ANDROID / "app" / "build" / "outputs" / "bundle" / "release" / "app-release.aab"
LISTING_DIR = ANDROID / "play" / "listing"
NOTES_DIR = REPO / "release-notes"

DEFAULT_PACKAGE = "aero.flyfun.weather"
DEFAULT_KEY = Path("~/.googleplaystore/private_keys/play-service-account.json")

API_ROOT = "https://androidpublisher.googleapis.com/androidpublisher/v3/applications"
UPLOAD_ROOT = "https://androidpublisher.googleapis.com/upload/androidpublisher/v3/applications"
SCOPE = "https://www.googleapis.com/auth/androidpublisher"

# Console names → API track names. "Closed testing" is the API's `alpha`,
# "Open testing" its `beta`.
TRACKS = {"internal": "internal", "closed": "alpha", "alpha": "alpha",
          "open": "beta", "beta": "beta", "production": "production"}
TRACK_LABELS = {"internal": "internal", "alpha": "closed", "beta": "open", "production": "production"}

NOTES_LIMIT = 500
LISTING_LIMITS = {"title.txt": 30, "short_description.txt": 80, "full_description.txt": 4000}
IMAGE_SETS = {"phone": "phoneScreenshots", "tablet": "tenInchScreenshots"}


def _fail(msg: str) -> NoReturn:
    sys.exit(f"error: {msg}")


def track_name(name: str) -> str:
    if name not in TRACKS:
        _fail(f"unknown track {name!r}; one of {', '.join(sorted(TRACKS))}")
    return TRACKS[name]


def gradle_version() -> tuple[str, int]:
    """versionName and versionCode as `build.gradle.kts` sets them."""
    text = GRADLE_FILE.read_text()
    name = re.search(r'versionName\s*=\s*"([^"]+)"', text)
    code = re.search(r"versionCode\s*=\s*(\d+)", text)
    if not name or not code:
        _fail(f"could not read versionName/versionCode from {GRADLE_FILE}")
    return name.group(1), int(code.group(1))


# --- API ---------------------------------------------------------------------


class Play:
    def __init__(self, package: str, key_path: Path, dry_run: bool = False):
        try:
            from google.auth.transport.requests import AuthorizedSession
            from google.oauth2 import service_account
        except ImportError:
            _fail("google-auth is missing: pip install -e '.[release]'")
        if not key_path.is_file():
            _fail(
                f"service account key not found: {key_path}\n"
                "Create one (Google Cloud → IAM → Service accounts → Keys), invite its "
                "email in Play Console → Users and permissions, and save the JSON there "
                "or set PLAY_SERVICE_ACCOUNT_JSON."
            )
        credentials = service_account.Credentials.from_service_account_file(str(key_path), scopes=[SCOPE])
        self.session = AuthorizedSession(credentials)
        self.package = package
        self.dry_run = dry_run
        self.edit_id: str | None = None
        self.read_only = False  # set by a command that only reads: discard the edit quietly

    def _url(self, path: str, upload: bool = False) -> str:
        root = UPLOAD_ROOT if upload else API_ROOT
        return f"{root}/{self.package}{path}"

    def request(self, method: str, path: str, upload: bool = False, **kwargs: Any) -> dict:
        response = self.session.request(method, self._url(path, upload), timeout=600, **kwargs)
        if not response.ok:
            _fail(f"{method} {path}: HTTP {response.status_code}\n{explain(response)}")
        return response.json() if response.content else {}

    def edit_path(self, path: str = "") -> str:
        assert self.edit_id, "no open edit"
        return f"/edits/{self.edit_id}{path}"

    @contextmanager
    def edit(self, send_for_review: bool = True) -> Iterator["Play"]:
        """Opens an edit; commits it on success, or validates and discards it in a dry run."""
        self.edit_id = self.request("POST", "/edits")["id"]
        try:
            yield self
        except BaseException:
            self.session.delete(self._url(self.edit_path()), timeout=60)
            self.edit_id = None
            raise
        if self.read_only:
            self.request("DELETE", self.edit_path())
        elif self.dry_run:
            self.request("POST", self.edit_path(":validate"))
            self.request("DELETE", self.edit_path())
            print("[dry-run] Google validated the changes; edit discarded, nothing published.")
        else:
            params = {} if send_for_review else {"changesNotSentForReview": "true"}
            self.request("POST", self.edit_path(":commit"), params=params)
            print("Committed." + ("" if send_for_review else " Send the changes for review in the Console."))
        self.edit_id = None

    # Reads inside an edit

    def details(self) -> dict:
        return self.request("GET", self.edit_path("/details"))

    def tracks(self) -> list[dict]:
        return self.request("GET", self.edit_path("/tracks")).get("tracks", [])

    def track(self, name: str) -> dict:
        return self.request("GET", self.edit_path(f"/tracks/{name}"))

    # Writes inside an edit

    def set_track(self, name: str, releases: list[dict]) -> None:
        self.request("PUT", self.edit_path(f"/tracks/{name}"), json={"track": name, "releases": releases})

    def upload_bundle(self, aab: Path) -> int:
        """Resumable upload: a multi-MB bundle is past what a simple upload is meant for."""
        start = self.session.post(
            self._url(self.edit_path("/bundles"), upload=True),
            params={"uploadType": "resumable"},
            headers={"X-Upload-Content-Type": "application/octet-stream",
                     "X-Upload-Content-Length": str(aab.stat().st_size)},
            timeout=60,
        )
        if not start.ok:
            _fail(f"starting the bundle upload: HTTP {start.status_code}\n{explain(start)}")
        with aab.open("rb") as body:
            done = self.session.put(start.headers["Location"], data=body,
                                    headers={"Content-Type": "application/octet-stream"}, timeout=1800)
        if not done.ok:
            _fail(f"uploading the bundle: HTTP {done.status_code}\n{explain(done)}")
        return int(done.json()["versionCode"])


def explain(response: requests.Response) -> str:
    """Google's error message, with a hint for the ones this script can foresee."""
    try:
        message = response.json().get("error", {}).get("message", response.text)
    except ValueError:
        message = response.text
    hints = {
        "Only releases with status draft": "The app has never been published: pass --status draft.",
        "changesNotSentForReview": "Re-run with --no-review, then press Send for review in the Console.",
        "APK specifies a version code that has already been used": "Bump versionCode in build.gradle.kts and rebuild.",
        "The caller does not have permission": "Check the service account's permissions in Play Console → Users and permissions.",
        "Package not found": "Create the app in Play Console first.",
    }
    extra = [hint for needle, hint in hints.items() if needle in message]
    return message + ("\nhint: " + " ".join(extra) if extra else "")


# --- Release notes -------------------------------------------------------------


def load_notes(version_name: str, default_language: str, notes_file: str | None) -> list[dict]:
    """Release notes for every language that has a file, default language first."""
    path = Path(notes_file) if notes_file else NOTES_DIR / f"android-{version_name}.txt"
    if not path.is_file():
        _fail(f"release notes not found: {path} (or pass --notes-file)")
    notes = [(default_language, path.read_text().strip())]
    for extra in sorted(path.parent.glob(f"{path.stem}.*.txt")):
        notes.append((extra.name[len(path.stem) + 1:-len(".txt")], extra.read_text().strip()))
    for language, text in notes:
        if len(text) > NOTES_LIMIT:
            _fail(f"{language} release notes are {len(text)} characters; Play allows {NOTES_LIMIT}.")
    return [{"language": language, "text": text} for language, text in notes]


# --- Commands ------------------------------------------------------------------


def guard_production(track: str, args: argparse.Namespace) -> None:
    if track == "production" and not args.yes_production:
        _fail("this writes to production, which publishes to users after review. Add --yes-production.")
    # A new production release is staged unless the owner asks for everyone.
    if (track == "production" and hasattr(args, "rollout") and getattr(args, "status", None) != "draft"
            and args.rollout is None and not args.full):
        _fail("a production release goes out as a staged rollout: add --rollout 0.2 (or --full for 100%).")


def release_status(args: argparse.Namespace, rollout: float | None) -> dict:
    if args.status == "draft":
        return {"status": "draft"}
    if rollout is not None and rollout < 1:
        return {"status": "inProgress", "userFraction": rollout}
    return {"status": "completed"}


def describe(release: dict) -> str:
    codes = ", ".join(release.get("versionCodes", [])) or "-"
    fraction = f" {release['userFraction']:.0%}" if "userFraction" in release else ""
    return f"{release.get('name', '?')} [{codes}] {release.get('status')}{fraction}"


def cmd_status(play: Play, args: argparse.Namespace) -> None:
    version_name, version_code = gradle_version()
    with play.edit():
        details = play.details()
        print(f"{play.package} — default language {details.get('defaultLanguage')}, "
              f"contact {details.get('contactEmail', '-')}")
        print(f"build.gradle.kts: {version_name} ({version_code})")
        for track in play.tracks():
            label = TRACK_LABELS.get(track["track"], track["track"])
            releases = track.get("releases", [])
            print(f"  {label:<10} " + ("; ".join(describe(r) for r in releases) if releases else "(empty)"))
        play.read_only = True


def cmd_upload(play: Play, args: argparse.Namespace) -> None:
    track = track_name(args.track)
    guard_production(track, args)
    version_name, version_code = gradle_version()
    aab = Path(args.aab).expanduser()
    if not aab.is_file():
        _fail(f"bundle not found: {aab}. Build it: cd app/android && ./gradlew :app:bundleRelease")
    with play.edit(send_for_review=not args.no_review):
        notes = load_notes(version_name, play.details()["defaultLanguage"], args.notes_file)
        if play.dry_run:
            print(f"[dry-run] would upload {aab.name} ({aab.stat().st_size / 1e6:.0f} MB)")
            uploaded = version_code
        else:
            print(f"Uploading {aab.name} ({aab.stat().st_size / 1e6:.0f} MB)…")
            uploaded = play.upload_bundle(aab)
            if uploaded != version_code:
                _fail(f"the bundle is versionCode {uploaded}, build.gradle.kts says {version_code}: rebuild.")
            print(f"Uploaded versionCode {uploaded}.")
        release = {"name": f"{version_name} ({uploaded})", "versionCodes": [str(uploaded)],
                   "releaseNotes": notes, **release_status(args, args.rollout)}
        if play.dry_run:
            # The bundle was not uploaded, so the track cannot point at it.
            print(f"[dry-run] would set {TRACK_LABELS[track]}:")
        else:
            play.set_track(track, [release])
        print(f"{TRACK_LABELS[track]}: {describe(release)}, notes in {', '.join(n['language'] for n in notes)}")


def cmd_promote(play: Play, args: argparse.Namespace) -> None:
    source, target = track_name(args.source), track_name(args.target)
    guard_production(target, args)
    with play.edit(send_for_review=not args.no_review):
        releases = [r for r in play.track(source).get("releases", []) if r.get("status") != "halted"]
        if not releases:
            _fail(f"nothing on {TRACK_LABELS[source]} to promote.")
        # The newest release on the source track is what its testers have.
        latest = max(releases, key=lambda r: max(int(c) for c in r.get("versionCodes", ["0"])))
        release = {key: latest[key] for key in ("name", "versionCodes", "releaseNotes") if key in latest}
        release.update(release_status(args, args.rollout))
        play.set_track(target, [release])
        print(f"{TRACK_LABELS[source]} → {TRACK_LABELS[target]}: {describe(release)}")


def cmd_rollout(play: Play, args: argparse.Namespace) -> None:
    track = track_name(args.track)
    guard_production(track, args)
    with play.edit(send_for_review=not args.no_review):
        releases = play.track(track).get("releases", [])
        staged = [r for r in releases if r.get("status") in ("inProgress", "halted")]
        if not staged:
            _fail(f"no staged rollout on {TRACK_LABELS[track]}.")
        release = dict(staged[0])
        if args.complete:
            release.pop("userFraction", None)
            release["status"] = "completed"
        elif args.halt:
            release["status"] = "halted"
        else:
            if not 0 < args.fraction < 1:
                _fail("--fraction is between 0 and 1, e.g. 0.5; use --complete for everyone.")
            release.update(status="inProgress", userFraction=args.fraction)
        # While staged, keep the completed release that serves everyone else;
        # once complete, the new release replaces it.
        others = [] if args.complete else [r for r in releases if r is not staged[0] and r.get("status") == "completed"]
        play.set_track(track, [release] + others)
        print(f"{TRACK_LABELS[track]}: {describe(release)}")


def cmd_listing(play: Play, args: argparse.Namespace) -> None:
    if not LISTING_DIR.is_dir():
        _fail(f"no listing directory: {LISTING_DIR}")
    with play.edit(send_for_review=not args.no_review):
        default_language = play.details()["defaultLanguage"]
        for directory in sorted(p for p in LISTING_DIR.iterdir() if p.is_dir()):
            language = default_language if directory.name == "default" else directory.name
            texts = {}
            for name, limit in LISTING_LIMITS.items():
                path = directory / name
                if not path.is_file():
                    _fail(f"{path} is missing")
                text = path.read_text().strip()
                if len(text) > limit:
                    _fail(f"{path} is {len(text)} characters; Play allows {limit}.")
                texts[name] = text
            play.request("PUT", play.edit_path(f"/listings/{language}"), json={
                "language": language,
                "title": texts["title.txt"],
                "shortDescription": texts["short_description.txt"],
                "fullDescription": texts["full_description.txt"],
            })
            print(f"{language}: text")
            if args.images and directory.name == "default":
                upload_images(play, language, directory)


def upload_images(play: Play, language: str, directory: Path) -> None:
    """Replaces each image set whole, so a removed screenshot goes too."""
    sets: list[tuple[str, list[Path]]] = [
        ("icon", [directory / "icon.png"]),
        ("featureGraphic", [directory / "feature-graphic.png"]),
    ]
    for folder, image_type in IMAGE_SETS.items():
        sets.append((image_type, sorted((directory / folder).glob("*.png"))))
    for image_type, paths in sets:
        paths = [p for p in paths if p.is_file()]
        if not paths:
            continue
        base = play.edit_path(f"/listings/{language}/{image_type}")
        play.request("DELETE", base)
        for path in paths:
            play.request("POST", base, upload=True, params={"uploadType": "media"},
                         data=path.read_bytes(), headers={"Content-Type": "image/png"})
        print(f"{language}: {image_type} × {len(paths)}")


def cmd_testers(play: Play, args: argparse.Namespace) -> None:
    track = track_name(args.track)
    if track == "production":
        _fail("production has no tester list.")
    with play.edit(send_for_review=not args.no_review):
        if args.group:
            play.request("PUT", play.edit_path(f"/testers/{track}"), json={"googleGroups": args.group})
        groups = play.request("GET", play.edit_path(f"/testers/{track}")).get("googleGroups", [])
        print(f"{TRACK_LABELS[track]} testers: {', '.join(groups) or '(none)'}")
        play.read_only = not args.group


# --- CLI -----------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--dry-run", action="store_true",
                       help="make the changes in an edit, have Google validate them, then discard it")
        p.add_argument("--no-review", action="store_true",
                       help="commit without sending for review (press Send for review in the Console)")
        p.add_argument("--yes-production", action="store_true", help="allow writing to the production track")

    def add_release(p: argparse.ArgumentParser) -> None:
        p.add_argument("--status", choices=["live", "draft"], default="live",
                       help="draft until the app's first production release (Google's rule)")
        p.add_argument("--rollout", type=float, help="staged rollout fraction, e.g. 0.2 (production)")
        p.add_argument("--full", action="store_true", help="production: release to everyone at once, not staged")

    sub.add_parser("status", help="tracks and their releases")

    p = sub.add_parser("upload", help="upload the .aab to a track with its release notes")
    p.add_argument("--track", default="internal", help="internal, closed, open or production")
    p.add_argument("--aab", default=str(DEFAULT_AAB))
    p.add_argument("--notes-file", help="default: release-notes/android-<versionName>.txt")
    add_release(p)
    add_common(p)

    p = sub.add_parser("promote", help="put a track's newest release on another track")
    p.add_argument("--from", dest="source", required=True)
    p.add_argument("--to", dest="target", required=True)
    add_release(p)
    add_common(p)

    p = sub.add_parser("rollout", help="change a staged rollout")
    p.add_argument("--track", default="production")
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--fraction", type=float, help="new fraction of users, e.g. 0.5")
    group.add_argument("--complete", action="store_true", help="release to everyone")
    group.add_argument("--halt", action="store_true", help="stop the rollout")
    add_common(p)

    p = sub.add_parser("listing", help="push the store listing from app/android/play/listing")
    p.add_argument("--images", action="store_true", help="also replace icon, feature graphic and screenshots")
    add_common(p)

    p = sub.add_parser("testers", help="show or set a testing track's Google Group")
    p.add_argument("--track", default="closed")
    p.add_argument("--group", action="append", help="Google Group email, repeatable; omit to show")
    add_common(p)
    return parser


COMMANDS = {"status": cmd_status, "upload": cmd_upload, "promote": cmd_promote,
            "rollout": cmd_rollout, "listing": cmd_listing, "testers": cmd_testers}


def main() -> None:
    args = build_parser().parse_args()
    if load_dotenv is not None:
        load_dotenv(REPO / ".env")
    key = Path(os.environ.get("PLAY_SERVICE_ACCOUNT_JSON") or DEFAULT_KEY).expanduser()
    play = Play(os.environ.get("PLAY_PACKAGE_NAME") or DEFAULT_PACKAGE, key, getattr(args, "dry_run", False))
    COMMANDS[args.command](play, args)


if __name__ == "__main__":
    main()
