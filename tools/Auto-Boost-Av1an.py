#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "vsjetpack",
#   "numpy",
# ]
# ///

# Requires manually installing:
# Av1an:             Native Windows Version (in VapourSynth folder OR in PATH)
# SVT-AV1:           Native Windows Version
# FFmpeg:            Windows version
# FFVship:           Native Windows Version (portable FFVship_nvidia/FFVship_amd/FFVship_Vulkan folders) for SSIMU2 metrics
# vs-zip:            Required for XPSNR (Default metric) and SSIMU2 fallback

# Auto-Boost-Essential
# Modified for Av1an + Standard SVT-AV1 flow + FFVship + Zones Support

import os as _bootstrap_os

PLUGIN_ENV_VAR = "VAPOURSYNTH_EXTRA_PLUGIN_PATH"


def vs_plugin_dir():
    """Absolute path to the package's vs-plugins folder, or "" if it is gone."""
    root_dir = _bootstrap_os.path.dirname(_bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__)))
    candidate = _bootstrap_os.path.join(root_dir, "VapourSynth", "vs-plugins")
    return candidate if _bootstrap_os.path.isdir(candidate) else ""


def ensure_vs_plugin_path():
    """Point VapourSynth at the package's vs-plugins folder.

    VapourSynth used to run in portable mode: a portable.vs marker next to
    python.exe made the core autoload VapourSynth\\vs-plugins, which is where
    this package keeps ffms2, DFTTest, libvs_placebo and vszip. VapourSynth 78
    dropped that and reads VAPOURSYNTH_EXTRA_PLUGIN_PATH instead; without it
    scripts fail with "No attribute with the name ffms2 exists".

    This has to run before vstools pulls in a core, so it sits above the
    imports. Child processes inherit the variable.
    """
    found = vs_plugin_dir()
    if not found:
        return ""

    existing = [part for part in _bootstrap_os.environ.get(PLUGIN_ENV_VAR, "").split(_bootstrap_os.pathsep) if part]
    if not existing:
        # Set a single path: valid whether the core reads one path or a list.
        _bootstrap_os.environ[PLUGIN_ENV_VAR] = found
    elif not any(_bootstrap_os.path.normcase(part) == _bootstrap_os.path.normcase(found) for part in existing):
        _bootstrap_os.environ[PLUGIN_ENV_VAR] = _bootstrap_os.pathsep.join(existing + [found])

    return found


ensure_vs_plugin_path()

# Silence the core's plugin-load chatter (API3 deprecation notices, duplicate
# plugin DLLs). Must run before the first vs.core access, since that is what
# triggers the autoload that emits them.
try:
    import sys as _quiet_sys, os as _quiet_os
    _quiet_dir = _quiet_os.path.dirname(_quiet_os.path.abspath(__file__))
    if _quiet_dir not in _quiet_sys.path:
        _quiet_sys.path.insert(0, _quiet_dir)
    from vs_quiet import silence_plugin_noise as _silence_plugin_noise
    _silence_plugin_noise()
except Exception:
    pass

from vstools import vs, core, depth, DitherType, clip_async_render
try:
    from vstools.functions.progress import get_render_progress, FPSColumn
except:
    from vstools.functions.render.progress import get_render_progress, FPSColumn
from rich.progress import Progress, TextColumn, BarColumn, TimeElapsedColumn, TimeRemainingColumn, SpinnerColumn, ProgressColumn
from rich.console import Console, Group
from rich.live import Live
from rich.text import Text
from statistics import quantiles
from math import ceil, log10
from pathlib import Path
from collections import Counter
import subprocess
import argparse
import platform
import shutil
import struct
import glob
import sys
import gc
import os
import filecmp
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import re
import json
import csv
import time
import threading
import collections
from contextlib import contextmanager
import numpy as np
import concurrent.futures
from svt_fork_setup import setup_svt_av1_fork
import tonemap_backend
import source_filter
import vpy_template
import lossless_mode


def finish_cleanly(code=0):
    """Leave the process without letting the interpreter finalize.

    VapourSynth R79 segfaults while it tears the core down, once frames
    have actually been served - the fast pass and the metric passes both
    do that. The crash lands after the work is finished and every file is
    written, but Python still reports a crash exit code, and a caller that
    trusts the exit code throws away a complete encode. Nothing this
    script owns needs finalizing, so flush what is buffered and go.

    Reproduced on this package (VapourSynth R79, API R4.2, Python 3.13.9)
    with vship and with vszip alike; gc.collect() beforehand does not help.
    """
    try:
        sys.stdout.flush()
    except Exception:
        pass
    try:
        sys.stderr.flush()
    except Exception:
        pass
    os._exit(code)


ver_str = "v3.4.5"

# --- TOOL PATHS HELPER ---
def resolve_tool(portable_path_str: str, binary_name: str) -> Path:
    """
    Checks for the tool in the specified portable folder first.
    If not found, checks the system PATH.
    Returns the Path object to the executable.
    """
    # 1. Check Portable Path relative to this script's tools/ folder.
    p_path = Path(portable_path_str)
    if not p_path.is_absolute():
        p_path = Path(__file__).parent / p_path
    p_path = p_path.resolve()
    if p_path.exists():
        return p_path

    # 2. Check System Path
    sys_path = shutil.which(binary_name)
    if sys_path:
        return Path(sys_path)

    # 3. Return Portable Path (defaults to this for error handling later)
    return p_path

# --- TOOL PATHS CONFIGURATION ---
# Auto-detects between portable folder or system installed versions
av1an_exe = resolve_tool("av1an/av1an", "av1an")

# FFVship replaces fssimu2 for SSIMULACRA2 metrics.
# Benchmarking writes the selected backend/gpuThreads to tools/workercount-ssimu2.txt.
tools_dir = Path(__file__).parent.resolve()
ffvship_nvidia_exe = resolve_tool("FFVship/FFVship_nvidia/FFVship", "FFVship")
ffvship_amd_exe = resolve_tool("FFVship/FFVship_amd/FFVship", "FFVship")
ffvship_vulkan_exe = resolve_tool("FFVship/FFVship_Vulkan/FFVship", "FFVship")
ffvship_config_file = tools_dir / "workercount-ssimu2.txt"

# Cropdetect remains a script path, usually local to the toolset
cropdetect_script = (Path(__file__).parent / "cropdetect.py").resolve()
# --------------------------------

parser = argparse.ArgumentParser()
parser.add_argument("-s", "--stage", help = "Select stage: 1 = fast encode, 2 = calculate metrics, 3 = generate zones, 4 = final encode | Default: all", default=0)
parser.add_argument("-i", "--input", required=True, help = "Video input filepath (original source file)")
parser.add_argument("-o", "--output", help = "Where to write the finished <name>-av1.mkv | Default: next to the input file", default=None)
parser.add_argument("--scenes", help = "Path to external scenes JSON generated by Progressive-Scene-Detection")
parser.add_argument("-t", "--temp", help = "The temporary directory for the script to store files in | Default: video input filename")
parser.add_argument("--fast-speed", help = "Fast encode speed (Allowed: 0-10 or presets) | Default: 10", default="10")
parser.add_argument("--final-speed", help = "Final encode speed (Allowed: 0-10 or presets) | Default: 4", default="4")
parser.add_argument("--quality", help = "Base encoder --quality (Allowed: low, medium, high, breeze or int) | Default: medium", default="medium")
parser.add_argument("-a", "--aggressive", action='store_true', help = "More aggressive boosting | Default: not active")
parser.add_argument("-u", "--unshackle", action='store_true', help = "Less restrictive boosting | Default: not active")
parser.add_argument("--fast-params", help="Custom fast encoding parameters (SVT flags)")
parser.add_argument("--final-params", help="Custom final encoding parameters (SVT flags passed to Av1an)")
parser.add_argument("--ssimu2", help = "SSIMU2 mode: auto, gpu, vs-hip, ffvship, ffvship_nvidia, ffvship_amd, ffvship_vulkan, vs-zip | If omitted, defaults to XPSNR", default=None)
parser.add_argument("--ssimu2-cpu-workers", help = "GPU streams for FFVship or CPU workers for vs-zip | Default: 4", default="4")
parser.add_argument("--workers", help="Number of Av1an workers | Default: 1", default=None)
parser.add_argument("--photon-noise", help="Av1an photon noise strength | Default: 0", default="0")
parser.add_argument("--zones", help="Path to specific zones file override", default=None)
parser.add_argument("--verbose", action='store_true', help = "Enable more verbosity | Default: not active")
parser.add_argument("-r", "--resume", action='store_true', help = "Resume the process from the last (un)completed stage | Default: not active")
parser.add_argument("-nb", "--no-boosting", action='store_true', help = "Runs the script without boosting (final encode only) | Default: not active")
parser.add_argument("--autocrop", action='store_true', help = "Enable automatic crop detection | Default: not active")
parser.add_argument("--convert-to-YUV420P10", action='store_true', help = "Convert to YUV420P10 during processing for sources such as 422 or 444 etc | Default: not active")
parser.add_argument("-v", "--version", action='version', version = f"Auto-Boost-Essential {ver_str}")
parser.add_argument("--debug", action='store_true', help = "Checks the installation and provides relevant information for troubleshooting | Default: not active")
parser.add_argument("--fork", help="SVT-AV1 fork to copy before encoding: 5fish, essential, hdr, custom | Default: essential", default="essential")
parser.add_argument("--avx512", action='store_true', help="Deprecated spelling of --arch avx512")
parser.add_argument("--arch", help="CPU build of the SVT-AV1 fork to use: x86-64-v3, znver2, avx512 | Default: x86-64-v3", default=None)
parser.add_argument("--tonemap", nargs="?", const="true", default="false", help="Tonemap HDR to SDR (BT.709) inside the VapourSynth script, via libplacebo or the CPU fallback depending on the GPU: true/false | Default: false")

args = parser.parse_args()

# --- PRIVACY HELPER ---
def obscure_user_path(text: str) -> str:
    """
    Replaces any username in C:\\Users\\<username> with 'av1enjoyer'
    to obscure the real username in console output.
    Also ensures av1an.exe is displayed in lowercase.
    """
    if platform.system() == 'Windows':
        text = re.sub(r'(Users[\\/])([^\\/]+)', r'\1av1enjoyer', text, flags=re.IGNORECASE)
        text = re.sub(r'av1an\.exe', 'av1an.exe', text, flags=re.IGNORECASE)
        return text
    text = re.sub(r'(/home/)([^/]+)/', r'\1av1enjoyer/', text)
    return text
# ----------------------

# --- SETTINGS PARSER ---
def find_settings_path() -> Path | None:
    """Find settings.txt using the existing script-dir then cwd lookup order."""
    script_settings_path = Path(__file__).parent.resolve() / "settings.txt"
    if script_settings_path.exists():
        return script_settings_path

    cwd_settings_path = Path.cwd() / "settings.txt"
    if cwd_settings_path.exists():
        return cwd_settings_path

    return None


