#!/bin/bash

# Source common functions if not already sourced
if [ -z "$COMMON_SOURCED" ]; then
    source "$(dirname "$0")/common.sh"
fi

install_wwxd() {
    local VS_PLUGIN_PATH=""
    if command -v pkg-config &> /dev/null; then
        VS_PLUGIN_PATH=$(pkg-config --variable=libdir vapoursynth)/vapoursynth
    else
        VS_PLUGIN_PATH="/usr/local/lib/vapoursynth"
    fi
    mkdir -p "$VS_PLUGIN_PATH"

    mkdir -p build_tmp
    cd build_tmp || exit 1

    log_info "Compiling VapourSynth-WWXD (API 4)..."
    if [ -d "vapoursynth-wwxd" ]; then rm -rf vapoursynth-wwxd; fi
    git clone --branch v1.0 --depth 1 https://github.com/dubhater/vapoursynth-wwxd.git || { log_error "Failed to clone WWXD"; cd ..; return 1; }
    cd vapoursynth-wwxd || { log_error "Failed to cd into vapoursynth-wwxd"; cd ..; cd ..; return 1; }
    
    # Check for VapourSynth headers
    if [ ! -d "/usr/local/include/vapoursynth" ]; then
        log_error "VapourSynth headers not found. Please install VapourSynth first."
        exit 1
    fi

    # Modernize wwxd.c for VapourSynth API 4
    cat << 'EOF' > src/wwxd.c
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <VapourSynth4.h>
#include <VSHelper4.h>

#include "detection.h"

typedef struct {
   VSNode *node;
   VSVideoInfo vi;

   MBParam Param;
   int intraCount;
   int fcode;

   uint8_t *padded_prevp;
   uint8_t *padded_curp;

   MACROBLOCK *pMBs;
} WwxdData;

static const VSFrame *VS_CC wwxdGetFrame(int n, int activationReason, void *instanceData, void **frameData, VSFrameContext *frameCtx, VSCore *core, const VSAPI *vsapi) {
   (void)frameData;
   WwxdData *d = (WwxdData *)instanceData;

   if (activationReason == arInitial) {
      vsapi->requestFrameFilter(n > 0 ? n - 1 : 0, d->node, frameCtx);
      vsapi->requestFrameFilter(n, d->node, frameCtx);
   } else if (activationReason == arAllFramesReady) {
      int scenechange, i;

      const VSFrame *prev = vsapi->getFrameFilter(n > 0 ? n - 1 : 0, d->node, frameCtx);
      const VSFrame *cur = vsapi->getFrameFilter(n, d->node, frameCtx);

      int stride = vsapi->getStride(cur, 0);

      uint8_t *padded_prevp = d->padded_prevp + d->Param.edge_size * d->Param.edged_width + d->Param.edge_size;
      uint8_t *padded_curp = d->padded_curp + d->Param.edge_size * d->Param.edged_width + d->Param.edge_size;

      vsh_bitblt(padded_prevp, d->Param.edged_width, vsapi->getReadPtr(prev, 0), stride, d->Param.width, d->Param.height);
      vsh_bitblt(padded_curp, d->Param.edged_width, vsapi->getReadPtr(cur, 0), stride, d->Param.width, d->Param.height);

      if (d->vi.width % 16) {
         int pad_width = 16 - (d->vi.width % 16);
         for (i = 0; i < d->vi.height; i++) {
            memset(padded_prevp + i*d->Param.edged_width + d->vi.width, *(padded_prevp + i*d->Param.edged_width + d->vi.width - 1), pad_width);
            memset(padded_curp + i*d->Param.edged_width + d->vi.width, *(padded_curp + i*d->Param.edged_width + d->vi.width - 1), pad_width);
         }
      }

      if (d->vi.height % 16) {
         int pad_height = 16 - (d->vi.height % 16);
         for (i = 0; i < pad_height; i++) {
            memcpy(padded_prevp + (d->vi.height + i)*d->Param.edged_width, padded_prevp + (d->vi.height - 1)*d->Param.edged_width, d->Param.edged_width);
            memcpy(padded_curp + (d->vi.height + i)*d->Param.edged_width, padded_curp + (d->vi.height - 1)*d->Param.edged_width, d->Param.edged_width);
         }
      }

      if (n == 0) {
         scenechange = 1;
      } else {
         scenechange = MEanalysis(padded_prevp, padded_curp, &d->Param, d->pMBs, d->intraCount, d->fcode);
      }

      if (scenechange) {
         d->intraCount = 1;
      } else {
         d->intraCount++;
      }

      VSFrame *dst = vsapi->copyFrame(cur, core);
      vsapi->freeFrame(prev);
      vsapi->freeFrame(cur);

      VSMap *props = vsapi->getFramePropertiesRW(dst);
      vsapi->mapSetInt(props, "Scenechange", scenechange, maReplace);

      return dst;
   }

   return NULL;
}

static void VS_CC wwxdFree(void *instanceData, VSCore *core, const VSAPI *vsapi) {
   (void)core;
   WwxdData *d = (WwxdData *)instanceData;

   vsapi->freeNode(d->node);
   free(d->padded_prevp);
   free(d->padded_curp);
   free(d->pMBs);
   free(d);
}

static void VS_CC wwxdCreate(const VSMap *in, VSMap *out, void *userData, VSCore *core, const VSAPI *vsapi) {
   (void)userData;
   WwxdData *d = (WwxdData *)malloc(sizeof(WwxdData));
   int err = 0;

   d->node = vsapi->mapGetNode(in, "clip", 0, &err);
   if (err) {
      vsapi->mapSetError(out, "WWXD: Failed to get clip");
      free(d);
      return;
   }
   const VSVideoInfo *vi = vsapi->getVideoInfo(d->node);
   d->vi = *vi;

   d->Param.edge_size = 64;
   d->Param.width = d->vi.width;
   d->Param.height = d->vi.height;
   d->Param.mb_width = (d->vi.width + 15) / 16;
   d->Param.mb_height = (d->vi.height + 15) / 16;
   d->Param.edged_width = 16 * d->Param.mb_width + 2 * d->Param.edge_size;
   d->Param.edged_height = 16 * d->Param.mb_height + 2 * d->Param.edge_size;

   d->intraCount = 1;
   d->fcode = 4;

   d->padded_prevp = (uint8_t *)malloc(d->Param.edged_width * (d->Param.edged_height + 1) + 64);
   d->padded_curp = (uint8_t *)malloc(d->Param.edged_width * (d->Param.edged_height + 1) + 64);
   memset(d->padded_prevp, 0, d->Param.edged_width * (d->Param.edged_height + 1) + 64);
   memset(d->padded_curp, 0, d->Param.edged_width * (d->Param.edged_height + 1) + 64);

   d->pMBs = (MACROBLOCK *)malloc(d->Param.mb_width * d->Param.mb_height * sizeof(MACROBLOCK));
   memset(d->pMBs, 0, d->Param.mb_width * d->Param.mb_height * sizeof(MACROBLOCK));

   VSFilterDependency deps[] = {{d->node, rpStrictSpatial}};
   vsapi->createVideoFilter(out, "WWXD", &d->vi, wwxdGetFrame, wwxdFree, fmUnordered, deps, 1, d, core);
}

VS_EXTERNAL_API(void) VapourSynthPluginInit2(VSPlugin *plugin, const VSPLUGINAPI *vspapi) {
   vspapi->configPlugin("com.nodame.wwxd", "wwxd", "Scene change detection approximately like Xvid's", VS_MAKE_VERSION(1, 0), VAPOURSYNTH_API_VERSION, 0, plugin);
   vspapi->registerFunction("WWXD", "clip:vnode;", "clip:vnode;", wwxdCreate, NULL, plugin);
}
EOF
    
    gcc -o libwwxd.so -fPIC -shared -O3 -Wall -Wextra -I. -I/usr/local/include/vapoursynth src/*.c -lm || \
        { log_error "Compilation failed"; cd ..; cd ..; return 1; }
    
    cp -v libwwxd.so "$VS_PLUGIN_PATH/" || { log_error "Failed to copy libwwxd.so"; cd ..; cd ..; return 1; }
    
    if [ -d "/usr/lib/x86_64-linux-gnu/vapoursynth" ]; then
        cp -v libwwxd.so "/usr/lib/x86_64-linux-gnu/vapoursynth/"
    fi
    cd ..
    cd .. # Exit build_tmp
    
    log_success "WWXD installed."
}

uninstall_wwxd() {
    log_info "Uninstalling WWXD..."
    find /usr/local/lib/vapoursynth -name "libwwxd.so" -delete
    find /usr/lib/x86_64-linux-gnu/vapoursynth -name "libwwxd.so" -delete
    log_success "WWXD uninstalled."
}
