#!/bin/bash

# Source common functions if not already sourced
if [ -z "$COMMON_SOURCED" ]; then
    source "$(dirname "$0")/common.sh"
fi

install_system_deps() {
    # Non-interactive apt: containers and CI have no debconf console, and
    # tzdata's timezone prompt hangs the install otherwise. Desktop installs
    # keep their already-configured defaults either way.
    export DEBIAN_FRONTEND=noninteractive

    log_info "Updating apt..."
    apt update

    log_info "Installing System Packages..."
    # Core build tools and libraries
    local DEPS=(
        software-properties-common ffmpeg x264 mkvtoolnix mkvtoolnix-gui 
        python3 python3-pip git curl wget build-essential cmake pkg-config 
        autoconf automake libtool yasm nasm clang libavcodec-dev libavformat-dev 
        libavutil-dev libswscale-dev libavdevice-dev libavfilter-dev  
        libzimg-dev python3-numpy python3-psutil python3-rich jq mediainfo 
        opus-tools x265 xclip meson ninja-build libass-dev nvidia-cuda-toolkit
        python3-dev
        # GUI builder (Tk), mkvpropedit GUI, vs-placebo/vship GPU build deps
        python3-tk libplacebo-dev libvulkan-dev glslang-tools
        # Dependencies for BestSource
        libjpeg-turbo8-dev libwebp-dev libavif-dev libxxhash-dev
    )

    apt install -y "${DEPS[@]}" || { log_error "Failed to install system dependencies via apt"; return 1; }

    log_success "System packages installed."
}

uninstall_system_deps() {
    log_warn "Uninstalling system dependencies can break your system!"
    log_warn "This will remove packages like ffmpeg, python3, git, gcc, etc."
    if ask_yes_no "Are you ABSOLUTELY SURE you want to continue?" "N"; then
        local DEPS=(
            software-properties-common ffmpeg x264 mkvtoolnix mkvtoolnix-gui 
            python3 python3-pip git curl wget build-essential cmake pkg-config 
            autoconf automake libtool yasm nasm clang libavcodec-dev libavformat-dev 
            libavutil-dev libswscale-dev libavdevice-dev libavfilter-dev  
            libzimg-dev python3-numpy python3-psutil python3-rich jq mediainfo 
            opus-tools x265 xclip meson ninja-build libass-dev nvidia-cuda-toolkit
            libjpeg-turbo8-dev libwebp-dev libavif-dev
        )
        apt remove -y "${DEPS[@]}"
        log_success "System packages removed (hopefully you knew what you were doing)."
    else
        log_info "Uninstall aborted."
    fi
}
