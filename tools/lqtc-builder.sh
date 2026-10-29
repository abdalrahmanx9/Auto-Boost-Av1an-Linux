#!/bin/bash
# Launcher for the LQTC (Large Quality Target Collider) builder
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR" || exit 1
exec python3 "tools/lqtc-builder.py" "$@"
