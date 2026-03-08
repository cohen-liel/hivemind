#!/bin/bash
# restart.sh — Build frontend + restart Nexus Agent OS
# Usage: ./restart.sh           (foreground — shows logs)
#        ./restart.sh --bg      (background — returns immediately)

set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

BG=false
[[ "$1" == "--bg" ]] && BG=true

echo ""
echo "  ⚡ Nexus — Agent OS"
echo "  ━━━━━━━━━━━━━━━━━━━"
echo ""

# 1. Kill any existing server
echo "  🔄 Stopping existing server..."
lsof -ti :8080 2>/dev/null | xargs kill -9 2>/dev/null || true
sleep 1

# 2. Build frontend
echo "  📦 Building frontend..."
cd frontend
npx tsc && npx vite build --logLevel error
cp public/manifest.json dist/manifest.json 2>/dev/null || true
cd ..
echo "  ✅ Frontend built"

# 3. Start server
if $BG; then
  echo "  🚀 Starting server (background)..."
  ./venv/bin/python3 server.py > /tmp/claude-bot-server.log 2>&1 &
  SERVER_PID=$!

  # Wait for server
  for i in {1..15}; do
    if curl -s http://localhost:8080/api/stats > /dev/null 2>&1; then
      echo "  ✅ Server running (PID: $SERVER_PID)"
      echo ""
      echo "  🌐 http://localhost:8080"
      echo "  📱 PWA: Add to Home Screen from Safari"
      echo "  📋 Logs: tail -f /tmp/claude-bot-server.log"
      echo ""
      exit 0
    fi
    sleep 1
  done
  echo "  ❌ Server failed to start. Check /tmp/claude-bot-server.log"
  exit 1
else
  echo "  🚀 Starting server on http://localhost:8080"
  echo "  Press Ctrl+C to stop"
  echo ""
  ./venv/bin/python3 server.py
fi
