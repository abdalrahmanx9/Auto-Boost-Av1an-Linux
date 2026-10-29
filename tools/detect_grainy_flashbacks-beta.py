#!/usr/bin/env python3
"""
detect_grainy_flashbacks.py

Workflow (supervised calibration + detection):
1) Load clip (this may take some time)
2) Prompt user for:
     - clean frame range (e.g. 200-300)
     - grainy/flashback frame range (e.g. 1500-1900)
3) Extract DFTTest-based features for several settings (configs)
   - Uses dynamic parallelism:
       Every 10 seconds while metrics extraction is running, measure FREE CPU% and FREE RAM%.
       If >= 30% CPU free and >= 30% RAM free, start a second worker instance so two configs
       are processed at the same time (max 2 workers by default).
4) Learn what "grainy" looks like from your two example ranges and classify all frames.
5) Group into ranges and write a .txt next to input:
     <input>.mkv -> <input>.txt

TXT format (one range per line):
  <start> <end> <encoder> <crf_flag> <crf>
Example:
  615 5476 svt-av1 --crf 30
  11405 11560 svt-av1 --crf 30

Requirements:
- VapourSynth + source plugin (BestSource/LSMASHSource/FFMS2)
- vsdenoise (preferred) OR DFTTest plugin in core.dfttest/core.dfttest2
- numpy
- psutil
"""

from __future__ import annotations

# --- VapourSynth R79 plugin-path bootstrap ---
# R72 autoloaded VapourSynth\vs-plugins from the portable.vs marker. R78+ dropped
# that and reads VAPOURSYNTH_EXTRA_PLUGIN_PATH instead, so without this the
# plugins kept in vs-plugins (wwxd, fmtconv, ...) are invisible and scripts die
# with "There is no attribute or namespace named <plugin>".
# Must run before vapoursynth/vstools pull in a core. Child processes inherit it.
import os as _bootstrap_os

_PLUGIN_ENV_VAR = "VAPOURSYNTH_EXTRA_PLUGIN_PATH"


def _ensure_vs_plugin_path():
    root_dir = _bootstrap_os.path.dirname(_bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__)))
    found = _bootstrap_os.path.join(root_dir, "VapourSynth", "vs-plugins")
    if not _bootstrap_os.path.isdir(found):
        return ""

    existing = [part for part in _bootstrap_os.environ.get(_PLUGIN_ENV_VAR, "").split(_bootstrap_os.pathsep) if part]
    if not existing:
        # Set a single path: valid whether the core reads one path or a list.
        _bootstrap_os.environ[_PLUGIN_ENV_VAR] = found
    elif not any(_bootstrap_os.path.normcase(part) == _bootstrap_os.path.normcase(found) for part in existing):
        _bootstrap_os.environ[_PLUGIN_ENV_VAR] = _bootstrap_os.pathsep.join(existing + [found])

    return found


_ensure_vs_plugin_path()
# --- end bootstrap ---

import argparse
import json
import math
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple, Union

import numpy as np
import psutil
# Silence the core's plugin-load chatter (API3 deprecation notices, duplicate
# plugin DLLs). Must run before the first vs.core access, since that is what
# triggers the autoload that emits them.
try:
    import sys as _quiet_sys, os as _quiet_os
    _quiet_dir = _quiet_os.path.dirname(_quiet_os.path.abspath(__file__))
    if _quiet_dir not in _quiet_sys.path:
        _quiet_sys.path.insert(0, _quiet_dir)
    from vs_quiet import silence_plugin_noise as _silence_plugin_noise
    _silence_plugin_noise()
except Exception:
    pass

import vapoursynth as vs

core = vs.core


# ----------------------------
# Progress UI (stderr)
# ----------------------------

def _fmt_hms(seconds: float) -> str:
    if not np.isfinite(seconds) or seconds < 0:
        return "??:??:??"
    s = int(round(seconds))
    h = s // 3600
    m = (s % 3600) // 60
    sec = s % 60
    return f"{h:02d}:{m:02d}:{sec:02d}"


