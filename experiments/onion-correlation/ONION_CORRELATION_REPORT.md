# Onion service correlation: client Guard vs service Guard

Part of the Tor network-security project (experiment `onion-correlation/`).
Notebook: `onion_analysis.ipynb`. Traffic generator: `traffic_generator.py`. Raw run diary: `EXPERIMENT_RUN_2026-10-02.md`.

## 1. Question and attacker model

If an observer sees only the Guard relay used by the client and the Guard relay used by the onion service, can the traffic at the two points be linked by timing alone?

The observer never sees the client container, the onion service container, or the HTTP content. The server log and the client request log are used **only as ground truth** to check the result, not as attacker input.

## 2. Setup

- Private Tor network in Docker (`testing-tor`, subnet 10.5.0.0/16): 3 directory authorities, 5 relays, 3 exits, 1 client, 1 onion service.
- Onion service serves `<h1>Hello Tor</h1>` through a Python HTTP server on `127.0.0.1:80`.
- Client Guard: `relayngpufjeq` (10.5.184.229). Service Guard: `relaysgufrlnh` (10.5.237.145). The two Guards are distinct.
- 5 runs (`run_01`-`run_05`), each with 100 HTTP requests through the client's SOCKS port, one every 2-5 s (`traffic_generator.py`). All 500 requests succeeded (HTTP 200) and are confirmed by the server log.
- During each run `tcpdump` captured TCP port 9001 on both Guards. The capture was trimmed to the request window +/- 5 s and exported with `tshark` to CSV (time, frame length, source IP, destination IP).

## 3. Method (what the notebook does)

1. **Bursts.** Packets of each Guard are grouped into bursts: consecutive packets in the same direction with gaps of at most 0.1 s. Bursts smaller than 1000 bytes are discarded.
2. **Pairing.** Client bursts are scanned in time order; each takes the closest still-unused service burst whose start lies within a lag window (service start minus client start). The pairing is strictly one-to-one.
3. **Controls** (cannot contain a real correspondence):
   - *time shift*: service trace of the same run moved by 10-50 s (mean over 5 shifts);
   - *cross-run*: service trace of another run j, aligned to the first request of client run i (mean over the other 4 runs). It keeps a realistic burst structure.
4. **Request coverage.** A request counts as covered if at least one pair has its client burst inside the request interval (plus the lag margins). Computed the same way for real data and controls.
5. **Sensitivity** to the lag window ([-0.1, +0.5], +/-0.05, +/-0.02 s) and to packet direction (any / same / opposite).
6. **Diagnostics** of bursts inside and between request windows, and of uncovered requests.

Parameters (burst gap, minimum size, lag window) are hand-chosen, not calibrated.

## 4. Results

### 4.1 Per run, lag window [-0.1, +0.5] s

| Run | Client bursts | Service bursts | Pairs | Paired % | Time-shift % | Cross-run % | Requests covered | Covered (shift) | Covered (cross-run) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| run_01 | 179 | 173 | 167 | 93.3 | 9.7 | 12.6 | 82/100 | 8.8 | 11.0 |
| run_02 | 413 | 199 | 177 | 42.9 | 8.1 | 11.8 | 83/100 | 15.8 | 17.5 |
| run_03 | 400 | 198 | 180 | 45.0 | 6.1 | 9.9 | 90/100 | 12.2 | 13.2 |
| run_04 | 218 | 314 | 174 | 79.8 | 10.4 | 15.0 | 86/100 | 11.4 | 15.5 |
| run_05 | 298 | 493 | 176 | 59.1 | 9.3 | 10.1 | 88/100 | 13.2 | 14.5 |
| **All** | 1508 | 1377 | 874 | 58.0 | 8.3 | 11.5 | 429/500 | 61.4 | 71.8 |

"Paired %" is the share of client bursts that find a partner. The pooled median lag is -0.0008 s.

### 4.2 Pooled sensitivity

| Lag window (s) | Direction | Paired % | Time-shift % | Cross-run % | Requests covered % | Covered, cross-run % |
|---|---|---:|---:|---:|---:|---:|
| [-0.1, +0.5] | any / same / opposite | 58.0 | 8.3 | 11.5 | 85.8 | 14.4 |
| [-0.05, +0.05] | any / same / opposite | 57.9 | 1.5 | 2.6 | 85.8 | 3.1 |
| [-0.02, +0.02] | any | 57.8 | 0.8 | 1.5 | 85.8 | 1.8 |
| [-0.02, +0.02] | same / opposite | 57.8 / 57.7 | 0.8 | 1.5 | 85.8 | 1.7 / 1.8 |

### 4.3 Diagnostics (bursts per request window)

