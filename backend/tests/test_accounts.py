import os
import importlib.util
from pathlib import Path
import pytest
from backend.accounts import (
    parse_accounts_text,
    get_navidrome_account_by_id,
    get_active_slskd_account,
    get_accounts_file_path,
)

def test_parse_navidrome_accounts_spec():
    raw_content = """
Server: "http://test.pl"
user: "test_user"
password: "test"
user: "test_user2"
password: "test2"
"""
    accounts = parse_accounts_text(raw_content)
    assert len(accounts) == 2

    assert accounts[0]["server"] == "http://test.pl"
    assert accounts[0]["username"] == "test_user"
    assert accounts[0]["password"] == "test"
    assert accounts[0]["id"] == "http://test.pl::test_user"

    assert accounts[1]["server"] == "http://test.pl"
    assert accounts[1]["username"] == "test_user2"
    assert accounts[1]["password"] == "test2"
    assert accounts[1]["id"] == "http://test.pl::test_user2"


def test_parse_slskd_accounts_spec():
    raw_content = """
Server: "http://test.pl"
user: "test_user"
password: "test"
"""
    accounts = parse_accounts_text(raw_content)
    assert len(accounts) == 1
    assert accounts[0]["server"] == "http://test.pl"
    assert accounts[0]["username"] == "test_user"
    assert accounts[0]["password"] == "test"


def test_parse_accounts_with_comments_and_multiple_servers():
    raw_content = """
# Gowny serwer Navidrome
Server: "http://navi.local:4533"
user: "admin"
password: "secret_admin_pass"

# Drugi serwer
Server: http://backup.local:4533
user: user_backup
password: backup_pass
"""
    accounts = parse_accounts_text(raw_content)
    assert len(accounts) == 2
    assert accounts[0]["server"] == "http://navi.local:4533"
    assert accounts[0]["username"] == "admin"
    assert accounts[0]["password"] == "secret_admin_pass"

    assert accounts[1]["server"] == "http://backup.local:4533"
    assert accounts[1]["username"] == "user_backup"
    assert accounts[1]["password"] == "backup_pass"


def test_parse_accounts_empty_or_malformed():
    assert parse_accounts_text("") == []
    assert parse_accounts_text("   \n# nothing here\n") == []
    assert parse_accounts_text("invalid line without colon") == []


def test_file_reading_navidrome_and_slskd(tmp_path):
    navi_file = tmp_path / "navidrome_accounts.txt"
    navi_file.write_text("""
Server: "http://my-navi:4533"
user: "alice"
password: "alice_password"
user: "bob"
password: "bob_password"
""", encoding="utf-8")

    slskd_file = tmp_path / "slskd_accounts.txt"
    slskd_file.write_text("""
Server: "http://my-slskd:5030"
user: "slskd_user"
password: "slskd_pass"
""", encoding="utf-8")

    import backend.accounts as acc_mod
    navi_accs = acc_mod.get_navidrome_accounts(config_dir=str(tmp_path))
    assert len(navi_accs) == 2
    assert navi_accs[0]["username"] == "alice"
    assert navi_accs[1]["username"] == "bob"

    account_alice = acc_mod.get_navidrome_account_by_id("http://my-navi:4533::alice", config_dir=str(tmp_path))
    assert account_alice is not None
    assert account_alice["password"] == "alice_password"

    account_bob = acc_mod.get_navidrome_account_by_id("bob", config_dir=str(tmp_path))
    assert account_bob is not None
    assert account_bob["password"] == "bob_password"

    slskd_acc = acc_mod.get_active_slskd_account(config_dir=str(tmp_path))
    assert slskd_acc is not None
    assert slskd_acc["server"] == "http://my-slskd:5030"
    assert slskd_acc["username"] == "slskd_user"
    assert slskd_acc["password"] == "slskd_pass"


def test_parse_accounts_quotes_and_special_chars():
    raw_content = """
Server: "http://test.pl"
user: "complex'user"
password: "pass'word"
user: 'single_quote_user'
password: 'p@ss"w0rd!'
user: unquoted_user
password: simple_pass
"""
    accounts = parse_accounts_text(raw_content)
    assert len(accounts) == 3
    assert accounts[0]["username"] == "complex'user"
    assert accounts[0]["password"] == "pass'word"
    assert accounts[1]["username"] == "single_quote_user"
    assert accounts[1]["password"] == 'p@ss"w0rd!'
    assert accounts[2]["username"] == "unquoted_user"
    assert accounts[2]["password"] == "simple_pass"


