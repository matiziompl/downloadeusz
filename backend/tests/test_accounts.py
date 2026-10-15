import os
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


