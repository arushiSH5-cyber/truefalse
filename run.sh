#!/bin/bash
# TrueFalse on one laptop: no Docker.
#   ./run.sh           http://localhost:3200 (this computer only)
#   ./run.sh --phone   also reachable from phones on the same Wi-Fi
set -euo pipefail
cd "$(dirname "$0")"

WEB_PORT=3200
API_PORT=8000
HOST=127.0.0.1
[ "${1:-}" = "--phone" ] && HOST=0.0.0.0

for tool in node npm uv; do
  command -v "$tool" >/dev/null || { echo "Missing $tool. Run: brew install node uv ffmpeg"; exit 1; }
done
command -v ffmpeg >/dev/null || echo "No ffmpeg: voice notes, videos and HEIC photos won't work."
for port in "$API_PORT" "$WEB_PORT"; do
  if lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "Port $port is already in use. Close what's using it, then run this again."
    exit 1
  fi
done
[ -f .env ] || { echo "No .env yet. Run: cp .env.example .env"; exit 1; }

echo "Getting ready..."
(cd backend && uv sync --quiet --python 3.12 --frozen --extra media)
(
  cd web
  [ -d node_modules ] || npm ci
  NEXT_TELEMETRY_DISABLED=1 API_URL="http://127.0.0.1:$API_PORT" npx next build >/dev/null
)

backend/.venv/bin/python -c 'import sys; sys.exit(sys.version_info < (3, 12))'
(cd backend && exec .venv/bin/python -m app.local --port "$API_PORT") &
API=$!
trap 'kill "$API" 2>/dev/null || true' EXIT
trap 'exit 0' INT TERM
# The first start can take a while (Python compiles the packages), so wait for it.
echo "Starting the checker..."
for _ in $(seq 1 120); do
  kill -0 "$API" 2>/dev/null || { echo "The checker didn't start. See the error above."; exit 1; }
  curl -s -m 2 -o /dev/null "http://127.0.0.1:$API_PORT/health" && break
  sleep 1
done
(cd web && API_URL="http://127.0.0.1:$API_PORT" exec node_modules/.bin/next start -H "$HOST" -p "$WEB_PORT") &
WEB=$!
trap 'kill "$API" "$WEB" 2>/dev/null || true' EXIT

echo "TrueFalse: http://localhost:$WEB_PORT"
if [ "$HOST" = 0.0.0.0 ]; then
  echo "On your phone: http://$(ipconfig getifaddr en0 2>/dev/null || echo '<this computer IP>'):$WEB_PORT"
  echo "Anyone on this Wi-Fi can use it, and your AI quota, while it runs."
fi
while kill -0 "$API" 2>/dev/null && kill -0 "$WEB" 2>/dev/null; do sleep 1; done
