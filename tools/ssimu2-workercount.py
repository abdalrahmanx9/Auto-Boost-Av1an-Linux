import os
import sys
import psutil
import subprocess
import time
import shutil
import concurrent.futures
import gc
import random
import threading
import queue
import re
import json
import statistics
from pathlib import Path
# On Linux the VapourSynth autoload dirs are system-wide; the libvship build
# the benchmark activates is copied into the first one of them.
PLUGIN_ENV_VAR = "VAPOURSYNTH_EXTRA_PLUGIN_PATH"


def vs_plugin_dirs():
    """System VapourSynth autoload directories, pkg-config dir first."""
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
    seen, out = set(), []
    for d in dirs:
        if d not in seen and os.path.isdir(d):
            seen.add(d)
            out.append(d)
    return out


def ensure_vs_plugin_path():
    dirs = vs_plugin_dirs()
    if not dirs:
        return ""
    existing = [part for part in os.environ.get(PLUGIN_ENV_VAR, "").split(os.pathsep) if part]
    add = [d for d in dirs if not any(os.path.normcase(p) == os.path.normcase(d) for p in existing)]
    if add:
        os.environ[PLUGIN_ENV_VAR] = os.pathsep.join(existing + add)
    return dirs[0]


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

import vapoursynth as vs

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
VERBOSE = False  # Set to True to see all raw output for troubleshooting

BASE_DIR = Path(__file__).parent.parent.resolve()
TOOLS_DIR = BASE_DIR / "tools"
AV1AN_DIR = TOOLS_DIR / "av1an"
AV1AN_EXE = AV1AN_DIR / "av1an" if (AV1AN_DIR / "av1an").exists() else Path(shutil.which("av1an") or "av1an")
FORKS_DIR = AV1AN_DIR / "svt-av1 forks"
SAMPLE_FILE = TOOLS_DIR / "sample.mkv"
CONFIG_FILE = TOOLS_DIR / "workercount-ssimu2.txt"
TEMP_DIR = TOOLS_DIR / "ssimu2_bench_temp"

# FFVship paths
FFVSHIP_NVIDIA_EXE = TOOLS_DIR / "FFVship" / "FFVship_nvidia" / "FFVship"
FFVSHIP_AMD_EXE = TOOLS_DIR / "FFVship" / "FFVship_amd" / "FFVship"
FFVSHIP_VULKAN_EXE = TOOLS_DIR / "FFVship" / "FFVship_Vulkan" / "FFVship"

# Updated path: Both scripts are in the tools folder
AUTO_BOOST_SCRIPT = TOOLS_DIR / "Auto-Boost-Av1an.py"

# Benchmark Settings
SKIP = 3
STALL_TIMEOUT = 10.0  # Seconds to wait before killing stalled process
# FFVship indexes both clips before it prints anything, so it gets a longer
# grace period than the vs-hip runs before "no start" is declared.
FFVSHIP_INIT_TIMEOUT = 60.0  # Seconds to wait for FFVship's first output
FFVSHIP_MAX_RUNTIME = 300.0  # Hard ceiling for a single FFVship run

# vs-zip worker sweet-spot tuning.
# The vs-zip count is found by measuring REAL SSIMU2 throughput (fps) at
# several worker counts and keeping the fastest - the only reliable signal on
# power/thermally limited CPUs where more workers collapse clock speeds.
VSZIP_TEST_DURATION = 12        # Seconds of rendering measured per candidate
VSZIP_TIE_MARGIN = 0.03         # Within 3% fps counts as a tie -> prefer FEWER workers
VSZIP_RAM_HEADROOM = 0.85       # Candidates that push RAM past 85% are rejected
VSZIP_RAM_ABORT_PERCENT = 90.0  # LIVE kill-switch: abort the render immediately when
VSZIP_RAM_ABORT_MIN_MB = 2048   # system RAM crosses 90% used or free RAM drops
                                # below 2 GB, protecting the rest of the system.
CPU_WARMUP_SECONDS = 3.0        # Ignore vs-zip spin-up before sampling CPU usage

# GPU / Plugin Paths
VS_PLUGINS_DIR = Path(vs_plugin_dirs()[0]) if vs_plugin_dirs() else (BASE_DIR / "VapourSynth" / "Lib" / "site-packages"
                  / "vapoursynth" / "plugins")
VS_HIP_SOURCE_DIR = TOOLS_DIR / "vs-hip"

try:
    from vstools import core, clip_async_render
