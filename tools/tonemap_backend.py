"""Picks the HDR -> SDR tonemap backend and emits the .vpy block that runs it.

Why this exists: vs-placebo's Tonemap hardcodes its output texture as three
16-bit UNORM components and asks the GPU to render into it
(vs-placebo/src/tonemap.c:115). AMD and NVIDIA drivers do not expose
R16G16B16_UNORM at all, so libplacebo pads the request to RGBA16, which is
renderable, and the filter works. Intel's Windows driver *does* expose the
3-component format, but sampleable-only, so libplacebo rejects the texture:

    Validation failed: !params->renderable || fmt_caps & PL_FMT_CAP_RENDERABLE
      (../subprojects/libplacebo/src/gpu.c:250)
      for texture: ../src/tonemap.c:115
    Failed creating GPU textures!

The filter logs that once per frame and then hands back black frames instead of
raising, so on an Intel card the whole encode silently comes out black. Nothing
in the .vpy can steer that format choice - it is baked into the plugin - so the
GPU is probed once per run and the tonemap falls back to a CPU implementation
(ITU-R BT.2390 EETF) when the probe comes back broken.

Only Tonemap is affected. placebo.Resample and placebo.Deband pick renderable
formats and keep working on Intel, so the rest of the filter chain is untouched.

Run this file directly to see which backend the current GPU gets:

    VapourSynth\\python.exe tools\\tonemap_backend.py
"""

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
import subprocess
import sys

PLACEBO = "placebo"
CPU = "cpu"

# A working tonemap turns the probe's ~490 nit patch into near-white; the broken
# path returns ~0.00003. Anything in between is treated as broken.
_PROBE_MIN_AVG = 0.05
_PROBE_TIMEOUT = 180

# Ran in a child process so a driver crash or hang cannot take the dispatch with
# it, and so the Vulkan context is gone before av1an starts its own workers.
_PROBE_SOURCE = r"""
import os
import sys

import vapoursynth as vs

core = vs.core
plugin_dir = sys.argv[1] if len(sys.argv) > 1 else ""
if plugin_dir and not hasattr(core, "placebo") and os.path.isdir(plugin_dir):
    for dll in sorted(os.listdir(plugin_dir)):
        if dll.lower().endswith((".dll", ".so")):
            try:
                core.std.LoadPlugin(os.path.join(plugin_dir, dll))
            except Exception:
                pass

if not hasattr(core, "placebo"):
    print("PROBE:missing")
    raise SystemExit(0)

# A flat PQ patch around 490 nits, tagged as BT.2020 PQ limited range.
clip = core.std.BlankClip(width=64, height=64, format=vs.YUV444P16, length=1,
                          color=[40000, 32768, 32768])
clip = core.std.SetFrameProps(clip, _Matrix=9, _Transfer=16, _Primaries=9, _ColorRange=1)
try:
    out = core.placebo.Tonemap(clip, src_csp=1, dst_csp=0)
    avg = core.std.PlaneStats(out).get_frame(0).props["PlaneStatsAverage"]
except Exception as e:
    print("PROBE:error " + " ".join(str(e).split()))
    raise SystemExit(0)
print("PROBE:avg %.6f" % avg)
"""

_cached_backend = None
_cached_reason = ""


def vs_plugin_dir():
    """VapourSynth/vs-plugins next to this tools/ directory."""
    tools_dir = os.path.dirname(os.path.abspath(__file__))
    root_dir = os.path.dirname(tools_dir)
    return os.path.join(root_dir, "VapourSynth", "vs-plugins")


