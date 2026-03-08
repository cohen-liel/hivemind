#!/bin/bash
# restart.sh — Kill old server, build frontend, start fresh
# Usage: ./restart.sh

set -e

cd "$(dirname "$0")"

echo "🔄 Restarting web-claude-bot..."

# 1. Kill any existing server on port 8080
echo "  ⏹  Killing old server..."
lsof -ti :8080 2>/dev/null | xargs kill -9 2>/dev/null || true
sleep 1

# 2. Build frontend
echo "  🔨 Building frontend..."
cd frontend
./node_modules/.bin/vite build --logLevel error
cd ..

# 3. Start server
echo "  🚀 Starting server on http://localhost:8080"
echo "  Press Ctrl+C to stop"
echo ""
./venv/bin/python3 server.py
