#!/bin/bash
# Audio Encoder (Opus) for Linux
# ------------------------------
# Place mkv files in this folder to process them.
# Output goes to "opus-output"

# Resolution: Get script directory to find tools relative to it
SCRIPT_DIR="$(dirname "$(realpath "$0")")"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
TOOLS_DIR="$ROOT_DIR/tools"
OPUS_SCRIPT="$TOOLS_DIR/audio-opus.py"

echo "============================================================"
echo "      Audio Encoder (Opus) - Linux"
echo "============================================================"
echo ""
echo "Place mkv files in this folder (audio-encoding)."
echo ""
echo "Re-encoding compressed audio degrades quality like a copy of a copy,"
echo "so we recommend encoding only lossless tracks."
echo ""
echo "The opus audio (or preserved original audio) will be muxed"
echo "into new files inside the 'opus-output' folder."
echo ""

if [ ! -f "$OPUS_SCRIPT" ]; then
    echo "[ERROR] Could not find audio-opus.py at: $OPUS_SCRIPT"
    exit 1
fi

# Run the python script
python3 "$OPUS_SCRIPT"

echo ""
echo "Workflow finished."
echo "Cleaning up temporary extracted files..."
rm *.flac *.ac3 *.thd *.dtshd *.dts *.aac *.opus *.wav 2>/dev/null || true

read -p "Press Enter to exit..."
