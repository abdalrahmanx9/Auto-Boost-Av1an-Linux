"""Interactive editor for the Encoded_Library_Settings MKV tag.

Walks the MKV files in video-output one by one, shows the encode settings tag
written by tag.py / av1an-tag.py, and offers it back as a pre-filled, editable
prompt line: arrow keys and backspace work as usual, Enter submits.

All other tags in the file (BPS, DURATION, statistics, other tracks) are read
back out with mkvextract and re-written unchanged, so only the settings string
is touched.
"""

import json
import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent.resolve()
TOOLS_DIR = BASE_DIR / "tools"
MKVTOOLNIX_DIR = TOOLS_DIR / "MKVToolNix"
VIDEO_OUTPUT_DIR = BASE_DIR / "video-output"

TAG_NAME = "Encoded_Library_Settings"
SEPARATOR = "-" * 79


def mkvtoolnix_binary(name):
    """Prefer the bundled MKVToolNix build, fall back to whatever is on PATH."""
    bundled = MKVTOOLNIX_DIR / f"{name}.exe"
    if not bundled.exists():
        bundled = MKVTOOLNIX_DIR / name
    if bundled.exists():
        return str(bundled)
    return name


MKVEXTRACT = mkvtoolnix_binary("mkvextract")
MKVMERGE = mkvtoolnix_binary("mkvmerge")
MKVPROPEDIT = mkvtoolnix_binary("mkvpropedit")


# --- pre-filled prompt ---------------------------------------------------

def _readline_prompt(prompt, default):
    """Pre-fill via readline (POSIX, or pyreadline3 if it happens to be there)."""
    try:
        import readline
    except ImportError:
        return None

    def hook():
        readline.insert_text(default)
        readline.redisplay()

    set_hook = getattr(readline, "set_pre_input_hook", None) or getattr(
        readline, "set_startup_hook", None
    )
    if set_hook is None:
        return None

    set_hook(hook)
    try:
        return input(prompt)
    finally:
        try:
            set_hook(None)
        except Exception:
            pass


def _stuff_console_input(text):
    """Push text into the Windows console input queue so the normal line editor
    picks it up as if it had been typed: the console handles arrows, backspace
    and wrapping for us."""
    import ctypes
    from ctypes import wintypes

    class CharUnion(ctypes.Union):
        _fields_ = [("UnicodeChar", wintypes.WCHAR), ("AsciiChar", ctypes.c_char)]

    class KeyEventRecord(ctypes.Structure):
        _fields_ = [
            ("bKeyDown", wintypes.BOOL),
            ("wRepeatCount", wintypes.WORD),
            ("wVirtualKeyCode", wintypes.WORD),
            ("wVirtualScanCode", wintypes.WORD),
            ("uChar", CharUnion),
            ("dwControlKeyState", wintypes.DWORD),
        ]

    class EventUnion(ctypes.Union):
        _fields_ = [("KeyEvent", KeyEventRecord)]

    class InputRecord(ctypes.Structure):
        _fields_ = [("EventType", wintypes.WORD), ("Event", EventUnion)]

    KEY_EVENT = 0x0001
    STD_INPUT_HANDLE = -10

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.GetStdHandle(STD_INPUT_HANDLE)
    if handle in (0, -1, None):
        return False

    records = (InputRecord * len(text))()
    for i, char in enumerate(text):
        record = records[i]
        record.EventType = KEY_EVENT
        record.Event.KeyEvent.bKeyDown = True
        record.Event.KeyEvent.wRepeatCount = 1
        record.Event.KeyEvent.wVirtualKeyCode = 0
        record.Event.KeyEvent.wVirtualScanCode = 0
        record.Event.KeyEvent.uChar.UnicodeChar = char
        record.Event.KeyEvent.dwControlKeyState = 0

    written = wintypes.DWORD(0)
    offset = 0
    # Written in chunks so a small input queue drains/grows instead of failing
    # outright on one huge write.
    while offset < len(text):
        chunk = min(64, len(text) - offset)
        ok = kernel32.WriteConsoleInputW(
            handle,
            ctypes.byref(records, ctypes.sizeof(InputRecord) * offset),
            chunk,
            ctypes.byref(written),
        )
        if not ok or written.value == 0:
            return offset > 0
        offset += written.value
    return True


def prompt_prefilled(prompt, default):
    """input() with the text field already filled out and ready for edits."""
    result = _readline_prompt(prompt, default)
    if result is not None:
        return result

    if os.name == "nt" and default:
        try:
            if _stuff_console_input(default):
                return input(prompt)
        except Exception:
            pass

    # Last resort: no editable pre-fill available on this console.
    if default:
        print("(pre-fill unavailable; copy/paste the line above to edit it)")
    return input(prompt)


def press_any_key(prompt="Press any key to exit . . . "):
    print(prompt, end="", flush=True)
    try:
        if os.name == "nt":
            import msvcrt

            msvcrt.getch()
        else:
            input()
    except (EOFError, KeyboardInterrupt, ImportError, OSError):
        pass
    print()


# --- tag reading / writing -----------------------------------------------

def run_tool(cmd):
    return subprocess.run(cmd, capture_output=True)


def read_tags(path):
    """Return the file's full tag XML root, or None when it carries no tags."""
    try:
        result = run_tool([MKVEXTRACT, str(path), "tags"])
    except FileNotFoundError:
        print(f"Error: mkvextract not found at {MKVEXTRACT}")
        return None

    if result.returncode != 0:
        message = (result.stderr or b"").decode("utf-8", errors="replace").strip()
        print(f"Error reading tags: {message}")
        return None

    xml_text = (result.stdout or b"").decode("utf-8-sig", errors="replace").strip()
    if not xml_text:
        return None

    try:
        return ET.fromstring(xml_text)
    except ET.ParseError as e:
        print(f"Error: could not parse the tag XML ({e}).")
        return None