def load_script_settings() -> dict[str, str]:
    """Read settings.txt once into a case-insensitive key/value dict."""
    settings_path = find_settings_path()
    if settings_path is None:
        return {}

    settings = {}
    try:
        with open(settings_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith(("#", ";", "[")) or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                settings[key.strip().lower()] = value.strip()
    except Exception:
        pass

    return settings


def get_script_setting(settings: dict[str, str], key_name: str, default_value: str) -> str:
    """Return a setting from a dict loaded by load_script_settings()."""
    return settings.get(key_name.lower(), default_value)


script_settings = load_script_settings()

# video-input\template.vpy, when it exists, is the filter chain: the settings.txt
# crop/downscale/dehalo/denoise/deband keys below stop being read. It is looked
# up here, before those keys are validated, so a settings.txt that would be
# refused outright (both dehalo switches on, say) cannot block a run that is
# never going to use it.
vpy_template_file = vpy_template.find_template(args.input)

# Load Settings
s_crop_mode = get_script_setting(script_settings, "crop", "auto")
s_crop_top = get_script_setting(script_settings, "top", "0")
s_crop_bottom = get_script_setting(script_settings, "bottom", "0")
s_crop_left = get_script_setting(script_settings, "left", "0")
s_crop_right = get_script_setting(script_settings, "right", "0")
s_downscale = get_script_setting(script_settings, "downscale", "False")
s_target_res = get_script_setting(script_settings, "target_resolution", "1920x1080")
s_kernel = get_script_setting(script_settings, "kernel_type", "Hermite")
s_dehalo = get_script_setting(script_settings, "dehalo", "False")
s_dehalo_strength = get_script_setting(script_settings, "dehalo_strength", "5")
s_dehalo_rmode = get_script_setting(script_settings, "dehalo_rmode", "17")
s_dehalo_hot = get_script_setting(script_settings, "dehalo_hot", "False")
s_dehalo_smode = get_script_setting(script_settings, "dehalo_smode", "False")
s_dehalo_edgemask = get_script_setting(script_settings, "dehalo_edgemask", "Prewitt")
s_fine_dehalo = get_script_setting(script_settings, "fine_dehalo", "False")
s_fine_dehalo_rx = get_script_setting(script_settings, "fine_dehalo_rx", "2")
s_fine_dehalo_ry = get_script_setting(script_settings, "fine_dehalo_ry", "2")
s_fine_dehalo_darkstr = get_script_setting(script_settings, "fine_dehalo_darkstr", "0.0")
s_fine_dehalo_brightstr = get_script_setting(script_settings, "fine_dehalo_brightstr", "1.0")
s_fine_dehalo_lowsens = get_script_setting(script_settings, "fine_dehalo_lowsens", "50")
s_fine_dehalo_highsens = get_script_setting(script_settings, "fine_dehalo_highsens", "50")
s_fine_dehalo_ss = get_script_setting(script_settings, "fine_dehalo_ss", "1.5")
s_fine_dehalo_contra = get_script_setting(script_settings, "fine_dehalo_contra", "0.0")
s_fine_dehalo_edgemask = get_script_setting(script_settings, "fine_dehalo_edgemask", "Robinson3")
s_denoise = get_script_setting(script_settings, "denoise", "False")
s_denoise_setting = get_script_setting(script_settings, "denoise_setting", "")
s_deband = get_script_setting(script_settings, "deband", "False")
s_deband_setting = get_script_setting(script_settings, "deband_setting", "")

# Normalize boolean string
do_downscale_bool = s_downscale.lower() == "true"
do_dehalo_bool = s_dehalo.lower() == "true"
do_fine_dehalo_bool = s_fine_dehalo.lower() == "true"
do_denoise_bool = s_denoise.lower() == "true"
do_deband_bool = s_deband.lower() == "true"

# Dehalo values are written straight into the generated .vpy, so they are
# validated here instead of trusting whatever is in settings.txt.
dehalo_setting_warnings: list[str] = []

# AWarp rejects a warp depth outside -128..127. edge_cleaner adds 4 to strength
# when smode is on, so the usable ceiling drops by 4 in that mode.
DEHALO_STRENGTH_MAX = 127
DEHALO_SMODE_STRENGTH_MAX = DEHALO_STRENGTH_MAX - 4

# vsrgtools maps 1-24 onto the zsmooth Repair plugin and 26-28 onto expression
# fallbacks; 0 is a no-op and 25 is unimplemented.
DEHALO_RMODES = frozenset(list(range(0, 25)) + [26, 27, 28])

# Keys from the old dehalo_alpha implementation. They are silently ignored now,
# so say so rather than letting a stale settings.txt look like it still applies.
DEHALO_LEGACY_KEYS = ("dehalo_rx", "dehalo_ry", "dehalo_brightstr", "dehalo_darkstr",
                      "dehalo_lowsens", "dehalo_highsens", "dehalo_ss")


def _read_dehalo_int(value: str, key_name: str, default_value: int,
                     minimum: int | None = None, maximum: int | None = None) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        dehalo_setting_warnings.append(f"Invalid {key_name}={value!r}; using {default_value}.")
        return default_value
    if minimum is not None and parsed < minimum:
        dehalo_setting_warnings.append(f"{key_name}={parsed} is below {minimum}; using {minimum}.")
        return minimum
    if maximum is not None and parsed > maximum:
        dehalo_setting_warnings.append(f"{key_name}={parsed} is above {maximum}; using {maximum}.")
        return maximum
    return parsed


def _read_dehalo_bool(value: str, key_name: str, default_value: bool = False) -> bool:
    raw = str(value).strip().lower()
    if raw in ("1", "true", "yes", "y", "on"):
        return True
    if raw in ("0", "false", "no", "n", "off"):
        return False
    dehalo_setting_warnings.append(f"Invalid {key_name}={value!r}; using {default_value}.")
    return default_value


def _read_dehalo_rmode(value: str) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        dehalo_setting_warnings.append(f"Invalid dehalo_rmode={value!r}; using 17.")
        return 17
    if parsed not in DEHALO_RMODES:
        dehalo_setting_warnings.append(f"dehalo_rmode={parsed} is not a supported repair mode; using 17.")
        return 17
    return parsed


def _read_dehalo_edgemask(value: str) -> str:
    """Validate dehalo_edgemask.

    The name is written into the generated .vpy as a string literal, so it is
    restricted to a bare identifier. Whether that identifier is a real
    vsmasktools edge detector is resolved inside the .vpy, which keeps this
    working across vsmasktools versions that add or rename detectors.
    """
    name = str(value).strip()
    if not name:
        return "Prewitt"
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name):
        dehalo_setting_warnings.append(f"Invalid dehalo_edgemask={value!r}; using Prewitt.")
        return "Prewitt"
    return name


dehalo_smode = _read_dehalo_bool(s_dehalo_smode, "dehalo_smode", False)
dehalo_strength = _read_dehalo_int(
    s_dehalo_strength, "dehalo_strength", 5, minimum=0,
    maximum=DEHALO_SMODE_STRENGTH_MAX if dehalo_smode else DEHALO_STRENGTH_MAX,
)
dehalo_rmode = _read_dehalo_rmode(s_dehalo_rmode)
dehalo_hot = _read_dehalo_bool(s_dehalo_hot, "dehalo_hot", False)
dehalo_edgemask = _read_dehalo_edgemask(s_dehalo_edgemask)

_stale_dehalo_keys = [key for key in DEHALO_LEGACY_KEYS if key in script_settings]
if _stale_dehalo_keys:
    dehalo_setting_warnings.append(
        f"settings.txt [dehalo] still has {', '.join(_stale_dehalo_keys)}; "
        "these are ignored since dehalo now uses edge_cleaner."
    )

# edge_cleaner and fine_dehalo attack the same artifact by different means.
# Chaining them hands the second one edges the first already altered, which is
# what destroys thin line art, so this stops the run rather than silently
# picking a winner. Checked here, at settings-parse time, so nothing is encoded
# before the user hears about it.
if do_dehalo_bool and do_fine_dehalo_bool and vpy_template_file is None:
    print("ERROR: dehalo=True and fine_dehalo=True are mutually exclusive.")
    print("       Set one of them to False in settings.txt.")
    print("       [dehalo] warps edges inward (edge_cleaner); [fine_dehalo] blurs")
    print("       the halo behind an edge mask. Running both would dehalo an")
    print("       already dehaloed clip and eat thin lines.")
    raise SystemExit(1)

# rx/ry are Morpho.expand iteration counts, not pixel radii. 0 builds an empty
# mask and dehalos nothing; much past 8 the mask swallows the line it should be
# protecting.
FINE_DEHALO_RADIUS_MIN = 1
FINE_DEHALO_RADIUS_MAX = 8

# dehalo_alpha raises CustomIndexError unless lowsens/highsens are both within
# 0-100, with -1 on BOTH as its documented way to switch that mask off. One of
# the pair set to -1 on its own is the error case, so it is caught here.
FINE_DEHALO_SENS_OFF = -1.0

# fine_dehalo's pre_ss supersampler runs through vsaa.NNEDI3, which needs the
# znedi3 or sneedif plugin. Neither ships in VapourSynth/vs-plugins, so pre_ss
# is deliberately not exposed in settings.txt and the .vpy never sets it.


def _read_fine_dehalo_float(value: str, key_name: str, default_value: float,
                            minimum: float | None = None, maximum: float | None = None) -> float:
    try:
        parsed = float(str(value).strip())
    except (TypeError, ValueError):
        dehalo_setting_warnings.append(f"Invalid {key_name}={value!r}; using {default_value}.")
        return default_value
    if minimum is not None and parsed < minimum:
        dehalo_setting_warnings.append(f"{key_name}={parsed} is below {minimum}; using {minimum}.")
        return minimum
    if maximum is not None and parsed > maximum:
        dehalo_setting_warnings.append(f"{key_name}={parsed} is above {maximum}; using {maximum}.")
        return maximum
    return parsed


def _read_fine_dehalo_sens() -> tuple[float, float]:
    """Validate the lowsens/highsens pair.

    They are read together because -1 is only legal when both carry it; a lone
    -1 would reach dehalo_alpha and raise there, mid-encode.
    """
    def as_float(raw: str, key_name: str) -> float:
        try:
            return float(str(raw).strip())
        except (TypeError, ValueError):
            dehalo_setting_warnings.append(f"Invalid {key_name}={raw!r}; using 50.")
            return 50.0

    low = as_float(s_fine_dehalo_lowsens, "fine_dehalo_lowsens")
    high = as_float(s_fine_dehalo_highsens, "fine_dehalo_highsens")

    low_off = low == FINE_DEHALO_SENS_OFF
    high_off = high == FINE_DEHALO_SENS_OFF
    if low_off and high_off:
        return FINE_DEHALO_SENS_OFF, FINE_DEHALO_SENS_OFF
    if low_off or high_off:
        dehalo_setting_warnings.append(
            "fine_dehalo_lowsens/fine_dehalo_highsens only accept -1 when both are -1; "
            "using 50 for both."
        )
        return 50.0, 50.0

    return (
        _read_fine_dehalo_float(low, "fine_dehalo_lowsens", 50.0, minimum=0.0, maximum=100.0),
        _read_fine_dehalo_float(high, "fine_dehalo_highsens", 50.0, minimum=0.0, maximum=100.0),
    )


def _read_fine_dehalo_edgemask(value: str) -> str:
    """Validate fine_dehalo_edgemask; same identifier rule as dehalo_edgemask."""
    name = str(value).strip()
    if not name:
        return "Robinson3"
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name):
        dehalo_setting_warnings.append(f"Invalid fine_dehalo_edgemask={value!r}; using Robinson3.")
        return "Robinson3"
    return name


fine_dehalo_rx = _read_dehalo_int(
    s_fine_dehalo_rx, "fine_dehalo_rx", 2,
    minimum=FINE_DEHALO_RADIUS_MIN, maximum=FINE_DEHALO_RADIUS_MAX,
)
fine_dehalo_ry = _read_dehalo_int(
    s_fine_dehalo_ry, "fine_dehalo_ry", 2,
    minimum=FINE_DEHALO_RADIUS_MIN, maximum=FINE_DEHALO_RADIUS_MAX,
)
fine_dehalo_darkstr = _read_fine_dehalo_float(
    s_fine_dehalo_darkstr, "fine_dehalo_darkstr", 0.0, minimum=0.0, maximum=1.0)
fine_dehalo_brightstr = _read_fine_dehalo_float(
    s_fine_dehalo_brightstr, "fine_dehalo_brightstr", 1.0, minimum=0.0, maximum=1.0)
fine_dehalo_lowsens, fine_dehalo_highsens = _read_fine_dehalo_sens()
fine_dehalo_ss = _read_fine_dehalo_float(
    s_fine_dehalo_ss, "fine_dehalo_ss", 1.5, minimum=1.0, maximum=4.0)
fine_dehalo_contra = _read_fine_dehalo_float(
    s_fine_dehalo_contra, "fine_dehalo_contra", 0.0, minimum=0.0, maximum=4.0)
fine_dehalo_edgemask = _read_fine_dehalo_edgemask(s_fine_dehalo_edgemask)
# -----------------------

stage = int(args.stage)
src_file = Path(args.input).resolve()
if platform.system() == 'Windows':
    src_file = type(src_file)(r"\\?" + rf"\{src_file}")
file_ext = src_file.suffix
output_dir = src_file.parent

if args.temp is not None:
    tmp_dir = Path(args.temp).resolve()
    if platform.system() == 'Windows':
        tmp_dir = type(tmp_dir)(r"\\?" + rf"\{tmp_dir}")
else:
    tmp_dir = output_dir / src_file.stem

# Files
vpy_file = tmp_dir / f"{src_file.stem}.vpy"
# ffms2 unless tools\source-filter-override.txt says otherwise. The two filters
# name their index differently, so the cache path follows the choice.
active_source_filter = source_filter.resolve()
cache_file = source_filter.cache_path(tmp_dir, src_file.stem, active_source_filter)
# Fast pass is now MKV
fast_output_file = tmp_dir / f"{src_file.stem}_fastpass.mkv"
# Final output
# Av1an is pointed straight at this path, so the finished video is written where
# it is wanted and nothing has to move it afterwards. The dispatchers pass
# --output temp\<name>-av1.mkv, which is where tagging and muxing already run;
# run on its own the script keeps writing next to the source as it always did.
if args.output:
    final_output_file = Path(args.output).resolve()
    if platform.system() == 'Windows':
        final_output_file = type(final_output_file)(r"\\?" + rf"\{final_output_file}")
else:
    final_output_file = output_dir / f"{src_file.stem}-av1.mkv"


def plain_path(path):
    """A path without the \\\\?\\ prefix, for handing to external tools.

    The prefix keeps Python's own file calls safe on long paths, but Av1an
    records whatever it is given into its options.json and passes that on to
    mkvmerge, so the plain spelling is the one that travels well.
    """
    text = str(path)
    return text[4:] if text.startswith("\\\\?\\") else text

ssimu2_log_file = tmp_dir / f"{src_file.stem}_ssimu2.log"
xpsnr_log_file = tmp_dir / f"{src_file.stem}_xpsnr.log"
scenes_file = tmp_dir / f"{src_file.stem}_scenes.json"
stage_file = tmp_dir / f"{src_file.stem}_stage.txt"
stage_resume = 0

# Handle external scenes path
external_scenes_file = None
if args.scenes:
    external_scenes_file = Path(args.scenes).resolve()
    if not external_scenes_file.exists():
        print(f"Warning: External scenes file {external_scenes_file} not found. Will fallback to internal detection.")
        external_scenes_file = None

# Handle zones override
zones_override_path = None
if args.zones:
    zones_override_path = Path(args.zones).resolve()

# Speed Mapping
speed_map = {
    "slower": "2",
    "slow": "4",
    "medium": "6",
    "fast": "8",
    "faster": "10",
    "0": "0"
}

fast_speed = str(args.fast_speed)
if fast_speed.lower() in speed_map:
    fast_speed = speed_map[fast_speed.lower()]

final_speed = str(args.final_speed)
if final_speed.lower() in speed_map:
    final_speed = speed_map[final_speed.lower()]

quality = args.quality
aggressive = args.aggressive
unshackle = args.unshackle
fast_params = args.fast_params if args.fast_params is not None else ""
final_params = args.final_params if args.final_params is not None else ""

# Handle ssimu2 default
if args.ssimu2 is None:
    ssimu2 = ""
else:
    ssimu2 = args.ssimu2.lower()

ssimu2_cpu_workers = int(args.ssimu2_cpu_workers)
verbose = args.verbose
resume = args.resume
no_boosting = args.no_boosting
convert_yuv420p10 = args.convert_to_YUV420P10
tonemap_bool = str(args.tonemap).strip().lower() in ("1", "true", "yes", "y", "on")

# Worker Logic
av1an_workers_arg = args.workers
workers_specified = True
if av1an_workers_arg is None:
    # Default behavior if not specified
    final_pass_workers = "1"
    fast_pass_workers = "2"
    workers_specified = False
else:
    # User specified behavior
    final_pass_workers = av1an_workers_arg
    fast_pass_workers = av1an_workers_arg

photon_noise_val = int(args.photon_noise)


