#!/bin/bash

# Source common functions if not already sourced
if [ -z "$COMMON_SOURCED" ]; then
    source "$(dirname "$0")/common.sh"
fi

# VapourSynth filter plugins required by the settings.txt filter chain:
#   akarin    - akarin.Expr (fine_dehalo mask math)
#   awarp     - edge warp used by vsdehalo.edge_cleaner ([dehalo])
#   zsmooth   - zsmooth.Repair (fine_dehalo repair / contra-sharpen)
#   dfttest   - DFTTest denoise (vsdenoise wrapper)
#   vs-placebo- libplacebo Deband/Resample/Tonemap
# Every plugin is optional: a failure is logged and the rest continue.
vsf_plugin_dirs() {
    pkg-config --variable=libdir vapoursynth 2>/dev/null | sed 's|$|/vapoursynth|'
    echo "/usr/local/lib/vapoursynth"
    echo "/usr/lib/x86_64-linux-gnu/vapoursynth"
}

vsf_install_so() {
    local so="$1"
    for d in $(vsf_plugin_dirs); do
        if [ -d "$d" ]; then
            mkdir -p "$d"
            cp -v "$so" "$d/$(basename "$so")"
        fi
    done
    ldconfig
}

# build_cmake_plugin <name> <repo> [extra cmake args]
build_cmake_plugin() {
    local name="$1" repo="$2"; shift 2
    mkdir -p build_tmp && cd build_tmp || return 1
    [ -d "$name" ] && rm -rf "$name"
    git clone --depth 1 "$repo" "$name" || { log_warn "clone failed: $name"; cd ..; return 1; }
    cd "$name" || { log_warn "cd failed: $name"; cd ..; return 1; }
    mkdir -p build && cd build || { log_warn "mkdir failed: $name"; cd ../..; return 1; }
    cmake .. -DCMAKE_BUILD_TYPE=Release "$@" -DCMAKE_C_COMPILER=clang -DCMAKE_CXX_COMPILER=clang++ \
        || { log_warn "cmake failed: $name"; cd ../../..; return 1; }
    make -j "$(nproc)" || { log_warn "make failed: $name"; cd ../../..; return 1; }
    local SO
    SO="$(find . -name "*.so" -type f | head -n 1)"
    if [ -n "$SO" ]; then
        vsf_install_so "$SO"
        log_info "$name installed ($(basename "$SO"))."
    else
        log_warn "$name built but no .so found."
    fi
    cd ../../..
}

