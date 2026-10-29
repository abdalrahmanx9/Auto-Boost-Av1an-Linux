import os
import re
import subprocess

BLUE = "\033[94m"
RED = "\033[91m"
RESET = "\033[0m"

LQTC_DIR_NAME = "LQTC"

# Every backend/fork combination ships in three CPU builds. x86-64-v3 is the
# one every modern CPU can run, so it is both the default and what a missing
# znver2/avx512 build falls back to.
DEFAULT_ARCH = "x86-64-v3"

# The CPU build menu, and the cputarget= value each option stores in
# tools\workercount-config.txt so the next build can default to it.
ARCH_BY_CHOICE = {"1": "znver2", "2": DEFAULT_ARCH, "3": "avx512"}
CHOICE_BY_ARCH = {arch: choice for choice, arch in ARCH_BY_CHOICE.items()}

# CVVDP lives on a 0-10 JOD scale, SSIMULACRA2 on a 0-100 scale. LQTC picks the
# metric from the number it is given, so the ranges below are not just advice -
# a target outside them selects a different metric than the user asked for.
CVVDP_DEFAULT = "9.54-9.56"
SSIMU2_DEFAULT = "88-90"

# The range of CRF values the quality target search is allowed to pick from.
# LQTC's own default is 8.0-48.0, which searches far more than 1080p needs.
DEFAULT_CRF_RANGE = "12-38"


def enable_ansi_colors():
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


def clear_screen():
    os.system('cls' if os.name == 'nt' else 'clear')


def workercount_config_path():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "workercount-config.txt")


def read_saved_arch():
    """The cputarget= remembered in tools\\workercount-config.txt, or None."""
    try:
        with open(workercount_config_path(), "r", encoding="utf-8",
                  errors="ignore") as handle:
            text = handle.read()
    except OSError:
        return None
    match = re.search(r"^\s*cputarget\s*=\s*(\S+)", text,
                      re.IGNORECASE | re.MULTILINE)
    if not match:
        return None
    arch = match.group(1).strip().lower()
    return arch if arch in CHOICE_BY_ARCH else None


def save_arch(arch):
    """Remember the chosen CPU build in tools\\workercount-config.txt.

    cputarget goes on the first line and the existing workers= line stays
    last, because the .bat files read this file with a for/f loop where the
    last line wins. For the same reason a file with no workers= line is left
    alone: the .bats treat the file existing as "the worker benchmark already
    ran", so writing a lone cputarget= would skip the benchmark and hand the
    encoder an empty worker count. Once any encode has run, the line is there
    and the setting sticks.
    """
    path = workercount_config_path()
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as handle:
            lines = handle.read().splitlines()
    except OSError:
        return
    kept = [line for line in lines
            if line.strip() and not re.match(r"\s*cputarget\s*=", line, re.IGNORECASE)]
    if not any(re.match(r"\s*workers\s*=", line, re.IGNORECASE) for line in kept):
        return
    try:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(f"cputarget={arch}\n")
            for line in kept:
                handle.write(line.rstrip() + "\n")
    except OSError:
        pass


