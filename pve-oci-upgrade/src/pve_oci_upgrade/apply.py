"""Apply and destroy orchestrators for YAML-based deployments."""

from __future__ import annotations

import sys

from .config import DeployConfig
from .deploy import deploy
from .destroy import destroy_container
from .manifest import DeploymentManifest, container_spec_to_deploy_config
from .validate import validate_manifest, validate_storage


def apply_manifest(
    manifest: DeploymentManifest,
    host: str,
    token_id: str,
    token_secret: str,
    node: str | None,
    verify_ssl: bool,
    poll_timeout: int = 300,
):
    """Deploy/upgrade all containers in a manifest sequentially."""
    errors = validate_manifest(manifest)
    if errors:
        print(f"Validation errors:")
        for e in errors:
            print(f"  - {e}")
        sys.exit(2)

    if not manifest.containers:
        print("Warning: no containers defined — nothing to do.")
        return

    # Validate storage against Proxmox before deploying
    from proxmoxer import ProxmoxAPI
    api = ProxmoxAPI(
        host,
        user=token_id.split("!")[0],
        token_name=token_id.split("!")[1],
        token_value=token_secret,
        verify_ssl=verify_ssl,
    )
    target_node = node or api.nodes.get()[0]["node"]
    storage_errors = validate_storage(api, target_node, manifest)
    if storage_errors:
        print("Storage validation errors:")
        for e in storage_errors:
            print(f"  - {e}")
        sys.exit(2)

    total = len(manifest.containers)
    created = upgraded = failed = 0

    for i, spec in enumerate(manifest.containers, 1):
        label = spec.hostname or f"vmid-{spec.vmid}"
        print(f"\n[{i}/{total}] Container {spec.vmid} ({label})")
        try:
            cfg = container_spec_to_deploy_config(
                spec, host, token_id, token_secret, node, verify_ssl, poll_timeout,
            )
            # deploy() handles fresh vs upgrade internally
            deploy(cfg)
            # We can't easily distinguish created vs upgraded here without
            # checking container existence beforehand, but deploy() prints it
            created += 1
        except Exception as e:
            print(f"  Error: {e}")
            failed += 1

    print(f"\nDone. {total} containers processed ({created} succeeded, {failed} failed).")
    if failed:
        sys.exit(1)


def destroy_from_manifest(
    manifest: DeploymentManifest,
    host: str,
    token_id: str,
    token_secret: str,
    node: str | None,
    verify_ssl: bool,
    purge: bool = False,
):
    """Destroy all containers in a manifest sequentially."""
    errors = validate_manifest(manifest)
    if errors:
        print(f"Validation errors:")
        for e in errors:
            print(f"  - {e}")
        sys.exit(2)

    if not manifest.containers:
        print("Warning: no containers defined — nothing to do.")
        return

    total = len(manifest.containers)
    destroyed = skipped = failed = 0

    for i, spec in enumerate(manifest.containers, 1):
        print(f"\n[{i}/{total}] Destroying container {spec.vmid} ...")
        try:
            cfg = DeployConfig(
                host=host,
                token_id=token_id,
                token_secret=token_secret,
                vmid=spec.vmid,
                image="",
                node=node,
                verify_ssl=verify_ssl,
            )
            destroy_container(cfg, purge=purge)
            destroyed += 1
        except Exception as e:
            print(f"  Error: {e}")
            failed += 1

    print(f"\nDone. {destroyed} containers destroyed, {failed} failed.")
    if failed:
        sys.exit(1)
