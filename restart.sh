#!/bin/bash
# ============================================================
#  Hivemind — Agent OS: Start / Restart
# ============================================================
#  Usage:  ./restart.sh           (foreground — shows logs)
#          ./restart.sh --bg      (background — returns immediately)
#          ./restart.sh --no-clear (don't clear history)
# ============================================================
set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

BG=false
CLEAR_HISTORY=true
for arg in "$@"; do
  case "$arg" in
    --bg)       BG=true ;;
    --no-clear) CLEAR_HISTORY=false ;;
  esac
done

PORT=${DASHBOARD_PORT:-8080}

# Detect local IP (cross-platform)
get_local_ip() {
  # macOS
  ipconfig getifaddr en0 2>/dev/null && return
  ipconfig getifaddr en1 2>/dev/null && return
  # Linux
  hostname -I 2>/dev/null | awk '{print $1}' && return
  # Fallback
  echo "localhost"
}
LOCAL_IP=$(get_local_ip)

# Bind to all interfaces so LAN devices can connect
export DASHBOARD_HOST="0.0.0.0"
export RATE_LIMIT_MAX_REQUESTS="300"
export RATE_LIMIT_BURST="100"

echo ""
echo "  ⚡ Hivemind — Agent OS"
echo "  ━━━━━━━━━━━━━━━━━━━"
echo ""

# ── 1. Stop existing server ─────────────────────────────────
echo "  🔄 Stopping existing server..."
lsof -ti :$PORT 2>/dev/null | xargs kill -9 2>/dev/null || true
pkill -9 -f "python3 server.py" 2>/dev/null || true
pkill -f "cloudflared tunnel" 2>/dev/null || true
sleep 1

# Verify port is free
if lsof -ti :$PORT >/dev/null 2>&1; then
  echo "  ❌ Port $PORT still in use."
  echo "     Run: lsof -ti :$PORT | xargs kill -9"
  exit 1
fi
echo "  ✅ Port $PORT is free"

