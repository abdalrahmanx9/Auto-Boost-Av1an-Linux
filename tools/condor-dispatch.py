import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import copy
import importlib.util
import json
import re
import shutil
import subprocess
import time

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

# Condor is a fork of av1an and lives beside av1an in tools/av1an, so it
# already has ffmpeg, mkvmerge, SvtAv1EncApp and the ffms2/avcodec libs next to
# it. Everything else this dispatcher needs is shared with av1an-dispatch.py.
CONDOR_EXE_NAME = "condor"
AV1AN_DIR_NAME = "av1an"

# Metrics that Condor measures through the vship VapourSynth plugin
# (com.lumen.vship). xpsnr goes through vszip, which the system autoloads,
# so it needs no activation.
VSHIP_METRICS = ("ssimulacra2", "butteraugli", "butteraugli-3", "cvvdp")

VSHIP_DLLS = {
    "nvidia": "libvship_NVIDIA.so",
    "amd": "libvship_AMD.so",
    "vulkan": "libvship_VULKAN.so",
}

# Written by tools\ssimu2-workercount.py, the benchmark the generated .bat runs
# on its first use (and the same one bat-builder.py's .bat files run). It
# measures every vship/FFVship build this machine can load and records the
# winner as tool=/variant=. Only those two lines are read here: the worker and
# stream counts in that file belong to the Auto-Boost metrics pass, while
# Condor's worker count comes from WORKERS in the .bat.
SSIMU2_CONFIG_NAME = "workercount-ssimu2.txt"

# GPU vendor -> the vship build that runs on it. Intel has no dedicated build,
# so Vulkan is its only path.
VENDOR_BACKENDS = {"nvidia": "nvidia", "amd": "amd", "intel": "vulkan"}


