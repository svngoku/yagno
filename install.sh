#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────
#  Yagno installer
#
#  Usage:
#    curl -fsSL https://raw.githubusercontent.com/svngoku/yagno/main/install.sh | bash
#
#  What it does:
#    1. Installs uv (Astral's fast Python package manager) if not present
#    2. Ensures Python >= 3.11 is available
#    3. Installs yagno into an isolated tool environment via `uv tool`
#    4. Verifies the installation
# ──────────────────────────────────────────────────────────────────────
set -euo pipefail

YAGNO_VERSION="${YAGNO_VERSION:-}"          # pin: YAGNO_VERSION=1.1.0
YAGNO_REPO="https://github.com/svngoku/yagno"
MIN_PYTHON="3.11"

# ── Colours ───────────────────────────────────────────────────────────
if [ -t 1 ]; then
    BOLD="\033[1m"
    CYAN="\033[36m"
    GREEN="\033[32m"
    YELLOW="\033[33m"
    RED="\033[31m"
    DIM="\033[2m"
    RESET="\033[0m"
else
    BOLD="" CYAN="" GREEN="" YELLOW="" RED="" DIM="" RESET=""
fi

info()  { printf "${CYAN}>${RESET} %s\n" "$*"; }
ok()    { printf "${GREEN}✓${RESET} %s\n" "$*"; }
warn()  { printf "${YELLOW}!${RESET} %s\n" "$*"; }
err()   { printf "${RED}✗${RESET} %s\n" "$*" >&2; }
die()   { err "$*"; exit 1; }

# ── Detect OS / arch ─────────────────────────────────────────────────
detect_platform() {
    OS="$(uname -s)"
    ARCH="$(uname -m)"
    case "$OS" in
        Linux*)  PLATFORM="linux" ;;
        Darwin*) PLATFORM="macos" ;;
        *)       die "Unsupported OS: $OS" ;;
    esac
    info "Platform: $PLATFORM ($ARCH)"
}

# ── Install uv if missing ────────────────────────────────────────────
ensure_uv() {
    if command -v uv &>/dev/null; then
        ok "uv found: $(uv --version)"
        return
    fi

    info "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh

    # Source the env so uv is on PATH for this session
    if [ -f "$HOME/.local/bin/env" ]; then
        # shellcheck disable=SC1091
        . "$HOME/.local/bin/env"
    fi
    export PATH="$HOME/.local/bin:$PATH"

    command -v uv &>/dev/null || die "uv installation failed — please install manually: https://docs.astral.sh/uv/getting-started/installation/"
    ok "uv installed: $(uv --version)"
}

# ── Ensure Python >= 3.11 ────────────────────────────────────────────
ensure_python() {
    # uv can fetch Python for us if needed
    if uv python find "$MIN_PYTHON" &>/dev/null; then
        PYTHON_PATH="$(uv python find "$MIN_PYTHON")"
        ok "Python found: $PYTHON_PATH"
        return
    fi

    info "Python >= $MIN_PYTHON not found, installing via uv..."
    uv python install "$MIN_PYTHON"
    PYTHON_PATH="$(uv python find "$MIN_PYTHON")"
    ok "Python installed: $PYTHON_PATH"
}

# ── Install yagno ────────────────────────────────────────────────────
install_yagno() {
    local pkg="yagno"
    if [ -n "$YAGNO_VERSION" ]; then
        pkg="yagno==$YAGNO_VERSION"
    fi

    info "Installing $pkg..."

    # uv tool install puts yagno in an isolated venv with its own bin on PATH
    if uv tool install "$pkg" --python "$MIN_PYTHON" 2>/dev/null; then
        ok "yagno installed via PyPI"
        return
    fi

    # Fallback: install from git if not yet published on PyPI
    warn "PyPI install failed, trying git source..."
    uv tool install "yagno @ git+${YAGNO_REPO}.git" --python "$MIN_PYTHON" \
        || die "Failed to install yagno. Check ${YAGNO_REPO} for manual instructions."
    ok "yagno installed from git"
}

# ── Verify ────────────────────────────────────────────────────────────
verify() {
    if ! command -v yagno &>/dev/null; then
        # uv tool bin might not be on PATH yet
        warn "yagno not found on PATH — you may need to add it:"
        printf "  ${CYAN}export PATH=\"\$HOME/.local/bin:\$PATH\"${RESET}\n"
        printf "  Then run: ${CYAN}yagno --help${RESET}\n"
        return
    fi
    ok "yagno is ready"
    echo
    yagno --help
}

# ── Main ──────────────────────────────────────────────────────────────
main() {
    printf "\n${BOLD}Yagno Installer${RESET}\n"
    printf "${DIM}YAML-first declarative layer for Agno agents${RESET}\n\n"

    detect_platform
    ensure_uv
    ensure_python
    install_yagno
    verify

    echo
    printf "${GREEN}${BOLD}All done!${RESET} Get started:\n"
    echo
    printf "  ${CYAN}yagno init my-project${RESET}     # scaffold a new project\n"
    printf "  ${CYAN}cd my-project${RESET}\n"
    printf "  ${CYAN}cp .env.example .env${RESET}      # add your API keys\n"
    printf "  ${CYAN}yagno run specs/*.yaml -i '\"Hello\"'${RESET}\n"
    echo
}

main "$@"
