#!/usr/bin/env python3
"""Link the traffic seen at the clients' guards with the requests seen at the exits.

Attacker view (the only input of the matching):
  entry side - packets between each client and its guard(s) on the Tor port, captured at the guards;
  exit side  - TCP connections from the exits to the web target, captured at the exits.
Ground truth (used only to score the result):
  client_requests_log.csv - which client sent which token and when; server_access.log - what the server got.

Method (docs/METHODOLOGIES.md): packets become bursts (new burst after a gap > 0.1 s or a change of
direction), entry bursts are paired one-to-one with exit bursts by closest start time inside a lag
window, and each exit connection (one HTTP request) is attributed to the client whose bursts it was
paired with. Controls repeat the pairing with the exit trace shifted in time, or taken from another
run, where no real match can exist.

Usage:
  python experiments/clearnet-correlation/correlate.py run_01 [run_02 ...] [--plots]
"""

import argparse
import json
import re
import socket
import struct
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
TOKEN_RE = re.compile(rb"GET /\?t=([0-9a-f]+) ")
SHIFTS = (10.0, 20.0, 30.0, 40.0, 50.0)  # time-shift control, seconds


# --- pcap reading (classic pcap as written by tcpdump -w; no tshark needed) ----------------------

_MAGIC = {b"\xd4\xc3\xb2\xa1": ("<", 1e-6), b"\xa1\xb2\xc3\xd4": (">", 1e-6),
          b"\x4d\x3c\xb2\xa1": ("<", 1e-9), b"\xa1\xb2\x3c\x4d": (">", 1e-9)}
_LINK = {1: (14, 12), 113: (16, 14), 276: (20, 0)}  # linktype -> (header length, ethertype offset)


def read_pcap(path: Path, payload: bool = False):
    """Yield (time, frame_len, src, dst, sport, dport, tcp_flags, payload_len, payload) for IPv4/TCP."""
    data = path.read_bytes()
    if len(data) < 24:
        return
    if data[:4] not in _MAGIC:
        raise ValueError(f"{path} is not a classic pcap file")
    endian, resolution = _MAGIC[data[:4]]
    linktype = struct.unpack_from(endian + "I", data, 20)[0]
    if linktype not in _LINK:
        raise ValueError(f"{path}: unsupported link type {linktype}")
    l2, ethertype_at = _LINK[linktype]
    record = struct.Struct(endian + "IIII")
    off = 24
    while off + 16 <= len(data):
        sec, frac, incl, orig = record.unpack_from(data, off)
        off += 16
        pkt = data[off:off + incl]
        off += incl
        if len(pkt) < l2 + 20 or pkt[ethertype_at:ethertype_at + 2] != b"\x08\x00" or pkt[l2 + 9] != 6:
            continue
        ihl = (pkt[l2] & 0x0F) * 4
        tcp = l2 + ihl
        if len(pkt) < tcp + 14:
            continue
        total_len = struct.unpack_from("!H", pkt, l2 + 2)[0]
        sport, dport = struct.unpack_from("!HH", pkt, tcp)
        thl = (pkt[tcp + 12] >> 4) * 4
        plen = max(0, total_len - ihl - thl)
        yield (sec + frac * resolution, orig, socket.inet_ntoa(pkt[l2 + 12:l2 + 16]),
               socket.inet_ntoa(pkt[l2 + 16:l2 + 20]), sport, dport, pkt[tcp + 13], plen,
               pkt[tcp + thl:tcp + thl + plen] if payload else b"")


# --- loading a run ------------------------------------------------------------------------------

