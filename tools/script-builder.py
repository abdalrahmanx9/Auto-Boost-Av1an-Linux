import importlib.util
import os
import re
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import source_filter
import vpy_template

BLUE = "\033[94m"
RED = "\033[91m"
RESET = "\033[0m"

# The CPU build menu, and the cputarget= value each option stores in
# tools\workercount-config.txt so the next build can default to it.
ARCH_BY_CHOICE = {"1": "znver2", "2": "x86-64-v3", "3": "avx512"}
CHOICE_BY_ARCH = {arch: choice for choice, arch in ARCH_BY_CHOICE.items()}


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
    last, because the .sh files read this file with a for/f loop where the
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

def setup_condor():
    """Copy tools\\condor-builder.sh into the main folder so it can be run from there.

    Returns True if the tool was set up (caller should stop), False to go back
    to the main menu.
    """
    tools_dir = os.path.dirname(os.path.abspath(__file__))
    root_dir = os.path.dirname(tools_dir)
    source = os.path.join(tools_dir, "condor-builder.sh")
    dest = os.path.join(root_dir, "condor-builder.sh")

    if not os.path.exists(source):
        print(f"\n{RED}Could not find tools\\condor-builder.sh.")
        print(f"Your install may be incomplete - try re-downloading the package.{RESET}")
        input('Press Enter to exit...')
        return True

    if os.path.exists(dest):
        print("\ncondor-builder.sh already exists in the main folder.")
        print("Overwriting will discard any edits you made to it.\n")
        overwrite = input("Replace it with a fresh copy? [1 Yes / 2 No] (Press Enter for No): ").strip()
        if overwrite != "1":
            print("\nLeft the existing condor-builder.sh alone. Nothing was changed.")
            input('Press Enter to exit...')
            return True

    shutil.copy2(source, dest)

    print("\n-------------------------------------------------------------------------------")
    print("Condor is ready.")
    print("File: condor-builder.sh (main folder)")
    print("-------------------------------------------------------------------------------")
    print("Run it from the main folder to build a Condor batch script, which lets you")
    print("set a target quality using CVVDP, SSIM2, butteraugli instead of a")
    print("fixed CRF.")
    print("-------------------------------------------------------------------------------")
    input('Press Enter to exit...')
    return True

def lqtc_acknowledgement():
    """Show the LQTC "I understand" page.

    Returns True if the user chose to continue, False to go back.
    """
    items = [
        ["I will use the Windows command line to see if LQTC",
         "displays an error for a hardware incompatibility."],
        ["I know how to extract and apply my own AV1 grain synth",
         "tables, and I will edit the grain .sh files in",
         "video-output manually."],
        ["I accept that LQTC might not be compatible with my PC."],
        ["I accept and understand that this package has two target",
         "based encoders, and if one doesn't work, I can use the",
         "other."],
        ["I know what x86-64-v3 / znver2 / avx512 means."],
    ]

    while True:
        clear_screen()
        print("================================================")
        print("      Large Quality Target Collider (LQTC)      ")
        print("================================================\n")
        print("Before setting up LQTC, please read and accept the following:\n")
        for item in items:
            print(f"  I understand: {item[0]}")
            for extra in item[1:]:
                print(f"                {extra}")
            print("")
        print("------------------------------------------------")
        print("  1. Continue -- I understand all of the above")
        print("  2. Go back\n")
        choice = input("Select [1/2] (Press Enter to go back): ").strip()

        if choice == "1":
            return True
        if choice == "2" or choice == "":
            return False

def setup_lqtc():
    """Copy tools\\lqtc-builder.sh into the main folder so it can be run from there.

    Also copies the contents of tools\\grav1synth into video-output, since LQTC
    needs it there.

    Returns True if the tool was set up (caller should stop), False to go back
    to the main menu.
    """
    tools_dir = os.path.dirname(os.path.abspath(__file__))
    root_dir = os.path.dirname(tools_dir)
    source = os.path.join(tools_dir, "lqtc-builder.sh")
    dest = os.path.join(root_dir, "lqtc-builder.sh")

    if not os.path.exists(source):
        print(f"\n{RED}Could not find tools\\lqtc-builder.sh.")
        print(f"Your install may be incomplete - try re-downloading the package.{RESET}")
        input('Press Enter to exit...')
        return True

    if os.path.exists(dest):
        print("\nlqtc-builder.sh already exists in the main folder.")
        print("Overwriting will discard any edits you made to it.\n")
        overwrite = input("Replace it with a fresh copy? [1 Yes / 2 No] (Press Enter for No): ").strip()
        if overwrite != "1":
            print("\nLeft the existing lqtc-builder.sh alone. Nothing was changed.")
            input('Press Enter to exit...')
            return True

    shutil.copy2(source, dest)

    grav1synth_src = os.path.join(tools_dir, "grav1synth")
    grav1synth_dest = os.path.join(root_dir, "video-output")
    grav1synth_copied = False
    if os.path.isdir(grav1synth_src):
        os.makedirs(grav1synth_dest, exist_ok=True)
        shutil.copytree(grav1synth_src, grav1synth_dest, dirs_exist_ok=True)
        grav1synth_copied = True
    else:
        print(f"\n{RED}Could not find tools\\grav1synth - skipping the grav1synth copy.")
        print(f"Your install may be incomplete - try re-downloading the package.{RESET}")

    print("\n-------------------------------------------------------------------------------")
    print("Large Quality Target Collider is ready.")
    print("File: lqtc-builder.sh (main folder)")
    if grav1synth_copied:
        print("grav1synth files copied into video-output.")
    print("-------------------------------------------------------------------------------")
    print("Run it from the main folder to build an LQTC batch script, which lets you")
    print("set a target quality using CVVDP or SSIM2 instead of a fixed CRF.")
    print("-------------------------------------------------------------------------------")
    input('Press Enter to exit...')
    return True

def setup_afterzone():
    """Copy tools\\AfterZone.sh into the main folder so it can be run from there.

    Returns True if the tool was set up (caller should stop), False to go back
    to the main menu.
    """
    tools_dir = os.path.dirname(os.path.abspath(__file__))
    root_dir = os.path.dirname(tools_dir)
    source = os.path.join(tools_dir, "AfterZone.sh")
    dest = os.path.join(root_dir, "AfterZone.sh")

    if not os.path.exists(source):
        print(f"\n{RED}Could not find tools\\AfterZone.sh.")
        print(f"Your install may be incomplete - try re-downloading the package.{RESET}")
        input('Press Enter to exit...')
        return True

    if os.path.exists(dest):
        print("\nAfterZone.sh already exists in the main folder.")
        print("Overwriting will discard any edits you made to it.\n")
        overwrite = input("Replace it with a fresh copy? [1 Yes / 2 No] (Press Enter for No): ").strip()
        if overwrite != "1":
            print("\nLeft the existing AfterZone.sh alone. Nothing was changed.")
            input('Press Enter to exit...')
            return True

    shutil.copy2(source, dest)

    print("\n-------------------------------------------------------------------------------")
    print("AfterZone is ready.")
    print("File: AfterZone.sh (main folder)")
    print("-------------------------------------------------------------------------------")
    print("Before running it:")
    print("  1. Finish a normal encode and leave the temp folder in place")
    print("     (do not run cleanup yet - AfterZone needs temp\\<name>\\.<hash>\\chunks.json).")
    print("  2. Keep the original input in video-input\\<name>.mkv")
    print("  3. Put a zones file next to it: video-input\\<name>.txt")
    print("     See zones-example.txt in the main folder for the format.")
    print("     AfterZone can also auto-generate one from the finished file's bitrate.")
    print("")
    print("Open AfterZone.sh in a text editor and copy the av1an_settings, FINAL_SPEED and")
    print("CRF lines from the .sh you originally encoded with, so the MKV tag stays")
    print("truthful. Result: video-output\\<name>-afterzone.mkv (your original output is")
    print("left alone).")
    print("-------------------------------------------------------------------------------")
    input('Press Enter to exit...')
    return True

