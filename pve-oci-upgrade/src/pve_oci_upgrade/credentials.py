"""Credentials file management for PVE API authentication."""

import configparser
import os
from pathlib import Path

PVE_CREDENTIALS_PATH = Path.home() / ".pve" / "credentials"


def load_credentials(profile: str = "default") -> dict:
    """Load PVE credentials from ~/.pve/credentials (INI format).

    File format:
        [default]
        host = 192.168.1.10
        token_id = user@pam!mytoken
        token_secret = xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
        node = mynode          # optional
        verify_ssl = false     # optional

        [staging]
        host = 10.0.0.5
        token_id = admin@pam!ci
        token_secret = yyyyyyyy-yyyy-yyyy-yyyy-yyyyyyyyyyyy

    Returns a dict with keys: host, token_id, token_secret, and optionally node, verify_ssl.
    Missing file or profile returns an empty dict (falls back to env/CLI).
    """
    if not PVE_CREDENTIALS_PATH.exists():
        return {}
    cfg = configparser.ConfigParser()
    cfg.read(PVE_CREDENTIALS_PATH)
    if profile not in cfg:
        return {}
    section = dict(cfg[profile])
    # Normalize verify_ssl to bool string for downstream
    if "verify_ssl" in section:
        section["verify_ssl"] = section["verify_ssl"].strip().lower()
    return section


def init_credentials(profile: str = "default"):
    """Interactively create or update ~/.pve/credentials."""
    creds_dir = PVE_CREDENTIALS_PATH.parent
    creds_dir.mkdir(parents=True, exist_ok=True)

    cfg = configparser.ConfigParser()
    if PVE_CREDENTIALS_PATH.exists():
        cfg.read(PVE_CREDENTIALS_PATH)

    if profile in cfg:
        print(f"Profile [{profile}] already exists in {PVE_CREDENTIALS_PATH}")
        overwrite = input("Overwrite? [y/N]: ").strip().lower()
        if overwrite != "y":
            print("Aborted.")
            return

    print(f"\nConfiguring profile [{profile}] in {PVE_CREDENTIALS_PATH}\n")
    host = input("  PVE host (e.g. 192.168.1.10): ").strip()
    token_id = input("  API token ID (e.g. user@pam!mytoken): ").strip()
    token_secret = input("  API token secret: ").strip()
    node = input("  Default node (leave empty to auto-detect): ").strip()
    verify_ssl = input("  Verify SSL? [y/N]: ").strip().lower()

    cfg[profile] = {"host": host, "token_id": token_id, "token_secret": token_secret}
    if node:
        cfg[profile]["node"] = node
    cfg[profile]["verify_ssl"] = "true" if verify_ssl == "y" else "false"

    with open(PVE_CREDENTIALS_PATH, "w") as f:
        cfg.write(f)

    print(f"\nCredentials saved to {PVE_CREDENTIALS_PATH}")