class Progress:
    """
    Single-line progress to STDERR:
      Phase x/x <title> | <percent>% | ETA <hh:mm:ss>
    Updated at most once per second (and once at completion).
    """
    def __init__(self, title: str, total: int):
        self.title = str(title)
        self.total = max(int(total), 0)
        self.start = time.perf_counter()
        self.last_print = 0.0
        self._printed_any = False

    def update(self, current: int) -> None:
        if self.total <= 0:
            if not self._printed_any:
                self._print_line(1.0, 0.0, force=True)
                sys.stderr.write("\n")
                sys.stderr.flush()
                self._printed_any = True
            return

        cur = max(0, min(int(current), self.total))
        now = time.perf_counter()
        force = (cur >= self.total)

        if (now - self.last_print) < 1.0 and not force:
            return

        elapsed = now - self.start
        frac = cur / self.total if self.total else 1.0
        rate = cur / elapsed if elapsed > 0 and cur > 0 else 0.0
        remaining = (self.total - cur) / rate if rate > 0 else float("inf")

        self._print_line(frac, remaining, force=force)
        self.last_print = now
        self._printed_any = True

        if force:
            sys.stderr.write("\n")
            sys.stderr.flush()

    def _print_line(self, frac: float, eta_seconds: float, force: bool = False) -> None:
        pct = frac * 100.0
        line = f"\r{self.title} | {pct:6.2f}% | ETA {_fmt_hms(eta_seconds)}"
        sys.stderr.write(line.ljust(130))
        sys.stderr.flush()


class PhaseManager:
    """Creates progress trackers with a Phase x/x prefix."""
    def __init__(self, total_phases: int):
        self.total = max(int(total_phases), 1)
        self.i = 0

    def make(self, title: str, total_steps: int) -> Progress:
        self.i += 1
        return Progress(f"Phase {self.i}/{self.total} {title}", total_steps)


# ----------------------------
# Data classes
# ----------------------------

@dataclass(frozen=True)
class DFTConfig:
    name: str
    sigma: float = 40.0
    tbsize: int = 1
    sbsize: int = 16
    sosize: int = 12
    luma_mask: Optional[Tuple[float, float]] = None  # not required by core DFTTest; used for vsdenoise curve


@dataclass
class WorkerJob:
    cfg: DFTConfig
    npz_path: Path
    proc: subprocess.Popen
    start_time: float


# ----------------------------
# VapourSynth helpers
# ----------------------------

def _pick_source(path: str) -> vs.VideoNode:
    if hasattr(core, "bs"):
        return core.bs.VideoSource(path)
    if hasattr(core, "lsmas"):
        return core.lsmas.LWLibavSource(path)
    if hasattr(core, "ffms2"):
        return core.ffms2.Source(path)
    raise RuntimeError("No source plugin found. Install BestSource (bs), L-SMASH-Works (lsmas), or FFMS2.")


def _to_gray16(clip: vs.VideoNode, matrix: str = "709", full_range: bool = False) -> vs.VideoNode:
    fmt = clip.format
    if fmt is None:
        raise RuntimeError("Clip has no format?")

    if fmt.color_family == vs.GRAY:
        g = clip
    elif fmt.color_family == vs.YUV:
        g = core.std.ShufflePlanes(clip, 0, vs.GRAY)
    elif fmt.color_family == vs.RGB:
        g = core.resize.Bicubic(
            clip,
            format=vs.GRAY16,
            matrix_in_s=matrix,
            range_in_s="full" if full_range else "limited",
        )
        return g
    else:
        try:
            g = core.std.ShufflePlanes(clip, 0, vs.GRAY)
        except Exception as e:
            raise RuntimeError(f"Unsupported color family {fmt.color_family} and fallback failed: {e}")

    if g.format.sample_type != vs.INTEGER or g.format.bits_per_sample != 16:
        g = core.resize.Bicubic(g, format=vs.GRAY16)
    return g


def _resize_for_analysis(g: vs.VideoNode, target_w: int = 640) -> vs.VideoNode:
    if g.width <= target_w:
        return g
    new_w = target_w
    new_h = int(round(g.height * (new_w / g.width)))
    new_h = max(2, new_h - (new_h % 2))
    return core.resize.Bicubic(g, new_w, new_h)


def _find_core_dfttest_function() -> Optional[Callable[..., vs.VideoNode]]:
    for mod_name in ("dfttest", "dfttest2"):
        if hasattr(core, mod_name):
            mod = getattr(core, mod_name)
            for fn_name in ("DFTTest", "Dfttest"):
                if hasattr(mod, fn_name):
                    fn = getattr(mod, fn_name)
                    if callable(fn):
                        return fn
    return None


