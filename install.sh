#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PKG_DIR="$SCRIPT_DIR/pve-oci-upgrade"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC}  $*"; }
err()  { echo -e "${RED}[ERROR]${NC} $*"; }

usage() {
    cat <<EOF
Usage: $0 [OPTIONS]

Install pve-oci-upgrade from source.

Options:
  -e, --editable   Install in editable/development mode
  -p, --production Install in production mode (non-editable, default)
  -h, --help       Show this help message
EOF
    exit 0
}

MODE="production"
while [[ $# -gt 0 ]]; do
    case "$1" in
        -e|--editable)   MODE="editable"; shift ;;
        -p|--production) MODE="production"; shift ;;
        -h|--help)       usage ;;
        *) err "Unknown option: $1"; usage ;;
    esac
done

log "Installing pve-oci-upgrade v0.1.0 from source"

PYTHON=""
for cmd in python3 python; do
    if command -v "$cmd" &>/dev/null; then
        ver=$("$cmd" -c 'import sys; print(sys.version_info[:2])' 2>/dev/null || true)
        if [[ -n "$ver" ]]; then
            major=$(echo "$ver" | sed 's/[^0-9]//g' | cut -c1-2)
            if [[ "$major" -ge 31 ]]; then
                PYTHON="$cmd"
                break
            fi
        fi
    fi
done

if [[ -z "$PYTHON" ]]; then
    err "Python 3.10+ is required but was not found."
    err "Install it with: apt install python3 python3-pip python3-venv"
    exit 1
fi

log "Using Python: $($PYTHON --version)"

if ! "$PYTHON" -m pip --version &>/dev/null; then
    log "Installing pip..."
    "$PYTHON" -m ensurepip --upgrade 2>/dev/null || {
        err "Failed to install pip. Try: apt install python3-pip"
        exit 1
    }
fi

if "$PYTHON" -m pip show pve-oci-upgrade &>/dev/null; then
    log "Uninstalling existing pve-oci-upgrade..."
    "$PYTHON" -m pip uninstall -y pve-oci-upgrade --quiet
fi

log "Upgrading pip and build tools..."
"$PYTHON" -m pip install --upgrade pip setuptools wheel --quiet

if [[ ! -d "$PKG_DIR" ]]; then
    err "Package directory not found: $PKG_DIR"
    exit 1
fi

log "Installing runtime dependencies..."
"$PYTHON" -m pip install -r "$PKG_DIR/requirements.txt" --quiet

# pyyaml is listed in pyproject.toml but not requirements.txt; ensure it's installed
"$PYTHON" -m pip install pyyaml --quiet

if [[ "$MODE" == "editable" ]]; then
    log "Installing package in editable mode: $PKG_DIR"
    "$PYTHON" -m pip install -e "$PKG_DIR" --quiet
else
    log "Installing package in production mode: $PKG_DIR"
    "$PYTHON" -m pip install "$PKG_DIR" --quiet
fi

log "Verifying installation..."

if command -v pve-oci &>/dev/null; then
    pve-oci --help &>/dev/null || true
    log "CLI command 'pve-oci' is available."
else
    warn "CLI command 'pve-oci' not found on PATH."
    warn "You may need to add $HOME/.local/bin to your PATH."
    warn "Or run via: $PYTHON -m pve_oci_upgrade"
fi

log ""
log "============================================"
log "Installation complete!"
log ""
log "Next steps:"
log "  1. Run 'pve-oci init' to configure Proxmox credentials"
log "  2. Create a YAML manifest (see examples/containers.yml)"
log "  3. Validate:  pve-oci validate <manifest.yml>"
log "  4. Deploy:    pve-oci apply <manifest.yml>"
log "============================================"
