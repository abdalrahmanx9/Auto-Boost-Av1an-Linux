"""Optional hand-written VapourSynth filtering script: video-input\\template.vpy.

Auto-Boost, Av1an single pass and Condor normally generate a .vpy per input from
settings.txt. That covers crop/downscale/dehalo/denoise/deband and nothing else,
so anything settings.txt has no key for - a rescale, an AA pass, a custom mask, a
second denoiser - cannot be expressed at all.

bat-builder.py's "Setup advanced tools" menu can now write video-input\\template.vpy:
the filter chain settings.txt describes, written out as a plain script in the
style of video-input\\example.vpy. While that file exists, the three dispatchers
stop generating and use it instead. Delete or rename it and they go back to
generating, exactly as before - this is opt-in and absent on every existing
install.

The template is used verbatim apart from one substitution, the one value the
file cannot know for itself:

  replace.mkv   the placeholder path in the source line becomes the file being
                encoded. This is the same convention example.vpy already uses.

The crop is the template's own. Its std.Crop line is passed through untouched,
and settings.txt's [crop] keys - along with the .bat --autocrop switch - stop
applying the moment the file exists: a chain the user edits by hand cannot also
be rewritten per input without the two disagreeing about which crop is real. The
template writer seeds the line from settings.txt once, when the file is created;
after that it is the user's, and deleting the line means no crop. crop=auto has
no equivalent here, so an auto user has to put real numbers in that line -
template-preview.bat is how to check them.

Nothing else is touched: no injected imports, no plugin loading, no wrapper. The
package sets VAPOURSYNTH_EXTRA_PLUGIN_PATH before anything runs, so plain
imports and core.<plugin> calls resolve the same way they do in example.vpy.

bat-builder.py's template page asks which rescale the file should have before it
writes it: none at all, DirectML (any GPU) or NVIDIA TensorRT-RTX. Pick one and
block goes out switched on - a descale to the show's native resolution, ArtCNN to
rebuild it, credit and line masks, and a downscale back - with its imports inside
the block, so a template written without a rescale never pays for them. What is
left for the user is new_height and the descale kernel, the two values that
cannot be known in advance. See tools/mlrt_backend.py for the backend files.

The block is written against vs-jetpack 2, which reorganised all of this: the
rescale is vsscale.Rescale (vodesfunc.RescaleBuilder is gone), the inference
backend is vsscale.Backend (the standalone vsmlrt module is gone), and ArtCNN's
ONNX models are no longer shipped alongside the plugin. The package carries its
own copy of them in VapourSynth\\.vsjet, which is the folder
model_storage_preamble() below points a rendered script at. See the comment above
MODEL_STORAGE_PREAMBLE for why they sit there and not beside the script.

Two things the template cannot do, because they are per-run .bat flags rather
than part of a filter chain: --tonemap and --convert-to-YUV420P10. The
dispatchers refuse a tonemap run against a template (silently encoding HDR as if
it were SDR wastes the whole encode) and warn about the conversion, in both cases
pointing at the template as the place to do it.

One hard rule: do not change the frame count. Scene detection
(Progressive-Scene-Detection.py) reads the source file directly, and av1an trims
this script to the frame ranges that detection produced. A trim or a splice here
puts the last scene past the end of the clip and the encode dies on an empty
chunk. Crop, resize and any per-frame filter are fine.

Consumers:
  * Auto-Boost-Av1an.py   (Auto-Boost two pass, via dispatch.py)
  * av1an-dispatch.py     (Av1an single pass)
  * condor-dispatch.py    (Condor; reuses av1an-dispatch's .vpy builder)
  * template-preview.py   (video-input\\template-preview.bat)
LQTC decodes without VapourSynth, so it ignores this file.
"""

import hashlib
import os
import re

import source_filter

TEMPLATE_FILENAME = "template.vpy"

# The stand-in path in the template's source line, swapped for the real input.
# Deliberately the same spelling example.vpy uses.
SOURCE_TOKEN = "replace.mkv"

# Read only. Nothing rewrites the crop any more; this is how the dispatchers
# read the numbers back out of a template so they can report the crop they are
# about to encode with. A crop written in any other shape, or none at all, just
# reports as the template's own business.
CROP_RE = re.compile(
    r"std\.Crop\(\s*top\s*=\s*(?P<top>-?\d+)\s*,\s*bottom\s*=\s*(?P<bottom>-?\d+)\s*,"
    r"\s*left\s*=\s*(?P<left>-?\d+)\s*,\s*right\s*=\s*(?P<right>-?\d+)\s*\)")

# Stamp written into the rendered .vpy so an unchanged template does not force a
# re-render on every run.
STAMP_PREFIX = "# Template render: "


class TemplateError(Exception):
    """A template.vpy exists but cannot be used."""


