"""One page GUI for script-builder.py: every question on a single window.

This is the "0. GUI (experimental)" entry in the batch builder's first menu. It
asks nothing the numbered questions do not ask, and it builds nothing itself:
the page fills in the same settings dict script-builder.py's questionnaire fills
in and hands it back to build_script(), so a .sh built here is byte for byte
the .sh the questions build from the same answers.

What the page shows depends on the fork, because the forks do not take the same
settings:

  5fish       CPU build.                     denoise=True.
  essential   CPU build, fidelity 0-4.       denoise=False.
  hdr         film grain handling. No CPU    denoise=False.
              build - the fork is x86-64-v3
              only - and its HDR question is
              worded differently.
  custom      neither. The encoder binary is
              whatever the user dropped in
              tools/av1an/svt-av1 forks/custom.

Everything under "Setup advanced tools" - Condor, LQTC, AfterZone, the source
filter and template.vpy - is deliberately not here.
Those write into the package rather than producing a .sh, several of them
download hundreds of megabytes, and they read better as the pages of text they
already are. Option 3 in the console menu is still the way to reach them.

Tk is used because it is in the Python standard library, so this costs the
package no download. The portable VapourSynth\\python.exe that ships here is
built without Tcl/Tk, so script-builder.py re-runs itself under a normal Python
install when it finds one; that plumbing lives in script-builder.py, not here.

The look is Windows 11 dark: the palette below is Fluent's dark surface,
stroke and accent colours, painted onto ttk's "clam" theme because that is the
only built-in theme that lets colours be set at all - "vista" draws through the
OS and ignores them. Message boxes are drawn here as well rather than through
tkinter.messagebox, whose dialogs are native and always light.
"""

import tkinter as tk
from tkinter import ttk

# Fluent dark. BG is the window, CARD is a surface sitting on it, and the two
# strokes are the hairlines Windows 11 draws around cards and controls.
BG = "#202020"
CARD = "#272727"
CONTROL = "#2d2d2d"
CONTROL_HOVER = "#323232"
CONTROL_PRESSED = "#282828"
STROKE = "#383838"
STROKE_SOFT = "#303030"
TEXT = "#ffffff"
TEXT_SECONDARY = "#a7a7a7"
TEXT_DISABLED = "#6d6d6d"
ACCENT = "#4cc2ff"
ACCENT_HOVER = "#47b1e8"
ACCENT_PRESSED = "#42a1d2"
ACCENT_TEXT = "#000000"
WARN_COLOUR = "#ff99a4"
OK_COLOUR = "#6ccb5f"

# Preset speed and CRF are typed rather than picked from a list in the console
# version, so they are typed here too - the forks disagree about which presets
# are worth using and a hard list would be wrong for one of them.
CRF_MIN, CRF_MAX = 1.0, 63.0
SPEED_MIN, SPEED_MAX = 0, 13

MODES = [
    ("autoboost", "Auto-Boost  (two pass, metric guided)",
     "A fast first pass measures quality with SSIMU2, and the second pass "
     "uses those measurements to pick a CRF per scene. Slower, and it can "
     "produce a better result. Not for grainy sources: the metrics read "
     "grain as detail and boost it."),
    ("av1an", "Av1an single pass  (straight through)",
     "Encodes once, no measuring. Faster turnaround, and the right choice "
     "for anything with a lot of grain."),
]

FORKS = [
    ("5fish", "5fish        - anime",
     "Tuned for animation: sharp lines and subtle textures."),
    ("essential", "essential    - anime or live action",
     "Well rounded on both, with detail retention you set below."),
    ("hdr", "hdr          - HDR or SDR live action",
     "SVT-AV1-HDR. Keeps live action detail and grain."),
    ("custom", "custom       - your own encoder binary",
     "Advanced. Put SvtAv1EncApp.exe in tools > av1an > "
     "'svt-av1 forks' > custom."),
]

ARCHES = [
    ("znver2", "znver2      - try this first",
     "Faster on AMD Ryzen 3000 and newer, and it runs on Intel too."),
    ("x86-64-v3", "x86-64-v3   - the safe choice",
     "Any Intel or AMD processor from roughly 2015 onwards."),
    ("avx512", "avx512      - fastest, if supported",
     "Only on AVX-512 processors: recent Ryzen 5000+ or certain Intel."),
]

FIDELITY = [
    ("0", "0 - default, balanced. Start here."),
    ("1", "1 - slightly more detail preserved."),
    ("2", "2 - noticeably more detail, a little larger."),
    ("3", "3 - high fidelity, good for very detailed scenes."),
    ("4", "4 - maximum fidelity, can be much larger."),
]

