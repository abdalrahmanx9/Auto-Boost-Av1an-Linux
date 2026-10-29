#!/bin/bash

# Source common functions if not already sourced
if [ -z "$COMMON_SOURCED" ]; then
    source "$(dirname "$0")/common.sh"
fi

install_python_libs() {
    log_info "Installing Python Libraries..."
    
    # Use --ignore-installed to avoid conflicts with apt-installed packages
    # Added dependencies for tools/comp.py (anitopy, pyperclip, requests, natsort, colorama)
    # vsjetpack is pinned to 2.2.2 (the version bundled with VapourSynth R79 on Windows)
    # matplotlib is used by AfterZone for bitrate-zone charts
    pip3 install "vsjetpack==2.2.2" numpy rich vstools psutil anitopy pyperclip requests \
        requests_toolbelt natsort colorama wakepy Cython matplotlib \
        --break-system-packages --ignore-installed || { log_error "Failed to install Python libraries"; return 1; }

    # Remove the pip-installed vapoursynth which conflicts with the source build we are about to do
    log_info "Removing pip-installed VapourSynth to avoid version mismatch..."
    pip3 uninstall -y vapoursynth --break-system-packages || true
    
    log_success "Python libraries installed."
}

uninstall_python_libs() {
    log_info "Uninstalling Python Libraries..."
    pip3 uninstall -y vsjetpack numpy rich vstools psutil anitopy pyperclip \
        requests requests_toolbelt natsort colorama wakepy Cython \
        --break-system-packages
    log_success "Python libraries uninstalled."
}
