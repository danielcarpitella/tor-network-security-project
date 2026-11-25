"""Genera il traffico HTTP di prova e verifica quali richieste arrivano al server.

Questo modulo si esegue sulla VM del laboratorio. Produce il riferimento
applicativo (ground truth) del run clearnet: per ogni richiesta si può
controllare quando il client l'ha tentata, quale esito ha visto, se il
server ne ha registrato l'ID e quale exit l'ha inoltrata. L'unità di
osservazione è la richiesta HTTP. Il coordinatore run_clearnet.py usa le
funzioni di questo modulo durante la raccolta completa, con le catture.

Con «generate» lo script invia tramite curl N GET sequenziali (default 100)
alla pagina del server interno (default /10k.bin), passando per il proxy
SOCKS5 del client Tor. Ogni URL contiene un ID formato da un prefisso
casuale per il run e da un progressivo. Fra due tentativi consecutivi lo
script attende una pausa casuale uniforme di 2–5 secondi. Per ogni
invocazione di curl completata, anche con errore, scrive subito una riga in
client_requests.csv. I timestamp Unix delimitano l'invocazione vista dalla
VM; duration_sec è il tempo misurato da curl. Il CSV conserva anche lo stato
HTTP e il codice di uscita del processo. Usato da solo, «generate» salva
anche server.log con i log Docker di clearnet-target-web dall'avvio del
ciclo; nel coordinatore il salvataggio del log lo fa il coordinatore.
I file di uscita vengono creati in modalità esclusiva per preservare i dati
di prove precedenti.

Con «summary» si può ripetere il solo confronto sui due file già raccolti,
anche a laboratorio spento. Il riepilogo JSON conta i tentativi, i successi
lato client (curl terminato con codice zero e HTTP 200) e gli altri esiti.
Un ID del CSV è confermato se compare in una GET nel log del server,
indipendentemente dallo stato HTTP. Per ogni ID confermato riporta l'IP del
peer TCP registrato dal server, cioè l'IP dell'exit sulla rete
clearnet-target, e conta le richieste per exit. Gli elenchi degli ID
mancanti sul server e dei duplicati nei due file permettono di esaminare le
anomalie.
"""

import argparse
import csv
import json
import random
import re
import subprocess
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4


FIELDS = ("request_id", "timestamp_start", "timestamp_end", "http_code",
          "duration_sec", "curl_exit_code")
# Riga del log di http.server: «IP - - [data] "GET /percorso HTTP/1.1" 200 -»
ACCESS_LINE = re.compile(r'^(\S+) \S+ \S+ \[[^\]]*\] "GET (\S+) HTTP/[\d.]+" (\d{3})\b')
SERVER_IP = "172.30.0.10"
SERVER_CONTAINER = "clearnet-target-web"
SOCKS = "127.0.0.1:9050"
PAGE = "/10k.bin"
REQUESTS = 100
PAUSE = (2.0, 5.0)


def timestamp():
    seconds, nanos = divmod(time.time_ns(), 10**9)
    return f"{seconds}.{nanos:09d}"


def send_requests(csv_path, page=PAGE, requests=REQUESTS, pause=PAUSE, progress=None):
    """Invia le richieste sequenziali e scrive una riga per tentativo.

    Restituisce il numero di tentativi con curl riuscito e HTTP 200.
    `progress`, se dato, viene chiamato dopo ogni tentativo con
    (numero, request_id, http_code, curl_exit_code).
    """
    prefix = uuid4().hex
    successes = 0
    # La creazione esclusiva preserva i run parziali e impedisce scritture concorrenti.
    with Path(csv_path).open("x", newline="") as client:
        writer = csv.writer(client)
        writer.writerow(FIELDS)
        client.flush()
        for number in range(1, requests + 1):
            request_id = f"clearnet_{prefix}_{number:03d}"
            start = timestamp()
            result = subprocess.run([
                "curl", "--disable", "--silent", "--show-error",
                "--noproxy", "", "--socks5-hostname", SOCKS,
                "--connect-timeout", "10", "--max-time", "60",
                "--output", "/dev/null", "--write-out", "%{http_code} %{time_total}",
                f"http://{SERVER_IP}{page}?request_id={request_id}",
            ], stdout=subprocess.PIPE, text=True, check=False)
            end = timestamp()
            code, _, duration = result.stdout.strip().partition(" ")
            writer.writerow((request_id, start, end, code, duration, result.returncode))
            client.flush()
            successes += result.returncode == 0 and code == "200"
            if progress:
                progress(number, request_id, code, result.returncode)
            if number < requests:
                time.sleep(random.uniform(*pause))
    return successes


def save_server_log(path, since):
    """Salva i log Docker del server da `since` (datetime UTC) in poi."""
    with Path(path).open("x") as server:
        subprocess.run(["docker", "logs", "--since", since.isoformat(), SERVER_CONTAINER],
                       stdout=server, stderr=subprocess.STDOUT, check=True)


def server_requests(server_log):
    """Per ogni request_id trovato nel log del server, le coppie (IP del peer, stato HTTP)."""
    seen = defaultdict(list)
    with Path(server_log).open() as source:
        for line in source:
            match = ACCESS_LINE.search(line)
            if match:
                ids = parse_qs(urlsplit(match[2]).query).get("request_id", [])
                if len(ids) == 1:
                    seen[ids[0]].append((match[1], match[3]))
    return seen


def summarize(directory):
    with (directory / "client_requests.csv").open(newline="") as source:
        rows = list(csv.DictReader(source))
    attempts = Counter(row["request_id"] for row in rows)
    seen = server_requests(directory / "server.log")
    received = {key: value for key, value in seen.items() if key in attempts}
    exit_ip = {key: value[0][0] for key, value in received.items()}
    successful = sum(row["curl_exit_code"] == "0" and row["http_code"] == "200"
                     for row in rows)
    return {
        "tentativi": len(rows),
        "http_200_client": successful,
        "errori_o_esiti_diversi_da_200": len(rows) - successful,
        "id_confermati_server": len(received),
        "id_mancanti_server": sorted(attempts.keys() - received.keys()),
        "id_duplicati_client": sorted(key for key in attempts if attempts[key] > 1),
        "id_duplicati_server": sorted(key for key in received if len(received[key]) > 1),
        "id_con_piu_exit": sorted(key for key in received
                                  if len({ip for ip, _ in received[key]}) > 1),
        "richieste_per_exit": dict(Counter(exit_ip.values())),
        "exit_per_richiesta": dict(sorted(exit_ip.items())),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=("generate", "summary"))
    parser.add_argument("directory", type=Path, help="Cartella dei dati di un run")
    parser.add_argument("--page", default=PAGE, help=f"pagina richiesta (default {PAGE})")
    parser.add_argument("--requests", type=int, default=REQUESTS,
                        help=f"numero di richieste (default {REQUESTS})")
    args = parser.parse_args()
    if args.action == "generate":
        if not args.page.startswith("/") or args.requests < 1:
            parser.error("--page deve iniziare con / e --requests deve essere almeno 1")
        if (args.directory / "server.log").exists():
            parser.error("server.log esiste già nella cartella del run")
        args.directory.mkdir(parents=True, exist_ok=True)
        since = datetime.now(timezone.utc)
        try:
            send_requests(args.directory / "client_requests.csv", args.page, args.requests)
        finally:
            save_server_log(args.directory / "server.log", since)
    print(json.dumps(summarize(args.directory), indent=2))
