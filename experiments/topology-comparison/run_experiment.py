#!/usr/bin/env python3
"""Record ONE run of the correlation experiment on the private Tor lab.

PURPOSE
    The attacker watches two relays: the Guard of the client and the Guard of the onion service.
    A run records what those two relays see while the client sends N requests to the onion service,
    plus the ground truth (when each request really happened), in a form ready for analyze.py.
    A "test" is a group of runs with the same settings (e.g. test "size_1m" = 5 runs with the 1 MB file);
    analyze.py compares the runs of a test with each other, so every run of a test must use the same settings.

REQUIREMENTS
    The lab running (./shared-base/lab.sh up), the project venv active, tshark installed on the host.

HOW IT WORKS, STEP BY STEP
    Before anything else: checks that docker and tshark exist, and that the run folder
    results/<scenario>/<test>/<run>/ does not exist yet (a run is never overwritten).

    Step 1 - Find the lab
      1. Containers: lists the running containers named testing-tor-client-* and testing-tor-hs-*.
         There must be exactly one of each (e.g. testing-tor-client-rxaojgqn, testing-tor-hs-kgumuaxx).
      2. Onion address: reads /var/lib/tor/.tor/hs/hostname inside the onion service container.
      3. Web server: checks that the web server started by lab.sh onion-web is running inside the onion service,
         and that the requested file exists in /tmp/webroot (index.html, the small page, if --page is not given).
      4. Warm-up: sends one request, http://<onion>/<page>?id=warmup, through the client's SOCKS port,
         so the client builds its rendezvous circuit. The requests of the run will reuse that circuit.
      5. Guards: asks each Tor process for its built circuits (a small Python program using stem, run inside
         the container with docker exec, talking to Tor's ControlPort 9051) and takes the first relay of the
         rendezvous circuit:
             client        -> circuit HS_CLIENT_REND  -> first relay e.g. dapgxxnfie -> container testing-tor-da-pgxxnfie
             onion service -> circuit HS_SERVICE_REND -> first relay e.g. daivbevvpt -> container testing-tor-da-ivbevvpt
         Lab naming rule: relay nickname = role + suffix of the container name. A Guard can be a directory
         authority (da): in the lab they also work as relays. Stops with an error if a side has no rendezvous
         circuit, or several first relays, or if both sides use the same Guard.
      6. IPs: reads the IP of the client, the onion service and both Guards on the testing-tor network.
      7. Preparation: creates the run folder and counts the lines already in the web server log
         (/tmp/http_server.log in the onion service), so that later it copies only the lines of this run.

    Step 2 - Start the captures
      Inside each Guard container, in the background, the same command:
          tcpdump -i any -nn -s 0 -U -w /tmp/capture_<scenario>_<test>_<run>_<side>.pcap 'tcp port 9001'
      It records every Tor packet the Guard sends or receives, to and from any neighbour (client, service,
      other relays, directory authorities), into a file inside the container; -U writes each packet at once.
      tcpdump's own messages (start-up lines and, at the end, its packet counters) go to tcpdump_<side>_guard.log.
      After 2 s the script checks that both tcpdumps are still running, then waits 5 s, so the capture
      starts well before the first request.

    Step 3 - Send the requests (send_requests in generator.py)
      For each request i = 1 ... N:
        1. note the time (timestamp_start);
        2. GET http://<onion>/<page>?id=<run>-<i> through the SOCKS proxy;
        3. note the time again (timestamp_end). Two times because a request lasts a while: the analysis needs
           the whole interval (a request is "caught" if a pair of bursts falls between its start and its end),
           and the capture window is computed from the first start and the last end;
        4. write one row to client_requests_log.csv: id, start, end, latency, response size, HTTP status, success;
        5. wait a random pause (--pause, 2-5 s by default), except after the last request.
      Then waits 6 s, so the capture also covers the 5 s after the last request.

    Step 4 - Stop and collect
      1. Stop: sends Ctrl+C (SIGINT, with pkill) to both tcpdumps; each one writes its packet counters and
         closes its file properly. This also happens if the run fails or is stopped with Ctrl+C.
      2. Captures: copies the two .pcap files out of the containers (docker cp) as guard_client_capture.pcap
         and guard_service_capture.pcap, then deletes them inside the containers.
      3. Server log: copies the lines the web server wrote during the run to server_access.log, and counts
         the ids of this run that it answered with status 200 ("confirmed by server").
      4. Guard re-check: asks both Tor processes again for their rendezvous circuits, to see whether
         the Guards changed during the run.

    Step 5 - Export to CSV
      1. Window: from client_requests_log.csv, start = first timestamp_start - 5 s, end = last timestamp_end + 5 s.
      2. Export: tshark turns each .pcap into a CSV with one row per packet: frame.time_epoch, frame.len,
         ip.src, ip.dst (Unix time, frame length in bytes, source IP, destination IP). This full CSV is temporary.
      3. Trim: only the rows inside the window are saved, in analysis/window_5s/client_metadata.csv and
         analysis/window_5s/service_metadata.csv. These two files are what the analysis reads.
      4. Checks, each one printed as a WARNING if it fails: packets in the window on both sides (none would mean
         different clocks), no packets dropped by tcpdump, every request successful, every successful request
         confirmed by the server, no rendezvous circuits through other first relays after the run.

    Step 6 - Record
      Writes run_info.json: settings, containers, Guards and their IPs, the window, packets in the window,
      tcpdump's counters, the ExcludeNodes/EntryNodes lines of both torrc files, how many containers of each
      role the lab has, the Tor version and the warnings. Then prints the summary: requests OK, confirmed by
      server, packets in the window for each Guard, and the warnings.

DESIGN CHOICES (and why)
    * Guards are detected, not typed in. Each Tor process is asked for its circuits through its ControlPort
      (with the stem library, already in the image for nyx), so names/IPs can't be mistyped and stay right
      after the lab is rebuilt. --client-guard / --service-guard override the detection if ever needed.
    * The Guard is the first relay of the RENDEZVOUS circuit (purpose HS_CLIENT_REND on the client,
      HS_SERVICE_REND on the service), not of any circuit: the requests travel only on that circuit, while
      other circuits can start elsewhere (directory downloads use other directory guards; conflux, Tor >= 0.4.8,
      builds exit circuits that start at different guards on purpose). Seen in practice: the client had
      circuits through two different first relays.
    * A warm-up request is sent before detecting the Guards, so the rendezvous circuit exists. The later
      requests reuse that circuit. The warm-up is not part of the run (its id is "warmup", not counted).
    * The run is refused if both sides use the same Guard: the two observation points must be different relays.
      With ~8 possible guards in the lab this happens often. Fix: ./shared-base/lab.sh separate-guards, which
      tells the client to avoid that relay (ExcludeNodes in its torrc) and restarts it. The fix is deliberately
      not automatic here: it changes the lab for all later runs, so it should be a conscious step.
      Guard-related options (ExcludeNodes, EntryNodes) of both containers are saved in run_info.json.
    * Capture filter: "tcp port 9001" on all interfaces (-i any), i.e. all Tor relay traffic of the Guard,
      to and from every neighbour. Splitting by neighbour IP is left to the analysis.
    * 5 s margin: tcpdump starts 5 s before the first request and stops 5 s after the last one, and the CSV
      keeps exactly first request - 5 s ... last request + 5 s, so both Guards cover the same time span.
    * Trimming happens on the CSV in Python (not with editcap), so no timestamps are copied by hand.
      Only the trimmed CSV is saved: the full CSV would just repeat the pcap, which is kept and from which
      it can always be regenerated (same tshark command as export_packets below).
    * Each request carries its id in the URL (?id=<run>-<n>), so the web server log confirms it independently
      of the client's own log ("confirmed by server" in the summary).
    * tcpdump is always stopped, even on errors or Ctrl+C, so it never keeps running inside a Guard.
    * A run folder is never overwritten: reusing --test/--run that already exist is an error.

OUTPUT  results/<scenario>/<test>/<run>/ next to this script (results/ is ignored by git)
    client_requests_log.csv                 ground truth: start/end time of every request (from generator.py)
    server_access.log                       web server log lines written during this run
    guard_client_capture.pcap               full capture on the client's Guard
    guard_service_capture.pcap              full capture on the onion service's Guard
    tcpdump_*_guard.log                     tcpdump's own messages and packet counters
    analysis/window_5s/<side>_metadata.csv  one row per packet inside the request window: time, frame length,
                                            source IP, destination IP (what analyze.py reads)
    run_info.json                           settings, containers, Guards, IPs, counters and warnings of the run
    File and folder names match the runs in experiments/onion-correlation/, so its notebooks can read these too.

EXAMPLES
    python experiments/topology-comparison/run_experiment.py --test baseline --run run_01
    python experiments/topology-comparison/run_experiment.py --test size_1m --run run_01 --page 1m.bin
    python experiments/topology-comparison/run_experiment.py --test smoke --run try_01 --requests 5 --pause 1 2
"""

