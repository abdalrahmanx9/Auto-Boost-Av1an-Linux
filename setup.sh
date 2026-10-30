#!/bin/bash
# If called with sh instead of bash (e.g. via sudo ./setup.sh), re-exec with bash
if [ -z "$BASH_VERSION" ]; then exec bash "$0" "$@"; fi

# Auto-Boost-Av1an Setup Script
# Modularized Installer with Dependency Resolution

BASE_DIR="$(dirname "$(realpath "$0")")"
SETUP_DIR="$BASE_DIR/setup"

# Source all modules
source "$SETUP_DIR/common.sh"
COMMON_SOURCED=true
source "$SETUP_DIR/system_deps.sh"
source "$SETUP_DIR/python_libs.sh"
source "$SETUP_DIR/vapoursynth.sh"
source "$SETUP_DIR/av1an.sh"
source "$SETUP_DIR/svt_av1.sh"
source "$SETUP_DIR/ffvship.sh"
source "$SETUP_DIR/oxipng.sh"
source "$SETUP_DIR/wwxd.sh"
source "$SETUP_DIR/vszip.sh"
source "$SETUP_DIR/subtext.sh"
source "$SETUP_DIR/vs_filters.sh"
source "$SETUP_DIR/mlrt.sh"
source "$SETUP_DIR/tq_encoders.sh"

# Dependency Graph Definition
# Format: declare -A DEPS
# DEPS[tool]="dep1 dep2"
declare -A DEPENDENCIES
DEPENDENCIES["python_libs"]="system_deps"
DEPENDENCIES["vapoursynth"]="python_libs system_deps"
DEPENDENCIES["av1an"]="vapoursynth system_deps"
DEPENDENCIES["svt_av1"]="system_deps"
DEPENDENCIES["ffvship"]="vapoursynth system_deps"
DEPENDENCIES["oxipng"]="system_deps"
DEPENDENCIES["wwxd"]="vapoursynth"
DEPENDENCIES["vszip"]="vapoursynth"
DEPENDENCIES["subtext"]="vapoursynth"
DEPENDENCIES["vs_filters"]="vapoursynth"
DEPENDENCIES["mlrt"]="vapoursynth system_deps"
DEPENDENCIES["tq_encoders"]="system_deps"

# Helper: Check if a tool is installed
is_installed() {
    local tool=$1
    case "$tool" in
        "system_deps") 
            dpkg -s build-essential &> /dev/null
            ;;
        "python_libs")
            pip3 show vsjetpack &> /dev/null
            ;;
        "vapoursynth")
            command -v vspipe &> /dev/null
            ;;
        "av1an")
            command -v av1an &> /dev/null
            ;;
        "svt_av1")
            command -v SvtAv1EncApp &> /dev/null
            ;;
        "ffvship")
            command -v FFVship &> /dev/null || ls "$(dirname "$(realpath "$0")")/tools/vs-hip/"libvship_*.so &> /dev/null
            ;;
        "oxipng")
            command -v oxipng &> /dev/null
            ;;
        "wwxd")
            [ -f "$(pkg-config --variable=libdir vapoursynth 2>/dev/null)/vapoursynth/libwwxd.so" ] || \
            [ -f "/usr/lib/x86_64-linux-gnu/vapoursynth/libwwxd.so" ] || \
            [ -f "/usr/local/lib/vapoursynth/libwwxd.so" ]
            ;;
        "vszip")
            [ -f "$(pkg-config --variable=libdir vapoursynth 2>/dev/null)/vapoursynth/libvszip.so" ] || \
            [ -f "/usr/lib/x86_64-linux-gnu/vapoursynth/libvszip.so" ] || \
            [ -f "/usr/local/lib/vapoursynth/libvszip.so" ]
            ;;
        "subtext")
            [ -f "$(pkg-config --variable=libdir vapoursynth 2>/dev/null)/vapoursynth/libsubtext.so" ] || \
            [ -f "/usr/lib/x86_64-linux-gnu/vapoursynth/libsubtext.so" ] || \
            [ -f "/usr/local/lib/vapoursynth/libsubtext.so" ]
            ;;
        "vs_filters")
            [ -f "$(pkg-config --variable=libdir vapoursynth 2>/dev/null)/vapoursynth/libakarin.so" ] || \
            [ -f "/usr/lib/x86_64-linux-gnu/vapoursynth/libakarin.so" ] || \
            [ -f "/usr/local/lib/vapoursynth/libakarin.so" ]
            ;;
        "mlrt")
            [ -f "$(pkg-config --variable=libdir vapoursynth 2>/dev/null)/vapoursynth/libvstrt.so" ] || \
            [ -f "/usr/lib/x86_64-linux-gnu/vapoursynth/libvstrt.so" ] || \
            [ -f "/usr/local/lib/vapoursynth/libvsort.so" ] || \
            [ -f "/usr/local/lib/vapoursynth/libvsmigx.so" ] || \
            [ -f "/usr/local/lib/vapoursynth/vsncnn.so" ] || \
            [ -f "/usr/local/lib/vapoursynth/vsov.so" ]
            ;;
        "tq_encoders")
            [ -x "$(dirname "$(realpath "$0")")/tools/av1an/condor" ] || \
            ls "$(dirname "$(realpath "$0")")/tools/LQTC"/lqtc-* &> /dev/null
            ;;
        *) return 1 ;;
    esac
}

