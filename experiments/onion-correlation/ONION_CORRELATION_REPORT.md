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

### 4.4 Who talks to the Guards (packets per peer)

Packets exchanged with the container on one side (client container at the client Guard, onion service container at the service Guard) compared with all other peers (other relays and directory authorities):

| Run | Client Guard: with client container | with other relays | ratio | Service Guard: with service container | with other relays | ratio |
|---|---:|---:|---:|---:|---:|---:|
| run_01 | 808 | 848 | 1.05 | 710 | 772 | 1.09 |
| run_02 | 1053 | 2868 | 2.72 | 808 | 930 | 1.15 |
| run_03 | 843 | 2136 | 2.53 | 732 | 788 | 1.08 |
| run_04 | 717 | 933 | 1.30 | 712 | 1727 | 2.43 |
| run_05 | 837 | 1769 | 2.11 | 1086 | 1720 | 1.58 |

A ratio near 1 is what a Guard that only relays the traffic of its own circuit looks like. Ratios of 2-3 mean the Guard exchanges two to three times more packets with other relays than with its own client, so it also carries traffic of other circuits. This happens at the client Guard in run_02, run_03 and run_05, and at the service Guard in run_04 and run_05. The heavy peers differ from run to run (e.g. run_02: directory authority 10.5.143.166 with 1785 packets and exit 10.5.2.120 with 875).

**Direct link between the two Guards.** The two Guards also exchange packets with each other: 657 packets in run_03, 118 in run_04, 18 in run_02, 8 in run_01 and 6 in run_05 (the same counts appear in both captures). Both captures contain those same packets with the same timestamps, so they can pair trivially. See section 4.6.

### 4.5 Requests without a linked pair

71 of 500 requests are not covered. For all 71, there is no burst of at least 1000 bytes at the service Guard in the request window, and for 46 there is none at the client Guard either; no request has big bursts on both sides without a pair. Even these requests have about 13 smaller bursts per side. So the uncovered requests are explained by the 1000-byte filter, not by the timing rule, and the 85.8% coverage depends on that threshold.

### 4.6 Robustness checks (notebook section 7)

**Minimum burst size** (pooled over the five runs, lag window [-0.05, +0.05] s, direction ignored):

| Min. burst size (bytes) | Client bursts | Paired % | Time-shift % | Cross-run % | Requests covered % | Covered, cross-run % |
|---:|---:|---:|---:|---:|---:|---:|
| 0 | 9259 | 56.3 | 2.8 | 4.1 | 100.0 | 10.8 |
| 300 | 7603 | 60.2 | 2.6 | 3.9 | 100.0 | 8.2 |
| 500 | 7602 | 60.2 | 2.6 | 3.9 | 100.0 | 8.2 |
| 1000 (reference) | 1508 | 57.9 | 1.5 | 2.6 | 85.8 | 3.1 |
| 1500 | 87 | 14.9 | 0.0 | 2.0 | 1.2 | 0.1 |

Lowering the threshold to 300-500 bytes raises request coverage from 85.8% to 100%, but also raises the cross-run control from 3.1% to 8.2%, so the gap between real data and control stays large (100% vs 8.2%). The share of paired bursts barely changes (57.9% to 60.2%). The 300 and 500 byte thresholds give almost the same number of bursts (7603 vs 7602), so very few bursts lie between these sizes. At 1500 bytes only 87 bursts remain, so almost all the bursts of at least 1000 bytes are smaller than 1500 bytes. The 1000-byte threshold therefore sits just below a group of bursts of similar size and removes the many smaller ones; this explains why 71 requests are uncovered with the reference setting.

**Direct Guard-to-Guard link** (pooled over the five runs, direction ignored):

| Traffic | Lag window (s) | Client bursts | Paired % | Time-shift % | Cross-run % | Requests covered % | Covered, cross-run % |
|---|---|---:|---:|---:|---:|---:|---:|
| all packets | [-0.1, +0.5] | 1508 | 58.0 | 8.3 | 11.5 | 85.8 | 14.4 |
| all packets | [-0.05, +0.05] | 1508 | 57.9 | 1.5 | 2.6 | 85.8 | 3.1 |
| without the link | [-0.1, +0.5] | 1405 | 54.9 | 8.0 | 10.5 | 85.8 | 14.4 |
| without the link | [-0.05, +0.05] | 1405 | 54.8 | 1.6 | 2.4 | 85.8 | 3.1 |

Removing the packets exchanged directly between the two Guards removes 103 client bursts (1508 to 1405). The number of pairs falls by about the same amount (about 873 to about 770), so these bursts were all paired, as expected for packets seen at both points. The share of paired bursts falls by about 3 percentage points (57.9% to 54.8% with the +/-0.05 s window), the controls stay at 1.6% and 2.4%, and request coverage is unchanged (85.8%, against 3.1% in the cross-run control). The shared link therefore inflates the pairing slightly but does not explain the correlation: it persists, at about 23 times the cross-run control level, without it. The conservative figures to report are the ones without the link.

## 5. Findings

