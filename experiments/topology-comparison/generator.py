#!/usr/bin/env python3
"""Send HTTP requests through the Tor client and log when each one started and ended.

The log is the ground truth of a run: it records when each request really happened.
The attacker never sees it; analyze.py uses it only to check the results.

The CSV has the same columns as experiments/onion-correlation/traffic_generator.py,
so the notebooks in that folder can read these logs too.

Background clients (multi-client tests): run_experiment.py starts one generator per background client with
--until-stopped, and stops it with SIGTERM at the end of the run. On SIGTERM the generator finishes the request
in progress, logs it and exits, so the log covers all the traffic the client sent.

Example, a short test with 5 requests:
    python experiments/topology-comparison/generator.py \\
        --url http://<onion-address>/ --requests 5 --out /tmp/test_log.csv
"""

import argparse
import csv
import random
import re
import signal
import sys
import time
from pathlib import Path

import requests

# Defaults of the command-line options (each one can be changed with the option in brackets)
REQUESTS = 100                        # --requests: number of requests
PAUSE = (2.0, 5.0)                    # --pause: random pause between requests, min and max seconds
SOCKS = "socks5h://127.0.0.1:9050"    # --socks: SOCKS proxy of the Tor client, published on the host by lab.sh
TIMEOUT = 60                          # --timeout: seconds, for a single request

# Columns of the log (the same as experiments/onion-correlation/traffic_generator.py)
COLUMNS = ["request_id", "timestamp_start", "timestamp_end", "latency_sec",
           "response_size_bytes", "status_code", "success", "error_message"]


def pause(seconds, should_stop):
    """Sleep for `seconds`, but return early as soon as should_stop() is True."""
    deadline = time.monotonic() + seconds
    while not should_stop():
        left = deadline - time.monotonic()
        if left <= 0:
            return
        time.sleep(min(left, 0.1))


def send_requests(url, count, pause_min, pause_max, socks, timeout, out_path, run_id=None, should_stop=None):
    """Send GET requests to `url`, one after another, and log each one to `out_path`.

    count: number of requests, or None to keep going until should_stop() is True.
    should_stop: optional function, checked before each request and during the pauses. A request already
    in progress is always completed and logged.
    Returns (successful requests, requests sent).
    """
    if count is None and should_stop is None:
        raise ValueError("count=None needs should_stop, otherwise the requests never end")
    should_stop = should_stop or (lambda: False)
    # socks5h (with the "h"): the Tor client resolves the hostname, which is required for .onion addresses
    proxies = {"http": socks, "https": socks}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    successes = sent = 0

    # Mode "x" creates the file and fails if it already exists, so an old log is never overwritten
    with open(out_path, "x", newline="") as f:
        log = csv.DictWriter(f, fieldnames=COLUMNS)
        log.writeheader()

        while (count is None or sent < count) and not should_stop():
            i = sent + 1
            # The id in the query string shows up in the web server's access log, so every request
            # can be matched with what the server received. The server ignores it when serving the file.
            tag = f"{run_id}-{i}" if run_id else str(i)
            request_url = f"{url}{'&' if '?' in url else '?'}id={tag}"

            start = time.time()            # wall-clock time, comparable with the tcpdump timestamps
            t0 = time.perf_counter()       # precise timer, only used for the latency
            try:
                response = requests.get(request_url, proxies=proxies, timeout=timeout)
                response.raise_for_status()
                result = {"response_size_bytes": len(response.content), "status_code": response.status_code,
                          "success": True, "error_message": ""}
            except requests.RequestException as exc:
                status = exc.response.status_code if exc.response is not None else ""
                result = {"response_size_bytes": 0, "status_code": status,
                          "success": False, "error_message": str(exc)}
            end = time.time()
            latency = time.perf_counter() - t0

            log.writerow({"request_id": i, "timestamp_start": start, "timestamp_end": end,
                          "latency_sec": latency, **result})
            f.flush()                      # write each row immediately: if the run is stopped, the log is still usable
            sent = i

            progress = f"{i:>3}/{count}" if count else f"{i:>3}"
            if result["success"]:
                successes += 1
                print(f"[{progress}] OK    {result['response_size_bytes']:>8} B  {latency:6.2f} s", flush=True)
            else:
                print(f"[{progress}] FAIL  {result['error_message']}", flush=True)

            if count is None or i < count:
                pause(random.uniform(pause_min, pause_max), should_stop)

    return successes, sent


def main():
    parser = argparse.ArgumentParser(description="Send HTTP requests through Tor and log their timing.")
    parser.add_argument("--url", required=True, help="page to request, e.g. http://<onion-address>/1m.bin")
    parser.add_argument("--requests", type=int, default=REQUESTS, help=f"number of requests (default: {REQUESTS})")
    parser.add_argument("--until-stopped", action="store_true",
                        help="ignore --requests and keep going until SIGTERM (used for the background clients)")
    parser.add_argument("--pause", type=float, nargs=2, default=list(PAUSE), metavar=("MIN", "MAX"),
                        help=f"random pause between requests, in seconds (default: {PAUSE[0]:g} {PAUSE[1]:g})")
    parser.add_argument("--socks", default=SOCKS, help=f"SOCKS proxy of the Tor client (default: {SOCKS})")
    parser.add_argument("--timeout", type=float, default=TIMEOUT,
                        help=f"timeout of a single request, in seconds (default: {TIMEOUT})")
    parser.add_argument("--out", required=True, type=Path, help="CSV file to create (must not exist)")
    parser.add_argument("--run-id", help="label added to each request id in the URL, e.g. run_01")
    args = parser.parse_args()

    if not args.url.startswith("http://"):
        parser.error("--url must start with http://")
    if args.requests < 1:
        parser.error("--requests must be at least 1")
    if not 0 <= args.pause[0] <= args.pause[1]:
        parser.error("--pause needs 0 <= MIN <= MAX")
    if args.run_id and not re.fullmatch(r"[A-Za-z0-9_-]+", args.run_id):
        parser.error("--run-id may contain only letters, numbers, '_' and '-'")

    # SIGTERM = "stop after the request in progress" (a plain flag: the handler must not take locks)
    stop_requested = []
    signal.signal(signal.SIGTERM, lambda *_: stop_requested.append(True))
    count = None if args.until_stopped else args.requests

    print(f"Sending {'requests until stopped' if count is None else f'{count} requests'} to {args.url} via {args.socks}", flush=True)
    try:
        successes, sent = send_requests(args.url, count, *args.pause, args.socks, args.timeout, args.out, args.run_id,
                                        should_stop=lambda: bool(stop_requested))
    except FileExistsError:
        sys.exit(f"ERROR: {args.out} already exists. Choose another --out so an old log is not overwritten.")
    except KeyboardInterrupt:
        sys.exit(f"\nStopped by the user. The requests sent so far are logged in {args.out}")

    print(f"Done: {successes}/{sent} successful. Log: {args.out}")
    all_done = successes == sent and (count is None or sent == count)
    sys.exit(0 if all_done else 1)   # exit code 1 = some request failed, or stopped before --requests


if __name__ == "__main__":
    main()
