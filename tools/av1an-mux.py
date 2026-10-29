import os
import subprocess
import glob
import sys
import json
import shutil

# Paths to executables (system-wide on Linux)
MKVMERGE = shutil.which("mkvmerge") or "mkvmerge"
MKVEXTRACT = shutil.which("mkvextract") or "mkvextract"
MKVPROPEDIT = shutil.which("mkvpropedit") or "mkvpropedit"
MEDIAINFO = shutil.which("mediainfo") or "mediainfo"

def run_command(cmd, status_label):
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
    )
    print(f"{status_label}: Starting...          ", end="\r")
    sys.stdout.flush()
    for line in process.stdout:
        line = line.strip()
        if line.startswith("Progress:"):
            percent = line.split(":")[-1].strip()
            print(f"{status_label}: {percent}          ", end="\r")
            sys.stdout.flush()
    process.wait()
    if process.returncode != 0:
        print(f"\n[ERROR] Command failed: {' '.join(cmd)}")
        raise subprocess.CalledProcessError(process.returncode, cmd)
    print(f"{status_label}: Done.          ")

def force_vfr_metadata(file_path, status_label):
    if not os.path.exists(MKVPROPEDIT):
        print(f"[WARN] mkvpropedit not found. Skipping metadata edit.")
        return
    video_track_count = get_video_track_count(file_path) or 1
    cmd = [MKVPROPEDIT, "--parse-mode", "full", file_path]
    for i in range(1, video_track_count + 1):
        cmd.extend(["--edit", f"track:v{i}", "--delete", "default-duration"])
    print(f"{status_label}: Updating...          ", end="\r")
    sys.stdout.flush()
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        print(f"{status_label}: Done.          ")
    except subprocess.CalledProcessError as e:
        print(f"[WARN] Failed to edit properties: {e}")

def get_video_track_id(source_file):
    cmd = [MKVMERGE, "-J", source_file]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", check=True)
        data = json.loads(result.stdout)
        for track in data.get("tracks", []):
            if track.get("type") == "video":
                return track.get("id")
    except Exception as e:
        print(f"[WARN] Failed to get track ID: {e}")
    return None

def get_video_track_count(source_file):
    cmd = [MKVMERGE, "-J", source_file]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", check=True)
        data = json.loads(result.stdout)
        count = 0
        for track in data.get("tracks", []):
            if track.get("type") == "video":
                count += 1
        return count
    except Exception:
        return 1

def check_vfr_mediainfo(source_file):
    if not os.path.exists(MEDIAINFO):
        return False
    cmd = [MEDIAINFO, source_file]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", check=True)
        for line in result.stdout.splitlines():
            if "frame rate mode" in line.lower() and "variable" in line.lower():
                return True
    except Exception:
        pass
    return False

