#!/usr/bin/env python3
"""AfterZone - apply a zones.txt to an encode that has ALREADY finished.

Normally zones have to be decided before the encoder starts. AfterZone lets you
decide afterwards: you watch the finished file, note the scenes you are not
happy with, write a zones.txt, and AfterZone re-encodes only those parts.

Both encoders this package ships are supported, and which one produced an
encode is read off the working folder it left behind rather than guessed:

    .<hash>\\chunks.json + .<hash>\\done.json + encode\\NNNNN.ivf   av1an
    .<hash>\\cmd.txt     + .<hash>\\done.txt  + encode\\NNNN.obu    LQTC

The two halves of this file mirror each other: the av1an notes are immediately
below, the LQTC ones are at "LQTC working folders" further down. Everything in
between - the zones file, the bitrate analysis, the plan, the split logic and
the in-progress marker - is shared.

How it works (av1an)
--------------------
When av1an runs with --keep, its working folder survives:

    .<hash>\\chunks.json   one entry per chunk, each with its own start_frame /
                           end_frame and video_params
    .<hash>\\done.json     {"frames":N,"done":{"00003":{...}},...}
    .<hash>\\encode\\*.ivf every finished chunk

That .<hash> folder ends up in one of two places, because av1an names it after
a hash of the input path and creates it in whatever folder it was run from:

    temp\\<name>\\.<hash>     Auto-Boost .bat files. Auto-Boost-Av1an.py runs
                             av1an in video-input\\<name>\\, and dispatch.py
                             moves that whole folder to temp\\<name>\\ afterwards.
                             The .vpy sits next to it in temp\\<name>\\.
    video-input\\.<hash>      av1an-only .bat files. av1an-dispatch.py runs
                             av1an in video-input itself, so the folder is
                             created there and is never moved. The .vpy for
                             these lives in temp\\<name>.vpy.

Both are searched. The folder name is a hash, so a loose .<hash> in video-input
is matched to its input file by the .vpy path recorded in its chunks.json.

On --resume, av1an reads chunks.json, throws away every chunk whose name is a
key in done.json, and encodes the rest with the video_params stored in
chunks.json.  It performs no freshness check on either file, and the final
concat simply reads whatever .ivf files are in encode\\.

So AfterZone does exactly this, per chunk that a zone touches:
  1. rewrite that chunk's video_params in chunks.json with the zone's params
  2. delete its done.json entry
  3. move its encode\\NNNNN.ivf out of the way
then re-runs av1an with --resume.  Untouched chunks are never re-encoded.

A zone edge that lands inside a chunk splits that chunk in two, which is what
av1an itself does with --zones during a fresh encode: chunks.json is just a
list, so the entry is replaced by one entry per piece, each with its own
start_frame/end_frame, its own frame range in source_cmd, and its own
video_params.  A zone covering frames 300-500 of a 100-500 chunk therefore
gives frames 100-300 the original parameters and 300-500 the zone's.

Both pieces still have to be encoded, because the existing .ivf covers the
whole original chunk and cannot be cut, so splitting costs the same encode
time as re-encoding the chunk whole -- what it buys is the parameters being
right per piece.  It also puts a keyframe at the split, exactly as a fresh
--zones encode would.

Inserting entries renumbers every chunk after the split, because a chunk's
name is "{index:05}" and all three of av1an's concat methods depend on that
numbering being contiguous from zero.  AfterZone therefore also renames the
encoded .ivf files and re-keys done.json, and writes a rename-map json into
afterzone-backup\\ before it touches anything.

Chunks that cannot be split fall back to taking the zone across their whole
range: multi-pass chunks (both pieces would share one --stats file) and
chunks whose source_cmd has no frame range AfterZone recognizes.  Either way
it prints exactly which frames are affected before it changes anything.

How it works (LQTC)
-------------------
LQTC keeps the same kind of working folder, also under --keep, but everything
in it is its own format and its zones are native: the scene list doubles as the
zones file, because anything written after the start frame on a line is passed
to the encoder for that scene alone.  So AfterZone writes the zone's parameters
onto the scene's line, deletes that chunk's line from done.txt, moves its
encode\\NNNN.obu aside and re-runs the command LQTC recorded in cmd.txt, which
resumes at every chunk done.txt no longer lists.  Splitting works the same way
as for av1an - a zone edge inside a scene inserts a new start frame - and
renumbers the later chunks, which is why done.txt, chunks.json and the .obu
files are renumbered with it.  See "LQTC working folders" below for the layout.

An LQTC encode chooses its own CRF per scene with the quality target search, so
a zone that sets --crf overrides that search for the scenes it covers, and a
zone that does not sets everything else and lets the search run again.

That scene file is the whole zone mechanism for LQTC -- nothing else AfterZone
writes carries a single encoder parameter -- which makes losing it the one
failure that cannot be noticed while it is happening. A scene list that keeps
its frame numbers and loses its parameters still resumes cleanly, still
re-encodes every chunk the plan freed, and reproduces the encode that was
already there chunk for chunk: hours of work, no error, and an output the same
size as the one it replaced. So it is not assumed to have been written. Before
anything is touched the file is proved writable (Windows refuses to open a
read-only file for writing, and the attribute survives being moved to temp\\ and
staged back, so make_writable clears it first); it is read back and compared
against the plan the moment it is written; and it is compared again against the
file the recorded command actually names, immediately before LQTC is launched.
Any mismatch stops the run while the backups are still good. A resumed run
cannot write zones by definition, so it reports how many scenes carry
parameters and asks before encoding when the answer is none.

zones.txt format (one zone per line, blank lines and #-comments ignored):

    start_frame end_frame encoder [reset] encoder_params
    0 2157 svt-av1 --crf 28 --psy-rd 0.6
    31769 -1 svt-av1 --crf 30 --tf-strength 3

end_frame is exclusive; -1 means "to the end of the video".  Without the
optional `reset` keyword the zone's params are MERGED onto the params that
chunk was originally encoded with (matching flags are replaced, new flags are
appended), so you only need to list what you want to change.  With `reset`,
the zone's params replace the chunk's params completely.  Every parameter is
passed to the encoder, which is how the .bat files in this package already
handle things like --photon-noise.

For LQTC the merge is done by the encoder rather than here: a scene's own
parameters are applied on top of the global ones the encode was started with,
so a zone line only ever has to name what it changes.  `reset` therefore has
nothing to act on and is ignored (with a warning) for LQTC encodes.

Output goes to video-output\\<name>-afterzone.mkv.  The original
video-output\\<name>-output.mkv is never touched, so you can compare them.

Writing the zones file
----------------------
With no video-input\\<name>.txt to read, AfterZone offers to write one: it
measures the finished encode, takes the median of the 1 second rolling average
as the typical bitrate, and turns every region that sustains a percentage above
that into a zone line.  Each line repeats the parameters its range was already
encoded with, so the file does nothing until it is edited -- which is why the
run ends by offering to edit it:

    add +3 to the CRF on every line and --tf-strength 2 to every line's settings
    customize those two levels, i.e. type in the CRF step and the --tf-strength

Those two numbers are the ones worth choosing per film.  --tf-strength is 0-4:
1 is the encoder default and what the lines already carry, 2 is usually enough
for the high bitrate scenes this targets, and 3 is destructive enough that it
only belongs on extremely grainy, difficult flashbacks.

Batch runs
----------
Every input in video-input with a working folder is processed in turn, so a
folder of episodes is one run.  When more than one of them still needs a zones
file written, the questions for that -- how the ranges are picked and the boost
above -- are asked once for all of them if you want, and every zones file is
then written the same way without stopping to ask again.

A file that already has its zones file is never part of that.  It goes straight
to its plan and the prompt that starts the encode, which is the whole point of
the second run, so nothing is allowed in between:

    1 - Encode: re-encode the chunks in the plan above
    2 - Start over: measure again and replace the zones file
    3 - Exit without changing anything

Encode is answered once for the run, not once per file.  The plan for each file
is still printed as it is reached, but a folder of episodes is approved with one
keypress and then left to finish.

The ranges are picked either as a percentage of each file's own typical bitrate
(so every episode is measured against itself) or as one bitrate in kbps for all
of them, which zones every scene averaging that or more.  A file with no scene
that expensive falls back to the percentage, so the batch never leaves a file
without a zones file.

Only writing zones files is shared.  Re-encoding is still planned and confirmed
one file at a time, because that is per-encode and it is the only irreversible
thing here.

Interrupted runs
----------------
As soon as the encoder's state files are about to be rewritten, AfterZone drops
a marker in temp\\afterzone-active-<name>.txt naming the file it is encoding,
the working folder it is encoding into, the worker count and the chunk counts.
While that marker exists AfterZone modifies nothing: it skips zones, planning
and every file operation, and simply restarts the encoder, which continues at
the first chunk it has no completion record for.  So the window can be closed
in the middle of an encode, and running AfterZone.bat again picks it up where
it stopped.  The marker is deleted once the result is muxed into video-output.

For LQTC that also covers moving its working folder and scene file back into
video-input, which is the only place it looks for them.  That is a move rather
than an edit, so it happens on a resumed run too, and the pair is returned to
temp\\ once the result is muxed.

Worker count
------------
Every .bat in this package writes a tools\\bat-used-<name>.bat.txt marker as it
starts.  AfterZone reads the newest marker that is not its own and uses that
.bat's custom-av1an-workers value when the one-time optimization benchmark has
filled it in, so it encodes with the same worker count the original encode ran
with.  The name of that .bat is remembered in tools\\afterzone-lastbat.txt,
because AfterZone.bat clears tools\\bat*.txt before this script starts.  With
no .bat to read, tools\\workercount-config.txt is used exactly as before.

None of that applies to an LQTC encode: its worker count is part of the command
line in cmd.txt, which AfterZone re-runs unchanged, so the re-encode always uses
the same -w and -v the encode itself ran with.

Run this via AfterZone.bat (it sets PATH and the encoder-settings tag).
"""

import copy
import glob
import json
import os
import re
import shlex
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path
from xml.etree import ElementTree
from xml.sax.saxutils import escape as xml_escape

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from wakepy import keep as wakepy_keep
except Exception:  # pragma: no cover - wakepy is bundled, but never hard-fail
    wakepy_keep = None

BLUE = "\033[94m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
BOLD = "\033[1m"
RESET = "\033[0m"

VIDEO_EXTS = (".mkv", ".mp4", ".m2ts")

# LQTC names its chunk files with four digits and writes Annex B OBU streams,
# where av1an writes five digits and .ivf. Both numberings have to stay
# contiguous from zero, because both concat steps just sort the folder.
LQTC_CHUNK_EXT = "obu"
LQTC_INDEX_WIDTH = 4
AV1AN_INDEX_WIDTH = 5

# LQTC's scene list, kept next to the input while the encode runs. Its lines are
# "<start_frame> [encoder params for this scene]", so it is both the scene list
# and the zones file.
LQTC_SCENE_SUFFIX = "_scd.txt"

# val_scenes() in tools\lqtc-build\src\chunk.rs refuses to start when any scene
# is longer than this, so a scene file AfterZone writes has to respect it too.
# Splitting only ever shortens scenes, so this is really a check on what was
# already there.
LQTC_MAX_SCENE_FRAMES = 300

# Condor (tools\av1an\condor.exe) is a fork of av1an, but it keeps everything in
# one file: condor.json holds the scene list, the per-scene encoder parameters,
# the quantizer Target Quality settled on, and the encoded byte counts. There is
# no chunks.json and no done.json to keep in step with each other.
#
# It also has no done list at all. run() in the parallel encoder filters the
# scenes with
#
#     !scenes_directory.join(format!("{}.{}", scene_id(index), ext)).exists()
#
# so a scene is re-encoded purely because its chunk file is missing. Deleting a
# file is the whole of "mark this for redo"; the parallel_encoder record in
# condor.json is written afterwards and never read back.
#
# scene_id() is format!("{:>05}", index), i.e. the scene's position in the
# array, so inserting a scene renumbers every chunk after it.
CONDOR_CHUNK_EXT = "ivf"
CONDOR_INDEX_WIDTH = 5
CONDOR_SCENES_DIR = "scenes"
CONDOR_CONFIG_SUFFIX = "-condor.json"
CONDOR_TEMP_SUFFIX = "-condor"

# Condor's per-scene encoder block is a JSON map of option name to a tagged
# CLIParameter, not a command line. These are the three variants in
# andean-condor\src\models\encoder\cli_parameter.rs.
CONDOR_PARAM_NUMBER = "Number"
CONDOR_PARAM_STRING = "String"
CONDOR_PARAM_BOOL = "Bool"

# The option each encoder carries its quantizer in, from set_quantizer() in
# andean-condor\src\core\encoder\mod.rs. Only the key differs; the value is
# always a Number.
CONDOR_QUANTIZER_KEYS = {
    "SVTAV1": "crf",
    "AOM": "cq-level",
    "VPX": "cq-level",
    "RAV1E": "quantizer",
    "AVM": "qp",
    "X264": "crf",
    "X265": "crf",
    "VVENC": "q",
}

# Default starting threshold for auto-generation, as a multiple of the typical
# bitrate (the median of the 1 second rolling average). The user can change it
# interactively, so this is only where the search starts.
BITRATE_ZONE_THRESHOLD = 1.25

# The zones file is written as a no-op -- every line repeats the parameters its
# range was already encoded with -- so it does nothing until it is edited. These
# are the one-keypress edit offered at the end of generation: raise every line's
# quantizer, which is what actually spends fewer bits there, and set
# --tf-strength (default 1, range 0-4 in every fork shipped here) on every line.
ZONE_BOOST_CRF_STEP = 3
ZONE_BOOST_TF_FLAG = "--tf-strength"
ZONE_BOOST_TF_STRENGTH = "2"
ZONE_BOOST_EXTRA_PARAMS = [ZONE_BOOST_TF_FLAG, ZONE_BOOST_TF_STRENGTH]

# Both numbers above can be typed in instead, because they are the two that
# actually depend on the content: how far the CRF can move before the zone stops
# looking like the rest of the film, and how hard the temporal filter may push on
# a scene that is expensive because of grain rather than because of motion.
# --tf-strength is 0-4 in every fork shipped here.
TF_STRENGTH_MIN = 0
TF_STRENGTH_MAX = 4

# The flag a zone line carries its quantizer in. svt-av1 (both encoders here)
# uses --crf, but a zone line is passed to the encoder verbatim and the other
# encoders av1an drives spell it differently, so the boost finds it either way.
# The value is a plain number in all of them, which is why one bump covers them.
ZONE_QUANTIZER_FLAGS = ("--crf", "--cq-level", "--qp", "--quantizer", "-q")

# Written into the zones file when the boost is applied, so the header does not
# keep claiming the lines are unchanged, and so a second boost does not repeat
# the note. It is a comment, so parse_zones_file ignores it.
ZONE_BOOST_MARKER = ("# AfterZone applied its CRF / --tf-strength boost to the "
                     "lines below.")

# Exit code AfterZone.bat reads to skip its own "press any key". Zone generation
# ends on an interactive prompt of its own, and two pauses back to back reads as
# if something went wrong.
ALREADY_PAUSED_EXIT = 7
_ended_on_prompt = False

# A zone edge that lands inside a chunk splits that chunk in two, the same way
# --zones would have split it during a fresh encode. Splitting at an arbitrary
# frame can leave a sliver of a chunk behind, which wastes a keyframe on a
# handful of frames, so an edge closer than this to a chunk boundary is pushed
# out to the boundary instead (the zone absorbs the sliver).
MIN_SPLIT_SEGMENT = 12

DISPLAY_USERNAME = "av1enjoyer"
_WINDOWS_USER_PATH_RE = re.compile(r"([\\/]+users[\\/]+)[A-Za-z0-9._-]+", re.IGNORECASE)

# Everything in this package lives under the folder holding tools\, so wherever
# the user unzipped it is noise in the console (and one more thing to censor in
# a screenshot). Paths are printed starting at the package folder itself.
PACKAGE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _package_prefix_re():
    """Match the path leading up to (but not including) the package folder.

    Keyed on the folder name rather than the install path, so a path that
    reaches the console spelled differently (mapped drive, UNC share) still
    gets trimmed.
    """
    name = os.path.basename(PACKAGE_ROOT)
    if not name:
        return None  # package sits at a filesystem root, nothing to trim
    return re.compile(
        # A path can only start at the start of the text or after whitespace, a
        # quote or '='. Without this the match could begin in the middle of one
        # argument and run into the next, because a folder name may contain
        # spaces and so the folder pattern below happily crosses them. A whole
        # command line is printed in one go, so that really happens: the folders
        # of the second path would swallow the first path's file name and every
        # argument between the two.
        r"(?<![^\s\"'=])"
        r"(?:[A-Za-z]:|\\\\[^\\/\r\n]+)?"  # drive letter or UNC host
        # The folders above the package, lazily, so a line holding two paths
        # into the package trims each of them instead of everything from the
        # first separator to the last package folder on the line.
        r"[\\/](?:[^\\/\r\n\"']+[\\/])*?"
        # Lookahead keeps the package folder itself; `(?![^\\/])` lets the path
        # end there instead of requiring a child component.
        rf"(?={re.escape(name)}(?![^\\/]))",
        re.IGNORECASE,
    )


_PACKAGE_PREFIX_RE = _package_prefix_re()


def anonymize_user_paths(text):
    """Trim package paths and hide the Windows profile name in console output."""
    if not isinstance(text, str):
        return text
    text = _WINDOWS_USER_PATH_RE.sub(lambda m: f"{m.group(1)}{DISPLAY_USERNAME}", text)
    if _PACKAGE_PREFIX_RE is not None:
        text = _PACKAGE_PREFIX_RE.sub("", text)
    return text


class AnonymizedTextStream:
    """Proxy a text stream while anonymizing C:\\Users\\<name> paths."""

    def __init__(self, stream):
        self._stream = stream

    def write(self, text):
        return self._stream.write(anonymize_user_paths(text))

    def writelines(self, lines):
        return self._stream.writelines(anonymize_user_paths(line) for line in lines)

    def __getattr__(self, name):
        return getattr(self._stream, name)


sys.stdout = AnonymizedTextStream(sys.stdout)
sys.stderr = AnonymizedTextStream(sys.stderr)

try:
    import colorama

    colorama.just_fix_windows_console()
except Exception:
    pass


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------


def hr():
    print("-" * 79)


def fs_path(path):
    r"""The form of `path` to hand a file-system call, free of the 260 limit.

    The Windows file APIs reject any path longer than 260 characters, and the
    names here reach that on their own: a backup is

        temp\<stem>-condor\afterzone-backup\<stem>-condor.json.<stamp>.bak

    which spells a release name -- often 80 characters of one -- out twice.
    Worse than the failure is the silence: os.path.exists() on an over-long
    path answers False rather than raising, so a guard written around it reads
    "not there" and the copy or the move it protects is skipped or overwrites
    something.

    The \\?\ prefix opts the call out of that limit. It also opts out of path
    parsing, so the path has to be absolute, backslash-separated and free of
    . and .. first, which is what abspath() leaves behind.

    For os/shutil/open calls only. A prefixed path must never be written into
    a config file, a scene list or a command line: av1an, LQTC and ffmpeg do
    not all understand it, and condor writes its own prefixes (which
    windows_path_to_local strips back off).
    """
    if os.name != "nt":
        return path
    text = str(path)
    if text.startswith("\\\\?\\"):
        return text
    absolute = os.path.abspath(text)
    if absolute.startswith("\\\\"):      # \\server\share -> \\?\UNC\server\share
        return "\\\\?\\UNC\\" + absolute.lstrip("\\")
    return "\\\\?\\" + absolute


def make_writable(path):
    """Clear the read-only attribute so a file can be rewritten in place.

    Windows refuses open(path, "w"), os.replace onto, and os.remove of any file
    carrying the read-only attribute, and the files AfterZone rewrites are not
    its own: they belong to the encoders, and they pick the attribute up easily
    -- copied off a network share or a read-only medium, restored from a backup
    that carried it, or marked by hand to stop something else touching them. A
    move preserves it, so it survives being parked in temp\\ and staged back.

    Nothing here needs the attribute kept, and losing the write is far worse
    than losing the flag. That is not hypothetical for the LQTC scene file: it
    is written last, after the chunks have already been moved aside and the
    state files rewritten, and a scene list that keeps its frame numbers and
    loses its zone parameters re-encodes every freed chunk to exactly what it
    already was.
    """
    path = fs_path(path)
    try:
        if os.path.exists(path) and not os.access(path, os.W_OK):
            os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
    except OSError:
        pass


def remove_file(path):
    """os.remove, but not defeated by the read-only attribute. True if it went."""
    make_writable(path)
    try:
        os.remove(fs_path(path))
    except OSError:
        return False
    return True


def die(message):
    print(f"{RED}[AfterZone] {message}{RESET}")
    sys.exit(1)


def ask(prompt, valid):
    """Prompt until the user types one of `valid` (case-insensitive)."""
    while True:
        try:
            answer = input(prompt).strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            sys.exit(1)
        if answer in valid:
            return answer
        print(f"{YELLOW}Please enter one of: {', '.join(valid)}{RESET}")


def ask_percentage(prompt, blank_hint="press Enter to give up"):
    """Read a percentage above 100. Blank input means 'give up'.

    `blank_hint` is only the wording of the error message: what a blank line
    means is the caller's business (giving up in the per-file prompts, keeping
    the default in the batch one), and the two read very differently.
    """
    while True:
        try:
            raw = input(prompt).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            sys.exit(1)
        if not raw:
            return None
        try:
            value = float(raw.rstrip("%").strip())
        except ValueError:
            print(f"{YELLOW}Enter a number above 100 (for example 115), or "
                  f"{blank_hint}.{RESET}")
            continue
        if value <= 100:
            print(f"{YELLOW}The value has to be above 100. At 100% the threshold is "
                  f"the median bitrate itself, which would select roughly half the "
                  f"video.{RESET}")
            continue
        return value


def ask_kbps(prompt):
    """Read a bitrate in kbps. Blank input means 'never mind'."""
    while True:
        try:
            raw = input(prompt).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            sys.exit(1)
        if not raw:
            return None
        cleaned = raw.lower().replace(",", "")
        for suffix in ("kbps", "kbit", "kb", "k"):
            if cleaned.endswith(suffix):
                cleaned = cleaned[:-len(suffix)]
                break
        try:
            value = float(cleaned.strip())
        except ValueError:
            print(f"{YELLOW}Enter a bitrate in kbps (for example 8000), or press "
                  f"Enter to go back.{RESET}")
            continue
        if value <= 0:
            print(f"{YELLOW}The bitrate has to be above zero.{RESET}")
            continue
        return value


def press_any_key(prompt="Press any key to exit . . . "):
    """Hold the window open.

    AfterZone tells the .bat to skip its own pause when it has already ended on
    a prompt (see ALREADY_PAUSED_EXIT), so anything printed on the way out is
    only readable if something here waits for the user.
    """
    print(prompt, end="", flush=True)
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.getch()
        else:
            input()
    except (EOFError, KeyboardInterrupt, ImportError, OSError):
        pass
    print()


def print_zones_ready(zones_path):
    """Where the zones file is and what to do with it, on the way out."""
    print()
    print(f"{BOLD}The zones file is written and ready for your edits:{RESET}")
    print(f"  {zones_path}")
    print()
    print("Open it in a text editor (Notepad++ is suggested), change the encoder")
    print("parameters on the lines you want, and delete any line you do not want")
    print("re-encoded. Then run AfterZone again to re-encode just those chunks.")
    print()


def megabytes(size):
    """Size in MB the way Windows reports it, for the savings estimates."""
    return f"{size / (1024.0 * 1024.0):,.0f}MB"


def print_savings_estimate(region_bytes, file_size, label="these regions",
                           reduction=0.5):
    """'Halve these regions and the file gets this much smaller.'

    The saving is measured from the encoded video bytes those regions actually
    occupy, so it is what re-encoding them at `reduction` of their current
    bitrate would hand back. file_size is the whole container, audio and all,
    which is why the new size is the container minus the saving rather than a
    figure derived from the video stream alone.
    """
    if region_bytes <= 0:
        return
    saved = region_bytes * reduction
    print(f"If you reduce {label} to {int(round(reduction * 100))}% of their "
          f"original bitrate it will save {megabytes(saved)}.")
    if file_size > 0:
        print(f"Total mkv current size: {megabytes(file_size)}   "
              f"new size potential: {megabytes(max(0.0, file_size - saved))}")


def human_bytes(size):
    step = float(size)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if step < 1024.0 or unit == "TiB":
            return f"{step:.1f} {unit}" if unit != "B" else f"{int(step)} B"
        step /= 1024.0
    return f"{step:.1f} TiB"


def frames_to_timecode(frame, fps):
    if not fps or fps <= 0:
        return "??:??:??"
    total = frame / fps
    hours, rem = divmod(int(total), 3600)
    minutes, seconds = divmod(rem, 60)
    return f"{hours:d}:{minutes:02d}:{seconds:02d}"


