import os
import glob
import locale
import re
import subprocess
import sys
import tempfile
import shlex
import shutil
from xml.sax.saxutils import escape as xml_escape

# Runs in 'temp', tools are in ../tools
TOOLS_DIR = os.path.join("..", "tools")

# --- Encoding safety -------------------------------------------------------
# On Windows the "Language for non-Unicode programs" region setting decides the
# ANSI codepage (cp932 for Japanese, cp1251 for Russian, ...). Python's text
# mode follows that codepage, but the tools shipped here print UTF-8 and the
# .bat files may be saved in either. Decoding UTF-8 bytes as cp932 raises
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
    """Scans tools/ for the marker file."""
    pattern = os.path.join(TOOLS_DIR, "bat-used-*.txt")
    files = glob.glob(pattern)
    
    if not files:
        print("Error: No active batch marker found.")
        return None
    
    # Pick the newest marker if multiple exist
    marker_file = max(files, key=os.path.getctime)
    filename = os.path.basename(marker_file)
    batch_name = filename.replace("bat-used-", "").replace(".txt", "")
    
    if batch_name.lower().endswith(".bat"):
        batch_name = batch_name[:-4]
        
    return batch_name

def parse_av1an_batch(batch_name):
    """
    Parses the new batch format which defines av1an_settings.
    Handles 'set "VAR=VAL"' and 'set VAR=VAL' correctly.
    """
    batch_path = os.path.join("..", f"{batch_name}.bat")
    
    settings = {
        "av1an_settings": "",
        "FINAL_SPEED": "4",
        "CRF": "30",
        "QUALITY": "30",
        "PHOTON_NOISE": "0"
    }

    if not os.path.exists(batch_path):
        return settings

    try:
        # Read via read_text_file: a .bat re-saved by Notepad on a non-English
        # system is written in the ANSI codepage, not UTF-8. Decoding must not
        # throw here, or every setting silently falls back to its default.
        for line in read_text_file(batch_path).splitlines():
            line = line.strip()
            if not line.lower().startswith("set"):
                continue

            # Remove 'set ' prefix
            clean_line = re.sub(r"^set\s+", "", line, flags=re.IGNORECASE).strip()

            key = None
            val = None

            # Check for set "VAR=VAL" syntax (Starts with quote)
            if clean_line.startswith('"'):
                # Find the first equals sign
                if "=" in clean_line:
                    parts = clean_line.split("=", 1)
                    # Remove leading quote from key
                    key = parts[0].lstrip('"').strip()
                    val = parts[1].strip()
                    # Remove trailing quote from value if it exists (standard batch syntax)
                    if val.endswith('"'):
                        val = val[:-1]
            else:
                # Standard set VAR=VAL
                if "=" in clean_line:
                    parts = clean_line.split("=", 1)
                    key = parts[0].strip()
                    val = parts[1].strip()
                    # Handle set VAR="VAL" (explicit quotes in value)
                    if val.startswith('"') and val.endswith('"'):
                        val = val[1:-1]

            if key and key in settings:
                settings[key] = val
                if key == "CRF":
                    settings["QUALITY"] = val

    except Exception:
        pass
        
    return settings

def get_crf_string(quality):
    return f"--crf {quality}"

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
    
    mkvpropedit = shutil.which("mkvpropedit") or os.path.join(TOOLS_DIR, "MKVToolNix", "mkvpropedit")
    
    try:
        print(f"Applying tag to: {filepath}")
        code, out, err = run_capture([mkvpropedit, filepath, "--tags", "track:v1:" + tmp_path])
        if code != 0:
            message = (err.strip() or out.strip() or f"exit code {code}").split("\n")[0]
            print(f"Error tagging {filepath}: {message}")
        else:
            print("Success.")
    except Exception as e:
        print(f"Error tagging {filepath}: {e}")
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

def main():
    print("Tagging output files...")
    
    batch_name = get_active_batch_filename()
    if not batch_name: return 

    config = parse_av1an_batch(batch_name)
    svt_version = get_svt_av1_version()
    script_version = get_script_version()
    
    # Construct info string
    # Format: Auto-Boost-Av1an [Version] [Flags] SVT_AV1_Version... settings: "..."
    
    # 1. Prefix
    info_parts = [f"Auto-Boost-Av1an {script_version} Single Pass"]
    
    # 2. Photon Noise (if > 0)
    if config["PHOTON_NOISE"] and config["PHOTON_NOISE"] != "0":
        info_parts.append(f"--photon-noise {config['PHOTON_NOISE']}")
        
    # 3. SVT-AV1 version
    info_parts.append(svt_version)
    
    # 4. Settings Block
    settings_content = []
    settings_content.append(f"--preset {config['FINAL_SPEED']}")
    settings_content.append(get_crf_string(config.get('CRF') or config['QUALITY']))
    
    if config['av1an_settings']:
        clean_params = config['av1an_settings'].strip()
        # Safety check: strip external quotes if they somehow remain
        if len(clean_params) >= 2 and clean_params.startswith('"') and clean_params.endswith('"'):
            clean_params = clean_params[1:-1]
        settings_content.append(normalize_fgs_table_path(strip_untagged_options(clean_params)))

    combined_settings_str = " ".join(settings_content)
    
    # Final String Assembly
    full_string = " ".join(info_parts) + f' settings: "{combined_settings_str}"'
    
    print("-------------------------------------------------------------------------------")
    print(f"Scanned: {batch_name}.bat")
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