1. **A timing correlation exists.** About 58% of client bursts find a partner at the service Guard, against 1.5-2.6% in the controls with a +/-0.05 s window (8-12% with the wide window). 85.8% of the requests (429/500) have at least one linked pair, against 3.1% in the cross-run control. This is roughly 20-40 times the chance level.
2. **The lag is tiny.** Paired bursts start within about 10 ms of each other (median about 0 s), so an observer needs no wide time margin.
3. **The 0.5 s margin is unnecessary.** Shrinking the window to +/-0.05 s or +/-0.02 s leaves the real result unchanged (57.9% / 57.8%) and lowers the chance level, so the contrast with the controls becomes clearer. A window of +/-0.05 s is recommended as the reference setting.
4. **Packet direction does not help.** Same, opposite and any direction give almost identical results. A possible reason is that each relay captures every packet both entering and leaving, so bursts of both directions start at the same time at each Guard (hypothesis, not verified).
5. **The result is consistent across methods.** The simplified one-to-one pairing reproduces the 874 pairs and 429/500 coverage of the earlier, more complex analysis.
6. **Run_02 and run_03 have more client bursts, but not because of background traffic between requests.** The client Guard has about 4 bursts per request against about 1.8 at the service Guard, and about twice as many packets (3921 vs 1738, 2979 vs 1520). Almost all the extra bursts fall inside request windows (20 and 0 client bursts between requests), with similar size (median 1.1-1.2 kB). The number of distinct peers is similar in all runs (10-11), but at the client Guard in run_02 and run_03 about 2.5 times more packets go to other relays than to the client container (section 4.4), so the Guard carries traffic of circuits other than the client's. This is consistent with the small network (5 relays, 3 exits, 3 directory authorities that also relay) reusing the same relays in several positions; it is not yet confirmed by circuit data. The lower "paired %" in these runs is a consequence of the unpairable bursts in the denominator; request coverage is less affected (83% and 90%). Run_05 has a one-off event at the service Guard of about 230 bursts around t = 275 s (median 3.3 kB), cause unknown.
7. **The two Guards also see each other.** In run_03 (657 packets) and run_04 (118) the two Guards exchange packets directly, and both captures contain them. Removing these packets lowers the share of paired bursts from 57.9% to 54.8% and leaves request coverage unchanged at 85.8% (section 4.6), so the correlation does not depend on the shared link.
8. **Request coverage is limited by the size filter.** All 71 uncovered requests have no 1000-byte burst at the service Guard (section 4.5).

## 6. Limits

- **One host, one clock.** All containers share the same machine and clock, with almost no network delay. On the real Tor network, latency and clock differences are much larger.
- **A single tiny page** (`Hello Tor`): every request has the same shape, simpler than real browsing.
- **No other users.** One client and one service: the observer only links one pair and does not choose among many candidates.
- **Hand-chosen thresholds** (burst gap, minimum size, lag window). The sensitivity table shows their effect.
- **Shared packets.** In some runs the two Guards communicate directly, so the same packets appear in both captures (section 4.4).
- **Burst size is the frame size** at the Guard (Tor cells and TLS included), not the HTTP payload.
- **Ground truth is at request level.** The server log has 1-second resolution; individual bursts are not labelled, so request attribution uses time windows.
- The percentages are **timing statistics, not precision, recall or accuracy**, and not proof of de-anonymisation. They show that a timing correlation exists in this setup.

## 7. Open checks

Done:
- **Requests without a linked pair:** explained by the 1000-byte filter (section 4.5).
- **Extra traffic in run_02 / run_03:** supported by the packets-per-peer table (section 4.4), not yet confirmed.

Still open:
- **Confirm the cause of the extra traffic.** Run `GETINFO circuit-status` on the client and service containers during a run, and check which relays appear in which position. If the circuits of client and service share relays, this confirms the explanation.
- **Run_05 spike** at the service Guard (about 230 bursts at t = 275 s): check `docker logs --since` on the service container for that time (descriptor upload, circuit rebuild). If the logs are gone, write "cause not determined".
- Not tested: several clients and services at the same time (can the attacker pick the right pair?), varied pages, background load, and the public Tor network.

## 8. How to reproduce

1. Create the network and start the captures as in `EXPERIMENT_RUN_2026-10-02.md` (sections 3-7).
2. **Set the correct `ONION_URL` in `traffic_generator.py`.** The onion address is generated when the onion service is created and changes every time the containers or the volume are recreated, so the address written in the script only works for the original network. Before the first run, replace the value of `ONION_URL` at the top of the script with the address of your own network (including `http://` and the final `/`):
   ```python
   ONION_URL = "http://<your-address>.onion/"
   ```
   The address is the content of the `hostname` file of the hidden service, for example:
   ```bash
   docker exec testing-tor-hs-<name> cat /var/lib/tor/.tor/hs/hostname
   ```
   If the address is wrong or outdated, every request fails with a timeout and the run is useless.
3. Start the two `tcpdump` captures, then generate the requests, always passing the run name (the script's default `RUN_ID` is `run_08`):
   ```bash
   RUN_ID=run_01 NUM_REQUESTS=100 python experiments/onion-correlation/traffic_generator.py
   ```
   Then `docker cp` the PCAPs and the server log, and run `editcap` + `tshark` for the trimmed CSVs.
4. In the configuration cell of `onion_analysis.ipynb`, set the two Guard IPs (client Guard and service Guard of your network), the list of runs and the parameters. The Guard IPs also change when the network is recreated.
5. Run the notebook top to bottom. It writes in `results/`: `onion_summary.csv` (exchange table for Person C), `onion_sensitivity.csv`, `onion_diagnostics.csv`, `onion_uncovered_requests.csv`, `onion_robustness_link.csv`, `onion_robustness_size.csv`.

## 9. Notes for the group report

- Onion observes Guard client + Guard service; clearnet observes Guard client + Exit relay. The numbers are not directly comparable unless features and parameters are aligned with Person A.
- Hand `onion_summary.csv` to Person C with the definitions from section 3.
- Before presenting numbers, confirm which lag window is used (recommended +/-0.05 s) and update section 4.1 if the notebook is rerun with it.
- Mention the shared-link caveat (section 4.4) when comparing with the clearnet experiment, where the observation points are Guard client and Exit relay.