GRAIN = [
    ("clean", "Clean / low noise",
     "Modern digital footage, clean CGI, animation. Noise is smoothed "
     "rather than kept.  --tune 0 --noise 4"),
    ("film", "Film grain mode",
     "Film sourced content, older movies, grainy footage. The grain is "
     "preserved.  --tune 5 --film-grain 10"),
]

# The HDR question is asked of every fork, but the hdr fork can keep HDR as HDR
# while the others only ever produce SDR, so option 1 means something different
# on each and is worded to match.
HDR_CHOICES = {
    "hdr": [
        ("False", "Auto detect SDR / HDR content",
         "MediaInfo checks each source. SDR gets BT.709/BT.601, HDR gets "
         "matching SVT-AV1-HDR colour settings - HDR stays HDR."),
        ("True", "Tonemap HDR to SDR",
         "HDR sources are converted to SDR (BT.709) with libplacebo inside "
         "the VapourSynth script. Uses the GPU. GPU/iGPU 2016 or newer. "
         "Not compatible with Intel GPUs."),
    ],
    "other": [
        ("False", "SDR encoding",
         "Sources are encoded as-is. SDR sources get standard BT.709/BT.601 "
         "colour settings when detected."),
        ("True", "Tonemap HDR to SDR",
         "HDR sources are converted to SDR (BT.709) with libplacebo inside "
         "the VapourSynth script. Uses the GPU. GPU/iGPU 2016 or newer. "
         "Not compatible with Intel GPUs."),
    ],
}

DENOISE_NOTE = {
    "5fish": ("denoise=True is written to settings.txt for 5fish (recommended). "
              "The suggested line is denoise_setting=src = DFTTest().denoise(src, "
              "{0.00:0.30, 0.40:0.30, 0.60:0.60, 0.80:1.50, 1.00:2.00}, "
              "planes=[0, 1, 2])"),
    "other": "denoise=False is written to settings.txt for this fork.",
}


# --- dark styling ------------------------------------------------------------

def dark_titlebar(window):
    """No-op on Linux: DWM dark titlebars are a Windows thing."""
    return


