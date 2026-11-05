#!/bin/bash

# Auto-Boost-Av1an — Fedora and Nobara dependency setup.
# Supports Fedora Workstation, Nobara Linux, RHEL, AlmaLinux, and Rocky Linux.

if [ -z "$COMMON_SOURCED" ]; then
    source "$(dirname "$0")/common.sh"
fi

FEDORA_PACKAGES=(
    # Core CLI tools & encoders
    ffmpeg
    ffmpeg-devel
    x264
    x265
    mkvtoolnix
    mediainfo
    opus-tools
    xclip
    jq
    curl
    wget
    git

    # Build toolchain
    gcc
    gcc-c++
    make
    patch
    cmake
    pkgconf-pkg-config
    autoconf
    automake
    libtool
    yasm
    nasm
    clang
    llvm
    rust
    cargo
    meson
    ninja-build

    # Python & desktop
    python3
    python3-devel
    python3-pip
    python3-numpy
    python3-psutil
    python3-rich
    python3-pyperclip
    python3-requests
    python3-natsort
    python3-colorama
    python3-requests-toolbelt
    python3-tkinter

    # VapourSynth, decoders & media libraries
    vapoursynth
    python3-vapoursynth
    vapoursynth-devel
    vapoursynth-tools
    vapoursynth-libs
    ffms2
    ffms2-devel
    zimg-devel
    libass-devel
    libdav1d-devel
    libplacebo-devel
    vulkan-headers
    vulkan-loader-devel
    glslang-devel
    libwebp-devel
    libavif-devel
    libjpeg-turbo-devel
    xxhash-devel
)

get_dnf_cmd() {
    if command -v dnf5 &> /dev/null; then
        echo "dnf5"
    else
        echo "dnf"
    fi
}

enable_rpm_fusion() {
    local DNF
    DNF="$(get_dnf_cmd)"
    log_info "Checking RPM Fusion repositories..."
    if ! $DNF repolist 2>/dev/null | grep -qi "rpmfusion"; then
        log_info "Enabling RPM Fusion (Free & Non-Free)..."
        local FEDORA_VER="branched"
        if [ -f /etc/os-release ]; then
            . /etc/os-release
            if [ -n "$VERSION_ID" ]; then
                FEDORA_VER="$VERSION_ID"
            fi
        fi

        $DNF install -y \
            "https://mirrors.rpmfusion.org/free/fedora/rpmfusion-free-release-${FEDORA_VER}.noarch.rpm" \
            "https://mirrors.rpmfusion.org/nonfree/fedora/rpmfusion-nonfree-release-${FEDORA_VER}.noarch.rpm" 2>/dev/null || \
        $DNF install -y \
            "https://mirrors.rpmfusion.org/free/fedora/rpmfusion-free-release-branched.noarch.rpm" \
            "https://mirrors.rpmfusion.org/nonfree/fedora/rpmfusion-nonfree-release-branched.noarch.rpm" 2>/dev/null || {
            log_warn "RPM Fusion install reported warnings; continuing with available repositories."
        }
    else
        log_info "RPM Fusion repositories already enabled."
    fi
}

install_fedora_deps() {
    local DNF
    DNF="$(get_dnf_cmd)"
    if ! command -v "$DNF" &> /dev/null; then
        log_error "Neither dnf nor dnf5 was found — this module is for Fedora/Nobara systems only."
        return 1
    fi

    enable_rpm_fusion

    local PACKAGES=("${FEDORA_PACKAGES[@]}")

    # Detect NVIDIA GPU
    if command -v lspci &> /dev/null && lspci -nn 2>/dev/null | grep -iq "\[10de:"; then
        log_info "NVIDIA GPU detected: adding akmod-nvidia..."
        PACKAGES+=(akmod-nvidia)
    fi

    log_info "Installing system packages via $DNF..."
    $DNF install -y --allowerasing --skip-unavailable "${PACKAGES[@]}" || {
        log_error "Failed to install packages via $DNF."
        return 1
    }

    # Ensure VapourSynth headers and TOML configuration
    setup_vs_links

    # Install Python packages via pip
    log_info "Installing required Python libraries via pip..."
    pip3 install --break-system-packages --no-deps "vsjetpack==2.2.2" vstools jetpytools \
        anitopy wakepy Cython matplotlib || {
        log_error "Failed to install Python libraries via pip."
        return 1
    }

    log_success "Fedora/Nobara system dependencies installed."
}

uninstall_fedora_deps() {
    local DNF
    DNF="$(get_dnf_cmd)"
    log_warn "Uninstalling system dependencies can break your system!"
    log_warn "This will remove packages like ffmpeg, python3, git, gcc, etc."
    if ask_yes_no "Are you ABSOLUTELY SURE you want to continue?" "N"; then
        $DNF remove -y "${FEDORA_PACKAGES[@]}" 2>/dev/null || log_warn "Some packages could not be removed."
        log_success "Fedora/Nobara system packages removed."
    else
        log_info "Uninstall aborted."
    fi
}