def find_settings_element(root):
    """Locate the <Simple> holding Encoded_Library_Settings, if present."""
    if root is None:
        return None
    for simple in root.iter("Simple"):
        if (simple.findtext("Name") or "").strip() == TAG_NAME:
            return simple
    return None


def get_video_track_uid(path):
    try:
        result = run_tool([MKVMERGE, "-J", str(path)])
    except FileNotFoundError:
        return None
    if result.returncode != 0:
        return None
    try:
        info = json.loads((result.stdout or b"").decode("utf-8", errors="replace"))
    except ValueError:
        return None
    for track in info.get("tracks", []):
        if track.get("type") == "video":
            uid = track.get("properties", {}).get("uid")
            if uid is not None:
                return str(uid)
    return None


def set_settings_string(root, path, settings):
    """Insert or update the settings tag inside the extracted tag XML."""
    simple = find_settings_element(root)
    if simple is not None:
        string_el = simple.find("String")
        if string_el is None:
            string_el = ET.SubElement(simple, "String")
        string_el.text = settings
        return True

    uid = get_video_track_uid(path)
    target_tag = None
    if uid is not None:
        for tag in root.findall("Tag"):
            targets = tag.find("Targets")
            if targets is None:
                continue
            if any((el.text or "").strip() == uid for el in targets.findall("TrackUID")):
                target_tag = tag
                break

    if target_tag is None:
        target_tag = ET.SubElement(root, "Tag")
        targets = ET.SubElement(target_tag, "Targets")
        ET.SubElement(targets, "TrackUID").text = uid or "1"

    simple = ET.Element("Simple")
    ET.SubElement(simple, "Name").text = TAG_NAME
    ET.SubElement(simple, "String").text = settings
    # Keep the settings first, matching how tag.py writes a fresh file.
    target_tag.insert(1, simple)
    return True


def build_fresh_tags(settings):
    """Tag XML for files that carry no tags at all (targeted via track:v1)."""
    root = ET.Element("Tags")
    tag = ET.SubElement(root, "Tag")
    targets = ET.SubElement(tag, "Targets")
    ET.SubElement(targets, "TrackUID").text = "1"
    simple = ET.SubElement(tag, "Simple")
    ET.SubElement(simple, "Name").text = TAG_NAME
    ET.SubElement(simple, "String").text = settings
    return root


def write_tags(path, root, selector):
    tree = ET.ElementTree(root)
    try:
        ET.indent(tree, space="  ")
    except AttributeError:
        pass

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            delete=False, suffix=".xml", mode="wb"
        ) as tmp:
            tmp_path = tmp.name
            tree.write(tmp, encoding="utf-8", xml_declaration=True)

        result = run_tool([MKVPROPEDIT, str(path), "--tags", f"{selector}:{tmp_path}"])
        if result.returncode != 0:
            message = (result.stderr or result.stdout or b"").decode(
                "utf-8", errors="replace"
            ).strip()
            print(f"Error: mkvpropedit failed: {message}")
            return False
        return True
    except FileNotFoundError:
        print(f"Error: mkvpropedit not found at {MKVPROPEDIT}")
        return False
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)


# --- main loop -----------------------------------------------------------

def collect_files(argv):
    if argv:
        files = []
        for arg in argv:
            target = Path(arg.strip('"')).expanduser()
            if not target.is_absolute():
                target = (Path.cwd() / target).resolve()
            if target.is_dir():
                files.extend(sorted(target.glob("*.mkv")))
            elif target.is_file():
                files.append(target)
            else:
                print(f"Skipping missing path: {target}")
        return files

    if not VIDEO_OUTPUT_DIR.is_dir():
        print(f"Error: folder not found: {VIDEO_OUTPUT_DIR}")
        return []
    return sorted(VIDEO_OUTPUT_DIR.glob("*.mkv"))


def edit_file(path, index, total):
    print(SEPARATOR)
    print(f"[{index}/{total}] {path.name}")
    print(SEPARATOR)

    root = read_tags(path)
    simple = find_settings_element(root)
    current = ""
    if simple is not None:
        current = (simple.findtext("String") or "").strip()

    if current:
        print("Current encode settings tag:")
    else:
        print(f"No {TAG_NAME} tag found - enter one or press Enter to skip.")
    print(current or "(empty)")
    print()

    new_settings = prompt_prefilled("New settings: ", current).strip()
    print()

    if new_settings == current:
        print("Unchanged, nothing written.")
        return
    if not new_settings:
        print("Empty input, skipped.")
        return

    if root is None:
        ok = write_tags(path, build_fresh_tags(new_settings), "track:v1")
    else:
        set_settings_string(root, path, new_settings)
        ok = write_tags(path, root, "all")

    if ok:
        print("Updated:")
        print(new_settings)


def main():
    files = collect_files(sys.argv[1:])
    if not files:
        print("No MKV files found to edit.")
        press_any_key()
        return

    print(f"Editing encode settings tags for {len(files)} file(s).")
    print("Enter submits the line as shown; clear it to skip. Ctrl+C quits.")
    print()

    total = len(files)
    for index, path in enumerate(files, start=1):
        try:
            edit_file(path, index, total)
        except (KeyboardInterrupt, EOFError):
            print()
            print("Aborted.")
            return
        print()

    print(SEPARATOR)
    print("Done.")
    press_any_key()


if __name__ == "__main__":
    main()