def append_encoder_photon_noise(params: list[str]) -> list[str]:
    """Keep --photon-noise as an SVT-AV1 encoder setting, not Av1an JSON photon_noise."""
    if photon_noise_val > 0 and "--photon-noise" not in params:
        return params + ["--photon-noise", str(photon_noise_val)]
    return params


if args.debug:
    print("=" * 54)
    print("SYSTEM INFORMATION")
    print("=" * 54)
    print(f"System: {platform.platform()}")
    print(f"Python Version: {sys.version}")
    print("=" * 54)
    print("Check for Tools")
    print("=" * 54)
    print(f"Av1an Path:   {av1an_exe}")
    print(f"Av1an Exists: {av1an_exe.exists()}")
    print(f"FFVship NVIDIA Path: {ffvship_nvidia_exe}")
    print(f"FFVship NVIDIA Exists: {ffvship_nvidia_exe.exists()}")
    print(f"FFVship AMD Path: {ffvship_amd_exe}")
    print(f"FFVship AMD Exists: {ffvship_amd_exe.exists()}")
    print(f"FFVship Vulkan Path: {ffvship_vulkan_exe}")
    print(f"FFVship Vulkan Exists: {ffvship_vulkan_exe.exists()}")
    print(f"Cropdetect Path: {cropdetect_script}")
    print(f"Cropdetect Exists: {cropdetect_script.exists()}")
    raise SystemExit(1)

if not os.path.exists(src_file):
    print("The source input doesn't exist. Double-check the provided path.")
    raise SystemExit(1)

if "--preset" in fast_params.split():
    print("Please use --fast-speed argument instead of putting --preset in fast-params")
    raise SystemExit(1)

if "--crf" in fast_params:
    index = fast_params.index("--crf")
    try:
        quality = float(fast_params[index+6:index+11])
    except:
        try:
            quality = float(fast_params[index+6:index+10])
        except:
            try:
                quality = float(fast_params[index+6:index+8])
            except:
                print("CRF must have 0, 1 or 2 decimals.")
                raise SystemExit(1)
else:
    if quality not in ["low", "medium", "high", "breeze"]:
        try:
            float(quality)
        except ValueError:
            print("The quality preset must be either low, medium, high, breeze or a number.")
            raise SystemExit(1)

if stage != 0 and resume:
    print("Resume will auto-resume from the last (un)completed stage. You cannot provide both stage and resume.")
    raise SystemExit(1)

if os.path.exists(tmp_dir):
    if resume and os.path.exists(stage_file):
        with open(stage_file, "r") as file:
            lines = file.readlines()
            stage_resume = int(lines[0].strip())
            if stage_resume == 5:
                print('Final encode already finished. Nothing to resume.')
                finish_cleanly(0)
            else:
                print(f'Resuming from stage {stage_resume}.')

    if not resume and stage in [0, 1]:
        shutil.rmtree(tmp_dir)

if not os.path.exists(tmp_dir):
    os.makedirs(tmp_dir)

core.max_cache_size = 1024
console = Console()

# ==========================================================================
# Simple-mode (non --verbose) progress display
#
# Without --verbose, the noisy phases (fast pass, metric measuring, final
# pass) are shown as Auto-Boost-Essential style progress bars with a short
# beginner-friendly explanation underneath. The explanation for a phase
# disappears as soon as that phase finishes. With --verbose, everything is
# displayed the way it always has been.
# ==========================================================================

FAST_PASS_EXPLANATION = (
    "Creating a quick, low-effort preview encode of your video. This is not your final\n"
    "file - it's a fast draft used to measure how well each scene compresses."
)
SSIMU2_EXPLANATION = (
    "Comparing the preview encode against your original video, scene by scene, using the\n"
    "SSIMULACRA2 visual quality metric. Scenes that lost too much quality will be boosted\n"
    "in the final encode."
)
XPSNR_EXPLANATION = (
    "Comparing the preview encode against your original video, scene by scene, using the\n"
    "XPSNR visual quality metric. Scenes that lost too much quality will be boosted in\n"
    "the final encode."
)
FINAL_PASS_EXPLANATION = (
    "Encoding your final video. Each scene gets its own fine-tuned crf level based\n"
    "on the measurements that will influence bitate to maintain consistent quality."
)

SIMPLE_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")

# All simple-mode bars pad their description to this width so every progress
# bar in the workflow starts at the same column and stays aligned.
SIMPLE_DESC_WIDTH = 42


def simple_description(text):
    return "[green]" + text.ljust(SIMPLE_DESC_WIDTH)


class SimpleFPSColumn(ProgressColumn):
    """fps readout on the right side of the bar (default color), fed via
    task.fields['fps']. Renders fixed-width so bars stay aligned."""

    def render(self, task):
        fps = task.fields.get("fps")
        if fps is None:
            return Text(" " * 12)
        return Text(f"{fps:>8.2f} fps")


class TaskSpeedFPSColumn(ProgressColumn):
    """fps derived from the task's measured speed (like vstools' FPSColumn),
    rendered in the default color and fixed width so bars stay aligned."""

    def render(self, task):
        speed = task.finished_speed or task.speed
        if speed is None:
            return Text(" " * 12)
        return Text(f"{speed:>8.2f} fps")


class PlainTimeElapsedColumn(TimeElapsedColumn):
    """Elapsed time in the default color instead of rich's cyan."""

    def render(self, task):
        text = super().render(task)
        text.style = ""
        return text


class PlainTimeRemainingColumn(TimeRemainingColumn):
    """Remaining time in the default color instead of rich's cyan."""

    def render(self, task):
        text = super().render(task)
        text.style = ""
        return text


class PhaseRenderable:
    """A progress display plus an explanation line that can be hidden."""

    def __init__(self, progress, explanation):
        self.progress = progress
        self.explanation = explanation
        self.show_explanation = explanation is not None

    def __rich__(self):
        if self.show_explanation:
            return Group(self.progress, Text(self.explanation, style="dim"))
        return self.progress


def essential_style_progress(fps_column=None, indeterminate=False):
    """Progress bar with the same appearance Auto-Boost-Essential uses.
    Percentage, fps and time readouts use the default (white) color, and the
    fps column is fixed-width so all bars in the workflow align."""
    columns = [
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
    ]
    if indeterminate:
        columns.append(PlainTimeElapsedColumn())
    else:
        columns.append("{task.percentage:>3.0f}%")
        columns.append(fps_column if fps_column is not None else TaskSpeedFPSColumn())
        columns.extend([PlainTimeElapsedColumn(), PlainTimeRemainingColumn()])
    return Progress(*columns, console=console)


@contextmanager
def metric_progress_display(description, total, explanation, indeterminate=False):
    """Progress bar wrapper for the metric measuring phase.

    Verbose mode keeps the original minimal bars; default mode shows an
    Essential-style bar with the beginner explanation underneath, hidden
    once the phase completes."""
    if verbose:
        if indeterminate:
            with Progress(SpinnerColumn(), BarColumn(), TimeElapsedColumn(), console=console) as p:
                yield p, p.add_task(description, total=total)
        else:
            with Progress(SpinnerColumn(), BarColumn(), FPSColumn(), console=console) as p:
                yield p, p.add_task(description, total=total)
        return
    p = essential_style_progress(fps_column=TaskSpeedFPSColumn(), indeterminate=indeterminate)
    task = p.add_task(simple_description(description), total=total)
    renderable = PhaseRenderable(p, explanation)
    with Live(renderable, console=console, refresh_per_second=8) as live:
        completed_ok = False
        try:
            yield p, task
            completed_ok = True
        finally:
            if completed_ok:
                # Mark the task finished so the bar turns green like the
                # other simple-mode bars.
                finished_total = p.tasks[0].total
                if finished_total is None:
                    p.update(task, total=1, completed=1)
                else:
                    p.update(task, completed=finished_total)
            renderable.show_explanation = False
            live.refresh()


def stream_subprocess_lines(proc, on_line):
    """Read a subprocess's merged output, splitting on \\r and \\n so live
    carriage-return progress updates are seen as individual lines."""
    buf = b""
    stream = proc.stdout
    while True:
        chunk = stream.read1(4096) if hasattr(stream, "read1") else stream.read(1)
        if not chunk:
            break
        buf += chunk
        parts = re.split(b"[\r\n]", buf)
        buf = parts.pop()
        for raw_line in parts:
            if not raw_line:
                continue
            line = SIMPLE_ANSI_ESCAPE_RE.sub("", raw_line.decode("utf-8", "replace")).strip()
            if line:
                on_line(line)
    if buf:
        line = SIMPLE_ANSI_ESCAPE_RE.sub("", buf.decode("utf-8", "replace")).strip()
        if line:
            on_line(line)


def find_av1an_done_json(search_dir, min_mtime):
    """Find av1an's done.json in search_dir or one directory level below it,
    ignoring stale files from earlier runs (mtime older than min_mtime)."""
    candidates = []
    direct = os.path.join(str(search_dir), "done.json")
    if os.path.isfile(direct):
        candidates.append(direct)
    try:
        names = os.listdir(str(search_dir))
    except OSError:
        names = []
    for name in names:
        candidate = os.path.join(str(search_dir), name, "done.json")
        if os.path.isfile(candidate):
            candidates.append(candidate)

    def _mtime(path):
        try:
            return os.path.getmtime(path)
        except OSError:
            return 0.0

    fresh = [path for path in candidates if _mtime(path) >= min_mtime]
    if not fresh:
        return None
    return max(fresh, key=_mtime)


