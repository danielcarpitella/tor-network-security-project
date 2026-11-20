#!/usr/bin/env python3
"""Run one clearnet correlation experiment on the private Tor lab.

Steps:
  1. find the lab containers (clients, routers, exits, web target) and check they are running;
  2. save a topology snapshot: container IPs, the consensus, each client's guards and circuits;
  3. start tcpdump inside every router on the Tor port (9001) and inside every exit on its
     clearnet interface (HTTP to the web target);
  4. send N HTTP requests per client through its SOCKS port, each with a random token in the URL;
  5. stop the captures and copy pcaps, request log and web server log into results/<run_id>/.

The request log and the web server log are ground truth: correlate.py uses them only to score
the matching, never as input to it.

Usage (repo root, venv active, lab running with the clearnet target prepared):
  python experiments/clearnet-correlation/run_experiment.py --run-id run_01
"""

import argparse
import csv
import json
import random
import re
import secrets
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
PREFIX = "testing-tor-"
WEB_CONTAINER = "clearnet-target-web"
TOR_NET, WEB_NET = "testing-tor", "clearnet-target"
ROUTER_ROLES = ("da", "relay", "exit")

# Runs inside a Tor container (stem ships with nyx in the image) and prints the client's view as JSON
STEM_SNAPSHOT = r"""
import json
from stem.control import Controller
with Controller.from_port(port=9051) as c:
    c.authenticate()
    guards = []
    for line in c.get_info("entry-guards", "").splitlines():
        parts = line.split()
        if parts:
            guards.append({"guard": parts[0].split("~")[-1], "status": " ".join(parts[1:])})
    circuits = [{"id": x.id, "purpose": x.purpose, "status": x.status, "path": [n for _, n in x.path]}
                for x in c.get_circuits()]
    print(json.dumps({"tor_version": str(c.get_version()), "conflux_enabled": c.get_conf("ConfluxEnabled", "auto"),
                      "entry_guards": guards, "circuits": circuits}))
"""


def log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def sh(*args: str, check: bool = True) -> str:
    proc = subprocess.run(args, capture_output=True, text=True)
    if check and proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        raise RuntimeError(f"command failed ({proc.returncode}): {' '.join(args)}\n{detail}")
    return proc.stdout


# --- lab discovery ------------------------------------------------------------------------------

def discover() -> dict:
    """Running lab containers with their role and IP on each Docker network."""
    lab = {}
    for name in sorted(sh("docker", "ps", "--format", "{{.Names}}").split()):
        if name == WEB_CONTAINER:
            role = "web"
        elif name.startswith(PREFIX):
            role = name[len(PREFIX):].split("-")[0]
        else:
            continue
        nets = json.loads(sh("docker", "inspect", "-f", "{{json .NetworkSettings.Networks}}", name))
        lab[name] = {"role": role, "ips": {net: v["IPAddress"] for net, v in nets.items()}}
    return lab


def socks_port(container: str) -> int | None:
    out = sh("docker", "port", container, "9050/tcp", check=False).strip()
    if not out:
        return None
    host, _, port = out.splitlines()[0].rpartition(":")
    if host not in ("127.0.0.1", "localhost"):
        log(f"WARNING: {container} SOCKS port is published on {host}, not only on localhost")
    return int(port)


def iface_for(container: str, ip: str) -> str:
    """Network interface inside the container that holds `ip` (e.g. eth0)."""
    for line in sh("docker", "exec", container, "ip", "-o", "-4", "addr", "show").splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[3].split("/")[0] == ip:
            return parts[1].split("@")[0]
    raise RuntimeError(f"no interface with address {ip} in {container}")


def stem_snapshot(container: str) -> dict:
    try:
        return json.loads(sh("docker", "exec", container, "python3", "-c", STEM_SNAPSHOT))
    except (RuntimeError, json.JSONDecodeError) as exc:
        return {"error": str(exc)}


def fetch_consensus(lab: dict) -> str:
    da = next((n for n, c in lab.items() if c["role"] == "da"), None)
    if da is None:
        return ""
    return sh("docker", "exec", da, "curl", "-s", "-m", "5", "http://127.0.0.1/tor/status-vote/current/consensus",
              check=False)


# --- captures -----------------------------------------------------------------------------------

class Capture:
    def __init__(self, container: str, kind: str, iface: str, bpf: str, snaplen: int, run_id: str):
        self.container, self.kind, self.iface, self.bpf, self.snaplen = container, kind, iface, bpf, snaplen
        self.remote = f"/tmp/cc_{run_id}_{kind}.pcap"
        self.local_name = f"{container}_{kind}.pcap"

    def start(self) -> None:
        sh("docker", "exec", "-d", self.container, "tcpdump", "-i", self.iface, "-nn", "-s", str(self.snaplen),
           "-U", "-w", self.remote, *self.bpf.split())

    def running(self) -> bool:
        return subprocess.run(["docker", "exec", self.container, "pgrep", "-f", self.remote],
                              capture_output=True).returncode == 0

    def stop(self, timeout: float = 10.0) -> None:
        sh("docker", "exec", self.container, "pkill", "-INT", "-f", self.remote, check=False)
        deadline = time.time() + timeout
        while self.running() and time.time() < deadline:
            time.sleep(0.2)

    def collect(self, dest_dir: Path) -> Path:
        dest = dest_dir / self.local_name
        sh("docker", "cp", f"{self.container}:{self.remote}", str(dest))
        sh("docker", "exec", self.container, "rm", "-f", self.remote, check=False)
        return dest

    def info(self) -> dict:
        return {"container": self.container, "kind": self.kind, "iface": self.iface, "filter": self.bpf,
                "snaplen": self.snaplen, "file": f"captures/{self.local_name}"}


