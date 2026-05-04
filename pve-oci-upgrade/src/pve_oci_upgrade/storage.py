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

    def cleanup_old_rootfs(self, config: dict):
        """Remove the old rootfs volume after a successful upgrade."""
        rootfs = config.get("rootfs", "")
        if not rootfs:
            return
        volid = rootfs.split(",")[0]
        if ":" in volid and "vm-" in volid:
            try:
                print(f"  Cleaning up old rootfs volume: {volid}")
                storage_name = volid.split(":")[0]
                self.api.nodes(self.node).storage(storage_name).content(volid).delete()
            except Exception as e:
                print(f"  Warning: could not remove old rootfs volume {volid}: {e}")