def _sigma_curve_from_luma_mask(cfg: DFTConfig) -> Dict[float, float]:
    if cfg.luma_mask is None:
        return {0.0: float(cfg.sigma), 1.0: float(cfg.sigma)}

    lo, hi = cfg.luma_mask
    lo = float(np.clip(lo, 0.0, 1.0))
    hi = float(np.clip(hi, 0.0, 1.0))
    if hi < lo:
        lo, hi = hi, lo

    curve: Dict[float, float] = {0.0: 0.0, 1.0: 0.0}
    curve[0.0 if lo <= 0.0 else lo] = float(cfg.sigma)
    curve[1.0 if hi >= 1.0 else hi] = float(cfg.sigma)
    return curve


def _dfttest_denoise(g16: vs.VideoNode, cfg: DFTConfig) -> vs.VideoNode:
    # Preferred: vsdenoise wrapper
    try:
        from vsdenoise import DFTTest as VSDFTTest  # type: ignore
        dft = VSDFTTest()

        sigma_param: Union[float, Dict[float, float]]
        if cfg.luma_mask is not None:
            sigma_param = _sigma_curve_from_luma_mask(cfg)
        else:
            sigma_param = float(cfg.sigma)

        attempts = [
            lambda: dft.denoise(
                g16, sigma_param,
                tbsize=int(cfg.tbsize), sbsize=int(cfg.sbsize), sosize=int(cfg.sosize),
                planes=[0]
            ),
            lambda: dft.denoise(g16, sigma_param, tbsize=int(cfg.tbsize), planes=[0]),
            lambda: dft.denoise(g16, sigma_param, planes=[0]),
        ]
        if not isinstance(sigma_param, dict):
            curve = {0.0: float(cfg.sigma), 1.0: float(cfg.sigma)}
            attempts += [
                lambda: dft.denoise(
                    g16, curve,
                    tbsize=int(cfg.tbsize), sbsize=int(cfg.sbsize), sosize=int(cfg.sosize),
                    planes=[0]
                ),
                lambda: dft.denoise(g16, curve, tbsize=int(cfg.tbsize), planes=[0]),
                lambda: dft.denoise(g16, curve, planes=[0]),
            ]

        last_err: Optional[Exception] = None
        for a in attempts:
            try:
                return a()
            except Exception as e:
                last_err = e
        raise RuntimeError(f"vsdenoise.DFTTest exists but all call signatures failed. Last error: {last_err}")

    except ModuleNotFoundError:
        pass

    # Fallback: core plugin
    core_fn = _find_core_dfttest_function()
    if core_fn is None:
        raise RuntimeError(
            "DFTTest not available. Install vsdenoise (preferred) or install a DFTTest plugin providing core.dfttest."
        )

    try:
        return core_fn(
            g16,
            sigma=float(cfg.sigma),
            tbsize=int(cfg.tbsize),
            sbsize=int(cfg.sbsize),
            sosize=int(cfg.sosize),
            planes=[0],
        )
    except vs.Error as e:
        raise RuntimeError(
            "Found core.dfttest/core.dfttest2, but it doesn't accept sigma/tbsize/sbsize/sosize.\n"
            "Recommended: use vsdenoise.DFTTest() (Python wrapper).\n"
            f"Original error: {e}"
        )


