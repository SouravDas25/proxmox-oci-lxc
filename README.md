# pve-oci

Deploy and manage OCI-based LXC containers on Proxmox VE 9.1+ using YAML configuration.

## Features

- **YAML-based deployment** — Define containers declaratively like docker-compose
- **OCI image support** — Pull images directly from Docker Hub, GHCR, etc.
- **Upgrade with rollback** — Automatic backup before upgrades, restore on failure
- **Generate from existing** — Export running containers to YAML
- **Storage validation** — Validates storage compatibility before deployment

## Installation

```bash
cd pve-oci-upgrade
pip install -e .
```

Or run without installing:
```bash
$env:PYTHONPATH = "pve-oci-upgrade\src"  # PowerShell
export PYTHONPATH=pve-oci-upgrade/src    # Bash
python -m pve_oci_upgrade <command>
```

## Quick Start

### 1. Initialize credentials

```bash
pve-oci init
```

Creates `~/.pve/credentials` with your Proxmox host, API token, and node.

### 2. Create a YAML config

```yaml
# containers.yml
auth:
  profile: default
  node: pve1

containers:
  - vmid: 100
    image: docker.io/library/nginx:alpine
    hostname: web1
    memory: 512
    cores: 2
    storage: cephfs
    rootfs_storage: local-lvm
    net:
      - name=eth0,bridge=vmbr0,ip=dhcp
```

### 3. Deploy

```bash
pve-oci apply containers.yml
```

### 4. Destroy

```bash
pve-oci destroy -f containers.yml --purge
```

## Commands

| Command | Description |
|---------|-------------|
| `apply <file>` | Deploy/upgrade containers from YAML |
| `destroy -f <file>` | Destroy containers defined in YAML |
| `generate -o <file>` | Generate YAML from existing Proxmox containers |
| `validate <file>` | Validate YAML without deploying |
| `deploy --vmid <id> --image <ref>` | Deploy single container (CLI flags) |
| `rollback --vmid <id> --backup-file <path>` | Rollback to previous config |
| `init` | Create credentials file interactively |

## YAML Schema

```yaml
auth:
  profile: default        # Credentials profile (~/.pve/credentials)
  host: 192.168.1.10      # Proxmox host (optional, overrides profile)
  node: pve1              # Target node (optional)

defaults:                 # Shared defaults for all containers
  storage: cephfs
  rootfs_storage: local-lvm
  memory: 512

containers:
  - vmid: 100             # Required
    image: docker.io/library/nginx:alpine  # Required
    hostname: web1
    memory: 1024
    swap: 256
    cores: 2
    rootfs_size: "8"
    storage: cephfs           # Template storage
    rootfs_storage: local-lvm # Container rootfs storage
    privileged: false
    purge_on_upgrade: false
    net:
      - name=eth0,bridge=vmbr0,ip=dhcp
    mp:
      - /mnt/data,mp=/data
    environment:
      APP_ENV: production
```

## Generate YAML from Existing Containers

```bash
# All containers
pve-oci generate -o my-infra.yml

# Specific VMIDs
pve-oci generate --vmid 100 101 102 -o selected.yml

# Filter by node
pve-oci generate --node pve1 -o node1.yml
```

## Credentials

Create `~/.pve/credentials`:

```ini
[default]
host = 192.168.1.10
token_id = user@pam!mytoken
token_secret = xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
node = pve1
verify_ssl = false
```

Or use environment variables:
- `PVE_HOST`
- `PVE_TOKEN_ID`
- `PVE_TOKEN_SECRET`
- `PVE_NODE`

## Requirements

- Proxmox VE 9.1+ (OCI registry pull support)
- Python 3.10+
- API token with PVEAdmin or appropriate permissions

## License

MIT
