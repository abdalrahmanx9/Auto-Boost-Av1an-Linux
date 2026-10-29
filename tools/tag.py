import os
import glob
import locale
import re
import subprocess
import sys
import tempfile
import shlex
from xml.sax.saxutils import escape as xml_escape

# Since this script runs in 'temp' via dispatch, path to tools is ../tools
TOOLS_DIR = os.path.join("..", "tools")

# --- Encoding safety -------------------------------------------------------
# On Windows the "Language for non-Unicode programs" region setting decides the
# ANSI codepage (cp932 for Japanese, cp1251 for Russian, ...). Python's text
# mode follows that codepage, but the tools shipped here print UTF-8 and the
# .bat/config files may be saved in either. Decoding UTF-8 bytes as cp932 raises
# UnicodeDecodeError, which killed the tagging step. Everything below therefore
# reads bytes and decodes them defensively, so the script behaves identically
# no matter what the system codepage is.

def _preferred_encodings():
    """UTF-8 first, then whatever the system claims, then a never-fails fallback."""
    candidates = ["utf-8-sig", "utf-8"]
    for enc in (locale.getpreferredencoding(False), sys.getfilesystemencoding()):
        if enc and enc.lower().replace("_", "-") not in candidates:
            candidates.append(enc)
    return candidates


def decode_bytes(data):
    """Decode subprocess/file bytes without ever raising."""
    if data is None:
        return ""
    if isinstance(data, str):
        return data
    # Notepad's "Unicode" save produces UTF-16; only the BOM identifies it.
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        try:
            return data.decode("utf-16")
        except UnicodeDecodeError:
            pass
    for enc in _preferred_encodings():
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    # Plan B: latin-1 maps every byte, replace anything unprintable.
    return data.decode("latin-1", errors="replace")


def read_text_file(path):
    """Read a text file as UTF-8, falling back to the system codepage."""
    with open(path, "rb") as f:
        return decode_bytes(f.read())


def run_capture(cmd):
    """Run a command and return (returncode, stdout, stderr) as decoded text.

    Capturing bytes and decoding here (instead of using text=True) keeps the
    subprocess reader threads out of the ANSI codepage entirely.
    """
    result = subprocess.run(cmd, capture_output=True)
    return result.returncode, decode_bytes(result.stdout), decode_bytes(result.stderr)