def apply_dark_theme(root):
    """Repaint ttk's clam theme in Fluent dark colours.

    Widgets sitting straight on the window use the Window.* styles; everything
    inside a card gets the plain style names, because that is where most of the
    page is.
    """
    style = ttk.Style(root)
    style.theme_use("clam")
    root.configure(background=BG)

    style.configure(".", background=CARD, foreground=TEXT, fieldbackground=CONTROL,
                    bordercolor=STROKE, lightcolor=CARD, darkcolor=CARD,
                    troughcolor=CONTROL, focuscolor=ACCENT, insertcolor=TEXT)
    style.configure("TFrame", background=CARD)
    style.configure("TLabel", background=CARD, foreground=TEXT)
    style.configure("Window.TFrame", background=BG)
    style.configure("Window.TLabel", background=BG, foreground=TEXT)
    style.configure("Title.TLabel", background=BG, foreground=TEXT,
                    font=("Segoe UI Semibold", 14))
    style.configure("Subtitle.TLabel", background=BG, foreground=TEXT_SECONDARY)
    style.configure("Hint.TLabel", background=CARD, foreground=TEXT_SECONDARY,
                    font=("Segoe UI", 8))
    style.configure("Filename.TLabel", background=BG, foreground=TEXT,
                    font=("Segoe UI Semibold", 10))
    style.configure("Warn.TLabel", background=BG, foreground=WARN_COLOUR)
    style.configure("Ok.TLabel", background=BG, foreground=OK_COLOUR)

    style.configure("TLabelframe", background=CARD, bordercolor=STROKE,
                    lightcolor=STROKE, darkcolor=STROKE, borderwidth=1,
                    relief="solid")
    style.configure("TLabelframe.Label", background=CARD, foreground=TEXT,
                    font=("Segoe UI Semibold", 9))

    for name in ("TRadiobutton", "TCheckbutton"):
        style.configure(name, background=CARD, foreground=TEXT,
                        indicatorcolor=CONTROL, indicatorbackground=CONTROL,
                        bordercolor=STROKE, lightcolor=STROKE,
                        darkcolor=STROKE, focusthickness=1,
                        focuscolor=ACCENT, padding=(2, 3))
        style.map(name,
                  background=[("active", CARD)],
                  foreground=[("disabled", TEXT_DISABLED)],
                  indicatorcolor=[("selected", ACCENT), ("active", CONTROL_HOVER)],
                  indicatorbackground=[("selected", ACCENT),
                                       ("active", CONTROL_HOVER)],
                  bordercolor=[("selected", ACCENT), ("active", "#4a4a4a")])

    style.configure("TButton", background=CONTROL, foreground=TEXT,
                    bordercolor=STROKE, lightcolor=STROKE, darkcolor=STROKE,
                    focusthickness=1, focuscolor=STROKE, borderwidth=1,
                    padding=(14, 6), relief="solid")
    style.map("TButton",
              background=[("pressed", CONTROL_PRESSED), ("active", CONTROL_HOVER)],
              bordercolor=[("active", "#4a4a4a")])
    style.configure("Accent.TButton", background=ACCENT, foreground=ACCENT_TEXT,
                    bordercolor=ACCENT, lightcolor=ACCENT, darkcolor=ACCENT)
    style.map("Accent.TButton",
              background=[("pressed", ACCENT_PRESSED), ("active", ACCENT_HOVER)],
              bordercolor=[("pressed", ACCENT_PRESSED), ("active", ACCENT_HOVER)],
              foreground=[("disabled", TEXT_DISABLED)])

    style.configure("TSpinbox", fieldbackground=CONTROL, background=CONTROL,
                    foreground=TEXT, bordercolor=STROKE, lightcolor=STROKE,
                    darkcolor=STROKE, arrowcolor=TEXT, arrowsize=12,
                    insertcolor=TEXT, padding=(6, 3))
    style.map("TSpinbox",
              bordercolor=[("focus", ACCENT)],
              lightcolor=[("focus", ACCENT)],
              darkcolor=[("focus", ACCENT)],
              arrowcolor=[("active", ACCENT)])

    style.configure("TCombobox", fieldbackground=CONTROL, background=CONTROL,
                    foreground=TEXT, bordercolor=STROKE, lightcolor=STROKE,
                    darkcolor=STROKE, arrowcolor=TEXT, arrowsize=13,
                    padding=(6, 3))
    style.map("TCombobox",
              fieldbackground=[("readonly", CONTROL), ("focus", CONTROL)],
              foreground=[("readonly", TEXT)],
              bordercolor=[("focus", ACCENT), ("hover", "#4a4a4a")],
              lightcolor=[("focus", ACCENT)],
              darkcolor=[("focus", ACCENT)],
              arrowcolor=[("active", ACCENT)])
    # The drop-down list is a plain Tk listbox, so it is coloured through the
    # option database rather than the ttk style.
    root.option_add("*TCombobox*Listbox.background", CONTROL)
    root.option_add("*TCombobox*Listbox.foreground", TEXT)
    root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
    root.option_add("*TCombobox*Listbox.selectForeground", ACCENT_TEXT)
    root.option_add("*TCombobox*Listbox.borderWidth", 0)

    # The options area has no scrollbar - the window opens at the height its
    # content needs. This styling is kept because a dark page cannot afford a
    # default light scrollbar the moment one is put back, and because a combobox
    # dropdown long enough to need one draws its own.
    style.configure("Vertical.TScrollbar", background=CONTROL, troughcolor=BG,
                    bordercolor=BG, lightcolor=CONTROL, darkcolor=CONTROL,
                    arrowcolor=TEXT_SECONDARY, arrowsize=12, width=12)
    style.map("Vertical.TScrollbar",
              background=[("pressed", "#5a5a5a"), ("active", "#4a4a4a")],
              arrowcolor=[("active", TEXT)])

    style.configure("TSeparator", background=STROKE)
    style.configure("Card.TSeparator", background=STROKE_SOFT)
    return style


def show_message(parent, title, message, kind="info"):
    """A dark dialog, because tkinter.messagebox draws the native light one."""
    window = tk.Toplevel(parent)
    window.title(title)
    window.configure(background=BG)
    window.transient(parent)
    window.resizable(False, False)
    dark_titlebar(window)

    body = ttk.Frame(window, style="Window.TFrame", padding=(20, 18, 20, 14))
    body.grid(row=0, column=0, sticky="nsew")
    ttk.Label(body, text=title, style="Title.TLabel").grid(row=0, column=0,
                                                           sticky="w")
    ttk.Label(body, text=message, style="Warn.TLabel" if kind == "error"
              else "Window.TLabel", wraplength=430,
              justify="left").grid(row=1, column=0, sticky="w", pady=(8, 16))
    ttk.Button(body, text="OK", style="Accent.TButton",
               command=window.destroy).grid(row=2, column=0, sticky="e")

    window.update_idletasks()
    x = parent.winfo_rootx() + (parent.winfo_width() - window.winfo_width()) // 2
    y = parent.winfo_rooty() + (parent.winfo_height() - window.winfo_height()) // 3
    window.geometry(f"+{max(x, 0)}+{max(y, 0)}")
    window.grab_set()
    window.focus_force()
    parent.wait_window(window)


