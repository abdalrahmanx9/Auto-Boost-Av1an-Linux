#!/bin/bash
# Launcher for the Condor target-quality builder
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR" || exit 1
exec python3 "tools/condor-builder.py" "$@"
