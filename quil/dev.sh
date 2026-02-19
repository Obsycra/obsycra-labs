#!/bin/bash
# ═══════════════════════════════════════════════════════════════════════
# Quil — Dev Mode
# Starts Ollama + backend + Tauri window. Ctrl+C cleanly shuts down all.
# ═══════════════════════════════════════════════════════════════════════

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

GREEN='\033[32m'; YELLOW='\033[33m'; BOLD='\033[1m'; RESET='\033[0m'

ok()   { echo -e "  ${GREEN}✓${RESET}  $1"; }
warn() { echo -e "  ${YELLOW}⚠${RESET}  $1"; }
step() { echo -e "\n  ${BOLD}→${RESET}  $1"; }

BACKEND_PID=""
OLLAMA_PID=""

cleanup() {
  echo -e "\n\n  Shutting down Quil..."
  [ -n "$BACKEND_PID" ] && kill "$BACKEND_PID" 2>/dev/null && ok "Backend stopped"
  [ -n "$OLLAMA_PID"  ] && kill "$OLLAMA_PID"  2>/dev/null && ok "Ollama stopped"
  echo "  Goodbye."
}
trap cleanup EXIT INT TERM

echo ""
echo -e "${BOLD}  🪶  Starting Quil (dev mode)${RESET}"

# ── Verify virtualenv exists ──────────────────────────────────────────────
if [ ! -d "backend/.venv" ]; then
  echo "  ✗  backend/.venv not found. Run ./setup.sh first."
  exit 1
fi

# ── 1. Ollama ────────────────────────────────────────────────────────────
step "Checking Ollama..."

if pgrep -x "ollama" > /dev/null 2>&1; then
  ok "Ollama already running"
else
  ollama serve > /tmp/quil_ollama.log 2>&1 &
  OLLAMA_PID=$!

  for i in {1..10}; do
    curl -sf http://localhost:11434/api/tags > /dev/null 2>&1 && break
    sleep 1
    if [ $i -eq 10 ]; then
      echo "  ✗  Ollama failed to start. Log: /tmp/quil_ollama.log"
      exit 1
    fi
  done
  ok "Ollama started (PID $OLLAMA_PID)"
fi

# ── 2. Python backend ────────────────────────────────────────────────────
step "Starting Quil backend on :8765..."

# Kill any leftover process holding the port before binding
STALE_PID=$(lsof -ti :8765 2>/dev/null)
if [ -n "$STALE_PID" ]; then
  kill -9 $STALE_PID 2>/dev/null
  sleep 0.5
  warn "Killed stale process on :8765 (PID $STALE_PID)"
fi

source backend/.venv/bin/activate
cd backend
python3 main.py > /tmp/quil_backend.log 2>&1 &
BACKEND_PID=$!
cd ..

for i in {1..20}; do
  curl -sf http://127.0.0.1:8765/health > /dev/null 2>&1 && break
  sleep 1
  if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
    echo "  ✗  Backend crashed on startup. Log:"
    tail -20 /tmp/quil_backend.log
    exit 1
  fi
  if [ $i -eq 20 ]; then
    echo "  ✗  Backend didn't respond in 20s. Log:"
    tail -20 /tmp/quil_backend.log
    exit 1
  fi
done

HEALTH=$(curl -sf http://127.0.0.1:8765/health)
MODEL_READY=$(echo "$HEALTH" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('model_ready'))" 2>/dev/null)
ok "Backend running (PID $BACKEND_PID)"

if [ "$MODEL_READY" = "True" ]; then
  ok "LLM model ready"
else
  warn "LLM model not confirmed ready. Pull it with: ollama pull qwen3:4b"
fi

# ── 3. Tauri frontend ────────────────────────────────────────────────────
step "Starting Tauri window..."
echo "  (First Tauri build compiles Rust — may take 2-5 minutes)"
echo "  Backend logs: tail -f /tmp/quil_backend.log"
echo ""

# cargo tauri dev serves the HTML from src/ and opens the native window
cargo tauri dev

# If cargo tauri dev exits, we exit too (trap handles cleanup)