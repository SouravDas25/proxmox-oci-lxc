#!/usr/bin/env python3
"""
pve-oci-deploy: Deploy & upgrade OCI-based LXC containers on Proxmox VE 9.1+

- If the container doesn't exist yet, it creates it from the OCI image.
- If it already exists, it upgrades it (stop → save config → destroy → recreate → start).

Usage:
    # First deploy (creates container 100)
    python pve_oci_upgrade.py deploy --vmid 100 --image docker.io/library/nginx:1.0 \
        --hostname my-nginx --memory 512 --cores 2 --net0 "name=eth0,bridge=vmbr0,ip=dhcp"

    # Upgrade to v2.0 (same command, detects existing container)
    python pve_oci_upgrade.py deploy --vmid 100 --image docker.io/library/nginx:2.0

    # Rollback
    python pve_oci_upgrade.py rollback --vmid 100 \
        --backup-file pve-ct-100-config-backup.json \
        --template local:vztmpl/nginx-1.0.tar.zst

Environment variables (alternative to CLI flags):
    PVE_HOST, PVE_TOKEN_ID, PVE_TOKEN_SECRET, PVE_NODE, PVE_VERIFY_SSL
"""

import argparse
import configparser
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from proxmoxer import ProxmoxAPI

# ---------------------------------------------------------------------------
# Credentials file (~/.pve/credentials)
# ---------------------------------------------------------------------------

PVE_CREDENTIALS_PATH = Path.home() / ".pve" / "credentials"


def load_credentials(profile: str = "default") -> dict:
    """Load PVE credentials from ~/.pve/credentials (INI format).

    File format:
        [default]
        host = 192.168.1.10
        token_id = user@pam!mytoken
        token_secret = xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
        node = mynode          # optional
        verify_ssl = false     # optional

        [staging]
        host = 10.0.0.5
        token_id = admin@pam!ci
        token_secret = yyyyyyyy-yyyy-yyyy-yyyy-yyyyyyyyyyyy

    Returns a dict with keys: host, token_id, token_secret, and optionally node, verify_ssl.
    Missing file or profile returns an empty dict (falls back to env/CLI).
    """
    if not PVE_CREDENTIALS_PATH.exists():
        return {}
    cfg = configparser.ConfigParser()
    cfg.read(PVE_CREDENTIALS_PATH)
    if profile not in cfg:
        return {}
    section = dict(cfg[profile])
    # Normalize verify_ssl to bool string for downstream
    if "verify_ssl" in section:
        section["verify_ssl"] = section["verify_ssl"].strip().lower()
    return section


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Proxmox helpers
# ---------------------------------------------------------------------------

