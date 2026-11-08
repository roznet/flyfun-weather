"""The data root is decided in one place: ``weatherbrief.paths.data_dir``."""

from __future__ import annotations

import re
from pathlib import Path

from weatherbrief import paths

SRC = Path(__file__).resolve().parents[1] / "src" / "weatherbrief"

# os.environ.get("DATA_DIR"...), os.environ["DATA_DIR"], os.getenv("DATA_DIR"...),
# including a call split over lines.
_ENV_READ = re.compile(
    r"""(environ\s*\.\s*get|getenv)\s*\(\s*["']DATA_DIR["']|environ\s*\[\s*["']DATA_DIR["']"""
)


def test_set_value_wins_and_is_absolute(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    assert paths.data_dir() == tmp_path


def test_relative_value_is_made_absolute(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATA_DIR", "somewhere")
    assert paths.data_dir() == tmp_path / "somewhere"


def test_unset_falls_back_to_checkout_not_cwd(monkeypatch, tmp_path):
    monkeypatch.delenv("DATA_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    root = paths.checkout_root()
    assert root is not None and (root / "pyproject.toml").is_file()
    assert paths.data_dir() == root / "data"


def test_value_set_after_import_is_seen(monkeypatch, tmp_path):
    """The bug this module replaces: snapshots.py read DATA_DIR at import, before
    create_app() loaded .env, and the pipeline wrote under the cwd's ``data/``."""
    from weatherbrief.storage import snapshots

    assert not hasattr(snapshots, "DEFAULT_DATA_DIR")
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "late"))
    assert paths.data_dir() == tmp_path / "late"


def test_dotenv_loaded_once_per_process(monkeypatch):
    import weatherbrief.envfiles as envfiles

    calls = []
    monkeypatch.setattr(envfiles, "load_env", lambda *a, **k: calls.append(1) or False)
    monkeypatch.setattr(paths, "_env_loaded", False)
    paths.data_dir()
    paths.data_dir()
    assert calls == [1]


def test_nothing_else_reads_data_dir_from_the_environment():
    offenders = []
    for f in sorted(SRC.rglob("*.py")):
        if f.name == "paths.py" and f.parent == SRC:
            continue
        text = f.read_text()
        for m in _ENV_READ.finditer(text):
            line = text.count("\n", 0, m.start()) + 1
            offenders.append(f"{f.relative_to(SRC.parent)}:{line}")
    assert not offenders, (
        "read DATA_DIR through weatherbrief.paths.data_dir(), not the environment: "
        + ", ".join(offenders)
    )
