#!/bin/bash

# Source common functions if not already sourced
if [ -z "$COMMON_SOURCED" ]; then
    source "$(dirname "$0")/common.sh"
fi

install_subtext() {
    mkdir -p build_tmp
    cd build_tmp || exit 1

    log_info "Compiling SubText (Pinned: ee915bc)..."
    if [ -d "subtext" ]; then rm -rf subtext; fi
    clone_exact_commit "https://github.com/vapoursynth/subtext.git" "ee915bcaa31d2106d5156e602a470d0b4964321f" "subtext" || { log_error "Failed to clone SubText"; cd ..; return 1; }
    cd subtext || { log_error "Failed to cd into subtext"; cd ..; cd ..; return 1; }
    
    # avcodec_close() was removed in FFmpeg 6.0; replace with avcodec_free_context()
    sed -i 's/avcodec_close(d->avctx)/avcodec_free_context(\&d->avctx)/g' src/image.cpp
    
    meson setup build --buildtype=release || { log_error "SubText meson setup failed"; cd ..; cd ..; return 1; }
    ninja -C build || { log_error "SubText ninja build failed"; cd ..; cd ..; return 1; }
    
    if [ -f "build/libsubtext.so" ]; then
        install_vs_plugin "build/libsubtext.so"
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
    uninstall_vs_plugin "libsubtext.so"
    log_success "SubText uninstalled."
}
