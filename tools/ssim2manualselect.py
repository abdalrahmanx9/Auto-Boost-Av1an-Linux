import importlib.util
import shutil
import sys
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent.resolve()
TOOLS_DIR = BASE_DIR / "tools"
CONFIG_FILE = TOOLS_DIR / "workercount-ssimu2.txt"
SSIMU2_WORKERCOUNT = TOOLS_DIR / "ssimu2-workercount.py"
# R79 autoloads VapourSynth\Lib\site-packages\vapoursynth\plugins, the same
# folder ssimu2-workercount.py installs the winning libvship build into. The old
# VapourSynth\vs-plugins folder no longer ships and is never autoloaded.
VS_PLUGINS_DIR = (BASE_DIR / "VapourSynth" / "Lib" / "site-packages"
                  / "vapoursynth" / "plugins")
if not VS_PLUGINS_DIR.is_dir():
    for _d in ("/usr/local/lib/vapoursynth", "/usr/lib/x86_64-linux-gnu/vapoursynth", "/usr/lib/vapoursynth"):
        import os as _os
        if _os.path.isdir(_d):
            VS_PLUGINS_DIR = Path(_d)
            break
VS_HIP_SOURCE_DIR = TOOLS_DIR / "vs-hip"

TOOL_CHOICES = [
    {
        "label": "vs-hip NVIDIA (GPU/VapourSynth plugin)",
        "tool": "vs-hip",
        "variant": "nvidia",
        "filter_tool": "vs-hip",
        "kind": "gpu",
        "dll": "libvship_NVIDIA.dll",
    },
    {
        "label": "vs-hip AMD (GPU/VapourSynth plugin)",
        "tool": "vs-hip",
        "variant": "amd",
        "filter_tool": "vs-hip",
        "kind": "gpu",
        "dll": "libvship_AMD.dll",
    },
    {
        "label": "vs-hip Vulkan (GPU/VapourSynth plugin)",
        "tool": "vs-hip",
        "variant": "vulkan",
        "filter_tool": "vs-hip",
        "kind": "gpu",
        "dll": "libvship_VULKAN.dll",
    },
    {
        "label": "FFVship NVIDIA (GPU executable)",
        "tool": "ffvship_nvidia",
        "variant": "nvidia",
        "filter_tool": "vs-zip",
        "kind": "gpu",
    },
    {
        "label": "FFVship AMD (GPU executable)",
        "tool": "ffvship_amd",
        "variant": "amd",
        "filter_tool": "vs-zip",
        "kind": "gpu",
    },
    {
        "label": "FFVship Vulkan (GPU executable)",
        "tool": "ffvship_vulkan",
        "variant": "vulkan",
        "filter_tool": "vs-zip",
        "kind": "gpu",
    },
    {
        "label": "vs-zip CPU (VapourSynth plugin)",
        "tool": "vs-zip",
        "variant": "cpu",
        "filter_tool": "vs-zip",
        "kind": "cpu",
    },
]


def prompt_number(prompt, min_value, max_value):
    while True:
        value = input(prompt).strip()
        try:
            number = int(value)
        except ValueError:
            print(f"Please enter a number from {min_value} to {max_value}.")
            continue
        if min_value <= number <= max_value:
            return number
        print(f"Please enter a number from {min_value} to {max_value}.")


def write_ssimu2_config(tool, filter_tool, workercount, variant, streams):
    workercount = max(1, int(workercount))
    streams = max(0, int(streams))
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        f.write(f"tool={tool}\n")
        f.write(f"filter-tool={filter_tool}\n")
        f.write(f"workercount={workercount}\n")
        if variant:
            f.write(f"variant={variant}\n")
        if streams:
            f.write(f"streams={streams}\n")


def force_remove(path):
    path = Path(path)
    if not path.exists():
        return
    try:
        path.unlink(missing_ok=True)
    except (PermissionError, OSError):
        trash_name = path.with_suffix(path.suffix + ".trash_manualselect")
        try:
            path.rename(trash_name)
        except OSError:
            pass


def remove_vship_dlls():
    if not VS_PLUGINS_DIR.exists():
        return
    for dll in VS_PLUGINS_DIR.glob("libvship*.dll"):
        force_remove(dll)


def apply_vship_dll(choice):
    remove_vship_dlls()
    dll_name = choice.get("dll")
    if not dll_name:
        return True
    src = VS_HIP_SOURCE_DIR / dll_name
    dst = VS_PLUGINS_DIR / dll_name
    if not src.exists():
        print(f"Warning: {src} was not found. Config will still be written, but the plugin DLL was not copied.")
        return False
    VS_PLUGINS_DIR.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(src, dst)
        print(f"Copied {dll_name} into {VS_PLUGINS_DIR} for {choice['label']}.")
        return True
    except OSError as exc:
        print(f"Warning: could not copy {dll_name}: {exc}")
        return False


