#!/usr/bin/env python3
"""Coordinatore di un run clearnet: catture su tutti i relay + richieste HTTP.

Segue i passi descritti nel README («Come lo script coordinerà ogni run»):

1. Preparazione: controlla docker e tshark, crea /srv/<pagina> nel server se
   manca (10k.bin = 10.000 byte casuali). Il server e la rete li crea
   shared-base/clearnet/scripts/prepare.sh, che non viene modificato.
2. Prima della prima richiesta: legge da Docker i relay e i loro IP (gli exit
   ne hanno due, uno per rete), crea results/<run>/ (mai sovrascritta), avvia
   tcpdump in ogni relay e il registratore degli eventi CIRC/STREAM nel
   client, controlla che tutto sia attivo e attende 5 s.
3. Invia le richieste con http_run.send_requests.
4. Attende 5 s, ferma catture e registratore (anche su errore o Ctrl+C),
   copia i pcap, salva il log del server, converte i pcap in CSV con tshark
   e li ritaglia alla finestra prima richiesta − 5 s … ultima + 5 s.
5. Scrive run_info.json con parametri, container, IP, versione di Tor,
   intervalli di cattura, contatori di tcpdump, riepilogo HTTP e avvisi.

Se il run si interrompe, i file parziali restano nella cartella del run.

Esempi (dalla cartella del progetto o da qualunque altra):
    python3 experiments/clearnet-correlation/run_clearnet.py --run run_01
    python3 experiments/clearnet-correlation/run_clearnet.py --run sim_01 --requests 5
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

import http_run

HERE = Path(__file__).resolve().parent
MARGIN = 5.0
ROLES = ("da", "relay", "exit")
PAGE_SIZES = {"/10k.bin": 10000}
FIELDS = ("frame.time_epoch", "frame.len", "ip.src", "ip.dst", "tcp.srcport", "tcp.dstport")

# Registratore dei circuiti: gira nel container del client e scrive su stdout
# una riga per evento: «epoch<TAB>tipo<TAB>evento grezzo».
RECORDER = r"""
import signal, sys, threading, time
from stem.control import Controller, EventType
stop, lock = threading.Event(), threading.Lock()
signal.signal(signal.SIGINT, lambda *a: stop.set())
signal.signal(signal.SIGTERM, lambda *a: stop.set())
def out(kind, text):
    with lock:
        sys.stdout.write(f"{time.time():.6f}\t{kind}\t{text}\n")
        sys.stdout.flush()
with Controller.from_port(port=9051) as tor:
    tor.authenticate()
    for line in tor.get_info("circuit-status").splitlines():
        out("SNAPSHOT_START", line)
    tor.add_event_listener(lambda ev: out(ev.type, str(ev)), EventType.CIRC, EventType.STREAM)
    out("READY", sys.argv[1])
    while not stop.wait(0.5):
        pass
    for line in tor.get_info("circuit-status").splitlines():
        out("SNAPSHOT_END", line)