# --- traffic generator --------------------------------------------------------------------------

def client_worker(client: dict, web_ip: str, args, rng: random.Random, rows: list, lock: threading.Lock,
                  stop: threading.Event) -> None:
    proxies = {"http": f"socks5h://127.0.0.1:{client['socks_port']}"}
    stop.wait(rng.uniform(0, args.max_sleep))  # desynchronise the clients
    for i in range(1, args.requests + 1):
        if stop.is_set():
            return
        token = secrets.token_hex(8)
        url = f"http://{web_ip}/?t={token}"
        row = {"client": client["name"], "client_ip": client["ip"], "socks_port": client["socks_port"],
               "request_id": i, "token": token, "url": url}
        start, perf = time.time(), time.perf_counter()
        try:
            resp = requests.get(url, proxies=proxies, timeout=args.timeout, headers={"Connection": "close"})
            row.update(status_code=resp.status_code, success=resp.ok, response_size_bytes=len(resp.content),
                       error_message="")
        except requests.RequestException as exc:
            row.update(status_code="", success=False, response_size_bytes=0, error_message=str(exc)[:300])
        row.update(timestamp_start=start, timestamp_end=time.time(), latency_sec=time.perf_counter() - perf)
        with lock:
            rows.append(row)
        if not row["success"]:
            log(f"{client['name']} request {i} FAILED: {row['error_message'][:120]}")
        elif i % 10 == 0 or i == args.requests:
            log(f"{client['name']}: {i}/{args.requests} requests done")
        if i < args.requests:
            stop.wait(rng.uniform(args.min_sleep, args.max_sleep))


LOG_FIELDS = ["client", "client_ip", "socks_port", "request_id", "token", "url", "timestamp_start", "timestamp_end",
              "latency_sec", "status_code", "success", "response_size_bytes", "error_message"]


# --- main ---------------------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--run-id", required=True, help="name of the run folder, e.g. run_01")
    p.add_argument("--requests", type=int, default=100, help="requests per client (default 100)")
    p.add_argument("--min-sleep", type=float, default=2.0, help="min pause between requests of a client (s)")
    p.add_argument("--max-sleep", type=float, default=5.0, help="max pause between requests of a client (s)")
    p.add_argument("--timeout", type=float, default=30.0, help="per-request timeout (s)")
    p.add_argument("--clients", help="comma-separated client containers (default: every client with a SOCKS port)")
    p.add_argument("--pre-roll", type=float, default=3.0, help="capture time before the first request (s)")
    p.add_argument("--post-roll", type=float, default=3.0, help="capture time after the last request (s)")
    p.add_argument("--seed", type=int, help="seed for the request schedule (tokens stay random)")
    args = p.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_-]+", args.run_id):
        p.error("--run-id may contain only letters, digits, '_' and '-'")
    if args.requests < 1 or args.min_sleep < 0 or args.max_sleep < args.min_sleep:
        p.error("need --requests >= 1 and 0 <= --min-sleep <= --max-sleep")
    return args