import argparse
import csv
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from generator import send_requests

HERE = Path(__file__).resolve().parent

# Defaults of the command-line options (each one can be changed with the option in brackets)
SCENARIO = "onion"                    # --scenario: only onion for now
PAGE = ""                             # --page: file requested from the onion service; "" = the small index page
REQUESTS = 100                        # --requests: requests per run
PAUSE = (2.0, 5.0)                    # --pause: random pause between requests, min and max seconds
SOCKS = "socks5h://127.0.0.1:9050"    # --socks: SOCKS proxy of the Tor client, published on the host by lab.sh

# Fixed settings
MARGIN = 5.0                          # seconds kept before the first and after the last request
TIMEOUT = 60                          # seconds, for a single request
WEBROOT = "/tmp/webroot"              # folder served by the onion service (created by lab.sh onion-web)
SERVER_LOG = "/tmp/http_server.log"   # its access log (written by lab.sh onion-web)

# Runs inside a lab container: asks the local Tor process (ControlPort 9051) for its built circuits and prints,
# for each one, its first relay, its length and its purpose. The stem library comes with nyx in the image.
CIRCUITS = """
import json
from stem.control import Controller
with Controller.from_port(port=9051) as tor:
    tor.authenticate()
    print(json.dumps([{"first": c.path[0][1], "hops": len(c.path), "purpose": c.purpose}
                      for c in tor.get_circuits() if c.status == "BUILT" and c.path]))
"""