install_vs_filters() {
    log_info "Installing VapourSynth filter plugins..."

    # akarin (expr) - check python package wheel first, then build fallback
    local AKARIN_SO
    AKARIN_SO="$(find /usr/local/lib/python* /usr/lib/python* -name "libakarin.so" 2>/dev/null | head -n 1)"
    if [ -n "$AKARIN_SO" ]; then
        vsf_install_so "$AKARIN_SO"
        local AKARIN_DIR="$(dirname "$AKARIN_SO")"
        local AKARIN_LIBS="$(dirname "$AKARIN_DIR")/vapoursynth_akarin.libs"
        if [ -d "$AKARIN_LIBS" ]; then
            echo "$AKARIN_LIBS" > /etc/ld.so.conf.d/vapoursynth_akarin.conf
            ldconfig
        fi
        log_info "akarin installed from wheel ($AKARIN_SO)."
    else
        mkdir -p build_tmp && cd build_tmp || return 1
        [ -d "akarin" ] && rm -rf "akarin"
        if git clone --depth 1 "https://github.com/AkarinVS/vapoursynth-plugin.git" "akarin"; then
            cd akarin
            if meson setup build && ninja -C build; then
                find build -name "*.so" -type f -exec vsf_install_so {} \;
                log_info "akarin built and installed."
            else
                log_warn "akarin build failed (requires LLVM < 16). Try pip install vapoursynth-akarin."
            fi
            cd ..
        fi
        cd ..
    fi

    # AWarp - meson
    mkdir -p build_tmp && cd build_tmp || return 1
    [ -d "VapourSynth-AWarp" ] && rm -rf "VapourSynth-AWarp"
    if git clone --depth 1 "https://github.com/HolyWu/VapourSynth-AWarp.git" "VapourSynth-AWarp"; then
        cd VapourSynth-AWarp
        if meson setup build && ninja -C build; then
            find build -name "*.so" -type f -exec vsf_install_so {} \;
            log_info "AWarp built and installed."
        else
            log_warn "AWarp build failed."
        fi
        cd ..
    fi
    cd ..

    # DFTTest (API 4 build)
    mkdir -p build_tmp && cd build_tmp || return 1
    local DFT_URL="https://github.com/RyougiKukoc/VapourSynth-DFTTest-api4/releases/download/v1.1/dfttest-linux-x86_64.zip"
    if curl -sL -o dfttest-linux-x86_64.zip "$DFT_URL"; then
        if unzip -o dfttest-linux-x86_64.zip; then
            find dfttest -name "*.so" -type f -exec vsf_install_so {} \;
            log_info "DFTTest (API 4) installed."
        fi
    else
        log_warn "DFTTest download failed."
    fi
    cd ..

    # zsmooth - Zig build like vszip (build-help/build.sh fetches its own Zig)
    mkdir -p build_tmp && cd build_tmp || return 1
    if [ -d "zsmooth" ]; then rm -rf zsmooth; fi
    git clone --depth 1 https://github.com/adworacz/zsmooth.git || { log_warn "clone failed: zsmooth"; cd ..; }
    if [ -d "zsmooth" ]; then
        cd zsmooth || { cd ..; }
        if [ -d "build-help" ]; then
            cd build-help && chmod +x build.sh && ./build.sh \
                && find .. -name "libzsmooth.so" -type f -exec vsf_install_so {} \; \
                || log_warn "zsmooth build failed."
            cd ..
        elif command -v zig &> /dev/null; then
            zig build -Doptimize=ReleaseFast \
                && find . -name "libzsmooth.so" -type f -exec vsf_install_so {} \; \
                || log_warn "zsmooth zig build failed."
        else
            log_warn "zsmooth: no build-help/build.sh and no zig installed."
        fi
        cd ..
    fi
    cd .. # exit build_tmp

    # vs-placebo - meson, needs libplacebo-dev
    mkdir -p build_tmp && cd build_tmp || return 1
    if [ -d "vs-placebo" ]; then rm -rf vs-placebo; fi
    git clone --depth 1 https://github.com/Lypheo/vs-placebo.git || log_warn "clone failed: vs-placebo"
    if [ -d "vs-placebo" ]; then
        cd vs-placebo || { cd ..; }
        export PKG_CONFIG_PATH="/usr/local/lib/pkgconfig:$PKG_CONFIG_PATH"
        if meson setup build && ninja -C build; then
            local SO
            SO="$(find build -name "libvsplacebo.so" -o -name "vs_placebo.so" | head -n 1)"
            if [ -n "$SO" ]; then
                vsf_install_so "$SO"
            else
                ninja -C build install || log_warn "vs-placebo install target missing."
            fi
        else
            log_warn "vs-placebo build failed (libplacebo-dev installed?)."
        fi
        cd ..
    fi
    cd .. # exit build_tmp

    log_success "Filter plugin pass complete (failures above are non-fatal per plugin)."
}

uninstall_vs_filters() {
    log_info "Uninstalling VapourSynth filter plugins..."
    for d in $(vsf_plugin_dirs); do
        [ -d "$d" ] || continue
        rm -vf "$d/libakarin.so" "$d/libawarp.so" "$d/libdfttest.so" \
               "$d/libzsmooth.so" "$d/libvsplacebo.so" "$d/vs_placebo.so" 2>/dev/null
    done
    ldconfig
    log_success "Filter plugins uninstalled."
}
