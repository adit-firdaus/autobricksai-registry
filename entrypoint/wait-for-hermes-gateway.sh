#!/bin/sh
# Wait until the Hermes gateway api_server exposes /v1/models (zero-fork ready).
# Prevents the workspace onboarding modal from racing a still-booting gateway.
set -eu

HERMES_HOME="${HERMES_HOME:-/opt/data}"
API_KEY=""
if [ -f "$HERMES_HOME/.env" ]; then
    API_KEY=$(grep -E '^API_SERVER_KEY=' "$HERMES_HOME/.env" | head -1 | cut -d= -f2- | tr -d "'\"")
fi

deadline=$(( $(date +%s) + 180 ))
last_code="000"
while [ "$(date +%s)" -lt "$deadline" ]; do
    last_code=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 2 \
        -H "Authorization: Bearer ${API_KEY}" \
        http://127.0.0.1:8642/v1/models 2>/dev/null || echo 000)
    if [ "$last_code" = "200" ]; then
        echo "[wait-for-hermes-gateway] gateway ready (http=$last_code)" >&2
        exit 0
    fi
    sleep 2
done

echo "[wait-for-hermes-gateway] timed out after 180s (last http=$last_code)" >&2
exit 1
