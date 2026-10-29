#!/bin/bash
# Creates a video-only MKV sample capped at 9.98 MB, small enough to upload
# to Discord. Place your MKV file in this folder and run this script.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR" || exit 1

MAX_SIZE=9980000

read -r -p "Place your MKV file in this folder and press Enter to continue..."

MKVMERGE="$(command -v mkvmerge || echo "../tools/MKVToolNix/mkvmerge")"
if ! command -v mkvmerge &> /dev/null && [ ! -x "$MKVMERGE" ]; then
    echo "[ERROR] mkvmerge not found. Install mkvtoolnix (apt install mkvtoolnix)."
    read -r -p "Press Enter to exit..."
    exit 1
fi

INPUT_FILE=""
for f in *.mkv; do
    [ -e "$f" ] || continue
    INPUT_FILE="$f"
    break
done
if [ -z "$INPUT_FILE" ]; then
    echo "[ERROR] No MKV file found in this folder."
    read -r -p "Press Enter to exit..."
    exit 1
fi

OUT_BASE="${INPUT_FILE%.*}"
TEMP="discord_10mb_sample_temp.mkv"

# Start from the last 10 minutes, walk backwards until under the cap.
START="00:10:00"
for _ in 1 2 3 4 5 6 7 8 9 10; do
    rm -f "$TEMP"
    "$MKVMERGE" -o "$TEMP" --no-audio --no-subtitles --no-buttons \
        --no-track-tags --no-attachments --no-chapters --split parts:"$START"- \
        "$INPUT_FILE" > /dev/null 2>&1
    # Keep only the first (last-10-min) part
    if [ -f "${TEMP}-001.mkv" ]; then
        SIZE=$(stat -c %s "${TEMP}-001.mkv" 2>/dev/null || echo 0)
        if [ "$SIZE" -le "$MAX_SIZE" ]; then
            mv "${TEMP}-001.mkv" "${OUT_BASE}-discord-sample.mkv"
            rm -f "$TEMP"-*.mkv "$TEMP" 2>/dev/null
            echo "Created: ${OUT_BASE}-discord-sample.mkv ($SIZE bytes)"
            read -r -p "Press Enter to exit..."
            exit 0
        fi
    fi
    # Halve the sample window
    case "$START" in
        00:10:00) START="00:05:00" ;;
        00:05:00) START="00:02:30" ;;
        00:02:30) START="00:01:15" ;;
        00:01:15) START="00:00:40" ;;
        *)        START="00:00:15" ;;
    esac
done

echo "[ERROR] Could not get under the size cap; the video may be too short."
rm -f "$TEMP" "$TEMP"-*.mkv 2>/dev/null
read -r -p "Press Enter to exit..."
exit 1