class PVEClient:
    """Thin wrapper around proxmoxer for OCI deploy/upgrade workflow."""

    def __init__(self, cfg: DeployConfig):
        self.cfg = cfg
        self.api = ProxmoxAPI(
            cfg.host,
            user=cfg.token_id.split("!")[0],
            token_name=cfg.token_id.split("!")[1],
            token_value=cfg.token_secret,
            verify_ssl=cfg.verify_ssl,
        )
        self.node = cfg.node or self._detect_node()

    # -- discovery ----------------------------------------------------------

    def _detect_node(self) -> str:
        """Auto-detect the node the VMID lives on.
        For fresh deploys (VMID not found), requires --node to be set explicitly."""
        from proxmoxer.core import ResourceException
        nodes = self.api.nodes.get()
        # Try to find the VMID on an existing node
        for node in nodes:
            try:
                self.api.nodes(node["node"]).lxc(self.cfg.vmid).status.current.get()
                return node["node"]
            except ResourceException as e:
                # 404 or 500 "does not exist" → CT not on this node, keep looking
                if e.status_code == 404:
                    continue
                if e.status_code == 500 and "does not exist" in str(e):
                    continue
                # Permission denied or other API error → warn but skip node
                print(f"  Warning: could not query node '{node['node']}': {e}")
                continue
            except Exception as e:
                print(f"  Warning: could not query node '{node['node']}': {e}")
                continue
        # VMID not found — refuse to guess on a multi-node cluster
        online_nodes = [n["node"] for n in nodes if n.get("status") == "online"]
        if not online_nodes:
            raise RuntimeError("No online Proxmox node found")
        if len(online_nodes) == 1:
            return online_nodes[0]
        raise RuntimeError(
            f"Container {self.cfg.vmid} not found and cluster has multiple nodes "
            f"({', '.join(online_nodes)}). Use --node to specify the target node."
        )

    def container_exists(self) -> bool:
        """Check if the VMID already exists on this node.
        Only treats HTTP 404/500 'does not exist' as absent.
        Re-raises network errors, auth failures, etc."""
        from proxmoxer.core import ResourceException
        try:
            self.api.nodes(self.node).lxc(self.cfg.vmid).status.current.get()
            return True
        except ResourceException as e:
            # proxmoxer raises ResourceException for HTTP errors;
            # 500 with "does not exist" or 404 means the CT is absent.
            if e.status_code == 404:
                return False
            if e.status_code == 500 and "does not exist" in str(e):
                return False
            raise

    def list_rootfs_storages(self) -> list[dict]:
        """Return storages on this node that support container rootfs (rootdir content)."""
        storages = self.api.nodes(self.node).storage.get(content="rootdir", enabled=1)
        return [
            {
                "storage": s["storage"],
                "type": s.get("type", "?"),
                "avail": s.get("avail", 0),
                "total": s.get("total", 0),
            }
            for s in storages
        ]

    def list_template_storages(self) -> list[dict]:
        """Return storages on this node that support container templates (vztmpl content)."""
        storages = self.api.nodes(self.node).storage.get(content="vztmpl", enabled=1)
        return [
            {
                "storage": s["storage"],
                "type": s.get("type", "?"),
                "avail": s.get("avail", 0),
                "total": s.get("total", 0),
            }
            for s in storages
        ]

    # -- container operations -----------------------------------------------

    def get_config(self) -> dict:
        return self.api.nodes(self.node).lxc(self.cfg.vmid).config.get()

    def get_status(self) -> dict:
        return self.api.nodes(self.node).lxc(self.cfg.vmid).status.current.get()

    def stop(self) -> str:
        print(f"  Stopping container {self.cfg.vmid} ...")
        return self.api.nodes(self.node).lxc(self.cfg.vmid).status.shutdown.post(
            forceStop=1, timeout=30
        )

    def destroy(self, purge: bool = False) -> str:
        print(f"  Destroying container {self.cfg.vmid} (purge={purge}) ...")
        return self.api.nodes(self.node).lxc(self.cfg.vmid).delete(
            purge=int(purge)
        )

    def create(self, ostemplate: str, config: dict) -> str:
        print(f"  Creating container {self.cfg.vmid} from {ostemplate} ...")
        params = self._config_to_create_params(config)
        params["vmid"] = self.cfg.vmid
        params["ostemplate"] = ostemplate
        return self.api.nodes(self.node).lxc.create(**params)

    def create_fresh(self, ostemplate: str) -> str:
        """Create a brand new container from CLI-provided settings."""
        print(f"  Creating new container {self.cfg.vmid} from {ostemplate} ...")
        params = {
            "vmid": self.cfg.vmid,
            "ostemplate": ostemplate,
            "memory": self.cfg.memory,
            "swap": self.cfg.swap,
            "cores": self.cfg.cores,
            "rootfs": f"{self.cfg.rootfs_storage}:{self.cfg.rootfs_size}",
            "unprivileged": int(self.cfg.unprivileged),
            "start": int(self.cfg.start_after_create),
        }
        if self.cfg.hostname:
            params["hostname"] = self.cfg.hostname
        # Network interfaces: --net0 "name=eth0,bridge=vmbr0,ip=dhcp"
        for key, val in self.cfg.net.items():
            params[key] = val
        # Mount points: --mp0 "/data,mp=/app/data"
        for key, val in self.cfg.mp.items():
            params[key] = val
        return self.api.nodes(self.node).lxc.create(**params)

    def start(self) -> str:
        print(f"  Starting container {self.cfg.vmid} ...")
        return self.api.nodes(self.node).lxc(self.cfg.vmid).status.start.post()

    # -- vzdump backup/restore (safety net for upgrades) --------------------

    def vzdump_backup(self, storage: str) -> str:
        """Create a vzdump backup of the container. Returns UPID."""
        print(f"  Creating vzdump backup of container {self.cfg.vmid} on '{storage}' ...")
        return self.api.nodes(self.node).vzdump.create(
            vmid=self.cfg.vmid,
            mode="stop",
            compress="zstd",
            storage=storage,
        )

    def find_latest_backup(self, storage: str) -> str | None:
        """Find the most recent vzdump backup for this VMID on the given storage."""
        try:
            contents = self.api.nodes(self.node).storage(storage).content.get(
                content="backup", vmid=self.cfg.vmid
            )
            if not contents:
                return None
            contents.sort(key=lambda x: x.get("ctime", 0), reverse=True)
            return contents[0].get("volid")
        except Exception as e:
            print(f"  Warning: could not list backups: {e}")
            return None

    def restore_backup(self, backup_volid: str) -> str:
        """Restore a container from a vzdump backup. Returns UPID."""
        print(f"  Restoring container {self.cfg.vmid} from {backup_volid} ...")
        return self.api.nodes(self.node).lxc.create(
            vmid=self.cfg.vmid,
            ostemplate=backup_volid,
            restore=1,
        )

    def delete_backup(self, backup_volid: str):
        """Delete a vzdump backup file from storage."""
        if not backup_volid:
            return
        try:
            storage_name = backup_volid.split(":")[0]
            print(f"  Cleaning up vzdump backup: {backup_volid}")
            self.api.nodes(self.node).storage(storage_name).content(backup_volid).delete()
        except Exception as e:
            print(f"  Warning: could not remove backup {backup_volid}: {e}")

    # -- template / image ---------------------------------------------------

    def _init_template_ts(self):
        """Set the timestamp used for this deploy's template filename."""
        if not hasattr(self, "_template_ts"):
            from datetime import datetime, timezone
            self._template_ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")

    def pull_oci_image(self) -> str:
        """Pull an OCI image from a registry to the configured storage. Returns UPID."""
        self._init_template_ts()
        print(f"  Pulling image {self.cfg.image} to storage '{self.cfg.storage}' ...")
        return self.api.nodes(self.node).storage(self.cfg.storage).post(
            "oci-registry-pull",
            reference=self.cfg.image,
            filename=self._image_base_name(),
        )

    def _image_base_name(self) -> str:
        """Derive a timestamped base name from the OCI reference.
        e.g. docker.io/library/nginx:2.0 → nginx-2.0-20260322-143000
        This ensures each pull creates a unique template."""
        self._init_template_ts()
        ref = self.cfg.image
        if "/" in ref:
            ref = ref.split("/")[-1]
        return f"{ref.replace(':', '-')}-{self._template_ts}"

    def _image_to_template_name(self) -> str:
        """Full template filename as it will appear on storage.
        After pulling, we scan the storage to find the actual file
        (extension may be .tar, .tar.zst, .tar.gz depending on PVE version)."""
        return self._image_base_name() + ".tar"

    def _detect_template_volid(self) -> str:
        """Scan the template storage for the pulled template, matching by base name.
        Handles varying extensions (.tar, .tar.zst, .tar.gz)."""
        base = self._image_base_name()
        try:
            contents = self.api.nodes(self.node).storage(self.cfg.storage).content.get(
                content="vztmpl"
            )
            for item in contents:
                volid = item.get("volid", "")
                # volid looks like "storage:vztmpl/nginx-2.0-20260322-143000.tar.zst"
                filename = volid.split("/")[-1] if "/" in volid else volid
                if filename.startswith(base):
                    return volid
        except Exception as e:
            print(f"  Warning: could not scan storage for template: {e}")
        # Fallback to the .tar guess
        return f"{self.cfg.storage}:vztmpl/{base}.tar"

    def template_volid(self) -> str:
        return self._detect_template_volid()

    # -- task polling -------------------------------------------------------

    def wait_for_task(self, upid: str, label: str = "task"):
        """Poll a Proxmox task until it completes or times out."""
        elapsed = 0
        while elapsed < self.cfg.poll_timeout:
            task = self.api.nodes(self.node).tasks(upid).status.get()
            status = task.get("status", "unknown")
            if status == "stopped":
                exitstatus = task.get("exitstatus", "")
                if exitstatus == "OK" or exitstatus.startswith("WARNINGS"):
                    if exitstatus.startswith("WARNINGS"):
                        print(f"  {label} completed with warnings: {exitstatus}")
                    else:
                        print(f"  {label} completed OK.")
                    return
                raise RuntimeError(f"{label} failed: {exitstatus}")
            time.sleep(self.cfg.poll_interval)
            elapsed += self.cfg.poll_interval
        raise TimeoutError(f"{label} timed out after {self.cfg.poll_timeout}s")

    # -- config translation -------------------------------------------------

    SKIP_KEYS = {"digest", "lxc", "status"}
    READONLY_KEYS = {"lock"}
    # Keys re-applied after create (overwritten by OCI image defaults)
    POST_CREATE_KEYS = {"env"}

    def _config_to_create_params(self, config: dict) -> dict:
        params = {}
        skip = self.SKIP_KEYS | self.READONLY_KEYS | self.POST_CREATE_KEYS
        for k, v in config.items():
            if k in skip:
                continue
            params[k] = v
        params.pop("ostype", None)

        # Rewrite rootfs: "storage:vm-XXX-disk-N,size=8G" → "storage:SIZE"
        # so PVE allocates a fresh volume instead of looking for the old one.
        # NOTE: if the original rootfs has no size= parameter, we fall back to
        # the configured rootfs_size (default 8G) to avoid silent data loss.
        rootfs = params.get("rootfs", "")
        if rootfs:
            parts = rootfs.split(",")
            storage_vol = parts[0]  # e.g. "pxpool01:vm-999-disk-0"
            size = None
            for p in parts[1:]:
                if p.startswith("size="):
                    size = p.split("=")[1].rstrip("GgMm")
                    break
            if size is None:
                size = self.cfg.rootfs_size
                print(f"  Warning: original rootfs had no size= parameter, "
                      f"using {size}G as fallback.")
            storage_name = storage_vol.split(":")[0]
            params["rootfs"] = f"{storage_name}:{size}"

        return params

    def reapply_post_create_config(self, config: dict):
        """Re-apply env vars that get overwritten by OCI image defaults during create."""
        if "env" not in config:
            return
        print(f"  Re-applying env vars ...")
        self.api.nodes(self.node).lxc(self.cfg.vmid).config.put(env=config["env"])

    def cleanup_old_rootfs(self, config: dict):
        """Remove the old rootfs volume after a successful upgrade to prevent storage leaks."""
        rootfs = config.get("rootfs", "")
        if rootfs:
            volid = rootfs.split(",")[0]  # e.g. "pxpool01:vm-999-disk-0"
            if ":" in volid and "vm-" in volid:
                try:
                    print(f"  Cleaning up old rootfs volume: {volid}")
                    storage_name = volid.split(":")[0]
                    self.api.nodes(self.node).storage(storage_name).content(volid).delete()
                except Exception as e:
                    print(f"  Warning: could not remove old rootfs volume {volid}: {e}")


