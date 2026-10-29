#!/bin/bash
# Deletes comparison leftovers from this folder:
#   Folders: Comparisons, screens
#   Files:   generated.compframes, *.lwi
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR" || exit 1

echo "This will delete the following from:"
echo "   $PWD"
echo ""
echo "   Folders: Comparisons, screens"
echo "   Files:   generated.compframes, *.lwi"
echo ""
read -r -p "Press 1 to continue, 2 to exit: " choice

if [ "$choice" != "1" ]; then
    exit 0
fi

for d in Comparisons screens; do
    if [ -d "$d" ]; then
        rm -rf "$d"
        echo "Deleted folder: $d"
    else
        echo "Not found: $d"
    fi
done

if [ -f "generated.compframes" ]; then
    rm -f "generated.compframes"
    echo "Deleted file: generated.compframes"
else
    echo "Not found: generated.compframes"
fi

found=0
for f in *.lwi; do
    [ -e "$f" ] || continue
    rm -f "$f"
    echo "Deleted file: $f"
    found=1
done
[ "$found" -eq 0 ] && echo "Not found: *.lwi"

echo ""
echo "Done."
read -r -p "Press Enter to exit..."
