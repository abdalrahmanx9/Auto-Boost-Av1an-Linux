# --- VapourSynth R79 plugin-path bootstrap ---
# R72 autoloaded VapourSynth\vs-plugins from the portable.vs marker. R78+ dropped
# that and reads VAPOURSYNTH_EXTRA_PLUGIN_PATH instead, so without this the
# plugins kept in vs-plugins (wwxd, fmtconv, ...) are invisible and scripts die
# with "There is no attribute or namespace named <plugin>".
# Must run before vapoursynth/vstools pull in a core. Child processes inherit it.
import os as _bootstrap_os

_PLUGIN_ENV_VAR = "VAPOURSYNTH_EXTRA_PLUGIN_PATH"


def _ensure_vs_plugin_path():
    root_dir = _bootstrap_os.path.dirname(_bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__)))
    found = _bootstrap_os.path.join(root_dir, "VapourSynth", "vs-plugins")
    if not _bootstrap_os.path.isdir(found):
        return ""

    existing = [part for part in _bootstrap_os.environ.get(_PLUGIN_ENV_VAR, "").split(_bootstrap_os.pathsep) if part]
    if not existing:
        # Set a single path: valid whether the core reads one path or a list.
        _bootstrap_os.environ[_PLUGIN_ENV_VAR] = found
    elif not any(_bootstrap_os.path.normcase(part) == _bootstrap_os.path.normcase(found) for part in existing):
        _bootstrap_os.environ[_PLUGIN_ENV_VAR] = _bootstrap_os.pathsep.join(existing + [found])

    return found


_ensure_vs_plugin_path()
# --- end bootstrap ---

import os
import re
import sys
import json
import glob
import psutil
import subprocess
import time
import shutil

DISPLAY_USERNAME = "av1enjoyer"
_WINDOWS_USER_PATH_RE = re.compile(
    r"([\\/]+users[\\/]+)[A-Za-z0-9._-]+",
    re.IGNORECASE,
)


def anonymize_user_paths(text):
    """Hide the real Windows profile name in user-facing console output."""
    if not isinstance(text, str):
        return text
    return _WINDOWS_USER_PATH_RE.sub(
        lambda match: f"{match.group(1)}{DISPLAY_USERNAME}",
        text,
    )


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

# --- CONFIGURATION ---
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS_DIR = os.path.join(BASE_DIR, "tools")
# Prefer the portable av1an.exe in tools\av1an; fall back to "av1an" on PATH.
# (The generated .bat prepends tools\av1an to PATH anyway, but this keeps the
# portable copy first even when workercount.py is launched standalone.)
_PORTABLE_AV1AN = os.path.join(TOOLS_DIR, "av1an", "av1an")
AV1AN_PATH = _PORTABLE_AV1AN if os.path.exists(_PORTABLE_AV1AN) else "av1an"
MKVMERGE_EXE = shutil.which("mkvmerge") or os.path.join(TOOLS_DIR, "MKVToolNix", "mkvmerge")
VIDEO_INPUT_DIR = os.path.join(BASE_DIR, "video-input")
SAMPLE_FILE = os.path.join(TOOLS_DIR, "sample.mkv")                  # legacy fallback
BENCH_SAMPLE_FILE = os.path.join(TOOLS_DIR, "benchmark-sample.mkv")  # created from video-input
CONFIG_FILE = os.path.join(TOOLS_DIR, "workercount-config.txt")
BENCH_TEMP_DIR = os.path.join(TOOLS_DIR, "workercount_bench_temp")
TEST_OUTPUT_FILE = os.path.join(BASE_DIR, "workercount-test-output.mkv")

# Encoder params used for the DEFAULT (non-optimized) test
ENCODER_PARAMS = " --preset 4 --crf 30 --lp 3"

# --- TUNING ---
# The benchmark finds the worker-count SWEET SPOT by measuring REAL encode
# throughput (frames per second actually written by the encoder) at several
# worker counts and keeping the fastest. This is the only reliable signal on
# power/thermally limited CPUs (especially laptops), where adding workers
# collapses clock speeds: CPU usage still reads 100%, but fps drops hard.
RAM_HEADROOM = 0.90              # Candidates that push RAM past 90% are rejected
RAM_ABORT_PERCENT = 90.0         # LIVE kill-switch: system RAM use at/above this
RAM_ABORT_MIN_AVAILABLE_MB = 2048  # ...or less than this much free -> the test is
                                 # killed IMMEDIATELY (checked every 0.25s, armed
                                 # from process start) so the benchmark can never
                                 # starve the rest of the system of memory.
RAM_GUARD_INTERVAL = 0.25        # Seconds between RAM safety checks
FIRST_FRAME_TIMEOUT = 600        # Max seconds to wait for the FIRST encoded frame
                                 # AFTER scene detection is done (source indexing
                                 # and lookahead fill can take minutes at
                                 # preset <= 2). Scene detection itself is waited
                                 # out separately (see SCENE_DETECT_TIMEOUT).
SCENE_DETECT_TIMEOUT = 3600      # Max seconds to wait for av1an's ONE-TIME scene
                                 # detection pass. The benchmark clock does NOT
                                 # run during this phase: fps measurement only
                                 # starts once av1an reports scene detection is
                                 # done and saves the scenes file to the temp
                                 # folder, and the SVT-AV1 encode begins.
FPS_SETTLE_DELTA = 0.5           # Settled = av1an's fps readings stay within this
FPS_SETTLE_SPAN = 30.0           # ...range over this many seconds -> stop the test
FPS_MAX_MEASURE_SECONDS = 180    # Never watch a candidate longer than this after
                                 # its first fps reading (median of the last
                                 # readings is used if it never settles)
COOLDOWN_BETWEEN_TESTS = 5       # Idle seconds between candidates (clock recovery)
MAX_CANDIDATE_SECONDS = 900      # Hard safety timeout per candidate
POLL_INTERVAL = 1.0              # Seconds between frame-count/RAM polls
SEARCH_BUDGET = 6                # Max worker counts tested (typical runs use 3-4)
TIE_MARGIN = 0.03                # Within 3% fps counts as a tie -> prefer FEWER workers
SAMPLE_MAX_AGE_SECONDS = 6 * 3600  # Reuse an existing benchmark sample this recent

FILTER_SETTING_KEYS = ("downscale", "denoise", "deband")
VIDEO_EXTENSIONS = (".mkv", ".mp4", ".m2ts")

# Regex for `set "key=value"` (or bare key=value) lines inside a .bat file
BAT_SET_RE = re.compile(r'^(?:set\s+)?"?([A-Za-z_][A-Za-z0-9_-]*)\s*=\s*(.*?)"?\s*$', re.IGNORECASE)


# ---------------------------------------------------------------------------
# .bat FILE HELPERS (optimize-workers support)
# ---------------------------------------------------------------------------

def find_active_bat_file():
    """Locate the .bat that launched this run via the tools/bat-used-*.txt marker."""
    markers = glob.glob(os.path.join(TOOLS_DIR, "bat-used-*.txt"))
    markers.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    for marker in markers:
        name = os.path.basename(marker)
        if not (name.startswith("bat-used-") and name.endswith(".txt")):
            continue
        bat_name = name[len("bat-used-"):-len(".txt")]
        bat_path = os.path.join(BASE_DIR, bat_name)
        if os.path.isfile(bat_path):
            return bat_path
    return None


def parse_bat_settings(bat_path):
    """Parse `set "key=value"` (and bare key=value) lines from a .bat into a dict."""
    settings = {}
    try:
        with open(bat_path, "r", encoding="utf-8", errors="replace") as f:
            for raw in f.read().splitlines():
                line = raw.strip()
                if not line or line.startswith("::") or line.startswith("#") or line.lower().startswith("rem "):
                    continue
                m = BAT_SET_RE.match(line)
                if m:
                    settings[m.group(1).lower()] = m.group(2).strip()
                elif "=" in line and " " not in line.split("=", 1)[0] and "%" not in line.split("=", 1)[0]:
                    k, v = line.split("=", 1)
                    settings.setdefault(k.strip().lower(), v.strip())
    except Exception as e:
        print(f"[Optimize] Warning: Could not parse {bat_path}: {e}", file=sys.stderr)
    return settings


