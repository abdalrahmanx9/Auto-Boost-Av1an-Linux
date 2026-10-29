#!/bin/bash
# =============================================================================
#                            CREATE SAMPLE SCRIPT
# =============================================================================
# Creates a 90-second sample from your MKV file for testing encode settings.
# Defaults to 03:00-04:30; a custom mm:ss-mm:ss range can be given.
# =============================================================================

echo "Place your mkv file in this folder and run this. It will create a 90 second"
echo "sample mkv you can use for testing different encode settings."
echo ""
read -p "Press Enter to continue..."

# Change to script directory
cd "$(dirname "$0")" || exit 1

SCRIPT_PATH="$(cd "$(dirname "$0")/.." && pwd)/tools/create-sample.py"
if [ ! -f "$SCRIPT_PATH" ]; then
    echo "[ERROR] Could not find tools/create-sample.py."
    exit 1
fi

python3 "$SCRIPT_PATH" "$@"
exit $?