# ---------------------------------------------------------------------------
# Deploy orchestrator (create or upgrade)
# ---------------------------------------------------------------------------

def deploy(cfg: DeployConfig):
    """Unified deploy: creates if new, upgrades if existing."""
    client = PVEClient(cfg)
    print(f"Node: {client.node}")

    exists = client.container_exists()

    # 0. Resolve template storage: prompt if not explicitly provided
    if not cfg.storage:
        storages = client.list_template_storages()
        if not storages:
            raise RuntimeError(
                f"No storages on node '{client.node}' support container templates (vztmpl)."
            )
        if len(storages) == 1:
            cfg.storage = storages[0]["storage"]
            print(f"  Auto-selected template storage: {cfg.storage}")
        else:
            print("\n  Available storages for OCI image / templates:")
            for i, s in enumerate(storages, 1):
                avail_gb = s["avail"] / (1024 ** 3)
                total_gb = s["total"] / (1024 ** 3)
                print(f"    {i}) {s['storage']}  ({s['type']}, "
                      f"{avail_gb:.1f} GB free / {total_gb:.1f} GB total)")
            while True:
                try:
                    choice = int(input(f"\n  Select template storage [1-{len(storages)}]: "))
                    if 1 <= choice <= len(storages):
                        cfg.storage = storages[choice - 1]["storage"]
                        break
                except (ValueError, EOFError):
                    pass
                print("  Invalid choice, try again.")

    # 1. Pull image
    print(f"\n[1] Pulling OCI image: {cfg.image} to storage '{cfg.storage}' ...")
    upid = client.pull_oci_image()
    client.wait_for_task(upid, label="Image pull")
    ostemplate = client.template_volid()

    if not exists:
        # --- Fresh deploy ---
        # Resolve rootfs storage: prompt if not provided
        if not cfg.rootfs_storage:
            storages = client.list_rootfs_storages()
            if not storages:
                raise RuntimeError(
                    f"No storages on node '{client.node}' support container rootfs (rootdir)."
                )
            if len(storages) == 1:
                cfg.rootfs_storage = storages[0]["storage"]
                print(f"  Auto-selected rootfs storage: {cfg.rootfs_storage}")
            else:
                print("\n  Available storages for container rootfs:")
                for i, s in enumerate(storages, 1):
                    avail_gb = s["avail"] / (1024 ** 3)
                    total_gb = s["total"] / (1024 ** 3)
                    print(f"    {i}) {s['storage']}  ({s['type']}, "
                          f"{avail_gb:.1f} GB free / {total_gb:.1f} GB total)")
                while True:
                    try:
                        choice = int(input(f"\n  Select storage [1-{len(storages)}]: "))
                        if 1 <= choice <= len(storages):
                            cfg.rootfs_storage = storages[choice - 1]["storage"]
                            break
                    except (ValueError, EOFError):
                        pass
                    print("  Invalid choice, try again.")

        print(f"\n[2] Container {cfg.vmid} does not exist — creating fresh ...")
        upid = client.create_fresh(ostemplate)
        client.wait_for_task(upid, label="Create")
        print(f"\nDone. Container {cfg.vmid} created from {cfg.image}")
    else:
        # --- Upgrade existing ---
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

        # Create a vzdump backup as a safety net before destroying
        print(f"\n[4] Creating vzdump backup (safety net) ...")
        backup_storage = cfg.storage  # use the same storage as templates
        upid = client.vzdump_backup(storage=backup_storage)
        client.wait_for_task(upid, label="Vzdump backup")
        backup_volid = client.find_latest_backup(backup_storage)
        if backup_volid:
            print(f"  Backup saved: {backup_volid}")
        else:
            print("  Warning: could not locate backup after vzdump. Proceeding without safety net.")

        # Destroy → Recreate → Start
        purge = cfg.purge_on_upgrade
        print(f"\n[5] Destroying old container (purge={purge}) ...")
        upid = client.destroy(purge=purge)
        client.wait_for_task(upid, label="Destroy")

        try:
            # Recreate with saved config + new image
            print(f"\n[6] Recreating with new image ...")
            upid = client.create(ostemplate, config)
            client.wait_for_task(upid, label="Create")
        except Exception as create_err:
            print(f"\n  ERROR during recreate: {create_err}")
            if backup_volid:
                # The failed create may have left a partial container behind;
                # destroy it first so the restore doesn't hit "CT already exists".
                try:
                    print("  Destroying partially-created container before restore ...")
                    upid = client.destroy(purge=True)
                    client.wait_for_task(upid, label="Destroy (cleanup)")
                except Exception:
                    pass  # container may not exist — that's fine
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
            else:
                print(f"  No vzdump backup available. Use 'rollback' command with:")
                print(f"    --backup-file pve-ct-{cfg.vmid}-config-backup.json")
            raise create_err

        # Re-apply env vars that get lost/overwritten during create
        try:
            client.reapply_post_create_config(config)
        except Exception as e:
            print(f"  Warning: failed to re-apply some config: {e}")
            print(f"  Container was created successfully but may need manual config fixes.")

        # Clean up old rootfs volume to prevent storage leaks
        # (only needed when purge was not used — purge handles this automatically)
        if not cfg.purge_on_upgrade:
            client.cleanup_old_rootfs(config)

        # Restart if it was running
        print(f"\n[7] Starting container ...")
        if was_running:
            upid = client.start()
            client.wait_for_task(upid, label="Start")
        else:
            print("  Was stopped before upgrade, skipping start.")

        # Success — clean up the vzdump backup
        if backup_volid:
            print(f"\n[8] Cleaning up vzdump backup ...")
            client.delete_backup(backup_volid)

        print(f"\nDone. Container {cfg.vmid} upgraded to {cfg.image}")


