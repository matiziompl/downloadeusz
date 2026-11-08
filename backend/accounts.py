import os
import re
from pathlib import Path

ACCOUNTS_DIR = os.getenv("ACCOUNTS_DIR") or str(Path(__file__).resolve().parent.parent)

def parse_accounts_text(content: str) -> list[dict]:
    """
    Parses account definitions from text.
    Format example:
        Server: "http://test.pl"
        user: "test_user"
        password: "test"
        user: "test_user2"
        password: "test2"
    """
    accounts = []
    current_server = ""
    current_user = None

    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        if ":" in line:
            key, val = line.split(":", 1)
            key = key.strip().lower()
            val = val.strip()
            if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")) and len(val) >= 2:
                val = val[1:-1]

            if key == "server":
                current_server = val
            elif key == "user":
                current_user = val
            elif key == "password":
                if current_user is not None:
                    accounts.append({
                        "id": f"{current_server}::{current_user}",
                        "server": current_server,
                        "username": current_user,
                        "password": val
                    })
                    current_user = None

    return accounts


def get_accounts_file_path(filename: str, config_dir: str | None = None) -> str:
    """Resolve only the chosen account directory, never unrelated working/config dirs."""
    return os.path.join(config_dir or ACCOUNTS_DIR, filename)


def get_navidrome_accounts(config_dir: str | None = None) -> list[dict]:
    """Reads and parses navidrome_accounts.txt dynamically on demand."""
    path = get_accounts_file_path("navidrome_accounts.txt", config_dir)
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            return parse_accounts_text(f.read())
    except Exception as e:
        print(f"[Accounts] Error reading {path}: {e}")
        return []


def get_slskd_accounts(config_dir: str | None = None) -> list[dict]:
    """Reads and parses slskd_accounts.txt dynamically on demand."""
    path = get_accounts_file_path("slskd_accounts.txt", config_dir)
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            return parse_accounts_text(f.read())
    except Exception as e:
        print(f"[Accounts] Error reading {path}: {e}")
        return []



def get_navidrome_account_by_id(account_id: str, config_dir: str | None = None) -> dict | None:
    """Finds a Navidrome account matching account_id (or username/server)."""
    accounts = get_navidrome_accounts(config_dir)
    for acc in accounts:
        if acc.get("id") == account_id:
            return acc
        if acc.get("username") == account_id:
            return acc
    return None


def get_active_slskd_account(config_dir: str | None = None) -> dict | None:
    """Returns the first configured slskd account, or None."""
    accounts = get_slskd_accounts(config_dir)
    return accounts[0] if accounts else None
