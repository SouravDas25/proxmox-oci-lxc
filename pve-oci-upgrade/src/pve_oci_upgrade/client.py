"""Proxmox VE API client wrapper for OCI container operations."""

import time

from proxmoxer import ProxmoxAPI

from .config import DeployConfig
from .storage import StorageMixin
from .backup import BackupMixin
from .template import TemplateMixin


class PVEClient(StorageMixin, BackupMixin, TemplateMixin):
    """Thin wrapper around proxmoxer for OCI deploy/upgrade workflow."""

    SKIP_KEYS = {"digest", "lxc", "status"}
    READONLY_KEYS = {"lock"}
    POST_CREATE_KEYS = {"env"}

    def __init__(self, cfg: DeployConfig):
        self.cfg = cfg
        self.api = ProxmoxAPI(
            cfg.host,
            user=cfg.token_id.split("!")[0],
            token_name=cfg.token_id.split("!")[1],
            token_value=cfg.token_secret,
            verify_ssl=cfg.verify_ssl,
            timeout=30,
        )
        self.node = cfg.node or self._detect_node()

    def _detect_node(self) -> str:
        """Auto-detect the node the VMID lives on."""
        from proxmoxer.core import ResourceException
        nodes = self.api.nodes.get()
        for node in nodes:
            try:
                self.api.nodes(node["node"]).lxc(self.cfg.vmid).status.current.get()
                return node["node"]
            except ResourceException as e:
                if e.status_code == 404:
                    continue
                if e.status_code == 500 and "does not exist" in str(e):
                    continue
                print(f"  Warning: could not query node '{node['node']}': {e}")
            except Exception as e:
                print(f"  Warning: could not query node '{node['node']}': {e}")
        online_nodes = [n["node"] for n in nodes if n.get("status") == "online"]
        if not online_nodes:
            raise RuntimeError("No online Proxmox node found")
        if len(online_nodes) == 1:
            return online_nodes[0]
        raise RuntimeError(
            f"Container {self.cfg.vmid} not found and cluster has multiple nodes "
            f"({', '.join(online_nodes)}). Use --node to specify the target node.")

    def container_exists(self) -> bool:
        """Check if the VMID already exists on this node."""
        from proxmoxer.core import ResourceException
        try:
            self.api.nodes(self.node).lxc(self.cfg.vmid).status.current.get()
            return True
        except ResourceException as e:
            if e.status_code == 404:
                return False
            if e.status_code == 500 and "does not exist" in str(e):
                return False
            raise

    def get_config(self) -> dict:
        return self.api.nodes(self.node).lxc(self.cfg.vmid).config.get()

    def get_status(self) -> dict:
        return self.api.nodes(self.node).lxc(self.cfg.vmid).status.current.get()

    def stop(self) -> str:
        print(f"  Stopping container {self.cfg.vmid} ...")
        return self.api.nodes(self.node).lxc(self.cfg.vmid).status.shutdown.post(
            forceStop=1, timeout=30)

    def destroy(self, purge: bool = False) -> str:
        print(f"  Destroying container {self.cfg.vmid} (purge={purge}) ...")
        return self.api.nodes(self.node).lxc(self.cfg.vmid).delete(purge=int(purge))

    def start(self) -> str:
        print(f"  Starting container {self.cfg.vmid} ...")
        return self.api.nodes(self.node).lxc(self.cfg.vmid).status.start.post()

    def create(self, ostemplate: str, config: dict) -> str:
        """Recreate container from saved config."""
        print(f"  Creating container {self.cfg.vmid} from {ostemplate} ...")
        params = self._config_to_create_params(config)
        params["vmid"] = self.cfg.vmid
        params["ostemplate"] = ostemplate
        return self.api.nodes(self.node).lxc.create(**params)

    def create_fresh(self, ostemplate: str) -> str:
        """Create a brand new container from CLI-provided settings."""
        print(f"  Creating new container {self.cfg.vmid} from {ostemplate} ...")
        params = {
            "vmid": self.cfg.vmid, "ostemplate": ostemplate,
            "memory": self.cfg.memory, "swap": self.cfg.swap, "cores": self.cfg.cores,
            "rootfs": f"{self.cfg.rootfs_storage}:{self.cfg.rootfs_size}",
            "unprivileged": int(self.cfg.unprivileged),
            "start": int(self.cfg.start_after_create),
        }
        if self.cfg.hostname:
            params["hostname"] = self.cfg.hostname
        for key, val in self.cfg.net.items():
            params[key] = val
        for key, val in self.cfg.mp.items():
            params[key] = val
        return self.api.nodes(self.node).lxc.create(**params)

    def apply_env(self):
        """Add manifest env vars that aren't already set on the container.

        The deployed container's env is the source of truth: existing values are
        never overwritten. Manifest keys only fill in variables that are missing
        (e.g. a new var introduced by a newer app version).
        """
        if not self.cfg.env:
            return
        print(f"  Setting environment variables ...")
        config = self.api.nodes(self.node).lxc(self.cfg.vmid).config.get()
        existing = config.get("env", "")
        env_dict = {}
        for entry in existing.split("\0"):
            if "=" in entry:
                k, v = entry.split("=", 1)
                env_dict[k] = v
        for entry in self.cfg.env.split("\0"):
            if "=" in entry:
                k, v = entry.split("=", 1)
                env_dict.setdefault(k, v)  # only add if not already on the container
        merged = "\0".join(f"{k}={v}" for k, v in env_dict.items())
        self.api.nodes(self.node).lxc(self.cfg.vmid).config.put(env=merged)

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

    def _config_to_create_params(self, config: dict) -> dict:
        params = {}
        skip = self.SKIP_KEYS | self.READONLY_KEYS | self.POST_CREATE_KEYS
        for k, v in config.items():
            if k in skip:
                continue
            params[k] = v
        params.pop("ostype", None)
        rootfs = params.get("rootfs", "")
        if rootfs:
            parts = rootfs.split(",")
            storage_vol = parts[0]
            size = None
            for p in parts[1:]:
                if p.startswith("size="):
                    size = p.split("=")[1].rstrip("GgMm")
                    break
            if size is None:
                size = self.cfg.rootfs_size
                print(f"  Warning: original rootfs had no size= parameter, using {size}G as fallback.")
            storage_name = storage_vol.split(":")[0]
            params["rootfs"] = f"{storage_name}:{size}"
        return params

    def reapply_post_create_config(self, config: dict):
        """Re-apply env vars that get overwritten by OCI image defaults during create."""
        if "env" not in config:
            return
        print(f"  Re-applying env vars ...")
        self.api.nodes(self.node).lxc(self.cfg.vmid).config.put(env=config["env"])
