#!/bin/bash

# Source common functions if not already sourced
if [ -z "$COMMON_SOURCED" ]; then
    source "$(dirname "$0")/common.sh"
fi

install_oxipng() {
    if ! command -v oxipng &> /dev/null; then
        log_info "Installing oxipng..."
        source "$HOME/.cargo/env"
        cargo install oxipng || { log_error "Failed to install oxipng via cargo"; return 1; }
        
        if [ -f "$HOME/.cargo/bin/oxipng" ]; then
            cp "$HOME/.cargo/bin/oxipng" /usr/local/bin/oxipng
            chmod +x /usr/local/bin/oxipng
            log_success "oxipng installed."
        fi
    else
        log_info "oxipng is already installed."
    fi
}

uninstall_oxipng() {
    log_info "Uninstalling oxipng..."
    rm -vf /usr/local/bin/oxipng
    source "$HOME/.cargo/env"
    cargo uninstall oxipng 2>/dev/null || true
    log_success "oxipng uninstalled."
}
