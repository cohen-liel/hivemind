#!/bin/bash
# Start the Agent Dashboard
# Run from a normal Terminal (NOT inside Claude Code sandbox)
#
# Single entry point:  http://localhost:8080

set -e
cd "$(dirname "$0")"

# Kill any existing server
pkill -f "python.*server\.py" 2>/dev/null || true
pkill -f "node.*vite" 2>/dev/null || true
sleep 0.5

# Activate venv
if [ -d ".venv" ]; then
  source .venv/bin/activate
elif [ -d "venv" ]; then
  source venv/bin/activate
fi

# Build frontend (produces frontend/dist/ served by FastAPI)
echo "Building frontend..."
cd frontend
npx vite build --emptyOutDir 2>&1 | tail -3
cd ..

# Start the server — serves both API and frontend on one port
echo ""
echo "Starting server on http://localhost:8080"
echo "Open in browser or on your phone: http://$(ipconfig getifaddr en0 2>/dev/null || echo localhost):8080"
echo ""
python server.py