# ---------------------------------------------------------------------------
# Rollback
# ---------------------------------------------------------------------------

def rollback(cfg: DeployConfig, backup_file: str, old_template: str, no_start: bool = False):
    """Rollback to a previous config + template."""
    client = PVEClient(cfg)
    print(f"Rolling back container {cfg.vmid} using {backup_file} ...")

    config = json.loads(Path(backup_file).read_text())

    if client.container_exists():
        status = client.get_status()
        was_running = status.get("status") == "running"
        if was_running:
            upid = client.stop()
            client.wait_for_task(upid, label="Stop")
        upid = client.destroy(purge=False)
        client.wait_for_task(upid, label="Destroy")
    else:
        was_running = True  # default: start after rollback unless --no-start

    upid = client.create(old_template, config)
    client.wait_for_task(upid, label="Create")

    if was_running and not no_start:
        upid = client.start()
        client.wait_for_task(upid, label="Start")
    elif no_start:
        print("  --no-start specified, skipping start.")

    print(f"Rollback complete. Container {cfg.vmid} restored.")


# ---------------------------------------------------------------------------
# Destroy
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def init_credentials(profile: str = "default"):
    """Interactively create or update ~/.pve/credentials."""
    creds_dir = PVE_CREDENTIALS_PATH.parent
    creds_dir.mkdir(parents=True, exist_ok=True)

    cfg = configparser.ConfigParser()
    if PVE_CREDENTIALS_PATH.exists():
        cfg.read(PVE_CREDENTIALS_PATH)

    if profile in cfg:
        print(f"Profile [{profile}] already exists in {PVE_CREDENTIALS_PATH}")
        overwrite = input("Overwrite? [y/N]: ").strip().lower()
        if overwrite != "y":
            print("Aborted.")
            return

    print(f"\nConfiguring profile [{profile}] in {PVE_CREDENTIALS_PATH}\n")
    host = input("  PVE host (e.g. 192.168.1.10): ").strip()
    token_id = input("  API token ID (e.g. user@pam!mytoken): ").strip()
    token_secret = input("  API token secret: ").strip()
    node = input("  Default node (leave empty to auto-detect): ").strip()
    verify_ssl = input("  Verify SSL? [y/N]: ").strip().lower()

    cfg[profile] = {"host": host, "token_id": token_id, "token_secret": token_secret}
    if node:
        cfg[profile]["node"] = node
    cfg[profile]["verify_ssl"] = "true" if verify_ssl == "y" else "false"

    with open(PVE_CREDENTIALS_PATH, "w") as f:
        cfg.write(f)

    print(f"\nCredentials saved to {PVE_CREDENTIALS_PATH}")


