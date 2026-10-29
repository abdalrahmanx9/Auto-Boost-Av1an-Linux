import sys
import subprocess
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import glob
import shutil
import re
import time
import urllib.parse
import urllib.request
from svt_fork_setup import cpu_supports_avx512
import source_filter

try:
    from wakepy import keep
except Exception:  # pragma: no cover - wakepy ships with the package
    import contextlib

    class _KeepFallback:
        @staticmethod
        def running():
            return contextlib.nullcontext()

    keep = _KeepFallback()

BLUE = "\033[94m"
RED = "\033[91m"
RESET = "\033[0m"

# LQTC is a self-contained encoder: it does its own decoding, scene detection,
# chunking, target-quality search and muxing. There is no VapourSynth, no
# FFmpeg and no av1an in this path, so this dispatcher is deliberately much
# thinner than av1an-dispatch.py - it only feeds files in and moves files out.
LQTC_DIR_NAME = "LQTC"
DEFAULT_DISPLAY_FILE = "display.json"

# Every backend/fork combination ships in three CPU builds. x86-64-v3 is the
# one every modern CPU can run, so it is both the default and the fallback when
# the selected build is missing.
ARCHES = ("x86-64-v3", "znver2", "avx512")
DEFAULT_ARCH = "x86-64-v3"

# Filtering keys in settings.txt that LQTC cannot honour. Purely informational.
UNSUPPORTED_FILTER_KEYS = ("denoise", "deband", "dehalo", "downscale")


def format_elapsed_hhmmss(seconds):
    """Format elapsed seconds as hh:mm:ss, allowing totals longer than 24 hours."""
    total_seconds = max(0, int(float(seconds) + 0.5))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def print_lqtc_timing_report(report):
    print("\n" + "-" * 80)
    print(f"LQTC time report for: {report['filename']}")
    print("Time format legend: hh:mm:ss = hours:minutes:seconds")
    print(f"LQTC encoding time: {format_elapsed_hhmmss(report['lqtc_encoding'])}")
    print("-" * 80)


def parse_settings_lines(lines):
    """Parse settings.txt lines into a case-insensitive key/value dict."""
    settings = {}
    for line in lines:
        line = line.strip()
        if not line or line.startswith(("#", ";", "[")) or "=" not in line:
            continue
        key, value = line.split("=", 1)
        settings[key.strip().lower()] = value.strip()
    return settings


def load_script_settings(settings_path):
    if not os.path.exists(settings_path):
        return {}
    try:
        with open(settings_path, "r", encoding="utf-8", errors="replace") as f:
            return parse_settings_lines(f.read().splitlines())
    except Exception as e:
        print(f"[Dispatch] Warning: Could not read {settings_path}: {e}")
        return {}


def parse_bool_setting(value, default=False):
    if value is None:
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "y", "on")


def warn_about_unsupported_filtering(settings):
    """settings.txt drives the VapourSynth filter chain, which LQTC never runs."""
    enabled = [key for key in UNSUPPORTED_FILTER_KEYS if parse_bool_setting(settings.get(key))]
    if not enabled:
        return
    print(f"{BLUE}[Dispatch] Note: settings.txt has {', '.join(enabled)} enabled.{RESET}")
    print(f"{BLUE}[Dispatch] Filtering is not currently supported in quality target mode and is ignored.{RESET}")
    print(f"{BLUE}[Dispatch] Cropping is handled automatically by LQTC itself.{RESET}")


def warn_about_source_filter_override():
    """bat-builder's source filter picks the plugin used inside a .vpy.

    LQTC decodes on its own with no VapourSynth in the path, so there is no .vpy
    and nothing for the override to change. Said out loud rather than ignored,
    so a user who set it does not sit waiting for a BestSource index that is
    never going to be built.
    """
    override = source_filter.read_override()
    if not override:
        return
    print(f"{BLUE}[Dispatch] Note: tools\\{source_filter.OVERRIDE_FILENAME} selects "
          f"{override} as the VapourSynth source filter.{RESET}")
    print(f"{BLUE}[Dispatch] LQTC decodes on its own without VapourSynth, so that setting "
          f"does not apply here.{RESET}")


