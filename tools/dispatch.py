# Plugin-load chatter from VapourSynth children (API3 deprecation notices,
# duplicate plugin DLLs) is dropped back out of any output relayed below.
try:
    import sys as _quiet_sys, os as _quiet_os
    _quiet_dir = _quiet_os.path.dirname(_quiet_os.path.abspath(__file__))
    if _quiet_dir not in _quiet_sys.path:
        _quiet_sys.path.insert(0, _quiet_dir)
    from vs_quiet import is_plugin_noise as _is_plugin_noise
except Exception:
    def _is_plugin_noise(line):
        return False

import sys
import subprocess
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import glob
import re
import shutil
import threading
import time
import collections
import urllib.parse
import urllib.request
import unicodedata

try:
    from wakepy import keep
except Exception:  # pragma: no cover - wakepy ships with the package
    import contextlib

    class _KeepFallback:
        @staticmethod
        def running():
            return contextlib.nullcontext()

    keep = _KeepFallback()

from svt_fork_setup import setup_svt_av1_fork
import source_filter

BLUE = "\033[94m"
RED = "\033[91m"
RESET = "\033[0m"

DISPLAY_USERNAME = "av1enjoyer"
_WINDOWS_USER_PATH_RE = re.compile(
    r"([\\/]+users[\\/]+)[A-Za-z0-9._-]+",
    re.IGNORECASE,
)
_LINUX_USER_PATH_RE = re.compile(r"(/home/)[A-Za-z0-9._-]+(/)")


def anonymize_user_paths(text):
    """Hide the real profile name in user-facing console output."""
    if not isinstance(text, str):
        return text
    text = _LINUX_USER_PATH_RE.sub(r"\g<1>" + DISPLAY_USERNAME + r"\g<2>", text)
    return _WINDOWS_USER_PATH_RE.sub(
        lambda match: f"{match.group(1)}{DISPLAY_USERNAME}",
        text,
    )


class AnonymizedTextStream:
    """Proxy a text stream while anonymizing C:\\Users\\<name> paths."""

    def __init__(self, stream):
        self._stream = stream

    def write(self, text):
        return self._stream.write(anonymize_user_paths(text))

    def writelines(self, lines):
        return self._stream.writelines(anonymize_user_paths(line) for line in lines)

    def __getattr__(self, name):
        return getattr(self._stream, name)


sys.stdout = AnonymizedTextStream(sys.stdout)
sys.stderr = AnonymizedTextStream(sys.stderr)


def format_elapsed_hhmmss(seconds):
    """Format elapsed seconds as hh:mm:ss, allowing totals longer than 24 hours."""
    total_seconds = max(0, int(float(seconds) + 0.5))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def print_dispatch_timing_report(report):
    print("\n" + "-" * 80)
    print(f"Time report for: {report['filename']}")
    print("Time format legend: hh:mm:ss = hours:minutes:seconds")
    print(f"Scene detection:   {format_elapsed_hhmmss(report['scene_detection'])}")
    print(f"Visual metrics:    {format_elapsed_hhmmss(report['visual_metrics'])}")
    print(f"Encoding:          {format_elapsed_hhmmss(report['encoding'])}")
    print(f"Output MKV muxing: {format_elapsed_hhmmss(report['muxing'])}")
    print(f"Total time: {format_elapsed_hhmmss(report['total'])}")
    print("-" * 80)


class StageTimingMonitor:
    """Observe Auto-Boost's stage file without changing its normal one-process flow."""

    def __init__(self, stage_file):
        self.stage_file = stage_file
        self.transitions = []
        self._stop_event = threading.Event()
        self._last_signature = None
        self.initial_stage = self._read_stage()[1]
        initial = self._read_stage()
        self._last_signature = initial[0]
        self._thread = threading.Thread(target=self._run, name="auto-boost-stage-timer", daemon=True)

    def _read_stage(self):
        try:
            stat = os.stat(self.stage_file)
            with open(self.stage_file, "r", encoding="utf-8", errors="replace") as f:
                value = int(f.readline().strip())
            return (stat.st_mtime_ns, stat.st_size, value), value
        except (OSError, ValueError):
            return None, None

    def _sample(self):
        signature, stage = self._read_stage()
        if signature is None:
            self._last_signature = None
            return
        if signature != self._last_signature:
            self.transitions.append((stage, time.monotonic()))
            self._last_signature = signature

    def _run(self):
        while not self._stop_event.wait(0.02):
            self._sample()

    def start(self):
        self._thread.start()

    def stop(self):
        self._sample()
        self._stop_event.set()
        self._thread.join(timeout=1)
        self._sample()


# How long to wait before each attempt at muxing. The first is immediate;
# a file the encoder only just closed is sometimes still held for a moment
# by antivirus or a slow drive, and a short pause is enough to clear it.
MUX_RETRY_WAITS = (0, 5, 10)


def encode_finished_on_disk(av1_path, stage_path, started_at):
    """True when the encode really finished, whatever the exit code said.

    Auto-Boost-Av1an.py has been seen returning a non-zero code after it has
    already printed 'Auto-boost complete!' - a crash while the interpreter
    tears down VapourSynth, long after every file is safely written.
    Believing the exit code there throws away a finished encode, so ask the
    disk instead: a non-empty <name>-av1.mkv that this run produced, plus a
    stage marker reading 5 ('final encode finished').
    """
    try:
        if os.path.getsize(av1_path) <= 0:
            return False
        # A leftover from an earlier run says nothing about this one. The
        # slack absorbs filesystem timestamp granularity.
        if os.path.getmtime(av1_path) < started_at - 2:
            return False
    except OSError:
        return False
    try:
        with open(stage_path, 'r', encoding='utf-8', errors='replace') as f:
            return f.read().strip() == '5'
    except OSError:
        return False


def summarize_auto_boost_stage_timings(started_at, ended_at, transitions, initial_stage=1):
    """Return visual-metric and encoding seconds from Auto-Boost stage transitions.

    Stage 1 is the fast encode, stage 2 calculates visual metrics, stage 3 builds
    zones, and stage 4 is the final encode. Encoding combines stages 1 and 4.
    """
    current_stage = initial_stage if initial_stage in (1, 2, 3, 4) else 1
    cursor = started_at
    visual_metrics = 0.0
    encoding = 0.0

    for next_stage, transition_at in sorted(transitions, key=lambda item: item[1]):
        transition_at = min(ended_at, max(cursor, transition_at))
        elapsed = transition_at - cursor
        if current_stage in (1, 4):
            encoding += elapsed
        elif current_stage == 2:
            visual_metrics += elapsed
        current_stage = next_stage
        cursor = transition_at

    remaining = max(0.0, ended_at - cursor)
    if current_stage in (1, 4):
        encoding += remaining
    elif current_stage == 2:
        visual_metrics += remaining
    return visual_metrics, encoding

def scene_detection_env():
    """Environment for scene detection subprocesses.

    Forces unbuffered Python output so live progress lines from
    Progressive-Scene-Detection.py are visible in the parent console.
    """
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["AUTOBOOST_SCENE_X264_PROGRESS"] = "1"
    return env

# =========================================================================
# Simple-mode (non --verbose) progress display
#
# When the generated .bat does not pass --verbose, the noisy phases of the
# workflow are shown as Auto-Boost-Essential style progress bars with a
# short beginner-friendly explanation underneath. The explanation for a
# phase disappears as soon as that phase finishes.
# =========================================================================

SIMPLE_EXPLANATION_SCENE_DETECTION = (
    "Using VapourSynth and x264 for scene detection to break the video into chunks for\n"
    "visual metrics quality measuring and parallel encoding for increased encoding speed."
)
SIMPLE_EXPLANATION_MUXING = (
    "Packaging the finished video together with the original audio, subtitles, chapters,\n"
    "etc, into your final MKV file in the video-output folder."
)

# All simple-mode bars pad their description to this width so every progress
# bar in the workflow starts at the same column and stays aligned.
SIMPLE_DESC_WIDTH = 42


def simple_description(text):
    return "[green]" + text.ljust(SIMPLE_DESC_WIDTH)


try:
    from rich.console import Console, Group
    from rich.live import Live
    from rich.text import Text
    from rich.progress import (
        Progress,
        ProgressColumn,
        TextColumn,
        BarColumn,
        SpinnerColumn,
        TimeElapsedColumn,
        TimeRemainingColumn,
    )

    class SimpleFPSColumn(ProgressColumn):
        """fps readout on the right side of the bar (default color), fed via
        task.fields['fps']. Renders fixed-width so bars stay aligned."""

        def render(self, task):
            fps = task.fields.get("fps")
            if fps is None:
                return Text(" " * 12)
            return Text(f"{fps:>8.2f} fps")

    class PlainTimeElapsedColumn(TimeElapsedColumn):
        """Elapsed time in the default color instead of rich's cyan."""

        def render(self, task):
            text = super().render(task)
            text.style = ""
            return text

    class PlainTimeRemainingColumn(TimeRemainingColumn):
        """Remaining time in the default color instead of rich's cyan."""

        def render(self, task):
            text = super().render(task)
            text.style = ""
            return text

    class PhaseRenderable:
        """A progress display plus an explanation line that can be hidden."""

        def __init__(self, progress, explanation):
            self.progress = progress
            self.explanation = explanation
            self.show_explanation = explanation is not None

        def __rich__(self):
            if self.show_explanation:
                return Group(self.progress, Text(self.explanation, style="dim"))
            return self.progress

    RICH_AVAILABLE = True