def set_bat_value(bat_path, key, value):
    """Write key=value into the generated script.

    bash reads the whole file fresh at every line, so unlike the Windows
    byte-offset .bat edit there is no padding to preserve: the matching
    KEY= line is simply rewritten. Generated .sh scripts spell the custom
    keys with underscores (bash identifiers), so both spellings are tried.
    """
    value = str(value)
    variants = [key.lower()]
    if "-" in key:
        variants.append(key.lower().replace("-", "_"))
    elif "_" in key:
        variants.append(key.lower().replace("_", "-"))
    try:
        with open(bat_path, "r", encoding="utf-8", errors="replace", newline="") as f:
            content = f.read()
    except Exception as e:
        print(f"[Optimize] Error reading {bat_path}: {e}", file=sys.stderr)
        return False

    lines = content.splitlines(keepends=True)
    for idx, raw in enumerate(lines):
        body = raw.rstrip("\r\n")
        ending = raw[len(body):]
        stripped = body.strip()
        lead = body[:len(body) - len(body.lstrip())]
        m = BAT_SET_RE.match(stripped)
        hit = None
        if m and m.group(1).lower() in variants:
            hit = m.group(1)
        elif "=" in stripped and " " not in stripped.split("=", 1)[0] and stripped.split("=", 1)[0].strip().lower() in variants:
            hit = stripped.split("=", 1)[0].strip()
        if hit is None:
            continue
        new_core = lead + f'{hit}="{value}"'
        lines[idx] = new_body = new_core + ending
        try:
            with open(bat_path, "w", encoding="utf-8", errors="replace", newline="") as f:
                f.write("".join(lines))
            return True
        except Exception as e:
            print(f"[Optimize] Error writing {bat_path}: {e}", file=sys.stderr)
            return False

    print(f"[Optimize] Warning: {key} line not found in {os.path.basename(bat_path)}.", file=sys.stderr)
    return False


def bat_runs_ssimu2_optimize(bat_path):
    """True when the launching .bat will also run ssimu2-workercount.py later,
    meaning the benchmark sample must be left in place for it."""
    try:
        with open(bat_path, "r", encoding="utf-8", errors="replace") as f:
            return "ssimu2-workercount.py" in f.read()
    except Exception:
        return False


# ---------------------------------------------------------------------------
# settings.txt HELPERS (filtering awareness)
# ---------------------------------------------------------------------------

def read_settings_values():
    settings_paths = [os.path.join(BASE_DIR, "settings.txt"), os.path.join(TOOLS_DIR, "settings.txt")]
    values = {}
    for settings_path in settings_paths:
        if not os.path.exists(settings_path):
            continue
        try:
            with open(settings_path, "r", encoding="utf-8", errors="replace") as f:
                for raw_line in f.read().splitlines():
                    line = raw_line.strip()
                    if not line or line.startswith(("#", ";", "[")) or "=" not in line:
                        continue
                    key, value = line.split("=", 1)
                    values[key.strip().lower()] = value.strip()
        except Exception:
            pass
    return values


def settings_filters_enabled(values=None):
    if values is None:
        values = read_settings_values()
    return any(values.get(key, "false").lower() == "true" for key in FILTER_SETTING_KEYS)


def build_filtered_vpy(values, source_path, tonemap=False):
    """Build a temporary VapourSynth script mirroring the real dispatch/Auto-Boost
    pipeline (initialize_clip -> tonemap -> denoise/deband -> downscale ->
    finalize_clip) so the benchmark includes the cost of the user's filtering.
    When the launching .bat has tonemap=True, the libplacebo HDR->SDR tonemap
    is included so the benchmark measures the true CPU+GPU load. Crop is left
    off for the benchmark; that only makes the result slightly conservative."""
    os.makedirs(BENCH_TEMP_DIR, exist_ok=True)
    vpy_path = os.path.join(BENCH_TEMP_DIR, "bench_source.vpy")
    cache_path = os.path.join(BENCH_TEMP_DIR, "bench_source.ffindex")

    # The index is keyed to the exact bytes of the sample it was built from, so
    # a leftover one from an earlier run (the end-of-run rmtree does not happen
    # if the benchmark is interrupted) makes ffms2.Source fail outright with
    # "The index does not match the source file" as soon as the sample is recut.
    # It costs seconds to rebuild, so always start from a fresh one.
    try:
        os.remove(cache_path)
    except OSError:
        pass

    do_downscale = values.get("downscale", "false").lower() == "true"
    target_res = values.get("target_resolution", "1920x1080")
    kernel = values.get("kernel_type", "Hermite")
    do_denoise = values.get("denoise", "false").lower() == "true"
    denoise_setting = values.get("denoise_setting", "")
    do_deband = values.get("deband", "false").lower() == "true"
    deband_setting = values.get("deband_setting", "")

    denoise_line = denoise_setting if do_denoise and denoise_setting else ""
    deband_line = deband_setting if do_deband and deband_setting else ""

    vpy_template = """
from vstools import vs, core, initialize_clip, finalize_clip
try:
    from vsdenoise import DFTTest
except Exception:
    DFTTest = None
core.max_cache_size = 1024

# Load Source
src = core.ffms2.Source(source=r"{source}", cachefile=r"{cache}")

# Initialize (Fixes Placebo bitdepth error by ensuring 16-bit)
src = initialize_clip(src)

# Tonemap HDR -> SDR (libplacebo; libvs_placebo.dll autoloads from the plugins directory)
do_tonemap = {tonemap}
if do_tonemap:
    src = core.fmtc.bitdepth(src, bits=16)
    src = core.placebo.Tonemap(src, src_csp=1, dst_csp=0, dynamic_peak_detection=1, gamut_mapping=1, tone_mapping_function=1, metadata=0, contrast_recovery=0.0, smoothing_period=20.0, percentile=100.0)
    # Tonemap returns RGB or 4:4:4 output; SVT-AV1 only supports 4:2:0, so convert
    # back to YUV420P16 here. finalize_clip then produces the same 10-bit
    # YUV420P10 output as the non-tonemap pipeline.
    if src.format.color_family == vs.RGB:
        src = src.resize.Bicubic(format=vs.YUV420P16, matrix_s="709")
    elif src.format.id != vs.YUV420P16:
        src = src.resize.Bicubic(format=vs.YUV420P16)
    src = src.std.SetFrameProps(_Matrix=1, _Transfer=1, _Primaries=1)

# Optional settings.txt denoise/deband hooks
{denoise_line}
{deband_line}

# DOWNSCALE
should_downscale = {downscale}
target_res_str = "{target_res}"
user_kernel = "{kernel}"

if should_downscale:
    k_map = {{
        "hermite": "hermite",
        "bilinear": "triangle",
        "bicubic": "catmull_rom",
        "gaussian": "gaussian",
        "catmull_rom": "catmull_rom",
        "mitchell": "mitchell",
        "lanczos": "lanczos",
        "spline36": "spline36"
    }}
    pl_filter = k_map.get(user_kernel.lower(), "spline36")
    target_w = 0
    target_h = 0
    if "x" in target_res_str.lower():
        try:
            w_str, h_str = target_res_str.lower().split("x")
            target_w = int(w_str)
            target_h = int(h_str)
        except Exception:
            pass
    else:
        try:
            target_w = int(target_res_str)
        except Exception:
            pass
    if target_w > 0:
        if target_h == 0:
            target_h = int(target_w * src.height / src.width)
            if target_h % 2 != 0:
                target_h -= 1
        if target_w % 2 != 0:
            target_w -= 1
        if target_w < src.width or target_h < src.height:
            src = core.placebo.Resample(src, target_w, target_h, filter=pl_filter)

# Finalize (Sets 10-bit output)
final = finalize_clip(src)
final.set_output(0)
"""
    with open(vpy_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(vpy_template.format(
            source=source_path,
            cache=cache_path,
            tonemap=str(bool(tonemap)),
            denoise_line=denoise_line,
            deband_line=deband_line,
            downscale=str(do_downscale),
            target_res=target_res,
            kernel=kernel,
        ))
    return vpy_path


# ---------------------------------------------------------------------------
# BENCHMARK SAMPLE CREATION (mirrors extras/create-sample.bat)
# ---------------------------------------------------------------------------

def find_first_input_video():
    """First real source file in video-input (skips encode artifacts)."""
    if not os.path.isdir(VIDEO_INPUT_DIR):
        return None
    for name in sorted(os.listdir(VIDEO_INPUT_DIR)):
        path = os.path.join(VIDEO_INPUT_DIR, name)
        if not os.path.isfile(path):
            continue
        stem, ext = os.path.splitext(name)
        if ext.lower() not in VIDEO_EXTENSIONS:
            continue
        if stem.lower().endswith(("-av1", "-output")):
            continue
        return path
    return None


def delete_benchmark_sample():
    for pattern in ("benchmark-sample*.mkv", "benchmark-sample.mkv.*"):
        for path in glob.glob(os.path.join(TOOLS_DIR, pattern)):
            try:
                os.remove(path)
                print(f"[Sample] Deleted {os.path.basename(path)}", file=sys.stderr)
            except OSError:
                pass


def _sample_source_signature(source):
    try:
        st = os.stat(source)
        return f"{os.path.basename(source)}|{st.st_size}|{int(st.st_mtime)}"
    except OSError:
        return None


def ensure_benchmark_sample(force=False):
    """Create tools/benchmark-sample.mkv: a 90 second, audio-free cut of the
    first video in video-input, made with mkvmerge exactly like
    extras/create-sample.bat (--no-audio --split parts:00:03:00-00:04:30).
    Falls back to the first 90 seconds for short sources. Reuses a recent
    sample so workercount.py and ssimu2-workercount.py share one file.
    Returns the sample path, or None if it could not be created."""
    source = find_first_input_video()
    sidecar = BENCH_SAMPLE_FILE + ".source.txt"

    if not force and os.path.exists(BENCH_SAMPLE_FILE):
        try:
            fresh = time.time() - os.path.getmtime(BENCH_SAMPLE_FILE) < SAMPLE_MAX_AGE_SECONDS
            recorded = open(sidecar, encoding="utf-8").read().strip() if os.path.exists(sidecar) else None
            # Reuse ONLY when the sample provably came from the CURRENT first
            # source file - if video-input changed, a fresh cut is made so the
            # benchmark always measures the exact content this run will encode.
            if fresh and source and recorded and recorded == _sample_source_signature(source):
                print(f"[Sample] Reusing benchmark sample (same source: "
                      f"{os.path.basename(source)}).", file=sys.stderr)
                return BENCH_SAMPLE_FILE
            if fresh and recorded and not source:
                # video-input is empty now; the recent sample is still the
                # best available representative content.
                print(f"[Sample] Reusing recent benchmark sample: "
                      f"{os.path.basename(BENCH_SAMPLE_FILE)}", file=sys.stderr)
                return BENCH_SAMPLE_FILE
        except OSError:
            pass
    if not source:
        print("[Sample] No source video found in video-input.", file=sys.stderr)
        return None

    mkvmerge = MKVMERGE_EXE if os.path.exists(MKVMERGE_EXE) else "mkvmerge"

    # Clear any previous sample and stale split parts
    for path in glob.glob(os.path.join(TOOLS_DIR, "benchmark-sample*.mkv")):
        try:
            os.remove(path)
        except OSError:
            pass

    # Prefer 3:00-4:30 (skips intros); fall back to 0:00-1:30 for short sources,
    # where mkvmerge produces no output file at all.
    for time_range in ("00:03:00-00:04:30", "00:00:00-00:01:30"):
        cmd = [mkvmerge, "-o", BENCH_SAMPLE_FILE, "--no-audio",
               "--split", f"parts:{time_range}", source]
        try:
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=600)
        except FileNotFoundError:
            print("[Sample] Warning: mkvmerge not found; cannot create benchmark sample.", file=sys.stderr)
            return None
        except subprocess.TimeoutExpired:
            print("[Sample] Warning: mkvmerge timed out; cannot create benchmark sample.", file=sys.stderr)
            return None

        produced = None
        if os.path.exists(BENCH_SAMPLE_FILE) and os.path.getsize(BENCH_SAMPLE_FILE) > 1024 * 1024:
            produced = BENCH_SAMPLE_FILE
        else:
            # Some mkvmerge versions append part numbers when splitting
            parts = sorted(glob.glob(os.path.join(TOOLS_DIR, "benchmark-sample-0*.mkv")))
            if parts and os.path.getsize(parts[0]) > 1024 * 1024:
                try:
                    os.replace(parts[0], BENCH_SAMPLE_FILE)
                    produced = BENCH_SAMPLE_FILE
                except OSError:
                    produced = parts[0]
                for extra in parts[1:]:
                    try:
                        os.remove(extra)
                    except OSError:
                        pass

        if produced:
            sig = _sample_source_signature(source)
            if sig:
                try:
                    with open(sidecar, "w", encoding="utf-8") as f:
                        f.write(sig + "\n")
                except OSError:
                    pass
            print(f"[Sample] Created 90s benchmark sample (no audio) from "
                  f"{os.path.basename(source)} [{time_range}].", file=sys.stderr)
            return produced

    print(f"[Sample] Warning: mkvmerge produced no usable sample from "
          f"{os.path.basename(source)}.", file=sys.stderr)
    return None


