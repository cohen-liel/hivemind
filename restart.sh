#!/bin/bash
# restart.sh — Build frontend + restart Nexus Agent OS
# Usage: ./restart.sh           (foreground — shows logs)
#        ./restart.sh --bg      (background — returns immediately)

set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

BG=false
[[ "$1" == "--bg" ]] && BG=true

# Get local IP for network access
LOCAL_IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo "localhost")
PORT=${DASHBOARD_PORT:-8080}

# Bind to all interfaces so LAN devices (phone, tablet) can connect.
# Rate limits are generous for local dev — a page load fires ~7 API calls
# in parallel; with multiple tabs the default burst-30 is too tight.
export DASHBOARD_HOST="${DASHBOARD_HOST:-0.0.0.0}"
export RATE_LIMIT_MAX_REQUESTS="${RATE_LIMIT_MAX_REQUESTS:-300}"
export RATE_LIMIT_BURST="${RATE_LIMIT_BURST:-60}"

echo ""
echo "  ⚡ Nexus — Agent OS"
echo "  ━━━━━━━━━━━━━━━━━━━"
echo ""

# 1. Kill ALL existing server processes aggressively
echo "  🔄 Stopping existing server..."
# Kill by port
lsof -ti :$PORT 2>/dev/null | xargs kill -9 2>/dev/null || true
# Kill by process name
pkill -9 -f "python3 server.py" 2>/dev/null || true
# Kill any orphaned caffeinate from previous runs
pkill -f "caffeinate.*-i" 2>/dev/null || true
sleep 2

# Verify port is free
if lsof -ti :$PORT >/dev/null 2>&1; then
  echo "  ❌ Port $PORT still in use! Kill it manually:"
  echo "     lsof -ti :$PORT | xargs kill -9"
  exit 1
fi
echo "  ✅ Port $PORT is free"

# 2. Build frontend
echo "  📦 Building frontend..."
cd frontend
npx vite build --logLevel error
cp public/manifest.json dist/manifest.json 2>/dev/null || true
cd ..
echo "  ✅ Frontend built"

# 3. Start server
if $BG; then
  echo "  🚀 Starting server (background)..."
  nohup ./venv/bin/python3 server.py > /tmp/claude-bot-server.log 2>&1 &
  SERVER_PID=$!

  # Wait for server to be ready
  for i in {1..15}; do
    if curl -s http://localhost:$PORT/api/health > /dev/null 2>&1; then
      echo "  ✅ Server running (PID: $SERVER_PID)"
      echo ""
      echo "  🌐 Local:   http://localhost:$PORT"
      echo "  📱 Network: http://$LOCAL_IP:$PORT"
      echo "  📋 Logs:    tail -f /tmp/claude-bot-server.log"
      echo ""
      exit 0
    fi
    sleep 1
  done
  echo "  ❌ Server failed to start. Check /tmp/claude-bot-server.log"
  tail -5 /tmp/claude-bot-server.log 2>/dev/null
  exit 1
else
  echo "  🚀 Starting server..."
  echo ""
  echo "  🌐 Local:   http://localhost:$PORT"
  echo "  📱 Network: http://$LOCAL_IP:$PORT"
  echo "  Press Ctrl+C to stop"
  echo ""
  ./venv/bin/python3 server.py
fi