def _extract_metrics_worker(
    input_path: str,
    cfg: DFTConfig,
    out_npz: str,
    matrix: str,
    full_range: bool,
    analysis_width: int,
    eps: float,
) -> None:
    """
    Worker process body: compute arrays for a single config and save to NPZ.
    Saves:
      noise_level, ratio, luma_diff
    """
    clip = _pick_source(input_path)
    g16 = _to_gray16(clip, matrix=matrix, full_range=full_range)
    g16 = _resize_for_analysis(g16, target_w=int(analysis_width))

    den = _dfttest_denoise(g16, cfg)

    gf = core.resize.Bicubic(g16, format=vs.GRAYS)
    df = core.resize.Bicubic(den, format=vs.GRAYS)

    res = core.std.Expr([gf, df], "x y -")
    abs_res = core.std.Expr(res, "x abs")

    nl_clip = core.std.PlaneStats(abs_res, prop="NL")

    l_curr = gf[1:]
    l_prev = gf[:-1]
    ld_clip = core.std.PlaneStats(l_curr, l_prev, prop="LD")

    n_curr = abs_res[1:]
    n_prev = abs_res[:-1]
    nd_clip = core.std.PlaneStats(n_curr, n_prev, prop="ND")

    n = int(g16.num_frames)
    noise_level = np.zeros(n, dtype=np.float32)
    luma_diff = np.zeros(n, dtype=np.float32)
    noise_diff = np.zeros(n, dtype=np.float32)

    for i in range(n):
        f = nl_clip.get_frame(i)
        noise_level[i] = float(f.props["NLAverage"])

    for i in range(1, n):
        f_ld = ld_clip.get_frame(i - 1)
        f_nd = nd_clip.get_frame(i - 1)
        luma_diff[i] = float(f_ld.props["LDDiff"])
        noise_diff[i] = float(f_nd.props["NDDiff"])

    ratio = noise_diff / (luma_diff + float(eps))

    np.savez_compressed(
        out_npz,
        cfg_name=np.array([cfg.name]),
        noise_level=noise_level,
        ratio=ratio.astype(np.float32),
        luma_diff=luma_diff.astype(np.float32),
    )


# ----------------------------
# Classification + grouping
# ----------------------------

def _scene_boundaries_from_luma_diff(luma_diff: np.ndarray, q: float = 0.995, floor: float = 0.18) -> List[int]:
    thr = max(float(np.quantile(luma_diff, q)), float(floor))
    cuts = np.where(luma_diff >= thr)[0].tolist()
    b = sorted(set([0] + cuts + [len(luma_diff)]))
    out = [b[0]]
    for x in b[1:]:
        if x - out[-1] >= 2:
            out.append(x)
    if out[-1] != len(luma_diff):
        out.append(len(luma_diff))
    return out


def _apply_scene_grouping(mask: np.ndarray, luma_diff: np.ndarray, scene_frac: float) -> np.ndarray:
    bounds = _scene_boundaries_from_luma_diff(luma_diff)
    out = np.zeros_like(mask, dtype=bool)
    for a, b in zip(bounds[:-1], bounds[1:]):
        seg = mask[a:b]
        if len(seg) and float(np.mean(seg)) >= float(scene_frac):
            out[a:b] = True
    return out


def _ranges_from_mask(mask: np.ndarray, min_len: int, merge_gap: int) -> List[Tuple[int, int]]:
    ranges: List[Tuple[int, int]] = []
    start: Optional[int] = None

    for i, v in enumerate(mask):
        if v and start is None:
            start = i
        elif (not v) and start is not None:
            end = i - 1
            if end - start + 1 >= min_len:
                ranges.append((start, end))
            start = None

    if start is not None:
        end = len(mask) - 1
        if end - start + 1 >= min_len:
            ranges.append((start, end))

    if not ranges:
        return []

    merged = [ranges[0]]
    for s, e in ranges[1:]:
        ps, pe = merged[-1]
        if s - pe - 1 <= merge_gap:
            merged[-1] = (ps, e)
        else:
            merged.append((s, e))
    return merged


def _robust_z(x: np.ndarray) -> np.ndarray:
    x = x.astype(np.float64, copy=False)
    med = np.median(x)
    mad = np.median(np.abs(x - med))
    scale = mad * 1.4826
    if scale <= 1e-12:
        scale = float(np.std(x) + 1e-6)
    return (x - med) / scale


