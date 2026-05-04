"""Vzdump backup and restore utilities."""


class BackupMixin:
    """Mixin providing backup-related methods for PVEClient."""

    def vzdump_backup(self, storage: str) -> str:
        """Create a vzdump backup of the container. Returns UPID."""
        print(f"  Creating vzdump backup of container {self.cfg.vmid} on '{storage}' ...")
        return self.api.nodes(self.node).vzdump.create(
            vmid=self.cfg.vmid, mode="stop", compress="zstd", storage=storage)

    def find_latest_backup(self, storage: str) -> str | None:
        """Find the most recent vzdump backup for this VMID on the given storage."""
        try:
            contents = self.api.nodes(self.node).storage(storage).content.get(
                content="backup", vmid=self.cfg.vmid)
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
            vmid=self.cfg.vmid, ostemplate=backup_volid, restore=1)

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
