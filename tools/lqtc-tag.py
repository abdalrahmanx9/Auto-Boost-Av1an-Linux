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

# Settings read out of the active lqtc .bat. These mirror the "set" lines that
# lqtc-builder.py writes, so the MKV tag describes the encode truthfully even
# after the user hand-edits the .bat.
BAT_SETTINGS_KEYS = (
    "lqtc_params",
    "FORK",
    "BACKEND",
    "METRIC",
    "TARGET",
    "CRF_RANGE",
    "DISPLAY_FILE",
)


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


def svt_fork_tag_name(fork):
    """Fork name as it appears inside the [brackets] of the tag."""
    fork_key = (fork or "essential").strip().lower()
    if fork_key in ("svt-av1-essential", "essential"):
        return "svt-av1-essential"
    if fork_key in ("svt-av1-hdr", "hdr"):
        return "svt-av1-hdr"
    return (fork or "essential").strip() or "svt-av1-essential"


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


def parse_lqtc_batch(batch_name):
    """
    Parses the lqtc batch format.
    Handles 'set "VAR=VAL"' and 'set VAR=VAL' correctly.
    """
    batch_path = os.path.join("..", f"{batch_name}.bat")

    settings = {
        "lqtc_params": "",
        "FORK": "essential",
        "BACKEND": "cuda",
        "METRIC": "cvvdp",
        "TARGET": "",
        "CRF_RANGE": "",
        "DISPLAY_FILE": "display.json",
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

            # Remove 'set ' prefix
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
                    # Handle set VAR="VAL" (explicit quotes in value)
                    if val.startswith('"') and val.endswith('"'):
                        val = val[1:-1]

            if key and key.lower() in key_lookup:
                settings[key_lookup[key.lower()]] = val

    except Exception:
        pass

    return settings


def uses_cvvdp(settings):
    """CVVDP is what needs the -d display file in the tag.

    The .bat records METRIC, but the encoder actually picks the metric from the
    numeric target: below 8 is Butteraugli, 8-10 is CVVDP, above 10 is
    SSIMULACRA2. Trust the target when it parses, so a hand-edited TARGET still
    tags correctly.
    """
    target = (settings.get("TARGET") or "").strip()
    match = re.match(r"^\s*([0-9]*\.?[0-9]+)\s*-", target)
    if match:
        try:
            low = float(match.group(1))
            return 8.0 < low <= 10.0
        except ValueError:
            pass
    return (settings.get("METRIC") or "").strip().lower() == "cvvdp"


def build_settings_string(config):
    """Rebuild the LQTC command line that describes this encode.

    Deliberately omits the parts that are machine-specific or constant:
    the executable, input/output paths, -w (worker count), -e svt-av1,
    -v (metric workers) and -m (metric stat).
    """
    parts = []

    target = (config.get("TARGET") or "").strip()
    if target:
        parts.append(f"-t {target}")

    if uses_cvvdp(config):
        # Bare filename only: dispatch passes an absolute path, which would
        # bake a machine-specific location into the tag.
        display_file = (config.get("DISPLAY_FILE") or "display.json").strip().strip('"')
        display_file = re.split(r"[\\/]", display_file)[-1] or "display.json"
        parts.append(f"-d {display_file}")

    crf_range = (config.get("CRF_RANGE") or "").strip()
    if crf_range:
        parts.append(f"-f {crf_range}")

    parts.append("--keep")

    params = (config.get("lqtc_params") or "").strip()
    if len(params) >= 2 and params.startswith('"') and params.endswith('"'):
        params = params[1:-1]
    # The encoder params are quoted as a group. The closing quote is supplied by
    # the quote that closes the whole settings block in main().
    parts.append(f'-p "{params}')

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

    config = parse_lqtc_batch(batch_name)
    script_version = get_script_version()
    fork_name = svt_fork_tag_name(config.get("FORK"))

    settings_str = build_settings_string(config)

    full_string = (
        f"Auto-Boost-Av1an {script_version} quality target mode "
        f'[{fork_name}] settings: "{settings_str}"'
    )

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
