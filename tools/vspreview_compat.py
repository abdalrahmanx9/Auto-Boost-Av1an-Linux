"""Launcher that starts VSPreview with the vs-jetpack 2 rename bridged.

VSPreview 0.20.1 - the newest release there is - does

    from vstools import vs, vs_object

in vspreview\\plugins\\utils.py. vs-jetpack 2 renamed that class to VSObject, so
on a current install VSPreview dies during import, before any window appears:

    ImportError: cannot import name 'vs_object' from 'vstools'.
                 Did you mean: 'VSObject'?

vspreview declares "vsjetpack>=1.1.0" with no upper bound, so pip installs the
two happily side by side and the mismatch only shows up at launch. VSObject is
the same class under a new name - nothing about it changed - so putting the old
name back on the module is the whole fix.

Two other names VSPreview reaches for are bridged the same way: DitherType's
is_fmtc property, and set_output, which vs-jetpack 2 moved out of vstools and
into vspreview.api. Without the latter, VSPreview's own VSSource Loader plugin
dies on "cannot import name 'set_output' from 'vstools'" every launch.

The alias is applied here rather than by editing site-packages\\vspreview,
because a patched third-party package is undone by the next pip install and
leaves nothing behind to explain why it was patched. Run this file instead of
"python -m vspreview" and VSPreview gets its argv exactly as it would have:

    VapourSynth\\python.exe tools\\vspreview_compat.py script.vpy [args...]

vspreview-dispatch.py's launch_command() builds that line, and the three places
that open a previewer - template-preview.py, settings-preview.py and
photon-test.py - all go through it.

Delete this file and go back to "python -m vspreview" once VSPreview ships a
release built against vs-jetpack 2, or once the package moves to vsview, the
successor previewer from the same authors.
"""

import runpy
import sys


def apply_vstools_aliases():
    """Put the pre-vs-jetpack-2 spelling of the renamed names back on vstools.

    Returns the names that were bridged, so a caller can say what it did. An
    install that already has them - an older vstools, or a newer VSPreview that
    no longer needs this - is left untouched, so every entry here disappears on
    its own once it is no longer needed.
    """
    try:
        import vstools
    except ImportError:
        # Nothing to bridge. Let VSPreview raise its own error about it, which
        # will be clearer than anything invented here.
        return []

    applied = []
    for old_name, new_name in (("vs_object", "VSObject"),):
        if hasattr(vstools, old_name):
            continue
        replacement = getattr(vstools, new_name, None)
        if replacement is None:
            continue
        setattr(vstools, old_name, replacement)
        applied.append(f"{old_name} -> {new_name}")

    applied += _restore_dither_type_is_fmtc(vstools)
    applied += _restore_set_output(vstools)
    return applied


def _restore_set_output(vstools):
    """Give vstools back a set_output name.

    vs-jetpack 2 dropped set_output; the function now lives in
    vspreview.api.output, which is where VSPreview itself gets it. Its builtin
    plugins were not updated, so the vssource_load.ppy under
    vspreview\\plugins\\builtins still does
    "from vstools import set_output" and fails to load - that is the plugin
    which opens a plain video file instead of a .vpy, which is the one thing
    a previewer should always be able to do.

    The bridge points back at VSPreview's own copy rather than at a
    reimplementation, so both sides register outputs through the same code.
    Importing it here would pull in vspreview.api - and Qt with it - before
    VSPreview has started, so the name put on vstools is a thin forwarder that
    imports on first call, by which time the app is up.
    """
    if hasattr(vstools, "set_output"):
        return []

    def set_output(*args, **kwargs):
        from vspreview.api.output import set_output as _set_output
        return _set_output(*args, **kwargs)

    set_output.__doc__ = "Forwards to vspreview.api.output.set_output."
    vstools.set_output = set_output
    return ["set_output -> vspreview.api.output.set_output"]


# The dither types zimg's own resize can do. zimg_dither_type_e stops at
# error_diffusion; everything below it in DitherType needs a separate pass.
_ZIMG_DITHER_NAMES = ("NONE", "ORDERED", "RANDOM", "ERROR_DIFFUSION")


def _restore_dither_type_is_fmtc(vstools):
    """Give DitherType back its is_fmtc property.

    VSPreview branches on it in core\\types\\video.py to decide how to get a clip
    to the format it displays:

        is_fmtc  -> resize to the target format at the source's own depth, then
                    change depth separately, because resize cannot do this
                    dither type
        else     -> one resize call that converts and dithers at the same time

    vs-jetpack 2 removed the property along with the fmtconv path it was named
    after - depth() now dithers through the vszip plugin instead. The name is
    therefore a misnomer now, but the question it answered still has the same
    answer: these six dither types are the ones zimg cannot do, so they still
    need the two-step branch and the other four still do not. Restoring it keeps
    VSPreview taking the branch it has always taken.
    """
    dither_type = getattr(vstools, "DitherType", None)
    if dither_type is None or hasattr(dither_type, "is_fmtc"):
        return []

    try:
        zimg_members = frozenset(
            getattr(dither_type, name) for name in _ZIMG_DITHER_NAMES
            if hasattr(dither_type, name))
        dither_type.is_fmtc = property(lambda self: self not in zimg_members)
    except (AttributeError, TypeError):
        # An enum that will not take a new attribute. Let VSPreview fail on its
        # own terms rather than on a half-applied shim.
        return []
    return ["DitherType.is_fmtc"]


def main():
    apply_vstools_aliases()
    # alter_sys=True is what makes this behave like "python -m vspreview": it
    # replaces argv[0] - this file's path - with vspreview's own __main__ and
    # leaves argv[1:] alone, which is exactly the argv VSPreview expects to
    # parse. Nothing here has to touch sys.argv itself.
    runpy.run_module("vspreview", run_name="__main__", alter_sys=True)


if __name__ == "__main__":
    main()
