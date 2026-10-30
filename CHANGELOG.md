# Changelog

All notable changes to the Linux Port of Auto-Boost-Av1an will be documented in this file.

## [3.0.0-linux] - 2026-10-29

Full catch-up with the Windows suite (feature parity with Windows v3.4.5; Linux version numbers are independent of Windows ones).

### Removed
- **Progression Boost** suite (removed upstream in Windows v1.64): all scripts, dispatchers, muxers, taggers and worker benchmarks for it.
- **fssimu2** backend and installer module (replaced upstream by the FFVship / vs-hip / vs-zip metric ladder).
- Legacy `run_linux_*` scripts, replaced by the premade + script-builder workflow below.

### Changed
- **Folder layout**: `Input/`/`Output/` renamed to `video-input/`/`video-output/` matching the Windows package.
- **Main engine** moved to `tools/Auto-Boost-Av1an.py` (Windows layout).
- **Installer**: VapourSynth R73 -> **R79**, vsjetpack pinned **2.2.2**, Vship **5.1.0** (builds CUDA/HIP/Vulkan libvship + FFVship variants into `tools/vs-hip` + `tools/FFVship`), VSZIP **R19**, SVT-AV1 installed as a fork scheme (`tools/av1an/svt-av1 forks/<5fish|essential|hdr|custom>/<arch>/`), new modules: filter plugins (akarin, AWarp, DFTTest, zsmooth, vs-placebo), vs-mlrt (AI rescale), Condor/LQTC layout.
- **Dependency Pinning**: 100% of all git clone and build dependencies across all 13 modules locked to exact verified commit hashes in `DEPENDENCIES.md` for reproducible builds.
- **Non-Interactive Setup**: Full automated batch install/uninstall support (`sudo ./setup.sh --install all -y` and `sudo ./setup.sh --uninstall all -y`) with component status check (`--status`).
- **AV1 Decoder Integration**: Added `libdav1d-dev` and compiled FFmpeg master with `--enable-libdav1d` to ensure FFMS2 and BestSource decode AV1 fast-pass video without empty frame errors.
- **VapourSynth R79 Compatibility**: Robust BestSource patch and filter plugin installation loop in `setup/vs_filters.sh`.

### Added - Encoding workflows
- **Fork system** (`--fork 5fish|essential|hdr|custom`, `--arch x86-64-v3|znver2|avx512`): dynamic SVT-AV1 fork selection with AVX-512 detection via `/proc/cpuinfo`.
- **HDR support**: svt-av1-hdr fork, automatic HDR color signaling (primaries/transfer/matrix/mastering display/content light via MediaInfo), and HDR->SDR **tonemapping** (libplacebo GPU path with CPU BT.2390 fallback; Intel-GPU black-frame workaround kept).
- **Direct Av1an mode** (`tools/av1an-dispatch.py`): single-pass encodes with the full settings.txt filter chain, zones machinery (zone flags, `reset` keyword, zones.txt in-place repair with backups, sliver-merge, zoned scenes JSON).
- **Condor target-quality encoding** (user-supplied `condor` Linux binary): metric targets (ssimulacra2/cvvdp/butteraugli), quantizer ranges, fast/standard/slow target profiles, scenes injected with `--skip-scd`, photon-noise ISO tables on condor itself.
- **LQTC target-quality encoding** (user-supplied `lqtc-*` binaries): metric chosen by target (butteraugli <8, CVVDP 8-10 with display.json, SSIMULACRA2 >10), CRF-range search, metric workers/mode.
- **AfterZone**: re-encode chosen frame ranges of finished encodes across av1an/LQTC/Condor working folders; auto zone generation from bitrate (rolling median + threshold) with a matplotlib chart; +CRF/--tf-strength boost automation; batch mode; resumable markers.
- **Zones**: scene-based per-zone encoder settings, `zones-example.txt` format, `--zones` override.
- **Lossless intermediary mode**: filter chain runs once into a lossless x264 mkv; encodes read that file (offered by rescale options).
- **template.vpy mode**: write your settings.txt filters as an editable VapourSynth script; template owns cropping while present; three ready-made rescale sections (commented): DirectML, NVIDIA TensorRT (vs-mlrt), AMD; template-preview.sh renders it.
- **AI rescaling**: vs-mlrt backends (TRT/ORT/MIGX) + ArtCNN models via vsscale into `<repo>/.vsjet`.
- **ntfy phone notifications** on encode completion (`extras/ntfy-setup.sh`).
- **Rich progress bars** (simple mode) for every phase, with beginner-friendly explanations; `--verbose` for full output.
- **MKV tagging**: settings written into `Encoded_Library_Settings` (media info visible), system-specific flags filtered out, tag-edit.py interactive editor.

