#!/bin/bash
# TrueFalse on one laptop: no Docker.
#   ./run.sh           http://localhost:3200 (this computer only)
#   ./run.sh --phone   also reachable from phones on the same Wi-Fi
#   ./run.sh --share   a public https link anyone can open, while this runs (needs cloudflared)
set -euo pipefail

# Everything is inside main(), so bash reads the whole file before running any of it. Then
# updating this file (git pull, switching branches) can't break a copy that is already running.
env_value() { { grep -E "^$1=" .env || true; } | tail -1 | cut -d= -f2- | sed 's/ #.*//' | tr -d '[:space:]'; }

# Webhook addresses for the chat apps that have a token in .env, to paste into Meta's dashboard.
chat_apps() {
  local link="$1" shown=""
  for app in whatsapp instagram messenger; do
    case $app in
      whatsapp) token=$(env_value WHATSAPP_TOKEN); name=WhatsApp ;;
      instagram) token=$(env_value INSTAGRAM_TOKEN); name=Instagram ;;
      messenger) token=$(env_value MESSENGER_PAGE_TOKEN); name=Messenger ;;
    esac
    [ -n "$token" ] || continue
    upper=$(echo "$app" | tr a-z A-Z)
    [ -z "$shown" ] && { echo "  Chat apps: paste these into Meta's webhook settings (they change each run):"; shown=1; }
    echo "    $name  callback URL: $link/webhooks/$app"
    echo "    $name  verify token: $(env_value "${upper}_VERIFY_TOKEN")"
  done
  [ -n "$shown" ] && echo
  return 0
}

main() {
cd "$(dirname "$0")"

WEB_PORT=3200
API_PORT=8000
HOST=127.0.0.1
MODE="${1:-}"
[ "$MODE" = "--phone" ] && HOST=0.0.0.0
if [ "$MODE" = "--share" ] && ! command -v cloudflared >/dev/null; then
  echo "--share needs cloudflared. Run: brew install cloudflared"
  exit 1
fi

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
# Webhook verify tokens are just random strings you paste into Meta's dashboard: make any missing.
python3 - <<'PY'
import os, re, secrets, tempfile
names = ("WHATSAPP_VERIFY_TOKEN", "INSTAGRAM_VERIFY_TOKEN", "MESSENGER_VERIFY_TOKEN")
lines = open(".env").read().splitlines()
changed = False
for name in names:
    at = [i for i, l in enumerate(lines) if l.startswith(name + "=")]
    if at and lines[at[-1]].split("=", 1)[1].strip():
        continue
    value = f"{name}={secrets.token_urlsafe(18)}"
    if at:
        lines[at[-1]] = value
    else:
        lines.append(value)
    changed = True
if changed:
    fd, tmp = tempfile.mkstemp(dir=".", prefix=".env.")
    with os.fdopen(fd, "w") as f:
        f.write("\n".join(lines) + "\n")
    os.replace(tmp, ".env")
PY

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
TUNNEL=""
trap 'kill "$API" "$WEB" $TUNNEL 2>/dev/null || true' EXIT

echo "TrueFalse: http://localhost:$WEB_PORT"
if [ "$MODE" = "--share" ]; then
  LOG=$(mktemp -t truefalse-tunnel)
  cloudflared tunnel --no-autoupdate --url "http://localhost:$WEB_PORT" >"$LOG" 2>&1 &
  TUNNEL=$!
  LINK=""
  for _ in $(seq 1 30); do
    # "|| true": no link yet is normal while the tunnel starts, not an error
    LINK=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$LOG" | head -1 || true)
    [ -n "$LINK" ] && break
    sleep 1
  done
  if [ -z "$LINK" ]; then
    echo "Couldn't get a public link. Details: $LOG"
  else
    LIMIT=$(grep -E '^DAILY_CHECK_LIMIT=[0-9]+' .env | cut -d= -f2 || true)
    echo
    echo "  Public link (works on any phone while this window stays open):"
    echo "  $LINK"
    echo
    echo "  Anyone with the link can check things using your Gemini key,"
    echo "  up to ${LIMIT:-100} checks a day. A new link is made each time you start this."
    chat_apps "$LINK"
  fi
fi
if [ "$HOST" = 0.0.0.0 ]; then
  echo "On your phone: http://$(ipconfig getifaddr en0 2>/dev/null || echo '<this computer IP>'):$WEB_PORT"
  echo "Anyone on this Wi-Fi can use it, and your AI quota, while it runs."
fi
while kill -0 "$API" 2>/dev/null && kill -0 "$WEB" 2>/dev/null; do
  if [ -n "$TUNNEL" ] && ! kill -0 "$TUNNEL" 2>/dev/null; then
    echo "The public link stopped working (the tunnel closed). Run ./run.sh --share again."
    exit 1
  fi
  sleep 1
done
echo "TrueFalse stopped."
}

main "$@"
exit