except ImportError:
    # Without rich we cannot draw the simple interface; every phase falls
    # back to the verbose behaviour instead of failing.
    RICH_AVAILABLE = False

ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
SCENE_PROGRESS_RE = re.compile(
    r"Frame\s+(\d+)\s*/\s*([\d.]+)%\s*/\s*(VapourSynth|x264) based scene detection"
    r"(?:\s+\w+)?\s*/\s*([\d.]+)\s*fps"
)
MUX_PROGRESS_RE = re.compile(r"[Pp]rogress:\s*(\d+)\s*%")


def essential_style_progress(console):
    """Progress bar with the same appearance Auto-Boost-Essential uses.
    Percentage, fps and time readouts use the default (white) color, and the
    fps column is fixed-width so all bars in the workflow align."""
    return Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        "{task.percentage:>3.0f}%",
        SimpleFPSColumn(),
        PlainTimeElapsedColumn(),
        PlainTimeRemainingColumn(),
        console=console,
    )


def stream_subprocess_lines(proc, on_line):
    """Read a subprocess's merged output, splitting on \r and \n so live
    carriage-return progress updates are seen as individual lines."""
    buf = b""
    stream = proc.stdout
    while True:
        chunk = stream.read1(4096) if hasattr(stream, "read1") else stream.read(1)
        if not chunk:
            break
        buf += chunk
        parts = re.split(b"[\r\n]", buf)
        buf = parts.pop()
        for raw in parts:
            if not raw:
                continue
            line = ANSI_ESCAPE_RE.sub("", raw.decode("utf-8", "replace")).strip()
            if line and not _is_plugin_noise(line):
                on_line(line)
    if buf:
        line = ANSI_ESCAPE_RE.sub("", buf.decode("utf-8", "replace")).strip()
        if line and not _is_plugin_noise(line):
            on_line(line)


def print_captured_tail(label, lines):
    """Show the tail of a quiet subprocess's output after a failure."""
    lines = [line for line in lines if not _is_plugin_noise(line)]
    if not lines:
        return
    print(f"{RED}[Dispatch] {label} (last {len(lines)} output lines):{RESET}")
    for line in lines:
        print(f"  {line}")


