#!/bin/bash
# Upgrade an existing LXC container to a new OCI image
# Usage: ./upgrade-test-lxc.sh


VMID=999
NODE="kiki"
IMAGE="docker.io/library/alpine:edge"


pve-oci deploy \
    --vmid "$VMID" \
    --node "$NODE" \
    --image "$IMAGE"
