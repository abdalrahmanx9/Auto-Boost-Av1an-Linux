#!/bin/bash

# Auto-Boost-Av1an — CachyOS/Arch dependency setup.
# Every package listed here exists in the official Arch repositories (core/extra).
# Builds on Linux work natively across Arch Linux, EndeavourOS, Manjaro, and CachyOS.

if [ -z "$COMMON_SOURCED" ]; then
    source "$(dirname "$0")/common.sh"
fi

CACHYOS_PACMAN_PACKAGES=(
    # Core CLI tools & encoders
    ffmpeg
    x264
    x265
    mkvtoolnix-cli
    mediainfo
    opus-tools
    xclip
    jq
    curl
    wget
    git

    # Build toolchain
    cmake
    pkgconf
    autoconf
    automake
    libtool
    yasm
    nasm
    clang
    llvm
    rust
    meson
    ninja

    # Python & desktop
    python
    python-pip
    python-numpy
    python-psutil
    python-rich
    python-pyperclip
    python-requests
    python-natsort
    python-colorama
    python-requests-toolbelt
    tk

    # VapourSynth, decoders & media libraries
    vapoursynth
    ffms2
    vapoursynth-plugin-bestsource
    zimg
    libass
    dav1d
    libplacebo
    vulkan-headers
    vulkan-icd-loader
    glslang
    libwebp
    libavif
    libjpeg-turbo
    xxhash
)

install_cachyos_deps() {
    if ! command -v pacman &> /dev/null; then
        log_error "pacman not found — this module is for Arch/CachyOS systems only."
        return 1
    fi

    # Detect NVIDIA GPU to add CUDA package if present
    local PACKAGES=("${CACHYOS_PACMAN_PACKAGES[@]}")
    if command -v lspci &> /dev/null && lspci -nn 2>/dev/null | grep -iq "\[10de:"; then
        log_info "NVIDIA GPU detected: adding cuda package..."
        PACKAGES+=(cuda)
    fi

    log_info "Installing system packages via pacman..."
    pacman -Syu --needed --noconfirm "${PACKAGES[@]}" || {
        log_error "Failed to install pacman packages."
        return 1
    }

    # Python packages that may not be in official Arch repos
    log_info "Installing required Python libraries via pip..."
    pip3 install --break-system-packages "vsjetpack==2.2.2" vstools anitopy wakepy Cython matplotlib || {
        log_error "Failed to install Python libraries via pip."
        return 1
    }

    # Optional VapourSynth plugins from AUR (if yay or paru is available)
    if command -v yay &> /dev/null || command -v paru &> /dev/null; then
        local aur_helper
        if command -v paru &> /dev/null; then aur_helper="paru"; else aur_helper="yay"; fi
        log_info "Installing VapourSynth plugins from AUR via $aur_helper..."
        $aur_helper -S --needed --noconfirm vapoursynth-plugin-wwxd vapoursynth-plugin-vszip 2>/dev/null || {
            log_warn "AUR helper install had warnings — built-in source builders remain available."
        }
    fi

    log_success "Arch/CachyOS system dependencies installed."
}

uninstall_cachyos_deps() {
    log_warn "Uninstalling system dependencies can break your system!"
    log_warn "This will remove packages like ffmpeg, python, git, clang, etc."
    if ask_yes_no "Are you ABSOLUTELY SURE you want to continue?" "N"; then
        pacman -Rns --noconfirm "${CACHYOS_PACMAN_PACKAGES[@]}" 2>/dev/null || log_warn "Some packages could not be removed."
        log_success "Arch/CachyOS system packages removed."
    else
        log_info "Uninstall aborted."
    fi
}
