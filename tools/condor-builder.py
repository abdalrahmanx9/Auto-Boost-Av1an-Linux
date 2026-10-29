import os
import re

CONDOR_EXE_NAME = "condor"
AV1AN_DIR_NAME = "av1an"

# The CPU build menu, and the cputarget= value each option stores in
# tools\workercount-config.txt so the next build can default to it.
ARCH_BY_CHOICE = {"1": "znver2", "2": "x86-64-v3", "3": "avx512"}
CHOICE_BY_ARCH = {arch: choice for choice, arch in ARCH_BY_CHOICE.items()}

# Metrics Condor measures through the vship plugin (com.lumen.vship). These are
# the ones that need a GPU backend, because tools\vs-hip ships a separate NVIDIA,
# AMD and Vulkan build and only one of them may be active at a time. The backend
# is never asked for here: the generated .bat runs tools\ssimu2-workercount.py -
# the same benchmark bat-builder.py uses - which measures every backend this
# machine can actually load and writes the winner to
# tools\workercount-ssimu2.txt for condor-dispatch.py to read.
VSHIP_METRICS = ("ssimulacra2", "butteraugli", "cvvdp")

# Written by tools\ssimu2-workercount.py. Only its tool=/variant= lines matter
# to Condor; the worker counts in it belong to the Auto-Boost metrics pass, and
# Condor's worker count is asked for in STEP 8 instead.
SSIMU2_CONFIG_NAME = "workercount-ssimu2.txt"

# Per metric: (default target, low bound, high bound, higher_is_better).
# The bounds are sanity checks on the scale, not quality advice - a butteraugli
# target of 90 or an ssimulacra2 target of 1 is a typo, not a preference.
METRIC_RANGES = {
    "ssimulacra2": ("88", 0.0, 100.0, True),
    "cvvdp": ("9.55", 0.0, 10.0, True),
    "butteraugli": ("1.0", 0.0, 20.0, False),
}

# The quantizer values Target Quality is allowed to search. Condor's own default
# is 5-55, which spends probes on quantizers nothing at 1080p ever wants.
DEFAULT_MIN_QUANTIZER = "12"
DEFAULT_MAX_QUANTIZER = "45"


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
    ran", so writing a lone cputarget= would skip the benchmark and hand av1an
    an empty worker count. Once any encode has run, the line is there and the
    setting sticks.
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


def ask_number(prompt, default, low, high):
    """Ask for a single number, re-asking until it lands inside [low, high]."""
    while True:
        answer = input(f"{prompt} (Press Enter for {default}): ").strip()
        if not answer:
            return default
        try:
            value = float(answer)
        except ValueError:
            print("\nThat is not a number. Try again.\n")
            continue
        if not (low <= value <= high):
            print(f"\nThat is outside the {low:g} to {high:g} range this metric uses.\n")
            continue
        return answer


def ask_int(prompt, default, low, high):
    while True:
        answer = input(f"{prompt} (Press Enter for {default}): ").strip()
        if not answer:
            return default
        if not re.match(r"^-?[0-9]+$", answer):
            print("\nThat is not a whole number. Try again.\n")
            continue
        if not (low <= int(answer) <= high):
            print(f"\nEnter a value between {low} and {high}.\n")
            continue
        return answer


def print_header():
    print("===============================================")
    print("        Condor Batch Builder")
    print("===============================================\n")
    print("This tool will create a batch script that encodes your videos to a")
    print("visual quality target instead of a fixed CRF. Condor measures each")
    print("scene and picks the crf that hits the quality you asked for.\n")
    print("Just answer the questions below and your script will be ready to run.\n")


def print_support_notes(condor_present):
    print("--------------------------------------------------------")
    print("  * There is no CRF setting. Target Quality chooses a crf")
    print("    per scene, and you set the quality target it aims for.")
    if not condor_present:
        print("")
        print(f"  * WARNING: tools\\{AV1AN_DIR_NAME}\\{CONDOR_EXE_NAME} was not found.")
        print("    Your install may be incomplete - try re-downloading the package.")
    print("")


