#!/bin/bash
# Never add noise/grain to fast params, this will break metrics.
FAST_PARAMS="--scd 0 --lineart-psy-bias 3 --texture-psy-bias 3 --hbd-mds 0"
FINAL_PARAMS="--scd 0 --lineart-psy-bias 3 --texture-psy-bias 3 --hbd-mds 1 --lp 3 --photon-noise 200"
FINAL_SPEED="4"
CRF="30"
# Set photon noise to 0 if using film-grain
fork="5fish"
# example forks: 5fish, essential, hdr, custom
DENOISE="True"
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

read_ssimu2_key() {
    grep -E "^$1=" tools/workercount-ssimu2.txt 2>/dev/null | tail -n 1 | cut -d= -f2
}
if [ -f tools/workercount-ssimu2.txt ]; then
    SSIMU2_TOOL="$(read_ssimu2_key tool)"
    SSIMU2_WORKERS="$(read_ssimu2_key workercount)"
else
    echo "-------------------------------------------------------------------------------"
    echo "First Run Detected: Calculating optimal SSIMU2 settings..."
    echo "-------------------------------------------------------------------------------"
    python3 "$SCRIPT_DIR/tools/ssimu2-workercount.py"
    SSIMU2_TOOL="$(read_ssimu2_key tool)"
    SSIMU2_WORKERS="$(read_ssimu2_key workercount)"
fi
SSIMU2_TOOL="${SSIMU2_TOOL:-vs-hip}"
SSIMU2_WORKERS="${SSIMU2_WORKERS:-1}"

echo "Starting Auto-Boost-Av1an Dispatcher..."
echo "Encoding inputs from: video-input"
echo "Outputs will go to:   video-output"

python3 "$SCRIPT_DIR/tools/dispatch.py" --fork "$fork" --arch "$ARCH" --denoise "$DENOISE" --crf "$CRF" --ssimu2 "$SSIMU2_TOOL" --ssimu2-cpu-workers "$SSIMU2_WORKERS" --resume --fast-speed 8 --final-speed "$FINAL_SPEED" --workers "$WORKER_COUNT" --fast-params "$FAST_PARAMS" --final-params "$FINAL_PARAMS"

echo ""
echo "All tasks finished."
echo "Press Enter to clean up temp files (Ctrl+C to keep them and exit)."
read -r _

echo "Cleaning up temporary files and folders..."
python3 "$SCRIPT_DIR/tools/cleanup.py"