# ── 2. Optionally clear history ─────────────────────────────
if $CLEAR_HISTORY; then
  echo "  🧹 Clearing agent history..."
  if [ -f data/platform.db ]; then
    sqlite3 data/platform.db "DELETE FROM agent_actions;" 2>/dev/null || true
    sqlite3 data/platform.db "DELETE FROM messages;" 2>/dev/null || true
    sqlite3 data/platform.db "VACUUM;" 2>/dev/null || true
  fi
  rm -f state_snapshot.json 2>/dev/null || true
  rm -rf .hivemind/agent_logs/* 2>/dev/null || true
  echo "  ✅ History cleared"
else
  echo "  ⏭️  Keeping existing history"
fi

# ── 3. Resolve Python & install deps ──────────────────────
# Find python (support both venv and .venv)
if [ -f ./venv/bin/python3 ]; then
  PY=./venv/bin/python3
elif [ -f ./.venv/bin/python3 ]; then
  PY=./.venv/bin/python3
else
  PY=python3
fi

$PY -m pip install -q -r requirements.txt 2>/dev/null || true

# ── 4. Build frontend ────────────────────────────────────
echo "  📦 Building frontend..."
cd frontend
npx vite build --logLevel error 2>/dev/null
cp public/manifest.json dist/manifest.json 2>/dev/null || true
cd ..
echo "  ✅ Frontend built"

# ── 5. Start server ─────────────────────────────────────────
LOG_FILE="/tmp/hivemind-server.log"

if $BG; then
  echo "  🚀 Starting server (background)..."
  nohup $PY server.py > "$LOG_FILE" 2>&1 &
  SERVER_PID=$!

  # Wait for server to be ready
  for i in {1..20}; do
    if curl -s http://localhost:$PORT/api/health > /dev/null 2>&1; then
      echo "  ✅ Server running (PID: $SERVER_PID)"

      # Wait for cloudflare tunnel URL
      TUNNEL_URL=""
      if command -v cloudflared &>/dev/null; then
        for j in {1..20}; do
          TUNNEL_URL=$(grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' "$LOG_FILE" 2>/dev/null | head -1)
          if [ -n "$TUNNEL_URL" ]; then
            echo "$TUNNEL_URL" | pbcopy 2>/dev/null || true
            break
          fi
          sleep 1
        done
      fi

      QR_TARGET="${TUNNEL_URL:-http://$LOCAL_IP:$PORT}"
      echo ""
      echo "  ╔══════════════════════════════════════════════════════╗"
      echo "  ║  🌐 Local:   http://localhost:$PORT                     ║"
      echo "  ║  🏠 Network: http://$LOCAL_IP:$PORT                     ║"
      if [ -n "$TUNNEL_URL" ]; then
      echo "  ║  🌍 Public:  $TUNNEL_URL"
      fi
      echo "  ║  📋 Logs:    tail -f $LOG_FILE              ║"
      echo "  ╚══════════════════════════════════════════════════════╝"
      echo ""
      exit 0
    fi
    sleep 1
  done
  echo "  ❌ Server failed to start. Check: tail -20 $LOG_FILE"
  tail -10 "$LOG_FILE" 2>/dev/null
  exit 1
else
  echo "  🚀 Starting server..."
  echo ""

  # Run server in background, tail the log, and wait for the URL
  $PY server.py > "$LOG_FILE" 2>&1 &
  SERVER_PID=$!

  # Wait for server to be ready
  echo "  ⏳ Waiting for server..."
  for i in {1..30}; do
    if curl -s http://localhost:$PORT/api/health > /dev/null 2>&1; then
      echo "  ✅ Server running (PID: $SERVER_PID)"

      # Collect access code
      ACCESS_CODE=$(grep "ACCESS CODE:" "$LOG_FILE" 2>/dev/null | tail -1 | sed 's/.*ACCESS CODE:  *//')

      # Wait for Cloudflare tunnel URL
      echo "  ⏳ Waiting for public URL..."
      TUNNEL_URL=""
      for j in {1..30}; do
        TUNNEL_URL=$(grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' "$LOG_FILE" 2>/dev/null | head -1)
        if [ -n "$TUNNEL_URL" ]; then
          echo "$TUNNEL_URL" | pbcopy 2>/dev/null || true
          break
        fi
        sleep 1
      done

      # ── Print unified startup banner ──────────────────────────
      QR_TARGET="${TUNNEL_URL:-http://$LOCAL_IP:$PORT}"
      echo ""
      echo "  ╔══════════════════════════════════════════════════════╗"
      echo "  ║              ⚡ Hivemind is running                  ║"
      echo "  ╠══════════════════════════════════════════════════════╣"
      echo "  ║                                                      ║"
      echo "  ║  🌐 Local:   http://localhost:$PORT                     ║"
      echo "  ║  🏠 Network: http://$LOCAL_IP:$PORT                     ║"
      if [ -n "$TUNNEL_URL" ]; then
      echo "  ║                                                      ║"
      echo "  ║  🌍 Public:  $TUNNEL_URL"
      echo "  ║             (copied to clipboard)                    ║"
      fi
      echo "  ║                                                      ║"
      if [ -n "$ACCESS_CODE" ]; then
      echo "  ╠══════════════════════════════════════════════════════╣"
      echo "  ║                                                      ║"
      echo "  ║  🔑 Access Code:  $ACCESS_CODE                           ║"
      echo "  ║     Enter in browser to connect (new devices only)   ║"
      echo "  ║                                                      ║"
      fi
      echo "  ╠══════════════════════════════════════════════════════╣"
      echo "  ║                                                      ║"
      echo "  ║  📱 Scan QR to open on your phone:                   ║"
      echo "  ║                                                      ║"
      # Inline QR code
      $PY -c "
try:
    from terminal_qr import print_qr_for_url
    print_qr_for_url('$QR_TARGET')
except Exception:
    print('     (install qrcode: pip install qrcode)')
" 2>/dev/null
      echo "  ║                                                      ║"
      echo "  ╠══════════════════════════════════════════════════════╣"
      echo "  ║  📋 Logs: tail -f $LOG_FILE              ║"
      echo "  ║  🛑 Stop: kill $SERVER_PID                                   ║"
      echo "  ╚══════════════════════════════════════════════════════╝"
      echo ""

      # Follow logs
      tail -f "$LOG_FILE"
      exit 0
    fi
    sleep 1
  done
  echo "  ❌ Server failed to start. Check: tail -20 $LOG_FILE"
  tail -10 "$LOG_FILE" 2>/dev/null
  exit 1
fi
