"""Deploy orchestrator: routes to fresh or upgrade deployment."""

from .client import PVEClient
from .config import DeployConfig
from .deploy_fresh import deploy_fresh
from .deploy_upgrade import deploy_upgrade


def _resolve_template_storage(client: PVEClient, cfg: DeployConfig):
    """Prompt for template storage if not provided."""
    if cfg.storage:
        return
    storages = client.list_template_storages()
    if not storages:
        raise RuntimeError(f"No storages on node '{client.node}' support container templates.")
    if len(storages) == 1:
        cfg.storage = storages[0]["storage"]
        print(f"  Auto-selected template storage: {cfg.storage}")
        return
    print("\n  Available storages for OCI image / templates:")
    for i, s in enumerate(storages, 1):
        avail_gb, total_gb = s["avail"] / (1024**3), s["total"] / (1024**3)
        print(f"    {i}) {s['storage']}  ({s['type']}, {avail_gb:.1f} GB free / {total_gb:.1f} GB total)")
    while True:
        try:
            choice = int(input(f"\n  Select template storage [1-{len(storages)}]: "))
            if 1 <= choice <= len(storages):
                cfg.storage = storages[choice - 1]["storage"]
                return
        except (ValueError, EOFError):
            pass
        print("  Invalid choice, try again.")


def deploy(cfg: DeployConfig):
    """Unified deploy: creates if new, upgrades if existing."""
    client = PVEClient(cfg)
    print(f"Node: {client.node}")

    _resolve_template_storage(client, cfg)

    # Pull image
    print(f"\n[1] Pulling OCI image: {cfg.image} to storage '{cfg.storage}' ...")
    upid = client.pull_oci_image()
    client.wait_for_task(upid, label="Image pull")
    ostemplate = client.template_volid()

    if client.container_exists():
        deploy_upgrade(client, cfg, ostemplate)
    else:
        deploy_fresh(client, cfg, ostemplate)