def read_worker_count(tools_dir):
    """Read workers=N from tools/workercount-config.txt (same file the .bats use)."""
    config = os.path.join(tools_dir, "workercount-config.txt")
    try:
        with open(config, "r", encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                if "=" in line and line.split("=", 1)[0].strip().lower() == "workers":
                    value = int(line.split("=", 1)[1].strip())
                    if value > 0:
                        return value
    except Exception:
        pass
    return 4


def read_key_value_file(path):
    """Parse a simple `key = value` text file. None when it cannot be read.

    Lines that are blank, start with # or hold no '=' are ignored, so the files
    written by this script can carry a plain-English header above their data.
    """
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            text = handle.read()
    except OSError:
        return None
    data = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().lower()
        if key and " " not in key:
            data[key] = value.strip()
    return data


# `set "custom-av1an-workers=8"`, including the reserved trailing spaces that
# the .bat files keep after the closing quote.
_BAT_SET_RE = re.compile(r'^set\s+"?([^=\s"]+)\s*=(.*?)"?\s*$', re.IGNORECASE)

# The .bat that last ran an encode, remembered by name. AfterZone.bat runs
# `del tools\bat*.txt` before this script starts, which takes the encode .bat's
# own bat-used-*.txt marker with it, so this file deliberately does not start
# with "bat" and therefore survives.
LAST_BAT_RECORD = "afterzone-lastbat.txt"


def read_bat_settings(bat_path):
    """Every `set "key=value"` line of a .bat file, keyed by lowercased name."""
    settings = {}
    try:
        with open(bat_path, "r", encoding="utf-8", errors="replace") as handle:
            for raw in handle:
                line = raw.strip()
                if not line or line.startswith("::") or line.lower().startswith("rem "):
                    continue
                match = _BAT_SET_RE.match(line)
                if match:
                    settings[match.group(1).strip().lower()] = match.group(2).strip()
    except OSError:
        pass
    return settings


def remember_last_encode_bat(tools_dir, bat_name):
    """Record which .bat ran the encode, for runs where the marker is gone."""
    path = os.path.join(tools_dir, LAST_BAT_RECORD)
    if (read_key_value_file(path) or {}).get("bat") == bat_name:
        return
    try:
        with open(path, "w", encoding="utf-8", newline="\r\n") as handle:
            handle.write("\n".join([
                "# Written by AfterZone.py - the .bat that last ran an encode.",
                "# AfterZone reads that .bat's custom-av1an-workers value so it",
                "# encodes with the same worker count the encode itself used.",
                "# It is kept here because AfterZone.bat runs `del tools\\bat*.txt`",
                "# before AfterZone starts, which removes the .bat's own marker.",
                "",
                f"bat = {bat_name}",
                "",
            ]))
    except OSError:
        pass


def find_last_encode_bat(tools_dir, root_dir):
    """The .bat that ran the encode, plus a short note on how it was found.

    Every .bat here drops a tools\\bat-used-<name>.bat.txt marker as it starts,
    so the newest marker names the .bat that ran last. AfterZone.bat drops one
    too (av1an-tag.py needs it to find the encoder-settings lines), so its own
    marker is skipped. Returns (None, "") when nothing identifies a .bat.
    """
    markers = glob.glob(os.path.join(tools_dir, "bat-used-*.txt"))
    markers.sort(key=lambda path: os.path.getmtime(path), reverse=True)
    for marker in markers:
        name = os.path.basename(marker)
        bat_name = name[len("bat-used-"):-len(".txt")]
        if not bat_name or bat_name.lower() == "afterzone.bat":
            continue
        bat_path = os.path.join(root_dir, bat_name)
        if os.path.isfile(bat_path):
            remember_last_encode_bat(tools_dir, bat_name)
            return bat_path, f"named by tools\\{name}"

    remembered = (read_key_value_file(os.path.join(tools_dir, LAST_BAT_RECORD))
                  or {}).get("bat", "")
    if remembered:
        bat_path = os.path.join(root_dir, remembered)
        if os.path.isfile(bat_path):
            return bat_path, f"remembered in tools\\{LAST_BAT_RECORD}"

    # No marker survived. Only an unambiguous folder can name the .bat now:
    # with exactly one encode .bat present there is nothing to confuse it with.
    candidates = [path for path in sorted(glob.glob(os.path.join(root_dir, "*.bat")))
                  if os.path.basename(path).lower() not in ("afterzone.bat",
                                                            "bat-builder.bat")]
    if len(candidates) == 1:
        return candidates[0], "the only encode .bat in the package folder"
    return None, ""


def resolve_worker_count(tools_dir, root_dir):
    """The worker count to run av1an with, and where it came from.

    Mirrors what the .bat files themselves do: read
    tools\\workercount-config.txt, then let a custom-av1an-workers value written
    by the one-time optimization benchmark override it. Following the same
    order means AfterZone encodes with the worker count the original encode
    was tuned for instead of the shared default.
    """
    workers = read_worker_count(tools_dir)
    source = "tools\\workercount-config.txt"

    bat_path, note = find_last_encode_bat(tools_dir, root_dir)
    if bat_path:
        settings = read_bat_settings(bat_path)
        digits = re.search(r"\d+", settings.get("custom-av1an-workers") or "")
        if digits:
            workers = max(1, int(digits.group(0)))
            source = (f"custom-av1an-workers in "
                      f"{os.path.basename(bat_path)} ({note})")
        else:
            source = (f"tools\\workercount-config.txt "
                      f"({os.path.basename(bat_path)}, {note}, has no "
                      f"optimized worker count)")
    return workers, source


def svt_av1_version(tools_dir):
    """Report the fork currently installed as tools/av1an/SvtAv1EncApp.exe."""
    import shutil as _sh
    exe = os.path.join(tools_dir, "av1an", "SvtAv1EncApp")
    if not os.path.exists(exe):
        exe = _sh.which("SvtAv1EncApp")
    if not exe:
        return "SvtAv1EncApp not found"
    try:
        result = subprocess.run([exe, "--version"], capture_output=True, text=True)
        output = (result.stdout or result.stderr or "").strip()
        if output:
            return output.splitlines()[0].replace(" (release)", "").strip()
    except Exception as exc:
        return f"unknown ({exc})"
    return "unknown"


# ---------------------------------------------------------------------------
# encoder parameter parsing / merging
# ---------------------------------------------------------------------------


def _is_flag(token):
    """True for '--crf' / '-q', False for values including negatives like '-1'."""
    return bool(re.match(r"^--?[A-Za-z]", token))


def parse_params(tokens):
    """['--crf','30','--hbd-mds','1'] -> [['--crf','30'], ['--hbd-mds','1']]."""
    parsed = []
    index = 0
    while index < len(tokens):
        token = str(tokens[index])
        if _is_flag(token):
            if "=" in token:
                flag, value = token.split("=", 1)
                parsed.append([flag, value])
                index += 1
            elif index + 1 < len(tokens) and not _is_flag(str(tokens[index + 1])):
                parsed.append([token, str(tokens[index + 1])])
                index += 2
            else:
                parsed.append([token, None])
                index += 1
        else:
            # Stray value with no flag in front of it; keep it so nothing is lost.
            parsed.append([token, None])
            index += 1
    return parsed


def flatten_params(parsed):
    flat = []
    for flag, value in parsed:
        flat.append(flag)
        if value is not None:
            flat.append(value)
    return flat


def merge_params(base_tokens, zone_tokens):
    """Override matching flags in place, append flags the base does not have."""
    merged = parse_params(base_tokens)
    for flag, value in parse_params(zone_tokens):
        for entry in merged:
            if entry[0] == flag:
                entry[1] = value
                break
        else:
            merged.append([flag, value])
    return flatten_params(merged)


def params_summary(base_tokens, new_tokens):
    """Describe what actually changed, for the confirmation screen."""
    before = {flag: value for flag, value in parse_params(base_tokens)}
    after = {flag: value for flag, value in parse_params(new_tokens)}
    changes = []
    for flag, value in after.items():
        if flag not in before:
            changes.append(f"+{flag} {value}" if value is not None else f"+{flag}")
        elif before[flag] != value:
            changes.append(f"{flag} {before[flag]} -> {value}")
    for flag in before:
        if flag not in after:
            changes.append(f"-{flag}")
    return ", ".join(changes) if changes else "no change"


# ---------------------------------------------------------------------------
# zones.txt
# ---------------------------------------------------------------------------


class Zone:
    def __init__(self, line_number, start, end, encoder, reset, params):
        self.line_number = line_number
        self.start = start
        self.end = end
        self.encoder = encoder
        self.reset = reset
        self.params = params

    def overlaps(self, chunk_start, chunk_end):
        return chunk_start < self.end and chunk_end > self.start


def parse_zones_file(path, total_frames, quiet=False):
    """Parse `start end encoder [reset] params` lines. Blank / #-comments skipped.

    `quiet` drops the per-line complaints, for the callers that parse a file only
    to price it: those run over every file in the run, ahead of the pass that
    reads each one properly, and the same warning twice reads as two problems.
    """
    def warn(message):
        if not quiet:
            print(message)

    zones = []
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        for line_number, raw in enumerate(handle, 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            try:
                tokens = shlex.split(line, posix=False)
            except ValueError:
                tokens = line.split()
            if len(tokens) < 3:
                warn(f"{YELLOW}[AfterZone] Line {line_number}: need at least "
                     f"'start end encoder', skipping: {line}{RESET}")
                continue
            try:
                start = int(tokens[0])
                end = int(tokens[1])
            except ValueError:
                warn(f"{YELLOW}[AfterZone] Line {line_number}: frame numbers are "
                     f"not integers, skipping: {line}{RESET}")
                continue

            encoder = tokens[2]
            rest = tokens[3:]
            reset = bool(rest) and rest[0].lower() == "reset"
            if reset:
                rest = rest[1:]

            if end < 0:
                end = total_frames
            if start >= total_frames:
                warn(f"{YELLOW}[AfterZone] Line {line_number}: starts at frame "
                     f"{start} but the video is only {total_frames} frames, "
                     f"skipping.{RESET}")
                continue
            start = max(0, start)
            end = min(end, total_frames)
            if end <= start:
                warn(f"{YELLOW}[AfterZone] Line {line_number}: empty range "
                     f"({start}-{end}), skipping.{RESET}")
                continue
            if not rest:
                warn(f"{YELLOW}[AfterZone] Line {line_number}: no encoder "
                     f"parameters, skipping.{RESET}")
                continue
            zones.append(Zone(line_number, start, end, encoder, reset, rest))
    return zones


# ---------------------------------------------------------------------------
# locating the av1an working folder
# ---------------------------------------------------------------------------


class Job:
    """One input video plus the encoder working folder that produced it."""

    def __init__(self, source, work_dir, hash_dir, chunks, done, kind="av1an",
                 rest_dir=None, config_path=None):
        self.source = source                  # video-input\name.mkv
        self.kind = kind                      # "av1an", "lqtc" or "condor"
        # The encoder's cwd for the original run, and the cwd AfterZone re-runs
        # it in:
        #   temp\name      av1an, Auto-Boost .bat files
        #   video-input    av1an-only .bat files, and every LQTC encode
        self.stem = Path(source).stem
        self.work_dir = work_dir
        self.hash_dir = hash_dir              # work_dir\.3926ad3 (the temp folder)
        self.chunks_path = chunks             # chunks.json, both encoders
        self.done_path = done                 # done.json (av1an) / done.txt (LQTC)
        # LQTC only: its working folder has to sit next to the input to be
        # found again, but lqtc-dispatch.py parks it in temp\ once the encode is
        # over. This is where it rests between runs; hash_dir is where it is
        # right now.
        self.rest_dir = rest_dir
        # Condor only: temp\<name>-condor.json, which is chunks.json, done.json
        # and the encoder parameters all in one. hash_dir is temp\<name>-condor,
        # the --temp folder holding scenes\.
        self.config_path = config_path

    @property
    def is_lqtc(self):
        return self.kind == "lqtc"

    @property
    def is_condor(self):
        return self.kind == "condor"

    @property
    def encoder_name(self):
        """How this encoder is named in messages."""
        return {"lqtc": "LQTC", "condor": "Condor"}.get(self.kind, "av1an")

    @property
    def index_width(self):
        if self.is_lqtc:
            return LQTC_INDEX_WIDTH
        if self.is_condor:
            return CONDOR_INDEX_WIDTH
        return AV1AN_INDEX_WIDTH

    @property
    def chunk_ext(self):
        """The extension of a finished chunk file, for messages."""
        if self.is_lqtc:
            return LQTC_CHUNK_EXT
        if self.is_condor:
            return CONDOR_CHUNK_EXT
        return "ivf"

    @property
    def chunk_dir(self):
        """The folder finished chunk files live in.

        av1an and LQTC both use encode\\; Condor writes to the scenes\\ folder
        named by parallel_encoder.scenes_directory in its config.
        """
        return os.path.join(self.hash_dir,
                            CONDOR_SCENES_DIR if self.is_condor else "encode")

    def chunk_label(self, index):
        """A chunk's name in encode\\, without the extension."""
        return f"{index:0{self.index_width}d}"

    @property
    def cmd_path(self):
        """LQTC only: the command line the encode was started with."""
        return os.path.join(self.hash_dir, "cmd.txt")

    def scene_path_in(self, directory):
        """LQTC only: where the scene list lives when parked in `directory`."""
        return os.path.join(directory, f"{self.stem}{LQTC_SCENE_SUFFIX}")


def hash_dirs_in(directory):
    """Every av1an working folder sitting directly inside `directory`."""
    found = []
    try:
        entries = sorted(os.scandir(directory), key=lambda e: e.name)
    except OSError:
        return found
    for entry in entries:
        if not entry.is_dir():
            continue
        chunks = os.path.join(entry.path, "chunks.json")
        done = os.path.join(entry.path, "done.json")
        if os.path.exists(chunks) and os.path.exists(done):
            found.append(entry.path)
    return found


def find_hash_dir(work_dir):
    """av1an names its temp folder '.<hash of input filename>'. Find it by content."""
    candidates = hash_dirs_in(work_dir)
    if not candidates:
        return None
    if len(candidates) > 1:
        # Prefer the dotted hash folder, then the most recently written one.
        dotted = [c for c in candidates if os.path.basename(c).startswith(".")]
        candidates = dotted or candidates
        candidates.sort(key=lambda p: os.path.getmtime(os.path.join(p, "done.json")),
                        reverse=True)
        print(f"{YELLOW}[AfterZone] Multiple av1an temp folders found; using "
              f"{os.path.basename(candidates[0])}{RESET}")
    return candidates[0]


_SOURCE_CMD_TEXT = {}


def hash_dir_source_text(hash_dir):
    """The first chunk's source_cmd as one lowercased string, for identification.

    A loose .<hash> folder in video-input is named after a hash of the input
    path, so the folder name says nothing about which video it belongs to. The
    chunk commands do: every one of them names the .vpy av1an was given.
    """
    if hash_dir in _SOURCE_CMD_TEXT:
        return _SOURCE_CMD_TEXT[hash_dir]
    text = ""
    try:
        with open(os.path.join(hash_dir, "chunks.json"), "r",
                  encoding="utf-8") as handle:
            data = json.load(handle)
        if isinstance(data, list) and data and isinstance(data[0], dict):
            parts = [osstring_decode(item)
                     for item in (data[0].get("source_cmd") or [])]
            text = " ".join(part for part in parts if part).lower()
    except (OSError, ValueError, TypeError, AttributeError):
        text = ""
    _SOURCE_CMD_TEXT[hash_dir] = text
    return text


def hash_dir_matches(hash_dir, stem):
    """True when this folder's chunks decode frames from `stem`'s script.

    The extension is required to be right after the stem so that 'foo' does not
    claim the folder belonging to 'foobar'.
    """
    text = hash_dir_source_text(hash_dir)
    if not text:
        return False
    needle = stem.lower()
    return (f"{needle}.vpy" in text
            or f"\\{needle}." in text
            or f"/{needle}." in text)


# ---------------------------------------------------------------------------
# LQTC working folders
# ---------------------------------------------------------------------------

# LQTC (tools\LQTC\lqtc-*.exe, source in tools\lqtc-build\src) is a single
# self-contained binary: it does its own decoding, scene detection, chunking,
# CRF search and muxing, so its working folder looks nothing like av1an's even
# though it is there for the same reason.
#
#   .<hash>\cmd.txt     the exact command line the encode was started with.
#                       On resume LQTC reads this and IGNORES whatever it was
#                       given on the command line (get_saved_args() in
#                       lqtc-build\src\main.rs), so the only honest way to
#                       continue an encode is to run this file verbatim - which
#                       is exactly what AfterZone does.
#   .<hash>\done.txt    "elapsed <seconds>", then one "<chunk> <frames> <bytes>"
#                       line per finished chunk. This is the entire resume
#                       state: LQTC encodes every chunk with no line here.
#   .<hash>\chunks.json one JSON object per line recording the quality target
#                       search for a chunk (its probes and the CRF it settled
#                       on), appended as chunks finish. It is only read again
#                       to build the summary log next to the input, so it does
#                       not steer the encode - but a chunk being re-encoded
#                       still has its line dropped, or the summary would
#                       describe the encode that was replaced.
#   .<hash>\encode\     0000.obu, 0001.obu, ... every finished chunk
#   .<hash>\split\      the quality target search's probe encodes
#   <name>_scd.txt      the scene list: one start frame per line. A scene runs
#                       to the next line's frame, and the last one to the end
#                       of the video. Anything after the frame number is passed
#                       to the encoder for that scene alone, on top of the
#                       global -p parameters - so this file is also where LQTC's
#                       zones live, and writing them is all a zone has to do.
#
# The folder is named after a hash of the input path and created next to the
# input, so LQTC only finds it again while it sits in video-input.
# lqtc-dispatch.py moves it, and the scene file, to temp\ when the encode is
# over; AfterZone moves both back for the re-run and returns them afterwards.

# Options that take a value. Anything else that does not start with '-' is a
# positional (input first, then output), matching parse_args_loop() in main.rs.
_LQTC_VALUE_FLAGS = {
    "-e", "--encoder", "-w", "--worker", "-s", "--sc", "-p", "--param",
    "-b", "--buff", "-r", "--range", "-a", "--audio", "-t", "--tq",
    "-m", "--mode", "-f", "--qp", "-v", "--vship", "-d", "--display",
    "-P", "--alt-param",
}


def parse_lqtc_cmd_line(text):
    """Split cmd.txt back into argv the way LQTC itself does.

    Deliberately a copy of parse_quoted_args() in main.rs, quirks included: a
    quote anywhere toggles quoting and is dropped, so the -p "..." block comes
    back as one argument.
    """
    args = []
    current = ""
    in_quotes = False
    for char in text:
        if char == '"':
            in_quotes = not in_quotes
        elif char == " " and not in_quotes:
            if current:
                args.append(current)
                current = ""
        elif char in "\r\n" and not in_quotes:
            if current:
                args.append(current)
                current = ""
        else:
            current += char
    if current:
        args.append(current)
    return args


def read_lqtc_cmd(hash_dir):
    """The argv recorded in cmd.txt, or [] when it cannot be read."""
    try:
        with open(os.path.join(hash_dir, "cmd.txt"), "r", encoding="utf-8",
                  errors="replace") as handle:
            return parse_lqtc_cmd_line(handle.read().strip())
    except OSError:
        return []


def lqtc_cmd_positionals(argv):
    """(input, output) as LQTC would read them from `argv`."""
    positional = []
    index = 1                       # argv[0] is the executable
    while index < len(argv):
        arg = argv[index]
        if arg in _LQTC_VALUE_FLAGS:
            index += 2
            continue
        if not arg.startswith("-"):
            positional.append(arg)
        index += 1
    while len(positional) < 2:
        positional.append("")
    return positional[0], positional[1]


def lqtc_cmd_option(argv, *names):
    """The value of the first of `names` present in `argv`, or None."""
    for index in range(1, len(argv) - 1):
        if argv[index] in names:
            return argv[index + 1]
    return None


def lqtc_cmd_has_flag(argv, *names):
    return any(arg in names for arg in argv[1:])


def write_lqtc_cmd(hash_dir, argv):
    """Write cmd.txt back in the form LQTC wrote it.

    save_args() in main.rs quotes any argument containing a space and joins the
    rest with single spaces, which is exactly what its reader expects.
    """
    quoted = [f'"{arg}"' if " " in arg else arg for arg in argv]
    make_writable(os.path.join(hash_dir, "cmd.txt"))
    with open(os.path.join(hash_dir, "cmd.txt"), "w", encoding="utf-8",
              newline="") as handle:
        handle.write(" ".join(quoted))


def lqtc_target_uses_cvvdp(argv):
    """True when this encode's quality target selects the CVVDP metric.

    LQTC picks the metric from the number, not from a flag: below 8 is
    Butteraugli, 8 to 10 is CVVDP, above 10 is SSIMULACRA2. Only CVVDP reads
    the display description file.
    """
    match = re.match(r"^\s*([0-9]*\.?[0-9]+)\s*-",
                     lqtc_cmd_option(argv, "-t", "--tq") or "")
    if not match:
        return False
    try:
        return 8.0 < float(match.group(1)) <= 10.0
    except ValueError:
        return False


def resolve_lqtc_display_file(value, work_dir, tools_dir, root_dir):
    """Where the CVVDP display description named by -d actually is now.

    Same search lqtc-dispatch.py does, for the same reason: the file lives in
    tools\\LQTC so that cleanup.py cannot throw away edited viewing conditions.
    None when it is nowhere to be found.
    """
    if not value:
        return None
    direct = value if os.path.isabs(value) else os.path.join(work_dir, value)
    if os.path.exists(direct):
        return os.path.abspath(direct)
    name = re.split(r"[\\/]", value)[-1]
    if not name:
        return None
    for candidate in (os.path.join(tools_dir, "LQTC", name),
                      os.path.join(tools_dir, name),
                      os.path.join(root_dir, name)):
        if os.path.exists(candidate):
            return os.path.abspath(candidate)
    return None


def lqtc_hash_dirs_in(directory):
    """Every LQTC working folder sitting directly inside `directory`.

    cmd.txt is what makes a folder LQTC's rather than av1an's, and done.txt is
    what makes it worth anything to AfterZone: with no finished chunks there is
    nothing to reuse and the encode would simply start over.
    """
    found = []
    try:
        entries = sorted(os.scandir(directory), key=lambda e: e.name)
    except OSError:
        return found
    for entry in entries:
        if not entry.is_dir():
            continue
        if (os.path.exists(os.path.join(entry.path, "cmd.txt"))
                and os.path.exists(os.path.join(entry.path, "done.txt"))
                and os.path.isdir(os.path.join(entry.path, "encode"))):
            found.append(entry.path)
    return found


def lqtc_dir_input_name(hash_dir):
    """The input file name recorded in this folder's cmd.txt, lowercased."""
    source, _output = lqtc_cmd_positionals(read_lqtc_cmd(hash_dir))
    return os.path.basename(source).lower() if source else ""


def read_lqtc_scenes(path):
    """[(start_frame, params_text)] from a scene file, in frame order.

    Mirrors load_scenes() in chunk.rs: the frame number is the first
    whitespace-separated token and everything after it is that scene's own
    encoder parameters.
    """
    scenes = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            text = handle.read()
    except OSError:
        return None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        try:
            start = int(parts[0])
        except ValueError:
            continue
        scenes.append((start, parts[1].strip() if len(parts) > 1 else ""))
    scenes.sort(key=lambda item: item[0])
    return scenes


def write_lqtc_scenes(path, scenes):
    """Write [(start_frame, params_text)] back out as a scene file.

    No comments and no header: LQTC skips a line whose first token is not a
    number, but a stray comment would still be silently dropped from the scene
    list rather than ignored, and losing a scene boundary is not worth the
    convenience.
    """
    lines = []
    for start, params in scenes:
        params = (params or "").strip()
        lines.append(f"{start} {params}".strip())
    make_writable(path)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(lines) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def normalized_lqtc_scenes(scenes):
    """[(start, params)] in the exact form read_lqtc_scenes gives them back."""
    return [(int(start), (params or "").strip()) for start, params in scenes]


def lqtc_scene_file_for(job, argv):
    """The scene file LQTC will actually open for `argv`.

    -s wins when it is there; otherwise LQTC derives <input folder>\\<name>_scd.txt
    and AfterZone runs it with the input folder as its cwd. This is deliberately
    read off the command rather than off `state`, because the command is what
    the encoder acts on.
    """
    recorded = lqtc_cmd_option(argv, "-s", "--sc")
    if recorded:
        return recorded if os.path.isabs(recorded) \
            else os.path.join(job.work_dir, recorded)
    return job.scene_path_in(job.work_dir)


def verify_lqtc_scenes(path, scenes, context):
    """Read a scene file back and confirm it says what it was asked to say.

    For an LQTC encode the scene file is the entire zone mechanism: nothing else
    AfterZone writes carries a single encoder parameter. That makes losing it the
    one failure that cannot be noticed while it is happening -- a scene list that
    kept its frame numbers and lost its parameters still resumes cleanly, still
    re-encodes every chunk the plan freed, and reproduces the encode that was
    already there chunk for chunk. The run looks perfect and the finished file
    comes out the same size.

    So it is checked rather than assumed, both right after it is written and
    again against the command LQTC is about to be launched with. Returns the
    number of scenes carrying parameters.
    """
    expected = normalized_lqtc_scenes(scenes)
    on_disk = read_lqtc_scenes(path)
    if on_disk is None:
        die(f"The LQTC scene file could not be read back after writing it "
            f"({context}):\n"
            f"            {path}\n"
            f"            That file is the only place the zones exist, so the "
            f"encode is not\n"
            f"            started. Nothing has been encoded yet.")
    if on_disk != expected:
        wanted = len([1 for _start, params in expected if params])
        found = len([1 for _start, params in on_disk if params])
        die(f"The LQTC scene file does not hold the zones ({context}):\n"
            f"            {path}\n"
            f"            Expected {len(expected)} scene(s), {wanted} carrying "
            f"zone parameters;\n"
            f"            it has {len(on_disk)} scene(s), {found} carrying "
            f"parameters.\n"
            f"            The zones reach LQTC through this file and nothing "
            f"else, so running\n"
            f"            the encode now would re-encode those chunks to exactly "
            f"what they\n"
            f"            already are. Nothing is started. The original scene "
            f"file, done.txt\n"
            f"            and the replaced chunks are in the afterzone-backup "
            f"folder.")
    return len([1 for _start, params in expected if params])


def mirror_lqtc_scenes(path, scenes, rest_dir, job):
    """Keep temp\\<name>_scd.txt holding the scene list that is being encoded.

    The scene file lives in video-input only while the encode runs; temp\\ is
    where it rests, and where anyone looking for it (including the next
    AfterZone run) reads it. Writing it there as soon as the zones are applied
    means the zoned scene list is visible immediately instead of only after the
    encode finishes -- and it survives an interrupted run.
    """
    if not rest_dir or not os.path.isdir(rest_dir):
        return None
    parked = job.scene_path_in(rest_dir)
    if os.path.abspath(parked) == os.path.abspath(path):
        return None
    try:
        write_lqtc_scenes(parked, scenes)
    except OSError as exc:
        print(f"{YELLOW}[AfterZone] Could not update {parked}: {exc}{RESET}")
        return None
    return parked


def read_lqtc_done(path):
    """(elapsed_seconds, {chunk index: {"frames": n, "size_bytes": n}})."""
    elapsed = 0
    done = {}
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            text = handle.read()
    except OSError:
        return elapsed, done
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("elapsed "):
            try:
                elapsed = int(line.split(None, 1)[1])
            except (IndexError, ValueError):
                elapsed = 0
            continue
        parts = line.split()
        if len(parts) != 3:
            continue
        try:
            index, frames, size = (int(parts[0]), int(parts[1]), int(parts[2]))
        except ValueError:
            continue
        done[index] = {"frames": frames, "size_bytes": size}
    return elapsed, done


def write_lqtc_done(path, elapsed, done):
    """Write done.txt in exactly the shape get_resume() expects."""
    lines = [f"elapsed {int(elapsed)}"]
    for index in sorted(done):
        entry = done[index]
        lines.append(f"{index} {int(entry['frames'])} {int(entry['size_bytes'])}")
    make_writable(path)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(lines) + "\n")


_LQTC_LOG_ID_RE = re.compile(r'^(\{"id":)(\d+)(,)')


def read_lqtc_chunk_log(path):
    """{chunk index: record} from the JSON-lines chunks.json.

    The line it was read from is kept alongside the values under "__line",
    because that text is what has to be written back: parse_chunks() in
    lqtc-build\\src\\atofu reads this file by counting numbers per line and
    parsing each one at a fixed number of decimals (two for a CRF, four for a
    score), so a line that has been through a JSON encoder and come back as
    "25.0" instead of "25.00" is read as a different number.

    A chunk that was encoded more than once has more than one line; the last
    one is the one that produced the .obu currently in encode\\.
    """
    entries = {}
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            text = handle.read()
    except OSError:
        return entries
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict) and isinstance(record.get("id"), int):
            record["__line"] = line
            entries[record["id"]] = record
    return entries


def write_lqtc_chunk_log(path, entries):
    """Write {index: record} back out as JSON lines, in index order.

    Each line is the original text with only its id rewritten, so the number
    formatting LQTC's own parser depends on survives being renumbered.
    """
    make_writable(path)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        for index in sorted(entries):
            record = entries[index]
            line = record.get("__line") or ""
            renumbered, count = _LQTC_LOG_ID_RE.subn(rf'\g<1>{index}\g<3>', line)
            if count:
                handle.write(renumbered + "\n")
                continue
            # Nothing recognizable to patch. Dropping the line loses only a log
            # entry, where writing a reformatted one would corrupt the parse of
            # every line after it.
            print(f"{YELLOW}[AfterZone] Dropped an unrecognized chunks.json line "
                  f"for chunk {index}.{RESET}")


def probe_stream_value(ffprobe, path, entry):
    """One `-show_entries stream=<entry>` value for the first video stream."""
    if not path or not os.path.exists(path) or not os.path.exists(ffprobe):
        return ""
    cmd = [ffprobe, "-v", "error", "-select_streams", "v:0",
           "-show_entries", f"stream={entry}", "-of", "csv=p=0", path]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True,
                                encoding="utf-8", errors="replace")
    except OSError:
        return ""
    if result.returncode != 0:
        return ""
    return (result.stdout or "").strip().splitlines()[0].strip() \
        if (result.stdout or "").strip() else ""


def probe_frame_rate(ffprobe, path, default=24.0):
    """Frames per second of a file's video track. LQTC records none anywhere."""
    for entry in ("r_frame_rate", "avg_frame_rate"):
        value = probe_stream_value(ffprobe, path, entry)
        if not value or value in ("0/0", "N/A"):
            continue
        try:
            if "/" in value:
                numerator, denominator = value.split("/", 1)
                rate = float(numerator) / float(denominator)
            else:
                rate = float(value)
        except (ValueError, ZeroDivisionError):
            continue
        if rate > 0:
            return rate
    return default


def probe_frame_count(ffprobe, path):
    """Frame count of a file's video track, or 0 when it cannot be read.

    nb_frames is usually absent from an mkv, so counting packets is the
    fallback. That reads the whole file, but it does not decode it, and it only
    runs for an encode whose last chunk never finished.
    """
    value = probe_stream_value(ffprobe, path, "nb_frames")
    try:
        count = int(value)
    except (TypeError, ValueError):
        count = 0
    if count > 0:
        return count

    if not path or not os.path.exists(path) or not os.path.exists(ffprobe):
        return 0
    try:
        result = subprocess.run(
            [ffprobe, "-v", "error", "-select_streams", "v:0", "-count_packets",
             "-show_entries", "stream=nb_read_packets", "-of", "csv=p=0", path],
            capture_output=True, text=True, encoding="utf-8", errors="replace")
    except OSError:
        return 0
    try:
        count = int((result.stdout or "").strip().splitlines()[0])
    except (IndexError, ValueError):
        return 0
    return count if count > 0 else 0


def lqtc_total_frames(scenes, done, chunk_log, ffprobe, fallback_paths):
    """Total frames of the encode, from whatever recorded it.

    Every scene but the last is bounded by the next scene's start frame; only
    the last one needs a length from somewhere, and both done.txt and
    chunks.json record it for a chunk that finished. Probing a file is the
    fallback for an encode that was interrupted before its final chunk.
    """
    if not scenes:
        return 0
    last = len(scenes) - 1
    frames = (done.get(last) or {}).get("frames") \
        or (chunk_log.get(last) or {}).get("f")
    if frames:
        return scenes[-1][0] + int(frames)
    print(f"{YELLOW}[AfterZone] The last chunk never finished, so its length is "
          f"not recorded anywhere.{RESET}")
    print(f"{YELLOW}            Measuring the video to find where it ends.{RESET}")
    for path in fallback_paths:
        count = probe_frame_count(ffprobe, path)
        if count:
            return count
    print(f"{YELLOW}[AfterZone] That did not work either, so the last scene is "
          f"treated as one frame{RESET}")
    print(f"{YELLOW}            long. Zones inside it, and the total the plan "
          f"reports, will be wrong.{RESET}")
    return scenes[-1][0] + 1


def build_lqtc_chunks(scenes, total_frames, global_params, fps, chunk_log):
    """LQTC's scene list as the chunk dicts the rest of AfterZone works with.

    Everything downstream - the plan, the scene map, the zones file - is written
    against av1an's chunk shape, so the two encoders are normalised here instead
    of being special-cased in a dozen places.

    "video_params" is the encode's global parameters, because that is what every
    scene was encoded with; the CRF is not in there since the quality target
    search picked one per scene. That CRF is carried separately as "zone_seed",
    which is what a generated zones file starts each line from.
    """
    chunks = []
    for index, (start, _params) in enumerate(scenes):
        end = scenes[index + 1][0] if index + 1 < len(scenes) else total_frames
        record = chunk_log.get(index) or {}
        crf = record.get("fc")
        seed = list(global_params)
        if isinstance(crf, (int, float)):
            seed = merge_params(seed, ["--crf", f"{crf:.2f}"])
        chunks.append({
            "index": index,
            "start_frame": start,
            "end_frame": max(end, start + 1),
            "video_params": list(global_params),
            "zone_seed": seed,
            "frame_rate": fps,
            "output_ext": LQTC_CHUNK_EXT,
            "passes": 1,
            "encoder": "svt_av1",
            "lqtc": True,
            "lqtc_crf": crf,
        })
    return chunks


# ---------------------------------------------------------------------------
# Condor working folders
# ---------------------------------------------------------------------------

# Condor (tools\av1an\condor.exe, source in the andean-condor / california-condor
# crates) is a fork of av1an, but its state lives in one JSON file:
#
#   condor.json     {"condor": {"scenes": [...], "encoder": {...},
#                               "sequence_config": {...}}, "input": ..., ...}
#                   Each scene is {"start_frame", "end_frame", "sub_scenes",
#                   "encoder", "sequence_data"}. The encoder block is a full
#                   copy per scene, which is what makes per-zone parameters a
#                   natural fit: there is no zone_overrides layer to merge.
#   <temp>\scenes\NNNNN.ivf
#                   one finished chunk per scene, numbered by array position.
#
# condor-dispatch.py names these temp\<stem>-condor.json and temp\<stem>-condor,
# so unlike av1an and LQTC there is no hash folder to identify by content.


def condor_encoder_key(encoder):
    """The single key naming the encoder inside a condor encoder block.

    The block is an externally tagged enum: {"SVTAV1": {...}}.
    """
    if isinstance(encoder, dict):
        for key in encoder:
            return key
    return None


def condor_encoder_options(encoder):
    """The {name: CLIParameter} map inside a condor encoder block."""
    key = condor_encoder_key(encoder)
    if not key:
        return {}
    return (encoder.get(key) or {}).get("options") or {}


def condor_quantizer_key(encoder):
    return CONDOR_QUANTIZER_KEYS.get(condor_encoder_key(encoder) or "", "crf")


def condor_param_to_tokens(name, parameter):
    """One condor CLIParameter as the command line tokens it prints as.

    Mirrors to_string_pair() in cli_parameter.rs: a space delimiter splits into
    two tokens, anything else is glued into one, and a Bool is the flag alone
    (or nothing at all when false).
    """
    if not isinstance(parameter, dict):
        return []
    for variant, body in parameter.items():
        if not isinstance(body, dict):
            continue
        prefix = body.get("prefix", "--")
        if variant == CONDOR_PARAM_BOOL:
            return [f"{prefix}{name}"] if body.get("value") else []
        value = body.get("value")
        if variant == CONDOR_PARAM_NUMBER:
            number = float(value or 0)
            text = f"{number:g}"
        else:
            text = str(value if value is not None else "")
        delimiter = body.get("delimiter", " ")
        if delimiter == " ":
            return [f"{prefix}{name}", text]
        return [f"{prefix}{name}{delimiter}{text}"]
    return []


def condor_options_to_tokens(options):
    """A condor options map as a sorted command line token list.

    Sorted by option name so the same parameters always render the same way:
    the map comes out of JSON in arbitrary order, and an unstable ordering would
    make the plan's before/after comparison show differences that are not real.
    """
    tokens = []
    for name in sorted(options or {}):
        tokens.extend(condor_param_to_tokens(name, options[name]))
    return tokens


