#!/usr/bin/env bash
set -euo pipefail

TARGET_NETWORK="${TARGET_NETWORK:-clearnet-target}"
TARGET_CONTAINER="${TARGET_CONTAINER:-clearnet-target-web}"

docker rm -f "$TARGET_CONTAINER" >/dev/null 2>&1 || true

if docker network inspect "$TARGET_NETWORK" >/dev/null 2>&1; then
    while IFS= read -r exit_name; do
        [ -z "$exit_name" ] && continue
        docker network disconnect -f "$TARGET_NETWORK" "$exit_name" >/dev/null 2>&1 || true
    done < <(docker ps -a --filter "name=testing-tor-exit-" --format '{{.Names}}')
    docker network rm "$TARGET_NETWORK" >/dev/null
fi

echo "Removed ${TARGET_CONTAINER} and ${TARGET_NETWORK}."
