#!/usr/bin/env bash
set -euo pipefail

SOCKS_HOST="${SOCKS_HOST:-127.0.0.1}"
SOCKS_PORT="${SOCKS_PORT:-9050}"
TARGET_IP="${TARGET_IP:-172.30.0.10}"
TARGET_PORT="${TARGET_PORT:-80}"

echo "Requesting http://${TARGET_IP}:${TARGET_PORT} through SOCKS5 ${SOCKS_HOST}:${SOCKS_PORT}"
curl --fail --silent --show-error \
    --socks5-hostname "${SOCKS_HOST}:${SOCKS_PORT}" \
    --connect-timeout 10 \
    --max-time 60 \
    "http://${TARGET_IP}:${TARGET_PORT}/"
printf '\n'