def read_av1an_done_json(path):
    """Return (total_frames or None, completed_frames) from av1an's done.json.
    Handles both old (int) and new (dict) per-chunk formats, and returns None
    if the file is mid-write or unreadable."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    total = data.get("frames")
    if not isinstance(total, (int, float)) or total <= 0:
        total = None
    completed = 0
    done = data.get("done", {})
    if isinstance(done, dict):
        for value in done.values():
            if isinstance(value, dict):
                try:
                    completed += int(value.get("frames", 0))
                except (TypeError, ValueError):
                    pass
            elif isinstance(value, (int, float)):
                completed += int(value)
    return total, completed


def run_av1an_with_simple_progress(av1an_cmd, run_cwd, description, explanation):
    """Run av1an behind a single Essential-style progress bar with a short
    explanation underneath. Progress advances as av1an chunks complete, read
    from av1an's done.json inside its temporary folder, with a rolling fps
    readout on the right side of the bar.

    Returns the av1an return code."""
    progress = essential_style_progress(fps_column=SimpleFPSColumn())
    task = progress.add_task(simple_description(description), total=None, fps=None)
    renderable = PhaseRenderable(progress, explanation)
    other_lines = collections.deque(maxlen=40)
    stop_event = threading.Event()
    started_wall_time = time.time()

    def watch_done_json():
        samples = collections.deque()
        done_path = None
        while not stop_event.wait(1.0):
            if done_path is None or not os.path.isfile(done_path):
                done_path = find_av1an_done_json(run_cwd, started_wall_time - 1.0)
                if done_path is None:
                    continue
            info = read_av1an_done_json(done_path)
            if info is None:
                continue
            total, completed = info
            now = time.monotonic()
            samples.append((now, completed))
            while samples and now - samples[0][0] > 120.0:
                samples.popleft()
            fps = None
            if len(samples) >= 2:
                elapsed = samples[-1][0] - samples[0][0]
                frames = samples[-1][1] - samples[0][1]
                if elapsed > 0 and frames > 0:
                    fps = frames / elapsed
            progress.update(task, total=total, completed=completed, fps=fps)

    proc = subprocess.Popen(av1an_cmd, cwd=run_cwd,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    reader = threading.Thread(target=stream_subprocess_lines,
                              args=(proc, other_lines.append), daemon=True)
    watcher = threading.Thread(target=watch_done_json, daemon=True)
    try:
        with Live(renderable, console=console, refresh_per_second=8) as live:
            reader.start()
            watcher.start()
            try:
                returncode = proc.wait()
                if returncode == 0:
                    final_total = progress.tasks[0].total
                    if final_total:
                        progress.update(task, completed=final_total)
                    else:
                        progress.update(task, total=1, completed=1)
            finally:
                stop_event.set()
                watcher.join(timeout=3)
                reader.join(timeout=3)
                renderable.show_explanation = False
                live.refresh()
    except KeyboardInterrupt:
        stop_event.set()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
        raise
    if returncode != 0 and other_lines:
        console.print(f"[red]Av1an output (last {len(other_lines)} lines):[/red]")
        for line in other_lines:
            console.print(f"  {line}")
    return returncode


def _read_crop_int(value: str, key_name: str) -> int:
    try:
        crop_value = int(value)
    except (TypeError, ValueError):
        console.print(f"[yellow]Invalid manual crop {key_name}={value!r}; using 0.[/yellow]")
        return 0
    if crop_value < 0:
        console.print(f"[yellow]Invalid manual crop {key_name}={crop_value}; using 0.[/yellow]")
        return 0
    if crop_value % 2 != 0:
        adjusted = crop_value - 1
        console.print(f"[yellow]Manual crop {key_name}={crop_value} is not mod2; using {adjusted}.[/yellow]")
        return adjusted
    return crop_value

def report_crop_status(mode: str, top: int, bottom: int, left: int, right: int) -> None:
    normalized_mode = mode.lower()
    active = any((top, bottom, left, right))
    if normalized_mode == "off":
        console.print("[blue]Crop:[/blue] off")
    elif active:
        console.print(
            f"[blue]Crop:[/blue] {normalized_mode} active "
            f"(top={top}, bottom={bottom}, left={left}, right={right})"
        )
    else:
        console.print(f"[blue]Crop:[/blue] {normalized_mode} selected, no crop values active")


def report_filter_status() -> None:
    # Printed whether or not any filter is on: it decides how every frame in the
    # chain is decoded, so it belongs with the rest of the .vpy summary.
    console.print(f"[blue]Source filter:[/blue] {source_filter.describe(active_source_filter)}")
    active_filters = []
    if tonemap_bool:
        active_filters.append(
            tonemap_backend.describe_backend(tonemap_backend_name, tonemap_backend_reason)
        )
    if do_downscale_bool:
        active_filters.append(f"downscale: target_resolution={s_target_res}, kernel_type={s_kernel}")
    if do_dehalo_bool:
        active_filters.append(
            f"dehalo (edge_cleaner): strength={dehalo_strength}, rmode={dehalo_rmode}, "
            f"hot={dehalo_hot}, smode={dehalo_smode}, edgemask={dehalo_edgemask}"
        )
        for warning in dehalo_setting_warnings:
            console.print(f"[yellow]{warning}[/yellow]")
    if do_fine_dehalo_bool:
        active_filters.append(
            f"fine_dehalo: rx={fine_dehalo_rx}, ry={fine_dehalo_ry}, "
            f"darkstr={fine_dehalo_darkstr}, brightstr={fine_dehalo_brightstr}, "
            f"lowsens={fine_dehalo_lowsens}, highsens={fine_dehalo_highsens}, "
            f"ss={fine_dehalo_ss}, contra={fine_dehalo_contra}, edgemask={fine_dehalo_edgemask}"
        )
        # dehalo and fine_dehalo cannot both be on, so this list is never printed twice.
        for warning in dehalo_setting_warnings:
            console.print(f"[yellow]{warning}[/yellow]")
    if do_denoise_bool:
        active_filters.append(f"denoise: denoise_setting={s_denoise_setting or 'enabled'}")
    if do_deband_bool:
        active_filters.append(f"deband: deband_setting={s_deband_setting or 'enabled'}")

    if not active_filters:
        console.print("[blue]Filters active:[/blue] none")
        return

    for filter_status in active_filters:
        console.print(f"[blue]Filter active:[/blue] {filter_status}")

def parse_crop_values_from_vpy(vpy_path: Path) -> tuple[int, int, int, int] | None:
    if not vpy_path.exists():
        return None
    try:
        text = vpy_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None
    match = re.search(r"std\.Crop\(([^)]*)\)", text)
    if not match:
        return 0, 0, 0, 0
    values = {"top": 0, "bottom": 0, "left": 0, "right": 0}
    for key, value in re.findall(r"(top|bottom|left|right)\s*=\s*(-?\d+)", match.group(1)):
        values[key] = int(value)
    return values["top"], values["bottom"], values["left"], values["right"]

def detect_crop_values(source_path: Path) -> tuple[int, int, int, int]:
    """
    Uses external tools/cropdetect.py to detect crop values.
    Saves the CSV to the source directory (next to the input file/bat file).
    Retries with aggressive mode if standard mode yields 0 crop.
    """
    console.print("Detecting crop values via cropdetect.py...")
    # Just show filename, not the scary long path
    console.print(f"[cyan]{source_path.name}[/cyan]")

    if not cropdetect_script.exists():
        console.print(f"[red]cropdetect.py not found at {cropdetect_script}. Proceeding with 0 crop.[/red]")
        return 0, 0, 0, 0

    csv_output = source_path.parent / f"{source_path.stem}_crop.csv"

    def run_crop_process(aggressive_mode: bool) -> bool:
        # We use --progress-mode to get machine-readable updates
        cmd = [
            sys.executable,
            str(cropdetect_script),
            str(source_path),
            "--out", str(csv_output),
            "--samples", "3",
            "--progress-mode"
        ]

        mode_label = "Standard"
        if aggressive_mode:
            cmd.append("--aggressive")
            mode_label = "Aggressive"

        # Run process and stream output to update progress bar
        try:
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                "[progress.percentage]{task.percentage:>3.0f}%",
                TimeRemainingColumn(),
                console=console
            ) as progress:
                task = progress.add_task(f"[green]Sampling ({mode_label})...", total=100)

                # Popen allows real-time output reading
                with subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding='utf-8'
                ) as proc:
                    for line in proc.stdout:
                        line = line.strip()
                        if line.startswith("PROGRESS:"):
                            try:
                                # Parse PROGRESS:X
                                percent = int(line.split(":")[1])
                                progress.update(task, completed=percent)
                            except (IndexError, ValueError):
                                pass
                        elif verbose:
                            # Only show other lines if verbose is on
                            # This keeps the main UI clean
                            console.print(f"[dim]{line}[/dim]")

                    # Check return code
                    if proc.wait() != 0:
                        console.print(f"[red]Crop detection ({mode_label}) finished with errors.[/red]")
                        return False
            return True

        except Exception as e:
            console.print(f"[red]Error during crop detection execution: {e}[/red]")
            return False

    def parse_csv_result() -> tuple[int, int, int, int, str]:
        if not csv_output.exists():
            console.print(f"[yellow]Crop CSV not found after execution.[/yellow]")
            return 0, 0, 0, 0, ""

        # Read the CSV to get final values
        try:
            with open(csv_output, newline='', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                rows = list(reader)
                if not rows:
                    return 0, 0, 0, 0, ""

                row = rows[0]
                orig_w = int(row.get('width', '0'))
                orig_h = int(row['height'])
                c_w = int(row.get('crop_w', orig_w))
                c_h = int(row['crop_h'])
                c_x = int(row.get('crop_x', '0'))
                c_y = int(row['crop_y'])

                crop_top = c_y
                crop_bottom = orig_h - (c_y + c_h)
                crop_left = c_x
                crop_right = orig_w - (c_x + c_w) if orig_w else 0

                # Ensure mod2
                if crop_top % 2 != 0: crop_top -= 1
                if crop_bottom % 2 != 0: crop_bottom -= 1
                if crop_left % 2 != 0: crop_left -= 1
                if crop_right % 2 != 0: crop_right -= 1

                return crop_top, crop_bottom, crop_left, crop_right, row.get('crop', '')

        except Exception as e:
            console.print(f"[red]Failed to parse crop CSV: {e}[/red]")
            return 0, 0, 0, 0, ""

    # Attempt 1: Standard
    if not run_crop_process(aggressive_mode=False):
        return 0, 0, 0, 0

    t, b, l, r, crop_str = parse_csv_result()

    # Attempt 2: Aggressive if Standard failed (0 on all sides)
    if t == 0 and b == 0 and l == 0 and r == 0:
        console.print("[yellow]No crop found. Retrying with --aggressive mode...[/yellow]")
        if run_crop_process(aggressive_mode=True):
            t, b, l, r, crop_str = parse_csv_result()

    if t != 0 or b != 0 or l != 0 or r != 0:
        console.print(f"[bold green]Crop Found:[/bold green] Top={t}, Bottom={b}, Left={l}, Right={r} [dim](Based on {crop_str})[/dim]")
    else:
        console.print("[yellow]No crop detected (0 on all sides).[/yellow]")

    return t, b, l, r

# Generate VPY file

# Which tonemap implementation lands in the .vpy. The GPU is only probed when
# there is something to tonemap, and the probe result is cached for the rest of
# the run. See tools/tonemap_backend.py: vs-placebo's Tonemap hands back black
# frames on Intel GPUs, so those fall back to the CPU implementation.
tonemap_backend_name = tonemap_backend.PLACEBO
tonemap_backend_reason = ""
if tonemap_bool:
    tonemap_backend_name, tonemap_backend_reason = tonemap_backend.resolve_backend(script_settings)

requested_crop_mode = s_crop_mode.strip().lower()
if not args.autocrop:
    crop_mode = "off"
elif requested_crop_mode in ("auto", "manual", "off"):
    crop_mode = requested_crop_mode
else:
    console.print(f"[yellow]Unknown crop mode {s_crop_mode!r}; using auto.[/yellow]")
    crop_mode = "auto"

# Marker written into the generated .vpy so a cached script is rebuilt whenever a
# settings.txt filter is toggled. Without it an existing .vpy is reused as-is and a
# newly enabled filter would silently never run. The dehalo parameters are part of
# the marker too, so retuning one of them also rebuilds the script.
if do_dehalo_bool:
    _dehalo_state = (
        f"edge_cleaner(strength={dehalo_strength},rmode={dehalo_rmode},hot={dehalo_hot},"
        f"smode={dehalo_smode},edgemask={dehalo_edgemask})"
    )
else:
    _dehalo_state = "False"
if do_fine_dehalo_bool:
    _fine_dehalo_state = (
        f"fine_dehalo(rx={fine_dehalo_rx},ry={fine_dehalo_ry},darkstr={fine_dehalo_darkstr},"
        f"brightstr={fine_dehalo_brightstr},lowsens={fine_dehalo_lowsens},"
        f"highsens={fine_dehalo_highsens},ss={fine_dehalo_ss},contra={fine_dehalo_contra},"
        f"edgemask={fine_dehalo_edgemask})"
    )
else:
    _fine_dehalo_state = "False"
filter_state_marker = (
    f"# Filter state: dehalo={_dehalo_state}, fine_dehalo={_fine_dehalo_state}, "
    f"denoise={do_denoise_bool}, deband={do_deband_bool}"
)

# video-input\template.vpy short-circuits all of this: the user's script is the
# filter chain, and only the source path is filled in for this input. Nothing
# below reads settings.txt in that case - the crop in the template is the crop.
template_rendered = False
if vpy_template_file is not None:
    # --tonemap and --convert-to-YUV420P10 are steps the generated script builds
    # into the chain, and a template IS the chain, so they have nowhere to go.
    # Tonemapping stops the run rather than quietly encoding an HDR source as if
    # it were SDR and wasting the whole encode.
    if tonemap_bool:
        _template_name = os.path.basename(vpy_template_file)
        console.print(f"[red]ERROR: this .bat asks for --tonemap, but {_template_name} is doing[/red]")
        console.print("[red]       the filtering and a template cannot be tonemapped from outside.[/red]")
        console.print(f"[red]       Add the tonemap to {_template_name} - it was written with the[/red]")
        console.print("[red]       lines you need in a comment near the bottom - or delete the[/red]")
        console.print("[red]       template to go back to the generated script.[/red]")
        raise SystemExit(1)
    if convert_yuv420p10:
        console.print(f"[yellow]--convert-to-YUV420P10 does not apply to "
                      f"{os.path.basename(vpy_template_file)}; the template's own "
                      f"finalize_clip() decides the output format.[/yellow]")
    # Same for --autocrop: detecting a crop per file would mean rewriting a
    # script the user edits by hand, so the template's std.Crop line wins.
    if args.autocrop:
        console.print(f"[yellow]--autocrop does not apply to "
                      f"{os.path.basename(vpy_template_file)}; the std.Crop line in the "
                      f"template is the crop.[/yellow]")

    try:
        _template_text = vpy_template.read_template(vpy_template_file)
        _template_stamp = vpy_template.render_stamp(_template_text, src_file)
        _template_crop = vpy_template.read_crop_values(_template_text)

        # Lossless intermediary mode: filter the source once into a lossless
        # file and read that for both passes, instead of running the rescale
        # for the fast pass and again for the final one. src_file is left
        # alone - the output name, the audio mux, scene detection and the zones
        # file are all still the original's. Returns None when the mode is off
        # or the intermediary could not be built, and then this filters on
        # every pass exactly as it always did.
        _intermediary = lossless_mode.prepare(src_file, _template_text)
        _template_script = None
        if _intermediary:
            _template_script, _template_stamp = lossless_mode.passthrough_render(_intermediary)

        _template_reused = False
        if os.path.exists(vpy_file):
            try:
                with open(vpy_file, "r", encoding="utf-8", errors="replace") as _f:
                    _template_reused = vpy_template.is_rendered_stamp(_f.read(), _template_stamp)
            except OSError:
                _template_reused = False

        if not _template_reused:
            if _template_script is None:
                _template_script = vpy_template.render(_template_text, src_file,
                                                       stamp=_template_stamp)
            vpy_template.write_rendered(vpy_file, _template_script)
    except vpy_template.TemplateError as e:
        console.print(f"[red]ERROR: {os.path.basename(vpy_template_file)} cannot be used: {e}[/red]")
        console.print("[red]       Fix the template, or delete it to go back to the generated script.[/red]")
        raise SystemExit(1)

    if _intermediary:
        console.print(f"[bold green]Filtering script:[/bold green] {vpy_template_file} "
                      f"[dim]- already applied on the way into "
                      f"{os.path.basename(str(_intermediary))}, which both passes read, "
                      f"so nothing is filtered twice[/dim]")
    else:
        console.print(f"[bold green]Filtering script:[/bold green] {vpy_template_file} "
                      "[dim](settings.txt filter settings are not used)[/dim]")
    if _template_crop is None:
        console.print("[bold green]Crop:[/bold green] no crop line in the template")
    elif any(_template_crop):
        console.print(f"[bold green]Crop:[/bold green] from the template "
                      f"(top={_template_crop[0]}, bottom={_template_crop[1]}, "
                      f"left={_template_crop[2]}, right={_template_crop[3]})")
    else:
        console.print("[bold green]Crop:[/bold green] from the template, nothing cropped")
    if _template_reused:
        console.print(f"[dim]Reusing rendered template: {vpy_file}[/dim]")
    else:
        console.print(f"[dim]Rendered template to: {vpy_file}[/dim]")

    template_rendered = True
    rebuild_vpy = False
else:
    rebuild_vpy = not os.path.exists(vpy_file)
    if not rebuild_vpy:
        try:
            with open(vpy_file, "r", encoding="utf-8", errors="replace") as _f:
                _existing_vpy_text = _f.read()
            if ("do_tonemap = True" in _existing_vpy_text) != tonemap_bool:
                console.print("[yellow]Existing VapourSynth script tonemap state differs from --tonemap; rebuilding.[/yellow]")
                rebuild_vpy = True
            elif tonemap_bool and tonemap_backend.vpy_backend_marker(tonemap_backend_name) not in _existing_vpy_text:
                # Written for the other tonemap backend, e.g. carried over from a
                # machine whose GPU could run the libplacebo path.
                console.print("[yellow]Existing VapourSynth script uses a different tonemap backend; rebuilding.[/yellow]")
                rebuild_vpy = True
            elif filter_state_marker not in _existing_vpy_text:
                console.print("[yellow]Existing VapourSynth script filter state differs from settings.txt; rebuilding.[/yellow]")
                rebuild_vpy = True
            elif "_plugin_dir" not in _existing_vpy_text:
                # Written before the vs-plugins fallback existed; on VapourSynth 78
                # it would fail to find ffms2.
                console.print("[yellow]Existing VapourSynth script predates the plugin fallback; rebuilding.[/yellow]")
                rebuild_vpy = True
            elif not source_filter.marker_matches(_existing_vpy_text, active_source_filter):
                # tools\source-filter-override.txt changed since this script was
                # written; without the rebuild the old source filter would stay.
                console.print("[yellow]Existing VapourSynth script uses a different source filter; rebuilding.[/yellow]")
                rebuild_vpy = True
        except Exception:
            rebuild_vpy = True

if rebuild_vpy:

    crop_top, crop_bottom, crop_left, crop_right = 0, 0, 0, 0
    if crop_mode == "auto":
        crop_top, crop_bottom, crop_left, crop_right = detect_crop_values(src_file)
    elif crop_mode == "manual":
        crop_top = _read_crop_int(s_crop_top, "top")
        crop_bottom = _read_crop_int(s_crop_bottom, "bottom")
        crop_left = _read_crop_int(s_crop_left, "left")
        crop_right = _read_crop_int(s_crop_right, "right")
    report_crop_status(crop_mode, crop_top, crop_bottom, crop_left, crop_right)
    report_filter_status()

    # settings.txt denoise/deband hooks. These are raw VapourSynth lines supplied by
    # the user, injected verbatim only when the matching switch is on.
    denoise_line = s_denoise_setting if do_denoise_bool and s_denoise_setting else ""
    deband_line = s_deband_setting if do_deband_bool and s_deband_setting else ""

    # Template
    vpy_source_template = """