class Run:
    def __init__(self, run_id: str):
        self.id = run_id
        self.dir = RESULTS / run_id
        if not (self.dir / "run_metadata.json").exists():
            sys.exit(f"{self.dir} has no run_metadata.json: run run_experiment.py --run-id {run_id} first")
        self.meta = json.loads((self.dir / "run_metadata.json").read_text())
        self.requests = pd.read_csv(self.dir / "client_requests_log.csv")
        self.server_log = (self.dir / "server_access.log").read_text()
        self.clients = sorted(c["name"] for c in self.meta["clients"])
        self.entry = self._entry_packets()
        self.exit, self.flows = self._exit_packets()
        ok = self.requests[self.requests["success"]]
        self.t0 = float(ok["timestamp_start"].min()) if len(ok) else float(self.requests["timestamp_start"].min())

    def _entry_packets(self) -> pd.DataFrame:
        """Packets on the client <-> guard links. direction is relative to the guard: +1 guard->client."""
        client_by_ip = {c["ip"]: c["name"] for c in self.meta["clients"]}
        router_by_ip = {r["ip_tor"]: name for name, r in self.meta["routers"].items()}
        rows = []
        for cap in self.meta["captures"]:
            if cap["kind"] != "or":
                continue
            for ts, size, src, dst, _sp, _dp, flags, plen, _ in read_pcap(self.dir / cap["file"]):
                if src in client_by_ip:
                    rows.append((ts, size, client_by_ip[src], router_by_ip.get(dst, dst), -1, "up", plen, flags))
                elif dst in client_by_ip:
                    rows.append((ts, size, client_by_ip[dst], router_by_ip.get(src, src), 1, "down", plen, flags))
        cols = ["ts", "size", "client", "guard", "direction", "stream", "payload_len", "flags"]
        return pd.DataFrame(rows, columns=cols).sort_values("ts", ignore_index=True)

    def _exit_packets(self):
        """Packets between exits and the web target, split into TCP connections (flows).
        direction is relative to the exit: +1 exit->web (request side, 'up')."""
        web_ip = self.meta["web"]["ip"]
        rows = []
        for cap in self.meta["captures"]:
            if cap["kind"] != "web":
                continue
            exit_ip = self.meta["routers"][cap["container"]]["ip_web"]
            for ts, size, src, dst, sp, dp, flags, plen, data in read_pcap(self.dir / cap["file"], payload=True):
                if src == exit_ip and dst == web_ip:
                    m = TOKEN_RE.search(data) if plen else None
                    rows.append((ts, size, cap["container"], sp, 1, "up", plen, flags, m.group(1).decode() if m else None))
                elif src == web_ip and dst == exit_ip:
                    rows.append((ts, size, cap["container"], dp, -1, "down", plen, flags, None))
        cols = ["ts", "size", "exit", "exit_port", "direction", "stream", "payload_len", "flags", "token"]
        pk = pd.DataFrame(rows, columns=cols).sort_values("ts", ignore_index=True)
        # one flow per TCP connection; a new SYN from the exit on a reused port starts a new flow
        flow_ids, current, next_id = [], {}, 0
        for exit_name, port, direction, flags in zip(pk["exit"], pk["exit_port"], pk["direction"], pk["flags"]):
            key = (exit_name, port)
            if key not in current or (direction == 1 and flags & 0x02 and not flags & 0x10):
                current[key] = next_id
                next_id += 1
            flow_ids.append(current[key])
        pk["flow"] = flow_ids
        flows = pk.groupby("flow").agg(
            exit=("exit", "first"), exit_port=("exit_port", "first"), t_start=("ts", "min"), t_end=("ts", "max"),
            packets=("ts", "size"), bytes_up=("size", lambda s: int(s[pk.loc[s.index, "direction"] == 1].sum())),
            bytes_down=("size", lambda s: int(s[pk.loc[s.index, "direction"] == -1].sum())),
            token=("token", lambda s: next((t for t in s if isinstance(t, str)), None)))
        truth = self.requests.set_index("token")
        for src, dst in (("client", "true_client"), ("request_id", "request_id"),
                         ("timestamp_start", "req_start"), ("timestamp_end", "req_end")):
            flows[dst] = flows["token"].map(truth[src])
        pk["token"] = pk["flow"].map(flows["token"])
        return pk, flows.reset_index()


# --- bursts and pairing -------------------------------------------------------------------------