def main() -> int:
    args = parse_args()
    out = RESULTS / args.run_id
    if out.exists():
        sys.exit(f"{out} already exists: choose a new --run-id")

    lab = discover()
    web = lab.get(WEB_CONTAINER)
    if not web or WEB_NET not in web["ips"]:
        sys.exit("clearnet target not running: run ./shared-base/clearnet/scripts/prepare.sh first")
    web_ip = web["ips"][WEB_NET]
    routers = {n: c for n, c in lab.items() if c["role"] in ROUTER_ROLES}
    exits = [n for n, c in routers.items() if c["role"] == "exit"]
    if not exits or any(WEB_NET not in routers[e]["ips"] for e in exits):
        sys.exit("every exit must be attached to the clearnet-target network (run prepare.sh again)")

    wanted = set(args.clients.split(",")) if args.clients else None
    clients = []
    for name, c in lab.items():
        if c["role"] != "client" or (wanted and name not in wanted):
            continue
        port = socks_port(name)
        if port is None:
            log(f"skipping {name}: no SOCKS port published on the host")
            continue
        clients.append({"name": name, "ip": c["ips"][TOR_NET], "socks_port": port})
    if not clients:
        sys.exit("no usable client (create one with: python cli/main.py container add-client --port 9050)")

    consensus = fetch_consensus(lab)
    listed = {role: len(re.findall(rf"^r {role}", consensus, re.M)) for role in ROUTER_ROLES}
    present = {role: sum(c["role"] == role for c in routers.values()) for role in ROUTER_ROLES}
    log("Consensus lists " + ", ".join(f"{listed[r]}/{present[r]} {r}" for r in ROUTER_ROLES))
    if listed != present:
        log("WARNING: not every router is in the consensus yet (the last DA joins ~20 min after 'lab.sh up')")

    out.mkdir(parents=True)
    (out / "captures").mkdir()
    (out / "consensus.txt").write_text(consensus)
    snapshot_before = {c["name"]: stem_snapshot(c["name"]) for c in clients}
    (out / "topology_before.json").write_text(json.dumps(snapshot_before, indent=2))
    for name, snap in snapshot_before.items():
        guards = [g["guard"] for g in snap.get("entry_guards", []) if g["status"].startswith("up")]
        log(f"{name}: guards in use {guards or '?'}, conflux {snap.get('conflux_enabled', '?')}")

    captures = []
    for name, c in routers.items():
        captures.append(Capture(name, "or", iface_for(name, c["ips"][TOR_NET]), "tcp port 9001", 128, args.run_id))
    for name in exits:
        captures.append(Capture(name, "web", iface_for(name, routers[name]["ips"][WEB_NET]),
                                f"host {web_ip} and tcp port 80", 0, args.run_id))

    rows, lock, stop = [], threading.Lock(), threading.Event()
    times = {}
    started = []
    interrupted = False
    try:
        log(f"Starting {len(captures)} captures ({len(routers)} routers on port 9001, {len(exits)} exits toward the web target)")
        for cap in captures:
            cap.start()
            started.append(cap)
        time.sleep(1.5)
        dead = [cap.container for cap in captures if not cap.running()]
        if dead:
            raise RuntimeError(f"tcpdump did not start in: {', '.join(dead)}")
        times["captures_started"] = time.time()
        time.sleep(args.pre_roll)

        log(f"Sending {args.requests} requests per client from {len(clients)} client(s) to http://{web_ip}/")
        rng = random.Random(args.seed)
        threads = [threading.Thread(target=client_worker, daemon=True,
                                    args=(c, web_ip, args, random.Random(rng.random()), rows, lock, stop))
                   for c in clients]
        times["generator_started"] = time.time()
        for t in threads:
            t.start()
        while any(t.is_alive() for t in threads):
            time.sleep(0.5)
        times["generator_finished"] = time.time()
        time.sleep(args.post_roll)
    except KeyboardInterrupt:
        interrupted = True
        log("Interrupted: stopping the clients and saving what was captured so far")
        stop.set()
    finally:
        log("Stopping captures and copying the files")
        for cap in started:
            cap.stop()
        times["captures_stopped"] = time.time()
        for cap in started:
            try:
                cap.collect(out / "captures")
            except RuntimeError as exc:
                log(f"WARNING: could not copy capture from {cap.container}: {exc}")

    rows.sort(key=lambda r: r["timestamp_start"])
    with open(out / "client_requests_log.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=LOG_FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    since = str(int(times.get("captures_started", time.time()) - 2))
    server_log = subprocess.run(["docker", "logs", "--since", since, WEB_CONTAINER], capture_output=True, text=True)
    (out / "server_access.log").write_text(server_log.stdout + server_log.stderr)
    (out / "topology_after.json").write_text(json.dumps({c["name"]: stem_snapshot(c["name"]) for c in clients}, indent=2))

    commit = subprocess.run(["git", "-C", str(HERE), "rev-parse", "--short", "HEAD"], capture_output=True, text=True)
    meta = {
        "run_id": args.run_id,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "git_commit": commit.stdout.strip(),
        "interrupted": interrupted,
        "params": {k: getattr(args, k) for k in ("requests", "min_sleep", "max_sleep", "timeout", "pre_roll",
                                                  "post_roll", "seed")},
        "web": {"name": WEB_CONTAINER, "ip": web_ip, "port": 80},
        "clients": clients,
        "routers": {n: {"role": c["role"], "ip_tor": c["ips"][TOR_NET], "ip_web": c["ips"].get(WEB_NET)}
                    for n, c in routers.items()},
        "consensus_listed": listed,
        "captures": [cap.info() for cap in started],
        "times": times,
    }
    (out / "run_metadata.json").write_text(json.dumps(meta, indent=2))

    ok = sum(bool(r["success"]) for r in rows)
    tokens_at_server = sum(r["token"] in (server_log.stdout + server_log.stderr) for r in rows)
    log(f"Done: {ok}/{len(rows)} requests succeeded, {tokens_at_server} confirmed in the web server log")
    log(f"Results in {out.relative_to(HERE.parent.parent)}")
    log(f"Next: python experiments/clearnet-correlation/correlate.py {args.run_id}")
    return 1 if interrupted else 0


if __name__ == "__main__":
    sys.exit(main())
