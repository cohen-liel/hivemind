#!/usr/bin/env bash
# ============================================================
#  Hivemind — Agent OS: One-Time Setup
# ============================================================
#  Usage:  chmod +x setup.sh && ./setup.sh
# ============================================================
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m'

info()  { echo -e "${BLUE}[INFO]${NC}  $*"; }
ok()    { echo -e "${GREEN}  ✅${NC}  $*"; }
warn()  { echo -e "${YELLOW}  ⚠️${NC}   $*"; }
fail()  { echo -e "${RED}  ❌${NC}  $*"; exit 1; }

echo ""
echo -e "${BOLD}============================================================${NC}"
echo -e "${BOLD}  Hivemind — Agent OS: Setup${NC}"
echo -e "${BOLD}============================================================${NC}"
echo ""

OS="$(uname -s)"

# ── 1. Check Python ──────────────────────────────────────────
info "Checking Python..."
if command -v python3 &>/dev/null; then
    PY_VERSION=$(python3 --version 2>&1 | awk '{print $2}')
    ok "Python $PY_VERSION"
else
    fail "Python 3 is required. Install from https://python.org"
fi

# ── 2. Check Node.js ─────────────────────────────────────────
info "Checking Node.js..."
if command -v node &>/dev/null; then
    NODE_VERSION=$(node --version 2>&1)
    ok "Node.js $NODE_VERSION"
else
    fail "Node.js 18+ is required. Install from https://nodejs.org"
fi

# ── 3. Check Claude Code CLI ────────────────────────────────
info "Checking Claude Code CLI..."
CLAUDE_PATH="${CLAUDE_CLI_PATH:-claude}"
if command -v "$CLAUDE_PATH" &>/dev/null; then
    ok "Claude Code CLI found at $(which "$CLAUDE_PATH")"
else
    warn "Claude Code CLI not found."
    echo ""
    echo "    Install it with:"
    echo "      npm install -g @anthropic-ai/claude-code"
    echo "    Then login:"
    echo "      claude login"
    echo ""
fi

# ── 4. Create .env if missing ────────────────────────────────
info "Setting up configuration..."
if [ -f .env ]; then
    ok ".env already exists"
else
    cp .env.example .env
    ok ".env created (edit .env to customize settings)"
fi

# ── 5. Python virtual environment ────────────────────────────
info "Setting up Python environment..."
if [ -d venv ]; then
    ok "Virtual environment exists"
else
    python3 -m venv venv
    ok "Virtual environment created"
fi

info "Installing Python dependencies..."
./venv/bin/pip install -q -r requirements.txt 2>/dev/null
ok "Python dependencies installed"

# ── 6. Frontend ──────────────────────────────────────────────
info "Setting up frontend..."
cd frontend
if command -v pnpm &>/dev/null; then
    pnpm install --silent 2>/dev/null || npm install --silent
else
    npm install --silent
fi
cd ..
ok "Frontend dependencies installed"

info "Building frontend..."
cd frontend
npx vite build --logLevel error 2>/dev/null
cp public/manifest.json dist/manifest.json 2>/dev/null || true
cd ..
ok "Frontend built"

# ── 7. Create projects directory ─────────────────────────────
PROJECTS_DIR=$(grep -oP 'CLAUDE_PROJECTS_DIR=\K.*' .env 2>/dev/null || echo "~/hivemind-projects")
PROJECTS_DIR="${PROJECTS_DIR/#\~/$HOME}"
if [ ! -d "$PROJECTS_DIR" ]; then
    mkdir -p "$PROJECTS_DIR"
    ok "Created projects directory: $PROJECTS_DIR"
else
    ok "Projects directory: $PROJECTS_DIR"
fi

# ── 8. Install Cloudflare Tunnel (for remote access) ────────
info "Setting up remote access (Cloudflare Tunnel)..."
if command -v cloudflared &>/dev/null; then
    ok "cloudflared already installed"
else
    echo ""
    echo "    Cloudflare Tunnel lets you access Hivemind from anywhere"
    echo "    (phone, laptop, etc.) via a secure HTTPS link."
    echo ""

    INSTALL_CF=true

    if [[ "$OS" == "Darwin" ]]; then
        # macOS
        if command -v brew &>/dev/null; then
            info "Installing cloudflared via Homebrew..."
            brew install cloudflare/cloudflare/cloudflared 2>/dev/null && ok "cloudflared installed" || {
                warn "Auto-install failed. Install manually:"
                echo "      brew install cloudflare/cloudflare/cloudflared"
                INSTALL_CF=false
            }
        else
            warn "Homebrew not found. Install cloudflared manually:"
            echo "      brew install cloudflare/cloudflare/cloudflared"
            echo "    Or download from: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/"
            INSTALL_CF=false
        fi
    elif [[ "$OS" == "Linux" ]]; then
        # Linux
        ARCH=$(uname -m)
        if [[ "$ARCH" == "x86_64" ]]; then
            CF_ARCH="amd64"
        elif [[ "$ARCH" == "aarch64" || "$ARCH" == "arm64" ]]; then
            CF_ARCH="arm64"
        else
            CF_ARCH="amd64"
        fi

        info "Installing cloudflared for Linux ($CF_ARCH)..."
        CF_URL="https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-${CF_ARCH}"
        if curl -fsSL "$CF_URL" -o /tmp/cloudflared 2>/dev/null; then
            chmod +x /tmp/cloudflared
            sudo mv /tmp/cloudflared /usr/local/bin/cloudflared 2>/dev/null || mv /tmp/cloudflared ~/.local/bin/cloudflared 2>/dev/null || {
                mkdir -p ~/.local/bin
                mv /tmp/cloudflared ~/.local/bin/cloudflared
                export PATH="$HOME/.local/bin:$PATH"
            }
            ok "cloudflared installed"
        else
            warn "Auto-install failed. Install manually:"
            echo "      https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/"
            INSTALL_CF=false
        fi
    else
        warn "Unsupported OS for auto-install. Install cloudflared manually:"
        echo "      https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/"
        INSTALL_CF=false
    fi
fi

# ── Done ─────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}============================================================${NC}"
echo -e "  ${GREEN}${BOLD}Setup complete!${NC}"
echo ""
echo "  To start Hivemind:"
echo ""
echo -e "    ${BOLD}./restart.sh${NC}"
echo ""
echo "  This will:"
echo "    1. Build the frontend"
echo "    2. Start the server"
echo "    3. Open a secure tunnel (if cloudflared is installed)"
echo "    4. Print your access URL"
echo ""
if command -v cloudflared &>/dev/null; then
    echo -e "  ${GREEN}Remote access:${NC} A public HTTPS link will be shown"
    echo "  when you start the server. Use it from any device."
else
    echo -e "  ${YELLOW}Remote access:${NC} Install cloudflared for remote access."
    echo "  Without it, Hivemind is only accessible on this computer."
fi
echo ""
echo -e "${BOLD}============================================================${NC}"
echo ""