def run_scene_detection_simple(cmd, cwd, env):
    """Run Progressive-Scene-Detection.py behind two Essential-style progress
    bars (VapourSynth + x264) with a short explanation underneath.

    Returns the subprocess return code."""
    console = Console()
    progress = essential_style_progress(console)
    vs_task = progress.add_task(simple_description("VapourSynth scene detection"),
                                total=100.0, fps=None)
    x264_task = progress.add_task(simple_description("x264 scene detection"),
                                  total=100.0, fps=None)
    renderable = PhaseRenderable(progress, SIMPLE_EXPLANATION_SCENE_DETECTION)
    other_lines = collections.deque(maxlen=40)

    def on_line(line):
        match = SCENE_PROGRESS_RE.search(line)
        if match:
            percent = min(100.0, float(match.group(2)))
            fps = float(match.group(4))
            task = vs_task if match.group(3) == "VapourSynth" else x264_task
            progress.update(task, completed=percent, fps=fps)
        else:
            other_lines.append(line)

    proc = subprocess.Popen(cmd, cwd=cwd, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        with Live(renderable, console=console, refresh_per_second=8) as live:
            try:
                stream_subprocess_lines(proc, on_line)
                returncode = proc.wait()
                if returncode == 0:
                    progress.update(vs_task, completed=100.0)
                    progress.update(x264_task, completed=100.0)
            finally:
                if proc.poll() is None:
                    proc.terminate()
                renderable.show_explanation = False
                live.refresh()
    except KeyboardInterrupt:
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
        raise
    if returncode != 0:
        print_captured_tail("Scene detection failed", list(other_lines))
    return returncode


def run_with_mux_progress(cmd, cwd, description):
    """Run a muxing helper behind a single Essential-style progress bar,
    driven by mkvmerge's 'Progress: N%' output.

    Returns the subprocess return code."""
    console = Console()
    progress = essential_style_progress(console)
    task = progress.add_task(simple_description(description), total=100.0, fps=None)
    renderable = PhaseRenderable(progress, SIMPLE_EXPLANATION_MUXING)
    other_lines = collections.deque(maxlen=40)

    def on_line(line):
        match = MUX_PROGRESS_RE.search(line)
        if match:
            progress.update(task, completed=min(100.0, float(match.group(1))))
        else:
            other_lines.append(line)

    proc = subprocess.Popen(cmd, cwd=cwd,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        with Live(renderable, console=console, refresh_per_second=8) as live:
            try:
                stream_subprocess_lines(proc, on_line)
                returncode = proc.wait()
                if returncode == 0:
                    progress.update(task, completed=100.0)
            finally:
                if proc.poll() is None:
                    proc.terminate()
                renderable.show_explanation = False
                live.refresh()
    except KeyboardInterrupt:
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
        raise
    if returncode != 0:
        print_captured_tail("Muxing output", list(other_lines))
    return returncode


def run_quiet(cmd, cwd, label):
    """Run a helper silently; on failure print the tail of its output.

    Returns the subprocess return code."""
    proc = subprocess.run(cmd, cwd=cwd,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if proc.returncode != 0 and proc.stdout:
        lines = [ANSI_ESCAPE_RE.sub("", raw).rstrip() for raw in
                 proc.stdout.decode("utf-8", "replace").splitlines() if raw.strip()]
        print_captured_tail(label, lines[-40:])
    return proc.returncode


def parse_settings_lines(lines):
    """Parse settings.txt lines into a case-insensitive key/value dict."""
    settings = {}
    for line in lines:
        line = line.strip()
        if not line or line.startswith(("#", ";", "[")) or "=" not in line:
            continue
        key, value = line.split("=", 1)
        settings[key.strip().lower()] = value.strip()
    return settings


def load_script_settings(settings_path):
    """Read settings.txt once into a case-insensitive key/value dict."""
    if not os.path.exists(settings_path):
        return {}
    try:
        with open(settings_path, "r", encoding="utf-8", errors="replace") as f:
            return parse_settings_lines(f.read().splitlines())
    except Exception:
        return {}

def set_settings_value(settings_path, key, value):
    """Set key=value in settings.txt, preserving the rest of the file."""
    key_l = key.lower()
    lines = []
    found = False
    if os.path.exists(settings_path):
        with open(settings_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", ";", "[")) or "=" not in line:
            continue
        k, _ = line.split("=", 1)
        if k.strip().lower() == key_l:
            lines[idx] = f"{k.strip()}={value}"
            found = True
            break
    if not found:
        lines.append(f"{key}={value}")
    with open(settings_path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")

# --- Optimized worker settings (read from the generating script file) ---
# Matches Windows 'set KEY=VALUE' lines and shell 'KEY=VALUE' / 'KEY="VALUE"'.
_BAT_SET_RE = re.compile(r'^(?:set\s+)?"?([A-Za-z_][A-Za-z0-9_-]*)\s*=\s*(.*?)"?\s*$', re.IGNORECASE)


def find_active_bat_file(tools_dir, root_dir):
    """Locate the .bat that launched this run via the tools/bat-used-*.txt marker."""
    markers = glob.glob(os.path.join(tools_dir, "bat-used-*.txt"))
    markers.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    for marker in markers:
        name = os.path.basename(marker)
        if not (name.startswith("bat-used-") and name.endswith(".txt")):
            continue
        bat_name = name[len("bat-used-"):-len(".txt")]
        bat_path = os.path.join(root_dir, bat_name)
        if os.path.isfile(bat_path):
            return bat_path
    return None


def read_bat_optimize_settings(tools_dir, root_dir):
    """Parse optimize-workers, custom av1an workers, and custom SSIMU2
    tool/worker settings from the active .bat. Returns an empty dict when the settings are absent, so
    callers proceed exactly as before."""
    bat_path = find_active_bat_file(tools_dir, root_dir)
    if not bat_path:
        return {}
    wanted = {"optimize-workers", "custom-av1an-workers", "custom-ssim2-workers", "custom-ssim2-tool"}
    settings = {}
    try:
        with open(bat_path, "r", encoding="utf-8", errors="replace") as f:
            for raw_line in f.read().splitlines():
                line = raw_line.strip()
                if not line or line.startswith("::") or line.startswith("#") or line.lower().startswith("rem "):
                    continue
                m = _BAT_SET_RE.match(line)
                key = value = None
                if m:
                    key, value = m.group(1).lower(), m.group(2).strip()
                elif "=" in line and " " not in line.split("=", 1)[0] and "%" not in line.split("=", 1)[0]:
                    k, v = line.split("=", 1)
                    key, value = k.strip().lower(), v.strip()
                if key is not None:
                    # Generated .sh scripts use bash-safe underscores
                    key = key.replace("_", "-")
                if key in wanted:
                    settings[key] = value
    except Exception:
        return {}
    return settings


def read_and_repair_workercount_config(tools_dir):
    """Read tools\\workercount-config.txt tolerantly and repair it in place.

    Users hand-edit this file, and Notepad likes to re-save it as UTF-16 or
    UTF-8-with-BOM. Strict UTF-8 readers downstream (the Rust tools) then
    fail with "stream did not contain valid UTF-8", and the .bat reads
    garbage for the worker value. This reader accepts any common Windows
    encoding, extracts the worker count, and - if the file was malformed -
    rewrites it as plain ASCII 'workers=N'. Returns the worker count int,
    or None if the file does not exist.

    A cputarget= line (the CPU build the builders remember) is carried through
    the repair untouched and kept above workers=, since the .bat files read
    this file with a last-line-wins for/f loop. Nothing here interprets it -
    the encoder arch comes from the .bat's own ARCH value."""
    path = os.path.join(tools_dir, "workercount-config.txt")
    try:
        with open(path, "rb") as f:
            blob = f.read()
    except OSError:
        return None
    text = None
    for enc in ("utf-8-sig", "utf-16", "utf-16-le", "utf-16-be"):
        try:
            text = blob.decode(enc)
            break
        except (UnicodeDecodeError, UnicodeError):
            continue
    if text is None:
        text = blob.decode("latin-1", errors="replace")
    text = text.replace("\ufeff", "").replace("\x00", "")
    cpu = re.search(r"^\s*cputarget\s*=\s*([A-Za-z0-9_.-]+)", text,
                    re.IGNORECASE | re.MULTILINE)
    cpu_line = f"cputarget={cpu.group(1).strip()}\n" if cpu else ""
    m = re.search(r"workers\s*=\s*(\d+)", text, re.IGNORECASE)
    if not m:
        # Tolerate a file that is just a bare number
        m = re.search(r"^\s*(\d+)\s*$", text, re.MULTILINE)
    if m:
        workers = max(1, int(m.group(1)))
        broken = False
    else:
        workers = 1  # unreadable/garbage: safe default
        broken = True
    # Rewrite only if the on-disk bytes are not already the clean canonical
    # form (either newline style counts as clean).
    canonical = {f"{cpu_line}workers={workers}\n".encode("ascii"),
                 f"{cpu_line}workers={workers}\r\n".encode("ascii"),
                 cpu_line.replace("\n", "\r\n").encode("ascii")
                 + f"workers={workers}\r\n".encode("ascii")}
    if blob not in canonical:
        try:
            with open(path, "w", encoding="ascii") as f:
                f.write(f"{cpu_line}workers={workers}\n")
            reason = ("no readable worker count found - reset to the safe default"
                      if broken else "re-saved as plain text (was UTF-16/BOM/malformed)")
            print(f"\033[93m[Dispatch] Repaired workercount-config.txt -> "
                  f"workers={workers} ({reason})\033[0m")
        except OSError:
            pass
    return workers


def sanitize_worker_value(raw, fallback):
    """Return a clean positive-integer worker string from a possibly mangled
    value (BOM/null/UTF-16 junk leaking out of a hand-edited config file via
    the .bat). Returns `fallback` when no digits can be recovered."""
    if raw is None:
        return fallback
    cleaned = str(raw).replace("\ufeff", "").replace("\x00", "").strip()
    m = re.search(r"\d+", cleaned)
    if not m:
        return fallback
    return str(max(1, int(m.group(0))))


def svt_fork_display_name(fork):
    fork_key = (fork or "essential").strip().lower()
    if fork_key in ("svt-av1-essential", "essential"):
        return "Essential"
    if fork_key in ("svt-av1-hdr", "hdr"):
        return "HDR"
    if fork_key in ("5fish", "svt-av1-psy", "psy"):
        return "psy 5fish"
    if fork_key == "custom":
        return "custom"
    return (fork or "essential").strip() or "Essential"


def parse_bool_setting(value, default=False):
    if value is None:
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "y", "on")


def is_hdr_fork(fork):
    return (fork or "").strip().lower() in ("svt-av1-hdr", "hdr")


def parse_mediainfo_color_metadata(mediainfo_text):
    """Return detected color metadata from MediaInfo text."""
    metadata = {
        "primaries": "",
        "transfer": "",
        "matrix": "",
        "hdr_format": "",
        "chroma_position": "",
        "color_range_full": False,
        "mastering_primaries": "",
        "mastering_luminance": "",
        "max_cll": "",
        "max_fall": "",
        "is_bt709": False,
        "is_bt601": False,
        "is_hdr": False,
    }
    for line in mediainfo_text.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key_l = key.strip().lower()
        value = value.strip()
        value_l = value.lower()
        if key_l == "color primaries":
            metadata["primaries"] = value
        elif key_l == "transfer characteristics":
            metadata["transfer"] = value
        elif key_l == "matrix coefficients":
            metadata["matrix"] = value
        elif key_l == "hdr format":
            metadata["hdr_format"] = value
        elif key_l == "chroma subsampling":
            type_match = re.search(r"type\s*(\d)", value_l)
            if type_match:
                metadata["chroma_position"] = type_match.group(1)
        elif key_l == "color range":
            metadata["color_range_full"] = value_l == "full"
        elif key_l == "mastering display color primaries":
            metadata["mastering_primaries"] = value
        elif key_l == "mastering display luminance":
            metadata["mastering_luminance"] = value
        elif key_l == "maximum content light level":
            metadata["max_cll"] = value
        elif key_l == "maximum frame-average light level":
            metadata["max_fall"] = value

    prim_l = metadata["primaries"].lower()
    trans_l = metadata["transfer"].lower()
    mat_l = metadata["matrix"].lower()
    hdr_format_l = metadata["hdr_format"].lower()
    metadata["is_bt709"] = (
        metadata["primaries"] == "BT.709"
        and metadata["transfer"] == "BT.709"
        and metadata["matrix"] == "BT.709"
    )
    metadata["is_bt601"] = "bt.601" in prim_l and "bt.601" in trans_l and "bt.601" in mat_l
    hdr_markers = (
        "hdr", "dolby vision", "smpte st 2084", "st 2084", "pq",
        "hlg", "arib std-b67", "bt.2020", "bt2020", "smpte 2086"
    )
    metadata["is_hdr"] = any(marker in hdr_format_l for marker in hdr_markers) or any(
        marker in text for text in (prim_l, trans_l, mat_l) for marker in hdr_markers
    )
    return metadata


# --- MediaInfo -> SVT-AV1-HDR color flag mapping (Appendix A.2 of the SVT-AV1 User Guide) ---

# CIE 1931 G/B/R coordinates for common mastering display primaries (white point D65).
KNOWN_MASTERING_PRIMARIES = {
    "display p3": "G(0.265,0.690)B(0.15,0.06)R(0.68,0.32)",
    "p3 d65": "G(0.265,0.690)B(0.15,0.06)R(0.68,0.32)",
    "dci p3": "G(0.265,0.690)B(0.15,0.06)R(0.68,0.32)",
    "p3": "G(0.265,0.690)B(0.15,0.06)R(0.68,0.32)",
    "bt.2020": "G(0.17,0.797)B(0.131,0.046)R(0.708,0.292)",
    "bt.2100": "G(0.17,0.797)B(0.131,0.046)R(0.708,0.292)",
    "bt.709": "G(0.30,0.60)B(0.15,0.06)R(0.64,0.33)",
}


def _format_nits(value):
    """4000.0 -> '4000', 0.0050 -> '0.005', 0.0001 -> '0.0001' (matches StaxRip-style output)."""
    return f"{value:g}"


def map_color_primaries_code(value):
    v = (value or "").lower()
    if "bt.2020" in v or "bt.2100" in v:
        return "9"
    if "bt.709" in v:
        return "1"
    if "display p3" in v or "p3 d65" in v or "smpte 432" in v or "smpte eg 432" in v:
        return "12"
    if "dci p3" in v or "p3 dci" in v or "smpte 431" in v or "smpte rp 431" in v:
        return "11"
    if "bt.601" in v:
        return "6"
    if "bt.470 system m" in v:
        return "4"
    if "bt.470" in v:
        return "5"
    if "smpte 240" in v:
        return "7"
    if "xyz" in v or "smpte 428" in v:
        return "10"
    if "ebu" in v:
        return "22"
    return None


def map_transfer_code(value):
    v = (value or "").lower()
    if "pq" in v or "2084" in v:
        return "16"
    if "hlg" in v or "b67" in v:
        return "18"
    if "bt.2020" in v:
        return "15" if "12" in v else "14"
    if "bt.709" in v:
        return "1"
    if "bt.601" in v:
        return "6"
    if "srgb" in v or "sycc" in v:
        return "13"
    if "smpte 428" in v or "st 428" in v:
        return "17"
    if "linear" in v:
        return "8"
    if "smpte 240" in v:
        return "7"
    if "bt.470 system m" in v:
        return "4"
    if "bt.470" in v:
        return "5"
    return None


def map_matrix_code(value):
    v = (value or "").lower()
    if "ictcp" in v:
        return "14"
    if "bt.2020" in v and "constant" in v and "non" not in v:
        return "10"
    if "bt.2020" in v or "bt.2100" in v:
        return "9"
    if "bt.709" in v:
        return "1"
    if "bt.601" in v:
        return "6"
    if "ycgco" in v:
        return "8"
    if "smpte 240" in v:
        return "7"
    if "fcc" in v:
        return "4"
    if "bt.470" in v:
        return "5"
    if "identity" in v:
        return "0"
    return None


def build_mastering_display_value(metadata):
    """Build the --mastering-display G(x,y)B(x,y)R(x,y)WP(x,y)L(max,min) value from MediaInfo text."""
    prim_text = (metadata.get("mastering_primaries") or "").strip()
    lum_text = (metadata.get("mastering_luminance") or "").strip()
    if not prim_text or not lum_text:
        return None

    gbr_wp = None
    # Coordinate form: "R: x=0.680000 y=0.320000, G: ..., B: ..., White point: x=... y=..."
    coords = re.findall(
        r"(R|G|B|White point)\s*:?\s*x\s*=?\s*([0-9.]+)[ ,;]+y\s*=?\s*([0-9.]+)",
        prim_text,
        re.IGNORECASE,
    )
    coord_map = {name.lower(): (x, y) for name, x, y in coords}
    if all(k in coord_map for k in ("g", "b", "r")):
        wp = coord_map.get("white point", ("0.3127", "0.329"))
        gbr_wp = (
            f"G({coord_map['g'][0]},{coord_map['g'][1]})"
            f"B({coord_map['b'][0]},{coord_map['b'][1]})"
            f"R({coord_map['r'][0]},{coord_map['r'][1]})"
            f"WP({wp[0]},{wp[1]})"
        )
    else:
        # Named form: "Display P3", "BT.2020", etc.
        prim_l = prim_text.lower()
        for name, value in KNOWN_MASTERING_PRIMARIES.items():
            if name in prim_l:
                gbr_wp = value + "WP(0.3127,0.329)"
                break
    if not gbr_wp:
        return None

    min_match = re.search(r"min\s*:\s*([0-9.]+)", lum_text, re.IGNORECASE)
    max_match = re.search(r"max\s*:\s*([0-9.]+)", lum_text, re.IGNORECASE)
    if not (min_match and max_match):
        return None
    try:
        l_max = _format_nits(float(max_match.group(1)))
        l_min = _format_nits(float(min_match.group(1)))
    except ValueError:
        return None
    return f"{gbr_wp}L({l_max},{l_min})"


def build_content_light_value(metadata):
    """Build the --content-light max_cll,max_fall value from MediaInfo text like '1 264 cd/m2'."""
    def _int_of(text):
        if not text:
            return None
        head = re.split(r"cd", text, flags=re.IGNORECASE)[0]
        digits = re.sub(r"[^\d]", "", head)
        return digits or None

    max_cll = _int_of(metadata.get("max_cll", ""))
    max_fall = _int_of(metadata.get("max_fall", ""))
    if max_cll and max_fall:
        return f"{max_cll},{max_fall}"
    return None


def build_hdr_color_flags(metadata):
    """Return the SVT-AV1-HDR color flag string for an HDR source, or None if
    the primaries/transfer/matrix could not be mapped. Values never contain
    spaces so they survive param-string whitespace splitting."""
    primaries_code = map_color_primaries_code(metadata.get("primaries", ""))
    transfer_code = map_transfer_code(metadata.get("transfer", ""))
    matrix_code = map_matrix_code(metadata.get("matrix", ""))
    if not (primaries_code and transfer_code and matrix_code):
        return None

    flags = (
        f" --color-primaries {primaries_code}"
        f" --transfer-characteristics {transfer_code}"
        f" --matrix-coefficients {matrix_code}"
    )
    if metadata.get("chroma_position") in ("1", "2"):
        flags += f" --chroma-sample-position {metadata['chroma_position']}"
    if metadata.get("color_range_full"):
        flags += " --color-range 1"
    mastering_display = build_mastering_display_value(metadata)
    if mastering_display:
        flags += f" --mastering-display {mastering_display}"
    content_light = build_content_light_value(metadata)
    if content_light:
        flags += f" --content-light {content_light}"
    return flags


def detect_color_metadata(input_path, mediainfo_exe):
    if not os.path.exists(mediainfo_exe):
        return None
    try:
        res = subprocess.run(
            [mediainfo_exe, input_path],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
        )
    except Exception as e:
        print(f"[Dispatch] Warning: MediaInfo color detection failed: {e}")
        return None
    if res.returncode != 0:
        print(f"[Dispatch] Warning: MediaInfo color detection failed with exit code {res.returncode}.")
        return None
    return parse_mediainfo_color_metadata(res.stdout)


def pause_for_hdr_color_settings(input_path, color_metadata):
    print("\n" + "=" * 80)
    print(f"{RED}[Dispatch] ERROR: HDR color settings are detected in the input file, but they could not be auto-mapped.{RESET}")
    print(f"{RED}[Dispatch] Input: {os.path.basename(input_path)}{RESET}")
    print(f"{RED}[Dispatch] You need to manually edit the color settings for your .bat file before using the SVT-AV1-HDR fork,{RESET}")
    print(f"{RED}[Dispatch] or set tonemap=True in the .bat to tonemap HDR to SDR.{RESET}")
    print(f"{RED}[Dispatch] Detected color settings:{RESET}")
    print(f"{RED}[Dispatch]   Color primaries: {color_metadata.get('primaries') or 'unknown'}{RESET}")
    print(f"{RED}[Dispatch]   Transfer characteristics: {color_metadata.get('transfer') or 'unknown'}{RESET}")
    print(f"{RED}[Dispatch]   Matrix coefficients: {color_metadata.get('matrix') or 'unknown'}{RESET}")
    if color_metadata.get("hdr_format"):
        print(f"{RED}[Dispatch]   HDR format: {color_metadata['hdr_format']}{RESET}")
    print("=" * 80)
    try:
        input("Press Enter to exit...")
    except EOFError:
        pass
    sys.exit(1)


def resolve_configured_path(configured_path, root_dir, tools_dir, default_filename="ntfy.txt"):
    configured_path = (configured_path or "").strip().strip('"')
    if not configured_path:
        return os.path.join(tools_dir, default_filename)

    if re.match(r"^[A-Za-z]:[\\/]", configured_path):
        if os.name != "nt":
            drive = configured_path[0].lower()
            rest = configured_path[2:].lstrip("\\/").replace("\\", "/")
            return os.path.join("/mnt", drive, rest)
        return configured_path

    if os.path.isabs(configured_path):
        return configured_path

    if os.name != "nt":
        configured_path = configured_path.replace("\\", "/")

    return os.path.join(root_dir, configured_path)


def read_ntfy_config(settings, root_dir, tools_dir):
    ntfy_path_setting = (settings or {}).get("ntfy", "").strip()
    if ntfy_path_setting.lower() in ("", "false", "off", "none", "disabled"):
        if not ntfy_path_setting:
            ntfy_path_setting = os.path.join(tools_dir, "ntfy.txt")
        else:
            return {}, None
    ntfy_path = resolve_configured_path(ntfy_path_setting, root_dir, tools_dir)
    if not os.path.exists(ntfy_path):
        # Notifications are optional; a missing ntfy.txt should be silent.
        return {}, None

    config = {}
    try:
        with open(ntfy_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                stripped = line.strip()
                if not stripped or stripped.startswith(("#", ";")):
                    continue
                if "=" in stripped:
                    key, value = stripped.split("=", 1)
                    config[key.strip().lower()] = value.strip()
                elif "secretword" not in config:
                    # Backward compatibility with older one-line ntfy.txt files.
                    config["secretword"] = stripped
    except Exception as e:
        print(f"[Dispatch] Warning: Could not read ntfy settings from {ntfy_path}: {e}")
    return config, ntfy_path


def send_ntfy_notification(settings, root_dir, tools_dir, title, message):
    ntfy_config, ntfy_path = read_ntfy_config(settings, root_dir, tools_dir)
    secret_word = ntfy_config.get("secretword", "").strip()
    pc_name = ntfy_config.get("pcname", "").strip() or "this PC"
    if not secret_word:
        if ntfy_path:
            print(f"[Dispatch] ntfy not sent; secretword missing or empty in: {ntfy_path}")
        return False

    body = f"{message}\nPC: {pc_name}"
    topic = urllib.parse.quote(secret_word, safe="")
    request = urllib.request.Request(
        f"https://ntfy.sh/{topic}",
        data=body.encode("utf-8"),
        headers={
            "Title": title,
            "Tags": "movie_camera",
            "Priority": "default",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            response.read()
        print("[Dispatch] ntfy notification sent.")
        return True
    except Exception as e:
        print(f"[Dispatch] Warning: Failed to send ntfy notification: {e}")
        return False

WINDOWS_MAX_PATH = 260


def display_path_for_length(path):
    """Return the Windows-style path users see, so length warnings match Windows tools."""
    abs_path = os.path.abspath(path)
    normalized = abs_path.replace("/", "\\")
    lower = normalized.lower()
    if lower.startswith("\\mnt\\") and len(normalized) > 6 and normalized[5].isalpha() and normalized[6:7] == "\\":
        return f"{normalized[5].upper()}:\\{normalized[7:]}"
    return normalized


def pause_for_long_paths(long_paths):
    print("\n" + "=" * 80)
    print("[Dispatch] ERROR: One or more backend file paths exceed the Windows 260-character limit.")
    print("[Dispatch] Processing has been paused/stopped before encoding to avoid tool failures.")
    print("[Dispatch] The full filename + folder path is too long for Windows tools.")
    print("[Dispatch] Please rename your input filenames to be shorter and/or move the")
    print("[Dispatch] Auto-Boost-Av1an-portable folder to a lower drive path, then run this again.")
    print("-" * 80)
    for path in long_paths:
        print(f"[Dispatch] {len(path)} characters: {path}")
    print("=" * 80)
    try:
        input("Press Enter to exit...")
    except EOFError:
        pass
    sys.exit(1)


def warn_and_pause_if_paths_too_long(input_files, video_output_dir, temp_dir):
    # Windows-only guard: the 260-char MAX_PATH limit does not exist on Linux.
    return


# --- Python-safe source filename normalization ---
# Every source video is renamed before anything else runs so that its filename
# contains ONLY "." , "-" , "[" , "]" , a-z, A-Z and 0-9. Characters outside
# that set are folded to their closest plain-ASCII equivalent first
# (o-with-macron -> o, sharp s -> ss, ae-ligature -> ae, Cyrillic and Greek
# letters transliterated) and are dropped when no equivalent exists (CJK, emoji,
# punctuation). Spaces become dots. Doing this up front means ffmpeg,
# ffms2/VapourSynth, x264, av1an, SvtAv1EncApp, MediaInfo and mkvmerge only ever
# see a plain ASCII path.
FILENAME_ALLOWED_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "0123456789"
    ".-[]"
)

# What replaces spaces and every other kind of whitespace. "." keeps the word
# boundaries readable in the renamed files while still leaving nothing that a
# downstream command line would need quoting for.
FILENAME_SPACE_REPLACEMENT = "."

# Used when sanitizing leaves nothing behind (for example a fully CJK name).
FILENAME_FALLBACK_STEM = "video"

# Case-sensitive folds that must not go through the lowercase table below.
_FILENAME_ASCII_FOLD_EXACT = {
    "\u1e9e": "SS",   # capital sharp s
    "\u0130": "I",    # capital I with dot above
    "\u0131": "i",    # dotless i
    "\u00b5": "u",    # micro sign (not the Greek letter mu)
}

# Lowercase-keyed folds for characters that Unicode decomposition alone cannot
# reduce to ASCII. Capital letters reuse these entries and get re-capitalized.
_FILENAME_ASCII_FOLD = {
    # Latin
    "\u00df": "ss", "\u00e6": "ae", "\u0153": "oe", "\u00f8": "o",
    "\u0111": "d", "\u00f0": "d", "\u00fe": "th", "\u0142": "l",
    "\u0127": "h", "\u014b": "ng", "\u0167": "t", "\u0138": "k",
    "\u2116": "No",
    # Cyrillic
    "\u0430": "a", "\u0431": "b", "\u0432": "v", "\u0433": "g", "\u0434": "d",
    "\u0435": "e", "\u0451": "e", "\u0436": "zh", "\u0437": "z", "\u0438": "i",
    "\u0439": "y", "\u043a": "k", "\u043b": "l", "\u043c": "m", "\u043d": "n",
    "\u043e": "o", "\u043f": "p", "\u0440": "r", "\u0441": "s", "\u0442": "t",
    "\u0443": "u", "\u0444": "f", "\u0445": "kh", "\u0446": "ts", "\u0447": "ch",
    "\u0448": "sh", "\u0449": "shch", "\u044a": "", "\u044b": "y", "\u044c": "",
    "\u044d": "e", "\u044e": "yu", "\u044f": "ya",
    "\u0454": "ye", "\u0456": "i", "\u0457": "yi", "\u0491": "g", "\u045e": "u",
    # Greek
    "\u03b1": "a", "\u03b2": "v", "\u03b3": "g", "\u03b4": "d", "\u03b5": "e",
    "\u03b6": "z", "\u03b7": "i", "\u03b8": "th", "\u03b9": "i", "\u03ba": "k",
    "\u03bb": "l", "\u03bc": "m", "\u03bd": "n", "\u03be": "x", "\u03bf": "o",
    "\u03c0": "p", "\u03c1": "r", "\u03c2": "s", "\u03c3": "s", "\u03c4": "t",
    "\u03c5": "y", "\u03c6": "f", "\u03c7": "ch", "\u03c8": "ps", "\u03c9": "o",
}

_FILENAME_LATIN_LETTER_RE = re.compile(r"^LATIN (SMALL|CAPITAL) LETTER ([A-Z])\b")


def fold_char_to_ascii(ch):
    """Return the closest plain-ASCII spelling of one character, or "" if none."""
    if ch in _FILENAME_ASCII_FOLD_EXACT:
        return _FILENAME_ASCII_FOLD_EXACT[ch]

    lowered = ch.lower()
    if lowered in _FILENAME_ASCII_FOLD:
        folded = _FILENAME_ASCII_FOLD[lowered]
        if folded and ch != lowered:
            # Preserve the original capitalization: sharp-sh -> "Sh", not "SH".
            folded = folded[0].upper() + folded[1:]
        return folded

    # Strip diacritics: o-with-macron decomposes to "o" + a combining macron.
    kept = "".join(
        c for c in unicodedata.normalize("NFKD", ch)
        if c in FILENAME_ALLOWED_CHARS and not unicodedata.combining(c)
    )
    if kept:
        return kept

    # Accented Cyrillic/Greek letters decompose to a base letter that is still
    # non-ASCII, so fold that base letter through the tables above.
    base = unicodedata.normalize("NFKD", ch)[:1]
    if base and base != ch:
        return fold_char_to_ascii(base)

    # Last resort: read the base letter out of the Unicode character name,
    # e.g. "LATIN SMALL LETTER O WITH STROKE" -> "o".
    try:
        name = unicodedata.name(ch)
    except ValueError:
        return ""
    match = _FILENAME_LATIN_LETTER_RE.match(name)
    if not match:
        return ""
    return match.group(2).lower() if match.group(1) == "SMALL" else match.group(2)


def sanitize_filename_stem(stem):
    """Reduce a filename stem to "." , "-" , "[" , "]" , a-z, A-Z and 0-9 only."""
    pieces = []
    for ch in stem:
        if ch in FILENAME_ALLOWED_CHARS:
            pieces.append(ch)
        elif ch.isspace():
            pieces.append(FILENAME_SPACE_REPLACEMENT)
        else:
            pieces.append(fold_char_to_ascii(ch))
    safe = "".join(pieces)
    # A leading dot hides the file, Windows silently drops a trailing dot, and
    # runs of dots confuse extension parsing - normalize all three.
    safe = re.sub(r"\.{2,}", ".", safe).strip(".")
    # Hyphens are kept anywhere inside the name, but a leading one would make
    # the filename look like a command-line switch to the backend tools.
    safe = safe.lstrip("-")
    return safe or FILENAME_FALLBACK_STEM


def sanitize_filename_extension(ext):
    """Return a lowercase extension built only from a-z and 0-9."""
    body = "".join(c for c in ext.lower()
                   if c in FILENAME_ALLOWED_CHARS and c not in ".-[]")
    return f".{body}" if body else ""


def safe_input_filename(filename):
    """Full Python-safe filename for a source video."""
    stem, ext = os.path.splitext(filename)
    safe_ext = sanitize_filename_extension(ext) or ext.lower()
    return f"{sanitize_filename_stem(stem)}{safe_ext}"


def _blocked_by_other_file(dst_path, src_path):
    """True when dst_path already exists as a *different* file than src_path.

    A case-only rename ("Movie.MKV" -> "Movie.mkv") makes os.path.exists()
    report True on Windows even though it is the same file, and renaming a file
    onto itself is exactly what we want in that case.
    """
    if not os.path.exists(dst_path):
        return False
    try:
        return not os.path.samefile(dst_path, src_path)
    except OSError:
        return True


def sanitize_input_filenames(video_input_dir, extensions):
    """Rename every source video so its name holds only "." , "-" , "[" , "]" , a-z, A-Z and 0-9.

    This is the first step that touches the input files - it runs before scene
    detection, encoding, tagging and muxing - so no downstream tool ever has to
    cope with a non-ASCII path. Spaces become dots and brackets are kept, so
    "Dead End no Boken [1080p].mkv" (with a macron on the o) becomes
    "Dead.End.no.Boken.[1080p].mkv".

    The rename is permanent: a file keeps its safe name once it has one, both
    while it is being encoded and after the batch is finished.
    """
    supported_exts = {pattern[1:].lower() for pattern in extensions if pattern.startswith("*")}
    renamed = 0
    try:
        entries = sorted(os.listdir(video_input_dir))
    except OSError as e:
        print(f"{RED}[Dispatch] ERROR: could not list {video_input_dir}: {e}{RESET}")
        return 0

    for filename in entries:
        src_path = os.path.join(video_input_dir, filename)
        if not os.path.isfile(src_path):
            continue
        ext = os.path.splitext(filename)[1]
        if ext.lower() not in supported_exts:
            continue

        safe_name = safe_input_filename(filename)
        if safe_name == filename:
            continue

        safe_stem, safe_ext = os.path.splitext(safe_name)
        dst_path = os.path.join(video_input_dir, safe_name)
        suffix = 1
        while _blocked_by_other_file(dst_path, src_path):
            dst_path = os.path.join(video_input_dir, f"{safe_stem}{suffix}{safe_ext}")
            suffix += 1

        try:
            os.rename(src_path, dst_path)
        except OSError as e:
            print(f"{RED}[Dispatch] ERROR: could not rename {filename} -> "
                  f"{os.path.basename(dst_path)}: {e}{RESET}")
            print(f"{RED}[Dispatch]        Close anything using that file, or rename it by hand "
                  f"so it only contains '.', '-', '[', ']', a-z and 0-9, then run this again.{RESET}")
            continue

        renamed += 1
        print(f"{BLUE}[Dispatch] Renamed source file to a fully safe name: {filename} -> "
              f"{os.path.basename(dst_path)}{RESET}")

    return renamed



def gather_input_files(video_input_dir, extensions):
    input_files = []
    for ext in extensions:
        input_files.extend(sorted(glob.glob(os.path.join(video_input_dir, ext))))
    return input_files


def scan_for_new_input_files(video_input_dir, extensions, known_input_files):
    """Rescan video-input between encodes and queue files added after startup."""
    sanitize_input_filenames(video_input_dir, extensions)
    current_files = gather_input_files(video_input_dir, extensions)
    new_files = [path for path in current_files if path not in known_input_files]
    if new_files:
        print(f"{BLUE}[Dispatch] New input file(s) detected in video-input; adding to queue:{RESET}")
        for path in new_files:
            print(f"{BLUE}{os.path.basename(path)}{RESET}")
        known_input_files.update(new_files)
    return new_files

def resolve_fgs_table_path(param_str, root_dir, tools_dir=None):
    """Rewrite any relative '--fgs-table <path>' in an encoder params string
    to an absolute path so av1an worker processes (which run in their own
    working directories) can find the table file.

    Relative paths are resolved against the portable package root first,
    then the tools directory, then the current working directory. The
    resolved path uses forward slashes (accepted by Windows and safe for
    av1an's shell-style splitting of --video-params) and is double-quoted
    only when it contains whitespace.
    """
    if not param_str or "--fgs-table" not in param_str:
        return param_str

    pattern = re.compile(r'(--fgs-table[ \t]+)("([^"]*)"|\'([^\']*)\'|(\S+))')

    def _repl(match):
        raw = match.group(3) or match.group(4) or match.group(5) or ""
        if not raw:
            return match.group(0)
        if os.path.isabs(raw):
            resolved = os.path.abspath(raw)
        else:
            candidates = [os.path.join(root_dir, raw)]
            if tools_dir:
                candidates.append(os.path.join(tools_dir, raw))
            candidates.append(os.path.abspath(raw))
            resolved = next((c for c in candidates if os.path.isfile(c)), candidates[0])
            resolved = os.path.abspath(resolved)
        if not os.path.isfile(resolved):
            print(f"{RED}[Dispatch] WARNING: film grain table not found: {resolved}{RESET}")
            print(f"{RED}[Dispatch]          Place '{raw}' in the package root next to the .bat file.{RESET}")
        resolved = resolved.replace("\\", "/")
        if any(ch.isspace() for ch in resolved):
            resolved = f'"{resolved}"'
        print(f"[Dispatch] Resolved film grain table path: {resolved}")
        return match.group(1) + resolved

    return pattern.sub(_repl, param_str)


def main():
    # --- Configuration ---
    # Paths relative to this script (tools/dispatch.py)
    # Root is Auto-Boost-Av1an-portable
    script_path = os.path.abspath(__file__)
    tools_dir = os.path.dirname(script_path)
    root_dir = os.path.dirname(tools_dir)

    # Read (and if hand-edited/mis-encoded, repair) the shared worker-count
    # config up front, so every later consumer sees a clean file and value.
    cfg_workers = read_and_repair_workercount_config(tools_dir)

    video_input_dir = os.path.join(root_dir, "video-input")
    video_output_dir = os.path.join(root_dir, "video-output")
    temp_dir = os.path.join(root_dir, "temp")

    # Scripts
    av1an_script = os.path.join(tools_dir, "Auto-Boost-Av1an.py")
    scene_detect_script = os.path.join(tools_dir, "Progressive-Scene-Detection.py")
    tag_script = os.path.join(tools_dir, "tag.py")
    mux_script = os.path.join(tools_dir, "mux.py")

    # Locate MediaInfo (system-wide on Linux)
    mediainfo_exe = shutil.which("mediainfo") or os.path.join(tools_dir, "MediaInfo_CLI", "MediaInfo")

    # --- Ensure Directories Exist ---
    if not os.path.exists(video_input_dir):
        os.makedirs(video_input_dir)
        print(f"[Dispatch] Created missing input directory: {video_input_dir}")
        sys.exit(0)

    if not os.path.exists(video_output_dir):
        os.makedirs(video_output_dir)

    if not os.path.exists(temp_dir):
        os.makedirs(temp_dir)

    # --- Rename Source Files To Python-Safe Names (must be the first step) ---
    # Do this before argument parsing, scene detection or anything else touches
    # the files, so every later stage only ever sees ".", "-", "[", "]", a-z,
    # A-Z and 0-9. The rename is permanent - a source file keeps its safe name
    # for good, so a second run finds nothing left to rename.
    extensions = ("*.mkv", "*.mp4", "*.m2ts")
    sanitize_input_filenames(video_input_dir, extensions)

    # --- Argument Parsing (settings + dispatcher-only options) ---
    args = sys.argv[1:]
    # --verbose (verbose mode) shows everything the way it used to be shown and
    # is also passed through to Auto-Boost-Av1an.py. Without it (default mode),
    # the noisy phases are drawn as simple progress bars with explanations.
    # --no-verbose is the .bat's VERBOSE= placeholder for default mode.
    verbose_mode = "--verbose" in args
    args = [arg for arg in args if arg != "--no-verbose"]
    simple_mode = RICH_AVAILABLE and not verbose_mode
    if not RICH_AVAILABLE and not verbose_mode:
        print("[Dispatch] rich is unavailable; falling back to verbose output.")
    denoise_setting = None
    initial_args = []
    idx = 0
    while idx < len(args):
        if args[idx] == "--denoise" and idx + 1 < len(args):
            val = args[idx + 1].strip().lower()
            denoise_setting = "True" if val in ("1", "true", "yes", "y", "on") else "False"
            idx += 2
        else:
            initial_args.append(args[idx])
            idx += 1
    args = initial_args

    # --- Copy settings.txt to Temp ---
    # Auto-Boost-Av1an.py searches CWD (temp) for settings.txt
    settings_src = os.path.join(root_dir, "settings.txt")
    settings_dst = os.path.join(temp_dir, "settings.txt")

    if denoise_setting is not None:
        try:
            set_settings_value(settings_src, "denoise", denoise_setting)
            print(f"[Dispatch] Set settings.txt denoise={denoise_setting}")
        except Exception as e:
            print(f"[Dispatch] Warning: Failed to update settings.txt denoise: {e}")

    if os.path.exists(settings_src):
        try:
            shutil.copy2(settings_src, settings_dst)
            if denoise_setting is not None:
                set_settings_value(settings_dst, "denoise", denoise_setting)
            if not simple_mode:
                print(f"[Dispatch] Copied settings.txt to temp folder.")
        except Exception as e:
            print(f"[Dispatch] Warning: Failed to copy settings.txt: {e}")
    else:
        print(f"[Dispatch] Warning: settings.txt not found at {settings_src}")

    ntfy_settings = load_script_settings(settings_src)

    # Extract dispatcher-only options and worker count for logic checks
    worker_count = None
    selected_fork = "essential"
    arch = "x86-64-v3"
    tonemap_enabled = False
    passthrough_args = []
    idx = 0
    while idx < len(args):
        arg = args[idx]
        if arg == "--tonemap":
            if idx + 1 < len(args) and not args[idx + 1].startswith("--"):
                tonemap_enabled = parse_bool_setting(args[idx + 1])
                idx += 2
            else:
                tonemap_enabled = True
                idx += 1
        elif arg == "--workers" and idx + 1 < len(args):
            # A hand-edited (UTF-16/BOM) config read by the .bat can leak
            # garbage into this value; recover the number or substitute the
            # repaired config value so a clean integer always reaches
            # Auto-Boost-Av1an.py.
            raw_workers = args[idx + 1]
            val = sanitize_worker_value(
                raw_workers, str(cfg_workers) if cfg_workers else "1")
            if val != str(raw_workers).strip():
                print(f"\033[93m[Dispatch] --workers value {raw_workers!r} was "
                      f"invalid - using {val} instead\033[0m")
            try:
                worker_count = int(val)
            except ValueError:
                pass
            passthrough_args.extend([arg, val])
            idx += 2
        elif arg == "--fork" and idx + 1 < len(args):
            selected_fork = args[idx + 1]
            idx += 2
        elif arg == "--arch" and idx + 1 < len(args) and not args[idx + 1].startswith("--"):
            arch = args[idx + 1]
            idx += 2
        elif arg == "--avx512":
            # Kept for .bat files generated before ARCH replaced AVX512.
            if idx + 1 < len(args) and not args[idx + 1].startswith("--"):
                arch = "avx512" if parse_bool_setting(args[idx + 1]) else "x86-64-v3"
                idx += 2
            else:
                arch = "avx512"
                idx += 1
        elif arg == "--crf" and idx + 1 < len(args):
            # Auto-Boost-Av1an.py still names its base CRF input --quality internally.
            # Generated/user .bat files now expose this as CRF, so translate here.
            passthrough_args.extend(["--quality", args[idx + 1]])
            idx += 2
        else:
            passthrough_args.append(arg)
            idx += 1
    args = passthrough_args

    # --- Optimized worker overrides from the generating .bat (optional) ---
    # Written by the one-time benchmark (workercount.py / ssimu2-workercount.py
    # --optimize-bat). When absent, everything proceeds exactly as before.
    bat_opt = read_bat_optimize_settings(tools_dir, root_dir)
    if bat_opt.get("optimize-workers", "").strip().lower() in ("true", "1", "yes", "on"):
        def _override_flag_value(flag, value):
            for i in range(len(args)):
                if args[i] == flag and i + 1 < len(args):
                    args[i + 1] = value
                    return
            args.extend([flag, value])

        custom_enc = bat_opt.get("custom-av1an-workers", "").strip()
        if custom_enc.isdigit() and int(custom_enc) > 0:
            worker_count = int(custom_enc)
            _override_flag_value("--workers", custom_enc)
            print(f"\033[94m[Dispatch] Using optimized av1an worker count from bat: {custom_enc}\033[0m")

        custom_ssimu2_tool = bat_opt.get("custom-ssim2-tool", "").strip()
        if custom_ssimu2_tool:
            _override_flag_value("--ssimu2", custom_ssimu2_tool.replace(" ", "-"))
            print(f"\033[94m[Dispatch] Using optimized SSIMU2 tool from bat: {custom_ssimu2_tool}\033[0m")

        custom_ssimu2 = bat_opt.get("custom-ssim2-workers", "").strip()
        if custom_ssimu2.isdigit() and int(custom_ssimu2) > 0:
            _override_flag_value("--ssimu2-cpu-workers", custom_ssimu2)
            print(f"\033[94m[Dispatch] Using optimized SSIMU2 worker/stream count from bat: {custom_ssimu2}\033[0m")

    setup_svt_av1_fork(tools_dir, selected_fork, arch=arch, verbose=verbose_mode)

    # Auto-Boost-Av1an.py builds the .vpy and reads the same override file; this
    # is only so the choice is visible up front rather than buried in its output.
    source_filter_override = source_filter.read_override()
    if source_filter_override:
        print(f"{BLUE}[Dispatch] Source filter override active: "
              f"{source_filter.describe(source_filter_override)}{RESET}")

    # --- Worker Safety Check ---
    strip_lp_3 = False
    if worker_count is not None and worker_count in (1, 2):
        print("\033[93m[Dispatch] 1-2 workers detected, setting --lp mode to default auto parallelism\033[0m")
        strip_lp_3 = True
    # workercount-config.txt saying workers=1 (e.g. the benchmark failed and
    # wrote the safe fallback) must also drop --lp 3: with a single worker,
    # --lp 3 would cap SVT-AV1's threading and leave most of the CPU idle.
    if not strip_lp_3 and cfg_workers == 1:
        print("\033[93m[Dispatch] workercount-config.txt says 1 worker - setting --lp mode to default auto parallelism\033[0m")
        strip_lp_3 = True

    # --- Gather Input Files ---
    input_files = gather_input_files(video_input_dir, extensions)
    known_input_files = set(input_files)

    if not input_files:
        print(f"[Dispatch] No video files found in {video_input_dir}")
        sys.exit(0)

    warn_and_pause_if_paths_too_long(input_files, video_output_dir, temp_dir)

    if not simple_mode:
        print(f"[Dispatch] Found {len(input_files)} files to process.")

    # --- Main Processing Loop ---
    timing_reports = []
    batch_started_at = time.monotonic()
    input_index = 0
    while input_index < len(input_files):
        input_abspath_origin = input_files[input_index]
        input_index += 1
        filename = os.path.basename(input_abspath_origin)
        basename = os.path.splitext(filename)[0]

        # Final destination for the encoded file
        final_output_path = os.path.join(video_output_dir, basename + "-output.mkv")

        print("\n" + "="*80)
        print(f"Processing: {filename}")
        print("="*80)

        if os.path.exists(final_output_path):
            print(f"[Dispatch] Output file already exists: {final_output_path}")
            print("[Dispatch] Skipping...")
            continue

        file_started_at = time.monotonic()
        scene_elapsed = 0.0
        visual_metrics_elapsed = 0.0
        encoding_elapsed = 0.0
        mux_elapsed = 0.0

        try:
            # 1. Scene Detection
            json_file = f"{basename}_scenedetect.json"
            json_abspath = os.path.join(temp_dir, json_file)

            scenes_override = source_filter.read_override()
            scenes_are_current = (os.path.exists(json_abspath)
                                  and source_filter.scenes_marker_matches(json_abspath, scenes_override))
            if scenes_are_current:
                print(f"[Dispatch] Skipping scene detection (JSON exists in temp): {json_file}")
            else:
                if os.path.exists(json_abspath):
                    # Scene frame numbers only line up with the .vpy while both
                    # decode the source the same way.
                    print("[Dispatch] Source filter changed since these scenes were detected; re-running scene detection.")
                print("[Dispatch] Running Scene Detection...")
                cmd_scene = [
                    sys.executable,
                    scene_detect_script,
                    "-i", input_abspath_origin,
                    "-o", json_file
                ]
                scene_started_at = time.monotonic()
                try:
                    if simple_mode:
                        scene_rc = run_scene_detection_simple(
                            cmd_scene, cwd=temp_dir, env=scene_detection_env())
                        if scene_rc != 0:
                            raise subprocess.CalledProcessError(scene_rc, cmd_scene)
                    else:
                        subprocess.check_call(cmd_scene, cwd=temp_dir, env=scene_detection_env())
                    source_filter.write_scenes_marker(json_abspath, scenes_override)
                except subprocess.CalledProcessError:
                    print("[Dispatch] Scene detection failed.")
                finally:
                    scene_elapsed = time.monotonic() - scene_started_at

            # 2. Color Space Detection / HDR handling
            color_metadata = detect_color_metadata(input_abspath_origin, mediainfo_exe)
            is_bt709 = bool(color_metadata and color_metadata["is_bt709"])
            is_bt601 = bool(color_metadata and color_metadata["is_bt601"])
            is_hdr_source = bool(color_metadata and color_metadata["is_hdr"])
            tonemap_this_file = tonemap_enabled and is_hdr_source

            bt709_flags = " --color-primaries 1 --transfer-characteristics 1 --matrix-coefficients 1"
            bt601_flags = " --color-primaries 6 --transfer-characteristics 6 --matrix-coefficients 6"
            current_color_flags = ""
            if tonemap_this_file:
                # Tonemapped output is SDR BT.709.
                current_color_flags = bt709_flags
                # Auto-Boost-Av1an.py picks libplacebo or the CPU fallback when it
                # builds the .vpy and logs which one it settled on.
                print(f"{BLUE}[Dispatch] HDR source detected; tonemapping HDR to SDR (BT.709).{RESET}")
            elif is_hdr_source and is_hdr_fork(selected_fork):
                # Auto detect: build SVT-AV1-HDR color settings from MediaInfo.
                hdr_flags = build_hdr_color_flags(color_metadata)
                if hdr_flags:
                    current_color_flags = hdr_flags
                    print(f"{BLUE}[Dispatch] HDR source detected; auto-applying SVT-AV1-HDR color settings:{RESET}")
                    print(f"{BLUE}[Dispatch]  {hdr_flags.strip()}{RESET}")
                else:
                    pause_for_hdr_color_settings(input_abspath_origin, color_metadata)
            elif is_hdr_source:
                print(f"{BLUE}[Dispatch] HDR source detected. This fork encodes it as-is (set tonemap=True in the .bat to tonemap to SDR).{RESET}")
            elif is_bt709:
                current_color_flags = bt709_flags
                if simple_mode:
                    pass
                elif is_hdr_fork(selected_fork):
                    print("[Dispatch] MediaInfo confirmed full BT.709 source; copying BT.709 color settings for SVT-AV1-HDR fork.")
                else:
                    print("[Dispatch] MediaInfo confirmed full BT.709 source.")
            elif is_bt601:
                current_color_flags = bt601_flags
                if not simple_mode:
                    print("[Dispatch] MediaInfo confirmed full BT.601 source.")

            # 3. Encoding
            work_dir = os.path.join(temp_dir, basename)
            av1_file_dst = os.path.join(temp_dir, f"{basename}-av1.mkv")

            # A resume folder whose chunks were cut from a different scene list -
            # a re-detection after the source filter changed, say - would keep
            # encoding the old frame ranges against the .vpy that
            # Auto-Boost-Av1an.py builds in the working folder.
            for discarded in source_filter.discard_stale_av1an_resume_state(
                    work_dir, f"{basename}.vpy", json_abspath):
                print(f"[Dispatch] Discarded av1an resume state that was built from an older "
                      f"scene list: {os.path.basename(discarded)}")

            # Everything this encode produces is written where it is finally
            # wanted, so nothing is ever moved afterwards:
            #   temp\<name>\           the working folder - .vpy, fast pass,
            #                          scenes, stage marker, and Av1an's own
            #                          .<hash> chunk folder inside it
            #   temp\<name>-av1.mkv    the encoded video, at temp root, which is
            #                          where tag.py and mux.py look for it
            final_cmd = [
                sys.executable,
                av1an_script,
                "--fork", selected_fork,
                "-i", input_abspath_origin,
                "--temp", work_dir,
                "-o", av1_file_dst,
                "--scenes", json_file,
            ]
            final_cmd.extend(["--arch", arch])
            if tonemap_this_file:
                final_cmd.extend(["--tonemap", "true"])

            skip_next = False
            for i, a in enumerate(args):
                if skip_next:
                    skip_next = False
                    continue
                if a in ("-i", "--input", "-o", "--output"):
                    skip_next = True
                    continue
                if a in ("--fast-params", "--final-params"):
                    final_cmd.append(a)
                    if i + 1 < len(args):
                        param_str = args[i + 1]
                        if strip_lp_3:
                            param_str = param_str.replace("--lp 3", "")
                        if current_color_flags:
                            param_str += current_color_flags
                        # Rewrite relative --fgs-table paths to absolute so the
                        # encoder can open the table from any working directory.
                        param_str = resolve_fgs_table_path(param_str, root_dir, tools_dir)
                        final_cmd.append(param_str)
                        skip_next = True
                    else:
                        final_cmd.append("")
                else:
                    final_cmd.append(a)

            if not simple_mode:
                print(f"[Dispatch] Processing {filename}...")
                print("[Dispatch] Starting Encoding...")
            print(f"svt-av1 fork: {svt_fork_display_name(selected_fork)}")
            stage_file = os.path.join(work_dir, f"{basename}_stage.txt")
            stage_monitor = StageTimingMonitor(stage_file)
            resume_requested = "-r" in args or "--resume" in args
            auto_boost_started_at = time.monotonic()
            encode_began_at = time.time()
            stage_monitor.start()
            encode_rc = 0
            try:
                with keep.running():
                    subprocess.check_call(final_cmd, cwd=temp_dir)
            except subprocess.CalledProcessError as exc:
                encode_rc = exc.returncode
            finally:
                auto_boost_ended_at = time.monotonic()
                stage_monitor.stop()

            # A non-zero exit code is not on its own proof that the encode
            # failed: the encoder can die on the way out, once the finished
            # video is already written. Check the disk before throwing away
            # hours of work.
            if encode_rc != 0:
                if encode_finished_on_disk(av1_file_dst, stage_file, encode_began_at):
                    print(f"[Dispatch] Note: the encoder exited with code {encode_rc} "
                          f"after finishing. {os.path.basename(av1_file_dst)} is "
                          f"complete, so the run continues.")
                else:
                    print(f"[Dispatch] Encoding failed (exit code {encode_rc}).")
                    send_ntfy_notification(
                        ntfy_settings,
                        root_dir,
                        tools_dir,
                        "Auto-Boost encode failed",
                        "An encode failed.",
                    )
                    continue

            initial_stage = stage_monitor.initial_stage if resume_requested else 1
            visual_metrics_elapsed, encoding_elapsed = summarize_auto_boost_stage_timings(
                auto_boost_started_at,
                auto_boost_ended_at,
                stage_monitor.transitions,
                initial_stage=initial_stage,
            )

            # 4. Nothing to move: the encode wrote straight into temp.
            if not os.path.exists(av1_file_dst):
                print(f"[Dispatch] Warning: Expected encoded file not found at {av1_file_dst}")

            # 5. Tagging
            if not simple_mode:
                print("[Dispatch] Applying Tags...")
            try:
                if simple_mode:
                    tag_rc = run_quiet([sys.executable, tag_script], cwd=temp_dir,
                                       label="Tagging output")
                    if tag_rc != 0:
                        raise subprocess.CalledProcessError(tag_rc, tag_script)
                else:
                    subprocess.check_call([sys.executable, tag_script], cwd=temp_dir)
            except subprocess.CalledProcessError:
                print("[Dispatch] Warning: Tagging reported an error.")

            # 6. Muxing
            if not simple_mode:
                print("[Dispatch] Muxing...")
            mux_started_at = time.monotonic()
            temp_output_mkv = os.path.join(temp_dir, f"{basename}-output.mkv")
            # Name the file to mux rather than letting mux.py sweep the folder:
            # Av1an writes into temp directly now, so an interrupted run can
            # leave a partial -av1.mkv here that is not ours to touch.
            mux_cmd = [sys.executable, mux_script, basename]
            # mux.py exits 0 even when it finds nothing to mux, so the only
            # honest test is whether <name>-output.mkv is there afterwards.
            # Give a file that is still settling a second chance, and then a
            # longer one, before calling the run lost.
            mux_ok = False
            for wait in MUX_RETRY_WAITS:
                if wait:
                    print(f"[Dispatch] Muxing produced no {basename}-output.mkv. "
                          f"Waiting {wait} seconds and trying again...")
                    time.sleep(wait)
                try:
                    if simple_mode:
                        mux_rc = run_with_mux_progress(
                            mux_cmd, cwd=temp_dir,
                            description="Muxing")
                    else:
                        mux_rc = subprocess.call(mux_cmd, cwd=temp_dir)
                except OSError as exc:
                    print(f"[Dispatch] Could not run mux.py: {exc}")
                    mux_rc = 1
                if (mux_rc == 0 and os.path.exists(temp_output_mkv)
                        and os.path.getsize(temp_output_mkv) > 0):
                    mux_ok = True
                    break
            mux_elapsed = time.monotonic() - mux_started_at
            if not mux_ok:
                print("[Dispatch] Muxing failed.")
                continue

            # 7. Move Output
            output_moved = False
            if os.path.exists(temp_output_mkv):
                if not simple_mode:
                    print(f"[Dispatch] Moving final file to: {final_output_path}")
                try:
                    shutil.move(temp_output_mkv, final_output_path)
                    output_moved = True
                except Exception as e:
                    print(f"[Dispatch] Error moving output file: {e}")
            else:
                print(f"[Dispatch] Error: Expected output file not found: {temp_output_mkv}")

            if output_moved:
                timing_report = {
                    "filename": filename,
                    "scene_detection": scene_elapsed,
                    "visual_metrics": visual_metrics_elapsed,
                    "encoding": encoding_elapsed,
                    "muxing": mux_elapsed,
                    "total": time.monotonic() - file_started_at,
                }
                timing_reports.append(timing_report)
                print_dispatch_timing_report(timing_report)

                newly_detected_files = scan_for_new_input_files(video_input_dir, extensions, known_input_files)
                if newly_detected_files:
                    warn_and_pause_if_paths_too_long(newly_detected_files, video_output_dir, temp_dir)
                    input_files.extend(newly_detected_files)

        except Exception as e:
            print(f"[Dispatch] Critical Error during processing: {e}")
            send_ntfy_notification(
                ntfy_settings,
                root_dir,
                tools_dir,
                "Auto-Boost encode failed",
                "An encode failed.",
            )

    batch_elapsed = time.monotonic() - batch_started_at

    send_ntfy_notification(
        ntfy_settings,
        root_dir,
        tools_dir,
        "Auto-Boost encode complete",
        "All queued encodes are complete.",
    )

    print("\n" + "="*80)
    print("Dispatch Batch Complete.")
    print("="*80)

    if len(timing_reports) > 1:
        print("\nAll completed MKV time reports:")
        for timing_report in timing_reports:
            print_dispatch_timing_report(timing_report)

    print("\nTime format legend: hh:mm:ss = hours:minutes:seconds")
    print(f"Total time for all files: {format_elapsed_hhmmss(batch_elapsed)}")

if __name__ == "__main__":
    main()