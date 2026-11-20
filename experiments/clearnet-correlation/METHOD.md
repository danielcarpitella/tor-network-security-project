# Clearnet correlation: client guard vs exit

Can an observer who sees only the client's guard and the exit relay tell **who sent which request**,
by timing alone?

- `run_experiment.py`: runs one experiment (captures + requests) and saves everything in `results/<run_id>/`.
- `correlate.py`: links guard-side traffic to exit-side requests, scores the result against the ground truth, and runs the controls.

## Observation model

| | What it is | Used for |
|---|---|---|
| **Entry side** (attacker) | Packets between each client and its guard(s) on the Tor port 9001, captured inside the guards. With conflux a client uses two guards, so both links are included. | matching |
| **Exit side** (attacker) | TCP connections from the exits to the web target (`172.30.0.10:80`), captured on the exits' clearnet interface. One connection = one HTTP request. | matching |
| **Ground truth** | `client_requests_log.csv`: which client sent which token, and when. `server_access.log`: the server's own log of the tokens it received. | scoring only |

Every request goes to `http://172.30.0.10/?t=<random token>`. The exit sees the token in the URL, but the token
doesn't reveal who sent it. Only the client log, which the attacker doesn't have, connects a token to a client.
This gives an independent ground truth for each request, so precision and recall can be computed properly
(as `docs/METHODOLOGIES.md` requires).

## How to run

Prerequisites: the lab is up (`./shared-base/lab.sh up`), `./shared-base/lab.sh status` shows 3/3 DAs in the
consensus, and the project venv is active.

```bash
# one run = N requests per client, 2-5 s apart (about 6 minutes for 100)
python experiments/clearnet-correlation/run_experiment.py --run-id run_01
# ...repeat with run_02 ... run_05, then analyse them together (enables the cross-run control)
python experiments/clearnet-correlation/correlate.py run_01 run_02 run_03 run_04 run_05 --plots --sensitivity
```

**Several clients ("who sent what").** With one client the question is only *which request*. To make the
attacker choose among senders, add clients before a run. `run_experiment.py` uses every client that has a
SOCKS port, and they send at the same time:

```bash
python cli/main.py container add-client --port 9060
python cli/main.py container add-client --port 9061
# wait ~1 min for them to bootstrap, then run as above
```

Useful options: `--requests`, `--min-sleep/--max-sleep`, `--clients name1,name2`, `--seed` (run_experiment.py);
`--min-entry-bytes`, `--lag-min/--lag-max`, `--direction same|any`, `--gap` (correlate.py). `--help` lists them all.

## What a run folder contains

```text
results/<run_id>/
  run_metadata.json          clients, router IPs, capture list, parameters, timestamps
  topology_before/after.json each client's guards and circuits (from its ControlPort), before and after
  consensus.txt              consensus at start (which routers existed, with flags)
  client_requests_log.csv    ground truth: client, request_id, token, start/end, status
  server_access.log          ground truth: the web server's log for the run
  captures/<router>_or.pcap  port 9001 at every router (headers only, snaplen 128)
  captures/<exit>_web.pcap   exit <-> web target, full packets
  analysis/                  written by correlate.py:
    entry_bursts.csv, exit_bursts.csv, exit_flows.csv (with predicted_client), pairs.csv,
    summary.json, sensitivity.csv, timeline.png, lags.png
```

`results/` is ignored by git. Multi-run analysis also writes `results/clearnet_summary.csv` for the comparison
with the onion experiment.

## Method

1. **Bursts.** On each side, consecutive packets in the same direction with gaps of at most 0.1 s form a burst.
   Guard-side bursts are grouped per client, exit-side bursts per TCP connection. Size is the captured frame length.
2. **Pairing.** Guard-side bursts, in time order, each take the closest unused exit-side burst whose
   `lag = t_exit - t_entry` lies in the lag window, one-to-one. By default only bursts going the same way are
   paired (client-to-guard with exit-to-server, guard-to-client with server-to-exit).
3. **Attribution.** Each exit connection (one request) is attributed to the client with the most paired bursts,
   with ties broken by bytes.
4. **Controls.** The same pairing is repeated with the exit trace shifted by 10-50 s (*time shift*), and against
   the exit trace of another run aligned on its first request (*cross-run*). No real match exists there, so they
   show the chance level.

### Metrics (all computed against the ground truth)

| Metric | Definition |
|---|---|
| `pairs` | number of guard-burst / exit-burst pairs |
| `precision_%` | pairs that are correct / all pairs. A pair is **correct** when the guard burst belongs to the client that really sent that exit connection's request and starts inside the request's time window (start to end, ±0.05 s). Wrong pairs are the false positives. |
| `requests_linked_correctly_%` | requests seen at the exit with at least one correct pair (recall at request level) |
| `attribution_accuracy_%` | requests attributed to the right client / requests seen at the exit. Unattributed requests count as wrong. Compare with `chance_attribution_%` = 100 / number of clients. |
| `entry_paired_%` | guard-side bursts that found a partner. This matches Elena's "paired %", and the control columns give its chance level. |
| `lag_median_ms`, `lag_p95_abs_ms` | timing of the correct pairs |

## Parameters and differences from the onion analysis

Same as `docs/METHODOLOGIES.md` and Elena's reference setting: 0.1 s burst gap, new burst on a change of
direction, all frames counted (ACKs included), greedy one-to-one pairing by closest start, ±0.05 s lag window,
1000-byte minimum burst size on the guard side.

These are different, and should be stated when comparing the two scenarios:

- **Guard side = only the client's link.** The onion analysis used all traffic at the guard except the
  guard-to-guard link. Here only packets between the client and its guard(s) count, since a guard can tell its
  connections apart.
- **Exit side = HTTP to the destination, with no size threshold.** These packets are 66-260 bytes, so a
  1000-byte threshold would remove all of them.
- **Direction matched by default** (`--direction same`). `--sensitivity` also reports `any`.
- **Supervised metrics.** The per-request token is an independent ground truth, so precision, recall and
  attribution accuracy are defined here. The onion analysis reports only timing statistics.

### Lag: two modes, so keep the window at ±0.05 s

In the test run (`test_100`, 1 client, 100 requests) the correct pairs have two lag values. About −1 ms covers
the request itself: TCP handshake, GET and response at the exit against the cells at the guard. About −41 ms
covers the end of the connection: the exit's TCP teardown comes 41 ms before a ~1.1 KB burst at the guard. With
the 1000-byte threshold almost every surviving guard burst is of the second kind. A ±0.02 s window therefore
links only 13% of the requests, while ±0.05 s links 96%. The cause of the 41 ms is not confirmed; it's close to
Linux's 40 ms delayed-ACK timer. Re-check `lags.png` on every run before narrowing the window.

## Limits

- One host and one clock: the lag is a few milliseconds, far smaller than on the real Tor network.
- A tiny page (33 bytes) with every request the same shape; no other users unless clients are added.
- Conflux is on, as Tor's default: a request can use either of two guards, both observed here.
- Tor's padding and directory traffic appear on the guard side as unpaired bursts. The 1000-byte threshold
  removes most single-cell padding bursts.
- Thresholds are chosen by hand: check the `--sensitivity` table before reporting a number.
