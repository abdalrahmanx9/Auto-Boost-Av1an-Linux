#!/bin/bash
# This script uses AfterZone.py to re-encode chosen scenes of an encode
# that has already finished.
#
# AfterZone re-encodes ONLY the chunks a zones.txt touches, reusing every other
# chunk from that finished encode. Run it after the normal encode, once you
# know which scenes you want different settings for.
#
# All three encoders in this package are supported. Which one made an encode is
# read off the working folder it left behind, so there is nothing to set here:
#
#   av1an   temp/<name>/.<hash>/chunks.json  (+ done.json, encode/NNNNN.ivf)
#           left by the av1an-* and autoboost-* scripts
#   LQTC    temp/.<hash>/cmd.txt             (+ done.txt, encode/NNNN.obu)
#           left by the lqtc-* scripts, along with temp/<name>_scd.txt
#   Condor  temp/<name>-condor.json          (+ temp/<name>-condor/scenes/NNNNN.ivf)
#           left by the condor-* scripts
#
# Requirements:
#   1. The encode already finished and its working folder above still exists
#      (all generated scripts keep it; don't run cleanup before AfterZone)
#   2. The original input is still in video-input/<name>.mkv
#   3. A zones file named after the input sits next to it:
#        video-input/<name>.txt
#      AfterZone can auto-generate one from the finished file's bitrate if it
#      is missing. See zones-example.txt for the format.
#
# Result: video-output/<name>-afterzone.mkv
# Your original video-output/<name>-output.mkv is left alone.
#
# On an LQTC encode, the zones go into LQTC's own scene file, and LQTC applies
# a scene's parameters on top of the ones the encode was started with - so a
# zone line only has to name what it changes, and the 'reset' keyword does not
# apply. A zone that sets --crf overrides the quality target search for the
# scenes it covers; one that does not sets everything else and lets the search
# choose a CRF again. AfterZone re-runs the exact command LQTC recorded in
# cmd.txt, because that is the only command LQTC will resume with, so the
# build, the worker counts and the quality target all stay as they were.
#
# On a Condor encode, the zones go straight into condor.json's scene array.
# Condor has no zones option of its own, so this is the only way to zone one.
# Each scene there carries a complete set of encoder parameters, including the
# quantizer Target Quality picked for it, and AfterZone keeps that quantizer
# for every scene no zone covers - a zone line only changes what it names.
# Target Quality is NOT run again: AfterZone runs 'condor encode' followed by
# 'condor concatenate', so no scene is re-measured and nothing outside your
# zones changes settings. A scene is re-encoded purely because its chunk file
# was removed from scenes/, which is exactly how condor's own resume works.
#
# Interrupting is safe. Once AfterZone has handed the job to the encoder it
# leaves a marker in temp/afterzone-active-<name>.txt. While that file is
# there, running this script again changes NOTHING on disk: AfterZone skips the
# zones file and the plan and just restarts the encoder, which resumes at the
# first chunk that is not finished. The marker is removed once the result is
# muxed.

# --- ENCODER SETTINGS FOR THE MKV TAG (av1an encodes only) ---
# These are written into the output's Encoded_Library_Settings tag by
# av1an-tag.py. They do NOT drive the encode: the base settings come from the
# original chunks.json and the per-zone overrides come from your zones.txt.
# Copy these three lines from the script you originally encoded with so the tag
# stays truthful.
#
# They are a fallback. AfterZone reads the tag off your finished
# video-output/<name>-output.mkv first and copies that, because it already
# describes the encode these chunks came from. An LQTC result only ever uses
# that route - its settings live in its own lqtc-* script, not here - so it is
# left untagged rather than wrongly tagged when there is nothing to copy.
av1an_settings="--lineart-psy-bias 3 --texture-psy-bias 3 --hbd-mds 1 --lp 3 --photon-noise 200"
FINAL_SPEED="4"
CRF="30"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR" || exit 1

# --- CREATE MARKER ---
# av1an-tag.py reads the newest tools/bat-used-*.txt marker to find which
# script to pull the settings above from, so this one is written last and wins.
# Only this script's own marker is cleared; the marker left by the script that
# ran the encode is deliberately kept for the worker count.
rm -f "tools/bat-used-$(basename "$0").txt"
touch "tools/bat-used-$(basename "$0").txt"

# --- SET TEMP PATH ---
# tools/LQTC is deliberately not on here: an LQTC encode is restarted with the
# full path to the build it originally used, which AfterZone.py takes from
# cmd.txt.
export PATH="$SCRIPT_DIR/tools/av1an:$PATH"

# --- AFTERZONE ---
# No renaming step here on purpose: the working folder is tied to the input
# file's name, so renaming the input now would orphan the finished chunks.
# An unfinished encode from a previous run (temp/afterzone-active-*.txt) is
# picked up here automatically and simply continued.
echo "Starting AfterZone..."
echo "Reading finished encodes from: temp"
echo "Outputs will go to:            video-output"

python3 "$SCRIPT_DIR/tools/AfterZone.py"
rc=$?

# Exit code 7 means AfterZone already finished on its own interactive prompt
# (zone generation), so pausing again here would just be a second key press.
if [ "$rc" -ne 7 ]; then
    echo ""
    echo "All tasks finished."
    read -r -p "Press Enter to exit..."
fi

# --- CLEANUP ---
# Cleanup is intentionally NOT run here. AfterZone keeps the chunks it
# replaced, and the state files it rewrote, in an afterzone-backup folder
# inside the working folder it used.
exit "$rc"
