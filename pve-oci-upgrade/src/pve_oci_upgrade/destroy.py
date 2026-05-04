"""Destroy a container."""

from .client import PVEClient
from .config import DeployConfig


def destroy_container(cfg: DeployConfig, purge: bool = False):
    """Stop and destroy a container."""
    client = PVEClient(cfg)
    print(f"Node: {client.node}")

    if not client.container_exists():
        print(f"Container {cfg.vmid} does not exist. Nothing to do.")
        return

    status = client.get_status()
    if status.get("status") == "running":
        print(f"\n[1] Stopping container {cfg.vmid} ...")
        upid = client.stop()
        client.wait_for_task(upid, label="Stop")
    else:
        print(f"\n[1] Container {cfg.vmid} already stopped.")

    print(f"\n[2] Destroying container {cfg.vmid} (purge={purge}) ...")
    upid = client.destroy(purge=purge)
    try:
        client.wait_for_task(upid, label="Destroy")
    except RuntimeError as e:
        if "No such file" in str(e) or "rbd error" in str(e) or "unable to parse" in str(e):
            print(f"\n  Destroy failed: {e}")
            print(f"  The container references a volume that no longer exists.")
            print(f"  To force-remove, run on the Proxmox node:")
            print(f"    rm /etc/pve/lxc/{cfg.vmid}.conf")
            return
        raise
    print(f"\nDone. Container {cfg.vmid} destroyed.")
