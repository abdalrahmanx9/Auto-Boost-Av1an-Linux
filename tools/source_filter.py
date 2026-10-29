"""Which VapourSynth source filter the generated filtering scripts use.

The package has always loaded sources with ffms2. ffms2 is fast and its index
files are small, but it is seek-based and is known to truncate or mis-seek on
some sources (open-GOP BT.2020 material in particular), which is why
Progressive-Scene-Detection.py already switches to BestSource on its own for
HDR scene detection.

bat-builder.py's "Setup advanced tools" menu can now write a one-line marker,
tools\\source-filter-override.txt, that tells the rest of the package which
source filter to put in the .vpy scripts it generates. When that file is
absent - which is the case for every existing install - read_override() returns
None and every caller keeps its built-in default, so nothing changes until the
user opts in.

Consumers:
  * Auto-Boost-Av1an.py           (Auto-Boost two pass, via dispatch.py)
  * av1an-dispatch.py             (Av1an single pass)
  * condor-dispatch.py            (Condor; reuses av1an-dispatch's .vpy builder)
  * vspreview-dispatch.py         (the comparison preview)
LQTC does its own decoding with no VapourSynth in the path, so lqtc-dispatch.py
only reports that the override does not apply to it.
"""

import json
import os
import re
import shutil

FFMS2 = "ffms2"
BESTSOURCE = "bestsource"
VALID_FILTERS = (FFMS2, BESTSOURCE)

# What the scripts used before the override existed. Also what an unreadable or
# nonsense override falls back to.
DEFAULT_FILTER = FFMS2

OVERRIDE_FILENAME = "source-filter-override.txt"

# Spellings accepted in the marker file. Condor's own --decoder names are in
# here too so a value copied out of a condor .bat still resolves.
_ALIASES = {
    "ffms2": FFMS2,
    "ffms": FFMS2,
    "vs-ffms2": FFMS2,
    "ffmpegsource": FFMS2,
    "ffmpegsource2": FFMS2,
    "bestsource": BESTSOURCE,
    "best-source": BESTSOURCE,
    "bs": BESTSOURCE,
}

# The core attribute each filter needs. The generated scripts check this before
# hand-loading VapourSynth\vs-plugins.
PLUGIN_ATTR = {FFMS2: "ffms2", BESTSOURCE: "bs"}

_MARKER_PREFIX = "# Source filter: "


def tools_dir():
    """The tools folder this module lives in."""
    return os.path.dirname(os.path.abspath(__file__))


def override_path(base_dir=None):
    """Absolute path of the marker file, whether or not it exists."""
    return os.path.join(base_dir or tools_dir(), OVERRIDE_FILENAME)


def normalize(value):
    """Map a user-supplied spelling onto ffms2/bestsource, or None if unknown."""
    if value is None:
        return None
    key = str(value).strip().strip('"').strip("'").lower().replace("_", "-")
    return _ALIASES.get(key)


def read_override(base_dir=None):
    """The source filter named in tools\\source-filter-override.txt, or None.

    None means "no marker, no opinion": the caller keeps whatever it did
    before. A file that exists but holds an unknown value is treated the same
    way rather than guessing, so a typo cannot silently change the encode.
    """
    path = override_path(base_dir)
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            text = handle.read()
    except OSError:
        return None

    match = re.search(r"^\s*source[_-]?filter\s*=\s*(\S+)", text,
                      re.IGNORECASE | re.MULTILINE)
    if match:
        return normalize(match.group(1))

    # Also accept a file that is nothing but the bare filter name, since that is
    # the obvious thing for someone editing it by hand to write.
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(("#", ";")):
            continue
        return normalize(line)
    return None


def resolve(base_dir=None, default=DEFAULT_FILTER):
    """The source filter to actually use: the override if set, else the default."""
    return read_override(base_dir) or default


def write_override(value, base_dir=None):
    """Write the marker file. Returns its path; raises OSError if it cannot."""
    name = normalize(value)
    if name is None:
        raise ValueError(f"Unknown source filter: {value!r}")
    path = override_path(base_dir)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(
            "# VapourSynth source filter override.\n"
            "# Written by bat-builder.py (Setup advanced tools > VapourSynth source filter).\n"
            "#\n"
            "# Applies to the .vpy filtering scripts built by Auto-Boost, Av1an single\n"
            "# pass and Condor. LQTC does its own decoding and ignores this file.\n"
            "#\n"
            "# Valid values: ffms2, bestsource\n"
            "# Delete this file to go back to the built-in default (ffms2).\n"
            f"source_filter={name}\n"
        )
    return path