# ---------------------------------------------------------------------------
# CLEANUP
# ---------------------------------------------------------------------------

def cleanup_temp_folders():
    """Deletes temp folders and the test output files with retry logic."""
    print("Cleaning up temporary test files...", file=sys.stderr)

    # Wait 2 seconds to let Windows release file locks
    time.sleep(2)

    # 1. Clean up folders starting with a period
    try:
        for item in os.listdir(BASE_DIR):
            item_path = os.path.join(BASE_DIR, item)
            if os.path.isdir(item_path) and item.startswith("."):
                deleted = False
                for attempt in range(3):
                    try:
                        shutil.rmtree(item_path)
                        print(f"   - Deleted: {item}", file=sys.stderr)
                        deleted = True
                        break
                    except OSError:
                        time.sleep(1)

                if not deleted:
                    print(f"   - Warning: Could not fully delete {item} (File in use).", file=sys.stderr)
    except Exception as e:
        print(f"Error during folder cleanup: {e}", file=sys.stderr)

    # 2. Clean up the test output video files
    for output_file in (TEST_OUTPUT_FILE, os.path.join(BASE_DIR, "sample_svt-av1.mkv")):
        if os.path.exists(output_file):
            deleted = False
            for attempt in range(3):
                try:
                    os.remove(output_file)
                    print(f"   - Deleted: {os.path.basename(output_file)}", file=sys.stderr)
                    deleted = True
                    break
                except OSError:
                    time.sleep(1)

            if not deleted:
                print(f"   - Warning: Could not delete {os.path.basename(output_file)} (File in use).", file=sys.stderr)

    # 3. Clean up the benchmark temp folder (per-candidate av1an temps,
    #    scenes json, temporary VapourSynth script and its cache)
    if os.path.isdir(BENCH_TEMP_DIR):
        try:
            shutil.rmtree(BENCH_TEMP_DIR)
            print("   - Deleted: workercount_bench_temp", file=sys.stderr)
        except OSError:
            print("   - Warning: Could not delete workercount_bench_temp (File in use).", file=sys.stderr)


def kill_process_tree(pid):
    """Kills a process and all of its children.

    Fully race-proof: the target can die on its own at ANY point between
    these calls (e.g. av1an exiting right as the RAM guard fires), and
    psutil raises NoSuchProcess from surprising places (children() looks up
    the parent again internally). A kill helper must never be able to crash
    the benchmark - every step is individually guarded."""
    try:
        parent = psutil.Process(pid)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return
    except Exception:
        return
    try:
        children = parent.children(recursive=True)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        children = []
    except Exception:
        children = []
    for child in children:
        try:
            child.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
        except Exception:
            pass
    try:
        parent.kill()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass
    except Exception:
        pass


def parse_lp(params):
    """Reads the --lp value out of the encoder param string. Defaults to 3."""
    m = re.search(r"--lp\s+(\d+)", params)
    return int(m.group(1)) if m else 3


# ---------------------------------------------------------------------------
# THROUGHPUT MEASUREMENT (frames actually encoded per second)
# ---------------------------------------------------------------------------

# av1an prints a "Scene detection" header/progress while it runs its one-time
# scene detection pass - that bar has its OWN fps number which must never be
# mistaken for encode throughput. The real encode progress line is the one
# that contains the chunk counter, e.g. "[1/8 Chunks]".
SCENE_TEXT_RE = re.compile(r"scene\s*detection", re.IGNORECASE)
CHUNK_TEXT_RE = re.compile(r"chunk", re.IGNORECASE)
# av1an dumps the failed encoder's output with "stdout:"/"stderr:" sections
# when a worker crashes. Any of these markers on screen = a crashed run.
CRASH_TEXT_RE = re.compile(
    r"encoder crashed|Encoder failed|shutting down worker|\bstderr:",
    re.IGNORECASE)