get_status_icon() {
    if is_installed "$1"; then
        echo -e "${GREEN}[INSTALLED]${NC}"
    else
        echo -e "${RED}[MISSING]${NC}"
    fi
}

# Global variables for dependency resolution
declare -A RESOLVE_VISITED
declare -a RESOLVE_PLAN

resolve_deps_recursive() {
    local tool=$1
    if [[ -n "${RESOLVE_VISITED[$tool]}" ]]; then return; fi
    RESOLVE_VISITED[$tool]=1
    local deps="${DEPENDENCIES[$tool]}"
    for dep in $deps; do
        resolve_deps_recursive "$dep"
    done
    RESOLVE_PLAN+=("$tool")
}

# Wrapper to install a tool with deps
install_tool() {
    local target_tool=$1
    
    RESOLVE_PLAN=()
    RESOLVE_VISITED=()
    resolve_deps_recursive "$target_tool"
    
    echo ""
    log_info "Target: $target_tool"
    echo "Dependency Tree Calculation:"
    local install_queue=()
    
    for item in "${RESOLVE_PLAN[@]}"; do
        if is_installed "$item"; then
            echo -e "  - $item: ${GREEN}Already Installed${NC}"
        else
            echo -e "  - $item: ${RED}To be installed${NC}"
            install_queue+=("$item")
        fi
    done
    
    if [ ${#install_queue[@]} -eq 0 ]; then
        log_success "All dependencies for $target_tool are already installed."
        if [ "$FORCE_YES" == "1" ]; then
            return 0
        fi
        if ! ask_yes_no "Re-install/Update $target_tool anyway?" "N"; then
            return 0
        fi
        install_queue+=("$target_tool")
    fi

    echo ""
    log_info "The following will be installed: ${install_queue[*]}"
    if [ "$FORCE_YES" != "1" ]; then
        if ! ask_yes_no "Proceed?" "Y"; then
            log_warn "Installation cancelled."
            return 0
        fi
    fi
    
    for item in "${install_queue[@]}"; do
        log_info "Installing: $item"
        local status=0
        case "$item" in
            "system_deps") install_system_deps ;;
            "python_libs") install_python_libs ;;
            "vapoursynth") install_vapoursynth ;;
            "av1an") install_av1an ;;
            "svt_av1") install_svt_av1 ;;
            "ffvship") install_ffvship ;;
            "oxipng") install_oxipng ;;
            "wwxd") install_wwxd ;;
            "vszip") install_vszip ;;
            "subtext") install_subtext ;;
            "vs_filters") install_vs_filters ;;
            "mlrt") install_mlrt ;;
            "tq_encoders") install_tq_encoders ;;
            *) log_error "Unknown module: $item"; exit 1 ;;
        esac
        status=$?
        if [ $status -ne 0 ]; then
            log_error "Failed to install $item (Exit code: $status). Stopping."
            return 1
        fi
    done
    
    log_success "Operation completed."
}