def tokens_to_condor_options(tokens, template=None):
    """Command line tokens as a condor options map.

    `template` is the scene's existing map, used only to keep an option's
    original prefix and delimiter (svt-av1 takes "--crf 30", aom "--cq-level=30")
    so a rewritten option still renders the way condor wrote it.

    A flag with no value becomes a Bool, a numeric value a Number, anything else
    a String - the same three variants condor can deserialize.
    """
    template = template or {}
    options = {}
    index = 0
    tokens = [str(token) for token in tokens or []]
    while index < len(tokens):
        token = tokens[index]
        if not token.startswith("-"):
            index += 1                        # value without a flag; skip it
            continue

        prefix = "--" if token.startswith("--") else "-"
        body = token[len(prefix):]

        # "--crf=30" carries its own delimiter and value.
        inline_delimiter = None
        value = None
        for candidate in ("=", ":"):
            if candidate in body:
                body, value = body.split(candidate, 1)
                inline_delimiter = candidate
                break

        name = body
        if value is None and index + 1 < len(tokens) and not _is_flag(tokens[index + 1]):
            value = tokens[index + 1]
            index += 1
        index += 1

        previous = template.get(name) or {}
        previous_body = {}
        for _variant, existing in previous.items():
            if isinstance(existing, dict):
                previous_body = existing
            break
        prefix = previous_body.get("prefix", prefix)
        delimiter = inline_delimiter or previous_body.get("delimiter", " ")

        if value is None:
            options[name] = {CONDOR_PARAM_BOOL: {"prefix": prefix, "value": True}}
            continue
        try:
            options[name] = {CONDOR_PARAM_NUMBER: {
                "prefix": prefix, "delimiter": delimiter, "value": float(value)}}
        except (TypeError, ValueError):
            options[name] = {CONDOR_PARAM_STRING: {
                "prefix": prefix, "delimiter": delimiter, "value": str(value)}}
    return options


def condor_scene_quantizer(scene):
    """The quantizer Target Quality settled on for this scene, or None."""
    encoder = scene.get("encoder") or {}
    options = condor_encoder_options(encoder)
    parameter = options.get(condor_quantizer_key(encoder))
    if not isinstance(parameter, dict):
        return None
    body = parameter.get(CONDOR_PARAM_NUMBER)
    if isinstance(body, dict):
        try:
            return float(body.get("value"))
        except (TypeError, ValueError):
            return None
    return None


def condor_scene_bytes(scene):
    """What the finished chunk weighed, as condor recorded it."""
    data = ((scene.get("sequence_data") or {}).get("parallel_encoder") or {})
    size = data.get("bytes")
    return int(size) if isinstance(size, (int, float)) else 0


def blank_condor_sequence_data():
    """A sequence_data record for a scene that has never been encoded.

    None is not allowed for scene_detection, parallel_encoder or target_quality:
    condor's config loader rejects the whole file with "Failed to load config
    file" if any of the three is null, so they are written empty but present.
    """
    now = time.time()
    return {
        "scene_detection": {
            "scenecut_scores": {},
            "created_on": {
                "secs_since_epoch": int(now),
                "nanos_since_epoch": int((now - int(now)) * 1_000_000_000),
            },
        },
        "noise_detection": None,
        "noise_scaling": None,
        "parallel_encoder": {"started_on": None, "completed_on": None,
                             "bytes": None},
        "target_quality": {"passes": []},
    }


def read_condor_config(path):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def write_condor_config(path, data):
    make_writable(path)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)


def condor_config_scenes(config):
    return ((config or {}).get("condor") or {}).get("scenes") or []


def condor_config_input(config):
    """The input path recorded in the config, for matching a job to a source."""
    value = (config or {}).get("input")
    if isinstance(value, str):
        return value
    return ""


def condor_temp_dir(config, config_path):
    """The --temp folder holding scenes\\, as an absolute local path."""
    recorded = (config or {}).get("temp")
    if isinstance(recorded, str) and recorded:
        local = windows_path_to_local(recorded)
        if os.path.isdir(local):
            return local
    # Fall back on the naming condor-dispatch.py uses: the config is
    # temp\<stem>-condor.json beside temp\<stem>-condor.
    guess = config_path[:-len(".json")] if config_path.endswith(".json") else config_path
    return guess


def windows_path_to_local(path):
    r"""Strip the \\?\ long-path prefix condor writes into its config."""
    text = str(path or "")
    for prefix in ("\\\\?\\UNC\\", "\\\\?\\"):
        if text.startswith(prefix):
            text = text[len(prefix):]
            break
    return text


def condor_config_workers(config_path):
    """The worker count recorded in a condor config, or None.

    Condor takes this from its own config when it resumes, so it is what the
    re-encode will really use.
    """
    try:
        config = read_condor_config(config_path)
    except (OSError, ValueError, TypeError):
        return None
    workers = (((config.get("condor") or {}).get("sequence_config") or {})
               .get("parallel_encoder") or {}).get("workers")
    return workers if isinstance(workers, int) else None


def condor_configs_in(directory):
    """Every condor config sitting directly inside `directory`."""
    found = []
    try:
        entries = sorted(os.scandir(directory), key=lambda e: e.name)
    except OSError:
        return found
    for entry in entries:
        if not entry.is_file():
            continue
        name = entry.name.lower()
        if not name.endswith(".json"):
            continue
        if not (name.endswith(CONDOR_CONFIG_SUFFIX) or name == "condor.json"):
            continue
        try:
            config = read_condor_config(entry.path)
        except (OSError, ValueError):
            continue
        if condor_config_scenes(config):
            found.append(entry.path)
    return found


def condor_config_matches(config_path, source):
    """True when this config was written for `source`.

    The recorded input is usually the .vpy the filter chain was built into
    rather than the video itself, so the stem is what has to match.
    """
    try:
        config = read_condor_config(config_path)
    except (OSError, ValueError):
        return False
    recorded = windows_path_to_local(condor_config_input(config)).lower()
    if not recorded:
        return False
    stem = Path(source).stem.lower()
    name = os.path.basename(source).lower()
    recorded_name = os.path.basename(recorded)
    return recorded_name == name or Path(recorded_name).stem == stem


def build_condor_chunks(scenes, fps, encoder_name):
    """Condor's scene list as the chunk dicts the rest of AfterZone works with.

    Same normalisation as build_lqtc_chunks: the plan, the scene map and the
    zones file are all written against av1an's chunk shape.

    "video_params" is the scene's own parameters including the quantizer Target
    Quality chose, because unlike LQTC every condor scene carries a complete
    encoder block. That is also why "zone_seed" is the same list: a generated
    zones file starts each line from the settings that scene really used.
    """
    chunks = []
    for index, scene in enumerate(scenes):
        options = condor_encoder_options(scene.get("encoder") or {})
        tokens = condor_options_to_tokens(options)
        chunks.append({
            "index": index,
            "start_frame": int(scene.get("start_frame") or 0),
            "end_frame": int(scene.get("end_frame") or 0),
            "video_params": list(tokens),
            "zone_seed": list(tokens),
            "frame_rate": fps,
            "output_ext": CONDOR_CHUNK_EXT,
            "passes": 1,
            "encoder": encoder_name,
            "condor": True,
            "condor_crf": condor_scene_quantizer(scene),
            # The scene as condor wrote it, so the rewrite can keep everything
            # AfterZone has no opinion about (sub_scenes, probe history).
            "condor_scene": scene,
        })
    return chunks


def discover_jobs(video_input_dir, temp_dir):
    """Pair every input video with the encoder working folder that produced it.

    Four shapes are recognized:

        temp\\<name>\\.<hash>    av1an, Auto-Boost .bat, after dispatch.py
                                moved it
        video-input\\<name>\\.<hash>
                                the same run, interrupted before the move
        video-input\\.<hash>     av1an-only .bat, which never has its folder
                                moved because av1an-dispatch.py runs av1an in
                                video-input itself
        temp\\.<hash>            LQTC, after lqtc-dispatch.py moved it out of
        video-input\\.<hash>     video-input, or still there because the run was
                                interrupted

    A loose folder in video-input is named after a hash, so it says nothing
    about which video it belongs to: an av1an one is matched by the .vpy path
    recorded in its chunks.json, an LQTC one by the input file named in its
    cmd.txt.

    If a stem somehow has folders in more than one place, the one whose done
    file was written last wins: that is the encode the mkv in video-output came
    from. That also settles a file encoded first with one encoder and then the
    other.
    """
    jobs = []
    sources = []
    for ext in VIDEO_EXTS:
        sources.extend(sorted(glob.glob(os.path.join(video_input_dir, f"*{ext}"))))

    loose = hash_dirs_in(video_input_dir)     # av1an-only .bat files land here
    lqtc_dirs = lqtc_hash_dirs_in(temp_dir) + lqtc_hash_dirs_in(video_input_dir)
    condor_configs = (condor_configs_in(temp_dir)
                      + condor_configs_in(video_input_dir))
    claimed = set()

    def done_written(candidate):
        _work_dir, hash_dir, kind = candidate[:3]
        if kind == "condor":
            # Condor has no done file; the config is rewritten as scenes finish.
            try:
                return os.path.getmtime(candidate[4])
            except (OSError, IndexError, TypeError):
                return 0.0
        name = "done.txt" if kind == "lqtc" else "done.json"
        try:
            return os.path.getmtime(os.path.join(hash_dir, name))
        except OSError:
            return 0.0

    for source in sources:
        stem = Path(source).stem
        candidates = []
        for base in (temp_dir, video_input_dir):
            work_dir = os.path.join(base, stem)
            if not os.path.isdir(work_dir):
                continue
            hash_dir = find_hash_dir(work_dir)
            if hash_dir:
                candidates.append((work_dir, hash_dir, "av1an", None, None))
        for hash_dir in loose:
            if hash_dir_matches(hash_dir, stem):
                candidates.append((video_input_dir, hash_dir, "av1an", None, None))

        # A loose folder whose chunks.json could not be read identifies nothing,
        # so fall back to it only when there is exactly one of each and no
        # other candidate: then there is nothing it could be confused with.
        if not candidates and len(sources) == 1 and len(loose) == 1:
            candidates.append((video_input_dir, loose[0], "av1an", None, None))

        # LQTC always runs in video-input, so that is the cwd to re-run it in
        # whichever folder its working directory is parked in right now.
        for hash_dir in lqtc_dirs:
            if lqtc_dir_input_name(hash_dir) == os.path.basename(source).lower():
                candidates.append((video_input_dir, hash_dir, "lqtc", temp_dir, None))

        # Condor is addressed by its config file rather than by a hash folder,
        # and every path inside that config is absolute, so it can be re-run
        # from anywhere. video-input keeps it consistent with the other two.
        for config_path in condor_configs:
            if condor_config_matches(config_path, source):
                try:
                    config = read_condor_config(config_path)
                except (OSError, ValueError):
                    continue
                candidates.append((video_input_dir,
                                   condor_temp_dir(config, config_path),
                                   "condor", None, config_path))

        if not candidates:
            continue
        if len(candidates) > 1:
            candidates.sort(key=done_written, reverse=True)
            print(f"{YELLOW}[AfterZone] {stem}: found {len(candidates)} encoder "
                  f"folders; using the most recently written one, "
                  f"{candidates[0][1]}{RESET}")

        work_dir, hash_dir, kind, rest_dir, config_path = candidates[0]
        claimed.add(hash_dir)
        if config_path:
            claimed.add(config_path)
        done_name = "done.txt" if kind == "lqtc" else "done.json"
        # Condor keeps both roles in the one config file.
        chunks_path = config_path if kind == "condor" \
            else os.path.join(hash_dir, "chunks.json")
        done_path = config_path if kind == "condor" \
            else os.path.join(hash_dir, done_name)
        jobs.append(Job(source, work_dir, hash_dir, chunks_path, done_path,
                        kind=kind, rest_dir=rest_dir, config_path=config_path))

    orphans = [hash_dir for hash_dir in loose + lqtc_dirs + condor_configs
               if hash_dir not in claimed]
    return jobs, sources, orphans


# ---------------------------------------------------------------------------
# bitrate analysis + zones.txt auto-generation
# ---------------------------------------------------------------------------


def find_encoded_output(video_output_dir, stem):
    """Locate the finished encode to analyse (prefer <stem>-output.mkv)."""
    for suffix in ("-output.mkv", "-afterzone.mkv", "-av1.mkv"):
        candidate = os.path.join(video_output_dir, f"{stem}{suffix}")
        if os.path.exists(candidate):
            return candidate
    matches = sorted(glob.glob(os.path.join(video_output_dir, f"{stem}*.mkv")))
    return matches[0] if matches else None


def frame_size_cache_path(temp_dir, stem):
    """Where a probe result is kept between runs. Same naming rules as markers,
    so a stem with characters Windows rejects still produces a usable name."""
    return os.path.join(temp_dir,
                        f"afterzone-framesizes-{marker_safe_name(stem)}.json")


def load_frame_sizes(cache_path, encoded):
    """Frame sizes from an earlier probe, if they still describe `encoded`.

    Probing a feature length encode takes minutes, and the second run (the one
    that reads the edited zones file) needs exactly the same numbers the first
    run measured. Anything that does not match the file on disk byte for byte is
    treated as a miss, so a re-encoded or replaced output is never scored
    against stale measurements.
    """
    try:
        stat = os.stat(encoded)
        with open(cache_path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    if (data.get("file") != os.path.basename(encoded)
            or int(data.get("bytes") or -1) != stat.st_size
            or int(data.get("mtime") or -1) != int(stat.st_mtime)):
        return None
    sizes = data.get("sizes")
    if not isinstance(sizes, list) or not sizes:
        return None
    return sizes


def save_frame_sizes(cache_path, encoded, sizes):
    """Cache a probe result. Failure is silent: it only costs a re-probe."""
    try:
        stat = os.stat(encoded)
        with open(cache_path, "w", encoding="utf-8") as handle:
            json.dump({"file": os.path.basename(encoded),
                       "bytes": stat.st_size,
                       "mtime": int(stat.st_mtime),
                       "sizes": sizes}, handle)
    except (OSError, ValueError, TypeError):
        pass


def probe_frame_sizes(ffprobe, path):
    """Return per-frame compressed sizes in presentation order, via ffprobe packets."""
    cmd = [
        ffprobe, "-v", "error", "-select_streams", "v:0",
        "-show_entries", "packet=pts_time,size", "-of", "csv=p=0", path,
    ]
    packets = []
    unordered = 0
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, encoding="utf-8", errors="replace")
    last_report = time.monotonic()
    for line in process.stdout:
        line = line.strip()
        if not line:
            continue
        fields = line.split(",")
        if len(fields) < 2:
            continue
        try:
            size = int(fields[-1])
        except ValueError:
            continue
        try:
            pts = float(fields[0])
        except ValueError:
            pts = None
            unordered += 1
        packets.append((pts, size))
        now = time.monotonic()
        if now - last_report > 0.5:
            print(f"  Read {len(packets):,} video frames...", end="\r", flush=True)
            last_report = now
    stderr = process.stderr.read()
    process.wait()
    print(" " * 60, end="\r")
    if process.returncode != 0:
        die(f"ffprobe failed:\n{stderr.strip()}")
    if not packets:
        die("ffprobe returned no video frames; is the output file valid?")
    if unordered == 0:
        packets.sort(key=lambda item: item[0])
    return [size for _pts, size in packets]


def rolling_mean(values, window):
    """Centred rolling mean, edges handled by shrinking the window."""
    count = len(values)
    if window <= 1 or count == 0:
        return list(values)
    prefix = [0]
    for value in values:
        prefix.append(prefix[-1] + value)
    half = window // 2
    out = []
    for index in range(count):
        low = max(0, index - half)
        high = min(count, index + half + 1)
        out.append((prefix[high] - prefix[low]) / float(high - low))
    return out


def median(values):
    ordered = sorted(values)
    count = len(ordered)
    if count == 0:
        return 0.0
    middle = count // 2
    if count % 2:
        return float(ordered[middle])
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def find_high_bitrate_runs(smoothed, threshold, min_len, merge_gap):
    """Contiguous regions that are *sustainably* above threshold.

    Spike handling is the whole difficulty here. A single huge frame (a
    scene-change keyframe, or a flash/explosion) is smeared by the rolling
    average into a bump exactly one window wide, which looks like a region.
    min_len is therefore set wider than the smoothing window by the caller, so
    a lone spike cannot produce a long enough run to qualify while a genuinely
    expensive scene can.

    Judging the run by raw frame sizes instead does not work: on content with
    many skip/repeat frames the median raw frame size is a couple of bytes even
    inside the most expensive scene, so every region would be rejected.
    """
    runs = []
    start = None
    for index, value in enumerate(smoothed):
        if value >= threshold:
            if start is None:
                start = index
        elif start is not None:
            runs.append([start, index])
            start = None
    if start is not None:
        runs.append([start, len(smoothed)])

    merged = []
    for run in runs:
        if merged and run[0] - merged[-1][1] <= merge_gap:
            merged[-1][1] = run[1]
        else:
            merged.append(run)

    return [run for run in merged if run[1] - run[0] >= min_len]


def chunk_frame_window(chunk, count, frame_scale):
    """The slice of the measured frame sizes that belongs to one chunk."""
    low = int(round(chunk["start_frame"] / frame_scale))
    high = int(round(chunk["end_frame"] / frame_scale))
    low = max(0, min(count, low))
    high = max(low + 1, min(count, high))
    return low, high


def chunks_encoded_bytes(picked, sizes, frame_scale):
    """Encoded bytes and frame count occupied by a set of ranked chunks.

    Overlapping chunks would double count, but neither encoder's chunks
    overlap, so summing each window is exact.
    """
    total_bytes = 0.0
    total_frames = 0
    for _mean_size, chunk in picked:
        low, high = chunk_frame_window(chunk, len(sizes), frame_scale)
        total_bytes += sum(sizes[low:high])
        total_frames += high - low
    return total_bytes, total_frames


def spans_encoded_bytes(spans, sizes, frame_scale):
    """Encoded bytes and frame count covered by a list of source frame spans.

    Spans are merged first: zone lines are user-editable and two of them may
    overlap, which would otherwise count the shared frames twice and overstate
    the saving.
    """
    ordered = sorted([int(start), int(end)] for start, end in spans if end > start)
    merged = []
    for span in ordered:
        if merged and span[0] <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], span[1])
        else:
            merged.append(span)

    count = len(sizes)
    total_bytes = 0.0
    total_frames = 0
    for start, end in merged:
        low = max(0, min(count, int(round(start / frame_scale))))
        high = max(low, min(count, int(round(end / frame_scale))))
        total_bytes += sum(sizes[low:high])
        total_frames += high - low
    return total_bytes, total_frames


def rank_chunks_by_bitrate(chunks, sizes, frame_scale):
    """Every chunk with its mean bytes-per-frame, most expensive first.

    Both encoders chunk on scene changes, so this ranks scenes. Used for the
    top-N fallback when threshold detection finds nothing worth zoning.
    """
    count = len(sizes)
    ranked = []
    for chunk in chunks:
        low, high = chunk_frame_window(chunk, count, frame_scale)
        window = sizes[low:high]
        if not window:
            continue
        ranked.append((sum(window) / float(len(window)), chunk))
    ranked.sort(key=lambda item: item[0], reverse=True)
    return ranked


def merge_chunk_spans(entries):
    """Frame spans for a set of ranked chunks, adjacent ones merged.

    Merging matters because expensive scenes cluster: twelve consecutive chunks
    written as twelve zone lines would put a keyframe at every boundary, where
    one line over the whole run does not.
    """
    picked = sorted([chunk["start_frame"], chunk["end_frame"]]
                    for _mean, chunk in entries)
    merged = []
    for span in picked:
        if merged and span[0] <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], span[1])
        else:
            merged.append(span)
    return merged


def top_scene_spans(ranked, count):
    """Frame spans for the N most expensive chunks, adjacent ones merged."""
    return merge_chunk_spans(ranked[:count])


def print_scene_map(job, ranked, reference, fps, to_kbps, sizes, frame_scale,
                    file_size, limit=12):
    """List the most expensive scenes with the frame range each one occupies.

    This is the table you need to hand-write a zones.txt. Ranges that line up
    with these boundaries re-encode exactly one scene each; ranges that do not
    split the chunks they land in, which costs the rest of those chunks a
    re-encode at their original parameters.

    The savings line under the table answers the question the table always
    raises next: the top scenes are expensive, but is zoning them worth the
    re-encode at all? A file where the worst twelve scenes are 40 MB of a 1.4 GB
    encode is not worth zoning, and that is only obvious once it is stated.
    """
    if not ranked:
        return
    shown = ranked[:limit]
    print(f"{BOLD}Most expensive scenes (the encoder's own chunks){RESET}")
    print(f"{'chunk':>6}  {'frames':>15}  {'timecode':>19}  {'bitrate':>11}  vs typical")
    for mean_size, chunk in shown:
        start, end = chunk["start_frame"], chunk["end_frame"]
        print(f"{job.chunk_label(chunk['index']):>6}  {f'{start}-{end}':>15}  "
              f"{frames_to_timecode(start, fps) + ' - ' + frames_to_timecode(end, fps):>19}  "
              f"{mean_size * to_kbps:>8,.0f} kbps  {mean_size / reference:>6.1f}x")
    if len(ranked) > limit:
        print(f"       ... and {len(ranked) - limit} more scene(s), lower bitrate.")
    print()
    # Measured over every scene, not the abridged dozen above: the table is cut
    # short to keep it readable, and an estimate that silently covered only the
    # visible rows would understate the file by an order of magnitude.
    region_bytes, region_frames = chunks_encoded_bytes(ranked, sizes, frame_scale)
    print(f"The table is abridged. This estimate covers all {len(ranked):,} "
          f"scene(s)")
    print(f"({region_frames:,} frames), not just the {len(shown)} shown above.")
    print_savings_estimate(region_bytes, file_size)
    print()
    print("Ranges that match the frame numbers above re-encode exactly those scenes.")
    print("Any other range is fine too: the chunks it starts or ends inside are split")
    print("at your frame numbers, and the leftover pieces are re-encoded unchanged.")
    print("You can hand-write zone lines from this table instead of using a threshold.")
    print()


def strongest_sustained_factor(detect, peak_factor):
    """Highest multiple of the median that still selects at least one region.

    Used to suggest a threshold when the user's value found nothing. `detect`
    takes a factor and returns the spans it would select, so this reflects the
    real detector (minimum length, gap stitching and the median-of-run check)
    rather than just the peak of the curve.
    """
    low = 1.0
    if not detect(1.0001):
        return None
    high = max(peak_factor, low)
    if detect(high):
        return high
    for _ in range(24):
        mid = (low + high) / 2.0
        if detect(mid):
            low = mid
        else:
            high = mid
        if high - low < 0.005:
            break
    return low


def scale_runs_to_frames(runs, total_frames, frame_scale):
    """Detector positions to source frame numbers, clamped and merged.

    These stay at the exact frames the bitrate rose and fell. Zone edges no
    longer have to line up with chunk boundaries, because a zone edge inside a
    chunk splits it (see build_plan), so widening here would only re-encode
    frames the user did not ask for.
    """
    spans = []
    for run_start, run_end in runs:
        start = max(0, int(round(run_start * frame_scale)))
        end = min(total_frames, int(round(run_end * frame_scale)))
        if end > start:
            spans.append([start, end])
    spans.sort()
    merged = []
    for span in spans:
        if merged and span[0] <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], span[1])
        else:
            merged.append(span)
    return merged


def ascii_bitrate_plot(smoothed, fps, width=72, height=12):
    """Console bitrate graph so the shape of the file is visible without a viewer."""
    if not smoothed:
        return
    count = len(smoothed)
    columns = []
    for column in range(width):
        low = int(column * count / width)
        high = max(low + 1, int((column + 1) * count / width))
        window = smoothed[low:high]
        columns.append(sum(window) / len(window))
    peak = max(columns) or 1.0
    ramp = " .:-=+*#%@"
    print()
    print(f"  Bitrate shape (0 to {peak * 8 * fps / 1000.0:,.0f} kbps peak, smoothed):")
    for row in range(height, 0, -1):
        line = ""
        for value in columns:
            level = value / peak * height
            line += ramp[-1] if level >= row else (ramp[3] if level >= row - 0.5 else " ")
        print(f"  |{line}|")
    print(f"  +{'-' * width}+")
    print(f"  0{' ' * (width - 12)}{frames_to_timecode(count, fps):>11}")
    print()


def write_bitrate_png(path, smoothed, fps, median_size, threshold, spans, frame_scale,
                      factor=BITRATE_ZONE_THRESHOLD):
    """Optional matplotlib plot; skipped silently if matplotlib is unavailable."""
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return None

    try:
        to_kbps = 8.0 * fps / 1000.0

        # Feature-length files have hundreds of thousands of frames. Decimate to
        # keep rendering quick and the PNG small, using the per-bucket mean so
        # the drawn curve still matches the curve the threshold was applied to.
        max_points = 4000
        count = len(smoothed)
        if count > max_points:
            frames, kbps = [], []
            for bucket in range(max_points):
                low = int(bucket * count / max_points)
                high = max(low + 1, int((bucket + 1) * count / max_points))
                chunk = smoothed[low:high]
                frames.append(low * frame_scale)
                kbps.append(sum(chunk) / len(chunk) * to_kbps)
        else:
            frames = [index * frame_scale for index in range(count)]
            kbps = [value * to_kbps for value in smoothed]

        figure, axes = plt.subplots(figsize=(16, 5), dpi=110)
        axes.plot(frames, kbps, linewidth=0.7, color="#1f77b4", label="bitrate (1s mean)")
        axes.axhline(median_size * to_kbps, color="#2ca02c", linestyle="--", linewidth=1,
                     label=f"typical {median_size * to_kbps:,.0f} kbps")
        # factor is None when the zones came from the top-N scenes rather than
        # from a threshold, in which case there is no threshold line to draw.
        if factor is not None and threshold is not None:
            axes.axhline(threshold * to_kbps, color="#d62728", linestyle=":",
                         linewidth=1,
                         label=f"threshold {threshold * to_kbps:,.0f} kbps "
                               f"({factor * 100:.0f}% of typical)")
        for index, (start, end) in enumerate(spans):
            axes.axvspan(start, end, color="#ff7f0e", alpha=0.25,
                         label="generated zone" if index == 0 else None)
        axes.set_xlabel("frame")
        axes.set_ylabel("kbps")
        axes.set_title(os.path.basename(path).replace("-bitrate.png", "") + " - bitrate")
        axes.margins(x=0)
        axes.grid(alpha=0.25)
        axes.legend(loc="upper right", fontsize="small")
        figure.tight_layout()
        figure.savefig(path)
        plt.close(figure)
        return path
    except Exception as exc:
        print(f"{YELLOW}[AfterZone] Could not write the bitrate plot: {exc}{RESET}")
        return None


def write_zones_file(zones_path, spans, chunks, job, encoded, fps, reference,
                     peak, to_kbps, source_note, chunk_aligned=False):
    """Write zone lines covering `spans`, replacing whatever was there.

    Each line repeats the parameters the range was already encoded with, so the
    file is a starting point to edit rather than something to run as-is.
    `chunk_aligned` only changes the header comment: spans picked per scene sit
    on chunk boundaries and never split anything, which is worth saying because
    the splitting note otherwise reads as a warning that does not apply.
    """
    if chunk_aligned:
        range_note = [
            "# Ranges are whole chunks (scenes), so nothing gets split:",
            "# each range re-encodes exactly the scenes it covers. end_frame is",
            "# exclusive.",
        ]
    else:
        range_note = [
            "# Ranges are the exact frames the bitrate stayed high. end_frame is",
            "# exclusive. A range that starts or ends inside a chunk splits that",
            "# chunk, so only these frames get the parameters below -- but the rest of a",
            "# split chunk is re-encoded too (with its original parameters), because the",
            "# encoded chunk cannot be cut in half.",
        ]
    if job.is_lqtc:
        # The seed on each line is the global parameters plus the CRF the
        # quality target search picked for that scene, so running the file
        # unedited reproduces the encode. Saying so is the only way the --crf
        # on those lines is not read as something AfterZone invented.
        range_note += [
            "#",
            "# This was an LQTC encode, so each line below is the encode's own",
            "# parameters plus the --crf its quality target search settled on for",
            "# that scene. Raise the --crf to spend fewer bits there, lower it to",
            "# spend more. Delete the --crf instead and the search runs again for",
            "# that scene with whatever else the line says.",
            "#",
            "# LQTC applies a scene's parameters on top of the encode's global",
            "# ones, so a line only has to name what it changes. The 'reset'",
            "# keyword has nothing to act on there and is ignored.",
        ]
    lines = [
        f"# AfterZone auto-generated zones for {os.path.basename(job.source)}",
        f"# Measured from: {os.path.basename(encoded)}",
        f"# Typical bitrate {reference * to_kbps:,.0f} kbps "
        f"(median of the 1 second average); peak {peak * to_kbps:,.0f} kbps.",
        f"# Ranges below were selected by: {source_note}.",
        "#",
        *range_note,
        "#",
        "# EDIT THE PARAMETERS BELOW BEFORE RUNNING AfterZone AGAIN.",
        "# Each line currently repeats the settings the range was already encoded",
        "# with, so running as-is would re-encode it to the same thing. Change the",
        "# values you want different, e.g. raise --crf to spend fewer bits here or",
        "# lower it to spend more. You only need to list the flags you are changing;",
        "# anything you leave out keeps its original value.",
        "#",
        "# Delete any line you do not want re-encoded.",
        "",
    ]
    for start, end in spans:
        base = []
        for chunk in chunks:
            if chunk["start_frame"] <= start < chunk["end_frame"]:
                # zone_seed is what the line should start from when it differs
                # from the raw parameters, which is the LQTC case: its scenes
                # each got their own CRF from the quality target search.
                base = [str(token) for token in
                        (chunk.get("zone_seed") or chunk.get("video_params") or [])]
                break
        lines.append(f"# frames {start}-{end}  ({frames_to_timecode(start, fps)} - "
                     f"{frames_to_timecode(end, fps)})")
        lines.append(f"{start} {end} svt-av1 {' '.join(base)}".rstrip())
    lines.append("")

    with open(zones_path, "w", encoding="utf-8", newline="\r\n") as handle:
        handle.write("\n".join(lines))


def _bump_number_text(text, step):
    """`text` + step, written the way it was written. None if it is not a number.

    An integer stays an integer, because a CRF of "31.0" where the line said
    "28" would read as AfterZone having changed the kind of value rather than
    the value itself. %g keeps a fractional one short (28.5 -> 31.5).
    """
    text = (text or "").strip()
    try:
        if re.fullmatch(r"[+-]?\d+", text):
            return str(int(text) + step)
        return f"{float(text) + step:g}"
    except ValueError:
        return None


def describe_boost(step, extra):
    """'+3 on the CRF and --tf-strength 2', for messages about a boost.

    Both halves are optional: a step of 0 changes only the extra parameters, and
    an empty `extra` changes only the CRF.
    """
    parts = []
    if step:
        parts.append(f"{step:+g} on the CRF")
    if extra:
        parts.append(" ".join(str(token) for token in extra))
    return " and ".join(parts) if parts else "no change"


