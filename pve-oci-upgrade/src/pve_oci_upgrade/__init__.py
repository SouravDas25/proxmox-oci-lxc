"""pve_oci_upgrade: Deploy & upgrade OCI-based LXC containers on Proxmox VE."""

from .config import DeployConfig
from .credentials import load_credentials, init_credentials, PVE_CREDENTIALS_PATH
from .client import PVEClient
from .deploy import deploy
from .deploy_fresh import deploy_fresh
from .deploy_upgrade import deploy_upgrade
from .rollback import rollback
from .destroy import destroy_container
from .manifest import (
    AuthConfig, ContainerSpec, DeploymentManifest,
    load_manifest, container_spec_to_deploy_config, env_dict_to_proxmox,
)
from .apply import apply_manifest, destroy_from_manifest
from .validate import validate_manifest

__all__ = [
    "DeployConfig", "PVEClient",
    "load_credentials", "init_credentials", "PVE_CREDENTIALS_PATH",
    "deploy", "deploy_fresh", "deploy_upgrade", "rollback", "destroy_container",
    "AuthConfig", "ContainerSpec", "DeploymentManifest",
    "load_manifest", "container_spec_to_deploy_config", "env_dict_to_proxmox",
    "apply_manifest", "destroy_from_manifest", "validate_manifest",
]
