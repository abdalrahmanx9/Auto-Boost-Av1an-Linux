#!/bin/bash
# Auto-Boost-Av1an: Opus Audio Encoder Workflow
# Converts audio in MKV files to Opus format using tools/audio-opus.py

# Resolve paths
SCRIPT_DIR="$(dirname "$(realpath "$0")")"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
TOOL_SCRIPT="$ROOT_DIR/tools/audio-opus.py"

if [ ! -f "$TOOL_SCRIPT" ]; then
    echo "Error: tools/audio-opus.py not found!"
    exit 1
fi

echo "Starting Opus Audio Workflow..."
echo "This will scan 'video-input/' and current folder for MKVs and encode audio to Opus."
echo ""

python3 "$TOOL_SCRIPT"

echo ""
echo "Workflow finished."

# Cleanup intermediate files from current directory if any (audio-opus.py handles its own temp dirs mostly)
rm -f *.flac *.ac3 *.thd *.dtshd *.opus 2>/dev/null