def suggested_worker_count():
    """A sane starting point for -w based on this machine's logical cores.

    Each worker runs a full SVT-AV1 encoder, so worker count is really a
    CPU saturation dial. Roughly a quarter of the logical cores keeps a slow
    preset busy without making the machine unusable.
    """
    try:
        cores = os.cpu_count() or 4
    except Exception:
        cores = 4
    return max(1, min(16, cores // 4))


def parse_range(value):
    """Return (low, high) floats for a 'min-max' string, or None if malformed."""
    match = re.match(r"^\s*([0-9]*\.?[0-9]+)\s*-\s*([0-9]*\.?[0-9]+)\s*$", value or "")
    if not match:
        return None
    try:
        low = float(match.group(1))
        high = float(match.group(2))
    except ValueError:
        return None
    if low >= high:
        return None
    return low, high


def ask_target(metric):
    """Ask for the quality target range, validating it against the chosen metric."""
    default = CVVDP_DEFAULT if metric == "cvvdp" else SSIMU2_DEFAULT
    while True:
        answer = input(
            f"Enter a target range (Press Enter for the default of {default}): "
        ).strip()
        if not answer:
            return default

        parsed = parse_range(answer)
        if not parsed:
            print(f"\n{RED}That is not a valid range. Use the form min-max, for example {default}.{RESET}\n")
            continue

        low, high = parsed
        if metric == "cvvdp" and not (8.0 < low <= 10.0 and high <= 10.0):
            print(f"\n{RED}CVVDP targets must be above 8 and no higher than 10.{RESET}")
            print(f"{RED}A value outside that range makes the encoder switch to a different metric.{RESET}\n")
            continue
        if metric == "ssimulacra2" and low <= 10.0:
            print(f"\n{RED}SSIMULACRA2 targets must be above 10.{RESET}")
            print(f"{RED}A value of 10 or lower makes the encoder switch to a different metric.{RESET}\n")
            continue

        return answer


def available_builds(tools_dir):
    """Map every LQTC executable present in tools\\LQTC to (backend, fork, arch)."""
    lqtc_dir = os.path.join(tools_dir, LQTC_DIR_NAME)
    found = set()
    if not os.path.isdir(lqtc_dir):
        return found
    for name in os.listdir(lqtc_dir):
        match = re.match(
            r"^lqtc-(cuda|vulkan)-(essential|hdr)-(x86-64-v3|znver2|avx512)\.exe$", name, re.IGNORECASE
        )
        if match:
            found.add((match.group(1).lower(), match.group(2).lower(), match.group(3).lower()))
    return found


def print_header():
    print("===============================================")
    print("  Large Quality Target Collider Batch Builder")
    print("===============================================\n")
    print("This tool will create a batch script that encodes your videos to a")
    print("visual quality target instead of a fixed CRF. The encoder measures")
    print("each scene and picks the CRF that hits the quality you asked for.\n")
    print("Just answer the questions below and your script will be ready to run.\n")


def print_support_notes():
    print(f"{BLUE}--------------------------------------------------------")
    print("Before you start - what is supported right now")
    print(f"--------------------------------------------------------{RESET}")
    print("  * 1080p SDR & HDR content is supported for now.")
    print("  * Filtering is not currently supported. Denoise, deband, dehalo")
    print("    and downscale in settings.txt are ignored in this mode.")
    print("    Cropping is handled automatically by the encoder itself.")
    print("    To filter anyway, see option 2: Tools.")
    print("  * The 5fish fork is not possible in this mode.")
    print(f"{RED}  * av1 grain synth tables are currently manual. These might be")
    print("    automated in a future update. Grain tools have been copied")
    print(f"    to video-output folder.{RESET}")
    print("")


def run_x264_filter(tools_dir, root_dir):
    """Hand the whole job to tools\\x264-filter.bat.

    Nothing about the pipeline is duplicated here. That .bat is the one place
    the x264 settings live, it can be run on its own, and it already prints its
    own progress and pauses when it finishes.
    """
    bat_path = os.path.join(tools_dir, "x264-filter.bat")

    clear_screen()
    print("===============================================")
    print("  Lossless x264 Intermediary")
    print("===============================================\n")
    print("LQTC does its own decoding and cannot apply a filter chain, so any")
    print("filtering has to happen before LQTC ever sees the video.\n")
    print("This runs every video in video-input through the settings.txt chain")
    print("- denoise, deband, dehalo, downscale and crop - and writes the")
    print("result back into video-input as a lossless x264 file:\n")
    print("    <name>-x264filter.mkv\n")
    print("Build your LQTC .bat afterwards and it will encode those")
    print("already-filtered files.\n")

    if not os.path.isfile(bat_path):
        print(f"{RED}ERROR: x264-filter.bat was not found at:{RESET}")
        print(f"{RED}       {bat_path}{RESET}")
        print(f"{RED}       Your install may be incomplete - try re-downloading the package.{RESET}\n")
        input("Press Enter to go back...")
        return

    print(f"{RED}--------------------------------------------------------")
    print("Disk space - read this before you start")
    print(f"--------------------------------------------------------{RESET}")
    print(f"{RED}Lossless means lossless. One 24 minute 1080p anime episode can{RESET}")
    print(f"{RED}come out around 40 GB, and a film proportionally more, so a full{RESET}")
    print(f"{RED}cour runs into the hundreds of gigabytes. You also need roughly{RESET}")
    print(f"{RED}double an episode's size free while that episode is muxed.{RESET}\n")
    print("These are working files. Delete them once the LQTC encode is done.\n")
    print("Also worth knowing:")
    print("  * The intermediary is VIDEO ONLY. No audio, subtitles, chapters")
    print("    or attachments come across from the source.")
    print(f"  * Each source is moved into {os.path.basename(root_dir)}\\originals once its")
    print("    intermediary has been written, so video-input is left holding")
    print("    only the intermediaries.")
    print("  * Quality, autocrop and tonemap are the SET lines at the top of")
    print("    tools\\x264-filter.bat if you want to change them.\n")

    answer = input("Start now? [1 Yes / 2 No] (Press Enter for No): ").strip()
    if answer != "1":
        return

    clear_screen()
    try:
        # .bat files are not directly executable, so they go through cmd.
        subprocess.run(["cmd", "/c", bat_path], cwd=tools_dir)
    except Exception as e:
        print(f"\n{RED}ERROR: Could not run x264-filter.bat: {e}{RESET}")
        print(f"{RED}       Run it yourself from: {bat_path}{RESET}")
        input("\nPress Enter to go back...")
    # No pause on the normal path: x264-filter.bat ends with its own.


def tools_menu(tools_dir, root_dir):
    while True:
        clear_screen()
        print("===============================================")
        print("  Tools")
        print("===============================================\n")
        print("  1: Create a lossless x264 intermediary")
        print("     LQTC cannot filter. This applies your settings.txt filter")
        print("     chain first, into a lossless file LQTC can then encode.")
        print("")
        print("  2: Back to the main menu")
        print("")
        choice = input("Select [1/2] (Press Enter for 2): ").strip()
        if choice == "1":
            run_x264_filter(tools_dir, root_dir)
        elif not choice or choice == "2":
            return


def main():
    enable_ansi_colors()

    tools_dir = os.path.dirname(os.path.abspath(__file__))
    root_dir = os.path.dirname(tools_dir)

    while True:
        clear_screen()
        print_header()
        print("  1: Build LQTC bat file")
        print("")
        print("  2: Tools")
        print("")
        print_support_notes()
        choice = input("Select [1/2] (Press Enter for 1): ").strip()
        if choice == "2":
            tools_menu(tools_dir, root_dir)
            continue
        if not choice or choice == "1":
            build_batch_file(tools_dir, root_dir)
            return
        # Anything else falls through and redraws the menu.


def build_batch_file(tools_dir, root_dir):
    builds = available_builds(tools_dir)
    clear_screen()

    # --- 1. GPU backend ---
    print("\n--------------------------------------------------------")
    print("STEP 1 OF 6: Choose a GPU Backend")
    print("--------------------------------------------------------")
    print("The quality metric runs on your GPU. Pick the backend that")
    print("matches your graphics card:\n")
    print("  1: cuda    -- NVIDIA only")
    print("               The fastest option if you have an NVIDIA card.\n")
    print("  2: vulkan  -- NVIDIA, AMD or Intel")
    print("               Works on any modern card. Pick this if you are")
    print("               not on NVIDIA, or if cuda gives you trouble.\n")
    backend_choice = input("Select [1/2] (Press Enter for 1): ").strip()
    backend = "vulkan" if backend_choice == "2" else "cuda"

    # --- 2. Fork ---
    print("\n--------------------------------------------------------")
    print("STEP 2 OF 6: Choose an Encoder Preset (Fork)")
    print("--------------------------------------------------------")
    print("Different forks are tuned for different types of video.")
    print("Think of these like pre-configured settings profiles:\n")
    print("  1: svt-av1-essential -- Best for: SDR content")
    print("                          A well-rounded profile that works great")
    print("                          on both animated and real-world footage.\n")
    print("  2: svt-av1-hdr       -- Best for: SDR or HDR content")
    print("                          Specifically designed to retain live action")
    print("                          detail and grain. Handles HDR sources.\n")
    print("  NOTE: The 5fish fork is not possible in this mode.\n")
    print("  NOTE: Tonemapping HDR to SDR is not currently supported.\n")
    fork_choice = input("Select [1/2] (Press Enter for 1): ").strip()
    fork = "hdr" if fork_choice == "2" else "essential"

    # --- CPU build ---
    print("\n--------------------------------------------------------")
    print("CPU Build")
    print("--------------------------------------------------------")
    print("The encoder ships in three builds, each compiled for a different")
    print("set of CPU instructions. Picking one your CPU cannot run will make")
    print("the encode crash, so choose the one that matches your hardware.")
    print("If you are not sure, take option 2: x86-64-v3.\n")
    print("  1: znver2    -- Tuned for AMD Ryzen. Pick this if you have a")
    print("                  Zen2 or newer. If you have Intel, try this first.\n")
    print("  2: x86-64-v3 -- The safe choice. Runs on any Intel or AMD CPU")
    print("                  from roughly 2013-2015 onwards (AVX2).\n")
    print("  3: avx512    -- Only for CPUs that support AVX-512. Do not pick")
    print("                  this unless you are sure yours does.\n")
    saved_arch = read_saved_arch()
    default_choice = CHOICE_BY_ARCH.get(saved_arch, "1")
    if saved_arch:
        print(f"Last time you chose {saved_arch} (option {default_choice}).\n")
    arch_choice = input(f"Select [1/2/3] (Press Enter for {default_choice}): ").strip()
    arch = ARCH_BY_CHOICE.get(arch_choice, ARCH_BY_CHOICE[default_choice])
    save_arch(arch)

    # The executables are used in place from tools\LQTC; nothing is copied.
    exe_name = f"lqtc-{backend}-{fork}-{arch}.exe"
    if builds and (backend, fork, arch) not in builds:
        print(f"\n{RED}WARNING: {exe_name} was not found in tools\\{LQTC_DIR_NAME}.{RESET}")
        if (backend, fork, DEFAULT_ARCH) in builds:
            print(f"{RED}The encode will fall back to lqtc-{backend}-{fork}-{DEFAULT_ARCH}.exe.{RESET}")
        else:
            print(f"{RED}Your install may be incomplete - try re-downloading the package.{RESET}")
        input("\nPress Enter to continue anyway...")

    # --- 3. Metric + target ---
    print("\n--------------------------------------------------------")
    print("STEP 3 OF 6: Choose a Quality Metric")
    print("--------------------------------------------------------")
    print("This is the metric the encoder uses to decide when a scene")
    print("looks good enough. You give it a target range, and it searches")
    print("for the CRF that lands inside that range.\n")
    print("  1: CVVDP        -- Models how a human eye sees your screen.")
    print("                     Scored 0-10. Slower, but the scores line up")
    print("                     well with what people actually notice.\n")
    print("  2: SSIMULACRA2  -- The established image quality metric.")
    print("                     Scored 0-100. Faster, and widely used.\n")
    metric_choice = input("Select [1/2] (Press Enter for 1): ").strip()
    metric = "ssimulacra2" if metric_choice == "2" else "cvvdp"

    if metric == "cvvdp":
        print("\n--------------------------------------------------------")
        print("CVVDP Target Range")
        print("--------------------------------------------------------")
        print("Higher numbers = higher quality + larger file size.\n")
        print("  Recommended starting points:")
        print("    9.83-9.85 -- High fidelity. Around 9.84 is a very fair")
        print("                 target for high quality releases, and a")
        print("                 common choice for full movies.")
        print("    9.64-9.66 -- Good quality. What people often settle for")
        print("                 on 1080p SDR.")
        print("    9.54-9.56 -- DEFAULT. \"Mini\" anime quality. Tested at")
        print("                 length for Sokudo encodes with good results.")
        print("    9.40-9.42 -- For films that are extremely hard to encode,")
        print("                 where the bitrate needs reining in.\n")
        print("If you are unsure, press Enter and adjust from there.\n")
    else:
        print("\n--------------------------------------------------------")
        print("SSIMULACRA2 Target Range")
        print("--------------------------------------------------------")
        print("Higher numbers = higher quality + larger file size.\n")
        print("  Recommended starting points:")
        print("    92-94 -- High fidelity. Anime reaches 90+ easily, so a 90")
        print("             on anime buys you roughly what a 70 buys on live")
        print("             action. 92 is a common casual anime target.")
        print("    88-90 -- DEFAULT. High quality baseline.")
        print("    78-82 -- Relaxed, medium-to-high quality. Most viewers")
        print("             cannot tell scenes apart within this spread.\n")
        print("  WARNING: Sensitivity climbs steeply at the top of the scale.")
        print("  Moving a target from 83 to 85 can double the output bitrate.\n")
        print("If you are unsure, press Enter and adjust from there.\n")

    target = ask_target(metric)

    # --- 4. Fork specific encoder parameters ---
    content = ""
    if fork == "essential":
        print("\n--------------------------------------------------------")
        print("STEP 4 OF 6: Content Type (svt-av1-essential)")
        print("--------------------------------------------------------")
        print("Animation and live action want different things from the")
        print("encoder, so this picks a tuning to match your source:\n")
        print("  1: Anime")
        print("     Tuned for flat colour areas, sharp lines and the subtle")
        print("     textures animation relies on.\n")
        print("  2: Live Action")
        print("     A lighter touch, tuned for real-world footage.\n")
        content_choice = input("Select [1/2] (Press Enter for 1): ").strip()
        if content_choice == "2":
            content = "liveaction"
            fork_params = "--enable-dlf 2"
        else:
            content = "anime"
            fork_params = (
                "--ac-bias 0.75 --enable-dlf 3 --enable-alt-cdef 2 "
                "--qm-min 4 --chroma-qm-min 6"
            )
    else:
        print("\n--------------------------------------------------------")
        print("STEP 4 OF 6: Film Grain / Noise Handling (svt-av1-hdr)")
        print("--------------------------------------------------------")
        print("Real-world video (especially film) contains natural grain/noise.")
        print("This setting tells the encoder how to handle it:\n")
        print("  1: Clean / Low Noise")
        print("     Best for: Modern digital footage, clean CGI, animation.")
        print("     The encoder will smooth out noise rather than preserve it.\n")
        print("  2: Film Grain Mode")
        print("     Best for: Film-sourced content, older movies, grainy footage.")
        print("     Preserves the natural film grain in your source video.\n")
        noise_choice = input("Select mode [1/2] (Press Enter for 1): ").strip()
        if noise_choice == "2":
            content = "grain"
            fork_params = "--tune 5 --film-grain 10"
            print(f"\n{BLUE}Note: grain is expensive to measure. Metrics can read grain as detail,")
            print(f"so expect higher bitrates than the same target on clean footage.{RESET}")
        else:
            content = "clean"
            fork_params = "--tune 0 --noise 4"

    # --- 5. Preset speed ---
    print("\n--------------------------------------------------------")
    print("STEP 5 OF 6: Encoding Speed (Preset)")
    print("--------------------------------------------------------")
    print("This controls how much time and CPU power the encoder spends")
    print("searching for efficient ways to store your video.")
    print("Slower presets = more effort, which usually means better")
    print("quality for the file size (results vary by video).")
    print("Faster presets = quicker encodes, but possibly less efficient")
    print("and less quality.\n")

    print("  Recommended presets for this fork:")
    print("  0 -- Slowest. Maximum effort. Great if you can wait.")
    if fork == "hdr":
        print("  1 -- Improves on preset 2, especially for grainy")
        print("       video. Handles grain better and cleans up some")
        print("       artifacts that other presets can leave behind.")
        print("       With tune grain, it can bring extra quality")
        print("       improvements.")
    else:
        print("  1 -- Possible slight improvement over preset 2,")
        print("       at the cost of extra encode time.")
    print("  2 -- Very high effort. Still slow, but a good choice")
    print("       for encodes you care about.")
    print("  3 -- Not useful at this time. Offers no significant")
    print("       measurable advantage over preset 4.")
    print("  4 -- DEFAULT. Fastest recommended preset. A solid")
    print("       balance of speed and efficiency. Use this if you")
    print("       have a slower CPU or need results sooner.\n")
    print("  WARNING: Presets faster than 4 are not recommended.")
    print("  They skip many of the tools designed to preserve")
    print("  quality while staying efficient, so output quality")
    print("  will suffer.\n")

    default_speed = "4"
    speed = input(f"Enter a preset speed (Press Enter for the recommended default of {default_speed}): ").strip()
    if not speed:
        speed = default_speed

    # --- 6. Worker count ---
    suggested = suggested_worker_count()
    print("\n--------------------------------------------------------")
    print("STEP 6 OF 6: Worker Count")
    print("--------------------------------------------------------")
    print("The video is split into chunks and several chunks are encoded")
    print("at the same time. Each worker is one encoder running in parallel,")
    print("so this is really a dial for how hard your CPU is pushed.\n")
    print("  Too few  -- Your CPU sits idle and the encode takes longer")
    print("              than it needs to.")
    print("  Too many -- Your CPU is oversaturated, the machine becomes")
    print("              unusable, and you can run out of RAM.\n")
    print(f"  This machine reports {os.cpu_count() or 'an unknown number of'} logical cores,")
    print(f"  so {suggested} is a reasonable starting point.\n")
    print("Tip: Task Manager is not accurate for CPU percent used. Use HWiNFO.")
    print("If CPU usage is low, raise this. If the machine is unusable or you")
    print("hit out-of-memory errors, lower it.\n")
    workers = input(f"Enter a worker count (Press Enter for {suggested}): ").strip()
    if not workers.isdigit() or int(workers) < 1:
        workers = str(suggested)

    # --- Build the encoder parameter string ---
    # --crf is deliberately absent: the quality target search owns the CRF.
    # Colour metadata (primaries, transfer, matrix, mastering display, content
    # light) is set by the encoder automatically and must not be passed here.
    lqtc_params = f"--preset {speed} {fork_params}".strip()

    metric_tag = "cvvdp" if metric == "cvvdp" else "ssim2"
    output_filename = (
        f"lqtc-{backend}-{fork}-{content}-{metric_tag}{target}-p{speed}.sh"
    )

    script = "#!/bin/bash\n"
    script += "# This script uses lqtc-dispatch.py to call the LQTC encoder in tools/LQTC.\n"
    script += "# LQTC handles decoding, scene detection, chunking and the CRF search itself.\n"
    script += f'lqtc_params="{lqtc_params}"\n'
    script += "# Do not add --crf here: the quality target search chooses the CRF.\n"
    script += "# Do not add colour settings (--color-primaries, --mastering-display, ...):\n"
    script += "# the encoder sets those from the source automatically and will reject them.\n"
    script += f'BACKEND="{backend}"\n'
    script += "# BACKEND options: cuda (NVIDIA only), vulkan (NVIDIA/AMD/Intel).\n"
    script += f'FORK="{fork}"\n'
    script += "# FORK options: essential (SDR), hdr (SDR or HDR). 5fish is not available here.\n"
    script += f'ARCH="{arch}"\n'
    script += "# ARCH options: x86-64-v3 (any modern CPU), znver2 (AMD Zen 2+),\n"
    script += "# avx512 (only CPUs with AVX-512). A missing build falls back to x86-64-v3.\n"
    script += f'METRIC="{metric}"\n'
    script += f'TARGET="{target}"\n'
    script += "# TARGET is what selects the metric: 8-10 = CVVDP, above 10 = SSIMULACRA2.\n"
    script += f'CRF_RANGE="{DEFAULT_CRF_RANGE}"\n'
    script += "# CRF_RANGE limits which CRF values the quality target search may pick from.\n"
    script += "# Widen it if the encoder reports it could not reach your target.\n"
    script += f'WORKERS="{workers}"\n'
    script += "# WORKERS is how many chunks encode at the same time (CPU saturation).\n"
    script += 'METRIC_WORKERS="1"\n'
    script += "# METRIC_WORKERS is how many metric calculations run at once on the GPU.\n"
    script += 'METRIC_MODE="mean"\n'
    script += "# METRIC_MODE: mean, or pN for a percentile of the worst frames (e.g. p5).\n"
    script += "# Do not add a percent sign - use p5, not p5 percent.\n"
    script += 'DISPLAY_FILE="display.json"\n'
    script += "# DISPLAY_FILE describes your screen and viewing conditions. CVVDP only.\n"
    script += "# It lives in tools/LQTC. Edit it to match your own screen.\n\n"

    script += 'SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"\n'
    script += 'cd "$SCRIPT_DIR" || exit 1\n'
    script += "rm -f tools/bat*.txt\n"
    script += "mv -n *.mkv *.mp4 *.m2ts video-input/ 2>/dev/null\n"
    script += 'touch "tools/bat-used-$(basename \"$0\").txt"\n'
    script += 'export PATH="$SCRIPT_DIR/tools/av1an:$PATH"\n\n'

    script += "# --- DISPATCH ---\n"
    script += 'echo "Starting LQTC Quality Target Dispatcher..."\n'
    script += 'echo "Encoding inputs from: video-input"\necho "Outputs will go to:   video-output"\n\n'
    script += (
        'python3 "$SCRIPT_DIR/tools/lqtc-dispatch.py" --backend "$BACKEND" --fork "$FORK"'
        ' --arch "$ARCH" --target "$TARGET" --crf-range "$CRF_RANGE" --workers "$WORKERS"'
        ' --metric-workers "$METRIC_WORKERS" --metric-mode "$METRIC_MODE"'
        ' --display "$DISPLAY_FILE" --params "$lqtc_params"\n\n'
    )

    script += 'echo ""\necho "All tasks finished."\necho "Press Enter to clean up temp files (Ctrl+C to keep them and exit)."\nread -r _\n\n'

    script += "# --- CLEANUP ---\n"
    script += 'echo "Cleaning up temporary files and folders..."\n'
    script += 'python3 "$SCRIPT_DIR/tools/cleanup.py"\n'

    file_path = os.path.join(root_dir, output_filename)
    with open(file_path, 'w', newline="\n") as f:
        f.write(script)
    try:
        os.chmod(file_path, 0o755)
    except OSError:
        pass

    print("\n-------------------------------------------------------------------------------")
    print("Success! Your batch script has been generated:")
    print(f"File: {output_filename}")
    print("-------------------------------------------------------------------------------")
    print(f"Encoder:        tools/{LQTC_DIR_NAME}/{exe_name}")
    print(f"Quality target: {target}  ({'CVVDP' if metric == 'cvvdp' else 'SSIMULACRA2'})")
    print(f"Encoder params: {lqtc_params}")
    print(f"Workers:        {workers}")
    print("-------------------------------------------------------------------------------")
    print("Drop your video files into the 'video-input' folder, then run")
    print("the .sh file to start encoding. Encoded files will appear in 'video-output'.")
    print("")
    if metric == "cvvdp":
        print("CVVDP scores depend on the screen you are judging against.")
        print(f"tools/{LQTC_DIR_NAME}/display.json describes that screen - edit it if your monitor,")
        print("viewing distance or room lighting differ from the defaults.")
        print("")
    print("If an encode is interrupted, run the same .sh again and it will resume.")
    print("To change settings, let the run finish or delete the leftover '.<hash>' folder")
    print("in video-input first - the encoder reuses the settings it was started with.")
    print("")
    print("Want to tweak the settings manually? Open the .sh file in a text editor.")
    print("-------------------------------------------------------------------------------")
    input("Press Enter to exit...")


if __name__ == "__main__":
    main()
