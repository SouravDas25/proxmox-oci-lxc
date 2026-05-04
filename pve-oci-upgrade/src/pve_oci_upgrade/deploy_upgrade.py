"""Upgrade existing container deployment."""

import json
from pathlib import Path

from .client import PVEClient
from .config import DeployConfig


def deploy_upgrade(client: PVEClient, cfg: DeployConfig, ostemplate: str):
    """Upgrade an existing container to a new OCI image."""
    print(f"\n[2] Container {cfg.vmid} exists — upgrading ...")

    config = client.get_config()
    status = client.get_status()
    was_running = status.get("status") == "running"

    if cfg.backup_config:
        backup_path = Path(f"pve-ct-{cfg.vmid}-config-backup.json")
        backup_path.write_text(json.dumps(config, indent=2))
        print(f"  Config backed up to {backup_path}")

    # Stop
    print(f"\n[3] Stopping container ...")
    if was_running:
        upid = client.stop()
        client.wait_for_task(upid, label="Stop")
    else:
        print("  Already stopped.")

    # Vzdump backup as safety net
    print(f"\n[4] Creating vzdump backup (safety net) ...")
    backup_storage = cfg.storage
    upid = client.vzdump_backup(storage=backup_storage)
    client.wait_for_task(upid, label="Vzdump backup")
    backup_volid = client.find_latest_backup(backup_storage)
    if backup_volid:
        print(f"  Backup saved: {backup_volid}")
    else:
        print("  Warning: could not locate backup after vzdump.")

    # Destroy
    purge = cfg.purge_on_upgrade
    print(f"\n[5] Destroying old container (purge={purge}) ...")
    upid = client.destroy(purge=purge)
    client.wait_for_task(upid, label="Destroy")

    # Recreate
    try:
        print(f"\n[6] Recreating with new image ...")
        upid = client.create(ostemplate, config)
        client.wait_for_task(upid, label="Create")
    except Exception as create_err:
        _handle_create_failure(client, cfg, backup_volid, was_running, create_err)
        raise

    # Re-apply env vars
    try:
        client.reapply_post_create_config(config)
    except Exception as e:
        print(f"  Warning: failed to re-apply some config: {e}")

    # Cleanup old rootfs if not purged
    if not cfg.purge_on_upgrade:
        client.cleanup_old_rootfs(config)

    # Restart if was running
    print(f"\n[7] Starting container ...")
    start_ok = False
    if was_running:
        try:
            upid = client.start()
            client.wait_for_task(upid, label="Start")
            start_ok = True
        except Exception as e:
            print(f"  Start failed: {e}")
            print(f"  Vzdump backup preserved: {backup_volid}")
    else:
        print("  Was stopped before upgrade, skipping start.")
        start_ok = True

    # Cleanup vzdump backup only after successful start
    if start_ok and backup_volid:
        print(f"\n[8] Cleaning up vzdump backup ...")
        client.delete_backup(backup_volid)

    # Cleanup template after successful deployment
    if start_ok:
        print(f"\n[9] Cleaning up pulled template ...")
        client.delete_template(ostemplate)

    print(f"\nDone. Container {cfg.vmid} upgraded to {cfg.image}")


def _handle_create_failure(client: PVEClient, cfg: DeployConfig, backup_volid: str | None,
                           was_running: bool, create_err: Exception):
    """Attempt to restore from backup after failed recreate."""
    print(f"\n  ERROR during recreate: {create_err}")
    if not backup_volid:
        print(f"  No vzdump backup available. Use 'rollback' command with:")
        print(f"    --backup-file pve-ct-{cfg.vmid}-config-backup.json")
        return
    try:
        print("  Destroying partially-created container before restore ...")
        upid = client.destroy(purge=True)
        client.wait_for_task(upid, label="Destroy (cleanup)")
    except Exception:
        pass
    print("  Restoring from vzdump backup ...")
    try:
        upid = client.restore_backup(backup_volid)
        client.wait_for_task(upid, label="Restore")
        print("  Restore succeeded — container is back to pre-upgrade state.")
        if was_running:
            upid = client.start()
            client.wait_for_task(upid, label="Start (restored)")
    except Exception as restore_err:
        print(f"  Restore also failed: {restore_err}")
        print(f"  Vzdump backup is still available: {backup_volid}")
        print(f"  Manually restore with: pct restore {cfg.vmid} <backup-path>")