# --- Encoder selection -------------------------------------------------------

def normalize_arch(arch):
    """Map whatever the .bat set ARCH to onto one of the three build names."""
    arch_key = (arch or "").strip().strip('"').lower().replace("_", "-")
    if arch_key in ("x86-64-v3", "x8664v3", "v3", "generic", "standard"):
        return "x86-64-v3"
    if arch_key in ("znver2", "zen2", "zen"):
        return "znver2"
    if arch_key in ("avx512", "avx-512", "x86-64-v4"):
        return "avx512"
    return DEFAULT_ARCH


def lqtc_exe_name(backend, fork, arch):
    backend_key = (backend or "cuda").strip().lower()
    if backend_key not in ("cuda", "vulkan"):
        backend_key = "cuda"

    fork_key = (fork or "essential").strip().lower()
    if fork_key in ("svt-av1-hdr", "hdr"):
        fork_key = "hdr"
    else:
        fork_key = "essential"

    return f"lqtc-{backend_key}-{fork_key}-{normalize_arch(arch)}"


def resolve_lqtc_exe(tools_dir, backend, fork, arch):
    """Locate the LQTC build for this backend/fork/CPU combination.

    The executables are used in place from tools\\LQTC; nothing is copied or
    moved. If the selected CPU build is missing we fall back to the x86-64-v3
    build of the same combination - the one any modern CPU can run - rather
    than failing the whole run.

    ARCH=avx512 is also downgraded to x86-64-v3 when the CPU has no AVX-512,
    which happens when a .bat generated on an AVX-512 machine is run on one
    without it. A per-arch build has no runtime fallback of its own: it dies
    with an illegal instruction once it reaches its SIMD kernels, and it can
    get as far as printing a banner first, so the crash looks like the encode
    simply vanished.
    """
    lqtc_dir = os.path.join(tools_dir, LQTC_DIR_NAME)

    if normalize_arch(arch) == "avx512" and not cpu_supports_avx512():
        print(f"{BLUE}[Dispatch] ARCH=avx512 was selected but this CPU has no AVX-512 -{RESET}")
        print(f"{BLUE}[Dispatch] using the {DEFAULT_ARCH} build instead.{RESET}")
        arch = DEFAULT_ARCH

    wanted = lqtc_exe_name(backend, fork, arch)
    wanted_path = os.path.join(lqtc_dir, wanted)
    if os.path.exists(wanted_path):
        return wanted_path

    if normalize_arch(arch) != DEFAULT_ARCH:
        fallback = lqtc_exe_name(backend, fork, DEFAULT_ARCH)
        fallback_path = os.path.join(lqtc_dir, fallback)
        if os.path.exists(fallback_path):
            print(f"{RED}[Dispatch] WARNING: {wanted} not found in tools\\{LQTC_DIR_NAME}.{RESET}")
            print(f"{RED}[Dispatch] Falling back to the {DEFAULT_ARCH} build: {fallback}{RESET}")
            return fallback_path

    print(f"{RED}[Dispatch] ERROR: Could not find {wanted} in tools\\{LQTC_DIR_NAME}.{RESET}")
    print(f"{RED}[Dispatch] Your install may be incomplete - try re-downloading the package.{RESET}")
    try:
        input("Press Enter to exit...")
    except EOFError:
        pass
    sys.exit(1)


def lqtc_display_name(backend, fork, exe_path):
    fork_key = (fork or "essential").strip().lower()
    fork_name = "svt-av1-hdr" if fork_key in ("svt-av1-hdr", "hdr") else "svt-av1-essential"
    return f"{fork_name} via LQTC {(backend or 'cuda').strip().lower()} ({os.path.basename(exe_path)})"


# --- Target quality ----------------------------------------------------------