def print_tf_strength_notes():
    """What the --tf-strength numbers actually do, above the prompt for one."""
    print(f"  {BOLD}--tf-strength{RESET} is the encoder's temporal filter strength "
          f"({TF_STRENGTH_MIN}-{TF_STRENGTH_MAX}):")
    print("     1  the default. Every line was already encoded with this, so")
    print("        leaving it at 1 changes nothing about the filtering.")
    print("     2  usually enough for the high bitrate scenes AfterZone targets,")
    print("        and what the one-key boost sets.")
    print("     3  destructive. Only worth it on extremely grainy, difficult")
    print("        flashbacks; on anything else it smears detail away.")
    print("     0 turns the filter off entirely and 4 pushes harder than 3, so")
    print("     neither is what you want here unless you are experimenting.")


def ask_crf_step(default=ZONE_BOOST_CRF_STEP):
    """How much to ADD to every zone line's CRF. Blank keeps `default`."""
    while True:
        try:
            raw = input(f"CRF to add to every line [{default:+g}]: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            sys.exit(1)
        if not raw:
            return default
        try:
            value = float(raw.lstrip("+"))
        except ValueError:
            print(f"{YELLOW}Enter a number, for example 3 to raise every line's "
                  f"CRF by 3. Press Enter for {default:+g}.{RESET}")
            continue
        if value == int(value):
            value = int(value)      # keep '3' a 3, so the lines read as they did
        if value == 0:
            print(f"{YELLOW}0 leaves every CRF exactly where it is.{RESET}")
        elif value < 0:
            print(f"{YELLOW}A negative value LOWERS the CRF, which spends MORE bits "
                  f"on these ranges and makes the file bigger.{RESET}")
        return value


def ask_tf_strength():
    """The --tf-strength to put on every line.

    Returns the extra parameters for boost_zone_params: either
    ['--tf-strength', '<value>'] or [] for 'leave it unchanged', which is what a
    blank line means -- a line with no --tf-strength on it keeps whatever the
    encode was started with instead of being pinned to a number.
    """
    while True:
        try:
            raw = input(f"{ZONE_BOOST_TF_FLAG} for every line "
                        f"[2, 3, or Enter to leave unchanged]: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            sys.exit(1)
        if not raw or raw.lower() in ("off", "none", "skip", "no"):
            return []
        if not re.fullmatch(r"[+-]?\d+", raw):
            print(f"{YELLOW}Enter a whole number from {TF_STRENGTH_MIN} to "
                  f"{TF_STRENGTH_MAX}, or press Enter to leave "
                  f"{ZONE_BOOST_TF_FLAG} off the lines entirely.{RESET}")
            continue
        value = int(raw)
        if not TF_STRENGTH_MIN <= value <= TF_STRENGTH_MAX:
            print(f"{YELLOW}{ZONE_BOOST_TF_FLAG} only goes from {TF_STRENGTH_MIN} "
                  f"to {TF_STRENGTH_MAX}.{RESET}")
            continue
        if value >= 3:
            print(f"{YELLOW}{value} is destructive on anything that is not an "
                  f"extremely grainy, difficult flashback.{RESET}")
        return [ZONE_BOOST_TF_FLAG, str(value)]


def prompt_custom_boost_levels():
    """Ask for the CRF step and --tf-strength the boost should write.

    Returns (step, extra) ready to hand to apply_zone_boost, or None when the
    answers add up to no change at all and there is nothing to apply.
    """
    hr()
    print(f"{BOLD}Customize the automatic CRF and --tf-strength levels{RESET}")
    hr()
    print("Every zone line currently repeats the settings its range was already")
    print("encoded with, so these two values are what actually make those ranges")
    print("smaller when AfterZone re-encodes them.")
    print()
    print(f"  {BOLD}CRF{RESET}: how much to ADD to the CRF on every line. Higher CRF")
    print(f"       spends fewer bits, so +{ZONE_BOOST_CRF_STEP} (the one-key boost) "
          f"is a solid trim,")
    print("       +1 or +2 is gentler, and +5 and up is visible on most content.")
    print()
    print_tf_strength_notes()
    print()
    step = ask_crf_step()
    extra = ask_tf_strength()
    print()
    if not step and not extra:
        print(f"{YELLOW}That adds up to no change, so the zones file is left "
              f"alone.{RESET}")
        return None
    print(f"Every zone line will get {describe_boost(step, extra)}.")
    return step, extra


def boost_zone_params(tokens, step=ZONE_BOOST_CRF_STEP,
                      extra=ZONE_BOOST_EXTRA_PARAMS):
    """Raise a zone line's quantizer by `step` and set `extra` on it.

    Returns (new_tokens, note), where note describes the quantizer change for
    the console and is None when the line carries no quantizer to raise (an
    LQTC line whose --crf was deleted on purpose, so the quality target search
    runs again -- `extra` is still applied there).
    """
    parsed = parse_params(tokens)
    note = None
    # step 0 is a legitimate answer to the custom prompt: it means "only set
    # --tf-strength", so the quantizer is not looked at at all.
    for entry in (parsed if step else []):
        if entry[0].lower() in ZONE_QUANTIZER_FLAGS and entry[1] is not None:
            bumped = _bump_number_text(entry[1], step)
            if bumped is not None:
                note = f"{entry[0]} {entry[1]} -> {bumped}"
                entry[1] = bumped
            break
    # merge_params rather than a plain append, so a line that already sets the
    # flag has its value replaced instead of the encoder seeing it twice.
    return merge_params(flatten_params(parsed), extra), note


def boost_zones_file(path, step=ZONE_BOOST_CRF_STEP, extra=ZONE_BOOST_EXTRA_PARAMS):
    """Apply boost_zone_params to every zone line in `path`, in place.

    Comments, blank lines and anything that does not parse as a zone line are
    written back untouched, so the header this file was generated with (and any
    line the user has already edited by hand) survives.

    Returns (notes, unbumped, skipped): one console note per line that had its
    quantizer raised, the number of zone lines that had none to raise, and the
    number of lines that could not be read as zones. None when the file cannot
    be read or written.
    """
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            original = handle.read().splitlines()
    except OSError as exc:
        print(f"{RED}Could not read {path}: {exc}{RESET}")
        return None

    out = []
    notes, unbumped, skipped = [], 0, 0
    for raw in original:
        line = raw.strip()
        if not line or line.startswith("#"):
            out.append(raw)
            continue
        try:
            tokens = shlex.split(line, posix=False)
        except ValueError:
            tokens = line.split()
        if len(tokens) < 4:
            out.append(raw)
            skipped += 1
            continue
        try:
            start = int(tokens[0])
            end = int(tokens[1])
        except ValueError:
            out.append(raw)
            skipped += 1
            continue

        head = [str(start), str(end), tokens[2]]
        rest = tokens[3:]
        if rest and rest[0].lower() == "reset":
            head.append(rest[0])
            rest = rest[1:]
        params, note = boost_zone_params(rest, step, extra)
        if note:
            notes.append(f"frames {start}-{end}: {note}")
        else:
            unbumped += 1
        out.append(" ".join(head + params))

    if ZONE_BOOST_MARKER not in "\n".join(out):
        # Say what was done in the file itself: the header above claims each
        # line repeats what the range was already encoded with, which stops
        # being true the moment this runs.
        insert_at = next((index for index, text in enumerate(out)
                          if text.strip() and not text.strip().startswith("#")), 0)
        # Back up over the "# frames 100-200" comment that belongs to that first
        # zone line, so the note lands above it rather than between the two.
        while insert_at > 0 and out[insert_at - 1].strip().startswith("#"):
            insert_at -= 1
        out[insert_at:insert_at] = [
            ZONE_BOOST_MARKER,
            f"# Every line below was given {describe_boost(step, extra)},",
            "# so the ranges are no longer a no-op: they will be re-encoded smaller.",
            "# Edit any line further if you want, then run AfterZone again.",
            "#",
        ]

    try:
        make_writable(path)
        with open(path, "w", encoding="utf-8", newline="\r\n") as handle:
            handle.write("\n".join(out) + "\n")
    except OSError as exc:
        print(f"{RED}Could not write {path}: {exc}{RESET}")
        return None
    return notes, unbumped, skipped


def apply_zone_boost(zones_path, step=ZONE_BOOST_CRF_STEP,
                     extra=ZONE_BOOST_EXTRA_PARAMS):
    """The boost menu options: boost every zone line and report what changed."""
    result = boost_zones_file(zones_path, step, extra)
    if result is None:
        return False
    notes, unbumped, skipped = result
    if not notes and not unbumped:
        print(f"{YELLOW}No zone lines were found in {zones_path}, so nothing was "
              f"changed.{RESET}")
        return False

    hr()
    print(f"{GREEN}Updated {zones_path}{RESET}")
    print(f"  {len(notes) + unbumped} zone line(s) now carry "
          f"{describe_boost(step, extra)}.")
    for note in notes:
        print(f"  {note}")
    if unbumped and step:
        # Only reachable per line, not per file: an LQTC line whose --crf was
        # deleted on purpose still gets everything else the boost writes.
        print(f"{YELLOW}  {unbumped} line(s) had no CRF to raise, so "
              + (f"only {' '.join(extra)} was added to them."
                 if extra else "they were left as they were.") + RESET)
    if skipped:
        print(f"{YELLOW}  {skipped} line(s) did not look like zones and were left "
              f"alone.{RESET}")
    hr()
    return True


def offer_zone_boost(zones_path):
    """Offer the boost once a zones file is settled, e.g. after a threshold.

    Applying a threshold rewrites the zones file from scratch, so a boost taken
    before it would be silently undone. That makes this the last question of the
    run, which is also why anything other than 1 or 2 leaves the file alone: a
    mistyped key at the end of a run should not change the zones.
    """
    print("Press Enter to finish, or:")
    print()
    print(f"  {BOLD}1{RESET}: Add +{ZONE_BOOST_CRF_STEP} to the CRF on every line "
          f"of the zones file and")
    print(f"     {' '.join(ZONE_BOOST_EXTRA_PARAMS)} to every line's settings")
    print(f"  {BOLD}2{RESET}: Customize automatic CRF and --tf-strength levels")
    print()
    try:
        answer = input("> ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    print()
    if answer == "1":
        return apply_zone_boost(zones_path)
    if answer == "2":
        levels = prompt_custom_boost_levels()
        if levels is None:
            return False
        return apply_zone_boost(zones_path, *levels)
    return False


def custom_threshold_prompt(ranked, sizes, frame_scale, file_size, to_kbps,
                            reference, fps, zones_path, apply_threshold):
    """Try bitrate thresholds, see what each would save, and optionally apply one.

    Nothing is re-encoded here. A zones file has already been written by the
    time this runs, so the threshold the user tries is measured and shown first
    and only replaces that file if they say so: the point is to answer 'what
    would this cost me' before committing, not to keep regenerating the file.

    `apply_threshold(picked, kbps)` rewrites the zones file (and the plot) for
    the scenes at or above `kbps`. It lives with the caller because it needs the
    encode's chunk list and smoothed curve, neither of which this prompt has.
    """
    global _ended_on_prompt
    _ended_on_prompt = True
    while True:
        print("Press enter to exit. Or:")
        print()
        print(f"  {BOLD}1{RESET}: Set a custom bitrate threshold for zones")
        print(f"  {BOLD}2{RESET}: Add +{ZONE_BOOST_CRF_STEP} to the CRF on every "
              f"line of the zones file and "
              f"{' '.join(ZONE_BOOST_EXTRA_PARAMS)}")
        print("     to every line's settings")
        print(f"  {BOLD}3{RESET}: Customize automatic CRF and --tf-strength levels")
        print()
        try:
            answer = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not answer:
            return
        if answer in ("2", "3"):
            print()
            # Applying a threshold rewrites the zones file from scratch, which
            # would undo this, so the boost is the last thing a run does.
            levels = (ZONE_BOOST_CRF_STEP, ZONE_BOOST_EXTRA_PARAMS)
            if answer == "3":
                levels = prompt_custom_boost_levels()
            if levels is not None:
                apply_zone_boost(zones_path, *levels)
            print_zones_ready(zones_path)
            press_any_key()
            return
        if answer != "1":
            print(f"{YELLOW}Press Enter to exit, 1 to set a custom bitrate "
                  f"threshold, 2 to raise every zone's CRF by "
                  f"{ZONE_BOOST_CRF_STEP}, or 3 to type in your own CRF and "
                  f"--tf-strength.{RESET}")
            print()
            continue

        # Stay in here while the user tries thresholds, so 'try a different one'
        # does not make them walk back through the outer prompt every time.
        while True:
            print()
            kbps = ask_kbps("Bitrate threshold in kbps (for example 8000): ")
            print()
            if kbps is None:
                break

            cutoff = (kbps / to_kbps) if to_kbps else 0.0
            picked = [entry for entry in ranked if entry[0] >= cutoff]
            if not picked:
                highest = ranked[0][0] * to_kbps if ranked else 0.0
                print(f"{YELLOW}No scene averages {kbps:,.0f} kbps or more. The "
                      f"most expensive one is {highest:,.0f} kbps.{RESET}")
                continue

            region_bytes, region_frames = chunks_encoded_bytes(picked, sizes,
                                                               frame_scale)
            typical = reference * to_kbps
            print(f"{len(picked)} scene(s) average {kbps:,.0f} kbps or more"
                  + (f" ({kbps / typical:.1f}x typical)" if typical > 0 else "")
                  + f", {region_frames:,} frames "
                    f"({frames_to_timecode(region_frames * frame_scale, fps)}).")
            print_savings_estimate(region_bytes, file_size)
            print()
            print(f"  {BOLD}1{RESET} - Apply this threshold "
                  f"(rewrite the zones file with these {len(picked)} scene(s))")
            print(f"  {BOLD}2{RESET} - Keep the zones AfterZone already wrote")
            print(f"  {BOLD}3{RESET} - Select a new threshold")
            print()
            choice = ask("Enter 1, 2 or 3: ", {"1", "2", "3"})
            print()
            if choice == "3":
                continue
            if choice == "2":
                print("Keeping the zones AfterZone wrote. Nothing was changed.")
            else:
                apply_threshold(picked, kbps)
            print()
            # The zones are settled now, so the boost is safe to offer here:
            # nothing after this rewrites the file out from under it.
            offer_zone_boost(zones_path)
            # Both paths leave a zones file behind and then end the run, and the
            # .bat will not pause after this one, so say where it is and wait.
            print_zones_ready(zones_path)
            press_any_key()
            return


class BatchZoneSettings:
    """One set of answers, applied to every file's zones.txt.

    Generating a zones file asks two questions that have nothing to do with
    which file is being measured -- the bitrate threshold that picks the ranges,
    and the CRF / --tf-strength written onto them -- and both are usually
    answered the same way for a whole batch of episodes. Answering them once and
    keeping them here means the run is unattended from that point on.

    Only zones-file generation is covered. Whether to re-encode is asked
    separately, on the run that reads the finished zones files, and each file's
    plan is printed before anything starts.
    """

    def __init__(self, generate_missing=True, factor=BITRATE_ZONE_THRESHOLD,
                 boost=None, kbps=None):
        self.generate_missing = generate_missing
        self.factor = factor
        self.boost = boost              # (crf step, extra params), or None
        # An absolute bitrate instead of a percentage: every scene averaging
        # this or more becomes a zone, exactly as the per-file threshold prompt
        # does. `factor` stays set as the fallback for a file where no scene
        # reaches it -- a batch of episodes is not guaranteed to have an
        # expensive one in every episode.
        self.kbps = kbps

    def describe(self):
        """The settings as lines to print back for confirmation."""
        lines = []
        if self.generate_missing:
            if self.kbps:
                lines.append(f"Files with no zones file: zone every scene "
                             f"averaging {self.kbps:,.0f} kbps or more.")
                lines.append(f"  If a file has no such scene, "
                             f"{self.factor * 100:.0f}% of its own typical bitrate "
                             f"is used instead.")
            else:
                lines.append(f"Files with no zones file: generate one at "
                             f"{self.factor * 100:.0f}% of that file's typical "
                             f"bitrate.")
            lines.append("Every generated line gets: "
                         + (describe_boost(*self.boost) if self.boost
                            else "nothing, left as a no-op for you to edit."))
        else:
            lines.append("Files with no zones file: skipped.")
        lines.append("Files that already have a zones file: planned and offered "
                     "for encoding, as usual.")
        return lines


def ask_batch_zone_settings(ready):
    """Offer one set of zone answers for every file. None means 'ask per file'.

    `ready` is every job that has no zones file yet and a finished encode to
    measure one from. Files that already have a zones file are not part of this:
    they go straight to their plan and the prompt that starts the encode, which
    is what AfterZone is for. With fewer than two of them there is nothing to
    share and this is never called.
    """
    hr()
    print(f"{BOLD}{len(ready)} of these have no zones file yet{RESET}")
    hr()
    for job in ready:
        print(f"  {os.path.basename(job.source)}")
    print()
    print("AfterZone can write one for each of them from the bitrate of its own")
    print("finished encode, and normally asks how per file: the threshold that")
    print("picks the ranges, and the CRF / --tf-strength boost that goes onto")
    print("them. You can answer that once for all of these instead.")
    print()
    print(f"  {BOLD}1{RESET} - Answer once, and use those settings for every zones "
          f"file")
    print(f"  {BOLD}2{RESET} - Answer the questions for each video file separately")
    print()
    if ask("Enter 1 or 2: ", {"1", "2"}) == "2":
        print()
        return None
    print()

    hr()
    print(f"{BOLD}Settings for every zones file{RESET}")
    hr()
    print("Generating a zones file only reads the encode: nothing is deleted or")
    print("re-encoded while they are written. You still get a plan, and a prompt")
    print("before anything is re-encoded, on the next run once you have edited")
    print("them.")
    print()
    print(f"  {BOLD}1{RESET} - Generate a zones file for each of these files")
    print(f"  {BOLD}2{RESET} - Skip them, I will write the zones files myself")
    print()
    generate_missing = ask("Enter 1 or 2: ", {"1", "2"}) == "1"
    print()

    factor = BITRATE_ZONE_THRESHOLD
    boost = None
    kbps = None
    if generate_missing:
        default_percent = int(BITRATE_ZONE_THRESHOLD * 100)
        print("Which ranges become zones can be decided two ways, and the same")
        print("answer is used for every file:")
        print()
        print(f"  {BOLD}1{RESET} - A percentage of each file's own typical bitrate")
        print("      Every file is measured against itself, so episodes encoded at")
        print("      different sizes each get their own worst regions zoned.")
        print(f"  {BOLD}2{RESET} - One bitrate in kbps, for all of them")
        print("      Every scene averaging that or more becomes a zone. Use this")
        print("      when you know the bitrate you do not want any scene to exceed.")
        print()
        if ask("Enter 1 or 2: ", {"1", "2"}) == "2":
            print()
            print("Zones are whole scenes here, so nothing gets split: each range")
            print("re-encodes exactly the scenes it covers. A file with no scene")
            print(f"that expensive falls back to {default_percent}% of its own "
                  f"typical bitrate,")
            print("so no file is left without a zones file.")
            print()
            kbps = ask_kbps("Bitrate threshold in kbps (for example 8000): ")
            print()
        if kbps is None:
            print("A region becomes a zone when its bitrate stays this far above the")
            print("typical bitrate of its own file, so the same percentage suits "
                  "files")
            print(f"of different sizes. The default is {default_percent}% "
                  f"({default_percent - 100}% above typical);")
            print("higher percentages select only the most expensive scenes.")
            print()
            answer = ask_percentage(
                f"Percentage above 100 [Enter for {default_percent}]: ",
                blank_hint=f"press Enter for {default_percent}")
            if answer is not None:
                factor = answer / 100.0
            print()

        print("Every generated line repeats the settings its range was already")
        print("encoded with, so a zones file does nothing until something on its")
        print("lines changes.")
        print()
        print(f"  {BOLD}1{RESET} - Add +{ZONE_BOOST_CRF_STEP} to the CRF on every "
              f"line and {' '.join(ZONE_BOOST_EXTRA_PARAMS)} to every")
        print("      line's settings")
        print(f"  {BOLD}2{RESET} - Customize automatic CRF and --tf-strength levels")
        print(f"  {BOLD}3{RESET} - Leave the lines as a no-op so I can edit them "
              f"myself")
        print()
        choice = ask("Enter 1, 2 or 3: ", {"1", "2", "3"})
        print()
        if choice == "1":
            boost = (ZONE_BOOST_CRF_STEP, ZONE_BOOST_EXTRA_PARAMS)
        elif choice == "2":
            boost = prompt_custom_boost_levels()
            print()

    settings = BatchZoneSettings(generate_missing, factor, boost, kbps)
    hr()
    print(f"{BOLD}These settings will be used for all {len(ready)} file(s){RESET}")
    for line in settings.describe():
        print(f"  {line}")
    hr()
    print()
    return settings


def generate_zones_file(job, chunks, total_frames, fps, zones_path,
                        video_output_dir, ffprobe, temp_dir, settings=None):
    """Analyse the finished encode and write a zones.txt the user can edit.

    With `settings` (a BatchZoneSettings) the threshold and the boost are
    already decided, so nothing here stops to ask: the run walks the whole batch
    and leaves one zones file per file behind.
    """
    encoded = find_encoded_output(video_output_dir, job.stem)
    if not encoded:
        die(f"No finished encode found in video-output for '{job.stem}'.\n"
            f"            AfterZone needs the encoded file to measure bitrate.\n"
            f"            Expected something like video-output\\{job.stem}-output.mkv")

    hr()
    print(f"{BOLD}Generating a zones.txt from the bitrate of the finished encode{RESET}")
    hr()
    print(f"Analysing: {encoded}")
    print()
    print("What this does:")
    print("  1. Reads the compressed size of every video frame with ffprobe.")
    print("  2. Smooths those sizes over a 1 second window to get local bitrate.")
    print("  3. Takes the median of that curve as 'the majority of the video'.")
    start_factor = settings.factor if settings else BITRATE_ZONE_THRESHOLD
    batch_kbps = settings.kbps if settings else None
    if batch_kbps:
        print(f"  4. Marks every scene averaging {batch_kbps:,.0f} kbps or more, "
              f"which is the")
        print(f"     bitrate you set for every file. If nothing here reaches it, "
              f"this")
        print(f"     file falls back to {start_factor * 100:.0f}% of its own "
              f"typical bitrate.")
    else:
        print(f"  4. Marks every region that sustains at least "
              f"{start_factor * 100:.0f}% of that typical bitrate")
        print(f"     (i.e. {start_factor * 100 - 100:.0f}% above the majority "
              f"of the video). Regions have to be longer than the")
        print("     smoothing window, so single-frame spikes do not become zones.")
    print("  5. Writes those regions out as zone lines for you to edit.")
    if job.is_lqtc:
        print("     Each line starts from this encode's own parameters plus the CRF")
        print("     LQTC's quality target search picked for that scene, so it is a")
        print("     no-op until you change something.")
    if settings is None:
        print("  You choose the percentage if the default does not suit the file.")
    else:
        print("  The threshold and the boost are the ones you chose for every file,")
        print("  so this runs through without asking anything else.")
    print()
    print(f"{YELLOW}This reads the whole file and can take several minutes on a "
          f"feature length encode.{RESET}")
    print("Nothing is deleted or re-encoded during this step.")
    print()

    sizes = probe_frame_sizes(ffprobe, encoded)
    print(f"Read {len(sizes):,} video frames.")
    # Kept so the run that reads the edited zones file can price it instantly
    # instead of probing the same encode a second time.
    save_frame_sizes(frame_size_cache_path(temp_dir, job.stem), encoded, sizes)

    # The encode should have the same frame count as the chunk list, but stay
    # correct if it does not (VFR sources, stray packets).
    frame_scale = 1.0
    if len(sizes) != total_frames and len(sizes) > 0:
        frame_scale = total_frames / float(len(sizes))
        print(f"{YELLOW}Note: encode has {len(sizes):,} frames but chunks.json covers "
              f"{total_frames:,}; scaling positions by {frame_scale:.4f}.{RESET}")

    window = max(1, int(round(fps)))
    smoothed = rolling_mean(sizes, window)

    # The reference is the median of the *smoothed* curve, i.e. the typical
    # bitrate over time. The median raw frame size is useless for this: content
    # with many skip/repeat frames measures 26 bytes at the median while
    # averaging 7,000, so a percentage of it selects effectively everything.
    reference = median(smoothed)
    if reference <= 0:
        die("Measured bitrate is zero throughout; cannot analyse this file.")

    to_kbps = 8.0 * fps / 1000.0
    peak = max(smoothed) if smoothed else 0.0
    print(f"Typical bitrate: {reference * to_kbps:,.0f} kbps "
          f"(median of the 1 second average)")
    print(f"Peak bitrate:    {peak * to_kbps:,.0f} kbps "
          f"({peak / reference:.1f}x typical)")

    ascii_bitrate_plot(smoothed, fps)

    # Always leave a plot on disk, even if no zones are found or the user gives
    # up, so the bitrate can be reviewed before picking a threshold. It gets
    # rewritten with the zones shaded once a threshold selects something.
    png_path = os.path.join(os.path.dirname(zones_path), f"{job.stem}-bitrate.png")
    png = write_bitrate_png(png_path, smoothed, fps, reference,
                            reference * start_factor, [], frame_scale,
                            start_factor)
    if png:
        print(f"{GREEN}Bitrate plot saved to:{RESET} {png}")
        print("Open it to see the shape of the encode and where the bitrate peaks.")
    else:
        print(f"{YELLOW}Could not write the bitrate plot image "
              f"(matplotlib unavailable).{RESET}")
    print()

    # A lone spike is smeared by the rolling average into a bump one window
    # wide, so require a run to be half again longer than the window. Anything
    # shorter is not worth a zone of its own once it is split out.
    min_len = max(1, int(round(window * 1.5)))
    merge_gap = max(1, window)                # stitch regions under 1s apart

    def detect(factor):
        runs = find_high_bitrate_runs(smoothed, reference * factor,
                                      min_len, merge_gap)
        return scale_runs_to_frames(runs, total_frames, frame_scale)

    try:
        file_size = os.path.getsize(encoded)
    except OSError:
        file_size = 0          # savings still print, just without a total

    ranked = rank_chunks_by_bitrate(chunks, sizes, frame_scale)
    print_scene_map(job, ranked, reference, fps, to_kbps, sizes, frame_scale,
                    file_size)

    def offer_top_scenes(count=3):
        """Fallback that always produces something usable to start from."""
        spans = top_scene_spans(ranked, count)
        print()
        print(f"Top {min(count, len(ranked))} highest-bitrate scene(s):")
        for mean_size, chunk in ranked[:count]:
            print(f"  chunk {job.chunk_label(chunk['index'])}  frames "
                  f"{chunk['start_frame']}-{chunk['end_frame']}  "
                  f"({frames_to_timecode(chunk['start_frame'], fps)} - "
                  f"{frames_to_timecode(chunk['end_frame'], fps)})  "
                  f"{mean_size * to_kbps:,.0f} kbps "
                  f"({mean_size / reference:.1f}x typical)")
        return spans

    peak_factor = (peak / reference) if reference else 1.0
    best_factor = "unknown"          # computed once, on the first failure
    factor = start_factor
    threshold = None
    spans = None
    source_note = ""
    # Only the kbps answer picks whole scenes; a percentage detects the exact
    # frames the bitrate stayed high, which splits the chunks at both ends.
    chunk_aligned = False

    if settings is not None and settings.kbps:
        # The batch form of the per-file "custom bitrate threshold" prompt: the
        # same scene-by-scene selection, with the number answered once instead
        # of once per file.
        cutoff = (settings.kbps / to_kbps) if to_kbps else 0.0
        picked = [entry for entry in ranked if entry[0] >= cutoff]
        if reference and to_kbps:
            print(f"Zone threshold:  {settings.kbps:,.0f} kbps "
                  f"({settings.kbps / (reference * to_kbps):.1f}x this file's "
                  f"typical)")
        else:
            print(f"Zone threshold:  {settings.kbps:,.0f} kbps")
        if picked:
            region_bytes, region_frames = chunks_encoded_bytes(picked, sizes,
                                                               frame_scale)
            print(f"{len(picked)} scene(s) average that or more, {region_frames:,} "
                  f"frames "
                  f"({frames_to_timecode(region_frames * frame_scale, fps)}).")
            print_savings_estimate(region_bytes, file_size, label="these zones")
            spans = merge_chunk_spans(picked)
            source_note = f"scenes averaging {settings.kbps:,.0f} kbps or more"
            chunk_aligned = True
            threshold = cutoff
            factor = (settings.kbps / (reference * to_kbps)
                      if reference and to_kbps else None)
        else:
            highest = ranked[0][0] * to_kbps if ranked else 0.0
            print(f"{YELLOW}No scene in this file averages "
                  f"{settings.kbps:,.0f} kbps; the most expensive one is "
                  f"{highest:,.0f} kbps.{RESET}")
            print(f"Falling back to {factor * 100:.0f}% of this file's own typical "
                  f"bitrate.")
            print()

    while spans is None:
        threshold = reference * factor
        print(f"Zone threshold:  {threshold * to_kbps:,.0f} kbps "
              f"({factor * 100:.0f}% of typical)")
        found = detect(factor)

        if found:
            covered = sum(end - start for start, end in found)
            share = covered * 100.0 / max(1, total_frames)
            print(f"Found {len(found)} region(s) covering {covered:,} frames "
                  f"({share:.1f}% of the video).")
            if share <= 50.0:
                spans = found
                source_note = f"{factor * 100:.0f}% of the typical bitrate"
                break

            # Selecting most of the video means re-encoding most of it, which
            # defeats the point of AfterZone. Let the user decide.
            print()
            print(f"{YELLOW}That is over half the video, so this would re-encode "
                  f"most of it.{RESET}")
            if settings is not None:
                # One answer covers every file, so there is nobody to ask. The
                # zones are kept: writing the file costs nothing, and the plan
                # for it is still printed and confirmed before anything is
                # re-encoded.
                print("Keeping these regions: the batch settings answer this once")
                print("for every file. Raise the percentage and run AfterZone again")
                print("if that is more of this file than you meant to zone.")
                spans = found
                source_note = f"{factor * 100:.0f}% of the typical bitrate"
                break
            print("A higher percentage would target only the most expensive scenes.")
            print()
            print(f"  {BOLD}1{RESET} - Enter a different percentage")
            print(f"  {BOLD}2{RESET} - Use the top 3 highest-bitrate scenes instead")
            print(f"  {BOLD}3{RESET} - Keep these regions anyway")
            print()
            choice = ask("Enter 1, 2 or 3: ", {"1", "2", "3"})
            print()
            if choice == "3":
                spans = found
                source_note = f"{factor * 100:.0f}% of the typical bitrate"
                break
            if choice == "2":
                spans = offer_top_scenes()
                source_note = "the top 3 highest-bitrate scenes"
                factor = None  # no threshold was used
                threshold = None
                break
            answer = ask_percentage("Enter a percentage above 100: ")
            if answer is not None:
                factor = answer / 100.0
            print()
            continue

        # Nothing found at this threshold.
        print()
        print(f"{YELLOW}No region sustains {factor * 100:.0f}% of the typical "
              f"bitrate for longer than {min_len} frames.{RESET}")

        if settings is not None:
            # The fallback the interactive path offers first, taken without
            # asking: it always produces a usable file, and a batch that skipped
            # this episode entirely would be the surprise.
            print("Using the top 3 highest-bitrate scenes for this file instead.")
            spans = offer_top_scenes()
            source_note = "the top 3 highest-bitrate scenes"
            factor = None          # no threshold was used
            threshold = None
            break

        if best_factor == "unknown":
            best_factor = strongest_sustained_factor(detect, peak_factor)

        if best_factor is None:
            print("This encode is very even, so there is no region that stands out")
            print("at any threshold above 100%.")
            print()
            print(f"  {BOLD}1{RESET} - Create zones for the top 3 highest-bitrate "
                  f"scenes anyway")
            print("      They are the most expensive scenes in the file, so they are a")
            print("      reasonable starting point even without a clear peak.")
            print(f"  {BOLD}2{RESET} - Exit without creating a zones file")
            print()
            if ask("Enter 1 or 2: ", {"1", "2"}) == "2":
                print()
                print("No zones file was created.")
                if png:
                    print(f"The bitrate plot is at {png} if you want to read frame "
                          f"ranges off it yourself.")
                print(f"Write {zones_path} by hand to re-encode specific frame ranges.")
                return None
            spans = offer_top_scenes()
            source_note = "the top 3 highest-bitrate scenes"
            factor = None      # no threshold was used
            threshold = None
            break

        suggestion = max(101, int(best_factor * 100))
        print(f"The strongest sustained region is about {best_factor * 100:.0f}% of "
              f"typical ({reference * best_factor * to_kbps:,.0f} kbps), so anything "
              f"up to {suggestion} will find something.")
        print("Lower percentages select more of the video; higher ones select only")
        print("the most expensive scenes.")
        print()
        print(f"  {BOLD}1{RESET} - Enter a different percentage "
              f"(suggested {suggestion})")
        print(f"  {BOLD}2{RESET} - Use the top 3 highest-bitrate scenes instead")
        print(f"  {BOLD}3{RESET} - Exit without creating a zones file")
        print()
        choice = ask("Enter 1, 2 or 3: ", {"1", "2", "3"})
        print()
        if choice == "3":
            print("No zones file was created.")
            if png:
                print(f"The bitrate plot is at {png} if you want to read frame "
                      f"ranges off it yourself.")
            print(f"Write {zones_path} by hand to re-encode specific frame ranges.")
            return None
        if choice == "2":
            spans = offer_top_scenes()
            source_note = "the top 3 highest-bitrate scenes"
            factor = None      # no threshold was used
            threshold = None
            break
        answer = ask_percentage(f"Enter a percentage above 100 "
                                f"(suggested {suggestion}): ")
        if answer is not None:
            factor = answer / 100.0
        print()

    # Redraw with the selected zones shaded, at the threshold that found them.
    png = write_bitrate_png(png_path, smoothed, fps, reference, threshold, spans,
                            frame_scale, factor) or png

    covered = sum(end - start for start, end in spans)
    write_zones_file(zones_path, spans, chunks, job, encoded, fps, reference,
                     peak, to_kbps, source_note, chunk_aligned=chunk_aligned)

    hr()
    print(f"{GREEN}Wrote {zones_path}{RESET}")
    print(f"  {len(spans)} zone(s) from {source_note}, covering {covered:,} of "
          f"{total_frames:,} frames "
          f"({covered * 100.0 / max(1, total_frames):.1f}% of the video).")
    zone_bytes, _zone_frames = spans_encoded_bytes(spans, sizes, frame_scale)
    print_savings_estimate(zone_bytes, file_size, label="these zones")
    if png:
        print(f"{GREEN}Bitrate plot saved to:{RESET} {png}")
        print("  The selected zones are shaded on it, so you can check they line up")
        print("  with the scenes you actually care about.")
    hr()

    if settings is not None:
        # No threshold prompt and no pause: the answers are already in hand, and
        # the next file is waiting. The boost goes on last for the same reason it
        # does interactively -- it is the only thing here that edits the lines
        # rather than rewriting the file.
        if settings.boost:
            print()
            apply_zone_boost(zones_path, *settings.boost)
        print()
        print(f"{GREEN}Zones file ready:{RESET} {zones_path}")
        if not settings.boost:
            print("  Its lines still repeat what the ranges were encoded with, so")
            print("  edit them before running AfterZone again.")
        print()
        return zones_path

    print()
    print(f"{BOLD}Next steps:{RESET}")
    if png:
        print(f"  1. Review {png} to see which regions were picked.")
        print(f"  2. Open {zones_path} in a text editor (Notepad++ is suggested).")
        print("  3. Change the encoder parameters on the lines you care about, and")
        print("     delete the lines you do not want re-encoded.")
        print("  4. Run AfterZone again to re-encode just those chunks.")
    else:
        print(f"  1. Open {zones_path} in a text editor (Notepad++ is suggested).")
        print("  2. Change the encoder parameters on the lines you care about, and")
        print("     delete the lines you do not want re-encoded.")
        print("  3. Run AfterZone again to re-encode just those chunks.")
    print()
    print()

    def apply_threshold(picked, kbps):
        """Replace the zones file with the scenes at or above `kbps`."""
        new_spans = merge_chunk_spans(picked)
        new_covered = sum(end - start for start, end in new_spans)
        note = f"scenes averaging {kbps:,.0f} kbps or more"
        write_zones_file(zones_path, new_spans, chunks, job, encoded, fps,
                         reference, peak, to_kbps, note, chunk_aligned=True)
        # Zone edges here are chunk boundaries, so the shaded plot lines up with
        # the scene table rather than with the smoothed curve's crossings.
        new_png = write_bitrate_png(png_path, smoothed, fps, reference,
                                    kbps / to_kbps if to_kbps else None,
                                    new_spans, frame_scale,
                                    kbps / (reference * to_kbps) if to_kbps else None)
        hr()
        print(f"{GREEN}Rewrote {zones_path}{RESET}")
        print(f"  {len(new_spans)} zone(s) from {note}, covering {new_covered:,} "
              f"of {total_frames:,} frames "
              f"({new_covered * 100.0 / max(1, total_frames):.1f}% of the video).")
        new_bytes, _new_frames = spans_encoded_bytes(new_spans, sizes, frame_scale)
        print_savings_estimate(new_bytes, file_size, label="these zones")
        if new_png:
            print(f"{GREEN}Bitrate plot updated:{RESET} {new_png}")
        hr()

    custom_threshold_prompt(ranked, sizes, frame_scale, file_size, to_kbps,
                            reference, fps, zones_path, apply_threshold)
    return zones_path


# ---------------------------------------------------------------------------
# splitting a chunk at a zone edge
# ---------------------------------------------------------------------------

# av1an serializes every source_cmd argument as a Rust OsString, which json
# encodes as {"Windows": [utf-16 code units]} or {"Unix": [bytes]} depending on
# the platform that wrote the file. Plain strings are accepted as well, in case
# a future av1an changes the representation.

_SELECT_RANGE_RE = re.compile(r"(between\(n\\?,)(\d+)(\\?,)(\d+)(\))")


def osstring_decode(item):
    """The text of one serialized source_cmd argument, or None if unreadable."""
    if isinstance(item, str):
        return item
    if isinstance(item, dict) and len(item) == 1:
        kind, payload = next(iter(item.items()))
        if not isinstance(payload, list):
            return None
        try:
            if kind == "Windows":
                raw = b"".join(struct.pack("<H", int(unit)) for unit in payload)
                return raw.decode("utf-16-le", "surrogatepass")
            if kind == "Unix":
                return bytes(int(byte) for byte in payload).decode("utf-8",
                                                                   "surrogateescape")
        except (TypeError, ValueError, struct.error, UnicodeDecodeError):
            return None
    return None


def osstring_encode(template, text):
    """Re-encode `text` in whatever representation `template` used."""
    if isinstance(template, str):
        return text
    kind = next(iter(template))
    if kind == "Windows":
        raw = text.encode("utf-16-le", "surrogatepass")
        return {"Windows": list(struct.unpack(f"<{len(raw) // 2}H", raw))}
    return {"Unix": list(text.encode("utf-8", "surrogateescape"))}


def retarget_source_cmd(cmd, start, end):
    """A copy of `cmd` that decodes frames [start, end) instead of its own range.

    Two forms exist, and both count frames inclusively at the end, so the last
    frame written is end - 1:

      vspipe ... -s <first> -e <last>          lsmash / ffms2 / dgdecnv /
                                               bestsource, i.e. anything that
                                               goes through a .vpy
      ... select=between(n\\,<first>\\,<last>)   the ffmpeg chunk methods

    Anything else returns None, which makes the caller refuse to split rather
    than write a chunk whose source_cmd disagrees with its start_frame.
    """
    if not isinstance(cmd, list) or not cmd:
        return None
    decoded = [osstring_decode(item) for item in cmd]
    if any(text is None for text in decoded):
        return None

    out = list(cmd)
    has_start = "-s" in decoded
    has_end = "-e" in decoded

    if has_start or has_end:
        # Half a range would silently produce the wrong frames; refuse instead.
        if not (has_start and has_end):
            return None
        for flag, value in (("-s", start), ("-e", end - 1)):
            position = decoded.index(flag)
            if position + 1 >= len(decoded) or not decoded[position + 1].isdigit():
                return None
            out[position + 1] = osstring_encode(cmd[position + 1], str(value))
        return out

    changed = False
    for position, text in enumerate(decoded):
        replaced = _SELECT_RANGE_RE.sub(
            lambda match: f"{match.group(1)}{start}{match.group(3)}{end - 1}"
                          f"{match.group(5)}", text)
        if replaced != text:
            out[position] = osstring_encode(cmd[position], replaced)
            changed = True
    return out if changed else None


def split_blocker(chunk):
    """Why this chunk cannot be split at an interior frame, or None if it can."""
    # An LQTC chunk is a line in the scene file and nothing else: splitting it
    # means inserting a start frame, so there is no command to retarget and
    # nothing that can get in the way.
    if chunk.get("lqtc"):
        return None
    # A condor chunk is an entry in condor.json's scene array: splitting it
    # means inserting entries with new start/end frames. Condor derives every
    # chunk's frame range from those numbers when it encodes, so there is no
    # per-chunk command to retarget either.
    if chunk.get("condor"):
        return None
    if int(chunk.get("passes") or 1) > 1:
        return "it is multi-pass, so both pieces would share one --stats file"
    # Even at passes=1 some setups hand a chunk its own stats file; two pieces
    # writing to one file at the same time would corrupt it.
    for token in chunk.get("video_params") or []:
        if str(token).split("=", 1)[0] in ("--stats", "--fpf", "--pass"):
            return f"its parameters include {str(token).split('=', 1)[0]}, which is " \
                   f"per-chunk state the pieces would share"
    if retarget_source_cmd(chunk.get("source_cmd"), 0, 2) is None:
        return "its source_cmd has no frame range AfterZone recognizes"
    if chunk.get("proxy_cmd") is not None and \
            retarget_source_cmd(chunk.get("proxy_cmd"), 0, 2) is None:
        return "its proxy_cmd has no frame range AfterZone recognizes"
    return None


def split_chunk(chunk, start, end):
    """A copy of `chunk` covering frames [start, end) only."""
    piece = copy.deepcopy(chunk)
    piece["start_frame"] = start
    piece["end_frame"] = end
    if chunk.get("lqtc"):
        # The CRF the quality target search settled on was measured over the
        # whole original scene, so it does not describe this piece. Both the
        # cached value and the seed a generated zones file would start from go.
        piece["lqtc_crf"] = None
        piece["zone_seed"] = list(chunk.get("video_params") or [])
        return piece
    if chunk.get("condor"):
        # The quantizer Target Quality chose is kept, deliberately. It was
        # measured over the whole original scene rather than this piece, but it
        # is a real measurement of this footage and re-encoding the piece with
        # it reproduces the settings the scene already had. A piece no zone
        # covers therefore comes back with the picture it started with.
        #
        # Nothing re-probes it either: AfterZone runs "condor encode", which
        # never runs the Target Quality stage. Only a zone changes a piece's
        # parameters, and then only to what the zones file asked for.
        return piece
    piece["source_cmd"] = retarget_source_cmd(chunk.get("source_cmd"), start, end)
    if chunk.get("proxy_cmd") is not None:
        piece["proxy_cmd"] = retarget_source_cmd(chunk.get("proxy_cmd"), start, end)
    # A cached per-shot target-quality CQ was probed against the original frame
    # range and does not describe this one, so drop it and let av1an re-probe.
    if "per_shot_target_quality_cq" in piece:
        piece["per_shot_target_quality_cq"] = None
    return piece


# ---------------------------------------------------------------------------
# planning
# ---------------------------------------------------------------------------


class PlanEntry:
    """One chunk of the rewritten chunks.json."""

    def __init__(self, chunk, old_index, zone, base, params, reencode, piece):
        self.chunk = chunk
        self.old_index = old_index    # chunk it came from, for the .ivf rename
        self.zone = zone              # None when no zone covers it
        self.base = base              # params before the zone was applied
        self.params = params
        self.reencode = reencode
        self.piece = piece            # (number, count) when part of a split
        self.new_index = None

    @property
    def is_piece(self):
        return self.piece is not None


class Plan:
    def __init__(self, original_count=0):
        self.entries = []       # every chunk of the new chunks.json, in frame order
        # (original index, start, end, reason) where a zone edge could not split.
        # The index is captured here because build_plan renumbers the chunks it
        # is handed, and this is the number the user saw in the scene map.
        self.blocked = []
        self.original_count = original_count

    @property
    def reencodes(self):
        return [entry for entry in self.entries if entry.reencode]

    @property
    def carried(self):
        return [entry for entry in self.entries if not entry.reencode]

    @property
    def split_indices(self):
        return sorted({entry.old_index for entry in self.entries if entry.is_piece})


def covering_zone(zones, start, end):
    """The last zone in file order overlapping [start, end), or None.

    Later zones win where two overlap, which is how av1an resolves them too.
    """
    found = None
    for zone in zones:
        if zone.overlaps(start, end):
            found = zone
    return found


def cut_points(chunk, zones, min_segment):
    """Zone edges strictly inside this chunk, ignoring ones that leave a sliver.

    An edge within min_segment of a chunk boundary (or of the previous edge) is
    dropped, so the zone absorbs those few frames instead of them becoming a
    chunk of their own with a keyframe all to themselves.
    """
    start, end = chunk["start_frame"], chunk["end_frame"]
    edges = {edge for zone in zones for edge in (zone.start, zone.end)
             if start < edge < end}
    kept = []
    for edge in sorted(edges):
        previous = kept[-1] if kept else start
        if edge - previous < min_segment or end - edge < min_segment:
            continue
        kept.append(edge)
    return kept


def build_plan(chunks, zones, min_segment=MIN_SPLIT_SEGMENT):
    """Map zones onto chunks, splitting chunks at zone edges where possible.

    `chunks` must be sorted by start_frame; the result is the complete new
    chunk list in the same order, renumbered from zero: both encoders concat by
    sorting encode\\ on the number in the file name, so it has to stay
    contiguous.
    """
    plan = Plan(len(chunks))
    for chunk in chunks:
        start, end = chunk["start_frame"], chunk["end_frame"]
        base = [str(token) for token in chunk.get("video_params") or []]
        cuts = cut_points(chunk, zones, min_segment)

        if cuts:
            reason = split_blocker(chunk)
            if reason:
                plan.blocked.append((chunk["index"], start, end, reason))
                cuts = []

        bounds = [start] + cuts + [end]
        segments = []
        for piece_start, piece_end in zip(bounds, bounds[1:]):
            zone = covering_zone(zones, piece_start, piece_end)
            if zone is None:
                params = base
            elif zone.reset:
                params = list(zone.params)
            else:
                params = merge_params(base, zone.params)

            # Two zones meeting inside a chunk, or a zone that resolves to the
            # parameters the chunk already had, need no split between them --
            # and no keyframe.
            if segments and segments[-1][3] == params:
                previous = segments[-1]
                segments[-1] = (previous[0], piece_end, zone or previous[2], params)
            else:
                segments.append((piece_start, piece_end, zone, params))

        for number, (piece_start, piece_end, zone, params) in enumerate(segments, 1):
            if len(segments) == 1:
                entry_chunk = chunk
                piece = None
                reencode = zone is not None
            else:
                entry_chunk = split_chunk(chunk, piece_start, piece_end)
                piece = (number, len(segments))
                # The encoded chunk file covers the whole original range and
                # cannot be cut, so every piece has to be encoded again --
                # including the pieces no zone touches.
                reencode = True

            # A copy per piece: several pieces of one chunk can resolve to the
            # same list, and they must not end up sharing it in chunks.json.
            entry_chunk["video_params"] = list(params)
            plan.entries.append(PlanEntry(entry_chunk, chunk["index"], zone,
                                          base, list(params), reencode, piece))

    for new_index, entry in enumerate(plan.entries):
        entry.new_index = new_index
        entry.chunk["index"] = new_index
    return plan


def print_plan(job, plan, chunks, done_sizes, fps, total_frames):
    """The whole change, chunk by chunk, before anything is touched.

    `done_sizes` is {chunk index: encoded bytes} for the chunks that finished,
    normalised from done.json or done.txt by the caller.
    """
    hr()
    print(f"{BOLD}Plan for {os.path.basename(job.source)}{RESET}")
    hr()
    print(f"{'LQTC' if job.is_lqtc else 'av1an'} folder: {job.hash_dir}")
    print(f"Chunks total: {len(chunks)}   already encoded: {len(done_sizes)}")
    if plan.split_indices:
        print(f"Chunks split at a zone edge: {len(plan.split_indices)}   "
              f"chunks.json: {plan.original_count} -> {len(plan.entries)} entries")
    if plan.blocked:
        print()
        for index, start, end, reason in plan.blocked:
            print(f"{YELLOW}[AfterZone] Chunk {job.chunk_label(index)} "
                  f"({start}-{end}) cannot be split because {reason}.{RESET}")
        print(f"{YELLOW}            The zone applies across its whole range "
              f"instead.{RESET}")

    if job.is_lqtc:
        print()
        print(f"{BLUE}[AfterZone] This encode chose its CRF per scene with LQTC's "
              f"quality target search.{RESET}")
        print(f"{BLUE}            A zone that sets --crf overrides that search for "
              f"the scenes it covers;{RESET}")
        print(f"{BLUE}            the search still runs a probe or two whose result "
              f"is then discarded. A{RESET}")
        print(f"{BLUE}            zone that sets no --crf lets the search pick one "
              f"again, with the zone's{RESET}")
        print(f"{BLUE}            parameters applied to it.{RESET}")
    else:
        probing = [entry for entry in plan.reencodes
                   if (entry.chunk.get("target_quality") or {}).get("target")
                   is not None]
        if probing:
            print()
            print(f"{YELLOW}[AfterZone] Target quality is on for {len(probing)} "
                  f"chunk(s) being re-encoded. av1an probes{RESET}")
            print(f"{YELLOW}            for its own CQ, which overrides a --crf set "
                  f"by a zone. Split pieces are{RESET}")
            print(f"{YELLOW}            re-probed, because the cached CQ belonged to "
                  f"the whole original chunk.{RESET}")
    print()
    print(f"{'chunk':>6}  {'from':>11}  {'frames':>15}  {'timecode':>19}  "
          f"{'size':>10}  changes")

    reencode_frames = 0
    reencode_bytes = 0
    counted = set()
    for entry in plan.reencodes:
        chunk = entry.chunk
        start, end = chunk["start_frame"], chunk["end_frame"]
        old_name = job.chunk_label(entry.old_index)
        reencode_frames += end - start

        # Recorded sizes are per original chunk, so a split chunk's size is
        # shown once and counted once rather than per piece.
        size = int(done_sizes.get(entry.old_index) or 0)
        if entry.old_index in counted:
            size_text = ""
        else:
            counted.add(entry.old_index)
            reencode_bytes += size
            size_text = human_bytes(size)

        origin = old_name if entry.piece is None \
            else f"{old_name} {entry.piece[0]}/{entry.piece[1]}"
        if entry.zone is None:
            changes = f"{BLUE}unchanged, re-encoded because of the split{RESET}"
        elif entry.zone.reset:
            changes = "reset (params replaced)"
        else:
            changes = params_summary(entry.base, entry.params)
        timecode = (frames_to_timecode(start, fps) + " - "
                    + frames_to_timecode(end, fps))
        print(f"{job.chunk_label(entry.new_index):>6}  {origin:>11}  "
              f"{f'{start}-{end}':>15}  "
              f"{timecode:>19}  {size_text:>10}  {changes}")

    print()
    print(f"Chunks to re-encode: {len(plan.reencodes)} of {len(plan.entries)}")
    print(f"Frames to re-encode: {reencode_frames:,} of {total_frames:,} "
          f"({reencode_frames * 100.0 / max(1, total_frames):.1f}%)")
    print(f"Encoded data being replaced: {human_bytes(reencode_bytes)}")
    if plan.split_indices:
        shifted = sum(1 for entry in plan.carried
                      if entry.old_index != entry.new_index)
        print()
        print(f"{BOLD}Splitting renumbers chunks.{RESET} Pieces marked 'unchanged' "
              f"keep the parameters they")
        print("were encoded with, but still have to be encoded again: the existing "
              "chunk")
        print("file covers the whole original range and cannot be cut in half. A "
              "keyframe")
        print("is placed at each split, exactly as --zones would have during the "
              "first encode.")
        if shifted:
            print(f"{shifted} finished chunk file(s) are renamed to keep the "
                  f"numbering contiguous.")
    print()
    print("Everything else is reused from the existing encode and will not be")
    print(f"re-encoded. The replaced .{job.chunk_ext} chunks are moved to an "
          f"afterzone-backup")
    print("folder rather than deleted, so nothing is lost if you change your mind.")
    hr()


def print_zone_savings(zones, total_frames, fps, video_output_dir, stem, ffprobe,
                       cache_path):
    """Price the zones read from an edited zones.txt, before anything is touched.

    This is the second-run counterpart to the estimate printed when the file was
    generated: the zone lines may have been edited or deleted since, so the
    numbers are recomputed from whatever is in the file now.

    Never fatal. A missing encode, a failed probe or an unreadable cache only
    costs the estimate -- the re-encode itself does not depend on any of it.
    """
    encoded = find_encoded_output(video_output_dir, stem)
    if not encoded:
        return
    try:
        file_size = os.path.getsize(encoded)
    except OSError:
        return

    sizes = load_frame_sizes(cache_path, encoded)
    if sizes is None:
        print()
        print(f"Measuring {os.path.basename(encoded)} to estimate the size change.")
        print("This only reads the file, and the result is cached for next time.")
        try:
            sizes = probe_frame_sizes(ffprobe, encoded)
        except (SystemExit, OSError, ValueError):
            print(f"{YELLOW}[AfterZone] Could not measure the encode; skipping the "
                  f"size estimate.{RESET}")
            return
        save_frame_sizes(cache_path, encoded, sizes)
    if not sizes:
        return

    frame_scale = 1.0
    if len(sizes) != total_frames:
        frame_scale = total_frames / float(len(sizes))

    zone_bytes, zone_frames = spans_encoded_bytes(
        [(zone.start, zone.end) for zone in zones], sizes, frame_scale)
    if zone_bytes <= 0:
        return

    print()
    print(f"{BOLD}Size estimate{RESET}")
    print(f"The {len(zones)} zone(s) cover {zone_frames:,} frames "
          f"({frames_to_timecode(zone_frames * frame_scale, fps)}) and hold "
          f"{megabytes(zone_bytes)} of the current encode.")
    print_savings_estimate(zone_bytes, file_size, label="these zones")
    print("How much you actually save depends on the parameters on those lines;")
    print("50% is shown as a reference point, not a prediction.")


def measure_file_savings(zones_path, video_output_dir, stem, ffprobe, cache_path,
                         reduction=0.5):
    """What one file weighs now and would weigh with its zones at `reduction`.

    Deliberately self-contained: it reads the finished encode and the zones file
    and nothing else, so the table covering the whole run can be built without
    loading every encoder's state files. The frame count comes from the encode
    itself, which is what the per-file estimate falls back to anyway whenever
    the two agree.

    Returns None when there is nothing to measure -- a missing encode, a failed
    probe, an unreadable zones file. Never fatal: the re-encode does not depend
    on any of it.
    """
    encoded = find_encoded_output(video_output_dir, stem)
    if not encoded:
        return None
    try:
        file_size = os.path.getsize(encoded)
    except OSError:
        return None

    sizes = load_frame_sizes(cache_path, encoded)
    if sizes is None:
        print(f"  Measuring {os.path.basename(encoded)} for the size estimate...")
        try:
            sizes = probe_frame_sizes(ffprobe, encoded)
        except (SystemExit, OSError, ValueError):
            return None
        save_frame_sizes(cache_path, encoded, sizes)
    if not sizes:
        return None

    try:
        zones = parse_zones_file(zones_path, len(sizes), quiet=True)
    except OSError:
        return None
    zone_bytes, zone_frames = spans_encoded_bytes(
        [(zone.start, zone.end) for zone in zones], sizes, 1.0)
    saved = zone_bytes * reduction
    return {
        "zones": len(zones),
        "zone_frames": zone_frames,
        "current": file_size,
        "saved": saved,
        "after": max(0.0, file_size - saved),
    }


def print_run_size_estimate(jobs, video_input_dir, video_output_dir, ffprobe,
                            temp_dir, reduction=0.5):
    """Every file this run would encode, with its size now and after the zones.

    The encode is approved once for the whole run, so the whole run's sizes
    belong next to that decision. Without this the numbers on screen describe
    the first file while the keypress commits to all of them.

    Prints nothing for a single file: the estimate above it already says the
    same thing, in more detail.
    """
    rows = []
    for job in jobs:
        zones_path = os.path.join(video_input_dir, f"{job.stem}.txt")
        if not os.path.exists(zones_path):
            continue
        if find_marker(temp_dir, job.stem)[1]:
            # Mid-encode from an earlier run. It is restarted regardless of what
            # is answered here, so it is not what this keypress decides.
            continue
        measured = measure_file_savings(
            zones_path, video_output_dir, job.stem, ffprobe,
            frame_size_cache_path(temp_dir, job.stem), reduction)
        if measured:
            rows.append((os.path.basename(job.source), measured))
    if len(rows) < 2:
        return

    percent = int(round(reduction * 100))
    width = max([len("file"), len("all files")]
                + [len(name) for name, _ in rows])
    print(f"{BOLD}Every file with a zones file, at {percent}% of the zone "
          f"bitrates{RESET}")
    print(f"  {'file':<{width}}  {'zones':>5}  {'now':>10}  {'after':>10}  "
          f"{'saving':>10}")
    for name, row in rows:
        print(f"  {name:<{width}}  {row['zones']:>5}  "
              f"{megabytes(row['current']):>10}  {megabytes(row['after']):>10}  "
              f"{megabytes(row['saved']):>10}")
    if len(rows) > 1:
        print(f"  {'-' * width}  {'-' * 5}  {'-' * 10}  {'-' * 10}  {'-' * 10}")
        totals = {key: sum(row[key] for _name, row in rows)
                  for key in ("zones", "current", "after", "saved")}
        print(f"  {'all files':<{width}}  {totals['zones']:>5}  "
              f"{megabytes(totals['current']):>10}  "
              f"{megabytes(totals['after']):>10}  "
              f"{megabytes(totals['saved']):>10}")
    print(f"  'after' is what each file would weigh if its zones came back at "
          f"{percent}% of")
    print("  the bitrate they hold now. It is a reference point, not a "
          "prediction:")
    print("  what you actually get is whatever the parameters on those lines do.")


# ---------------------------------------------------------------------------
# applying the plan
# ---------------------------------------------------------------------------


def backup_state_files(job, backup_dir, stamp, names):
    """Copy the files that describe the encode into afterzone-backup\\."""
    kept = []
    for name in names:
        source = os.path.join(job.hash_dir, name) if os.path.dirname(name) == "" \
            else name
        if os.path.exists(fs_path(source)):
            shutil.copy2(fs_path(source), fs_path(os.path.join(
                backup_dir, f"{os.path.basename(source)}.{stamp}.bak")))
            kept.append(os.path.basename(source))
    if kept:
        print(f"[AfterZone] Backed up {', '.join(kept)} to {backup_dir}")


def relocate_chunk_files(job, plan, backup_dir, stamp):
    """Move replaced chunk files aside and renumber the ones being kept.

    Order matters. Everything being re-encoded leaves encode\\ first, then the
    surviving files are renamed into their new numbering from the highest index
    down. Splitting only ever inserts chunks, so a chunk's new index is never
    lower than its old one, which makes descending order collision-free.

    Shared by all three encoders: only the folder and file names differ (five
    digits and .ivf in encode\\ for av1an, four and .obu for LQTC, five and .ivf
    in scenes\\ for Condor), and job.chunk_dir/chunk_label know which.

    For Condor this is the entire "mark for redo" step: it re-encodes a scene
    precisely because the chunk file is not there.
    """
    encode_dir = job.chunk_dir
    default_ext = job.chunk_ext

    extensions = {}
    for entry in plan.entries:
        extensions.setdefault(entry.old_index,
                              entry.chunk.get("output_ext") or default_ext)

    def name_of(index):
        return f"{job.chunk_label(index)}.{extensions[index]}"

    replaced = sorted({entry.old_index for entry in plan.reencodes})
    renames = [(entry.old_index, entry.new_index)
               for entry in plan.carried if entry.old_index != entry.new_index]

    # Written before anything moves, so an interrupted run can be reconstructed
    # by hand: this says what encode\ was supposed to end up looking like.
    journal_path = os.path.join(backup_dir, f"rename-map.{stamp}.json")
    with open(fs_path(journal_path), "w", encoding="utf-8") as handle:
        json.dump({
            "encoder": job.kind,
            "chunks_before": plan.original_count,
            "chunks_after": len(plan.entries),
            "moved_to_backup": [name_of(index) for index in replaced],
            "renames": [{"from": name_of(old),
                         "to": f"{job.chunk_label(new)}.{extensions[old]}"}
                        for old, new in renames],
        }, handle, indent=2)
    if renames:
        print(f"[AfterZone] Wrote {os.path.basename(journal_path)} to "
              f"afterzone-backup\\ before renaming anything")

    moved = 0
    missing = 0
    for index in replaced:
        name = name_of(index)
        source = os.path.join(encode_dir, name)
        if os.path.exists(fs_path(source)):
            destination = os.path.join(backup_dir, name)
            if os.path.exists(fs_path(destination)):
                remove_file(destination)
            shutil.move(fs_path(source), fs_path(destination))
            moved += 1
        else:
            missing += 1

    print(f"[AfterZone] Moved {moved} encoded chunk(s) out of encode\\ "
          f"into afterzone-backup\\")
    if missing:
        print(f"{YELLOW}[AfterZone] {missing} chunk file(s) were already absent "
              f"from encode\\.{RESET}")

    renamed = 0
    for old_index, new_index in sorted(renames, reverse=True):
        ext = extensions[old_index]
        source = os.path.join(encode_dir, f"{job.chunk_label(old_index)}.{ext}")
        destination = os.path.join(encode_dir,
                                   f"{job.chunk_label(new_index)}.{ext}")
        if not os.path.exists(fs_path(source)):
            continue                      # never finished in the original encode
        if os.path.exists(fs_path(destination)):
            die(f"encode\\{job.chunk_label(new_index)}.{ext} already exists, so "
                f"renumbering would overwrite it.\n"
                f"            Nothing else has been changed: the encode's own "
                f"state files are still\n"
                f"            the originals. Restore encode\\ using "
                f"{journal_path} and the backups in\n"
                f"            {backup_dir}.")
        os.replace(fs_path(source), fs_path(destination))
        renamed += 1
    if renamed:
        print(f"[AfterZone] Renamed {renamed} finished chunk file(s) to keep the "
              f"numbering contiguous")

    return journal_path


def apply_plan(job, plan, done_data):
    """Rewrite chunks.json / done.json and move the replaced chunk files aside."""
    backup_dir = os.path.join(job.hash_dir, "afterzone-backup")
    os.makedirs(fs_path(backup_dir), exist_ok=True)

    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup_state_files(job, backup_dir, stamp, ("chunks.json", "done.json"))

    done_map = done_data.get("done") or {}
    relocate_chunk_files(job, plan, backup_dir, stamp)

    # av1an skips a chunk when done.json holds its name, so entries have to
    # follow their chunk to its new number, and re-encoded chunks lose theirs.
    done_data["done"] = {
        f"{entry.new_index:05d}": done_map[f"{entry.old_index:05d}"]
        for entry in plan.carried if f"{entry.old_index:05d}" in done_map
    }

    # Stale concat leftovers from the previous run; av1an regenerates these.
    stale = (glob.glob(os.path.join(job.hash_dir, "group_options_*.json"))
             + glob.glob(os.path.join(job.hash_dir, "group_output_*.mkv"))
             + glob.glob(os.path.join(job.hash_dir, "options.json")))
    for path in stale:
        destination = os.path.join(backup_dir, os.path.basename(path))
        if os.path.exists(fs_path(destination)):
            remove_file(destination)
        shutil.move(fs_path(path), fs_path(destination))
    if stale:
        print(f"[AfterZone] Moved {len(stale)} stale concat file(s) aside so av1an "
              f"rebuilds them.")

    make_writable(job.chunks_path)
    with open(job.chunks_path, "w", encoding="utf-8") as handle:
        json.dump([entry.chunk for entry in plan.entries], handle)
    make_writable(job.done_path)
    with open(job.done_path, "w", encoding="utf-8") as handle:
        json.dump(done_data, handle)
    print(f"[AfterZone] Rewrote chunks.json ({len(plan.entries)} chunk(s), "
          f"{len(plan.reencodes)} to encode) and done.json "
          f"({len(done_data['done'])} chunk(s) still done).")
    return backup_dir


def apply_condor_plan(job, plan, state):
    """Rewrite condor.json's scene array and move the replaced chunks aside.

    Simpler than either of the other two, because condor keeps one state file
    and has no done list. Two moves:

      * every planned chunk becomes a scene, carrying its own encoder block. A
        zone's parameters go straight into that block, which is the whole zone
        mechanism - condor has no --zones of its own, so this is the only way to
        zone one of its encodes.
      * the chunk file of every scene being re-encoded leaves scenes\\. That is
        condor's entire resume rule: run() encodes a scene when, and only when,
        its file is missing.

    Scenes that are kept kept their quantizer, their probe history and their
    recorded byte count, so they are skipped and their pictures are untouched.
    """
    config = state["config"]
    scenes = condor_config_scenes(config)
    if not scenes:
        die("condor.json has no scenes to rewrite.")

    backup_dir = os.path.join(job.hash_dir, "afterzone-backup")
    os.makedirs(fs_path(backup_dir), exist_ok=True)

    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup_state_files(job, backup_dir, stamp, (job.config_path,))

    relocate_chunk_files(job, plan, backup_dir, stamp)

    new_scenes = []
    for entry in plan.entries:
        chunk = entry.chunk
        # The scene condor wrote, so anything AfterZone has no opinion about
        # (sub_scenes, the probe history) survives untouched.
        original = chunk.get("condor_scene")
        scene = copy.deepcopy(original) if isinstance(original, dict) else {}
        scene["start_frame"] = int(chunk["start_frame"])
        scene["end_frame"] = int(chunk["end_frame"])
        scene.setdefault("sub_scenes", None)

        encoder = scene.get("encoder")
        key = condor_encoder_key(encoder)
        if not key:
            die("A condor scene has no encoder block to write parameters into.")
        template = condor_encoder_options(encoder)
        scene["encoder"][key]["options"] = tokens_to_condor_options(
            chunk.get("video_params"), template)

        if entry.reencode:
            # Nothing about the old encode of these frames is true any more.
            # A split piece also gets a fresh record rather than the parent's,
            # so a later run cannot read a byte count that described a
            # different frame range.
            scene["sequence_data"] = blank_condor_sequence_data()
            if isinstance(original, dict) and not entry.is_piece:
                # Not a split: keep the probe history, which still describes
                # these exact frames, so re-running Target Quality by hand
                # later has something to work from.
                previous = (original.get("sequence_data") or {}).get("target_quality")
                if previous:
                    scene["sequence_data"]["target_quality"] = copy.deepcopy(previous)
        new_scenes.append(scene)

    config["condor"]["scenes"] = new_scenes

    # The concatenator caches the mkvmerge command line, which names every
    # chunk file by number. After a split that list is the wrong length.
    stale = glob.glob(os.path.join(job.chunk_dir, "Scene Concatenator",
                                   "options.json"))
    for path in stale:
        destination = os.path.join(backup_dir, os.path.basename(path))
        if os.path.exists(fs_path(destination)):
            remove_file(destination)
        shutil.move(fs_path(path), fs_path(destination))
    if stale:
        print(f"[AfterZone] Moved the cached concatenator command aside so "
              f"condor rebuilds it.")

    write_condor_config(job.config_path, config)
    print(f"[AfterZone] Rewrote {os.path.basename(job.config_path)} "
          f"({len(new_scenes)} scene(s), {len(plan.reencodes)} to encode).")
    return backup_dir


def apply_lqtc_plan(job, plan, state):
    """Rewrite the scene file, done.txt and chunks.json for an LQTC encode.

    The same three moves as the av1an path, in LQTC's own files:
      * the scene file gets one line per planned chunk, carrying that chunk's
        zone parameters - which is the whole zone mechanism
      * done.txt loses the line of every chunk being re-encoded, which is what
        makes LQTC encode it again, and keeps the rest at their new numbers
      * chunks.json loses the quality target record of those chunks, so the
        summary log written at the end of the run describes this encode rather
        than the one it replaced
    """
    # The scene file is written last, because until it changes the encode is
    # still recoverable by putting the backups back. That ordering only helps if
    # the write can be relied on to happen, so it is proved here, before the
    # first chunk moves: a failure now costs nothing, where the same failure
    # after the state files are rewritten leaves an encode that resumes happily
    # and reproduces itself chunk for chunk.
    scene_path = state["scene_path"]
    make_writable(scene_path)
    try:
        with open(scene_path, "a", encoding="utf-8"):
            pass
    except OSError as exc:
        die(f"The LQTC scene file cannot be written to:\n"
            f"            {scene_path}\n"
            f"            {exc}\n"
            f"            That file is the only place the zones can go, so "
            f"nothing has been\n"
            f"            changed and no chunk has been moved. Check that it is "
            f"not open in\n"
            f"            another program and not marked read-only, then run "
            f"AfterZone again.")

    backup_dir = os.path.join(job.hash_dir, "afterzone-backup")
    os.makedirs(fs_path(backup_dir), exist_ok=True)

    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup_state_files(job, backup_dir, stamp,
                       ("done.txt", "chunks.json", "cmd.txt",
                        state["scene_path"]))

    relocate_chunk_files(job, plan, backup_dir, stamp)

    # done.txt is the resume state: a chunk with no line here gets encoded.
    # Carried chunks keep their recorded frame count and size, because their
    # frame range is untouched - only their number moved.
    old_done = state["done"]
    new_done = {}
    for entry in plan.carried:
        record = old_done.get(entry.old_index)
        if record:
            new_done[entry.new_index] = record
    write_lqtc_done(job.done_path, state["elapsed"], new_done)

    old_log = state["chunk_log"]
    new_log = {}
    for entry in plan.carried:
        record = old_log.get(entry.old_index)
        if record:
            new_log[entry.new_index] = record
    if old_log or os.path.exists(job.chunks_path):
        write_lqtc_chunk_log(job.chunks_path, new_log)

    # Written last: while this file still describes the old chunk list, the
    # encode is recoverable from the backups above by putting it back.
    scenes = []
    for entry in plan.entries:
        zone = entry.zone
        # LQTC applies a scene's parameters on top of the global ones itself,
        # so the line carries the zone as written rather than the merged result
        # AfterZone computed for the plan display.
        params = " ".join(str(token) for token in zone.params) if zone else ""
        scenes.append((entry.chunk["start_frame"], params))
    write_lqtc_scenes(state["scene_path"], scenes)
    zoned = verify_lqtc_scenes(state["scene_path"], scenes,
                               "just after writing it")

    # Kept for the launch-time check in run_lqtc, which reads the file LQTC's own
    # command names rather than the one this function was pointed at.
    state["planned_scenes"] = normalized_lqtc_scenes(scenes)

    print(f"[AfterZone] Rewrote {os.path.basename(state['scene_path'])} "
          f"({len(scenes)} scene(s), {zoned} carrying zone parameters), done.txt "
          f"({len(new_done)} chunk(s) still done) and chunks.json.")
    print(f"[AfterZone] Verified the zones are in the scene file LQTC reads:")
    print(f"            {state['scene_path']}")
    shown = 0
    for start, params in state["planned_scenes"]:
        if not params:
            continue
        print(f"              {start} {params}")
        shown += 1
        if shown >= 3:
            break
    if zoned > shown:
        print(f"              ... and {zoned - shown} more zoned scene(s)")

    mirrored = mirror_lqtc_scenes(state["scene_path"], scenes, job.rest_dir, job)
    if mirrored:
        print(f"[AfterZone] Copied the same scene list to "
              f"{os.path.join('temp', os.path.basename(mirrored))}")
    return backup_dir


def repair_vpy_paths(vpy_path, video_input_dir):
    """Fix ffindex/source paths in the .vpy that moved when temp was relocated.

    Auto-Boost moves the working folder out of video-input after encoding,
    which leaves the cachefile= path inside the generated .vpy pointing
    somewhere that no longer exists. Left alone that forces a full re-index (or
    fails outright), so point it at the index file that is actually sitting
    next to the script.
    """
    try:
        with open(vpy_path, "r", encoding="utf-8", errors="replace") as handle:
            text = handle.read()
    except OSError:
        return False

    vpy_dir = os.path.dirname(os.path.abspath(vpy_path))
    stem = Path(vpy_path).stem
    original = text

    def strip_prefix(path):
        return path[4:] if path.startswith("\\\\?\\") else path

    def has_prefix(path):
        return path.startswith("\\\\?\\")

    for keyword in ("cachefile", "source"):
        match = re.search(keyword + r'\s*=\s*r"([^"]*)"', text)
        if not match:
            continue
        current = match.group(1)
        plain = strip_prefix(current)
        if os.path.exists(plain):
            continue

        replacement = None
        if keyword == "cachefile":
            for candidate in (os.path.join(vpy_dir, f"{stem}.ffindex"),
                              os.path.join(vpy_dir, os.path.basename(plain))):
                if os.path.exists(candidate):
                    replacement = candidate
                    break
            if replacement is None and os.path.isdir(vpy_dir):
                # No index yet: let ffms2 write one next to the script.
                replacement = os.path.join(vpy_dir, f"{stem}.ffindex")
        else:
            for ext in VIDEO_EXTS:
                candidate = os.path.join(video_input_dir, f"{stem}{ext}")
                if os.path.exists(candidate):
                    replacement = candidate
                    break

        if replacement:
            new_value = ("\\\\?\\" + replacement) if has_prefix(current) else replacement
            text = text.replace(f'{keyword}=r"{current}"', f'{keyword}=r"{new_value}"')
            text = text.replace(f'{keyword} = r"{current}"', f'{keyword} = r"{new_value}"')
            print(f"[AfterZone] Repaired stale {keyword} path in "
                  f"{os.path.basename(vpy_path)}")

    if text != original:
        shutil.copy2(vpy_path, vpy_path + ".afterzone.bak")
        with open(vpy_path, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        return True
    return False


def baseline_video_params(plan):
    """The commonest params among unzoned chunks: a sane -v for av1an.

    On --resume av1an takes each chunk's params from chunks.json, so this only
    exists so av1an has a coherent global -v to validate and log.
    """
    pool = [tuple(str(token) for token in (entry.chunk.get("video_params") or []))
            for entry in plan.entries if entry.zone is None]
    if not pool:
        pool = [tuple(str(token) for token in (entry.chunk.get("video_params") or []))
                for entry in plan.entries]
    if not pool:
        return []
    return list(Counter(pool).most_common(1)[0][0])


def find_scenes_file(job, temp_dir, video_input_dir):
    """An existing scenes file stops av1an redoing scene detection on resume.

    Auto-Boost writes <name>_scenes.json into the working folder; av1an-only
    .bat files pass av1an a temp\\<name>_scenedetect.json instead, and their
    working folder is video-input, so both roots are searched.
    """
    for candidate in (os.path.join(job.work_dir, f"{job.stem}_scenes.json"),
                      os.path.join(job.work_dir, "scenes.json"),
                      os.path.join(job.hash_dir, "scenes.json"),
                      os.path.join(temp_dir, f"{job.stem}_scenedetect.json"),
                      os.path.join(temp_dir, f"{job.stem}_scenes.json"),
                      os.path.join(video_input_dir, f"{job.stem}_scenedetect.json"),
                      os.path.join(video_input_dir, f"{job.stem}_scenes.json")):
        if os.path.exists(candidate):
            return candidate
    return None


def find_vpy_file(job, temp_dir, video_input_dir):
    """The .vpy av1an was originally given.

    Auto-Boost keeps it in the working folder. av1an-only .bat files leave the
    working folder in video-input but pass av1an an absolute path to a script
    in temp\\, so that is checked too.
    """
    name = f"{job.stem}.vpy"
    for candidate in (os.path.join(job.work_dir, name),
                      os.path.join(temp_dir, name),
                      os.path.join(video_input_dir, name),
                      os.path.join(temp_dir, job.stem, name)):
        if os.path.exists(candidate):
            return candidate
    return None


def run_av1an(job, av1an_exe, temp_dir, video_input_dir, workers, video_params):
    """Re-run av1an with --resume in the original working folder."""
    vpy_name = f"{job.stem}.vpy"
    vpy_path = find_vpy_file(job, temp_dir, video_input_dir)
    if vpy_path is None:
        die(f"VapourSynth script not found: {os.path.join(job.work_dir, vpy_name)}\n"
            f"            av1an needs the same input script it originally used.\n"
            f"            Also looked in {temp_dir}")
    repair_vpy_paths(vpy_path, video_input_dir)

    # av1an resolves -i against its cwd, which is job.work_dir. A script sitting
    # in the working folder is passed by name, because chunks.json's source_cmd
    # is relative in that case too; a script anywhere else is passed absolute,
    # exactly as av1an-dispatch.py does for the av1an-only .bat files.
    if os.path.dirname(os.path.abspath(vpy_path)) == os.path.abspath(job.work_dir):
        input_arg = vpy_name
    else:
        input_arg = os.path.abspath(vpy_path)

    av1_output = f"{job.stem}-av1.mkv"
    stale_output = os.path.join(job.work_dir, av1_output)
    if os.path.exists(stale_output):
        os.remove(stale_output)

    cmd = [
        av1an_exe,
        "-i", input_arg,
        "-e", "svt-av1",
        "--no-defaults",
        "--temp", os.path.basename(job.hash_dir),      # the existing .<hash> folder
        "--resume",                                    # reuse chunks.json / done.json
        "--keep",
        "-y",
        "-x", "0",
        "-w", str(workers),
        "-o", av1_output,
    ]
    if video_params:
        cmd.extend(["-v", " ".join(video_params)])

    scenes = find_scenes_file(job, temp_dir, video_input_dir)
    if scenes:
        cmd.extend(["-s", scenes])
    else:
        print(f"{YELLOW}[AfterZone] No scenes file found; av1an may redo scene "
              f"detection (the chunk list still comes from chunks.json).{RESET}")

    hr()
    print(f"{BOLD}Running av1an{RESET}")
    print(f"Working folder: {job.work_dir}")
    print(f"Command: {' '.join(cmd)}")
    hr()

    started = time.monotonic()
    try:
        if wakepy_keep is not None:
            with wakepy_keep.running():
                subprocess.check_call(cmd, cwd=job.work_dir)
        else:
            subprocess.check_call(cmd, cwd=job.work_dir)
    except KeyboardInterrupt:
        # The marker in temp\ is deliberately left in place: it is what makes
        # the next run skip planning and resume this encode instead.
        print()
        print(f"{YELLOW}[AfterZone] Interrupted. Finished chunks are kept, so run "
              f"AfterZone again to{RESET}")
        print(f"{YELLOW}            carry on from the chunk it stopped at. Nothing "
              f"needs to be redone.{RESET}")
        sys.exit(1)
    except subprocess.CalledProcessError as exc:
        die(f"av1an failed with exit code {exc.returncode}.\n"
            f"            Finished chunks are kept, so running AfterZone again "
            f"resumes this\n"
            f"            encode instead of starting over.\n"
            f"            Your original chunks are in "
            f"{os.path.join(job.hash_dir, 'afterzone-backup')}")
    elapsed = time.monotonic() - started
    print(f"{GREEN}[AfterZone] av1an finished in "
          f"{int(elapsed // 60)}m {int(elapsed % 60)}s{RESET}")
    return stale_output


def stage_lqtc_workdir(job, state):
    """Put LQTC's working folder and scene file back next to the input.

    LQTC names its working folder after a hash of the input path and only looks
    for it next to the input, so an encode can only be continued while both sit
    in video-input. lqtc-dispatch.py moves them to temp\\ once an encode is
    finished, which is where AfterZone usually finds them.

    Idempotent: an encode that was interrupted before the move already has them
    in the right place.
    """
    moves = []
    if os.path.dirname(os.path.abspath(job.hash_dir)) != os.path.abspath(job.work_dir):
        moves.append((job.hash_dir,
                      os.path.join(job.work_dir, os.path.basename(job.hash_dir)),
                      "working folder"))
    scene_target = job.scene_path_in(job.work_dir)
    if os.path.abspath(state["scene_path"]) != os.path.abspath(scene_target):
        moves.append((state["scene_path"], scene_target, "scene file"))

    for source, destination, label in moves:
        if os.path.exists(destination):
            die(f"Cannot move the LQTC {label} into video-input: something is "
                f"already there.\n"
                f"            {destination}\n"
                f"            Nothing has been changed. Move or delete it and "
                f"run AfterZone again.")
        shutil.move(source, destination)
        print(f"[AfterZone] Moved the LQTC {label} to "
              f"{os.path.join('video-input', os.path.basename(destination))}")

    job.hash_dir = os.path.join(job.work_dir, os.path.basename(job.hash_dir))
    job.chunks_path = os.path.join(job.hash_dir, "chunks.json")
    job.done_path = os.path.join(job.hash_dir, "done.txt")
    state["scene_path"] = scene_target
    return job.hash_dir


def restore_lqtc_workdir(job, scene_path):
    """Park LQTC's working folder and scene file back in temp\\ when done.

    Same tidy-up lqtc-dispatch.py does after an encode: video-input is left
    holding nothing but the source, and what is left of the encode ends up
    where cleanup.py can remove it.
    """
    if not job.rest_dir or not os.path.isdir(job.rest_dir):
        return
    parked = os.path.join(job.rest_dir, os.path.basename(job.hash_dir))
    for source, destination, label in (
            (job.hash_dir, parked, "working folder"),
            (scene_path, job.scene_path_in(job.rest_dir), "scene file")):
        if not os.path.exists(source) or \
                os.path.abspath(source) == os.path.abspath(destination):
            continue
        if os.path.exists(destination):
            if label != "scene file":
                print(f"{YELLOW}[AfterZone] Left the LQTC {label} in video-input: "
                      f"{destination} already exists.{RESET}")
                continue
            # The scene file is the exception, and skipping it here used to be
            # the worst outcome available: what is in temp is the stale list this
            # run started from, what is in video-input is the list the encode
            # actually used, zones and all. Keeping the stale one would leave the
            # zoned one stranded in video-input, hide the zones from the next
            # AfterZone run, and make temp\<name>_scd.txt describe an encode that
            # no longer exists. The stale copy is in afterzone-backup already, so
            # it is replaced rather than kept.
            make_writable(destination)
            try:
                os.replace(source, destination)
            except OSError:
                pass                       # different volume; fall through
            else:
                print(f"[AfterZone] Moved the LQTC scene file back to "
                      f"{os.path.join('temp', os.path.basename(destination))}, "
                      f"replacing the copy that was there")
                continue
            if not remove_file(destination):
                print(f"{YELLOW}[AfterZone] Left the LQTC scene file in "
                      f"video-input: could not replace {destination}{RESET}")
                continue
        try:
            shutil.move(source, destination)
        except OSError as exc:
            print(f"{YELLOW}[AfterZone] Could not move the LQTC {label} back to "
                  f"temp: {exc}{RESET}")
            continue
        print(f"[AfterZone] Moved the LQTC {label} back to "
              f"{os.path.join('temp', os.path.basename(destination))}")
    # Only follow the folder if it actually moved, so anything printed after
    # this points at where the chunks really are.
    if os.path.isdir(parked) and not os.path.isdir(job.hash_dir):
        job.hash_dir = parked
        job.chunks_path = os.path.join(job.hash_dir, "chunks.json")
        job.done_path = os.path.join(job.hash_dir, "done.txt")


def resolve_lqtc_exe(argv, tools_dir):
    """The LQTC build to run, from the path cmd.txt recorded.

    That path is absolute, so it is wrong as soon as the package is moved or
    unzipped somewhere else. The file name still names the right build, and
    every build lives in tools\\LQTC.
    """
    recorded = argv[0] if argv else ""
    if recorded and os.path.exists(recorded):
        return recorded
    candidate = os.path.join(tools_dir, "LQTC", os.path.basename(recorded))
    if os.path.basename(recorded) and os.path.exists(candidate):
        print(f"[AfterZone] {recorded} is gone; using {candidate}")
        return candidate
    die(f"The LQTC build this encode used is not there any more:\n"
        f"            {recorded or '(no command recorded)'}\n"
        f"            It is named in {os.path.join('temp', '.<hash>', 'cmd.txt')} "
        f"and has to be in tools\\LQTC.")


def repair_lqtc_cmd(job, state, tools_dir):
    """Fix paths in the recorded command that no longer resolve, in cmd.txt.

    The fix has to go into the file. LQTC re-reads cmd.txt whenever its working
    folder still lists finished chunks and then IGNORES the command line it was
    given (get_saved_args() in main.rs), so correcting an argument here and
    passing it would change nothing: the encode would still use the stale one
    out of the file.

    Two things go stale. A command that was typed by hand rather than written by
    lqtc-dispatch.py can hold paths relative to whatever folder it was run in,
    and AfterZone runs it in video-input; and an absolute path stops resolving
    as soon as the package is moved or unzipped somewhere else.

    Returns the argv to launch.
    """
    argv = list(state["argv"])
    root_dir = os.path.dirname(tools_dir)
    changes = []

    exe = resolve_lqtc_exe(argv, tools_dir)
    if exe != argv[0]:
        changes.append(f"the encoder: {argv[0]} -> {exe}")
        argv[0] = exe

    if not lqtc_cmd_has_flag(argv, "-k", "--keep"):
        # Without it LQTC deletes its working folder after muxing, which would
        # take the chunks with it and leave nothing to run AfterZone on again.
        argv.append("--keep")
        changes.append("added --keep so the chunks survive the run")

    # -d, the CVVDP display description.
    for index in range(1, len(argv) - 1):
        if argv[index] not in ("-d", "--display"):
            continue
        recorded = argv[index + 1]
        found = resolve_lqtc_display_file(recorded, job.work_dir, tools_dir,
                                          root_dir)
        if found is None:
            message = (
                f"The display file this encode used is nowhere to be found:\n"
                f"            -d {recorded}\n"
                f"            Looked in video-input, tools\\LQTC, tools and the "
                f"package folder.\n"
                f"            Put it back (tools\\LQTC\\display.json is where "
                f"this package keeps it)\n"
                f"            and run AfterZone again - nothing else needs "
                f"redoing.")
            if lqtc_target_uses_cvvdp(argv):
                die(message)
            print(f"{YELLOW}[AfterZone] {message}{RESET}")
            print(f"{YELLOW}            This encode's target does not use CVVDP, "
                  f"so it may not be read at all.{RESET}")
        elif found != recorded:
            changes.append(f"the display file: {recorded} -> {found}")
            argv[index + 1] = found
        break

    # -s, the scene list - which for an AfterZone run is the file the zones were
    # just written into. A path that does not resolve is the dangerous one: LQTC
    # would not fail, it would run scene detection and write a fresh scene file
    # there, throwing the zones away and chunking the video differently from the
    # chunks being reused.
    scene_path = os.path.abspath(state["scene_path"])
    recorded_index = None
    for index in range(1, len(argv) - 1):
        if argv[index] in ("-s", "--sc"):
            recorded_index = index + 1
            break
    if recorded_index is not None:
        recorded = argv[recorded_index]
        current = recorded if os.path.isabs(recorded) \
            else os.path.join(job.work_dir, recorded)
        if os.path.abspath(current) != scene_path:
            changes.append(f"the scene file: {recorded} -> {scene_path}")
            argv[recorded_index] = scene_path
    else:
        # With no -s, LQTC derives <input folder>\<name>_scd.txt, which is where
        # AfterZone stages it. Only say so when that somehow is not true.
        default = os.path.abspath(job.scene_path_in(job.work_dir))
        if default != scene_path:
            changes.append(f"named the scene file explicitly: {scene_path}")
            argv.extend(["-s", scene_path])

    if not changes:
        return argv

    print(f"{YELLOW}[AfterZone] The recorded command has paths that do not "
          f"resolve any more. Repairing{RESET}")
    print(f"{YELLOW}            cmd.txt, because that is the command LQTC "
          f"resumes with:{RESET}")
    for change in changes:
        print(f"{YELLOW}              {change}{RESET}")

    backup_dir = os.path.join(job.hash_dir, "afterzone-backup")
    os.makedirs(fs_path(backup_dir), exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    original = os.path.join(job.hash_dir, "cmd.txt")
    if os.path.exists(fs_path(original)):
        shutil.copy2(fs_path(original),
                     fs_path(os.path.join(backup_dir, f"cmd.txt.{stamp}.bak")))
    write_lqtc_cmd(job.hash_dir, argv)
    state["argv"] = list(argv)
    return argv


def run_lqtc(job, state, tools_dir):
    """Re-run the command LQTC recorded, which resumes the encode.

    Nothing about the command is rebuilt or second-guessed beyond repairing
    paths that no longer resolve (see repair_lqtc_cmd). LQTC reads cmd.txt
    itself whenever its working folder still lists finished chunks and ignores
    whatever it was given on the command line, so running anything else here
    would only be misleading: the encode would still use cmd.txt.
    """
    argv = repair_lqtc_cmd(job, state, tools_dir)

    # The zones exist in one file and nowhere else, and everything between
    # writing it and here moves files around: the working folder and the scene
    # file are staged into video-input, and cmd.txt may have been repaired to
    # name a different -s. So the check is repeated against the file this exact
    # command will open, which is the last moment it can still be caught. An
    # encode started with a scene file that lost its zones cannot be told from a
    # correct one until it finishes and the output is the size it always was.
    launch_scene_path = lqtc_scene_file_for(job, argv)
    if state.get("planned_scenes") is not None:
        verify_lqtc_scenes(launch_scene_path, state["planned_scenes"],
                           "the file the recorded command names")
    elif not os.path.exists(launch_scene_path):
        # A resumed run never planned, so there is nothing to compare against -
        # but LQTC silently runs scene detection when this file is missing, and
        # a fresh scene list would not match the chunks being reused.
        die(f"The scene file LQTC's command names is not there:\n"
            f"            {launch_scene_path}\n"
            f"            It holds both the chunk boundaries and the zones, and "
            f"LQTC would\n"
            f"            quietly detect scenes again and encode against a chunk "
            f"list that no\n"
            f"            longer matches the finished chunks. A copy is in "
            f"{os.path.join(job.hash_dir, 'afterzone-backup')}.")

    av1_output = state["output_name"]
    output_path = os.path.join(job.work_dir, av1_output)
    if os.path.exists(output_path):
        remove_file(output_path)

    hr()
    print(f"{BOLD}Running LQTC{RESET}")
    print(f"Working folder: {job.work_dir}")
    print(f"Command: {subprocess.list2cmdline(argv)}")
    print("This is the command from cmd.txt, which is what LQTC resumes with.")
    hr()

    started = time.monotonic()
    try:
        if wakepy_keep is not None:
            with wakepy_keep.running():
                subprocess.check_call(argv, cwd=job.work_dir)
        else:
            subprocess.check_call(argv, cwd=job.work_dir)
    except KeyboardInterrupt:
        # The marker in temp\ is deliberately left in place: it is what makes
        # the next run skip planning and resume this encode instead.
        print()
        print(f"{YELLOW}[AfterZone] Interrupted. Finished chunks are kept, so run "
              f"AfterZone again to{RESET}")
        print(f"{YELLOW}            carry on from the chunk it stopped at. Nothing "
              f"needs to be redone.{RESET}")
        sys.exit(1)
    except subprocess.CalledProcessError as exc:
        die(f"LQTC failed with exit code {exc.returncode}.\n"
            f"            Finished chunks are kept, so running AfterZone again "
            f"resumes this\n"
            f"            encode instead of starting over.\n"
            f"            Your original chunks are in "
            f"{os.path.join(job.hash_dir, 'afterzone-backup')}")
    elapsed = time.monotonic() - started
    print(f"{GREEN}[AfterZone] LQTC finished in "
          f"{int(elapsed // 60)}m {int(elapsed % 60)}s{RESET}")
    return output_path


def resolve_condor_exe(tools_dir):
    import shutil as _sh
    condor_exe = os.path.join(tools_dir, "av1an", "condor")
    if not os.path.exists(condor_exe):
        condor_exe = _sh.which("condor")
    if not condor_exe:
        die("condor was not found in tools/av1an or PATH, so this Condor encode "
            "cannot be re-run.")
    return condor_exe


def condor_environment(root_dir):
    """The environment condor needs in this portable package.

    Condor decodes through the VapourSynth script API rather than vspipe.
    On Linux that is libvapoursynth-script.so, found through the standard
    library paths and exported as VSSCRIPT_PATH.
    """
    env = os.environ.copy()
    configured = (env.get("VSSCRIPT_PATH") or "").strip().strip('"')
    if configured and os.path.exists(configured):
        return env
    for candidate in ("/usr/local/lib/libvapoursynth-script.so",
                      "/usr/lib/x86_64-linux-gnu/libvapoursynth-script.so",
                      "/usr/lib/libvapoursynth-script.so"):
        if os.path.exists(candidate):
            env["VSSCRIPT_PATH"] = candidate
            return env
    print(f"{YELLOW}[AfterZone] libvapoursynth-script.so was not found; "
          f"Condor may fail to start.{RESET}")
    return env


def run_condor(job, state, tools_dir, root_dir):
    """Encode the scenes whose chunk files are gone, then rebuild the output.

    Two stages, deliberately, and neither of them is Target Quality:

      condor encode        encodes every scene with no file in scenes\\, which
                           after apply_condor_plan is exactly the planned ones.
      condor concatenate   muxes the whole scenes\\ folder back into the output.

    The Target Quality stage is not run, so no scene is re-measured and no
    quantizer changes behind your back. A piece a zone does not cover keeps the
    quantizer Target Quality chose during the original encode; a piece a zone
    does cover uses what the zones file asked for, and nothing else.
    """
    condor_exe = resolve_condor_exe(tools_dir)
    env = condor_environment(root_dir)
    output_path = state["output_path"]

    common = ["--config-file", job.config_path, "--temp", job.hash_dir]
    remaining = len([entry for entry in state["planned_entries"]]) \
        if state.get("planned_entries") is not None else None

    hr()
    print(f"{BOLD}Running Condor{RESET}")
    print(f"Config: {job.config_path}")
    print(f"Temp:   {job.hash_dir}")
    if remaining:
        print(f"Condor encodes the {remaining} scene(s) with no file in "
              f"scenes\\, then concatenates.")
    print("Target Quality is not run, so no scene is re-measured.")
    hr()

    started = time.monotonic()
    for stage, argv in (("encode", [condor_exe, "encode"] + common),
                        ("concatenate", [condor_exe, "concatenate"] + common)):
        print(f"[AfterZone] condor {stage}")
        print(f"            {subprocess.list2cmdline(argv)}")
        try:
            if wakepy_keep is not None and stage == "encode":
                with wakepy_keep.running():
                    subprocess.check_call(argv, cwd=job.work_dir, env=env)
            else:
                subprocess.check_call(argv, cwd=job.work_dir, env=env)
        except KeyboardInterrupt:
            # The marker in temp\ is deliberately left in place: it is what
            # makes the next run skip planning and resume this encode instead.
            print()
            print(f"{YELLOW}[AfterZone] Interrupted. Finished chunks are kept, so "
                  f"run AfterZone again to{RESET}")
            print(f"{YELLOW}            carry on from the scene it stopped at. "
                  f"Nothing needs to be redone.{RESET}")
            sys.exit(1)
        except subprocess.CalledProcessError as exc:
            die(f"Condor {stage} failed with exit code {exc.returncode}.\n"
                f"            Finished chunks are kept, so running AfterZone "
                f"again resumes this\n"
                f"            encode instead of starting over.\n"
                f"            Your original chunks are in "
                f"{os.path.join(job.hash_dir, 'afterzone-backup')}")

    elapsed = time.monotonic() - started
    print(f"{GREEN}[AfterZone] Condor finished in "
          f"{int(elapsed // 60)}m {int(elapsed % 60)}s{RESET}")

    if not os.path.exists(output_path):
        die(f"Condor reported success but the output is not there:\n"
            f"            {output_path}")
    return output_path


def read_encoding_settings_tag(mkv_path, tools_dir):
    """Return the Encoded_Library_Settings tag of an existing mkv, or None.
    Files tagged before the rename still carry ENCODING_SETTINGS, so accept
    either name."""
    mkvextract = shutil.which("mkvextract") or os.path.join(tools_dir, "MKVToolNix", "mkvextract")
    if not mkv_path or not os.path.exists(mkv_path) or not os.path.exists(mkvextract):
        return None

    xml = b""
    # New syntax first, then the older "mode first" form for good measure.
    for cmd in ([mkvextract, mkv_path, "tags"], [mkvextract, "tags", mkv_path]):
        try:
            result = subprocess.run(cmd, capture_output=True)
        except OSError:
            return None
        if result.returncode == 0 and result.stdout.strip():
            xml = result.stdout
            break
    if not xml.strip():
        return None

    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError:
        return None
    for simple in root.iter("Simple"):
        name = (simple.findtext("Name") or "").strip().upper()
        if name in ("ENCODED_LIBRARY_SETTINGS", "ENCODING_SETTINGS"):
            value = (simple.findtext("String") or "").strip()
            if value:
                return value
    return None


def apply_encoding_settings_tag(mkv_path, settings, tools_dir):
    """Write settings into the video track's Encoded_Library_Settings tag.
    True on success."""
    mkvpropedit = shutil.which("mkvpropedit") or os.path.join(tools_dir, "MKVToolNix", "mkvpropedit")
    if not os.path.exists(mkvpropedit):
        return False

    xml = (
        '<?xml version="1.0"?>\n'
        "<Tags>\n  <Tag>\n    <Targets>\n      <TrackUID>1</TrackUID>\n"
        "    </Targets>\n    <Simple>\n      <Name>Encoded_Library_Settings</Name>\n"
        f"      <String>{xml_escape(settings)}</String>\n"
        "    </Simple>\n  </Tag>\n</Tags>\n"
    )
    with tempfile.NamedTemporaryFile(delete=False, suffix=".xml", mode="w",
                                    encoding="utf-8") as tmp:
        tmp.write(xml)
        tmp_path = tmp.name
    try:
        subprocess.run([mkvpropedit, mkv_path, "--tags", "track:v1:" + tmp_path],
                       check=True, capture_output=True)
        return True
    except (subprocess.CalledProcessError, OSError):
        return False
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def tag_and_mux(job, av1_path, tools_dir, temp_dir, video_output_dir):
    """Tag and mux the finished encode the way its own dispatcher does.

    av1an-dispatch.py, lqtc-dispatch.py and condor-dispatch.py all stage
    <name>-av1.mkv in temp\\ and then run a tag script and a mux script there,
    so the same pair is reused here - just the encoder's own pair. Condor shares
    av1an-mux.py, exactly as condor-dispatch.py does.
    """
    if not os.path.exists(av1_path):
        die(f"{job.encoder_name} did not produce {av1_path}")

    final = os.path.join(video_output_dir, f"{job.stem}-afterzone.mkv")

    # The encoder settings of this encode are the ones from the original run, so
    # inherit the Encoded_Library_Settings tag from the finished output instead of
    # rebuilding it from whatever .bat happens to be marked as active now.
    reference = find_encoded_output(video_output_dir, job.stem)
    inherited = None
    if reference and os.path.abspath(reference) != os.path.abspath(final):
        inherited = read_encoding_settings_tag(reference, tools_dir)
        if inherited:
            print(f"[AfterZone] Encoder settings taken from "
                  f"{os.path.basename(reference)}")

    # Both helpers run with cwd=temp and glob *-av1.mkv there.
    staged = os.path.join(temp_dir, f"{job.stem}-av1.mkv")
    if os.path.abspath(staged) != os.path.abspath(av1_path):
        if os.path.exists(staged):
            remove_file(staged)
        shutil.move(av1_path, staged)
        print(f"[AfterZone] Moved encode to {staged}")

    # Condor has its own tag script but no mux script: condor-dispatch.py muxes
    # with av1an-mux.py, which only cares about the <name>-av1.mkv naming.
    prefix = {"lqtc": "lqtc", "condor": "condor"}.get(job.kind, "av1an")
    tag_script = os.path.join(tools_dir, f"{prefix}-tag.py")
    mux_script = os.path.join(tools_dir,
                              "av1an-mux.py" if job.is_condor
                              else f"{prefix}-mux.py")

    if inherited:
        print("[AfterZone] Applying inherited encoder-settings tag...")
        if not apply_encoding_settings_tag(staged, inherited, tools_dir):
            print(f"{YELLOW}[AfterZone] Warning: could not copy the encoder-settings "
                  f"tag.{RESET}")
            inherited = None
    elif job.is_lqtc or job.is_condor:
        # lqtc-tag.py and condor-tag.py both rebuild the tag from the newest
        # tools\bat-used-*.txt
        # marker, which while AfterZone is running is AfterZone.bat's own - and
        # that .bat holds none of the LQTC settings. Writing that would describe
        # the encode wrongly, so leave the file untagged and say so.
        print(f"{YELLOW}[AfterZone] No encoder-settings tag to inherit from the "
              f"original output, so{RESET}")
        print(f"{YELLOW}            {job.stem}-afterzone.mkv is left untagged: "
              f"the settings of a{RESET}")
        print(f"{YELLOW}            {job.encoder_name} encode are in its .bat, "
              f"not in anything AfterZone can read here.{RESET}")
    else:
        print("[AfterZone] Applying encoder-settings tag...")
        try:
            subprocess.check_call([sys.executable, tag_script], cwd=temp_dir)
        except subprocess.CalledProcessError:
            print(f"{YELLOW}[AfterZone] Warning: tagging reported an error.{RESET}")

    print("[AfterZone] Muxing audio, subtitles and chapters back in...")
    try:
        subprocess.check_call([sys.executable, mux_script], cwd=temp_dir)
    except subprocess.CalledProcessError:
        die("Muxing failed.")

    muxed = os.path.join(temp_dir, f"{job.stem}-output.mkv")
    if not os.path.exists(muxed):
        die(f"Expected muxed file not found: {muxed}")

    if os.path.exists(final):
        print(f"{YELLOW}[AfterZone] Replacing existing {final}{RESET}")
        remove_file(final)
    shutil.move(muxed, final)

    # mkvmerge may or may not carry track tags across, so stamp the finished file.
    if inherited and not apply_encoding_settings_tag(final, inherited, tools_dir):
        print(f"{YELLOW}[AfterZone] Warning: could not write the encoder-settings tag "
              f"to {os.path.basename(final)}.{RESET}")
    return final


# ---------------------------------------------------------------------------
# the in-progress marker in temp\
# ---------------------------------------------------------------------------

# Written the moment AfterZone is about to touch chunks.json / done.json, and
# removed only once the encode is muxed into video-output. Everything AfterZone
# does to the av1an folder is a one-way edit -- chunks are renumbered, done.json
# entries are dropped, .ivf files are moved aside -- so doing it twice to the
# same folder would apply the zones on top of chunks that already have them and
# renumber a chunk list that has already been renumbered. While the marker is
# there, AfterZone therefore changes nothing at all and only runs av1an, which
# resumes on its own from done.json.

MARKER_PREFIX = "afterzone-active-"
MARKER_SUFFIX = ".txt"
MARKER_STAGE_APPLYING = "applying"     # json files are being rewritten
MARKER_STAGE_ENCODING = "encoding"     # rewritten; av1an is running

MARKER_FIELDS = ("stage", "encoder", "input_file", "stem", "input_path",
                 "zones_file", "scene_file", "output_file", "work_dir",
                 "rest_dir", "hash_dir", "av1an_temp", "av1an_output",
                 "workers", "worker_source", "video_params",
                 "chunks_total", "chunks_to_encode", "frames_total",
                 "started", "resumed", "resumes", "pid")

MARKER_HEADER = [
    "# AfterZone is part way through an encode.",
    "#",
    "# This file exists only while AfterZone has already rewritten the",
    "# encoder's own state files (chunks.json / done.json for av1an, the scene",
    "# file / done.txt for LQTC) and handed the job back to it. Start AfterZone",
    "# again while it is here and it will modify nothing: it skips the zones",
    "# file and the whole plan, and just restarts the encoder, which continues",
    "# at the first chunk that is not finished yet.",
    "#",
    "# It is deleted automatically once the result is muxed into video-output.",
    "# Delete it by hand only if you want AfterZone to plan this job from",
    "# scratch again - your zones would then be applied a second time, on top",
    "# of chunks that already have them.",
    "",
]


def marker_safe_name(stem):
    """A filename-safe version of the input stem, for the marker's own name."""
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", stem).strip(" .")
    return cleaned or "encode"


def marker_path_for(temp_dir, stem):
    return os.path.join(temp_dir,
                        f"{MARKER_PREFIX}{marker_safe_name(stem)}{MARKER_SUFFIX}")


def write_marker(path, data):
    """Write the marker atomically, so a kill mid-write cannot truncate it."""
    lines = list(MARKER_HEADER)
    for key in MARKER_FIELDS:
        if key in data:
            lines.append(f"{key} = {data[key]}")
    for key in sorted(set(data) - set(MARKER_FIELDS)):
        lines.append(f"{key} = {data[key]}")
    lines.append("")
    temporary = path + ".tmp"
    make_writable(temporary)
    with open(temporary, "w", encoding="utf-8", newline="\r\n") as handle:
        handle.write("\n".join(lines))
    make_writable(path)
    os.replace(temporary, path)


def update_marker(path, **changes):
    """Change some fields of an existing marker, keeping the rest."""
    data = read_key_value_file(path) or {}
    data.update({key: str(value) for key, value in changes.items()})
    write_marker(path, data)


def remove_marker(path):
    try:
        os.remove(path)
    except OSError:
        return False
    return True


def all_markers(temp_dir):
    """Every readable marker in temp\\, as (path, fields)."""
    found = []
    pattern = os.path.join(temp_dir, f"{MARKER_PREFIX}*{MARKER_SUFFIX}")
    for path in sorted(glob.glob(pattern)):
        data = read_key_value_file(path)
        if data and data.get("stem"):
            found.append((path, data))
    return found


def find_marker(temp_dir, stem):
    """The marker belonging to `stem`, or (None, None).

    Matched on the stem recorded inside the file rather than on the file name,
    so a name that had to be sanitized still finds its own marker.
    """
    for path, data in all_markers(temp_dir):
        if data.get("stem", "").lower() == stem.lower():
            return path, data
    return None, None


def build_marker_data(job, plan, zones_path, workers, worker_source,
                      video_params, total_frames, stage, av1_output=None,
                      scene_path=None):
    data = {
        "stage": stage,
        "encoder": job.kind,
        "input_file": os.path.basename(job.source),
        "stem": job.stem,
        "input_path": os.path.abspath(job.source),
        "zones_file": os.path.abspath(zones_path),
        "output_file": f"{job.stem}-afterzone.mkv",
        "work_dir": os.path.abspath(job.work_dir),
        "hash_dir": os.path.abspath(job.hash_dir),
        "av1an_temp": os.path.basename(job.hash_dir),
        "av1an_output": av1_output or f"{job.stem}-av1.mkv",
        "workers": str(workers),
        "worker_source": worker_source,
        "video_params": " ".join(video_params),
        "chunks_total": str(len(plan.entries)),
        "chunks_to_encode": str(len(plan.reencodes)),
        "frames_total": str(total_frames),
        "started": time.strftime("%Y-%m-%d %H:%M:%S"),
        "pid": str(os.getpid()),
    }
    if job.is_condor:
        # The config is the whole of the state, and it does not live inside the
        # temp folder, so a resumed run has to be told where it is.
        data["condor_config"] = os.path.abspath(job.config_path or "")
        data["av1an_output"] = av1_output or ""
        # Condor takes its worker count from its own config, so the number
        # AfterZone resolves for av1an is never used. Record the real one.
        data["workers"] = str(condor_config_workers(job.config_path) or "")
        data["worker_source"] = "the workers recorded in condor.json"
    elif job.is_lqtc:
        # Where the working folder and the scene file belong once the encode is
        # over, so a resumed run puts them back even though it never planned.
        data["rest_dir"] = os.path.abspath(job.rest_dir or "")
        data["scene_file"] = os.path.abspath(scene_path or "")
        # The worker count AfterZone resolves for av1an means nothing here: LQTC
        # takes it from the command it resumes with, so record that instead of a
        # number this encode will never use.
        argv = read_lqtc_cmd(job.hash_dir)
        data["workers"] = lqtc_cmd_option(argv, "-w", "--worker") or "1"
        data["worker_source"] = "the command in cmd.txt"
    return data


def describe_remaining(job, scene_path=None):
    """'12 of 480 chunks still to encode', or None if the state is unreadable."""
    if job.is_condor:
        # No done list to read: a scene is finished when its chunk file is
        # there, which is the same rule condor's own resume applies.
        try:
            scenes = condor_config_scenes(read_condor_config(job.config_path))
        except (OSError, ValueError):
            return None
        if not scenes:
            return None
        pending = sum(
            1 for index in range(len(scenes))
            if not os.path.exists(os.path.join(
                job.chunk_dir, f"{job.chunk_label(index)}.{CONDOR_CHUNK_EXT}")))
        return f"{pending} of {len(scenes)} chunk(s) still to encode"

    if job.is_lqtc:
        scenes = read_lqtc_scenes(scene_path) if scene_path else None
        if not scenes:
            return None
        _elapsed, done = read_lqtc_done(job.done_path)
        pending = sum(1 for index in range(len(scenes)) if index not in done)
        return f"{pending} of {len(scenes)} chunk(s) still to encode"

    try:
        with open(job.chunks_path, "r", encoding="utf-8") as handle:
            chunks = json.load(handle)
        with open(job.done_path, "r", encoding="utf-8") as handle:
            done = json.load(handle)
    except (OSError, ValueError):
        return None
    if not isinstance(chunks, list):
        return None
    done_map = done.get("done") or {}
    pending = sum(1 for chunk in chunks
                  if f"{chunk.get('index', -1):05d}" not in done_map)
    return f"{pending} of {len(chunks)} chunk(s) still to encode"


def resume_encode(job, marker_path, marker, av1an_exe, tools_dir, temp_dir,
                  video_input_dir, video_output_dir, fallback_workers):
    """Continue an encode a previous run started, without changing any file.

    No zones are read and no plan is built: chunks.json and done.json already
    describe exactly what is left to do, which is what av1an --resume acts on.
    For Condor the same is true of condor.json plus whatever is in scenes\\.
    """
    encoder_name = job.encoder_name
    hr()
    print(f"{BOLD}Resuming the encode already in progress{RESET}")
    hr()
    print("A previous AfterZone run was interrupted while encoding this file.")
    print(f"Marker: {marker_path}")
    print()
    print(f"  file started:  {marker.get('started', 'unknown')}")
    if marker.get("resumed"):
        print(f"  last resumed:  {marker.get('resumed')} "
              f"(resume #{marker.get('resumes', '?')})")
    print(f"  encoder:       {encoder_name}")
    print(f"  zones file:    {marker.get('zones_file', 'unknown')}")
    print(f"  {(encoder_name + ' folder:'):<14} "
          f"{marker.get('hash_dir', 'unknown')}")
    print(f"  planned:       {marker.get('chunks_to_encode', '?')} of "
          f"{marker.get('chunks_total', '?')} chunk(s) to re-encode")
    print()

    if job.is_condor:
        done_name = os.path.basename(job.config_path or "condor.json")
        state_names = done_name
    elif job.is_lqtc:
        done_name = "done.txt"
        state_names = "cmd.txt / done.txt"
    else:
        done_name = "done.json"
        state_names = "chunks.json / done.json"

    # The marker names the config that run was encoding from. Condor's config
    # sits outside the temp folder, so discovery could have paired this input
    # with a different one.
    if job.is_condor:
        recorded_config = marker.get("condor_config") or ""
        if recorded_config and os.path.exists(recorded_config):
            job.config_path = recorded_config
            job.chunks_path = recorded_config
            job.done_path = recorded_config

    # The marker names the folder that run was encoding into, which is the one
    # to continue even if discovery would have preferred a different one.
    hash_dir = marker.get("hash_dir") or ""
    if hash_dir and os.path.isdir(hash_dir) and \
            os.path.abspath(hash_dir) != os.path.abspath(job.hash_dir):
        print(f"{YELLOW}[AfterZone] Using the {encoder_name} folder named in the "
              f"marker instead of {job.hash_dir}{RESET}")
        job.hash_dir = hash_dir
        if not job.is_condor:
            # Condor's two paths are the one config file, which the marker
            # records separately: its location does not follow the temp folder.
            job.chunks_path = os.path.join(hash_dir, "chunks.json")
            job.done_path = os.path.join(hash_dir, done_name)
        work_dir = marker.get("work_dir") or ""
        job.work_dir = work_dir if os.path.isdir(work_dir) \
            else os.path.dirname(hash_dir)

    required = [job.done_path]
    if job.is_lqtc:
        required.append(job.cmd_path)
    elif not job.is_condor:
        required.append(job.chunks_path)
    if not all(os.path.exists(path) for path in required):
        print(f"{RED}[AfterZone] The {encoder_name} folder in the marker no longer "
              f"has {state_names}:{RESET}")
        print(f"            {job.hash_dir}")
        print("            Nothing can be resumed. Delete the marker if that "
              "encode is gone:")
        print(f"            {marker_path}")
        return None

    scene_path = None
    if job.is_lqtc:
        # The scene file carries the zones the interrupted run wrote, so losing
        # it would mean LQTC re-detecting scenes and encoding something else
        # entirely into a chunk list that no longer matches.
        recorded = marker.get("scene_file") or ""
        for candidate in (recorded, job.scene_path_in(job.work_dir),
                          job.scene_path_in(job.rest_dir or job.work_dir)):
            if candidate and os.path.exists(candidate):
                scene_path = candidate
                break
        if scene_path is None:
            print(f"{RED}[AfterZone] The LQTC scene file for this encode is "
                  f"gone:{RESET}")
            print(f"            {recorded or job.scene_path_in(job.work_dir)}")
            print("            It holds both the chunk boundaries and the zones "
                  "that were applied,")
            print("            so the encode cannot be continued without it. A "
                  "copy is in")
            print(f"            {os.path.join(job.hash_dir, 'afterzone-backup')} "
                  f"if the run got far enough to make one.")
            return None

    remaining = describe_remaining(job, scene_path)
    if remaining:
        print(f"Right now: {remaining}.")

    if job.is_lqtc:
        # A resumed run deliberately changes nothing, which means it also cannot
        # put zones into a scene file that has none -- and LQTC will re-encode
        # every freed chunk to exactly what it already was, finish without an
        # error and produce an output the same size as the one it replaced. That
        # is the only way this tool can fail silently, so the file is read here
        # and the user is told what is actually in it.
        zoned = len([1 for _start, params in (read_lqtc_scenes(scene_path) or [])
                     if params])
        planned = re.sub(r"\D", "", marker.get("chunks_to_encode") or "")
        print(f"Scene file:  {scene_path}")
        print(f"             {zoned} scene(s) carry zone parameters.")
        if not zoned:
            print()
            print(f"{RED}[AfterZone] That scene file holds no zone parameters at "
                  f"all.{RESET}")
            print(f"{YELLOW}            The zones reach LQTC through this file "
                  f"and nothing else, so the{RESET}")
            print(f"{YELLOW}            interrupted run did not get as far as "
                  f"writing them"
                  f"{' (it planned ' + planned + ' chunk(s))' if planned else ''}"
                  f".{RESET}")
            print(f"{YELLOW}            Resuming now re-encodes those chunks to "
                  f"exactly what they already{RESET}")
            print(f"{YELLOW}            are: it will take just as long and the "
                  f"output will be the same{RESET}")
            print(f"{YELLOW}            size as "
                  f"{job.stem}-output.mkv.{RESET}")
            print()
            print("To apply the zones instead, restore this encode from")
            print(f"  {os.path.join(job.hash_dir, 'afterzone-backup')}")
            print("(put the .obu files back in encode\\ and the .bak state files "
                  "back), delete")
            print(f"  {marker_path}")
            print("and run AfterZone again so it plans the job from scratch.")
            print()
            if ask("Resume anyway, knowing the zones are not in it? [y/n]: ",
                   {"y", "n"}) != "y":
                print("Nothing was changed. The marker is still there, so this "
                      "encode can be")
                print("resumed later.")
                return None
            print()

    if marker.get("stage") == MARKER_STAGE_APPLYING:
        print()
        print(f"{YELLOW}[AfterZone] The previous run stopped while it was still "
              f"rewriting {encoder_name}'s{RESET}")
        print(f"{YELLOW}            state files, so the chunk list may be half "
              f"renumbered.{RESET}")
        print(f"{YELLOW}            The originals are in "
              f"{os.path.join(job.hash_dir, 'afterzone-backup')} together with "
              f"the{RESET}")
        print(f"{YELLOW}            rename-map json describing what encode\\ was "
              f"meant to look like.{RESET}")
        print()
        print(f"Continuing runs {encoder_name} on the folder exactly as it "
              f"stands now. That is safe")
        print("in the sense that AfterZone still edits nothing, but the result "
              "is only")
        print("correct if the rewrite had finished.")
        print()
        if ask(f"Run {encoder_name} on the folder as it stands? [y/n]: ",
               {"y", "n"}) != "y":
            print("Nothing was changed. The marker is still there, so this "
                  "encode can be")
            print("resumed later.")
            return None
        print()

    if job.is_condor:
        print()
        print(f"{GREEN}[AfterZone] Nothing on disk is being changed. Condor is "
              f"being restarted, and it{RESET}")
        print(f"{GREEN}            encodes exactly the scenes whose chunk file "
              f"is missing from scenes\\.{RESET}")

        state = load_condor_state(job, shutil.which("ffprobe") or
                                    os.path.join(tools_dir, "av1an", "ffprobe"))
        if state is None:
            return None
        # The zones already went into condor.json before the interruption, so
        # a resumed run reads them straight back out of the scene array. Unlike
        # the LQTC path there is no separate file that could have been lost.
        zoned = len(state["pending"])
        print(f"Config:      {job.config_path}")
        print(f"             {zoned} scene(s) still have no chunk file.")
        if not zoned:
            print()
            print(f"{YELLOW}[AfterZone] Every scene already has a chunk file, so "
                  f"only the concatenate{RESET}")
            print(f"{YELLOW}            step is left to run.{RESET}")

        update_marker(marker_path,
                      stage=MARKER_STAGE_ENCODING,
                      resumed=time.strftime("%Y-%m-%d %H:%M:%S"),
                      resumes=int(re.sub(r"\D", "", marker.get("resumes") or "0")
                                  or 0) + 1,
                      pid=os.getpid())

        av1_path = run_condor(job, state, tools_dir,
                              os.path.dirname(tools_dir))
        final = tag_and_mux(job, av1_path, tools_dir, temp_dir, video_output_dir)
        remove_marker(marker_path)
        return final

    if job.is_lqtc:
        print()
        print(f"{GREEN}[AfterZone] Nothing on disk is being changed. LQTC is "
              f"being restarted with the{RESET}")
        print(f"{GREEN}            command in cmd.txt, which continues at the "
              f"first chunk that is not{RESET}")
        print(f"{GREEN}            listed in done.txt.{RESET}")

        argv = read_lqtc_cmd(job.hash_dir)
        if not argv:
            print(f"{RED}[AfterZone] cmd.txt is empty or unreadable, so there is "
                  f"no command to resume with.{RESET}")
            return None
        _source, output_name = lqtc_cmd_positionals(argv)
        state = {
            "argv": argv,
            "scene_path": scene_path,
            # The name recorded in the marker is the one the interrupted run
            # was already encoding to; cmd.txt is only the fallback.
            "output_name": marker.get("av1an_output") or output_name
                           or f"{job.stem}_xav.mkv",
        }

        update_marker(marker_path,
                      stage=MARKER_STAGE_ENCODING,
                      resumed=time.strftime("%Y-%m-%d %H:%M:%S"),
                      resumes=int(re.sub(r"\D", "", marker.get("resumes") or "0")
                                  or 0) + 1,
                      pid=os.getpid())

        # Staging is a move, not an edit: it only puts the working folder and
        # the scene file back where LQTC looks for them.
        job.rest_dir = job.rest_dir or marker.get("rest_dir") or temp_dir
        stage_lqtc_workdir(job, state)
        update_marker(marker_path, hash_dir=os.path.abspath(job.hash_dir),
                      scene_file=os.path.abspath(state["scene_path"]))

        av1_path = run_lqtc(job, state, tools_dir)
        final = tag_and_mux(job, av1_path, tools_dir, temp_dir, video_output_dir)
        restore_lqtc_workdir(job, state["scene_path"])
        remove_marker(marker_path)
        return final

    digits = re.search(r"\d+", marker.get("workers") or "")
    workers = max(1, int(digits.group(0))) if digits else fallback_workers
    video_params = (marker.get("video_params") or "").split()

    print()
    print(f"{GREEN}[AfterZone] Nothing on disk is being changed. av1an is being "
          f"restarted with{RESET}")
    print(f"{GREEN}            --resume, so it continues at the first chunk "
          f"that is not done.{RESET}")
    print(f"[AfterZone] Workers: {workers} (as recorded when this encode "
          f"started)")

    update_marker(marker_path,
                  stage=MARKER_STAGE_ENCODING,
                  resumed=time.strftime("%Y-%m-%d %H:%M:%S"),
                  resumes=int(re.sub(r"\D", "", marker.get("resumes") or "0")
                              or 0) + 1,
                  pid=os.getpid())

    av1_path = run_av1an(job, av1an_exe, temp_dir, video_input_dir, workers,
                         video_params)
    final = tag_and_mux(job, av1_path, tools_dir, temp_dir, video_output_dir)
    remove_marker(marker_path)
    return final


def report_stale_markers(temp_dir, jobs):
    """Warn about markers whose input video is no longer in video-input."""
    stems = {job.stem.lower() for job in jobs}
    stale = [(path, data) for path, data in all_markers(temp_dir)
             if data.get("stem", "").lower() not in stems]
    if not stale:
        return
    print(f"{YELLOW}[AfterZone] {len(stale)} interrupted encode(s) are recorded "
          f"in temp\\, but their input{RESET}")
    print(f"{YELLOW}            file is not in video-input any more:{RESET}")
    for path, data in stale:
        print(f"  {data.get('input_file', '?')}  ->  {os.path.basename(path)}")
    print("            Put the input file back under the name it had to "
          "resume it, or")
    print("            delete the marker if that encode is not wanted any "
          "more.")
    print()


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def load_av1an_state(job):
    """Everything the plan needs, read out of chunks.json and done.json.

    None when this encode cannot be worked with; the reason is printed.
    """
    try:
        with open(job.chunks_path, "r", encoding="utf-8") as handle:
            chunks_data = json.load(handle)
        with open(job.done_path, "r", encoding="utf-8") as handle:
            done_data = json.load(handle)
    except (OSError, ValueError) as exc:
        print(f"{RED}[AfterZone] Could not read av1an's json files: {exc}{RESET}")
        return None

    if not isinstance(chunks_data, list) or not chunks_data:
        print(f"{RED}[AfterZone] chunks.json is empty or not a list; "
              f"skipping.{RESET}")
        return None

    chunks = sorted(chunks_data, key=lambda c: c.get("start_frame", 0))
    done_map = done_data.get("done") or {}
    return {
        "chunks": chunks,
        "done_data": done_data,
        "done_sizes": {int(name): (record or {}).get("size_bytes") or 0
                       for name, record in done_map.items() if name.isdigit()},
        "total_frames": int(done_data.get("frames") or 0)
                        or max(c["end_frame"] for c in chunks),
        "fps": float(chunks[0].get("frame_rate") or 24.0),
        "pending": [c for c in chunks if f"{c['index']:05d}" not in done_map],
    }


def load_lqtc_state(job, ffprobe, video_output_dir, tools_dir):
    """Everything the plan needs, read out of an LQTC working folder.

    None when this encode cannot be worked with; the reason is printed. The
    checks are deliberately strict, because every reused chunk is trusted to
    still cover exactly the frames the scene file says it does - and if the
    scene file has moved on since the encode, the result would be a video with
    frames missing or repeated rather than an error.
    """
    argv = read_lqtc_cmd(job.hash_dir)
    if not argv:
        print(f"{RED}[AfterZone] Could not read the command in "
              f"{os.path.join(job.hash_dir, 'cmd.txt')}.{RESET}")
        return None

    if lqtc_cmd_has_flag(argv, "-r", "--range"):
        print(f"{RED}[AfterZone] This encode was made with LQTC's -r/--range "
              f"(trim and splice).{RESET}")
        print("            That rewrites the scene list before chunking, so the "
              "scene file no")
        print("            longer says which frames a chunk holds and AfterZone "
              "cannot map")
        print("            zones onto it. Skipping this file.")
        return None

    encoder = (lqtc_cmd_option(argv, "-e", "--encoder") or "svt-av1").lower()
    if encoder != "svt-av1":
        print(f"{RED}[AfterZone] This encode used LQTC with -e {encoder}.{RESET}")
        print("            AfterZone only handles LQTC's svt-av1 output, which is "
              "the only one")
        print("            the .bat files in this package produce. Skipping this "
              "file.")
        return None

    _source, output_name = lqtc_cmd_positionals(argv)
    # LQTC's own default when no output is named (apply_defaults() in main.rs).
    output_name = output_name or f"{job.stem}_xav.mkv"
    if not output_name.lower().endswith(".mkv"):
        print(f"{RED}[AfterZone] This encode wrote {output_name}.{RESET}")
        print("            AfterZone muxes the result with lqtc-mux.py, which "
              "works on mkv only.")
        print("            Skipping this file.")
        return None

    global_params = (lqtc_cmd_option(argv, "-p", "--param") or "").split()

    # -s points somewhere explicit; otherwise LQTC derives the name from the
    # input, and lqtc-dispatch.py parks that file in temp\ after the encode.
    scene_path = None
    recorded = lqtc_cmd_option(argv, "-s", "--sc")
    candidates = []
    if recorded:
        candidates.append(recorded if os.path.isabs(recorded)
                          else os.path.join(job.work_dir, recorded))
    candidates.append(job.scene_path_in(job.work_dir))
    if job.rest_dir:
        candidates.append(job.scene_path_in(job.rest_dir))
    for candidate in candidates:
        if os.path.exists(candidate):
            scene_path = candidate
            break
    if scene_path is None:
        print(f"{RED}[AfterZone] The LQTC scene file for this encode is not "
              f"there:{RESET}")
        for candidate in candidates:
            print(f"            {candidate}")
        print("            It is the chunk list, so without it the finished "
              "chunks cannot be")
        print("            matched to frames. Skipping this file.")
        return None

    scenes = read_lqtc_scenes(scene_path)
    if not scenes:
        print(f"{RED}[AfterZone] {scene_path} has no scene lines; skipping.{RESET}")
        return None

    # Checked now rather than at launch: the encoder gets this path out of
    # cmd.txt and dies on it, and by then the working folder has been rewritten.
    display = lqtc_cmd_option(argv, "-d", "--display")
    if display and lqtc_target_uses_cvvdp(argv) and resolve_lqtc_display_file(
            display, job.work_dir, tools_dir, os.path.dirname(tools_dir)) is None:
        print(f"{RED}[AfterZone] This encode's quality target uses CVVDP, which "
              f"needs the display file{RESET}")
        print(f"            named in its command, and that file is not there: "
              f"-d {display}")
        print("            Looked in video-input, tools\\LQTC, tools and the "
              "package folder.")
        print("            Put it back (tools\\LQTC\\display.json is where this "
              "package keeps it)")
        print("            and run AfterZone again. Skipping this file.")
        return None

    elapsed, done = read_lqtc_done(job.done_path)
    chunk_log = read_lqtc_chunk_log(job.chunks_path)

    stray = [index for index in done if index >= len(scenes)]
    if stray:
        print(f"{RED}[AfterZone] done.txt lists chunk {stray[0]} but the scene "
              f"file only has {len(scenes)} scene(s).{RESET}")
        print("            The scene file has changed since the encode, so the "
              "finished chunks")
        print("            no longer line up with it. Skipping this file.")
        return None

    fps = probe_frame_rate(ffprobe, job.source)
    total_frames = lqtc_total_frames(
        scenes, done, chunk_log, ffprobe,
        [find_encoded_output(video_output_dir, job.stem), job.source])

    chunks = build_lqtc_chunks(scenes, total_frames, global_params, fps, chunk_log)

    # Same check the other way round: a scene's length has to be the frame count
    # that was actually encoded for it.
    for chunk in chunks:
        record = done.get(chunk["index"])
        if not record:
            continue
        expected = chunk["end_frame"] - chunk["start_frame"]
        if int(record["frames"]) != expected:
            print(f"{RED}[AfterZone] Chunk {chunk['index']:04d} was encoded with "
                  f"{record['frames']} frames but the scene{RESET}")
            print(f"            file gives it {expected} (frames "
                  f"{chunk['start_frame']}-{chunk['end_frame']}).")
            print("            The scene file and the finished chunks disagree, "
                  "so reusing them")
            print("            would produce a broken video. Skipping this file.")
            return None

    long_scenes = [chunk["index"] for chunk in chunks
                   if chunk["end_frame"] - chunk["start_frame"]
                   > LQTC_MAX_SCENE_FRAMES]
    if long_scenes:
        print(f"{YELLOW}[AfterZone] {len(long_scenes)} scene(s) are longer than "
              f"{LQTC_MAX_SCENE_FRAMES} frames, which LQTC{RESET}")
        print(f"{YELLOW}            refuses to start on. That is how the scene "
              f"file already is, not{RESET}")
        print(f"{YELLOW}            something AfterZone did, and the re-encode "
              f"will fail on it.{RESET}")

    return {
        "chunks": chunks,
        "scenes": scenes,
        "scene_path": scene_path,
        "argv": argv,
        "global_params": global_params,
        "output_name": output_name,
        "done": done,
        "elapsed": elapsed,
        "chunk_log": chunk_log,
        "done_sizes": {index: record["size_bytes"]
                       for index, record in done.items()},
        "total_frames": total_frames,
        "fps": fps,
        "pending": [chunk for chunk in chunks if chunk["index"] not in done],
    }


def warn_about_lqtc_zone_params(zones):
    """Flag zone parameters LQTC will refuse to start on.

    val() in lqtc-build\\src\\svterr.rs walks the parameter string in
    flag/value pairs and errors on a flag with nothing after it, so a bare
    switch stops the whole encode - after AfterZone has already rewritten the
    scene file. Saying so before the confirmation prompt is the difference
    between editing one zone line and restoring from afterzone-backup.
    """
    for zone in zones:
        for flag, value in parse_params(zone.params):
            if value is None:
                print(f"{YELLOW}[AfterZone] Line {zone.line_number}: '{flag}' has "
                      f"no value.{RESET}")
                print(f"{YELLOW}            LQTC requires a value for every "
                      f"parameter and refuses to start{RESET}")
                print(f"{YELLOW}            otherwise, so write it as e.g. "
                      f"'{flag} 1'.{RESET}")


def load_condor_state(job, ffprobe):
    """Everything the plan needs, read out of condor.json.

    None when this encode cannot be worked with; the reason is printed.

    There is no done file to reconcile: a scene counts as finished when its
    chunk file is in scenes\\, which is the same rule condor itself applies.
    """
    try:
        config = read_condor_config(job.config_path)
    except (OSError, ValueError) as exc:
        print(f"{RED}[AfterZone] Could not read condor's config: {exc}{RESET}")
        return None

    scenes = condor_config_scenes(config)
    if not scenes:
        print(f"{RED}[AfterZone] {os.path.basename(job.config_path)} lists no "
              f"scenes; skipping.{RESET}")
        return None

    scenes = sorted(scenes, key=lambda scene: int(scene.get("start_frame") or 0))
    encoder_key = condor_encoder_key((config.get("condor") or {}).get("encoder")
                                     or (scenes[0].get("encoder") or {}))
    encoder_name = {"SVTAV1": "svt_av1"}.get(encoder_key or "",
                                             (encoder_key or "").lower())

    output_path = windows_path_to_local(
        (((config.get("condor") or {}).get("output") or {}).get("path")) or "")
    if not output_path:
        print(f"{RED}[AfterZone] condor.json records no output path; "
              f"skipping.{RESET}")
        return None

    # The config has no frame rate, so it comes from the encode itself. The
    # source is the fallback because a finished output is not guaranteed to
    # still be there.
    fps = 24.0
    for candidate in (output_path, job.source):
        if candidate and os.path.exists(candidate):
            fps = probe_frame_rate(ffprobe, candidate, 24.0)
            break

    chunks = build_condor_chunks(scenes, fps, encoder_name)
    total_frames = max((chunk["end_frame"] for chunk in chunks), default=0)

    chunk_dir = job.chunk_dir
    finished = {}
    pending = []
    for chunk in chunks:
        path = os.path.join(chunk_dir,
                            f"{job.chunk_label(chunk['index'])}.{CONDOR_CHUNK_EXT}")
        if os.path.exists(path):
            # The file on disk is the truth; the recorded byte count is only a
            # fallback for a chunk written by a condor that did not record one.
            try:
                finished[chunk["index"]] = os.path.getsize(path)
            except OSError:
                finished[chunk["index"]] = condor_scene_bytes(scenes[chunk["index"]])
        else:
            pending.append(chunk)

    return {
        "config": config,
        "chunks": chunks,
        "scenes": scenes,
        "encoder_key": encoder_key,
        "output_path": output_path,
        "done_sizes": finished,
        "total_frames": total_frames,
        "fps": fps,
        "pending": pending,
    }


def describe_condor_encode(job, state):
    """The few lines of context the plan is easier to read with."""
    config = state["config"]
    target_quality = (((config.get("condor") or {}).get("sequence_config") or {})
                      .get("target_quality") or {})
    metric = target_quality.get("metric") or {}
    metric_name = condor_encoder_key(metric) or "unknown"
    body = (metric.get(metric_name) or {}) if isinstance(metric, dict) else {}
    target_range = body.get("target_range")

    print(f"Encoder: Condor (condor.exe), {state['encoder_key'] or 'unknown'}")
    if target_range and len(target_range) == 2:
        print(f"Quality target: {metric_name} {target_range[0]:g}-{target_range[1]:g}"
              f"   quantizer range: "
              f"{'-'.join(str(v) for v in target_quality.get('quantizer_range') or [])}")
    quantizers = [chunk["condor_crf"] for chunk in state["chunks"]
                  if chunk.get("condor_crf") is not None]
    if quantizers:
        print(f"Target Quality chose quantizers {min(quantizers):g}-"
              f"{max(quantizers):g} across {len(state['chunks'])} scene(s).")
        print("Those are kept: a scene no zone covers is re-encoded with the "
              "quantizer it")
        print("already had, and Target Quality is not run again.")
    print(f"Config: {job.config_path}")
    print()


def describe_lqtc_encode(job, state):
    """The few lines of context the plan is easier to read with."""
    argv = state["argv"]
    target = lqtc_cmd_option(argv, "-t", "--tq")
    crf_range = lqtc_cmd_option(argv, "-f", "--qp")
    workers = lqtc_cmd_option(argv, "-w", "--worker")
    print(f"Encoder: LQTC ({os.path.basename(argv[0])})")
    if target:
        print(f"Quality target: {target}"
              + (f"   CRF range: {crf_range}" if crf_range else ""))
    print(f"Encode workers: {workers or '1'}  (from the command in cmd.txt, "
          f"which is what LQTC resumes with)")
    if state["global_params"]:
        print(f"Global encoder parameters: {' '.join(state['global_params'])}")
    print(f"Scene file: {state['scene_path']}")
    print()


def print_intro(fork):
    hr()
    print(f"{BOLD}AfterZone{RESET}")
    hr()
    print("AfterZone will use a zones.txt after your encoding is already finished.")
    print("It will remove encoded chunks from the encoder's working folder and edit")
    print("its state files so only the chunks that need new settings are encoded.")
    print()
    print("It works with all three encoders in this package - av1an, LQTC and")
    print("Condor - and reads which one produced an encode off the working folder")
    print("it left behind.")
    print()
    print("Zone edges do not have to line up with the chunks. An edge inside a chunk")
    print("splits that chunk, the same way zones would have during a fresh encode, so")
    print("only the frames you name get the new settings. The rest of a split chunk is")
    print("re-encoded unchanged, because a finished chunk cannot be cut in half.")
    print()
    print(f"{BLUE}For av1an encodes it will use whatever svt-av1 fork was used "
          f"last:{RESET}")
    print(f"  {fork}")
    print("  (that is tools\\av1an\\SvtAv1EncApp.exe, put there by the last .bat you")
    print("   ran. Run that .bat again first if you want a different fork.)")
    print(f"{BLUE}LQTC encodes are re-run with the exact command the encode itself "
          f"used,{RESET}")
    print("  which is the only thing LQTC will resume with, so the build, the worker")
    print("  counts and the quality target all stay as they were.")
    print(f"{BLUE}Condor encodes get their zones written into condor.json, which is "
          f"the{RESET}")
    print("  only way to zone one: condor has no zones option of its own. The")
    print("  quantizer Target Quality chose for each scene is kept, and Target")
    print("  Quality is not run again, so only the frames a zone names change.")
    print()
    print("A zones.txt that matches the input video's filename is required, placed")
    print("alongside the input file in the video-input folder.")
    print("  example.mkv  ->  video-input\\example.txt")
    print("There is no need to write one by hand: AfterZone can measure the finished")
    print("encode and generate it, then raise the CRF and set --tf-strength on every")
    print("line for you, at the levels it suggests or at ones you type in.")
    print()
    print("Every input with a working folder is done in turn, so a folder of episodes")
    print("is one run. When more than one of them still needs a zones file written,")
    print("you are asked whether to answer the questions for that per file or once")
    print("for all of them. A file that already has its zones file is never part of")
    print("that: it goes straight to its plan and the prompt that starts the")
    print("encode. That prompt is answered once for the whole run, so a folder of")
    print("episodes is approved with one keypress and then left to finish.")
    print()
    print("Output is written to video-output as <name>-afterzone.mkv. Your original")
    print("<name>-output.mkv is left alone so you can compare the two.")
    print()
    print("Closing this window during an encode is safe. AfterZone leaves a marker in")
    print("temp\\ while the encoder is running, and the next run finds it, changes")
    print("nothing, and just restarts the encoder so it resumes where it left off.")
    hr()
    print()


def main():
    script_path = os.path.abspath(__file__)
    tools_dir = os.path.dirname(script_path)
    root_dir = os.path.dirname(tools_dir)

    video_input_dir = os.path.join(root_dir, "video-input")
    video_output_dir = os.path.join(root_dir, "video-output")
    temp_dir = os.path.join(root_dir, "temp")

    av1an_exe = shutil.which("av1an") or os.path.join(tools_dir, "av1an", "av1an")
    ffprobe_exe = shutil.which("ffprobe") or os.path.join(tools_dir, "av1an", "ffprobe")

    # Match the PATH the .bat files set, so this also works when run directly.
    for entry in (os.path.join(root_dir, "VapourSynth"),
                  os.path.join(tools_dir, "av1an"),
                  os.path.join(tools_dir, "MKVToolNix")):
        if os.path.isdir(entry):
            os.environ["PATH"] = entry + os.pathsep + os.environ.get("PATH", "")

    # av1an is not checked here any more: an LQTC encode never touches it,
    # and refusing to start over a missing av1an would block a package that is
    # only used for quality target encodes. The av1an path checks it per job.
    for directory in (video_input_dir, video_output_dir):
        if not os.path.isdir(directory):
            short = os.path.join(os.path.basename(root_dir),
                                 os.path.relpath(directory, root_dir))
            die("Required folder is missing, this tool is for reencoding scenes "
                f"after an output mkv and associated temp files already exist: {short}")
    # temp\ holds the av1an folder for Auto-Boost encodes, but for av1an-only
    # ones it may hold nothing AfterZone needs. It is still where tagging and
    # muxing run, so make sure it exists rather than refusing to start.
    os.makedirs(temp_dir, exist_ok=True)

    print_intro(svt_av1_version(tools_dir))

    jobs, sources, orphans = discover_jobs(video_input_dir, temp_dir)
    report_stale_markers(temp_dir, jobs)
    if not jobs:
        print(f"{RED}[AfterZone] No finished encode found to work with.{RESET}")
        print()
        if not sources:
            print("There is no video in video-input. AfterZone needs the original")
            print("input file still sitting there.")
        else:
            print("Found these inputs but no matching working folder:")
            for source in sources:
                stem = Path(source).stem
                print(f"  {os.path.basename(source)}  ->  looked for "
                      f"temp\\{stem}\\.<hash>\\chunks.json, "
                      f"video-input\\{stem}\\.<hash>\\chunks.json")
                print(f"      any video-input\\.<hash>\\chunks.json naming "
                      f"{stem}.vpy (av1an), and")
                print(f"      any temp\\.<hash>\\cmd.txt or "
                      f"video-input\\.<hash>\\cmd.txt naming "
                      f"{os.path.basename(source)} (LQTC)")
                print(f"      temp\\{stem}{CONDOR_CONFIG_SUFFIX} beside "
                      f"temp\\{stem}{CONDOR_TEMP_SUFFIX}\\{CONDOR_SCENES_DIR} "
                      f"(Condor)")
            print()
            print("AfterZone needs the working folder that the encode left behind,")
            print("which only survives when the encode ran with --keep and cleanup did")
            print("not remove it.")
            if orphans:
                print()
                print(f"{YELLOW}These working folders are still here but none of "
                      f"them names an input file that is still there:{RESET}")
                for hash_dir in orphans:
                    print(f"  {hash_dir}")
                print("Put the matching source video back in video-input, under the")
                print("name it had when it was encoded.")
        sys.exit(1)

    if orphans:
        print(f"{YELLOW}[AfterZone] Ignoring {len(orphans)} working folder(s) with "
              f"no matching input file:{RESET}")
        for hash_dir in orphans:
            print(f"  {hash_dir}")
        print()

    # The batch question is about WRITING zones files, so it is only worth
    # asking for jobs that need one written. A job that already has its zones
    # file is what AfterZone exists for: it goes straight to its plan and the
    # y/n that starts the encode, and nothing may come between the two. A job
    # with a marker is handed back to its encoder without a single question, and
    # a job with no encode in video-output has nothing to measure.
    batch = None
    needs_zones = [job for job in jobs
                   if not os.path.exists(
                       os.path.join(video_input_dir, f"{job.stem}.txt"))
                   and find_encoded_output(video_output_dir, job.stem)
                   and not find_marker(temp_dir, job.stem)[1]]

    if len(jobs) > 1:
        print(f"Found {len(jobs)} finished encode(s) with working folders still "
              f"present:")
        for job in jobs:
            encoded = find_encoded_output(video_output_dir, job.stem)
            where = (os.path.join("video-output", os.path.basename(encoded))
                     if encoded else f"{YELLOW}nothing in video-output{RESET}")
            print(f"  {os.path.basename(job.source)}  ({job.encoder_name})  ->  "
                  f"{where}")
        print()

    if len(needs_zones) > 1:
        batch = ask_batch_zone_settings(needs_zones)

    # Only meaningful for av1an. An LQTC encode's worker count is part of the
    # command line it is resumed with, and a Condor one is in its config, so
    # neither is chosen here.
    workers, worker_source = resolve_worker_count(tools_dir, root_dir)
    if any(job.kind == "av1an" for job in jobs):
        print(f"Encode workers: {workers}  (from {worker_source})")
        print()
    processed = []
    zones_written = []
    # Set when the encode is approved. That approval is for the run, not for one
    # file: a folder of episodes is confirmed once and then left to finish.
    encode_all = False

    for index, job in enumerate(jobs):
        hr()
        print(f"{BOLD}{os.path.basename(job.source)}{RESET}")
        hr()

        # An unfinished encode from a previous run takes priority over
        # everything else: the state files were already rewritten for it, so the
        # only correct thing to do is hand it back to the encoder untouched.
        marker_path, marker = find_marker(temp_dir, job.stem)
        if marker:
            final = resume_encode(job, marker_path, marker, av1an_exe, tools_dir,
                                  temp_dir, video_input_dir, video_output_dir,
                                  workers)
            if final:
                processed.append(final)
                hr()
                print(f"{GREEN}Done: {final}{RESET}")
                print(f"Original encode is untouched at "
                      f"{os.path.join(video_output_dir, job.stem + '-output.mkv')}")
                print(f"Replaced chunks are kept in "
                      f"{os.path.join(job.hash_dir, 'afterzone-backup')}")
                print("Delete that folder once you are happy with the result.")
                hr()
            continue

        if job.is_condor:
            state = load_condor_state(job, ffprobe_exe)
        elif job.is_lqtc:
            state = load_lqtc_state(job, ffprobe_exe, video_output_dir,
                                    tools_dir)
        else:
            if not os.path.exists(av1an_exe):
                print(f"{RED}[AfterZone] av1an not found at {av1an_exe}, so "
                      f"this av1an encode cannot be re-run.{RESET}")
                continue
            state = load_av1an_state(job)
        if state is None:
            continue

        chunks = state["chunks"]
        total_frames = state["total_frames"]
        fps = state["fps"]
        if job.is_condor:
            describe_condor_encode(job, state)
        elif job.is_lqtc:
            describe_lqtc_encode(job, state)

        pending = state["pending"]
        if pending:
            print(f"{YELLOW}[AfterZone] {len(pending)} chunk(s) were never finished in "
                  f"the original encode.{RESET}")
            print("            They will be encoded too. Zones still apply normally.")

        zones_path = os.path.join(video_input_dir, f"{job.stem}.txt")
        if not os.path.exists(zones_path) and batch is not None:
            if not batch.generate_missing:
                print(f"{YELLOW}No zones file at {zones_path}, and the batch "
                      f"settings say to skip those.{RESET}")
                continue
            if not find_encoded_output(video_output_dir, job.stem):
                # Nothing to measure. Skipped rather than fatal: the rest of the
                # batch is still worth doing, and this one only needs its encode
                # put back in video-output.
                print(f"{YELLOW}No zones file, and no finished encode in "
                      f"video-output to measure for one. Skipping this "
                      f"file.{RESET}")
                continue
            print(f"No zones file yet: {zones_path}")
            print("Generating one with the settings chosen for every file.")
            print()
            if generate_zones_file(job, chunks, total_frames, fps, zones_path,
                                   video_output_dir, ffprobe_exe, temp_dir,
                                   settings=batch):
                zones_written.append(zones_path)
            continue

        if not os.path.exists(zones_path):
            print(f"{RED}No zones file found: {zones_path}{RESET}")
            print()
            print("AfterZone needs to know which frame ranges to re-encode.")
            print()
            print(f"  {BOLD}1{RESET} - Auto-generate a zones.txt I can edit.")
            print("      AfterZone measures the bitrate of the finished encode and")
            print("      writes zone lines covering every region whose bitrate is at")
            print(f"      least {int((BITRATE_ZONE_THRESHOLD - 1) * 100)}% higher than "
                  f"the majority of the video.")
            print("      This only reads the file. Nothing is deleted or re-encoded.")
            print(f"  {BOLD}2{RESET} - Exit so I can write my own zones.txt.")
            print("      See zones-example.txt in the package root for the format.")
            print()
            choice = ask("Enter 1 or 2: ", {"1", "2"})
            print()
            if choice == "2":
                print("Exiting. Create "
                      f"{os.path.join('video-input', job.stem + '.txt')} "
                      "and run AfterZone again.")
                continue
            if generate_zones_file(job, chunks, total_frames, fps, zones_path,
                                   video_output_dir, ffprobe_exe, temp_dir):
                zones_written.append(zones_path)
            continue

        print(f"Zones file: {zones_path}")
        zones = parse_zones_file(zones_path, total_frames)
        if not zones:
            print(f"{RED}[AfterZone] No usable zone lines in {zones_path}; "
                  f"skipping.{RESET}")
            continue
        print(f"Parsed {len(zones)} zone(s), video is {total_frames:,} frames "
              f"at {fps:.3f} fps.")

        for zone in zones:
            normalized = zone.encoder.lower().replace("-", "_")
            chunk_encoder = str(chunks[0].get("encoder") or "").lower()
            if chunk_encoder and normalized != chunk_encoder:
                print(f"{YELLOW}[AfterZone] Line {zone.line_number}: zone encoder "
                      f"'{zone.encoder}' does not match the encode's "
                      f"'{chunk_encoder}'. Continuing; parameters are passed through "
                      f"as written.{RESET}")
            if job.is_lqtc and zone.reset:
                print(f"{YELLOW}[AfterZone] Line {zone.line_number}: 'reset' is "
                      f"ignored for an LQTC encode.{RESET}")
                print(f"{YELLOW}            LQTC applies a scene's parameters on "
                      f"top of the global ones itself,{RESET}")
                print(f"{YELLOW}            so there is no per-chunk parameter set "
                      f"for it to replace. The{RESET}")
                print(f"{YELLOW}            line's parameters are used as an "
                      f"override, as they would be without it.{RESET}")

        if job.is_lqtc:
            warn_about_lqtc_zone_params(zones)

        plan = build_plan(chunks, zones)
        if not plan.reencodes:
            print(f"{RED}[AfterZone] No chunk overlaps any zone; nothing to do.{RESET}")
            continue

        print_plan(job, plan, chunks, state["done_sizes"], fps, total_frames)
        print_zone_savings(zones, total_frames, fps, video_output_dir, job.stem,
                           ffprobe_exe, frame_size_cache_path(temp_dir, job.stem))
        print()

        if encode_all:
            # Approval was given once, for every file. Asking again per episode
            # is the thing it was given to avoid, so the plan above is printed
            # and the encode just starts.
            print(f"{GREEN}Encoding this one too - you chose to encode every "
                  f"file.{RESET}")
            print()
        else:
            # Files after this one that will reach this same prompt. Only an
            # estimate: whether their zones actually overlap a chunk is not
            # known until each is planned. It is here so 'encode' is understood
            # to cover them before it is pressed, not after.
            later = len([other for other in jobs[index + 1:]
                         if os.path.exists(os.path.join(video_input_dir,
                                                        f"{other.stem}.txt"))])
            # One keypress commits to every file, so every file's size is shown
            # before it is pressed rather than only this one's.
            print_run_size_estimate(jobs, video_input_dir, video_output_dir,
                                    ffprobe_exe, temp_dir)
            print()
            print(f"  {BOLD}1{RESET} - Encode: re-encode the chunks in the plan "
                  f"above")
            if later:
                print(f"      This also encodes the other {later} file(s) that have "
                      f"a zones file,")
                print("      without asking again for each one.")
            print(f"  {BOLD}2{RESET} - Start over: measure the finished encode again "
                  f"and replace")
            print(f"      {os.path.basename(zones_path)} with a freshly generated "
                  f"zones file.")
            print("      Nothing is re-encoded, and your current zone lines are "
                  "overwritten.")
            print(f"  {BOLD}3{RESET} - Exit without changing anything")
            print()
            answer = ask("Enter 1, 2 or 3: ", {"1", "2", "3"})
            print()
            if answer == "2":
                print(f"Replacing {zones_path} with a freshly generated zones file.")
                print()
                # Same call the missing-zones branch makes, and it ends on its
                # own prompt, so this job is done for this run either way. With
                # batch settings in hand it uses those, exactly as a generated
                # file would.
                if generate_zones_file(job, chunks, total_frames, fps, zones_path,
                                       video_output_dir, ffprobe_exe, temp_dir,
                                       settings=batch):
                    zones_written.append(zones_path)
                continue
            if answer == "3":
                print("Nothing was changed.")
                if later:
                    print(f"The other {later} file(s) with a zones file were left "
                          f"alone too.")
                break
            encode_all = True

        video_params = baseline_video_params(plan)
        encoder_name = job.encoder_name

        if job.is_lqtc:
            # A move, not an edit: LQTC only finds its working folder next to
            # the input, and lqtc-dispatch.py parked it in temp\. Done before
            # the marker so an interruption here leaves an untouched encode that
            # the next run simply plans again.
            stage_lqtc_workdir(job, state)

        # Written before the first edit, not after it: from here on the
        # encoder's working folder is part way through being converted, and the
        # marker is what stops a second run planning it all over again.
        marker_path = marker_path_for(temp_dir, job.stem)
        write_marker(marker_path,
                     build_marker_data(job, plan, zones_path, workers,
                                       worker_source, video_params, total_frames,
                                       MARKER_STAGE_APPLYING,
                                       av1_output=state.get("output_name"),
                                       scene_path=state.get("scene_path")))
        print(f"[AfterZone] Wrote {os.path.join('temp', os.path.basename(marker_path))}")
        print("            While that file exists AfterZone changes nothing: close "
              "this window")
        print(f"            during the encode and the next run just restarts "
              f"{encoder_name},")
        print("            which resumes at the first chunk that is not finished.")
        print()

        if job.is_condor:
            apply_condor_plan(job, plan, state)
            state["planned_entries"] = plan.reencodes
            update_marker(marker_path, stage=MARKER_STAGE_ENCODING)
            av1_path = run_condor(job, state, tools_dir, root_dir)
            final = tag_and_mux(job, av1_path, tools_dir, temp_dir,
                                video_output_dir)
        elif job.is_lqtc:
            apply_lqtc_plan(job, plan, state)
            update_marker(marker_path, stage=MARKER_STAGE_ENCODING)
            av1_path = run_lqtc(job, state, tools_dir)
            final = tag_and_mux(job, av1_path, tools_dir, temp_dir,
                                video_output_dir)
            restore_lqtc_workdir(job, state["scene_path"])
        else:
            apply_plan(job, plan, state["done_data"])
            update_marker(marker_path, stage=MARKER_STAGE_ENCODING)
            av1_path = run_av1an(job, av1an_exe, temp_dir, video_input_dir,
                                 workers, video_params)
            final = tag_and_mux(job, av1_path, tools_dir, temp_dir,
                                video_output_dir)
        remove_marker(marker_path)
        processed.append(final)

        hr()
        print(f"{GREEN}Done: {final}{RESET}")
        print(f"Original encode is untouched at "
              f"{os.path.join(video_output_dir, job.stem + '-output.mkv')}")
        print(f"Replaced chunks are kept in "
              f"{os.path.join(job.hash_dir, 'afterzone-backup')}")
        print("Delete that folder once you are happy with the result.")
        hr()

    if len(zones_written) > 1:
        print()
        print(f"{GREEN}{BOLD}{len(zones_written)} zones files written:{RESET}")
        for path in zones_written:
            print(f"  {path}")
        print()
        print("Review them (each has a -bitrate.png next to it), edit anything you")
        print("want different, then run AfterZone again to re-encode those ranges.")

    if processed:
        print()
        print(f"{GREEN}{BOLD}AfterZone finished.{RESET}")
        for path in processed:
            print(f"  {path}")
    elif _ended_on_prompt:
        # Nothing was re-encoded and the user has already pressed Enter to leave
        # the threshold prompt; tell the .bat not to ask again.
        sys.exit(ALREADY_PAUSED_EXIT)


if __name__ == "__main__":
    main()
