#!/bin/bash
# Audio Encoder (AC3) for Linux
# -----------------------------
# Place mkv files in this folder to process them.
# Output goes to "ac3-output"

# Resolution: Get script directory to find tools relative to it
SCRIPT_DIR="$(dirname "$(realpath "$0")")"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
TOOLS_DIR="$ROOT_DIR/tools"
AC3_SCRIPT="$TOOLS_DIR/audio-ac3.py"

echo "============================================================"
echo "      Audio Encoder (AC3) - Linux"
echo "============================================================"
echo ""
echo "This script is included for people who have older audio hardware and need AC3 Dolby audio."
echo "Generally, if you want Dolby audio, you should be using the EAC3 script."
echo ""
echo "Place mkv files in this folder (audio-encoding)."
echo ""

if [ ! -f "$AC3_SCRIPT" ]; then
    echo "[ERROR] Could not find audio-ac3.py at: $AC3_SCRIPT"
    exit 1
fi

# Run the python script
python3 "$AC3_SCRIPT"

echo ""
echo "Workflow finished."
echo "Cleaning up temporary extracted files..."
rm *.flac *.ac3 *.thd *.dtshd *.dts *.aac *.opus *.eac3 *.wav 2>/dev/null || true

read -p "Press Enter to exit..."
