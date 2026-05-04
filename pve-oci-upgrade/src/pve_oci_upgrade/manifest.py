"""YAML manifest parsing, data structures, and conversion for declarative deployments."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Any

import yaml

from .config import DeployConfig


@dataclass
class AuthConfig:
    """Authentication settings from YAML (optional)."""
    profile: str = "default"
    host: str | None = None
    node: str | None = None


@dataclass
class ContainerSpec:
    """Single container definition from YAML."""
    vmid: int
    image: str
    hostname: str | None = None
    memory: int = 512
    swap: int = 256
    cores: int = 1
    rootfs_size: str = "8"
    storage: str | None = None
    rootfs_storage: str | None = None
    net: list[str] = field(default_factory=list)
    mp: list[str] = field(default_factory=list)
    privileged: bool = False
    purge_on_upgrade: bool = False
    environment: dict[str, str] = field(default_factory=dict)


@dataclass
class DeploymentManifest:
    """Parsed YAML deployment file."""
    auth: AuthConfig
    defaults: dict[str, Any]
    containers: list[ContainerSpec]


def load_manifest(path: str) -> DeploymentManifest:
    """Parse a YAML deployment file into a DeploymentManifest.

    Raises FileNotFoundError if the file doesn't exist.
    Prints a warning and returns an empty manifest if no containers are defined.
    """
    try:
        with open(path) as f:
            raw = yaml.safe_load(f)
    except FileNotFoundError:
        raise FileNotFoundError(f"Manifest file not found: {path}")

    if not raw or not isinstance(raw, dict):
        print(f"Warning: {path} is empty or invalid — no containers to process.")
        return DeploymentManifest(auth=AuthConfig(), defaults={}, containers=[])

    # Parse auth section
    auth_raw = raw.get("auth") or {}
    auth = AuthConfig(
        profile=auth_raw.get("profile", "default"),
        host=auth_raw.get("host"),
        node=auth_raw.get("node"),
    )

    defaults = raw.get("defaults") or {}
    raw_containers = raw.get("containers") or []

    if not raw_containers:
        print(f"Warning: {path} has no containers defined.")
        return DeploymentManifest(auth=auth, defaults=defaults, containers=[])

    containers = []
    for i, c in enumerate(raw_containers):
        if not isinstance(c, dict):
            sys.exit(f"Error: container at index {i} is not a mapping.")
        # Merge defaults under container values
        merged = {**defaults, **c}
        containers.append(ContainerSpec(
            vmid=merged["vmid"],
            image=merged["image"],
            hostname=merged.get("hostname"),
            memory=int(merged.get("memory", 512)),
            swap=int(merged.get("swap", 256)),
            cores=int(merged.get("cores", 1)),
            rootfs_size=str(merged.get("rootfs_size", "8")),
            storage=merged.get("storage"),
            rootfs_storage=merged.get("rootfs_storage"),
            net=merged.get("net") or [],
            mp=merged.get("mp") or [],
            privileged=bool(merged.get("privileged", False)),
            purge_on_upgrade=bool(merged.get("purge_on_upgrade", False)),
            environment=merged.get("environment") or {},
        ))

    return DeploymentManifest(auth=auth, defaults=defaults, containers=containers)


def env_dict_to_proxmox(env: dict[str, str]) -> str:
    """Convert environment dict to Proxmox comma-separated format."""
    return "\0".join(f"{k}={v}" for k, v in env.items())


def container_spec_to_deploy_config(
    spec: ContainerSpec,
    host: str,
    token_id: str,
    token_secret: str,
    node: str | None,
    verify_ssl: bool,
    poll_timeout: int = 300,
) -> DeployConfig:
    """Convert a ContainerSpec + auth into a DeployConfig for the existing deploy pipeline."""
    net = {f"net{i}": v for i, v in enumerate(spec.net)}
    mp = {f"mp{i}": v for i, v in enumerate(spec.mp)}
    env = env_dict_to_proxmox(spec.environment) if spec.environment else ""

    return DeployConfig(
        host=host,
        token_id=token_id,
        token_secret=token_secret,
        vmid=spec.vmid,
        image=spec.image,
        node=node,
        storage=spec.storage,
        rootfs_storage=spec.rootfs_storage,
        verify_ssl=verify_ssl,
        poll_timeout=poll_timeout,
        hostname=spec.hostname,
        memory=spec.memory,
        swap=spec.swap,
        cores=spec.cores,
        rootfs_size=spec.rootfs_size,
        net=net,
        mp=mp,
        unprivileged=not spec.privileged,
        purge_on_upgrade=spec.purge_on_upgrade,
        env=env,
    )
