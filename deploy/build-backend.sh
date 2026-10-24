#!/usr/bin/env bash
set -euo pipefail
# Build after applying deploy/browser-source.patch to upstream tag v4.23.0.
# The source directory must contain no credentials.
cd /root/gemini-web-browser-source
docker build -t gemini-web-browser-source:4.23.0 .
# A full upstream build must explicitly enable this on container creation.
# The small cross-compiled image in Dockerfile.browser-source sets it by default.
# Never prune images, containers, rollback artifacts, or data volumes.
docker builder prune --force --keep-storage 5GB
