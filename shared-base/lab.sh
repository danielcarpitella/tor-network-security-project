#!/usr/bin/env bash
# Build the whole private Tor lab in one go (same steps as docs/GETTING_STARTED.md).
#
# Usage (from anywhere, with the project venv created in .venv/):
#   ./shared-base/lab.sh up               create DAs, relays, exits, onion service, client, clearnet target, and test both scenarios
#   ./shared-base/lab.sh status           show containers, IPs, consensus, onion address
#   ./shared-base/lab.sh onion-web        create the test files and (re)start the web server inside the onion service
#   ./shared-base/lab.sh separate-guards  make sure the target client and onion service use different Guards (fixes it if not)
#   ./shared-base/lab.sh add-clients N    add N background clients (SOCKS ports 9061, 9062, ...) for the multi-client tests
#   ./shared-base/lab.sh remove-clients   delete the background clients (the target client, SOCKS port 9050, stays)
#   ./shared-base/lab.sh down             delete every lab container, the lab networks and the volume (keeps the Docker image)
#
# Topology is configurable with environment variables, e.g.:
#   RELAY_COUNT=8 EXIT_COUNT=2 ./shared-base/lab.sh up
set -euo pipefail

DA_COUNT="${DA_COUNT:-3}"
RELAY_COUNT="${RELAY_COUNT:-5}"
EXIT_COUNT="${EXIT_COUNT:-3}"
# The client created by 'up' is the TARGET client (the one the experiments watch); its SOCKS port is published
# on the host on SOCKS_PORT. Background clients (add-clients) get the free ports from BACKGROUND_PORT upwards.
SOCKS_PORT="${SOCKS_PORT:-9050}"
BACKGROUND_PORT="${BACKGROUND_PORT:-9061}"
TOR_SUBNET="${TOR_SUBNET:-10.5.0.0/16}"
ONION_PAGE='<h1>Hello from my private Tor network</h1>'
# Extra files served by the onion service for the response-size experiments, as name:bytes.
# Each one is served at http://<onion>/<name>.bin and filled with random bytes.
ONION_FILES="${ONION_FILES:-10k:10000 100k:100000 1m:1000000}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

log() { printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*"; }
die() { printf '[%s] ERROR: %s\n' "$(date +%H:%M:%S)" "$*" >&2; exit 1; }

# Names of running containers with a given role (da, relay, exit, hs, client)
names() { docker ps --filter "name=testing-tor-$1-" --format '{{.Names}}'; }

# Run a check every 5 s until it succeeds or the timeout (seconds) expires
wait_for() {
    local desc=$1 timeout=$2 start=$SECONDS
    shift 2
    printf '[%s] Waiting: %s ' "$(date +%H:%M:%S)" "$desc"
    until "$@" >/dev/null 2>&1; do
        if ((SECONDS - start >= timeout)); then
            echo
            die "timed out after ${timeout}s waiting for: $desc"
        fi
        printf '.'
        sleep 5
    done
    echo " ok ($((SECONDS - start))s)"
}

find_python() {
    if [ -n "${VIRTUAL_ENV:-}" ]; then
        PY=python
    elif [ -x "$ROOT/.venv/bin/python" ]; then
        PY="$ROOT/.venv/bin/python"
    else
        die "no Python venv found: create it with 'python3.11 -m venv .venv' and install requirements.txt"
    fi
    "$PY" -c 'import sys; sys.exit(sys.version_info < (3, 11))' || die "the CLI needs Python >= 3.11 (it uses enum.StrEnum)"
    "$PY" -c 'import docker, typer, rich' 2>/dev/null || die "missing CLI packages: run 'pip install -r requirements.txt' in the venv"
}

cli() { "$PY" "$ROOT/cli/main.py" "$@"; }

# Like 'grep -q' but reads the whole input: with pipefail, 'grep -q' exiting early
# kills the writer with SIGPIPE (exit 141) and the pipeline counts as failed.
contains() { grep -F -- "$1" >/dev/null; }

consensus() {
    local da
    da=$(names da | head -1)
    [ -n "$da" ] && docker exec "$da" curl -s -m 5 http://127.0.0.1/tor/status-vote/current/consensus
}

# --- readiness checks -------------------------------------------------------

all_das_published() {
    local c
    [ "$(names da | wc -l)" -eq "$DA_COUNT" ] || return 1
    for c in $(names da); do
        docker logs "$c" 2>&1 | contains 'Published ns consensus' || return 1
    done
}

# Number of lines of $2 matching the regex $1 (0 instead of a failure when nothing matches)
count() { grep -c -- "$1" <<<"$2" || true; }

# Relays and exits are what the experiments need, so setup waits for all of them. DAs also act as
# relays, but the other DAs sometimes take ~20 min to vote them Running; that only produces a warning.
relays_and_exits_listed() {
    local cons
    cons=$(consensus) || return 1
    [ "$(count '^r relay' "$cons")" -eq "$RELAY_COUNT" ] && [ "$(count '^r exit' "$cons")" -eq "$EXIT_COUNT" ]
}

# Flags of router $2 in consensus $1 (empty if not listed). Nickname = role + container suffix.
router_flags() {
    awk -v n="$2" '$1 == "r" { cur = $2 } $1 == "s" && cur == n { $1 = ""; print substr($0, 2); exit }' <<<"$1"
}

bootstrapped() { docker logs "$1" 2>&1 | contains 'Bootstrapped 100%'; }

# Same, counting only the log lines written after the Unix time $2 (e.g. after a restart)
bootstrapped_since() { docker logs --since "$2" "$1" 2>&1 | contains 'Bootstrapped 100%'; }

# Nickname(s) of the first relay of the built circuits with purpose $2 in container $1, asked to its Tor
# through the ControlPort (stem is in the image for nyx). HS_CLIENT_REND / HS_SERVICE_REND are the two halves
# of the rendezvous circuit, which carries the traffic between client and onion service.
first_relay() {
    docker exec "$1" python3 -c "
from stem.control import Controller
with Controller.from_port(port=9051) as tor:
    tor.authenticate()
    print(' '.join(sorted({c.path[0][1] for c in tor.get_circuits() if c.status == 'BUILT' and c.path and c.purpose == '$2'})))
"
}

# Page of onion address $1 received through the client whose SOCKS port is on host port $2 (default: the target)
onion_reachable() {
    curl -s -m 30 --socks5-hostname "127.0.0.1:${2:-$SOCKS_PORT}" "http://$1/" | contains 'Hello from my private Tor network'
}

# Host port on which the SOCKS port (9050 inside the container) of client container $1 is published (empty if none).
# Read from the container's configuration, so it works for stopped containers too.
socks_port_of() {
    docker inspect -f '{{range $b := index .HostConfig.PortBindings "9050/tcp"}}{{$b.HostPort}} {{end}}' "$1" 2>/dev/null | awk '{print $1}'
}

# The target client: the running client whose SOCKS port is published on SOCKS_PORT (fails if there is none)
target_client() {
    local c
    for c in $(names client); do
        if [ "$(socks_port_of "$c")" = "$SOCKS_PORT" ]; then
            echo "$c"
            return 0
        fi
    done
    return 1
}

# Host port $1 is free: no listening socket on this machine and no client container (also stopped ones) reserving it
port_free() {
    local c
    [ -z "$(ss -ltnH "sport = :$1")" ] || return 1
    for c in $(docker ps -a --filter name=testing-tor-client- --format '{{.Names}}'); do
        [ "$(socks_port_of "$c")" != "$1" ] || return 1
    done
}

clearnet_reachable() {
    SOCKS_PORT="$SOCKS_PORT" ./shared-base/clearnet/scripts/test.sh 2>/dev/null | contains 'Private clearnet target'
}

# --- commands ---------------------------------------------------------------

cmd_down() {
    log "Removing clearnet target..."
    ./shared-base/clearnet/scripts/cleanup.sh
    log "Removing Tor containers..."
    docker ps -a --filter name=testing-tor- -q | xargs -r docker rm -f >/dev/null
    docker network rm testing-tor >/dev/null 2>&1 || true
    docker volume rm testing-tor >/dev/null 2>&1 || true
    rm -f cli/status.json
    log "Lab removed (the testing-tor image is kept, so the next 'up' is faster)."
}

cmd_up() {
    find_python
    command -v curl >/dev/null || die "curl is required on the host"

    if [ -n "$(docker ps -a --filter name=testing-tor- -q)" ] || docker container inspect clearnet-target-web >/dev/null 2>&1; then
        die "a lab already exists. Run './shared-base/lab.sh down' first."
    fi
    if [ -n "$(ss -ltnH "sport = :$SOCKS_PORT")" ]; then
        die "port $SOCKS_PORT is already in use on this machine; choose another with SOCKS_PORT=..."
    fi

    log "Topology: $DA_COUNT DAs, $RELAY_COUNT relays, $EXIT_COUNT exits, 1 onion service, 1 client (SOCKS 127.0.0.1:$SOCKS_PORT)"

    # No containers exist, so any leftover network/volume/status file is stale. A stale volume would
    # still list old directory authorities, so it is always recreated.
    docker network rm clearnet-target testing-tor >/dev/null 2>&1 || true
    docker volume rm testing-tor >/dev/null 2>&1 || true
    rm -f cli/status.json

    log "Building image and creating network/volume..."
    docker build -q -t testing-tor docker >/dev/null
    docker volume create testing-tor >/dev/null
    docker network create testing-tor --subnet "$TOR_SUBNET" >/dev/null

    log "Adding $DA_COUNT directory authorities..."
    cli container add-da --count "$DA_COUNT" >/dev/null
    wait_for "first consensus published by every DA" 600 all_das_published

    log "Adding $RELAY_COUNT relays and $EXIT_COUNT exits..."
    cli container add-relay --count "$RELAY_COUNT" >/dev/null
    cli container add-exit --count "$EXIT_COUNT" >/dev/null
    wait_for "consensus listing all $RELAY_COUNT relays and $EXIT_COUNT exits (new consensus every 5 min)" 900 relays_and_exits_listed
    local das_listed
    das_listed=$(count '^r da' "$(consensus)")
    if [ "$das_listed" -lt "$DA_COUNT" ]; then
        log "WARNING: only $das_listed of $DA_COUNT DAs are listed as relays in the consensus, so the missing ones cannot"
        log "         be picked as guard/middle yet (they usually join within ~20 min). The network works anyway;"
        log "         check the CONSENSUS column of './shared-base/lab.sh status' before starting an experiment run."
    fi

    log "Adding onion service and client..."
    cli container add-hs >/dev/null
    cli container add-client --port "$SOCKS_PORT" >/dev/null
    local hs client onion
    hs=$(names hs)
    client=$(names client)

    cmd_onion_web

    wait_for "client bootstrap" 300 bootstrapped "$client"
    wait_for "onion service bootstrap" 300 bootstrapped "$hs"
    onion=$(cli container get-onion-domain "$hs" | tr -d '[:space:]')
    wait_for "onion test: page received through Tor" 300 onion_reachable "$onion"

    log "Preparing clearnet target..."
    ./shared-base/clearnet/scripts/prepare.sh
    wait_for "clearnet test: page received through an exit" 300 clearnet_reachable

    echo
    log "Lab ready."
    cmd_status
}

# Web server inside the onion service; its request log is the ground truth of what the service received.
# It does not survive a container restart (e.g. 'cli/main.py network stop/start'): run 'lab.sh onion-web' again.
cmd_onion_web() {
    local hs
    hs=$(names hs | head -1)
    [ -n "$hs" ] || die "no running onion service container"

    # Files are (re)created even when the server is already running, so ONION_FILES can be changed later.
    # A file that already has the right size is kept as it is.
    local f name bytes
    docker exec "$hs" sh -c "mkdir -p /tmp/webroot && echo '$ONION_PAGE' > /tmp/webroot/index.html"
    for f in $ONION_FILES; do
        name=${f%%:*}
        bytes=${f#*:}
        [[ "$name" =~ ^[A-Za-z0-9_-]+$ && "$bytes" =~ ^[0-9]+$ ]] || die "bad entry '$f' in ONION_FILES (expected name:bytes, e.g. 1m:1000000)"
        docker exec "$hs" sh -c "[ \"\$(stat -c %s /tmp/webroot/$name.bin 2>/dev/null)\" = $bytes ] || head -c $bytes /dev/urandom > /tmp/webroot/$name.bin"
    done
    log "Onion test files ready in $hs: / $(for f in $ONION_FILES; do printf '/%s.bin ' "${f%%:*}"; done)"

    if docker exec "$hs" pgrep -f http.server >/dev/null 2>&1; then
        log "Onion web server already running in $hs"
        return
    fi
    docker exec -d "$hs" sh -c "cd /tmp/webroot && exec python3 -u -m http.server 80 >> /tmp/http_server.log 2>&1"
    log "Onion web server started in $hs (log: /tmp/http_server.log)"
}

# The experiments watch the client's Guard and the onion service's Guard, so they must be two different relays.
# With few relays in the lab they are often the same one. In that case the client is told to avoid that relay
# (ExcludeNodes in its torrc, kept across restarts) and restarted, so Tor picks another Guard.
#
# Only the target client is changed: background clients may use any Guard (the analysis filters the client
# side by the target's IP).
cmd_separate_guards() {
    local client hs onion cg sg since
    client=$(target_client) || die "no running client publishes its SOCKS port on $SOCKS_PORT (the target client)"
    hs=$(names hs | head -1)
    [ -n "$hs" ] || die "the onion service container is not running"
    onion=$(docker exec "$hs" cat /var/lib/tor/.tor/hs/hostname)

    # A request builds the rendezvous circuits, whose first relays are the two Guards
    wait_for "request to the onion service" 120 onion_reachable "$onion"
    cg=$(first_relay "$client" HS_CLIENT_REND)
    sg=$(first_relay "$hs" HS_SERVICE_REND)
    [[ "$cg" =~ ^[a-z]+$ && "$sg" =~ ^[a-z]+$ ]] || die "expected one Guard per side, found client: '$cg', onion service: '$sg'"
    if [ "$cg" != "$sg" ]; then
        log "Guards already different: client $cg, onion service $sg. Nothing to do."
        return
    fi

    log "Client and onion service both use $cg: telling the client to avoid it..."
    # Tor keeps only the last ExcludeNodes line, so an existing one is extended instead of adding a second one
    docker exec "$client" sh -c "if grep -q '^ExcludeNodes ' /etc/tor/torrc; then sed -i 's/^ExcludeNodes .*/&,$cg/' /etc/tor/torrc; else echo 'ExcludeNodes $cg' >> /etc/tor/torrc; fi"
    since=$(date +%s)
    docker restart "$client" >/dev/null
    wait_for "client bootstrap after the restart" 300 bootstrapped_since "$client" "$since"
    wait_for "request to the onion service" 120 onion_reachable "$onion"

    cg=$(first_relay "$client" HS_CLIENT_REND)
    [[ "$cg" =~ ^[a-z]+$ && "$cg" != "$sg" ]] || die "after the restart the client Guard is '$cg' (onion service: $sg)"
    log "Done: client Guard $cg, onion service Guard $sg"
    log "Client torrc now has: $(docker exec "$client" grep '^ExcludeNodes ' /etc/tor/torrc)"
}

# Multi-client tests: background clients send the same kind of requests as the target client, at the same time,
# so the observed relays also carry other people's traffic. Each one is a separate client container (its own Tor,
# Guard and circuits) with its own SOCKS port on the host: several programs on the same SOCKS port would share
# one Tor and even one circuit, i.e. they would be one client.
cmd_add_clients() {
    local n=${1:-} hs onion port before new i
    [[ "$n" =~ ^[1-9][0-9]*$ ]] || die "usage: ./shared-base/lab.sh add-clients <number of background clients to add>"
    find_python
    target_client >/dev/null || die "no running client publishes its SOCKS port on $SOCKS_PORT (the target client): is the lab up?"
    hs=$(names hs | head -1)
    [ -n "$hs" ] || die "the onion service container is not running"
    onion=$(docker exec "$hs" cat /var/lib/tor/.tor/hs/hostname)

    local -a added=() ports=()
    port=$BACKGROUND_PORT
    for i in $(seq 1 "$n"); do
        until port_free "$port"; do port=$((port + 1)); done
        before=$(names client | sort)
        cli container add-client --port "$port" >/dev/null
        new=$(comm -13 <(echo "$before") <(names client | sort))
        [ -n "$new" ] || die "the new client container did not start"
        log "Added background client $i/$n: $new (SOCKS 127.0.0.1:$port)"
        added+=("$new")
        ports+=("$port")
        port=$((port + 1))
    done

    for i in "${!added[@]}"; do
        wait_for "bootstrap of ${added[$i]}" 300 bootstrapped "${added[$i]}"
        wait_for "onion page through ${added[$i]}" 300 onion_reachable "$onion" "${ports[$i]}"
    done
    log "Done: the lab now has $(names client | wc -l) clients (target included). Use --clients $(names client | wc -l) in run_experiment.py."
}

cmd_remove_clients() {
    local target c removed=0
    find_python
    target=$(target_client) || die "no running client publishes its SOCKS port on $SOCKS_PORT (the target client): nothing removed"
    for c in $(docker ps -a --filter name=testing-tor-client- --format '{{.Names}}'); do
        [ "$c" != "$target" ] || continue
        # Through the CLI so that cli/status.json stays in sync; plain docker rm if the CLI does not know it
        cli container delete "$c" >/dev/null 2>&1 || docker rm -f "$c" >/dev/null
        log "Removed background client $c"
        removed=$((removed + 1))
    done
    log "Removed $removed background client(s); the target client $target is kept."
}

cmd_status() {
    local c role ip hs client cons flags port
    if [ -z "$(docker ps -a --filter name=testing-tor- -q)" ]; then
        echo "No lab containers found. Start the lab with: ./shared-base/lab.sh up"
        return
    fi
    cons=$(consensus 2>/dev/null || true)

    printf '\n%-7s %-28s %-15s %-8s %s\n' ROLE CONTAINER IP STATE 'CONSENSUS (flags) / SOCKS port of clients'
    for c in $(docker ps -a --filter name=testing-tor- --format '{{.Names}}' | sort); do
        role=$(cut -d- -f3 <<<"$c")
        ip=$(docker inspect -f '{{with index .NetworkSettings.Networks "testing-tor"}}{{.IPAddress}}{{end}}' "$c")
        case "$role" in
        hs) flags='-' ;; # not a relay
        client)
            port=$(socks_port_of "$c")
            if [ "$port" = "$SOCKS_PORT" ]; then
                flags="SOCKS 127.0.0.1:$port (target)"
            else
                flags="SOCKS 127.0.0.1:${port:-none} (background)"
            fi
            ;;
        *)
            if [ -z "$cons" ]; then
                flags='? (no consensus available)'
            else
                flags=$(router_flags "$cons" "$role$(cut -d- -f4 <<<"$c")")
                flags=${flags:-NOT LISTED}
            fi
            ;;
        esac
        printf '%-7s %-28s %-15s %-8s %s\n' "$role" "$c" "$ip" "$(docker inspect -f '{{.State.Status}}' "$c")" "$flags"
    done
    if docker container inspect clearnet-target-web >/dev/null 2>&1; then
        printf '%-7s %-28s %-15s %-8s %s\n' web clearnet-target-web 172.30.0.10 "$(docker inspect -f '{{.State.Status}}' clearnet-target-web)" '-'
    fi

    if [ -n "$cons" ]; then
        printf '\nConsensus %s: %s/%s DAs, %s/%s relays, %s/%s exits listed\n' "$(grep '^valid-after' <<<"$cons" | cut -d' ' -f2-)" \
            "$(count '^r da' "$cons")" "$(names da | wc -l)" \
            "$(count '^r relay' "$cons")" "$(names relay | wc -l)" \
            "$(count '^r exit' "$cons")" "$(names exit | wc -l)"
    fi

    hs=$(names hs | head -1)
    client=$(target_client || true)
    echo
    if [ -n "$client" ]; then
        printf 'SOCKS proxy:    socks5h://127.0.0.1:%s (target client %s)\n' "$SOCKS_PORT" "$client"
    else
        printf 'SOCKS proxy:    no running client publishes its SOCKS port on %s\n' "$SOCKS_PORT"
    fi
    if [ "$(names client | wc -l)" -gt 1 ]; then
        printf 'Clients:        %s running (target included): use --clients %s, or remove-clients\n' "$(names client | wc -l)" "$(names client | wc -l)"
    fi
    if [ -n "$hs" ]; then
        printf 'Onion address:  http://%s/\n' "$(docker exec "$hs" cat /var/lib/tor/.tor/hs/hostname 2>/dev/null)"
        printf 'Onion files:    %s\n' "$(docker exec "$hs" sh -c 'cd /tmp/webroot 2>/dev/null && for f in *; do printf "/%s (%s B)  " "$f" "$(stat -c %s "$f")"; done' 2>/dev/null)"
        if docker exec "$hs" pgrep -f http.server >/dev/null 2>&1; then
            printf 'Onion web log:  docker exec -it %s tail -f /tmp/http_server.log\n' "$hs"
        else
            printf 'Onion web:      NOT running, start it with: ./shared-base/lab.sh onion-web\n'
        fi
    fi
    if docker container inspect clearnet-target-web >/dev/null 2>&1; then
        printf 'Clearnet log:   docker logs -f clearnet-target-web\n'
    fi
}

case "${1:-}" in
up) cmd_up ;;
down) cmd_down ;;
status) cmd_status ;;
onion-web) cmd_onion_web ;;
separate-guards) cmd_separate_guards ;;
add-clients) cmd_add_clients "${2:-}" ;;
remove-clients) cmd_remove_clients ;;
*)
    sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'
    exit 1
    ;;
esac