# Not every circuit starts at the Guard that carries the experiment: directory downloads can use other
# directory guards, and conflux (Tor >= 0.4.8) pre-builds exit circuits that start at different guards on purpose.
# The requests to the onion service travel on the rendezvous circuit, so its first relay is the Guard to watch.
REND_PURPOSE = {"client": "HS_CLIENT_REND", "service": "HS_SERVICE_REND"}


class RunError(Exception):
    """A problem that stops the run; the message is shown to the user."""


def sh(*cmd, check=True):
    """Run a command on the host and return what it printed."""
    result = subprocess.run(cmd, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise RunError(f"command failed: {' '.join(cmd)}\n{result.stderr.strip()}")
    return result.stdout.strip()


def step(text):
    print(f"\n== {text}", flush=True)


# --- the lab ---------------------------------------------------------------------------------

def containers(role):
    """Names of the running lab containers with a role: da, relay, exit, client or hs."""
    return sorted(sh("docker", "ps", "--filter", f"name=testing-tor-{role}-", "--format", "{{.Names}}").split())


def the_only(role, what):
    names = containers(role)
    if len(names) != 1:
        raise RunError(f"expected exactly 1 running {what} container (testing-tor-{role}-*), found {len(names)}: {names}")
    return names[0]


def ip_of(container):
    ip = sh("docker", "inspect", "-f", '{{with index .NetworkSettings.Networks "testing-tor"}}{{.IPAddress}}{{end}}', container)
    if not ip:
        raise RunError(f"{container} has no IP on the testing-tor network")
    return ip


def circuits(container):
    """Built circuits of the Tor process in `container`: first relay (nickname), number of hops, purpose."""
    return json.loads(sh("docker", "exec", container, "python3", "-c", CIRCUITS))


def first_hops(container, purpose):
    """How many built circuits with this purpose start at each relay (by nickname)."""
    return Counter(c["first"] for c in circuits(container) if c["purpose"] == purpose)


def describe(circs):
    return "\n".join(f"  first relay {c['first']:16} {c['hops']} hops  {c['purpose']}" for c in circs) or "  (none)"


def container_of(nickname):
    """Lab naming rule: nickname 'relayabcdefgh' belongs to container 'testing-tor-relay-abcdefgh'."""
    match = re.fullmatch(r"(da|relay|exit)([a-z]+)", nickname or "")
    if not match:
        raise RunError(f"cannot map the relay nickname {nickname!r} to a lab container")
    return f"testing-tor-{match[1]}-{match[2]}"


def nickname_of(container):
    _, _, role, suffix = container.split("-", 3)
    return role + suffix


def find_guard(container, purpose):
    """Container of the first relay of the rendezvous circuit(s) of `container`."""
    circs = circuits(container)
    hops = Counter(c["first"] for c in circs if c["purpose"] == purpose)
    if len(hops) != 1:
        problem = "no" if not hops else "several first relays for the"
        raise RunError(f"{container} has {problem} {purpose} circuit(s). Its built circuits:\n{describe(circs)}\n"
                       "Choose the Guard with --client-guard / --service-guard")
    return container_of(next(iter(hops)))


def guard_options(container):
    """Lines of the container's torrc that change how its Guard is chosen (e.g. ExcludeNodes), if any."""
    out = sh("docker", "exec", container, "grep", "-E", "^(ExcludeNodes|EntryNodes|StrictNodes)", "/etc/tor/torrc", check=False)
    return out.splitlines()


def warm_up(url, socks):
    """One request before the run, so that the rendezvous circuit exists (and later requests reuse it)."""
    import requests
    try:
        requests.get(f"{url}{'&' if '?' in url else '?'}id=warmup", proxies={"http": socks, "https": socks},
                     timeout=TIMEOUT).raise_for_status()
    except requests.RequestException as exc:
        raise RunError(f"warm-up request to {url} failed: {exc}")


# --- captures --------------------------------------------------------------------------------

class Capture:
    """tcpdump running inside one Guard container, on Tor's relay port 9001."""

    def __init__(self, guard, remote_file, log_file):
        self.guard, self.remote, self.log_file = guard, remote_file, log_file
        self.proc = self.log = None

    def start(self):
        sh("docker", "exec", self.guard, "rm", "-f", self.remote)
        self.log = open(self.log_file, "w")
        # start_new_session: a Ctrl+C in the terminal reaches only this script, which then stops tcpdump properly
        self.proc = subprocess.Popen(
            ["docker", "exec", self.guard, "tcpdump", "-i", "any", "-nn", "-s", "0", "-U", "-w", self.remote, "tcp port 9001"],
            stdout=subprocess.DEVNULL, stderr=self.log, start_new_session=True)

    def check_running(self):
        if self.proc.poll() is not None:
            raise RunError(f"tcpdump in {self.guard} stopped right away:\n{self.log_file.read_text().strip()}")

    def stop(self):
        """Send Ctrl+C (SIGINT) to tcpdump, so it closes the file properly, and wait until it exits."""
        if self.proc is None:
            return
        if self.proc.poll() is None:
            sh("docker", "exec", self.guard, "pkill", "-INT", "-f", self.remote, check=False)
            try:
                self.proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                sh("docker", "exec", self.guard, "pkill", "-KILL", "-f", self.remote, check=False)
                self.proc.wait(timeout=10)
        self.log.close()

    def counters(self):
        """tcpdump's own summary, e.g. {'captured': 1656, 'received by filter': 1656, 'dropped by kernel': 0}."""
        text = self.log_file.read_text()
        return {m[2]: int(m[1]) for m in re.finditer(r"(\d+) packets? (captured|received by filter|dropped by kernel)", text)}

    def fetch(self, local_file):
        sh("docker", "cp", f"{self.guard}:{self.remote}", str(local_file))
        self.discard()

    def discard(self):
        sh("docker", "exec", self.guard, "rm", "-f", self.remote, check=False)


# --- export ----------------------------------------------------------------------------------

def export_packets(pcap, out_csv):
    """One CSV row per packet: time (Unix epoch), frame length, source IP, destination IP."""
    with open(out_csv, "w") as f:
        result = subprocess.run(
            ["tshark", "-r", str(pcap), "-T", "fields",
             "-e", "frame.time_epoch", "-e", "frame.len", "-e", "ip.src", "-e", "ip.dst",
             "-E", "header=y", "-E", "separator=,", "-E", "occurrence=f"],
            stdout=f, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        raise RunError(f"tshark failed on {pcap}:\n{result.stderr.strip()}")


def keep_window(in_csv, out_csv, t_start, t_end):
    """Copy only the packets with t_start <= time <= t_end. Returns how many."""
    kept = 0
    with open(in_csv, newline="") as fin, open(out_csv, "w", newline="") as fout:
        rows, out = csv.reader(fin), csv.writer(fout)
        out.writerow(next(rows))                      # header
        for row in rows:
            if row and t_start <= float(row[0]) <= t_end:
                out.writerow(row)
                kept += 1
    return kept


def request_window(requests_csv):
    with open(requests_csv, newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise RunError(f"{requests_csv} has no requests")
    return (min(float(r["timestamp_start"]) for r in rows) - MARGIN,
            max(float(r["timestamp_end"]) for r in rows) + MARGIN)


def confirmed_by_server(server_log, run_id):
    """Request ids that the web server answered with 200 (its log lines contain the id from the URL)."""
    pattern = re.compile(rf'"GET /\S*[?&]id={re.escape(run_id)}-(\d+) HTTP/[\d.]+" 200 ')
    return {int(m[1]) for m in pattern.finditer(server_log.read_text())}


# --- main ------------------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(description="Do one run: capture on both Guards while sending requests through Tor.")
    p.add_argument("--scenario", choices=["onion"], default=SCENARIO, help=f"only onion for now (default: {SCENARIO})")
    p.add_argument("--test", required=True, help="name of the test, e.g. baseline or size_1m")
    p.add_argument("--run", required=True, help="name of the run, e.g. run_01")
    p.add_argument("--page", default=PAGE, help="file to request, e.g. 1m.bin (default: the small index page)")
    p.add_argument("--requests", type=int, default=REQUESTS, help=f"number of requests (default: {REQUESTS})")
    p.add_argument("--pause", type=float, nargs=2, default=list(PAUSE), metavar=("MIN", "MAX"),
                   help=f"random pause between requests, in seconds (default: {PAUSE[0]:g} {PAUSE[1]:g})")
    p.add_argument("--socks", default=SOCKS, help=f"SOCKS proxy of the Tor client (default: {SOCKS})")
    p.add_argument("--client-guard", help="container of the client's Guard (default: asked to Tor)")
    p.add_argument("--service-guard", help="container of the onion service's Guard (default: asked to Tor)")
    args = p.parse_args()

    for name in ("test", "run"):
        if not re.fullmatch(r"[A-Za-z0-9_-]+", getattr(args, name)):
            p.error(f"--{name} may contain only letters, numbers, '_' and '-'")
    if args.page and not re.fullmatch(r"[A-Za-z0-9_.-]+", args.page):
        p.error("--page must be a file name like 1m.bin")
    if args.requests < 1 or not 0 <= args.pause[0] <= args.pause[1]:
        p.error("--requests must be >= 1 and --pause needs 0 <= MIN <= MAX")
    return args


def main():
    args = parse_args()
    for tool in ("docker", "tshark"):
        if not shutil.which(tool):
            raise RunError(f"{tool} not found on this machine")
    run_dir = HERE / "results" / args.scenario / args.test / args.run
    if run_dir.exists():
        raise RunError(f"{run_dir} already exists: choose another --run, or delete that folder")
    warnings = []

    step("1/6 Finding the lab: containers, onion address, Guards")
    client = the_only("client", "Tor client")
    hs = the_only("hs", "onion service")
    onion = sh("docker", "exec", hs, "cat", "/var/lib/tor/.tor/hs/hostname")
    if not sh("docker", "exec", hs, "pgrep", "-f", "http.server", check=False):
        raise RunError("the onion service web server is not running: ./shared-base/lab.sh onion-web")
    if subprocess.run(["docker", "exec", hs, "test", "-f", f"{WEBROOT}/{args.page or 'index.html'}"]).returncode != 0:
        raise RunError(f"the onion service does not serve '{args.page}' (see ONION_FILES in shared-base/lab.sh)")
    url = f"http://{onion}/{args.page}"
    print(f"warm-up request to {url}", flush=True)
    warm_up(url, args.socks)
    client_guard = args.client_guard or find_guard(client, REND_PURPOSE["client"])
    service_guard = args.service_guard or find_guard(hs, REND_PURPOSE["service"])
    if client_guard == service_guard:
        raise RunError(f"client and onion service use the same Guard ({client_guard}): the two observation points must differ.\n"
                       "Fix it with: ./shared-base/lab.sh separate-guards")
    ips = {c: ip_of(c) for c in (client, hs, client_guard, service_guard)}
    print(f"client         {client:30} {ips[client]}")
    print(f"onion service  {hs:30} {ips[hs]}")
    print(f"client Guard   {client_guard:30} {ips[client_guard]}")
    print(f"service Guard  {service_guard:30} {ips[service_guard]}")
    print(f"URL            {url}")

    run_dir.mkdir(parents=True)
    log_lines_before = int(sh("docker", "exec", hs, "sh", "-c", f"wc -l < {SERVER_LOG}"))
    tag = f"{args.scenario}_{args.test}_{args.run}"
    captures = {
        "client": Capture(client_guard, f"/tmp/capture_{tag}_client.pcap", run_dir / "tcpdump_client_guard.log"),
        "service": Capture(service_guard, f"/tmp/capture_{tag}_service.pcap", run_dir / "tcpdump_service_guard.log"),
    }
    started = datetime.now(timezone.utc)
    finished = False
    try:
        step("2/6 Starting tcpdump on both Guards")
        for c in captures.values():
            c.start()
        time.sleep(2)
        for c in captures.values():
            c.check_running()
        print(f"capturing; waiting {MARGIN:.0f} s before the first request", flush=True)
        time.sleep(MARGIN)

        step(f"3/6 Sending {args.requests} requests")
        successes = send_requests(url, args.requests, *args.pause, args.socks, TIMEOUT,
                                  run_dir / "client_requests_log.csv", args.run)
        print(f"waiting {MARGIN:.0f} s after the last request", flush=True)
        time.sleep(MARGIN + 1)
        finished = True
    finally:
        step("4/6 Stopping tcpdump and copying the files")
        for c in captures.values():
            c.stop()
        if not finished:
            for c in captures.values():
                c.discard()
            print(f"\nRun not completed. Partial files are in {run_dir}: delete that folder before reusing --run {args.run}.")

    pcaps = {side: run_dir / f"guard_{side}_capture.pcap" for side in captures}
    for side, c in captures.items():
        c.fetch(pcaps[side])
    server_log = run_dir / "server_access.log"
    server_log.write_text(sh("docker", "exec", hs, "tail", "-n", f"+{log_lines_before + 1}", SERVER_LOG) + "\n")
    confirmed = confirmed_by_server(server_log, args.run)
    guards_after = {side: dict(first_hops(c, REND_PURPOSE[side])) for side, c in (("client", client), ("service", hs))}
    for side, guard in (("client", client_guard), ("service", service_guard)):
        if set(guards_after[side]) - {nickname_of(guard)}:
            warnings.append(f"after the run the {side} has rendezvous circuits through other first relays: {guards_after[side]}")

    step("5/6 Exporting the captures to CSV (only first request - 5 s ... last request + 5 s)")
    t_start, t_end = request_window(run_dir / "client_requests_log.csv")
    window_dir = run_dir / "analysis" / "window_5s"
    window_dir.mkdir(parents=True)
    in_window = {}
    with tempfile.TemporaryDirectory() as tmp:        # the full CSV is only an intermediate step, deleted at the end
        for side, pcap in pcaps.items():
            all_packets = Path(tmp) / f"{side}_all_packets.csv"
            export_packets(pcap, all_packets)
            in_window[side] = keep_window(all_packets, window_dir / f"{side}_metadata.csv", t_start, t_end)
    for side in pcaps:
        if in_window[side] == 0:
            warnings.append(f"no packets of the {side} Guard inside the request window: are the clocks different?")
    counters = {side: c.counters() for side, c in captures.items()}
    for side, n in counters.items():
        if n.get("dropped by kernel", 0) > 0:
            warnings.append(f"tcpdump on the {side} Guard dropped {n['dropped by kernel']} packets")
    if successes < args.requests:
        warnings.append(f"only {successes}/{args.requests} requests succeeded")
    if len(confirmed) < successes:
        warnings.append(f"the server log confirms only {len(confirmed)} of the {successes} successful requests")

    step("6/6 Writing run_info.json")
    info = {
        "scenario": args.scenario, "test": args.test, "run": args.run,
        "started_utc": started.isoformat(timespec="seconds"),
        "url": url, "page": args.page or "/", "requests": args.requests, "pause_s": args.pause,
        "requests_ok": successes, "requests_confirmed_by_server": len(confirmed),
        "window": {"start": t_start, "end": t_end, "margin_s": MARGIN},
        "client": {"container": client, "ip": ips[client]},
        "onion_service": {"container": hs, "ip": ips[hs], "onion_address": onion},
        "client_guard": {"container": client_guard, "nickname": nickname_of(client_guard), "ip": ips[client_guard]},
        "service_guard": {"container": service_guard, "nickname": nickname_of(service_guard), "ip": ips[service_guard]},
        "rendezvous_first_relays_after_run": guards_after,
        "packets_in_window": in_window, "tcpdump": counters,
        "guard_options_in_torrc": {side: guard_options(c) for side, c in (("client", client), ("service", hs))},
        "lab": {role: len(containers(role)) for role in ("da", "relay", "exit", "client", "hs")},
        "tor_version": sh("docker", "exec", client, "tor", "--version").splitlines()[0],
        "warnings": warnings,
    }
    (run_dir / "run_info.json").write_text(json.dumps(info, indent=2) + "\n")

    print(f"\nDone: {run_dir}")
    print(f"requests OK            {successes}/{args.requests}")
    print(f"confirmed by server    {len(confirmed)}/{args.requests}")
    print(f"packets in window      client Guard {in_window['client']} | service Guard {in_window['service']}")
    for w in warnings:
        print(f"WARNING: {w}")


if __name__ == "__main__":
    try:
        main()
    except RunError as e:
        sys.exit(f"\nERROR: {e}")
    except KeyboardInterrupt:
        sys.exit("\nStopped by the user.")
