#!/bin/bash

# Source common functions if not already sourced
if [ -z "$COMMON_SOURCED" ]; then
    source "$(dirname "$0")/common.sh"
fi

install_vapoursynth() {
    local VS_PLUGIN_PATH="/usr/local/lib/vapoursynth"
    mkdir -p "$VS_PLUGIN_PATH"
    mkdir -p build_tmp

    # Configure VapourSynth runtime paths (vapoursynth.toml)
    configure_vs_toml() {
        python3 -c '
import os, pathlib, glob
paths = [
    "/usr/lib/python3/dist-packages/vapoursynth/libvsscript.so",
    "/usr/local/lib/python3/dist-packages/vapoursynth/libvsscript.so",
    "/usr/local/lib/libvsscript.so",
]
for p in glob.glob("/usr/local/lib/python3*/dist-packages/vapoursynth/libvsscript.so"):
    paths.append(p)
val = ["/usr/bin/python3", "/usr/lib/x86_64-linux-gnu/libpython3.12.so.1.0", "1788171506"]
lines = [f"\"{p}\" = [\"{val[0]}\", \"{val[1]}\", \"{val[2]}\"]\n" for p in set(paths)]
content = "".join(lines)
for target in [pathlib.Path.home() / ".config/vapoursynth/vapoursynth.toml", pathlib.Path("/etc/vapoursynth.toml"), pathlib.Path("/root/.config/vapoursynth/vapoursynth.toml")]:
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    except Exception:
        pass
' 2>/dev/null || true
    }

    if ! command -v vspipe &> /dev/null; then
        log_info "Compiling VapourSynth from Source..."
        cd build_tmp || exit 1
        
        # 1. VapourSynth R79 (Pinned commit acabf60)
        if [ -d "vapoursynth" ]; then rm -rf vapoursynth; fi
        clone_exact_commit "https://github.com/vapoursynth/vapoursynth.git" "acabf605b2205b32d65859bb2736405719d2fafd" "vapoursynth" || { log_error "Failed to clone VapourSynth"; cd ..; return 1; }
        cd vapoursynth || { log_error "Failed to cd into vapoursynth"; cd ..; cd ..; return 1; }
        meson setup build --prefix=/usr/local --buildtype=release || { log_error "VapourSynth meson setup failed"; cd ..; cd ..; return 1; }
        ninja -C build -j "$(nproc)" || { log_error "VapourSynth ninja build failed"; cd ..; cd ..; return 1; }
        ninja -C build install || { log_error "VapourSynth ninja install failed"; cd ..; cd ..; return 1; }
        ldconfig
        cd ..

        # Deploy components from the R79 Meson Python install directory:
        local VS_PY_DIR
        VS_PY_DIR="$(find /usr/local/lib/python3* -type d -name vapoursynth 2>/dev/null | head -n 1)"
        if [ -n "$VS_PY_DIR" ] && [ -d "$VS_PY_DIR" ]; then
            log_info "Deploying VapourSynth R79 system components from $VS_PY_DIR..."
            if [ -f "$VS_PY_DIR/vspipe" ]; then
                ln -sf "$VS_PY_DIR/vspipe" /usr/local/bin/vspipe
                chmod +x /usr/local/bin/vspipe
                log_info "Linked vspipe -> /usr/local/bin/vspipe"
            fi
            for f in "$VS_PY_DIR"/libvapoursynth*.so* "$VS_PY_DIR"/libvsscript*.so*; do
                [ -e "$f" ] && ln -sf "$f" /usr/local/lib/
            done
            ldconfig
            mkdir -p /usr/local/lib/pkgconfig
            if [ -f "$VS_PY_DIR/pkgconfig/vapoursynth.pc" ]; then
                cp -f "$VS_PY_DIR/pkgconfig/vapoursynth.pc" /usr/local/lib/pkgconfig/vapoursynth.pc
                if ! grep -q "^libdir=" /usr/local/lib/pkgconfig/vapoursynth.pc; then
                    sed -i '1i libdir=/usr/local/lib' /usr/local/lib/pkgconfig/vapoursynth.pc
                    echo "Libs: -L\${libdir} -lvapoursynth" >> /usr/local/lib/pkgconfig/vapoursynth.pc
                fi
            fi
            mkdir -p /usr/local/include
            if [ -d "$VS_PY_DIR/include" ]; then
                ln -sfn "$VS_PY_DIR/include" /usr/local/include/vapoursynth
            fi
            local DIST_PKG_DIR="/usr/lib/python3/dist-packages"
            if [ -d "$DIST_PKG_DIR" ] && [ ! -e "$DIST_PKG_DIR/vapoursynth" ]; then
                ln -sfn "$VS_PY_DIR" "$DIST_PKG_DIR/vapoursynth"
            fi
            ln -sfn "$VS_PLUGIN_PATH" "$VS_PY_DIR/plugins" 2>/dev/null || true
            if [ -d "$DIST_PKG_DIR/vapoursynth" ]; then
                ln -sfn "$VS_PLUGIN_PATH" "$DIST_PKG_DIR/vapoursynth/plugins" 2>/dev/null || true
            fi
        fi
        cd .. # Exit build_tmp
    else
        log_info "VapourSynth core is already installed."
    fi

    configure_vs_toml

    # Ensure plugin directory symlinks
    local VS_PY_DIR
    VS_PY_DIR="$(find /usr/local/lib/python3* -type d -name vapoursynth 2>/dev/null | head -n 1)"
    if [ -n "$VS_PY_DIR" ]; then
        ln -sfn "$VS_PLUGIN_PATH" "$VS_PY_DIR/plugins" 2>/dev/null || true
    fi
    local DIST_PKG_DIR="/usr/lib/python3/dist-packages"
    if [ -d "$DIST_PKG_DIR" ]; then
        ln -sfn "$VS_PLUGIN_PATH" "$DIST_PKG_DIR/vapoursynth/plugins" 2>/dev/null || true
    fi

    # 2. FFMS2 (Pinned to 5.0 commit 7ed5e4d)
    if [ ! -f "$VS_PLUGIN_PATH/libffms2.so" ] && [ ! -f "/usr/local/lib/libffms2.so" ]; then
        log_info "Compiling FFMS2..."
        cd "$BASE_DIR/build_tmp" || exit 1
        if [ -d "ffms2" ]; then rm -rf ffms2; fi
        clone_exact_commit "https://github.com/FFMS/ffms2.git" "7ed5e4d039ca9a6236bd2ebdfdd656c4304fbe04" "ffms2" || { log_error "Failed to clone FFMS2"; cd ..; return 1; }
        cd ffms2 || { log_error "Failed to cd into ffms2"; cd ..; cd ..; return 1; }
        ./autogen.sh || { log_error "FFMS2 autogen failed"; cd ..; cd ..; return 1; }
        export PKG_CONFIG_PATH="/usr/local/lib/pkgconfig:$PKG_CONFIG_PATH"
        ./configure --enable-shared || { log_error "FFMS2 configure failed"; cd ..; cd ..; return 1; }
        make -j "$(nproc)" || { log_error "FFMS2 make failed"; cd ..; cd ..; return 1; }
        make install || { log_error "FFMS2 make install failed"; cd ..; cd ..; return 1; }
        cd ..
    fi

    if [ -f "/usr/local/lib/libffms2.so" ]; then
        ln -sf "/usr/local/lib/libffms2.so" "$VS_PLUGIN_PATH/libffms2.so"
    fi

    # 3. FFmpeg master & BestSource (Pinned FFmpeg commit b6d737a, BestSource 9faf675)
    if [ ! -f "$VS_PLUGIN_PATH/libbestsource.so" ]; then
        log_info "Compiling FFmpeg master from source to satisfy BestSource..."
        local FDIR="/tmp/ffmpeg_master_build"
        mkdir -p "$FDIR"
        cd "$FDIR" || return 1
        if [ ! -d "ffmpeg" ]; then
            clone_exact_commit "https://github.com/FFmpeg/FFmpeg.git" "b6d737a49c3422ea81381851fa49ef750c9fd367" "ffmpeg" || { log_error "Failed to clone ffmpeg repo"; cd ..; return 1; }
        fi
        cd ffmpeg || { log_error "Failed to cd into ffmpeg"; cd ..; cd ..; return 1; }
        if [ ! -f "config.mak" ]; then
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
        fi
        make -j"$(nproc)" || { log_error "FFmpeg make failed"; cd ..; cd ..; return 1; }
        make install || { log_error "FFmpeg make install failed"; cd ..; cd ..; return 1; }
        ldconfig
        cd .. # FDIR

        # 4. BestSource
        log_info "Compiling BestSource..."
        cd "$BASE_DIR/build_tmp" || return 1
        if [ -d "bestsource" ]; then rm -rf bestsource; fi
        clone_exact_commit "https://github.com/vapoursynth/bestsource.git" "9faf6750b3ac3519cf18987f34d4212a7a06ddea" "bestsource" || { log_error "Failed to clone BestSource"; cd ..; return 1; }
        cd bestsource || { log_error "Failed to cd into bestsource"; cd ..; cd ..; return 1; }
        git submodule update --init --recursive || true
        
        # Patch BestSource for VapourSynth R79 (API 4.0/4.1) compatibility
        python3 -c '
path = "src/vapoursynth.cpp"
with open(path, "r") as f:
    c = f.read()
c = c.replace("clip:vnode:all;", "clip:vnode;")
target = """        if (D->GpuExport)
            Node = vsapi->createVideoFilterEx2(\"VideoSource\", &D->VI, BestVideoSourceGetFrame, BestVideoSourceFree, fmUnordered,
                ffGPUOutput, nullptr, 0, D, Core);
        else
            Node = vsapi->createVideoFilter2(\"VideoSource\", &D->VI, BestVideoSourceGetFrame, BestVideoSourceFree, fmUnordered, nullptr, 0, D, Core);"""
replacement = """        Node = vsapi->createVideoFilter2(\"VideoSource\", &D->VI, BestVideoSourceGetFrame, BestVideoSourceFree, fmUnordered, nullptr, 0, D, Core);"""
if target in c:
    c = c.replace(target, replacement)
with open(path, "w") as f:
    f.write(c)
'

        export PKG_CONFIG_PATH="/usr/local/lib/pkgconfig:$PKG_CONFIG_PATH"
        pip install meson --break-system-packages || true
        meson setup build -Denable_gpu=disabled || { log_error "BestSource meson setup failed"; cd ..; cd ..; return 1; }
        ninja -C build || { log_error "BestSource ninja build failed"; cd ..; cd ..; return 1; }
        ninja -C build install || { log_error "BestSource ninja install failed"; cd ..; cd ..; return 1; }
        
        find /usr/local/lib -name "libbestsource.so*" -exec ln -sf {} "$VS_PLUGIN_PATH/libbestsource.so" \; 2>/dev/null || true
        cd ..
    fi

    ldconfig
    log_success "VapourSynth, FFMS2, and BestSource installed."
}

uninstall_vapoursynth() {
    log_info "Uninstalling VapourSynth, FFMS2, and BestSource..."
    
    # 1. Binaries
    rm -vf /usr/local/bin/vspipe
    
    # 2. Libraries
    rm -vf /usr/local/lib/libvapoursynth*
    rm -vf /usr/local/lib/libffms2*
    rm -vf /usr/local/lib/libbestsource*
    rm -vf /usr/local/lib/vapoursynth.so
    
    # 3. Headers
    rm -rf /usr/local/include/vapoursynth
    rm -rf /usr/local/include/ffms2
    rm -rf /usr/local/include/bestsource
    
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