def clear_override(base_dir=None):
    """Delete the marker file. True if one was removed, False if none existed."""
    path = override_path(base_dir)
    try:
        os.remove(path)
        return True
    except OSError:
        return False


def describe(name):
    """One-line description for console output."""
    if name == BESTSOURCE:
        return "BestSource (core.bs.VideoSource) - slower to index, frame exact"
    return "ffms2 (core.ffms2.Source) - fast, seek based"


def cache_path(temp_dir, basename, name):
    """Where the chosen filter should keep its index for this source.

    ffms2 is handed the full index filename. BestSource appends
    ".<track>.bsindex" to whatever cachepath it is given, so it gets the stem
    and ends up with <basename>.0.bsindex in the same folder.
    """
    if name == BESTSOURCE:
        return os.path.join(str(temp_dir), str(basename))
    return os.path.join(str(temp_dir), f"{basename}.ffindex")


def index_suffix(name):
    """The index filename the chosen filter actually ends up writing.

    Only used by the "is this path too long for Windows" checks, which need the
    real name rather than the cachepath that was handed to the plugin.
    """
    return ".0.bsindex" if name == BESTSOURCE else ".ffindex"


def marker(name):
    """Line dropped into a generated .vpy so a stale script can be spotted.

    The builders reuse an existing .vpy when nothing relevant changed; without a
    marker to compare, a script written for the other source filter would be
    picked up unchanged and the override would appear to do nothing.
    """
    return f"{_MARKER_PREFIX}{name}"


def marker_matches(script_text, name):
    """True when script_text was generated for this source filter.

    Scripts written before the override existed carry no marker at all; they are
    ffms2 by construction, so they only need rebuilding when BestSource is now
    wanted.
    """
    if marker(name) in script_text:
        return True
    if _MARKER_PREFIX in script_text:
        return False
    return name == FFMS2


def source_call(name, source_path, cache_path_value=None, extra=""):
    """The VapourSynth expression that opens a source with the chosen filter.

    cache_path_value of None means "do not touch the disk": ffms2 gets
    cache=False and BestSource gets cachemode=0. That is what the metric passes
    want, where the file being read is a throwaway encode.
    """
    source_literal = f'r"{source_path}"'
    if name == BESTSOURCE:
        if cache_path_value is None:
            args = f"source={source_literal}, cachemode=0"
        else:
            # cachemode=4 means "read and write the index, and treat cachepath as
            # an absolute path" - without it BestSource rebuilds the source's
            # whole directory tree under cachepath.
            args = f'source={source_literal}, cachemode=4, cachepath=r"{cache_path_value}"'
        return f"core.bs.VideoSource({args}{extra})"
    if cache_path_value is None:
        args = f"source={source_literal}, cache=False"
    else:
        args = f'source={source_literal}, cachefile=r"{cache_path_value}"'
    return f"core.ffms2.Source({args}{extra})"


# Scene detection writes a scenes JSON that av1an later trims the .vpy with, so
# the two have to agree on how many frames the source has. The two source
# filters do not always agree (a truncated file can be 114 frames to ffms2 and
# 113 to BestSource), which shows up as
#     source pipe stderr: Trim: last frame beyond clip end
# and an encoder error on the resulting empty chunk. A cached scenes JSON is
# therefore only reusable while the override is the one it was detected under,
# which is what this sidecar records.
SCENES_MARKER_SUFFIX = ".source-filter.txt"
NO_OVERRIDE = "none"


def scenes_marker_path(scenes_json_path):
    """Sidecar file that records the override a scenes JSON was detected under."""
    return f"{scenes_json_path}{SCENES_MARKER_SUFFIX}"


def read_scenes_marker(scenes_json_path):
    """The override in force when this scenes JSON was written.

    A scenes JSON from before this sidecar existed has none, and no override is
    what it must have run under, so a missing sidecar reads as "none" and does
    not force a needless re-detection.
    """
    try:
        with open(scenes_marker_path(scenes_json_path), "r", encoding="utf-8",
                  errors="replace") as handle:
            recorded = handle.read().strip().lower()
    except OSError:
        return NO_OVERRIDE
    return normalize(recorded) or NO_OVERRIDE


def write_scenes_marker(scenes_json_path, override):
    """Record the override used for this scenes JSON. Failure is not fatal."""
    try:
        with open(scenes_marker_path(scenes_json_path), "w", encoding="utf-8",
                  newline="\n") as handle:
            handle.write(f"{override or NO_OVERRIDE}\n")
    except OSError:
        pass


