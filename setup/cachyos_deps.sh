#!/bin/bash

# Auto-Boost-Av1an — CachyOS/Arch dependency setup.
# Every package listed here was verified to exist in the official Arch
# repositories (core/extra) at the time of writing. Packages that do NOT
# exist as pacman packages on Arch are installed from their own source
# builders (fssimu2, vszip, subtext, oxipng) or via cargo (av1an).

if [ -z "$COMMON_SOURCED" ]; then
    source "$(dirname "$0")/common.sh"
fi

# Packages verified against https://archlinux.org/packages (core/extra):
#   ffmpeg x264 x265 mkvtoolnix-cli python python-pip python-numpy
#   python-psutil python-rich python-pyperclip python-requests
#   python-natsort python-colorama python-requests-toolbelt
#   git wget cmake pkgconf autoconf automake libtool yasm nasm clang
#   libass mediainfo opus-tools xclip meson ninja jq vapoursynth
#   ffms2 vapoursynth-plugin-bestsource zimg libwebp libavif
#   libjpeg-turbo xxhash rust llvm
# NOTE: "mkvtoolnix" is NOT a package name on Arch — the split packages are
# mkvtoolnix-cli and mkvtoolnix-gui. The scripts only need the CLI tools.

CACHYOS_PACMAN_PACKAGES=(
    ffmpeg
    x264
    x265
    mkvtoolnix-cli
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
    git
    wget
    cmake
    pkgconf
    autoconf
    automake
    libtool
    yasm
    nasm
    clang
    libass
    mediainfo
    opus-tools
    xclip
    meson
    ninja
    jq
    vapoursynth
    ffms2
    vapoursynth-plugin-bestsource
    zimg
    libwebp
    libavif
    libjpeg-turbo
    xxhash
    rust
    llvm
)

# AUR-only packages (installed via an AUR helper if available):
#   vapoursynth-plugin-wwxd  (scene detection)
#   vapoursynth-plugin-vszip  (SSIMULACRA2/XPSNR metrics)
# The source builders in setup/wwxd.sh and setup/vszip.sh cover these if
# no AUR helper is present, so they are not hard requirements here.

install_cachyos_deps() {
    if ! command -v pacman &> /dev/null; then
        log_error "pacman not found — this module is for CachyOS/Arch only."
        return 1
    fi

    log_info "Installing system packages via pacman..."
    # shellcheck disable=SC2046
    pacman -Syu --needed --noconfirm "${CACHYOS_PACMAN_PACKAGES[@]}" || {
        log_error "Failed to install pacman packages."
        return 1
    }

    # Python libs that are not packaged on Arch (vsjetpack, vstools, anitopy,
    # wakepy) still come from pip. Everything else was installed via pacman.
    log_info "Installing remaining Python libs via pip..."
    pip3 install --break-system-packages vsjetpack vstools anitopy wakepy Cython || {
        log_error "Failed to install Python libraries via pip."
        return 1
    }

    # VapourSynth plugins from the AUR (optional — source builders are the
    # fallback). Only attempted when an AUR helper is available and the
    # plugin is not already provided by pacman.
    if command -v yay &> /dev/null || command -v paru &> /dev/null; then
        local aur_helper
        if command -v paru &> /dev/null; then aur_helper="paru"; else aur_helper="yay"; fi
        log_info "Installing VapourSynth plugins from AUR via $aur_helper..."
        $aur_helper -S --needed --noconfirm vapoursynth-plugin-wwxd vapoursynth-plugin-vszip || {
            log_warn "AUR plugin install failed — the source builders (menu options 7 and 8) can build these instead."
        }
    else
        log_warn "No AUR helper (yay/paru) found. WWXD and VSZIP will need to be built from source via menu options 7 and 8."
    fi

    log_success "CachyOS/Arch system dependencies installed."
}

uninstall_cachyos_deps() {
    log_warn "Removing system packages can break your system."
    if ! ask_yes_no "Remove ALL Auto-Boost-Av1an pacman packages?" "N"; then
        log_info "Uninstall aborted."
        return 0
    fi
    pacman -Rns --noconfirm "${CACHYOS_PACMAN_PACKAGES[@]}" || log_warn "Some packages could not be removed."
    log_success "CachyOS/Arch system dependencies removed."
}
