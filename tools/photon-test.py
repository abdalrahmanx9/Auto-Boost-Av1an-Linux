import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


# Match bat-builder.py's essential fork distortion/fidelity level 0 output:
#   final_params = "--scd 0 --enable-dlf 3 --photon-noise 200"
# For comparison runs only the selected grain/noise value changes.
DEFAULT_PHOTON_LEVELS = [200, 400, 600, 800, 1000]
DEFAULT_FILM_GRAIN_LEVELS = [6, 8, 10, 12, 14, 16]
CRF = "30"
PRESET = "8"
WORKERS = "2"
CLIP_SECONDS = 10.0
CLIP_NAME = "photon-test-source-10s.mkv"
TEMP_DIR_NAME = "temp"
OUTPUT_PATTERN = re.compile(r"^(photon|filmgrain)\d+\.mkv$", re.IGNORECASE)
IGNORED_MKV_NAMES = {CLIP_NAME}


def is_test_output(path):
    name = path.name.lower()
    if name in IGNORED_MKV_NAMES:
        return True
    return bool(OUTPUT_PATTERN.match(name))


def run_streamed(cmd, cwd):
    """Run a command attached to the real console so progress remains visible."""
    print(" ".join(f'"{part}"' if " " in str(part) else str(part) for part in cmd), flush=True)
    # Do not capture av1an stdout/stderr. Av1an detects redirected output and may
    # suppress or mangle its progress UI. Inheriting the batch window's console
    # keeps the normal progress display visible.
    return subprocess.call(cmd, cwd=cwd)


def wait_any_key():
    """Best-effort 'press any key' helper for direct runs outside the .bat."""
    print("Press any key to exit.")
    try:
        import msvcrt

        msvcrt.getch()
    except Exception:
        input("Press Enter to exit.")


def select_essential_fork(tools_dir):
    """Copy the essential SvtAv1EncApp.exe into tools/av1an, matching normal dispatch."""
    sys.path.insert(0, str(tools_dir))
    try:
        from svt_fork_setup import setup_svt_av1_fork
    except Exception as exc:
        print(f"Warning: Could not import svt_fork_setup.py: {exc}")
        return False
    # x86-64-v3 runs on any modern CPU, which is what a test needs.
    return setup_svt_av1_fork(tools_dir, "essential", arch="x86-64-v3", verbose=True)


def detect_source_mkv(work_dir, requested_source=None):
    if requested_source:
        source = (work_dir / requested_source).resolve()
        try:
            source.relative_to(work_dir.resolve())
        except ValueError:
            print(f"Error: Source MKV must be inside the extras folder: {source}")
            return None
        if not source.exists() or source.suffix.lower() != ".mkv":
            print(f"Error: Source MKV was not found: {source.name}")
            return None
        return source

    candidates = [
        path
        for path in sorted(work_dir.glob("*.mkv"), key=lambda p: p.name.lower())
        if not is_test_output(path)
    ]
    if not candidates:
        print("Error: No source MKV found.")
        print("Place exactly one MKV in this extras folder and run photon-noise-test.bat again.")
        return None
    if len(candidates) > 1:
        print("Error: Multiple MKV files were detected in the extras folder.")
        print("Please provide only a single source MKV for photon testing.")
        print("Detected source candidates:")
        for path in candidates:
            print(f"  {path.name}")
        wait_any_key()
        return None
    return candidates[0]


