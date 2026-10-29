# Auto-Boost-Av1an for Linux

Portable AV1 encoding suite for Ubuntu/Debian, kept at feature parity with the
Windows package. Powered by Av1an + SVT-AV1-PSY forks, VapourSynth R79 and
GPU metrics (Vship / FFVship).

---

## Quick Start

```bash
sudo ./setup.sh            # install everything (modular; menu-driven)
./autoboost-anime-crf30.sh # drop files into video-input/ and run a premade script
```

Or build your own script with the guided questionnaire:

```bash
./script-builder.sh
```

---

## Which Script Should I Choose?

### Premade scripts (repo root)

| Script | Mode | Fork | Notes |
|--------|------|------|-------|
| `autoboost-anime-crf30.sh` | Auto-Boost two-pass | 5fish | Denoise on, photon-noise 200 |
| `autoboost-liveaction-crf30.sh` | Auto-Boost two-pass | essential | Auto crop, photon-noise 400 |
| `av1an-anime-crf30.sh` | Single pass | 5fish | Straight-through encode |
| `av1an-liveaction-crf30.sh` | Single pass | essential | Auto crop, photon-noise 400 |
| `av1an-batch-anime-crf30.sh` | Single pass | 5fish | Batch variant |
| `av1an-batch-liveaction-crf30.sh` | Single pass | essential | Batch variant |

**Auto-Boost** measures quality on a fast first pass and boosts weak scenes on
the final pass. **Single pass** just encodes once - preferred for grainy
content (metrics mistake grain for detail).

**Tip:** Start at CRF 30. Want better? CRF 25. Archival? CRF 18. Any CRF is a
`script-builder.sh` question away - it generates a script for any fork/CRF/
preset/autocrop/benchmark combination, exactly like the Windows builder.

### Target-quality encoders (user-supplied binaries)

| Workflow | What it does |
|----------|--------------|
| `condor-builder.sh` | Builds scripts for **Condor**, a target-quality encoder: pick a metric score (SSIMULACRA2 / CVVDP / Butteraugli), it probes each scene until it hits it. Place your Linux `condor` binary in `tools/av1an/`. |
| `lqtc-builder.sh` | Builds scripts for **LQTC** (Large Quality Target Collider). Place `lqtc-<backend>-<fork>-<arch>` binaries and `display.json` in `tools/LQTC/`. |

Activate both from `script-builder.sh` -> `3: Setup advanced tools`.

### AfterZone

`tools/AfterZone.sh` re-encodes only the scenes you choose from an already
finished encode (av1an, LQTC and Condor working folders are all detected).
Zones go in `video-input/<name>.txt` - see `zones-example.txt`. It can
auto-generate a zones file from the finished file's bitrate and add CRF /
tf-strength boosts per zone.

---

## Folder Layout

| Folder | Purpose |
|--------|---------|
| `video-input/` | Drop your sources here |
| `video-output/` | Finished `<name>-output.mkv` files |
| `temp/` | Working folders (kept for AfterZone; cleaned by cleanup.py) |
| `tools/` | All Python tools, benchmarks, fork binaries |
| `tools/av1an/svt-av1 forks/` | `<5fish|essential|hdr|custom>/<arch>/SvtAv1EncApp` |
| `tools/vs-hip`, `tools/FFVship` | Vship metric builds (NVIDIA/AMD/Vulkan) |
| `tools/LQTC` | LQTC binaries + display.json |

---

## Forks (SVT-AV1 encoder presets)

- **5fish** - anime: line/texture psy, best detail retention
- **essential** - anime or live action: scalable fidelity (`--distortion-bias-preset 0-4`)
- **hdr** - HDR or grainy live action (`--tune 0 --noise 4` clean / `--tune 5 --film-grain 10` film)
- **custom** - your own `SvtAv1EncApp` in `tools/av1an/svt-av1 forks/custom/x86-64-v3/`

`ARCH` picks the CPU build: `x86-64-v3` (any modern CPU), `znver2`
(Ryzen 3000+), `avx512` (AVX-512 CPUs only; auto-downgraded if unsupported).

---

## settings.txt

The whole filter chain lives in `settings.txt` (see its `# info` section for
every knob):