### Added - Filters (settings.txt)
- `[crop]` auto/manual/off with all four edges; `[downscaling]` (8 kernels); `[dehalo]` (vsdehalo edge_cleaner + AWarp); `[fine_dehalo]` (mask pipeline, akarin/vszip/zsmooth); `[denoise]` (raw VS line, DFTTest example); `[deband]` (placebo); dehalo/fine_dehalo mutual exclusion; filter-state rebuild markers so toggles take effect on re-run; `tonemap_backend` key.
- `settings-preview.sh` previews the chain side by side before encoding.

### Added - Benchmarks
- **workercount.py**: real-throughput benchmark through the actual filter chain and preset; RAM kill-switch; fps settle detection; workers=1 fail-safe; `--optimize-script` writes tuned values into the running script (custom_av1an_workers etc.).
- **ssimu2-workercount.py**: benchmarks every vship backend your GPU vendor can run (nvidia/amd/vulkan plans via lspci), FFVship CLI runs with stall watchdogs, vs-zip CPU fallback; key-based `workercount-ssimu2.txt` (tool=/filter-tool=/workercount=/variant=/streams=).
- **Worker config auto-repair** (tolerant reads of hand-edited configs).

### Added - Tools & extras
- `script-builder.sh` (bat-builder port): generates encode scripts with the same questions/presets as Windows, advanced tools menu (Condor/LQTC/AfterZone setup, source filter override, template.vpy writer with rescale + lossless pages), optional Tk GUI (menu entry 0).
- Filenames auto-folded to plain ASCII (transliteration) before any tool sees them.
- Mid-encode file scanning: files dropped into video-input during a batch are picked up.
- `extras/create-discord-10mb-sample.sh`, `extras/clean-temp-files.sh`, `extras/photon-film-grain-test.sh` (photon-noise / film-grain strength sweeps), `extras/mkvpropedit-gui.sh` (stand-in for the Windows JMkvpropedit/Inviska/Breeze GUIs).
- Audio tools renamed to `audio-opus.py`/`audio-ac3.py`/`audio-eac3.py` with **zlib-subtitles=False** setting and source track-title carryover.
- `tools/x264-filter.sh`: lossless x264 pre-filter for LQTC sources.
- Progressive-Scene-Detection: BestSource auto-switch for BT.2020/open-GOP sources, progress %, audio-warning suppression, frame-mismatch retry.

### Fixed
- `av1an-batch-*.sh` scripts referenced a missing `tools/av1an-dispatch.py` since their introduction - now real.
- `--ssimu2 vs-zip` value now a recognized backend everywhere (was falling through by accident).

## [2.1.0-linux] - 2026-03-03