def current_source_filter_label():
    """What the source filter menu entry shows for the current setting."""
    override = source_filter.read_override()
    if override is None:
        return f"{source_filter.DEFAULT_FILTER} (default, no override file)"
    return f"{override} (set in tools\\{source_filter.OVERRIDE_FILENAME})"


def setup_source_filter():
    """Pick the VapourSynth source filter used by the generated .vpy scripts.

    The choice is stored in tools\\source-filter-override.txt, which Auto-Boost,
    Av1an single pass and Condor read when they build a filtering script. With
    no file there they use ffms2, exactly as they always have, so option 3
    (delete the file) restores the original behaviour.

    Returns False so the caller stays in the advanced tools menu: this only
    writes a setting, it does not build anything.
    """
    while True:
        clear_screen()
        print("================================================")
        print("          VapourSynth Source Filter             ")
        print("================================================\n")
        print("The source filter is the plugin that opens your video inside the")
        print("VapourSynth filtering script (crop, downscale, denoise, deband,")
        print("dehalo, tonemap). It decides how frames are decoded, not how they")
        print("are encoded.\n")
        print(f"Currently: {current_source_filter_label()}\n")
        print("  1: ffms2 (default)")
        print("     Fast to index and light on disk. Seek based, so on a few")
        print("     awkward sources - open-GOP HDR/BT.2020 in particular - it can")
        print("     return the wrong frames or stop short of the real end.\n")
        print("  2: BestSource")
        print("     Decodes linearly and is frame exact, so it is the safer pick")
        print("     when ffms2 gives you a frame count mismatch, a truncated")
        print("     encode or scenes that drift out of sync. Indexing takes")
        print("     noticeably longer, especially on long files.\n")
        print("  3: Remove the override (go back to the built-in default)\n")
        print("  4: Go back without changing anything\n")
        print("This applies to Auto-Boost, Av1an single pass and Condor. LQTC does")
        print("its own decoding without VapourSynth, so it ignores this setting.")
        print("Existing .vpy scripts in a leftover temp folder are rebuilt on the")
        print("next run, so a change takes effect straight away.\n")
        choice = input("Select [1/2/3/4]: ").strip()

        if choice in ("1", "2"):
            picked = source_filter.FFMS2 if choice == "1" else source_filter.BESTSOURCE
            try:
                path = source_filter.write_override(picked)
            except (OSError, ValueError) as e:
                print(f"\n{RED}Could not write the override file: {e}{RESET}")
                input('Press Enter to exit...')
                continue
            print("\n-------------------------------------------------------------------------------")
            print(f"Source filter set to: {picked}")
            print(f"File: tools\\{os.path.basename(path)}")
            print("-------------------------------------------------------------------------------")
            print("Every .sh you have already generated picks this up as well - the")
            print("dispatchers read the file at the start of each run.")
            print("-------------------------------------------------------------------------------")
            input('Press Enter to exit...')
            return False
        if choice == "3":
            if source_filter.clear_override():
                print(f"\nRemoved tools\\{source_filter.OVERRIDE_FILENAME}.")
                print(f"The scripts are back to their built-in default ({source_filter.DEFAULT_FILTER}).")
            else:
                print(f"\nThere was no tools\\{source_filter.OVERRIDE_FILENAME} to remove.")
                print(f"The scripts are already using the built-in default ({source_filter.DEFAULT_FILTER}).")
            input('Press Enter to exit...')
            return False
        if choice == "4" or choice == "":
            return False


def load_av1an_dispatch():
    """Import tools\\av1an-dispatch.py as a module.

    The template writer reuses the dispatcher's settings.txt readers so the file
    it writes is exactly what the dispatcher would have generated - a second copy
    of the dehalo/fine_dehalo validation here would drift from it. The hyphen in
    the filename is why this needs importlib. Loaded on demand, since nothing
    else in the batch builder needs it.

    Returns (module, reason). Exactly one of the two is set: reason is the line
    to show the user when the import did not work. It carries the real exception
    rather than a generic "something is wrong", because everything that goes
    wrong here goes wrong the same way - one import inside the dispatcher fails -
    and the name of the missing piece is the whole of the fix.
    """
    module = sys.modules.get("av1an_dispatch")
    if module is not None:
        return module, None

    tools_dir = os.path.dirname(os.path.abspath(__file__))
    module_path = os.path.join(tools_dir, "av1an-dispatch.py")
    if not os.path.exists(module_path):
        return None, "tools\\av1an-dispatch.py is not there."

    try:
        spec = importlib.util.spec_from_file_location("av1an_dispatch", module_path)
        module = importlib.util.module_from_spec(spec)
        sys.modules["av1an_dispatch"] = module
        spec.loader.exec_module(module)
    except Exception as e:
        sys.modules.pop("av1an_dispatch", None)
        return None, f"{type(e).__name__}: {e}"
    return module, None


def template_status_label():
    """What the template menu entry shows for the current state."""
    path = vpy_template.template_path()
    if os.path.isfile(path):
        if vpy_template.lossless_mode_active():
            return ("video-input\\template.vpy is present and in use, with lossless\n"
                    "            intermediary mode ON")
        return "video-input\\template.vpy is present and in use"
    return "not written (scripts are generated from settings.txt)"


TEMPLATE_PREVIEW_BAT = "template-preview.sh"


def copy_template_preview_bat():
    """Put template-preview.sh in video-input, next to the template.

    Returns (copied, message). The template is the thing that matters, so a
    failure here is reported and otherwise ignored rather than being allowed to
    undo a template that was written successfully.
    """
    tools_dir = os.path.dirname(os.path.abspath(__file__))
    source = os.path.join(tools_dir, TEMPLATE_PREVIEW_BAT)
    dest = os.path.join(os.path.dirname(vpy_template.template_path()), TEMPLATE_PREVIEW_BAT)

    if not os.path.exists(source):
        return False, (f"Could not find tools\\{TEMPLATE_PREVIEW_BAT}, so the preview "
                       f"launcher was not written.")
    try:
        shutil.copy2(source, dest)
    except OSError as e:
        return False, f"Could not write video-input\\{TEMPLATE_PREVIEW_BAT}: {e}"
    return True, f"video-input\\{TEMPLATE_PREVIEW_BAT}"


def delete_template_preview_bat():
    """Remove video-input\\template-preview.sh. True if one was there."""
    try:
        os.remove(os.path.join(os.path.dirname(vpy_template.template_path()),
                               TEMPLATE_PREVIEW_BAT))
        return True
    except OSError:
        return False


def confirm_template_overwrite():
    """Ask before replacing an existing template.vpy. True to go ahead."""
    if not os.path.isfile(vpy_template.template_path()):
        return True
    print("\nvideo-input\\template.vpy already exists.")
    print("Overwriting will discard any edits you made to it.\n")
    if input("Replace it? [1 Yes / 2 No] (Press Enter for No): ").strip() == "1":
        return True
    print("\nLeft the existing template.vpy alone. Nothing was changed.")
    input('Press Enter to exit...')
    return False