- `[crop]` auto (batch `--autocrop`) / manual (all four edges) / off
- `[downscaling]` target resolution + kernel (hermite anime, spline36 live action)
- `[dehalo]` edge_cleaner (AWarp) and `[fine_dehalo]` mask pipeline - pick one
- `[denoise]` raw VapourSynth line (DFTTest example included)
- `[deband]` libplacebo
- `ntfy=` path to phone-notification config (`extras/ntfy-setup.sh` writes it)
- `tonemap_backend=auto|gpu|cpu` for HDR->SDR

Preview any change with `settings-preview.sh` before committing to an encode.

**template.vpy mode:** script-builder's advanced tools can save your filter
chain as an editable `video-input/template.vpy` (with commented DirectML /
TensorRT / AMD ArtCNN rescale sections). While it exists it owns cropping and
the settings.txt filters are bypassed.

---

## Benchmarks

First run of any premade script measures:

- optimal **encode worker count** (real throughput through your filters)
- the fastest **SSIMU2 metric backend** your GPU can run
  (vs-hip CUDA/HIP/Vulkan, FFVship, vs-zip CPU)

Results go to `tools/workercount-config.txt` and `tools/workercount-ssimu2.txt`.
Delete either to re-benchmark, or edit by hand. Scripts built with the
"optimized workers" question re-benchmark with their own preset and write the
tuned values back into themselves.

---

## HDR

- **HDR stays HDR** with the hdr fork: color metadata is signaled
  automatically from MediaInfo.
- **Tonemap to SDR** (`tonemap="True"` or the builder question): libplacebo
  inside the VapourSynth script; Intel GPUs fall back to a CPU BT.2390 path.

---

## Extras (`extras/`)

| Script | Purpose |
|--------|---------|
| `compare.sh` | slow.pics comparison between two files |
| `create-sample.sh` | 90 s sample (custom mm:ss range) |
| `create-discord-10mb-sample.sh` | 9.98 MB uploadable sample |
| `simple-remux.sh` | remux mkv/mp4/m2ts into clean mkv |
| `add-subtitles.sh` | mux subs + fonts, language autodetect |
| `lossless-intermediary.sh` | x265 lossless intermediary for problem sources |
| `photon-film-grain-test.sh` | photon-noise / film-grain strength sweeps |
| `ntfy-setup.sh` | phone notifications when encodes finish |
| `clean-temp-files.sh` | comparison leftovers |
| `mkvpropedit-gui.sh` | mkvtoolnix-gui wrapper (JMkvpropedit stand-in) |
| `disk-usage.sh` | tools folder size report |
| `encode-opus-audio.sh`, `encode-ac3-audio.sh`, `encode-eac3-audio.sh` | audio-encoding/ batch encoders |

---

## Installation

```bash
sudo ./setup.sh                # interactive menu
sudo ./setup.sh --install A    # everything
sudo ./setup.sh --install 3    # just SVT-AV1 forks
```

Modules: system deps, python libs, VapourSynth R79 (+FFMS2/BestSource),
Av1an, SVT-AV1 forks, Vship 5.1.0 + FFVship, oxipng, WWXD, VSZIP 19,
SubText, filter plugins (akarin/AWarp/DFTTest/zsmooth/vs-placebo), vs-mlrt,
Condor/LQTC layout. See [DEPENDENCIES.md](DEPENDENCIES.md).

Manual setup and build flags are documented in DEPENDENCIES.md.

---

## Troubleshooting

- **`av1an: command not found`** - rerun `sudo ./setup.sh --install 1`.
- **A fork reports missing** - rebuild with `--install 3`, or drop the binary
  into `tools/av1an/svt-av1 forks/<name>/x86-64-v3/`.
- **SSIMU2 benchmark finds nothing** - no GPU vship build matched your card;
  vs-zip CPU is the automatic fallback.
- **dehalo/fine_dehalo error about a plugin** - `sudo ./setup.sh --install 9`
  (filter plugins).
- **AVX-512 build crashes at startup** - your CPU lacks AVX-512; keep
  `ARCH=x86-64-v3`.

For anything else, the Windows package's Discord/support channels apply
equally to this port.