### Fixed
- **Installer: aarch64 Support** (`setup/fssimu2.sh`): Fixed an architecture bug where the Zig download URL incorrectly hardcoded to `x86_64`, causing an "Exec format error" during automated installation on ARM64 devices. Now dynamically checks `uname -m` to download the correct binary.
- **Critical: Fast Pass Performance** (`Auto-Boost-Av1an.py`): Fast pass was hardcoded to `-w 1` regardless of the `--workers` argument passed by the shell script, causing ~1 fps encode on multi-core machines. Now uses the same worker count as the final pass.
- **Fast Pass: Missing `bestsource`**: Added `-m bestsource --cache-mode temp` to the fast pass av1an command, matching Windows behavior for faster/more reliable demuxing.
- **Fast Pass: Missing `--lp 4`**: When running with multiple workers, `--lp 4` is now appended to fast pass encoder params (mirrors Windows `fast_pass_workers > 1` logic).
- **Fast Pass: Unnecessary flags**: Removed `-x 0` and `--split-method none` from fast pass; external scenes are now passed with `-s` when available.

### Added
- **`av1an-batch-anime-crf30.sh`**: New single-pass direct Av1an encode script for Anime (no Auto-Boost). Mirrors `av1an-batch-anime-crf30.bat`.
- **`av1an-batch-liveaction-crf30.sh`**: New single-pass direct Av1an encode script for Live Action with `--autocrop`. Mirrors `av1an-batch-liveaction-crf30.bat`.

### Changed
- **Encoder Params — All Scripts**: Updated all 7 run scripts to v1.66 5fish SVT-AV1-PSY parameter set:
    - Anime scripts: `--lineart-psy-bias 4/5`, `--texture-psy-bias 2/4`, `--hbd-mds 0` (fast) / `1` (final), `--keyint 305`, `--noise-level-thr 16000`, `--filtering-noise-detection 4/1`
    - Live Action scripts: `--tune 3`, `--hbd-mds 0/1`, `--keyint 305`, `--ac-bias 0.8/1.0/1.2`, `--filtering-noise-detection 4/1`
    - Sports script: `--hbd-mds 0/1`, `--keyint 305`, removed `--complex-hvs 1`
    - Removed `--aggressive` flag from CRF 18 scripts (anime and live action)
    - Removed `--lp 3` from shell script params (now handled internally by `Auto-Boost-Av1an.py`)
- **`workercount.py`**: Updated benchmark av1an command to match Windows (`-m bestsource --cache-mode temp`, `--preset 4 --lp 3`). Added high-spec detection: machines with >6 physical cores and >20GB RAM now skip the -1 safety reduction.
- **`cleanup.py`**: Ported 5 missing cleanup steps from Windows: (1) temp folder fastpass exception (preserves folder if non-fastpass MKV files exist inside), (2) `filter/*.ffindex` deletion, (3) `Input/logs/` deletion, (4) `Input/*.bsindex` deletion, (5) `tools/ssimu2_bench_temp/` deletion.
- **`mux.py`**: Now deletes the intermediate `*-av1.mkv` file after successful mux (frees disk space, mirrors Windows). Added `.mp4` and `.m2ts` as source fallbacks in `possible_sources`.
- **`Auto-Boost-Av1an.py`**: Made `wakepy` import optional (try/except) to prevent crash on systems without it installed.
- **`setup/svt_av1.sh`**: Removed pinned commit (`2f788d04`); now clones and builds from the latest `main` branch. Added `-DENABLE_AVX512=ON -DNATIVE=ON` cmake flags for full performance on modern CPUs.
- **`README.md`**: Updated manual SVT-AV1-PSY build command to include `-DENABLE_AVX512=ON -DNATIVE=ON`. Added Direct Encode scripts section to the script selection table.

## [2.0.0-linux] - 2026-02-01


### Added
- **Modular Installer**: Replaced monolithic script with `setup.sh` and individual modules in `setup/`.
    - **Interactive Menu**: Select tools to install/uninstall.
    - **Multi-Select**: Install multiple tools at once (e.g., "1 3 5").
    - **Install All**: One-click full installation.
- **Uninstall Mode**: Dedicated uninstall functions for every component.
- **Granular Control**: Install specific components (e.g., just `svt-av1` or `oxipng`) without running the full suite.

