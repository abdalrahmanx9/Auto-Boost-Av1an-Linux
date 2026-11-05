#!/bin/bash

# Source common functions if not already sourced
if [ -z "$COMMON_SOURCED" ]; then
    source "$(dirname "$0")/common.sh"
fi

install_oxipng() {
    if ! command -v oxipng &> /dev/null; then
        if command -v pacman &> /dev/null; then
            log_info "Installing oxipng via pacman..."
            if pacman -S --needed --noconfirm oxipng; then
                log_success "oxipng installed."
                return 0
            fi
        fi
        log_info "Installing oxipng..."
        load_cargo_env
        if ! command -v cargo &> /dev/null; then
            log_info "Installing Rust for oxipng..."
            curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y || { log_error "Failed to install Rust via rustup"; return 1; }
            load_cargo_env
        fi
        cargo install --version 10.2.1 --locked oxipng || { log_error "Failed to install oxipng via cargo"; return 1; }
        
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
    load_cargo_env
    cargo uninstall oxipng 2>/dev/null || true
    log_success "oxipng uninstalled."
}
