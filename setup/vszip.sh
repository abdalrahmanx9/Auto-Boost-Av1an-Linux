#!/bin/bash

# Source common functions if not already sourced
if [ -z "$COMMON_SOURCED" ]; then
    source "$(dirname "$0")/common.sh"
fi

install_vszip() {
    log_info "Installing VSZIP..."
    # 1. Check if vapoursynth-vszip wheel is installed via pip
    if ! pip3 show vapoursynth-vszip &> /dev/null; then
        pip3 install vapoursynth-vszip --no-deps --break-system-packages || true
    fi

    # 2. Find and symlink the best suited libvszip.so
    local VSZIP_LIB
    VSZIP_LIB="$(find /usr/local/lib/python3* -name "libvszip.so" 2>/dev/null | head -n 1)"
    if [ -z "$VSZIP_LIB" ]; then
        VSZIP_LIB="$(find /usr/lib/python3* -name "libvszip.so" 2>/dev/null | head -n 1)"
    fi

    if [ -n "$VSZIP_LIB" ] && [ -f "$VSZIP_LIB" ]; then
        install_vs_plugin "$VSZIP_LIB"
        log_success "VSZIP installed from python package: $VSZIP_LIB"
        return 0
    fi

    mkdir -p build_tmp
    cd build_tmp || exit 1
    
    log_info "Fallback: Compiling VSZIP from source (Pinned: d11057c)..."
    if [ -d "vszip" ]; then rm -rf vszip; fi
    clone_exact_commit "https://github.com/dnjulek/vapoursynth-zip.git" "d11057c95442e6ca76c10128a9ddac4db2973aa0" "vszip" || { log_error "Failed to clone VSZIP"; cd ..; return 1; }
    cd vszip || { log_error "Failed to cd into vszip"; cd ..; cd ..; return 1; }
    
    if command -v zig &> /dev/null; then
        zig build -Doptimize=ReleaseFast
        if [ -f "zig-out/lib/libvszip.so" ]; then
            install_vs_plugin "zig-out/lib/libvszip.so"
        fi
    fi
    cd ..
    cd .. # Exit build_tmp
    
    if find /usr/lib /usr/lib64 /usr/local/lib /usr/local/lib64 -name "libvszip.so" 2>/dev/null | grep -q .; then
        log_success "VSZIP installed."
    else
        log_error "VSZIP installation failed."
        return 1
    fi
}

uninstall_vszip() {
    log_info "Uninstalling VSZIP..."
    uninstall_vs_plugin "libvszip.so"
    pip3 uninstall -y vapoursynth-vszip --break-system-packages 2>/dev/null || true
    log_success "VSZIP uninstalled."
}