from vstools import vs, core, initialize_clip, finalize_clip
try:
    from vsdenoise import DFTTest
except Exception:
    DFTTest = None
core.max_cache_size = 1024
{filter_state}

# VapourSynth 78 dropped the portable.vs autoload of the package's vs-plugins
# folder, so the source filter/DFTTest/placebo/vszip are loaded by hand when the
# core did not pick them up on its own. A no-op on installs that still autoload.
import os as _os
_plugin_dir = r"{plugin_dir}"
_have_plugins = hasattr(core, "ffms2") and hasattr(core, "{source_plugin}")
if _plugin_dir and not _have_plugins and _os.path.isdir(_plugin_dir):
    for _dll in sorted(_os.listdir(_plugin_dir)):
        if _dll.lower().endswith((".dll", ".so")):
            try:
                core.std.LoadPlugin(_os.path.join(_plugin_dir, _dll))
            except Exception:
                pass

# Load Source
{source_marker}
src = {source_call}

# Conversion
if {convert}:
    src = src.resize.Bicubic(format=vs.YUV420P10)

# Initialize (Fixes Placebo bitdepth error by ensuring 16-bit)
src = initialize_clip(src)

{tonemap_block}

# DENOISE (settings.txt [denoise]). Runs first: the dehalos below work off the
# denoised clip, so they are not sharpening grain back up.
{denoise_line}

# DEHALO (settings.txt [dehalo]; always runs after denoise)
do_dehalo = {dehalo}
if do_dehalo:
    from vsdehalo import edge_cleaner
    from vsmasktools import EdgeDetect, Prewitt
    # edge_cleaner warps edges via awarpsharp, which needs the AWarp plugin.
    if not hasattr(core, "awarp"):
        raise RuntimeError(
            "dehalo=True needs the AWarp plugin: put AWarp/libawarp.so in your VapourSynth plugin folder, "
            "or set dehalo=False in settings.txt."
        )
    try:
        dehalo_edgemask = EdgeDetect.ensure_obj("{dh_edgemask}")
    except Exception:
        print("[dehalo] Unknown dehalo_edgemask '{dh_edgemask}'; using Prewitt.")
        dehalo_edgemask = Prewitt
    src = edge_cleaner(
        src,
        strength={dh_strength},
        rmode={dh_rmode},
        hot={dh_hot},
        smode={dh_smode},
        edgemask=dehalo_edgemask,
    )

# FINE_DEHALO (settings.txt [fine_dehalo]; the alternative to [dehalo], also after denoise)
do_fine_dehalo = {fine_dehalo}
if do_fine_dehalo:
    from vsdehalo import fine_dehalo as _fine_dehalo
    from vsmasktools import EdgeDetect, Robinson3
    # Every mask stage and the dehalo itself go through vsexprtools.norm_expr,
    # which calls core.akarin.Expr. Unlike edge_cleaner this needs no AWarp.
    if not hasattr(core, "akarin"):
        raise RuntimeError(
            "fine_dehalo=True needs the akarin plugin: put libakarin.so in your VapourSynth plugin folder, "
            "or set fine_dehalo=False in settings.txt."
        )
    # box_blur inside the mask pipeline is vszip.BoxBlur.
    if not hasattr(core, "vszip"):
        raise RuntimeError(
            "fine_dehalo=True needs the vszip plugin: put libvszip.so in your VapourSynth plugin folder, "
            "or set fine_dehalo=False in settings.txt."
        )
    # ss=1 swaps supersampling for vsrgtools.repair, and contra>0 pulls in
    # contrasharpening_dehalo; both land on zsmooth.Repair. With ss>1 and no
    # contra, zsmooth is never touched.
    if ({fd_ss} == 1.0 or {fd_contra} > 0.0) and not hasattr(core, "zsmooth"):
        raise RuntimeError(
            "fine_dehalo with fine_dehalo_ss=1.0 or fine_dehalo_contra>0 needs the zsmooth plugin: "
            "put libzsmooth.so in your VapourSynth plugin folder, raise fine_dehalo_ss above 1.0 and set "
            "fine_dehalo_contra=0, or set fine_dehalo=False in settings.txt."
        )
    try:
        fine_dehalo_edgemask = EdgeDetect.ensure_obj("{fd_edgemask}")
    except Exception:
        print("[fine_dehalo] Unknown fine_dehalo_edgemask '{fd_edgemask}'; using Robinson3.")
        fine_dehalo_edgemask = Robinson3
    # pre_ss is left at its default of 1 on purpose: raising it routes through
    # vsaa.NNEDI3, which needs the znedi3 or sneedif plugin, and neither ships
    # with this package.
    src = _fine_dehalo(
        src,
        lowsens={fd_lowsens},
        highsens={fd_highsens},
        ss={fd_ss},
        darkstr={fd_darkstr},
        brightstr={fd_brightstr},
        rx={fd_rx},
        ry={fd_ry},
        edgemask=fine_dehalo_edgemask,
        contra={fd_contra},
    )

# DEBAND (settings.txt [deband]). Last of the four, so it grades the picture
# the encoder is actually handed.
{deband_line}

# 1. CROP
if {ct} > 0 or {cb} > 0 or {cl} > 0 or {cr} > 0:
    src = src.std.Crop(top={ct}, bottom={cb}, left={cl}, right={cr})

# 2. DOWNSCALE
should_downscale = {downscale}
target_res_str = "{target_res}"
user_kernel = "{kernel}"

if should_downscale:
    # Kernel Map
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

    # Parse Target Resolution
    target_w = 0
    target_h = 0

    if "x" in target_res_str.lower():
        try:
            w_str, h_str = target_res_str.lower().split("x")
            target_w = int(w_str)
            target_h = int(h_str)
        except:
            pass
    else:
        try:
            target_w = int(target_res_str)
        except:
            pass

    # Calculate Height
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

    # Write Windows VPY (Absolute paths okay here for local python)
    with open(vpy_file, 'w') as file:
        file.write(vpy_source_template.format(
            source_call=source_filter.source_call(active_source_filter, src_file, cache_file),
            source_marker=source_filter.marker(active_source_filter),
            source_plugin=source_filter.PLUGIN_ATTR[active_source_filter],
            plugin_dir=vs_plugin_dir(),
            ct=crop_top,
            cb=crop_bottom,
            cl=crop_left,
            cr=crop_right,
            downscale=str(do_downscale_bool),
            target_res=s_target_res,
            kernel=s_kernel,
            convert=convert_yuv420p10,
            tonemap_block=tonemap_backend.vpy_tonemap_block(tonemap_backend_name, tonemap_bool),
            dehalo=str(do_dehalo_bool),
            dh_strength=dehalo_strength,
            dh_rmode=dehalo_rmode,
            dh_hot=str(dehalo_hot),
            dh_smode=str(dehalo_smode),
            dh_edgemask=dehalo_edgemask,
            fine_dehalo=str(do_fine_dehalo_bool),
            fd_rx=fine_dehalo_rx,
            fd_ry=fine_dehalo_ry,
            fd_darkstr=fine_dehalo_darkstr,
            fd_brightstr=fine_dehalo_brightstr,
            fd_lowsens=fine_dehalo_lowsens,
            fd_highsens=fine_dehalo_highsens,
            fd_ss=fine_dehalo_ss,
            fd_contra=fine_dehalo_contra,
            fd_edgemask=fine_dehalo_edgemask,
            denoise_line=denoise_line,
            deband_line=deband_line,
            filter_state=filter_state_marker
        ))
elif not template_rendered:
    existing_crop_values = parse_crop_values_from_vpy(vpy_file)
    if existing_crop_values is None:
        existing_crop_values = (0, 0, 0, 0)
    report_crop_status(crop_mode, *existing_crop_values)
    report_filter_status()


def load_vpy_clip(script_path) -> vs.VideoNode:
    """Run a .vpy in this process and hand back the clip it produced.

    A generated script always calls its clip "src", with "final" for the
    finalized version, but video-input\\template.vpy is written by hand and
    neither name is guaranteed: the template is rendered verbatim apart from the
    source path, and the one thing every .vpy must do is set output 0. So the
    names are tried first, which covers the generated script and every template
    that keeps its shape, and output 0 is the fallback. template-preview.py
    reads the output node for the same reason.

    The namespace is a single dict used as both globals and locals, which is how
    a module runs. Two dicts give class-body scoping instead, where a function
    defined in the script cannot see the script's own top-level names - so a
    template with a helper in it dies here with a NameError that VSPipe, and
    therefore av1an, never raises.
    """
    script_path = Path(script_path)

    # Outputs live on the core and this runs several times per encode, so a
    # stale output 0 from an earlier call must not stand in for a missing one.
    vs.clear_outputs()

    namespace: dict = {"__name__": "__vpy__", "__file__": str(script_path)}
    source = script_path.read_text(encoding="utf-8", errors="replace")
    exec(compile(source, str(script_path), "exec"), namespace, namespace)

    clip = namespace.get("final") or namespace.get("src")
    if clip is None:
        output = vs.get_outputs().get(0)
        clip = getattr(output, "clip", output)
    if not isinstance(clip, vs.VideoNode):
        raise RuntimeError(
            f"{script_path.name} produced no video clip: it defines neither "
            f"'final' nor 'src', and sets nothing on output 0.")
    return clip


def get_file_info(vfile: Path, mode: str) -> tuple[list[int], bool, int, int, int, int, int]:
    if mode == "src":
        kf_file = tmp_dir / "info_src.txt"
    else:
        kf_file = tmp_dir / "info.txt"

    if kf_file.exists() and mode == "src" and (stage != 0 or resume):
        with open(kf_file, "r") as file:
            lines = file.readlines()
        # A run that died while this file was being written leaves a short or
        # garbled cache behind. Without this guard every later --resume dies
        # with an IndexError instead of just recalculating the scene info.
        try:
            if len(lines) < 6:
                raise ValueError(f"only {len(lines)} line(s)")
            print("Loading cached scene information...")
            return [int(line.strip()) for line in lines[1:-3]], lines[0].strip() == "True", int(lines[-5].strip()) , int(lines[-4].strip()) , int(lines[-3].strip()), int(lines[-2].strip()), int(lines[-1].strip())
        except (ValueError, IndexError) as e:
            console.print(f"[yellow]Ignoring corrupt scene cache {kf_file.name} ({e}); recalculating.[/yellow]")

    # Setup VPY environment to get src info from Windows VPY
    if mode == "src":
        src = load_vpy_clip(vpy_file)
    else:
        # For an encoded file (MKV/IVF) the clip is opened directly rather than
        # through the .vpy, with the same source filter the .vpy uses so the
        # frame counts on both sides of a comparison come from the same decoder.
        src = source_filter.open_source(core, active_source_filter, vfile)

    nframe = len(src)
    if mode == "len":
        return 0, 0, nframe, 0, 0, 0, 0

    fwidth, fheight = src.width, src.height
    hr = fwidth * fheight > 1920 * 1080
    with open(kf_file, "w") as file:
        file.write(str(hr)+"\n")

    ffpsnum = src.fps.numerator
    ffpsden = src.fps.denominator

    iframe_list = []

    # If external scenes are provided, use them instead of scanning
    if mode == "src" and external_scenes_file is not None:
        if verbose: console.print(f"[green]Using external scenes: {external_scenes_file.name}[/green]")
        try:
            with open(external_scenes_file, 'r') as f:
                scene_data = json.load(f)
            # Progressive-Scene-Detection outputs "scenes" list with "start_frame"
            if "scenes" in scene_data:
                for s in scene_data["scenes"]:
                    iframe_list.append(s["start_frame"])
            else:
                console.print("[red]Invalid external scenes JSON format. Falling back to detection.[/red]")
                # Fallback logic handled below by checking if iframe_list is empty
        except Exception as e:
            console.print(f"[red]Error reading external scenes: {e}. Falling back to detection.[/red]")

    if mode == "src" and not iframe_list:
        with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                "[progress.percentage]{task.percentage:>3.0f}%",
                FPSColumn(),
                TimeElapsedColumn(),
                TimeRemainingColumn(),
                console=console
            ) as progress:

            task = progress.add_task("[green]Analyzing Scenes (SCDetect)", total=nframe)

            def progress_func(n: int, num_frames: int) -> None:
                progress.update(task, completed=n)

            # Create a lightweight analysis clip (360p, 8-bit) for fast SCDetect
            analysis_clip = src.resize.Bilinear(640, 360, format=vs.YUV420P8)
            analysis_clip = analysis_clip.misc.SCDetect(threshold=0.1)

            def get_props(n: int, f: vs.VideoFrame) -> None:
                if n == 0 or f.props.get('_SceneChangePrev') == 1:
                    iframe_list.append(n)

            clip_async_render(
                analysis_clip,
                outfile=None,
                progress=progress_func,
                callback=get_props
            )
            progress.update(task, description="[cyan]Scenes Analyzed         ", completed=nframe)

    with open(kf_file, "a") as file:
        file.write("\n".join(map(str, iframe_list)))

    with open(kf_file, "a") as file:
        file.write(f"\n{nframe}\n{fwidth}\n{fheight}\n{ffpsnum}\n{ffpsden}")

    return iframe_list, hr, nframe, fwidth, fheight, ffpsnum, ffpsden

