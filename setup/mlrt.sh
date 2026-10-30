#!/bin/bash

# Source common functions if not already sourced
if [ -z "$COMMON_SOURCED" ]; then
    source "$(dirname "$0")/common.sh"
fi

# vs-mlrt: Machine-Learning Real-Time filters for VapourSynth.
# Used by template.vpy rescale sections (NCNN / OpenVINO / TensorRT / ORT backends)
# running ArtCNN models. Optional: only needed for AI rescaling.
MLRT_API4_REPO_API="https://api.github.com/repos/RyougiKukoc/vs-mlrt-api4/releases"

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
    JSON="$(curl -sL "$MLRT_API4_REPO_API")" || { log_error "Failed to query GitHub API."; return 1; }
    if [ -z "$JSON" ] || ! command -v jq &> /dev/null; then
        log_error "jq or network unavailable - download vs-mlrt Linux release manually from https://github.com/RyougiKukoc/vs-mlrt-api4/releases"
        return 1
    fi

    local FILTER="generic"
    if command -v nvidia-smi &> /dev/null; then FILTER="cu129"; fi
    local URL
    URL="$(echo "$JSON" | jq -r '.[].assets[] | select(.name | test("linux.*'"$FILTER"'.*zip$")) | .browser_download_url' | head -n 1)"
    if [ -z "$URL" ]; then
        URL="$(echo "$JSON" | jq -r '.[].assets[] | select(.name | test("linux.*generic.*zip$")) | .browser_download_url' | head -n 1)"
    fi
    if [ -z "$URL" ]; then
        log_warn "No suitable Linux assets in vs-mlrt-api4 release. Install manually."
        return 1
    fi

    mkdir -p build_tmp/mlrt && cd build_tmp/mlrt || return 1
    local FNAME="${URL##*/}"
    log_info "Downloading $FNAME..."
    curl -sL -o "$FNAME" "$URL" || { log_warn "download failed: $FNAME"; cd ../..; return 1; }
    unzip -o "$FNAME" || { log_warn "failed to extract $FNAME"; cd ../..; return 1; }

    # If support libraries exist, move them to /usr/local/lib so vs plugin loader doesn't choke on them
    if [ -d "vsmlrt" ]; then
        find vsmlrt -name "libopenvino*.so*" -o -name "libtbb*.so*" -o -name "libhwloc*.so*" | while read -r lib; do
            cp -v "$lib" "/usr/local/lib/$(basename "$lib")"
        done
        # Deploy plugin shared objects into vapoursynth plugin dirs
        for d in $(vsmlrt_plugin_dirs); do
            [ -d "$d" ] || continue
            find vsmlrt -name "vsncnn.so" -o -name "vsov.so" -o -name "libvsort.so" -o -name "libvstrt.so" -o -name "libvsmigx.so" | while read -r so; do
                cp -v "$so" "$d/$(basename "$so")"
            done
        done
    fi
    ldconfig

    # ArtCNN models go through vsscale's own downloader at first use; keep a cache dir
    mkdir -p "$REPO_ROOT/.vsjet"
    cd ../..
    log_success "vs-mlrt installed (AI rescale backends)."
}

uninstall_mlrt() {
    log_info "Uninstalling vs-mlrt..."
    for d in $(vsmlrt_plugin_dirs); do
        [ -d "$d" ] || continue
        rm -vf "$d/vsncnn.so" "$d/vsov.so" "$d/libvstrt.so" "$d/libvsort.so" "$d/libvsmigx.so" "$d/libvs_onnxruntime.so" 2>/dev/null
    done
    rm -vf /usr/local/lib/libopenvino*.so* /usr/local/lib/libtbb*.so* /usr/local/lib/libhwloc*.so* 2>/dev/null
    ldconfig
    log_success "vs-mlrt uninstalled."
}
