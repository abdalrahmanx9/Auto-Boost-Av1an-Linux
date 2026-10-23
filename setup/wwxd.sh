#!/bin/bash

# Source common functions if not already sourced
if [ -z "$COMMON_SOURCED" ]; then
    source "$(dirname "$0")/common.sh"
fi

install_wwxd() {
    local VS_PLUGIN_PATH=""
    if command -v pkg-config &> /dev/null; then
        VS_PLUGIN_PATH=$(pkg-config --variable=libdir vapoursynth)/vapoursynth
    else
        VS_PLUGIN_PATH="/usr/lib/x86_64-linux-gnu/vapoursynth"
    fi
    mkdir -p "$VS_PLUGIN_PATH"

    mkdir -p build_tmp
    cd build_tmp || exit 1

    log_info "Compiling VapourSynth-WWXD..."
    if [ -d "vapoursynth-wwxd" ]; then rm -rf vapoursynth-wwxd; fi
    git clone --branch v1.0 --depth 1 https://github.com/dubhater/vapoursynth-wwxd.git || { log_error "Failed to clone WWXD"; cd ..; return 1; }
    cd vapoursynth-wwxd || { log_error "Failed to cd into vapoursynth-wwxd"; cd ..; cd ..; return 1; }
    
    # Check for VapourSynth headers (source installs put them in /usr/local,
    # distro packages such as Arch/CachyOS put them in /usr)
    local VS_INCLUDE=""
    if [ -d "/usr/local/include/vapoursynth" ]; then
        VS_INCLUDE="/usr/local/include/vapoursynth"
    elif [ -d "/usr/include/vapoursynth" ]; then
        VS_INCLUDE="/usr/include/vapoursynth"
    else
        log_error "VapourSynth headers not found. Please install VapourSynth first."
        exit 1
    fi
    
    gcc -o libwwxd.so -fPIC -shared -O3 -Wall -Wextra -I. -I"$VS_INCLUDE" src/*.c -lm || \
        { log_error "Compilation failed"; cd ..; cd ..; return 1; }
    
    cp libwwxd.so "$VS_PLUGIN_PATH/" || { log_error "Failed to copy libwwxd.so"; cd ..; cd ..; return 1; }
    
    if [ -d "/usr/lib/x86_64-linux-gnu/vapoursynth" ]; then
        cp libwwxd.so "/usr/lib/x86_64-linux-gnu/vapoursynth/"
    fi
    cd ..
    cd .. # Exit build_tmp
    
    log_success "WWXD installed."
}

uninstall_wwxd() {
    log_info "Uninstalling WWXD..."
    find /usr/local/lib/vapoursynth -name "libwwxd.so" -delete
    find /usr/lib/x86_64-linux-gnu/vapoursynth -name "libwwxd.so" -delete
    log_success "WWXD uninstalled."
}