def make_bursts(pk: pd.DataFrame, key: str, gap: float, keep: list) -> pd.DataFrame:
    """Consecutive packets of the same `key` and direction, with gaps <= `gap` seconds."""
    pk = pk.sort_values([key, "ts"], kind="stable")
    new = (pk[key] != pk[key].shift()) | (pk["ts"].diff() > gap) | (pk["direction"] != pk["direction"].shift())
    pk = pk.assign(burst=new.cumsum())
    agg = {"t_start": ("ts", "min"), "t_end": ("ts", "max"), "total_size": ("size", "sum"),
           "packet_count": ("size", "size"), "direction": ("direction", "first"), "stream": ("stream", "first")}
    agg.update({c: (c, "first") for c in [key, *keep]})
    b = pk.groupby("burst").agg(**agg).sort_values("t_start", ignore_index=True)
    b["duration_sec"] = b["t_end"] - b["t_start"]
    return b


def pair(entry: pd.DataFrame, exit_: pd.DataFrame, lag: tuple, same_stream: bool, shift: float = 0.0) -> pd.DataFrame:
    """Greedy one-to-one pairing (docs/METHODOLOGIES.md): entry bursts in time order each take the
    closest unused exit burst whose lag = t_exit - t_entry lies in [lag[0], lag[1]]."""
    ex_t = exit_["t_start"].to_numpy() + shift
    order = np.argsort(ex_t, kind="stable")
    ex_t = ex_t[order]
    ex_stream = exit_["stream"].to_numpy()[order]
    used = np.zeros(len(ex_t), dtype=bool)
    out = []
    en_t, en_stream = entry["t_start"].to_numpy(), entry["stream"].to_numpy()
    for i in np.argsort(en_t, kind="stable"):
        lo = np.searchsorted(ex_t, en_t[i] + lag[0], side="left")
        hi = np.searchsorted(ex_t, en_t[i] + lag[1], side="right")
        cand = np.arange(lo, hi)
        cand = cand[~used[cand]]
        if same_stream:
            cand = cand[ex_stream[cand] == en_stream[i]]
        if cand.size:
            j = cand[np.argmin(np.abs(ex_t[cand] - en_t[i]))]
            used[j] = True
            out.append((i, order[j], ex_t[j] - en_t[i]))
    return pd.DataFrame(out, columns=["entry_burst", "exit_burst", "lag"])


def score(pairs: pd.DataFrame, entry: pd.DataFrame, exit_: pd.DataFrame, flows: pd.DataFrame, margin: float) -> dict:
    """Score pairs against the ground truth. A pair is correct when the entry burst belongs to the
    client that really sent the exit burst's request and starts inside that request's time window."""
    p = pairs.join(entry[["client", "t_start", "total_size"]], on="entry_burst")
    p = p.join(exit_[["flow"]], on="exit_burst").join(
        flows.set_index("flow")[["true_client", "req_start", "req_end", "token"]], on="flow")
    p["correct"] = ((p["client"] == p["true_client"]) & (p["t_start"] >= p["req_start"] - margin)
                    & (p["t_start"] <= p["req_end"] + margin))
    known = flows[flows["true_client"].notna()]
    # attribution: each exit flow goes to the client with the most paired bursts (ties: more bytes)
    votes = (p.groupby(["flow", "client"]).agg(n=("lag", "size"), b=("total_size", "sum")).reset_index()
             .sort_values(["flow", "n", "b"], ascending=[True, False, False]).drop_duplicates("flow"))
    predicted = known["flow"].map(votes.set_index("flow")["client"])
    correct_flows = p.loc[p["correct"], "flow"].unique()
    return {
        "pairs": len(p),
        "pairs_correct": int(p["correct"].sum()),
        "precision_%": 100 * p["correct"].mean() if len(p) else float("nan"),
        "entry_paired_%": 100 * len(p) / len(entry) if len(entry) else float("nan"),
        "exit_paired_%": 100 * len(p) / len(exit_) if len(exit_) else float("nan"),
        "requests_linked_%": 100 * known["flow"].isin(p["flow"]).mean() if len(known) else float("nan"),
        "requests_linked_correctly_%": 100 * known["flow"].isin(correct_flows).mean() if len(known) else float("nan"),
        "attributed_%": 100 * predicted.notna().mean() if len(known) else float("nan"),
        "attribution_accuracy_%": 100 * (predicted == known["true_client"]).mean() if len(known) else float("nan"),
        "lag_median_ms": 1000 * p.loc[p["correct"], "lag"].median() if p["correct"].any() else float("nan"),
        "lag_p95_abs_ms": 1000 * p.loc[p["correct"], "lag"].abs().quantile(0.95) if p["correct"].any() else float("nan"),
        "_pairs": p,
        "_predicted": predicted,
    }