def target_uses_cvvdp(target):
    """LQTC picks the metric from the numeric target it is given.

    Below 8 is Butteraugli, 8-10 is CVVDP, above 10 is SSIMULACRA2. Only CVVDP
    needs a display description file, so that is what decides whether we pass
    -d at all.
    """
    match = re.match(r"^\s*([0-9]*\.?[0-9]+)\s*-", target or "")
    if not match:
        return False
    try:
        return 8.0 < float(match.group(1)) <= 10.0
    except ValueError:
        return False


def resolve_display_file(display_setting, root_dir, tools_dir):
    """Find the CVVDP display description JSON.

    It lives in tools\\LQTC rather than the main folder on purpose: cleanup.py
    deletes *.json from the main folder after every run, which would quietly
    throw away a user's edited viewing conditions.
    """
    configured = (display_setting or "").strip().strip('"') or DEFAULT_DISPLAY_FILE
    if os.path.isabs(configured) and os.path.exists(configured):
        return configured

    filename = re.split(r"[\\/]", configured)[-1] or DEFAULT_DISPLAY_FILE
    for candidate in (
        os.path.join(tools_dir, LQTC_DIR_NAME, filename),
        os.path.join(tools_dir, filename),
        os.path.join(root_dir, filename),
    ):
        if os.path.exists(candidate):
            return candidate
    return ""


# --- ntfy notifications ------------------------------------------------------

def resolve_configured_path(configured_path, root_dir, tools_dir, default_filename="ntfy.txt"):
    configured_path = (configured_path or "").strip().strip('"')
    if not configured_path:
        return os.path.join(tools_dir, default_filename)

    if re.match(r"^[A-Za-z]:[\\/]", configured_path):
        if os.name != "nt":
            drive = configured_path[0].lower()
            rest = configured_path[2:].lstrip("\\/").replace("\\", "/")
            return os.path.join("/mnt", drive, rest)
        return configured_path

    if os.path.isabs(configured_path):
        return configured_path

    if os.name != "nt":
        configured_path = configured_path.replace("\\", "/")

    return os.path.join(root_dir, configured_path)


