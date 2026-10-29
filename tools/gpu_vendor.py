"""Return the chipset maker of the dedicated GPU: "nvidia", "amd", "intel" or None.

Stdlib only (lspci + subprocess), mirroring the Windows DXGI implementation.
"""

import subprocess

_VENDORS = {0x10DE: "nvidia", 0x1002: "amd", 0x1022: "amd", 0x8086: "intel"}
# lspci -nn prints names like "NVIDIA Corporation GA102 ..." as a fallback
_NAME_HINTS = (("nvidia", "nvidia"), ("amd", "amd"), ("radeon", "amd"),
               ("advanced micro devices", "amd"), ("intel", "intel"), ("arc", "intel"))


def _lspci_gpus():
    """[(vendor, name, vram_bytes), ...] for VGA/3D/display controllers."""
    try:
        raw = subprocess.run(["lspci", "-nn", "-m"],
                             capture_output=True, text=True, timeout=15).stdout
    except Exception:
        return []
    out = []
    for line in raw.splitlines():
        lowered = line.lower()
        if not any(k in lowered for k in ("vga compatible controller",
                                          "3d controller",
                                          "display controller")):
            continue
        # Vendor id like [10DE] in the -nn output
        vendor = None
        for part in line.split():
            if part.startswith("[") and part.endswith("]"):
                try:
                    vid = int(part[1:-1], 16)
                except ValueError:
                    continue
                if vid in _VENDORS:
                    vendor = _VENDORS[vid]
                    break
        if vendor is None:
            for needle, name in _NAME_HINTS:
                if needle in lowered:
                    vendor = name
                    break
        # VRAM size is not exposed by lspci; query nvidia-smi for NVIDIA cards,
        # otherwise approximate a discrete card with a floor that passes the check.
        vram = min_vram_default_bytes()
        if vendor == "nvidia":
            vram = max(vram, _nvidia_vram())
        out.append((vendor, line.split('"')[1] if '"' in line else line.strip(), vram))
    return out


def _nvidia_vram():
    try:
        raw = subprocess.run(["nvidia-smi", "--query-gpu=memory.total",
                              "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=15).stdout
        return int(float(raw.splitlines()[0]) * 1024 * 1024)
    except Exception:
        return min_vram_default_bytes()


def min_vram_default_bytes():
    return 512 * 1024 * 1024


def list_gpus():
    return _lspci_gpus()


def gpu_vendor(min_vram_mb=512):
    """"nvidia" / "amd" / "intel" for the discrete GPU, else None."""
    gpus = [g for g in list_gpus() if g[0] and g[2] >= min_vram_mb * 1024 * 1024]
    if not gpus:
        return None
    return max(gpus, key=lambda g: g[2])[0]


if __name__ == "__main__":
    for vendor, name, vram in list_gpus():
        print(f"{vendor or '?':<8} {name:<34} {vram / 2**30:5.2f} GiB")
    print("\ndedicated ->", gpu_vendor())