def fast_pass() -> None:
    """
    Fast pass using Av1an to generate an MKV file.
    """
    encoder_params = f'--preset {fast_speed} '

    # Check if CRF is manually specified in fast_params
    needs_crf = True
    if fast_params and "--crf" in fast_params:
        needs_crf = False

    if needs_crf:
        # Load VPY to check for HR content (High Resolution)
        try:
            # 'final' is preferred so a downscale in the chain is reflected here.
            src = load_vpy_clip(vpy_file)
            hr = src.width * src.height > 1920 * 1080
        except Exception as e:
            if verbose: console.print(f"[yellow]Warning: Could not determine resolution from VPY, defaulting hr=False. Error: {e}[/yellow]")
            hr = False

        # Match CRF based on user quality setting
        match quality:
            case "low": crf = 40 if hr else 35
            case "medium": crf = 35 if hr else 30
            case "high": crf = 30 if hr else 25
            case "breeze": crf = 18 if hr else 18
            case _: crf = float(quality)

        encoder_params += f" --crf {crf} "

    if fast_params:
        encoder_params += f'{fast_params}'

    if workers_specified:
        encoder_params += " --lp 4"

    if verbose:
        console.print(f'Fast params: "{encoder_params}"')

    # Av1an command
    av1an_cmd = [
        str(av1an_exe),
        '-i', vpy_file.name,  # Just the filename
        '-e', 'svt-av1',
        '-m', 'bestsource',
        '--cache-mode', 'temp',
        '-c', 'mkvmerge',
        '--resume',
        '--no-defaults',
        '-w', str(fast_pass_workers) # Use calculated workers (2 or user-defined)
    ]

    if external_scenes_file:
        av1an_cmd.extend(['-s', str(external_scenes_file)])

    av1an_cmd.extend([
        '-v', encoder_params,
        '-o', fast_output_file.name # Just the filename
    ])

    if verbose:
        print("-" * 50)
        print(f"Running Fast Pass in: {obscure_user_path(str(tmp_dir))}")
        print(f"Command:\n{obscure_user_path(' '.join(av1an_cmd))}")
        print("-" * 50)

    try:
        if verbose:
            # Run in tmp_dir so it picks up files from current dir
            subprocess.run(av1an_cmd, check=True, cwd=tmp_dir)
        else:
            returncode = run_av1an_with_simple_progress(
                av1an_cmd, tmp_dir, "Fast pass", FAST_PASS_EXPLANATION)
            if returncode != 0:
                raise subprocess.CalledProcessError(returncode, av1an_cmd)
    except subprocess.CalledProcessError as e:
        console.print(f"[red]Fast pass failed:[/red]\n{e}")
        raise SystemExit(1)

def final_pass() -> None:
    """
    Final encoding pass using Av1an.
    """
    if not scenes_file.exists() and not no_boosting:
        console.print("[red]Scenes file not found![/red]")
        raise SystemExit(1)

    # Av1an writes the finished file directly to --output, so its folder has to
    # be there before the encode rather than after it.
    final_output_file.parent.mkdir(parents=True, exist_ok=True)

    av1an_cmd = [
        str(av1an_exe),
        '-i', vpy_file.name, # Just the filename
        '-y',
        '--workers', str(final_pass_workers),
        '--resume',
        '--no-defaults',
        '-x', '0',
        '--keep',
    ]

    av1an_cmd.extend([
        '-e', 'svt-av1',
        '-m', 'bestsource',
        '--cache-mode', 'temp',
        '-o', plain_path(final_output_file) # Absolute: Av1an writes it where it belongs
    ])

    if not no_boosting:
        # Use generated scenes
        av1an_cmd.extend(['-s', scenes_file.name])
    else:
        v_params = append_encoder_photon_noise(["--preset", final_speed, "--crf", str(quality)] + (final_params.split() if final_params else []))
        av1an_cmd.extend(['-v', " ".join(v_params)])

    # Command display is now part of verbose mode; default mode keeps the
    # simple progress bar interface.
    if verbose:
        print("-" * 50)
        print(f"Running Final Pass in: {obscure_user_path(str(tmp_dir))}")
        print(f"Command:\n{obscure_user_path(' '.join(av1an_cmd))}")
        print("-" * 50)

    try:
        if verbose:
            # Run in tmp_dir so it picks up files from current dir
            subprocess.run(av1an_cmd, check=True, cwd=tmp_dir)
        else:
            returncode = run_av1an_with_simple_progress(
                av1an_cmd, tmp_dir, "Final pass", FINAL_PASS_EXPLANATION)
            if returncode != 0:
                raise subprocess.CalledProcessError(returncode, av1an_cmd)
    except subprocess.CalledProcessError as e:
        console.print(f"[red]Final pass failed:[/red]\n{e}")
        raise SystemExit(1)

# Each vship/FFVship build only runs on the hardware it was compiled for, so a
# backend name is meaningless without its GPU variant. ssimu2-workercount.py
# benchmarks all three and may name any of them as the winner.
#   nvidia -> NVIDIA only     amd -> AMD only     vulkan -> NVIDIA/AMD/Intel
GPU_VARIANTS = ("nvidia", "amd", "vulkan")

VSHIP_DLLS = {
    "nvidia": "libvship_NVIDIA.so",
    "amd": "libvship_AMD.so",
    "vulkan": "libvship_VULKAN.so",
}


def _split_tool_variant(value: str) -> tuple[str, str | None]:
    """Split a configured SSIMU2 tool string into (tool, variant).

    Accepts every spelling that reaches this script: the names
    ssimu2-workercount.py writes to workercount-ssimu2.txt ("ffvship_amd",
    "vs-hip"), the DLL filenames ("libvship_AMD.dll"), and the spaced form the
    .bat carries in custom-ssim2-tool ("ffvship amd"). The variant is None when
    the string names no GPU build, so the caller keeps the one it already has.
    """
    value_l = (value or "").strip().lower().replace(" ", "-").replace("_", "-")
    variant = next((v for v in GPU_VARIANTS if v in value_l), None)
    if value_l.startswith(("libvship", "vs-hip")):
        return "vs-hip", variant
    if value_l.startswith("ffvship"):
        return "ffvship", variant
    return value_l, variant


def _load_ffvship_config(default_gpu_threads: int) -> tuple[str, int]:
    """
    Reads tools/workercount-ssimu2.txt if present.
    The batch files only consume the first two lines (tool/workercount), so the
    selected FFVship backend is stored on later key/value lines for this script.
    """
    variant = "nvidia"
    gpu_threads = max(1, default_gpu_threads)

    if not ffvship_config_file.exists():
        return variant, gpu_threads

    try:
        with open(ffvship_config_file, "r", encoding="utf-8") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip().lower()
                value = value.strip()
                if key == "workercount":
                    try:
                        gpu_threads = max(1, int(value))
                    except ValueError:
                        pass
                elif key in ("ffvship_variant", "variant", "backend"):
                    value_l = value.lower()
                    if value_l in GPU_VARIANTS:
                        variant = value_l
    except Exception as e:
        if verbose:
            console.print(f"[yellow]Could not read FFVship config: {e}[/yellow]")

    return variant, gpu_threads


def _get_ffvship_exe(variant: str) -> Path:
    variant_l = (variant or "").lower()
    if variant_l == "vulkan":
        return ffvship_vulkan_exe
    if variant_l == "amd":
        return ffvship_amd_exe
    return ffvship_nvidia_exe


def _load_ssimu2_config(default_workers: int) -> dict:
    """Read tools/workercount-ssimu2.txt key/value config."""
    cfg = {
        "tool": "auto",
        "filter_tool": "vs-zip",
        "workercount": max(1, default_workers),
        "variant": "nvidia",
        "streams": max(1, default_workers),
    }
    if not ffvship_config_file.exists():
        return cfg
    try:
        for raw_line in ffvship_config_file.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip().lower().replace("_", "-")
            value = value.strip()
            value_l = value.lower()
            if key == "tool":
                tool, tool_variant = _split_tool_variant(value_l)
                cfg["tool"] = tool
                if tool_variant:
                    cfg["variant"] = tool_variant
            elif key in ("filter-tool", "downscale-tool"):
                cfg["filter_tool"] = value_l
            elif key == "workercount":
                try:
                    cfg["workercount"] = max(1, int(value))
                    cfg["streams"] = cfg["workercount"]
                except ValueError:
                    pass
            elif key in ("streams", "numstream", "num-stream"):
                try:
                    cfg["streams"] = max(1, int(value))
                except ValueError:
                    pass
            elif key in ("variant", "backend", "ffvship-variant", "vship-variant"):
                if value_l in GPU_VARIANTS:
                    cfg["variant"] = value_l
    except Exception as e:
        if verbose:
            console.print(f"[yellow]Could not read SSIMU2 config: {e}[/yellow]")
    return cfg


def _vship_autoload_dirs() -> list[Path]:
    """VapourSynth plugin autoload directories on Linux, writable-first."""
    import subprocess as _sp
    dirs: list[Path] = []
    try:
        libdir = _sp.run(["pkg-config", "--variable=libdir", "vapoursynth"],
                         capture_output=True, text=True).stdout.strip()
        if libdir:
            dirs.append(Path(libdir) / "vapoursynth")
    except Exception:
        pass
    dirs.append(Path("/usr/local/lib/vapoursynth"))
    dirs.append(Path("/usr/lib/x86_64-linux-gnu/vapoursynth"))
    dirs.append(Path("/usr/lib/vapoursynth"))
    return [d for d in dirs if d.is_dir()]


def _activate_vship_plugin(variant: str) -> str | None:
    """
    Ensure the selected libvship build is available to VapourSynth and return its name.

    On Linux the system autoload dirs are root-owned when installed system-wide;
    a user-run encode cannot copy into them. The benchmark normally installs the
    winning build already, so treat an already-loaded vship plugin as success and
    only attempt a copy into a writable autoload dir otherwise.
    """
    dll_name = VSHIP_DLLS.get((variant or "").lower(), VSHIP_DLLS["nvidia"])
    src = tools_dir / "vs-hip" / dll_name

    if hasattr(core, "vship") and hasattr(core.vship, "SSIMULACRA2"):
        if verbose:
            console.print(f"[yellow]Using already-loaded vs-hip plugin ({dll_name}).[/yellow]")
        return dll_name

    if not src.exists():
        console.print(f"[yellow]vs-hip build not found at {src}[/yellow]")
        return None

    for dst_dir in _vship_autoload_dirs():
        dst = dst_dir / dll_name
        needs_copy = not dst.exists()
        if dst.exists():
            try:
                needs_copy = not filecmp.cmp(src, dst, shallow=False)
            except OSError:
                needs_copy = True
        if not needs_copy:
            return dll_name
        try:
            shutil.copy2(src, dst)
            return dll_name
        except PermissionError:
            continue
        except OSError:
            continue

    console.print(
        f"[red]Could not activate {dll_name}: no writable VapourSynth autoload dir.[/red] "
        "Re-run setup (ffvship module) or copy it manually."
    )
    return None


def _calculate_ssimu2_vship(cut_source_clip, cut_encoded_clip, skip: int, nframe: int, streams: int) -> None:
    if not hasattr(core, "vship") or not hasattr(core.vship, "SSIMULACRA2"):
        raise RuntimeError("VapourSynth vship plugin not loaded")
    ref = cut_source_clip.resize.Bicubic(format=vs.RGB24, matrix_in_s="709")
    dist = cut_encoded_clip.resize.Bicubic(format=vs.RGB24, matrix_in_s="709")
    result = core.vship.SSIMULACRA2(ref, dist, numStream=streams)
    score_list = [None] * cut_source_clip.num_frames

    def get_ssimprops(n, f):
        val = f.props.get("_SSIMULACRA2")
        if val is None:
            val = f.props.get("SSIMULACRA2")
        if val is None:
            val = f.props.get("float_ssimulacra2")
        score_list[n] = 0.0 if val is None else float(val)

    with metric_progress_display("Calculating SSIMULACRA2 (vs-hip)",
                                 cut_source_clip.num_frames * skip,
                                 SSIMU2_EXPLANATION) as (p, task):
        def update_p(n, t):
            p.update(task, advance=skip)
        clip_async_render(result, progress=update_p, callback=get_ssimprops)

    _write_metric_log_from_scores([score if score is not None else 0.0 for score in score_list], ssimu2_log_file, skip, nframe)


def _try_ffvship_vs_hip_fallback(cut_source_clip, cut_encoded_clip, skip: int, nframe: int, cfg: dict) -> bool:
    """Fallback from external FFVship to in-process vs-hip/libvship metrics."""
    console.print("[yellow]FFVship failed, using vs-hip fallback[/yellow]")
    variant = cfg.get("variant", "nvidia")
    dll_name = _activate_vship_plugin(variant)
    if not dll_name:
        console.print("[yellow]vs-hip fallback is unavailable; falling back to vs-zip.[/yellow]")
        return False
    try:
        streams = int(cfg.get("streams", cfg.get("workercount", ssimu2_cpu_workers)))
        if verbose: console.print(f"[yellow]Calculating SSIMULACRA2 via vs-hip ({dll_name} | streams: {streams} | every {skip})...[/yellow]")
        _calculate_ssimu2_vship(cut_source_clip, cut_encoded_clip, skip, nframe, streams)
        return True
    except Exception as e:
        console.print(f"[yellow]vs-hip fallback failed ({e}); falling back to vs-zip.[/yellow]")
        return False