uninstall_tool() {
    local target_tool=$1
    echo ""
    log_warn "You are about to UNINSTALL: $target_tool"
    if [ "$FORCE_YES" != "1" ]; then
        if ! ask_yes_no "Are you sure?" "N"; then
            return 0
        fi
    fi
    
    case "$target_tool" in
        "system_deps") uninstall_system_deps ;;
        "python_libs") uninstall_python_libs ;;
        "vapoursynth") uninstall_vapoursynth ;;
        "av1an") uninstall_av1an ;;
        "svt_av1") uninstall_svt_av1 ;;
        "ffvship") uninstall_ffvship ;;
        "oxipng") uninstall_oxipng ;;
        "wwxd") uninstall_wwxd ;;
        "vszip") uninstall_vszip ;;
        "subtext") uninstall_subtext ;;
        "vs_filters") uninstall_vs_filters ;;
        "mlrt") uninstall_mlrt ;;
        "tq_encoders") uninstall_tq_encoders ;;
        *) log_error "Unknown module: $target_tool"; exit 1 ;;
    esac
}

install_all_tools() {
    local all_tools=("system_deps" "python_libs" "vapoursynth" "av1an" "svt_av1" "ffvship" "oxipng" "wwxd" "vszip" "subtext" "vs_filters" "mlrt" "tq_encoders")
    log_info "Starting Full Installation..."
    for t in "${all_tools[@]}"; do
        install_tool "$t" || { log_error "Failed to install $t. Aborting batch."; return 1; }
    done
}

uninstall_all_tools() {
    local all_tools=("system_deps" "python_libs" "vapoursynth" "av1an" "svt_av1" "ffvship" "oxipng" "wwxd" "vszip" "subtext" "vs_filters" "mlrt" "tq_encoders")
    log_warn "Starting Full Uninstallation..."
    # Reverse order for uninstall
    for (( i=${#all_tools[@]}-1; i>=0; i-- )); do
        uninstall_tool "${all_tools[$i]}"
    done
}

show_menu() {
    local mode=$1
    if [ -z "$mode" ]; then mode="INSTALL"; fi
    
    clear
    echo "=========================================================="
    echo "   Auto-Boost-Av1an Setup Menu"
    echo "=========================================================="
    echo "Mode: $mode"
    echo "----------------------------------------------------------"
    printf "%-3s %-30s %s\n" "ID" "Module" "Status"
    printf "%-3s %-30s %s\n" "--" "------" "------"
    printf "%-3s %-30s %s\n" "1" "Av1an" "$(get_status_icon av1an)"
    printf "%-3s %-30s %s\n" "2" "VapourSynth (+FFMS2/BestSource)" "$(get_status_icon vapoursynth)"
    printf "%-3s %-30s %s\n" "3" "SVT-AV1 forks (5fish+essential+hdr)" "$(get_status_icon svt_av1)"
    printf "%-3s %-30s %s\n" "4" "Vship 5.1.0 + FFVship" "$(get_status_icon ffvship)"
    printf "%-3s %-30s %s\n" "5" "oxipng" "$(get_status_icon oxipng)"
    printf "%-3s %-30s %s\n" "6" "WWXD" "$(get_status_icon wwxd)"
    printf "%-3s %-30s %s\n" "7" "VSZIP 19" "$(get_status_icon vszip)"
    printf "%-3s %-30s %s\n" "8" "SubText" "$(get_status_icon subtext)"
    printf "%-3s %-30s %s\n" "9" "Filter plugins" "$(get_status_icon vs_filters)"
    printf "%-3s %-30s %s\n" "12" "vs-mlrt (AI rescale)" "$(get_status_icon mlrt)"
    printf "%-3s %-30s %s\n" "13" "Condor + LQTC layout" "$(get_status_icon tq_encoders)"
    echo "----------------------------------------------------------"
    echo "10. System Deps Only"
    echo "11. Python Libs Only"
    if [ "$mode" == "INSTALL" ]; then
        echo "A. Install Everything (Full Setup)"
    else
        echo "A. Uninstall Everything"
    fi
    
    if [ "$mode" == "INSTALL" ]; then
        echo "T. Toggle to UNINSTALL Mode"
    else
        echo "T. Toggle to INSTALL Mode"
    fi
    echo "Q. Quit"
    echo "=========================================================="
    read -p "Select option(s) (e.g., 1 3 5): " choice_input
    
    # Toggle Mode
    if [[ "$choice_input" =~ ^[Tt]$ ]]; then
        if [ "$mode" == "INSTALL" ]; then show_menu "UNINSTALL"; else show_menu "INSTALL"; fi
        return
    fi
    
    # Quit
    if [[ "$choice_input" =~ ^[Qq]$ ]]; then exit 0; fi
    
    # Install/Uninstall All
    if [[ "$choice_input" =~ ^[Aa]$ ]]; then
        if [ "$mode" == "INSTALL" ]; then
            install_all_tools
        else
            if ask_yes_no "Are you sure you want to uninstall EVERYTHING?" "N"; then
                uninstall_all_tools
            fi
        fi
        
        echo "Press Enter to return to menu..."
        read
        show_menu "$mode"
        return
    fi
    
    # Split input by space or comma
    # Replace commas with spaces
    choice_input="${choice_input//,/ }"
    
    for choice in $choice_input; do
        local tool=""
        case "$choice" in
            1) tool="av1an" ;;
            2) tool="vapoursynth" ;;
            3) tool="svt_av1" ;;
            4) tool="ffvship" ;;
            5) tool="oxipng" ;;
            6) tool="wwxd" ;;
            7) tool="vszip" ;;
            8) tool="subtext" ;;
            9) tool="vs_filters" ;;
            10) tool="system_deps" ;;
            11) tool="python_libs" ;;
            12) tool="mlrt" ;;
            13) tool="tq_encoders" ;;
            *) log_warn "Invalid option: $choice (skipping)"; continue ;;
        esac
        
        if [ -n "$tool" ]; then
            if [ "$mode" == "INSTALL" ]; then
                install_tool "$tool" || { log_error "Failed to install $tool. Stopping batch."; break; }
            else
                uninstall_tool "$tool"
            fi
        fi
    done
    
    echo "Press Enter to return to menu..."
    read
    show_menu "$mode"
}