def mux_files(only_basename=None):
    """Mux the encoded video(s) in temp back together with their source tracks.

    Av1an writes "<name>-av1.mkv" straight into this folder rather than it being
    moved here after the encode. That is one hand-off fewer, but it also means an
    interrupted run can leave a partial file sitting here, so a caller that knows
    which name it just encoded passes it and only that file is touched. Called
    with no argument - by hand, or from condor-dispatch.py and AfterZone.py -
    every -av1.mkv in the folder is processed, exactly as before.
    """
    if only_basename:
        av1_files = [f"{only_basename}-av1.mkv"]
        if not os.path.exists(av1_files[0]):
            print(f"No '{av1_files[0]}' found in temp to mux.")
            return
    else:
        av1_files = glob.glob("*-av1.mkv")

    if not av1_files:
        print("No *-av1.mkv files found in temp to mux.")
        return

    print(f"Found {len(av1_files)} '-av1.mkv' files. Starting muxing process...\n")

    for av1_file in av1_files:
        base_name = av1_file.replace("-av1.mkv", "")

        # An empty file is an encode that died before mkvmerge wrote anything.
        # Muxing it would produce a valid-looking output with no video in it.
        if os.path.getsize(av1_file) == 0:
            print(f"[SKIP] {av1_file} is empty; the encode did not finish.")
            continue

        possible_sources = [
            os.path.join("..", "video-input", f"{base_name}.mkv"),
            os.path.join("..", "video-input", f"{base_name}.mp4"),
            os.path.join("..", "video-input", f"{base_name}.m2ts"),
            f"{base_name}.mkv",
            f"{base_name}-source.mkv"
        ]

        source_mkv = None
        for path in possible_sources:
            if os.path.exists(path):
                source_mkv = path
                break

        if not source_mkv:
            print(f"[SKIP] Source file not found for: {av1_file}")
            print(f"       Checked ../video-input/ and local temp folder.")
            continue

        # Intermediate filenames
        video_only_mkv = f"{base_name}-video.mkv"
        no_video_mkv = f"{base_name}-no-video.mkv"
        final_output = f"{base_name}-output.mkv"
        timestamp_file = f"{base_name}_timestamps.txt"

        try:
            is_vfr = check_vfr_mediainfo(source_mkv)
            total_steps = 5 if is_vfr else 3
            current_step = 1

            # 0. Extract Timestamps (if VFR)
            if is_vfr:
                print(f"\033[92mVariable framerate detected, applying timecodes...\033[0m")
                vid_track_id = get_video_track_id(source_mkv) or 0
                cmd_extract_ts = [MKVEXTRACT, source_mkv, "timestamps_v2", f"{vid_track_id}:{timestamp_file}"]
                run_command(cmd_extract_ts, f"[{base_name}] Step {current_step}/{total_steps} (Timecodes)")
                current_step += 1

            # Step 1: Remux AV1 to Video-Only MKV (Clean container)
            # UPDATED: Strictly disable audio/subs/chapters to prevent duplicates
            cmd_step1 = [
                MKVMERGE,
                "-o", video_only_mkv,
                "--no-audio",
                "--no-subtitles",
                "--no-chapters",
                "--no-attachments",
                "--no-global-tags",
                av1_file
            ]
            run_command(cmd_step1, f"[{base_name}] Step {current_step}/{total_steps} (Video Prep)")
            current_step += 1

            # Step 2: Remux Source to No-Video MKV (Audio/Subs)
            cmd_step2 = [MKVMERGE, "-o", no_video_mkv, "--no-video", source_mkv]
            run_command(cmd_step2, f"[{base_name}] Step {current_step}/{total_steps} (Audio Prep)")
            current_step += 1

            # Step 3: Merge Video-Only + No-Video to Final
            cmd_step3 = [MKVMERGE, "-o", final_output]

            # Apply timestamps to the video track in the final merge
            if is_vfr and os.path.exists(timestamp_file):
                # We are merging the freshly created video_only_mkv, track ID is likely 0
                vid_track_id_intermediate = get_video_track_id(video_only_mkv) or 0
                cmd_step3.extend(["--timestamps", f"{vid_track_id_intermediate}:{timestamp_file}"])

            cmd_step3.append(video_only_mkv)
            cmd_step3.append(no_video_mkv)
            run_command(cmd_step3, f"[{base_name}] Step {current_step}/{total_steps} (Final Merge)")
            current_step += 1

            # Step 4: VFR Metadata Fix
            if is_vfr:
                force_vfr_metadata(final_output, f"[{base_name}] Step {current_step}/{total_steps} (VFR Fix)")

            # Cleanup Temp Files
            for f in [video_only_mkv, no_video_mkv, timestamp_file]:
                if os.path.exists(f):
                    os.remove(f)

            # Delete Intermediate AV1 file if successful
            # Only once there is a real file to replace it with: this is the
            # only copy of the encode, since Av1an no longer leaves another one
            # next to the source.
            if os.path.exists(final_output) and os.path.getsize(final_output) > 0:
                print(f"Deleting intermediate file: {av1_file}")
                try:
                    os.remove(av1_file)
                except OSError as e:
                    print(f"[WARN] Failed to delete {av1_file}: {e}")
            else:
                print(f"[FAIL] No muxed output for {base_name}; keeping {av1_file}.")

        except subprocess.CalledProcessError:
            print(f"\n[FAIL] Could not process {base_name}. Skipping.")

if __name__ == "__main__":
    # Optional argument: mux only this basename, which is how av1an-dispatch.py
    # calls it. Without it, every -av1.mkv in the folder is processed as before.
    mux_files(sys.argv[1] if len(sys.argv) > 1 else None)