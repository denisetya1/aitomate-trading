#!/usr/bin/env bash
set -Eeuo pipefail

umask 077
export DISPLAY=:96
export XAUTHORITY="${XDG_RUNTIME_DIR}/mt5-auto/Xauthority"
install -d -m 700 "${XDG_RUNTIME_DIR}/mt5-auto"
touch "${XAUTHORITY}"
cookie="$(python3 -c 'import secrets; print(secrets.token_hex(16))')"
xauth -f "${XAUTHORITY}" add "${DISPLAY}" MIT-MAGIC-COOKIE-1 "${cookie}"

children=()
cleanup() {
  if ((${#children[@]})); then
    kill "${children[@]}" 2>/dev/null || true
    wait "${children[@]}" 2>/dev/null || true
  fi
}
trap cleanup EXIT

Xvfb "${DISPLAY}" -screen 0 1440x900x24 -nolisten tcp -auth "${XAUTHORITY}" -noreset &
children+=("$!")
for attempt in {1..50}; do
  kill -0 "${children[0]}"
  [[ -S /tmp/.X11-unix/X96 ]] && break
  sleep 0.1
done
[[ -S /tmp/.X11-unix/X96 ]]

openbox &
children+=("$!")
x11vnc -display "${DISPLAY}" -auth "${XAUTHORITY}" -localhost -rfbport 5906 -forever -shared -nopw -noxdamage &
children+=("$!")
websockify --web=/usr/share/novnc 127.0.0.1:6086 127.0.0.1:5906 &
children+=("$!")

wait -n "${children[@]}"
exit 1
