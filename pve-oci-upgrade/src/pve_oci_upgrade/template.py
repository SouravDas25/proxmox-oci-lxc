"""OCI image pull and template detection utilities."""

from datetime import datetime, timezone


class TemplateMixin:
    """Mixin providing template-related methods for PVEClient."""

    def _init_template_ts(self):
        """Set the timestamp used for this deploy's template filename."""
        if not hasattr(self, "_template_ts"):
            self._template_ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")

    def _image_base_name(self) -> str:
        """Derive a timestamped base name from the OCI reference."""
        self._init_template_ts()
        ref = self.cfg.image
        if "/" in ref:
            ref = ref.split("/")[-1]
        return f"{ref.replace(':', '-')}-{self._template_ts}"

    def pull_oci_image(self) -> str:
        """Pull an OCI image from a registry to the configured storage. Returns UPID."""
        self._init_template_ts()
        print(f"  Pulling image {self.cfg.image} to storage '{self.cfg.storage}' ...")
        return self.api.nodes(self.node).storage(self.cfg.storage).post(
            "oci-registry-pull", reference=self.cfg.image, filename=self._image_base_name())

    def template_volid(self) -> str:
        """Detect the pulled template volid by scanning storage."""
        base = self._image_base_name()
        try:
            contents = self.api.nodes(self.node).storage(self.cfg.storage).content.get(
                content="vztmpl")
            for item in contents:
                volid = item.get("volid", "")
                filename = volid.split("/")[-1] if "/" in volid else volid
                if filename.startswith(base):
                    return volid
        except Exception as e:
            print(f"  Warning: could not scan storage for template: {e}")
        return f"{self.cfg.storage}:vztmpl/{base}.tar"

    def delete_template(self, volid: str):
        """Delete a template from storage after successful deployment."""
        try:
            storage_name = volid.split(":")[0]
            self.api.nodes(self.node).storage(storage_name).content(volid).delete()
            print(f"  Cleaned up template: {volid}")
        except Exception as e:
            print(f"  Warning: could not delete template {volid}: {e}")