def _write_metric_log_from_scores(scores: list[float], log_path: Path, skip: int, nframe: int) -> None:
    """
    FFVship --every N returns one score for frames 0, N, 2N, ... .
    The rest of Auto-Boost expects one metric line per original frame, so each
    sampled score is expanded across the skipped frame interval.
    """
    with open(log_path, "w", encoding="utf-8") as file:
        for sample_index, score in enumerate(scores):
            start_frame = sample_index * skip
            end_frame = min(start_frame + skip, nframe)
            for frame_num in range(start_frame, end_frame):
                file.write(f"{frame_num}: {score}\n")


def _load_ffvship_scores(json_path: Path) -> list[float]:
    with open(json_path, "r", encoding="utf-8") as f:
        raw_scores = json.load(f)

    scores: list[float] = []
    for item in raw_scores:
        if isinstance(item, list) and item:
            scores.append(float(item[0]))
        elif isinstance(item, (int, float)):
            scores.append(float(item))
    return scores


def calculate_metric() -> None:
    # Use Windows VPY for source information and XPSNR / vs-zip paths.
    # 'final' (10-bit) is preferred over 'src', which prevents
    # "XPSNR only supports 8 or 10 bit clips" when src is 16-bit.
    source_clip = load_vpy_clip(vpy_file)

    # Read Fast Pass MKV
    try:
        if not fast_output_file.exists():
             console.print("[red]Fast pass output file not found. Did the fast pass fail?[/red]")
             raise SystemExit(1)
        encoded_clip = source_filter.open_source(core, active_source_filter, fast_output_file)
    except Exception as e:
        console.print(f"[red]Error indexing fast pass file: {e}[/red]")
        raise SystemExit(1)

    if len(source_clip) != len(encoded_clip):
        console.print(f"[red]Frame count mismatch: Src {len(source_clip)} vs Enc {len(encoded_clip)}[/red]")
        raise SystemExit(1)

    skip = 3
    cut_source_clip = source_clip[::skip]
    cut_encoded_clip = encoded_clip[::skip]

    global ssimu2

    # ----------------------------------------------------
    # 1. ATTEMPT XPSNR (Default if ssimu2 is empty)
    # ----------------------------------------------------
    if ssimu2 == "":
        if verbose: console.print("[yellow]Calculating XPSNR (Default)...[/yellow]")
        try:
            # Check for vszip
            if not hasattr(core, 'vszip'):
                 console.print("[red]vs-zip plugin not found! Required for XPSNR.[/red]")
                 raise SystemExit(1)

            result = core.vszip.XPSNR(cut_source_clip, cut_encoded_clip, temporal=False, verbose=False)

            # XPSNR requires storing Y, U, V separately
            score_list = [[None] * cut_source_clip.num_frames for _ in range(3)]

            def get_xpsnrprops(n: int, f: vs.VideoFrame) -> None:
                for i, plane in enumerate(["Y", "U", "V"]):
                    val = f.props.get(f"XPSNR_{plane}")
                    # inf = perfect match
                    if str(val) == "inf":
                        score_list[i][n] = "100.0"
                    else:
                        score_list[i][n] = float(val)

            with metric_progress_display("Calculating XPSNR",
                                         cut_source_clip.num_frames*skip,
                                         XPSNR_EXPLANATION) as (p, task):
                def update_p(n, t):
                     p.update(task, advance=skip)

                clip_async_render(result, progress=update_p, callback=get_xpsnrprops)

            # Write Log
            with open(xpsnr_log_file, "w") as file:
                skip_offset = 0
                for index in range(len(score_list[0])):
                    val_y = score_list[0][index]
                    val_u = score_list[1][index]
                    val_v = score_list[2][index]

                    if val_y is None: val_y = 0.0
                    if val_u is None: val_u = 0.0
                    if val_v is None: val_v = 0.0

                    for i in range(skip):
                        file.write(f"{index+skip_offset+i}: {val_y} {val_u} {val_v}\n")
                    skip_offset += skip - 1
            return

        except Exception as e:
            console.print(f"[red]XPSNR calculation failed: {e}[/red]")
            raise SystemExit(1)

    # ----------------------------------------------------
    # 2. SSIMULACRA2 VIA vs-hip / FFVship, WITH VS-ZIP FALLBACK
    # ----------------------------------------------------
    metric_calculated = False
    fallback_needed = False
    # Set once the vship plugin has proven unusable in this process, so the
    # FFVship path below does not bother offering it back as its own fallback.
    vship_unavailable = False
    cfg = _load_ssimu2_config(ssimu2_cpu_workers)

    requested_tool = ssimu2
    if requested_tool in ("auto", "gpu"):
        requested_tool = cfg.get("tool", "auto")
    # Collapse every backend spelling to a bare tool name plus its GPU variant.
    # An unrecognised variant-qualified name must never survive this step: the
    # dispatch checks below only match "vs-hip"/"ffvship", so anything else
    # would silently skip both GPU paths and land in the vs-zip CPU fallback.
    parsed_tool, parsed_variant = _split_tool_variant(requested_tool)
    if parsed_tool in ("vs-hip", "ffvship"):
        requested_tool = parsed_tool
        if parsed_variant:
            cfg["variant"] = parsed_variant

    # --ssimu2 naming a backend is almost never the user demanding that one: the
    # .bat passes whatever tool= line ssimu2-workercount.py last wrote into
    # workercount-ssimu2.txt, so the value is a stale benchmark result far more
    # often than a choice. A GPU backend that stops working between the
    # benchmark and the encode - a driver update that drops the HIP runtime, a
    # DLL that moved - used to end the run with SystemExit(1). Degrade down the
    # chain instead and say what happened: a broken GPU costs speed, not the
    # encode. FFVship already behaved this way; vs-hip now matches it.
    if requested_tool == "vs-hip":
        dll_name = _activate_vship_plugin(cfg.get("variant", "nvidia"))
        if not dll_name:
            console.print("[yellow]vs-hip is unavailable. Trying FFVship, then vs-zip.[/yellow]")
            console.print("[yellow]Delete tools/workercount-ssimu2.txt to re-benchmark the SSIMU2 backends.[/yellow]")
            vship_unavailable = True
            requested_tool = "ffvship"
        else:
            try:
                streams = int(cfg.get("streams", ssimu2_cpu_workers))
                if verbose: console.print(f"[yellow]Calculating SSIMULACRA2 via vs-hip ({dll_name} | streams: {streams} | every {skip})...[/yellow]")
                _calculate_ssimu2_vship(cut_source_clip, cut_encoded_clip, skip, len(source_clip), streams)
                metric_calculated = True
            except Exception as e:
                console.print(f"[yellow]vs-hip failed ({e}). Trying FFVship, then vs-zip.[/yellow]")
                vship_unavailable = True
                requested_tool = "ffvship"

    if (not metric_calculated) and (not fallback_needed) and requested_tool in ['auto', 'gpu', 'ffvship']:
        variant = cfg.get("variant", "nvidia")
        gpu_threads = int(cfg.get("workercount", ssimu2_cpu_workers))
        ffvship_exe = _get_ffvship_exe(variant)

        if not ffvship_exe.exists():
            msg = f"FFVship {variant} binary not found at {ffvship_exe}"
            console.print(f"[yellow]{msg}[/yellow]")
            if not vship_unavailable:
                metric_calculated = _try_ffvship_vs_hip_fallback(cut_source_clip, cut_encoded_clip, skip, len(source_clip), cfg)
            if not metric_calculated:
                fallback_needed = True

        if (not metric_calculated) and (not fallback_needed):
            if verbose: console.print(f"[yellow]Calculating SSIMULACRA2 via FFVship ({variant} | GPU streams: {gpu_threads} | every {skip})...[/yellow]")
            ffvship_json_file = tmp_dir / f"{src_file.stem}_ffvship.json"
            if ffvship_json_file.exists():
                try:
                    ffvship_json_file.unlink()
                except Exception:
                    pass

            cmd = [
                str(ffvship_exe),
                "--source", str(src_file),
                "--encoded", str(fast_output_file),
                "-m", "SSIMULACRA2",
                "--every", str(skip),
                "-t", "3",
                "-g", str(gpu_threads),
                "--json", str(ffvship_json_file)
            ]

            if verbose:
                console.print(f"[dim]FFVship command: {' '.join(cmd)}[/dim]")

            try:
                with metric_progress_display(f"Calculating SSIMULACRA2 (FFVship {variant.capitalize()})",
                                             None, SSIMU2_EXPLANATION,
                                             indeterminate=True) as (p, task):
                    proc = subprocess.run(
                        cmd,
                        cwd=tmp_dir,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT
                    )
                    p.update(task, completed=1)

                if verbose and proc.stdout:
                    console.print(proc.stdout)

                if proc.returncode != 0:
                    raise RuntimeError(f"FFVship exited with code {proc.returncode}")
                if not ffvship_json_file.exists():
                    raise RuntimeError(f"FFVship did not create JSON output at {ffvship_json_file}")

                scores = _load_ffvship_scores(ffvship_json_file)
                if not scores:
                    raise RuntimeError("FFVship JSON did not contain any SSIMULACRA2 scores")

                _write_metric_log_from_scores(scores, ssimu2_log_file, skip, len(source_clip))
                metric_calculated = True

            except Exception as e:
                console.print(f"[yellow]FFVship failed: {e}[/yellow]")
                if not vship_unavailable:
                    metric_calculated = _try_ffvship_vs_hip_fallback(cut_source_clip, cut_encoded_clip, skip, len(source_clip), cfg)
                if not metric_calculated:
                    fallback_needed = True

    if metric_calculated:
        return

    # FALLBACK CPU (VS-ZIP)
    if verbose: console.print(f"[yellow]Calculating SSIMULACRA2 (VS-ZIP | {ssimu2_cpu_workers} workers)...[/yellow]")

    core.num_threads = ssimu2_cpu_workers

    try:
        if not hasattr(core, 'vszip') or not hasattr(core.vszip, "SSIMULACRA2"):
            console.print("[red]Error: vs-zip plugin not found or does not support SSIMULACRA2. Cannot fallback.[/red]")
            raise SystemExit(1)

        fallback_ref = cut_source_clip.resize.Bicubic(format=vs.RGB24, matrix_in_s="709")
        fallback_dist = cut_encoded_clip.resize.Bicubic(format=vs.RGB24, matrix_in_s="709")

        result = core.vszip.SSIMULACRA2(fallback_ref, fallback_dist)

        score_list = [None] * cut_source_clip.num_frames

        def get_ssimprops(n, f):
            val = f.props.get("_SSIMULACRA2")
            if val is None:
                val = f.props.get("SSIMULACRA2")
            if val is None:
                val = f.props.get("float_ssimulacra2")

            if val is None:
                if n == 0: console.print("[red]Warning: _SSIMULACRA2 property missing in fallback frame 0[/red]")
                score_list[n] = 0.0
            else:
                score_list[n] = float(val)

        with metric_progress_display("Calculating SSIMULACRA2 (VS-ZIP)",
                                     cut_source_clip.num_frames*skip,
                                     SSIMU2_EXPLANATION) as (p, task):
            def update_p(n, t):
                 p.update(task, advance=skip)

            clip_async_render(result, progress=update_p, callback=get_ssimprops)

    except Exception as e:
        console.print(f"Fallback failed: {e}")
        raise SystemExit(1)

    _write_metric_log_from_scores([score if score is not None else 0.0 for score in score_list], ssimu2_log_file, skip, len(source_clip))

def metrics_aggregation(score_list: list[float]) -> tuple[float, float, float]:
    """
    Aggregate one scene's per-frame metric scores into (mean, bad-tail mean, min).

    The second value is what drives CRF boosting. It used to be the 15th
    percentile via quantiles(n=100)[14], but a fixed-rank percentile does not
    mean the same thing at every scene length: with the exclusive method the
    cut point sits at 15*(m+1)/100, so on a 24-frame scene it lands between
    the first and second distinct samples (effectively the single worst frame),
    while on a 240-frame scene it lands around the 12th worst. Short scenes
    were therefore judged on one unlucky frame and boosted more noisily than
    long ones.

    Averaging the worst TAIL_FRACTION of samples keeps a constant meaning
    across scene lengths and is less sensitive to a single outlier, while
    still targeting the bad frames the same way the percentile did.

    Negative scores are no longer clamped to 0.0: SSIMULACRA2 legitimately
    returns negative values on badly broken frames, and those are exactly the
    frames that should pull the boost down rather than being floored.

    :param score_list: per-frame metric scores for one scene
    :type score_list: list[float]

    :return: mean score, mean of the worst TAIL_FRACTION, and minimum score
    :rtype: tuple[float, float, float]
    """
    TAIL_FRACTION = 0.20

    sorted_score_list = sorted(score_list)

    if not sorted_score_list:
        return 0.0, 0.0, 0.0

    average = sum(sorted_score_list) / len(sorted_score_list)

    # Use at least 2 samples where they exist, and never more than we have.
    # Dividing by len(tail) rather than k keeps single-sample scenes correct.
    k = min(len(sorted_score_list),
            max(2, ceil(len(sorted_score_list) * TAIL_FRACTION)))
    tail = sorted_score_list[:k]
    tail_mean = sum(tail) / len(tail)

    min_score = sorted_score_list[0]
    return (average, tail_mean, min_score)

# --- ZONES HELPERS ---

