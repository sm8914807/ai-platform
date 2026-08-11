#!/usr/bin/env bash
# Start control plane API (:8080) + Platform Studio console (:5173)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

mkdir -p .platform

# Backend
if ! curl -sf http://127.0.0.1:8080/health >/dev/null 2>&1; then
  echo "Starting API on :8080…"
  PYTHONPATH="$ROOT" PLATFORM_DB_PATH="$ROOT/.platform/registry.db" \
    "$ROOT/.venv/bin/python" -m uvicorn ai_platform.api.app:create_app \
    --factory --host 0.0.0.0 --port 8080 &
  API_PID=$!
  for i in $(seq 1 30); do
    curl -sf http://127.0.0.1:8080/health >/dev/null 2>&1 && break
    sleep 0.3
  done
  echo "API pid=$API_PID"
else
  echo "API already running on :8080"
fi

# Frontend — Vite 5 needs Node >= 18 (you have nvm Node 20)
if [ -s "$HOME/.nvm/nvm.sh" ]; then
  # shellcheck disable=SC1091
  source "$HOME/.nvm/nvm.sh"
  nvm use 20 >/dev/null
fi
NODE_MAJOR="$(node -p "process.versions.node.split('.')[0]")"
if [ "$NODE_MAJOR" -lt 18 ]; then
  echo "Node $NODE_MAJOR detected. Vite 5 needs >=18. Run: nvm use 20"
  exit 1
fi

cd "$ROOT/console"
echo "Starting console on :5173 (Node $(node -v))…"
npm run dev