def _add_common_args(parser):
    """Add auth/connection args shared by all subcommands."""
    parser.add_argument("--profile", default="default",
                        help="Credentials profile from ~/.pve/credentials (default: default)")
    parser.add_argument("--host", default=None,
                        help="Proxmox host (or PVE_HOST env, or ~/.pve/credentials)")
    parser.add_argument("--token-id", default=None,
                        help="API token ID, e.g. user@pam!tokenname (or PVE_TOKEN_ID env)")
    parser.add_argument("--token-secret", default=None,
                        help="API token secret (or PVE_TOKEN_SECRET env)")
    parser.add_argument("--vmid", type=int, required=True, help="Container VMID")
    parser.add_argument("--node", default=None,
                        help="Proxmox node (auto-detected if omitted)")
    parser.add_argument("--verify-ssl", action="store_true", default=None,
                        help="Verify SSL certificate")


def _resolve_auth(args) -> tuple[str, str, str, str | None, bool]:
    """Resolve host, token_id, token_secret, node, verify_ssl.
    Priority: CLI flag > env var > credentials file."""
    creds = load_credentials(args.profile)

    host = args.host or os.environ.get("PVE_HOST") or creds.get("host")
    token_id = args.token_id or os.environ.get("PVE_TOKEN_ID") or creds.get("token_id")
    token_secret = args.token_secret or os.environ.get("PVE_TOKEN_SECRET") or creds.get("token_secret")
    node = args.node or os.environ.get("PVE_NODE") or creds.get("node")

    if not host:
        sys.exit("Error: --host, PVE_HOST env, or 'host' in ~/.pve/credentials is required.")
    if not token_id:
        sys.exit("Error: --token-id, PVE_TOKEN_ID env, or 'token_id' in ~/.pve/credentials is required.")
    if not token_secret:
        sys.exit("Error: --token-secret, PVE_TOKEN_SECRET env, or 'token_secret' in ~/.pve/credentials is required.")

    if args.verify_ssl is not None:
        verify_ssl = args.verify_ssl
    elif os.environ.get("PVE_VERIFY_SSL", "").lower() == "true":
        verify_ssl = True
    elif creds.get("verify_ssl") == "true":
        verify_ssl = True
    else:
        verify_ssl = False

    return host, token_id, token_secret, node or None, verify_ssl