def perform_template_write(rescale=None, num_streams=None, lossless=False):
    """Write video-input\\template.vpy from settings.txt. True when it went out.

    The file is the filter chain the dispatchers would have generated, laid out
    for hand editing. While it exists, Auto-Boost, Av1an single pass and Condor
    render it instead of generating a script, so settings.txt is not read at all
    - its crop, downscale, dehalo, denoise and deband keys stop applying.

    rescale is None for the standard template, which has no rescale in it at
    all, or the name of a vpy_template.LIVE_RESCALE_BACKENDS entry, which goes
    in switched on. num_streams and lossless are the follow-up answers from
    rescale_setup_page(); lossless also writes the marker the dispatchers read
    and turns on the automatic intermediary the dispatchers build.
    """
    dispatch, reason = load_av1an_dispatch()
    if dispatch is None:
        print(f"\n{RED}Could not load tools\\av1an-dispatch.py, which the template is")
        print(f"built from.")
        print("")
        print(f"  {reason}")
        print("")
        if "ModuleNotFoundError" in (reason or ""):
            missing = (reason or "").rsplit("'", 2)
            package = missing[1] if len(missing) == 3 else "the missing module"
            print(f"That is a Python package the dispatcher needs and this install")
            print(f"does not have. Install it into the package's own Python with:")
            print(f"  python3 -m pip install {package}")
        else:
            print(f"Your install may be incomplete - try re-downloading the package.")
        print(f"{RESET}")
        input('Press Enter to exit...')
        return False

    root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    settings = dispatch.load_script_settings(os.path.join(root_dir, "settings.txt"))
    if not settings:
        print(f"\n{RED}Could not read settings.txt in the main folder, so there is")
        print(f"nothing to build the template from.{RESET}")
        input('Press Enter to exit...')
        return False

    # The crop line is seeded here and hand-edited from then on: nothing rewrites
    # it per input, because the template is the user's script. crop=auto has no
    # numbers to copy - detection needs a video - so an auto user gets a zeroed
    # line and a note below telling them to fill it in.
    crop_mode = dispatch.get_script_setting(settings, "crop", "auto").strip().lower()
    if crop_mode == "manual":
        crop_values = (
            dispatch.read_crop_int(dispatch.get_script_setting(settings, "top", "0"), "top"),
            dispatch.read_crop_int(dispatch.get_script_setting(settings, "bottom", "0"), "bottom"),
            dispatch.read_crop_int(dispatch.get_script_setting(settings, "left", "0"), "left"),
            dispatch.read_crop_int(dispatch.get_script_setting(settings, "right", "0"), "right"),
        )
    else:
        crop_values = (0, 0, 0, 0)

    try:
        text = vpy_template.build_template_text(
            settings,
            dispatch.read_dehalo_settings(settings),
            dispatch.read_fine_dehalo_settings(settings),
            crop_values,
            source_filter.resolve(),
            rescale=rescale,
            num_streams=num_streams,
            lossless=lossless,
        )
        written = vpy_template.write_template(text)
    except (OSError, ValueError, KeyError) as e:
        print(f"\n{RED}Could not write the template: {e}{RESET}")
        input('Press Enter to exit...')
        return False

    preview_copied, preview_message = copy_template_preview_bat()

    # The marker is the whole of the mode: tools\\lossless_mode.py reads it from
    # inside whichever dispatcher is running and builds the intermediary itself,
    # so there is nothing for the user to launch. Writing it here, next to the
    # template it belongs to, keeps the two from disagreeing - and writing a
    # template without the mode has to clear it, or the dispatchers would go on
    # building intermediaries for a template that no longer asks for one.
    lossless_message = ""
    marker_path = ""
    if lossless:
        try:
            marker_path = vpy_template.write_lossless_marker(rescale, num_streams)
        except OSError as e:
            lossless_message = (f"Could not write video-input\\"
                                f"{vpy_template.LOSSLESS_MARKER_FILENAME}: {e}. Lossless "
                                f"intermediary mode is NOT on.")
        else:
            vpy_template.ensure_lossless_passthrough()
    else:
        vpy_template.delete_lossless_marker()

    print("\n-------------------------------------------------------------------------------")
    print("Template written.")
    print(f"File: video-input\\{os.path.basename(written)}")
    if preview_copied:
        print(f"File: {preview_message}")
    if marker_path:
        print(f"File: video-input\\{vpy_template.LOSSLESS_MARKER_FILENAME}")
    print("-------------------------------------------------------------------------------")
    print("Every .sh you have already generated picks this up - the dispatchers")
    print("look for the file at the start of each input. While it is there,")
    print("settings.txt is ignored completely: this file is the filter chain now.")
    print("")
    print("Edit the file freely. Delete it to go back to scripts generated from")
    print("settings.txt.")
    print("")
    if crop_mode == "manual":
        print(f"Crop: the std.Crop line was filled in from settings.txt (top={crop_values[0]},")
        print(f"bottom={crop_values[1]}, left={crop_values[2]}, right={crop_values[3]}). Change the crop in the template")
        print("from now on - settings.txt [crop] and the auto crop question in the .sh")
        print("builder no longer do anything while the file is there.")
    elif crop_mode == "auto":
        print(f"{RED}Crop: settings.txt has crop=auto, but a template cannot detect a crop -")
        print("it is one script for every video, and auto crop needs to look at each")
        print("one. The std.Crop line went out as all zeros, which crops nothing. Put")
        print(f"your own numbers in it and check them with {TEMPLATE_PREVIEW_BAT}.{RESET}")
    else:
        print("Crop: the std.Crop line went out as all zeros, which crops nothing. Put")
        print("numbers in it to crop - settings.txt [crop] is no longer read while the")
        print("file is there.")
    print("")
    if rescale:
        label = vpy_template.LIVE_RESCALE_BACKENDS[rescale][0]
        print(f"Rescale: the {label} block is switched ON in")
        print("the file. Open it and set new_height to the resolution the show was")
        print("drawn at, and the descale kernel to the one it was scaled with - what")
        print("is in there now is a starting point, not an answer.")
    else:
        print("Rescale: this template was written without one. Come back to this page")
        print("and pick option 2 or 3 if you want a rescale in it.")
    if num_streams:
        print("")
        print(f"Streams: the block sets num_streams = {num_streams}. That is how many frames the")
        print("GPU works on at once. Lower it to 1 if the rescale crashes or the card")
        print("runs out of memory.")
    if lossless:
        print("")
        if marker_path:
            print("Lossless intermediary: ON. The rescale runs once per video instead of")
            print("once per pass, and your encoding .sh does it for you - there is nothing")
            print("extra to run.")
            print("")
            print("  1. Put your videos in video-input and start your encoding .sh as usual.")
            print("  2. Before it encodes a video, the dispatcher filters it through this")
            print(f"     template into temp\\lossless\\<name>{vpy_template.LOSSLESS_SUFFIX}.mkv - around 40 GB for")
            print("     a 24 minute episode - and then encodes from that file.")
            print("  3. Only one of those exists at a time. The previous video's is deleted")
            print("     before the next is built, and the last one goes when the .sh clears")
            print("     temp at the end of the run.")
            print("")
            print(f"Delete video-input\\{vpy_template.LOSSLESS_MARKER_FILENAME} to leave the mode.")
            print("Everything then goes back to filtering through the template on every pass.")
        else:
            print(f"{RED}{lossless_message}{RESET}")
    elif lossless_message:
        print("")
        print(f"{RED}{lossless_message}{RESET}")
    if preview_copied:
        print("")
        print(f"Run video-input\\{TEMPLATE_PREVIEW_BAT} to see what your template does to")
        print("a video in video-input before you spend hours encoding with it. It opens")
        print("vspreview with the untouched source on 1 and the template applied on 2.")
    else:
        print("")
        print(f"{RED}{preview_message}{RESET}")
    print("-------------------------------------------------------------------------------")
    input('Press Enter to exit...')
    return True


