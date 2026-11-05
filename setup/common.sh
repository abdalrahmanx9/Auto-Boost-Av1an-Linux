#!/bin/bash

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BASE_DIR="$REPO_ROOT"
BUILD_TMP="$REPO_ROOT/build_tmp"

export PKG_CONFIG_PATH="/usr/local/lib/pkgconfig:/usr/local/share/pkgconfig:${PKG_CONFIG_PATH}"
export LD_LIBRARY_PATH="/usr/local/lib:${LD_LIBRARY_PATH}"
export VAPOURSYNTH_EXTRA_PLUGIN_PATH="/usr/local/lib/vapoursynth:${VAPOURSYNTH_EXTRA_PLUGIN_PATH}"

load_cargo_env() {
    [ -f "$HOME/.cargo/env" ] && source "$HOME/.cargo/env" 2>/dev/null || true
    export PATH="$HOME/.cargo/bin:$PATH"
}

find_in_python_libs() {
    local pattern="$1"
    find /usr/local/lib*/python* /usr/lib*/python* -name "$pattern" 2>/dev/null | head -n 1
}

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
    elif command -v dnf &> /dev/null || command -v dnf5 &> /dev/null || [ "$DISTRO" = "fedora" ] || [ "$DISTRO" = "nobara" ] || [[ "$DISTRO_LIKE" =~ "fedora" ]]; then
        log_success "Detected Fedora/Nobara-based system."
    else
        log_warn "Distribution '$DISTRO' is not officially verified. Proceeding with caution."
    fi
}

clone_exact_commit() {
    local url="$1" commit="$2" dir="$3"
    mkdir -p "$dir" && cd "$dir" || return 1
    git config --global --add safe.directory "$PWD" 2>/dev/null || true
    git -c safe.directory=* init -q
    git -c safe.directory=* remote remove origin 2>/dev/null || true
    git -c safe.directory=* remote add origin "$url"
    git -c safe.directory=* fetch --depth 1 origin "$commit" || { git -c safe.directory=* fetch origin "$commit"; } || return 1
    git -c safe.directory=* checkout -q FETCH_HEAD || return 1
    cd - >/dev/null
}

