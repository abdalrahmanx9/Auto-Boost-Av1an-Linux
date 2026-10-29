"""Inference backends for the rescale section of video-input\\template.vpy.

The rescale block the template writer can add descales an anime source to its
native production resolution and rebuilds it with ArtCNN, which is an ONNX model
run through vs-mlrt. vs-jetpack 2 drives that through vsscale.Backend rather
than the standalone vsmlrt module, but the plugin side is the same one: an
inference backend has to be there, and which one is available decides how fast
the rescale is:

  DirectML     ships with this package. vsort.dll plus DirectML.dll and
               onnxruntime.dll, about 40 MB, and it runs on any Direct3D 12 GPU -
               NVIDIA, AMD or Intel. The slow one, but it works out of the box
               on every machine.
  TensorRT-RTX ships with this package too. NVIDIA RTX only, 20-series and
               newer. About 250 MB installed. "The TensorRT-RTX files" below is
               what it is made of.
  MIGraphX     AMD only. Faster than DirectML, 385 MB installed, and the one
               backend here that is still fetched on demand.

How much TensorRT-RTX is worth depends entirely on the model. On an RTX 5060,
rescaling a 1500x844 descale of a 1080p source, measured over the rescale alone:

  ArtCNN C4F32   33 fps on TensorRT-RTX, 32 fps on DirectML
  ArtCNN R8F64   17 fps on TensorRT-RTX, 11 fps on DirectML

C4F32 is small enough that neither backend is the bottleneck - the descale, the
masks and the downscale are - so the choice barely shows. R8F64 is where the
backend does the work, and where the download pays for itself. A full filter
chain with a dehalo in it moves both numbers down together.

MIGraphX comes from the vs-mlrt release this package is pinned to, with curl and
the 7z.exe in the VapourSynth folder, and is unpacked into VapourSynth\\vs-plugins.
Pinning matters: the plugin DLLs and the runtime folders that go with them are
versioned together, and mixing releases is what produces "unable to load" errors
that look like a broken install.

Full TensorRT is deliberately not one of the choices. It is the faster of the two
NVIDIA paths, but it wants vsmlrt-cuda for the plugin (2.6 GB to download, 3.5 GB
installed) and NVIDIA's tensorrt_cu13_libs wheel for the engine builder that
vs-jetpack 2 drives (1.9 GB more). TensorRT-RTX does the same job for the models
this package uses in 250 MB, and builds its engine in under a second rather than
minutes.

A vs-mlrt plugin DLL and its runtime folder (vsmlrt-cuda, vsmlrt-hip) are looked
for next to each other - the DLL builds an absolute path to that folder out of
its own location and gives up if nothing is there - so both land in the same
directory. The archives already carry the folder names, so they are simply
extracted alongside the DLL.

--- The TensorRT-RTX files -------------------------------------------------

Four things have to be in place, and all four ship with the package:

    Lib\\site-packages\\vapoursynth\\plugins\\vstrt_rtx.dll
        the plugin, out of VSTRT-RTX-Windows-x64 in the pinned vs-mlrt release.
    Lib\\site-packages\\vapoursynth\\plugins\\vsmlrt-cuda\\
        tensorrt_rtx_1_4.dll and tensorrt_onnxparser_rtx_1_4.dll, out of
        NVIDIA's tensorrt_rtx_cu13_libs wheel. vstrt_rtx.dll preloads the first
        of those by absolute path from its own folder, which is why the runtime
        sits here instead of in site-packages where the wheel puts it.
    Lib\\site-packages\\tensorrt_rtx_bindings, tensorrt_rtx, tensorrt_rtx_libs
        the Python side. vs-jetpack 2 builds the engine through the tensorrt_rtx
        module rather than through trtexec, so the bindings are not optional.
        tensorrt_rtx and tensorrt_rtx_libs are two short files written by hand:
        NVIDIA publishes the first only as an sdist that pip would have to
        build, and the second is what loads the runtime out of vsmlrt-cuda
        instead of keeping a second 200 MB copy of it.
    Lib\\site-packages\\cuda
        cuda-core, with cuda-bindings and cuda-pathfinder under it, which
        vsscale uses to select the CUDA device before it builds an engine.

The versions are tied together: the plugin reports the TensorRT-RTX version it
was built against - 1.4.0 for v15.16 - and vsscale refuses to run if the Python
bindings do not match it to the patch number. To rebuild the set, run this from
the VapourSynth folder

    python.exe -m pip install --no-deps --extra-index-url https://pypi.nvidia.com
        tensorrt_rtx_cu13_bindings==1.4.0.76 tensorrt_rtx_cu13_libs==1.4.0.76
    python.exe -m pip install cuda-core cuda-bindings

then move the two DLLs out of site-packages\\tensorrt_rtx_libs into the
plugins\\vsmlrt-cuda folder above, leaving that package's hand-written
__init__.py in place.

Two things moved in vs-jetpack 2 and are no longer this file's business. The
ArtCNN ONNX models used to sit in vs-plugins\\models; vsscale now resolves them
out of a .vsjet folder beside the running script, falling back to a per-user
cache under AppData. Neither is any use to a portable package - every script
that asks for a model here is a rendered copy in a temp folder, and AppData does
not travel with the folder - so vpy_template.render() puts a few lines on the
front of any rendered script that uses vsscale, redirecting the first of those
lookups to VapourSynth\\.vsjet, where this package keeps the models. They go
there with

    python.exe -c "from vsscale.mlrt.cli import app; app()" onnx download ArtCNN --latest

run from the VapourSynth folder, which is the directory vsscale's downloader
hangs its .vsjet off. The Scripts\\vsscale.exe launcher is not used: like the
other Scripts launchers it carries the build machine's python path and exits
without doing anything here. That is also why directml_installed() below no
longer looks for the models. That same redirect is what decides where a built
TensorRT engine is cached - VapourSynth\\.vsjet\\vsscale\\artifact - so engines
travel with the package too.

Used by bat-builder.py's template.vpy page. Nothing in an encode imports this.
"""