def rescale_setup_page(rescale):
    """The page a rescale choice on the template menu leads to.

    Returns (lossless, num_streams, workers), or None to go back to the menu
    without writing anything.

    Only one of num_streams / workers is ever filled in. They are the two ways
    of giving a rescale enough work to do, and which one applies falls out of
    the lossless answer:

      Lossless yes -> one av1an worker reading one file that was filtered once,
                      so the rescale's own num_streams is the throughput knob.
      Lossless no  -> every av1an worker filters its own chunk and runs its own
                      copy of the rescale, so the worker count is the knob and
                      num_streams stays at the default of 1.
    """
    label = vpy_template.LIVE_RESCALE_BACKENDS[rescale][0]

    while True:
        clear_screen()
        print("================================================")
        print("          Lossless Intermediary File            ")
        print("================================================\n")
        print(f"Rescale: {label}\n")
        print("Rescale filtering can be very slow when the video is filtered three")
        print("times: a fast pass, then visual metrics, then final encode. Or in")
        print("target quality encoding, an undetermined amount of times until the")
        print("target quality is found.\n")
        print("The way around it is to filter the video ONCE into a lossless file and")
        print("encode from that instead.\n")
        print(f"{RED}These files are big: roughly 40 GB for a 24 minute 1080p anime episode,")
        print("and more for a film. Only one lossless file is kept at a time, after a")
        print("successful encode, the lossless file is removed before the next one is")
        print(f"made.{RESET}\n")
        print("  1: Yes -- filter once into a lossless .mkv, then encode from that\n")
        print("  2: No  -- filter on every pass\n")
        print("  3: Go back without writing the template\n")
        choice = input("Select [1/2/3]: ").strip()

        if choice == "1":
            num_streams = ask_num_streams(rescale)
            if num_streams is None:
                continue
            return True, num_streams, None
        if choice == "2":
            workers = ask_rescale_workers(rescale)
            if workers is None:
                continue
            return False, None, workers
        if choice == "3":
            return None


def ask_number(prompt, warn_above=None, warn_text=()):
    """Read a whole number of at least 1. None when the answer is unusable.

    Anything above warn_above is read back to the user with warn_text before it
    is accepted, so a number that is known to crash some machines is never taken
    silently.
    """
    value = input(prompt).strip()
    try:
        number = int(value)
    except ValueError:
        print(f"\n{RED}\"{value}\" is not a number.{RESET}")
        input('Press Enter to exit...')
        return None
    if number < 1:
        print(f"\n{RED}It has to be at least 1.{RESET}")
        input('Press Enter to exit...')
        return None
    if warn_above and number > warn_above:
        print("")
        for line in warn_text:
            print(f"{RED}{line}{RESET}")
        print("")
        if input("Use it anyway? [1 Yes / 2 No] (Press Enter for No): ").strip() != "1":
            return None
    return number


def ask_num_streams(rescale):
    """num_streams for the rescale block. None to go back a page."""
    label = vpy_template.LIVE_RESCALE_BACKENDS[rescale][0]

    while True:
        clear_screen()
        print("================================================")
        print("               Inference Streams                ")
        print("================================================\n")
        print(f"Rescale: {label}\n")
        print("num_streams is how many frames the GPU is asked to work on at once.")
        print("With a lossless intermediary the encode runs one worker over one file")
        print("that was already filtered, so this is the setting that keeps the GPU")
        print("busy while the intermediary is being made.\n")
        print(f"{RED}Numbers above 3 are not always faster. Past the point where the GPU is")
        print("already busy, more streams buy nothing and only cost video memory.")
        if rescale == "NVIDIA":
            print("In testing, numbers above 3 sometimes crash depending on which Nvidia")
            print(f"GPU is in the machine.{RESET}\n")
        else:
            print("In testing, numbers above 3 sometimes crash depending on which GPU is")
            print("in the machine. DirectML has not been tested as widely as TensorRT-RTX")
            print(f"here, so treat 3 as the ceiling on it too.{RESET}\n")
        print("Each stream keeps its own copy of the model and its own workspace on")
        print("the GPU, so what fits is decided by video memory more than anything")
        print("else. On an 8 GB card even 2 can be too many. If the encode dies with")
        print("no error at all, this is the first thing to lower.\n")
        print("  1: Use it if anything higher crashes.\n")
        print("  2: 2 frames\n")
        print("  3: the recommended starting point.\n")
        print("  4: Enter a different number\n")
        print("  5: Go back\n")
        choice = input("Select [1/2/3/4/5] (Press Enter for 3): ").strip()

        if choice == "":
            return 3
        if choice in ("1", "2", "3"):
            return int(choice)
        if choice == "4":
            card = "Nvidia GPUs" if rescale == "NVIDIA" else "GPUs"
            number = ask_number(
                "How many streams? ",
                warn_above=3,
                warn_text=("That is above the 3 that has been tested. It may be no faster,",
                           f"and on some {card} it crashes the encode outright."))
            if number is None:
                continue
            return number
        if choice == "5":
            return None


def ask_rescale_workers(rescale):
    """Worker count for a rescale that filters on every pass. None to go back."""
    label = vpy_template.LIVE_RESCALE_BACKENDS[rescale][0]

    while True:
        clear_screen()
        print("================================================")
        print("                 Worker Count                   ")
        print("================================================\n")
        print(f"Rescale: {label}\n")
        print("Without a lossless intermediary, every av1an worker filters its own")
        print("chunk - so every worker runs its own copy of the rescale on the GPU.")
        print("The worker count is what decides how many of those there are.\n")
        engine = "an RTX TensorRT engine" if rescale == "NVIDIA" else "a GPU rescale"
        print(f"{RED}With {engine}, numbers above 3 could crash depending on your")
        print("GPU. Each worker holds its own copy of the model and its own share of")
        print(f"the video memory, and they all run at the same time.{RESET}\n")
        print("  1: 1 worker  -- slowest, and the one that always works.\n")
        print("  2: 2 workers\n")
        print("  3: 3 workers -- the highest that has been tested.\n")
        print("  4: Custom worker count\n")
        print("  5: Go back\n")
        choice = input("Select [1/2/3/4/5]: ").strip()

        if choice in ("1", "2", "3"):
            return int(choice)
        if choice == "4":
            number = ask_number(
                "How many workers? ",
                warn_above=3,
                warn_text=(f"That is above the 3 that has been tested with {engine}.",
                           "Each worker keeps its own copy of the model in video memory,",
                           "so this is where a rescale runs the GPU out and takes the",
                           "encode down with it."))
            if number is None:
                continue
            return number
        if choice == "5":
            return None


def worker_handoff_page(workers):
    """Offer the regular builder with the worker count pinned, or an exit.

    Returns ("workflow", workers), which unwinds to main()'s questions with that
    number remembered. Picking the exit never returns.
    """
    while True:
        clear_screen()
        print("================================================")
        print("               Worker Count Set                 ")
        print("================================================\n")
        print(f"Worker count chosen: {workers}\n")
        print("Nothing carries that number yet. A worker count is not something the")
        print("template holds - the .sh that runs the encode is what carries it.\n")
        print(f"  1: Build a .sh now, with the worker count pinned to {workers}\n")
        print("     Takes you to the regular script-builder questions. The .sh it")
        print("     writes at the end skips the worker benchmark's answer and uses")
        print(f"     {workers} instead.\n")
        print("  2: Exit script-builder now\n")
        print(f"{BLUE}Using condor-builder instead? It asks for the worker count as one of its")
        print(f"own questions - answer {workers} there by hand. This page cannot set it for")
        print(f"you.{RESET}\n")
        choice = input("Select [1/2] (Press Enter for 1): ").strip()

        if choice in ("1", ""):
            return ("workflow", workers)
        if choice == "2":
            sys.exit(0)