except ImportError:
    print("Error: vstools not found.", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------------------
# GPU VENDOR DETECTION -> BENCHMARK PLAN
# ---------------------------------------------------------------------------
#
# Each vship/FFVship build only runs on the hardware it was compiled for, so
# benchmarking every backend on every machine just burns minutes on candidates
# that are guaranteed to fail:
#
#   nvidia build  -> NVIDIA only
#   amd build     -> AMD only
#   vulkan build  -> NVIDIA / AMD / Intel, discrete or integrated
#
# tools/gpu_vendor.py reads the dedicated adapter's PCI vendor ID via DXGI.
# When it cannot identify a vendor we fall back to testing EVERYTHING, so an
# unrecognised or exotic setup degrades to the old exhaustive behaviour rather
# than silently skipping the backend that would have won.

if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

try:
    from gpu_vendor import gpu_vendor as _detect_gpu_vendor, list_gpus as _list_gpus
except Exception as _gpu_import_error:  # pragma: no cover - portable-package safety net
    print(f"[GPU] gpu_vendor.py unavailable ({_gpu_import_error}); "
          f"all GPU backends will be tested.", file=sys.stderr)
    _detect_gpu_vendor = None
    _list_gpus = None


def detect_gpu_vendor():
    """Return "nvidia" / "amd" / "intel", or None when detection fails."""
    if _detect_gpu_vendor is None:
        return None
    try:
        vendor = _detect_gpu_vendor()
    except Exception as e:
        print(f"[GPU] Vendor detection failed ({e}); all GPU backends will be tested.", file=sys.stderr)
        return None

    if _list_gpus is not None:
        try:
            for name_vendor, name, vram in _list_gpus():
                print(f"[GPU] Found: {name} ({name_vendor or 'unknown vendor'}, "
                      f"{vram / 2**30:.2f} GiB dedicated)", file=sys.stderr)
        except Exception:
            pass

    if vendor:
        print(f"[GPU] Dedicated GPU vendor: {vendor}", file=sys.stderr)
    else:
        print("[GPU] Could not identify a dedicated GPU; all GPU backends will be tested.", file=sys.stderr)
    return vendor


# variant -> (vs-hip DLL, FFVship exe). Order matters only for readability;
# the winner is chosen purely on measured fps.
GPU_BACKENDS = {
    "nvidia": ("libvship_NVIDIA.so", FFVSHIP_NVIDIA_EXE),
    "amd": ("libvship_AMD.so", FFVSHIP_AMD_EXE),
    "vulkan": ("libvship_VULKAN.so", FFVSHIP_VULKAN_EXE),
}

# Which variants are worth benchmarking for a given detected vendor. Intel has
# no dedicated vship build, so Vulkan is its only GPU path.
VENDOR_PLANS = {
    "nvidia": ("nvidia", "vulkan"),
    "amd": ("amd", "vulkan"),
    "intel": ("vulkan",),
}

# Used when nothing GPU-side survives, so the fallback config names a build the
# machine can actually load instead of always defaulting to NVIDIA.
FALLBACK_VARIANTS = {"nvidia": "nvidia", "amd": "amd", "intel": "vulkan"}


def plan_gpu_variants(vendor):
    """Variants to benchmark for this vendor; everything when vendor is unknown."""
    if vendor in VENDOR_PLANS:
        return VENDOR_PLANS[vendor]
    return tuple(GPU_BACKENDS)  # nvidia, amd, vulkan


def fallback_variant(vendor):
    return FALLBACK_VARIANTS.get(vendor, "nvidia")


FILTER_SETTING_KEYS = ("downscale", "denoise", "deband")

# Optional overrides applied on top of settings.txt (set by run_optimize_mode
# from the launching .bat, mirroring what dispatch writes into settings.txt
# right before encoding).
SETTINGS_OVERRIDES = {}


def read_settings_values() -> dict:
    settings_paths = [BASE_DIR / "settings.txt", TOOLS_DIR / "settings.txt"]
    values = {}
    for settings_path in settings_paths:
        if not settings_path.exists():
            continue
        try:
            for raw_line in settings_path.read_text(encoding="utf-8", errors="replace").splitlines():
                line = raw_line.strip()
                if not line or line.startswith(("#", ";", "[")) or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                values[key.strip().lower()] = value.strip()
        except Exception:
            pass
    values.update(SETTINGS_OVERRIDES)
    return values

def settings_filters_enabled() -> bool:
    values = read_settings_values()
    return any(values.get(key, "false").lower() == "true" for key in FILTER_SETTING_KEYS)


# ---------------------------------------------------------------------------
# .bat FILE HELPERS (optimize-workers support)
# ---------------------------------------------------------------------------

BAT_SET_RE = re.compile(r'^(?:set\s+)?"?([A-Za-z_][A-Za-z0-9_-]*)\s*=\s*(.*?)"?\s*$', re.IGNORECASE)


def find_active_bat_file():
    """Locate the .bat that launched this run via the tools/bat-used-*.txt marker."""
    try:
        markers = sorted(TOOLS_DIR.glob("bat-used-*.txt"), key=lambda p: p.stat().st_mtime, reverse=True)
    except Exception:
        markers = []
    for marker in markers:
        name = marker.name
        if not (name.startswith("bat-used-") and name.endswith(".txt")):
            continue
        bat_name = name[len("bat-used-"):-len(".txt")]
        bat_path = BASE_DIR / bat_name
        if bat_path.is_file():
            return bat_path
    return None


def parse_bat_settings(bat_path):
    """Parse `set "key=value"` (and bare key=value) lines from a .bat into a dict."""
    settings = {}
    try:
        text = Path(bat_path).read_text(encoding="utf-8", errors="replace")
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith("::") or line.lower().startswith("rem "):
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
    byte-offset .bat edit there is no padding to preserve. Generated .sh
    scripts spell the custom keys with underscores, so both spellings are
    tried.
    """
    value = str(value)
    variants = [key.lower()]
    if "-" in key:
        variants.append(key.lower().replace("-", "_"))
    elif "_" in key:
        variants.append(key.lower().replace("_", "-"))
    bat_path = Path(bat_path)
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
        elif ("=" in stripped and " " not in stripped.split("=", 1)[0]
                and stripped.split("=", 1)[0].strip().lower() in variants):
            hit = stripped.split("=", 1)[0].strip()
        if hit is None:
            continue
        new_core = lead + f'{hit}="{value}"'
        lines[idx] = new_core + ending
        try:
            with open(bat_path, "w", encoding="utf-8", errors="replace", newline="") as f:
                f.write("".join(lines))
            return True
        except Exception as e:
            print(f"[Optimize] Error writing {bat_path}: {e}", file=sys.stderr)
            return False

    print(f"[Optimize] Warning: {key} line not found in {bat_path.name}.", file=sys.stderr)
    return False


GPU_VARIANTS = ("nvidia", "amd", "vulkan")


def format_bat_ssimu2_tool(tool, variant=None):
    tool = (tool or "").strip().lower().replace("_", "-")
    variant = (variant or "").strip().lower()
    if tool == "vs-hip":
        return f"vs-hip {variant if variant in GPU_VARIANTS else 'nvidia'}"
    if tool in ("ffvship", "ffvship-nvidia", "ffvship-amd", "ffvship-vulkan"):
        if not variant:
            # Recover the variant from the tool name when it was not passed
            # separately (e.g. re-reading an older config).
            for known in GPU_VARIANTS:
                if known in tool:
                    variant = known
                    break
            else:
                variant = "nvidia"
        return f"ffvship {variant if variant in GPU_VARIANTS else 'nvidia'}"
    if tool == "vs-zip":
        return "vs-zip"
    return tool.replace("-", " ")


def read_ssimu2_config(config_path=CONFIG_FILE):
    cfg = {}
    try:
        for raw in Path(config_path).read_text(encoding="utf-8", errors="replace").splitlines():
            if not raw.strip() or raw.lstrip().startswith("#") or "=" not in raw:
                continue
            key, value = raw.split("=", 1)
            cfg[key.strip().lower().replace("_", "-")] = value.strip()
    except Exception:
        pass
    return cfg


def winner_count_from_config(cfg):
    tool = (cfg.get("tool") or "").strip().lower().replace("_", "-")
    if tool == "vs-zip":
        value = cfg.get("workercount", "")
    elif tool == "vs-hip":
        value = cfg.get("streams") or cfg.get("workercount", "")
    else:
        value = cfg.get("streams") or cfg.get("workercount", "")
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# CPU MEASUREMENT + FILTERED BENCHMARK SOURCE
# ---------------------------------------------------------------------------

class _CpuSampler(threading.Thread):
    """Samples this process's settled CPU usage (in cores) on a background thread.

    vs-zip runs in-process via VapourSynth threads, so the whole-process CPU
    percent IS the vs-zip worker load (plus decode overhead, which real runs
    also pay). The first CPU_WARMUP_SECONDS are skipped so the spin-up burst
    (graph construction, indexing, lookahead) is not counted."""

    def __init__(self, warmup=3.0, interval=0.25):
        super().__init__(daemon=True)
        self._warmup = warmup
        self._interval = interval
        self._stop_event = threading.Event()
        self.samples = []

    def run(self):
        try:
            proc = psutil.Process(os.getpid())
            proc.cpu_percent(None)  # prime the counter
            start = time.time()
            while True:
                if self._stop_event.wait(self._interval):
                    break
                pct = proc.cpu_percent(None)
                if time.time() - start >= self._warmup and pct > 0:
                    self.samples.append(pct / 100.0)
        except Exception:
            pass

    def stop(self):
        """Stop sampling and return the median settled cores used (0.0 if unknown)."""
        self._stop_event.set()
        try:
            self.join(timeout=2)
        except Exception:
            pass
        if not self.samples:
            return 0.0
        return statistics.median(self.samples)


def load_vpy_clip(script_path):
    """Run a .vpy in this process and hand back the clip it produced.

    The namespace is a single dict used as both globals and locals, which is how
    a module runs. Two dicts give class-body scoping instead, where a function
    defined in the script cannot see the script's own top-level names, so a
    script with a helper in it dies with a NameError that VSPipe never raises.

    "final" and "src" are what the generated script calls its clips; output 0 is
    the fallback, since setting it is the one thing every .vpy has to do.
    """
    script_path = Path(script_path)

    # Outputs live on the core, so a stale output 0 from an earlier call must
    # not stand in for a missing one.
    vs.clear_outputs()

    namespace = {"__name__": "__vpy__", "__file__": str(script_path)}
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


def build_benchmark_source_clip():
    """Return (clip, description) for the vs-zip benchmark source.

    When settings.txt filtering is enabled, this mirrors the temporary
    VapourSynth script that Auto-Boost-Av1an.py builds (initialize_clip ->
    denoise/deband hooks -> downscale -> finalize_clip) and exec()s it the
    same way Auto-Boost does, so the benchmark pays the same filtering CPU
    cost as a real metrics pass. Crop is left off for the benchmark, which
    only makes the result slightly conservative."""
    if not settings_filters_enabled():
        return core.ffms2.Source(source=str(SAMPLE_FILE)), "raw sample (no settings.txt filters enabled)"

    values = read_settings_values()
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    vpy_path = TEMP_DIR / "bench_source.vpy"
    cache_path = TEMP_DIR / "bench_source.ffindex"

    # Same reason as workercount.py: an index left behind by an interrupted run
    # no longer matches benchmark-sample.mkv once that sample is recut, and
    # ffms2.Source then fails hard instead of reindexing.
    cache_path.unlink(missing_ok=True)

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
    try:
        vpy_path.write_text(vpy_template.format(
            source=str(SAMPLE_FILE),
            cache=str(cache_path),
            denoise_line=denoise_line,
            deband_line=deband_line,
            downscale=str(do_downscale),
            target_res=target_res,
            kernel=kernel,
        ), encoding="utf-8")

        clip = load_vpy_clip(vpy_path)
        return clip, "filtered source (settings.txt filters applied, crop off)"
    except Exception as e:
        print(f"   Warning: filtered benchmark source failed ({e}); using raw sample.", file=sys.stderr)
        return core.ffms2.Source(source=str(SAMPLE_FILE)), "raw sample (filtered script failed)"

# ---------------------------------------------------------------------------
# BENCHMARK SAMPLE (created from the first video in video-input via mkvmerge,
# mirroring extras/create-sample.bat: 90 seconds, --no-audio)
# ---------------------------------------------------------------------------

MKVMERGE_EXE = Path(shutil.which("mkvmerge") or "mkvmerge")
VIDEO_INPUT_DIR = BASE_DIR / "video-input"
BENCH_SAMPLE_FILE = TOOLS_DIR / "benchmark-sample.mkv"
SAMPLE_MAX_AGE_SECONDS = 6 * 3600
VIDEO_EXTENSIONS = (".mkv", ".mp4", ".m2ts")


def find_first_input_video():
    """First real source file in video-input (skips encode artifacts)."""
    if not VIDEO_INPUT_DIR.is_dir():
        return None
    for path in sorted(VIDEO_INPUT_DIR.iterdir()):
        if not path.is_file():
            continue
        if path.suffix.lower() not in VIDEO_EXTENSIONS:
            continue
        if path.stem.lower().endswith(("-av1", "-output")):
            continue
        return path
    return None


def delete_benchmark_sample():
    for pattern in ("benchmark-sample*.mkv", "benchmark-sample.mkv.*"):
        for path in TOOLS_DIR.glob(pattern):
            try:
                path.unlink()
                print(f"[Sample] Deleted {path.name}", file=sys.stderr)
            except OSError:
                pass


def _sample_source_signature(source):
    try:
        st = Path(source).stat()
        return f"{Path(source).name}|{st.st_size}|{int(st.st_mtime)}"
    except OSError:
        return None


def ensure_benchmark_sample(force=False):
    """Create tools/benchmark-sample.mkv: a 90 second, audio-free cut of the
    first video in video-input, made with mkvmerge exactly like
    extras/create-sample.bat (--no-audio --split parts:00:03:00-00:04:30).
    Falls back to the first 90 seconds for short sources. Reuses a recent
    sample so workercount.py and ssimu2-workercount.py share one file.
    Returns the sample Path, or None if it could not be created."""
    source = find_first_input_video()
    sidecar = Path(str(BENCH_SAMPLE_FILE) + ".source.txt")

    if not force and BENCH_SAMPLE_FILE.exists():
        try:
            fresh = time.time() - BENCH_SAMPLE_FILE.stat().st_mtime < SAMPLE_MAX_AGE_SECONDS
            recorded = sidecar.read_text(encoding="utf-8").strip() if sidecar.exists() else None
            # Reuse ONLY when the sample provably came from the CURRENT first
            # source file (same rule as workercount.py).
            if fresh and source and recorded and recorded == _sample_source_signature(source):
                print(f"[Sample] Reusing benchmark sample (same source: {source.name}).", file=sys.stderr)
                return BENCH_SAMPLE_FILE
            if fresh and recorded and not source:
                print(f"[Sample] Reusing recent benchmark sample: {BENCH_SAMPLE_FILE.name}", file=sys.stderr)
                return BENCH_SAMPLE_FILE
        except OSError:
            pass
    if not source:
        print("[Sample] No source video found in video-input.", file=sys.stderr)
        return None

    mkvmerge = str(MKVMERGE_EXE) if MKVMERGE_EXE.exists() else "mkvmerge"

    for path in TOOLS_DIR.glob("benchmark-sample*.mkv"):
        try:
            path.unlink()
        except OSError:
            pass

    # Prefer 3:00-4:30 (skips intros); fall back to 0:00-1:30 for short
    # sources, where mkvmerge produces no output file at all.
    for time_range in ("00:03:00-00:04:30", "00:00:00-00:01:30"):
        cmd = [mkvmerge, "-o", str(BENCH_SAMPLE_FILE), "--no-audio",
               "--split", f"parts:{time_range}", str(source)]
        try:
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=600)
        except FileNotFoundError:
            print("[Sample] Warning: mkvmerge not found; cannot create benchmark sample.", file=sys.stderr)
            return None
        except subprocess.TimeoutExpired:
            print("[Sample] Warning: mkvmerge timed out; cannot create benchmark sample.", file=sys.stderr)
            return None

        produced = None
        if BENCH_SAMPLE_FILE.exists() and BENCH_SAMPLE_FILE.stat().st_size > 1024 * 1024:
            produced = BENCH_SAMPLE_FILE
        else:
            # Some mkvmerge versions append part numbers when splitting
            parts = sorted(TOOLS_DIR.glob("benchmark-sample-0*.mkv"))
            if parts and parts[0].stat().st_size > 1024 * 1024:
                try:
                    parts[0].replace(BENCH_SAMPLE_FILE)
                    produced = BENCH_SAMPLE_FILE
                except OSError:
                    produced = parts[0]
                for extra in parts[1:]:
                    try:
                        extra.unlink()
                    except OSError:
                        pass

        if produced:
            sig = _sample_source_signature(source)
            if sig:
                try:
                    sidecar.write_text(sig + "\n", encoding="utf-8")
                except OSError:
                    pass
            print(f"[Sample] Created 90s benchmark sample (no audio) from "
                  f"{source.name} [{time_range}].", file=sys.stderr)
            return produced

    print(f"[Sample] Warning: mkvmerge produced no usable sample from {source.name}.", file=sys.stderr)
    return None


def adopt_benchmark_sample():
    """Point every benchmark at a fresh 90s cut of the user's real source in
    video-input; keep the bundled tools/sample.mkv as the fallback."""
    global SAMPLE_FILE
    bench = ensure_benchmark_sample()
    if bench:
        SAMPLE_FILE = Path(bench)
        print(f"[Sample] Benchmarking against {SAMPLE_FILE.name}", file=sys.stderr)
    else:
        print(f"[Sample] Falling back to {SAMPLE_FILE.name}", file=sys.stderr)


def write_ssimu2_config(tool="vs-hip", filter_tool="vs-hip", workercount=1, variant="nvidia", streams=1):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        f.write(f"tool={tool}\n")
        f.write(f"filter-tool={filter_tool}\n")
        f.write(f"workercount={max(1, int(workercount))}\n")
        if variant:
            f.write(f"variant={variant}\n")
        if streams:
            f.write(f"streams={max(1, int(streams))}\n")

def force_remove(path: Path):
    if not path.exists():
        return
    try:
        path.unlink(missing_ok=True)
    except (PermissionError, OSError):
        try:
            trash_name = path.with_suffix(f".trash_{random.randint(1000,9999)}")
            path.rename(trash_name)
        except:
            pass

def cleanup_temp_files():
    try:
        if hasattr(vs.core, 'clear_cache'):
            vs.core.clear_cache()
    except:
        pass
    gc.collect()

    if TEMP_DIR.exists():
        try:
            shutil.rmtree(TEMP_DIR)
        except Exception:
            pass

    for ext in [".ffindex", ".lwi", ".json"]:
        f = SAMPLE_FILE.with_suffix(SAMPLE_FILE.suffix + ext)
        if f.exists():
            try:
                f.unlink()
            except:
                pass
    
    for f in VS_PLUGINS_DIR.glob("*.trash_*"):
        try:
            f.unlink(missing_ok=True)
        except:
            pass

def setup_svt_av1_fork(target_fork="5fish"):
    """Swaps in the standard SVT-AV1 encoder build for benchmarking."""
    print(f"Setting up SVT-AV1 fork: {target_fork}", file=sys.stderr)

    fork_parent = None
    if FORKS_DIR.exists():
        for f in FORKS_DIR.iterdir():
            if f.is_dir() and target_fork.lower() in f.name.lower():
                fork_parent = f
                break

    if fork_parent:
        target_subfolder = None
        subfolders = [d for d in fork_parent.iterdir() if d.is_dir()]

        # The benchmark only needs an encoder that runs, so prefer the builds
        # every CPU can execute. Never fall back to an arbitrary subfolder:
        # picking an icelake/znver4/znver5 build on a CPU without AVX-512
        # crashes the benchmark instead of just running it slowly.
        for needle in ('x86-64-v3', 'znver2', 'x86-64-v2'):
            for sub in subfolders:
                if needle in sub.name.lower():
                    target_subfolder = sub
                    break
            if target_subfolder:
                break

        if not target_subfolder:
            if not subfolders:
                # A fork like 'custom' can hold the exe directly.
                target_subfolder = fork_parent
            elif len(subfolders) == 1:
                # Only one build shipped, so there is nothing safer to pick.
                target_subfolder = subfolders[0]

        if target_subfolder:
            exe_src = target_subfolder / "SvtAv1EncApp"
            exe_dest = AV1AN_DIR / "SvtAv1EncApp"

            try:
                if exe_dest.exists():
                    exe_dest.unlink()
            except Exception as e:
                print(f"   - Warning: Could not clean up old SVT-AV1 files: {e}", file=sys.stderr)

            if exe_src.exists():
                try:
                    shutil.copy2(exe_src, exe_dest)
                    print(f"   - Copied SvtAv1EncApp.exe from {target_subfolder.name}", file=sys.stderr)
                except Exception as e:
                    print(f"   - Error copying fork files: {e}", file=sys.stderr)
            else:
                print(f"   - Error: SvtAv1EncApp.exe not found in {target_subfolder}", file=sys.stderr)
        else:
            print(f"   - Warning: No x86-64-v3/znver2 build found in {fork_parent.name}; "
                  f"leaving the existing SvtAv1EncApp.exe in place.", file=sys.stderr)
    else:
        print(f"   - Warning: Could not find a fork directory matching '{target_fork}' in {FORKS_DIR}", file=sys.stderr)

def run_fast_pass():
    print("Generating benchmark assets (Fast Pass)...", file=sys.stderr)
    output_file = TEMP_DIR / "sample_fastpass.mkv"
    if not TEMP_DIR.exists():
        TEMP_DIR.mkdir(parents=True, exist_ok=True)

    vpy_content = f"""
import vapoursynth as vs
core = vs.core
src = core.ffms2.Source(source=r"{SAMPLE_FILE}")
src.set_output()
"""
    vpy_path = TEMP_DIR / "source.vpy"
    with open(vpy_path, "w", encoding="utf-8") as f:
        f.write(vpy_content)

    cmd = [
        str(AV1AN_EXE), "-i", str(vpy_path), "-e", "svt-av1",
        "-c", "mkvmerge", "-w", "2",
        "-m", "bestsource", 
        "-v", "--preset 10 --crf 35", "-o", str(output_file)
    ]

    try:
        if VERBOSE:
            print(f"[Fast Pass CMD] {' '.join(cmd)}", file=sys.stderr)
        
        print("-" * 50, file=sys.stderr)
        subprocess.run(cmd, check=True, cwd=TEMP_DIR)
        print("-" * 50, file=sys.stderr)
        return output_file
    except subprocess.CalledProcessError as e:
        print(f"Error: Fast pass generation failed. {e}", file=sys.stderr)
        return None

def _print_progress(percent: float, end: bool = False, elapsed: float = 0.0):
    if end:
        sys.stderr.write(f"\r      Progress: 100.0%  (Time: {elapsed:.2f}s)\n")
    else:
        sys.stderr.write(f"\r      Progress: {percent:.1f}% ")
        sys.stderr.flush()

# --- BENCHMARK FUNCTIONS ---

def benchmark_gpu_candidate(dll_name: str, encoded_file: Path, num_streams: int):
    print(f"   Benchmarking vs-hip GPU ({dll_name} | Streams: {num_streams})...", file=sys.stderr)

    dst_dll = VS_PLUGINS_DIR / dll_name
    src_dll = VS_HIP_SOURCE_DIR / dll_name

    bench_script = f"""
import sys
import time
import vapoursynth as vs

try:
    from vstools import core, clip_async_render
except Exception:
    sys.exit(2)

core = vs.core
SKIP = {SKIP}
DURATION = 10.0

if not hasattr(core, "vship"):
    # The DLL should have autoloaded out of the interpreter's plugins folder via
    # VAPOURSYNTH_EXTRA_PLUGIN_PATH. Load it by path when it did not, so a
    # missing/renamed autoload folder cannot silently disable every GPU backend.
    for _cand in (r"{dst_dll}", r"{src_dll}"):
        try:
            core.std.LoadPlugin(_cand)
            break
        except Exception:
            continue

if not hasattr(core, "vship"):
    sys.stderr.write("VSHIP_ERROR:core.vship unavailable after loading {dll_name}\\n")
    sys.exit(5)

def emit_progress(p):
    try:
        sys.stdout.write(f"PROGRESS:{{p}}\\n")
        sys.stdout.flush()
    except Exception:
        pass

try:
    src = core.ffms2.Source(source=r"{SAMPLE_FILE}").resize.Bicubic(format=vs.RGB24, matrix_in_s="709")[::SKIP]
    enc = core.ffms2.Source(source=r"{str(encoded_file)}").resize.Bicubic(format=vs.RGB24, matrix_in_s="709")[::SKIP]

    # Injected numStream
    res = core.vship.SSIMULACRA2(src, enc, numStream={num_streams})

    # Total frames of the SKIPPED clip - this is what actually gets rendered.
    total_frames = max(1, int(res.num_frames))

    start = time.time()
    last_emit = [0.0]
    last_n = [-1]
    eff_frames = [0]

    def p(n, t):
        if n is None:
            return

        if n > last_n[0]:
            delta = n - last_n[0]
            if delta < 0:
                delta = 0
            eff_frames[0] += delta * SKIP
            last_n[0] = n

        elapsed = time.time() - start

        if elapsed - last_emit[0] >= 0.15:
            last_emit[0] = elapsed
            # The run ends when EITHER the frame-skipped clip completes OR
            # DURATION elapses, whichever comes first. Progress must reflect
            # both: a fast GPU can finish the whole skipped clip in ~2s, so a
            # purely time-based percent would sit at ~20% and jump to 100%.
            done = max(0, last_n[0] + 1)
            prog_frames = (done / total_frames) * 100.0
            prog_time = (elapsed / DURATION) * 100.0
            prog = min(100.0, max(prog_frames, prog_time))
            emit_progress(prog)

        if elapsed > DURATION:
            raise KeyboardInterrupt

    try:
        clip_async_render(res, outfile=None, progress=p)
    except KeyboardInterrupt:
        pass
    except Exception:
        pass

    elapsed = time.time() - start
    if elapsed <= 0:
        sys.exit(3)

    if eff_frames[0] <= 0:
        try:
            eff_frames[0] = int(res.num_frames) * SKIP
        except Exception:
            eff_frames[0] = 0

    fps = (eff_frames[0] / elapsed) if eff_frames[0] > 0 else 0.0
    sys.stdout.write(f"FPS:{{fps}}\\n")
    sys.stdout.write(f"ELAPSED:{{elapsed}}\\n")
    sys.stdout.flush()

except Exception as e:
    sys.stderr.write(f"VSHIP_ERROR:{{e}}\\n")
    sys.exit(4)
"""

    def _reader_thread(stdout_pipe, q: queue.Queue):
        try:
            for ln in stdout_pipe:
                q.put(ln)
        except Exception:
            pass
        finally:
            q.put(None)

    fps = 0.0
    elapsed_time = 0.0
    vship_errors = []
    saw_progress = False
    last_percent = 0.0
    stall_start_time = time.time()
    
    init_deadline = time.time() + 15.0 

    try:
        with subprocess.Popen(
            [sys.executable, "-c", bench_script],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=BASE_DIR,
            text=True,
            encoding="utf-8",
            errors="replace"
        ) as proc:
            
            if proc.stdout is None:
                sys.stderr.write("\n")
                return 0.0, 0.0

            q_lines: queue.Queue = queue.Queue()
            t = threading.Thread(target=_reader_thread, args=(proc.stdout, q_lines), daemon=True)
            t.start()
            
            # Read stderr for verbose logging, and keep the reason a run failed
            # so it can be reported even when VERBOSE is off.
            def _stderr_reader():
                for err_line in proc.stderr:
                    if err_line.startswith("VSHIP_ERROR:"):
                        vship_errors.append(err_line.strip()[len("VSHIP_ERROR:"):])
                    if VERBOSE:
                        sys.stderr.write(f"[vs-hip stderr] {err_line.strip()}\n")
            
            t_err = threading.Thread(target=_stderr_reader, daemon=True)
            t_err.start()

            while True:
                now = time.time()

                if (not saw_progress) and (now > init_deadline) and (proc.poll() is None):
                    sys.stderr.write(" [Timeout: No start]\n")
                    proc.kill()
                    return 0.0, 0.0

                if saw_progress and (now - stall_start_time > STALL_TIMEOUT) and (proc.poll() is None):
                    sys.stderr.write(f" [Timeout: Stalled at {last_percent:.1f}%]\n")
                    proc.kill()
                    return 0.0, 0.0

                if proc.poll() is not None and q_lines.empty():
                    break

                try:
                    item = q_lines.get(timeout=0.1)
                except queue.Empty:
                    continue

                if item is None:
                    break

                line = item.strip()

                if line.startswith("PROGRESS:"):
                    try:
                        percent = float(line.split(":", 1)[1])
                        if abs(percent - last_percent) > 0.01:
                            stall_start_time = time.time()
                            last_percent = percent
                        
                        saw_progress = True
                        if not VERBOSE:
                            _print_progress(percent, end=False)
                    except:
                        pass
                elif line.startswith("FPS:"):
                    try:
                        fps = float(line.split(":", 1)[1])
                    except:
                        fps = 0.0
                elif line.startswith("ELAPSED:"):
                    try:
                        elapsed_time = float(line.split(":", 1)[1])
                    except:
                        elapsed_time = 0.0
                elif VERBOSE:
                    sys.stderr.write(f"[vs-hip stdout] {line}\n")

            if not VERBOSE:
                if saw_progress or last_percent > 0.0:
                    _print_progress(100.0, end=True, elapsed=elapsed_time)
                else:
                    sys.stderr.write("\n")

    except Exception:
        sys.stderr.write("\n")
        return 0.0, 0.0

    if fps <= 0 and vship_errors and not VERBOSE:
        print(f"      Reason: {vship_errors[0]}", file=sys.stderr)

    return (fps, elapsed_time) if fps > 0 else (0.0, 0.0)

def run_gpu_suite(dll_name, encoded_file, variant_name):
    src_dll = VS_HIP_SOURCE_DIR / dll_name
    dst_dll = VS_PLUGINS_DIR / dll_name

    if not src_dll.exists():
        return 0.0, 1, 0.0

    for f in list(VS_PLUGINS_DIR.glob("libvship*.dll")) + list(VS_PLUGINS_DIR.glob("libvship*.so")):
        force_remove(f)

    try:
        shutil.copy(src_dll, dst_dll)
    except:
        return 0.0, 1, 0.0

    results = []

    for s in range(1, 5):
        fps, elapsed_time = benchmark_gpu_candidate(dll_name, encoded_file, num_streams=s)
        
        if s == 1 and fps <= 0.0:
            print(f"   [vs-hip-{variant_name}] Stream=1 failed. Skipping remaining tests.", file=sys.stderr)
            return 0.0, 1, 0.0
        
        if fps > 0:
            results.append((fps, s, elapsed_time))

    if not results:
        return 0.0, 1, 0.0

    best = max(results, key=lambda x: x[0])
    return best[0], best[1], best[2]


def _kill_process_tree(proc):
    """Kill a benchmark process and anything it spawned, so a wedged GPU run
    cannot keep holding the device after we have given up on it."""
    if proc is None:
        return
    try:
        parent = psutil.Process(proc.pid)
        for child in parent.children(recursive=True):
            try:
                child.kill()
            except Exception:
                pass
    except Exception:
        pass
    try:
        proc.kill()
    except Exception:
        pass
    try:
        proc.wait(timeout=5)
    except Exception:
        pass


def _ffvship_reader(pipe, q: queue.Queue):
    """Feed raw chunks of FFVship output into a queue.

    FFVship redraws its progress with carriage returns, so iterating the pipe
    line by line blocks until the process exits - which is exactly the hang
    this reader avoids. Chunked reads surface activity as it happens, letting
    the caller tell "still working" apart from "wedged"."""
    try:
        fd = pipe.fileno()
        while True:
            chunk = os.read(fd, 4096)
            if not chunk:
                break
            q.put(chunk)
    except Exception:
        pass
    finally:
        q.put(None)


def benchmark_ffvship(exe_path: Path, encoded_file: Path, gpu_threads: int, variant_name: str):
    print(f"   Benchmarking GPU (FFVship {variant_name.capitalize()} | Streams: {gpu_threads})...", file=sys.stderr)

    json_path = TEMP_DIR / f"ffvship_{gpu_threads}.json"
    # A leftover file from an earlier run must never be read back as a result.
    force_remove(json_path)

    cmd = [
        str(exe_path),
        "--source", str(SAMPLE_FILE),
        "--encoded", str(encoded_file),
        "-t", "3",
        "-g", str(gpu_threads),
        "--json", str(json_path)
    ]

    if VERBOSE:
        print(f"   [FFVship CMD] {' '.join(cmd)}", file=sys.stderr)

    fps = 0.0
    start_time = time.time()
    elapsed_time = 0.0
    proc = None

    def _consume(text, state):
        nonlocal fps
        for part in re.split(r"[\r\n]", text):
            line = part.strip()
            if not line:
                continue
            if VERBOSE:
                print(f"   [FFVship OUT] {line}", file=sys.stderr)
            m = re.search(r"at\s+([0-9.]+)\s+fps", line, re.IGNORECASE)
            if m:
                try:
                    fps = float(m.group(1))
                except ValueError:
                    pass
            m = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*%", line)
            if m:
                try:
                    state['percent'] = float(m.group(1))
                except ValueError:
                    continue
                state['showed_progress'] = True
                if not VERBOSE:
                    _print_progress(state['percent'], end=False)

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=str(exe_path.parent)
        )

        q_chunks: queue.Queue = queue.Queue()
        threading.Thread(target=_ffvship_reader, args=(proc.stdout, q_chunks), daemon=True).start()

        state = {'percent': 0.0, 'showed_progress': False}
        buf = ""
        saw_output = False
        last_activity = start_time
        init_deadline = start_time + FFVSHIP_INIT_TIMEOUT
        timed_out = None

        while True:
            now = time.time()

            # Never started: no byte of output at all within the init window.
            if (not saw_output) and (now > init_deadline) and (proc.poll() is None):
                timed_out = " [Timeout: No start]"
                break

            # Started, then went quiet - same rule the vs-hip suite uses.
            if saw_output and (now - last_activity > STALL_TIMEOUT) and (proc.poll() is None):
                timed_out = f" [Timeout: Stalled at {state['percent']:.1f}%]"
                break

            # Backstop for a run that keeps chattering but never finishes.
            if (now - start_time > FFVSHIP_MAX_RUNTIME) and (proc.poll() is None):
                timed_out = f" [Timeout: Exceeded {FFVSHIP_MAX_RUNTIME:.0f}s]"
                break

            if proc.poll() is not None and q_chunks.empty():
                break

            try:
                item = q_chunks.get(timeout=0.1)
            except queue.Empty:
                continue

            if item is None:
                break

            saw_output = True
            last_activity = time.time()
            buf += item.decode("utf-8", errors="replace")

            # Keep the trailing fragment: it may be a half-written line.
            split_at = max(buf.rfind("\r"), buf.rfind("\n"))
            if split_at >= 0:
                _consume(buf[:split_at + 1], state)
                buf = buf[split_at + 1:]

        if buf:
            _consume(buf, state)

        if timed_out:
            sys.stderr.write(timed_out + "\n")
            _kill_process_tree(proc)
            return 0.0, time.time() - start_time

        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            sys.stderr.write(" [Timeout: Process would not exit]\n")
            _kill_process_tree(proc)
            return 0.0, time.time() - start_time

        elapsed_time = time.time() - start_time

        if not VERBOSE:
            if state['showed_progress']:
                _print_progress(100.0, end=True, elapsed=elapsed_time)
            else:
                print(f"      Done. (Time: {elapsed_time:.2f}s)", file=sys.stderr)

        # Parse the JSON file directly 
        if json_path.exists():
            try:
                with open(json_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if VERBOSE:
                        print(f"   [FFVship JSON] {data}", file=sys.stderr)
                        
                    if "fps" in data:
                        fps = float(data["fps"])
                    elif "speed" in data:
                        fps = float(data["speed"])
            except Exception as e:
                if VERBOSE:
                    print(f"   [FFVship JSON Error] {e}", file=sys.stderr)

    except Exception as e:
        elapsed_time = time.time() - start_time
        print(f"   [FFVship Error] {e}", file=sys.stderr)
        _kill_process_tree(proc)
        return 0.0, elapsed_time

    return fps, elapsed_time

def run_ffvship_suite(exe_path: Path, variant_name: str, encoded_file: Path):
    if not exe_path.exists():
        return 0.0, 1, 0.0

    results = []
    for s in range(1, 5):
        fps, elapsed_time = benchmark_ffvship(exe_path, encoded_file, s, variant_name)
        
        if s == 1 and fps <= 0.0:
            print(f"   [FFVship-{variant_name}] Stream=1 failed. Skipping remaining tests.", file=sys.stderr)
            return 0.0, 1, 0.0
            
        if fps > 0:
            results.append((fps, s, elapsed_time))
            
    if not results:
        return 0.0, 1, 0.0
        
    best = max(results, key=lambda x: x[0])
    return best[0], best[1], best[2]


def benchmark_cpu_vszip(encoded_file, use_filters=False):
    """Find the vs-zip worker sweet spot by measuring REAL SSIMU2 throughput
    at several worker counts, instead of extrapolating from CPU usage.

    This catches power/thermally limited CPUs (especially laptops) where
    adding workers collapses clock speeds: total CPU still reads 100%, but
    fps drops hard. Ties within VSZIP_TIE_MARGIN go to the LOWER count.

    use_filters=False (regular mode): settings.txt is NOT read and NO filters
    are applied - the raw sample is used directly for the general worker test.
    use_filters=True (optimize mode): the source is built through the user's
    real settings.txt filtering chain."""
    if use_filters:
        source_clip, source_desc = build_benchmark_source_clip()
    else:
        source_clip = core.ffms2.Source(source=str(SAMPLE_FILE))
        source_desc = "raw sample (regular mode: settings.txt not read, no filters)"
    print(f"   vs-zip benchmark source: {source_desc}", file=sys.stderr)

    cpu_threads = os.cpu_count() or 1
    total_ram = psutil.virtual_memory().total
    results = {}   # workers -> (fps, elapsed)
    ram_cap = [cpu_threads]

    def measure(w):
        w = max(1, min(int(w), ram_cap[0]))
        if w in results:
            return w
        print(f"   Testing vs-zip with {w} workers...", file=sys.stderr)
        fps, rss, elapsed, cores = _run_vszip_internal(
            w, encoded_file, duration=VSZIP_TEST_DURATION, source_clip=source_clip)
        if rss > total_ram * VSZIP_RAM_HEADROOM and w > 1:
            print(f"   ! {w} workers pushed RAM to {rss // (1024**2)} MB "
                  f"(>{int(VSZIP_RAM_HEADROOM * 100)}% of system RAM) - rejected.", file=sys.stderr)
            ram_cap[0] = min(ram_cap[0], w)
            fps = 0.0
        results[w] = (fps, elapsed)
        if fps > 0:
            note = f" ({cores:.1f}/{cpu_threads} cores busy)" if cores > 0 else ""
            print(f"   -> {w} workers: {fps:.2f} fps{note}", file=sys.stderr)
        else:
            print(f"   -> {w} workers: no throughput (failed, stalled, or unsafe)", file=sys.stderr)
        return w

    # Coarse ladder across the thread range, then refine around the best.
    coarse = sorted({max(1, cpu_threads // 4), max(1, cpu_threads // 2),
                     max(1, (3 * cpu_threads) // 4), cpu_threads})
    for w in coarse:
        measure(w)

    valid = {w: r[0] for w, r in results.items() if r[0] > 0}
    if valid:
        tested = sorted(results)
        best_w = max(valid, key=lambda k: valid[k])
        idx = tested.index(best_w)
        refine = []
        if idx > 0:
            refine.append((best_w + tested[idx - 1]) // 2)
        if idx < len(tested) - 1:
            refine.append((best_w + tested[idx + 1]) // 2)
        for cand in refine:
            if cand not in results and 1 <= cand <= cpu_threads:
                measure(cand)

    valid = {w: r for w, r in results.items() if r[0] > 0}
    if not valid:
        return 0, 1, 0.0

    best_fps = max(r[0] for r in valid.values())
    contenders = [w for w, r in valid.items() if r[0] >= best_fps * (1 - VSZIP_TIE_MARGIN)]
    best_w = min(contenders)
    fps_best, elapsed_best = valid[best_w]

    print("   vs-zip sweet spot summary:", file=sys.stderr)
    for w in sorted(results):
        fps_w = results[w][0]
        marker = "  <-- sweet spot" if w == best_w else ""
        fps_str = f"{fps_w:.2f} fps" if fps_w > 0 else "failed/unsafe"
        print(f"     {w} workers: {fps_str}{marker}", file=sys.stderr)

    return fps_best, best_w, elapsed_best

def _run_vszip_internal(workers, encoded_file, duration, source_clip=None):
    if not hasattr(core, 'vszip'):
        return 0, 0, 0.0, 0.0

    core.num_threads = workers

    if source_clip is None:
        source_clip = core.ffms2.Source(source=str(SAMPLE_FILE))
    src = source_clip.resize.Bicubic(format=vs.RGB24, matrix_in_s="709")[::SKIP]
    enc = core.ffms2.Source(source=str(encoded_file)).resize.Bicubic(format=vs.RGB24, matrix_in_s="709")[::SKIP]

    try:
        res = core.vszip.SSIMULACRA2(src, enc)
    except:
        return 0, 0, 0.0, 0.0

    # Total frames of the SKIPPED clip - the run ends when either this many
    # frames are rendered or `duration` elapses, whichever comes first.
    total_frames = max(1, int(res.num_frames))

    # Sample this process's CPU usage while rendering. The first
    # CPU_WARMUP_SECONDS are excluded so vs-zip/ffms2 spin-up (graph build,
    # indexing, lookahead) is not counted as sustained per-worker cost.
    cpu_sampler = _CpuSampler(warmup=CPU_WARMUP_SECONDS)
    cpu_sampler.start()

    start = time.time()
    max_rss = [0]
    frames_sampled_done = [0]
    last_n = [-1]

    stall_data = {
        'last_update': time.time(),
        'last_count': 0
    }

    def p(n, t):
        if n is None:
            return

        if n > last_n[0]:
            delta = n - last_n[0]
            if delta < 0:
                delta = 0
            frames_sampled_done[0] += delta
            last_n[0] = n

            stall_data['last_update'] = time.time()
            stall_data['last_count'] = frames_sampled_done[0]

        elapsed = time.time() - start

        if (time.time() - stall_data['last_update']) > STALL_TIMEOUT:
            raise RuntimeError("Stalled")

        # Progress is the max of frame-based and time-based completion, since
        # the run ends when either the skipped clip finishes or `duration`
        # elapses. Prevents the ~20% -> 100% jump when rendering outpaces
        # the timer.
        prog_frames = (frames_sampled_done[0] / total_frames) * 100.0
        prog_time = (elapsed / duration) * 100.0
        prog = min(100.0, max(prog_frames, prog_time))

        if not VERBOSE:
            _print_progress(prog, end=False)

        if n % 10 == 0:
            try:
                max_rss[0] = max(max_rss[0], psutil.Process(os.getpid()).memory_info().rss)
            except:
                pass
            try:
                vm = psutil.virtual_memory()
                if (vm.percent >= VSZIP_RAM_ABORT_PERCENT
                        or vm.available < VSZIP_RAM_ABORT_MIN_MB * 1024 * 1024):
                    raise RuntimeError("RAMAbort")
            except RuntimeError:
                raise
            except Exception:
                pass

        if elapsed > duration:
            raise KeyboardInterrupt

    try:
        clip_async_render(res, outfile=None, progress=p)
    except RuntimeError as re_err:
        if "RAMAbort" in str(re_err):
            cpu_sampler.stop()
            sys.stderr.write("\n!! SYSTEM RAM CRITICAL - aborting this vs-zip test "
                             "immediately to protect the system.\n")
            # Report RSS as total system RAM so the caller rejects this count
            # and caps the ladder.
            return 0, psutil.virtual_memory().total, 0.0, 0.0
        if "Stalled" in str(re_err):
            cpu_sampler.stop()
            sys.stderr.write("\n[Timeout] vs-zip stalled.\n")
            return 0, 0, 0.0, 0.0
    except:
        pass

    settled_cores = cpu_sampler.stop()
    elapsed = time.time() - start

    if not VERBOSE:
        _print_progress(100.0, end=True, elapsed=elapsed)

    effective_frames = frames_sampled_done[0] * SKIP
    fps = (effective_frames / elapsed) if elapsed > 0 else 0

    del res, src, enc
    return fps, max_rss[0], elapsed, settled_cores

def update_auto_boost_script(winning_streams):
    """
    Updates the numStream parameter in Auto-Boost-Av1an.py.
    Target: result = core.vship.SSIMULACRA2(..., numStream = X)
    """
    if not AUTO_BOOST_SCRIPT.exists():
        print(f"Warning: {AUTO_BOOST_SCRIPT} not found. Skipping edit.", file=sys.stderr)
        return

    try:
        content = AUTO_BOOST_SCRIPT.read_text(encoding="utf-8")
        
        # Regex to find SSIMULACRA2 call and replace numStream value
        pattern = r"(result\s*=\s*core\.vship\.SSIMULACRA2\(.*numStream\s*=\s*)(\d+)(.*\))"
        
        if not re.search(pattern, content):
            return

        new_content = re.sub(
            pattern,
            f"\\g<1>{winning_streams}\\g<3>",
            content
        )
        
        if new_content != content:
            AUTO_BOOST_SCRIPT.write_text(new_content, encoding="utf-8")
            print(f"   [Auto-Config] Updated Auto-Boost-Av1an.py to numStream={winning_streams}", file=sys.stderr)
            
    except Exception as e:
        print(f"Error editing Auto-Boost-Av1an.py: {e}", file=sys.stderr)

# --- MAIN ---

def run_full_suite(target_fork="5fish", use_filters=False):
    """Run the full GPU + CPU benchmark suite and write workercount-ssimu2.txt.

    In regular mode (use_filters=False, the default), settings.txt is never
    read and no filters are applied to the general vs-zip worker test.
    Optimize mode passes use_filters=True to benchmark through the user's
    real filtering chain.

    Returns the winning config dict, or None if all benchmarks failed."""
    vszip_workers = None
    # Detected before the try block so the failure paths below can still write a
    # fallback config naming a build this machine is able to load.
    vendor = detect_gpu_vendor()
    try:
        cleanup_temp_files()

        # OPTIMIZE mode (use_filters=True): benchmark against a 90s cut of the
        # user's real source. GENERAL mode keeps the bundled tools/sample.mkv.
        if use_filters:
            adopt_benchmark_sample()

        # Setup specific SVT-AV1 fork with AVX-512 detection before pass
        setup_svt_av1_fork(target_fork=target_fork)

        encoded_file = run_fast_pass()
        if not encoded_file:
            raise RuntimeError("Fast pass failed")

        results = []

        # Only benchmark the GPU backends this machine's hardware can run.
        # An undetectable vendor plans every variant, matching the old behaviour.
        planned = plan_gpu_variants(vendor)
        skipped = [v for v in GPU_BACKENDS if v not in planned]
        print(f"\n[GPU] Benchmarking GPU backends: {', '.join(planned)}", file=sys.stderr)
        if skipped:
            print(f"[GPU] Skipping (incompatible with a {vendor} GPU): "
                  f"{', '.join(skipped)}", file=sys.stderr)

        # 1. vs-hip (in-process VapourSynth plugin)
        for variant in planned:
            dll_name = GPU_BACKENDS[variant][0]
            fps, streams, elapsed = run_gpu_suite(dll_name, encoded_file, variant)
            if fps > 0:
                print(f"   [vs-hip-{variant}] FPS: {fps:.2f} | Streams: {streams} | Time: {elapsed:.2f}s", file=sys.stderr)
                results.append({"tool": "vs-hip", "variant": variant, "fps": fps,
                                "workers": 1, "streams": streams, "time": elapsed})
            else:
                print(f"   [vs-hip-{variant}] Not compatible or failed.", file=sys.stderr)

        # Remove DLLs to keep environment clean
        for f in list(VS_PLUGINS_DIR.glob("libvship*.dll")) + list(VS_PLUGINS_DIR.glob("libvship*.so")):
            force_remove(f)

        # 2. FFVship (standalone exe)
        for variant in planned:
            exe_path = GPU_BACKENDS[variant][1]
            fps, streams, elapsed = run_ffvship_suite(exe_path, variant, encoded_file)
            if fps > 0:
                print(f"   [FFVship-{variant}] FPS: {fps:.2f} | Streams: {streams} | Time: {elapsed:.2f}s", file=sys.stderr)
                results.append({"tool": f"ffvship_{variant}", "variant": variant, "fps": fps,
                                "workers": streams, "streams": streams, "time": elapsed})

        # 3. vs-zip - always benchmarked, whatever the GPU situation, since it
        # is the CPU fallback that has to work when every GPU backend fails.
        fps_zip, w_zip, time_zip = benchmark_cpu_vszip(encoded_file, use_filters=use_filters)
        if fps_zip > 0:
            print(f"   [vs-zip]        FPS: {fps_zip:.2f} | Workers: {w_zip} | Time: {time_zip:.2f}s", file=sys.stderr)
            results.append({"tool": "vs-zip", "variant": "cpu", "fps": fps_zip, "workers": w_zip, "streams": 0, "time": time_zip})
            vszip_workers = w_zip

        # Regular mode never reads settings.txt; only optimize mode consults it.
        filters_enabled = settings_filters_enabled() if use_filters else False

        if not results:
            print("All benchmarks failed. Defaulting to vs-hip config so filtered runs do not select FFVship.", file=sys.stderr)
            write_ssimu2_config(tool="vs-hip", filter_tool="vs-hip", workercount=1,
                                variant=fallback_variant(vendor), streams=1)
            return None

        winner = max(results, key=lambda x: x['fps'])
        variant_str = winner.get('variant', '')
        stream_str = f"| Streams: {winner['streams']}" if winner['streams'] > 0 else ""
        print(f"\nWinner: {winner['tool']} ({variant_str}) | FPS: {winner['fps']:.2f} | Time: {winner['time']:.2f}s {stream_str}", file=sys.stderr)

        # Determine best filter tool (must be vs-hip or vs-zip when filtering is enabled).
        filter_candidates = [r for r in results if r['tool'] in ['vs-hip', 'vs-zip']]
        vs_hip_candidates = [r for r in filter_candidates if r['tool'] == 'vs-hip']
        if filters_enabled and vs_hip_candidates:
            # Prefer vs-hip for any filtering in settings.txt, even if vs-zip benchmarks faster.
            filter_winner = max(vs_hip_candidates, key=lambda x: x['fps'])
        elif filter_candidates:
            filter_winner = max(filter_candidates, key=lambda x: x['fps'])
        else:
            filter_winner = {'tool': 'vs-hip', 'variant': fallback_variant(vendor), 'streams': 1}
        filter_tool = filter_winner['tool']

        # POST-WINNER SETUP
        if winner['tool'] == "vs-hip":
            dll_name = f"libvship_{winner['variant'].upper()}.so"
            src = VS_HIP_SOURCE_DIR / dll_name
            dst = VS_PLUGINS_DIR / dll_name
            if src.exists():
                shutil.copy(src, dst)
            
            update_auto_boost_script(winner['streams'])
        else:
            for f in list(VS_PLUGINS_DIR.glob("libvship*.dll")) + list(VS_PLUGINS_DIR.glob("libvship*.so")):
                force_remove(f)

        # WRITE CONFIG
        config_variant = winner.get('variant', 'nvidia')
        config_streams = winner.get('streams', winner.get('workers', 1))
        if winner['tool'] == 'vs-hip':
            config_streams = winner.get('streams', 1)
        write_ssimu2_config(
            tool=winner['tool'],
            filter_tool=filter_tool,
            workercount=winner['workers'],
            variant=config_variant,
            streams=config_streams,
        )
        if filters_enabled and filter_tool not in ('vs-hip', 'vs-zip'):
            print("   [Config] Filtering is enabled; forced filter-tool to vs-hip/vs-zip.", file=sys.stderr)

        return {
            "tool": winner["tool"],
            "variant": config_variant,
            "workercount": winner["workers"],
            "streams": config_streams,
        }

    except Exception as e:
        print(f"Fatal error: {e}", file=sys.stderr)
        write_ssimu2_config(tool="vs-hip", filter_tool="vs-hip", workercount=1,
                            variant=fallback_variant(vendor), streams=1)
        return None
    finally:
        cleanup_temp_files()


def run_optimize_mode(bat_arg=None):
    """One-time optimized SSIMU2 benchmark driven by a generated .bat with
    optimize-workers=true. Writes the benchmark winner into that exact .bat.

    custom-ssim2-tool stores the winning backend and GPU variant when needed.
    custom-ssim2-workers stores the matching stream/worker count for that
    winning backend, not the unrelated vs-zip worker count unless vs-zip won.
    If both custom values are already present, this returns immediately."""
    bat_path = None
    if bat_arg and Path(bat_arg).is_file():
        bat_path = Path(bat_arg).resolve()
    else:
        bat_path = find_active_bat_file()

    if not bat_path:
        print("[Optimize] Could not locate the launching .bat file. Skipping optimized benchmark.", file=sys.stderr)
        return

    bat = parse_bat_settings(bat_path)

    if bat.get("optimize-workers", "").strip().lower() not in ("true", "1", "yes", "on"):
        # This bat did not opt in - nothing to do.
        return

    existing_workers = bat.get("custom-ssim2-workers", "").strip()
    existing_tool = bat.get("custom-ssim2-tool", "").strip()
    if existing_workers.isdigit() and int(existing_workers) > 0 and existing_tool:
        print(f"[Optimize] custom-ssim2-tool={existing_tool} and custom-ssim2-workers={existing_workers} already set in "
              f"{bat_path.name}. Skipping benchmark.", file=sys.stderr)
        return

    fork = (bat.get("fork", "5fish") or "5fish").strip()

    # The bat's DENOISE is what dispatch writes into settings.txt at encode
    # time - apply it now so the benchmark's filtering matches exactly.
    bat_denoise = bat.get("denoise", "").strip().lower()
    if bat_denoise in ("true", "false"):
        SETTINGS_OVERRIDES["denoise"] = bat_denoise

    print("\n-------------------------------------------------------------------------------", file=sys.stderr)
    print(f"[Optimize] One-time optimized SSIMU2 benchmark for: {bat_path.name}", file=sys.stderr)
    print("-------------------------------------------------------------------------------", file=sys.stderr)

    if CONFIG_FILE.exists():
        # Tool selection was already benchmarked. Reuse that winner instead of
        # replacing GPU winners with the vs-zip worker count.
        winner_cfg = read_ssimu2_config(CONFIG_FILE)
    else:
        # First run: the full suite handles tool selection and writes the
        # winning backend/worker-or-stream count to workercount-ssimu2.txt.
        winner_cfg = run_full_suite(target_fork=fork, use_filters=True) or {}

    winner_tool = format_bat_ssimu2_tool(winner_cfg.get("tool"), winner_cfg.get("variant"))
    winner_count = winner_count_from_config(winner_cfg)

    if winner_tool and winner_count:
        wrote_tool = set_bat_value(bat_path, "custom-ssim2-tool", winner_tool)
        wrote_workers = set_bat_value(bat_path, "custom-ssim2-workers", winner_count)
        if wrote_tool and wrote_workers:
            print(f"[Optimize] Wrote custom-ssim2-tool={winner_tool} and custom-ssim2-workers={winner_count} into {bat_path.name}", file=sys.stderr)
            print("[Optimize] Clear those values in the .bat to re-run this benchmark.", file=sys.stderr)
    else:
        print("[Optimize] SSIMU2 benchmark/config did not produce a winner; leaving custom SSIMU2 settings empty.", file=sys.stderr)


def _parse_cli(argv):
    optimize = False
    bat_arg = None
    i = 0
    while i < len(argv):
        if argv[i] == "--optimize-script":
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
    optimize, bat_arg = _parse_cli(sys.argv[1:])
    try:
        if optimize:
            run_optimize_mode(bat_arg)
        else:
            run_full_suite()
    finally:
        # This script is the last benchmark consumer of the shared sample.
        delete_benchmark_sample()