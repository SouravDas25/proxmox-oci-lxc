#!/bin/bash
# Create a test LXC container from an OCI image
# Usage: ./create-test-lxc.sh

# --- Configuration ---

VMID=999
NODE="kiki"
IMAGE="docker.io/library/alpine:latest"
HOSTNAME="test-alpine"
MEMORY=128
CORES=1
# STORAGE="local"             # template/image storage — leave unset to pick interactively
# ROOTFS_STORAGE="local-lvm"  # leave unset to pick interactively

# --- Run ---

pve-oci deploy \
    --vmid "$VMID" \
    --node "$NODE" \
    --image "$IMAGE" \
    --hostname "$HOSTNAME" \
    --memory "$MEMORY" \
    --cores "$CORES"
