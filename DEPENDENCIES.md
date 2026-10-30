# Auto-Boost-Av1an Dependencies (Linux)

This project relies on the following specific versions and forks of software.
The `setup.sh` script installs these automatically (`sudo ./setup.sh`).

## Core Tools

| Software | Version/Commit | Source / Notes |
| :--- | :--- | :--- |
| **Av1an** | `641ce62` (v0.6.0) | [rust-av/Av1an](https://github.com/rust-av/Av1an), via cargo pinned commit |
| **SVT-AV1-PSY (5fish)** | `9252c01` | [5fish/svt-av1-psy](https://github.com/5fish/svt-av1-psy) <br> *Default fork (supports `--lineart-psy-bias` & `--texture-psy-bias`). Built with Clang, PGO, LTO, AVX512, NATIVE; also installed system-wide.* |
| **SVT-AV1-PSY (essential)** | `8dcb7f8` | [psy-ex/svt-av1-psy](https://github.com/psy-ex/svt-av1-psy) <br> *Fidelity presets `--distortion-bias-preset`.* |
| **SVT-AV1-HDR** | `df788d6` | [emrakyz/svt-av1-hdr](https://github.com/emrakyz/svt-av1-hdr) <br> *HDR fork (`--tune`, `--noise`, `--film-grain`).* |
| **SVT-AV1 (custom)** | user-supplied | Drop `SvtAv1EncApp` into `tools/av1an/svt-av1 forks/custom/x86-64-v3/` |
| **VapourSynth** | `acabf60` (**R79**) | [vapoursynth/vapoursynth](https://github.com/vapoursynth/vapoursynth) (source build) |
| **FFMS2** | `7ed5e4d` (**5.0**) | [FFMS/ffms2](https://github.com/FFMS/ffms2) (pinned for FFmpeg compatibility) |
| **FFmpeg** | `b6d737a` (master) | Compiled to /usr/local for BestSource (libavcodec >= 61.19.0) |
| **BestSource** | `9faf675` | [vapoursynth/bestsource](https://github.com/vapoursynth/bestsource) |
| **MKVToolNix** | Repository Version | Standard Ubuntu/Debian Repos (`mkvmerge`, `mkvpropedit`, `mkvtoolnix-gui`) |
| **x264 / x265** | Repository Version | `apt install x264 x265` (lossless intermediaries, prefilter) |
| **Opus Tools** | Repository Version | `opusenc` (`apt install opus-tools`) |
| **xclip** | Repository Version | Clipboard support for `comp.py` |
| **vship / FFVship** | `d530205` (**v5.1.0**) | [Line-fr/Vship](https://codeberg.org/Line-fr/Vship) <br> *GPU metrics. CUDA/HIP/Vulkan builds deployed to `tools/vs-hip` + `tools/FFVship`.* |
| **oxipng** | `10.2.1` | Cargo install with locked version |
| **NVEncC** | Manual Install | [rigaya/NVEnc](https://github.com/rigaya/NVEnc) <br> *Optional: NVIDIA prefilter.* |
| **Condor** | user-supplied | Target-quality av1an fork. Place Linux binary at `tools/av1an/condor`. |
| **LQTC** | user-supplied | `lqtc-<cuda|vulkan>-<essential|hdr>-<arch>` binaries + `display.json` in `tools/LQTC/`. |

> fssimu2 was removed alongside the Windows package: metrics run on the
> vs-hip / FFVship / vs-zip ladder.

## VapourSynth Plugins

| Plugin | Version/Commit | Source | Notes |
| :--- | :--- | :--- | :--- |
| **FFMS2** | `7ed5e4d` (**5.0**) | [FFMS/ffms2](https://github.com/FFMS/ffms2) | Pinned for FFmpeg 6.x compatibility. |
| **BestSource** | `9faf675` | [vapoursynth/bestsource](https://github.com/vapoursynth/bestsource) | Auto-switched for BT.2020/open-GOP sources. |
| **WWXD** | `73c05a4` (**1.0**) | dubhater/vapoursynth-wwxd | Scene detection. Modernized API 4. |
| **VSZIP** | `d11057c` (**23.1.0**) | [dnjulek/vapoursynth-zip](https://github.com/dnjulek/vapoursynth-zip) | XPSNR + SSIMULACRA2 CPU + BoxBlur. |
| **SubText** | `ee915bc` (**R5**) | vapoursynth/subtext | Subtitle overlays for `comp.py`; FFmpeg 6+ patch applied. |
| **akarin** | `8b7ff6d` | [AkarinVS/vapoursynth-plugin](https://github.com/AkarinVS/vapoursynth-plugin) | `akarin.Expr` (fine_dehalo math). |
| **AWarp** | `86d1d40` (**v3.1**) | [HolyWu/VapourSynth-AWarp](https://github.com/HolyWu/VapourSynth-AWarp) | Edge warp for `edge_cleaner` dehalo. |
| **DFTTest** | **v1.1 API4** | HomeOfVapourSynthEvolution/VapourSynth-DFTTest | Denoise. |
| **zsmooth** | `187ae30` | [adworacz/zsmooth](https://github.com/adworacz/zsmooth) | `Repair` for fine_dehalo. |
| **vs-placebo** | `3cfd23f` (**2.0.4**) | [Lypheo/vs-placebo](https://github.com/Lypheo/vs-placebo) | Deband / Resample / Tonemap (libplacebo). |
| **vs-mlrt** | **v15.16** | [AmusementClub/vs-mlrt](https://github.com/AmusementClub/vs-mlrt) | TensorRT / ORT AI rescale backends. ArtCNN models cached in `.vsjet/`. |

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
