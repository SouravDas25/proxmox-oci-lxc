"""Rollback a container to a previous config and template."""

import json
from pathlib import Path

from .client import PVEClient
from .config import DeployConfig


def rollback(cfg: DeployConfig, backup_file: str, old_template: str, no_start: bool = False):
    """Rollback to a previous config + template."""
    client = PVEClient(cfg)
    print(f"Rolling back container {cfg.vmid} using {backup_file} ...")

    config = json.loads(Path(backup_file).read_text())

    if client.container_exists():
        status = client.get_status()
        was_running = status.get("status") == "running"
        if was_running:
            upid = client.stop()
            client.wait_for_task(upid, label="Stop")
        upid = client.destroy(purge=False)
        client.wait_for_task(upid, label="Destroy")
    else:
        was_running = True  # default: start after rollback unless --no-start

    upid = client.create(old_template, config)
    client.wait_for_task(upid, label="Create")

    if was_running and not no_start:
        upid = client.start()
        client.wait_for_task(upid, label="Start")
    elif no_start:
        print("  --no-start specified, skipping start.")

    print(f"Rollback complete. Container {cfg.vmid} restored.")
