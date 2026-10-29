import os
import sys
import time
import subprocess
import threading
import queue
import json
import re
import shutil
from pathlib import Path
import psutil

# ================= CONFIGURATION =================
# Detect CPU threads and leave 1 free for system responsiveness
TOTAL_THREADS = psutil.cpu_count(logical=True)
PARALLELISM = max(1, TOTAL_THREADS - 1)

IGNORE_EXTS = set() 
LOSSLESS_EXTS = {'.flac', '.wav', '.thd', '.dtshd', '.pcm'}
# =================================================

# Global Queues and Locks
slot_status = ["Idle"] * PARALLELISM
files_queue = queue.Queue()
stop_display = threading.Event()

# Regex for FFMPEG progress parsing
re_ffmpeg = re.compile(r"time=\s*(\S+).*bitrate=\s*(\S+).*speed=\s*(\S+)")

# --- PATH SETUP (Relative to this script) ---
# Location: Auto-Boost-Av1an-portable/tools/audio-eac3.py
SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR = SCRIPT_DIR.parent 

import shutil as _sh
def _resolve(binary, portable_name):
    local = ROOT_DIR / "tools" / portable_name
    return local if local.exists() else Path(_sh.which(binary) or local)

FFMPEG_EXE = _resolve("ffmpeg", "av1an/ffmpeg")
FFPROBE_EXE = _resolve("ffprobe", "av1an/ffprobe")
MKV_DIR = ROOT_DIR / "tools" / "MKVToolNix"
MKVMERGE_EXE = _resolve("mkvmerge", "MKVToolNix/mkvmerge")
MKVEXTRACT_EXE = _resolve("mkvextract", "MKVToolNix/mkvextract")

# Settings file is now in 'audio-encoding', NOT 'extras'
SETTINGS_FILE = ROOT_DIR / "audio-encoding" / "settings-encode-eac3-audio.txt"

# Add MKVToolNix to PATH
os.environ["PATH"] += os.pathsep + str(MKV_DIR)

# --- HELPER FUNCTIONS ---

def load_settings():
    """Reads the settings file for bitrates. Returns a dictionary with defaults if missing."""
    defaults = {
        "Above 5.1": "640",
        "5.1": "448",
        "2.1": "320",
        "2.0": "224",
        "zlib-subtitles": "False"
    }

    if not SETTINGS_FILE.exists():
        print(f"Warning: Settings file not found at {SETTINGS_FILE}. Using defaults.")
        return defaults

    try:
        with open(SETTINGS_FILE, 'r') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, val = line.split("=", 1)
                defaults[key.strip()] = val.strip()
    except Exception as e:
        print(f"Error reading settings file: {e}. Using defaults.")
    
    return defaults

BITRATE_SETTINGS = load_settings()

def setting_is_enabled(key):
    """Reads a True/False style value from the settings file."""
    return str(BITRATE_SETTINGS.get(key, "")).strip().lower() in ("true", "yes", "1", "on")

# Matroska content compression for subtitle tracks. Off by default: it shrinks
# heavily-typeset ASS tracks a lot, but not every player supports compressed
# tracks (Jellyfin shows them as gibberish).
SUBTITLE_COMPRESSION = "zlib" if setting_is_enabled("zlib-subtitles") else "none"