def parse_args():
    p = argparse.ArgumentParser(
        description="Deploy & upgrade OCI-based LXC containers on Proxmox VE 9.1+",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = p.add_subparsers(dest="command", required=True)

    # -- deploy subcommand --------------------------------------------------
    dp = sub.add_parser("deploy", help="Create or upgrade a container from an OCI image")
    _add_common_args(dp)
    dp.add_argument("--image", required=True,
                    help="OCI image reference, e.g. docker.io/library/nginx:2.0")
    dp.add_argument("--storage", default=None,
                    help="Storage for OCI image / templates (interactive if omitted)")
    dp.add_argument("--rootfs-storage", default=None,
                    help="Storage for container rootfs (interactive if omitted)")
    dp.add_argument("--no-backup", action="store_true", help="Skip config backup on upgrade")
    dp.add_argument("--poll-timeout", type=int, default=300)
    # Create-specific options (only used for fresh deploys)
    dp.add_argument("--hostname", help="Container hostname (defaults to image name)")
    dp.add_argument("--memory", type=int, default=512, help="Memory in MB (default: 512)")
    dp.add_argument("--swap", type=int, default=256, help="Swap in MB (default: 256)")
    dp.add_argument("--cores", type=int, default=1, help="CPU cores (default: 1)")
    dp.add_argument("--rootfs-size", default="8", help="Root disk size in GB (default: 8)")
    dp.add_argument("--net0", help="Network config, e.g. name=eth0,bridge=vmbr0,ip=dhcp")
    dp.add_argument("--net1", help="Second network interface (optional)")
    dp.add_argument("--mp0", help="Mount point 0, e.g. /data,mp=/app/data")
    dp.add_argument("--mp1", help="Mount point 1 (optional)")
    dp.add_argument("--mp2", help="Mount point 2 (optional)")
    # --unprivileged is the default; only --privileged flips it
    # No explicit --unprivileged flag needed (it's always true unless --privileged)
    dp.add_argument("--privileged", action="store_true",
                    help="Create as privileged container")
    dp.add_argument("--no-start", action="store_true",
                    help="Don't start after fresh create")
    dp.add_argument("--purge-on-upgrade", action="store_true",
                    help="Purge volumes, firewall refs, and replication jobs when destroying "
                         "during upgrade (safe for OCI containers; bind mounts unaffected)")

    # -- rollback subcommand ------------------------------------------------
    rb = sub.add_parser("rollback", help="Rollback a container to a previous config")
    _add_common_args(rb)
    rb.add_argument("--backup-file", required=True,
                    help="Path to the config backup JSON")
    rb.add_argument("--template", required=True,
                    help="Old template volid, e.g. local:vztmpl/nginx-1.0.tar.zst")
    rb.add_argument("--no-start", action="store_true",
                    help="Don't start the container after rollback")

    # -- destroy subcommand -------------------------------------------------
    ds = sub.add_parser("destroy", help="Stop and destroy a container")
    _add_common_args(ds)
    ds.add_argument("--purge", action="store_true",
                    help="Also remove replication jobs and firewall refs")

    # -- init subcommand ----------------------------------------------------
    ip = sub.add_parser("init", help="Create ~/.pve/credentials file interactively")
    ip.add_argument("--profile", default="default",
                    help="Profile name to create (default: default)")

    return p.parse_args()


def main():
    args = parse_args()

    if args.command == "init":
        init_credentials(args.profile)
        return

    host, token_id, token_secret, node, verify_ssl = _resolve_auth(args)

    if args.command == "deploy":
        # Collect net/mp args into dicts
        net = {}
        for i in range(2):
            val = getattr(args, f"net{i}", None)
            if val:
                net[f"net{i}"] = val
        mp = {}
        for i in range(3):
            val = getattr(args, f"mp{i}", None)
            if val:
                mp[f"mp{i}"] = val

        cfg = DeployConfig(
            host=host,
            token_id=token_id,
            token_secret=token_secret,
            vmid=args.vmid,
            image=args.image,
            node=node,
            storage=args.storage,
            rootfs_storage=args.rootfs_storage,
            verify_ssl=verify_ssl,
            backup_config=not args.no_backup,
            poll_timeout=args.poll_timeout,
            hostname=args.hostname,
            memory=args.memory,
            swap=args.swap,
            cores=args.cores,
            rootfs_size=args.rootfs_size,
            net=net,
            mp=mp,
            start_after_create=not args.no_start,
            unprivileged=not args.privileged,
            purge_on_upgrade=args.purge_on_upgrade,
        )
        deploy(cfg)

    elif args.command == "rollback":
        cfg = DeployConfig(
            host=host,
            token_id=token_id,
            token_secret=token_secret,
            vmid=args.vmid,
            image="",
            node=node,
            verify_ssl=verify_ssl,
        )
        rollback(cfg, args.backup_file, args.template, no_start=args.no_start)

    elif args.command == "destroy":
        cfg = DeployConfig(
            host=host,
            token_id=token_id,
            token_secret=token_secret,
            vmid=args.vmid,
            image="",
            node=node,
            verify_ssl=verify_ssl,
        )
        destroy_container(cfg, purge=args.purge)


if __name__ == "__main__":
    main()
