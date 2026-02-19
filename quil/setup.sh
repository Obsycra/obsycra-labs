#!/bin/bash
# ═══════════════════════════════════════════════════════════════════════
# Quil — First-Time Setup
# Run once from the quil/ project root.
# ═══════════════════════════════════════════════════════════════════════

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

BOLD='\033[1m'; RESET='\033[0m'; GREEN='\033[32m'
RED='\033[31m'; YELLOW='\033[33m'; DIM='\033[2m'

ok()   { echo -e "  ${GREEN}✓${RESET}  $1"; }
warn() { echo -e "  ${YELLOW}⚠${RESET}  $1"; }
fail() { echo -e "  ${RED}✗${RESET}  $1"; }
sep()  { echo -e "\n${DIM}────────────────────────────────────────${RESET}"; }

echo ""
echo -e "${BOLD}  🪶  Quil Setup${RESET}"
sep

# ── 1. macOS prerequisites ──────────────────────────────────────────────
echo -e "\n${BOLD}System${RESET}"

# Xcode command line tools (needed for Rust/Tauri on macOS)
if ! xcode-select -p &>/dev/null; then
  warn "Xcode Command Line Tools not found. Installing..."
  xcode-select --install
  echo "  → After the installer finishes, re-run this script."
  exit 1
fi
ok "Xcode CLI tools"

# ── 2. Python ────────────────────────────────────────────────────────────
echo -e "\n${BOLD}Python${RESET}"

if ! command -v python3 &>/dev/null; then
  fail "Python 3 not found. Install from https://python.org"
  exit 1
fi

PY_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PY_MAJOR=$(echo "$PY_VER" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VER" | cut -d. -f2)

if [ "$PY_MAJOR" -lt 3 ] || [ "$PY_MINOR" -lt 11 ]; then
  fail "Python 3.11+ required. Found: $PY_VER"
  fail "Install newer Python from https://python.org or via: brew install python@3.12"
  exit 1
fi
ok "Python $PY_VER"

# ── 3. Python virtualenv + deps ─────────────────────────────────────────
echo -e "\n${BOLD}Python Dependencies${RESET}"

if [ ! -d "backend/.venv" ]; then
  echo "  → Creating virtualenv..."
  python3 -m venv backend/.venv
fi

source backend/.venv/bin/activate

echo "  → Installing packages (first run may take 2-3 minutes)..."
pip install --quiet --upgrade pip
pip install --quiet -r backend/requirements.txt

# Verify key imports
python3 -c "import fastapi, uvicorn, httpx, pydantic" 2>/dev/null \
  && ok "Core packages (fastapi, uvicorn, httpx, pydantic)" \
  || { fail "Core package install failed. Run: cd backend && pip install -r requirements.txt"; exit 1; }

python3 -c "import lancedb, pyarrow" 2>/dev/null \
  && ok "Vector store (lancedb, pyarrow)" \
  || warn "LanceDB not installed — vector search disabled. Try: pip install lancedb pyarrow"

deactivate

# ── 4. Rust ──────────────────────────────────────────────────────────────
echo -e "\n${BOLD}Rust${RESET}"

if ! command -v cargo &>/dev/null; then
  echo "  → Installing Rust via rustup..."
  curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --no-modify-path
  source "$HOME/.cargo/env"
fi

ok "Rust $(rustc --version 2>/dev/null | cut -d' ' -f2)"

# ── 5. Tauri CLI ─────────────────────────────────────────────────────────
echo -e "\n${BOLD}Tauri${RESET}"

if ! cargo tauri --version &>/dev/null 2>&1; then
  echo "  → Installing Tauri CLI (this takes a few minutes, compiling from source)..."
  cargo install tauri-cli --version "^2" --locked
fi

ok "Tauri CLI $(cargo tauri --version 2>/dev/null)"

# ── 6. Ollama ────────────────────────────────────────────────────────────
echo -e "\n${BOLD}Ollama + Models${RESET}"

if ! command -v ollama &>/dev/null; then
  warn "Ollama not found."
  echo ""
  echo "  Install it from: https://ollama.com/download"
  echo "  (It's a ~100MB app. After installing, come back and run:)"
  echo "  ollama pull qwen2.5:3b"
  echo "  ollama pull nomic-embed-text"
  echo ""
  warn "Skipping model pull — install Ollama first, then re-run this script."
else
  ok "Ollama $(ollama --version 2>/dev/null | head -1)"

  # Start Ollama temporarily to pull models
  OLLAMA_WAS_RUNNING=false
  if pgrep -x "ollama" > /dev/null 2>&1; then
    OLLAMA_WAS_RUNNING=true
  else
    echo "  → Starting Ollama to pull models..."
    ollama serve > /tmp/ollama_setup.log 2>&1 &
    OLLAMA_SETUP_PID=$!
    sleep 3
  fi

  # Pull chat model
  echo "  → Pulling qwen2.5:3b (chat model ~2GB)..."
  if ollama pull qwen3:4b 2>/dev/null; then
    ok "qwen2.5:3b ready"
  else
    warn "Could not pull qwen2.5:3b — run manually: ollama pull qwen2.5:3b"
  fi

  # Pull embedding model
  echo "  → Pulling nomic-embed-text (embedding model ~270MB)..."
  if ollama pull nomic-embed-text 2>/dev/null; then
    ok "nomic-embed-text ready"
  else
    warn "Could not pull nomic-embed-text — run manually: ollama pull nomic-embed-text"
  fi

  # Stop the temporary Ollama if we started it
  if [ "$OLLAMA_WAS_RUNNING" = false ] && [ -n "$OLLAMA_SETUP_PID" ]; then
    kill "$OLLAMA_SETUP_PID" 2>/dev/null || true
  fi
fi

# ── 7. Data directory ────────────────────────────────────────────────────
echo -e "\n${BOLD}Data${RESET}"
mkdir -p ~/.quil
ok "Data directory: ~/.quil"

# ── 8. Make scripts executable ───────────────────────────────────────────
chmod +x dev.sh test-backend.sh 2>/dev/null || true

# ── Done ─────────────────────────────────────────────────────────────────
sep
echo ""
echo -e "${BOLD}  ✅  Setup complete!${RESET}"
echo ""
echo "  Next steps:"
echo ""
echo -e "  ${BOLD}1. Test the backend + LLM (no Tauri needed):${RESET}"
echo "     ./test-backend.sh"
echo ""
echo -e "  ${BOLD}2. Run full dev mode (backend + Tauri window):${RESET}"
echo "     ./dev.sh"
echo ""
echo -e "  ${BOLD}3. Build the DMG for distribution:${RESET}"
echo "     ./bundle.sh"
echo ""