def _calibrate_classifier(
    ratio_med: np.ndarray,
    noise_med: np.ndarray,
    clean_range: Tuple[int, int],
    grainy_range: Tuple[int, int],
) -> Tuple[np.ndarray, Dict[str, float]]:
    """
    Learn a simple 2D linear classifier from user examples:
      features = [robust_z(ratio_med), robust_z(noise_med)]
    """
    n = len(ratio_med)
    c0, c1 = clean_range
    g0, g1 = grainy_range

    z_ratio = _robust_z(ratio_med)
    z_noise = _robust_z(noise_med)
    X = np.column_stack([z_ratio, z_noise])

    idx_c = np.arange(c0, c1 + 1)
    idx_g = np.arange(g0, g1 + 1)

    Xc = X[idx_c]
    Xg = X[idx_g]

    mc = Xc.mean(axis=0)
    mg = Xg.mean(axis=0)

    cov_c = np.cov(Xc.T) if Xc.shape[0] > 1 else np.eye(2) * 1e-3
    cov_g = np.cov(Xg.T) if Xg.shape[0] > 1 else np.eye(2) * 1e-3
    Sw = (cov_c + cov_g) / 2.0 + np.eye(2) * 1e-3

    w = np.linalg.solve(Sw, (mg - mc))
    scores = X @ w

    med_c = float(np.median(scores[idx_c]))
    med_g = float(np.median(scores[idx_g]))
    thr = 0.5 * (med_c + med_g)

    grainy_high = (med_g >= med_c)
    mask = scores >= thr if grainy_high else scores <= thr

    diag = {
        "median_score_clean": med_c,
        "median_score_grainy": med_g,
        "threshold": float(thr),
        "grainy_high": 1.0 if grainy_high else 0.0,
        "selected_frames": float(np.sum(mask)),
        "total_frames": float(n),
    }
    return mask.astype(bool), diag


# ----------------------------
# User input parsing
# ----------------------------

def _parse_range(s: str, n_frames: int) -> Tuple[int, int]:
    s = s.strip()
    if "-" not in s:
        raise ValueError("Range must be in form start-end")
    a_str, b_str = s.split("-", 1)
    a = int(a_str.strip())
    b = int(b_str.strip())
    if a > b:
        a, b = b, a
    if a < 0 or b < 0:
        raise ValueError("Range must be non-negative")
    if a >= n_frames or b >= n_frames:
        raise ValueError(f"Range must be within 0-{n_frames - 1}")
    return a, b


def _prompt_range(prompt: str, n_frames: int) -> Tuple[int, int]:
    while True:
        try:
            s = input(prompt)
            return _parse_range(s, n_frames)
        except Exception as e:
            print(f"Invalid range: {e}")


# ----------------------------
# Resource monitoring + scheduling
# ----------------------------

def _get_free_cpu_ram_percent() -> Tuple[float, float]:
    """
    Returns (free_cpu_percent, free_ram_percent).
    CPU free = 100 - cpu_used.
    RAM free = available/total * 100.
    """
    cpu_used = psutil.cpu_percent(interval=0.5)  # brief sample
    free_cpu = max(0.0, 100.0 - float(cpu_used))

    vm = psutil.virtual_memory()
    free_ram = 100.0 * float(vm.available) / float(vm.total) if vm.total else 0.0
    return free_cpu, free_ram


def _start_worker(
    script_path: Path,
    input_path: str,
    cfg: DFTConfig,
    npz_path: Path,
    matrix: str,
    full_range: bool,
    analysis_width: int,
    eps: float,
) -> WorkerJob:
    cmd = [
        sys.executable,
        str(script_path),
        "--worker",
        "--input",
        input_path,
        "--out-npz",
        str(npz_path),
        "--cfg-name",
        cfg.name,
        "--cfg-sigma",
        str(cfg.sigma),
        "--cfg-tbsize",
        str(cfg.tbsize),
        "--cfg-sbsize",
        str(cfg.sbsize),
        "--cfg-sosize",
        str(cfg.sosize),
        "--matrix",
        matrix,
        "--analysis-width",
        str(int(analysis_width)),
        "--eps",
        str(float(eps)),
    ]
    if full_range:
        cmd.append("--full-range")
    if cfg.luma_mask is not None:
        cmd += ["--cfg-luma-mask", str(cfg.luma_mask[0]), str(cfg.luma_mask[1])]

    # Quiet worker output (keeps main progress clean). Errors still go to console.
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=None)
    return WorkerJob(cfg=cfg, npz_path=npz_path, proc=proc, start_time=time.time())


