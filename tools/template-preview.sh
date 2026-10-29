#!/bin/bash
# Previews video-input/template.vpy before you commit to an encode.
# It renders the template the same way the encoders do, then opens a previewer
# with the untouched source and the filtered result side by side.
#
# This file lives in video-input, next to template.vpy. script-builder.sh puts
# it there when it writes the template.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.." || exit 1

echo "-------------------------------------------------------------------------------"
echo "template.vpy preview"
echo "-------------------------------------------------------------------------------"

if [ ! -f "tools/template-preview.py" ]; then
    echo "Error: Could not find tools/template-preview.py."
    read -r -p "Press Enter to exit..."
    exit 1
fi

export PATH="$SCRIPT_DIR/tools/av1an:$PATH"
export AUTOBOOST_PREVIEWER=vsview

python3 "tools/template-preview.py" "$@"
rc=$?
echo ""
read -r -p "Press Enter to exit..."
exit "$rc"
