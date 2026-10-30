#!/bin/bash

# Source common functions if not already sourced
if [ -z "$COMMON_SOURCED" ]; then
    source "$(dirname "$0")/common.sh"
fi

# Vship 5.1.0 - SSIMULACRA2 GPU metric plugin (vs-hip) + FFVship CLI.
# Builds every backend the local toolchain supports:
#   CUDA  -> libvship_NVIDIA.so + FFVship_nvidia
#   HIP   -> libvship_AMD.so    + FFVship_amd
#   Vulkan-> libvship_VULKAN.so + FFVship_Vulkan   (baseline, always attempted)
# Binaries land in <repo>/tools/vs-hip and <repo>/tools/FFVship/<variant>
# mirroring the Windows portable layout the dispatchers expect.
VSHIP_REPO="https://codeberg.org/Line-fr/Vship.git"
VSHIP_TAG="v5.1.0"

vs_plugin_dirs() {
    pkg-config --variable=libdir vapoursynth 2>/dev/null | sed 's|$|/vapoursynth|'
    echo "/usr/local/lib/vapoursynth"
    echo "/usr/lib/x86_64-linux-gnu/vapoursynth"
}

install_ffvship() {
    local REPO_ROOT
    REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

    mkdir -p build_tmp
    cd build_tmp || exit 1

    if [ -d "Vship" ]; then rm -rf Vship; fi
    git clone --branch "$VSHIP_TAG" --depth 1 "$VSHIP_REPO" \
        || git clone --depth 1 "$VSHIP_REPO" \
        || { log_error "Failed to clone Vship"; cd ..; return 1; }
    cd Vship || { log_error "Failed to cd into Vship"; cd ..; cd ..; return 1; }

    local BUILT_ANY=0

    if command -v nvcc &> /dev/null; then
        log_info "Building CUDA backend (NVIDIA)..."
        if make buildcuda; then
            BUILT_ANY=1
            mkdir -p "$REPO_ROOT/tools/vs-hip" "$REPO_ROOT/tools/FFVship/FFVship_nvidia"
            find zig-out -name "libvship*.so" -exec cp -v {} "$REPO_ROOT/tools/vs-hip/libvship_NVIDIA.so" \; 2>/dev/null
            find . -maxdepth 3 -type f -name "FFVship*" ! -name "*.so" -exec cp -v {} "$REPO_ROOT/tools/FFVship/FFVship_nvidia/" \; 2>/dev/null
        else
            log_warn "CUDA build failed - continuing with other backends."
        fi
        make clean &> /dev/null || true
    fi

    if command -v hipcc &> /dev/null; then
        log_info "Building HIP backend (AMD)..."
        if make build; then
            BUILT_ANY=1
            mkdir -p "$REPO_ROOT/tools/vs-hip" "$REPO_ROOT/tools/FFVship/FFVship_amd"
            [ -f "libvship.so" ] && cp -v "libvship.so" "$REPO_ROOT/tools/vs-hip/libvship_AMD.so"
            find zig-out -name "libvship*.so" -exec cp -v {} "$REPO_ROOT/tools/vs-hip/libvship_AMD.so" \; 2>/dev/null || true
            make buildFFVSHIP 2>/dev/null || true
            [ -f "FFVship" ] && cp -v "FFVship" "$REPO_ROOT/tools/FFVship/FFVship_amd/FFVship" && cp -v "FFVship" /usr/local/bin/FFVship
        else
            log_warn "HIP build failed - continuing with other backends."
        fi
        make clean &> /dev/null || true
    fi

    log_info "Building Vulkan backend (baseline)..."
    if make buildVulkan; then
        BUILT_ANY=1
        mkdir -p "$REPO_ROOT/tools/vs-hip" "$REPO_ROOT/tools/FFVship/FFVship_Vulkan"
        [ -f "libvship.so" ] && cp -v "libvship.so" "$REPO_ROOT/tools/vs-hip/libvship_VULKAN.so"
        find zig-out -name "libvship*.so" -exec cp -v {} "$REPO_ROOT/tools/vs-hip/libvship_VULKAN.so" \; 2>/dev/null || true
        make buildFFVSHIP 2>/dev/null || true
        [ -f "FFVship" ] && cp -v "FFVship" "$REPO_ROOT/tools/FFVship/FFVship_Vulkan/FFVship"
        [ ! -f "/usr/local/bin/FFVship" ] && [ -f "FFVship" ] && cp -v "FFVship" /usr/local/bin/FFVship
    else
        log_warn "Vulkan build failed (libvulkan-dev / glslang-tools installed?)."
    fi

    [ "$BUILT_ANY" -eq 1 ] || { log_error "No Vship backend could be built."; cd ../..; return 1; }

    # FFVship CLI binary (metric streamer used as external ssimu2 backend)
    if [ ! -f "/usr/local/bin/FFVship" ]; then
        make buildFFVSHIP || log_warn "FFVship CLI build failed - vs-hip/vs-zip still available."
        [ -f "FFVship" ] && cp -v "FFVship" /usr/local/bin/FFVship
    fi
    make install PREFIX=/usr/local 2>/dev/null || log_warn "System-wide Vship install skipped."

    # Activate one libvship in the VapourSynth autoload path (prefer NVIDIA > AMD > Vulkan)
    local AUTOLOAD_DIR BEST=""
    for d in $(vs_plugin_dirs); do
        if [ -d "$d" ]; then AUTOLOAD_DIR="$d"; break; fi
    done
    for cand in libvship_NVIDIA.so libvship_AMD.so libvship_VULKAN.so; do
        if [ -f "$REPO_ROOT/tools/vs-hip/$cand" ]; then BEST="$cand"; break; fi
    done
    if [ -n "$AUTOLOAD_DIR" ] && [ -n "$BEST" ]; then
        mkdir -p "$AUTOLOAD_DIR"
        # Only one libvship may live in the autoload dir at a time
        rm -f "$AUTOLOAD_DIR"/libvship_*.so
        cp -v "$REPO_ROOT/tools/vs-hip/$BEST" "$AUTOLOAD_DIR/libvship.so"
        ldconfig
        log_info "Activated $BEST as $AUTOLOAD_DIR/libvship.so"
    fi

    cd ../.. # Exit build_tmp
    log_success "Vship 5.1.0 installed (tools/vs-hip + tools/FFVship)."
}

uninstall_ffvship() {
    log_info "Uninstalling FFVship..."
    rm -vf /usr/local/bin/FFVship
    for d in $(vs_plugin_dirs); do rm -vf "$d/libvship.so" "$d"/libvship_*.so 2>/dev/null; done
    local REPO_ROOT
    REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
    rm -rf "$REPO_ROOT/tools/vs-hip" "$REPO_ROOT/tools/FFVship"
    ldconfig
    log_success "FFVship uninstalled."
}