def write_vpy_template():
    """The template.vpy page: write it, with or without a rescale, or delete it.

    Option 1 writes the filter chain settings.txt describes and nothing else.
    Options 2 and 3 write that same chain with one rescale block in it, switched
    on, and go through rescale_setup_page() first - a rescale is slow enough
    that how the encode feeds it is a decision of its own.

    Returns False so the caller stays in the advanced tools menu, or
    ("workflow", workers) to fall through to the regular builder questions with
    that worker count pinned.
    """
    rescale_by_choice = {"1": None, "2": "DirectML", "3": "NVIDIA"}

    while True:
        clear_screen()
        print("================================================")
        print("        VapourSynth Filtering Template          ")
        print("================================================\n")
        print("Only one thing is filled in for each file encoded: replace.mkv in")
        print("the source line becomes the file being encoded. Everything else runs")
        print("exactly as written, the std.Crop line included - the crop in the")
        print("template is the only crop, and settings.txt [crop] and auto crop stop")
        print("applying. Do not change the frame count (no trimming), or else scene")
        print("detection will break.\n")
        print(f"Currently: {template_status_label()}\n")
        print("  1: Standard template.vpy\n")
        print("  2: DirectML rescale (Nvidia, AMD, Intel)\n")
        print("  3: Nvidia TensorRT-RTX rescale (RTX 20-series and newer)\n")
        print("  4: Delete template.vpy (go back to settings.txt auto scripts)\n")
        print("  5: Go back without changing anything\n")
        print("  6: Exit\n")
        print("Options 2 and 3 ask a few more questions - a rescale runs on the GPU and")
        print("is slow enough that how the encode feeds it is worth deciding up front.\n")
        print("a text editor is suggested for editing the file afterwards.")
        print("LQTC decodes without VapourSynth, so it ignores the template.\n")
        choice = input("Select [1/2/3/4/5/6]: ").strip()

        if choice in ("1", "2", "3"):
            if not confirm_template_overwrite():
                continue

            rescale = rescale_by_choice[choice]
            lossless, num_streams, workers = False, None, None
            if rescale:
                answers = rescale_setup_page(rescale)
                if answers is None:
                    continue
                lossless, num_streams, workers = answers

            if not perform_template_write(rescale, num_streams=num_streams,
                                          lossless=lossless):
                return False
            if workers:
                return worker_handoff_page(workers)
            return False
        if choice == "4":
            removed_template = vpy_template.delete_template()
            # The launchers go with it: on their own they would only ever report
            # a missing template. The lossless marker goes too - without a
            # template there is nothing for a -lossless file to skip, and a
            # marker left behind would keep the encoders skipping a template
            # that is not there.
            removed_preview = delete_template_preview_bat()
            removed_marker = vpy_template.delete_lossless_marker()
            if removed_template:
                print("\nRemoved video-input\\template.vpy.")
                print("The encoders are back to generating their script from settings.txt.")
            else:
                print("\nThere was no video-input\\template.vpy to remove.")
                print("The encoders already generate their script from settings.txt.")
            if removed_preview:
                print(f"Removed video-input\\{TEMPLATE_PREVIEW_BAT} as well.")
            if removed_marker:
                print("Lossless intermediary mode is off again - the marker went with")
                print("the template, so the dispatchers stop building intermediaries.")
            input('Press Enter to exit...')
            return False
        if choice == "5" or choice == "":
            return False
        if choice == "6":
            sys.exit(0)


def advanced_tools_menu():
    """Advanced tools submenu. Returns True if handled, False to go back."""
    while True:
        clear_screen()
        print("================================================")
        print("               Advanced Tools                   ")
        print("================================================\n")
        print("  1. Setup Condor in the main folder\n")
        print("  2. Setup Large Quality Target Collider in the main folder\n")
        print("  3. Setup AfterZone in the main folder\n")
        print(f"  4. VapourSynth source filter -- currently {current_source_filter_label()}\n")
        print("  5. Write template.vpy based off settings.txt to video-input folder\n")
        print("  6. Go back\n")
        print("  7. Exit\n")
        print("  Condor allows you to set a target quality using CVVDP, SSIM2")
        print("  or Butteraugli.\n")
        print("  LQTC allows you to set a target quality using CVVDP or SSIM2")
        print("  There is a potential that this Windows build will not work")
        print("  on every system\n")
        print("  AfterZone allows you to reencode frame ranges with different")
        print("  settings after encoding is already completed.\n")
        print("  Source filter picks the plugin that opens your video inside the")
        print("  VapourSynth filtering script: ffms2 (fast) or BestSource (frame")
        print("  exact, better on awkward HDR sources).\n")
        choice = input("Select [1/2/3/4/5/6/7]: ").strip()

        if choice == "1":
            return setup_condor()
        if choice == "2":
            if lqtc_acknowledgement():
                return setup_lqtc()
            continue
        if choice == "3":
            return setup_afterzone()
        if choice == "4":
            setup_source_filter()
            continue
        if choice == "5":
            result = write_vpy_template()
            # A tuple is the rescale page asking for the regular questions with
            # a worker count already decided; everything else stays on this menu.
            if isinstance(result, tuple):
                return result
            continue
        if choice == "6":
            return False
        if choice == "7":
            sys.exit(0)

# --- The generated script, from one settings dict ----------------------------


def output_filename_for(cfg):
    """The .sh name a settings dict produces."""
    fork = cfg["fork"]
    dist_suffix = f"-d{cfg.get('fidelity', '0')}" if fork == "essential" else ""
    autocrop_suffix = "-autocrop" if cfg.get("autocrop") else ""
    tonemap_suffix = "-tonemap" if cfg.get("tonemap") == "True" else ""
    worker_suffix = f"-w{cfg['forced_workers']}" if cfg.get("forced_workers") else ""
    return (f"scriptbuilder-{cfg['mode']}-{fork}{dist_suffix}-crf{cfg['crf']}"
            f"-p{cfg['speed']}{autocrop_suffix}{tonemap_suffix}{worker_suffix}.sh")