### Changed
- **Optimization**: Updated `tools/comp.py` to limit `oxipng` to 50% of CPU threads (matching Windows behavior) to prevent system overload.
- **Robustness**: Added strict error handling to all build steps; dependencies now stop immediately on failure.
- **Structure**: Split installation logic into `setup/` directory.

### Deprecated
- `install_deps_ubuntu.sh` and `cleanup_install.sh` are now wrappers around `setup.sh`.

## [1.9.0-linux] - 2026-01-27

### Added
- **Progression Boost Scripts** (v1.51/v1.52):
    - `Progression-Boost-SSIMU2-anime.sh`: Automated per-scene quality optimization for Anime.
    - `Progression-Boost-SSIMU2-liveaction.sh`: Automated per-scene quality optimization for Live Action.
    - **Features**:
        - Automatic "sweet spot" quality targeting using SSIMULACRA2 metrics.
        - Splits video into scenes, calculates complex metrics, and encodes each scene with optimal settings.
        - Includes automatic benchmarking (`tools/progression-workercount.py`) to determine optimal worker count based on RAM/CPU.
- **Audio Improvements** (v1.51):
    - **2.1 Channel Support**: Added detection and specific bitrate targets for 2.1 audio in `opus.py` (192k) and `ac3.py`/`eac3.py` (320k).
    - **Settings File Loading**: Audio tools now read bitrates from `audio-encoding/settings-encode-*-audio.txt`.
- **FFVship Support**: Updated `install_deps_ubuntu.sh` to compile `FFVship` (GPU) from source, enabling `vs-hip` mode parity.

### Changed
- **Crop Detection**: Updated `cropdetect.py` to match Windows v1.51 logic (StaxRip-based autocrop with aggressive mode).
- **Dispatch Logic**: Updated `progression-dispatch-Basic-SSIMU2.py` to dynamically switch between `FFVship` (GPU) and `fssimu2` (CPU) binaries based on benchmark results.

### Fixed
- **Audio Parity**: Updated `eac3.py` to support 2.1 channel detection and bitrate assignment, matching `ac3.py` behavior.
- **Tools Parity**: General parity improvements across Linux tools including fixes for `fssimu2` logic in dispatch.
- **Tagging**: Updated `progression-tag.py` regex to correctly detect and tag `ssimu2_quality` settings.

### Notes
- Progression Boost defaults to `ssimu2_quality="82"` for high quality.
- Requires `wakepy` python package (added in previous updates).

## [1.8.0-linux] - 2026-01-21

### Added
- **Extras Scripts** (v1.48):
    - `extras/create-sample.sh`: Creates a 90-second sample from MKV files for testing encode settings.
    - `extras/simple-remux.sh`: Remuxes MKV/MP4/M2TS files into clean MKV containers.
    - `extras/vspreview.sh`: Opens vspreview for viewing MKV files locally; useful for comparing files and finding frame numbers for zones.
    - `extras/add-subtitles.sh`: Muxes subtitles (.ass/.srt) and fonts (.ttf/.otf) into MKV files with language auto-detection.
- **Supporting Python Tools**:
    - `tools/add-subtitles.py`: Subtitle muxer with language detection from filename.
    - `tools/vspreview-dispatch.py`: VSPreview script generator for multi-file comparison.
- **Combined Prefilter Scripts** (v1.49):
    - `prefilter/nvidia-prefilter.sh`: Combined NVIDIA GPU prefilter with denoise/deband/downscale support.
    - `prefilter/x265-prefilter.sh`: Combined CPU prefilter with denoise/deband/downscale support.
    - `tools/nvidia-prefilter.py` and `tools/x265-prefilter.py`: Core Python scripts for combined prefiltering.
- **Automated Downscaling** (v1.48): Root-level `settings.txt` added with downscaling options for encoding workflow.
- **CRF 18 Scripts** (v1.5):
    - `run_linux_anime_crf18.sh`: Anime higher quality script (replaces CRF 15 for "sweet spot" encodes).
    - `run_linux_live_crf18.sh`: Live action higher quality script (replaces CRF 15).