ask_yes_no() {
    local prompt="$1"
    local default="$2" # Y or N
    
    if [ "$FORCE_YES" == "1" ]; then
        return 0
    fi
    
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

get_vs_py_dir() {
    local py_dir
    py_dir="$(python3 -c 'import vapoursynth, os; print(os.path.dirname(vapoursynth.__file__))' 2>/dev/null)"
    if [ -z "$py_dir" ]; then
        py_dir="$(find /usr/lib*/python3* /usr/local/lib*/python3* -type d -name vapoursynth 2>/dev/null | head -n 1)"
    fi
    echo "$py_dir"
}

get_vs_include_dir() {
    local vs_py
    vs_py="$(get_vs_py_dir)"
    if [ -d "/usr/include/vapoursynth" ] && ([ -f "/usr/include/vapoursynth/VapourSynth4.h" ] || [ -f "/usr/include/vapoursynth/VapourSynth.h" ]); then
        echo "/usr/include/vapoursynth"
        return 0
    fi
    if [ -n "$vs_py" ] && [ -d "$vs_py/include" ] && ([ -f "$vs_py/include/VapourSynth4.h" ] || [ -f "$vs_py/include/VapourSynth.h" ]); then
        echo "$vs_py/include"
        return 0
    fi
    if [ -d "/usr/local/include/vapoursynth" ] && ([ -f "/usr/local/include/vapoursynth/VapourSynth4.h" ] || [ -f "/usr/local/include/vapoursynth/VapourSynth.h" ]); then
        echo "/usr/local/include/vapoursynth"
        return 0
    fi
    if [ -n "$vs_py" ] && [ -d "$vs_py/include" ]; then
        echo "$vs_py/include"
        return 0
    fi
    echo "/usr/local/include/vapoursynth"
}

setup_vs_links() {
    local VS_PY_DIR
    VS_PY_DIR="$(get_vs_py_dir)"

    # 1. Provide API 4 compatibility symlinks (VSHelper4.h -> VSHelper.h, etc.)
    local inc_dirs=("/usr/include/vapoursynth" "/usr/local/include/vapoursynth")
    if [ -n "$VS_PY_DIR" ] && [ -d "$VS_PY_DIR/include" ]; then
        inc_dirs+=("$VS_PY_DIR/include")
        mkdir -p /usr/include /usr/local/include
        [ ! -e /usr/include/vapoursynth ] && ln -sfn "$VS_PY_DIR/include" /usr/include/vapoursynth 2>/dev/null || true
        [ ! -e /usr/local/include/vapoursynth ] && ln -sfn "$VS_PY_DIR/include" /usr/local/include/vapoursynth 2>/dev/null || true
    fi

    # Cross-link system include directories if one exists and the other doesn't
    if [ -d "/usr/include/vapoursynth" ] && [ ! -e "/usr/local/include/vapoursynth" ]; then
        mkdir -p /usr/local/include
        ln -sfn /usr/include/vapoursynth /usr/local/include/vapoursynth 2>/dev/null || true
    elif [ -d "/usr/local/include/vapoursynth" ] && [ ! -e "/usr/include/vapoursynth" ]; then
        mkdir -p /usr/include
        ln -sfn /usr/local/include/vapoursynth /usr/include/vapoursynth 2>/dev/null || true
    fi

    # Ensure VS_PY_DIR has an include dir linked for build systems (like BestSource) that check python's get_include()
    if [ -n "$VS_PY_DIR" ] && [ ! -e "$VS_PY_DIR/include" ]; then
        if [ -d "/usr/include/vapoursynth" ]; then
            ln -sfn /usr/include/vapoursynth "$VS_PY_DIR/include" 2>/dev/null || true
            inc_dirs+=("$VS_PY_DIR/include")
        elif [ -d "/usr/local/include/vapoursynth" ]; then
            ln -sfn /usr/local/include/vapoursynth "$VS_PY_DIR/include" 2>/dev/null || true
            inc_dirs+=("$VS_PY_DIR/include")
        fi
    fi

    for inc in "${inc_dirs[@]}"; do
        if [ -d "$inc" ]; then
            [ -f "$inc/VSHelper4.h" ] && [ ! -f "$inc/VSHelper.h" ] && ln -sf VSHelper4.h "$inc/VSHelper.h" 2>/dev/null || true
            [ -f "$inc/VapourSynth4.h" ] && [ ! -f "$inc/VapourSynth.h" ] && ln -sf VapourSynth4.h "$inc/VapourSynth.h" 2>/dev/null || true
            [ -f "$inc/VSConstants4.h" ] && [ ! -f "$inc/VSConstants.h" ] && ln -sf VSConstants4.h "$inc/VSConstants.h" 2>/dev/null || true
            [ -f "$inc/VSScript4.h" ] && [ ! -f "$inc/VSScript.h" ] && ln -sf VSScript4.h "$inc/VSScript.h" 2>/dev/null || true
        fi
    done

    # 2. Plugin directory creation
    mkdir -p /usr/lib/vapoursynth /usr/local/lib/vapoursynth
    if [ -d "/usr/lib64" ]; then
        mkdir -p /usr/lib64/vapoursynth
    fi
    if [ -d "/usr/local/lib64" ]; then
        mkdir -p /usr/local/lib64/vapoursynth
    fi
    if [ -n "$VS_PY_DIR" ]; then
        mkdir -p "$VS_PY_DIR/plugins"
    fi

    # 3. Cross-link ffms2 into all plugin directories
    for cand in /usr/lib/vapoursynth/libffms2.so /usr/lib64/vapoursynth/libffms2.so /usr/lib/libffms2.so* /usr/lib64/libffms2.so* /usr/local/lib/libffms2.so* /usr/local/lib64/libffms2.so*; do
        if [ -f "$cand" ]; then
            local real_ffms2
            real_ffms2="$(realpath "$cand")"
            if [ -f "$real_ffms2" ]; then
                [ -n "$VS_PY_DIR" ] && ln -sf "$real_ffms2" "$VS_PY_DIR/plugins/libffms2.so" 2>/dev/null || true
                ln -sf "$real_ffms2" /usr/lib/vapoursynth/libffms2.so 2>/dev/null || true
                ln -sf "$real_ffms2" /usr/local/lib/vapoursynth/libffms2.so 2>/dev/null || true
                [ -d "/usr/lib64/vapoursynth" ] && ln -sf "$real_ffms2" /usr/lib64/vapoursynth/libffms2.so 2>/dev/null || true
                break
            fi
        fi
    done

    # 4. Cross-link bestsource into all plugin directories
    local bs_cand
    bs_cand="$(find /usr/lib /usr/lib64 /usr/local/lib /usr/local/lib64 -name 'libbestsource.so' 2>/dev/null | head -n 1)"
    if [ -n "$bs_cand" ] && [ -f "$bs_cand" ]; then
        local real_bs
        real_bs="$(realpath "$bs_cand")"
        if [ -f "$real_bs" ]; then
            [ -n "$VS_PY_DIR" ] && ln -sf "$real_bs" "$VS_PY_DIR/plugins/libbestsource.so" 2>/dev/null || true
            ln -sf "$real_bs" /usr/lib/vapoursynth/libbestsource.so 2>/dev/null || true
            ln -sf "$real_bs" /usr/local/lib/vapoursynth/libbestsource.so 2>/dev/null || true
            [ -d "/usr/lib64/vapoursynth" ] && ln -sf "$real_bs" /usr/lib64/vapoursynth/libbestsource.so 2>/dev/null || true
        fi
    fi

    ldconfig 2>/dev/null || true
}

install_vs_plugin() {
    local src="$1"
    [ -f "$src" ] || return 1
    local name
    name="$(basename "$src")"
    local vs_py
    vs_py="$(get_vs_py_dir)"

    mkdir -p "/usr/lib/vapoursynth" "/usr/local/lib/vapoursynth"
    if [ -d "/usr/lib/x86_64-linux-gnu" ]; then
        mkdir -p "/usr/lib/x86_64-linux-gnu/vapoursynth"
    fi
    if [ -d "/usr/lib64" ]; then
        mkdir -p "/usr/lib64/vapoursynth"
    fi

    cp -f "$src" "/usr/lib/vapoursynth/$name"
    ln -sf "/usr/lib/vapoursynth/$name" "/usr/local/lib/vapoursynth/$name" 2>/dev/null || cp -f "$src" "/usr/local/lib/vapoursynth/$name"
    if [ -d "/usr/lib/x86_64-linux-gnu/vapoursynth" ]; then
        ln -sf "/usr/lib/vapoursynth/$name" "/usr/lib/x86_64-linux-gnu/vapoursynth/$name" 2>/dev/null || true
    fi
    if [ -d "/usr/lib64/vapoursynth" ]; then
        ln -sf "/usr/lib/vapoursynth/$name" "/usr/lib64/vapoursynth/$name" 2>/dev/null || true
    fi
    if [ -n "$vs_py" ]; then
        mkdir -p "$vs_py/plugins"
        ln -sf "/usr/lib/vapoursynth/$name" "$vs_py/plugins/$name" 2>/dev/null || cp -f "$src" "$vs_py/plugins/$name"
    fi
    ldconfig 2>/dev/null || true
}

uninstall_vs_plugin() {
    local name="$1"
    local vs_py
    vs_py="$(get_vs_py_dir)"
    rm -vf "/usr/lib/vapoursynth/$name" "/usr/local/lib/vapoursynth/$name" "/usr/lib/x86_64-linux-gnu/vapoursynth/$name" "/usr/lib64/vapoursynth/$name" 2>/dev/null || true
    if [ -n "$vs_py" ] && [ -d "$vs_py/plugins" ]; then
        rm -vf "$vs_py/plugins/$name" 2>/dev/null || true
    fi
    ldconfig 2>/dev/null || true
}