def build_script(cfg):
    """Write the .sh described by cfg into the main folder.

    Returns (output_filename, file_path).
    """
    mode = cfg["mode"]
    fork = cfg["fork"]
    arch_value = cfg["arch"]
    crf = cfg["crf"]
    speed = cfg["speed"]
    tonemap_value = cfg["tonemap"]
    use_autocrop = bool(cfg.get("autocrop"))
    optimize_workers = bool(cfg.get("optimize_workers"))
    verbose_value = cfg.get("verbose", "--no-verbose")
    denoise_value = cfg.get("denoise") or ("True" if fork == "5fish" else "False")

    dist_preset = ""
    if fork == "essential":
        fidelity = str(cfg.get("fidelity", "0"))
        if fidelity != "0":
            dist_preset = f" --distortion-bias-preset {fidelity}"

    hdr_noise = ""
    if fork == "hdr":
        hdr_noise = (" --tune 5 --film-grain 10" if cfg.get("grain") == "film"
                     else " --tune 0 --noise 4")

    autocrop_flag = " --autocrop" if use_autocrop else ""

    fast_params = ""
    final_params = ""
    has_rename = True
    film_grain_note = ""

    if fork == "5fish":
        fast_params = "--scd 0 --lineart-psy-bias 3 --texture-psy-bias 3 --hbd-mds 0"
        final_params = "--scd 0 --lineart-psy-bias 3 --texture-psy-bias 3 --hbd-mds 1 --lp 3 --photon-noise 200"
        has_rename = False
    elif fork == "essential":
        fast_params = f"--scd 0 --enable-dlf 3{dist_preset}"
        final_params = f"--scd 0 --enable-dlf 3{dist_preset} --lp 3 --photon-noise 200"
        film_grain_note = "# If you'd like to use --film-grain, then --photon-noise must be set to 0, do not remove the setting.\n"
    elif fork == "hdr":
        fast_params = "--tune 0" if "tune 0" in hdr_noise else "--tune 5"
        final_params = f"{hdr_noise.strip()} --lp 3"
    elif fork == "custom":
        fast_params = ""
        final_params = "--lp 3 --photon-noise 200"
    output_filename = output_filename_for(cfg)

    script = "#!/bin/bash\n"

    if mode == "autoboost":
        script += "# Never add noise/grain to fast params, this will break metrics.\n"
        script += f'FAST_PARAMS="{fast_params}"\n'
        script += f'FINAL_PARAMS="{final_params}"\n'
    else:
        script += "# This script uses av1an-dispatch.py to call av1an directly.\n"
        script += f'av1an_settings="{final_params}"\n'

    script += f'FINAL_SPEED="{speed}"\n'
    script += f'CRF="{crf}"\n'
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
    script += "# tonemap=False: the hdr fork auto-detects HDR sources and applies matching SVT-AV1-HDR color settings; other forks encode as-is.\n"
    script += f'VERBOSE="{verbose_value}"\n'
    script += "# VERBOSE=--verbose shows the full output of every tool during the workflow (verbose mode).\n"
    script += "# VERBOSE=--no-verbose keeps the simple interface with progress bars (default mode).\n\n"

    if optimize_workers:
        script += 'optimize_workers="true"\n'
        script += 'custom_av1an_workers=""\n'
        script += 'custom_ssim2_workers=""\n'
        script += 'custom_ssim2_tool=""\n'
        script += "# The one-time optimized benchmark fills in the custom worker/stream values and SSIMU2 tool above.\n"
        script += "# custom_ssim2_tool values: vs-hip nvidia, vs-hip amd, vs-hip vulkan, ffvship nvidia,\n"
        script += "#                            ffvship amd, ffvship vulkan, vs-zip.\n"
        script += "# To re-run a benchmark, clear the value between the quotes.\n"
        script += "# To disable optimized workers, set optimize_workers=\"false\".\n\n"

    script += 'SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"\n'
    script += 'cd "$SCRIPT_DIR" || exit 1\n'
    script += "rm -f tools/bat*.txt\n"
    script += "mv -n *.mkv *.mp4 *.m2ts video-input/ 2>/dev/null\n"
    script += 'touch "tools/bat-used-$(basename "$0").txt"\n'
    script += 'export PATH="$SCRIPT_DIR/tools/av1an:$PATH"\n\n'

    # Optimized One-Time Benchmark (encode workers)
    if optimize_workers:
        script += "# --- ONE-TIME OPTIMIZED WORKER BENCHMARK (ENCODE) ---\n"
        script += 'python3 "$SCRIPT_DIR/tools/workercount.py" --optimize-script "$0"\n'
        script += '[ -n "$custom_av1an_workers" ] && WORKER_COUNT="$custom_av1an_workers"\n\n'

    # Worker Check Encoding
    script += "# --- WORKER COUNT CHECK (ENCODE) ---\n"
    script += "# Only the workers= line is read; other keys (cputarget=) are ignored.\n"
    script += 'read_worker_count() {\n'
    script += '    grep -E "^workers=" tools/workercount-config.txt 2>/dev/null | tail -n 1 | cut -d= -f2\n'
    script += '}\n'
    script += 'WORKER_COUNT_CFG="$(read_worker_count)"\n'
    script += 'if [ -z "$WORKER_COUNT_CFG" ]; then\n'
    script += '    echo "-------------------------------------------------------------------------------"\n'
    script += '    echo "First Run Detected: Calculating optimal encode worker count..."\n'
    script += '    echo "-------------------------------------------------------------------------------"\n'
    script += '    python3 "$SCRIPT_DIR/tools/workercount.py"\n'
    script += '    WORKER_COUNT_CFG="$(read_worker_count)"\n'
    script += '    echo "Encode worker count calculated."\n'
    script += 'fi\n'
    script += 'WORKER_COUNT="${WORKER_COUNT_CFG:-4}"\n\n'

    # Optimized One-Time Benchmark (SSIMU2 vs-zip workers, Autoboost Only)
    if mode == "autoboost" and optimize_workers:
        script += "# --- ONE-TIME OPTIMIZED SSIMU2 BENCHMARK ---\n"
        script += 'python3 "$SCRIPT_DIR/tools/ssimu2-workercount.py" --optimize-script "$0"\n'
        script += '[ -n "$custom_ssim2_tool" ] && SSIMU2_TOOL="$custom_ssim2_tool"\n'
        script += '[ -n "$custom_ssim2_workers" ] && SSIMU2_WORKERS="$custom_ssim2_workers"\n\n'

    # Worker Check SSIMU2 (Autoboost Only)
    if mode == "autoboost":
        script += "# --- WORKER COUNT CHECK (SSIMU2) ---\n"
        script += 'read_ssimu2_key() {\n'
        script += '    grep -E "^'"$1"'=" tools/workercount-ssimu2.txt 2>/dev/null | tail -n 1 | cut -d= -f2\n'
        script += '}\n'
        script += 'if [ -f tools/workercount-ssimu2.txt ]; then\n'
        script += '    SSIMU2_TOOL="$(read_ssimu2_key tool)"\n'
        script += '    SSIMU2_WORKERS="$(read_ssimu2_key workercount)"\n'
        script += 'else\n'
        script += '    echo "-------------------------------------------------------------------------------"\n'
        script += '    echo "First Run Detected: Calculating optimal SSIMU2 settings..."\n'
        script += '    echo "-------------------------------------------------------------------------------"\n'
        script += '    python3 "$SCRIPT_DIR/tools/ssimu2-workercount.py"\n'
        script += '    SSIMU2_TOOL="$(read_ssimu2_key tool)"\n'
        script += '    SSIMU2_WORKERS="$(read_ssimu2_key workercount)"\n'
        script += 'fi\n'
        script += 'SSIMU2_TOOL="${SSIMU2_TOOL:-vs-hip}"\n'
        script += 'SSIMU2_WORKERS="${SSIMU2_WORKERS:-1}"\n\n'

    if optimize_workers:
        script += '[ -n "$custom_av1an_workers" ] && WORKER_COUNT="$custom_av1an_workers"\n'

    if mode == "autoboost":
        if optimize_workers:
            script += '[ -n "$custom_ssim2_tool" ] && SSIMU2_TOOL="$custom_ssim2_tool"\n'
            script += '[ -n "$custom_ssim2_workers" ] && SSIMU2_WORKERS="$custom_ssim2_workers"\n'
        script += 'SSIMU2_TOOL="${SSIMU2_TOOL:-vs-hip}"\n'
        script += 'SSIMU2_WORKERS="${SSIMU2_WORKERS:-1}"\n\n'

    forced_workers = cfg.get("forced_workers")
    if forced_workers:
        script += "# --- WORKER COUNT OVERRIDE (GPU rescale) ---\n"
        script += "# video-input/template.vpy runs an ArtCNN rescale on the GPU, and every\n"
        script += "# av1an worker runs its own copy of it with its own share of the video\n"
        script += "# memory. The benchmark above only measures the CPU, so it does not know\n"
        script += "# about that - this is the number you picked in the builder, and it wins.\n"
        script += "# Edit the line to change it. Above 3 can run the GPU out of memory and\n"
        script += "# take the encode down with it. Delete the line to go back to the\n"
        script += "# benchmark's answer.\n"
        script += f'WORKER_COUNT="{forced_workers}"\n\n'

    # Renaming
    if has_rename:
        script += "# --- RENAMING ---\n"
        script += 'python3 "$SCRIPT_DIR/tools/rename.py"\n\n'

    # Dispatch
    if mode == "autoboost":
        script += "# --- HANDOFF TO DISPATCH ---\n"
        script += 'echo "Starting Auto-Boost-Av1an Dispatcher..."\n'
    else:
        script += "# --- DISPATCH ---\n"
        script += 'echo "Starting Av1an Direct Dispatcher..."\n'

    script += 'echo "Encoding inputs from: video-input"\necho "Outputs will go to:   video-output"\n\n'

    if film_grain_note:
        script += film_grain_note + "\n"

    if mode == "autoboost":
        script += f'python3 "$SCRIPT_DIR/tools/dispatch.py" --fork "$fork" --arch "$ARCH" --denoise "$DENOISE" --tonemap "$tonemap" --crf "$CRF"{autocrop_flag} --ssimu2 "$SSIMU2_TOOL" "$VERBOSE" --ssimu2-cpu-workers "$SSIMU2_WORKERS" --resume --fast-speed 8 --final-speed "$FINAL_SPEED" --workers "$WORKER_COUNT" --fast-params "$FAST_PARAMS" --final-params "$FINAL_PARAMS"\n\n'
    else:
        script += f'python3 "$SCRIPT_DIR/tools/av1an-dispatch.py" --resume "$VERBOSE" --fork "$fork" --arch "$ARCH" --denoise "$DENOISE" --tonemap "$tonemap"{autocrop_flag} --crf "$CRF" --workers "$WORKER_COUNT" --final-speed "$FINAL_SPEED" --final-params "$av1an_settings"\n\n'

    script += 'echo ""\necho "All tasks finished."\necho "Press Enter to clean up temp files (Ctrl+C to keep them and exit)."\nread -r _\n\n'

    # Cleanup
    script += "# --- CLEANUP ---\n"
    script += 'echo "Cleaning up temporary files and folders..."\n'
    script += 'python3 "$SCRIPT_DIR/tools/cleanup.py"\n'

    # --- Write to Output ---
    root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    file_path = os.path.join(root_dir, output_filename)

    with open(file_path, 'w', newline="\n") as f:
        f.write(script)
    try:
        os.chmod(file_path, 0o755)
    except OSError:
        pass
    return output_filename, file_path
