"""CLI entry point for pve-oci command."""

import argparse
import os
import sys

from .config import DeployConfig
from .credentials import load_credentials, init_credentials
from .deploy import deploy
from .destroy import destroy_container
from .rollback import rollback


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
