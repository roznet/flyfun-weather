"""weatherbrief.envfiles: `.env` (config) then `.env.secrets` (sops-managed secrets)."""
from __future__ import annotations

import dotenv
import dotenv.main
import pytest

from weatherbrief import envfiles

KEYS = ("WB_TEST_CONFIG", "WB_TEST_SECRET", "WB_TEST_BOTH", "WB_TEST_PRESET")


@pytest.fixture(autouse=True)
def real_dotenv(monkeypatch):
    # tests/conftest.py stubs dotenv.load_dotenv to a no-op for the whole session;
    # these tests are about loading, so put the real one back (monkeypatch undoes it).
    monkeypatch.setattr(dotenv, "load_dotenv", dotenv.main.load_dotenv)
    for key in KEYS:
        monkeypatch.delenv(key, raising=False)


def write(root, name, text):
    (root / name).write_text(text)


def test_loads_config_then_secrets(tmp_path, monkeypatch):
    write(tmp_path, ".env", "WB_TEST_CONFIG=plain\nWB_TEST_BOTH=from-env\n")
    write(tmp_path, ".env.secrets", "WB_TEST_SECRET=hidden\nWB_TEST_BOTH=from-secrets\n")
    assert envfiles.load_env(tmp_path) is True
    import os
    assert os.environ["WB_TEST_CONFIG"] == "plain"
    assert os.environ["WB_TEST_SECRET"] == "hidden"
    # a key in both files (shouldn't happen): .env is loaded first and wins
    assert os.environ["WB_TEST_BOTH"] == "from-env"


def test_environment_wins(tmp_path, monkeypatch):
    import os
    monkeypatch.setenv("WB_TEST_PRESET", "from-environment")
    write(tmp_path, ".env", "WB_TEST_PRESET=from-env\n")
    write(tmp_path, ".env.secrets", "WB_TEST_PRESET=from-secrets\n")
    envfiles.load_env(tmp_path)
    assert os.environ["WB_TEST_PRESET"] == "from-environment"


def test_override_true_lets_the_files_win(tmp_path, monkeypatch):
    import os
    monkeypatch.setenv("WB_TEST_PRESET", "from-environment")
    monkeypatch.setenv("WB_TEST_SECRET", "from-environment")
    write(tmp_path, ".env", "WB_TEST_PRESET=from-env\n")
    write(tmp_path, ".env.secrets", "WB_TEST_SECRET=from-secrets\n")
    envfiles.load_env(tmp_path, override=True)
    assert os.environ["WB_TEST_PRESET"] == "from-env"
    assert os.environ["WB_TEST_SECRET"] == "from-secrets"


def test_missing_secrets_file_is_fine(tmp_path):
    import os
    write(tmp_path, ".env", "WB_TEST_CONFIG=plain\n")
    assert envfiles.env_paths(tmp_path) == [tmp_path / ".env"]
    assert envfiles.load_env(tmp_path) is True
    assert os.environ["WB_TEST_CONFIG"] == "plain"
    assert "WB_TEST_SECRET" not in os.environ


def test_secrets_only_and_no_files(tmp_path):
    import os
    assert envfiles.load_env(tmp_path) is False
    write(tmp_path, ".env.secrets", "WB_TEST_SECRET=hidden\n")
    assert envfiles.env_paths(tmp_path) == [tmp_path / ".env.secrets"]
    envfiles.load_env(tmp_path)
    assert os.environ["WB_TEST_SECRET"] == "hidden"


def test_goes_through_the_module_attribute(tmp_path, monkeypatch):
    # what the conftest stub and mock.patch("dotenv.load_dotenv") rely on
    calls = []
    monkeypatch.setattr(dotenv, "load_dotenv", lambda path, override=False: calls.append(path) or True)
    write(tmp_path, ".env", "X=1\n")
    write(tmp_path, ".env.secrets", "Y=2\n")
    envfiles.load_env(tmp_path)
    assert calls == [tmp_path / ".env", tmp_path / ".env.secrets"]


def test_default_root_is_the_checkout():
    # in a checkout (editable install) the package sits under src/ of the repo root,
    # where .env lives; same directory find_dotenv() resolved for load_dotenv()
    root = envfiles.default_root()
    if root is None:
        pytest.skip("no .env/.env.secrets above the package (CI / installed copy)")
    assert (root / "src" / "weatherbrief" / "envfiles.py").is_file()