def find_zones_file(video_path: Path) -> Path | None:
    """
    Find a zones txt file for the input video.

    Primary match: S01E02 in the video filename -> s01e02-zones.txt.
    Search the source folder first, then common Auto-Boost folders so a run
    launched from temp/ or with a differently-resolved input path still finds
    video-input/s01e02-zones.txt.
    """
    stem = video_path.stem
    match = re.search(r"(s\d{2}e\d{2})", stem, flags=re.IGNORECASE)
    if not match:
        return None

    ep_str = match.group(1).lower()
    exact_filename = f"{ep_str}-zones.txt"

    search_dirs: list[Path] = []

    def add_search_dir(path: Path) -> None:
        try:
            resolved = path.resolve()
        except Exception:
            resolved = path
        if resolved not in search_dirs:
            search_dirs.append(resolved)

    add_search_dir(video_path.parent)
    add_search_dir(video_path.parent / "video-input")
    add_search_dir(video_path.parent.parent / "video-input")
    add_search_dir(Path.cwd())
    add_search_dir(Path.cwd() / "video-input")
    add_search_dir(tools_dir.parent / "video-input")

    for directory in search_dirs:
        zones_path = directory / exact_filename
        if zones_path.exists():
            return zones_path

    # Fallback: accept names like show-s01e02-zones.txt or S01E02.zones.txt.
    # This keeps exact s01e02-zones.txt preferred while making auto-match less brittle.
    for directory in search_dirs:
        if not directory.exists():
            continue
        for candidate in sorted(directory.glob("*.txt")):
            candidate_stem = candidate.stem.lower()
            if ep_str in candidate_stem and "zones" in candidate_stem:
                return candidate

    return None

def parse_param_string_to_dict(param_list: list[str]) -> dict:
    """
    Converts a list of params ['--crf', '20', '--enable-cdef', '1']
    into a dict {'--crf': '20', '--enable-cdef': '1'}.
    Handles boolean flags (no value) if necessary, though SVT usually has values.
    """
    d = {}
    i = 0
    while i < len(param_list):
        key = param_list[i]
        if key.startswith('--'):
            # Check if next item exists and is not a flag
            if i + 1 < len(param_list) and not param_list[i+1].startswith('--'):
                d[key] = param_list[i+1]
                i += 2
            else:
                # Boolean flag or flag at end
                d[key] = None
                i += 1
        else:
            # stray value? ignore
            i += 1
    return d

def dict_to_param_list(d: dict) -> list[str]:
    l = []
    for k, v in d.items():
        l.append(k)
        if v is not None:
            l.append(v)
    return l

def merge_params(base_params: list[str], zone_params_str: str) -> list[str]:
    """
    Merges base params with zone params.
    --photon-noise is an SVT-AV1 encoder setting and stays in video_params.
    """
    base_dict = parse_param_string_to_dict(base_params)

    # Split zone params string into list
    zone_list = zone_params_str.strip().split()
    zone_dict = parse_param_string_to_dict(zone_list)

    # Update base with zone. Zones can override --photon-noise here just like
    # any other SVT-AV1 setting; it must not be moved into Av1an's JSON
    # photon_noise field, which is limited to u8 and should remain null.
    for k, v in zone_dict.items():
        base_dict[k] = v

    # Reconstruct list
    return dict_to_param_list(base_dict)

# ---------------------

def calculate_zones_json(ranges: list[float], hr: bool, nframe: int, override_zones: Path | None = None) -> None:
    metric_scores = []

    # If using XPSNR (Default behavior if ssimu2 string is empty)
    if ssimu2 == "":
        if not xpsnr_log_file.exists():
            console.print("[red]XPSNR log file missing! Did stage 2 finish?[/red]")
            raise SystemExit(1)

        with open(xpsnr_log_file, "r") as file:
            for line in file:
                # Format: "frame: y u v"
                match = re.search(r"([0-9]+): ([0-9]+\.[0-9]+) ([0-9]+\.[0-9]+) ([0-9]+\.[0-9]+)", line)
                if match:
                    score_y, score_u, score_v = float(match.group(2)), float(match.group(3)), float(match.group(4))
                    maxval = 255
                    # Convert PSNR to MSE
                    # avoid div by zero if perfect match
                    try:
                        mse_y = (maxval**2) / (10 ** (score_y / 10))
                        mse_u = (maxval**2) / (10 ** (score_u / 10))
                        mse_v = (maxval**2) / (10 ** (score_v / 10))
                    except OverflowError:
                        mse_y, mse_u, mse_v = 0.0001, 0.0001, 0.0001 # approx 0

                    # 4:1:1 weighted average (Y is dominant)
                    w_mse = ((4.0 * mse_y) + mse_u + mse_v) / 6.0

                    if w_mse <= 0: w_mse = 0.000001

                    # Convert back to Logarithmic Score (Similar to PSNR but weighted)
                    score_weighted = 10.0 * log10((maxval**2) / w_mse)
                    metric_scores.append(score_weighted)
    else:
         # SSIMU2 read
         with open(ssimu2_log_file, "r") as file:
            for line in file:
                match = re.search(r"([0-9]+): (-?[0-9eE\.\-\+]+)", line)
                if match: metric_scores.append(float(match.group(2)))

    metric_total_scores = []
    metric_percentile_15_total = []

    for index in range(len(ranges)):
        metric_chunk_scores = []
        if index == len(ranges)-1:
            metric_frames = nframe - ranges[index]
        else:
            metric_frames = ranges[index+1] - ranges[index]

        for scene_index in range(metric_frames):
            try:
                metric_score = metric_scores[ranges[index]+scene_index]
                metric_chunk_scores.append(metric_score)
                metric_total_scores.append(metric_score)
            except IndexError:
                pass

        if metric_chunk_scores:
            (metric_average, metric_percentile_15, _) = metrics_aggregation(metric_chunk_scores)
            metric_percentile_15_total.append(metric_percentile_15)
        else:
            metric_percentile_15_total.append(0)

    (metric_average, _, _) = metrics_aggregation(metric_total_scores)

    match quality:
        case "low": crf = 40 if hr else 35
        case "medium": crf = 35 if hr else 30
        case "high": crf = 30 if hr else 25
        case "breeze": crf = 18 if hr else 18
        case _: crf = float(quality)

    # 1. Generate Base Auto-Boost Scenes
    base_scenes = []

    for index in range(len(ranges)):
        multiplier = 40 if aggressive else 20
        if metric_average == 0: metric_average = 1

        adjustment = ceil((1.0 - (metric_percentile_15_total[index] / metric_average)) * multiplier * 4) / 4
        new_crf = crf - adjustment

        limit = 10 if unshackle else 5
        if adjustment < -limit: new_crf = crf + limit
        elif adjustment > limit: new_crf = crf - limit

        start_frame = ranges[index]
        if index == len(ranges)-1:
            end_frame = nframe
        else:
            end_frame = ranges[index+1]

        if verbose:
            console.print(f'Chunk: [{start_frame}:{end_frame}] / 15th: {metric_percentile_15_total[index]:.2f} / CRF: {new_crf}')

        extra_params = final_params.split() if final_params else []
        scene_params = append_encoder_photon_noise(["--preset", final_speed, "--crf", f"{new_crf:.2f}"] + extra_params)

        base_scenes.append({
            "start_frame": start_frame,
            "end_frame": end_frame,
            "photon_noise": None,
            "video_params": scene_params
        })

    # 2. Check for Zones File
    if override_zones:
        zones_file = override_zones
    else:
        zones_file = find_zones_file(src_file)

    final_scenes = base_scenes

    if zones_file:
        if not zones_file.exists():
            console.print(f"[yellow]Warning: Zones file {zones_file} not found. Ignoring.[/yellow]")
        else:
            console.print(f"[green]Zones file found: {zones_file.name}[/green]")
            console.print("[yellow]Applying zones overrides...[/yellow]")

            # Read zones
            zones = []
            with open(zones_file, 'r') as f:
                for line in f:
                    if not line.strip() or line.strip().startswith('#'): continue
                    parts = line.split(maxsplit=3) # start, end, enc, params
                    if len(parts) < 4: continue
                    try:
                        z_start = int(parts[0])
                        z_end_raw = int(parts[1])

                        # Handle -1 as final frame
                        if z_end_raw == -1:
                            z_end = nframe - 1
                        else:
                            z_end = z_end_raw

                        # parts[2] is encoder (ignored mostly), parts[3] is params
                        z_params = parts[3]
                        zones.append((z_start, z_end, z_params))
                    except ValueError:
                        console.print(f"[red]Invalid zone line: {line.strip()}[/red]")

            # Apply zones iteratively (Cookie Cutter)
            for z_start, z_end, z_params_str in zones:
                new_list = []

                # z_end in zones.txt is typically inclusive in user intent
                # e.g., "0 270" includes frame 270.
                # Av1an internal scene logic is usually [Start, End) (exclusive end)
                # So the scene logic split point should be z_end + 1

                z_end_exclusive = z_end + 1

                for scene in final_scenes:
                    s_start = scene["start_frame"]
                    s_end = scene["end_frame"]

                    # Check for overlap
                    # Overlap if: start1 < end2 AND start2 < end1
                    if s_start < z_end_exclusive and z_start < s_end:
                        # Overlap exists. We might need to split into 3 parts:
                        # 1. Pre-zone (Original)
                        # 2. Zone (Modified)
                        # 3. Post-zone (Original)

                        # 1. Pre-zone
                        if s_start < z_start:
                            new_list.append({
                                "start_frame": s_start,
                                "end_frame": z_start,
                                "photon_noise": scene["photon_noise"],
                                "video_params": scene["video_params"]
                            })

                        # 2. Zone Intersection
                        # Intersection start: max(s_start, z_start)
                        # Intersection end: min(s_end, z_end_exclusive)
                        int_start = max(s_start, z_start)
                        int_end = min(s_end, z_end_exclusive)

                        # Merge params. --photon-noise remains in video_params.
                        merged_params = merge_params(scene["video_params"], z_params_str)

                        new_list.append({
                            "start_frame": int_start,
                            "end_frame": int_end,
                            "photon_noise": None,
                            "video_params": merged_params
                        })

                        # 3. Post-zone
                        if s_end > z_end_exclusive:
                            new_list.append({
                                "start_frame": z_end_exclusive,
                                "end_frame": s_end,
                                "photon_noise": scene["photon_noise"],
                                "video_params": scene["video_params"]
                            })

                    else:
                        # No overlap, keep original
                        new_list.append(scene)

                final_scenes = new_list
                # Sort by start frame to be safe
                final_scenes.sort(key=lambda x: x["start_frame"])

    # 3. Construct Final JSON
    scenes_data_output = []
    for s in final_scenes:
        scenes_data_output.append({
            "start_frame": s["start_frame"],
            "end_frame": s["end_frame"],
            "zone_overrides": {
                "encoder": "svt_av1",
                "passes": 1,
                "video_params": s["video_params"],
                "photon_noise": None,
                "photon_noise_height": None,
                "photon_noise_width": None,
                "chroma_noise": False,
                "extra_splits_len": 240,
                "min_scene_len": 24
            }
        })

    output_json = {"frames": nframe, "scenes": scenes_data_output}

    with open(scenes_file, "w") as f:
        json.dump(output_json, f, indent=2)

    if verbose: console.print(f"[cyan]Generated Av1an scenes file: {obscure_user_path(str(scenes_file))}[/cyan]")


# --- ZONES CHECK FOR DISPLAY ---
current_zones_file = None
zones_msg = ""

if zones_override_path:
    # User specified
    current_zones_file = zones_override_path
    if current_zones_file.exists():
        zones_msg = f"Zones file specified: {current_zones_file.name}"
    else:
        zones_msg = f"Zones file specified: {current_zones_file.name} (not found)"
else:
    # Auto-detect
    current_zones_file = find_zones_file(src_file)
    if current_zones_file:
        zones_msg = f"Zones file detected: {current_zones_file.name}"

if zones_msg:
    console.print(f"[blue]{zones_msg}[/blue]")

console.print("[bold]Auto-Boost-Av1an start!\n")

# Make direct Auto-Boost invocations behave like the generated .bat dispatchers.
setup_svt_av1_fork(tools_dir, args.fork, arch=args.arch, avx512=args.avx512, verbose=verbose)
# -------------------------------

if no_boosting:
    stage = 4

match stage:
    case 0:
        if stage_resume < 2:
            fast_pass()
            with open(stage_file, "w") as file: file.write("2")
            if verbose: print('Stage 1 complete! Now calculating metric scores')
        if stage_resume < 3:
            try: calculate_metric()
            except KeyboardInterrupt: raise SystemExit(1)
            with open(stage_file, "w") as file: file.write("3")
            if verbose: print('Stage 2 complete!')
        if stage_resume < 4:
            try:
                ranges, hr, nframe, _, _, _, _ = get_file_info(src_file, "src")
                calculate_zones_json(ranges, hr, nframe, zones_override_path)
            except KeyboardInterrupt: raise SystemExit(1)

            if zones_override_path:
                 print(f"Manual zones file applied: {zones_override_path.name}")
            else:
                 z_file = find_zones_file(src_file)
                 if z_file:
                    print(f"Zones file found and applied: {z_file.name}")

            with open(stage_file, "w") as file: file.write("4")
            if verbose: print('Stage 3 complete!')
        if stage_resume < 5:
            final_pass()
            with open(stage_file, "w") as file: file.write("5")
            if verbose: print('Stage 4 complete!')
    case 1:
        fast_pass()
        with open(stage_file, "w") as file: file.write("2")
        if verbose: print('Stage 1 complete! Now calculating metric scores')
    case 2:
        try: calculate_metric()
        except KeyboardInterrupt: raise SystemExit(1)
        with open(stage_file, "w") as file: file.write("3")
        if verbose: print('Stage 2 complete!')
    case 3:
        try:
            ranges, hr, nframe, _, _, _, _ = get_file_info(src_file, "src")
            calculate_zones_json(ranges, hr, nframe, zones_override_path)
        except KeyboardInterrupt: raise SystemExit(1)

        if zones_override_path:
             print(f"Manual zones file applied: {zones_override_path.name}")
        else:
             z_file = find_zones_file(src_file)
             if z_file:
                print(f"Zones file found and applied: {z_file.name}")

        with open(stage_file, "w") as file: file.write("4")
        if verbose: print('Stage 3 complete!')
    case 4:
        final_pass()
        with open(stage_file, "w") as file: file.write("5")
        if verbose: print('Stage 4 complete!')
    case _:
        console.print("[red]Stage argument invalid, exiting.")
        raise SystemExit(1)

console.print("\n[bold]Auto-boost complete!")

# Every stage above has finished and every file is on disk. Exit without
# interpreter finalization so a VapourSynth teardown crash cannot turn a
# successful encode into a failed one for whoever called this script.
finish_cleanly(0)