| Run | Client bursts per request | Service bursts per request | Client bursts between requests | Service bursts between requests | Packets client / service |
|---|---:|---:|---:|---:|---:|
| run_01 | 1.79 | 1.67 | 0 | 6 | 1656 / 1482 |
| run_02 | 3.93 | 1.79 | 20 | 20 | 3921 / 1738 |
| run_03 | 4.00 | 1.80 | 0 | 18 | 2979 / 1520 |
| run_04 | 2.18 | 3.14 | 0 | 0 | 1650 / 2439 |
| run_05 | 2.95 | 2.63 | 3 | 230 | 2606 / 2806 |

## 5. Findings

1. **A timing correlation exists.** About 58% of client bursts find a partner at the service Guard, against 1.5-2.6% in the controls with a +/-0.05 s window (8-12% with the wide window). 85.8% of the requests (429/500) have at least one linked pair, against 3.1% in the cross-run control. This is roughly 20-40 times the chance level.
2. **The lag is tiny.** Paired bursts start within about 10 ms of each other (median about 0 s), so an observer needs no wide time margin.
3. **The 0.5 s margin is unnecessary.** Shrinking the window to +/-0.05 s or +/-0.02 s leaves the real result unchanged (57.9% / 57.8%) and lowers the chance level, so the contrast with the controls becomes clearer. A window of +/-0.05 s is recommended as the reference setting.
4. **Packet direction does not help.** Same, opposite and any direction give almost identical results. A possible reason is that each relay captures every packet both entering and leaving, so bursts of both directions start at the same time at each Guard (hypothesis, not verified).
5. **The result is consistent across methods.** The simplified one-to-one pairing reproduces the 874 pairs and 429/500 coverage of the earlier, more complex analysis.
6. **Run_02 and run_03 have more client bursts, but not because of background traffic.** The client Guard has about 4 bursts per request against about 1.8 at the service Guard, and about twice as many packets (3921 vs 1738, 2979 vs 1520). Almost all the extra bursts fall inside request windows (20 and 0 client bursts between requests), with similar size (median 1.1-1.2 kB). The number of distinct peers is similar in all runs (10-11). The lower "paired %" in these runs is a consequence of the unpairable bursts in the denominator; request coverage is less affected (83% and 90%). Run_05 has a one-off event at the service Guard of about 230 bursts around t = 275 s (median 3.3 kB).

## 6. Limits

- **One host, one clock.** All containers share the same machine and clock, with almost no network delay. On the real Tor network, latency and clock differences are much larger.
- **A single tiny page** (`Hello Tor`): every request has the same shape, simpler than real browsing.
- **No other users.** One client and one service: the observer only links one pair and does not choose among many candidates.
- **Hand-chosen thresholds** (burst gap, minimum size, lag window). The sensitivity table shows their effect.
- **Burst size is the frame size** at the Guard (Tor cells and TLS included), not the HTTP payload.
- **Ground truth is at request level.** The server log has 1-second resolution; individual bursts are not labelled, so request attribution uses time windows.
- The percentages are **timing statistics, not precision, recall or accuracy**, and not proof of de-anonymisation. They show that a timing correlation exists in this setup.

## 7. Open checks (not verified yet)

Section 6 of the notebook prints the data for the first two checks.

- **Cause of the extra client bursts in run_02 / run_03.** Hypothesis: with few relays, the client Guard may also appear elsewhere on the circuit (e.g. rendezvous point or middle relay) and carry both halves of the traffic. Check the packets-per-peer table in the notebook and `GETINFO circuit-status` on both containers during a run.
- **Requests without a linked pair** (10-18 per run, 71 in total). The notebook lists them with the number of bursts at each Guard in their window, with and without the 1000-byte filter. If many have no big burst on one side, try a lower `MIN_BURST_BYTES`.
- **Run_05 spike** at the service Guard: check `docker logs --since` on the service container for that time (descriptor upload, circuit rebuild).
- Not tested: several clients and services at the same time (can the attacker pick the right pair?), varied pages, background load, and the public Tor network.

## 8. How to reproduce

1. Create the network and start the captures as in `EXPERIMENT_RUN_2026-10-02.md` (sections 3-7): servers, `tcpdump` on both Guards, `RUN_ID=run_0X NUM_REQUESTS=100 python experiments/onion-correlation/traffic_generator.py`, `docker cp` of the PCAPs and the server log, `editcap` + `tshark` for the trimmed CSVs.
2. Set the two Guard IPs, the run list and the parameters in the configuration cell of `onion_analysis.ipynb` (the Guard IPs change when the network is recreated).
3. Run the notebook top to bottom. It writes in `results/`: `onion_summary.csv` (exchange table for Person C), `onion_sensitivity.csv`, `onion_diagnostics.csv`, `onion_uncovered_requests.csv`.

## 9. Notes for the group report

- Onion observes Guard client + Guard service; clearnet observes Guard client + Exit relay. The numbers are not directly comparable unless features and parameters are aligned with Person A.
- Hand `onion_summary.csv` to Person C with the definitions from section 3.
- Before presenting numbers, confirm which lag window is used (recommended +/-0.05 s) and update section 4.1 if the notebook is rerun with it.
