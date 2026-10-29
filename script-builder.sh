#!/bin/bash
# Launcher for the Auto-Boost/Av1an Script Builder
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR" || exit 1

exec python3 "tools/script-builder.py" "$@"