class ConsoleFpsReader:
    """Reads av1an's live fps from the Windows console screen buffer.

    av1an hides its progress bar when stdout/stderr are piped, so the
    benchmark leaves av1an's output attached to the console (fully visible to
    the user) and samples the fps number av1an itself prints, e.g.:
        00:00:13 [1/8 Chunks] [...]  29% 192/655 (14.15 fps, eta 33s, ...)
    ONLY that encode progress line (identified by its "Chunks" counter) is
    accepted as an fps source - the fps shown by av1an's one-time scene
    detection bar is explicitly ignored, otherwise the benchmark would settle
    on scene-detection speed and kill av1an before SVT-AV1 ever starts.

    Below 1 fps av1an switches units and prints SECONDS PER FRAME instead,
    e.g. "(1.56 s/fr, eta 55m)". Those readings are parsed too and converted
    to fps (fps = 1 / s_per_frame), so very slow candidates are measured
    correctly instead of being invisible.

    STALE-LINE PROTECTION: the finished progress line of the PREVIOUS
    candidate stays frozen on screen above the new one. Two filters stop it
    from ever being read as the current candidate's fps: (1) mark_candidate_
    start() records the console cursor row when av1an launches and rows
    printed before that are ignored; (2) a reading is only accepted from a
    row whose text CHANGED since the previous poll - a live av1an progress
    line updates every second (its elapsed-time field ticks), a dead one
    never changes.

    Reading the buffer never disturbs what is on screen. On failure (or on
    non-Windows systems) .ok is False and the caller falls back to counting
    frames in av1an's temp folder."""

    FPS_RE = re.compile(r"([0-9]+(?:[.,][0-9]+)?)\s*fps", re.IGNORECASE)
    # Matches BOTH units av1an uses on its progress line: "14.15 fps" and,
    # below 1 fps, "1.56 s/fr" (seconds per frame). "Kb/s" can never match.
    RATE_RE = re.compile(r"([0-9]+(?:[.,][0-9]+)?)\s*(fps|s/fr)", re.IGNORECASE)

    @classmethod
    def parse_rate_fps(cls, text):
        """Latest throughput on a line, normalized to fps. '1.56 s/fr' ->
        0.641 fps. Returns None if the line carries no fps/s-per-frame."""
        latest = None
        for m in cls.RATE_RE.finditer(text):
            try:
                val = float(m.group(1).replace(",", "."))
            except ValueError:
                continue
            if m.group(2).lower() == "s/fr":
                if val <= 0:
                    continue
                val = 1.0 / val
            if val > 0:
                latest = val
        return latest

    def __init__(self):
        self.ok = False
        self._baseline_row = 0   # console row where the CURRENT candidate's
                                 # output starts (mark_candidate_start)
        self._prev_rows = {}     # row index -> last text seen there (staleness)
        if os.name != "nt":
            return
        try:
            import ctypes
            from ctypes import wintypes
            self._ctypes = ctypes
            self._k32 = ctypes.windll.kernel32

            class COORD(ctypes.Structure):
                _fields_ = [("X", ctypes.c_short), ("Y", ctypes.c_short)]

            class SMALL_RECT(ctypes.Structure):
                _fields_ = [("Left", ctypes.c_short), ("Top", ctypes.c_short),
                            ("Right", ctypes.c_short), ("Bottom", ctypes.c_short)]

            class CSBI(ctypes.Structure):
                _fields_ = [("dwSize", COORD), ("dwCursorPosition", COORD),
                            ("wAttributes", ctypes.c_ushort), ("srWindow", SMALL_RECT),
                            ("dwMaximumWindowSize", COORD)]

            self._COORD = COORD
            self._CSBI = CSBI
            # Open the console output buffer directly; works even if this
            # process's own stdout has been redirected.
            GENERIC_READ, GENERIC_WRITE = 0x80000000, 0x40000000
            FILE_SHARE_READ, FILE_SHARE_WRITE, OPEN_EXISTING = 1, 2, 3
            self._handle = self._k32.CreateFileW(
                "CONOUT$", GENERIC_READ | GENERIC_WRITE,
                FILE_SHARE_READ | FILE_SHARE_WRITE, None, OPEN_EXISTING, 0, None)
            if self._handle in (None, -1):
                return
            csbi = CSBI()
            if not self._k32.GetConsoleScreenBufferInfo(self._handle, ctypes.byref(csbi)):
                return
            self._DWORD = wintypes.DWORD
            self.ok = True
        except Exception:
            self.ok = False

    def _read_rows(self, rows_back=12):
        """[(absolute_row_index, text), ...] for the last rows up to cursor."""
        if not self.ok:
            return []
        rows = []
        try:
            ctypes = self._ctypes
            csbi = self._CSBI()
            if not self._k32.GetConsoleScreenBufferInfo(self._handle, ctypes.byref(csbi)):
                return []
            width = csbi.dwSize.X
            cursor_y = csbi.dwCursorPosition.Y
            for row in range(max(0, cursor_y - rows_back), cursor_y + 1):
                buf = ctypes.create_unicode_buffer(width + 1)
                nread = self._DWORD(0)
                if not self._k32.ReadConsoleOutputCharacterW(
                        self._handle, buf, width, self._COORD(0, row), ctypes.byref(nread)):
                    continue
                rows.append((row, buf.value))
            return rows
        except Exception:
            return []

    def read_recent_lines(self, rows_back=12):
        """Raw text of the last `rows_back` console rows up to the cursor."""
        return [text for _row, text in self._read_rows(rows_back)]

    def mark_candidate_start(self):
        """Call right when a candidate's av1an process launches: everything
        already on screen (including the PREVIOUS candidate's frozen progress
        line and its old fps) is above this row and will be ignored."""
        self._prev_rows = {}
        if not self.ok:
            return
        try:
            ctypes = self._ctypes
            csbi = self._CSBI()
            if self._k32.GetConsoleScreenBufferInfo(self._handle, ctypes.byref(csbi)):
                self._baseline_row = csbi.dwCursorPosition.Y
        except Exception:
            pass

    def erase_candidate_output(self):
        """Blank every console row from the candidate baseline down to the
        cursor and move the cursor back to the baseline row. Used to HIDE the
        entire output of a crashed av1an run (its multi-page encoder crash
        dumps included) so a single clean status line can replace it."""
        if not self.ok:
            return False
        try:
            ctypes = self._ctypes
            csbi = self._CSBI()
            if not self._k32.GetConsoleScreenBufferInfo(self._handle, ctypes.byref(csbi)):
                return False
            width = csbi.dwSize.X
            cursor_y = csbi.dwCursorPosition.Y
            first = max(0, min(self._baseline_row, cursor_y))
            length = width * (cursor_y - first + 1)
            if length <= 0:
                return False
            start = self._COORD(0, first)
            written = self._DWORD(0)
            self._k32.FillConsoleOutputCharacterW(
                self._handle, ctypes.c_wchar(" "), length, start, ctypes.byref(written))
            self._k32.FillConsoleOutputAttribute(
                self._handle, csbi.wAttributes, length, start, ctypes.byref(written))
            self._k32.SetConsoleCursorPosition(self._handle, start)
            return True
        except Exception:
            return False

    def read_latest_fps(self, rows_back=12):
        """Latest LIVE encode throughput near the cursor as fps, or None.

        Accepts both av1an units ("14.15 fps" and "1.56 s/fr" -> 1/1.56 fps).
        Only rows that carry av1an's chunk counter ("[n/m Chunks]") are
        parsed; the scene-detection bar is skipped. Rows printed before this
        candidate launched (mark_candidate_start) are skipped, and a value is
        only taken from a row whose text changed since the previous poll -
        so the frozen final line of an earlier test can never be re-read as
        the current candidate's speed."""
        latest = None
        for row_idx, text in self._read_rows(rows_back):
            prev_text = self._prev_rows.get(row_idx)
            self._prev_rows[row_idx] = text
            if row_idx < self._baseline_row:
                continue  # printed before this candidate started
            if SCENE_TEXT_RE.search(text):
                continue  # scene detection line - not encode throughput
            if not CHUNK_TEXT_RE.search(text):
                continue  # only trust the encode (Chunks) progress line
            if prev_text is None or prev_text == text:
                continue  # not (yet) proven live: a real progress line's
                          # elapsed-time field changes every second
            rate = self.parse_rate_fps(text)
            if rate is not None:
                latest = rate
        return latest


def kill_svt_stragglers():
    """Kill every SvtAv1EncApp instance on the system. av1an's own tree kill
    covers normal cases; this sweep guarantees no encoder keeps burning CPU
    into the cooldown or the next test."""
    for proc in psutil.process_iter(["name"]):
        try:
            if (proc.info.get("name") or "").lower().startswith("svtav1encapp"):
                proc.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass


def _median(values):
    ordered = sorted(values)
    n = len(ordered)
    if n == 0:
        return 0.0
    mid = n // 2
    return ordered[mid] if n % 2 else (ordered[mid - 1] + ordered[mid]) / 2.0


def count_ivf_frames(path):
    """Count complete frames in a (possibly still-growing) IVF file by walking
    its 12-byte frame headers. Safe to run while the encoder is writing."""
    frames = 0
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            header = f.read(32)
            if len(header) < 32 or header[:4] != b"DKIF":
                return 0
            pos = 32
            while pos + 12 <= size:
                f.seek(pos)
                frame_header = f.read(12)
                if len(frame_header) < 12:
                    break
                frame_size = int.from_bytes(frame_header[:4], "little")
                if frame_size <= 0 or pos + 12 + frame_size > size:
                    break
                frames += 1
                pos += 12 + frame_size
    except OSError:
        return 0
    return frames


def _ebml_vint_len(b0):
    for i in range(8):
        if b0 & (0x80 >> i):
            return i + 1
    return 0