def test_default_account_directory_is_project_root_independent_of_cwd_and_spotdl(tmp_path, monkeypatch):
    from backend import accounts as acc_mod

    project = tmp_path / "project"
    backend = project / "backend"
    backend.mkdir(parents=True)
    module_path = backend / "accounts.py"
    module_path.write_text(Path(acc_mod.__file__).read_text(encoding="utf-8"), encoding="utf-8")
    stale_config, other_cwd = tmp_path / "old-spotdl", tmp_path / "other-cwd"
    stale_config.mkdir()
    other_cwd.mkdir()
    for directory, user in ((project, "project-user"), (stale_config, "stale-user"), (other_cwd, "cwd-user")):
        for filename in ("navidrome_accounts.txt", "slskd_accounts.txt"):
            (directory / filename).write_text(f'Server: "http://{user}"\nuser: "{user}"\npassword: "secret"\n')
    monkeypatch.delenv("ACCOUNTS_DIR", raising=False)
    monkeypatch.setenv("SPOTDL_CONFIG_DIR", str(stale_config))
    monkeypatch.chdir(other_cwd)
    spec = importlib.util.spec_from_file_location("isolated_accounts", module_path)
    isolated_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(isolated_module)

    assert isolated_module.get_navidrome_accounts()[0]["username"] == "project-user"
    assert isolated_module.get_active_slskd_account()["username"] == "project-user"
    assert Path(isolated_module.get_accounts_file_path("navidrome_accounts.txt")) == project / "navidrome_accounts.txt"


def test_account_directory_environment_override_is_respected(tmp_path, monkeypatch):
    from backend import accounts as acc_mod

    override = tmp_path / "configured-accounts"
    override.mkdir()
    (override / "navidrome_accounts.txt").write_text('Server: "http://navi"\nuser: "override-user"\npassword: "secret"\n')
    monkeypatch.setenv("ACCOUNTS_DIR", str(override))
    monkeypatch.setenv("SPOTDL_CONFIG_DIR", str(tmp_path / "unrelated-spotdl"))
    monkeypatch.chdir(tmp_path)
    spec = importlib.util.spec_from_file_location("isolated_accounts_override", acc_mod.__file__)
    isolated_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(isolated_module)

    assert [account["username"] for account in isolated_module.get_navidrome_accounts()] == ["override-user"]


@pytest.mark.parametrize("filename,reader", [
    ("navidrome_accounts.txt", "get_navidrome_accounts"),
    ("slskd_accounts.txt", "get_slskd_accounts"),
])
@pytest.mark.parametrize("explicit_directory", [True, False])
def test_missing_accounts_in_selected_directory_never_fall_back_to_cwd(tmp_path, monkeypatch, filename, reader, explicit_directory):
    from backend import accounts as acc_mod

    selected, cwd, stale = tmp_path / "selected", tmp_path / "cwd", tmp_path / "old-spotdl"
    selected.mkdir()
    cwd.mkdir()
    stale.mkdir()
    (cwd / filename).write_text('Server: "http://wrong"\nuser: "wrong-user"\npassword: "old-secret"\n')
    (stale / filename).write_text('Server: "http://stale"\nuser: "stale-user"\npassword: "old-secret"\n')
    monkeypatch.setattr(acc_mod, "ACCOUNTS_DIR", str(selected), raising=False)
    monkeypatch.setattr(acc_mod, "SPOTDL_CONFIG_DIR", str(stale), raising=False)
    monkeypatch.chdir(cwd)
    arguments = {"config_dir": str(selected)} if explicit_directory else {}

    assert getattr(acc_mod, reader)(**arguments) == []
    assert Path(acc_mod.get_accounts_file_path(filename, **arguments)) == selected / filename


def test_account_changes_are_reloaded_on_each_read(tmp_path):
    from backend import accounts as acc_mod

    account_file = tmp_path / "navidrome_accounts.txt"
    account_file.write_text('Server: "http://navi"\nuser: "listener"\npassword: "first-secret"\n')
    assert acc_mod.get_navidrome_account_by_id("listener", config_dir=str(tmp_path))["password"] == "first-secret"
    account_file.write_text('Server: "http://navi"\nuser: "listener"\npassword: "updated-secret"\n')
    assert acc_mod.get_navidrome_account_by_id("listener", config_dir=str(tmp_path))["password"] == "updated-secret"