import os
import shutil
import subprocess

# The vs-mlrt release everything here is pinned to. The plugin DLLs and the
# runtime folders that go with them all come from this one release. The ArtCNN
# models no longer do - vs-jetpack 2 fetches those itself, from the ArtCNN
# releases, into a .vsjet folder.
RELEASE = "v15.16"
RELEASE_URL = f"https://github.com/AmusementClub/vs-mlrt/releases/tag/{RELEASE}"
DOWNLOAD_BASE = f"https://github.com/AmusementClub/vs-mlrt/releases/download/{RELEASE}"

# Downloads land here and the folder is removed once everything is unpacked.
TEMP_DIRNAME = "mlrt-download.tmp"

# Spare room left over after the archive and its extracted contents, so the
# install cannot be the thing that fills the disk.
FREE_SPACE_MARGIN = 512 * 1024 * 1024


def root_dir():
    """The package root, one level above this tools folder."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _linux_plugin_dirs():
    """System VapourSynth autoload dirs on Linux."""
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
    return [d for d in dirs if os.path.isdir(d)] or ["/usr/local/lib/vapoursynth"]


def vapoursynth_dir():
    return root_dir()


def plugins_dir():
    """Where a backend plugin lives on Linux (first system autoload dir)."""
    return _linux_plugin_dirs()[0]


def curl_path():
    return shutil.which("curl") or "curl"


def sevenzip_path():
    return shutil.which("7z") or shutil.which("7zz") or "7z"


class Backend:
    """One downloadable vs-mlrt backend.

    assets   (filename, size in bytes) for each release asset, in download
             order. A multi-volume 7z is listed as its parts; only the .001 is
             handed to 7z, which pulls the rest in itself.
    markers  paths under vs-plugins that have to exist afterwards. Both the
             plugin DLL and its runtime folder are checked, because the DLL on
             its own loads and then fails at the first frame.
    """

    def __init__(self, key, title, hardware, backend_call, assets, markers,
                 installed_bytes, notes):
        self.key = key
        self.title = title
        self.hardware = hardware
        self.backend_call = backend_call
        self.assets = assets
        self.markers = markers
        self.installed_bytes = installed_bytes
        self.notes = notes

    @property
    def download_bytes(self):
        return sum(size for _, size in self.assets)

    def marker_paths(self):
        return [os.path.join(plugins_dir(), marker) for marker in self.markers]

    def is_installed(self):
        return all(os.path.exists(path) for path in self.marker_paths())

    def missing_markers(self):
        return [marker for marker, path in zip(self.markers, self.marker_paths())
                if not os.path.exists(path)]


# Sizes are the release's own, so the figures shown before a download are the
# real ones rather than an estimate.
#
# NVIDIA is not in here. TensorRT-RTX is small enough to ship, so it is already
# in the package rather than something to fetch - tensorrt_rtx_installed() is
# the NVIDIA question now.
BACKENDS = {
    "amd": Backend(
        key="amd",
        title="AMD (MIGraphX)",
        hardware="AMD Radeon with ROCm/HIP support",
        backend_call="Backend.MIGX(fp16=True)",
        assets=[
            ("vsmlrt-hip.v15.16.7z", 67621170),
            ("VSMIGX-Windows-x64.v15.16.7z", 130464),
        ],
        markers=["libvsmigx.so", "vsmlrt-hip"],
        installed_bytes=403595328,
        notes=(
            "MIGraphX compiles the model for your GPU on the first rescale, "
            "which takes a minute or two and is cached afterwards."
        ),
    ),
}


def human_size(num_bytes):
    """A size for reading, in the largest unit that does not round it to zero."""
    gigabytes = num_bytes / (1024 ** 3)
    if gigabytes >= 1:
        return f"{gigabytes:.1f} GB"
    megabytes = num_bytes / (1024 ** 2)
    if megabytes >= 1:
        return f"{megabytes:.0f} MB"
    return f"{num_bytes / 1024:.0f} KB"


def pip_plugins_dir():
    """Alias of plugins_dir() on Linux (single system autoload tree)."""
    return plugins_dir()


def find_plugin_file(filename, max_depth=2):
    """The first copy of filename under any autoload dir, or None."""
    roots = [(root, 0) for root in dict.fromkeys(_linux_plugin_dirs())]
    while roots:
        root, depth = roots.pop(0)
        candidate = os.path.join(root, filename)
        if os.path.exists(candidate):
            return candidate
        if depth >= max_depth:
            continue
        try:
            roots.extend((entry.path, depth + 1)
                         for entry in os.scandir(root) if entry.is_dir())
        except OSError:
            continue
    return None


def directml_installed():
    """True when an ONNX Runtime backend plugin is in place (Linux: libvsort.so)."""
    return find_plugin_file("libvsort.so") is not None


def tensorrt_rtx_installed():
    """True when the TensorRT backend plugin is in place (Linux: libvstrt.so)."""
    return find_plugin_file("libvstrt.so") is not None


def tools_present():
    """(ok, message) - on Linux the installer handles downloads, always ok."""
    return True, ""


def free_space():
    """Bytes free on the drive the package is on, or None if it cannot be read."""
    try:
        return shutil.disk_usage(root_dir()).free
    except OSError:
        return None


def space_shortfall(backend):
    """How much more free space is needed, or 0 when there is enough.

    The archive and its extracted contents are both on the disk at once, since
    the download is only deleted after 7z has finished with it.
    """
    available = free_space()
    if available is None:
        return 0
    needed = backend.download_bytes + backend.installed_bytes + FREE_SPACE_MARGIN
    return max(0, needed - available)


def temp_dir():
    return os.path.join(vapoursynth_dir(), TEMP_DIRNAME)


def clear_temp_dir():
    """Remove the download folder. Failure is not fatal - it is only a cache."""
    try:
        shutil.rmtree(temp_dir())
    except OSError:
        pass


def _complete(path, expected_size):
    """True when this file is already the size the release says it should be."""
    try:
        return os.path.getsize(path) == expected_size
    except OSError:
        return False


def _download(url, dest):
    """Fetch one asset with curl, leaving its progress bar on screen.

    -C - resumes a part-downloaded file, which is what makes a 2.4 GB download
    survivable on a connection that drops: running the install again picks up
    where it stopped instead of starting over.
    """
    command = [
        curl_path(),
        "-L",                    # release assets redirect to a CDN
        "--fail",                # an HTML error page is not a download
        "--retry", "3",
        "--retry-delay", "5",
        "-C", "-",               # resume a partial file
        "-o", dest,
        url,
    ]
    result = subprocess.run(command)
    return result.returncode == 0


def _extract(archive, destination):
    """Unpack one archive into vs-plugins with the bundled 7-Zip.

    The archives carry their own folder names (vsmlrt-cuda\\, vsmlrt-hip\\) plus
    the plugin DLL at the root, so extracting straight into vs-plugins puts each
    runtime folder next to the DLL that goes with it.
    """
    command = [sevenzip_path(), "x", "-y", f"-o{destination}", archive]
    result = subprocess.run(command)
    return result.returncode == 0


def install(key):
    """Install one backend. Returns (ok, message).

    Linux: the vs-mlrt backends are system plugins, so installation goes
    through the repo's installer rather than an in-app download.
    """
    backend = BACKENDS.get(key)
    if backend is None:
        return False, f"Unknown backend {key!r}."
    return False, (
        f"On Linux the vs-mlrt backends are installed system-wide.\n"
        f"Run:  sudo ./setup.sh --install mlrt\n"
        f"(then re-run this tool; {backend.title} markers: {', '.join(backend.markers)})"
    )
