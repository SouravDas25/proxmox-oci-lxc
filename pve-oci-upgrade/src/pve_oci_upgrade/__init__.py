"""pve_oci_upgrade: Deploy & upgrade OCI-based LXC containers on Proxmox VE."""

from .config import DeployConfig
from .credentials import load_credentials, init_credentials, PVE_CREDENTIALS_PATH
from .client import PVEClient
from .deploy import deploy
from .deploy_fresh import deploy_fresh
from .deploy_upgrade import deploy_upgrade
from .rollback import rollback
from .destroy import destroy_container

__all__ = [
    "DeployConfig", "PVEClient",
    "load_credentials", "init_credentials", "PVE_CREDENTIALS_PATH",
    "deploy", "deploy_fresh", "deploy_upgrade", "rollback", "destroy_container",
]