# Main Execution Flow
check_root
check_distro

if [ "$1" == "--status" ]; then
    echo "=========================================================="
    echo "   Auto-Boost-Av1an Setup Status"
    echo "=========================================================="
    printf "%-3s %-35s %s\n" "ID" "Module" "Status"
    printf "%-3s %-35s %s\n" "--" "------" "------"
    printf "%-3s %-35s %s\n" "1" "Av1an" "$(get_status_icon av1an)"
    printf "%-3s %-35s %s\n" "2" "VapourSynth (+FFMS2/BestSource)" "$(get_status_icon vapoursynth)"
    printf "%-3s %-35s %s\n" "3" "SVT-AV1 forks (5fish+essential+hdr)" "$(get_status_icon svt_av1)"
    printf "%-3s %-35s %s\n" "4" "Vship 5.1.0 + FFVship" "$(get_status_icon ffvship)"
    printf "%-3s %-35s %s\n" "5" "oxipng" "$(get_status_icon oxipng)"
    printf "%-3s %-35s %s\n" "6" "WWXD" "$(get_status_icon wwxd)"
    printf "%-3s %-35s %s\n" "7" "VSZIP 19" "$(get_status_icon vszip)"
    printf "%-3s %-35s %s\n" "8" "SubText" "$(get_status_icon subtext)"
    printf "%-3s %-35s %s\n" "9" "Filter plugins" "$(get_status_icon vs_filters)"
    printf "%-3s %-35s %s\n" "10" "System Deps Only" "$(get_status_icon system_deps)"
    printf "%-3s %-35s %s\n" "11" "Python Libs Only" "$(get_status_icon python_libs)"
    printf "%-3s %-35s %s\n" "12" "vs-mlrt (AI rescale)" "$(get_status_icon mlrt)"
    printf "%-3s %-35s %s\n" "13" "Condor + LQTC layout" "$(get_status_icon tq_encoders)"
    echo "=========================================================="
    exit 0
elif [ "$1" == "--install" ] && [ -n "$2" ]; then
    if [ "$3" == "-y" ] || [ "$3" == "--yes" ]; then
        export FORCE_YES=1
    fi
    if [[ "$2" =~ ^[Aa]$ || "$2" == "all" ]]; then
        install_all_tools
    else
        install_tool "$2"
    fi
elif [ "$1" == "--uninstall" ] && [ -n "$2" ]; then
    if [ "$3" == "-y" ] || [ "$3" == "--yes" ]; then
        export FORCE_YES=1
    fi
    if [[ "$2" =~ ^[Aa]$ || "$2" == "all" ]]; then
        if [ "$FORCE_YES" == "1" ] || ask_yes_no "Are you sure you want to uninstall EVERYTHING?" "N"; then
            uninstall_all_tools
        fi
    else
        uninstall_tool "$2"
    fi
else
    show_menu "INSTALL"
fi
