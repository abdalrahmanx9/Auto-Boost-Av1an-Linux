#!/bin/bash

# Source common functions if not already sourced
if [ -z "$COMMON_SOURCED" ]; then
    source "$(dirname "$0")/common.sh"
fi

# SVT-AV1 fork scheme, mirroring the Windows portable layout:
#   tools/av1an/svt-av1 forks/<name>/<arch>/SvtAv1EncApp
# The dispatcher's fork matcher looks for '5fish' / 'essential' / 'hdr' in the
# folder name and 'x86-64-v3' / 'znver2' / 'avx512' in the arch subfolder.
# Linux builds are native-tuned (>= x86-64-v3 on any modern CPU) and placed in
# the x86-64-v3 slot so every fork/arch combination resolves.
# 'custom' is never built: drop your own SvtAv1EncApp into
#   tools/av1an/svt-av1 forks/custom/x86-64-v3/
SVT_FISH_REPO="https://github.com/5fish/svt-av1-psy.git"
SVT_FISH_REF="9252c01ee243c2c329cfcc6804d24bbdccf48434"
SVT_ESSENTIAL_REPO="https://github.com/psy-ex/svt-av1-psy.git"
SVT_ESSENTIAL_REF="8dcb7f8d6bff69bd73f663aac61849468f3a9d71"
SVT_HDR_REPO="https://github.com/emrakyz/svt-av1-hdr.git"
SVT_HDR_REF="df788d61dca67c7117af05e52a24f7ee9b1f8178"

# build_svt_fork <repo> <ref> <name> <extra cmake args...>
build_svt_fork() {
    local repo="$1" ref="$2" name="$3"; shift 3
    local REPO_ROOT
    REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
    local FORK_DIR="$REPO_ROOT/tools/av1an/svt-av1 forks/$name/x86-64-v3"

    if [ -x "$FORK_DIR/SvtAv1EncApp" ] && ! ask_yes_no "Rebuild $name fork?" "N"; then
        log_info "$name fork already present - skipping."
        return 0
    fi

    log_info "Building SVT-AV1 fork: $name (Pinned: ${ref:0:7})..."
    mkdir -p build_tmp
    cd build_tmp || return 1
    local SRC="svt-$name"
    [ -d "$SRC" ] && rm -rf "$SRC"
    clone_exact_commit "$repo" "$ref" "$SRC" || { log_error "Failed to clone $name"; cd ..; return 1; }
    cd "$SRC" || { log_error "Failed to cd into $SRC"; cd ../..; return 1; }

    sed -i 's/\r$//' Source/App/CMakeLists.txt 2>/dev/null
    sed -i 's/target_link_libraries(SvtAv1EncApp ${PLATFORM_LIBS})/target_link_libraries(SvtAv1EncApp PRIVATE ${PLATFORM_LIBS})/' Source/App/CMakeLists.txt 2>/dev/null
    sed -i 's/target_link_libraries(SvtAv1EncApp$/target_link_libraries(SvtAv1EncApp PRIVATE/' Source/App/CMakeLists.txt 2>/dev/null

    mkdir -p Build/linux && cd Build/linux || { log_error "mkdir Build/linux failed"; cd ../../..; return 1; }
    cmake ../.. -G"Unix Makefiles" -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=OFF \
        -DCMAKE_C_COMPILER=clang -DCMAKE_CXX_COMPILER=clang++ "$@" \
        || { log_error "$name cmake failed"; cd ../../../..; return 1; }
    make -j "$(nproc)" || { log_error "$name make failed"; cd ../../../..; return 1; }

    mkdir -p "$FORK_DIR"
    local APP_BIN
    if [ -f "SvtAv1EncApp" ]; then
        APP_BIN="SvtAv1EncApp"
    elif [ -f "../../Bin/Release/SvtAv1EncApp" ]; then
        APP_BIN="../../Bin/Release/SvtAv1EncApp"
    elif [ -f "Bin/Release/SvtAv1EncApp" ]; then
        APP_BIN="Bin/Release/SvtAv1EncApp"
    else
        APP_BIN="$(find ../.. -name SvtAv1EncApp -type f -perm -111 2>/dev/null | head -n 1)"
    fi

    if [ -n "$APP_BIN" ] && [ -f "$APP_BIN" ]; then
        cp -v "$APP_BIN" "$FORK_DIR/SvtAv1EncApp" || { log_error "copy failed"; cd ../../../..; return 1; }
    else
        log_error "SvtAv1EncApp binary not found after build"; cd ../../../..; return 1
    fi
    cd ../../.. # build_tmp/<src>
    cd ..      # build_tmp
    log_info "$name fork installed to: $FORK_DIR"
    return 0
}

install_svt_av1() {
    # Ensure llvm-profdata for PGO
    if ! command -v llvm-profdata &> /dev/null; then
        local LLVM_PROFDATA
        LLVM_PROFDATA=$(find /usr/bin -name "llvm-profdata-*" | sort -V | tail -n 1)
        if [ -n "$LLVM_PROFDATA" ]; then
            log_info "Found $LLVM_PROFDATA. Linking..."
            ln -sf "$LLVM_PROFDATA" /usr/local/bin/llvm-profdata
        else
            log_warn "llvm-profdata not found. PGO might fail."
        fi
    fi

    # 1. 5fish (default fork): PGO+LTO native build, also installed system-wide
    build_svt_fork "$SVT_FISH_REPO" "$SVT_FISH_REF" "5fish-svt-av1-psy" \
        -DENABLE_AVX512=ON -DNATIVE=ON -DSVT_AV1_PGO=ON -DSVT_AV1_LTO=ON \
        || return 1

    local REPO_ROOT
    REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
    cp -v "$REPO_ROOT/tools/av1an/svt-av1 forks/5fish-svt-av1-psy/x86-64-v3/SvtAv1EncApp" /usr/local/bin/SvtAv1EncApp

    # 2. essential fork
    build_svt_fork "$SVT_ESSENTIAL_REPO" "$SVT_ESSENTIAL_REF" "svt-av1-essential" \
        -DENABLE_AVX512=ON -DNATIVE=ON \
        || log_warn "essential fork build failed - install it later or drop a binary into 'svt-av1 forks/svt-av1-essential/x86-64-v3/'."

    # 3. hdr fork
    build_svt_fork "$SVT_HDR_REPO" "$SVT_HDR_REF" "svt-av1-hdr" \
        -DENABLE_AVX512=ON -DNATIVE=ON \
        || log_warn "hdr fork build failed - install it later or drop a binary into 'svt-av1 forks/svt-av1-hdr/x86-64-v3/'."

    # 4. custom slot (user supplied)
    mkdir -p "$REPO_ROOT/tools/av1an/svt-av1 forks/custom/x86-64-v3"

    log_success "SVT-AV1 forks installed (5fish + essential + hdr + custom slot)."
}

uninstall_svt_av1() {
    log_info "Uninstalling SVT-AV1-PSY..."
    rm -vf /usr/local/bin/SvtAv1EncApp
    rm -vf /usr/local/lib/libSvtAv1Enc*
    rm -rf /usr/local/include/svt-av1
    rm -vf /usr/local/lib/pkgconfig/SvtAv1Enc.pc
    local REPO_ROOT
    REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
    rm -rf "$REPO_ROOT/tools/av1an/svt-av1 forks"
    log_success "SVT-AV1-PSY uninstalled."
}
