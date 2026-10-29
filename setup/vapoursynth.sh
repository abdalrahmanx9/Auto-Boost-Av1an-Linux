#!/bin/bash

# Source common functions if not already sourced
if [ -z "$COMMON_SOURCED" ]; then
    source "$(dirname "$0")/common.sh"
fi

install_vapoursynth() {
    if command -v vspipe &> /dev/null; then
        log_info "VapourSynth is already installed."
        return 0
    fi

    log_info "Compiling VapourSynth from Source..."
    
    mkdir -p build_tmp
    cd build_tmp || exit 1
    
    # 1. VapourSynth (R79 builds with meson; the old autotools flow is gone upstream)
    if [ -d "vapoursynth" ]; then rm -rf vapoursynth; fi
    git clone --branch R79 --depth 1 https://github.com/vapoursynth/vapoursynth.git || { log_error "Failed to clone VapourSynth"; cd ..; return 1; }
    cd vapoursynth || { log_error "Failed to cd into vapoursynth"; cd ..; cd ..; return 1; }
    meson setup build --prefix=/usr/local --buildtype=release || { log_error "VapourSynth meson setup failed"; cd ..; cd ..; return 1; }
    ninja -C build -j "$(nproc)" || { log_error "VapourSynth ninja build failed"; cd ..; cd ..; return 1; }
    ninja -C build install || { log_error "VapourSynth ninja install failed"; cd ..; cd ..; return 1; }
    ldconfig
    cd ..
    
    # Link Python module if not found (R79 installs into the active python's site-packages)
    local SITE_PKG_DIR
    SITE_PKG_DIR="$(find /usr/local/lib/python3* -maxdepth 2 -type d -name site-packages 2>/dev/null | sort -V | tail -n 1)"
    local DIST_PKG_DIR="/usr/lib/python3/dist-packages"
    
    if [ -f "$SITE_PKG_DIR/vapoursynth.so" ]; then
        log_info "Linking VapourSynth Python module to dist-packages..."
        ln -sf "$SITE_PKG_DIR/vapoursynth.so" "$DIST_PKG_DIR/vapoursynth.so"
    else
        log_warn "vapoursynth.so not found in $SITE_PKG_DIR"
    fi
    
    # 2. FFMS2
    log_info "Compiling FFMS2..."
    if [ -d "ffms2" ]; then rm -rf ffms2; fi
    # Use tag 5.0 (compatible with FFmpeg 6.x on Ubuntu 24.04)
    git clone --branch 5.0 --depth 1 https://github.com/FFMS/ffms2.git || { log_error "Failed to clone FFMS2"; cd ..; return 1; }
    cd ffms2 || { log_error "Failed to cd into ffms2"; cd ..; cd ..; return 1; }
    ./autogen.sh || { log_error "FFMS2 autogen failed"; cd ..; cd ..; return 1; }
    ./configure --enable-shared || { log_error "FFMS2 configure failed"; cd ..; cd ..; return 1; }
    make -j "$(nproc)" || { log_error "FFMS2 make failed"; cd ..; cd ..; return 1; }
    make install || { log_error "FFMS2 make install failed"; cd ..; cd ..; return 1; }
    cd ..
    
    # Symlink FFMS2 to VapourSynth Autoload Plugin Path
    local VS_PLUGIN_PATH="/usr/local/lib/vapoursynth"
    mkdir -p "$VS_PLUGIN_PATH"
    
    if [ -f "/usr/local/lib/libffms2.so" ]; then
        log_info "Linking FFMS2 to VapourSynth plugin folder..."
        ln -sf "/usr/local/lib/libffms2.so" "$VS_PLUGIN_PATH/libffms2.so"
    fi

    # 3. FFmpeg master (BestSource needs libavcodec >= 61.19.0, which Ubuntu
    #    noble does not ship). Built here rather than in system_deps so the
    #    "is build-essential present" skip can never silently drop it.
    log_info "Compiling FFmpeg master from source to satisfy BestSource (libavcodec >= 61.19.0)..."
    local FDIR="/tmp/ffmpeg_master_build"
    mkdir -p "$FDIR"
    cd "$FDIR" || return 1
    if [ -d "ffmpeg" ]; then rm -rf ffmpeg; fi
    git clone --depth 1 https://github.com/FFmpeg/FFmpeg.git ffmpeg || { log_error "Failed to clone ffmpeg repo"; cd ..; return 1; }
    cd ffmpeg || { log_error "Failed to cd into ffmpeg"; cd ..; return 1; }
    ./configure \
      --prefix="/usr/local" \
      --enable-shared \
      --enable-gpl \
      --enable-libx264 \
      --enable-libx265 \
      --enable-libass \
      --enable-libfreetype \
      --disable-doc \
      --disable-programs || { log_error "FFmpeg configure failed"; cd ..; cd ..; return 1; }
    make -j"$(nproc)" || { log_error "FFmpeg make failed"; cd ..; cd ..; return 1; }
    make install || { log_error "FFmpeg make install failed"; cd ..; cd ..; return 1; }
    ldconfig
    cd .. # FDIR

    # 4. BestSource (Alternative and preferred VapourSynth Source plugin)
    log_info "Compiling BestSource..."
    if [ -d "bestsource" ]; then rm -rf bestsource; fi
    git clone --depth 1 --recurse-submodules https://github.com/vapoursynth/bestsource.git || { log_error "Failed to clone BestSource"; cd ..; return 1; }
    cd bestsource || { log_error "Failed to cd into bestsource"; cd ..; cd ..; return 1; }
    
    # Export explicitly so meson finds the custom FFmpeg master build
    export PKG_CONFIG_PATH="/usr/local/lib/pkgconfig:$PKG_CONFIG_PATH"
    pip install meson --break-system-packages || true
    meson setup build || { log_error "BestSource meson setup failed"; cd ..; cd ..; return 1; }
    ninja -C build || { log_error "BestSource ninja build failed"; cd ..; cd ..; return 1; }
    ninja -C build install || { log_error "BestSource ninja install failed"; cd ..; cd ..; return 1; }
    
    # Ninja install typically places bestsource in /usr/local/lib/x86_64-linux-gnu/vapoursynth/ on Ubuntu 24.04
    # We should ensure VapourSynth can find it.
    if ls /usr/local/lib/x86_64-linux-gnu/vapoursynth/libbestsource* 1> /dev/null 2>&1; then
        log_info "Linking BestSource to VapourSynth plugin folder..."
        ln -sf /usr/local/lib/x86_64-linux-gnu/vapoursynth/libbestsource* "$VS_PLUGIN_PATH/"
    fi
    cd ..
    
    ldconfig
    cd .. # Exit build_tmp
    
    log_success "VapourSynth, FFMS2, and BestSource installed."
}

uninstall_vapoursynth() {
    log_info "Uninstalling VapourSynth and FFMS2..."
    
    # 1. Binaries
    rm -vf /usr/local/bin/vspipe
    
    # 2. Libraries
    rm -vf /usr/local/lib/libvapoursynth*
    rm -vf /usr/local/lib/libffms2*
    rm -vf /usr/local/lib/vapoursynth.so
    
    # 3. Headers
    rm -rf /usr/local/include/vapoursynth
    rm -rf /usr/local/include/ffms2
    
    # 4. Plugins
    rm -rf /usr/local/lib/vapoursynth
    
    # 5. Python Link
    rm -vf /usr/lib/python3/dist-packages/vapoursynth.so
    
    # 6. PkgConfig
    rm -vf /usr/local/lib/pkgconfig/vapoursynth.pc
    rm -vf /usr/local/lib/pkgconfig/ffms2.pc
    
    # Refresh libs
    ldconfig
    
    log_success "VapourSynth and FFMS2 uninstalled."
}
