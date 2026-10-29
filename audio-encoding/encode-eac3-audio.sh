#!/bin/bash
# Audio Encoder (EAC3) for Linux
# ------------------------------
# Place mkv files in this folder to process them.
# Output goes to "eac3-output"

# Resolution: Get script directory to find tools relative to it
SCRIPT_DIR="$(dirname "$(realpath "$0")")"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
TOOLS_DIR="$ROOT_DIR/tools"
EAC3_SCRIPT="$TOOLS_DIR/audio-eac3.py"

echo "============================================================"
echo "      Audio Encoder (EAC3) - Linux"
echo "============================================================"
echo ""
echo "Place mkv files in this folder (audio-encoding)."
echo ""
echo "Re-encoding compressed audio degrades quality like a copy of a copy,"
echo "so we recommend encoding only lossless tracks."
echo ""
echo "This script converts audio tracks to EAC3 (Dolby Digital Plus)"
echo "using FFMPEG and muxes them into new files in 'eac3-output'."
echo ""

if [ ! -f "$EAC3_SCRIPT" ]; then
    echo "[ERROR] Could not find audio-eac3.py at: $EAC3_SCRIPT"
    exit 1
fi

# Run the python script
python3 "$EAC3_SCRIPT"

echo ""
echo "Workflow finished."
echo "Cleaning up temporary extracted files..."
rm *.flac *.ac3 *.thd *.dtshd *.dts *.aac *.opus *.eac3 *.wav 2>/dev/null || true

read -p "Press Enter to exit..."
