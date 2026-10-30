#!/bin/bash

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

export PKG_CONFIG_PATH="/usr/local/lib/pkgconfig:/usr/local/share/pkgconfig:${PKG_CONFIG_PATH}"
export LD_LIBRARY_PATH="/usr/local/lib:${LD_LIBRARY_PATH}"
export VAPOURSYNTH_EXTRA_PLUGIN_PATH="/usr/local/lib/vapoursynth:${VAPOURSYNTH_EXTRA_PLUGIN_PATH}"

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

check_root() {
    if [ "$EUID" -ne 0 ]; then 
        log_error "Please run as root (sudo)."
        exit 1
    fi
}

check_distro() {
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        DISTRO=$ID
        DISTRO_LIKE=$ID_LIKE
    else
        log_error "Cannot detect Linux distribution."
        exit 1
    fi

    log_info "Detected Distribution: $DISTRO"

    if [ -f /etc/debian_version ] || [[ "$DISTRO_LIKE" =~ "debian" ]] || [[ "$DISTRO_LIKE" =~ "ubuntu" ]]; then
        log_success "Detected Debian/Ubuntu-based system."
    elif command -v pacman &> /dev/null || [ "$DISTRO" = "arch" ] || [ "$DISTRO" = "cachyos" ] || [[ "$DISTRO_LIKE" =~ "arch" ]]; then
        log_success "Detected Arch/CachyOS-based system."
    else
        log_warn "Distribution '$DISTRO' is not officially verified. Proceeding with caution."
    fi
}

clone_exact_commit() {
    local url="$1" commit="$2" dir="$3"
    mkdir -p "$dir" && cd "$dir" || return 1
    git init -q
    git remote remove origin 2>/dev/null || true
    git remote add origin "$url"
    git fetch --depth 1 origin "$commit" || { git fetch origin "$commit"; } || return 1
    git checkout -q FETCH_HEAD || return 1
    cd - >/dev/null
}

ask_yes_no() {
    local prompt="$1"
    local default="$2" # Y or N
    
    # If non-interactive (e.g. piped or automated script), return default immediately
    if [ ! -t 0 ]; then
        if [ "$default" == "Y" ]; then return 0; else return 1; fi
    fi
    
    local yn_prompt="[y/n]"
    if [ "$default" == "Y" ]; then yn_prompt="[Y/n]"; fi
    if [ "$default" == "N" ]; then yn_prompt="[y/N]"; fi
    
    read -p "$prompt $yn_prompt " -n 1 -r
    echo ""
    
    if [ -z "$REPLY" ]; then
        if [ "$default" == "Y" ]; then return 0; fi
        if [ "$default" == "N" ]; then return 1; fi
    fi
    
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        return 0
    else
        return 1
    fi
}
