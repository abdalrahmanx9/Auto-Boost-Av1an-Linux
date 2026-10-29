"""Automatic lossless intermediary, for a template.vpy with a rescale in it.

A rescale is the slowest thing a filtering script can hold, and an encode reads
the video more than once: Auto-Boost filters for the fast pass and again for the
final one, Condor filters once per target-quality probe, and a resumed run starts
over. Filtering an episode three times through ArtCNN costs hours that buy
nothing - the filtered frames are identical every time.

So when bat-builder.py has turned the mode on - video-input\\lossless-intermediary.txt
is there - the filter chain runs ONCE into a mathematically lossless x264 file
and everything after that reads the file:

    source.mkv --[template.vpy, rescale and all]--> temp\\lossless\\source-lossless.mkv
    temp\\lossless\\source-lossless.mkv --[passthrough .vpy]--> the AV1 encode

Nothing about this is a step the user takes. prepare() is called by whichever
dispatcher is building the filtering script, before it builds one, and hands
back the intermediary; the dispatcher then renders a passthrough script over it
instead of the template. The source file, the output name, the audio mux, scene
detection and the zones file are all untouched and still the original's.

ONE AT A TIME. These files are around 40 GB for a 24 minute 1080p episode, so
the folder holds one and prepare() deletes the last one before it builds the
next. The folder is under temp, which tools/cleanup.py removes at the end of
every .bat, so the last one goes with it.

REUSE. The intermediary carries a stamp of the template text and the source path
it was built from - the same stamp the rendered .vpy files use. A resumed run
finds it unchanged and skips straight to encoding; an edited template.vpy
changes the stamp and it is rebuilt.

The encode recipe is av1an's, and the same one video-input\\x264.bat uses:
one chunk, one worker, --qp 0. Chunk boundaries have no place in a file that is
meant to be lossless, and one chunk cannot use more than one worker anyway.
Video only, deliberately: a VapourSynth script carries no audio, and the
dispatcher muxes the original's audio onto the finished AV1 encode later.

Consumers:
  * av1an-dispatch.py     (Av1an single pass, and Condor through it)
  * Auto-Boost-Av1an.py   (Auto-Boost two pass)
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import vpy_template

BLUE = "\033[94m"
RED = "\033[91m"
RESET = "\033[0m"

ROOT_DIR = TOOLS_DIR.parent

# Under temp rather than video-input: an encoding .bat encodes everything it
# finds in video-input, and av1an-dispatch.py rescans that folder mid-run for
# files that turned up late, so an intermediary left there would be queued as an
# encode of its own. temp is also what cleanup.py clears at the end of a .bat,
# which is what finally removes the last one.
INTERMEDIARY_DIR_NAME = "lossless"

# Written beside the intermediary. Holds the same stamp the rendered .vpy files
# carry, so an unchanged template and source can reuse the file instead of
# spending another hour rebuilding it.
STAMP_SUFFIX = ".stamp"

# x264 at --qp 0 is mathematically lossless, so the AV1 encode reads exactly the
# frames the template produced. veryfast because this is an intermediary and the
# time saved is the whole point.
X264_PARAMS = "--preset veryfast --qp 0 --output-depth 10"
PIX_FORMAT = "yuv420p10le"


def log(message):
    print(f"[lossless] {message}", flush=True)


def active():
    """True while video-input\\lossless-intermediary.txt is there."""
    return vpy_template.lossless_mode_active()


def intermediary_dir():
    return ROOT_DIR / "temp" / INTERMEDIARY_DIR_NAME


def intermediary_for(source_path):
    stem = Path(str(source_path)).stem
    return intermediary_dir() / f"{stem}{vpy_template.LOSSLESS_SUFFIX}.mkv"


def stamp_path(intermediary):
    return Path(str(intermediary) + STAMP_SUFFIX)


def read_stamp(intermediary):
    try:
        with open(stamp_path(intermediary), "r", encoding="utf-8") as handle:
            return handle.read().strip()
    except OSError:
        return None


def write_stamp(intermediary, stamp):
    try:
        with open(stamp_path(intermediary), "w", encoding="utf-8") as handle:
            handle.write(stamp)
    except OSError:
        # Only costs a rebuild next time; not worth failing an encode over.
        pass


def gigabytes(size):
    return size / (1024 ** 3)


def executables():
    """The three av1an needs for this, or None when one is missing."""
    import shutil
    def _find(binary, portable_subpath):
        local = ROOT_DIR / "tools" / portable_subpath
        if local.is_file():
            return local
        found = shutil.which(binary)
        return Path(found) if found else local

    wanted = {
        "av1an": _find("av1an", "av1an/av1an"),
        "x264": _find("x264", "av1an/x264"),
        "mkvmerge": _find("mkvmerge", "MKVToolNix/mkvmerge"),
    }
    missing = [str(path) for path in wanted.values() if not path.is_file()]
    if missing:
        log(f"{RED}Cannot build a lossless intermediary - part of the toolchain is missing:{RESET}")
        for path in missing:
            log(f"{RED}  {path}{RESET}")
        return None
    return wanted


def prepend_to_path(*directories):
    """Put the portable toolchain first on PATH.

    The .bat files already do this, so normally it changes nothing. It is here
    for a dispatcher started by hand from a plain console, where av1an would
    otherwise not find vspipe, x264 or mkvmerge.
    """
    existing = os.environ.get("PATH", "")
    wanted = [str(d) for d in directories if os.path.isdir(str(d))]
    if not wanted:
        return
    parts = existing.split(os.pathsep) if existing else []
    lowered = {os.path.normcase(part) for part in parts}
    added = [d for d in wanted if os.path.normcase(d) not in lowered]
    if added:
        os.environ["PATH"] = os.pathsep.join(added + parts)


def clear_all_but(keep=None):
    """Delete every intermediary in the folder except one. Returns how many went.

    This is what makes "one at a time" true. Called before a new one is built,
    so the drive never holds two - the previous episode's file goes as soon as
    the next episode's is about to be made, which is after its encode finished.
    """
    folder = intermediary_dir()
    if not folder.is_dir():
        return 0

    removed = 0
    for entry in sorted(folder.iterdir()):
        if not entry.is_file() or entry.suffix.lower() != ".mkv":
            continue
        if keep is not None and os.path.normcase(str(entry)) == os.path.normcase(str(keep)):
            continue
        try:
            size = entry.stat().st_size
        except OSError:
            size = 0
        try:
            entry.unlink()
        except OSError as e:
            log(f"{RED}Could not delete the previous intermediary {entry.name}: {e}{RESET}")
            continue
        try:
            stamp_path(entry).unlink()
        except OSError:
            pass
        log(f"Removed the previous intermediary {entry.name} ({gigabytes(size):.1f} GB).")
        removed += 1
    return removed


def build(source_path, template_text, stamp, output_path, tools):
    """Filter the source through the template into output_path. True on success."""
    work_dir = intermediary_dir()
    work_dir.mkdir(parents=True, exist_ok=True)

    try:
        script_text = vpy_template.render(template_text, str(source_path))
    except vpy_template.TemplateError as e:
        log(f"{RED}template.vpy cannot be used: {e}{RESET}")
        return False

    vpy_file = work_dir / f"{Path(str(source_path)).stem}-lossless.vpy"
    try:
        vpy_template.write_rendered(vpy_file, script_text)
    except OSError as e:
        log(f"{RED}Could not write {vpy_file}: {e}{RESET}")
        return False

    command = [
        str(tools["av1an"]),
        "-i", str(vpy_file),
        "-e", "x264",
        "--no-defaults",
        "--split-method", "none",
        "-x", "0",
        "-w", "1",
        "--pix-format", PIX_FORMAT,
        "-v", X264_PARAMS,
        "-c", "mkvmerge",
        "-y",
        "-o", str(output_path),
    ]

    log(f"{BLUE}Filtering {Path(str(source_path)).name} once into a lossless file.{RESET}")
    log(f"{BLUE}Everything after this reads that file, so the filter chain - the rescale{RESET}")
    log(f"{BLUE}above all - runs once for this video instead of once per pass.{RESET}")
    try:
        code = subprocess.call(command, cwd=str(work_dir))
    except OSError as e:
        log(f"{RED}Could not run av1an: {e}{RESET}")
        return False

    if code != 0:
        log(f"{RED}av1an exited with code {code} while building the intermediary.{RESET}")
        return False
    if not output_path.is_file() or output_path.stat().st_size == 0:
        log(f"{RED}av1an reported success but {output_path.name} is not there.{RESET}")
        return False

    write_stamp(output_path, stamp)
    return True


def prepare(source_path, template_text):
    """The intermediary this source should be encoded from, or None.

    None means encode the source the way it always was: the mode is off, this
    input is already an intermediary, or building one failed - in which case the
    dispatcher filters normally and the run is slow rather than lost.

    The stamp is the template text plus the source path, the same one the
    rendered .vpy files carry, so an edit to template.vpy rebuilds and an
    unchanged one is reused across a resume.
    """
    if not active():
        return None
    if vpy_template.is_lossless_intermediary(source_path):
        # Already one of ours - filtering it again is the mistake this whole
        # mode exists to avoid.
        return None

    output_path = intermediary_for(source_path)
    stamp = vpy_template.render_stamp(template_text, str(source_path))

    if (output_path.is_file() and output_path.stat().st_size > 0
            and read_stamp(output_path) == stamp):
        log(f"Reusing the lossless intermediary {output_path.name} "
            f"({gigabytes(output_path.stat().st_size):.1f} GB) - the template and the "
            f"source are unchanged.")
        clear_all_but(output_path)
        return output_path

    tools = executables()
    if tools is None:
        log(f"{RED}Filtering normally instead. The encode will be slower but correct.{RESET}")
        return None

    prepend_to_path(ROOT_DIR / "VapourSynth", ROOT_DIR / "tools" / "av1an",
                    ROOT_DIR / "tools" / "MKVToolNix")

    # Only one on the drive at a time, and the previous episode's file is the
    # one that goes: its encode is finished, or this run would not have reached
    # the next source.
    clear_all_but(None)

    free = None
    try:
        intermediary_dir().mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(str(intermediary_dir())).free
    except OSError:
        pass
    if free is not None and gigabytes(free) < 90:
        log(f"{RED}Only {gigabytes(free):.0f} GB free. A 24 minute 1080p episode is about{RESET}")
        log(f"{RED}40 GB at --qp 0, and av1an holds the encoded chunk and the finished file{RESET}")
        log(f"{RED}at once, so this may run the drive out.{RESET}")

    if not build(source_path, template_text, stamp, output_path, tools):
        try:
            if output_path.is_file():
                output_path.unlink()
        except OSError:
            pass
        log(f"{RED}Falling back to filtering on every pass for this video. The encode{RESET}")
        log(f"{RED}will be slower but nothing is lost.{RESET}")
        return None

    log(f"{BLUE}Lossless intermediary ready: {output_path.name} "
        f"({gigabytes(output_path.stat().st_size):.1f} GB){RESET}")
    return output_path


def passthrough_render(intermediary):
    """(script text, stamp) for a .vpy that just opens the intermediary.

    Nothing is filtered here. The template already ran, on the way into that
    file; all this does is open it and hand it to the encoder.
    """
    text = vpy_template.lossless_passthrough_text()
    stamp = vpy_template.render_stamp(text, str(intermediary))
    return vpy_template.render(text, str(intermediary), stamp=stamp), stamp