def _run_metrics_with_dynamic_parallelism(
    configs: List[DFTConfig],
    script_path: Path,
    input_path: str,
    tmp_dir: Path,
    phases: PhaseManager,
    matrix: str,
    full_range: bool,
    analysis_width: int,
    eps: float,
    max_workers: int,
    check_interval_s: int,
    free_cpu_thresh: float,
    free_ram_thresh: float,
) -> List[Path]:
    """
    Runs workers over configs, starting with 1 worker and scaling up to 2 if resources allow.
    Returns list of NPZ paths in the same order as configs.
    """
    if max_workers < 1:
        max_workers = 1
    if max_workers > 2:
        # User request specifically mentions two phases at once; cap at 2.
        max_workers = 2

    pending = list(configs)
    running: List[WorkerJob] = []
    out_paths: Dict[str, Path] = {}

    # Progress phase: overall metrics completion count
    p = phases.make("[Metrics] Extract features (auto-parallel up to 2 workers)", total_steps=len(configs))
    completed = 0

    # Seed one worker immediately
    if pending:
        cfg = pending.pop(0)
        npz = tmp_dir / f"{cfg.name}.npz"
        running.append(_start_worker(script_path, input_path, cfg, npz, matrix, full_range, analysis_width, eps))
        out_paths[cfg.name] = npz

    last_resource_check = time.time()

    # Baseline cpu_percent so later reads are meaningful
    try:
        psutil.cpu_percent(interval=None)
    except Exception:
        pass

    while running or pending:
        # Poll running jobs
        for job in running[:]:
            ret = job.proc.poll()
            if ret is None:
                continue
            running.remove(job)
            if ret != 0:
                raise RuntimeError(f"Worker for config {job.cfg.name} failed with exit code {ret}")
            completed += 1
            p.update(completed)

        # Always keep at least 1 worker running if pending exists
        while pending and len(running) == 0:
            cfg = pending.pop(0)
            npz = tmp_dir / f"{cfg.name}.npz"
            running.append(_start_worker(script_path, input_path, cfg, npz, matrix, full_range, analysis_width, eps))
            out_paths[cfg.name] = npz

        # Every check_interval_s while a worker is running, see if we can start a second one
        now = time.time()
        if pending and running and len(running) < max_workers and (now - last_resource_check) >= float(check_interval_s):
            last_resource_check = now
            free_cpu, free_ram = _get_free_cpu_ram_percent()
            if free_cpu >= float(free_cpu_thresh) and free_ram >= float(free_ram_thresh):
                cfg = pending.pop(0)
                npz = tmp_dir / f"{cfg.name}.npz"
                running.append(_start_worker(script_path, input_path, cfg, npz, matrix, full_range, analysis_width, eps))
                out_paths[cfg.name] = npz

        # Keep progress ticking for ETA even if no completions in this second
        p.update(completed)
        time.sleep(0.2)

    # Mark completion
    p.update(len(configs))

    # Return paths in config order
    return [out_paths[c.name] for c in configs]


# ----------------------------
# Phase count
# ----------------------------

def _compute_total_phases() -> int:
    # 1) init
    # 2) metrics
    # 3) aggregate
    # 4) calibrate
    # 5) group
    # 6) ranges
    # 7) write
    return 7


# ----------------------------
# Worker CLI
# ----------------------------

def _worker_main(args: argparse.Namespace) -> None:
    cfg = DFTConfig(
        name=args.cfg_name,
        sigma=float(args.cfg_sigma),
        tbsize=int(args.cfg_tbsize),
        sbsize=int(args.cfg_sbsize),
        sosize=int(args.cfg_sosize),
        luma_mask=tuple(args.cfg_luma_mask) if args.cfg_luma_mask is not None else None,
    )
    _extract_metrics_worker(
        input_path=args.input,
        cfg=cfg,
        out_npz=args.out_npz,
        matrix=args.matrix,
        full_range=bool(args.full_range),
        analysis_width=int(args.analysis_width),
        eps=float(args.eps),
    )


# ----------------------------
# Main
# ----------------------------

