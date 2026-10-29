#!/bin/bash

# Source common functions if not already sourced
if [ -z "$COMMON_SOURCED" ]; then
    source "$(dirname "$0")/common.sh"
fi

# vs-mlrt: Machine-Learning Real-Time filters for VapourSynth.
# Used by template.vpy rescale sections (TensorRT / ONNX Runtime / HIP backends)
# running ArtCNN models. Optional: only needed for AI rescaling.
# Downloads the Linux release artifacts from AmusementClub/vs-mlrt.
MLRT_REPO_API="https://api.github.com/repos/AmusementClub/vs-mlrt/releases/latest"

vsmlrt_plugin_dirs() {
    pkg-config --variable=libdir vapoursynth 2>/dev/null | sed 's|$|/vapoursynth|'
    echo "/usr/local/lib/vapoursynth"
    echo "/usr/lib/x86_64-linux-gnu/vapoursynth"
}

install_mlrt() {
    local REPO_ROOT
    REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

    if ! command -v curl &> /dev/null && ! command -v wget &> /dev/null; then
        log_error "Need curl or wget to download vs-mlrt."
        return 1
    fi

    log_info "Fetching latest vs-mlrt release info..."
    local JSON
    JSON="$(curl -sL "$MLRT_REPO_API")" || { log_error "Failed to query GitHub API."; return 1; }
    if [ -z "$JSON" ] || ! command -v jq &> /dev/null; then
        log_error "jq or network unavailable - download vs-mlrt Linux release manually from https://github.com/AmusementClub/vs-mlrt/releases"
        return 1
    fi

    # Pick CUDA (TensorRT/ORT) when nvidia-smi exists, else the CPU(ORT) artifact
    local FILTER=".linux"
    if command -v nvidia-smi &> /dev/null; then FILTER=".cuda"; fi
    local URLS
    URLS="$(echo "$JSON" | jq -r ".assets[].browser_download_url" | grep -i linux | grep -i "$FILTER")"
    if [ -z "$URLS" ]; then
        URLS="$(echo "$JSON" | jq -r ".assets[].browser_download_url" | grep -i linux | head -n 5)"
    fi
    if [ -z "$URLS" ]; then
        log_warn "No Linux assets in the latest vs-mlrt release. Install manually."
        return 1
    fi

    mkdir -p build_tmp/mlrt && cd build_tmp/mlrt || return 1
    local ANY_OK=0
    for url in $URLS; do
        local FNAME="${url##*/}"
        log_info "Downloading $FNAME..."
        curl -sLO "$url" || { log_warn "download failed: $FNAME"; continue; }
        case "$FNAME" in
            *.tar.gz|*.tgz)  tar -xzf "$FNAME" && ANY_OK=1 ;;
            *.tar.xz)        tar -xJf "$FNAME" && ANY_OK=1 ;;
            *.zip)           python3 -c "import zipfile,sys; zipfile.ZipFile(sys.argv[1]).extractall()" "$FNAME" && ANY_OK=1 ;;
            *)               log_warn "unknown archive: $FNAME" ;;
        esac
    done

    # Deploy every .so found into the autoload dirs
    for d in $(vsmlrt_plugin_dirs); do
        [ -d "$d" ] || continue
        find . -name "libvstrt.so" -o -name "libvsort.so" -o -name "libvsmigx.so" -o -name "libvs_onnxruntime.so" | while read -r so; do
            cp -v "$so" "$d/$(basename "$so")"
        done
    done
    ldconfig

    # ArtCNN models go through vsscale's own downloader at first use; keep a cache dir
    mkdir -p "$REPO_ROOT/.vsjet"
    [ "$ANY_OK" -eq 1 ] || { log_warn "No vs-mlrt archive could be extracted."; cd ../..; return 1; }

    cd ../..
    log_success "vs-mlrt installed (AI rescale backends)."
}

uninstall_mlrt() {
    log_info "Uninstalling vs-mlrt..."
    for d in $(vsmlrt_plugin_dirs); do
        [ -d "$d" ] || continue
        rm -vf "$d/libvstrt.so" "$d/libvsort.so" "$d/libvsmigx.so" "$d/libvs_onnxruntime.so" 2>/dev/null
    done
    ldconfig
    log_success "vs-mlrt uninstalled."
}
