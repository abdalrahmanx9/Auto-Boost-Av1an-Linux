#!/bin/bash
# Previews the filtering in settings.txt before you commit to an encode.
# It reads settings.txt, builds a temporary VapourSynth script for a file in
# video-input, and opens a previewer with the untouched source and the
# filtered result side by side.
#
# Put the file you want to test in the video-input folder, then edit
# settings.txt (crop, downscaling, dehalo, denoise, deband) and run this.
#
# In the previewer, press 1 for the source and 2 for the filtered version.
# If crop or downscaling is on you also get 3: the source with only
# crop/downscale applied, so 2 against 3 shows what the filters alone do.
# Close the preview window when you are done. Nothing is encoded and no file
# is modified.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR" || exit 1

echo "-------------------------------------------------------------------------------"
echo "settings.txt preview"
echo "-------------------------------------------------------------------------------"
echo "Put the file you want to test in the video-input folder, then edit settings.txt"
echo "(crop, downscaling, dehalo, denoise, deband) and run this to see the result."
echo ""
echo "You will be asked about cropping first: press Enter for no crop, or 1 to crop"
echo "using the [crop] section of settings.txt. Auto crop has to scan the file, so"
echo "it adds a short wait before the preview opens."
echo "-------------------------------------------------------------------------------"
read -r -p "Press Enter to continue..."

export PATH="$SCRIPT_DIR/tools/av1an:$PATH"
# Open this preview in vsview instead of vspreview (when installed).
# Read by tools/vspreview-dispatch.py. Delete the line to use vspreview.
export AUTOBOOST_PREVIEWER=vsview

if [ ! -f "tools/settings-preview.py" ]; then
    echo "Error: Could not find tools/settings-preview.py."
    read -r -p "Press Enter to exit..."
    exit 1
fi

python3 "tools/settings-preview.py" "$@"
rc=$?
echo ""
if [ "$rc" -ne 0 ]; then
    echo "Preview did not finish cleanly (exit code $rc)."
else
    echo "Preview closed. Edit settings.txt and run this again to compare another setting."
fi
read -r -p "Press Enter to exit..."
exit "$rc"
