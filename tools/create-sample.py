import os
import re
import sys
import glob
import subprocess
import shutil

# --- CONFIGURATION ---

# Path to mkvmerge relative to THIS script (tools\create-sample.py)
# Structure: tools\ -> MKVToolNix\mkvmerge.exe
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
MKVMERGE_EXE = shutil.which("mkvmerge") or os.path.join(SCRIPT_DIR, "MKVToolNix", "mkvmerge")

# Default sample range if the user just presses Enter
DEFAULT_START = "00:03:00"
DEFAULT_END = "00:04:30"

OUTPUT_PREFIX = "sample_"

# Accepts mm:ss or hh:mm:ss, with optional fractional seconds (e.g. 15:00.500)
TIME_RE = re.compile(r"^(?:(\d+):)?(\d{1,2}):(\d{1,2}(?:\.\d+)?)$")


def parse_timestamp(text):
    """
    Parses 'mm:ss' or 'hh:mm:ss' (optional .fraction) into (seconds, 'HH:MM:SS.nnn').
    Returns None if the format is invalid.
    """
    match = TIME_RE.match(text.strip())
    if not match:
        return None

    hours = int(match.group(1) or 0)
    minutes = int(match.group(2))
    seconds = float(match.group(3))

    if minutes > 59 or seconds >= 60:
        return None

    total = hours * 3600 + minutes * 60 + seconds
    whole = int(total)
    millis = int(round((total - whole) * 1000))
    formatted = "%02d:%02d:%02d.%03d" % (whole // 3600, (whole % 3600) // 60, whole % 60, millis)
    return total, formatted


def parse_range(text):
    """
    Parses 'start-end' where each side is mm:ss or hh:mm:ss.
    Returns (start_str, end_str) formatted for mkvmerge, or None with a printed reason.
    """
    if text.count("-") != 1:
        print("[ERROR] Use exactly one dash between the two times, e.g. 15:00-16:00")
        return None

    start_text, end_text = text.split("-")

    start = parse_timestamp(start_text)
    if start is None:
        print(f"[ERROR] Could not read the start time '{start_text.strip()}'. Use mm:ss or hh:mm:ss.")
        return None

    end = parse_timestamp(end_text)
    if end is None:
        print(f"[ERROR] Could not read the end time '{end_text.strip()}'. Use mm:ss or hh:mm:ss.")
        return None

    if end[0] <= start[0]:
        print("[ERROR] The end time must be later than the start time.")
        return None

    return start[1], end[1]


def find_source_files():
    """Returns MKV files in the current folder, ignoring previously created samples."""
    files = [f for f in glob.glob("*.mkv") if not os.path.basename(f).startswith(OUTPUT_PREFIX)]
    return sorted(files)


def choose_source(files):
    """Returns the file to cut. Prompts only when there is more than one candidate."""
    if len(files) == 1:
        return files[0]

    print("Multiple MKV files found:")
    for index, name in enumerate(files, start=1):
        print(f"  {index}. {name}")
    print()

    while True:
        choice = input(f"Pick a file [1-{len(files)}, Enter for 1]: ").strip()
        if choice == "":
            return files[0]
        if choice.isdigit() and 1 <= int(choice) <= len(files):
            return files[int(choice) - 1]
        print("Please enter a number from the list.")


def ask_range():
    """Asks for a custom range, falling back to the default on Enter."""
    print("Sample range")
    print("  Press Enter for the default range " f"({DEFAULT_START}-{DEFAULT_END}, a 90 second sample)")
    print("  Or type a custom range as start-end, one dash between the two times.")
    print()
    print("  Each time can be mm:ss or hh:mm:ss, and may carry a fraction of a second:")
    print("    15:00-16:00           one minute, from 15:00 to 16:00")
    print("    2:30-4:00             90 seconds, from 2 min 30 sec to 4 min")
    print("    01:15:00-01:16:30     90 seconds, starting at 1 hour 15 min")
    print("    00:45-01:45           one minute, starting 45 seconds in")
    print("    10:00.500-10:30.500   fractional seconds are allowed too")
    print()
    print("  NOTE: the cut is not frame exact. mkvmerge can only split on a keyframe,")
    print("        so the start of the sample snaps outward to the closest keyframe")
    print("        at or before your start time. Expect the sample to begin slightly")
    print("        earlier (and therefore run slightly longer) than you asked for.")
    print()

    while True:
        answer = input("Range: ").strip()
        if answer == "":
            return DEFAULT_START, DEFAULT_END
        parsed = parse_range(answer)
        if parsed:
            return parsed


def print_intro():
    """Explains up front that this is a lossless cut, not a conversion."""
    print("Create Sample")
    print("  This makes a short clip out of your MKV so you have something small")
    print("  to run encoding tests against.")
    print()
    print("  Nothing is re-encoded. mkvmerge copies the original video track as is.")
    print()


def main():
    print_intro()

    if not os.path.exists(MKVMERGE_EXE):
        print("[ERROR] Could not find mkvmerge at:")
        print(f"        {MKVMERGE_EXE}")
        print("Please verify the folder structure.")
        return 1

    source_files = find_source_files()
    if not source_files:
        print("[ERROR] No .mkv files found in this folder.")
        print("Place your mkv file next to this launcher and run it again.")
        return 1

    source_file = choose_source(source_files)
    print(f"Input: {source_file}\n")

    start, end = ask_range()

    output_file = OUTPUT_PREFIX + os.path.basename(source_file)

    if os.path.exists(output_file):
        answer = input(f"'{output_file}' already exists. Overwrite? [y/N]: ").strip().lower()
        if answer not in ("y", "yes"):
            print("Cancelled.")
            return 1

    cmd = [
        MKVMERGE_EXE,
        "-o", output_file,
        "--no-audio",
        "--split", f"parts:{start}-{end}",
        source_file,
    ]

    print()
    print(f"Cutting {start} to {end} (video only)")
    print(f"Using: {MKVMERGE_EXE}")
    print()

    try:
        result = subprocess.run(cmd)
    except OSError as e:
        print(f"[FAILURE] Could not run mkvmerge: {e}")
        return 1

    print()
    # mkvmerge returns 1 for warnings, 2 for errors
    if result.returncode == 0:
        print(f"[SUCCESS] Sample created: {output_file}")
        return 0
    if result.returncode == 1:
        print(f"[SUCCESS] Sample created with warnings: {output_file}")
        return 0

    print("[FAILURE] Something went wrong.")
    if os.path.exists(output_file) and os.path.getsize(output_file) == 0:
        try:
            os.remove(output_file)
        except OSError:
            pass
    return result.returncode


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nCancelled.")
        sys.exit(1)