# --- analysis of one run --------------------------------------------------------------------------

def bursts_for(run: Run, args, min_entry: int | None = None):
    entry = make_bursts(run.entry, "client", args.gap, keep=["guard"])
    entry = entry[entry["total_size"] >= (args.min_entry_bytes if min_entry is None else min_entry)].reset_index(drop=True)
    exit_ = make_bursts(run.exit, "flow", args.gap, keep=["exit", "token"])
    exit_ = exit_[exit_["total_size"] >= args.min_exit_bytes].reset_index(drop=True)
    return entry, exit_


def analyse(run: Run, args, others: list) -> tuple[dict, dict]:
    entry, exit_ = bursts_for(run, args)
    lag, same = (args.lag_min, args.lag_max), args.direction == "same"
    real = score(pair(entry, exit_, lag, same), entry, exit_, run.flows, args.margin)

    shifted = [score(pair(entry, exit_, lag, same, shift=s), entry, exit_, run.flows, args.margin) for s in SHIFTS]
    cross = []
    for other in others:  # exit trace of another run, aligned on the first request
        o_entry, o_exit = bursts_for(other, args)
        cross.append(score(pair(entry, o_exit, lag, same, shift=run.t0 - other.t0), entry, o_exit, other.flows, args.margin))

    known = run.flows[run.flows["true_client"].notna()]
    ok = run.requests[run.requests["success"]]
    row = {
        "run": run.id,
        "clients": len(run.clients),
        "requests_ok": len(ok),
        "requests_failed": int((~run.requests["success"]).sum()),
        "requests_in_server_log": int(sum(t in run.server_log for t in ok["token"])),
        "requests_seen_at_exit": len(known),
        "exit_flows_unmatched": int(run.flows["true_client"].isna().sum()),
        "entry_bursts": len(entry),
        "exit_bursts": len(exit_),
        **{k: v for k, v in real.items() if not k.startswith("_")},
        "control_shift_entry_paired_%": np.mean([c["entry_paired_%"] for c in shifted]),
        "control_shift_requests_linked_%": np.mean([c["requests_linked_%"] for c in shifted]),
        "control_shift_attribution_accuracy_%": np.mean([c["attribution_accuracy_%"] for c in shifted]),
        "control_cross_entry_paired_%": np.mean([c["entry_paired_%"] for c in cross]) if cross else float("nan"),
        "control_cross_requests_linked_%": np.mean([c["requests_linked_%"] for c in cross]) if cross else float("nan"),
        "chance_attribution_%": 100 / len(run.clients),
    }
    return row, {"entry": entry, "exit": exit_, "real": real, "known": known}