- **--convert-to-YUV420P10** (v1.5): Added support in dispatch.py for non-standard chroma subsampling sources (4:2:2, 4:4:4, etc.).

### Changed
- **Fast-Pass Scene Detection** (v1.49): All run scripts now check for existing Progressive-Scene-Detection JSON files and skip detection if found, significantly speeding up resumed encodes.
- **Prefilter Settings**: Updated `prefilter/settings.txt` to match Windows v1.48/v1.49 format with combined [NVIDIA] and [x265] sections supporting downscale/denoise/deband options.
- **Updated Documentation**: Refreshed `extras/README.md` and `prefilter/README.md` to document all new scripts and usage.

### Fixed
- **mux.py**: Updated to match Windows version with VFR (Variable Frame Rate) support, timestamp extraction, and mkvpropedit metadata fixes.
- **cropdetect.py**: Updated to match Windows version with `load_settings()` for manual crop support from settings.txt and better progress reporting.
- **Auto-Boost-Av1an.py**: Updated VPY template with downscaling support and `--convert-to-YUV420P10` option, added `--zones` argument, and integrated settings.txt parser for downscaling configuration.

### Notes
- This release matches Windows v1.48, v1.49, and v1.5 features.
- CRF 15 scripts are retained for compatibility; new CRF 18 scripts provide the recommended "sweet spot" quality.

## [1.7.0-linux] - 2026-01-17

### Added
- **Sports/High-Motion Script**: Added `run_linux_sports_crf33.sh` for high-motion content (sports, action) with optimized temporal filtering (`--tf-strength 3`).
- **Prefilter Folder**: Added `prefilter/` directory with deband scripts matching Windows v1.46:
    - `nvidia-deband.sh`: NVIDIA GPU deband using NVEncC + libplacebo.
    - `x265-lossless-deband.sh`: CPU deband using VapourSynth + x265 lossless.
    - `tools/deband-nvencc.py` and `tools/deband-x265-lossless.py`: Core Python scripts for deband processing.
    - `settings.txt`: Configurable filter settings.
- **oxipng Integration**: Added lossless PNG compression to `tools/comp.py` before uploading to slow.pics.
    - Parallel execution using all CPU threads.
    - Displays before/after size statistics.

### Changed
- **BT.601 Color Space Support**: Updated `tools/dispatch.py` to detect and transfer BT.601 color spaces for DVD sources (addresses color shift).
- **Wakepy Integration in dispatch.py**: Moved wakepy integration to the dispatch layer for consistent system sleep prevention.
- **Updated comp.py Dependencies**: Added `psutil` to pip requirements for oxipng worker thread detection.

### Notes
- This release matches Windows v1.45 and v1.46 features.
- For NVIDIA deband scripts, NVEncC must be installed and in PATH.

## [1.6.0-linux] - 2026-01-14

### Added
- **Wakepy Integration**: Implemented `wakepy` support in `Auto-Boost-Av1an.py` to prevent system sleep during long encoding sessions (matches v1.44 feature).
- **Light Denoise Tool**: Added `extras/light-denoise.sh` (wraps `tools/light-denoise-x265-lossless.py`).
    - Uses `vsdenoise` (DFTTest) and `x265` lossless encoding.
    - Fully pipeline-based (VapourSynth Pipe -> x265) for Linux compatibility.
- **Improved Audio Workflow**:
    - Updated `tools/opus.py` to support "Lossless Only" (default) vs "All Tracks" encoding modes.
    - Added `LOSSLESS_EXTS` configuration matching upstream v1.43/v1.44.
