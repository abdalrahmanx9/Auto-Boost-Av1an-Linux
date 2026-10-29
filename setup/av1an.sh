#!/bin/bash

# Source common functions if not already sourced
if [ -z "$COMMON_SOURCED" ]; then
    source "$(dirname "$0")/common.sh"
fi

install_av1an() {
    # 1. Rust (Needed for Av1an)
    if ! command -v rustc &> /dev/null; then
        log_info "Installing Rust..."
        if command -v pacman &> /dev/null; then
            pacman -S --noconfirm rust || { log_error "Failed to install Rust with pacman"; return 1; }
        else
            curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y || { log_error "Failed to install Rust via rustup"; return 1; }
            source "$HOME/.cargo/env"
            export PATH="$HOME/.cargo/bin:$PATH"
        fi
    else
        log_info "Rust is already installed."
        source "$HOME/.cargo/env"
        export PATH="$HOME/.cargo/bin:$PATH"
    fi

    # 2. Av1an
    if ! command -v av1an &> /dev/null; then
        log_info "Installing Av1an via Cargo (Git Source)..."
        source "$HOME/.cargo/env"
        
        export PKG_CONFIG_PATH="/usr/local/lib/pkgconfig:$PKG_CONFIG_PATH"
        export LD_LIBRARY_PATH="/usr/local/lib:$LD_LIBRARY_PATH"
        export LIBRARY_PATH="/usr/local/lib:$LIBRARY_PATH"

        cargo install --git https://github.com/rust-av/Av1an.git --bin av1an || { log_error "Failed to install Av1an via cargo"; return 1; }
        
        if [ -f "$HOME/.cargo/bin/av1an" ]; then
            cp "$HOME/.cargo/bin/av1an" /usr/local/bin/av1an
            chmod +x /usr/local/bin/av1an
            log_success "Av1an installed to /usr/local/bin/av1an"
        else
             log_warn "Av1an binary not found in cargo bin after install?"
             return 1
        fi
    else
        log_info "Av1an is already installed."
        # Verify /usr/local/bin copy
        if [ -f "$HOME/.cargo/bin/av1an" ] && [ ! -f "/usr/local/bin/av1an" ]; then
             cp "$HOME/.cargo/bin/av1an" /usr/local/bin/av1an
             chmod +x /usr/local/bin/av1an
        fi
    fi
}

uninstall_av1an() {
    log_info "Uninstalling Av1an..."
    
    # Try cargo uninstall
    source "$HOME/.cargo/env"
    cargo uninstall av1an 2>/dev/null || true
    
    # Remove binary
    rm -vf /usr/local/bin/av1an
    rm -vf "$HOME/.cargo/bin/av1an"
    
    log_success "Av1an uninstalled."
}