def read_ntfy_config(settings, root_dir, tools_dir):
    ntfy_path_setting = (settings or {}).get("ntfy", "").strip()
    if ntfy_path_setting.lower() in ("", "false", "off", "none", "disabled"):
        if not ntfy_path_setting:
            ntfy_path_setting = os.path.join(tools_dir, "ntfy.txt")
        else:
            return {}, None
    ntfy_path = resolve_configured_path(ntfy_path_setting, root_dir, tools_dir)
    if not os.path.exists(ntfy_path):
        # Notifications are optional; a missing ntfy.txt should be silent.
        return {}, None

    config = {}
    try:
        with open(ntfy_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                stripped = line.strip()
                if not stripped or stripped.startswith(("#", ";")):
                    continue
                if "=" in stripped:
                    key, value = stripped.split("=", 1)
                    config[key.strip().lower()] = value.strip()
                elif "secretword" not in config:
                    # Backward compatibility with older one-line ntfy.txt files.
                    config["secretword"] = stripped
    except Exception as e:
        print(f"[Dispatch] Warning: Could not read ntfy settings from {ntfy_path}: {e}")
    return config, ntfy_path


def send_ntfy_notification(settings, root_dir, tools_dir, title, message):
    ntfy_config, ntfy_path = read_ntfy_config(settings, root_dir, tools_dir)
    secret_word = ntfy_config.get("secretword", "").strip()
    pc_name = ntfy_config.get("pcname", "").strip() or "this PC"
    if not secret_word:
        if ntfy_path:
            print(f"[Dispatch] ntfy not sent; secretword missing or empty in: {ntfy_path}")
        return False

    body = f"{message}\nPC: {pc_name}"
    topic = urllib.parse.quote(secret_word, safe="")
    request = urllib.request.Request(
        f"https://ntfy.sh/{topic}",
        data=body.encode("utf-8"),
        headers={
            "Title": title,
            "Tags": "movie_camera",
            "Priority": "default",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            response.read()
        print("[Dispatch] ntfy notification sent.")
        return True
    except Exception as e:
        print(f"[Dispatch] Warning: Failed to send ntfy notification: {e}")
        return False


# --- Path length guard -------------------------------------------------------

WINDOWS_MAX_PATH = 260


def display_path_for_length(path):
    """Return the Windows-style path users see, so length warnings match Windows tools."""
    abs_path = os.path.abspath(path)
    normalized = abs_path.replace("/", "\\")
    lower = normalized.lower()
    if lower.startswith("\\mnt\\") and len(normalized) > 6 and normalized[5].isalpha() and normalized[6:7] == "\\":
        return f"{normalized[5].upper()}:\\{normalized[7:]}"
    return normalized


def pause_for_long_paths(long_paths):
    print("\n" + "=" * 80)
    print("[Dispatch] ERROR: One or more backend file paths exceed the Windows 260-character limit.")
    print("[Dispatch] Processing has been paused/stopped before encoding to avoid tool failures.")
    print("[Dispatch] The full filename + folder path is too long for Windows tools.")
    print("[Dispatch] Please rename your input filenames to be shorter and/or move the")
    print("[Dispatch] Auto-Boost-Av1an-portable folder to a lower drive path, then run this again.")
    print("-" * 80)
    for path in long_paths:
        print(f"[Dispatch] {len(path)} characters: {path}")
    print("=" * 80)
    try:
        input("Press Enter to exit...")
    except EOFError:
        pass
    sys.exit(1)


def warn_and_pause_if_paths_too_long(input_files, video_output_dir, temp_dir):
    long_paths = []
    for input_path in input_files:
        filename = os.path.basename(input_path)
        basename = os.path.splitext(filename)[0]
        video_input_dir = os.path.dirname(input_path)
        paths_to_check = [
            input_path,
            os.path.join(video_output_dir, basename + "-output.mkv"),
            os.path.join(temp_dir, f"{basename}-av1.mkv"),
            os.path.join(temp_dir, f"{basename}-output.mkv"),
            os.path.join(temp_dir, f"{basename}_scd.txt"),
            os.path.join(video_input_dir, f"{basename}-av1.mkv"),
            os.path.join(video_input_dir, f"{basename}_scd.txt"),
        ]
        for path in paths_to_check:
            display_path = display_path_for_length(path)
            if len(display_path) > WINDOWS_MAX_PATH:
                long_paths.append(display_path)

    if long_paths:
        seen = set()
        unique_long_paths = []
        for path in long_paths:
            if path not in seen:
                seen.add(path)
                unique_long_paths.append(path)
        pause_for_long_paths(unique_long_paths)


# --- Input handling ----------------------------------------------------------

def sanitize_input_filenames(video_input_dir, extensions):
    """Replace parentheses in supported video filenames with safe inner spaces before processing."""
    supported_exts = {pattern[1:].lower() for pattern in extensions if pattern.startswith("*")}
    renamed = 0
    for filename in sorted(os.listdir(video_input_dir)):
        src_path = os.path.join(video_input_dir, filename)
        if not os.path.isfile(src_path):
            continue
        stem, ext = os.path.splitext(filename)
        if ext.lower() not in supported_exts or ("(" not in stem and ")" not in stem):
            continue

        safe_stem = " ".join(stem.replace("(", " ").replace(")", " ").split()) or "video"
        dst_path = os.path.join(video_input_dir, f"{safe_stem}{ext}")
        suffix = 1
        while os.path.exists(dst_path):
            dst_path = os.path.join(video_input_dir, f"{safe_stem}_{suffix}{ext}")
            suffix += 1

        os.rename(src_path, dst_path)
        renamed += 1
        print(f"[Dispatch] Renamed input file for Python-safe filename: {filename} -> {os.path.basename(dst_path)}")

    return renamed


def gather_input_files(video_input_dir, extensions):
    input_files = []
    for ext in extensions:
        input_files.extend(sorted(glob.glob(os.path.join(video_input_dir, ext))))
    return input_files


def scan_for_new_input_files(video_input_dir, extensions, known_input_files):
    """Pick up files dropped into video-input while an earlier encode was running."""
    new_files = []
    for path in gather_input_files(video_input_dir, extensions):
        if path not in known_input_files:
            known_input_files.add(path)
            new_files.append(path)
    if new_files:
        print(f"[Dispatch] Detected {len(new_files)} new input file(s) added during this run.")
    return new_files


# --- LQTC working directory --------------------------------------------------

def snapshot_lqtc_work_dirs(video_input_dir):
    """LQTC creates a hidden '.<hash>' working directory next to its input.

    The hash is computed inside the encoder, so we identify the directory by
    diffing before and after the run instead of trying to recreate it.
    """
    try:
        return {
            name for name in os.listdir(video_input_dir)
            if name.startswith(".") and os.path.isdir(os.path.join(video_input_dir, name))
        }
    except Exception:
        return set()


def move_into_temp(src, dst, label):
    if not os.path.exists(src):
        return False
    if os.path.exists(dst):
        try:
            if os.path.isdir(dst):
                shutil.rmtree(dst)
            else:
                os.remove(dst)
        except Exception as e:
            print(f"[Dispatch] Warning: Failed to clean destination {dst}: {e}")
    try:
        shutil.move(src, dst)
        print(f"[Dispatch] Moved {label}: {src} -> {dst}")
        return True
    except Exception as e:
        print(f"[Dispatch] Error moving {label}: {e}")
        return False


def main():
    # --- Configuration ---
    script_path = os.path.abspath(__file__)
    tools_dir = os.path.dirname(script_path)
    root_dir = os.path.dirname(tools_dir)

    video_input_dir = os.path.join(root_dir, "video-input")
    video_output_dir = os.path.join(root_dir, "video-output")
    temp_dir = os.path.join(root_dir, "temp")

    tag_script = os.path.join(tools_dir, "lqtc-tag.py")
    mux_script = os.path.join(tools_dir, "lqtc-mux.py")

    # --- Ensure Directories Exist ---
    if not os.path.exists(video_input_dir):
        os.makedirs(video_input_dir)
        print(f"[Dispatch] Created missing input directory: {video_input_dir}")
        sys.exit(0)
    if not os.path.exists(video_output_dir):
        os.makedirs(video_output_dir)
    if not os.path.exists(temp_dir):
        os.makedirs(temp_dir)

    # --- Argument Parsing ---
    args = sys.argv[1:]

    backend = "cuda"
    fork = "essential"
    arch = DEFAULT_ARCH
    target = ""
    crf_range = ""
    workers = "1"
    metric_workers = ""
    metric_mode = ""
    params = ""
    display_setting = DEFAULT_DISPLAY_FILE

    def has_value(index):
        """True when args[index + 1] is a value rather than the next flag.

        A blanked-out variable in the .bat (set "METRIC_MODE=") expands to
        nothing, which would otherwise make the following flag get swallowed as
        this option's value. Note --params is exempt: its value legitimately
        starts with "--" (the encoder parameters).
        """
        return index + 1 < len(args) and not args[index + 1].startswith("--")

    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--backend" and has_value(i):
            backend = args[i + 1]
            i += 2
        elif arg == "--fork" and has_value(i):
            fork = args[i + 1]
            i += 2
        elif arg == "--arch" and has_value(i):
            arch = args[i + 1]
            i += 2
        elif arg == "--avx512":
            # Kept for .bat files generated before the three-way CPU build
            # choice replaced the AVX-512 yes/no question.
            if i + 1 < len(args) and not args[i + 1].startswith("--"):
                arch = "avx512" if parse_bool_setting(args[i + 1]) else "znver2"
                i += 2
            else:
                arch = "avx512"
                i += 1
        elif arg in ("--target", "--tq") and has_value(i):
            target = args[i + 1]
            i += 2
        elif arg in ("--crf-range", "--qp-range") and has_value(i):
            crf_range = args[i + 1]
            i += 2
        elif arg == "--workers" and has_value(i):
            workers = args[i + 1]
            i += 2
        elif arg == "--metric-workers" and has_value(i):
            metric_workers = args[i + 1]
            i += 2
        elif arg == "--metric-mode" and has_value(i):
            metric_mode = args[i + 1]
            i += 2
        elif arg in ("--params", "--final-params") and i + 1 < len(args):
            # Exempt from has_value: encoder params start with "--".
            params = args[i + 1]
            i += 2
        elif arg == "--display" and has_value(i):
            display_setting = args[i + 1]
            i += 2
        else:
            i += 1

    target = target.strip()
    if not target:
        print(f"{RED}[Dispatch] ERROR: No quality target was given (--target).{RESET}")
        print(f"{RED}[Dispatch] Re-run lqtc-builder.bat to generate a valid .bat file.{RESET}")
        sys.exit(1)

    settings_path = os.path.join(root_dir, "settings.txt")
    settings = load_script_settings(settings_path)
    ntfy_settings = settings
    warn_about_unsupported_filtering(settings)
    warn_about_source_filter_override()

    lqtc_exe = resolve_lqtc_exe(tools_dir, backend, fork, arch)

    # CVVDP is the only metric that needs a display description file.
    display_path = ""
    if target_uses_cvvdp(target):
        display_path = resolve_display_file(display_setting, root_dir, tools_dir)
        if not display_path:
            print(f"{RED}[Dispatch] ERROR: CVVDP target {target} needs a display file, but{RESET}")
            print(f"{RED}[Dispatch] {DEFAULT_DISPLAY_FILE} was not found in tools\\{LQTC_DIR_NAME}.{RESET}")
            sys.exit(1)
        print(f"[Dispatch] CVVDP display description: {display_path}")

    # --- Gather Input Files ---
    extensions = ("*.mkv", "*.mp4", "*.m2ts")
    sanitize_input_filenames(video_input_dir, extensions)
    input_files = gather_input_files(video_input_dir, extensions)
    known_input_files = set(input_files)

    if not input_files:
        print(f"[Dispatch] No video files found in {video_input_dir}")
        sys.exit(0)

    warn_and_pause_if_paths_too_long(input_files, video_output_dir, temp_dir)

    print(f"[Dispatch] Found {len(input_files)} files to process.")

    # --- Main Processing Loop ---
    timing_reports = []
    batch_started_at = time.monotonic()
    input_index = 0
    while input_index < len(input_files):
        input_abspath_origin = input_files[input_index]
        input_index += 1
        filename = os.path.basename(input_abspath_origin)
        basename = os.path.splitext(filename)[0]

        final_output_path = os.path.join(video_output_dir, basename + "-output.mkv")

        print("\n" + "=" * 80)
        print(f"Processing: {filename}")
        print("=" * 80)

        if os.path.exists(final_output_path):
            print(f"[Dispatch] Output file already exists: {final_output_path}")
            continue

        try:
            av1_output = f"{basename}-av1.mkv"

            # LQTC handles decoding, scene detection, chunking, the CRF search
            # and colour metadata itself, so the command line stays short.
            cmd_lqtc = [
                lqtc_exe,
                filename,
                av1_output,
                "-e", "svt-av1",
                "-w", str(workers),
                "-t", target,
            ]
            if crf_range.strip():
                cmd_lqtc.extend(["-f", crf_range.strip()])
            if metric_mode.strip():
                cmd_lqtc.extend(["-m", metric_mode.strip()])
            if metric_workers.strip():
                cmd_lqtc.extend(["-v", metric_workers.strip()])
            if display_path:
                cmd_lqtc.extend(["-d", display_path])
            cmd_lqtc.append("--keep")
            if params.strip():
                cmd_lqtc.extend(["-p", params.strip()])

            print("[Dispatch] Starting LQTC quality target encode...")
            print(f"encoder: {lqtc_display_name(backend, fork, lqtc_exe)}")
            print(f"quality target: {target}")
            print(f"[Dispatch] Command: {subprocess.list2cmdline(cmd_lqtc)}")

            # Snapshot the hidden work dirs so we can tell which one is ours.
            work_dirs_before = snapshot_lqtc_work_dirs(video_input_dir)
            lqtc_started_at = time.monotonic()

            try:
                with keep.running():
                    # Run in video-input so LQTC's work directory and scene
                    # detection cache stay next to the source until we are done.
                    subprocess.check_call(cmd_lqtc, cwd=video_input_dir)
            except subprocess.CalledProcessError:
                print("[Dispatch] Encoding failed.")
                print("[Dispatch] LQTC resumes from its '.<hash>' folder in video-input on the next run.")
                send_ntfy_notification(
                    ntfy_settings,
                    root_dir,
                    tools_dir,
                    "Auto-Boost encode failed",
                    "An encode failed.",
                )
                continue

            lqtc_elapsed = time.monotonic() - lqtc_started_at

            # --- Move LQTC artifacts from video-input to temp ---
            print("[Dispatch] Moving encoding artifacts to temp folder...")

            # The hidden work directory holds the resume state. Moving it out of
            # video-input is what makes the next run start clean, and it also
            # puts it where cleanup.py can remove it.
            for dir_name in sorted(snapshot_lqtc_work_dirs(video_input_dir) - work_dirs_before):
                move_into_temp(
                    os.path.join(video_input_dir, dir_name),
                    os.path.join(temp_dir, dir_name),
                    "work folder",
                )

            move_into_temp(
                os.path.join(video_input_dir, f"{basename}_scd.txt"),
                os.path.join(temp_dir, f"{basename}_scd.txt"),
                "scene detection cache",
            )

            av1_file_src = os.path.join(video_input_dir, av1_output)
            if not move_into_temp(
                av1_file_src,
                os.path.join(temp_dir, av1_output),
                "encoded file",
            ):
                print(f"[Dispatch] Warning: Expected encoded file not found at {av1_file_src}")

            # --- Tagging ---
            print("[Dispatch] Applying Tags...")
            try:
                subprocess.check_call([sys.executable, tag_script], cwd=temp_dir)
            except subprocess.CalledProcessError:
                print("[Dispatch] Warning: Tagging reported an error.")

            # --- Muxing ---
            print("[Dispatch] Muxing...")
            try:
                subprocess.check_call([sys.executable, mux_script], cwd=temp_dir)
            except subprocess.CalledProcessError:
                print("[Dispatch] Muxing failed.")
                continue

            # --- Move Final Output ---
            temp_output_mkv = os.path.join(temp_dir, f"{basename}-output.mkv")

            output_moved = False
            if os.path.exists(temp_output_mkv):
                print(f"[Dispatch] Moving final file to: {final_output_path}")
                try:
                    shutil.move(temp_output_mkv, final_output_path)
                    output_moved = True
                except Exception as e:
                    print(f"[Dispatch] Error moving output file: {e}")
            else:
                print(f"[Dispatch] Error: Expected output file not found: {temp_output_mkv}")

            if output_moved:
                timing_report = {
                    "filename": filename,
                    "lqtc_encoding": lqtc_elapsed,
                }
                timing_reports.append(timing_report)
                print_lqtc_timing_report(timing_report)

                newly_detected_files = scan_for_new_input_files(video_input_dir, extensions, known_input_files)
                if newly_detected_files:
                    warn_and_pause_if_paths_too_long(newly_detected_files, video_output_dir, temp_dir)
                    input_files.extend(newly_detected_files)

        except Exception as e:
            print(f"[Dispatch] Critical Error during processing: {e}")
            send_ntfy_notification(
                ntfy_settings,
                root_dir,
                tools_dir,
                "Auto-Boost encode failed",
                "An encode failed.",
            )

    batch_elapsed = time.monotonic() - batch_started_at

    send_ntfy_notification(
        ntfy_settings,
        root_dir,
        tools_dir,
        "Auto-Boost encode complete",
        "All queued encodes are complete.",
    )

    print("\n" + "=" * 80)
    print("LQTC Quality Target Batch Complete.")
    print("=" * 80)

    if timing_reports:
        print("\nFinal LQTC time reports:")
        for timing_report in timing_reports:
            print_lqtc_timing_report(timing_report)

    print("\nTime format legend: hh:mm:ss = hours:minutes:seconds")
    print(f"Total time for all files: {format_elapsed_hhmmss(batch_elapsed)}")


if __name__ == "__main__":
    main()