"""


class RunError(Exception):
    pass


def sh(*cmd, check=True):
    result = subprocess.run(cmd, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise RunError(f"comando fallito: {' '.join(cmd)}\n{result.stderr.strip()}")
    return result.stdout.strip()


def step(text):
    print(f"\n== {text}", flush=True)


def now():
    return datetime.now(timezone.utc)


# --- laboratorio -----------------------------------------------------------------------------

def containers(role):
    return sorted(sh("docker", "ps", "--filter", f"name=testing-tor-{role}-",
                     "--format", "{{.Names}}").split())


def networks(container):
    raw = sh("docker", "inspect", "-f",
             "{{range $k, $v := .NetworkSettings.Networks}}{{$k}}={{$v.IPAddress}} {{end}}", container)
    return dict(item.split("=", 1) for item in raw.split())


def nickname(container):
    _, _, role, suffix = container.split("-", 3)
    return role + suffix


def prepare_page(page):
    if not sh("docker", "ps", "--filter", f"name=^{http_run.SERVER_CONTAINER}$", "--format", "{{.Names}}"):
        raise RunError(f"{http_run.SERVER_CONTAINER} non è attivo: ./shared-base/clearnet/scripts/prepare.sh")
    path = f"/srv{page}"
    size = PAGE_SIZES.get(page)
    if size:
        sh("docker", "exec", http_run.SERVER_CONTAINER, "sh", "-c",
           f'[ "$(stat -c %s {path} 2>/dev/null)" = {size} ] || head -c {size} /dev/urandom > {path}')
    if sh("docker", "exec", http_run.SERVER_CONTAINER, "sh", "-c", f"stat -c %s {path}", check=False) == "":
        raise RunError(f"il server non ha {path}")
    return int(sh("docker", "exec", http_run.SERVER_CONTAINER, "stat", "-c", "%s", path))


# --- processi nei container --------------------------------------------------------------------

class Background:
    """Un processo lanciato con docker exec e fermato con SIGINT (pkill -f marker)."""

    def __init__(self, container, cmd, marker, stdout, stderr):
        self.container, self.cmd, self.marker = container, cmd, marker
        self.stdout_path, self.stderr_path = stdout, stderr
        self.proc = None
        self.started = self.stopped = None

    def start(self):
        self.out = open(self.stdout_path, "w") if self.stdout_path else subprocess.DEVNULL
        self.err = open(self.stderr_path, "w")
        self.started = time.time()
        # start_new_session: Ctrl+C arriva solo a questo script, che poi ferma il processo
        self.proc = subprocess.Popen(["docker", "exec", self.container, *self.cmd],
                                     stdout=self.out, stderr=self.err, start_new_session=True)

    def running(self):
        return self.proc is not None and self.proc.poll() is None

    def stop(self):
        if self.proc is None:
            return
        if self.proc.poll() is None:
            sh("docker", "exec", self.container, "pkill", "-INT", "-f", self.marker, check=False)
            try:
                self.proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                sh("docker", "exec", self.container, "pkill", "-KILL", "-f", self.marker, check=False)
                self.proc.wait(timeout=10)
        self.stopped = time.time()
        for f in (self.out, self.err):
            if f is not subprocess.DEVNULL:
                f.close()


class Capture(Background):
    def __init__(self, container, bpf, run, run_dir):
        self.remote = f"/tmp/clearnet_{run}_{nickname(container)}.pcap"
        self.bpf = bpf
        super().__init__(container, ["tcpdump", "-i", "any", "-nn", "-s", "0", "-U", "-w", self.remote, bpf],
                         self.remote, None, run_dir / "tcpdump" / f"{nickname(container)}.log")

    def start(self):
        sh("docker", "exec", self.container, "rm", "-f", self.remote)
        super().start()

    def counters(self):
        text = Path(self.stderr_path).read_text()
        return {m[2]: int(m[1]) for m in
                re.finditer(r"(\d+) packets? (captured|received by filter|dropped by kernel)", text)}

    def fetch(self, local):
        result = subprocess.run(["docker", "cp", f"{self.container}:{self.remote}", str(local)],
                                capture_output=True, text=True)
        if result.returncode == 0:
            sh("docker", "exec", self.container, "rm", "-f", self.remote, check=False)
            return True
        return False


# --- esportazione ------------------------------------------------------------------------------

def export(pcap, out_csv, t_start, t_end, client_ip, server_ip):
    """Converte il pcap in CSV, tiene la finestra e restituisce contatori utili alla scelta dei nodi."""
    stats = {"packets_in_window": 0, "bytes_with_client": 0, "bytes_with_server": 0}
    with tempfile.NamedTemporaryFile("w+", suffix=".csv") as full:
        cmd = ["tshark", "-r", str(pcap), "-T", "fields"]
        for field in FIELDS:
            cmd += ["-e", field]
        cmd += ["-E", "header=y", "-E", "separator=,", "-E", "occurrence=f"]
        result = subprocess.run(cmd, stdout=full, stderr=subprocess.PIPE, text=True)
        if result.returncode != 0:
            raise RunError(f"tshark non riesce a leggere {pcap}:\n{result.stderr.strip()}")
        full.seek(0)
        rows = csv.reader(full)
        with open(out_csv, "w", newline="") as fout:
            out = csv.writer(fout)
            out.writerow(next(rows))
            for row in rows:
                if not row or not t_start <= float(row[0]) <= t_end:
                    continue
                out.writerow(row)
                stats["packets_in_window"] += 1
                size, ends = int(row[1]), (row[2], row[3])
                if client_ip in ends:
                    stats["bytes_with_client"] += size
                if server_ip in ends:
                    stats["bytes_with_server"] += size
    return stats


def request_window(csv_path):
    with open(csv_path, newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise RunError("nessuna richiesta registrata")
    return (min(float(r["timestamp_start"]) for r in rows) - MARGIN,
            max(float(r["timestamp_end"]) for r in rows) + MARGIN)


def recorder_summary(path):
    kinds = Counter(line.split("\t", 2)[1] for line in Path(path).read_text().splitlines() if "\t" in line)
    return dict(kinds)


# --- main --------------------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(description="Un run clearnet: catture su tutti i relay e richieste HTTP.")
    p.add_argument("--run", required=True, help="nome del run, es. run_01 o sim_01")
    p.add_argument("--page", default=http_run.PAGE, help=f"pagina richiesta (default {http_run.PAGE})")
    p.add_argument("--requests", type=int, default=http_run.REQUESTS,
                   help=f"numero di richieste (default {http_run.REQUESTS})")
    p.add_argument("--pause", type=float, nargs=2, default=list(http_run.PAUSE), metavar=("MIN", "MAX"))
    args = p.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_-]+", args.run):
        p.error("--run può contenere solo lettere, numeri, '_' e '-'")
    if not re.fullmatch(r"/[A-Za-z0-9_.-]+", args.page):
        p.error("--page deve essere un file come /10k.bin")
    if args.requests < 1 or not 0 <= args.pause[0] <= args.pause[1]:
        p.error("--requests >= 1 e 0 <= MIN <= MAX")
    return args


def main():
    args = parse_args()
    warnings = []

    step("1/5 Preparazione")
    for tool in ("docker", "tshark"):
        if not shutil.which(tool):
            raise RunError(f"{tool} non trovato")
    run_dir = HERE / "results" / args.run
    if run_dir.exists():
        raise RunError(f"{run_dir} esiste già: scegliere un altro --run")
    page_size = prepare_page(args.page)
    print(f"pagina {args.page}: {page_size} byte")

    step("2/5 Relay, catture e registratore dei circuiti")
    relays = {role: containers(role) for role in ROLES}
    clients = containers("client")
    if len(clients) != 1 or not all(relays.values()):
        raise RunError(f"laboratorio incompleto: client {clients}, relay {relays}")
    client = clients[0]
    nets = {c: networks(c) for c in [client, *sum(relays.values(), [])]}
    client_ip = nets[client]["testing-tor"]
    exit_by_ip = {}
    for e in relays["exit"]:
        if "clearnet-target" not in nets[e]:
            raise RunError(f"{e} non è collegato a clearnet-target: rilanciare prepare.sh")
        exit_by_ip[nets[e]["clearnet-target"]] = e
    for c in [client, *sum(relays.values(), [])]:
        print(f"{c:32} {' '.join(f'{k}={v}' for k, v in sorted(nets[c].items()))}")

    run_dir.mkdir(parents=True)
    (run_dir / "tcpdump").mkdir()
    (run_dir / "pcap").mkdir()
    sh("sh", "-c", f"docker exec {client} cat /etc/tor/torrc > '{run_dir / 'torrc_client.txt'}'")
    captures = []
    for role, names in relays.items():
        bpf = f"tcp port 9001 or host {http_run.SERVER_IP}" if role == "exit" else "tcp port 9001"
        captures += [Capture(c, bpf, args.run, run_dir) for c in names]
    marker = f"circ_recorder_{args.run}"
    recorder = Background(client, ["python3", "-u", "-c", RECORDER, marker], marker,
                          run_dir / "circuit_events.tsv", run_dir / "circuit_recorder.log")

    started = now()
    finished = False
    successes = 0
    try:
        for c in captures:
            c.start()
        recorder.start()
        time.sleep(2)
        for c in captures:
            if not c.running():
                raise RunError(f"tcpdump in {c.container} si è fermato:\n{Path(c.stderr_path).read_text()}")
        for _ in range(20):
            if "READY" in recorder_summary(recorder.stdout_path):
                break
            if not recorder.running():
                break
            time.sleep(0.5)
        if "READY" not in recorder_summary(recorder.stdout_path):
            raise RunError(f"il registratore dei circuiti non parte:\n{Path(recorder.stderr_path).read_text()}")
        print(f"{len(captures)} catture e registratore attivi; attesa di {MARGIN:.0f} s")
        time.sleep(MARGIN)

        step(f"3/5 {args.requests} richieste a http://{http_run.SERVER_IP}{args.page}")

        def progress(n, rid, code, rc):
            print(f"  {n:3}/{args.requests}  HTTP {code or '---'}  curl {rc}", flush=True)

        successes = http_run.send_requests(run_dir / "client_requests.csv", args.page,
                                           args.requests, tuple(args.pause), progress)
        print(f"attesa di {MARGIN:.0f} s dopo l'ultima richiesta", flush=True)
        time.sleep(MARGIN)
        finished = True
    finally:
        step("4/5 Stop, copia dei file e log del server")
        for c in captures:
            c.stop()
        recorder.stop()
        for c in captures:
            if not c.fetch(run_dir / "pcap" / f"{nickname(c.container)}.pcap"):
                warnings.append(f"pcap di {c.container} non copiato")
        try:
            http_run.save_server_log(run_dir / "server.log", started)
        except Exception as exc:          # il log non deve impedire di conservare il resto
            warnings.append(f"log del server non salvato: {exc}")
        if not finished:
            print(f"\nRun non completato. I file parziali restano in {run_dir}")

    t_start, t_end = request_window(run_dir / "client_requests.csv")
    window_dir = run_dir / "analysis" / "window_5s"
    window_dir.mkdir(parents=True)
    nodes = {}
    for c in captures:
        nick = nickname(c.container)
        stats = export(run_dir / "pcap" / f"{nick}.pcap", window_dir / f"{nick}.csv",
                       t_start, t_end, client_ip, http_run.SERVER_IP)
        role = c.container.split("-")[2]
        nodes[nick] = {"container": c.container, "role": role, "ips": nets[c.container], "filter": c.bpf,
                       "capture_start": c.started, "capture_stop": c.stopped,
                       "tcpdump": c.counters(), **stats}
        if stats["packets_in_window"] == 0:
            warnings.append(f"{nick}: nessun pacchetto nella finestra")
        if nodes[nick]["tcpdump"].get("dropped by kernel", 0):
            warnings.append(f"{nick}: tcpdump ha perso {nodes[nick]['tcpdump']['dropped by kernel']} pacchetti")

    step("5/5 Scheda del run")
    summary = http_run.summarize(run_dir)
    if successes < args.requests:
        warnings.append(f"solo {successes}/{args.requests} richieste con HTTP 200")
    if summary["id_confermati_server"] < summary["tentativi"]:
        warnings.append(f"il server conferma {summary['id_confermati_server']}/{summary['tentativi']} ID")
    for key in ("id_duplicati_client", "id_duplicati_server", "id_con_piu_exit"):
        if summary[key]:
            warnings.append(f"{key}: {summary[key]}")
    unknown = set(summary["richieste_per_exit"]) - set(exit_by_ip)
    if unknown:
        warnings.append(f"IP di exit sconosciuti nel log del server: {sorted(unknown)}")
    events = recorder_summary(recorder.stdout_path)
    if not events.get("STREAM"):
        warnings.append("il registratore non ha scritto eventi STREAM")

    by_client = {n: s["bytes_with_client"] for n, s in nodes.items() if s["bytes_with_client"]}
    by_server = {n: s["bytes_with_server"] for n, s in nodes.items() if s["bytes_with_server"]}
    info = {
        "run": args.run, "completed": finished, "started_utc": started.isoformat(timespec="seconds"),
        "page": args.page, "page_bytes": page_size, "requests": args.requests, "pause_s": args.pause,
        "margin_s": MARGIN, "window": {"start": t_start, "end": t_end},
        "server": {"container": http_run.SERVER_CONTAINER, "ip": http_run.SERVER_IP},
        "client": {"container": client, "ip": client_ip},
        "exit_by_clearnet_ip": {ip: nickname(c) for ip, c in sorted(exit_by_ip.items())},
        "lab": {role: len(names) for role, names in relays.items()},
        "tor_version": sh("docker", "exec", client, "tor", "--version").splitlines()[0],
        "http": {k: v for k, v in summary.items() if k != "exit_per_richiesta"},
        "requests_per_exit_node": {nickname(exit_by_ip[ip]) if ip in exit_by_ip else ip: n
                                   for ip, n in summary["richieste_per_exit"].items()},
        "bytes_with_client_per_node": dict(sorted(by_client.items(), key=lambda kv: -kv[1])),
        "bytes_with_server_per_node": dict(sorted(by_server.items(), key=lambda kv: -kv[1])),
        "circuit_events": events,
        "nodes": nodes,
        "warnings": warnings,
    }
    (run_dir / "run_info.json").write_text(json.dumps(info, indent=2) + "\n")

    print(f"\nFatto: {run_dir}")
    print(f"HTTP 200 lato client   {successes}/{args.requests}")
    print(f"confermati dal server  {summary['id_confermati_server']}/{summary['tentativi']}")
    print(f"richieste per exit     {info['requests_per_exit_node']}")
    print(f"byte con il client     {info['bytes_with_client_per_node']}")
    print(f"byte con il server     {info['bytes_with_server_per_node']}")
    print(f"eventi dei circuiti    {events}")
    for w in warnings:
        print(f"AVVISO: {w}")
    if not finished:
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except RunError as e:
        sys.exit(f"\nERRORE: {e}")
    except KeyboardInterrupt:
        sys.exit("\nInterrotto dall'utente.")