def save_choice(choice, count):
    count = max(1, int(count))
    if choice["tool"] == "vs-hip":
        workercount = 1
        streams = count
        apply_vship_dll(choice)
    elif choice["tool"] == "vs-zip":
        workercount = count
        streams = 0
        remove_vship_dlls()
    else:
        workercount = count
        streams = count
        remove_vship_dlls()

    write_ssimu2_config(
        tool=choice["tool"],
        filter_tool=choice["filter_tool"],
        workercount=workercount,
        variant=choice["variant"],
        streams=streams,
    )
    print(f"\nWrote {CONFIG_FILE}")
    print(f"tool={choice['tool']}")
    print(f"filter-tool={choice['filter_tool']}")
    print(f"workercount={workercount}")
    print(f"variant={choice['variant']}")
    if streams:
        print(f"streams={streams}")


def load_worker_module():
    if not SSIMU2_WORKERCOUNT.exists():
        raise FileNotFoundError(f"Could not find {SSIMU2_WORKERCOUNT}")
    spec = importlib.util.spec_from_file_location("ssimu2_workercount_manual", SSIMU2_WORKERCOUNT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load {SSIMU2_WORKERCOUNT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def benchmark_selected(choice):
    print("\nLoading benchmark code from tools\\ssimu2-workercount.py...")
    wc = load_worker_module()
    encoded_file = None
    try:
        wc.cleanup_temp_files()
        wc.setup_svt_av1_fork(target_fork="5fish")
        encoded_file = wc.run_fast_pass()
        if not encoded_file:
            raise RuntimeError("Fast pass generation failed; cannot benchmark this tool.")

        if choice["tool"] == "vs-hip":
            fps, count, elapsed = wc.run_gpu_suite(choice["dll"], encoded_file, choice["variant"])
            count_name = "streams"
        elif choice["tool"].startswith("ffvship"):
            # GPU_BACKENDS maps variant -> (vs-hip DLL, FFVship exe), so the exe
            # paths stay defined in one place alongside the automatic benchmark.
            backend = wc.GPU_BACKENDS.get(choice["variant"])
            if not backend:
                raise RuntimeError(f"Unknown FFVship variant: {choice['variant']}")
            fps, count, elapsed = wc.run_ffvship_suite(backend[1], choice["variant"], encoded_file)
            count_name = "streams"
        elif choice["tool"] == "vs-zip":
            fps, count, elapsed = wc.benchmark_cpu_vszip(encoded_file, use_filters=False)
            count_name = "workers"
        else:
            raise RuntimeError(f"Unsupported tool: {choice['tool']}")

        if fps <= 0:
            raise RuntimeError(f"Benchmark failed for {choice['label']}.")
        print(f"\nBenchmark winner for {choice['label']}: {count} {count_name} ({fps:.2f} fps, {elapsed:.2f}s)")
        save_choice(choice, count)
    finally:
        try:
            wc.cleanup_temp_files()
        except Exception:
            pass


def main():
    print("SSIMULACRA 2 manual tool/count selector")
    print("This creates or replaces tools\\workercount-ssimu2.txt.\n")
    print("Select the SSIMULACRA 2 metrics tool:")
    for i, choice in enumerate(TOOL_CHOICES, start=1):
        print(f"  {i}. {choice['label']}")
    selected = prompt_number("\nEnter tool number: ", 1, len(TOOL_CHOICES))
    choice = TOOL_CHOICES[selected - 1]

    print(f"\nSelected: {choice['label']}")
    if choice["kind"] == "gpu":
        print("  1. Automatically Benchmark stream count")
        print("  2. Manually input streams number")
        action = prompt_number("\nEnter option number: ", 1, 2)
        if action == 1:
            benchmark_selected(choice)
        else:
            streams = prompt_number("Enter streams number: ", 1, 999)
            save_choice(choice, streams)
    else:
        print("  1. Automatically Benchmark worker count")
        print("  2. Manually input worker count")
        action = prompt_number("\nEnter option number: ", 1, 2)
        if action == 1:
            benchmark_selected(choice)
        else:
            workers = prompt_number("Enter worker count: ", 1, 999)
            save_choice(choice, workers)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nCancelled.")
        sys.exit(130)
    except Exception as exc:
        print(f"\nError: {exc}", file=sys.stderr)
        sys.exit(1)
