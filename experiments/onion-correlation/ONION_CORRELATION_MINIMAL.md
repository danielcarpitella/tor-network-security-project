# Can the traffic of an onion service be linked from its Guard and the client's Guard?

**Short answer: yes, in this laboratory network.** An observer who sees only the Guard used by the client and the Guard used by the onion service can link the traffic at the two points by timing alone, far above chance.

Notebook: `onion_analysis_minimal.ipynb`. Full analysis (sensitivity, diagnostics, extra checks): `ONION_CORRELATION_REPORT.md`.

## Setup

- Private Tor network in Docker: 3 directory authorities, 5 relays, 3 exits, 1 client, 1 onion service (page `<h1>Hello Tor</h1>`).
- The client Guard and the service Guard are different relays. The observer captures only the packets on OR port 9001 of those two relays (`tcpdump` + `tshark`).
- 5 runs of 100 HTTP requests (one every 2-5 s). All 500 requests succeeded.
- The client request log is used only to check the result (which requests are covered), not as input of the attacker.

## Method

1. **Bursts.** Packets of each Guard are grouped into bursts (same direction, gap of at most 0.1 s). Bursts below 1000 bytes are discarded. Only the start time of a burst is used.
2. **Pairing.** Each client burst is paired with the closest unused service burst that starts within +/-0.05 s. One service burst is used at most once.
3. **Control.** The same pairing is done with the service trace of a *different* run, aligned to the start of the run (mean over the other 4 runs). No real match is possible there, so the result shows how many pairs appear by chance.
4. **Request coverage.** A request is covered if at least one pair falls in its time interval (+/-0.05 s).

The two Guards also exchange packets with each other (657 packets in run_03, 118 in run_04). Those packets appear in both captures with the same timestamps and would pair trivially, so they are removed before the analysis.

## Result (pooled over the five runs)

| | Real | Control (other run) |
|---|---:|---:|
| Client bursts that find a partner | 54.8% (of 1405 bursts) | 2.4% |
| Requests with at least one pair | 85.8% (429/500) | 3.1% |

Paired bursts start within about 10 ms of each other (median lag about 0 s). The per-run values are printed by the notebook (`onion_minimal_summary.csv`).

## What it means

- About 55% of the client bursts have a partner at the service Guard, against 2.4% by chance: roughly 23 times more.
- 85.8% of the requests can be recognised at both Guards, against 3.1% by chance.
- So the traffic of a client and of an onion service can be associated when the observer controls or watches both Guards. This is the situation described in the Tor threat model for an observer at both ends of the circuit.

## Limits

- One host and one clock: no real network delay, much easier than the public Tor network.
- One tiny page and a single client and service: every request looks the same, and there is nothing to choose among. The result shows that a correlation exists, not that an attacker could pick the right pair among many.
- Thresholds (0.1 s gap, 1000 bytes, +/-0.05 s) were chosen by hand.
- The percentages are timing statistics, not precision, recall or accuracy, and not proof of de-anonymisation in real conditions.
- In runs 02 and 03 the client Guard carries more traffic than expected (cause not confirmed), which lowers the share of paired bursts in those runs.

## How to reproduce

1. Create the network and capture the two Guards as in `EXPERIMENT_RUN_2026-10-02.md`.
2. In `traffic_generator.py` replace `ONION_URL` with the onion address of your network (it changes whenever the containers are recreated; it is the content of the `hostname` file in `/var/lib/tor/.tor/hs/` of the onion service container). Run it with `RUN_ID=run_0X NUM_REQUESTS=100`.
3. In the first code cell of the notebook set the two Guard IPs and the list of runs, then run the notebook from top to bottom.