- **New Tools Ported**:
    - `tools/detect_grainy_flashbacks-beta.py`: Ported with dynamic parallelism and Linux-safe multiprocessing.
    - `tools/ac3.py` & `tools/eac3.py`: Direct audio encoders ported to use system FFmpeg/MKVToolNix.
    - `tools/forced-aspect-remux.py`: Aspect ratio correction utility.
    - `tools/light-denoise-nvencc.py`: NVEncC denoise wrapper (requires `nvencc` in PATH).
    - `tools/compress-folders.py`: Adapted as a disk usage analyzer (compression is NTFS-only).
- **Audio Batch Scripts**:
    - Added default settings files for AC3/EAC3/Opus encoding in `audio-encoding/`.
    - Added `encode-ac3-audio.sh`, `encode-eac3-audio.sh`, `encode-opus-audio.sh` wrappers.
- **Extras Shell Wrappers**:
    - `disk-usage.sh`: Disk usage checker (replaces Windows-only compress-folders).
    - `forced-aspect-remux.sh`: Aspect ratio correction wrapper.
    - `light-denoise-nvidia.sh`: NVEncC GPU denoise wrapper.

### Changed
- **Robust Crop Detection**: Updated `tools/cropdetect.py` to the latest robust version (multi-segment analysis, higher accuracy).
- **Dependencies**: Added `wakepy` and `vsdenoise` to `install_deps_ubuntu.sh`.

### Fixed
- **Line Endings**: Converted all `.sh` scripts (`install_deps_ubuntu.sh`, run scripts, extras) to strict Unix (LF) line endings to prevent `$'\r': command not found` errors.

## [1.5.0-linux] - 2026-01-11

### Added
- **fssimu2 Support**: Updated `install_deps_ubuntu.sh` to compile native `fssimu2` from Rust source for full feature parity with Windows.
- **Input/Output Workflow**: 
    - All shell scripts now auto-create `Input/` and `Output/` folders.
    - Scripts process any `.mkv` file in the `Input/` folder (no longer requires `*-source.mkv` naming).
    - Encoded files are saved to `Output/`.
- **Cleaner Workspace**: Intermediate temp files are now hidden in `.temp` directories inside `Output/`, preventing clutter.
- **Safe Cleanup**: `cleanup.py` updated to support the new folder structure and strictly avoid deleting `.git` or project files.
- **Extras Folder**: Ported `extras/` scripts to Linux:
    - `encode-opus-audio.sh`: Batch audio re-encoding to Opus.
    - `lossless-intermediary.sh`: Lossless x265 helper.
    - `compare.sh`: VapourSynth-based video comparison tool.
        - **Ported `comp.py`**:
            - Switched from `lsmas` to `FFMS2` (native plugin support).
            - Switched from `fpng` to `FFmpeg` for reliable screenshot generation without extra plugins.
            - Added **SubText** support for on-screen frame info.
            - Added **Headless Support**: Handles clipboard/browser errors gracefully on servers without X11.

### Fixed
- **Tagging**: Fixed `tag.py` to correctly parse parameters from multiline shell scripts.
- **Paths**: Updated `mux.py` and `cleanup.py` to be folder-aware.

## [1.4.1-linux] - 2026-01-09

### Added
- **Live Action Scripts**: Dedicated batch scripts for live action content (`run_linux_live_*.sh`) with Auto-Crop enabled by default.
- **Auto-Crop**: Integrated `cropdetect.py` (Linux port) for robust black bar removal.
- **SSIMU2 Support**: Enabled `ssimu2` metrics using `vs-zip` backend.
- **Script Consolidation**: Renamed generic scripts to `run_linux_anime_*.sh` for better organization.

### Changed
- **Parameter Sync**: Updated encoding parameters to match Auto-Boost-Av1an v1.41 (Windows).
    - Anime Standard now uses Tune 3.
    - Live Action High uses Tune 3 + Variance Boost 2.
- **Core Update**: Updated `Auto-Boost-Av1an.py` to v2.9.20 with Linux patches (Path resolution, Shell usage).

## [1.1.0-linux] - 2026-01-08

