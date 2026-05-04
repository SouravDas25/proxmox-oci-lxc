# Deploy & upgrade OCI-based LXC containers on Proxmox

Deploy & upgrade OCI-based LXC containers on Proxmox VE 9.1+, no Docker needed.

Single `deploy` command — creates the container if it doesn't exist, upgrades it if it does.

## Install

```bash
pip install -e .
```

This installs the `pve-oci` command globally. You can also run it directly with `python pve_oci_upgrade.py`.

## Setup

Create an API token in Proxmox: Datacenter → Permissions → API Tokens.
The token needs `VM.Allocate`, `VM.Audit`, `Datastore.AllocateTemplate`, `Datastore.Allocate` privileges,
plus `Sys.Audit` and `Sys.Modify` on `/` (required for pulling OCI images and vzdump backups).

## Authentication

Auth is resolved in this order (first wins): CLI flags → environment variables → credentials file.

### Credentials file (recommended)

```bash
pve-oci init
pve-oci init --profile staging   # named profile
```

This creates an INI-style file at `~/.pve/credentials`:

```ini
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
```

Use `--profile staging` on any command to select a non-default profile.

### Environment variables

```bash
export PVE_HOST=192.168.1.10
export PVE_TOKEN_ID=user@pam!mytoken
export PVE_TOKEN_SECRET=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
```

These override values from the credentials file.

## Usage

### First deploy (creates the container)

```bash
pve-oci deploy \
    --vmid 100 \
    --image docker.io/library/nginx:1.0 \
    --hostname my-nginx \
    --memory 512 \
    --cores 2 \
    --net0 "name=eth0,bridge=vmbr0,ip=dhcp" \
    --mp0 "/mnt/data,mp=/app/data"
```

### Upgrade to a new version (same command)

```bash
pve-oci deploy \
    --vmid 100 \
    --image docker.io/library/nginx:2.0
```

On upgrade, the tool:
1. Saves the existing config to `pve-ct-<vmid>-config-backup.json`
2. Stops the container
3. Creates a vzdump backup as a safety net
4. Destroys the old container
5. Recreates with the new image and the saved config
6. Starts it back up
7. Deletes the vzdump backup on success

If recreate fails, the tool automatically restores from the vzdump backup.
The vzdump backup is only cleaned up after a fully successful upgrade.

### Upgrade with full purge

For OCI containers where the rootfs is disposable, use `--purge-on-upgrade`
to let Proxmox clean up all PVE-managed volumes, replication jobs, and firewall
references during the destroy step. Bind mounts (host paths) are not affected.

```bash
pve-oci deploy \
    --vmid 100 \
    --image docker.io/library/nginx:2.0 \
    --purge-on-upgrade
```

Without `--purge-on-upgrade`, only the old rootfs volume is cleaned up after
a successful upgrade.

### Rollback

Every upgrade saves a config backup as `pve-ct-<vmid>-config-backup.json`.

```bash
pve-oci rollback \
    --vmid 100 \
    --backup-file pve-ct-100-config-backup.json \
    --template local:vztmpl/nginx-1.0.tar.zst
```

Use `--no-start` to restore the container without starting it:

```bash
pve-oci rollback \
    --vmid 100 \
    --backup-file pve-ct-100-config-backup.json \
    --template local:vztmpl/nginx-1.0.tar.zst \
    --no-start
```

### Destroy

```bash
pve-oci destroy --vmid 100 --purge
```

If destroy fails due to missing volumes (e.g. orphaned RBD references),
the tool prints the `rm /etc/pve/lxc/<vmid>.conf` command to run on the node.

## Deploy options

| Flag | Default | Description |
|------|---------|-------------|
| `--storage` | interactive | Storage for OCI image / templates / vzdump backups |
| `--rootfs-storage` | interactive | Storage for container rootfs |
| `--hostname` | image name | Container hostname |
| `--memory` | 512 | Memory in MB |
| `--swap` | 256 | Swap in MB |
| `--cores` | 1 | CPU cores |
| `--rootfs-size` | 8 | Root disk size in GB |
| `--net0` | — | Network config |
| `--mp0..mp2` | — | Mount points for persistent data |
| `--privileged` | false | Run as privileged container |
| `--no-start` | false | Don't auto-start after create |
| `--no-backup` | false | Skip config backup on upgrade |
| `--purge-on-upgrade` | false | Purge all PVE-managed volumes/refs on upgrade destroy |
| `--poll-timeout` | 300 | Task polling timeout in seconds |
| `--profile` | default | Credentials profile to use |

## Rollback options

| Flag | Default | Description |
|------|---------|-------------|
| `--backup-file` | required | Path to the config backup JSON |
| `--template` | required | Old template volid, e.g. `local:vztmpl/nginx-1.0.tar.zst` |
| `--no-start` | false | Don't start the container after rollback |

## Important

- Always use bind mount points (`--mp0`) for persistent data — the rootfs gets replaced on upgrade.
  Proxmox-managed volumes (e.g. `local-lvm:8`) are destroyed along with the container.
  Only host bind mounts (e.g. `/mnt/data,mp=/app/data`) survive a destroy/recreate cycle.
- Old templates are kept (timestamped) so you can rollback.
- Node is auto-detected. Use `--node` to override.
- Template file extension is auto-detected after pull (handles `.tar`, `.tar.zst`, `.tar.gz`).
- Upgrades create a temporary vzdump backup for safe recovery. It's automatically deleted on success.
