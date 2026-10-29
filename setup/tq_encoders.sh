#!/bin/bash

# Source common functions if not already sourced
if [ -z "$COMMON_SOURCED" ]; then
    source "$(dirname "$0")/common.sh"
fi

# Target-quality encoders: Condor and LQTC.
# These are NOT open-source builds the installer can compile - the user must
# supply Linux binaries. This module creates the expected layout and verifies
# placement, mirroring the Windows portable folder contract:
#
#   tools/av1an/condor                      -> condor binary (av1an-fork TQ encoder)
#   tools/LQTC/lqtc-<backend>-<fork>-<arch> -> LQTC binaries (Rust)
#   tools/LQTC/display.json                 -> CVVDP viewing conditions file
#   video-output/grav1synth                 -> film-grain table generator (LQTC)
install_tq_encoders() {
    local REPO_ROOT
    REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
    local CONDOR_DIR="$REPO_ROOT/tools/av1an"
    local LQTC_DIR="$REPO_ROOT/tools/LQTC"

    mkdir -p "$CONDOR_DIR" "$LQTC_DIR" "$REPO_ROOT/video-output"

    echo "----------------------------------------------------------"
    echo " Condor + LQTC target-quality encoder setup"
    echo "----------------------------------------------------------"
    if [ -x "$CONDOR_DIR/condor" ] || command -v condor &> /dev/null; then
        log_info "condor: found"
    else
        log_warn "condor: MISSING"
        log_info "  Place your Linux condor binary at: tools/av1an/condor"
        log_info "  (or anywhere in PATH). See the encoder's own docs."
    fi

    if ls "$LQTC_DIR"/lqtc-* &> /dev/null 2>&1; then
        log_info "LQTC: found $(ls "$LQTC_DIR"/lqtc-* | wc -l) binary(ies)"
    else
        log_warn "LQTC: MISSING"
        log_info "  Place lqtc-<cuda|vulkan>-<essential|hdr>-<arch> binaries in: tools/LQTC/"
        log_info "  And the CVVDP display.json beside them."
    fi
    if [ ! -f "$LQTC_DIR/display.json" ]; then
        log_warn "display.json: MISSING (needed for LQTC CVVDP metric)"
    fi
    if [ ! -d "$REPO_ROOT/video-output/grav1synth" ]; then
        log_info "  Optional: place grav1synth grain tables into video-output/grav1synth"
    fi
    echo "----------------------------------------------------------"

    log_success "TQ encoder layout prepared. Missing binaries are non-fatal:"
    log_success "Condor/LQTC scripts will report 'not installed' until supplied."
}

uninstall_tq_encoders() {
    local REPO_ROOT
    REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
    rm -rf "$REPO_ROOT/tools/LQTC"
    rm -vf "$REPO_ROOT/tools/av1an/condor"
    log_success "TQ encoders removed."
}
