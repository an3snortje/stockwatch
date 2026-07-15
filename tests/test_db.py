from pathlib import Path

from stockwatch import db


def test_find_env_prefers_cwd(tmp_path, monkeypatch):
    """When a .env is discoverable from the working directory, use it."""
    (tmp_path / ".env").write_text("MSSQL_SERVER=cwd\n")
    monkeypatch.chdir(tmp_path)
    assert db._find_env() == str(tmp_path / ".env")


def test_find_env_falls_back_to_module_parents(tmp_path, monkeypatch):
    """With no .env reachable from CWD (as under Task Scheduler in System32),
    fall back to walking up from the installed module to the checkout root."""
    monkeypatch.setattr(db, "find_dotenv", lambda *a, **k: "")
    (tmp_path / "src" / "stockwatch").mkdir(parents=True)
    (tmp_path / ".env").write_text("MSSQL_SERVER=root\n")
    module = tmp_path / "src" / "stockwatch" / "db.py"
    assert db._find_env(start=module) == str(tmp_path / ".env")


def test_find_env_returns_none_when_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "find_dotenv", lambda *a, **k: "")
    module = tmp_path / "src" / "stockwatch" / "db.py"
    (tmp_path / "src" / "stockwatch").mkdir(parents=True)
    assert db._find_env(start=module) is None