def _load_av1an_dispatch():
    """Import av1an-dispatch.py as a module so its helpers can be reused here.

    Condor is a fork of av1an, so the surrounding pipeline is identical: the
    same settings.txt filter chain, the same MediaInfo colour detection, the
    same filename sanitising, path-length guard and ntfy notifications. Importing
    keeps one copy of all of that instead of a second one that drifts. The
    hyphen in the filename is why this needs importlib rather than a plain
    import statement.
    """
    tools_dir = os.path.dirname(os.path.abspath(__file__))
    module_path = os.path.join(tools_dir, "av1an-dispatch.py")
    if not os.path.exists(module_path):
        print(f"{RED}[Dispatch] ERROR: tools\\av1an-dispatch.py was not found.{RESET}")
        print(f"{RED}[Dispatch] Your install may be incomplete - try re-downloading the package.{RESET}")
        sys.exit(1)

    spec = importlib.util.spec_from_file_location("av1an_dispatch", module_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["av1an_dispatch"] = module
    try:
        spec.loader.exec_module(module)
    except Exception as e:
        print(f"{RED}[Dispatch] ERROR: Could not load tools\\av1an-dispatch.py: {e}{RESET}")
        sys.exit(1)
    return module


ad = _load_av1an_dispatch()


def format_elapsed_hhmmss(seconds):
    return ad.format_elapsed_hhmmss(seconds)


def print_condor_timing_report(report):
    print("\n" + "-" * 80)
    print(f"Condor time report for: {report['filename']}")
    print("Time format legend: hh:mm:ss = hours:minutes:seconds")
    print(f"Scene detection time: {format_elapsed_hhmmss(report['scene_detection'])}")
    print(f"Condor encoding time: {format_elapsed_hhmmss(report['condor_encoding'])}")
    print("-" * 80)


# --- Portable environment ----------------------------------------------------

def ensure_vsscript_path(root_dir):
    """Point Condor at the VapourSynth script API library.

    Condor opens VapourSynth through VSScript rather than vspipe. On Linux the
    equivalent is libvapoursynth-script.so, located through ldconfig / the
    standard library paths and exported via VSSCRIPT_PATH (Windows builds of
    Condor read it; a native Linux build may link it directly, in which case
    setting the variable is harmless).
    """
    configured = (os.environ.get("VSSCRIPT_PATH") or "").strip().strip('"')
    if configured and os.path.exists(configured):
        return configured

    import shutil as _sh
    candidates = []
    found = _sh.which("vspipe")
    if found:
        # libvapoursynth-script usually sits beside libvapoursynth in the
        # library dir; probe the common spots.
        import subprocess as _sp
        try:
            libdir = _sp.run(["pkg-config", "--variable=libdir", "vapoursynth"],
                             capture_output=True, text=True).stdout.strip()
            if libdir:
                candidates.append(os.path.join(libdir, "libvapoursynth-script.so"))
        except Exception:
            pass
    candidates += [
        "/usr/local/lib/libvapoursynth-script.so",
        "/usr/lib/x86_64-linux-gnu/libvapoursynth-script.so",
        "/usr/lib/libvapoursynth-script.so",
    ]
    for candidate in candidates:
        if os.path.exists(candidate):
            os.environ["VSSCRIPT_PATH"] = candidate
            return candidate

    print(f"{RED}[Dispatch] WARNING: libvapoursynth-script.so was not found.{RESET}")
    print(f"{RED}[Dispatch] Condor needs it to decode and will fail without it.{RESET}")
    return ""


def resolve_condor_exe(tools_dir):
    condor_exe = os.path.join(tools_dir, AV1AN_DIR_NAME, CONDOR_EXE_NAME)
    if os.path.exists(condor_exe):
        return condor_exe
    import shutil as _sh
    in_path = _sh.which("condor")
    if in_path:
        return in_path

    print(f"{RED}[Dispatch] ERROR: Could not find {CONDOR_EXE_NAME} in tools/{AV1AN_DIR_NAME} or PATH.{RESET}")
    print(f"{RED}[Dispatch] Supply a Linux condor binary - see setup.sh (tq_encoders).{RESET}")
    try:
        input("Press Enter to exit...")
    except EOFError:
        pass
    sys.exit(1)


def read_ssimu2_backend(tools_dir):
    """The GPU backend the metric benchmark settled on, or None.

    tools\\workercount-ssimu2.txt records the winner as tool= (vs-hip, ffvship_*
    or vs-zip) plus variant= (nvidia, amd, vulkan or cpu). Condor cares only
    which GPU build won, since vs-hip and FFVship are two front ends onto the
    same vship backends. A vs-zip win means the CPU out-ran every GPU backend,
    which says nothing about which build loads, so that returns None and the
    caller falls back to vendor detection.
    """
    path = os.path.join(tools_dir, SSIMU2_CONFIG_NAME)
    config = {}
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                config[key.strip().lower().replace("_", "-")] = value.strip()
    except OSError:
        return None

    variant = config.get("variant", "").strip().lower()
    if variant in VSHIP_DLLS:
        return variant

    # Older configs, or ones written before variant= existed, carry the backend
    # in the tool name instead (ffvship_nvidia, vs-hip-amd, ...).
    tool = config.get("tool", "").strip().lower().replace("_", "-")
    for backend in VSHIP_DLLS:
        if backend in tool:
            return backend
    return None


def detect_gpu_backend():
    """The vship build matching this machine's dedicated GPU, or None."""
    try:
        from gpu_vendor import gpu_vendor
        vendor = gpu_vendor()
    except Exception:
        return None
    return VENDOR_BACKENDS.get(vendor)


def resolve_gpu_backend(tools_dir, requested):
    """Decide which libvship build to activate for the GPU metrics.

    An explicit GPU= in the .bat always wins. Otherwise the benchmark result is
    used, and if that produced no GPU winner the dedicated GPU's vendor decides.
    Vulkan is the last resort because it is the one build that runs on NVIDIA,
    AMD and Intel alike.
    """
    requested = (requested or "").strip().lower()
    if requested in VSHIP_DLLS:
        print(f"[Dispatch] Metric GPU backend: {requested} (set as GPU in the .bat).")
        return requested
    if requested:
        print(f"{RED}[Dispatch] GPU={requested} in the .bat is not a known backend "
              f"(nvidia, amd, vulkan). Detecting one instead.{RESET}")

    backend = read_ssimu2_backend(tools_dir)
    if backend:
        print(f"[Dispatch] Metric GPU backend: {backend} "
              f"(fastest in tools\\{SSIMU2_CONFIG_NAME}).")
        return backend

    backend = detect_gpu_backend()
    if backend:
        print(f"[Dispatch] Metric GPU backend: {backend} (detected GPU vendor; "
              f"the benchmark named no GPU winner).")
        return backend

    print(f"{RED}[Dispatch] No GPU backend could be determined, so vulkan is being used:{RESET}")
    print(f"{RED}[Dispatch] it is the one build that runs on NVIDIA, AMD and Intel.{RESET}")
    print(f"{RED}[Dispatch] Delete tools\\{SSIMU2_CONFIG_NAME} and run the .bat again to{RESET}")
    print(f"{RED}[Dispatch] re-benchmark, or set GPU= in the .bat by hand.{RESET}")
    return "vulkan"


def _linux_plugin_dirs():
    """System VapourSynth autoload directories on Linux, first writable wins."""
    import subprocess as _sp
    dirs = []
    try:
        libdir = _sp.run(["pkg-config", "--variable=libdir", "vapoursynth"],
                         capture_output=True, text=True).stdout.strip()
        if libdir:
            dirs.append(os.path.join(libdir, "vapoursynth"))
    except Exception:
        pass
    dirs += ["/usr/local/lib/vapoursynth",
             "/usr/lib/x86_64-linux-gnu/vapoursynth",
             "/usr/lib/vapoursynth"]
    return [d for d in dirs if os.path.isdir(d)]


def activate_vship_plugin(root_dir, tools_dir, gpu):
    """Activate the requested libvship build in a VapourSynth autoload dir.

    Condor measures ssimulacra2, butteraugli and cvvdp with the vship plugin
    (com.lumen.vship), which this package keeps out of the autoload folder in
    tools/vs-hip so the NVIDIA, AMD and Vulkan builds cannot register the same
    plugin namespace at once. Only the selected one is copied in.
    """
    gpu_key = (gpu or "nvidia").strip().lower()
    if gpu_key not in VSHIP_DLLS:
        gpu_key = "nvidia"
    dll_name = VSHIP_DLLS[gpu_key]

    plugin_dirs = _linux_plugin_dirs()
    if not plugin_dirs:
        print(f"{RED}[Dispatch] WARNING: no VapourSynth plugin autoload folder found.{RESET}")
        return ""

    # Two libvship builds in the autoload folder collide on the same plugin ID,
    # so the unselected one has to go. A busy .so means VapourSynth already
    # loaded it in this session; leaving it is better than failing the run.
    for plugin_dir in plugin_dirs:
        for other_key, other_name in VSHIP_DLLS.items():
            if other_key == gpu_key:
                continue
            other_path = os.path.join(plugin_dir, other_name)
            if os.path.exists(other_path):
                try:
                    os.remove(other_path)
                    print(f"[Dispatch] Removed unused metric plugin: {other_name}")
                except Exception:
                    print(f"[Dispatch] Note: {other_name} is in use and was left in place.")

    dst = os.path.join(plugin_dirs[0], dll_name)
    src = os.path.join(tools_dir, "vs-hip", dll_name)

    if os.path.exists(dst):
        print(f"[Dispatch] Metric plugin active: {dll_name} ({gpu_key})")
        return dst

    if not os.path.exists(src):
        print(f"{RED}[Dispatch] WARNING: tools/vs-hip/{dll_name} was not found.{RESET}")
        print(f"{RED}[Dispatch] GPU metrics will fail. Use --metric xpsnr, or re-run setup.sh ffvship.{RESET}")
        return ""

    for plugin_dir in plugin_dirs:
        dst = os.path.join(plugin_dir, dll_name)
        try:
            shutil.copy2(src, dst)
            print(f"[Dispatch] Activated metric plugin: {dll_name} ({gpu_key})")
            return dst
        except PermissionError:
            continue
        except Exception as e:
            print(f"{RED}[Dispatch] WARNING: Could not copy {dll_name} into {plugin_dir}: {e}{RESET}")
    print(f"{RED}[Dispatch] WARNING: no writable autoload dir for {dll_name} (run setup.sh as root once).{RESET}")
    return ""


# --- Scene handling ----------------------------------------------------------

def read_scene_cuts(scenes_json_path):
    """Read an av1an-style scenes JSON into a list of (start_frame, end_frame).

    This is what Progressive-Scene-Detection.py writes, and it is the same file
    av1an-dispatch.py hands to av1an with -s. Condor has no equivalent flag, so
    the cuts get written into its config instead (see inject_scenes).
    """
    with open(scenes_json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    raw_scenes = data.get("scenes") or data.get("split_scenes") or []
    cuts = []
    for scene in raw_scenes:
        try:
            start = int(scene["start_frame"])
            end = int(scene["end_frame"])
        except (KeyError, TypeError, ValueError):
            continue
        if end > start:
            cuts.append((start, end))

    cuts.sort()
    return cuts, data.get("frames")


def inject_scenes(config_path, cuts):
    """Write the scene list into an existing condor.json.

    Condor keeps its scenes inside the config rather than in a separate file,
    so replacing them here and running with --skip-scd is what makes it use
    this package's scene detection instead of its own AVSceneChange pass.

    Every scene carries a full copy of the top-level encoder block, exactly as
    Condor's own detect-scenes writes it, because Target Quality rewrites the
    per-scene quantizer in place. The sequence_data members below are not
    optional: Condor's config loader rejects the file outright ("Failed to load
    config file") if scene_detection, parallel_encoder or target_quality is
    null, so they are written as empty-but-present records.
    """
    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    encoder = config.get("condor", {}).get("encoder")
    if encoder is None:
        raise ValueError("condor.json has no encoder block to copy into the scenes")

    now = time.time()
    created_on = {
        "secs_since_epoch": int(now),
        "nanos_since_epoch": int((now - int(now)) * 1_000_000_000),
    }

    scenes = []
    for start, end in cuts:
        scenes.append({
            "start_frame": start,
            "end_frame": end,
            "sub_scenes": None,
            "encoder": copy.deepcopy(encoder),
            "sequence_data": {
                "scene_detection": {
                    "scenecut_scores": {},
                    "created_on": dict(created_on),
                },
                "noise_detection": None,
                "noise_scaling": None,
                "parallel_encoder": {
                    "started_on": None,
                    "completed_on": None,
                    "bytes": None,
                },
                "target_quality": {"passes": []},
            },
        })

    config["condor"]["scenes"] = scenes

    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)

    return len(scenes)


def apply_target_profile(config_path, profile):
    """Write the Target Quality probe strategy into condor.json ourselves.

    Condor's own --target-profile standard stores the probe length as
    SubsetProbeLength::Percentage(25.0), but the code that turns that into
    frame numbers multiplies it by the scene length directly
    (percentage * frames), i.e. it wants a fraction. 25.0 therefore means
    2500 percent: the middle subset starts thousands of frames before the
    scene does, the subtraction underflows, and every probe ends up with an
    empty frame list. Condor then encodes zero frames per scene, writes
    0-byte .ivf probes and dies at "Failed to concatenate with mkvmerge:
    exit code: 2" without any encoder error, because the encoder itself
    exited fine.

    So the profile is written into the config as a fraction and
    --target-profile is never passed on the command line - passing it would
    overwrite this with 25.0 again. fast and slow do not use Percentage and
    are unaffected, but they are written here too so all three come from one
    place.
    """
    profiles = {
        "fast": (
            {"Subset": {"position": "Middle", "length": {"Frames": 11}}},
            "Mean",
        ),
        "standard": (
            {"Subset": {"position": "Middle", "length": {"Percentage": 0.25}}},
            "RootMeanSquare",
        ),
        "slow": ("Whole", {"Percentile": 10.0}),
    }

    key = (profile or "standard").strip().lower()
    if key not in profiles:
        print(f"[Dispatch] Unknown target profile '{profile}'; using standard.")
        key = "standard"
    strategy, statistic = profiles[key]

    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    target_quality = config.get("condor", {}).get("sequence_config", {}).get("target_quality")
    if not isinstance(target_quality, dict):
        # No Target Quality in this config: nothing to profile.
        return key

    probing = target_quality.get("probing")
    if not isinstance(probing, dict):
        probing = {"encoder_options": None}
        target_quality["probing"] = probing
    probing["strategy"] = strategy
    probing["statistic"] = statistic

    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)

    return key


