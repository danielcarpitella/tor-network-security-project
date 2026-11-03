import os
import re
import time
import random
import logging
from pathlib import Path

import requests
import pandas as pd

# --- CONFIGURATION ---
ONION_URL = "http://hy5r6cm6om6oap2obkfx4aaculnjgg72oh6zrvjjy5psdx3ucnmgxsqd.onion/"

# run the script on the host and have exposed the Tor client port
TOR_SOCKS_PROXY = os.getenv(
    "TOR_SOCKS_PROXY",
    "socks5h://127.0.0.1:9050"
)

try:
    NUM_REQUESTS = int(os.getenv("NUM_REQUESTS", "100"))
except ValueError as exc:
    raise ValueError("NUM_REQUESTS must be a positive integer") from exc
if NUM_REQUESTS < 1:
    raise ValueError("NUM_REQUESTS must be a positive integer")

MIN_SLEEP = 2.0
MAX_SLEEP = 5.0
RUN_ID = os.getenv("RUN_ID")
if not RUN_ID:
    raise ValueError("Set RUN_ID, e.g. RUN_ID=run_01")
if not re.fullmatch(r"[A-Za-z0-9_-]+", RUN_ID):
    raise ValueError("RUN_ID may contain only letters, numbers, underscores, and hyphens")

OUTPUT_FILE = (
    Path(__file__).resolve().parent / "results" / RUN_ID / "client_requests_log.csv"
)

logging.basicConfig(level=logging.INFO, format="%(message)s")


def generate_traffic():
    output_path = Path(OUTPUT_FILE)
    if output_path.exists():
        raise FileExistsError(
            f"Output already exists: {output_path}. Choose a new RUN_ID to avoid overwriting it."
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)

    logging.info("Starting traffic generation to %s", ONION_URL)
    logging.info("Using Tor SOCKS proxy: %s", TOR_SOCKS_PROXY)
    logging.info("Run ID: %s | Requests: %s", RUN_ID, NUM_REQUESTS)

    session = requests.Session()
    session.proxies = {
        "http": TOR_SOCKS_PROXY,
        "https": TOR_SOCKS_PROXY,
    }

    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )
    })

    log_data = []

    for i in range(1, NUM_REQUESTS + 1):
        start_unix = time.time()
        start_perf = time.perf_counter()

        try:
            logging.info("[%s/%s] Sending request...", i, NUM_REQUESTS)
            response = session.get(ONION_URL, timeout=30)
            response.raise_for_status()

            end_unix = time.time()
            end_perf = time.perf_counter()
            latency = end_perf - start_perf
            size = len(response.content)

            logging.info(
                "  -> Success! Latency: %.2fs | Size: %d bytes | Status: %s",
                latency, size, response.status_code
            )

            log_data.append({
                "request_id": i,
                "timestamp_start": start_unix,
                "timestamp_end": end_unix,
                "latency_sec": latency,
                "response_size_bytes": size,
                "status_code": response.status_code,
                "success": True,
                "error_message": None,
            })

        except requests.RequestException as exc:
            end_unix = time.time()
            end_perf = time.perf_counter()
            latency = end_perf - start_perf

            logging.error("  -> Request failed: %s", exc)

            log_data.append({
                "request_id": i,
                "timestamp_start": start_unix,
                "timestamp_end": end_unix,
                "latency_sec": latency,
                "response_size_bytes": 0,
                "status_code": None,
                "success": False,
                "error_message": str(exc),
            })

        if i < NUM_REQUESTS:
            sleep_time = random.uniform(MIN_SLEEP, MAX_SLEEP)
            logging.info("  -> Waiting for %.2f seconds...\n", sleep_time)
            time.sleep(sleep_time)

    df = pd.DataFrame(log_data)
    df.to_csv(output_path, index=False)
    logging.info("\nTraffic generated. Log saved to %s", output_path)


if __name__ == "__main__":
    generate_traffic()