def _make_console_lenient():
    """Stop prints from crashing when the console codepage can't encode a
    character (e.g. a Japanese filename on a cp1252 console, or a box-drawing
    character on cp932). Characters that don't fit are substituted, not fatal."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except Exception:
            pass


_make_console_lenient()

UNTAGGED_OPTIONS = {
    "--avx512": 0,
    "--arch": 1,
    "--denoise": 1,
    "--fast-speed": 1,
    "--verbose": 0,
    "--ssimu2-cpu-workers": 1,
    "--resume": 0,
}

def strip_untagged_options(params):
    """Remove operational helper options that should not be written to MKV tags."""
    if not params:
        return params
    params = params.strip()
    if len(params) >= 2 and params.startswith('"') and params.endswith('"'):
        params = params[1:-1]
    try:
        parts = shlex.split(params, posix=False)
    except ValueError:
        return params

    cleaned = []
    i = 0
    while i < len(parts):
        curr = parts[i]
        skip_count = UNTAGGED_OPTIONS.get(curr)
        if skip_count is not None:
            i += 1 + skip_count
            continue
        cleaned.append(curr)
        i += 1
    return " ".join(cleaned)


def normalize_fgs_table_path(params):
    """Reduce any '--fgs-table <path>' value to its bare filename so MKV tags
    stay portable and free of machine-specific absolute paths (the dispatch
    scripts expand the filename to an absolute path at encode time)."""
    if not params or "--fgs-table" not in params:
        return params

    pattern = re.compile(r'(--fgs-table[ \t]+)("([^"]*)"|\'([^\']*)\'|(\S+))')

    def _repl(match):
        raw = match.group(3) or match.group(4) or match.group(5) or ""
        if not raw:
            return match.group(0)
        filename = re.split(r"[\\/]", raw)[-1]
        if any(ch.isspace() for ch in filename):
            filename = f'"{filename}"'
        return match.group(1) + filename

    return pattern.sub(_repl, params)


def get_script_version():
    """Extracts the latest version number from Auto-Boost-Av1an.py."""
    script_path = os.path.join(TOOLS_DIR, "Auto-Boost-Av1an.py")
    version = "Unknown"
    if os.path.exists(script_path):
        try:
            content = read_text_file(script_path)
            # Looks for pattern like: ver_str = "v2.2"
            match = re.search(r'ver_str\s*=\s*["\'](v[0-9\.]+)["\']', content)
            if match:
                version = match.group(1)
        except Exception as e:
            pass
    return version

def get_svt_av1_version():
    """Executes SvtAv1EncApp.exe to get the precise version and formats it."""
    exe_path = os.path.join(TOOLS_DIR, "av1an", "SvtAv1EncApp")
    if not os.path.isfile(exe_path):
        exe_path = shutil.which("SvtAv1EncApp")
    if not os.path.exists(exe_path):
        return "SVT-AV1_Unknown"
        
    try:
        # Run the command and capture output
        _, out, err = run_capture([exe_path, "--version"])
        output = out.strip() or err.strip()

        if not output:
            return "SVT-AV1_Unknown"
            
        # Get first line and clean off the (release) tag
        line = output.split('\n')[0].strip()
        line = line.replace(" (release)", "").strip()
        
        # Format 1: svt-av1-psy 5fish fork
        if "SVT-AV1-PSY" in line and "5fish" in line:
            match = re.search(r"SVT-AV1-PSY \[5fish.*?\]\s+(v[0-9a-zA-Z\.\-]+)", line)
            if match:
                return f"svt-av1-psy 5fish fork {match.group(1)}"
            # Fallback if pattern slightly differs
            return re.sub(r"SVT-AV1-PSY \[5fish.*?\]", "svt-av1-psy 5fish fork", line).strip()
            
        # Format 2: SVT-AV1-Essential
        if "SVT-AV1-Essential" in line:
            match = re.search(r"(SVT-AV1-Essential)\s+(v[0-9\.]+)", line)
            if match:
                return f"{match.group(1)} {match.group(2)}"
                
        # Format 3: General Catch-all (e.g. SVT-AV1-HDR)
        return line

    except Exception as e:
        print(f"Error fetching SVT-AV1 version: {e}")
        return "SVT-AV1_Unknown"

def get_active_batch_filename():
    """Scans tools/ folder for the marker file created by the .bat script."""
    # Marker is now in TOOLS_DIR
    pattern = os.path.join(TOOLS_DIR, "bat-used-*.txt")
    files = glob.glob(pattern)
    
    if not files:
        print("Error: No active batch marker found in tools/. Cannot determine settings.")
        return None
    
    marker_file = files[0]
    filename = os.path.basename(marker_file)
    batch_name = filename.replace("bat-used-", "").replace(".txt", "")
    
    if batch_name.lower().endswith(".bat"):
        batch_name = batch_name[:-4]
    
    # Do NOT delete the marker yet, as other files in the loop might need it.
    # Cleanup.py should handle it, or we leave it.
    # If dispatch loops, we need this file to persist for all files.
    # Commenting out removal.
    # try:
    #     os.remove(marker_file)
    # except OSError:
    #     pass
        
    return batch_name

def get_dynamic_variables():
    vars_map = {
        "%WORKER_COUNT%": "Unknown",
        "%SSIMU2_TOOL%": "auto",
        "%SSIMU2_WORKERS%": "4"
    }
    
    wc_path = os.path.join(TOOLS_DIR, "workercount-config.txt")
    if os.path.exists(wc_path):
        try:
            for line in read_text_file(wc_path).splitlines():
                if "workers=" in line:
                    val = line.strip().split("=", 1)[1]
                    vars_map["%WORKER_COUNT%"] = val
        except Exception:
            pass

    ss_path = os.path.join(TOOLS_DIR, "workercount-ssimu2.txt")
    if os.path.exists(ss_path):
        try:
            for line in read_text_file(ss_path).splitlines():
                line = line.strip()
                if line.startswith("tool="):
                    vars_map["%SSIMU2_TOOL%"] = line.split("=", 1)[1]
                if line.startswith("workercount="):
                    vars_map["%SSIMU2_WORKERS%"] = line.split("=", 1)[1]
        except Exception:
            pass
            
    return vars_map

def parse_batch_line(line, vars_map):
    try:
        parts = shlex.split(line, posix=False)
    except ValueError:
        return [], "", "medium", None

    start_idx = -1
    for i, part in enumerate(parts):
        if "dispatch.py" in part or "Auto-Boost-Av1an.py" in part:
            start_idx = i
            break
            
    if start_idx == -1:
        return [], "", "medium", None

    raw_args = parts[start_idx+1:]
    general_flags = []
    final_params = ""
    crf = "medium"
    final_speed = None

    i = 0
    while i < len(raw_args):
        curr = raw_args[i]
        
        if curr in ["-i", "--scenes", "--workers"]:
            i += 2
            continue
        if curr in UNTAGGED_OPTIONS:
            i += 1 + UNTAGGED_OPTIONS[curr]
            continue
        if curr == "--fast-params":
            i += 2
            continue
        if curr == "--final-params":
            if i + 1 < len(raw_args):
                final_params = raw_args[i+1]
                i += 2
            else:
                i += 1
            continue
        if curr.startswith("-"):
            flag = curr
            val = None
            if i + 1 < len(raw_args):
                next_token = raw_args[i+1]
                if not next_token.startswith("-") or (next_token.startswith("-") and len(next_token) > 1 and next_token[1].isdigit()):
                    val = next_token
                    i += 2
                else:
                    i += 1
            else:
                i += 1
            if flag in ("--crf", "--quality"):
                if val:
                    crf = val
                continue
            if flag == "--final-speed" and val: final_speed = val
            if val: general_flags.append(f"{flag} {val}")
            else: general_flags.append(flag)
        else:
            i += 1

    return general_flags, final_params, crf, final_speed

def get_crf_string(crf):
    q = str(crf).lower().strip()
    if q == "high": return "--crf 25(variable)"
    if q == "low": return "--crf 35(variable)"
    if q == "medium": return "--crf 30(variable)"
    try:
        return f"--crf {q}(variable)"
    except ValueError:
        return "--crf 30(variable)"

def apply_tag_to_file(filepath, encoding_settings):
    # The encoding is declared explicitly so mkvpropedit reads the file as UTF-8
    # regardless of the system codepage, and &/</> are escaped so a stray
    # character in the parameters can't produce invalid XML.
    xml_template = f"""<?xml version="1.0" encoding="UTF-8"?>
