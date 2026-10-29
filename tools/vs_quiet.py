"""Suppress the VapourSynth core's plugin-load chatter.

VapourSynth R79 logs a warning for every plugin that still uses API3, and one
more for every DLL that exists in two plugin folders at once. Neither is
actionable from here - the plugins load and work - but they bury the pipeline's
real output, so they are filtered out of the "vapoursynth" logger.

Call silence_plugin_noise() BEFORE anything touches vs.core. Importing
vapoursynth is fine; it is the first *core access* that triggers the autoload
which emits these messages, and a filter added after that is too late.
"""

import logging
import re

# Matches only the two known-noisy core messages, so genuine warnings from
# plugins or from core.log_message() still reach the console.
PLUGIN_NOISE_RE = re.compile(
    r"is using API3 which is deprecated"
    r"|already loaded \(.*\) from "
)

_installed = False


class _PluginNoiseFilter(logging.Filter):
    def filter(self, record):
        try:
            return not PLUGIN_NOISE_RE.search(record.getMessage())
        except Exception:
            return True


def silence_plugin_noise():
    """Drop plugin-load chatter from the VapourSynth logger. Idempotent."""
    global _installed
    if _installed:
        return
    logging.getLogger("vapoursynth").addFilter(_PluginNoiseFilter())
    _installed = True


def is_plugin_noise(line):
    """True when a line of relayed subprocess output is that same chatter.

    Used by the dispatchers, which cannot install a log filter inside a child
    process but can drop the lines back out of the output they relay.
    """
    return bool(line) and bool(PLUGIN_NOISE_RE.search(line))
