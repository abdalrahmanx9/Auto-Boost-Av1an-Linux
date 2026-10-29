"""Python backend for x264-filter.bat.

Builds a lossless (or near-lossless) x264 intermediary for every video in the
video-input folder, with the settings.txt filter chain already applied.

Three steps per file, no av1an involved:

  1. tools\\av1an-dispatch.py builds the .vpy from settings.txt.
  2. VapourSynth\\VSPipe.exe pipes that script as y4m into tools\\av1an\\x264.exe,
     which writes a raw <name>.h264 elementary stream into temp\\x264-filter.
  3. tools\\MKVToolNix\\mkvmerge.exe muxes the .h264 into
     <name>-x264filter.mkv, written next to the source file.

The filter chain is not reimplemented here. This script imports
tools\\av1an-dispatch.py and calls its build_vapoursynth_script(), so crop,
HDR tonemap, dehalo, denoise, deband and downscale come out of settings.txt
exactly as they do for the AV1 encodes, and cannot drift from them.

The .mkv is VIDEO ONLY. A VapourSynth script carries nothing but video, so no
audio, subtitles, chapters or attachments are carried over from the source.

Once a source has been filtered, it is moved into the package's originals
folder. The intermediary is written next to it in video-input, so leaving the
source there would queue both of them on the next encode.

settings.txt is only read, never written. The av1an .bat files flip
denoise=True/False in settings.txt through their DENOISE= line before they
dispatch; this script uses whatever settings.txt already says.

Run it through x264-filter.bat, or directly:
    ..\\VapourSynth\\python.exe x264-filter.py
"""

import argparse
import importlib.util
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

BLUE = "\033[94m"
RED = "\033[91m"
RESET = "\033[0m"

# Marks our own outputs. They are written next to the sources, in this same
# folder, so this suffix is what keeps a finished intermediary from being
# picked up and filtered a second time on the next run.
OUTPUT_SUFFIX = "-x264filter"
VIDEO_EXTENSIONS = (".mkv", ".mp4", ".m2ts")
TRUTHY = ("1", "true", "yes", "y", "on")

# Where a source is parked once its intermediary exists, as a folder in the
# package root next to video-input.
ORIGINALS_DIR_NAME = "originals"


def enable_ansi_colors():
    """Turn on VT processing so the escape codes above render as colour.

    Windows consoles start with it off, and print the raw codes without it.
    """
    if os.name != "nt":
        return
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)
    except Exception:
        pass


def log(message):
    print(f"[x264-filter] {message}", flush=True)


def parse_bool(value):
    return str(value).strip().lower() in TRUTHY