def run_command(cmd, capture_output=False):
    """Run a subprocess command safely without bleeding stderr to console."""
    try:
        cmd_str = [str(c) for c in cmd]
        if capture_output:
            return subprocess.check_output(cmd_str, stderr=subprocess.DEVNULL, text=True, encoding='utf-8')
        subprocess.run(cmd_str, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        return True
    except Exception:
        return False

def run_with_progress(cmd):
    """Run a command (mkvextract/mkvmerge) and parse stdout for 'Progress: %' only."""
    try:
        process = subprocess.Popen(
            [str(c) for c in cmd],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1, 
            encoding='utf-8',
            errors='replace'
        )
        
        current_line = []
        while True:
            char = process.stdout.read(1)
            if not char and process.poll() is not None:
                break
            
            if char:
                if char in ('\r', '\n'):
                    line = "".join(current_line).strip()
                    if line.startswith("Progress:"):
                        sys.stdout.write(f"\r    {line}")
                        sys.stdout.flush()
                    current_line = []
                else:
                    current_line.append(char)
        
        print() 
        return process.returncode == 0
    except Exception as e:
        print(f"    Error during execution: {e}")
        return False

def get_track_title_string(lang_code):
    lookup = {
        "jpn": "Japanese", "eng": "English", "chi": "Chinese", "zho": "Chinese",
        "ger": "German", "deu": "German", "fra": "French", "fre": "French",
        "ita": "Italian", "spa": "Spanish", "kor": "Korean", "rus": "Russian",
        "por": "Portuguese", "hin": "Hindi", "und": ""
    }
    return lookup.get(lang_code.lower(), "")

def smart_truncate(text, max_len=55):
    """
    Truncates a string in the middle to preserve start and end.
    Example: 'VeryLongFilename_track1_eng.opus' -> 'VeryLongFil...track1_eng.opus'
    """
    if len(text) <= max_len:
        return text
    
    keep_end = 22
    keep_start = max_len - keep_end - 3 
    
    if keep_start < 1: 
        return text[:max_len] 
        
    return f"{text[:keep_start]}...{text[-keep_end:]}"

def get_user_choice():
    print("\n" + "="*50)
    print("      EAC3 AUDIO ENCODER - SELECTION MENU")
    print("="*50)
    print("1. Encode ONLY Lossless audio to EAC3 (Recommended)")
    print("   (Processing: FLAC, PCM/WAV, TrueHD, DTS-HD)")
    print("   (Preserves: AC3, AAC, DTS Core, Vorbis as-is)")
    print("\n2. Encode ALL audio tracks to EAC3")
    print("   (Warning: Causes generational loss on AC3/AAC/DTS)")
    print("="*50)
    
    while True:
        choice = input("\nEnter your choice (1 or 2): ").strip()
        if choice == '1':
            return 1
        elif choice == '2':
            return 2
        print("Invalid input. Please enter 1 or 2.")

def get_encoded_track_title_choice():
    print("\nUntouched audio tracks will carryover track titles.")
    print("For newly encoded audio tracks, you can copy over the original track titles")
    print("or leave newly encoded audio tracks without track titles.")
    print("You can use JMkvpropedit to edit multiple track titles across MKV files")
    print("after audio encoding is complete.")
    print("\nCopy original track titles to newly encoded audio tracks?")
    print("1: Yes")
    print("2: No")
    while True:
        choice = input("\nEnter your choice (1 or 2): ").strip()
        if choice == "1":
            return True
        if choice == "2":
            return False
        print("Invalid input. Please enter 1 or 2.")

# --- PHASE 1: EXTRACTION ---

def get_mkv_tracks(mkv_path):
    cmd = [MKVMERGE_EXE, '-J', str(mkv_path)]
    try:
        res = run_command(cmd, capture_output=True)
        data = json.loads(res)
        return [t for t in data.get('tracks', []) if t['type'] == 'audio']
    except:
        return []

def extract_tracks():
    mkvs = list(Path('.').glob('*.mkv'))
    if not mkvs:
        print("No .mkv files found in the current directory.")
        return []

    print(f"Found {len(mkvs)} MKV files. Analyzing tracks...")
    extracted_files = []
    
    for mkv in mkvs:
        tracks = get_mkv_tracks(mkv)
        extract_cmds = []
        
        for track in tracks:
            tid = track['id']
            lang = track['properties'].get('language', 'und')
            
            codec_id = track['properties'].get('codec_id', '')
            codec_name = track.get('codec', '') 
            full_codec_info = (codec_id + codec_name).upper()

            ext = ".unknown"
            if 'AAC' in full_codec_info: ext = '.aac'
            elif 'AC-3' in full_codec_info or 'E-AC-3' in full_codec_info: ext = '.ac3'
            elif 'DTS-HD' in full_codec_info: ext = '.dtshd'
            elif 'DTS' in full_codec_info: ext = '.dts'
            elif 'TRUEHD' in full_codec_info: ext = '.thd'
            elif 'FLAC' in full_codec_info: ext = '.flac'
            elif 'VORBIS' in full_codec_info: ext = '.ogg'
            elif 'OPUS' in full_codec_info: ext = '.opus'
            elif 'PCM' in full_codec_info: ext = '.wav'
            
            if ext in IGNORE_EXTS: continue

            out_name = f"{mkv.stem}_track{tid}_{lang}{ext}"
            
            if not Path(out_name).exists():
                extract_cmds.extend([f"{tid}:{out_name}"])
            
            extracted_files.append(Path(out_name))

        if extract_cmds:
            print(f"Extracting from {mkv.name}...")
            cmd = [MKVEXTRACT_EXE, 'tracks', str(mkv)] + extract_cmds
            run_with_progress(cmd)

    return extracted_files

# --- PHASE 2: DISPLAY ---

def display_loop():
    last_line_count = 0
    sys.stdout.write("\n") 

    while not stop_display.is_set():
        active_slots = [s for s in slot_status if s != "Idle"]
        current_line_count = len(active_slots)

        if last_line_count > 0:
            sys.stdout.write(f"\033[{last_line_count}A")
        
        for line in active_slots:
            clean_line = line[:110]
            sys.stdout.write(f"\r{clean_line}\033[K\n")
        
        if current_line_count < last_line_count:
            sys.stdout.write("\033[J")

        last_line_count = current_line_count
        sys.stdout.flush()
        time.sleep(0.1)

    if last_line_count > 0:
        sys.stdout.write(f"\033[{last_line_count}A")
        sys.stdout.write("\033[J")
    sys.stdout.flush()

# --- PHASE 3: WORKERS ---

def get_audio_channels(filepath):
    """
    Robustly attempts to determine audio channel count using ffprobe.
    Falls back to system ffprobe if portable one is missing.
    Defaults to '2' if all attempts fail.
    """
    exes_to_try = []
    
    if Path(FFPROBE_EXE).exists():
        exes_to_try.append(str(FFPROBE_EXE))
    
    if shutil.which("ffprobe"):
        exes_to_try.append("ffprobe")
    
    for exe in exes_to_try:
        cmd = [exe, '-v', 'error', '-select_streams', 'a:0', 
               '-show_entries', 'stream=channels', '-of', 'csv=p=0', str(filepath)]
        try:
            output = subprocess.check_output(cmd, stderr=subprocess.DEVNULL, text=True).strip()
            if output.isdigit():
                return output
        except Exception:
            continue

    return "2"

def worker_eac3(slot_id):
    while True:
        try:
            input_file = files_queue.get_nowait()
        except queue.Empty:
            break
        
        output_file = input_file.with_suffix('.eac3')
        fname = smart_truncate(input_file.name)
        
        slot_status[slot_id] = f"{slot_id+1}: [EAC3] {fname}.. Probing"
        channels = get_audio_channels(input_file)
        
        bitrate_val = BITRATE_SETTINGS.get("2.0", "224") # Default
        display_ch = f"{channels}ch"

        # Map nice display names
        channel_map = {
            "1": "1.0",
            "2": "2.0",
            "3": "2.1",
            "4": "4.0",
            "5": "5.0",
            "6": "5.1",
            "7": "6.1",
            "8": "7.1"
        }

        try:
            ch_int = int(channels)
            
            # Use mapped name if available
            if str(ch_int) in channel_map:
                display_ch = f"{channel_map[str(ch_int)]}ch"

            # Improved Bitrate Logic (EAC3)
            if ch_int > 6:
                bitrate_val = BITRATE_SETTINGS.get("Above 5.1", "640")
            elif ch_int >= 6:
                bitrate_val = BITRATE_SETTINGS.get("5.1", "448")
            elif ch_int >= 3:
                # New 2.1 Capability
                bitrate_val = BITRATE_SETTINGS.get("2.1", "320")
            else:
                bitrate_val = BITRATE_SETTINGS.get("2.0", "224")
        except:
            pass
        
        bitrate_str = f"{bitrate_val}k"

        # FFMPEG Command Setup
        cmd = [FFMPEG_EXE, '-y', '-i', str(input_file), '-c:a', 'eac3', 
               '-b:a', bitrate_str]
        
        status_suffix = ""

        # Mono -> Stereo Upmix Ability
        if channels == "1":
            cmd.extend(['-ac', '2'])
            status_suffix = " (Mono->Stereo)"

        cmd.append(str(output_file))

        slot_status[slot_id] = f"{slot_id+1}: [EAC3] {fname}.. Init ({display_ch} @ {bitrate_str}){status_suffix}"
        
        try:
            proc = subprocess.Popen(
                [str(c) for c in cmd], 
                stderr=subprocess.PIPE, 
                stdout=subprocess.DEVNULL, 
                text=True, 
                bufsize=1, 
                encoding='utf-8'
            )
            while True:
                chunk = proc.stderr.read(256)
                if not chunk and proc.poll() is not None:
                    break
                if chunk:
                    match = re_ffmpeg.search(chunk)
                    if match:
                        t, b, s = match.groups()
                        slot_status[slot_id] = f"{slot_id+1}: [EAC3] {fname}.. T:{t} Spd:{s}{status_suffix}"
        except Exception as e:
             slot_status[slot_id] = f"{slot_id+1}: [Err] {str(e)[:20]}"
             continue

        files_queue.task_done()
    slot_status[slot_id] = "Idle"

def run_phase(files, worker_func, name):
    if not files: return
    print(f"\n--- Starting {name} ({len(files)} files) ---")
    
    for f in files: files_queue.put(f)
    for i in range(PARALLELISM): slot_status[i] = "Idle" 

    stop_display.clear()
    d_thread = threading.Thread(target=display_loop, daemon=True)
    d_thread.start()
    
    threads = []
    for i in range(PARALLELISM):
        t = threading.Thread(target=worker_func, args=(i,))
        t.start()
        threads.append(t)
        
    files_queue.join()
    stop_display.set()
    d_thread.join()
    for t in threads: t.join()
    
    print(f"{name} Complete.")

# --- PHASE 4: MUXING ---

def get_track_delay_ms(mkv_path, track_id):
    temp_ts = Path(f"temp_delay_{mkv_path.stem}_{track_id}.txt")
    delay = 0
    try:
        cmd = [MKVEXTRACT_EXE, "timestamps_v2", str(mkv_path), f"{track_id}:{temp_ts}"]
        run_command(cmd)
        
        if temp_ts.exists():
            with open(temp_ts, 'r') as f:
                lines = f.readlines()
                for line in lines:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    try:
                        val = float(line)
                        delay = int(val)
                        break 
                    except:
                        pass
    except:
        pass
    finally:
        if temp_ts.exists():
            try: os.remove(temp_ts)
            except: pass
            
    return delay

def mux_final_files(copy_titles_for_encoded):
    current_dir = Path.cwd()
    output_dir = current_dir / "eac3-output"
    output_dir.mkdir(exist_ok=True)

    print(f"\n--- Starting Muxing Phase ---")
    files_processed = 0

    for mkv_path in current_dir.glob("*.mkv"):
        safe_stem = re.escape(mkv_path.stem)
        
        pattern = re.compile(rf"^{safe_stem}_track(\d+)_([a-zA-Z0-9]+)\.([a-zA-Z0-9]+)$")

        track_candidates = {}

        for f in current_dir.iterdir():
            match = pattern.match(f.name)
            if match:
                t_num = int(match.group(1))
                lang = match.group(2)
                ext = match.group(3).lower()
                
                if t_num not in track_candidates:
                    track_candidates[t_num] = {'lang': lang, 'eac3': None, 'orig': None}
                
                if ext == 'eac3':
                    track_candidates[t_num]['eac3'] = f
                else:
                    track_candidates[t_num]['orig'] = f

        if not track_candidates:
            print(f"Skipping {mkv_path.name} (No audio tracks identified)")
            continue

        output_file = output_dir / mkv_path.name
        print(f"Muxing: {mkv_path.name}")

        subtitle_flags = []
        audio_track_titles = {}
        audio_default_flags = {}
        try:
            cmd = [MKVMERGE_EXE, "-J", str(mkv_path)]
            res = run_command(cmd, capture_output=True)
            file_info = json.loads(res)
            for track in file_info.get("tracks", []):
                tid = track.get("id")
                if track.get("type") == "subtitles":
                    subtitle_flags.extend(["--compression", f"{tid}:{SUBTITLE_COMPRESSION}"])
                elif track.get("type") == "audio":
                    props = track.get("properties", {})
                    original_title = props.get("track_name") or props.get("name") or ""
                    audio_track_titles[tid] = original_title
                    original_default = props.get("default_track", props.get("flag_default", False))
                    audio_default_flags[tid] = "yes" if bool(original_default) else "no"
        except:
            pass

        cmd = [MKVMERGE_EXE, "-o", str(output_file)]
        cmd.extend(subtitle_flags) 
        cmd.append("--no-audio")
        cmd.append(str(mkv_path))

        sorted_ids = sorted(track_candidates.keys())
        
        for tid in sorted_ids:
            cand = track_candidates[tid]
            lang = cand['lang']
            
            final_path = cand['eac3'] if cand['eac3'] else cand['orig']
            
            if not final_path: continue

            original_title = audio_track_titles.get(tid, "")
            is_eac3 = bool(cand['eac3'] and cand['orig'])
            title_flag = original_title if (not is_eac3 or copy_titles_for_encoded) else ""
            
            delay_ms = get_track_delay_ms(mkv_path, tid)
            delay_str = ""
            if delay_ms != 0:
                delay_str = f" [Delay: {delay_ms}ms]"
                cmd.extend(["--sync", f"0:{delay_ms}"])

            display_str = "EAC3" if is_eac3 else f"Original ({final_path.suffix})"
            
            default_flag = audio_default_flags.get(tid, "no")
            title_display = title_flag if title_flag else "(no track title)"
            print(f"  + Track {tid}: {title_display} [{display_str}] [Default: {default_flag}]{delay_str}")
            
            cmd.extend(["--language", f"0:{lang}"])
            cmd.extend(["--default-track-flag", f"0:{default_flag}"])
            if title_flag:
                cmd.extend(["--track-name", f"0:{title_flag}"])
            cmd.append(str(final_path))
        
        if run_with_progress(cmd):
            files_processed += 1
        else:
            print("  > Error during muxing (check logs).")

    print(f"\nAll done. Processed {files_processed} videos into 'eac3-output'.")

# --- MAIN ENTRY ---

def main():
    try:
        mode = get_user_choice()
        copy_titles_for_encoded = get_encoded_track_title_choice()
        
        extracted = extract_tracks()
        
        to_encode_candidates = []

        if mode == 2:
            to_encode_candidates = extracted
        else:
            for f in extracted:
                if f.suffix in LOSSLESS_EXTS:
                    to_encode_candidates.append(f)

        to_eac3 = []
        
        print("\nPreparing files for EAC3 conversion...")
        for f in to_encode_candidates:
            if f.suffix == '.eac3':
                pass 
            else:
                to_eac3.append(f)
        
        if to_eac3:
            run_phase(to_eac3, worker_eac3, "Encoding to EAC3")

        mux_final_files(copy_titles_for_encoded)
        
    except KeyboardInterrupt:
        print("\n\nProcess interrupted by user. Exiting safely.")
        sys.exit(0)
    except Exception as e:
        print(f"\n\nAn unexpected error occurred: {e}")
        sys.exit(1)

if __name__ == "__main__":
    if os.name == 'nt': os.system('')
    main()