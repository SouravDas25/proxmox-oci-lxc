"""YAML manifest schema validation and error collection."""

from __future__ import annotations

from .manifest import DeploymentManifest


def validate_manifest(manifest: DeploymentManifest) -> list[str]:
    """Validate a parsed manifest and return a list of error strings.

    Returns an empty list if valid.
    """
    errors = []

    # Check each container
    seen_vmids: dict[int, int] = {}
    for i, c in enumerate(manifest.containers):
        # Required fields
        if not c.vmid:
            errors.append(f"Container at index {i}: missing required field 'vmid'")
        if not c.image:
            errors.append(f"Container at index {i}: missing required field 'image'")

        # Type checks
        if not isinstance(c.vmid, int) or c.vmid <= 0:
            errors.append(f"Container at index {i}: 'vmid' must be a positive integer, got '{c.vmid}'")
        if not isinstance(c.memory, int) or c.memory <= 0:
            errors.append(f"Container at index {i}: 'memory' must be a positive integer, got '{c.memory}'")
        if not isinstance(c.swap, int) or c.swap < 0:
            errors.append(f"Container at index {i}: 'swap' must be a non-negative integer, got '{c.swap}'")
        if not isinstance(c.cores, int) or c.cores <= 0:
            errors.append(f"Container at index {i}: 'cores' must be a positive integer, got '{c.cores}'")

        # Duplicate VMID detection
        if c.vmid in seen_vmids:
            errors.append(
                f"Container at index {i}: duplicate vmid {c.vmid} (also at index {seen_vmids[c.vmid]})"
            )
        else:
            seen_vmids[c.vmid] = i

    return errors


def validate_storage(api, node: str, manifest: DeploymentManifest) -> list[str]:
    """Validate storage references against Proxmox and return errors with suggestions."""
    errors = []
    storages = api.nodes(node).storage.get()

    storage_map = {}
    for s in storages:
        content = s.get("content", "")
        storage_map[s["storage"]] = content.split(",") if content else []

    template_stores = [n for n, c in storage_map.items() if "vztmpl" in c]
    rootdir_stores = [n for n, c in storage_map.items() if "rootdir" in c]

    for i, c in enumerate(manifest.containers):
        label = c.hostname or f"vmid-{c.vmid}"

        if c.storage:
            if c.storage not in storage_map:
                errors.append(
                    f"Container {label}: storage '{c.storage}' not found. "
                    f"Available: {', '.join(storage_map.keys())}")
            elif "vztmpl" not in storage_map[c.storage]:
                errors.append(
                    f"Container {label}: storage '{c.storage}' does not support templates (vztmpl). "
                    f"Valid options: {', '.join(template_stores)}")

        if c.rootfs_storage:
            if c.rootfs_storage not in storage_map:
                errors.append(
                    f"Container {label}: rootfs_storage '{c.rootfs_storage}' not found. "
                    f"Available: {', '.join(storage_map.keys())}")
            elif "rootdir" not in storage_map[c.rootfs_storage]:
                errors.append(
                    f"Container {label}: rootfs_storage '{c.rootfs_storage}' does not support "
                    f"container directories (rootdir). Valid options: {', '.join(rootdir_stores)}")

    return errors
