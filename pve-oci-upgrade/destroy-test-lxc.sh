#!/bin/bash
# Destroy the test LXC container
# Usage: ./destroy-test-lxc.sh

# --- Configuration ---

VMID=999
NODE="kiki"

pve-oci destroy \
    --vmid "$VMID" \
    --node "$NODE" \
    --purge