def purge_empty_scene_files(condor_temp_dir):
    """Delete 0-byte scene and probe files left behind by a failed run.

    Condor resumes by skipping every scene whose output file already exists -
    it never checks the size. One run that wrote empty .ivf files (see
    apply_target_profile) therefore poisons the temp folder: every later run
    skips straight to concatenation and fails there again, even once the cause
    is fixed. Clearing the empty ones makes those scenes encode again while
    genuinely finished scenes are still resumed.
    """
    if not os.path.isdir(condor_temp_dir):
        return 0

    media_extensions = (".ivf", ".obu", ".mkv", ".webm", ".mp4", ".av1")
    removed = 0
    for dirpath, _dirnames, filenames in os.walk(condor_temp_dir):
        for name in filenames:
            if not name.lower().endswith(media_extensions):
                continue
            path = os.path.join(dirpath, name)
            try:
                if os.path.getsize(path) == 0:
                    os.remove(path)
                    removed += 1
            except OSError:
                pass

    if removed:
        print(f"[Dispatch] Removed {removed} empty scene files from a previous failed run.")
    return removed


def config_has_scenes(config_path):
    """True when a previous run already built this config and filled in scenes."""
    if not os.path.exists(config_path):
        return False
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
        return bool(config.get("condor", {}).get("scenes"))
    except Exception:
        return False