def load_dispatch_module(tools_dir):
    """Import tools/av1an-dispatch.py as a module.

    Importing it also runs its ensure_vs_plugin_path(), which points
    VAPOURSYNTH_EXTRA_PLUGIN_PATH at VapourSynth\\vs-plugins. vspipe is started
    as a child process below, so it inherits it.
    """
    dispatch_path = os.path.join(tools_dir, "av1an-dispatch.py")
    if not os.path.isfile(dispatch_path):
        log(f"{RED}ERROR: av1an-dispatch.py not found at: {dispatch_path}{RESET}")
        log(f"{RED}       It is needed to build the .vpy from settings.txt.{RESET}")
        return None
    try:
        spec = importlib.util.spec_from_file_location("av1an_dispatch", dispatch_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    except Exception as e:
        log(f"{RED}ERROR: Could not load av1an-dispatch.py: {e}{RESET}")
        return None


def prepend_to_path(*directories):
    """Put the portable toolchain first on PATH.

    Every executable is called by full path below, but VSPipe still has to find
    the VapourSynth DLLs next to it, so the same three folders the package's
    .bat files prepend are prepended here too.
    """
    existing = os.environ.get("PATH", "")
    wanted = [d for d in directories if os.path.isdir(d)]
    os.environ["PATH"] = os.pathsep.join(wanted + ([existing] if existing else []))


def source_is_vfr(mediainfo_exe, source_path):
    """True when MediaInfo reports the video track as variable framerate."""
    if not os.path.isfile(mediainfo_exe):
        return False
    try:
        result = subprocess.run(
            [mediainfo_exe, "--Inform=Video;%FrameRate_Mode%", source_path],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
        )
    except Exception:
        return False
    return result.returncode == 0 and result.stdout.strip().upper() == "VFR"


def gather_inputs(input_dir):
    files = []
    for entry in sorted(os.listdir(input_dir)):
        path = os.path.join(input_dir, entry)
        if not os.path.isfile(path):
            continue
        if os.path.splitext(entry)[1].lower() in VIDEO_EXTENSIONS:
            files.append(path)
    return files


def move_original(source_path, originals_dir):
    """Move a finished source out of video-input, into <root>\\originals.

    The intermediary lands next to its source, and it is the intermediary that
    should be encoded from here on, so a source left in video-input would be
    queued alongside it by every later .bat. Nothing is ever overwritten: if a
    file of that name is already in originals the source stays where it is and
    is reported, which is the only outcome that cannot lose a file.
    """
    destination = os.path.join(originals_dir, os.path.basename(source_path))
    if os.path.exists(destination):
        log(f"{RED}WARNING: A file of this name is already in {ORIGINALS_DIR_NAME}, so the{RESET}")
        log(f"{RED}         source was left in video-input: {os.path.basename(source_path)}{RESET}")
        return False
    try:
        os.makedirs(originals_dir, exist_ok=True)
        shutil.move(source_path, destination)
    except Exception as e:
        log(f"{RED}WARNING: Could not move the source into {ORIGINALS_DIR_NAME} ({e}).{RESET}")
        log(f"{RED}         It was left in video-input: {os.path.basename(source_path)}{RESET}")
        return False
    return True


def build_vapoursynth_script(dispatch, root_dir, tools_dir, work_dir, source_path, args):
    """Build the .vpy for one source through av1an-dispatch.py.

    The old script is thrown away first. av1an-dispatch.py reuses a cached .vpy
    and only rebuilds it when the tonemap or dehalo/denoise/deband on/off state
    changes, so an edited denoise line, crop value or downscale target would be
    silently ignored on the next run. Building takes about a second, so there is
    nothing to gain by keeping a stale one. The .ffindex cache next to it is
    left alone: indexing is the slow part and it does not depend on settings.
    """
    settings = dispatch.load_script_settings(os.path.join(root_dir, "settings.txt"))

    if not args.reuse_vpy:
        stale_vpy = os.path.join(work_dir, f"{Path(source_path).stem}.vpy")
        if os.path.exists(stale_vpy):
            os.remove(stale_vpy)

    tonemap = False
    if args.tonemap:
        # Only tonemap what is actually HDR, the way the dispatcher decides it.
        metadata = dispatch.detect_color_metadata(
            source_path, os.path.join(tools_dir, "MediaInfo_CLI", "MediaInfo.exe")
        )
        tonemap = bool(metadata and metadata["is_hdr"])
        if args.tonemap and not tonemap:
            log("Source is not HDR; tonemapping is skipped for this file.")

    return dispatch.build_vapoursynth_script(
        source_path,
        work_dir,
        tools_dir,
        settings,
        autocrop=args.autocrop,
        tonemap=tonemap,
    )


def probe_clip(vspipe_exe, vpy_path):
    """Read the output clip's properties with VSPipe --info.

    The frame count lets x264 print a percentage and an ETA instead of a bare
    frame counter, and the frame rate is handed to mkvmerge, which otherwise
    has to guess it from the raw stream. Returns (clip_dict, error_text).
    """
    try:
        result = subprocess.run(
            [vspipe_exe, "--info", vpy_path, "-"],
            capture_output=True, text=True, encoding="utf-8", errors="ignore",
        )
    except Exception as e:
        return None, str(e)
    if result.returncode != 0:
        return None, (result.stderr or result.stdout or "").strip()

    fields = {}
    for line in result.stdout.splitlines():
        key, sep, value = line.partition(":")
        if sep:
            fields[key.strip().lower()] = value.strip()

    def as_int(key):
        try:
            return int(fields.get(key, ""))
        except ValueError:
            return None

    clip = {
        "width": as_int("width"),
        "height": as_int("height"),
        "frames": as_int("frames"),
        "fps_num": None,
        "fps_den": None,
        "format": fields.get("format name", ""),
    }
    # "FPS: 24000/1001 (23.976 fps)", or "FPS: Variable" on a clip with no
    # fixed rate, which VapourSynth also reports as 0/0.
    fps_match = re.search(r"(\d+)\s*/\s*(\d+)", fields.get("fps", ""))
    if fps_match:
        num, den = int(fps_match.group(1)), int(fps_match.group(2))
        if num > 0 and den > 0:
            clip["fps_num"], clip["fps_den"] = num, den
    return clip, None


def encode_with_x264(vspipe_exe, x264_exe, vpy_path, h264_path, x264_params, clip, vspipe_log_path, cwd):
    """Pipe the .vpy through VSPipe into x264 and write a raw .h264 stream.

    VSPipe writes y4m on stdout, x264 reads it on stdin; that is the pipeline
    VSPipe's own help suggests, and it is what av1an did internally for each
    chunk. Doing it directly means one process pair for the whole file instead
    of a chunked run, which is what you want for a single-worker lossless pass.

    x264's progress goes to the console. VSPipe's stderr is written to a log
    file instead, so a VapourSynth error is kept but does not fight x264 for
    the same line of the terminal; the tail of it is printed if anything fails.
    """
    if os.path.exists(h264_path):
        os.remove(h264_path)

    cmd = [x264_exe, "--demuxer", "y4m"]
    cmd.extend(shlex.split(x264_params))
    # Without a frame count x264 cannot show a percentage, and y4m carries none.
    # Skipped when the parameters already deal in frame ranges themselves.
    if clip.get("frames") and not any(p in x264_params for p in ("--frames", "--seek")):
        cmd.extend(["--frames", str(clip["frames"])])
    cmd.extend(["--output", h264_path, "-"])

    def discard_partial_stream():
        if os.path.exists(h264_path):
            os.remove(h264_path)

    vspipe_proc = None
    x264_proc = None
    with open(vspipe_log_path, "w", encoding="utf-8", errors="replace") as vspipe_log:
        try:
            vspipe_proc = subprocess.Popen(
                [vspipe_exe, "--container", "y4m", vpy_path, "-"],
                stdout=subprocess.PIPE, stderr=vspipe_log, cwd=cwd,
            )
            x264_proc = subprocess.Popen(cmd, stdin=vspipe_proc.stdout, cwd=cwd)
            # The parent has to let go of the read end, or x264 would never see
            # end-of-stream when VSPipe finishes.
            vspipe_proc.stdout.close()
            x264_code = x264_proc.wait()
            try:
                vspipe_code = vspipe_proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                # x264 is gone and VSPipe is still holding on; nothing is
                # reading its output any more, so end it.
                vspipe_proc.kill()
                vspipe_code = vspipe_proc.wait()
        except KeyboardInterrupt:
            for proc in (x264_proc, vspipe_proc):
                if proc and proc.poll() is None:
                    proc.kill()
            discard_partial_stream()
            raise
        except Exception as e:
            for proc in (x264_proc, vspipe_proc):
                if proc and proc.poll() is None:
                    proc.kill()
            log(f"{RED}ERROR: Could not start the VSPipe -> x264 pipeline: {e}{RESET}")
            discard_partial_stream()
            return False

    if vspipe_code != 0:
        # VSPipe failing is the dangerous one: x264 sees a stream that simply
        # stops and can still exit 0 after encoding a truncated file.
        log(f"{RED}ERROR: VSPipe exited with code {vspipe_code}. The VapourSynth script failed.{RESET}")
        print_log_tail(vspipe_log_path)
        discard_partial_stream()
        return False
    if x264_code != 0:
        log(f"{RED}ERROR: x264 exited with code {x264_code}.{RESET}")
        print_log_tail(vspipe_log_path)
        discard_partial_stream()
        return False
    if not os.path.exists(h264_path) or os.path.getsize(h264_path) == 0:
        log(f"{RED}ERROR: x264 reported success but wrote no data: {h264_path}{RESET}")
        discard_partial_stream()
        return False
    return True


def print_log_tail(log_path, lines=15):
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            tail = f.read().splitlines()[-lines:]
    except Exception:
        return
    if not tail:
        return
    log(f"Last {len(tail)} line(s) of {log_path}:")
    for line in tail:
        print(f"    {line}")


def mux_to_mkv(mkvmerge_exe, h264_path, output_path, clip, cwd):
    """Wrap the raw .h264 in Matroska.

    A raw elementary stream carries no container timing, so the frame rate
    VSPipe reported is passed in explicitly. mkvmerge would otherwise fall back
    to whatever it can read out of the bitstream, and to 25 fps if it finds
    nothing.
    """
    if os.path.exists(output_path):
        os.remove(output_path)

    cmd = [mkvmerge_exe, "-o", output_path]
    if clip.get("fps_num") and clip.get("fps_den"):
        # Input option, so it has to come before the file it applies to.
        # Track 0 is the only track in a raw stream.
        cmd.extend(["--default-duration", f"0:{clip['fps_num']}/{clip['fps_den']}fps"])
    else:
        log(f"{RED}WARNING: VSPipe reported no fixed frame rate; mkvmerge will guess one.{RESET}")
    cmd.append(h264_path)

    def discard_partial_output():
        if os.path.exists(output_path):
            os.remove(output_path)
            log("Removed the incomplete output file.")

    process = None
    try:
        # mkvmerge only redraws its progress in place when it is talking to a
        # terminal; through a pipe it emits one "Progress: 42%" line per step.
        # Those are caught here and rewritten onto a single line, the same way
        # tools/av1an-mux.py shows it, so the copy does not look frozen. Warnings
        # and errors are passed through untouched.
        process = subprocess.Popen(
            cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
        )
        print("    Muxing: starting...", end="\r", flush=True)
        last_was_progress = False
        for line in process.stdout:
            line = line.strip()
            if not line:
                continue
            if line.startswith("Progress:"):
                percent = line.split(":", 1)[1].strip()
                print(f"    Muxing: {percent}          ", end="\r", flush=True)
                last_was_progress = True
            else:
                if last_was_progress:
                    print()
                    last_was_progress = False
                print(f"    {line}", flush=True)
        if last_was_progress:
            print("    Muxing: done.          ", flush=True)
        # mkvmerge returns 1 for warnings and 2 for real errors.
        code = process.wait()
    except KeyboardInterrupt:
        if process and process.poll() is None:
            process.kill()
        discard_partial_output()
        raise
    except Exception as e:
        if process and process.poll() is None:
            process.kill()
        log(f"{RED}ERROR: Could not run mkvmerge: {e}{RESET}")
        discard_partial_output()
        return False
    if code > 1:
        log(f"{RED}ERROR: mkvmerge exited with code {code}.{RESET}")
        discard_partial_output()
        return False
    if not os.path.exists(output_path):
        log(f"{RED}ERROR: mkvmerge reported success but produced no file: {output_path}{RESET}")
        return False
    return True


def process_file(source_path, context, args):
    dispatch = context["dispatch"]
    basename = Path(source_path).stem
    # Next to the source, which for this script is always this folder.
    output_path = os.path.join(os.path.dirname(source_path), f"{basename}{OUTPUT_SUFFIX}.mkv")
    h264_path = os.path.join(context["work_dir"], f"{basename}{OUTPUT_SUFFIX}.h264")

    if OUTPUT_SUFFIX in basename:
        log(f"Skipping (this is an x264-filter output): {basename}")
        return None
    if os.path.exists(output_path):
        log(f"Skipping (output already exists): {basename}")
        return None

    print("\n" + "-" * 80)
    print(f"Processing: {basename}")
    print("-" * 80, flush=True)

    # VapourSynth output is constant framerate, and nothing here restores the
    # source's timestamps the way tools/av1an-mux.py does, so a VFR source ends
    # up on a timeline that no longer matches its own audio.
    if source_is_vfr(context["mediainfo_exe"], source_path):
        log(f"{RED}WARNING: MediaInfo reports this source as variable framerate.{RESET}")
        log(f"{RED}         The intermediary will be constant framerate, so it will not line{RESET}")
        log(f"{RED}         up with the source's audio. Encode this file with the normal{RESET}")
        log(f"{RED}         av1an .bat files instead: av1an-mux.py restores VFR timestamps.{RESET}")

    log("Building the VapourSynth script from settings.txt...")
    try:
        vpy_path = build_vapoursynth_script(
            dispatch, context["root_dir"], context["tools_dir"], context["work_dir"], source_path, args
        )
    except Exception as e:
        log(f"{RED}ERROR: Could not build the VapourSynth script: {e}{RESET}")
        return False
    if not vpy_path or not os.path.exists(vpy_path):
        log(f"{RED}ERROR: No VapourSynth script was produced for: {basename}{RESET}")
        return False

    started_at = time.monotonic()

    log("Reading the filtered clip's properties with VSPipe...")
    clip, probe_error = probe_clip(context["vspipe_exe"], vpy_path)
    if clip is None:
        log(f"{RED}ERROR: VSPipe could not open the script. VapourSynth said:{RESET}")
        for line in (probe_error or "").splitlines()[-15:]:
            print(f"    {line}")
        return False
    log("Filtered clip: {}x{}, {} frames, {} fps, {}".format(
        clip["width"] or "?", clip["height"] or "?", clip["frames"] or "?",
        f"{clip['fps_num']}/{clip['fps_den']}" if clip["fps_num"] else "variable",
        clip["format"] or "?",
    ))

    log("Encoding: VSPipe -> x264 -> .h264")
    if not encode_with_x264(
        context["vspipe_exe"], context["x264_exe"], vpy_path, h264_path,
        args.x264_params, clip, os.path.join(context["work_dir"], "logs", f"{basename}-vspipe.log"),
        context["input_dir"],
    ):
        return False

    log("Muxing the .h264 into Matroska with mkvmerge...")
    if not mux_to_mkv(context["mkvmerge_exe"], h264_path, output_path, clip, context["input_dir"]):
        return False

    # The raw stream is as large as the .mkv and is fully contained in it now.
    try:
        os.remove(h264_path)
    except OSError as e:
        log(f"Could not delete the intermediate .h264 ({e}): {h264_path}")

    # Only once the .mkv is on disk and the raw stream is gone, so an
    # interrupted or failed file always keeps its source in video-input.
    if args.move_originals and move_original(source_path, context["originals_dir"]):
        context["moved_originals"].append(os.path.basename(source_path))
        log(f"Source moved to {ORIGINALS_DIR_NAME}: {os.path.basename(source_path)}")

    elapsed = time.monotonic() - started_at
    log(f"Done in {dispatch.format_elapsed_hhmmss(elapsed)}: {output_path}")
    return True


def main():
    enable_ansi_colors()

    parser = argparse.ArgumentParser(
        description="Build a filtered lossless x264 intermediary for every video in video-input."
    )
    parser.add_argument(
        "--x264-params", default="--preset veryfast --qp 0 --output-depth 10",
        help="Parameters passed to x264. --qp 0 is lossless.",
    )
    parser.add_argument(
        "--autocrop", default="False",
        help="Apply the [crop] section of settings.txt (True/False).",
    )
    parser.add_argument(
        "--tonemap", default="False",
        help="Tonemap HDR sources to SDR BT.709 via libplacebo (True/False).",
    )
    parser.add_argument(
        "--reuse-vpy", default="False",
        help="Keep an existing .vpy instead of rebuilding it from settings.txt "
             "(True/False). Only worth it with --autocrop True, where a rebuild "
             "re-runs cropdetect.py; it will then ignore later settings.txt edits.",
    )
    parser.add_argument(
        "--move-originals", default="True",
        help=f"Move each source into <root>\\{ORIGINALS_DIR_NAME} once its "
             "intermediary is written, so only the intermediaries are left in "
             "video-input (True/False).",
    )
    args = parser.parse_args()
    args.autocrop = parse_bool(args.autocrop)
    args.tonemap = parse_bool(args.tonemap)
    args.reuse_vpy = parse_bool(args.reuse_vpy)
    args.move_originals = parse_bool(args.move_originals)

    # This ships in tools\, but an older layout kept it in video-input next to
    # the sources. The package root is the parent folder either way.
    root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    tools_dir = os.path.join(root_dir, "tools")
    input_dir = os.path.join(root_dir, "video-input")
    originals_dir = os.path.join(root_dir, ORIGINALS_DIR_NAME)

    if not os.path.isdir(input_dir):
        log(f"{RED}ERROR: No video-input folder at: {input_dir}{RESET}")
        log(f"{RED}       Run this from the portable package's tools folder.{RESET}")
        return 1

    # Scratch area for the .vpy scripts, their ffms2 .ffindex caches and the raw
    # .h264 each encode produces. The .vpy is rebuilt from settings.txt on every
    # run (see build_vapoursynth_script above); the .ffindex is reused; the
    # .h264 is deleted once mkvmerge has wrapped it. Kept separate from the temp
    # folder the AV1 pipeline uses so the two never reuse each other's scripts.
    work_dir = os.path.join(root_dir, "temp", "x264-filter")

    import shutil as _sh
    def _resolve(binary, local_path):
        return local_path if os.path.isfile(local_path) else (_sh.which(binary) or local_path)

    executables = {
        "vspipe_exe": _resolve("vspipe", os.path.join(tools_dir, "VSPipe")),
        "x264_exe": _resolve("x264", os.path.join(tools_dir, "av1an", "x264")),
        "mkvmerge_exe": _resolve("mkvmerge", os.path.join(tools_dir, "MKVToolNix", "mkvmerge")),
    }
    for path in executables.values():
        if not os.path.isfile(path):
            log(f"{RED}ERROR: Not found: {path}{RESET}")
            log(f"{RED}       Your install may be incomplete - try re-downloading the package.{RESET}")
            return 1

    dispatch = load_dispatch_module(tools_dir)
    if dispatch is None:
        return 1

    prepend_to_path(
        os.path.join(root_dir, "VapourSynth"),
        os.path.join(tools_dir, "av1an"),
        os.path.join(tools_dir, "MKVToolNix"),
    )

    os.makedirs(os.path.join(work_dir, "logs"), exist_ok=True)

    context = {
        "dispatch": dispatch,
        "root_dir": root_dir,
        "tools_dir": tools_dir,
        "input_dir": input_dir,
        "work_dir": work_dir,
        "originals_dir": originals_dir,
        "moved_originals": [],
        "mediainfo_exe": _resolve("mediainfo", os.path.join(tools_dir, "MediaInfo_CLI", "MediaInfo")),
    }
    context.update(executables)

    print("=" * 80)
    print("x264-filter: lossless x264 intermediary using the settings.txt filter chain")
    print("=" * 80)
    print(f"Input folder:  {input_dir}")
    print(f"Output:        <name>{OUTPUT_SUFFIX}.mkv, written into that same folder")
    print(f"Pipeline:      VSPipe -> x264 (.h264) -> mkvmerge (.mkv)")
    print(f"x264 params:   {args.x264_params}")
    print(f"Autocrop: {args.autocrop}    Tonemap: {args.tonemap}    Move originals: {args.move_originals}")
    print("Output is video only: a VapourSynth script carries no audio.")
    print("")
    print(f"{RED}These files are LARGE. --qp 0 is mathematically lossless, so a 24 minute{RESET}")
    print(f"{RED}1080p anime episode can land around 40 GB, and a film proportionally more.{RESET}")
    print(f"{RED}The raw .h264 in temp\\x264-filter exists alongside the finished .mkv while{RESET}")
    print(f"{RED}mkvmerge runs, so keep roughly double the output size free.{RESET}")

    encoded = 0
    failed = 0
    queued = 0
    batch_started_at = time.monotonic()
    for source_path in gather_inputs(input_dir):
        result = process_file(source_path, context, args)
        if result is None:
            continue
        queued += 1
        if result:
            encoded += 1
        else:
            failed += 1

    print("\n" + "=" * 80)
    if queued == 0:
        print("x264-filter: nothing to do. No new .mkv / .mp4 / .m2ts files in video-input.")
    else:
        print(f"x264-filter: {encoded} encoded, {failed} failed, out of {queued} queued.")
        print(f"Total time: {dispatch.format_elapsed_hhmmss(time.monotonic() - batch_started_at)}")
    if encoded:
        print(f"Output folder: {input_dir}")
    if context["moved_originals"]:
        print("")
        print(f"{BLUE}The original input files have been moved to:{RESET}")
        print(f"{BLUE}    {originals_dir}{RESET}")
        print("")
        print(f"{BLUE}video-input now holds only the {OUTPUT_SUFFIX}.mkv intermediaries, which are{RESET}")
        print(f"{BLUE}already filtered and are what your LQTC .bat should encode.{RESET}")
    print("=" * 80)
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n[x264-filter] Interrupted.")
        sys.exit(130)
