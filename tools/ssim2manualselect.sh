#!/bin/bash
# ssim2manualselect.sh - Interactive manual selection of SSIMULACRA2 backend

BASE_DIR="$(dirname "$(realpath "$0")")/.."
PYTHON_BIN="python3"

if ! command -v "$PYTHON_BIN" &> /dev/null; then
    PYTHON_BIN="python"
fi

"$PYTHON_BIN" "$BASE_DIR/tools/ssim2manualselect.py" "$@"