def main() -> None:
    ap = argparse.ArgumentParser()

    # Common args
    ap.add_argument("--input", help="Input video path (worker mode).")
    ap.add_argument("input_pos", nargs="?", help="Input video path (normal mode).")

    ap.add_argument("--matrix", default="709", help="RGB->GRAY matrix if input is RGB. Default: 709")
    ap.add_argument("--full-range", action="store_true", help="Treat RGB as full range when converting")
    ap.add_argument("--analysis-width", type=int, default=640, help="Downscale width for analysis. Default: 640")
    ap.add_argument("--eps", type=float, default=1e-6, help="Epsilon for ratio division. Default: 1e-6")

    ap.add_argument("--scene-frac", type=float, default=0.35, help="Keep whole scene if >= this fraction flagged. Default: 0.35")
    ap.add_argument("--min-len", type=int, default=12, help="Minimum run length in frames. Default: 12")
    ap.add_argument("--merge-gap", type=int, default=6, help="Merge gaps up to N frames. Default: 6")

    ap.add_argument("--encoder", default="svt-av1", help='Encoder token written to txt. Default: "svt-av1"')
    ap.add_argument("--crf", type=int, default=30, help="CRF value written to txt. Default: 30")
    ap.add_argument("--crf-flag", default="--crf", help='CRF flag written to txt. Default: "--crf"')

    ap.add_argument("--configs", help="Optional JSON file: list of DFT configs (sigma/tbsize/...).")

    # Dynamic parallelism knobs
    ap.add_argument("--max-workers", type=int, default=2, help="Max worker instances (capped at 2). Default: 2")
    ap.add_argument("--resource-check-interval", type=int, default=10, help="Seconds between resource checks. Default: 10")
    ap.add_argument("--free-cpu-threshold", type=float, default=30.0, help="Free CPU%% required to start 2nd worker. Default: 30")
    ap.add_argument("--free-ram-threshold", type=float, default=30.0, help="Free RAM%% required to start 2nd worker. Default: 30")

    # Worker-only args
    ap.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--out-npz", help=argparse.SUPPRESS)
    ap.add_argument("--cfg-name", help=argparse.SUPPRESS)
    ap.add_argument("--cfg-sigma", help=argparse.SUPPRESS)
    ap.add_argument("--cfg-tbsize", help=argparse.SUPPRESS)
    ap.add_argument("--cfg-sbsize", help=argparse.SUPPRESS)
    ap.add_argument("--cfg-sosize", help=argparse.SUPPRESS)
    ap.add_argument("--cfg-luma-mask", nargs=2, type=float, help=argparse.SUPPRESS)

    args = ap.parse_args()

    # Worker mode
    if args.worker:
        if not args.input:
            raise SystemExit("Worker mode requires --input")
        if not args.out_npz:
            raise SystemExit("Worker mode requires --out-npz")
        _worker_main(args)
        return

    # Normal mode
    input_path = args.input_pos
    if not input_path:
        raise SystemExit("Usage: detect_grainy_flashbacks.py <input.mkv>")

    # Default configs (unchanged)
    configs: List[DFTConfig] = [
        DFTConfig(name="A_sigma40_tb1", sigma=40.0, tbsize=1, luma_mask=(0.08, 0.32)),
        DFTConfig(name="B_sigma32_tb1", sigma=32.0, tbsize=1, luma_mask=(0.06, 0.36)),
        DFTConfig(name="C_sigma48_tb3", sigma=48.0, tbsize=3, luma_mask=(0.08, 0.32)),
    ]

    # Optional configs.json
    if args.configs:
        data = json.loads(Path(args.configs).read_text(encoding="utf-8"))
        configs = []
        for item in data:
            cfg = DFTConfig(
                name=str(item.get("name", f"cfg{len(configs)}")),
                sigma=float(item.get("sigma", 40.0)),
                tbsize=int(item.get("tbsize", 1)),
                sbsize=int(item.get("sbsize", 16)),
                sosize=int(item.get("sosize", 12)),
                luma_mask=tuple(item["luma_mask"]) if "luma_mask" in item and item["luma_mask"] else None,
            )
            configs.append(cfg)

    phases = PhaseManager(_compute_total_phases())

    # Phase 1: Init (edited to include "this may take some time")
    p0 = phases.make("[Init] Load + prep clip (this may take some time)", 4)
    p0.update(1)
    clip = _pick_source(input_path)
    p0.update(2)
    g16 = _to_gray16(clip, matrix=args.matrix, full_range=bool(args.full_range))
    p0.update(3)
    g16 = _resize_for_analysis(g16, target_w=int(args.analysis_width))
    p0.update(4)

    n_frames = int(g16.num_frames)

    # Prompt user at start (as requested)
    clean_range = _prompt_range("Provide clean frame range for testing, example: 200-300: ", n_frames)
    grainy_range = _prompt_range("Provide grainy frame range for testing, example: 1500-1900: ", n_frames)

    # Phase 2: Metrics extraction (dynamic 1->2 workers)
    script_path = Path(__file__).resolve()

    tmp_root = Path(tempfile.mkdtemp(prefix="grainy_flashbacks_"))
    try:
        npz_paths = _run_metrics_with_dynamic_parallelism(
            configs=configs,
            script_path=script_path,
            input_path=input_path,
            tmp_dir=tmp_root,
            phases=phases,
            matrix=args.matrix,
            full_range=bool(args.full_range),
            analysis_width=int(args.analysis_width),
            eps=float(args.eps),
            max_workers=int(args.max_workers),
            check_interval_s=int(args.resource_check_interval),
            free_cpu_thresh=float(args.free_cpu_threshold),
            free_ram_thresh=float(args.free_ram_threshold),
        )

        # Load results
        ratios_list: List[np.ndarray] = []
        noises_list: List[np.ndarray] = []
        luma_diff_ref: Optional[np.ndarray] = None

        for pth in npz_paths:
            data = np.load(str(pth), allow_pickle=True)
            noise_level = data["noise_level"].astype(np.float32)
            ratio = data["ratio"].astype(np.float32)
            luma_diff = data["luma_diff"].astype(np.float32)

            noises_list.append(noise_level)
            ratios_list.append(ratio)
            if luma_diff_ref is None:
                luma_diff_ref = luma_diff

        assert luma_diff_ref is not None

        # Phase 3: Aggregate features
        p_ag = phases.make("[Aggregate] Combine features across settings", 3)
        p_ag.update(1)
        ratio_med = np.median(np.stack(ratios_list, axis=0), axis=0).astype(np.float32)
        p_ag.update(2)
        noise_med = np.median(np.stack(noises_list, axis=0), axis=0).astype(np.float32)
        p_ag.update(3)

        # Phase 4: Calibrate
        p_cal = phases.make("[Calibrate] Learn grainy-vs-clean from your examples", 2)
        p_cal.update(1)
        raw_mask, diag = _calibrate_classifier(
            ratio_med=ratio_med,
            noise_med=noise_med,
            clean_range=clean_range,
            grainy_range=grainy_range,
        )
        p_cal.update(2)

        # Phase 5: Group
        p_grp = phases.make("[Group] Scene-aware grouping", 2)
        p_grp.update(1)
        grouped_mask = _apply_scene_grouping(raw_mask, luma_diff_ref, scene_frac=float(args.scene_frac))
        p_grp.update(2)

        # Phase 6: Ranges
        p_rng = phases.make("[Ranges] Build ranges", 2)
        p_rng.update(1)
        ranges = _ranges_from_mask(grouped_mask, min_len=int(args.min_len), merge_gap=int(args.merge_gap))
        p_rng.update(2)

        # Phase 7: Write
        p_wr = phases.make("[Write] Output txt", 2)
        p_wr.update(1)

        in_path = Path(input_path)
        out_path = in_path.with_suffix(".txt")

        encoder = str(args.encoder).strip()
        crf_flag = str(args.crf_flag).strip()
        crf_val = int(args.crf)

        lines: List[str] = []
        for a, b in ranges:
            lines.append(f"{a} {b} {encoder} {crf_flag} {crf_val}")

        out_text = ("\n".join(lines) + "\n") if lines else ""
        out_path.write_text(out_text, encoding="utf-8")

        p_wr.update(2)

        # Human summary
        if ranges:
            print(f"Wrote {len(ranges)} range(s) to: {out_path}")
        else:
            print(
                f"No ranges detected (txt written empty): {out_path}\n"
                f"Calibration diagnostics: threshold={diag['threshold']:.4f}, "
                f"median_clean={diag['median_score_clean']:.4f}, median_grainy={diag['median_score_grainy']:.4f}, "
                f"grainy_high={int(diag['grainy_high'])}, selected_frames={int(diag['selected_frames'])}/{int(diag['total_frames'])}"
            )

    finally:
        # Best-effort cleanup of temp dir
        try:
            for child in tmp_root.glob("*"):
                try:
                    child.unlink()
                except Exception:
                    pass
            tmp_root.rmdir()
        except Exception:
            pass


if __name__ == "__main__":
    main()