def config_scene_cuts(config_path):
    """The (start, end) frame ranges saved in an existing condor.json."""
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
        scenes = config.get("condor", {}).get("scenes") or []
    except Exception:
        return []
    cuts = []
    for scene in scenes:
        try:
            cuts.append((int(scene["start_frame"]), int(scene["end_frame"])))
        except (KeyError, TypeError, ValueError):
            continue
    cuts.sort()
    return cuts


def report_target_quality(metric, target, min_q, max_q, profile):
    print(f"target metric:  {metric}")
    print(f"target score:   {target}")
    print(f"quantizer range: {min_q}-{max_q}")
    print(f"target profile: {profile}")


# --- Argument parsing --------------------------------------------------------

def parse_args(args):
    parsed = {
        "fork": "essential",
        "arch": "x86-64-v3",
        "denoise": None,
        "tonemap": False,
        "autocrop": False,
        "metric": "ssimulacra2",
        "target": "",
        "min_quantizer": "",
        "max_quantizer": "",
        "target_profile": "standard",
        "decoder": "bestsource",
        "concat": "mkvmerge",
        # Empty means "work it out": the benchmark result first, then the
        # detected GPU vendor. Only a GPU= filled in by hand overrides that.
        "gpu": "",
        "workers": "",
        "photon_noise": "",
        "final_speed": "4",
        "final_params": "",
        "verbose": False,
    }

    def has_value(index):
        """True when args[index + 1] is a value rather than the next flag.

        A blanked-out variable in the .bat (set "WORKERS=") expands to nothing,
        which would otherwise make the following flag get swallowed as this
        option's value. --final-params is exempt: its value legitimately starts
        with "--".
        """
        return index + 1 < len(args) and not args[index + 1].startswith("--")

    def flag_or_value(index, key):
        if has_value(index):
            parsed[key] = ad.parse_bool_setting(args[index + 1])
            return index + 2
        parsed[key] = True
        return index + 1

    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--fork" and has_value(i):
            parsed["fork"] = args[i + 1]
            i += 2
        elif arg == "--arch" and has_value(i):
            parsed["arch"] = args[i + 1]
            i += 2
        elif arg == "--avx512":
            # Kept for .bat files generated before ARCH replaced AVX512.
            if has_value(i):
                parsed["arch"] = "avx512" if ad.parse_bool_setting(args[i + 1]) else "x86-64-v3"
                i += 2
            else:
                parsed["arch"] = "avx512"
                i += 1
        elif arg == "--tonemap":
            i = flag_or_value(i, "tonemap")
        elif arg == "--denoise" and has_value(i):
            parsed["denoise"] = "True" if ad.parse_bool_setting(args[i + 1]) else "False"
            i += 2
        elif arg == "--autocrop":
            i = flag_or_value(i, "autocrop")
        elif arg in ("--metric", "--target-metric") and has_value(i):
            parsed["metric"] = args[i + 1]
            i += 2
        elif arg == "--target" and has_value(i):
            parsed["target"] = args[i + 1]
            i += 2
        elif arg in ("--min-quantizer", "--minimum-quantizer") and has_value(i):
            parsed["min_quantizer"] = args[i + 1]
            i += 2
        elif arg in ("--max-quantizer", "--maximum-quantizer") and has_value(i):
            parsed["max_quantizer"] = args[i + 1]
            i += 2
        elif arg == "--target-profile" and has_value(i):
            parsed["target_profile"] = args[i + 1]
            i += 2
        elif arg == "--decoder" and has_value(i):
            parsed["decoder"] = args[i + 1]
            i += 2
        elif arg == "--concat" and has_value(i):
            parsed["concat"] = args[i + 1]
            i += 2
        elif arg == "--gpu" and has_value(i):
            parsed["gpu"] = args[i + 1]
            i += 2
        elif arg == "--workers" and has_value(i):
            parsed["workers"] = args[i + 1]
            i += 2
        elif arg == "--photon-noise" and has_value(i):
            parsed["photon_noise"] = args[i + 1]
            i += 2
        elif arg == "--final-speed" and has_value(i):
            parsed["final_speed"] = args[i + 1]
            i += 2
        elif arg in ("--final-params", "--params") and i + 1 < len(args):
            # Exempt from has_value: encoder params start with "--".
            parsed["final_params"] = args[i + 1]
            i += 2
        elif arg == "--verbose":
            parsed["verbose"] = True
            i += 1
        elif arg == "--no-verbose":
            parsed["verbose"] = False
            i += 1
        else:
            i += 1

    return parsed


