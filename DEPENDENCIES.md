# Auto-Boost-Av1an Dependencies (Linux)

This project relies on the following specific versions and forks of software.
The `setup.sh` script installs these automatically (`sudo ./setup.sh`).

## Core Tools

| Software | Version/Tag | Source / Notes |
| :--- | :--- | :--- |
| **Av1an** | Latest Git (Master) | [rust-av/Av1an](https://github.com/rust-av/Av1an), via cargo |
| **SVT-AV1-PSY (5fish)** | **main** | [5fish/svt-av1-psy](https://github.com/5fish/svt-av1-psy) <br> *Default fork (supports `--lineart-psy-bias` & `--texture-psy-bias`). Built with Clang, PGO, LTO, AVX512, NATIVE; also installed system-wide.* |
| **SVT-AV1-PSY (essential)** | master | [psy-ex/svt-av1-psy](https://github.com/psy-ex/svt-av1-psy) <br> *Fidelity presets `--distortion-bias-preset`.* |
| **SVT-AV1-HDR** | main | [emrakyz/svt-av1-hdr](https://github.com/emrakyz/svt-av1-hdr) <br> *HDR fork (`--tune`, `--noise`, `--film-grain`).* |
| **SVT-AV1 (custom)** | user-supplied | Drop `SvtAv1EncApp` into `tools/av1an/svt-av1 forks/custom/x86-64-v3/` |
| **VapourSynth** | **R79** | [vapoursynth/vapoursynth](https://github.com/vapoursynth/vapoursynth) (source build) |
| **FFmpeg** | master (>= 61.19.0) | Compiled to /usr/local for BestSource; apt ffmpeg also installed for CLI |
| **MKVToolNix** | Repository Version | Standard Ubuntu/Debian Repos (`mkvmerge`, `mkvpropedit`, `mkvtoolnix-gui`) |
| **x264 / x265** | Repository Version | `apt install x264 x265` (lossless intermediaries, prefilter) |
| **Opus Tools** | Repository Version | `opusenc` (`apt install opus-tools`) |
| **xclip** | Repository Version | Clipboard support for `comp.py` |
| **vship / FFVship** | **v5.1.0** | [Line-fr/Vship](https://codeberg.org/Line-fr/Vship) <br> *GPU metrics. CUDA/HIP/Vulkan builds deployed to `tools/vs-hip` + `tools/FFVship`.* |
| **oxipng** | Latest Crates.io | `cargo install oxipng` (comparison shot compression) |
| **NVEncC** | Manual Install | [rigaya/NVEnc](https://github.com/rigaya/NVEnc) <br> *Optional: NVIDIA prefilter.* |
| **Condor** | user-supplied | Target-quality av1an fork. Place Linux binary at `tools/av1an/condor`. |
| **LQTC** | user-supplied | `lqtc-<cuda|vulkan>-<essential|hdr>-<arch>` binaries + `display.json` in `tools/LQTC/`. |

> fssimu2 was removed alongside the Windows package: metrics run on the
> vs-hip / FFVship / vs-zip ladder.

## VapourSynth Plugins

| Plugin | Version/Branch | Source | Notes |
| :--- | :--- | :--- | :--- |
| **FFMS2** | **5.0** | [FFMS/ffms2](https://github.com/FFMS/ffms2) | Pinned for FFmpeg 6.x compatibility. |
| **BestSource** | master | [vapoursynth/bestsource](https://github.com/vapoursynth/bestsource) | Auto-switched for BT.2020/open-GOP sources. |
| **WWXD** | 1.0 | dubhater/vapoursynth-wwxd | Scene detection. Linked with `-lm`. |
| **VSZIP** | **R19** | [dnjulek/vapoursynth-zip](https://github.com/dnjulek/vapoursynth-zip) | XPSNR + SSIMULACRA2 CPU + BoxBlur. Zig build. |
| **SubText** | R5 | vapoursynth/subtext | Subtitle overlays for `comp.py`; FFmpeg 6+ patch applied. |
| **akarin** | master | [AkarinVS/vapoursynth-plugin](https://github.com/AkarinVS/vapoursynth-plugin) | `akarin.Expr` (fine_dehalo math). |
| **AWarp** | master | [HolyWu/VapourSynth-AWarp](https://github.com/HolyWu/VapourSynth-AWarp) | Edge warp for `edge_cleaner` dehalo. |
| **DFTTest** | master | HomeOfVapourSynthEvolution/VapourSynth-DFTTest | Denoise. |
| **zsmooth** | master | [adworacz/zsmooth](https://github.com/adworacz/zsmooth) | `Repair` for fine_dehalo. Zig build. |
| **vs-placebo** | master | [Lypheo/vs-placebo](https://github.com/Lypheo/vs-placebo) | Deband / Resample / Tonemap (libplacebo). |
| **vs-mlrt** | v15.16 (releases) | [AmusementClub/vs-mlrt](https://github.com/AmusementClub/vs-mlrt) | TensorRT / ORT AI rescale backends. ArtCNN models cached in `.vsjet/`. |

## Python Libraries (pip)

*   `vsjetpack == 2.2.2` (vstools/vskernels/vsscale/vsdenoise/vsdehalo/vsmasktools/vsrgtools)
*   `numpy`, `rich`, `psutil`, `matplotlib` (AfterZone bitrate charts)
*   `anitopy`, `pyperclip`, `requests`, `requests_toolbelt`, `natsort`, `colorama`
*   `wakepy` (prevent system sleep during encoding)
*   `Cython` (VapourSynth build requirement)

## Build Tools

*   **Rust** (stable via rustup) - Av1an, oxipng
*   **Zig** - VSZIP / zsmooth (auto-fetched by their build scripts)
*   **python3-tk** - script-builder GUI
*   **libplacebo-dev, libvulkan-dev, glslang-tools** - vs-placebo / Vship Vulkan
*   **nvidia-cuda-toolkit** or **hipcc** - GPU metric builds
*   **mediainfo** CLI (color/HDR auto-detection), **jq** (mlrt module)
*   **Git, CMake, Clang, Meson/Ninja, NASM/YASM** (build essentials)
