import os
import glob
import locale
import re
import subprocess
import sys
import tempfile
import shutil
from xml.sax.saxutils import escape as xml_escape

# Runs in 'temp', tools are in ../tools
TOOLS_DIR = os.path.join("..", "tools")

# --- Encoding safety -------------------------------------------------------
# On Windows the "Language for non-Unicode programs" region setting decides the
# ANSI codepage (cp932 for Japanese, cp1251 for Russian, ...). Python's text
# mode follows that codepage, but the tools shipped here (condor.exe,
# SvtAv1EncApp.exe) print UTF-8, and this file itself is UTF-8. Decoding UTF-8
# bytes as cp932 raises UnicodeDecodeError, which killed the tagging step.
# Everything below therefore reads bytes and decodes them defensively, so the
# script behaves identically no matter what the system codepage is.

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

# Settings read out of the active condor .bat. These mirror the "set" lines that
# condor-builder.py writes, so the MKV tag describes the encode truthfully even
# after the user hand-edits the .bat.
BAT_SETTINGS_KEYS = (
    "condor_params",
    "FINAL_SPEED",
    "fork",
    "METRIC",
    "TARGET",
    "MIN_QUANTIZER",
    "MAX_QUANTIZER",
    "TARGET_PROFILE",
    "DECODER",
)

# Operational helpers that belong on the dispatcher's command line, not in a tag
# that is supposed to describe the encode.
UNTAGGED_OPTIONS = {
    "--avx512": 1,
    "--arch": 1,
    "--denoise": 1,
    "--tonemap": 1,
    "--autocrop": 0,
    "--verbose": 0,
    "--no-verbose": 0,
}


def strip_untagged_options(params):
    """Remove operational helper options that should not be written to MKV tags."""
    if not params:
        return params
    params = params.strip()
    if len(params) >= 2 and params.startswith('"') and params.endswith('"'):
        params = params[1:-1]

    parts = params.split()
    cleaned = []
    i = 0
    while i < len(parts):
        skip_count = UNTAGGED_OPTIONS.get(parts[i])
        if skip_count is not None:
            i += 1 + skip_count
            continue
        cleaned.append(parts[i])
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
        except Exception:
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
        _, out, err = run_capture([exe_path, "--version"])
        output = out.strip() or err.strip()
        if not output:
            return "SVT-AV1_Unknown"

        line = output.split("\n")[0].strip().replace(" (release)", "").strip()

        # Format 1: svt-av1-psy 5fish fork
        if "SVT-AV1-PSY" in line and "5fish" in line:
            match = re.search(r"SVT-AV1-PSY \[5fish.*?\]\s+(v[0-9a-zA-Z\.\-]+)", line)
            if match:
                return f"svt-av1-psy 5fish fork {match.group(1)}"
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


def parse_condor_batch(batch_name):
    """
    Parses the condor batch format.
    Handles 'set "VAR=VAL"' and 'set VAR=VAL' correctly.
    """
    batch_path = os.path.join("..", f"{batch_name}.bat")

    settings = {
        "condor_params": "",
        "FINAL_SPEED": "4",
        "fork": "essential",
        "METRIC": "ssimulacra2",
        "TARGET": "",
        "MIN_QUANTIZER": "",
        "MAX_QUANTIZER": "",
        "TARGET_PROFILE": "standard",
        "DECODER": "bestsource",
    }

    if not os.path.exists(batch_path):
        return settings

    # Case-insensitive lookup, because cmd.exe variables are case-insensitive.
    key_lookup = {key.lower(): key for key in BAT_SETTINGS_KEYS}

    try:
        # Read via read_text_file: a .bat re-saved by Notepad on a non-English
        # system is written in the ANSI codepage, not UTF-8. Decoding must not
        # throw here, or every setting silently falls back to its default.
        for line in read_text_file(batch_path).splitlines():
            line = line.strip()
            if not line.lower().startswith("set"):
                continue

            clean_line = re.sub(r"^set\s+", "", line, flags=re.IGNORECASE).strip()

            key = None
            val = None

            # Check for set "VAR=VAL" syntax (Starts with quote)
            if clean_line.startswith('"'):
                if "=" in clean_line:
                    parts = clean_line.split("=", 1)
                    key = parts[0].lstrip('"').strip()
                    val = parts[1].strip()
                    if val.endswith('"'):
                        val = val[:-1]
            else:
                # Standard set VAR=VAL
                if "=" in clean_line:
                    parts = clean_line.split("=", 1)
                    key = parts[0].strip()
                    val = parts[1].strip()
                    if val.startswith('"') and val.endswith('"'):
                        val = val[1:-1]

            if key and key.lower() in key_lookup:
                settings[key_lookup[key.lower()]] = val

    except Exception:
        pass

    return settings


def build_target_quality_string(config):
    """The Condor Target Quality settings, written outside the quoted encoder
    settings because they are Condor's, not SVT-AV1's.

    There is deliberately no --crf: Condor picks a quantizer per scene, so a
    single CRF in the tag would be a lie. The Target Quality settings that
    produced those quantizers are recorded instead.
    """
    parts = []

    metric = (config.get("METRIC") or "").strip()
    if metric:
        parts.append(f"--target-metric {metric}")

    target = (config.get("TARGET") or "").strip()
    if target:
        parts.append(f"--target {target}")

    min_q = (config.get("MIN_QUANTIZER") or "").strip()
    if min_q:
        parts.append(f"--min-crf {min_q}")

    max_q = (config.get("MAX_QUANTIZER") or "").strip()
    if max_q:
        parts.append(f"--max-crf {max_q}")

    profile = (config.get("TARGET_PROFILE") or "").strip()
    if profile:
        parts.append(f"--target-profile {profile}")

    return " ".join(parts)


def build_encoder_settings_string(config):
    """The SVT-AV1 settings, i.e. what goes inside settings: "...".

    Worker count, decoder threads and file paths are left out because they are
    machine-specific and say nothing about the resulting video.
    """
    parts = [f"--preset {(config.get('FINAL_SPEED') or '4').strip()}"]

    params = (config.get("condor_params") or "").strip()
    if len(params) >= 2 and params.startswith('"') and params.endswith('"'):
        params = params[1:-1]
    if params:
        parts.append(normalize_fgs_table_path(strip_untagged_options(params)))

    return " ".join(parts)


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
    if not batch_name:
        return

    config = parse_condor_batch(batch_name)
    script_version = get_script_version()
    svt_version = get_svt_av1_version()

    target_quality_str = build_target_quality_string(config)
    settings_str = build_encoder_settings_string(config)

    # Condor's Target Quality settings sit unquoted before the encoder version;
    # only the SVT-AV1 settings belong inside settings: "...".
    head = " ".join(
        part for part in (
            f"Auto-Boost-Av1an {script_version} Condor target quality",
            target_quality_str,
            svt_version,
        ) if part
    )
    full_string = f'{head} settings: "{settings_str}"'

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