def main():
    # --- Configuration ---
    script_path = os.path.abspath(__file__)
    tools_dir = os.path.dirname(script_path)
    root_dir = os.path.dirname(tools_dir)

    video_input_dir = os.path.join(root_dir, "video-input")
    video_output_dir = os.path.join(root_dir, "video-output")
    temp_dir = os.path.join(root_dir, "temp")

    scene_detect_script = os.path.join(tools_dir, "Progressive-Scene-Detection.py")
    mediainfo_exe = shutil.which("mediainfo") or os.path.join(tools_dir, "MediaInfo_CLI", "MediaInfo")
    tag_script = os.path.join(tools_dir, "condor-tag.py")
    mux_script = os.path.join(tools_dir, "av1an-mux.py")

    # --- Ensure Directories Exist ---
    if not os.path.exists(video_input_dir):
        os.makedirs(video_input_dir)
        print(f"[Dispatch] Created missing input directory: {video_input_dir}")
        sys.exit(0)
    if not os.path.exists(video_output_dir):
        os.makedirs(video_output_dir)
    if not os.path.exists(temp_dir):
        os.makedirs(temp_dir)

    options = parse_args(sys.argv[1:])

    target = (options["target"] or "").strip()
    if not target:
        print(f"{RED}[Dispatch] ERROR: No quality target was given (--target).{RESET}")
        print(f"{RED}[Dispatch] Re-run condor-builder.bat to generate a valid .bat file.{RESET}")
        sys.exit(1)

    metric = (options["metric"] or "ssimulacra2").strip().lower()
    target_profile = (options["target_profile"] or "standard").strip().lower()
    decoder = (options["decoder"] or "bestsource").strip().lower()
    concat = (options["concat"] or "mkvmerge").strip().lower()
    workers = (options["workers"] or "").strip()
    final_speed = (options["final_speed"] or "4").strip()
    min_quantizer = (options["min_quantizer"] or "").strip()
    max_quantizer = (options["max_quantizer"] or "").strip()

    # Rewrite relative --fgs-table paths to absolute ones anchored at the package
    # root, so SvtAv1EncApp can open the table whatever directory Condor spawns
    # its workers in.
    final_params = ad.resolve_fgs_table_path(options["final_params"], root_dir, tools_dir)

    # Photon noise is Condor's own option, not an encoder parameter: Condor
    # builds the grain table and applies it, so it goes on the condor.exe
    # command line. tools\av1an\condor.txt states it must not be combined with
    # the encoder's internal film grain synthesis, so --film-grain in the
    # parameters wins and photon noise is dropped. A --photon-noise left in an
    # older .bat's params is removed as well, so it cannot be applied twice.
    photon_noise = (options["photon_noise"] or "").strip()
    if photon_noise:
        if re.search(r"(?<!\S)--film-grain(?!-denoise)(?!\S)", final_params):
            print(f"{RED}[Dispatch] --film-grain is in the encoder parameters, so Condor's{RESET}")
            print(f"{RED}[Dispatch] photon noise (ISO {photon_noise}) will not be used. Blank out{RESET}")
            print(f"{RED}[Dispatch] PHOTON_NOISE in the .bat to silence this.{RESET}")
            photon_noise = ""
        else:
            stripped = re.sub(r"(?<!\S)--photon-noise(?:\s+\S+)?(?!\S)", "", final_params)
            if stripped != final_params:
                print("[Dispatch] Removed --photon-noise from the encoder parameters; "
                      f"Condor applies it instead (ISO {photon_noise}).")
                final_params = " ".join(stripped.split())

    # --- Portable environment ---
    ensure_vsscript_path(root_dir)
    condor_exe = resolve_condor_exe(tools_dir)
    if metric in VSHIP_METRICS:
        activate_vship_plugin(root_dir, tools_dir,
                              resolve_gpu_backend(tools_dir, options["gpu"]))

    # --- settings.txt ---
    settings_path = os.path.join(root_dir, "settings.txt")
    settings = None
    if options["denoise"] is not None:
        try:
            settings = ad.set_settings_value(settings_path, "denoise", options["denoise"])
            print(f"[Dispatch] Set settings.txt denoise={options['denoise']}")
        except Exception as e:
            print(f"[Dispatch] Warning: Failed to update settings.txt denoise: {e}")
    if settings is None:
        settings = ad.load_script_settings(settings_path)
    ntfy_settings = settings

    ad.setup_svt_av1_fork(tools_dir, options["fork"], arch=options["arch"], verbose=True)

    # The .vpy comes from av1an-dispatch's builder, so the override applies here
    # too. Condor's own --decoder is separate: it only matters for inputs Condor
    # opens itself, and Condor is handed the .vpy.
    source_filter_override = source_filter.read_override()
    if source_filter_override:
        print(f"{BLUE}[Dispatch] Source filter override active: "
              f"{source_filter.describe(source_filter_override)}{RESET}")

    # --- Gather Input Files ---
    extensions = ("*.mkv", "*.mp4", "*.m2ts")
    # The rename is permanent - a source file keeps its safe name for good, so a
    # second run finds nothing left to rename.
    ad.sanitize_input_filenames(video_input_dir, extensions)
    input_files = ad.gather_input_files(video_input_dir, extensions)
    known_input_files = set(input_files)

    if not input_files:
        print(f"[Dispatch] No video files found in {video_input_dir}")
        sys.exit(0)

    ad.warn_and_pause_if_paths_too_long(input_files, video_output_dir, temp_dir)

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
            # 1. Scene Detection (this package's, not Condor's)
            json_file = f"{basename}_scenedetect.json"
            json_abspath = os.path.join(temp_dir, json_file)

            scene_detection_elapsed = 0.0
            scenes_override = source_filter.read_override()
            scenes_are_current = (os.path.exists(json_abspath)
                                  and source_filter.scenes_marker_matches(json_abspath, scenes_override))
            if scenes_are_current:
                print("[Dispatch] Skipping scene detection (JSON exists).")
            else:
                if os.path.exists(json_abspath):
                    # Scene frame numbers only line up with the .vpy while both
                    # decode the source the same way.
                    print("[Dispatch] Source filter changed since these scenes were detected; re-running scene detection.")
                print("[Dispatch] Running Scene Detection...")
                cmd_scene = [
                    sys.executable,
                    scene_detect_script,
                    "-i", input_abspath_origin,
                    "-o", json_file,
                ]
                scene_detection_started_at = time.monotonic()
                try:
                    subprocess.check_call(cmd_scene, cwd=temp_dir, env=ad.scene_detection_env())
                    source_filter.write_scenes_marker(json_abspath, scenes_override)
                except subprocess.CalledProcessError:
                    print("[Dispatch] Scene detection failed.")
                scene_detection_elapsed = time.monotonic() - scene_detection_started_at

            if not os.path.exists(json_abspath):
                print(f"{RED}[Dispatch] ERROR: No scenes file was produced for {filename}.{RESET}")
                print(f"{RED}[Dispatch] Condor is run with --skip-scd and cannot fall back to its own detector.{RESET}")
                continue

            cuts, detected_frames = read_scene_cuts(json_abspath)
            if not cuts:
                print(f"{RED}[Dispatch] ERROR: {json_file} contains no usable scenes.{RESET}")
                continue

            # 2. Colour Space Detection / HDR handling
            color_metadata = ad.detect_color_metadata(input_abspath_origin, mediainfo_exe)
            is_bt709 = bool(color_metadata and color_metadata["is_bt709"])
            is_bt601 = bool(color_metadata and color_metadata["is_bt601"])
            is_hdr_source = bool(color_metadata and color_metadata["is_hdr"])
            tonemap_this_file = options["tonemap"] and is_hdr_source

            bt709_flags = " --color-primaries 1 --transfer-characteristics 1 --matrix-coefficients 1"
            bt601_flags = " --color-primaries 6 --transfer-characteristics 6 --matrix-coefficients 6"
            current_color_flags = ""
            if tonemap_this_file:
                # Tonemapped output is SDR BT.709.
                current_color_flags = bt709_flags
                # build_vapoursynth_script picks libplacebo or the CPU fallback and
                # logs which one it settled on.
                print(f"{BLUE}[Dispatch] HDR source detected; tonemapping HDR to SDR (BT.709).{RESET}")
            elif is_hdr_source and ad.is_hdr_fork(options["fork"]):
                hdr_flags = ad.build_hdr_color_flags(color_metadata)
                if hdr_flags:
                    current_color_flags = hdr_flags
                    print(f"{BLUE}[Dispatch] HDR source detected; auto-applying SVT-AV1-HDR color settings:{RESET}")
                    print(f"{BLUE}[Dispatch]  {hdr_flags.strip()}{RESET}")
                else:
                    ad.pause_for_hdr_color_settings(input_abspath_origin, color_metadata)
            elif is_hdr_source:
                print(f"{BLUE}[Dispatch] HDR source detected. This fork encodes it as-is (set tonemap=True in the .bat to tonemap to SDR).{RESET}")
            elif is_bt709:
                current_color_flags = bt709_flags
                print("[Dispatch] MediaInfo confirmed full BT.709 source.")
            elif is_bt601:
                current_color_flags = bt601_flags
                print("[Dispatch] MediaInfo confirmed full BT.601 source.")

            # 3. Build the VapourSynth input script from settings.txt
            # Condor takes a .vpy directly, so the crop/downscale/dehalo/denoise/
            # deband chain reaches it exactly as it reaches av1an.
            vpy_abspath = ad.build_vapoursynth_script(
                input_abspath_origin,
                temp_dir,
                tools_dir,
                settings,
                autocrop=options["autocrop"],
                convert_yuv420p10=False,
                tonemap=tonemap_this_file,
            )

            # 4. Encoder parameters.
            # No --crf here: Target Quality chooses a quantizer per scene and
            # writes it into the config.
            encoder_params = f"--preset {final_speed} {final_params}"
            if current_color_flags:
                encoder_params += current_color_flags
            encoder_params = " ".join(encoder_params.split())

            av1_output = os.path.join(temp_dir, f"{basename}-av1.mkv")
            config_path = os.path.join(temp_dir, f"{basename}-condor.json")
            condor_temp_dir = os.path.join(temp_dir, f"{basename}-condor")

            # A saved config whose scenes are not the ones just detected - after a
            # re-detection under a different source filter, say - would carry on
            # encoding the old frame ranges, and the finished scene files Condor
            # resumes from belong to that old list too. Start that encode over
            # rather than mixing the two.
            if config_has_scenes(config_path) and config_scene_cuts(config_path) != cuts:
                try:
                    os.remove(config_path)
                    print("[Dispatch] The saved Condor config was built from an older scene list; discarding it.")
                except OSError as e:
                    print(f"{RED}[Dispatch] Warning: could not remove the stale Condor config: {e}{RESET}")
                if os.path.isdir(condor_temp_dir):
                    try:
                        shutil.rmtree(condor_temp_dir)
                        print(f"[Dispatch] Discarded temp\\{basename}-condor, which held scenes "
                              f"encoded from that older list.")
                    except OSError as e:
                        print(f"{RED}[Dispatch] Warning: could not remove {condor_temp_dir}: {e}{RESET}")

            # 5. Build the Condor config, then replace its scenes with ours.
            # A config that already has scenes is a run that was interrupted:
            # leave it alone so Condor resumes from its own temp folder.
            if config_has_scenes(config_path):
                print("[Dispatch] Existing Condor config found; resuming that encode.")
                print(f"[Dispatch] Delete temp\\{basename}-condor.json to start it over.")
            else:
                if os.path.exists(config_path):
                    # Condor refuses to initialize over an existing config file.
                    os.remove(config_path)

                cmd_init = [
                    condor_exe, "init",
                    vpy_abspath,
                    av1_output,
                    "--config-file", config_path,
                    "--temp", condor_temp_dir,
                    "-e", "svt-av1",
                    "--decoder", decoder,
                    "--concat", concat,
                    "--target-metric", metric,
                    "--target", target,
                    "--params", encoder_params,
                ]
                if photon_noise:
                    cmd_init.extend(["--photon-noise", photon_noise])
                if workers:
                    cmd_init.extend(["-w", workers])

                print("[Dispatch] Building Condor configuration...")
                print(f"[Dispatch] Command: {subprocess.list2cmdline(cmd_init)}")
                try:
                    subprocess.check_call(cmd_init, cwd=video_input_dir)
                except subprocess.CalledProcessError:
                    print(f"{RED}[Dispatch] Condor could not build a configuration for this file.{RESET}")
                    continue

                scene_count = inject_scenes(config_path, cuts)
                print(f"[Dispatch] Wrote {scene_count} scenes from {json_file} into the Condor config.")
                if detected_frames and cuts[-1][1] != int(detected_frames):
                    print(f"{RED}[Dispatch] Warning: scenes end at frame {cuts[-1][1]} but the scenes file reports {detected_frames} frames.{RESET}")

            # 6. Run the rest of the Condor pipeline on our scenes.
            # The probe strategy goes into the config rather than on the command
            # line; see apply_target_profile for why --target-profile cannot be
            # used here. This runs on resumes too, so an older config written by
            # a previous version of this script gets corrected as well.
            try:
                target_profile = apply_target_profile(config_path, target_profile)
            except Exception as e:
                print(f"{RED}[Dispatch] ERROR: Could not write the target profile into {os.path.basename(config_path)}: {e}{RESET}")
                continue

            purge_empty_scene_files(condor_temp_dir)

            cmd_condor = [
                condor_exe,
                "--config-file", config_path,
                "--temp", condor_temp_dir,
                "--skip-scd",
            ]
            if min_quantizer:
                cmd_condor.extend(["--minimum-quantizer", min_quantizer])
            if max_quantizer:
                cmd_condor.extend(["--maximum-quantizer", max_quantizer])
            if options["verbose"]:
                cmd_condor.append("--verbose")

            print("[Dispatch] Starting Condor Encoding...")
            print(f"svt-av1 fork: {ad.svt_fork_display_name(options['fork'])}")
            report_target_quality(metric, target, min_quantizer or "encoder default",
                                  max_quantizer or "encoder default", target_profile)
            print(f"[Dispatch] Command: {subprocess.list2cmdline(cmd_condor)}")

            condor_started_at = time.monotonic()
            try:
                with keep.running():
                    # Run in video-input so Condor's logs folder lands where
                    # cleanup.py already looks for it.
                    subprocess.check_call(cmd_condor, cwd=video_input_dir)
            except subprocess.CalledProcessError:
                print("[Dispatch] Encoding failed.")
                print(f"[Dispatch] Run the same .bat again to resume from temp\\{basename}-condor.")
                ad.send_ntfy_notification(
                    ntfy_settings,
                    root_dir,
                    tools_dir,
                    "Auto-Boost encode failed",
                    "An encode failed.",
                )
                continue

            condor_elapsed = time.monotonic() - condor_started_at

            if not os.path.exists(av1_output):
                print(f"{RED}[Dispatch] Error: Expected encoded file not found: {av1_output}{RESET}")
                continue

            # 7. Tagging
            print("[Dispatch] Applying Tags...")
            try:
                subprocess.check_call([sys.executable, tag_script], cwd=temp_dir)
            except subprocess.CalledProcessError:
                print("[Dispatch] Warning: Tagging reported an error.")

            # 8. Muxing (shared with av1an: same -av1.mkv naming, same temp layout)
            print("[Dispatch] Muxing...")
            try:
                subprocess.check_call([sys.executable, mux_script], cwd=temp_dir)
            except subprocess.CalledProcessError:
                print("[Dispatch] Muxing failed.")
                continue

            # 9. Move Final Output
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
                    "scene_detection": scene_detection_elapsed,
                    "condor_encoding": condor_elapsed,
                }
                timing_reports.append(timing_report)
                print_condor_timing_report(timing_report)

                newly_detected_files = ad.scan_for_new_input_files(video_input_dir, extensions, known_input_files)
                if newly_detected_files:
                    ad.warn_and_pause_if_paths_too_long(newly_detected_files, video_output_dir, temp_dir)
                    input_files.extend(newly_detected_files)

        except Exception as e:
            print(f"[Dispatch] Critical Error during processing: {e}")
            ad.send_ntfy_notification(
                ntfy_settings,
                root_dir,
                tools_dir,
                "Auto-Boost encode failed",
                "An encode failed.",
            )

    batch_elapsed = time.monotonic() - batch_started_at

    ad.send_ntfy_notification(
        ntfy_settings,
        root_dir,
        tools_dir,
        "Auto-Boost encode complete",
        "All queued encodes are complete.",
    )

    print("\n" + "=" * 80)
    print("Condor Batch Complete.")
    print("=" * 80)

    if timing_reports:
        print("\nFinal Condor time reports:")
        for timing_report in timing_reports:
            print_condor_timing_report(timing_report)

    print("\nTime format legend: hh:mm:ss = hours:minutes:seconds")
    print(f"Total time for all files: {format_elapsed_hhmmss(batch_elapsed)}")


if __name__ == "__main__":
    main()