<Tags>
  <Tag>
    <Targets>
      <TrackUID>1</TrackUID>
    </Targets>
    <Simple>
      <Name>Encoded_Library_Settings</Name>
      <String>{xml_escape(encoding_settings)}</String>
    </Simple>
  </Tag>
</Tags>
"""
    with tempfile.NamedTemporaryFile(delete=False, suffix=".xml", mode="w", encoding="utf-8") as tmp:
        tmp.write(xml_template)
        tmp_path = tmp.name
    
    # Path to mkvpropedit is in ../tools/MKVToolNix/
    mkvpropedit = shutil.which("mkvpropedit") or os.path.join(TOOLS_DIR, "MKVToolNix", "mkvpropedit")
    
    try:
        print(f"Applying tag to: {filepath}")
        code, out, err = run_capture([mkvpropedit, filepath, "--tags", "track:v1:" + tmp_path])
        if code != 0:
            message = (err.strip() or out.strip() or f"exit code {code}").split("\n")[0]
            print(f"Error tagging {filepath}: {message}")
        else:
            print("Success.")
    except FileNotFoundError:
        print(f"Error: mkvpropedit not found at {mkvpropedit}")
    except Exception as e:
        print(f"Error tagging {filepath}: {e}")
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

def main():
    print("Tagging output files...")
    
    batch_file = get_active_batch_filename()
    if not batch_file: return 

    cmd_line = ""
    batch_vars = {}
    
    # The batch file is in root, which is ..
    real_batch_path = os.path.join("..", f"{batch_file}.bat")
    
    if os.path.exists(real_batch_path):
        try:
            # Read via read_text_file: a .bat re-saved by Notepad on a non-English
            # system is written in the ANSI codepage, not UTF-8. Decoding must not
            # throw here, or tagging is abandoned for the whole encode.
            for line in read_text_file(real_batch_path).splitlines():
                strip = line.strip()
                if re.match(r'^set\s+', strip, re.IGNORECASE):
                    rest = re.sub(r'^set\s+', '', strip, flags=re.IGNORECASE).strip()
                    m = re.match(r'^"?([^"=]+)"?=(.*)', rest)
                    if m:
                        key = m.group(1).strip()
                        val = m.group(2).strip()
                        if rest.startswith('"') and val.endswith('"'):
                            val = val[:-1]
                        batch_vars[f"%{key}%"] = val

                if (not strip.lower().startswith("rem") and not strip.startswith("::")) and \
                   ("dispatch.py" in strip.lower() or "auto-boost-av1an.py" in strip.lower()):
                    cmd_line = strip
        except Exception as e:
            print(f"Error reading batch file: {e}")
            return
    else:
        print(f"Error: Could not find original batch file: {real_batch_path}")
        return

    vars_map = batch_vars.copy()
    dynamic_vars = get_dynamic_variables()
    vars_map.update(dynamic_vars) 

    if cmd_line:
        for key, val in vars_map.items():
            cmd_line = cmd_line.replace(key, val)

    general_flags, final_params, crf, final_speed = parse_batch_line(cmd_line, vars_map)
    script_version = get_script_version()
    svt_version = get_svt_av1_version()
    
    info_parts = [f"Auto-Boost-Av1an {script_version}"]
    info_parts.extend(general_flags)
    info_parts.append(svt_version)
    
    settings_content = []
    if final_speed: settings_content.append(f"--preset {final_speed}")
    settings_content.append(get_crf_string(crf))
    
    if final_params:
        clean_params = final_params.strip()
        if len(clean_params) >= 2 and clean_params.startswith('"') and clean_params.endswith('"'):
            clean_params = clean_params[1:-1]
        settings_content.append(normalize_fgs_table_path(strip_untagged_options(clean_params)))

    combined_settings_str = " ".join(settings_content)
    full_string = " ".join(info_parts) + f' settings: "{combined_settings_str}"'
    
    print("-------------------------------------------------------------------------------")
    print(f"Scanned: {batch_file}.bat")
    print(f"Generated Tag: \n{full_string}")
    print("-------------------------------------------------------------------------------")

    # Only tag files in temp that have been encoded
    found = False
    target_files = glob.glob("*-av1.mkv")
    
    for f in target_files:
        found = True
        apply_tag_to_file(f, full_string)
    
    if not found:
        print("No output MKV files found to tag.")

if __name__ == "__main__":
    # Plan B of last resort: tagging is cosmetic, so an unexpected failure here
    # (encoding or otherwise) must never abort the encode. Report it and let the
    # dispatcher carry on to muxing.
    try:
        main()
    except Exception as e:
        print(f"Tagging skipped: {type(e).__name__}: {e}")