def sensitivity(run: Run, args) -> pd.DataFrame:
    rows = []
    for min_entry in (0, 300, 600, 1000):
        for lag in ((-0.1, 0.5), (-0.05, 0.05), (-0.02, 0.02)):
            for direction in ("same", "any"):
                entry, exit_ = bursts_for(run, args, min_entry=min_entry)
                r = score(pair(entry, exit_, lag, direction == "same"), entry, exit_, run.flows, args.margin)
                c = [score(pair(entry, exit_, lag, direction == "same", shift=s), entry, exit_, run.flows, args.margin)
                     for s in SHIFTS]
                rows.append({"run": run.id, "min_entry_bytes": min_entry, "lag_window": f"[{lag[0]:+}, {lag[1]:+}]",
                             "direction": direction, "entry_bursts": len(entry), "pairs": r["pairs"],
                             "precision_%": r["precision_%"], "entry_paired_%": r["entry_paired_%"],
                             "requests_linked_correctly_%": r["requests_linked_correctly_%"],
                             "attribution_accuracy_%": r["attribution_accuracy_%"],
                             "control_shift_entry_paired_%": np.mean([x["entry_paired_%"] for x in c]),
                             "control_shift_requests_linked_%": np.mean([x["requests_linked_%"] for x in c])})
    return pd.DataFrame(rows)


def plot(run: Run, detail: dict, out: Path, seconds: float = 40.0) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    entry, exit_, p = detail["entry"], detail["exit"], detail["real"]["_pairs"]
    t0, t1 = run.t0 - 1, run.t0 - 1 + seconds
    lanes = {c: i for i, c in enumerate(run.clients)}
    exit_lane = len(lanes) + 1
    fig, ax = plt.subplots(figsize=(13, 2.2 + 0.6 * len(lanes)))
    e = entry[(entry["t_start"] >= t0) & (entry["t_start"] <= t1)]
    for stream, color in (("up", "tab:blue"), ("down", "tab:purple")):
        s = e[e["stream"] == stream]
        ax.scatter(s["t_start"] - t0, s["client"].map(lanes), s=8 + s["total_size"] / 60, color=color, alpha=0.7,
                   label=f"entry burst ({'client to guard' if stream == 'up' else 'guard to client'})")
    x = exit_[(exit_["t_start"] >= t0) & (exit_["t_start"] <= t1)]
    ax.scatter(x["t_start"] - t0, np.full(len(x), exit_lane), s=18, color="tab:orange", marker="s", label="exit burst")
    for _, r in p[(p["t_start"] >= t0) & (p["t_start"] <= t1)].iterrows():
        xe = exit_.loc[r["exit_burst"], "t_start"]
        ax.plot([r["t_start"] - t0, xe - t0], [lanes[r["client"]], exit_lane],
                color="tab:green" if r["correct"] else "tab:red", lw=0.8, alpha=0.6)
    for _, rq in run.requests[(run.requests["timestamp_start"] <= t1) & (run.requests["timestamp_end"] >= t0)].iterrows():
        ax.axvspan(rq["timestamp_start"] - t0, rq["timestamp_end"] - t0, color="0.85", zorder=0)
    ax.set_yticks(list(lanes.values()) + [exit_lane], [c.replace("testing-tor-", "") + " (guard side)" for c in lanes] + ["exits (web side)"])
    ax.set_xlim(0, seconds)
    ax.set_xlabel(f"seconds since {seconds:.0f}-s window start (grey: request in flight, green/red: correct/wrong pair)")
    ax.set_title(f"{run.id}: entry vs exit bursts")
    fig.legend(loc="lower center", fontsize=8, ncol=3, frameon=False)
    fig.tight_layout(rect=(0, 0.1, 1, 1))
    fig.savefig(out / "timeline.png", dpi=130)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(7, 3.5))
    bins = np.linspace(p["lag"].min() * 1000 if len(p) else -50, p["lag"].max() * 1000 if len(p) else 50, 41)
    ax.hist(p.loc[p["correct"], "lag"] * 1000, bins=bins, color="tab:green", alpha=0.8, label="correct pairs")
    ax.hist(p.loc[~p["correct"], "lag"] * 1000, bins=bins, color="tab:red", alpha=0.6, label="wrong pairs")
    ax.set_xlabel("lag = t_exit - t_entry (ms)")
    ax.set_ylabel("pairs")
    ax.set_title(f"{run.id}: lag of paired bursts")
    ax.legend()
    fig.tight_layout()
    fig.savefig(out / "lags.png", dpi=130)
    plt.close(fig)


