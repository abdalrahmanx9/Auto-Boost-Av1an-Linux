#!/bin/bash
# This script uses av1an-dispatch.py to call av1an directly.
av1an_settings="--scd 0 --distortion-bias-preset 1 --photon-noise 400"
FINAL_SPEED="4"
CRF="30"
# Set photon noise to 0 if using film-grain
fork="essential"
# example forks: 5fish, essential, hdr, custom
DENOISE="False"
# DENOISE updates denoise=True/False in settings.txt before dispatch. 5fish should use True; other forks default to False.
ARCH="x86-64-v3"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR" || exit 1
rm -f tools/bat*.txt
mv -n *.mkv *.mp4 *.m2ts video-input/ 2>/dev/null
touch "tools/bat-used-$(basename "$0").txt"
export PATH="$SCRIPT_DIR/tools/av1an:$PATH"

read_worker_count() {
    grep -E "^workers=" tools/workercount-config.txt 2>/dev/null | tail -n 1 | cut -d= -f2
}
WORKER_COUNT_CFG="$(read_worker_count)"
if [ -z "$WORKER_COUNT_CFG" ]; then
    echo "-------------------------------------------------------------------------------"
    echo "First Run Detected: Calculating optimal encode worker count..."
    echo "-------------------------------------------------------------------------------"
    python3 "$SCRIPT_DIR/tools/workercount.py"
    WORKER_COUNT_CFG="$(read_worker_count)"
    echo "Encode worker count calculated."
fi
WORKER_COUNT="${WORKER_COUNT_CFG:-4}"

# --- RENAMING ---
echo "Starting Renaming Process..."
python3 "$SCRIPT_DIR/tools/rename.py"

echo "Starting Av1an Direct Dispatcher..."
echo "Encoding inputs from: video-input"
echo "Outputs will go to:   video-output"

python3 "$SCRIPT_DIR/tools/av1an-dispatch.py" --resume --fork "$fork" --arch "$ARCH" --denoise "$DENOISE" --autocrop --crf "$CRF" --workers "$WORKER_COUNT" --final-speed "$FINAL_SPEED" --final-params "$av1an_settings"

echo ""
echo "All tasks finished."
echo "Press Enter to clean up temp files (Ctrl+C to keep them and exit)."
read -r _

echo "Cleaning up temporary files and folders..."
python3 "$SCRIPT_DIR/tools/cleanup.py"
