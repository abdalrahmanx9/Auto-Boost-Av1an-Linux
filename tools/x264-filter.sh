#!/bin/bash
# Lossless x264 intermediary using the settings.txt filter chain.
# Backend for tools/x264-filter.py - see that file for the full explanation.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.." || exit 1

exec python3 "tools/x264-filter.py" "$@"
