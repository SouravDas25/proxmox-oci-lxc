"""Fresh container deployment."""

from .client import PVEClient
from .config import DeployConfig


def _resolve_rootfs_storage(client: PVEClient, cfg: DeployConfig):
    """Prompt for rootfs storage if not provided."""
    if cfg.rootfs_storage:
        return
    storages = client.list_rootfs_storages()
    if not storages:
        raise RuntimeError(f"No storages on node '{client.node}' support container rootfs.")
    if len(storages) == 1:
        cfg.rootfs_storage = storages[0]["storage"]
        print(f"  Auto-selected rootfs storage: {cfg.rootfs_storage}")
        return
    print("\n  Available storages for container rootfs:")
    for i, s in enumerate(storages, 1):
        avail_gb, total_gb = s["avail"] / (1024**3), s["total"] / (1024**3)
        print(f"    {i}) {s['storage']}  ({s['type']}, {avail_gb:.1f} GB free / {total_gb:.1f} GB total)")
    while True:
        try:
            choice = int(input(f"\n  Select storage [1-{len(storages)}]: "))
            if 1 <= choice <= len(storages):
                cfg.rootfs_storage = storages[choice - 1]["storage"]
                return
        except (ValueError, EOFError):
            pass
        print("  Invalid choice, try again.")


def deploy_fresh(client: PVEClient, cfg: DeployConfig, ostemplate: str):
    """Create a brand new container."""
    _resolve_rootfs_storage(client, cfg)
    print(f"\n[2] Container {cfg.vmid} does not exist — creating fresh ...")
    upid = client.create_fresh(ostemplate)
    client.wait_for_task(upid, label="Create")
    print(f"\nDone. Container {cfg.vmid} created from {cfg.image}")
