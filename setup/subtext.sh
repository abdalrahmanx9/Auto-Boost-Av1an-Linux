#!/bin/bash

# Source common functions if not already sourced
if [ -z "$COMMON_SOURCED" ]; then
    source "$(dirname "$0")/common.sh"
fi

install_subtext() {
    local VS_PLUGIN_PATH=""
    if command -v pkg-config &> /dev/null; then
        VS_PLUGIN_PATH=$(pkg-config --variable=libdir vapoursynth)/vapoursynth
    else
        VS_PLUGIN_PATH="/usr/local/lib/vapoursynth"
    fi
    mkdir -p "$VS_PLUGIN_PATH"

    # Ensure headers are in /usr/local/include
    if [ -d "/usr/local/include/vapoursynth" ]; then
        cp -sf /usr/local/include/vapoursynth/* /usr/local/include/ 2>/dev/null || true
    fi

    mkdir -p build_tmp
    cd build_tmp || exit 1

    log_info "Compiling SubText..."
    if [ -d "subtext" ]; then rm -rf subtext; fi
    git clone --branch R5 --depth 1 https://github.com/vapoursynth/subtext.git || { log_error "Failed to clone SubText"; cd ..; return 1; }
    cd subtext || { log_error "Failed to cd into subtext"; cd ..; cd ..; return 1; }
    
    # avcodec_close() was removed in FFmpeg 6.0; replace with avcodec_free_context()
    sed -i 's/avcodec_close(d->avctx)/avcodec_free_context(\&d->avctx)/g' src/image.cpp
    
    meson setup build --buildtype=release || { log_error "SubText meson setup failed"; cd ..; cd ..; return 1; }
    ninja -C build || { log_error "SubText ninja build failed"; cd ..; cd ..; return 1; }
    
    if [ -f "build/libsubtext.so" ]; then
        cp -v "build/libsubtext.so" "$VS_PLUGIN_PATH/" || { log_error "Failed to copy libsubtext.so"; cd ..; cd ..; return 1; }
        if [ -d "/usr/lib/x86_64-linux-gnu/vapoursynth" ]; then
            cp -v "build/libsubtext.so" "/usr/lib/x86_64-linux-gnu/vapoursynth/"
        fi
    else
        log_error "SubText compilation failed!"
        cd ..; cd ..; return 1
    fi
    
    cd ..
    cd .. # Exit build_tmp
    
    log_success "SubText installed."
}

uninstall_subtext() {
    log_info "Uninstalling SubText..."
    find /usr/local/lib/vapoursynth -name "libsubtext.so" -delete
    find /usr/lib/x86_64-linux-gnu/vapoursynth -name "libsubtext.so" -delete
    log_success "SubText uninstalled."
}
