from pathlib import Path
import shutil


# The CPU builds 5fish and essential ship. Each fork names its subfolders
# differently ("Windows_x86-64_znver2", "Clang 21.1.8 znver2", ...), so an arch
# maps to the list of needles worth looking for, best match first. AVX-512 lives
# in the icelake/znver4/znver5 folders. svt-av1-hdr ships an x86-64-v3 build
# only and ignores this entirely.
ARCH_SUBFOLDER_NEEDLES = {
    "x86-64-v3": ("x86-64-v3",),
    "znver2": ("znver2",),
    "avx512": ("icelake", "znver5", "znver4"),
}
DEFAULT_ARCH = "x86-64-v3"
ENCODER_NAME = "SvtAv1EncApp"


def normalize_arch(arch) -> str:
    """Map whatever a script or caller supplied onto one of the build names.

    A bool is accepted so the old avx512=True/False callers keep working:
    False used to mean the x86-64-v3 build, which is what it still means.
    """
    if isinstance(arch, bool):
        return "avx512" if arch else DEFAULT_ARCH
    key = str(arch or "").strip().strip('"').lower().replace("_", "-")
    if key in ("x86-64-v3", "x8664v3", "v3", "generic", "standard"):
        return "x86-64-v3"
    if key in ("znver2", "zen2", "zen"):
        return "znver2"
    if key in ("avx512", "avx-512", "x86-64-v4", "icelake", "znver4", "znver5"):
        return "avx512"
    return DEFAULT_ARCH


def cpu_supports_avx512() -> bool:
    """True when this CPU can actually run AVX-512 code."""
    try:
        with open("/proc/cpuinfo", "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.startswith("flags") and " avx512f" in line:
                    return True
    except OSError:
        pass
    return False


def _norm(text: str) -> str:
    return text.lower().replace("_", "-").replace(" ", "")


def _find_first_file(folder: Path, name: str) -> Path | None:
    direct = folder / name
    if direct.exists():
        return direct
    matches = list(folder.rglob(name))
    return matches[0] if matches else None


def setup_svt_av1_fork(tools_dir: str | Path, fork: str = "essential", arch=None,
                       avx512=None, verbose: bool = True) -> bool:
    """Select an SVT-AV1 fork binary and copy it to tools/av1an/SvtAv1EncApp.

    Fork folders are matched dynamically by name; they only need to contain
    '5fish', 'essential', 'hdr', or 'custom'. 5fish/essential pick the CPU build
    named by arch ('x86-64-v3', 'znver2' or 'avx512'), falling back to x86-64-v3
    when that fork has no such build. hdr always uses x86-64-v3.
    Essential also copies libffms2.so when present.

    arch='avx512' is downgraded to x86-64-v3 on a CPU without AVX-512, so a script
    generated on an AVX-512 machine still runs on one without it.

    avx512 is the pre-arch spelling of the same choice and is still honoured
    when arch is not given, so older callers and .bat files keep working.
    """
    if arch is None:
        arch = avx512
    arch_key = normalize_arch(arch)

    tools_dir = Path(tools_dir)
    av1an_dir = tools_dir / "av1an"
    forks_dir = av1an_dir / "svt-av1 forks"
    fork_key = (fork or "essential").strip().lower()
    if fork_key in ("svt-av1-essential", "essential"):
        match_key = "essential"
    elif fork_key in ("svt-av1-hdr", "hdr"):
        match_key = "hdr"
    elif fork_key in ("5fish", "svt-av1-psy", "psy"):
        match_key = "5fish"
    else:
        match_key = fork_key

    def log(msg: str):
        if verbose:
            print(f"[SVT Fork] {msg}")

    # An avx512 .bat built on one machine and run on another would otherwise
    # install an icelake/znver4 SvtAv1EncApp that the CPU cannot execute,
    # failing at the encode step the same way the znver4 zsmooth build fails at
    # the dehalo step. Downgrade to the build every modern CPU can run.
    if arch_key == "avx512" and not cpu_supports_avx512():
        log(f"ARCH=avx512 was selected but this CPU has no AVX-512 - using the "
            f"{DEFAULT_ARCH} build instead.")
        arch_key = DEFAULT_ARCH

    if not forks_dir.exists():
        log(f"Forks directory not found: {forks_dir}")
        return False

    candidates = [d for d in forks_dir.iterdir() if d.is_dir() and match_key in d.name.lower()]
    if not candidates and match_key == "custom":
        candidates = [forks_dir / "custom"] if (forks_dir / "custom").exists() else []
    if not candidates:
        log(f"No fork directory containing '{match_key}' found in {forks_dir}")
        return False

    fork_parent = sorted(candidates, key=lambda p: p.name.lower())[0]
    subfolders = [d for d in fork_parent.iterdir() if d.is_dir()]
    target_dir = fork_parent

    if subfolders:
        if match_key == "hdr":
            if arch_key != DEFAULT_ARCH:
                log(f"The hdr fork ships an {DEFAULT_ARCH} build only - ignoring arch '{arch_key}'.")
            wanted = ARCH_SUBFOLDER_NEEDLES[DEFAULT_ARCH]
        else:
            wanted = ARCH_SUBFOLDER_NEEDLES[arch_key]
        for needle in wanted:
            for sub in subfolders:
                if needle in sub.name.lower():
                    target_dir = sub
                    break
            if target_dir != fork_parent:
                break
        # A fork without the requested build still has the one every modern CPU
        # can run, so try that before falling back to whatever holds an exe.
        if target_dir == fork_parent and arch_key != DEFAULT_ARCH:
            for sub in subfolders:
                if DEFAULT_ARCH in sub.name.lower():
                    log(f"No {arch_key} build in {fork_parent.name} - using {DEFAULT_ARCH} instead.")
                    target_dir = sub
                    break
        if target_dir == fork_parent:
            exe_holder = next((sub for sub in subfolders if (sub / "SvtAv1EncApp").exists()), None)
            if exe_holder:
                target_dir = exe_holder

    exe_src = _find_first_file(target_dir, "SvtAv1EncApp")
    if not exe_src:
        log(f"SvtAv1EncApp not found under {target_dir}")
        return False

    av1an_dir.mkdir(parents=True, exist_ok=True)
    exe_dest = av1an_dir / "SvtAv1EncApp"
    shutil.copy2(exe_src, exe_dest)
    log(f"Copied {exe_src} -> {exe_dest}")

    # Essential needs libffms2.so beside SvtAv1EncApp.
    if match_key == "essential":
        dll_src = _find_first_file(target_dir, "libffms2.so") or _find_first_file(fork_parent, "libffms2.so")
        if dll_src:
            dll_dest = av1an_dir / "libffms2.so"
            shutil.copy2(dll_src, dll_dest)
            log(f"Copied {dll_src} -> {dll_dest}")
        else:
            log("Warning: essential fork selected but libffms2.so was not found")

    return True
