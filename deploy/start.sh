#!/bin/bash
# Starts the checker, waits for it, then the web app. Stops both if either one stops.
set -uo pipefail
(cd backend && exec .venv/bin/python -m app.local --port 8000) &
API=$!
for _ in $(seq 1 120); do
  curl -s -m 2 -o /dev/null http://127.0.0.1:8000/health && break
  kill -0 "$API" 2>/dev/null || { echo "The checker didn't start."; exit 1; }
  sleep 1
done
(cd web && exec node node_modules/next/dist/bin/next start -H 0.0.0.0 -p "${PORT:-7860}") &
WEB=$!
while kill -0 "$API" 2>/dev/null && kill -0 "$WEB" 2>/dev/null; do sleep 2; done
kill "$API" "$WEB" 2>/dev/null
exit 1