def hint(parent, text, style="Hint.TLabel", indent=0):
    """A small wrapped explanation line, the GUI's version of the console text.

    The wrap follows the card it is in rather than a fixed width, so the text
    reflows instead of being cut off when the window is resized or Windows is
    scaling the display.
    """
    label = ttk.Label(parent, text=text, style=style, justify="left",
                      wraplength=380)

    def rewrap(event):
        # 24px of card padding plus a little slack, so nothing touches the
        # card's right hairline.
        label.configure(wraplength=max(event.width - indent - 32, 180))

    parent.bind("<Configure>", rewrap, add="+")
    return label


class BuilderPage:
    def __init__(self, root, build_callback, name_callback, defaults):
        self.root = root
        self.build_callback = build_callback
        self.name_callback = name_callback

        self.mode = tk.StringVar(value="autoboost")
        self.fork = tk.StringVar(value="essential")
        self.arch = tk.StringVar(value=defaults.get("arch", "znver2"))
        self.fidelity = tk.StringVar(value="0")
        self.grain = tk.StringVar(value="clean")
        self.tonemap = tk.StringVar(value="False")
        self.crf = tk.StringVar(value="30")
        self.speed = tk.StringVar(value="4")
        self.autocrop = tk.BooleanVar(value=False)
        self.optimize = tk.BooleanVar(value=False)
        self.verbose = tk.BooleanVar(value=False)

        self._build_layout()
        for var in (self.mode, self.fork, self.arch, self.fidelity, self.grain,
                    self.tonemap, self.crf, self.speed, self.autocrop,
                    self.optimize, self.verbose):
            var.trace_add("write", self.refresh)
        self.refresh()

    # --- layout ------------------------------------------------------------

    # The width is fixed; the height is worked out from the content in
    # fit_to_content(), so adding options later moves the bottom of the window
    # rather than pushing rows out of sight.
    WINDOW_WIDTH = 1000
    MIN_HEIGHT = 640

    # Title bar and borders, which geometry() does not count but the desktop
    # does. Taken off the work area so the window cannot end up taller than the
    # space there is to put it in.
    WINDOW_FRAME_ALLOWANCE = 48

    def _build_layout(self):
        self.root.title("Auto-Boost / Av1an Batch Builder - GUI (experimental)")
        self.root.minsize(940, self.MIN_HEIGHT)
        # A starting size only: fit_to_content() sets the real height once the
        # page below is built and filled in.
        self.root.geometry(f"{self.WINDOW_WIDTH}x780")
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)

        header = ttk.Frame(self.root, style="Window.TFrame",
                           padding=(16, 12, 16, 8))
        header.grid(row=0, column=0, sticky="ew")
        ttk.Label(header, text="Auto-Boost / Av1an Batch Builder",
                  style="Title.TLabel").pack(anchor="w")
        ttk.Label(header,
                  text="Every option on one page. Advanced tools (Condor, LQTC, "
                       "AfterZone, source filter, template.vpy) stay in the "
                       "console menu.",
                  style="Subtitle.TLabel").pack(anchor="w", pady=(2, 0))

        body = self._scrollable(self.root)
        body.columnconfigure(0, weight=1, uniform="col")
        body.columnconfigure(1, weight=1, uniform="col")

        left = ttk.Frame(body, style="Window.TFrame")
        left.grid(row=0, column=0, sticky="nsew", padx=(16, 8))
        right = ttk.Frame(body, style="Window.TFrame")
        right.grid(row=0, column=1, sticky="nsew", padx=(8, 16))
        for column in (left, right):
            column.columnconfigure(0, weight=1)

        self._method_box(left)
        self._fork_box(left)
        self._arch_box(left)
        self._quality_box(right)
        self._fidelity_box(right)
        self._grain_box(right)
        self._hdr_box(right)
        self._extras_box(right)

        self._footer(self.root)

    def _scrollable(self, parent):
        """The options area, with a scrollbar that only shows when it is needed.

        fit_to_content() opens the window at the height the options actually
        need, so on any normal screen there is nothing to scroll and the bar
        stays hidden - a permanently-full scrollbar down the side of a page that
        fits is just noise.

        It is not deleted, though, because the window is resizable and the screen
        may be too short to grant the height asked for. Drag the window smaller,
        or open it on a laptop, and the rows below the fold have to be reachable
        and have to look reachable. So the bar appears exactly when the content
        stops fitting and goes away again when it fits.
        """
        holder = ttk.Frame(parent, style="Window.TFrame")
        holder.grid(row=1, column=0, sticky="nsew")
        holder.columnconfigure(0, weight=1)
        holder.rowconfigure(0, weight=1)

        canvas = tk.Canvas(holder, borderwidth=0, highlightthickness=0,
                           background=BG)
        canvas.grid(row=0, column=0, sticky="nsew")

        bar = ttk.Scrollbar(holder, orient="vertical", command=canvas.yview,
                            style="Vertical.TScrollbar")
        bar.grid(row=0, column=1, sticky="ns")
        bar.grid_remove()

        def scrolled(first, last):
            # Tk reports the visible slice of the content. Covering all of it
            # means everything fits, which at the size the window opens at is
            # always the case - so the bar is put away rather than sitting there
            # full-length doing nothing. Drag the window shorter and it comes
            # back on its own, which is the only time it has anything to say.
            if float(first) <= 0.0 and float(last) >= 1.0:
                bar.grid_remove()
            else:
                bar.grid()
            bar.set(first, last)
        canvas.configure(yscrollcommand=scrolled)

        inner = ttk.Frame(canvas, style="Window.TFrame")
        window = canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>",
                   lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>",
                    lambda e: canvas.itemconfigure(window, width=e.width))

        def wheel(event):
            # Nothing moves when the whole page already fits, because the scroll
            # region is then no taller than the canvas.
            canvas.yview_scroll(-1 * (event.delta // 120), "units")
        canvas.bind_all("<MouseWheel>", wheel)

        # fit_to_content() needs both: the canvas to discount its placeholder
        # height, and the inner frame for what the options really come to.
        self._canvas = canvas
        self._inner = inner
        return inner

    def fit_to_content(self):
        """Open the window exactly as tall as the options, so nothing scrolls.

        Called once, after the page is built and refreshed, because the labels
        that refresh() fills in change how tall the page is.

        The root's own requested height is not usable on its own: the options sit
        in a canvas, so what it counts for that row is the canvas's placeholder
        height rather than the content inside it. Swapping that one number for
        the inner frame's height gives the height at which the last row of the
        form is on screen.

        The result is clamped to the desktop work area, and the window is placed
        in it as well as sized to it. Sizing alone is not enough: Tk drops a new
        window at a fixed offset from the top left corner and takes no interest
        in where the bottom of it lands, so a tall window opens with its last
        rows under the taskbar. Height and position are decided together here
        because neither is right without the other.

        The window has to be mapped before this is called. The options are two
        columns of wrapping text whose height depends on their width, and the
        hint labels only rewrap on the <Configure> event that a real layout
        sends - Tk does not lay a withdrawn window out at its true size, so
        measuring one reports the height of text wrapped to the canvas's small
        default width. Here that is a hundred pixels of empty space along the
        bottom of the window. run() maps it invisibly for this reason.
        """
        self.root.update()

        needed = (self.root.winfo_reqheight()
                  - self._canvas.winfo_reqheight()
                  + self._inner.winfo_reqheight())

        left, top, right, bottom = self._work_area()
        frame = self._frame_height()
        border = self._border_width()

        # geometry() sets the client area; the desktop has to fit the frame
        # around it as well, so the title bar and borders come off the room
        # available and are counted again when centring what is left.
        height = max(min(needed, (bottom - top) - frame), self.MIN_HEIGHT)
        width = self.WINDOW_WIDTH

        x = left + max(((right - left) - (width + border * 2)) // 2, 0)
        y = top + max(((bottom - top) - (height + frame)) // 2, 0)
        self.root.geometry(f"{width}x{height}+{x}+{y}")

    def _work_area(self):
        """The desktop rectangle a window can occupy, as (left, top, right, bottom).

        The taskbar is the point of asking. It is not always at the bottom and
        not always the same height, so the screen size alone cannot say where a
        window may end - and Tk's own wm_maxsize is no help here either: on this
        desktop it answers 1065 where the work area really ends at 1032, which is
        exactly the strip a window was disappearing into.

        Windows answers properly through SystemParametersInfo. Anything that does
        not gets the whole screen, which is what was assumed before this existed.
        """
        try:
            import ctypes
            import ctypes.wintypes

            SPI_GETWORKAREA = 0x0030
            rect = ctypes.wintypes.RECT()
            if ctypes.windll.user32.SystemParametersInfoW(
                    SPI_GETWORKAREA, 0, ctypes.byref(rect), 0):
                if rect.right > rect.left and rect.bottom > rect.top:
                    return rect.left, rect.top, rect.right, rect.bottom
        except Exception:
            pass
        return 0, 0, self.root.winfo_screenwidth(), self.root.winfo_screenheight()

    def _frame_height(self):
        """Title bar and borders - the part of the window geometry() does not set.

        Measured off the live window rather than assumed, so display scaling and
        whatever Windows is doing to title bars this year are accounted for. The
        gap between where Tk puts the window and where the client area actually
        starts is the title bar; a few pixels are added for the bottom border.
        Only if the window is not mapped, and the answer is therefore nonsense,
        does the fixed allowance stand in.
        """
        measured = self.root.winfo_rooty() - self.root.winfo_y()
        if 0 < measured < 200:
            return measured + 8
        return self.WINDOW_FRAME_ALLOWANCE

    def _border_width(self):
        """One side border, measured the same way as the title bar above.

        Centring the client width alone leaves the window off centre by a border
        on each side. Small, but it is measured for free at this point.
        """
        measured = self.root.winfo_rootx() - self.root.winfo_x()
        return measured if 0 <= measured < 100 else 0

    def _section(self, parent, title, row):
        frame = ttk.LabelFrame(parent, text=title, padding=(12, 8, 12, 10))
        frame.grid(row=row, column=0, sticky="ew", pady=(0, 12))
        frame.columnconfigure(0, weight=1)
        return frame

    def _radio_group(self, frame, variable, options):
        for index, (value, label, description) in enumerate(options):
            ttk.Radiobutton(frame, text=label, value=value,
                            variable=variable).grid(row=index * 2, column=0,
                                                    sticky="w")
            hint(frame, description, indent=22).grid(row=index * 2 + 1,
                                                     column=0, sticky="ew",
                                                     padx=(22, 0),
                                                     pady=(0, 6))

    def _method_box(self, parent):
        frame = self._section(parent, "Encoding method", 0)
        self._radio_group(frame, self.mode, MODES)

    def _fork_box(self, parent):
        frame = self._section(parent, "Encoder preset (fork)", 1)
        self._radio_group(frame, self.fork, FORKS)

    def _arch_box(self, parent):
        self.arch_frame = self._section(parent, "CPU build", 2)
        hint(self.arch_frame,
             "All three produce the same video; a matching build just encodes "
             "it faster. Picking one your processor cannot run stops the "
             "encode with an error rather than doing any harm, so a faster "
             "build is worth trying.").grid(row=0, column=0, sticky="ew",
                                            pady=(0, 8))
        holder = ttk.Frame(self.arch_frame)
        holder.grid(row=1, column=0, sticky="ew")
        self._radio_group(holder, self.arch, ARCHES)

    def _quality_box(self, parent):
        frame = self._section(parent, "Quality and speed", 0)
        row = ttk.Frame(frame)
        row.grid(row=0, column=0, sticky="w")
        ttk.Label(row, text="CRF").grid(row=0, column=0, sticky="w")
        ttk.Spinbox(row, from_=CRF_MIN, to=CRF_MAX, increment=1, width=6,
                    textvariable=self.crf).grid(row=0, column=1, padx=(10, 26))
        ttk.Label(row, text="Preset speed").grid(row=0, column=2, sticky="w")
        ttk.Spinbox(row, from_=SPEED_MIN, to=SPEED_MAX, increment=1, width=6,
                    textvariable=self.speed).grid(row=0, column=3, padx=(10, 0))
        hint(frame,
             "CRF: lower is higher quality and a bigger file. 20 very high, "
             "25 good, 30 smaller. Start at 30 if you are unsure.").grid(
                 row=1, column=0, sticky="ew", pady=(8, 2))
        self.speed_hint = hint(frame, "")
        self.speed_hint.grid(row=2, column=0, sticky="ew")

    def _fidelity_box(self, parent):
        self.fidelity_frame = self._section(
            parent, "Fidelity / detail preservation (essential)", 1)
        hint(self.fidelity_frame,
             "How hard the encoder works to keep fine detail instead of "
             "smoothing it away to save space. Start at 0; if textures or "
             "fine lines look soft, go up by one and compare.").grid(
                 row=0, column=0, sticky="ew", pady=(0, 8))
        # The combobox shows the descriptions and keeps self.fidelity holding
        # the bare "0".."4" the .sh is built from, so it has no textvariable
        # of its own - the two would fight over what the widget displays.
        self.fidelity_box = ttk.Combobox(self.fidelity_frame, state="readonly",
                                         width=42,
                                         values=[label for _, label in FIDELITY])
        self.fidelity_box.grid(row=1, column=0, sticky="w")
        self.fidelity_box.bind("<<ComboboxSelected>>", self._fidelity_picked)
        self.fidelity_box.set(FIDELITY[0][1])
        hint(self.fidelity_frame,
             "4 mimics SVT-AV1-HDR's tune grain: absolute grain retention "
             "with no regard for distortion at all.").grid(row=2, column=0,
                                                           sticky="ew",
                                                           pady=(6, 0))

    def _grain_box(self, parent):
        self.grain_frame = self._section(
            parent, "Film grain / noise handling (hdr)", 2)
        self._radio_group(self.grain_frame, self.grain, GRAIN)

    def _hdr_box(self, parent):
        self.hdr_frame = self._section(parent, "HDR handling", 3)
        self.hdr_holder = ttk.Frame(self.hdr_frame)
        self.hdr_holder.grid(row=0, column=0, sticky="ew")
        self.hdr_kind = None

    def _extras_box(self, parent):
        frame = self._section(parent, "Extras", 4)
        ttk.Checkbutton(frame, text="Auto crop black bars",
                        variable=self.autocrop).grid(row=0, column=0, sticky="w")
        hint(frame,
             "Detects letterboxing and crops it, so no bits are spent on black "
             "areas. If it takes too much or too little, switch to manual crop "
             "in settings.txt.", indent=22).grid(row=1, column=0, sticky="ew",
                                                 padx=(22, 0), pady=(0, 6))
        ttk.Checkbutton(frame, text="One-time worker optimization benchmark",
                        variable=self.optimize).grid(row=2, column=0, sticky="w")
        hint(frame,
             "On its first launch the .sh benchmarks YOUR preset, parameters "
             "and filtering to find the worker counts that saturate this PC, "
             "then writes them into itself and reuses them every run.",
             indent=22).grid(row=3, column=0, sticky="ew", padx=(22, 0),
                             pady=(0, 6))
        ttk.Checkbutton(frame, text="Verbose mode (show every tool's output)",
                        variable=self.verbose).grid(row=4, column=0, sticky="w")
        hint(frame,
             "Off: the simple interface with progress bars and a short "
             "explanation of each phase.", indent=22).grid(row=5, column=0,
                                                           sticky="ew",
                                                           padx=(22, 0),
                                                           pady=(0, 6))
        self.denoise_label = hint(frame, "")
        self.denoise_label.grid(row=6, column=0, sticky="ew", pady=(8, 0))

    def _footer(self, parent):
        frame = ttk.Frame(parent, style="Window.TFrame",
                          padding=(16, 10, 16, 14))
        frame.grid(row=2, column=0, sticky="ew")
        frame.columnconfigure(0, weight=1)
        ttk.Separator(parent, orient="horizontal").grid(row=2, column=0,
                                                        sticky="new")

        self.filename_label = ttk.Label(frame, text="", style="Filename.TLabel")
        self.filename_label.grid(row=0, column=0, sticky="w")
        self.warning_label = ttk.Label(frame, text="", style="Warn.TLabel",
                                       wraplength=700, justify="left")
        self.warning_label.grid(row=1, column=0, sticky="w", pady=(3, 0))
        self.status_label = ttk.Label(frame, text="", style="Ok.TLabel",
                                      wraplength=700, justify="left")
        self.status_label.grid(row=2, column=0, sticky="w", pady=(3, 0))

        buttons = ttk.Frame(frame, style="Window.TFrame")
        buttons.grid(row=0, column=1, rowspan=3, sticky="e")
        ttk.Button(buttons, text="Generate .sh", style="Accent.TButton",
                   command=self.on_generate).grid(row=0, column=0, padx=(0, 10))
        ttk.Button(buttons, text="Close",
                   command=self.root.destroy).grid(row=0, column=1)

    # --- behaviour ---------------------------------------------------------

    def _fidelity_picked(self, _event=None):
        label = self.fidelity_box.get()
        for value, text in FIDELITY:
            if text == label:
                self.fidelity.set(value)
                return

    def _show(self, frame, visible):
        if visible:
            frame.grid()
        else:
            frame.grid_remove()

    def _rebuild_hdr(self, fork):
        """The HDR radios are re-made when the fork changes their meaning."""
        kind = "hdr" if fork == "hdr" else "other"
        if kind == self.hdr_kind:
            return
        for child in self.hdr_holder.winfo_children():
            child.destroy()
        self._radio_group(self.hdr_holder, self.tonemap, HDR_CHOICES[kind])
        self.hdr_kind = kind
        # Both wordings share option 1 / option 2, so a tonemap choice made
        # before the fork was switched still means the same thing here.

    def refresh(self, *_args):
        fork = self.fork.get()
        mode = self.mode.get()

        self._show(self.arch_frame, fork in ("5fish", "essential"))
        self._show(self.fidelity_frame, fork == "essential")
        self._show(self.grain_frame, fork == "hdr")
        self._rebuild_hdr(fork)

        self.denoise_label.configure(
            text=DENOISE_NOTE["5fish"] if fork == "5fish" else DENOISE_NOTE["other"])

        if fork == "5fish":
            self.speed_hint.configure(
                text="Preset: 2 is the slowest 5fish recommends - its candidate "
                     "filtering often beats preset 0's full search. 4 is the "
                     "default and the fastest that is recommended.")
        elif fork == "hdr":
            self.speed_hint.configure(
                text="Preset: 0 is the slowest. 1 improves on 2 on grainy video "
                     "and pairs well with tune grain. 4 is the default. Faster "
                     "than 4 is not recommended.")
        else:
            self.speed_hint.configure(
                text="Preset: 0 is the slowest, 2 is very high effort, 4 is the "
                     "default and the fastest that is recommended. 3 is not "
                     "useful at this time.")

        warnings = []
        if mode == "autoboost" and fork == "hdr" and self.grain.get() == "film":
            warnings.append(
                "Auto-Boost is not recommended for high grain content: the "
                "visual metrics mistake grain for detail and over-boost it. "
                "Use Av1an single pass for grainy SVT-AV1-HDR sources.")
        if fork == "custom":
            warnings.append(
                "custom uses whatever SvtAv1EncApp.exe you placed in "
                "tools > av1an > 'svt-av1 forks' > custom, at x86-64-v3.")
        self.warning_label.configure(text="\n".join(warnings))

        try:
            self.filename_label.configure(
                text="Will create:  " + self.name_callback(self.collect()))
        except Exception:
            self.filename_label.configure(text="")

    def collect(self):
        return {
            "mode": self.mode.get(),
            "fork": self.fork.get(),
            "arch": self.arch.get() if self.fork.get() in ("5fish", "essential")
                    else "x86-64-v3",
            "crf": self.crf.get().strip(),
            "speed": self.speed.get().strip(),
            "fidelity": self.fidelity.get(),
            "grain": self.grain.get(),
            "tonemap": self.tonemap.get(),
            "autocrop": bool(self.autocrop.get()),
            "optimize_workers": bool(self.optimize.get()),
            "verbose": "--verbose" if self.verbose.get() else "--no-verbose",
        }

    def _validate(self, cfg):
        """Reject what the console version would have rejected, and no more."""
        try:
            crf = float(cfg["crf"])
        except ValueError:
            return "CRF must be a number, for example 30."
        if not CRF_MIN <= crf <= CRF_MAX:
            return f"CRF must be between {int(CRF_MIN)} and {int(CRF_MAX)}."
        try:
            speed = int(cfg["speed"])
        except ValueError:
            return "Preset speed must be a whole number, for example 4."
        if not SPEED_MIN <= speed <= SPEED_MAX:
            return f"Preset speed must be between {SPEED_MIN} and {SPEED_MAX}."
        return None

    def on_generate(self):
        cfg = self.collect()
        problem = self._validate(cfg)
        if problem:
            show_message(self.root, "Check the settings", problem, kind="error")
            return
        try:
            filename, _path = self.build_callback(cfg)
        except Exception as e:
            show_message(self.root, "Could not write the .sh", str(e),
                         kind="error")
            return
        self.status_label.configure(text="Created " + filename)
        show_message(
            self.root, "Batch script created",
            filename + "\n\nIt is in the main folder next to script-builder.sh.\n\n"
            "Drop your videos into video-input and double-click it to encode. "
            "Finished files appear in video-output.\n\n"
            "You can change the settings above and generate another one, or "
            "close this window.")


def run(build_callback, name_callback, defaults=None):
    """Open the page. build_callback(cfg) -> (filename, path).

    name_callback(cfg) -> filename is used for the live preview, so the name
    shown is the one script-builder.py will actually write.
    """
    root = tk.Tk()
    root.withdraw()
    try:
        root.call("tk", "scaling", root.winfo_fpixels("1i") / 72.0)
    except tk.TclError:
        pass
    apply_dark_theme(root)
    page = BuilderPage(root, build_callback, name_callback, defaults or {})
    dark_titlebar(root)

    # The page has to be on screen before it can be measured - see
    # fit_to_content - so it is mapped fully transparent, sized, and only then
    # made visible. Otherwise the window appears at a guessed height and snaps
    # to the right one in front of the user. A platform that will not do window
    # transparency just gets that snap; the size it lands on is the same.
    faded = True
    try:
        root.attributes("-alpha", 0.0)
    except tk.TclError:
        faded = False
    root.deiconify()
    page.fit_to_content()
    if faded:
        root.attributes("-alpha", 1.0)
    root.mainloop()
