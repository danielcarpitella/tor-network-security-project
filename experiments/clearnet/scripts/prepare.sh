#!/usr/bin/env bash
set -euo pipefail

TARGET_NETWORK="${TARGET_NETWORK:-clearnet-target}"
TARGET_SUBNET="${TARGET_SUBNET:-172.30.0.0/24}"
TARGET_IP="${TARGET_IP:-172.30.0.10}"
TARGET_PORT="${TARGET_PORT:-80}"
TARGET_CONTAINER="${TARGET_CONTAINER:-clearnet-target-web}"

if ! docker network inspect testing-tor >/dev/null 2>&1; then
    echo "testing-tor does not exist. Start the base Tor network first." >&2
    exit 1
fi

if ! docker network inspect "$TARGET_NETWORK" >/dev/null 2>&1; then
    docker network create --subnet "$TARGET_SUBNET" "$TARGET_NETWORK" >/dev/null
fi

docker rm -f "$TARGET_CONTAINER" >/dev/null 2>&1 || true
docker run -d \
    --name "$TARGET_CONTAINER" \
    --network "$TARGET_NETWORK" \
    --ip "$TARGET_IP" \
    python:3.11-alpine \
    sh -c "mkdir -p /srv && printf '<h1>Private clearnet target</h1>\n' > /srv/index.html && python3 -m http.server ${TARGET_PORT} --directory /srv" \
    >/dev/null

exit_count=0
while IFS= read -r exit_name; do
    [ -z "$exit_name" ] && continue
    docker network connect "$TARGET_NETWORK" "$exit_name" >/dev/null 2>&1 || true
    exit_count=$((exit_count + 1))
done < <(docker ps --filter "name=testing-tor-exit-" --filter status=running --format '{{.Names}}')

if [ "$exit_count" -eq 0 ]; then
    echo "No running testing-tor exit relay was found." >&2
    docker rm -f "$TARGET_CONTAINER" >/dev/null
    exit 1
fi

echo "Target: ${TARGET_CONTAINER} at ${TARGET_IP}:${TARGET_PORT}"
echo "Network: ${TARGET_NETWORK} (${TARGET_SUBNET})"
echo "Connected exit relays: ${exit_count}"