def build_batch_file(tools_dir, root_dir):
    clear_screen()

    # --- 1. Fork ---
    print("\n--------------------------------------------------------")
    print("STEP 1 OF 9: Choose an Encoder Preset (Fork)")
    print("--------------------------------------------------------")
    print("Different forks are tuned for different types of video.")
    print("Think of these like pre-configured settings profiles:\n")
    print("  1: 5fish       -- Best for: Anime")
    print("                    Tuned for animation's sharp lines and")
    print("                    subtle detailed textures.\n")
    print("  2: essential   -- Best for: Anime or Live Action")
    print("                    A well-rounded profile that works great on")
    print("                    both animated and real-world footage.")
    print("                    User scalable detail retention.\n")
    print("  3: hdr         -- Best for: HDR or SDR Live Action")
    print("                    Specifically designed to retain live action")
    print("                    detail and grain.\n")
    print("  4: custom      -- For advanced users only.")
    print("                    Use your own custom encoder binary.")
    print("                    Place SvtAv1EncApp in:")
    print(f"                    tools\\{AV1AN_DIR_NAME}\\svt-av1 forks\\custom\n")
    fork_choice = input("Select [1-4] (Press Enter for 1): ").strip()
    fork_map = {"1": "5fish", "2": "essential", "3": "hdr", "4": "custom"}
    fork = fork_map.get(fork_choice, "5fish")

    arch_value = "x86-64-v3"
    if fork in ("5fish", "essential"):
        print("\n--------------------------------------------------------")
        print("CPU Build")
        print("--------------------------------------------------------")
        print("The encoder comes in a few versions, each built for a different")
        print("type of processor. They all produce the same video - a matching")
        print("build just encodes it faster.")
        print("")
        print("Picking one your processor cannot run makes the encode stop with")
        print("an error rather than harm anything, so a faster build is worth a")
        print("try. If it will not start, build the bat again and take option 2,")
        print("which works on every modern computer.\n")
        print("  1: znver2    -- Try this one first. It is faster on AMD Ryzen")
        print("                  3000 series and newer, and it runs on Intel")
        print("                  processors too, so Intel users should give it")
        print("                  a go before settling for x86-64-v3.\n")
        print("  2: x86-64-v3 -- Works on any Intel or AMD processor from")
        print("                  roughly 2015 onwards. The safe choice, and")
        print("                  what to use if option 1 or 3 will not run.\n")
        print("  3: AVX-512   -- Fastest, but only on processors that support")
        print("                  AVX-512. That means recent AMD Ryzen (5000")
        print("                  and newer) or certain Intel chips. Only pick")
        print("                  this if you have checked that yours does.\n")
        saved_arch = read_saved_arch()
        default_choice = CHOICE_BY_ARCH.get(saved_arch, "1")
        if saved_arch:
            print(f"Last time you chose {saved_arch} (option {default_choice}).\n")
        arch_choice = input(f"Select [1/2/3] (Press Enter for {default_choice}): ").strip()
        arch_value = ARCH_BY_CHOICE.get(arch_choice, ARCH_BY_CHOICE[default_choice])
        save_arch(arch_value)

    denoise_value = "True" if fork == "5fish" else "False"
    if fork != "5fish":
        print("\nDenoise will be set to False in settings.txt for this generated batch file.")

    # --- 2. Metric ---
    print("\n--------------------------------------------------------")
    print("STEP 2 OF 9: Choose a Quality Metric")
    print("--------------------------------------------------------")
    print("This is the metric Condor uses to decide when a scene looks good")
    print("enough. You give it a target score, and it searches for the")
    print("quantizer that lands on that score.\n")
    print("  1: SSIMULACRA2  -- DEFAULT. The established image quality metric.")
    print("                     Scored 0-100, higher is better. Runs on your")
    print("                     GPU. A good all-round starting point.\n")
    print("  2: CVVDP        -- Models how a human eye sees your screen.")
    print("                     Scored 0-10, higher is better. Slower, but the")
    print("                     scores line up well with what people notice.\n")
    print("  3: butteraugli  -- Perceptual distance. LOWER is better, so the")
    print("                     target works the opposite way round to the")
    print("                     others. Sensitive to fine detail.\n")
    metric_map = {"1": "ssimulacra2", "2": "cvvdp", "3": "butteraugli"}
    metric = metric_map.get(input("Select [1-3] (Press Enter for 1): ").strip(), "ssimulacra2")

    # No GPU backend question: these metrics run on the GPU, and which build
    # (NVIDIA / AMD / Vulkan) is fastest here is measured, not guessed.
    if metric in VSHIP_METRICS:
        print("\nThis metric is measured on your GPU. You are not asked which")
        print("backend to use - the first run of the generated .bat benchmarks")
        print("NVIDIA, AMD, Vulkan and the CPU on this machine and remembers the")
        print(f"winner in tools\\{SSIMU2_CONFIG_NAME}.\n")

    # --- 3. Target ---
    default_target, low, high, higher_is_better = METRIC_RANGES[metric]
    print("\n--------------------------------------------------------")
    print("STEP 3 OF 9: Quality Target")
    print("--------------------------------------------------------")
    if higher_is_better:
        print("Higher numbers = higher quality + larger file size.\n")
    else:
        print("LOWER numbers = higher quality + larger file size.\n")

    if metric == "ssimulacra2":
        print("  Reference points (from this package's LQTC guidance):")
        print("    92 -- High fidelity. Anime reaches 90+ easily, so a 90 on")
        print("          anime buys you roughly what a 70 buys on live action.")
        print("    88 -- DEFAULT. High quality baseline.")
        print("    80 -- Relaxed, medium-to-high quality. Most viewers cannot")
        print("          tell scenes apart around this level.\n")
        print("  WARNING: Sensitivity climbs steeply at the top of the scale.")
        print("  Moving a target from 83 to 85 can double the output bitrate.\n")
    elif metric == "cvvdp":
        print("  Reference points (from this package's LQTC guidance):")
        print("    9.84 -- High fidelity. A very fair target for high quality")
        print("            releases and a common choice for full movies.")
        print("    9.65 -- Good quality. What people often settle for on 1080p SDR.")
        print("    9.55 -- DEFAULT. \"Mini\" anime quality.")
        print("    9.41 -- For films that are extremely hard to encode, where")
        print("            the bitrate needs reining in.\n")
    else:
        print("  Remember this metric is a distance, so smaller is better:")
        print("    0.6 -- Very close to the source, large files.")
        print("    1.0 -- DEFAULT. A sensible starting point.")
        print("    2.0 -- Visibly cheaper, smaller files.\n")

    print("These numbers are starting points, not settled values. Condor's")
    print("scoring differs from other tools in this package, so encode one")
    print("episode, look at it, and adjust from there.\n")
    target = ask_number("Enter a quality target", default_target, low, high)

    # --- 4. Quantizer range ---
    print("\n--------------------------------------------------------")
    print("STEP 4 OF 9: CRF Search Range")
    print("--------------------------------------------------------")
    print("Target Quality can only pick a CRF from inside this range.")
    print("It is a guard rail, not a quality setting: a narrow range keeps the")
    print("search quick and stops one odd scene running away, but if a scene")
    print("cannot reach your target inside it, the range is what gets blamed.\n")
    print(f"  DEFAULT: {DEFAULT_MIN_QUANTIZER}-{DEFAULT_MAX_QUANTIZER}, which suits 1080p.")
    print("  Condor's own default is 5-55, which spends probes on CRF values")
    print("  that nothing at this resolution ever wants.\n")
    print("  Widen it if Condor reports it could not reach your target.\n")
    min_quantizer = ask_int("Enter the lowest CRF", DEFAULT_MIN_QUANTIZER, 0, 63)
    max_quantizer = ask_int("Enter the highest CRF", DEFAULT_MAX_QUANTIZER, int(min_quantizer) + 1, 63)

    # --- 5. Target profile ---
    print("\n--------------------------------------------------------")
    print("STEP 5 OF 9: Target Quality Profile")
    print("--------------------------------------------------------")
    print("To find a CRF, Condor test-encodes part of each scene and")
    print("measures it. This decides how much of the scene it measures and")
    print("how it turns those frame scores into one number.\n")
    print("  1: fast     -- Measures 11 frames from the middle of the scene")
    print("                 and takes the mean. Quickest, roughest.\n")
    print("  2: standard -- DEFAULT. Measures the middle 25% of the scene")
    print("                 using a root-mean-square, which weighs the worse")
    print("                 frames more heavily than a plain average.\n")
    print("  3: slow     -- Measures the whole scene and targets the 10th")
    print("                 percentile, so the target describes the worst")
    print("                 frames rather than the typical ones. Much slower,")
    print("                 and for the same target number it will spend more")
    print("                 bitrate than the other two.\n")
    profile_map = {"1": "fast", "2": "standard", "3": "slow"}
    target_profile = profile_map.get(input("Select [1/2/3] (Press Enter for 2): ").strip(), "standard")

    # --- 6. Fork specific encoder parameters ---
    content = fork
    dist_preset = ""
    if fork == "essential":
        print("\n--------------------------------------------------------")
        print("STEP 6 OF 9: Fidelity / Detail Preservation (essential fork)")
        print("--------------------------------------------------------")
        print("This setting controls how aggressively the encoder preserves")
        print("fine detail vs. smoothing things out to save space.\n")
        print("  0 -- Default. Balanced. Good for most content. Start here.")
        print("  1 -- Slightly more detail preserved.")
        print("  2 -- Noticeably more detail (may increase file size a bit).")
        print("  3 -- High fidelity. Good for very detailed scenes.")
        print("  4 -- Maximum fidelity. Can significantly increase file size.")
        print("       Mimics SVT-AV1-HDR's tune grain for absolute grain")
        print("       retention with no regard to distortion at all.\n")
        print("Tip: Start at 0. If textures or fine lines look soft or blurry,")
        print("try bumping this up by 1 and compare.\n")
        val = input("Select a fidelity level [0-4] (Press Enter for 0): ").strip()
        if val not in ("0", "1", "2", "3", "4"):
            val = "0"
        content = f"essential-d{val}"
        if val != "0":
            dist_preset = f" --distortion-bias-preset {val}"
        fork_params = f"--scd 0 --enable-dlf 3{dist_preset} --lp 3"
    elif fork == "hdr":
        print("\n--------------------------------------------------------")
        print("STEP 6 OF 9: Film Grain / Noise Handling (hdr fork)")
        print("--------------------------------------------------------")
        print("Real-world video (especially film) contains natural grain/noise.")
        print("This setting tells the encoder how to handle it:\n")
        print("  1: Clean / Low Noise")
        print("     Best for: Modern digital footage, clean CGI, animation.")
        print("     The encoder will smooth out noise rather than preserve it.\n")
        print("  2: Film Grain Mode")
        print("     Best for: Film-sourced content, older movies, grainy footage.")
        print("     Preserves the natural film grain in your source video.\n")
        if input("Select mode [1/2] (Press Enter for 1): ").strip() == "2":
            content = "hdr-grain"
            fork_params = "--tune 5 --film-grain 10 --lp 3"
            print("\nNote: grain is expensive to measure. Metrics can read grain as detail,")
            print("so expect higher bitrates than the same target on clean footage.")
        else:
            content = "hdr-clean"
            fork_params = "--tune 0 --noise 4 --lp 3"
    elif fork == "5fish":
        fork_params = "--scd 0 --lineart-psy-bias 3 --texture-psy-bias 3 --hbd-mds 1 --lp 3"
    else:
        print("\n--------------------------------------------------------")
        print("STEP 6 OF 9: Encoder Parameters (custom fork)")
        print("--------------------------------------------------------")
        print("A custom binary gets a minimal parameter set, since this tool")
        print("cannot know what your build supports:\n")
        print("  --lp 3\n")
        print("Open the generated .bat in a text editor to change them.\n")
        input("Press Enter to continue...")
        fork_params = "--lp 3"

    # --- Grain synthesis: photon noise vs. the encoder's own film grain ---
    # Condor builds the photon noise table itself, so --photon-noise belongs on
    # the condor.exe command line and not in --params. tools\av1an\condor.txt is
    # explicit that it must not be combined with the encoder's internal film
    # grain synthesis, so this is an either/or. The hdr fork settles its own
    # grain handling in STEP 6 above and is left out of this question.
    photon_noise = ""
    if fork != "hdr":
        print("\n--------------------------------------------------------")
        print("Grain Synthesis")
        print("--------------------------------------------------------")
        print("AV1 can rebuild grain during playback instead of spending")
        print("bitrate encoding it. There are two ways to do that, and only")
        print("one of them can be used at a time:\n")
        print("  1: Photon noise -- DEFAULT. Condor generates a grain table")
        print("     from a film ISO model and applies it. Fixed at ISO 200,")
        print("     the recommended minimum, which is mostly there to stop")
        print("     gradient banding in skies and dark scenes.\n")
        print("  2: Film grain   -- The encoder synthesises its own grain")
        print("     instead, at strength 6. Better suited to genuinely grainy")
        print("     film sources. No photon noise is used in this mode.\n")
        if input("Select [1/2] (Press Enter for 1, photon noise 200): ").strip() == "2":
            fork_params += " --film-grain 6"
            content += "-fg"
        else:
            photon_noise = "200"

    # --- 7. Preset speed ---
    print("\n--------------------------------------------------------")
    print("STEP 7 OF 9: Encoding Speed (Preset)")
    print("--------------------------------------------------------")
    print("Controls how hard the encoder works to compress your video.")
    print("Slower presets: better quality per MB, longer encode times.")
    print("Faster presets: quicker encodes, larger files or slightly lower")
    print("quality. Actual gains vary by source video.\n")

    print("  Recommended presets for this fork:")
    if fork != "5fish":
        print("  0 -- Slowest. Maximum effort. Great if you can wait.")
    if fork == "hdr":
        print("  1 -- Improves on preset 2, especially for grainy")
        print("       video. Handles grain better and cleans up some")
        print("       artifacts that other presets can leave behind.")
        print("       With tune grain, it can bring extra quality")
        print("       improvements.")
    elif fork != "5fish":
        print("  1 -- Possible slight improvement over preset 2,")
        print("       at the cost of extra encode time.")
    print("  2 -- Very high effort. Still slow, but a good choice")
    print("       for encodes you care about.")
    print("  3 -- Not useful at this time.")
    print("  4 -- DEFAULT. Fastest recommended preset. A solid")
    print("       balance of speed and efficiency. Use this if you")
    print("       have a slower CPU or need results sooner.\n")
    print("  WARNING: Presets faster than 4 are not recommended.")
    print("  They skip many of the tools designed to preserve")
    print("  quality while staying efficient, quality suffers.\n")
    print("  Remember Target Quality test-encodes every scene before the")
    print("  real encode starts, so a slow preset costs you twice here.\n")
    if fork == "5fish":
        print("  Quote from 5fish github repo:")
        print("  \"even if you have time to burn, as of right now, we")
        print("  don't recommend --preset 0. --preset 2 has a smart")
        print("  system to filter candidates, while --preset 0 tests")
        print("  through all the candidates. This smart filtering system")
        print("  often outperforms doing the full tests, and for this")
        print("  reason --preset 2 often gives a better result than")
        print("  --preset 0\"\n")
    speed = ask_int("Enter a preset speed", "4", 0, 13)

    # --- 8. Worker count ---
    suggested = suggested_worker_count()
    print("\n--------------------------------------------------------")
    print("STEP 8 OF 9: Worker Count")
    print("--------------------------------------------------------")
    print("The video is split into scenes and several scenes are encoded")
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
    print("Leave this blank to let Condor benchmark the worker count itself on")
    print("every run, which costs time up front but adapts to the machine.\n")
    workers = input(f"Enter a worker count (Press Enter for {suggested}, or 0 to let Condor decide): ").strip()
    if workers == "0":
        workers = ""
    elif not workers.isdigit() or int(workers) < 1:
        workers = str(suggested)

    # --- 9. Auto crop / HDR / interface ---
    print("\n--------------------------------------------------------")
    print("STEP 9 OF 9: Auto Crop")
    print("--------------------------------------------------------")
    print("Most movies and TV shows have black bars on the top and bottom")
    print("(letterboxing). Auto crop automatically detects and removes them,")
    print("which saves file space and avoids wasting encoding bits on black areas.\n")
    print("  1: Yes -- Automatically detect and crop black bars")
    print("  2: No  -- Keep the video as-is, no cropping\n")
    print("Tip: If auto crop removes too much or too little, you can open")
    print("settings.txt in a text editor and switch to manual crop mode instead.\n")
    use_autocrop = input("Enable auto crop? [1 Yes / 2 No] (Press Enter for No): ").strip() == "1"

    print("\n--------------------------------------------------------")
    print("HDR Handling")
    print("--------------------------------------------------------")
    if fork == "hdr":
        print("How should HDR source content be handled?\n")
        print("  1: Auto detect SDR/HDR content")
        print("     MediaInfo detects each source. SDR sources get standard")
        print("     BT.709/BT.601 color settings. HDR sources automatically get")
        print("     matching SVT-AV1-HDR color settings -- HDR stays HDR.\n")
    else:
        print("This fork is intended for SDR output. If an HDR source is")
        print("dropped into video-input, how should it be handled?\n")
        print("  1: SDR encoding")
        print("     Sources are encoded as-is. SDR sources get standard")
        print("     BT.709/BT.601 color settings when detected.\n")
    print("  2: Tonemap HDR to SDR")
    print("     HDR sources are converted to SDR (BT.709) via libplacebo")
    print("     inside the VapourSynth script. Uses GPU. SDR sources are")
    print("     encoded normally.")
    print("     GPU/iGPU 2016 or newer required. Not compatible with Intel GPU\n")
    tonemap_value = "True" if input("Select [1/2] (Press Enter for 1): ").strip() == "2" else "False"

    print("\n--------------------------------------------------------")
    print("Interface Mode")
    print("--------------------------------------------------------")
    print("How much information do you want to see while encoding?\n")
    print("  1: Default mode -- Condor's own progress interface")
    print("     Recommended for new users.\n")
    print("  2: Verbose mode -- Show me everything")
    print("     Adds --verbose to Condor and logs a great deal more.\n")
    verbose_value = "--verbose" if input("Select [1/2] (Press Enter for 1): ").strip() == "2" else "--no-verbose"

    # --- Build the encoder parameter string ---
    # No --crf: Target Quality owns the quantizer, and a --crf here would be
    # overwritten per scene anyway. Colour settings are not here either; the
    # dispatcher adds them per file from MediaInfo.
    condor_params = fork_params.strip()

    autocrop_flag = " --autocrop" if use_autocrop else ""
    autocrop_suffix = "-autocrop" if use_autocrop else ""
    tonemap_suffix = "-tonemap" if tonemap_value == "True" else ""
    output_filename = (
        f"condor-{content}-{metric}-{target}-p{speed}{autocrop_suffix}{tonemap_suffix}.sh"
    )

    script = "#!/bin/bash\n"
    script += "# This script uses condor-dispatch.py to call condor in tools/av1an.\n"
    script += "# Condor is a fork of Av1an. Scene detection still comes from this package\n"
    script += "# (Progressive-Scene-Detection.py); Condor is run with --skip-scd and the\n"
    script += "# detected scenes are written into its config instead.\n"
    script += f'condor_params="{condor_params}"\n'
    script += "# Do not add --crf here: Target Quality chooses a quantizer per scene.\n"
    script += "# Do not add colour settings (--color-primaries, --mastering-display, ...):\n"
    script += "# the dispatcher adds those per file from MediaInfo.\n"
    script += "# Do not add --photon-noise here either; it goes in PHOTON_NOISE below.\n"
    script += f'PHOTON_NOISE="{photon_noise}"\n'
    script += "# PHOTON_NOISE is an ISO strength passed to condor itself, not to the encoder:\n"
    script += "# Condor generates the grain table and applies it. 200 is the recommended minimum.\n"
    script += "# Leave it empty when condor_params uses the encoder's own --film-grain instead.\n"
    script += "# condor.txt is explicit that the two must not be combined.\n"
    script += f'FINAL_SPEED="{speed}"\n'
    script += f'fork="{fork}"\n'
    script += "# example forks: 5fish, essential, hdr, custom\n"
    script += f'DENOISE="{denoise_value}"\n'
    script += "# DENOISE updates denoise=True/False in settings.txt before dispatch. 5fish defaults to True; all other forks default to False.\n"
    script += f'ARCH="{arch_value}"\n'
    script += "# ARCH picks the CPU build of the encoder: x86-64-v3 (any modern CPU),\n"
    script += "# znver2 (AMD Ryzen 3000+), avx512 (only CPUs with AVX-512).\n"
    script += "# A fork without that build falls back to x86-64-v3. The hdr fork is x86-64-v3 only.\n"
    script += f'tonemap="{tonemap_value}"\n'
    script += "# tonemap=True converts HDR sources to SDR (BT.709) via libplacebo inside the VapourSynth script (uses GPU).\n"
    script += f'METRIC="{metric}"\n'
    script += "# METRIC options: ssimulacra2, cvvdp, butteraugli, butteraugli-3.\n"
    script += f'TARGET="{target}"\n'
    script += "# TARGET is the score Target Quality aims for. For butteraugli, lower is better.\n"
    script += f'MIN_QUANTIZER="{min_quantizer}"\n'
    script += f'MAX_QUANTIZER="{max_quantizer}"\n'
    script += "# MIN/MAX_QUANTIZER limit which quantizers the search may pick from.\n"
    script += "# Widen them if Condor reports it could not reach your target.\n"
    script += f'TARGET_PROFILE="{target_profile}"\n'
    script += "# TARGET_PROFILE options: fast (11 middle frames, mean), standard (middle 25 percent, RMS),\n"
    script += "# slow (whole scene, 10th percentile).\n"
    script += 'GPU=""\n'
    script += "# GPU selects which libvship build in tools/vs-hip is activated for GPU metrics:\n"
    script += "# nvidia (NVIDIA only), amd (AMD only) or vulkan (NVIDIA/AMD/Intel).\n"
    script += "# Leave it EMPTY to let the dispatcher pick the backend the metric benchmark\n"
    script += f"# measured as fastest on this machine (tools/{SSIMU2_CONFIG_NAME}).\n"
    script += "# Fill one in only to override that result.\n"
    script += 'DECODER="bestsource"\n'
    script += "# DECODER options: bestsource, vs-ffms2, lsmash, dgdecnv, ffms2.\n"
    script += 'CONCAT="mkvmerge"\n'
    script += "# CONCAT options: mkvmerge, ffmpeg, ivf.\n"
    script += f'WORKERS="{workers}"\n'
    script += "# WORKERS is how many scenes encode at the same time (CPU saturation).\n"
    script += "# Leave it empty to let Condor benchmark the worker count itself on every run.\n"
    script += f'VERBOSE="{verbose_value}"\n'
    script += "# VERBOSE=--verbose turns on Condor's verbose output and logging.\n\n"

    script += 'SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"\n'
    script += 'cd "$SCRIPT_DIR" || exit 1\n'
    script += "rm -f tools/bat*.txt\n"
    script += "mv -n *.mkv *.mp4 *.m2ts video-input/ 2>/dev/null\n"
    script += 'touch "tools/bat-used-$(basename "$0").txt"\n'
    script += 'export PATH="$SCRIPT_DIR/tools/av1an:$PATH"\n\n'

    # No encode worker count benchmark here: WORKERS was chosen in the builder.
    # The metric backend IS benchmarked, because it depends on the GPU rather
    # than on anything the user can sensibly answer. This is the same script
    # script-builder.py runs; only its tool=/variant= lines are used here.
    step_num = 1
    if metric in VSHIP_METRICS:
        script += f"# --- STEP {step_num}: METRIC BACKEND CHECK (GPU) ---\n"
        script += f'if [ ! -f "tools/{SSIMU2_CONFIG_NAME}" ]; then\n'
        script += '    echo "-------------------------------------------------------------------------------"\n'
        script += '    echo "First Run Detected: Finding the fastest metric backend for this machine..."\n'
        script += '    echo "-------------------------------------------------------------------------------"\n'
        script += '    echo "Benchmarking NVIDIA, AMD, Vulkan and CPU. This runs once."\n'
        script += '    python3 "$SCRIPT_DIR/tools/ssimu2-workercount.py"\n'
        script += "fi\n"
        script += f"# The dispatcher reads tool=/variant= from tools/{SSIMU2_CONFIG_NAME} to pick the\n"
        script += "# libvship build. Delete that file to benchmark the backends again. The worker\n"
        script += "# counts in it are not used here - Condor's worker count is WORKERS above.\n\n"
        step_num += 1

    # An empty GPU means "use the benchmark result". Passing --gpu with no value
    # would make the dispatcher swallow the next flag, so the whole option is
    # left out unless the user filled GPU in.
    script += 'GPU_ARG=""\n'
    script += '[ -n "$GPU" ] && GPU_ARG="--gpu $GPU"\n\n'

    if fork != "5fish":
        script += f"# --- STEP {step_num}: RENAMING ---\n"
        script += 'echo "Starting Renaming Process..."\n'
        script += 'python3 "$SCRIPT_DIR/tools/rename.py"\n\n'
        step_num += 1

    script += f"# --- STEP {step_num}: DISPATCH ---\n"
    script += 'echo "Starting Condor Dispatcher..."\n'
    script += 'echo "Encoding inputs from: video-input"\necho "Outputs will go to:   video-output"\n\n'
    script += (
        'python3 "$SCRIPT_DIR/tools/condor-dispatch.py" "$VERBOSE" --fork "$fork"'
        ' --arch "$ARCH" --denoise "$DENOISE" --tonemap "$tonemap"' + autocrop_flag +
        ' --metric "$METRIC" --target "$TARGET" --min-quantizer "$MIN_QUANTIZER"'
        ' --max-quantizer "$MAX_QUANTIZER" --target-profile "$TARGET_PROFILE" $GPU_ARG'
        ' --decoder "$DECODER" --concat "$CONCAT" --workers "$WORKERS"'
        ' --photon-noise "$PHOTON_NOISE"'
        ' --final-speed "$FINAL_SPEED" --final-params "$condor_params"\n\n'
    )
    step_num += 1

    script += 'echo ""\necho "All tasks finished."\necho "Press Enter to clean up temp files (Ctrl+C to keep them and exit)."\nread -r _\n\n'

    script += f"# --- STEP {step_num}: CLEANUP ---\n"
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
    print("Want to tweak the settings manually? Open the .sh file in a text editor.")
    print("-------------------------------------------------------------------------------")
    input("Press Enter to exit...")


def main():
    tools_dir = os.path.dirname(os.path.abspath(__file__))
    root_dir = os.path.dirname(tools_dir)
    condor_present = os.path.exists(os.path.join(tools_dir, AV1AN_DIR_NAME, CONDOR_EXE_NAME))

    while True:
        clear_screen()
        print_header()
        print("  1: Build Condor script")
        print("")
        print("  2: Exit")
        print("")
        print_support_notes(condor_present)
        choice = input("Select [1/2] (Press Enter for 1): ").strip()
        if choice == "2":
            return
        if not choice or choice == "1":
            build_batch_file(tools_dir, root_dir)
            return
        # Anything else falls through and redraws the menu.


if __name__ == "__main__":
    main()
