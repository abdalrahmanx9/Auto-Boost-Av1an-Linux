#!/bin/bash
# Auto-Boost Grain / Photon Test
#
# Place exactly one MKV file into this folder.
#  1. Detects the single source MKV in this folder.
#  2. Asks whether you want a photon-noise test or film-grain test.
#  3. Asks for default values or a custom range.
#  4. Asks for a 10 second test range (Enter = middle of the video).
#  5. Encodes samples for each strength and opens a comparison preview.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR" || exit 1

python3 -u "../tools/photon-test.py"
rc=$?
echo ""
read -r -p "Press Enter to exit..."
exit "$rc"
