"""Storage discovery and cleanup utilities."""


class StorageMixin:
    """Mixin providing storage-related methods for PVEClient."""

    def list_rootfs_storages(self) -> list[dict]:
        """Return storages on this node that support container rootfs (rootdir content)."""
        storages = self.api.nodes(self.node).storage.get(content="rootdir", enabled=1)
        return [
            {"storage": s["storage"], "type": s.get("type", "?"),
             "avail": s.get("avail", 0), "total": s.get("total", 0)}
            for s in storages
        ]

    def list_template_storages(self) -> list[dict]:
        """Return storages on this node that support container templates (vztmpl content)."""
        storages = self.api.nodes(self.node).storage.get(content="vztmpl", enabled=1)
        return [
            {"storage": s["storage"], "type": s.get("type", "?"),
             "avail": s.get("avail", 0), "total": s.get("total", 0)}
            for s in storages
        ]

    def cleanup_old_rootfs(self, old_config: dict):
        """Remove the old rootfs volume after a successful upgrade, only if it differs from the new one."""
        old_rootfs = old_config.get("rootfs", "")
        if not old_rootfs:
            return
        old_volid = old_rootfs.split(",")[0]
        if ":" not in old_volid or "vm-" not in old_volid:
            return

        # Check new container's rootfs — skip if same volume was reused
        try:
            new_config = self.api.nodes(self.node).lxc(self.cfg.vmid).config.get()
            new_volid = new_config.get("rootfs", "").split(",")[0]
            if old_volid == new_volid:
                print(f"  Skipping rootfs cleanup: volume {old_volid} is reused by new container.")
                return
        except Exception:
            pass

        try:
            print(f"  Cleaning up old rootfs volume: {old_volid}")
            storage_name = old_volid.split(":")[0]
            self.api.nodes(self.node).storage(storage_name).content(old_volid).delete()
        except Exception as e:
            print(f"  Warning: could not remove old rootfs volume {old_volid}: {e}")
