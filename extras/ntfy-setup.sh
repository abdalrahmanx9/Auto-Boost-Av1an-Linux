#!/bin/bash
# ntfy notification setup - writes ntfy= into settings.txt
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.." || exit 1

python3 -u "tools/ntfy-setup.py"
rc=$?
echo ""
if [ "$rc" -ne 0 ]; then
    echo "ntfy setup failed."
else
    echo "ntfy setup finished."
fi
read -r -p "Press Enter to exit..."
exit "$rc"
