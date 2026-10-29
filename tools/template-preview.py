"""Preview video-input\\template.vpy against the untouched source.

The companion to settings-preview.py. That one previews the chain settings.txt
would generate; this one previews the hand-edited template.vpy that replaces it,
so what you see here is exactly what av1an, Auto-Boost and Condor will encode -
the same renderer, filling in the same one thing: the source path. The crop is
whatever the template's own std.Crop line says, here and at encode time.

Launched by video-input\\template-preview.bat, which bat-builder.py writes next
to the template.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS_DIR))

import source_filter
import vpy_template

ROOT_DIR = TOOLS_DIR.parent
VIDEO_INPUT_DIR = ROOT_DIR / "video-input"
# Deleted when VSPreview closes. The ".tmp" suffix is the fallback: if the
# preview is killed outright, tools/cleanup.py sweeps the folder up later.
TEMP_DIR = ROOT_DIR / "template-preview.tmp"

BLUE = "\033[94m"
RED = "\033[91m"
RESET = "\033[0m"

# Appended to the rendered script so VSPreview gets the source and the filtered
# result as two outputs. The template itself only sets output 0, and rather than
# assume it is still called "final" this reads back whatever it put there, so a
# renamed variable in a hand-edited template does not break the preview.
PREVIEW_EPILOGUE = '''

# --- Preview outputs -------------------------------------------------------
# Added by tools/template-preview.py. Not part of template.vpy, and never part
# of an encode: the encoders render the template without this block. Its own
# imports are used throughout so that renaming anything in the template cannot
# break the comparison.
import vapoursynth as _vs
from vstools import initialize_clip as _initialize, finalize_clip as _finalize

_core = _vs.core
_preview_output = _vs.get_output(0)
_preview_filtered = getattr(_preview_output, "clip", _preview_output)
_preview_source = _finalize(_initialize({source_call}))
_core.text.Text(_preview_source, "1: SOURCE - template.vpy not applied", alignment=7).set_output(0)
_core.text.Text(_preview_filtered, "2: TEMPLATE - template.vpy applied", alignment=7).set_output(1)
'''


def main():
    args = [arg for arg in sys.argv[1:] if arg.strip()]

    # settings-preview.py owns the input picking and the plugin-path setup;
    # reusing them keeps the two previews behaving the same. Its crop prompt is
    # not reused: the template crops itself, so there is nothing to ask.
    settings_preview = None
    try:
        spec_path = TOOLS_DIR / "settings-preview.py"
        import importlib.util
        spec = importlib.util.spec_from_file_location("settings_preview_shared", str(spec_path))
        settings_preview = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(settings_preview)
    except Exception as e:
        print(f"{RED}[Preview] Could not load tools\\settings-preview.py: {e}{RESET}")
        print(f"{RED}[Preview] Your install may be incomplete - try re-downloading the package.{RESET}")
        input("Press Enter to exit...")
        return 1

    # Before anything spawns a VapourSynth core.
    settings_preview.ensure_plugin_path()

    template_file = vpy_template.find_template()
    if not template_file:
        print(f"{RED}[Preview] No template.vpy found in {VIDEO_INPUT_DIR}{RESET}")
        print(f"{RED}[Preview] Write one from bat-builder.bat > Setup advanced tools >{RESET}")
        print(f"{RED}[Preview] \"Write template.vpy based off settings.txt\".{RESET}")
        input("Press Enter to exit...")
        return 1

    if not VIDEO_INPUT_DIR.is_dir():
        print(f"{RED}[Preview] video-input folder not found at {VIDEO_INPUT_DIR}{RESET}")
        input("Press Enter to exit...")
        return 1

    source_path = settings_preview.select_input_file(args[0] if args else None)
    if not source_path:
        input("Press Enter to exit...")
        return 1

    print(f"{BLUE}[Preview] Source:   {os.path.basename(source_path)}{RESET}")
    print(f"{BLUE}[Preview] Template: {template_file}{RESET}")

    vspreview_dispatch = settings_preview.load_module("vspreview_dispatch_template_preview",
                                                      TOOLS_DIR / "vspreview-dispatch.py")

    try:
        template_text = vpy_template.read_template(template_file)
    except vpy_template.TemplateError as e:
        print(f"{RED}[Preview] {e}{RESET}")
        input("Press Enter to exit...")
        return 1

    # The crop is the template's, exactly as it will be at encode time, so the
    # preview only reads it back to say what it is.
    crop_values = vpy_template.read_crop_values(template_text) or (0, 0, 0, 0)

    basename = Path(source_path).stem
    TEMP_DIR.mkdir(exist_ok=True)
    vpy_file = TEMP_DIR / f"{basename}-template-preview.vpy"
    active_source_filter = source_filter.resolve()

    try:
        script_text = vpy_template.render(template_text, source_path)
    except vpy_template.TemplateError as e:
        print(f"{RED}[Preview] template.vpy cannot be used: {e}{RESET}")
        cleanup(source_path)
        input("Press Enter to exit...")
        return 1

    # The reference is opened with the same source filter the template uses, so
    # both sides of the comparison decode identically.
    # The epilogue keeps its VapourSynth core under its own name, so the call
    # built here is pointed at that one rather than the template's.
    reference_call = source_filter.plain_source_call(active_source_filter, source_path)
    script_text += PREVIEW_EPILOGUE.format(
        source_call=reference_call.replace("core.", "_core.", 1))
    vpy_template.write_rendered(vpy_file, script_text)

    if any(crop_values):
        print(f"[Preview] Crop:     from the template (top={crop_values[0]}, "
              f"bottom={crop_values[1]}, left={crop_values[2]}, right={crop_values[3]})")
    else:
        print("[Preview] Crop:     nothing cropped by the template")
    print(f"[Preview] Rendered template to: {vpy_file}")
    print()

    # core.ffms2.Source reports no progress, so indexing a long source looks
    # like a hang with an empty console in front of it. ffmsindex prints a
    # percentage while it builds the index. Neither the template's source line
    # nor the epilogue's reference call passes a cachefile, so both read the
    # <source>.ffindex this writes and one index covers the pair.
    #
    # BestSource prints its own progress and keeps its own index, so it is left
    # to index itself - ffmsindex would only build a file nothing reads.
    indexed = False
    if active_source_filter != source_filter.BESTSOURCE:
        indexed = vspreview_dispatch.index_source(source_path)
    previewer = vspreview_dispatch.previewer_name()
    print(f"[Preview] In {previewer}:")
    print("[Preview]   1 = untouched source, 2 = template.vpy applied")
    print("[Preview]   Ctrl+mousewheel zooms. Close the window to return here.")
    if any(crop_values):
        print("[Preview]   Crop or downscaling changes the frame size, so 1 and 2 will not")
        print("[Preview]   line up pixel for pixel.")
    if indexed:
        print("[Preview] The source is indexed already, so the first frame is quick.")
    elif active_source_filter == source_filter.BESTSOURCE:
        print("[Preview] First frame takes a while: BestSource has to index the source.")
    else:
        print("[Preview] First frame takes a while: the source has to be indexed.")
    print()

    # Stale VSPreview storage missing WindowSettings.zoom_index crashes the
    # launch before the window appears. Whatever it says about the repair is
    # printed: nothing here hides console output from the user. Under vsview it
    # does nothing - vsview keeps its own settings and never reads that file.
    vspreview_dispatch.repair_vspreview_storage()

    # run_preview holds the previewer's console output back and throws it away
    # on a clean exit. This preview shows it instead, live, as the previewer
    # writes it - a deprecation notice or a plugin warning on the way up is the
    # user's to read, not this file's to decide about. The switch is the same
    # one a user could set by hand, so nothing in vspreview-dispatch.py has to
    # change for it.
    os.environ[vspreview_dispatch.VERBOSE_ENV_VAR] = "1"

    exit_code = 0
    try:
        # Run from the temp folder so VSPreview's own scratch files land there.
        vspreview_dispatch.run_preview(vpy_file, cwd=TEMP_DIR)
    except subprocess.CalledProcessError as e:
        print(f"{RED}[Preview] {previewer} exited with an error: {e}{RESET}")
        exit_code = e.returncode or 1
    except KeyboardInterrupt:
        pass
    finally:
        cleanup(source_path)

    return exit_code


def cleanup(source_path):
    """Remove everything the preview created once VSPreview has closed.

    The whole temp folder goes: the rendered .vpy, the source index, VSPreview's
    own scratch files and its .vsjet cache all live in there.
    """
    try:
        if TEMP_DIR.is_dir():
            shutil.rmtree(TEMP_DIR)
    except OSError:
        pass

    # An earlier crop detection, from settings-preview.py or an encode, can have
    # left a CSV next to the source. Nothing here writes one any more, but a
    # stale one is still worth clearing.
    if source_path:
        crop_csv = Path(source_path).with_name(f"{Path(source_path).stem}_crop.csv")
        try:
            if crop_csv.exists():
                os.remove(crop_csv)
        except OSError:
            pass


if __name__ == "__main__":
    sys.exit(main())
