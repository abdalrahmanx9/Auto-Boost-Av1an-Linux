#!/bin/bash
# Opens the MKVToolNix GUI (mkvtoolnix-gui) to edit track titles/properties
# without remuxing - the Linux stand-in for the bundled
# JMkvpropedit / Inviska MKV Extract / Breezemkvpropedit tools on Windows.
#
# Requires: sudo apt install mkvtoolnix-gui
if command -v mkvtoolnix-gui &> /dev/null; then
    exec mkvtoolnix-gui "$@"
else
    echo "mkvtoolnix-gui was not found."
    echo "Install it with:  sudo apt install mkvtoolnix-gui"
    read -r -p "Press Enter to exit..."
    exit 1
fi