def read_duration_seconds(mkvmerge_exe, source_file, work_dir):
    info_cmd = [str(mkvmerge_exe), "-J", str(source_file)]
    try:
        result = subprocess.run(
            info_cmd,
            cwd=work_dir,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError as exc:
        print(f"Error: Could not run mkvmerge.exe to read duration: {exc}")
        return None

    if result.returncode != 0:
        print("Warning: mkvmerge.exe could not read the source duration.")
        if result.stderr.strip():
            print(result.stderr.strip())
        return None

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        print(f"Warning: Could not parse mkvmerge duration JSON: {exc}")
        return None

    duration_ns = data.get("container", {}).get("properties", {}).get("duration")
    if duration_ns:
        try:
            return float(duration_ns) / 1_000_000_000.0
        except (TypeError, ValueError):
            pass

    track_durations = []
    for track in data.get("tracks", []):
        duration = track.get("properties", {}).get("duration")
        if duration:
            try:
                track_durations.append(float(duration) / 1_000_000_000.0)
            except (TypeError, ValueError):
                pass
    return max(track_durations) if track_durations else None


def format_timestamp(seconds):
    seconds = max(0.0, float(seconds))
    whole = int(seconds)
    millis = int(round((seconds - whole) * 1000))
    if millis >= 1000:
        whole += 1
        millis -= 1000
    hours, remainder = divmod(whole, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"


def choose_start_seconds(duration_seconds):
    default_start = None
    if duration_seconds is not None:
        default_start = max(0.0, (duration_seconds - CLIP_SECONDS) / 2.0)
        default_end = min(duration_seconds, default_start + CLIP_SECONDS)
        print(f"Source duration: {duration_seconds:.3f} seconds")
        print(
            "Default test clip: "
            f"{format_timestamp(default_start)} to {format_timestamp(default_end)} "
            "(10 seconds from the middle of the clip)"
        )
    else:
        print("Source duration is unknown, so the middle default is unavailable.")
        print("Enter a start time in seconds to create the 10 second test clip.")

    while True:
        if default_start is None:
            answer = input("Start time in seconds: ").strip()
            if not answer:
                print("Please enter a start time in seconds.")
                continue
        else:
            answer = input(
                "Press Enter for the default middle 10 seconds, "
                "or enter a start time in seconds: "
            ).strip()
            if not answer:
                return default_start

        try:
            start = float(answer)
        except ValueError:
            print("Please enter a numeric start time in seconds, or press Enter for the default.")
            continue
        if start < 0:
            print("Start time cannot be negative.")
            continue
        if duration_seconds is not None and start >= duration_seconds:
            print("Start time must be before the end of the source clip.")
            continue
        return start


def parse_range_answer(answer, default_levels, step, minimum=0):
    answer = answer.strip()
    if not answer:
        return list(default_levels)
    if "-" not in answer:
        try:
            value = int(answer)
        except ValueError:
            return None
        return [value] if value >= minimum else None

    start_text, end_text = answer.split("-", 1)
    try:
        start = int(start_text.strip())
        end = int(end_text.strip())
    except ValueError:
        return None
    if start < minimum or end < minimum or end < start:
        return None
    return list(range(start, end + 1, step))


def choose_test_mode_and_levels():
    print("\nWhich test do you want to run?")
    print("  1. Photon-noise test")
    print("  2. Film-grain test")
    while True:
        answer = input("Select 1 or 2: ").strip().lower()
        if answer in {"1", "p", "photon", "photon-noise", "photon noise"}:
            print("\nPhoton-noise test selected.")
            print("Five test files will be created for photon-noise values: 200, 400, 600, 800, 1000.")
            print("Or you can select a range, example: 600-1600")
            print("Values above 2000 could cause obvious patterns depending on source.")
            while True:
                levels = parse_range_answer(
                    input("Press Enter for defaults, or enter a photon-noise range: "),
                    DEFAULT_PHOTON_LEVELS,
                    200,
                    minimum=0,
                )
                if levels:
                    return "photon", levels
                print("Please enter a valid photon-noise value or range, for example: 600-1600")
        if answer in {"2", "f", "film", "film-grain", "film grain"}:
            print("\nFilm-grain test selected.")
            print("Six test files will be created for film-grain values: 6, 8, 10, 12, 14, 16.")
            print("Or you can select a range, example: 6-16")
            print("Values above 20 could cause obvious patterns depending on source.")
            print('SVT-AV1 syntax example: "--film-grain 8"')
            while True:
                levels = parse_range_answer(
                    input("Press Enter for defaults, or enter a film-grain range: "),
                    DEFAULT_FILM_GRAIN_LEVELS,
                    2,
                    minimum=0,
                )
                if levels:
                    return "filmgrain", levels
                print("Please enter a valid film-grain value or range, for example: 6-16")
        print("Please select 1 for photon-noise test or 2 for film-grain test.")


def create_test_clip(mkvmerge_exe, source_file, clip_file, start_seconds, work_dir):
    end_seconds = start_seconds + CLIP_SECONDS
    if clip_file.exists():
        print(f"Removing existing temporary test clip: {clip_file.name}")
        clip_file.unlink()

    temp_work_dir = clip_file.parent
    temp_work_dir.mkdir(exist_ok=True)
    temp_prefix = temp_work_dir / "photon-test-source-10s-part.mkv"
    for old_part in temp_work_dir.glob("photon-test-source-10s-part*.mkv"):
        print(f"Removing stale mkvmerge part file: {old_part.name}")
        old_part.unlink()

    split_range = f"{format_timestamp(start_seconds)}-{format_timestamp(end_seconds)}"
    cmd = [
        str(mkvmerge_exe),
        "-o",
        str(temp_prefix),
        "--split",
        f"parts:{split_range}",
        str(source_file),
    ]

    print("\nCreating 10 second test clip with MKVToolNix mkvmerge.exe")
    print(f"Source: {source_file.name}")
    print(f"Range:  {split_range}")
    rc = run_streamed(cmd, cwd=temp_work_dir)
    if rc != 0:
        print(f"Error: mkvmerge.exe failed while creating the 10 second test clip (exit code {rc}).")
        return False

    created_parts = [path for path in sorted(temp_work_dir.glob("photon-test-source-10s-part*.mkv")) if path.exists()]
    if not created_parts:
        print(f"Error: mkvmerge.exe did not create the expected test clip: {clip_file.name}")
        return False
    if len(created_parts) > 1:
        print("Warning: mkvmerge.exe created multiple part files; using the first part for testing.")
        for extra_part in created_parts[1:]:
            print(f"Removing extra part: {extra_part.name}")
            extra_part.unlink()

    created_parts[0].replace(clip_file)
    print(f"Test clip ready: {clip_file.name}")
    return True


def output_name_for(test_mode, level):
    if test_mode == "photon":
        return f"photon{level // 100:02d}.mkv"
    return f"filmgrain{level:02d}.mkv"


def encoder_params_for(test_mode, level):
    if test_mode == "photon":
        return f"--crf {CRF} --preset {PRESET} --scd 0 --enable-dlf 3 --photon-noise {level}"
    return f"--crf {CRF} --preset {PRESET} --scd 0 --enable-dlf 3 --film-grain {level}"


def remove_old_outputs(work_dir):
    for path in sorted(work_dir.glob("*.mkv"), key=lambda p: p.name.lower()):
        if is_test_output(path) and path.name.lower() != CLIP_NAME:
            print(f"Removing existing test output: {path.name}")
            path.unlink()


def ensure_temp_dir(work_dir):
    temp_dir = work_dir / TEMP_DIR_NAME
    temp_dir.mkdir(exist_ok=True)
    return temp_dir


def repair_vspreview_storage():
    """Repair stale VSPreview storage that crashes on missing WindowSettings.zoom_index."""
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return

    global_storage = os.path.join(appdata, "vspreview", "global.yml")
    if not os.path.exists(global_storage):
        return

    try:
        with open(global_storage, "r", encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except OSError as exc:
        print(f"[VSPreview] Warning: Could not read storage {global_storage}: {exc}")
        return

    changed = False
    in_window_settings = False
    block_start = None
    indent = "        "
    has_zoom_index = False

    for idx, line in enumerate(lines):
        if "!!python/object:vspreview.main.settings.WindowSettings" in line:
            in_window_settings = True
            block_start = idx
            has_zoom_index = False
            leading = line[:len(line) - len(line.lstrip())]
            indent = leading + "    "
            continue

        if not in_window_settings:
            continue

        stripped = line.strip()
        leading_len = len(line) - len(line.lstrip())
        if stripped and leading_len <= len(indent) - 4 and idx > (block_start or 0):
            if not has_zoom_index:
                lines.insert(idx, f"{indent}zoom_index: 1")
                changed = True
            in_window_settings = False
            continue

        if stripped.startswith("zoom_index:"):
            has_zoom_index = True
        elif stripped.startswith(("x_pos:", "y_pos:")) and not has_zoom_index:
            lines.insert(idx, f"{indent}zoom_index: 1")
            changed = True
            has_zoom_index = True
            in_window_settings = False
            break

    if in_window_settings and not has_zoom_index:
        lines.append(f"{indent}zoom_index: 1")
        changed = True

    if changed:
        backup_path = global_storage + ".bak"
        try:
            if not os.path.exists(backup_path):
                shutil.copy2(global_storage, backup_path)
            with open(global_storage, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
            print(f"[VSPreview] Repaired missing zoom_index in: {global_storage}")
        except OSError as exc:
            print(f"[VSPreview] Warning: Could not repair storage {global_storage}: {exc}")


def is_package_storage(path):
    """True for the package's own .vsjet folder.

    A .vsjet folder next to a script is scratch. The one holding ArtCNN's ONNX
    models - 35 MB, downloaded by hand - is not: it lives in VapourSynth\\.vsjet,
    with the package root still checked for installs from before the move. A
    sweep that happens to run from either has to leave it alone.
    """
    root_dir = Path(__file__).resolve().parent.parent
    return Path(path).resolve() in (root_dir / "VapourSynth" / ".vsjet",
                                    root_dir / ".vsjet")


def cleanup_preview(vpy_file, work_dir=None):
    """Clean up generated VPY files, scratch .vsjet folders, and .ffindex files."""
    base_dir = Path(work_dir) if work_dir is not None else Path.cwd()
    if vpy_file:
        vpy_path = Path(vpy_file)
        if not vpy_path.is_absolute():
            vpy_path = base_dir / vpy_path
        if vpy_path.exists():
            try:
                vpy_path.unlink()
            except OSError:
                pass

    for f in base_dir.glob("*.vpy"):
        try:
            f.unlink()
        except OSError:
            pass

    vsjet_dir = base_dir / ".vsjet"
    if vsjet_dir.exists() and not is_package_storage(vsjet_dir):
        try:
            shutil.rmtree(vsjet_dir)
        except OSError:
            pass

    for f in base_dir.glob("*.ffindex"):
        try:
            f.unlink()
        except OSError:
            pass


def create_vpy_script(mkv_files):
    """Generate the VSPreview .vpy script content for the encoded test files."""
    lines = [
        "import vapoursynth as vs",
        "core = vs.core",
        "",
        "# Define an ASS-style string with Alignment=7 (top-left)",
        "ass_style = ','.join([",
        "    'Arial', '24',",
        "    '&H00FFFFFF', '&H000000FF', '&H00000000', '&H00000000',",
        "    '0', '0', '0', '0',",
        "    '100', '100',",
        "    '0', '0',",
        "    '1', '2', '0', '7',",
        "    '10', '10', '10',",
        "    '1'",
        "])",
        "",
        "clips = []",
        "labels = []",
    ]

    vpy_filename = "preview.vpy"
    for mkv in mkv_files:
        mkv_path = str(mkv).replace("\\", "/")
        file_label = os.path.basename(mkv)
        lines.append(f'clips.append(core.ffms2.Source(source={mkv_path!r}))')
        lines.append(f'labels.append({file_label!r})')

    lines.extend([
        "",
        "# Find the smallest height among all loaded clips",
        "min_height = min([c.height for c in clips])",
        "",
        "for i, clip in enumerate(clips):",
        "    if clip.height > min_height:",
        "        # Calculate new width, maintaining aspect ratio and ensuring mod 2 for chroma subsampling",
        "        new_width = round((clip.width * (min_height / clip.height)) / 2) * 2",
        "        clip = core.resize.Lanczos(clip, width=new_width, height=min_height)",
        "",
        "    # Add the subtitle with the filename",
        "    clip = core.sub.Subtitle(clip, text=[labels[i]], style=ass_style)",
        "",
        "    # set_output(0) corresponds to pressing '1' in vspreview",
        "    clip.set_output(i)",
        "",
    ])

    return vpy_filename, "\n".join(lines)


def cleanup_generated_test_files(work_dir, encoded_files):
    """Delete generated comparison files, helper indexes, av1an logs, and test clip files."""
    generated_paths = set(encoded_files)
    generated_paths.add(work_dir / CLIP_NAME)

    # Remove the generated comparison MKVs and the temporary 10 second source clip.
    for path in sorted(generated_paths, key=lambda p: p.name.lower()):
        if path.exists():
            print(f"Deleting temp file: {path.name}")
            path.unlink()

    # Remove helper sidecars such as photon-test-source-10s.mkv.0.bsindex.
    for pattern in (f"{CLIP_NAME}.*", "photon-test-source-10s-part*.mkv"):
        for path in sorted(work_dir.glob(pattern), key=lambda p: p.name.lower()):
            if path.is_file():
                print(f"Deleting temp file: {path.name}")
                path.unlink()

    # Remove stale generated comparison outputs matching this helper's naming scheme.
    # This intentionally avoids source files such as "photon sample.mkv".
    for path in sorted(work_dir.glob("*.mkv"), key=lambda p: p.name.lower()):
        if is_test_output(path) and path.name.lower() != CLIP_NAME:
            print(f"Deleting generated test output: {path.name}")
            path.unlink()

    for temp_dir in sorted(work_dir.glob("*-av1an-temp"), key=lambda p: p.name.lower()):
        if temp_dir.is_dir():
            print(f"Deleting temp folder: {temp_dir.name}")
            shutil.rmtree(temp_dir, ignore_errors=True)

    logs_dir = work_dir / "logs"
    if logs_dir.is_dir():
        print("Deleting temp folder: logs")
        shutil.rmtree(logs_dir, ignore_errors=True)

    # If this helper-created extras/temp folder is now empty, remove the folder too.
    if work_dir.name.lower() == TEMP_DIR_NAME:
        try:
            work_dir.rmdir()
            print(f"Deleting temp folder: {work_dir.name}")
        except OSError:
            # Leave it alone if something user-created or still-open remains inside.
            pass


def launch_vspreview(mkv_files, work_dir, source_clip=None):
    if not mkv_files:
        print("No encoded MKV files were created for preview.")
        return 1

    preview_files = []
    if source_clip is not None:
        source_clip = Path(source_clip)
        if source_clip.exists():
            preview_files.append(source_clip)
        else:
            print(f"[VSPreview] Warning: 10 second source clip was not found: {source_clip.name}")
    preview_files.extend(mkv_files)

    print("\nLaunching VSPreview directly; no extra vspreview.bat pause is needed.")
    if source_clip is not None and source_clip in preview_files:
        print(f"[VSPreview] Loading source test clip too: {source_clip.name}")
    repair_vspreview_storage()
    cleanup_preview(None, work_dir)

    vpy_filename, script_content = create_vpy_script(preview_files)
    print(f"Generating script: {vpy_filename} for {len(preview_files)} file(s)...")
    with open(work_dir / vpy_filename, "w", encoding="utf-8") as f:
        f.write(script_content)

    # Via the vs-jetpack 2 compatibility shim when it is there - see
    # tools\\vspreview_compat.py. This file keeps its own copy of the launch
    # line rather than importing vspreview-dispatch.py, the same way it keeps
    # its own repair_vspreview_storage: it is meant to run standalone.
    shim = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "vspreview_compat.py")
    if os.path.exists(shim):
        cmd = [sys.executable, shim, vpy_filename]
    else:
        cmd = [sys.executable, "-m", "vspreview", vpy_filename]
    rc = 0
    try:
        # Held back and thrown away when VSPreview exits cleanly: a preview that
        # opened fine still prints plugin deprecation notices and a couple of
        # warnings that mean nothing here. On a failure it is printed in full,
        # because then it is the only account of what went wrong.
        result = subprocess.run(cmd, cwd=work_dir, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True,
                                encoding="utf-8", errors="replace")
        if result.returncode != 0:
            if (result.stdout or "").strip():
                print(result.stdout.strip())
            raise subprocess.CalledProcessError(result.returncode, cmd)
    except subprocess.CalledProcessError as exc:
        print(f"Error occurred while running vspreview: {exc}")
        rc = exc.returncode or 1
    except KeyboardInterrupt:
        rc = 0
    finally:
        input("Press enter to clean up temp files")
        print("Cleaning up temporary VSPreview and generated test files...")
        cleanup_preview(vpy_filename, work_dir)
        cleanup_generated_test_files(work_dir, mkv_files)
    return rc


def parse_args():
    parser = argparse.ArgumentParser(description="Create a 10 second grain/noise test clip and encode comparison variants.")
    parser.add_argument("--source", help="Source MKV filename located in the current extras folder.")
    parser.add_argument("--no-preview", action="store_true", help="Encode files but do not launch VSPreview.")
    return parser.parse_args()


def main():
    args = parse_args()
    script_dir = Path(__file__).resolve().parent          # ...\tools
    av1an_exe = script_dir / "av1an" / "av1an"
    if not av1an_exe.exists():
        av1an_exe = Path(shutil.which("av1an") or av1an_exe)
    mkvmerge_exe = Path(shutil.which("mkvmerge") or (script_dir / "MKVToolNix" / "mkvmerge"))
    work_dir = Path.cwd().resolve()
    temp_dir = ensure_temp_dir(work_dir)
    source_file = detect_source_mkv(work_dir, args.source)
    clip_file = temp_dir / CLIP_NAME

    print("\n--- Auto-Boost Grain / Photon Test ---")
    print(f"Working folder: {work_dir}")
    print(f"Temp folder: {temp_dir}")

    if source_file is None:
        return 1
    print(f"Source MKV: {source_file.name}")

    if not av1an_exe.exists():
        print(f"Error: av1an.exe was not found: {av1an_exe}")
        return 1
    if not mkvmerge_exe.exists():
        print(f"Error: mkvmerge.exe was not found: {mkvmerge_exe}")
        return 1

    test_mode, levels = choose_test_mode_and_levels()
    mode_label = "photon-noise" if test_mode == "photon" else "film-grain"
    print(f"Selected {mode_label} levels: " + ", ".join(str(level) for level in levels))
    print("External photon noise table files are not used.")

    duration_seconds = read_duration_seconds(mkvmerge_exe, source_file, work_dir)
    start_seconds = choose_start_seconds(duration_seconds)
    if not create_test_clip(mkvmerge_exe, source_file, clip_file, start_seconds, work_dir):
        return 1

    if not select_essential_fork(script_dir):
        print("Error: Could not select the essential SVT-AV1 fork.")
        return 1

    remove_old_outputs(work_dir)
    remove_old_outputs(temp_dir)
    encoded_files = []
    for level in levels:
        output_file = temp_dir / output_name_for(test_mode, level)
        av1an_temp_dir = temp_dir / f"{output_file.stem}-av1an-temp"
        encoder_params = encoder_params_for(test_mode, level)

        if output_file.exists():
            print(f"Removing existing output: {output_file.name}")
            output_file.unlink()
        if av1an_temp_dir.exists():
            print(f"Removing stale av1an temp folder: {av1an_temp_dir.name}")
            shutil.rmtree(av1an_temp_dir, ignore_errors=True)

        setting_name = "--photon-noise" if test_mode == "photon" else "--film-grain"
        print("\n" + "=" * 72)
        print(f"Creating {output_file.name} from {clip_file.name} with {setting_name} {level}")
        print(f"Encoder params: {encoder_params}")
        print("=" * 72)

        cmd = [
            str(av1an_exe),
            "-i", str(clip_file),
            "-e", "svt-av1",
            "--no-defaults",
            "--split-method", "none",
            "-y",
            "--temp", str(av1an_temp_dir),
            "-w", WORKERS,
            "-o", str(output_file),
            "-v", encoder_params,
        ]

        rc = run_streamed(cmd, cwd=temp_dir)
        if rc != 0:
            print(f"Error: av1an failed for {mode_label} {level} (exit code {rc}).")
            return rc
        if not output_file.exists():
            print(f"Error: Expected output was not created: {output_file}")
            return 1
        encoded_files.append(output_file)

    print(f"\nSuccessfully created {mode_label} comparison files from the 10 second test clip:")
    for path, level in zip(encoded_files, levels):
        setting_name = "--photon-noise" if test_mode == "photon" else "--film-grain"
        print(f"  {path.name}  ({setting_name} {level})")
    print("External photon noise table files are not used.")

    if args.no_preview:
        print("Skipping VSPreview because --no-preview was passed.")
        return 0
    return launch_vspreview(encoded_files, temp_dir, clip_file)


if __name__ == "__main__":
    raise SystemExit(main())
