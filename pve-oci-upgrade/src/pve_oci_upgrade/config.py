"""Configuration dataclass for PVE OCI deployments."""

from dataclasses import dataclass, field


@dataclass
class DeployConfig:
    host: str
    token_id: str
    token_secret: str
    vmid: int
    image: str
    node: str | None = None
    storage: str | None = None
    rootfs_storage: str | None = None
    verify_ssl: bool = False
    backup_config: bool = True
    poll_interval: int = 2
    poll_timeout: int = 300
    # Create-specific defaults (used only for fresh deploys)
    hostname: str | None = None
    memory: int = 512
    swap: int = 256
    cores: int = 1
    rootfs_size: str = "8"
    net: dict[str, str] = field(default_factory=dict)
    mp: dict[str, str] = field(default_factory=dict)
    start_after_create: bool = True
    unprivileged: bool = True
    purge_on_upgrade: bool = False
    env: str = ""  # Proxmox env format: "KEY=val,KEY2=val2"