def probe_gpu_tonemap(python_exe=None, plugin_dir=None):
    """Return (works, reason) for placebo.Tonemap on this machine's GPU."""
    python_exe = python_exe or sys.executable
    plugin_dir = plugin_dir or vs_plugin_dir()
    try:
        result = subprocess.run(
            [python_exe, "-c", _PROBE_SOURCE, plugin_dir],
            capture_output=True, text=True, errors="replace", timeout=_PROBE_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return False, "the GPU tonemap probe timed out"
    except Exception as e:
        return False, f"the GPU tonemap probe could not be started ({e})"

    stdout = result.stdout or ""
    stderr = result.stderr or ""
    if "Failed creating GPU textures" in stderr or "no good texture format" in stderr:
        return False, "this GPU has no renderable 16-bit RGB texture format for vs-placebo"
    if result.returncode != 0:
        detail = " ".join((stderr.strip().splitlines() or ["no output"])[-1].split())
        return False, f"the GPU tonemap probe failed ({detail})"

    for line in stdout.splitlines():
        line = line.strip()
        if line == "PROBE:missing":
            return False, "the libplacebo plugin is not loaded"
        if line.startswith("PROBE:error "):
            return False, f"placebo.Tonemap returned an error ({line[len('PROBE:error '):]})"
        if line.startswith("PROBE:avg "):
            try:
                avg = float(line.split()[1])
            except (IndexError, ValueError):
                break
            if avg >= _PROBE_MIN_AVG:
                return True, "GPU tonemap probe passed"
            return False, "placebo.Tonemap returned black frames on this GPU"
    return False, "the GPU tonemap probe returned no result"


def resolve_backend(settings=None, python_exe=None, force=None):
    """Return ("placebo"|"cpu", reason), probing the GPU at most once per run.

    settings: parsed settings.txt dict; tonemap_backend=auto|gpu|cpu.
    force:    overrides settings.txt, same values.
    """
    global _cached_backend, _cached_reason

    choice = force
    if choice is None and settings:
        choice = settings.get("tonemap_backend")
    choice = (choice or "auto").strip().lower()

    if choice in ("cpu", "software"):
        return CPU, "tonemap_backend=cpu in settings.txt"
    if choice in ("gpu", "placebo", "libplacebo"):
        return PLACEBO, "tonemap_backend=gpu in settings.txt"
    if choice not in ("auto", ""):
        print(f"[Dispatch] Warning: Unknown tonemap_backend={choice!r}; using auto.")

    if _cached_backend is None:
        works, reason = probe_gpu_tonemap(python_exe)
        _cached_backend = PLACEBO if works else CPU
        _cached_reason = reason
    return _cached_backend, _cached_reason


def describe_backend(backend, reason):
    """One line for the dispatch log."""
    if backend == PLACEBO:
        return "tonemap HDR -> SDR (BT.709) via libplacebo (GPU)"
    return f"tonemap HDR -> SDR (BT.709) via BT.2390 on the CPU ({reason})"


_PLACEBO_BLOCK = '''# Tonemap HDR -> SDR (libplacebo; libvsplacebo.so autoloads from the plugins directory)
do_tonemap = {tonemap}
tonemap_backend = "placebo"
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
    src = src.std.SetFrameProps(_Matrix=1, _Transfer=1, _Primaries=1)'''


_CPU_BLOCK = '''# Tonemap HDR -> SDR on the CPU (ITU-R BT.2390 EETF).
# vs-placebo's Tonemap asks the GPU to render into a 3-component 16-bit texture
# (src/tonemap.c:115). AMD and NVIDIA do not expose that format so libplacebo
# pads it to RGBA16 and the filter works; Intel exposes it sampleable-only, the
# texture is refused and every frame comes back black without an error. Nothing
# in this script can change the format the plugin asks for, so the tonemap is
# done here instead. See tools/tonemap_backend.py.
do_tonemap = {tonemap}
tonemap_backend = "cpu"
if do_tonemap:
    _PQ_M1 = 2610.0 / 16384.0
    _PQ_M2 = 2523.0 / 4096.0 * 128.0
    _PQ_C1 = 3424.0 / 4096.0
    _PQ_C2 = 2413.0 / 4096.0 * 32.0
    _PQ_C3 = 2392.0 / 4096.0 * 32.0

    def _pq_encode(nits):
        """Absolute luminance in nits -> PQ (SMPTE ST 2084) code value."""
        y = max(float(nits), 0.0) / 10000.0
        ym = y ** _PQ_M1
        return ((_PQ_C1 + _PQ_C2 * ym) / (1.0 + _PQ_C3 * ym)) ** _PQ_M2

    def _tonemap_src_peak(clip, default=1000.0):
        """Mastering peak from the HDR10 metadata, or 1000 nits when absent."""
        try:
            props = clip.get_frame(0).props
        except Exception:
            return default
        for key in ("MasteringDisplayMaxLuminance", "ContentLightLevelMax"):
            try:
                value = float(props[key])
            except Exception:
                continue
            if 100.0 <= value <= 10000.0:
                return value
        return default

    def _eetf_expr(src_peak, dst_peak=100.0):
        """BT.2390 EETF written as an std.Expr program over PQ code values.

        Below the knee KS the curve is the identity, so everything up to roughly
        10 nits comes through untouched and only highlights are rolled off into
        the SDR peak. Above it, BT.2390's Hermite spline, expanded into a cubic
        in T and evaluated by Horner's rule.
        """
        pq_src = _pq_encode(src_peak)
        maxlum = _pq_encode(dst_peak) / pq_src
        ks = 1.5 * maxlum - 0.5
        k1 = 1.0 / (pq_src * (1.0 - ks))   # T = (E1 - KS) / (1 - KS), with
        k2 = ks / (1.0 - ks)               # E1 = x / pq_src, folded into x*k1 - k2
        a = ks + 1.0 - 2.0 * maxlum
        b = 3.0 * maxlum - ks - 2.0
        c = 1.0 - ks
        d = ks
        x = f"x 0 max {pq_src:.10f} min"
        t = f"{x} {k1:.10f} * {k2:.10f} -"
        poly = (f"{t} {a:.10f} * {b:.10f} + {t} * {c:.10f} + "
                f"{t} * {d:.10f} + {pq_src:.10f} *")
        return f"{x} {ks * pq_src:.10f} <= {x} {poly} ?"

    # The EETF is defined on the PQ signal, so YUV -> RGB happens without a
    # transfer conversion and the curve is applied per channel. zimg then does
    # PQ -> linear -> BT.709 and BT.2020 -> BT.709 primaries in one step, with
    # 100 nits as reference white. The source is assumed to be HDR10 (PQ,
    # BT.2020), the same assumption the libplacebo path makes with src_csp=1.
    _tm_peak = _tonemap_src_peak(src)
    src = src.resize.Bicubic(format=vs.RGBS, matrix_in_s="2020ncl",
                             range_s="full", dither_type="none")
    src = core.std.Expr(src, _eetf_expr(_tm_peak))
    src = src.resize.Bicubic(format=vs.YUV420P16, matrix_s="709",
                             transfer_in_s="st2084", transfer_s="709",
                             primaries_in_s="2020", primaries_s="709",
                             range_s="limited", nominal_luminance=100,
                             dither_type="error_diffusion")
    src = src.std.SetFrameProps(_Matrix=1, _Transfer=1, _Primaries=1)'''


def vpy_tonemap_block(backend, tonemap):
    """The tonemap section of the generated .vpy, for the chosen backend.

    Substitution is a plain replace rather than str.format so the block can keep
    the f-strings and dict literals it needs.
    """
    template = _CPU_BLOCK if backend == CPU else _PLACEBO_BLOCK
    return template.replace("{tonemap}", str(bool(tonemap)))


def vpy_backend_marker(backend):
    """Marker a cached .vpy is checked against, so a backend change rebuilds it."""
    return f'tonemap_backend = "{backend}"'


if __name__ == "__main__":
    backend, reason = resolve_backend()
    print(f"tonemap backend: {backend} ({reason})")