def root_dir():
    """The portable package root, one level above this tools folder."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def tools_dir():
    """The folder this file lives in."""
    return os.path.dirname(os.path.abspath(__file__))


def video_input_dir():
    return os.path.join(root_dir(), "video-input")


def vapoursynth_dir():
    """Base folder for the package's model storage (.vsjet).

    Linux keeps everything system-installed except the shared model cache,
    which lives at <repo>/.vsjet (created by setup's mlrt module) so it
    travels with the repository.
    """
    return root_dir()


def template_path(base_dir=None):
    """Absolute path of video-input\\template.vpy, whether or not it exists."""
    return os.path.join(base_dir or video_input_dir(), TEMPLATE_FILENAME)


# --------------------------------------------------------------------------
# Lossless intermediary mode
# --------------------------------------------------------------------------
#
# A rescale is the slowest thing a template can hold, and every pass over the
# video pays for it again: Auto-Boost filters once for the fast pass and once
# for the final one, Condor once per probe, and a run resumed from the top
# starts the chain over. The way out is to filter once into a lossless x264 file
# and encode AV1 from that instead. tools/lossless_mode.py does exactly that,
# from inside whichever dispatcher is running - there is no step for the user.
#
# What lives here is the switch and the script. bat-builder.py writes
# video-input\lossless-intermediary.txt when it turns the mode on; the
# dispatchers read it through lossless_mode.active() and hand the encoder a
# passthrough script - open the file, hand it over, filter nothing - over the
# intermediary, in place of the template.
#
# find_template() carries the same rule as a backstop, for an intermediary that
# someone copied into video-input by hand: while the marker is there, an input
# whose name ends in -lossless gets the passthrough rather than the template, so
# it cannot be rescaled, denoised and debanded a second time.

LOSSLESS_MARKER_FILENAME = "lossless-intermediary.txt"

# What lossless_mode.py appends to an intermediary's name, and so how every
# other tool recognises one. Changing it means changing both.
LOSSLESS_SUFFIX = "-lossless"

# The script the encoders get instead of the template. Kept in tools rather than
# video-input so it is never mistaken for something to edit.
LOSSLESS_PASSTHROUGH_FILENAME = "lossless-passthrough.vpy"

LOSSLESS_MARKER_TEXT = """# Lossless intermediary mode is ON.
#
# Written by tools\\bat-builder.py. Delete this file to leave the mode - nothing
# else has to be undone, and the encoders go straight back to filtering every
# input through video-input\\template.vpy.
#
# What it changes: your encoding .bat filters each video through template.vpy
# ONCE, into a lossless file under temp\\lossless, and encodes from that file
# instead of filtering again on every pass. There is nothing extra to run - the
# dispatcher does it. Only one of those files exists at a time: the previous
# video's is deleted before the next is built, and the last one goes when the
# .bat clears temp at the end of the run.
#
# Read by tools\\lossless_mode.py and tools\\vpy_template.py, which
# av1an-dispatch.py, Auto-Boost-Av1an.py and condor-dispatch.py all go through.

mode=lossless-intermediary
rescale={rescale}
suffix={suffix}
num_streams={num_streams}
"""

LOSSLESS_PASSTHROUGH = """# lossless-passthrough.vpy - written by tools/vpy_template.py, not by hand.
#
# The encoders run this instead of video-input\\template.vpy while
# video-input\\lossless-intermediary.txt is present and the input is a
# {suffix} file. That file already has the template's whole filter chain in it,
# put there by tools/lossless_mode.py, so all that is left is to open it and
# hand it over. Filtering it again would rescale, denoise and deband twice.
#
# Rewritten whenever it is out of date, so editing it achieves nothing. Delete
# video-input\\lossless-intermediary.txt to leave the mode instead.
import vapoursynth as vs
from vstools import initialize_clip, finalize_clip

core = vs.core
core.max_cache_size = 1024

{source_line}
src = initialize_clip(src)

final = finalize_clip(src)
final.set_output(0)
"""


def lossless_marker_path(base_dir=None):
    """Absolute path of video-input\\lossless-intermediary.txt, present or not."""
    return os.path.join(base_dir or video_input_dir(), LOSSLESS_MARKER_FILENAME)


def read_lossless_marker(base_dir=None):
    """The marker's key=value pairs, or None when the mode is off.

    A file with nothing but comments in it still counts as on - the mode is the
    file existing, and the values are only there to report what it was turned on
    for.
    """
    try:
        with open(lossless_marker_path(base_dir), "r", encoding="utf-8",
                  errors="replace") as handle:
            text = handle.read()
    except OSError:
        return None

    values = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip().lower()] = value.strip()
    return values


def lossless_mode_active(base_dir=None):
    """True while video-input\\lossless-intermediary.txt is there."""
    return read_lossless_marker(base_dir) is not None


def write_lossless_marker(rescale=None, num_streams=None, base_dir=None):
    """Turn the mode on. Returns the path written.

    CRLF because this is a .txt the user opens in Notepad, like settings.txt.
    """
    directory = base_dir or video_input_dir()
    os.makedirs(directory, exist_ok=True)
    path = lossless_marker_path(directory)
    with open(path, "w", encoding="utf-8", newline="\r\n") as handle:
        handle.write(LOSSLESS_MARKER_TEXT.format(
            suffix=LOSSLESS_SUFFIX,
            rescale=rescale or "unknown",
            num_streams=num_streams if num_streams else ""))
    return path


def delete_lossless_marker(base_dir=None):
    """Turn the mode off. True if a marker was there."""
    try:
        os.remove(lossless_marker_path(base_dir))
        return True
    except OSError:
        return False


def is_lossless_intermediary(source_path):
    """True when this input is one of lossless_mode.py's own intermediaries."""
    stem = os.path.splitext(os.path.basename(str(source_path)))[0]
    return stem.lower().endswith(LOSSLESS_SUFFIX.lower())


def lossless_mode_applies(source_path):
    """True when this input must skip the template: the mode is on and it is one."""
    return (bool(source_path) and lossless_mode_active()
            and is_lossless_intermediary(source_path))


def lossless_passthrough_path():
    return os.path.join(tools_dir(), LOSSLESS_PASSTHROUGH_FILENAME)


def lossless_passthrough_text():
    """The passthrough script, opened with whichever source filter is selected."""
    source_line = "src = " + source_filter.plain_source_call(
        source_filter.resolve(), SOURCE_TOKEN)
    return LOSSLESS_PASSTHROUGH.format(suffix=LOSSLESS_SUFFIX, source_line=source_line)


def ensure_lossless_passthrough():
    """Write tools\\lossless-passthrough.vpy when it is missing or out of date.

    Returns its path, or None if it is neither there nor writable - the one case
    where the caller has to fall back to the template.
    """
    path = lossless_passthrough_path()
    wanted = lossless_passthrough_text()
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            if handle.read() == wanted:
                return path
    except OSError:
        pass

    try:
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(wanted)
    except OSError:
        return path if os.path.isfile(path) else None
    return path


def find_template(source_path=None):
    """The template.vpy that applies to this input, or None.

    The package's own video-input folder is the canonical place and is checked
    first, so everything stays inside the portable folder. The source's own
    folder is a fallback for an input encoded from somewhere else. An empty file
    counts as absent, since that is what a half-finished edit looks like.
    """
    # Lossless intermediary mode: this input already carries the template's
    # filter chain, so it gets the passthrough rather than being filtered twice.
    if lossless_mode_applies(source_path):
        passthrough = ensure_lossless_passthrough()
        if passthrough:
            return passthrough
        print(f"[template] Could not write tools\\{LOSSLESS_PASSTHROUGH_FILENAME}, so "
              f"{os.path.basename(str(source_path))} falls back to template.vpy and "
              f"will be filtered a second time.")

    candidates = [template_path()]
    if source_path:
        candidates.append(template_path(os.path.dirname(os.path.abspath(str(source_path)))))

    seen = set()
    for candidate in candidates:
        key = os.path.normcase(candidate)
        if key in seen:
            continue
        seen.add(key)
        try:
            if os.path.isfile(candidate) and os.path.getsize(candidate) > 0:
                return candidate
        except OSError:
            continue
    return None


def read_template(path):
    """Template text, or a TemplateError naming the file that could not be read."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except OSError as e:
        raise TemplateError(f"Could not read {path}: {e}")


def _map_code_lines(text, transform):
    """Apply transform to the code lines only, leaving whole-line comments alone.

    The template's header comment mentions replace.mkv by name to explain it, and
    substituting the path in there too would turn the explanation into nonsense.
    """
    lines = text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if not line.lstrip().startswith("#"):
            lines[index] = transform(line)
    return "".join(lines)


def read_crop_values(text):
    """The four numbers in the template's crop line, or None when there is none.

    Reporting only - the crop happens inside the script, exactly as written. A
    commented out or deleted crop line reads as no crop, which is what the
    encode sees too.
    """
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        found = CROP_RE.search(line)
        if found:
            return tuple(int(found.group(name))
                         for name in ("top", "bottom", "left", "right"))
    return None


def has_source_token(text):
    """True when a code line still carries the replace.mkv placeholder."""
    return any(SOURCE_TOKEN in line for line in text.splitlines()
               if not line.lstrip().startswith("#"))


def crop_call(top, bottom, left, right):
    return f"std.Crop(top={top}, bottom={bottom}, left={left}, right={right})"


# --------------------------------------------------------------------------
# Rendering a template into a per-input .vpy
# --------------------------------------------------------------------------

def render_stamp(template_text, source_path):
    """Identity of a rendered script: same stamp means the file on disk still fits.

    The template text and the input path are the whole of it. Nothing outside the
    template feeds the script any more, so an edited crop changes the text and
    re-renders on its own, and a settings.txt edit correctly changes nothing.
    """
    digest = hashlib.sha1()
    for part in (template_text, str(source_path)):
        digest.update(part.encode("utf-8", "replace"))
        digest.update(b"\0")
    return digest.hexdigest()


def stamp_line(stamp):
    return f"{STAMP_PREFIX}{stamp}"


def is_rendered_stamp(existing_text, stamp):
    return stamp_line(stamp) in existing_text


# --------------------------------------------------------------------------
# Where ArtCNN's ONNX model is looked for
# --------------------------------------------------------------------------
#
# vsscale resolves a model out of a .vsjet folder beside the script that is
# running, falling back to a per-user cache under AppData. Neither suits a
# portable package: the script that is running is always one of these rendered
# copies in a temp folder, and AppData does not travel with the folder.
#
# So a rendered script that actually uses vsscale gets a few lines on the front
# that point the first of those two lookups at VapourSynth\.vsjet, where this
# package keeps the models - beside the interpreter and site-packages that load
# them, rather than loose in the folder the user opens. They go there with
#
#     python.exe -c "from vsscale.mlrt.cli import app; app()" onnx download ArtCNN --latest
#
# run from the VapourSynth folder; vsscale's own downloader writes to a .vsjet
# beside whatever the working directory is. Scripts\vsscale.exe is not used for
# this - like the other Scripts launchers it has the build machine's python path
# written into it and exits without doing anything on anyone else's install. The
# AppData fallback is left alone underneath it, so an install that already
# downloaded globally keeps working.

# Any code line that names vsscale is enough: ArtCNN, Rescale and Backend all
# come from it, so nothing can reach a model without naming it first.
VSSCALE_RE = re.compile(r"\bvsscale\b")

MODEL_STORAGE_PREAMBLE = """# --- Model storage ---------------------------------------------------------
# Added by tools/vpy_template.py. Not part of template.vpy, and nothing in the
# template has to know about it: it only moves the folder vsscale searches for
# ArtCNN's .onnx file from beside this rendered copy - a temp folder with
# nothing in it - to VapourSynth\\.vsjet in the portable package, so the models
# travel with the package. The per-user AppData cache stays as the fallback it
# already was.
try:
    import vsscale.mlrt.settings as _vsjet_settings
    from vstools import PackageStorage as _VsjetPackageStorage

    _vsjet_cache = _VsjetPackageStorage({root!r}, package_name="vsscale").folder
    _vsjet_settings.get_local_cache = lambda: _vsjet_cache
except Exception:
    # Not worth failing an encode over. Left alone, vsscale searches where it
    # always did and names the model it could not find.
    pass
# ---------------------------------------------------------------------------
"""


def uses_vsscale(text):
    """True if the script actually reaches for vsscale.

    Only then is the preamble worth adding - importing vsscale pulls in the
    whole vs-mlrt chain, which an encode with no rescale should not pay for.
    Whole-line comments do not count, because the rescale sections ship
    commented out and most templates leave them that way.
    """
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if VSSCALE_RE.search(stripped):
            return True
    return False


def model_storage_preamble(base_dir=None):
    """The lines that put the package's .vsjet folder first in the search.

    base_dir is the folder the .vsjet sits in, not the .vsjet itself:
    PackageStorage appends .vsjet\vsscale to whatever it is given.
    """
    return MODEL_STORAGE_PREAMBLE.format(root=base_dir or vapoursynth_dir())


def render(template_text, source_path, stamp=None):
    """Render a template into the script for one input.

    The source path is the only substitution. Everything else, the crop
    included, is the template's own text as the user left it.
    """
    if not has_source_token(template_text):
        raise TemplateError(
            f"the source line has no '{SOURCE_TOKEN}' in it. That placeholder is what "
            f"gets swapped for the file being encoded, so without it every input would "
            f"decode the same video. Put it back, e.g. "
            f"src = core.bs.VideoSource(source=r\"{SOURCE_TOKEN}\")")

    text = _map_code_lines(template_text,
                           lambda line: line.replace(SOURCE_TOKEN, str(source_path)))

    if not text.endswith("\n"):
        text += "\n"

    header = f"# Rendered from {TEMPLATE_FILENAME}. Edit the template, not this file.\n"
    if stamp:
        header += stamp_line(stamp) + "\n"
    if uses_vsscale(text):
        header += model_storage_preamble()
    return header + text


def write_rendered(vpy_path, script_text):
    with open(vpy_path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(script_text)


# --------------------------------------------------------------------------
# Turning a rescale section on and off
# --------------------------------------------------------------------------
#
# The sections go out commented, and switching one on is a matter of taking the
# "# " off its lines. Doing that by hand is where it goes wrong: leave both
# .double lines uncommented and the clip is doubled twice, uncomment two
# sections and the second quietly overwrites the first, uncomment the "only
# uncomment one of these" line and it is a syntax error inside the call.
#
# So this does it by editing nothing but the leading "# " on lines that are
# already there. Everything inside a section that was edited by hand -
# new_height, the descale kernel, border_handling - survives being switched off
# and on again, which is the whole point of not regenerating the file.

RESCALE_HEADER_RE = re.compile(r"^#\s*---\s*(?P<name>DirectML|NVIDIA|AMD)\s+Rescale\b")

# The ArtCNN model on an upscaler line, which is what picks between the two
# alternatives a section offers. Written loosely enough to also match the
# ".double(ArtCNN.X(...))" spelling that templates from before the vs-jetpack 2
# rewrite carry, so an old file on disk can still be switched on and off.
RESCALE_MODEL_RE = re.compile(r"ArtCNN\.(?P<model>[A-Za-z0-9_]+)\s*\(")

# Prose inside the section, so it stays commented even when the section is live.
RESCALE_INSTRUCTION = "Only uncomment one of these lines"

RESCALE_SECTION_NAMES = ("DirectML", "NVIDIA", "AMD")

# The last line of every section, commented or not. Used as the section's end in
# preference to the blank line after it: templates written by an earlier version
# of this file have no blank there, and without this the AMD section would run
# on into the denoise line and comment it out along with itself.
#
# "src = rs.upscale" is what this file writes now; "src = rescale" is what the
# old vodesfunc RescaleBuilder sections ended with, and is still matched so a
# template written before the vs-jetpack 2 rewrite keeps working here.
RESCALE_END_RE = re.compile(r"^\s*#?\s*src\s*=\s*(?:rs\.upscale|rescale)\s*$")


def _uncomment_line(line):
    """Take one level of "# " off a line, leaving a lone "#" alone.

    The bare "#" lines are the blank separators inside a section. Turning them
    into empty lines would split the section in two for anything that reads it
    back, so they keep their hash in both states.
    """
    if line.strip() == "#":
        return line
    if line.startswith("# "):
        return line[2:]
    if line.startswith("#"):
        return line[1:]
    return line


def _comment_line(line):
    """Put "# " back on a code line. Already-commented and blank lines are left."""
    if line.startswith("#") or not line.strip():
        return line
    return "# " + line


def rescale_sections(text):
    """(all lines, {section name: [line indexes]}) for the rescale block.

    A section runs from its "# --- <name> Rescale ..." header to the next truly
    empty line, which is what separates the three of them.
    """
    lines = text.splitlines()
    sections = {}
    current = None
    for index, line in enumerate(lines):
        match = RESCALE_HEADER_RE.match(line)
        if match:
            current = match.group("name")
            sections[current] = []
            continue
        if current is None:
            continue
        if not line.strip():
            current = None
            continue
        sections[current].append(index)
        if RESCALE_END_RE.match(line):
            current = None
    return lines, sections


def rescale_models(text, name):
    """The ArtCNN models this section offers, in the order they appear."""
    lines, sections = rescale_sections(text)
    models = []
    for index in sections.get(name, []):
        found = RESCALE_MODEL_RE.search(lines[index])
        if found and found.group("model") not in models:
            models.append(found.group("model"))
    return models


def active_rescale(text):
    """(section name, model) currently live in this template, or (None, None).

    A section counts as live when any of its lines is real code rather than a
    comment.
    """
    lines, sections = rescale_sections(text)
    for name, indexes in sections.items():
        code = [lines[index] for index in indexes
                if not lines[index].lstrip().startswith("#")]
        if not code:
            continue
        model = None
        for line in code:
            found = RESCALE_MODEL_RE.search(line)
            if found:
                model = found.group("model")
        return name, model
    return None, None


def set_rescale(text, name, model=None):
    """Switch one rescale section on and the other two off. Returns the new text.

    name of None comments all three back out. model picks between a section's
    .double lines; the first one the section offers is used when it is not given
    or not there.
    """
    lines, sections = rescale_sections(text)
    if not sections:
        raise TemplateError(
            "there are no rescale sections in this template.vpy. Write the file "
            "again from the same menu to get them back, or uncomment your own "
            "rescale by hand.")
    if name is not None and name not in sections:
        raise TemplateError(f"this template.vpy has no {name} rescale section.")

    if name is not None:
        available = rescale_models(text, name)
        if model not in available:
            model = available[0] if available else None

    for section_name, indexes in sections.items():
        for index in indexes:
            line = lines[index]
            if section_name != name:
                lines[index] = _comment_line(line)
                continue
            if RESCALE_INSTRUCTION in line:
                lines[index] = _comment_line(line)
                continue
            found = RESCALE_MODEL_RE.search(line)
            if found and model is not None:
                lines[index] = (_uncomment_line(line)
                                if found.group("model") == model
                                else _comment_line(line))
                continue
            lines[index] = _uncomment_line(line)

    return "\n".join(lines) + ("\n" if text.endswith("\n") else "")


# --------------------------------------------------------------------------
# Writing the template itself (bat-builder.py's advanced tools menu)
# --------------------------------------------------------------------------

HEADER = """# template.vpy - the filtering script your encodes use.
#
# Written from settings.txt by bat-builder.bat > Setup advanced tools. While this
# file is in video-input, Auto-Boost, Av1an single pass and Condor run it instead
# of building a script from settings.txt, so the crop/downscale/dehalo/denoise/
# deband keys in settings.txt no longer apply - this is the filter chain now.
# Delete the file to go back to generated scripts.
#
# Edit freely. One thing is filled in for each file encoded: replace.mkv in the
# source line becomes the file being encoded. Everything else runs exactly as
# written - including the crop below, which is now the only crop there is. The
# [crop] keys in settings.txt and the auto crop switch in your .bat files do
# nothing while this file exists.
#
# Do NOT change the frame count. Scene detection reads your source file directly
# and av1an trims this script to the scenes it found, so a trim or a splice makes
# the last chunk start past the end of the clip and the encoder dies on it.
# Crop, resize and per-frame filters are all fine.
#
# Run template-preview.bat (next to this file) to see the result before encoding.
{rescale_note}"""


def header_text(rescale=None, num_streams=None, lossless=False):
    """HEADER with the line that describes the rescale this file was written with.

    Written at the top rather than left for the reader to work out from the
    body: the rescale is the one part of the file whose presence was a menu
    choice, so the header says which choice was made and what is left to set.
    The two answers the same page collects - num_streams and whether the encode
    goes through a lossless intermediary - are reported for the same reason.
    """
    if rescale:
        label = LIVE_RESCALE_BACKENDS[rescale][0]
        note = (f"# The rescale block below is switched ON, running ArtCNN on\n"
                f"# {label}. Set new_height and the descale\n"
                f"# kernel for the show you are encoding before you encode with it.\n")
        if num_streams:
            note += (f"# num_streams is set to {num_streams} in that block. Lower it to 1 if the\n"
                     f"# rescale runs the GPU out of memory or crashes part way through.\n")
    else:
        note = ("# There is no rescale block in this file. Write the template again from\n"
                "# bat-builder.bat > Setup advanced tools and pick one of the rescale\n"
                "# options if you want one.\n")
    if lossless:
        note += ("#\n"
                 "# Lossless intermediary mode is ON - see video-input\\\\lossless-intermediary.txt.\n"
                 "# Your encoding .bat runs this file ONCE per video, into a lossless .mkv\n"
                 "# under temp\\\\lossless, and encodes from that - so the rescale runs once\n"
                 "# instead of once per pass. It does that on its own; there is nothing\n"
                 "# extra to run. Delete the .txt to leave the mode.\n")
    return HEADER.format(rescale_note=note)

# Always emitted, even as an all-zero no-op, because it is where a user goes to
# change the crop now that settings.txt no longer has a say. Seeded from
# settings.txt [crop] at write time and hand-edited from then on.
CROP_COMMENT = """# Crop, applied to every file encoded. Edit the numbers here - settings.txt
# [crop] is no longer read. All zeros crops nothing; delete the line for no crop.
# Every number must be even, and the same crop has to suit every video you put in
# video-input. Check it with template-preview.bat."""

# Added to the crop comment only when the template was written with a rescale.
# Without a rescale block above it there is no order to get wrong, and a warning
# about one the file does not have reads like a mistake.
CROP_RESCALE_NOTE = """# Keep this line below the rescale block. The rescale works the descale width out
# from new_height and the frame's own aspect ratio, so a crop above it changes
# that ratio and the descale lands on a resolution the show was never drawn at.
# Crop after the rescale, never before it."""


def crop_comment(rescale=None):
    """The crop comment, with the ordering note when a rescale sits above it."""
    if not rescale:
        return CROP_COMMENT
    return CROP_COMMENT + "\n" + CROP_RESCALE_NOTE


TONEMAP_COMMENT = """# HDR to SDR: the .bat --tonemap flag does not apply to a template, so do it
# here. On AMD and NVIDIA:
#     src = core.placebo.Tonemap(src, src_csp=1, dst_csp=0, dynamic_peak_detection=1, gamut_mapping=1, tone_mapping_function=1)
#     src = src.resize.Bicubic(format=vs.YUV420P16, matrix_s="709")
#     src = src.std.SetFrameProps(_Matrix=1, _Transfer=1, _Primaries=1)
# Intel GPUs return black frames from placebo.Tonemap and need the CPU path in
# tools/tonemap_backend.py instead - run that file to see which one your GPU gets."""


# SUPERSEDED by LIVE_RESCALE_SECTION below: bat-builder's template page now
# asks which rescale to write and puts that one section in switched on, so
# nothing emits these any more. Kept, with the section reader and set_rescale()
# under it, because template.vpy files written before that change are still on
# disk and are still read by all of this.
#
# The three rescale sections the template writer used to emit. Commented out on
# purpose: a rescale is wrong for most sources, and the descale kernel and the
# native height have to be worked out per show before it is right for any of
# them. All three do the same thing and differ only in the backend= line, which
# is what decides whether ArtCNN runs on DirectML, TensorRT-RTX or MIGraphX.
#
# Each section is self-contained, imports included, so uncommenting exactly one
# of them is all it takes. Nothing here is imported while they stay commented,
# which matters: vsscale pulls in a large import chain that an encode with no
# rescale in it should not pay for.
#
# The chain is vs-jetpack 2's own: vsscale.Rescale replaced vodesfunc's
# RescaleBuilder, vsscale.Backend replaced the standalone vsmlrt module, and the
# builder's .errormask()/.linemask()/.final() became default_credit_mask(),
# default_line_mask() and the .upscale property. border_handling belongs to
# Rescale now - passing it to the kernel raises CustomValueError.
RESCALE_HEADER = """# ============================================================================
# Rescale (anime) - OFF by default
# ============================================================================
# Descales to the resolution the show was actually drawn at, rebuilds it at 2x
# with ArtCNN, masks the parts that did not descale cleanly, and scales back
# down. Delete this whole block if you are not rescaling.
#
# Uncomment ONE of the three sections below - whichever matches the backend you
# have installed. DirectML runs on any Direct3D 12 GPU and needs no vendor
# download, so it is the one to start with. NVIDIA TensorRT-RTX and AMD MIGraphX
# are
# faster and are downloaded from bat-builder.bat > Setup advanced tools > "Write
# template.vpy based off settings.txt".
#
# YOU NEED to edit the "new_height" and descale kernel (e.g. Bilinear) and
# border_handling. Do not leave them at their current values for every anime.
# The width is worked out from new_height and the source's own aspect ratio, so
# there is nothing to set for it - pass new_height as a float (844.0) if you
# want a fractional descale instead. border_handling says how the source was
# padded when it was scaled up: 0 mirror, 1 zero, 2 extend.
#
# ArtCNN also needs its ONNX model file, which is not part of vs-jetpack itself.
# It is fetched, once, with
#     python.exe -c "from vsscale.mlrt.cli import app; app()" onnx download ArtCNN --latest
# run from the VapourSynth folder. The models land in VapourSynth\\.vsjet,
# about 35 MB, and travel with the package. Without them
# the rescale fails as soon as it is asked for a frame.
#
# Run video-input\\template-preview.bat and compare 1 against 2 before
# committing to an encode."""

# The body every section shares. Only {backend_call} changes between them.
#
# Two upscaler lines go out, both commented: R8F64 is the better model and C4F32
# the faster one, and which is worth it depends on the source and the GPU. They
# are written as alternatives rather than one default because uncommenting a
# line is easier to get right than editing a model name inside a call, and they
# are whole statements rather than a keyword argument inside the Rescale call so
# that commenting one out cannot leave a half-written call behind.
RESCALE_SECTION = """# --- {label} ---
# from vskernels import Bilinear, Hermite
# from vsscale import ArtCNN, Backend, Rescale
#
# new_height = 844  # The height the show was actually drawn at. Set this.
#
#     Only uncomment one of these lines: R8F64 or C4F32
# upscaler = ArtCNN.R8F64({backend_call})  #R8F64: best quality, slower
# upscaler = ArtCNN.C4F32({backend_call})  #C4F32: good quality, faster
#
# rs = Rescale(
#     src,
#     new_height,
#     Bilinear,
#     upscaler=upscaler,
#     downscaler=Hermite(linear=True),
#     border_handling=1,
# )
# rs.default_line_mask()
# rs.default_credit_mask()
# src = rs.upscale"""

# Order matters: the bundled backend first, so the section that works without a
# download is the one read first.
RESCALE_BACKENDS = (
    ("DirectML Rescale (NVIDIA / AMD / Intel) - no vendor download needed",
     "Backend.ORT_DML(fp16=True)"),
    ("NVIDIA Rescale (TensorRT-RTX) - needs the NVIDIA components downloaded",
     "Backend.TRT_RTX(fp16=True)"),
    ("AMD Rescale (MIGraphX) - needs the AMD components downloaded",
     "Backend.MIGX(fp16=True)"),
)


def rescale_lines():
    """The rescale block as lines, ready to drop into the generated template.

    The trailing blank line is part of it. Without it the last section runs
    straight into whatever settings.txt puts next - a denoise line, usually -
    which reads badly and, worse, leaves nothing to mark where the section ends.
    """
    lines = [RESCALE_HEADER, ""]
    for label, backend_call in RESCALE_BACKENDS:
        lines.append(RESCALE_SECTION.format(label=label, backend_call=backend_call))
        lines.append("")
    return lines


# --------------------------------------------------------------------------
# The rescale that goes into a template written with one
# --------------------------------------------------------------------------
#
# bat-builder's template page asks for the backend before it writes the file -
# "Standard", DirectML or NVIDIA TensorRT-RTX - so the section that goes in is
# one that was asked for, and it goes in switched on. A section that still had
# to be uncommented afterwards would only be a second chance to get it wrong,
# and a template written without a rescale has no commented block to read past.
#
# The imports sit inside the block rather than at the top of the file, so a
# template written without a rescale never pays for vsscale's import chain.

# name -> (what to call it in prose, the vsscale Backend class the upscaler
# runs on). The class rather than a finished call, because the arguments depend
# on what the template page was told - see live_backend_call() below.
LIVE_RESCALE_BACKENDS = {
    "DirectML": ("DirectML (NVIDIA / AMD / Intel)", "Backend.ORT_DML"),
    "NVIDIA": ("NVIDIA TensorRT-RTX (RTX 20-series and newer)", "Backend.TRT_RTX"),
}

# Inference streams, written into the block as a variable rather than as a
# number inside the Backend call, so there is one obvious place to change it
# afterwards. Both backends take it: ORT_DML and TRT_RTX inherit num_streams
# from ORT and TRT respectively.
LIVE_RESCALE_NUM_STREAMS = """
# How many frames the GPU is asked to work on at once. Higher is not always
# faster - past the point where the GPU is already saturated it buys nothing and
# costs memory - and in testing anything above 3 crashed some cards outright.
# Drop it to 1 if the rescale is unstable, and leave it there if that fixes it.
num_streams = {num_streams}
"""


def live_backend_call(name, num_streams=None):
    """The Backend(...) call the upscaler lines in a live rescale block run on."""
    backend = LIVE_RESCALE_BACKENDS[name][1]
    if num_streams:
        return f"{backend}(fp16=True, num_streams=num_streams)"
    return f"{backend}(fp16=True)"

LIVE_RESCALE_SECTION = """# ============================================================================
# Rescale (anime) - ON, running ArtCNN on {label}
# ============================================================================
# Descales to the resolution the show was actually drawn at, rebuilds it at 2x
# with ArtCNN, masks the parts that did not descale cleanly, and scales back
# down. This is for anime that was drawn below the resolution it shipped at. On
# anything else it makes the picture worse - delete this whole block if that is
# what you have.
#
# YOU NEED to edit new_height, the descale kernel (Bilinear below) and
# border_handling for the show you are encoding. Do not leave them at these
# values for every anime. The width is worked out from new_height and the
# source's own aspect ratio, so there is nothing to set for it - pass new_height
# as a float (844.0) if you want a fractional descale instead. border_handling
# says how the source was padded when it was scaled up: 0 mirror, 1 zero,
# 2 extend.
#
# Run video-input\\template-preview.bat and compare 1 against 2 before you
# commit to an encode with this.
from vskernels import Bilinear, Hermite
from vsscale import ArtCNN, Backend, Rescale

new_height = 844  # The height the show was actually drawn at. Set this.
{num_streams_block}
# Only one of these two lines may be uncommented: C4F32 or R8F64.
upscaler = ArtCNN.C4F32({backend_call})  # C4F32: good quality, faster
# upscaler = ArtCNN.R8F64({backend_call})  # R8F64: best quality, slower

rs = Rescale(
    src,
    new_height,
    Bilinear,
    upscaler=upscaler,
    downscaler=Hermite(linear=True),
    border_handling=1,
)
rs.default_line_mask()
rs.default_credit_mask()
src = rs.upscale
# ============================================================================"""


def live_rescale_lines(name, num_streams=None):
    """The switched-on rescale block for one backend, as lines.

    num_streams of None leaves the block exactly as it always was, so a template
    written without an answer to that question is unchanged.

    The trailing blank line is part of it: without it the block runs straight
    into whatever settings.txt puts next - a denoise line, usually - which reads
    badly and leaves nothing to mark where the block ended.
    """
    label = LIVE_RESCALE_BACKENDS[name][0]
    block = ""
    if num_streams:
        block = LIVE_RESCALE_NUM_STREAMS.format(num_streams=num_streams)
    return [LIVE_RESCALE_SECTION.format(
        label=label,
        backend_call=live_backend_call(name, num_streams),
        num_streams_block=block), ""]


def _is_true(settings, key, default="False"):
    return str(settings.get(key, default)).strip().lower() in ("1", "true", "yes", "y", "on")


def build_template_text(settings, dehalo_values, fine_dehalo_values, crop_values,
                        filter_name, rescale=None, num_streams=None, lossless=False):
    """The full text of template.vpy, from the current settings.txt values.

    Only the filters that are switched on are written out, so the file reads like
    a script somebody wrote rather than a generator's output with every branch
    left in.

    rescale is a key of LIVE_RESCALE_BACKENDS, or None for no rescale block at
    all - the "Standard template.vpy" the template page offers. num_streams and
    lossless are the two follow-up answers that page collects when a rescale was
    picked; both only change what is written, never how it is written.
    """
    do_dehalo = _is_true(settings, "dehalo")
    do_fine_dehalo = _is_true(settings, "fine_dehalo")
    do_denoise = _is_true(settings, "denoise")
    do_deband = _is_true(settings, "deband")
    do_downscale = _is_true(settings, "downscale")

    denoise_line = settings.get("denoise_setting", "").strip() if do_denoise else ""
    deband_line = settings.get("deband_setting", "").strip() if do_deband else ""

    imports = ["import vapoursynth as vs",
               "from vstools import initialize_clip, finalize_clip"]
    masks = []
    if do_dehalo:
        imports.append("from vsdehalo import edge_cleaner")
        masks.append(dehalo_values["edgemask"])
    if do_fine_dehalo:
        imports.append("from vsdehalo import fine_dehalo")
        masks.append(fine_dehalo_values["edgemask"])
    if masks:
        imports.append("from vsmasktools import " + ", ".join(sorted(set(masks))))
    # The denoise/deband lines are raw VapourSynth copied out of settings.txt;
    # DFTTest is the one name in there that needs an import of its own.
    if "DFTTest" in denoise_line or "DFTTest" in deband_line:
        imports.append("from vsdenoise import DFTTest")

    # Rescale, denoise, either dehalo, deband - the same order the generated
    # scripts use, so a template and a generated .vpy built from one settings.txt
    # still describe the same chain.
    #
    # The rescale, when the template was written with one, is first because it
    # rebuilds the line art, and denoising or dehaloing before it would smear the
    # detail it works from. The dehalos come
    # after denoise rather than before it: run on a noisy clip they sharpen the
    # edges of the grain, and the denoiser then has more to remove.
    body = list(live_rescale_lines(rescale, num_streams)) if rescale else []
    if denoise_line:
        body.append(denoise_line)
    if do_dehalo:
        body.append(
            "src = edge_cleaner(src, strength={strength}, rmode={rmode}, hot={hot}, "
            "smode={smode}, edgemask={edgemask})".format(
                strength=dehalo_values["strength"],
                rmode=dehalo_values["rmode"],
                hot=bool(dehalo_values["hot"]),
                smode=bool(dehalo_values["smode"]),
                edgemask=dehalo_values["edgemask"]))
    if do_fine_dehalo:
        body.append(
            "src = fine_dehalo(src, rx={rx}, ry={ry}, darkstr={darkstr}, "
            "brightstr={brightstr}, lowsens={lowsens}, highsens={highsens}, ss={ss}, "
            "contra={contra}, edgemask={edgemask})".format(**fine_dehalo_values))
    if deband_line:
        body.append(deband_line)

    lines = [header_text(rescale, num_streams, lossless).rstrip("\n"), ""]
    lines.extend(imports)
    lines += ["", "core = vs.core", "core.max_cache_size = 1024", ""]
    lines.append("src = " + source_filter.plain_source_call(filter_name, SOURCE_TOKEN))
    lines.append("src = initialize_clip(src)")
    lines.append("")
    if body:
        # The rescale block carries its own trailing blank; anything after it
        # does not, so one separator is added here rather than two there.
        while body and not body[-1].strip():
            body.pop()
        lines.extend(body)
        lines.append("")
    lines.append(crop_comment(rescale))
    lines.append("src = src." + crop_call(*crop_values))
    lines.append("")
    if do_downscale:
        lines.extend(_downscale_lines(settings))
        lines.append("")
    lines.append(TONEMAP_COMMENT)
    lines.append("")
    lines.append("final = finalize_clip(src)")
    lines.append("final.set_output(0)")
    return "\n".join(lines) + "\n"


# placebo.Resample takes libplacebo's own filter names, which are not all spelled
# the way settings.txt spells them.
KERNEL_MAP = {
    "hermite": "hermite",
    "bilinear": "triangle",
    "bicubic": "catmull_rom",
    "gaussian": "gaussian",
    "catmull_rom": "catmull_rom",
    "mitchell": "mitchell",
    "lanczos": "lanczos",
    "spline36": "spline36",
}


def _downscale_lines(settings):
    """The [downscaling] section as plain lines.

    A WIDTHxHEIGHT target is one call. A bare width has to work the height out
    from the source's aspect ratio, which is only known once the clip is open, so
    that becomes the same two lines of arithmetic example.vpy uses.
    """
    target = str(settings.get("target_resolution", "1920x1080")).strip()
    kernel = KERNEL_MAP.get(str(settings.get("kernel_type", "Hermite")).strip().lower(),
                            "spline36")

    width = height = 0
    if "x" in target.lower():
        try:
            width_text, height_text = target.lower().split("x", 1)
            width, height = int(width_text), int(height_text)
        except ValueError:
            width = height = 0
    else:
        try:
            width = int(target)
        except ValueError:
            width = 0

    if width <= 0:
        return [f"# Could not read target_resolution={target!r} from settings.txt; "
                f"downscaling was left out."]

    width -= width % 2
    if height > 0:
        height -= height % 2
        return [f"src = core.placebo.Resample(src, {width}, {height}, filter='{kernel}')"]

    return [
        f"new_width = {width}",
        "new_height = new_width * src.height // src.width // 2 * 2",
        f"src = core.placebo.Resample(src, new_width, new_height, filter='{kernel}')",
    ]


def write_template(text, base_dir=None):
    """Write template.vpy into video-input. Returns the path it went to."""
    directory = base_dir or video_input_dir()
    os.makedirs(directory, exist_ok=True)
    path = template_path(directory)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    return path


def delete_template(base_dir=None):
    """Remove template.vpy. True if one was there, False if not."""
    try:
        os.remove(template_path(base_dir))
        return True
    except OSError:
        return False