def count_mkv_frames(path):
    """Count video frames in a (possibly still-growing) Matroska file by
    walking EBML elements and counting SimpleBlocks/BlockGroups.

    av1an 0.5.x writes its chunk files as MKV (despite the .ivf name), so this
    is the per-frame progress signal on those builds. Safe on partial files:
    parsing stops cleanly at the growing tail. Laced SimpleBlocks are counted
    frame-accurately."""
    SEGMENT, CLUSTER = 0x18538067, 0x1F43B675
    SIMPLEBLOCK, BLOCKGROUP = 0xA3, 0xA0
    frames = 0
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            while True:
                pos = f.tell()
                if pos >= size:
                    break
                # --- element ID (marker bits kept) ---
                b = f.read(1)
                if not b:
                    break
                idlen = _ebml_vint_len(b[0])
                if idlen == 0 or pos + idlen > size:
                    break
                idb = b + f.read(idlen - 1)
                if len(idb) < idlen:
                    break
                eid = int.from_bytes(idb, "big")
                # --- element size (marker bits masked) ---
                sb = f.read(1)
                if not sb:
                    break
                slen = _ebml_vint_len(sb[0])
                if slen == 0 or f.tell() + slen - 1 > size:
                    break
                szb = bytearray(sb + f.read(slen - 1))
                if len(szb) < slen:
                    break
                szb[0] &= (0xFF >> slen)
                esize = int.from_bytes(szb, "big")
                unknown = esize == (1 << (7 * slen)) - 1

                if eid in (SEGMENT, CLUSTER):
                    continue  # descend: children follow immediately
                if unknown:
                    break     # unknown-size non-master: cannot continue safely
                payload_start = f.tell()
                if payload_start + esize > size:
                    break     # partial element at the growing tail
                if eid == SIMPLEBLOCK:
                    # frame-accurate: honor lacing (flags bit 0x06 -> count byte)
                    head = f.read(min(esize, 8))
                    n = 1
                    if head:
                        tlen = _ebml_vint_len(head[0])
                        flag_idx = tlen + 2  # track vint + 2-byte timestamp
                        if tlen and len(head) > flag_idx:
                            if head[flag_idx] & 0x06 and len(head) > flag_idx + 1:
                                n = head[flag_idx + 1] + 1
                    frames += n
                    f.seek(payload_start + esize)
                elif eid == BLOCKGROUP:
                    frames += 1
                    f.seek(payload_start + esize)
                else:
                    f.seek(payload_start + esize)
    except OSError:
        return 0
    return frames


def count_chunk_frames(path):
    """Frames in one av1an chunk file, container detected by magic bytes:
    DKIF -> IVF walk, EBML -> Matroska block count. Anything else is not a
    chunk file and counts 0."""
    try:
        with open(path, "rb") as f:
            magic = f.read(4)
    except OSError:
        return 0
    if magic == b"DKIF":
        return count_ivf_frames(path)
    if magic == b"\x1a\x45\xdf\xa3":
        return count_mkv_frames(path)
    return 0


def count_done_json_frames(temp_dir):
    """Frames av1an itself has marked complete in its temp done.json (the
    resume ledger). Chunk-level granularity, so it lags the IVF walk, but it
    is layout-independent and works across av1an versions."""
    path = os.path.join(temp_dir, "done.json")
    if not os.path.exists(path):
        return 0
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            data = json.load(f)
    except Exception:
        return 0
    total = 0
    done = data.get("done", {}) if isinstance(data, dict) else {}
    if isinstance(done, dict):
        for value in done.values():
            if isinstance(value, (int, float)):
                total += int(value)
            elif isinstance(value, dict):
                for key in ("frames", "frame_count", "frames_encoded"):
                    if isinstance(value.get(key), (int, float)):
                        total += int(value[key])
                        break
    return total


def count_encoded_frames(temp_dir):
    """Total frames ENCODED so far in an av1an temp dir.

    CRITICAL EXCLUSION: av1an also writes SOURCE video segments into
    <temp>/split/ (Matroska files holding the ORIGINAL frames) while it
    prepares chunks - during/right after the one-time scene detection pass.
    Counting those once made the benchmark report scene detection / source
    splitting as "encode fps" (e.g. a stable 42 fps that was really
    2160 source frames / 51s of scene detection). Rules now:
      * anything under a directory named "split" is NEVER counted
      * Matroska (EBML) files only count inside a directory named "encode"
        (av1an's encoder-output folder) - source segments are Matroska too,
        so location is the only reliable discriminator
      * IVF (DKIF) files count anywhere outside "split" (IVF is always
        encoder output)
    av1an's own done.json (completed chunks) is used as a second signal and
    the larger of the two counts wins."""
    chunk_total = 0
    for root, _dirs, files in os.walk(temp_dir):
        parts = {p.lower() for p in os.path.normpath(root).split(os.sep)}
        if "split" in parts:
            continue  # av1an's SOURCE segments live here - not encoder output
        in_encode_dir = "encode" in parts
        for name in files:
            path = os.path.join(root, name)
            try:
                with open(path, "rb") as f:
                    magic = f.read(4)
            except OSError:
                continue
            if magic == b"DKIF":
                chunk_total += count_ivf_frames(path)
            elif magic == b"\x1a\x45\xdf\xa3" and in_encode_dir:
                chunk_total += count_mkv_frames(path)
    return max(chunk_total, count_done_json_frames(temp_dir))