### Added
- **Experimental SVT-AV1-PSY**: Updated `install_deps_ubuntu.sh` to checkout commit `e87a5ae3` (referencing `ac-bias` and `balancing-r0-based-layer-offset` features).
- **Auto-BT.709 Detection**: Integrated `dispatch.py` (ported to Linux) which uses `mediainfo` to scan source files and automatically inject BT.709 color flags if detected.
- **New Run Scripts**:
    - `run_linux_crf30.sh`: Standard quality (replaces `run_linux.sh`).
    - `run_linux_crf25.sh`: High quality (Tune 0, includes new variance/cdef bias settings).
    - `run_linux_crf15.sh`: Very High quality ("Thicc" mode, CRF 15, Aggressive).
- **Consolidated Dispatch**: All shell scripts now route through `tools/dispatch.py` for consistent handling of parameters and color detection.
- **Tagging Improvements**: Updated `tools/tag.py` to dynamically parse settings and detect `SvtAv1EncApp` version from the system binary.

### Changed
- **Dependencies**: Added `mediainfo` to `install_deps_ubuntu.sh` (required for auto-detection).
- **Removed**: Deleted obsolete `run_linux_hq.sh`, `run_linux_bt709.sh`, etc. in favor of the new CRF-based scripts.

## [1.0.0-linux] - 2026-01-07

### Added
- **Linux Support**: Full port of the Auto-Boost-Av1an suite to Linux (Ubuntu/Debian).
- **Automated Installer**: `install_deps_ubuntu.sh` script to set up the entire environment:
    - Installs system dependencies (FFmpeg, MKVToolNix, etc.).
    - Compiles **VapourSynth** and **FFMS2** plugin from source.
    - Compiles **SVT-AV1-PSY** (5fish fork) with Clang, PGO, and LTO optimizations.
    - Compiles **WWXD** (Scene Detection) with math library linking fix.
    - Compiles **VSZIP** (Metrics) using the official `build.sh` script (auto-fetches Zig).
    - Installs **Av1an** via Cargo.
- **Run Scripts**:
    - `run_linux.sh`: Standard run script (equivalent to `batch.bat`).
    - `run_linux_hq.sh`: High Quality mode (Tune 3, Slower).
    - `run_linux_bt709.sh`: Force BT.709 color signaling.
    - `run_linux_hq_bt709.sh`: Combined HQ + BT.709.
- **Cleanup Script**: `cleanup_install.sh` to remove all installed components.
- **Documentation**: `README_LINUX.md` and `DEPENDENCIES.md` detailing the setup and versions.

### Changed
- **Python Scripts**:
    - Updated `Auto-Boost-Av1an.py` shebang to `#!/usr/bin/env python3`.
    - Replaced Windows-hardcoded paths with `shutil.which` to find `av1an`, `mkvmerge`, etc. in system PATH.
    - Fixed `subprocess.run` calls to avoid `shell=True` on Linux (prevents quoting issues).
    - Modified `tag.py` to detect `sh-used-*.txt` marker files for correct batch name tagging on Linux.
- **VSZIP Integration**:
    - Removed `--ssimu2` flag from Linux shell scripts to force purely VapourSynth-based metric calculation (using `vszip` plugin) instead of relying on a missing `fssimu2` binary.
    - Updated `install_deps_ubuntu.sh` to use the repository's `build.sh` for VSZIP, ensuring the correct Zig compiler version is always used.

### Fixed
- **WWXD Compilation**: Fixed "undefined symbol: pow" verification error by manually linking `-lm` during compilation.
- **Metrics Fallback**: Ensured `Auto-Boost-Av1an.py` correctly falls back to `core.vszip.XPSNR` when `fssimu2` is not provided.
- **Python Conflicts**: Adjusted installer order to install `pip` packages *before* compiling VapourSynth source to avoid overwriting the source-built Python module with a generic pip version.
- **Worker Count**: Removed pre-generated `workercount-config.txt` to allow auto-detection on the user's hardware.