# --- main -----------------------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("runs", nargs="+", help="run ids under experiments/clearnet-correlation/results/")
    p.add_argument("--gap", type=float, default=0.1, help="burst gap in seconds (default 0.1)")
    p.add_argument("--min-entry-bytes", type=int, default=1000,
                   help="drop guard-side bursts smaller than this (default 1000, as in docs/METHODOLOGIES.md)")
    p.add_argument("--min-exit-bytes", type=int, default=0,
                   help="drop exit-side bursts smaller than this (default 0: HTTP packets are 66-260 bytes)")
    p.add_argument("--lag-min", type=float, default=-0.05, help="lower bound of t_exit - t_entry (s, default -0.05)")
    p.add_argument("--lag-max", type=float, default=0.05, help="upper bound of t_exit - t_entry (s, default +0.05)")
    p.add_argument("--direction", choices=("same", "any"), default="same",
                   help="pair only bursts going the same way (client->server with client->server), or any")
    p.add_argument("--margin", type=float, default=0.05, help="tolerance around a request window when scoring (s)")
    p.add_argument("--sensitivity", action="store_true", help="also try other thresholds and lag windows")
    p.add_argument("--plots", action="store_true", help="write timeline.png and lags.png for each run")
    return p.parse_args()


def fmt(df: pd.DataFrame) -> str:
    return df.to_string(index=False, float_format=lambda v: f"{v:.1f}")


def main() -> int:
    args = parse_args()
    runs = [Run(r) for r in args.runs]
    rows = []
    for run in runs:
        row, detail = analyse(run, args, [o for o in runs if o is not run])
        rows.append(row)
        out = run.dir / "analysis"
        out.mkdir(exist_ok=True)
        detail["entry"].to_csv(out / "entry_bursts.csv", index=False)
        detail["exit"].to_csv(out / "exit_bursts.csv", index=False)
        flows = run.flows.assign(predicted_client=run.flows["flow"].map(
            dict(zip(detail["known"]["flow"], detail["real"]["_predicted"]))))
        flows.to_csv(out / "exit_flows.csv", index=False)
        detail["real"]["_pairs"].to_csv(out / "pairs.csv", index=False)
        (out / "summary.json").write_text(json.dumps({k: (None if isinstance(v, float) and np.isnan(v) else v)
                                                      for k, v in row.items()}, indent=2, default=float))
        if args.plots:
            plot(run, detail, out)
        if args.sensitivity:
            sens = sensitivity(run, args)
            sens.to_csv(out / "sensitivity.csv", index=False)
            print(f"\nSensitivity for {run.id} (saved to {out.relative_to(HERE)}/sensitivity.csv):")
            print(fmt(sens.drop(columns=["run"])))

    table = pd.DataFrame(rows)
    show = ["run", "clients", "requests_ok", "requests_seen_at_exit", "entry_bursts", "exit_bursts", "pairs",
            "precision_%", "entry_paired_%", "control_shift_entry_paired_%", "control_cross_entry_paired_%",
            "requests_linked_correctly_%", "control_shift_requests_linked_%", "attribution_accuracy_%",
            "chance_attribution_%", "lag_median_ms"]
    print(f"\nParameters: gap {args.gap} s, lag [{args.lag_min:+}, {args.lag_max:+}] s, direction {args.direction}, "
          f"min entry {args.min_entry_bytes} B, min exit {args.min_exit_bytes} B")
    print(fmt(table[show].T.reset_index().rename(columns={"index": "metric"})))
    if len(runs) > 1:
        table.to_csv(RESULTS / "clearnet_summary.csv", index=False)
        print(f"\nPer-run summary saved to {(RESULTS / 'clearnet_summary.csv').relative_to(HERE)}")
    for run in runs:
        print(f"Details for {run.id}: {(run.dir / 'analysis').relative_to(HERE)}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