def measure_encode_fps(input_path, encoder_params, workers, scenes_file, first_candidate):
    """Run av1an with `workers` workers and measure its real encode fps.

    PHASE 0 - SCENE DETECTION GATE: av1an first runs its ONE-TIME scene
    detection pass (visible as "Scene detection" in its terminal output).
    That phase is NOT the benchmark: the loop watches the console text for
    "Scene detection" and simply waits - collecting no fps samples and
    running no timers except the RAM guard - until av1an finishes detection
    and saves the scenes file into the temp folder (or the encode's own
    "Chunks" progress line appears). Only then does the SVT-AV1 encode begin
    and the fps measurement clocks start. Later candidates reuse the saved
    scenes file, so the gate passes instantly for them.

    av1an's output stays ATTACHED TO THE CONSOLE so the user sees its normal
    progress bar. The fps number av1an prints on its encode ("Chunks")
    progress line is sampled once per second from the console screen buffer
    (ConsoleFpsReader); if the buffer cannot be read, cumulative fps derived
    from counting frames in av1an's temp folder is used instead - same rule
    either way:

      * SETTLED: the readings over the last FPS_SETTLE_SPAN seconds stayed
        within FPS_SETTLE_DELTA fps -> stop, use the latest reading.
      * FINISHED EARLY: the encode completed first -> use the median of the
        last few readings (the most stable numbers from the end).
      * NEVER SETTLED: after FPS_MAX_MEASURE_SECONDS, median of the last few.

    After each test the whole av1an tree is killed plus a global sweep of any
    SvtAv1EncApp.exe stragglers. The live RAM kill-switch (every
    RAM_GUARD_INTERVAL from process start) is unchanged: system RAM at
    RAM_ABORT_PERCENT or free RAM under RAM_ABORT_MIN_AVAILABLE_MB kills the
    test instantly.

    Returns (fps, rss_peak_bytes, ram_aborted)."""
    candidate_temp = os.path.join(BENCH_TEMP_DIR, f"av1an_w{workers}")
    shutil.rmtree(candidate_temp, ignore_errors=True)
    os.makedirs(candidate_temp, exist_ok=True)
    if os.path.exists(TEST_OUTPUT_FILE):
        try:
            os.remove(TEST_OUTPUT_FILE)
        except OSError:
            pass

    cmd = [
        AV1AN_PATH,
        "-i", str(input_path),
        "-y",
        "--workers", str(workers),
        "-e", "svt-av1",
        "-m", "bestsource",
        "--temp", candidate_temp,
        "--scenes", scenes_file,
        "-o", TEST_OUTPUT_FILE,
        "-v", " " + encoder_params.strip(),
    ]

    reader = CONSOLE_FPS_READER_FACTORY()
    if reader.ok:
        print(f"   Watching av1an's fps readout below. Settle rule: readings stay "
              f"within {FPS_SETTLE_DELTA} fps for {FPS_SETTLE_SPAN:.0f}s.", file=sys.stderr)
    else:
        print(f"   (console fps readout unavailable - falling back to frame counting; "
              f"same settle rule: within {FPS_SETTLE_DELTA} fps for {FPS_SETTLE_SPAN:.0f}s)",
              file=sys.stderr)

    # --- Scene detection gate state (decided BEFORE av1an launches so nothing
    #     is printed while av1an owns the console) ---
    # A VALID scenes file means av1an will load it instead of re-detecting,
    # so the gate can be skipped. A corrupt/partial scenes file (earlier
    # interrupted run) would make av1an error out or silently re-detect while
    # the gate wrongly believed detection was done - delete it up front.
    def _scenes_file_ready(delete_corrupt=False):
        try:
            if os.path.getsize(scenes_file) <= 0:
                return False
            with open(scenes_file, "r", encoding="utf-8", errors="replace") as f:
                json.load(f)
            return True
        except OSError:
            return False
        except ValueError:
            if delete_corrupt:
                try:
                    os.remove(scenes_file)
                except OSError:
                    pass
            return False

    scene_done = _scenes_file_ready(delete_corrupt=True)
    if not scene_done:
        # Printed BEFORE launch: from here until this test ends the console
        # belongs to av1an alone - the benchmark stays silent while av1an is
        # drawing (printing into its redraw region garbles its output).
        print("   av1an runs its one-time scene detection first. It is NOT "
              "measured: fps measurement starts only after detection finishes "
              "and the scenes file is saved to the temp folder.", file=sys.stderr)

    try:
        # stdout/stderr are NOT redirected: av1an's progress bar stays visible.
        # Baseline the console cursor FIRST so anything already on screen
        # (the previous candidate's frozen progress line, old fps values) is
        # excluded from this candidate's readings.
        reader.mark_candidate_start()
        process = subprocess.Popen(cmd, cwd=BASE_DIR)
    except FileNotFoundError:
        print("Error: av1an executable not found.", file=sys.stderr)
        return 0.0, 0, False

    start = time.time()
    init_timeout = FIRST_FRAME_TIMEOUT if first_candidate else max(240, FIRST_FRAME_TIMEOUT // 2)
    fps_samples = []            # (timestamp, fps) once av1an reports moving fps
    first_signal_time = None
    first_frame_time = None     # fallback path bookkeeping
    frames_seen = 0
    result_fps = 0.0
    rss_peak = 0
    ram_aborted = False
    heavy_due = 0.0
    phase_start = start         # reset when the gate opens: all measurement
                                # timeouts count from the START OF THE ENCODE,
                                # never from the start of scene detection

    def _candidate_crashed():
        """Kill everything, HIDE the crashed run's console output, and print
        the one status line the user should see instead."""
        nonlocal ram_aborted, result_fps
        ram_aborted = True      # steers the search toward FEWER workers
        result_fps = 0.0
        if process.poll() is None:
            kill_process_tree(process.pid)
        kill_svt_stragglers()
        time.sleep(0.5)
        reader.erase_candidate_output()
        print("   ram oversaturated, lowering worker count", file=sys.stderr)

    def tail_median():
        return _median([f for _, f in fps_samples[-5:]])

    try:
        while True:
            exited = process.poll() is not None
            now = time.time()

            # --- FAST RAM SAFETY GUARD (every RAM_GUARD_INTERVAL) ---
            if not exited:
                vm = psutil.virtual_memory()
                if (vm.percent >= RAM_ABORT_PERCENT
                        or vm.available < RAM_ABORT_MIN_AVAILABLE_MB * 1024 * 1024):
                    print(f"\n   !! SYSTEM RAM CRITICAL ({vm.percent:.0f}% used, "
                          f"{vm.available // (1024**2)} MB free) - killing this test "
                          f"immediately to protect the system.", file=sys.stderr)
                    ram_aborted = True
                    kill_process_tree(process.pid)
                    kill_svt_stragglers()
                    break

            # Hard safety timeout counts from the start of the ENCODE phase
            # (phase_start), so a long one-time scene detection pass can never
            # eat into - or trip - the per-candidate measurement budget.
            if scene_done and now - phase_start > MAX_CANDIDATE_SECONDS:
                print("\n   ! Candidate hit the hard safety timeout.", file=sys.stderr)
                result_fps = tail_median()
                break

            # Heavier bookkeeping runs once per POLL_INTERVAL; the loop spins
            # faster purely for the RAM guard above.
            if now < heavy_due and not exited:
                time.sleep(RAM_GUARD_INTERVAL)
                continue
            heavy_due = now + POLL_INTERVAL

            # Track peak RAM of the whole process tree
            if not exited:
                try:
                    parent = psutil.Process(process.pid)
                    rss = parent.memory_info().rss
                    for child in parent.children(recursive=True):
                        try:
                            rss += child.memory_info().rss
                        except (psutil.NoSuchProcess, psutil.AccessDenied):
                            pass
                    if rss > rss_peak:
                        rss_peak = rss
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass

            # --- CRASH DETECTION ---
            # A crashed encoder makes av1an dump the worker's output with
            # "stdout:" / "stderr:" sections ("Encoder failed", "encoder
            # crashed"). Any of those markers on screen = a crashed run:
            # kill everything, ERASE the whole crashed av1an output from the
            # console, and report it as RAM oversaturation so the search
            # lowers the worker count.
            if reader.ok and any(CRASH_TEXT_RE.search(r)
                                 for r in reader.read_recent_lines(40)):
                _candidate_crashed()
                break

            # --- ONE-TIME SCENE DETECTION GATE ---
            # Nothing is measured until av1an's scene detection pass is done.
            # Completion = the scenes file has been saved to the temp folder,
            # OR the encode's own "Chunks" progress line has appeared (belt
            # and suspenders; either one means SVT-AV1 encoding has begun).
            # DELIBERATELY SILENT: nothing is printed while av1an is running
            # and drawing its progress bars (printing into av1an's redraw
            # region can garble/erase its output on screen). The wait was
            # announced before launch.
            if not scene_done:
                rows = reader.read_recent_lines() if reader.ok else []
                encode_signal = any(CHUNK_TEXT_RE.search(r)
                                    and ConsoleFpsReader.RATE_RE.search(r)
                                    for r in rows)
                if not encode_signal and not reader.ok:
                    # No console access: encoded chunk data appearing in the
                    # temp folder proves scene detection is over. (Safe now:
                    # count_encoded_frames ignores av1an's source segments.)
                    encode_signal = count_encoded_frames(candidate_temp) > 0

                if _scenes_file_ready() or encode_signal:
                    scene_done = True
                    phase_start = now  # measurement clocks start NOW
                    # fall through to normal measurement this same iteration
                else:
                    if exited:
                        # av1an died before detection finished = crashed run
                        _candidate_crashed()
                        break
                    if now - start > SCENE_DETECT_TIMEOUT:
                        print("\n   ! Timed out waiting for av1an's scene "
                              "detection to finish.", file=sys.stderr)
                        break
                    time.sleep(RAM_GUARD_INTERVAL)
                    continue

            # --- fps signal: av1an's own readout, else counted frames ---
            fps_now = reader.read_latest_fps() if reader.ok else None
            if fps_now is None or fps_now <= 0:
                new_count = count_encoded_frames(candidate_temp)
                if new_count > frames_seen:
                    frames_seen = new_count
                if frames_seen > 0:
                    if first_frame_time is None:
                        first_frame_time = now
                    span = now - first_frame_time
                    if span >= 2.0:
                        fps_now = frames_seen / span

            if fps_now is not None and fps_now > 0:
                if first_signal_time is None:
                    first_signal_time = now
                fps_samples.append((now, fps_now))
            elif first_signal_time is None and not exited and now - phase_start > init_timeout:
                # phase_start = when scene detection finished, so this timeout
                # covers only source indexing / lookahead fill, never the
                # one-time scene detection pass itself.
                print("\n   ! Timed out waiting for av1an to report encoding progress.",
                      file=sys.stderr)
                break

            # --- stop conditions ---
            if exited:
                if not fps_samples and frames_seen == 0:
                    # av1an ended without ever producing encoded output: that
                    # is a crashed/oversaturated run, not a finished encode.
                    _candidate_crashed()
                    break
                # Encode finished: most stable numbers from the end
                result_fps = tail_median()
                break

            if fps_samples and first_signal_time is not None:
                span_covered = now - first_signal_time
                window = [f for t, f in fps_samples if t >= now - FPS_SETTLE_SPAN]
                if (span_covered >= FPS_SETTLE_SPAN and window
                        and max(window) - min(window) <= FPS_SETTLE_DELTA):
                    result_fps = fps_samples[-1][1]
                    print(f"\n   fps settled at {result_fps:.2f} "
                          f"(varied <= {FPS_SETTLE_DELTA} fps over the last "
                          f"{FPS_SETTLE_SPAN:.0f}s) - stopping this test.", file=sys.stderr)
                    break
                if span_covered >= FPS_MAX_MEASURE_SECONDS:
                    result_fps = tail_median()
                    print(f"\n   fps never fully settled within "
                          f"{FPS_MAX_MEASURE_SECONDS}s - using the median of the "
                          f"last readings ({result_fps:.2f} fps).", file=sys.stderr)
                    break

            time.sleep(RAM_GUARD_INTERVAL)
    finally:
        if process.poll() is None:
            kill_process_tree(process.pid)
        kill_svt_stragglers()
        time.sleep(1)
        shutil.rmtree(candidate_temp, ignore_errors=True)
        if os.path.exists(TEST_OUTPUT_FILE):
            for attempt in range(3):
                try:
                    os.remove(TEST_OUTPUT_FILE)
                    break
                except OSError:
                    time.sleep(1)

    return result_fps, rss_peak, ram_aborted


# Factory hook (kept swappable for testing)
CONSOLE_FPS_READER_FACTORY = ConsoleFpsReader


# ---------------------------------------------------------------------------
# SWEET-SPOT SEARCH
# ---------------------------------------------------------------------------

def sweet_spot_search(measure_fn, start, hard_max, budget=SEARCH_BUDGET,
                      tie_margin=TIE_MARGIN, label="workers"):
    """Hill-climb over worker counts using measured fps.

    measure_fn(w) -> fps (0.0 for failed/unsafe candidates). Starts at `start`,
    probes one step up; if that's not a clear win, probes one step down;
    keeps climbing in the winning direction while fps improves by more than
    tie_margin. With step 2 the winner's neighbors are refined by 1.
    Final pick: among everything within tie_margin of the best fps, the
    LOWEST worker count wins (fewer workers = less RAM, snappier system).

    Returns (best_worker_count_or_None, {tested: fps})."""
    results = {}

    def test(w):
        w = max(1, min(int(w), hard_max))
        if w in results:
            return w
        if len(results) >= budget:
            return None
        print(f"\n   Testing {w} {label}...", file=sys.stderr)
        fps = measure_fn(w)
        results[w] = fps
        if fps > 0:
            print(f"   -> {w} {label}: {fps:.2f} fps", file=sys.stderr)
        else:
            print(f"   -> {w} {label}: no throughput (failed, timed out, or unsafe)", file=sys.stderr)
        return w

    def fps_of(w):
        return results.get(w, -1.0)

    step = 1 if start <= 4 else 2
    test(start)

    up = start + step
    down = start - step
    if up <= hard_max and test(up) is not None and fps_of(up) > fps_of(start) * (1 + tie_margin):
        w = up
        while w + step <= hard_max and len(results) < budget:
            if test(w + step) is None or fps_of(w + step) <= fps_of(w) * (1 + tie_margin):
                break
            w += step
    elif down >= 1 and test(down) is not None and fps_of(down) > fps_of(start) * (1 + tie_margin):
        w = down
        while w - step >= 1 and len(results) < budget:
            if test(w - step) is None or fps_of(w - step) <= fps_of(w) * (1 + tie_margin):
                break
            w -= step

    # Refine around the current best when coarse-stepping
    if step == 2 and len(results) < budget:
        best_w = max(results, key=lambda k: results[k])
        for cand in (best_w - 1, best_w + 1):
            if 1 <= cand <= hard_max and cand not in results and len(results) < budget:
                test(cand)

    valid = {w: f for w, f in results.items() if f > 0}
    if not valid:
        return None, results
    best_fps = max(valid.values())
    contenders = [w for w, f in valid.items() if f >= best_fps * (1 - tie_margin)]
    return min(contenders), results


def run_benchmark(input_path, encoder_params):
    """Find the encode worker sweet spot for the given input and params."""
    cpu_threads = os.cpu_count() or 1
    physical_cores = psutil.cpu_count(logical=False) or max(1, cpu_threads // 2)
    total_ram = psutil.virtual_memory().total
    lp = parse_lp(encoder_params)
    start = max(1, min(cpu_threads, physical_cores // max(1, lp)))

    os.makedirs(BENCH_TEMP_DIR, exist_ok=True)
    scenes_file = os.path.join(BENCH_TEMP_DIR, "bench_scenes.json")
    # A scenes file left over from a previous run/video would pre-open the
    # scene-detection gate while av1an actually re-detects (frame counts
    # won't match), letting scene-detection activity leak into measurements.
    # Delete it: exactly one fresh detection pass per benchmark run.
    try:
        os.remove(scenes_file)
    except OSError:
        pass

    print(f"\nFinding the worker-count sweet spot on {os.path.basename(str(input_path))}", file=sys.stderr)
    print(f"Encoder params: {encoder_params.strip()}", file=sys.stderr)
    print(f"CPU: {physical_cores} cores / {cpu_threads} threads (--lp {lp}) | "
          f"starting at {start} workers", file=sys.stderr)
    print("Each candidate runs av1an with its normal output visible and is "
          f"stopped once av1an's fps readout settles (varies <= {FPS_SETTLE_DELTA} fps "
          f"over {FPS_SETTLE_SPAN:.0f}s), followed by a {COOLDOWN_BETWEEN_TESTS}s "
          "cooldown. av1an's one-time scene detection runs first and is NOT "
          "measured: the benchmark waits for it to finish (watching for 'Scene "
          "detection' in av1an's output), lets the scenes file save to the temp "
          "folder, and only then measures the SVT-AV1 encode.", file=sys.stderr)

    # Pre-flight: if the system is ALREADY under memory pressure, warn - the
    # live guard will keep everything safe, but results will skew low.
    vm0 = psutil.virtual_memory()
    if vm0.percent >= 80:
        print(f"   ! System RAM is already {vm0.percent:.0f}% used before the "
              f"benchmark starts. Close other programs for a representative "
              f"result; the benchmark will protect the system either way.", file=sys.stderr)

    state = {"first": True, "per_worker_rss": 0, "ram_cap": None}
    ram_limited = []

    def mark_unsafe(w):
        ram_limited.append(w)
        state["ram_cap"] = w if state["ram_cap"] is None else min(state["ram_cap"], w)

    def measure(w):
        # Hard cap: once any count has tripped the RAM guard, everything at or
        # above it is refused outright.
        if state["ram_cap"] is not None and w >= state["ram_cap"]:
            print(f"   ! {w} workers is at/above the RAM-unsafe count "
                  f"({state['ram_cap']}) - not testing.", file=sys.stderr)
            return 0.0

        # Prospective skip: using the per-worker RSS learned from completed
        # candidates, don't even LAUNCH counts that are clearly hopeless
        # (borderline ones still run under the live kill-switch).
        if state["per_worker_rss"] > 0:
            vm = psutil.virtual_memory()
            projected_available = vm.available - state["per_worker_rss"] * w
            if projected_available < RAM_ABORT_MIN_AVAILABLE_MB * 1024 * 1024 * 0.5:
                print(f"   ! {w} workers is projected to need ~"
                      f"{(state['per_worker_rss'] * w) // (1024**2)} MB "
                      f"(only {vm.available // (1024**2)} MB free) - not launching.",
                      file=sys.stderr)
                mark_unsafe(w)
                return 0.0

        fps, rss_peak, ram_aborted = measure_encode_fps(
            input_path, encoder_params, w, scenes_file, state["first"])
        state["first"] = False

        # Cooldown after every launched test so clocks/thermals recover before
        # the next candidate (skipped tests don't need one).
        print(f"   Cooling down {COOLDOWN_BETWEEN_TESTS}s...", file=sys.stderr)
        time.sleep(COOLDOWN_BETWEEN_TESTS)
        # After the cooldown, silently kill any SvtAv1EncApp.exe still
        # running so nothing bleeds CPU into the next candidate's numbers.
        kill_svt_stragglers()

        if not ram_aborted and rss_peak > 0:
            state["per_worker_rss"] = max(state["per_worker_rss"], rss_peak // max(1, w))

        if ram_aborted:
            print(f"   ! {w} workers marked unsafe - steering the search "
                  f"toward fewer workers.", file=sys.stderr)
            mark_unsafe(w)
            return 0.0
        if rss_peak > total_ram * RAM_HEADROOM:
            print(f"   ! {w} workers pushed RAM to {rss_peak // (1024**2)} MB "
                  f"(>{int(RAM_HEADROOM * 100)}% of system RAM) - rejected.", file=sys.stderr)
            mark_unsafe(w)
            return 0.0
        return fps

    best, results = sweet_spot_search(measure, start, cpu_threads)

    print("\n------------------------------------------------")
    print(f"   - Total System RAM: {total_ram // (1024**2)} MB")
    print(f"   - CPU: {physical_cores} cores / {cpu_threads} threads")
    for w in sorted(results):
        marker = "  <-- sweet spot" if w == best else ""
        fps_str = f"{results[w]:.2f} fps" if results[w] > 0 else "failed/unsafe"
        print(f"   - {w} workers: {fps_str}{marker}")
    if ram_limited:
        print(f"   - RAM-limited counts (unsafe on this system): {sorted(set(ram_limited))}")
    if best is None:
        best = 1
        print("   - WARNING: No candidate produced throughput (all benchmarks "
              "failed). Falling back to 1 worker (safe default; --lp 3 is "
              "dropped by the dispatcher at 1 worker).")
    else:
        print(f"   - Calculated Optimal Workers: {best}")
    print("------------------------------------------------")

    return best


def write_config(workers):
    try:
        # Carry over the cputarget= line the builders remember, above workers=
        # (the .bat files read this file with a last-line-wins for/f loop, so
        # the worker count has to stay on the last line).
        cpu_line = ""
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8", errors="ignore") as f:
                match = re.search(r"^\s*cputarget\s*=\s*([A-Za-z0-9_.-]+)",
                                  f.read(), re.IGNORECASE | re.MULTILINE)
            if match:
                cpu_line = f"cputarget={match.group(1).strip()}\n"
        except OSError:
            pass
        with open(CONFIG_FILE, "w") as f:
            f.write(f"{cpu_line}workers={workers}\n")
        return True
    except Exception as e:
        print(f"Error writing config file: {e}")
        return False


# ---------------------------------------------------------------------------
# MODES
# ---------------------------------------------------------------------------

def resolve_benchmark_input():
    """Prefer a fresh 90s sample cut from the user's actual source in
    video-input; fall back to the bundled tools/sample.mkv."""
    sample = ensure_benchmark_sample()
    if sample:
        return sample
    if os.path.exists(SAMPLE_FILE):
        print(f"[Sample] Falling back to {os.path.basename(SAMPLE_FILE)}.", file=sys.stderr)
        return SAMPLE_FILE
    return None


def maybe_delete_benchmark_sample(bat_path=None):
    """Delete the benchmark sample unless ssimu2-workercount.py will still
    need it later in the same .bat run."""
    if bat_path is None:
        bat_path = find_active_bat_file()
    if bat_path and bat_runs_ssimu2_optimize(bat_path):
        print("[Sample] Keeping benchmark sample for the SSIMU2 benchmark step.", file=sys.stderr)
        return
    delete_benchmark_sample()


def run_normal_mode():
    """GENERAL (non-optimized) benchmark: generates the shared
    tools\\workercount-config.txt using the bundled tools\\sample.mkv with
    default encoder params. settings.txt is NOT read and the user's sources
    are NOT touched - this config is a generic default shared by every bat
    without optimize-workers=true."""
    if not os.path.exists(SAMPLE_FILE):
        fallback = max(1, (os.cpu_count() or 4) // 4)
        print(f"Error: tools\\sample.mkv is missing - cannot run the general "
              f"benchmark. Falling back to {fallback} workers (threads/4).")
        workers = fallback
    else:
        # SAFETY NET: if the benchmark dies for ANY reason, the config file
        # must still be written - a missing workercount-config.txt cascades
        # into the dispatcher calling Auto-Boost-Av1an.py with a bare
        # "--workers" (no value) and the whole encode failing. When the
        # benchmark fails, 1 worker is the guaranteed-safe answer (and the
        # dispatchers drop --lp 3 for workers=1 so SVT-AV1 still uses the
        # whole CPU via its default auto parallelism).
        try:
            workers = run_benchmark(SAMPLE_FILE, ENCODER_PARAMS)
        except Exception as e:
            workers = 1
            print(f"\n   ! Benchmark aborted unexpectedly ({type(e).__name__}: {e}).",
                  file=sys.stderr)
            print("   Falling back to 1 worker (safe default) so the config "
                  "still gets written.", file=sys.stderr)
            try:
                kill_svt_stragglers()
            except Exception:
                pass
        finally:
            try:
                cleanup_temp_folders()
            except Exception:
                pass

    if write_config(workers):
        print("\nOne-time test complete. Auto worker count set.")
        print("You may manually edit tools\\workercount-config.txt if needed")


def run_optimize_mode(bat_arg=None):
    """Benchmark using the launching .bat's real encoder preset/params plus any
    settings.txt filtering, then write custom-av1an-workers back into the .bat."""
    bat_path = None
    if bat_arg and os.path.isfile(bat_arg):
        bat_path = os.path.abspath(bat_arg)
    else:
        bat_path = find_active_bat_file()

    if not bat_path:
        print("[Optimize] Could not locate the launching .bat file. Skipping optimized benchmark.")
        return

    bat = parse_bat_settings(bat_path)

    if bat.get("optimize-workers", "").strip().lower() not in ("true", "1", "yes", "on"):
        # This bat did not opt in - proceed as normal (do nothing here).
        return

    existing = bat.get("custom-av1an-workers", "").strip()
    if existing.isdigit() and int(existing) > 0:
        print(f"[Optimize] custom-av1an-workers already set to {existing} in "
              f"{os.path.basename(bat_path)}. Skipping benchmark.")
        if not os.path.exists(CONFIG_FILE):
            write_config(int(existing))
        return

    print("\n-------------------------------------------------------------------------------")
    print(f"[Optimize] One-time optimized encode benchmark for: {os.path.basename(bat_path)}")
    print("-------------------------------------------------------------------------------")

    source_path = resolve_benchmark_input()
    if not source_path:
        print("[Optimize] Error: No benchmark source available (video-input is empty "
              "and tools\\sample.mkv is missing). Skipping.")
        return

    # --- Set up the same SVT-AV1 fork the bat will use ---
    fork = (bat.get("fork", "essential") or "essential").strip()
    arch = (bat.get("arch", "") or "").strip()
    if not arch:
        # Bats generated before ARCH replaced AVX512.
        avx512 = ("--avx512" in bat.get("avx512_flag", "")
                  or bat.get("avx512", "").strip().lower() in ("true", "1", "yes", "on"))
        arch = "avx512" if avx512 else "x86-64-v3"
    try:
        sys.path.insert(0, TOOLS_DIR)
        from svt_fork_setup import setup_svt_av1_fork
        setup_svt_av1_fork(TOOLS_DIR, fork, arch=arch, verbose=True)
    except Exception as e:
        print(f"[Optimize] Warning: Could not set up SVT-AV1 fork '{fork}': {e}")

    # --- Build the real encoder parameters from the bat ---
    crf = (bat.get("crf") or bat.get("quality", "30")).strip() or "30"
    speed = bat.get("final_speed", "4").strip() or "4"
    extra = (bat.get("final_params") or bat.get("av1an_settings") or "").strip()
    encoder_params = " ".join(f"--crf {crf} --preset {speed} {extra}".split())

    # --- Build the VapourSynth input script (ALWAYS, matching the real run:
    #     Auto-Boost feeds av1an a .vpy unconditionally, so even with all
    #     filters off the encode decodes through VapourSynth with
    #     initialize_clip/finalize_clip bit-depth handling) ---
    input_path = source_path
    values = read_settings_values()

    # The bat's DENOISE setting is what dispatch will write into settings.txt
    # right before encoding, so it must override whatever settings.txt says
    # NOW for the benchmark to match the real encode exactly.
    bat_denoise = bat.get("denoise", "").strip().lower()
    if bat_denoise in ("true", "false"):
        if values.get("denoise", "").lower() != bat_denoise:
            print(f"[Optimize] Using the bat's DENOISE={bat_denoise.capitalize()} "
                  f"(overrides settings.txt for this benchmark; dispatch applies "
                  f"the same value before encoding).", file=sys.stderr)
        values["denoise"] = bat_denoise

    # The bat's tonemap setting: when True, the real encode runs the libplacebo
    # HDR->SDR tonemap inside the VapourSynth script, so the benchmark must
    # include it to measure the true CPU+GPU load.
    bat_tonemap = bat.get("tonemap", "").strip().lower() in ("true", "1", "yes", "on")

    try:
        input_path = build_filtered_vpy(values, source_path, tonemap=bat_tonemap)
        active = [k for k in FILTER_SETTING_KEYS if values.get(k, "false").lower() == "true"]
        if bat_tonemap:
            active.append("tonemap (libplacebo HDR->SDR, CPU+GPU)")
        if active:
            print(f"[Optimize] Benchmarking through a temporary VapourSynth script "
                  f"(mirrors the real encode; active filters: {', '.join(active)}).")
        else:
            print("[Optimize] Benchmarking through a temporary VapourSynth script "
                  "(mirrors the real encode; no settings.txt filters active).")
    except Exception as e:
        print(f"[Optimize] Warning: Could not build the VapourSynth script ({e}). "
              f"Benchmarking the raw sample instead.")
        input_path = source_path

    # SAFETY NET: any unexpected benchmark failure must still yield a worker
    # count - otherwise the .bat continues with an empty value and the
    # dispatcher's "--workers" call fails the whole encode. 1 worker is the
    # guaranteed-safe fallback (dispatchers drop --lp 3 for workers=1).
    try:
        workers = run_benchmark(input_path, encoder_params)
    except Exception as e:
        workers = 1
        print(f"\n   ! Benchmark aborted unexpectedly ({type(e).__name__}: {e}).",
              file=sys.stderr)
        print("   Falling back to 1 worker (safe default) so the config "
              "still gets written.", file=sys.stderr)
        try:
            kill_svt_stragglers()
        except Exception:
            pass

    if set_bat_value(bat_path, "custom-av1an-workers", workers):
        print(f"[Optimize] Wrote custom-av1an-workers={workers} into {os.path.basename(bat_path)}")
        print("[Optimize] Clear that value in the script to re-run this benchmark.")

    if not os.path.exists(CONFIG_FILE):
        write_config(workers)
        print("[Optimize] Also wrote tools/workercount-config.txt (used by non-optimized scripts).")

    cleanup_temp_folders()
    maybe_delete_benchmark_sample(bat_path)


def parse_cli(argv):
    optimize = False
    bat_arg = None
    i = 0
    while i < len(argv):
        if argv[i] in ("--optimize-bat", "--optimize-script"):
            optimize = True
            if i + 1 < len(argv) and not argv[i + 1].startswith("--"):
                bat_arg = argv[i + 1]
                i += 2
            else:
                i += 1
        else:
            i += 1
    return optimize, bat_arg


if __name__ == "__main__":
    optimize, bat_arg = parse_cli(sys.argv[1:])
    if optimize:
        run_optimize_mode(bat_arg)
    else:
        run_normal_mode()