def scenes_marker_matches(scenes_json_path, override):
    """True when a cached scenes JSON is still valid for the current override."""
    return read_scenes_marker(scenes_json_path) == (override or NO_OVERRIDE)


def _scenes_total_frames(scenes_json_path):
    """The frame count a scenes JSON was detected for, or None if unreadable."""
    try:
        with open(scenes_json_path, "r", encoding="utf-8", errors="replace") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    frames = data.get("frames") if isinstance(data, dict) else None
    return frames if isinstance(frames, int) and frames > 0 else None


def _chunks_json_summary(chunks_json_path):
    """(script filenames, frames covered) from an av1an chunks.json."""
    try:
        with open(chunks_json_path, "r", encoding="utf-8", errors="replace") as handle:
            chunks = json.load(handle)
    except (OSError, ValueError):
        return set(), None
    if not isinstance(chunks, list) or not chunks:
        return set(), None

    scripts = set()
    last_frame = 0
    for chunk in chunks:
        if not isinstance(chunk, dict):
            continue
        source = chunk.get("input")
        if isinstance(source, dict):
            vapoursynth = source.get("VapourSynth")
            if isinstance(vapoursynth, dict) and vapoursynth.get("path"):
                scripts.add(os.path.basename(str(vapoursynth["path"])).lower())
        end_frame = chunk.get("end_frame")
        if isinstance(end_frame, int):
            last_frame = max(last_frame, end_frame)
    return scripts, (last_frame or None)


def discard_stale_av1an_resume_state(search_dir, vpy_name, scenes_json_path):
    """Delete av1an resume state that no longer matches the current scene list.

    Re-detecting scenes is only half the job. av1an runs with --resume, and its
    chunk list - one entry per scene, with the frame range baked in - is cached
    in chunks.json inside its temp folder. A resumed run keeps that old list, so
    scenes detected under the other source filter carry on being encoded even
    though the .vpy now reports a different number of frames. The last chunk
    then starts past the end of the clip:
        source pipe stderr: Trim: last frame beyond clip end
    and the encoder is handed an empty chunk and dies.

    The test is the chunk list against the scenes it was built from rather than
    "did the override just change", so a run that already re-detected scenes and
    then failed still heals itself on the next attempt. Chunks covering exactly
    the scene list's frame count are left alone, which is every ordinary resume.

    av1an names the folder after a hash of the input path, which cannot be
    reproduced from here, so the folder is found by reading the script name out
    of its own chunks.json instead of by guessing the name. Matching is on the
    script's filename, since av1an records whatever path it resolved rather than
    the one passed in, and each source in this package has a uniquely named .vpy.

    Returns the folders that were removed.
    """
    removed = []
    total_frames = _scenes_total_frames(scenes_json_path)
    if total_frames is None:
        # Nothing to compare against; leave any resume state alone.
        return removed

    try:
        entries = sorted(os.listdir(search_dir))
    except OSError:
        return removed

    wanted = os.path.basename(str(vpy_name)).lower()
    for entry in entries:
        # av1an's temp folders are hidden ones named ".<hash>".
        if not entry.startswith("."):
            continue
        candidate = os.path.join(search_dir, entry)
        chunks_json = os.path.join(candidate, "chunks.json")
        if not os.path.isfile(chunks_json):
            continue

        scripts, chunk_frames = _chunks_json_summary(chunks_json)
        if wanted not in scripts or chunk_frames is None or chunk_frames == total_frames:
            continue
        try:
            shutil.rmtree(candidate)
            removed.append(candidate)
        except OSError:
            pass
    return removed


def plain_source_call(name, source_path):
    """The same expression with no cache arguments, so the plugin's own default applies.

    Used where an index in the default location is what is wanted, such as the
    comparison preview, which pre-builds one with ffmsindex.
    """
    if name == BESTSOURCE:
        return f'core.bs.VideoSource(source=r"{source_path}")'
    return f'core.ffms2.Source(source=r"{source_path}")'


def open_source(core, name, source_path, cache_path_value=None):
    """Open a clip with the chosen filter from live Python (not a generated .vpy)."""
    if name == BESTSOURCE:
        if cache_path_value is None:
            return core.bs.VideoSource(source=str(source_path), cachemode=0)
        return core.bs.VideoSource(source=str(source_path), cachemode=4,
                                   cachepath=str(cache_path_value))
    if cache_path_value is None:
        return core.ffms2.Source(source=str(source_path), cache=False)
    return core.ffms2.Source(source=str(source_path), cachefile=str(cache_path_value))
