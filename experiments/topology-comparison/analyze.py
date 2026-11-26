#!/usr/bin/env python3
"""Analyse the runs of one test: can the traffic seen at the two Guards be linked by timing alone?

PURPOSE
    The attacker sees only the packets at the client's Guard and at the onion service's Guard (no content,
    only time, size and IPs). This script measures how well the two sides can be linked by timing, and
    compares it with a control where no real link exists, to show how much of it could be luck.
    It reads the runs recorded by run_experiment.py (one test = several runs with the same settings)
    and prints one table with a row per run and a total row.
    Multi-client runs (run_experiment.py --clients N): the attacker watches the TARGET client's Guard, while
    background clients use the same onion service. Two extra columns show how often the target's pairs were
    made while a background client was active, i.e. when the attacker could have been fooled.
    A plain explanation of every column, with a worked example, the limits and the known issues: ANALYSIS.md.

REQUIREMENTS
    Two or more runs of the same test (the control of a run uses the other runs; with one run only the
    real result can be computed). The project venv with pandas and numpy.

HOW IT WORKS, STEP BY STEP
    Step 1 - Find the runs
      Each path given can be a run folder (it contains client_requests_log.csv) or a test folder, in which
      case every run folder inside it is used (e.g. results/onion/baseline -> run_01 ... run_05).
      The IPs of the two Guards come from each run's run_info.json, or from --client-guard-ip and
      --service-guard-ip (needed for runs without run_info.json, e.g. those in experiments/onion-correlation/).
      Same for the target client's IP (only used with --target-only): run_info.json, or --client-ip.
      Background clients: their request logs, background/bg<k>_requests_log.csv, if the run has them.
      If the runs have different settings (page, number of requests, pause, number of clients) a WARNING is printed.

    Step 2 - Bursts (for each run, each Guard)
      1. Read analysis/window_5s/<side>_metadata.csv: one row per packet (time, size, source IP, destination IP).
      2. Remove the packets that the two Guards exchange directly with each other: they are in both captures
         with the same timestamps, so they would pair trivially and inflate the result.
         With --target-only, on the client side keep only the packets between the target client and its Guard.
      3. Direction of each packet seen from the Guard: +1 if the Guard sends it, -1 if it receives it.
         Without --target-only the neighbour (client, service, other relay) is not used: all packets of the
         Guard form one timeline.
      4. Group consecutive packets into bursts: a new burst starts when the gap from the previous packet is
         more than BURST_GAP (0.1 s) or the direction changes.
      5. Keep the bursts of at least MIN_BYTES (1000 bytes, sum of the frame lengths); smaller ones are mostly
         Tor housekeeping. Only the start time of each burst is used from here on.

    Step 3 - Pair the bursts (real result)
      Client bursts are taken in time order. Each one is paired with the service burst that starts closest to it,
      within +/- LAG (0.05 s), among those not paired yet: one-to-one, a service burst is never used twice.

    Step 4 - Control (what luck alone gives)
      The same pairing, but the client bursts of run i against the service bursts of each other run j,
      shifted so that the first requests of the two runs line up. Run j happened at another time, so no
      pair can be real. The control of run i is the mean over all the other runs.

    Step 5 - Check the pairs against the ground truth (client_requests_log.csv, only successful requests)
      A request's interval is [timestamp_start - LAG, timestamp_end + LAG].
      1. Pairs inside requests: how many of the pairs have their client burst inside the interval of some
         request, i.e. how many of the attacker's matches fall during a real request.
      2. Request coverage: a request counts as covered if at least one pair falls inside its interval.
      Both are computed the same way for the real pairs and for the control.
      3. Background clients (only runs that have them). A background request's interval is
         [timestamp_start - LAG, timestamp_end + LAG], for every request sent (failed ones too: they also
         made traffic).
           - Target requests overlapping a background request: the target and someone else were active at
             the same time, so the service's Guard carried both.
           - Real pairs made while a background request was open: the service burst of such a pair may belong
             to the background client instead of the target, and timing alone cannot tell.

    Step 6 - Table
      Per run and in total (ALL, pooled over all runs):
        clients                         clients sending at the same time, target included (from run_info.json)
        client_bursts, service_bursts   bursts of at least MIN_BYTES at each Guard
        pairs                           real pairs
        paired_%                        pairs / client_bursts
        control_%                       control pairs / client_bursts (mean over the other runs)
        pairs_in_req_%                  pairs inside a request / pairs
        control_pairs_in_req_%          the same for the control pairs
        covered                         requests covered / successful requests
        covered_%, covered_control_%    the same as a percentage, real and control
        req_overlap_%                   target requests overlapping a background request / successful requests
        pairs_overlap_%                 real pairs made while a background request was open / pairs
                                        (both empty for runs without background clients)
      Printed, and saved as summary_<settings>.csv in the test folder (or in --out).

DESIGN CHOICES (and why)
    * Same method and the same settings as the minimal analysis in experiments/onion-correlation/
      (onion_analysis_minimal.ipynb), so results are comparable with it; there, running this script on those
      runs gives the same numbers. Settings can be changed with --gap, --min-bytes and --lag for sensitivity checks;
      the output file name contains them, so different settings never overwrite each other.
    * Three percentages, each with its own denominator:
        - out of the client bursts: how many find a partner (how clean the signal is; extra traffic on a Guard
          adds bursts with no partner);
        - out of the pairs: how many fall during a real request (are the attacker's matches real?);
        - out of the requests: how many have at least one pair (how many requests the attacker would spot).
    * "Pairs inside requests" is close to 100% for real AND control as long as one client sends requests with
      silence in between: then almost every client burst happens during a request anyway, so even lucky pairs
      fall inside one.
    * --target-only: the attacker watches a known user, so on the client side only the packets between that
      user (the target client) and its Guard matter. Without it, a background client that happens to use the
      same Guard would add its bursts to the target's. The filter also drops the Guard's packets towards other
      relays, so the numbers change a little even with one client: compare runs with different numbers of
      clients only when they are all analysed with --target-only (the baseline too; its recorded files already
      contain everything, only the analysis is run again). Without --target-only the results stay comparable
      with experiments/onion-correlation/.
    * pairs_overlap_% is an UPPER BOUND on the wrong pairs, not a count of them. A burst has no label, and at
      the service's Guard all clients arrive through the same connection from the service, so the IPs cannot
      tell them apart either. A pair made while the target was the only client active is almost certainly
      right; one made while a background client was active too may be right or wrong. ("Pairs made only
      during background requests" would be ~0 by construction: with --target-only the client burst of a pair
      is the target's own traffic, so it falls during a target request.) Read it next to req_overlap_%:
      how busy it was vs how many pairs were exposed to confusion.
    * Every result comes with its control: a percentage alone says nothing, the gap between real and control does.
    * Percentages are timing statistics, not precision/recall: a burst has no independent label saying which
      request caused it; the request log is only used to count covered requests.
    * The control aligns runs on their first request (not on capture start), so both sides have a
      realistic request rhythm and only the true timing link is missing.

OUTPUT
    The table on screen and summary_gap<gap>_min<bytes>_lag<lag>[_target].csv (by default in the test folder;
    "_target" with --target-only).

EXAMPLES
    python experiments/topology-comparison/analyze.py experiments/topology-comparison/results/onion/baseline
    python experiments/topology-comparison/analyze.py results/onion/size_1m --lag 0.02
    python experiments/topology-comparison/analyze.py results/onion/clients_2 --target-only
    python experiments/topology-comparison/analyze.py ../onion-correlation/results/run_0{1,2,3,4,5} \\
        --client-guard-ip 10.5.184.229 --service-guard-ip 10.5.237.145 --out /tmp/elena_check.csv
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

BURST_GAP = 0.1      # seconds: a bigger gap between packets starts a new burst
MIN_BYTES = 1000     # bytes: smaller bursts are discarded
LAG = 0.05           # seconds: max distance between the starts of two paired bursts

SETTINGS_TO_MATCH = ("scenario", "page", "requests", "pause_s", "clients")   # must be equal across the runs of a test
SETTING_DEFAULTS = {"clients": 1}     # for runs recorded before the setting existed


class AnalysisError(Exception):
    """A problem that stops the analysis; the message is shown to the user."""


# --- step 1: runs ----------------------------------------------------------------------------

def find_runs(paths):
    """Run folders among `paths`: a path is a run if it has client_requests_log.csv, otherwise its sub-folders are."""
    runs = []
    for path in map(Path, paths):
        if (path / "client_requests_log.csv").exists():
            runs.append(path)
        elif path.is_dir():
            found = sorted(d for d in path.iterdir() if (d / "client_requests_log.csv").exists())
            if not found:
                raise AnalysisError(f"{path} is not a run folder and contains no run folders")
            runs.extend(found)
        else:
            raise AnalysisError(f"{path} does not exist")
    if len(set(runs)) != len(runs):
        raise AnalysisError("the same run was given twice")
    return runs


def run_info(run):
    info_file = run / "run_info.json"
    return json.loads(info_file.read_text()) if info_file.exists() else {}


def guard_ips(run, info, args):
    if args.client_guard_ip and args.service_guard_ip:
        return args.client_guard_ip, args.service_guard_ip
    if info:
        return info["client_guard"]["ip"], info["service_guard"]["ip"]
    raise AnalysisError(f"{run} has no run_info.json: give the Guard IPs with --client-guard-ip and --service-guard-ip")


def target_ip(run, info, args):
    """IP of the target client, for --target-only."""
    if args.client_ip:
        return args.client_ip
    if info:
        return info["client"]["ip"]
    raise AnalysisError(f"{run} has no run_info.json: give the target client's IP with --client-ip")


def load_requests(run):
    req = pd.read_csv(run / "client_requests_log.csv")
    req = req[req["success"]]
    if req.empty:
        raise AnalysisError(f"{run} has no successful requests")
    return req


def load_background(run, info):
    """All requests sent by the background clients of a run (one table), or None if it has none."""
    files = sorted((run / "background").glob("*_requests_log.csv"))
    expected = info.get("clients", 1) - 1
    if len(files) != expected and info:
        raise AnalysisError(f"{run}: run_info.json says {expected} background client(s), found {len(files)} logs in background/")
    if not files:
        return None
    return pd.concat([pd.read_csv(f) for f in files], ignore_index=True)


# --- step 2: bursts --------------------------------------------------------------------------

def burst_starts(csv_path, guard_ip, other_guard_ip, gap, min_bytes, only_peer=None):
    """Start times of the bursts of one Guard (see step 2 in the description at the top).
    only_peer: keep only the packets between the Guard and this IP (--target-only, client side)."""
    df = pd.read_csv(csv_path)
    df.columns = ["time", "size", "src", "dst"]
    df = df.dropna(subset=["src", "dst"]).sort_values("time")
    if not ((df["src"] == guard_ip) | (df["dst"] == guard_ip)).any():
        raise AnalysisError(f"{csv_path}: no packet to or from the Guard IP {guard_ip} (wrong IP?)")
    peer = np.where(df["src"] == guard_ip, df["dst"], df["src"])
    keep = peer != other_guard_ip                              # remove the direct Guard-to-Guard packets
    if only_peer:
        keep &= peer == only_peer
        if not keep.any():
            raise AnalysisError(f"{csv_path}: no packet between the Guard {guard_ip} and the client {only_peer} (wrong IP?)")
    df = df[keep].copy()
    df["dir"] = np.where(df["src"] == guard_ip, 1, -1)         # +1 sent by the Guard, -1 received
    new_burst = (df["time"].diff() > gap) | (df["dir"] != df["dir"].shift())
    bursts = df.assign(burst=new_burst.cumsum()).groupby("burst").agg(start=("time", "min"), bytes=("size", "sum"))
    return np.sort(bursts.loc[bursts["bytes"] >= min_bytes, "start"].to_numpy())


# --- steps 3-5: pairing, control, coverage ---------------------------------------------------

def pair(client_starts, service_starts, lag, shift=0.0):
    """One-to-one: each client burst (in time order) takes the closest unused service burst within +/- lag.
    `shift` moves the service bursts in time (used by the control). Returns the start times of the paired client bursts."""
    svc = np.sort(service_starts) + shift
    used = np.zeros(len(svc), dtype=bool)
    paired = []
    for t in client_starts:
        lo = np.searchsorted(svc, t - lag, side="left")
        hi = np.searchsorted(svc, t + lag, side="right")
        free = np.arange(lo, hi)[~used[lo:hi]]
        if free.size:
            j = free[np.argmin(np.abs(svc[free] - t))]
            used[j] = True
            paired.append(t)
    return np.array(paired)


def covered(paired_starts, req, lag):
    """Number of requests with at least one paired client burst starting in [start - lag, end + lag]."""
    starts = np.sort(paired_starts)
    lo = np.searchsorted(starts, req["timestamp_start"].to_numpy() - lag, side="left")
    hi = np.searchsorted(starts, req["timestamp_end"].to_numpy() + lag, side="right")
    return int((hi > lo).sum())


def inside_requests(starts, req, lag):
    """Number of `starts` (paired client bursts) that fall inside a request, i.e. in [start - lag, end + lag] of some request."""
    if len(starts) == 0:
        return 0
    lo = req["timestamp_start"].to_numpy() - lag
    hi = req["timestamp_end"].to_numpy() + lag
    return int(((starts[:, None] >= lo[None, :]) & (starts[:, None] <= hi[None, :])).any(axis=1).sum())


def overlapping_requests(req, other, lag):
    """Number of requests in `req` whose interval [start - lag, end + lag] overlaps the interval of some
    request in `other` (the background requests)."""
    s, e = req["timestamp_start"].to_numpy() - lag, req["timestamp_end"].to_numpy() + lag
    os_, oe = other["timestamp_start"].to_numpy() - lag, other["timestamp_end"].to_numpy() + lag
    return int(((s[:, None] <= oe[None, :]) & (os_[None, :] <= e[:, None])).any(axis=1).sum())


def pct(part, whole):
    return round(100 * part / whole, 1) if whole else float("nan")


# --- main ------------------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(description="Link the traffic of the two Guards by timing, real vs control.")
    p.add_argument("paths", nargs="+", help="a test folder (e.g. results/onion/baseline) or run folders")
    p.add_argument("--gap", type=float, default=BURST_GAP, help=f"burst gap in seconds (default: {BURST_GAP})")
    p.add_argument("--min-bytes", type=int, default=MIN_BYTES, help=f"minimum burst size in bytes (default: {MIN_BYTES})")
    p.add_argument("--lag", type=float, default=LAG, help=f"max start distance of a pair in seconds (default: {LAG})")
    p.add_argument("--target-only", action="store_true",
                   help="client side: keep only the packets between the target client and its Guard (multi-client tests)")
    p.add_argument("--client-guard-ip", help="IP of the client's Guard, for runs without run_info.json")
    p.add_argument("--service-guard-ip", help="IP of the onion service's Guard, for runs without run_info.json")
    p.add_argument("--client-ip", help="IP of the target client, for --target-only on runs without run_info.json")
    p.add_argument("--out", type=Path, help="CSV file for the table (default: summary_<settings>.csv in the test folder)")
    args = p.parse_args()
    if bool(args.client_guard_ip) != bool(args.service_guard_ip):
        p.error("give both --client-guard-ip and --service-guard-ip, or neither")
    return args


def main():
    args = parse_args()
    runs = find_runs(args.paths)
    names = [r.name for r in runs] if len({r.name for r in runs}) == len(runs) else [f"{r.parent.name}/{r.name}" for r in runs]
    warnings = []

    # Step 1-2: load every run and compute the bursts of both Guards
    client, service, requests, background, t0 = {}, {}, {}, {}, {}
    infos = {}
    for run in runs:
        infos[run] = run_info(run)
        client_ip, service_ip = guard_ips(run, infos[run], args)
        only = target_ip(run, infos[run], args) if args.target_only else None
        window = run / "analysis" / "window_5s"
        client[run] = burst_starts(window / "client_metadata.csv", client_ip, service_ip, args.gap, args.min_bytes, only)
        service[run] = burst_starts(window / "service_metadata.csv", service_ip, client_ip, args.gap, args.min_bytes)
        requests[run] = load_requests(run)
        background[run] = load_background(run, infos[run])
        t0[run] = requests[run]["timestamp_start"].min()     # first request: used to align runs in the control
    has_background = any(b is not None for b in background.values())

    for key in SETTINGS_TO_MATCH:
        values = {json.dumps(infos[r].get(key, SETTING_DEFAULTS.get(key))) for r in runs if infos[r]}
        if len(values) > 1:
            warnings.append(f"the runs have different '{key}': {sorted(values)}; a test should use the same settings")
    if len(runs) < 2:
        warnings.append("only one run: the control needs other runs, so it is not computed")
    if has_background and not args.target_only:
        warnings.append("the runs have background clients but --target-only is not set: a background client "
                        "using the target's Guard adds its bursts to the client side")

    # Steps 3-5: real pairs, control, coverage, overlap with the background clients
    rows = []
    for run, name in zip(runs, names):
        req, bg = requests[run], background[run]
        real = pair(client[run], service[run], args.lag)
        controls = [pair(client[run], service[other], args.lag, shift=t0[run] - t0[other]) for other in runs if other != run]
        rows.append({
            "run": name, "clients": infos[run].get("clients", 1), "requests": len(req),
            "client_bursts": len(client[run]), "service_bursts": len(service[run]),
            "pairs": len(real),
            "pairs_control": np.mean([len(c) for c in controls]) if controls else float("nan"),
            "pairs_in_req": inside_requests(real, req, args.lag),
            "pairs_control_in_req": np.mean([inside_requests(c, req, args.lag) for c in controls]) if controls else float("nan"),
            "covered": covered(real, req, args.lag),
            "covered_control": np.mean([covered(c, req, args.lag) for c in controls]) if controls else float("nan"),
            "req_overlap": overlapping_requests(req, bg, args.lag) if bg is not None else float("nan"),
            "pairs_overlap": inside_requests(real, bg, args.lag) if bg is not None else float("nan"),
        })
    raw = pd.DataFrame(rows)
    total = raw.drop(columns=["run", "clients"]).sum(min_count=1)
    same_clients = raw["clients"].iloc[0] if raw["clients"].nunique() == 1 else float("nan")
    raw.loc[len(raw)] = {"run": "ALL", "clients": same_clients, **total.to_dict()}

    # Step 6: table
    table = pd.DataFrame({
        "run": raw["run"],
        "clients": raw["clients"].astype("Int64"),
        "requests": raw["requests"].astype(int),
        "client_bursts": raw["client_bursts"].astype(int),
        "service_bursts": raw["service_bursts"].astype(int),
        "pairs": raw["pairs"].astype(int),
        "paired_%": [pct(p, c) for p, c in zip(raw["pairs"], raw["client_bursts"])],
        "control_%": [pct(p, c) for p, c in zip(raw["pairs_control"], raw["client_bursts"])],
        "pairs_in_req_%": [pct(i, p) for i, p in zip(raw["pairs_in_req"], raw["pairs"])],
        "control_pairs_in_req_%": [pct(i, p) for i, p in zip(raw["pairs_control_in_req"], raw["pairs_control"])],
        "covered":[f"{int(c)}/{int(n)}" for c, n in zip(raw["covered"], raw["requests"])],
        "covered_%": [pct(c, n) for c, n in zip(raw["covered"], raw["requests"])],
        "covered_control_%": [pct(c, n) for c, n in zip(raw["covered_control"], raw["requests"])],
        "req_overlap_%": [pct(o, n) for o, n in zip(raw["req_overlap"], raw["requests"])],
        "pairs_overlap_%": [pct(o, p) for o, p in zip(raw["pairs_overlap"], raw["pairs"])],
    })

    settings = f"gap{args.gap:g}_min{args.min_bytes}_lag{args.lag:g}" + ("_target" if args.target_only else "")
    print(f"Runs: {len(runs)}   |   burst gap {args.gap:g} s, min burst {args.min_bytes} B, "
          f"pairs within +/-{args.lag:g} s, Guard-to-Guard packets removed, client side: "
          f"{'target client only' if args.target_only else 'all packets of the Guard'}\n")
    print(table.to_string(index=False))
    all_row = table.iloc[-1]
    print(f"\nPaired client bursts:  {all_row['paired_%']}%   (control {all_row['control_%']}%)")
    print(f"Pairs inside requests: {all_row['pairs_in_req_%']}%   (control {all_row['control_pairs_in_req_%']}%)")
    print(f"Covered requests:      {all_row['covered_%']}%   (control {all_row['covered_control_%']}%)")
    if has_background:
        print(f"Target requests overlapping a background request: {all_row['req_overlap_%']}%")
        print(f"Pairs made while a background client was active:  {all_row['pairs_overlap_%']}%   (upper bound on wrong pairs)")
    for w in warnings:
        print(f"WARNING: {w}")

    out = args.out
    if out is None:
        parents = {r.parent for r in runs}
        if len(parents) != 1:
            print("Not saved: the runs are in different folders, choose a file with --out")
            return
        out = parents.pop() / f"summary_{settings}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    table.to_csv(out, index=False)
    print(f"Saved: {out}")


if __name__ == "__main__":
    try:
        main()
    except AnalysisError as e:
        sys.exit(f"ERROR: {e}")