def main():
    enable_ansi_colors()

    # Set when the template menu's rescale page hands back here with a worker
    # count already decided, and pinned into the .sh at the end.
    forced_workers = None

    # --- 1. Pass Type ---
    while True:
        clear_screen()
        print("================================================")
        print("       Auto-Boost / Av1an Batch Builder")
        print("================================================\n")
        print("This tool will create a batch script to encode your videos.")
        print("Just answer the questions below and your script will be ready to run.\n")
        if forced_workers:
            print(f"{BLUE}Worker count for this .bat: {forced_workers}, from the rescale page.")
            print(f"The worker benchmark still runs, but this number is what encodes.{RESET}\n")
        print("--------------------------------------------------------")
        print("STEP 1 OF 5: Choose an Encoding Method")
        print("--------------------------------------------------------")
        print("How should the encoder approach your video?\n")
        print("  1: Auto-Boost")
        print("     Two pass encoding with visual metrics. The first pass is")
        print("     a fast-speed preview to measure quality. The second pass")
        print("     uses those measurements to fine-tune the final encode")
        print("     automatically. Can potentially produce better results.\n")
        print("  2: Av1an Single Pass")
        print("     Encodes the video once, straight through.")
        print("     Good if you want faster turnaround.")
        print("     ")
        print("  3: Setup advanced tools\n")
        print("  0: GUI (experimental) - the same questions on one page\n")
        print("  NOTE: If your video has a lot of grain, pick Av1an Single Pass. Auto-Boost can")
        print("  mistake the grain for fine detail and try to preserve it, which wastes bitrate")
        print("  and makes your final file much larger than it needs to be.\n")
        mode_choice = input("Select [0/1/2/3]: ").strip()

        if mode_choice == "0":
            try:
                import scriptbuilder_gui
                scriptbuilder_gui.run(build_script, output_filename_for)
            except ImportError as e:
                print(f"\n{RED}GUI unavailable: {e}")
                print(f"Install python3-tk (apt install python3-tk) and try again.{RESET}")
                input("Press Enter to exit...")
            return

        if mode_choice == "3":
            result = advanced_tools_menu()
            # A tuple is the rescale page sending us into these questions with a
            # worker count already chosen. Checked before the plain truthiness
            # test below, which a tuple would also pass.
            if isinstance(result, tuple):
                forced_workers = result[1]
                continue
            if result:
                return
            continue

        mode = "autoboost" if mode_choice == "1" else "av1an"
        break

    # --- 2. Fork ---
    print("\n--------------------------------------------------------")
    print("STEP 2 OF 5: Choose an Encoder Preset (Fork)")
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
    print("                    tools/av1an/svt-av1 forks/custom/x86-64-v3\n")
    fork_choice = input("Select [1-4]: ").strip()
    fork_map = {"1": "5fish", "2": "essential", "3": "hdr", "4": "custom"}
    fork = fork_map.get(fork_choice, "essential")

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

    # --- HDR Handling (SVT-AV1-HDR fork only; other forks are asked at the end) ---
    tonemap_value = "False"
    if fork == "hdr":
        print("\n--------------------------------------------------------")
        print("HDR Handling (SVT-AV1-HDR fork)")
        print("--------------------------------------------------------")
        print("How should HDR source content be handled?\n")
        print("  1: Auto detect SDR/HDR content")
        print("     MediaInfo detects each source. SDR sources get standard")
        print("     BT.709/BT.601 color settings. HDR sources automatically get")
        print("     matching SVT-AV1-HDR color settings (primaries, transfer,")
        print("     matrix, mastering display, content light) -- HDR stays HDR.\n")
        print("  2: Tonemap HDR to SDR")
        print("     HDR sources are converted to SDR (BT.709) via libplacebo")
        print("     inside the VapourSynth script. Uses GPU. SDR sources are")
        print("     encoded normally. GPU/iGPU 2016 or newer required.")
        print("     Not currently compatible with Intel GPUs.\n")
        hdr_handling_choice = input("Select [1/2] (Press Enter for 1): ").strip()
        if hdr_handling_choice == "2":
            tonemap_value = "True"

    denoise_value = "True" if fork == "5fish" else "False"
    if fork == "5fish":
        print(f"\n{BLUE}--------------------------------------------------------")
        print("5fish Denoise Recommendation")
        print(f"--------------------------------------------------------{RESET}")
        print("For 5fish, denoise=True will be enabled in settings.txt.")
        print("The .sh scripts have the ability to edit denoise= in settings.txt")
        print("This is highly recommended with:")
        print("denoise_setting=src = DFTTest().denoise(src, {0.00:0.30, 0.40:0.30, 0.60:0.60, 0.80:1.50, 1.00:2.00}, planes=[0, 1, 2])\n")
    else:
        print("\nDenoise will be set to False in settings.txt for this generated batch file.")

    # --- 3. CRF ---
    print("\n--------------------------------------------------------")
    print("STEP 3 OF 5: Choose a Quality Level (CRF)")
    print("--------------------------------------------------------")
    print("CRF controls the balance between file size and visual quality.")
    print("Lower numbers = higher quality + larger file size.")
    print("Higher numbers = lower quality + smaller file size.\n")
    print("  Recommended starting points:")
    print("    20 -- Very high quality, large files")
    print("    25 -- Good quality, medium files")
    print("    30 -- Lower quality, small files\n")
    print("If you are unsure, start with 30 and adjust from there.")
    print("You can always re-run this tool to generate a new script.\n")
    crf = input("Enter a CRF value (Press Enter to use the default of 30): ").strip()
    if not crf:
        crf = "30"

    # --- 4. Special Parameters based on Fork ---
    fidelity_value = "0"
    if fork == "essential":
        print("\n--------------------------------------------------------")
        print("STEP 4 OF 5: Fidelity / Detail Preservation (essential fork)")
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
        fidelity_value = val

    grain_value = "clean"
    if fork == "hdr":
        print("\n--------------------------------------------------------")
        print("STEP 4 OF 5: Film Grain / Noise Handling (hdr fork)")
        print("--------------------------------------------------------")
        print("Real-world video (especially film) contains natural grain/noise.")
        print("This setting tells the encoder how to handle it:\n")
        print("  1: Clean / Low Noise")
        print("     Best for: Modern digital footage, clean CGI, animation.")
        print("     The encoder will smooth out noise rather than preserve it.\n")
        print("  2: Film Grain Mode")
        print("     Best for: Film-sourced content, older movies, grainy footage.")
        print("     Preserves the natural film grain in your source video.\n")
        noise_choice = input("Select mode [1/2]: ").strip()
        if noise_choice == "2":
            if mode == "autoboost":
                print(f"\n{RED}WARNING: Auto-Boost mode is NOT recommended for high grain content.")
                print("Visual metrics can mistake grain for detail, which can lead")
                print("to excessive boosting. For grainy SVT-AV1-HDR content, use")
                print(f"Av1an Single Pass mode instead.{RESET}\n")
            grain_value = "film"

    # --- 5. Preset Speed ---
    print("\n--------------------------------------------------------")
    print("STEP 5 OF 5: Encoding Speed (Preset)")
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
    print("  quality while staying efficient, so output quality")
    print("  will suffer.\n")

    if fork == "5fish":
        print("  Quote from 5fish github repo:")
        print("  \"even if you have time to burn, as of right now, we")
        print("  don't recommend --preset 0. --preset 2 has a smart")
        print("  system to filter candidates, while --preset 0 tests")
        print("  through all the candidates. This smart filtering system")
        print("  often outperforms doing the full tests, and for this")
        print("  reason --preset 2 often gives a better result than")
        print("  --preset 0\"\n")

    default_speed = "4"
    speed = input(f"Enter a preset speed (Press Enter for the recommended default of {default_speed}): ").strip()
    if not speed:
        speed = default_speed

    # --- Auto Crop ---
    print("\n--------------------------------------------------------")
    print("Auto Crop")
    print("--------------------------------------------------------")
    print("Most movies and TV shows have black bars on the top and bottom")
    print("(letterboxing). Auto crop automatically detects and removes them,")
    print("which saves file space and avoids wasting encoding bits on black areas.\n")
    print("  1: Yes -- Automatically detect and crop black bars")
    print("  2: No  -- Keep the video as-is, no cropping\n")
    print("Tip: If auto crop removes too much or too little, you can open")
    print("settings.txt in a text editor and switch to manual crop mode instead.\n")
    autocrop_input = input("Enable auto crop? [1 Yes / 2 No] (Press Enter for No): ").strip()
    use_autocrop = autocrop_input == "1"

    # --- One-Time Worker Optimization ---
    print("\n--------------------------------------------------------")
    print("One-Time Worker Optimization Benchmark")
    print("--------------------------------------------------------")
    print("Optimize av1an svt-av1 and ssim2 worker count for this bat")
    print("script with one-time benchmark? This will target proper cpu")
    print("and gpu saturation for your svt-av1 preset and any filtering.\n")
    print("  1: Yes -- On first launch, this .sh runs a one-time benchmark")
    print("            using YOUR chosen encoder preset/params and any")
    print("            settings.txt filtering. The tuned worker counts are")
    print("            saved inside this .sh and reused on every run.")
    print("  2: No  -- Use the standard shared worker calculation\n")
    optimize_input = input("Select [1 Yes / 2 No] (Press Enter for No): ").strip()
    optimize_workers = optimize_input == "1"

    # --- HDR Handling (non-HDR forks, asked last) ---
    if fork != "hdr":
        print("\n--------------------------------------------------------")
        print("HDR Handling")
        print("--------------------------------------------------------")
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
        sdr_handling_choice = input("Select [1/2] (Press Enter for the default of 1): ").strip()
        if sdr_handling_choice == "2":
            tonemap_value = "True"

    # --- Interface Mode (final question) ---
    print("\n--------------------------------------------------------")
    print("Interface Mode")
    print("--------------------------------------------------------")
    print("How much information do you want to see while encoding?\n")
    print("  1: Default mode -- Simple interface with progress bars")
    print("     Each phase of the workflow shows a clean progress bar")
    print("     with a short explanation of what is happening.")
    print("     Recommended for new users.\n")
    print("  2: Verbose mode -- Show me everything")
    print("     Adds --verbose to the generated .sh and displays the")
    print("     full output of every tool during the workflow.\n")
    interface_choice = input("Select [1/2] (Press Enter for the default of 1): ").strip()
    verbose_value = "--verbose" if interface_choice == "2" else "--no-verbose"

    # --- Construct Script Content ---
    cfg = {
        "mode": mode,
        "fork": fork,
        "arch": arch_value,
        "crf": crf,
        "speed": speed,
        "fidelity": fidelity_value,
        "grain": grain_value,
        "tonemap": tonemap_value,
        "denoise": denoise_value,
        "autocrop": use_autocrop,
        "optimize_workers": optimize_workers,
        "forced_workers": forced_workers,
        "verbose": verbose_value,
    }
    output_filename, _path = build_script(cfg)

    print("\n-------------------------------------------------------------------------------")
    print(f"Success! Your batch script has been generated:")
    print(f"File: {output_filename}")
    print("-------------------------------------------------------------------------------")
    print("Drop your video files into the 'video-input' folder, then double-click")
    print("the .sh file to start encoding. Encoded files will appear in 'video-output'.")
    print("")
    if forced_workers:
        print(f"This .sh encodes with {forced_workers} worker(s), set on the rescale page. The line")
        print("that does it is near the top of the worker section, marked WORKER COUNT")
        print("OVERRIDE - edit or delete it there to change it.")
        print("")
    if vpy_template.lossless_mode_active():
        print(f"{BLUE}Lossless intermediary mode is on, so this .sh filters each video")
        print("once into temp\\lossless and encodes from that file. Nothing extra to")
        print(f"run, and only one of those files is on the drive at a time.{RESET}")
        print("")
    print("Want to tweak the settings manually? Open the .sh file in a text editor.")
    print("-------------------------------------------------------------------------------")
    input('Press Enter to exit...')

if __name__ == "__main__":
    main()
