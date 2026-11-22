"""Genera il traffico HTTP di prova e verifica quali richieste arrivano al server.

Questo modulo si esegue sulla VM del laboratorio. Produce il riferimento
applicativo (ground truth) del run clearnet: per ogni richiesta si può
controllare quando il client l'ha tentata, quale esito ha visto e se il
server ne ha registrato l'ID. L'unità di osservazione è la richiesta HTTP.
La raccolta sui relay e l'analisi dei burst sono attività distinte del piano.

Con «generate» lo script invia tramite curl 100 GET sequenziali alla pagina
del server interno, passando per il proxy SOCKS5 del client Tor. Ogni URL
contiene un ID formato da un prefisso casuale per il run e da un progressivo.
Fra due tentativi consecutivi lo script attende una pausa casuale uniforme
di 2–5 secondi. Per ogni invocazione di curl completata, anche con errore,
scrive subito una riga in client_requests.csv. I timestamp Unix delimitano
l'invocazione vista dalla VM; duration_sec è il tempo misurato da curl.
Il CSV conserva anche lo stato HTTP e il codice di uscita del processo.

Al termine del ciclo, anche in caso di interruzione, lo script tenta di
salvare in server.log i log Docker di clearnet-target-web dall'avvio del run.
I file di uscita vengono creati in modalità esclusiva per preservare i dati
di prove precedenti. Dopo una generazione completata stampa il riepilogo.

Con «summary» si può ripetere il solo confronto sui due file già raccolti,
anche a laboratorio spento. Il riepilogo JSON conta i tentativi, i successi
lato client (curl terminato con codice zero e HTTP 200) e gli altri esiti.
Un ID del CSV è confermato se compare in una GET nel log del server,
indipendentemente dallo stato HTTP. Gli elenchi degli ID mancanti sul server
e dei duplicati nei due file permettono di esaminare le anomalie.
"""

import argparse
import csv
import json
import random
import re
import subprocess
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4


FIELDS = ("request_id", "timestamp_start", "timestamp_end", "http_code",
          "duration_sec", "curl_exit_code")
ACCESS_LINE = re.compile(r'"GET (\S+) HTTP/[\d.]+" (\d{3})\b')
REQUESTS = 100


def timestamp():
    seconds, nanos = divmod(time.time_ns(), 10**9)
    return f"{seconds}.{nanos:09d}"


def generate(directory):
    directory.mkdir(parents=True, exist_ok=True)
    prefix = uuid4().hex
    since = datetime.now(timezone.utc).isoformat()
    # Exclusive creation preserves partial runs and prevents concurrent writers.
    with (directory / "client_requests.csv").open("x", newline="") as client:
        writer = csv.writer(client)
        writer.writerow(FIELDS)
        client.flush()
        try:
            for number in range(1, REQUESTS + 1):
                request_id = f"clearnet_{prefix}_{number:03d}"
                start = timestamp()
                result = subprocess.run([
                    "curl", "--disable", "--silent", "--show-error",
                    "--noproxy", "", "--socks5-hostname", "127.0.0.1:9050",
                    "--connect-timeout", "10", "--max-time", "60",
                    "--output", "/dev/null", "--write-out", "%{http_code} %{time_total}",
                    f"http://172.30.0.10/?request_id={request_id}",
                ], stdout=subprocess.PIPE, text=True, check=False)
                end = timestamp()
                code, _, duration = result.stdout.strip().partition(" ")
                writer.writerow((request_id, start, end, code, duration, result.returncode))
                client.flush()
                if number < REQUESTS:
                    time.sleep(random.uniform(2, 5))
        finally:
            with (directory / "server.log").open("x") as server:
                subprocess.run(["docker", "logs", "--since", since, "clearnet-target-web"],
                               stdout=server, stderr=subprocess.STDOUT, check=True)


def summarize(directory):
    with (directory / "client_requests.csv").open(newline="") as source:
        rows = list(csv.DictReader(source))
    attempts = Counter(row["request_id"] for row in rows)
    received = Counter()
    with (directory / "server.log").open() as source:
        for line in source:
            match = ACCESS_LINE.search(line)
            if match:
                ids = parse_qs(urlsplit(match[1]).query).get("request_id", [])
                if len(ids) == 1 and ids[0] in attempts:
                    received[ids[0]] += 1
    successful = sum(row["curl_exit_code"] == "0" and row["http_code"] == "200"
                     for row in rows)
    return {
        "tentativi": len(rows),
        "http_200_client": successful,
        "errori_o_esiti_diversi_da_200": len(rows) - successful,
        "id_confermati_server": len(received),
        "id_mancanti_server": sorted(attempts.keys() - received.keys()),
        "id_duplicati_client": sorted(key for key in attempts if attempts[key] > 1),
        "id_duplicati_server": sorted(key for key in received if received[key] > 1),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("generate", "summary"))
    parser.add_argument("directory", type=Path, help="Cartella dei dati di un run")
    args = parser.parse_args()
    if args.action == "generate":
        if (args.directory / "server.log").exists():
            parser.error("server.log esiste già nella cartella del run")
        generate(args.directory)
    print(json.dumps(summarize(args.directory), indent=2))
