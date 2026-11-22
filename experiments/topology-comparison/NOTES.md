# Topology comparison: working notes

Working notes for the third contribution: where the work stands, observations worth keeping for the
report, and the plan for the clearnet scenario. How the scripts work is described at the top of each script.

## Status (2026-11-22)

Done:

- `generator.py`: sends N requests through the Tor client's SOCKS port and logs start/end time of each.
- `run_experiment.py`: records one run (finds the Guards, tcpdump on both, requests, export to CSV, `run_info.json`).
  Onion scenario only.
- `analyze.py`: compares the runs of a test; real pairs vs cross-run control (method of
  `experiments/onion-correlation/onion_analysis_minimal.ipynb`).
- `run_test.sh`: runs a whole test (`run_01 ... run_N`) and the analysis; skips runs already complete.
- `shared-base/lab.sh`: `onion-web` serves `/10k.bin`, `/100k.bin`, `/1m.bin`; new command `separate-guards`.
- Smoke tests on the real lab passed (5 requests: 5/5 OK, 5/5 confirmed by the server).

Next:

- Onion baseline: `./experiments/topology-comparison/run_test.sh baseline 5` (5 runs x 100 requests, ~35 min).
  Expected roughly like the onion-correlation result (~85% of requests covered vs a few % in the control).
- Clearnet support (plan below).
- Optional check: run `analyze.py` on Elena's runs (command in its EXAMPLES); it should give her numbers.

Note: `results/` is not in git. Results recorded on one PC must be copied by hand to another one.

## Observation for the report: Guards are stable, exits are not

- In the onion scenario both observation points are **Guards**. Tor keeps the same Guard for months, on
  purpose, so the entry relay does not keep changing. During a run the rendezvous circuit is replaced after
  ~10 minutes (new middles, new rendezvous point), but every new circuit still starts at the same Guard on
  each side. The observation points stay fixed.
- In the clearnet scenario the second observation point is the **exit**, and Tor picks an exit **per circuit,
  on purpose**, so that no single exit sees a user's traffic for long. Circuits are replaced every ~10 minutes,
  so the exit can change in the middle of a run.
- Consequence for real Tor: an attacker running an exit sees a given user only **occasionally** (when Tor
  happens to pick that exit). An attacker who is the **onion service's Guard** sees **all** the traffic of that
  service, for months. In the onion scenario being in position is harder, but much more valuable.
- Consequence for the lab: in clearnet the exit must be pinned (see decision 1), otherwise the capture can be
  on the wrong relay. In onion this is not needed; `run_experiment.py` only checks after each run that the
  rendezvous circuits still start at the same Guards (WARNING otherwise).

## Clearnet plan

### How a clearnet request travels

```
your PC (WSL)                     Tor network (Docker)                          clearnet-target network
generator.py ──SOCKS 9050──► client ══► guard ══► middle ══► exit ──plain HTTP──► web server 172.30.0.10:80
                                   └──── port 9001: Tor cells, encrypted ────┘    └─ port 80: normal TCP ─┘
```

The generator asks the client's SOCKS port to connect to `172.30.0.10:80`; the client builds
guard → middle → exit and asks the exit to open that connection. The client container cannot reach
`172.30.0.10` directly: only the exits are connected to the `clearnet-target` network (`prepare.sh`).

### Differences from the onion scenario

| | Onion | Clearnet |
|---|---|---|
| 2nd observation point | service Guard (stable for months) | exit: one per circuit among 3, can change every ~10 min |
| Client Guard | one | possibly two at once, because of conflux (exit traffic only) |
| Traffic at the 2nd point | only Tor cells (traffic never leaves Tor) | Tor cells (port 9001) **and** plain HTTP (port 80) |
| Web server log | `/tmp/http_server.log` in the onion service | `docker logs clearnet-target-web` |
| Test files 10k/100k/1m | created by `lab.sh onion-web` | do not exist yet on the clearnet server |

### Decisions

1. **Pin the exit** (recommended): `ExitNodes <exit nickname>` in the client's torrc, same mechanism as
   `separate-guards`. One fixed relay per side, like the onion case. It does not change the attacker model:
   the attacker is "whoever watches the exit you use"; pinning makes sure we watch the right one.
   Alternative: capture on all 3 exits (more data, more noise, more complex analysis).

   **Is pinning unrealistic?** Tor does not pin exits, but the attack has two separate factors:
   `P(attack works) = P(attacker in position) x P(correlation works | attacker in position)`.
   The experiment measures only the second factor, in both scenarios: in onion too the attacker is placed
   exactly on the two Guards in use (that feels natural only because Guards do not move). Pinning puts
   clearnet on the same footing. Not pinning would not be more realistic, only mixed: with one exit captured
   out of 3, the result would blend "Tor used my exit ~1/3 of the time" (meaningless outside the lab: real Tor
   has over a thousand exits, and an attacker's chance depends on the exit bandwidth it controls) with
   "correlation worked".
   In the report: state that results are conditional on the attacker observing the exit used, and discuss
   the position factor qualitatively (observation above). Optional extra test: one run without pinning that
   only records how often the exit changes during a run, to show the rotation with lab data.
2. **Turn conflux off on the client** (`ConfluxEnabled 0`), so a download is not split across two Guards
   (a capture on one Guard would see only part of it, making clearnet look harder for a reason unrelated to
   the comparison). It affects only exit traffic, so onion runs do not change. Check first that conflux is
   really active (command below).

   Conflux (Tor 0.4.8, 2023): for speed and reliability the client builds two circuits that start at
   different Guards and end at the same exit, and splits or moves the traffic between them; the exit merges it.

   ```
   client ══ guard A ══ middle 1 ══╗
                                   ╠══ exit ── web server
   client ══ guard B ══ middle 2 ══╝
   ```

   A capture on guard A alone would miss what travels through guard B. Same reasoning as pinning the exit:
   the attacker is placed on everything the user's traffic passes through, and the report says so.
   Alternative: keep conflux and capture both client Guards (closer to default Tor, more complex).
   Why check first: in the first smoke test the client had circuits through two first relays
   (`daivbevvpt` x6, `dapgxxnfie` x2), which fits conflux legs but could also be directory traffic; and with
   tiny pages Tor may keep everything on one leg anyway (splitting matters more for big downloads).
3. **Capture both sides at the exit**: `tcp port 9001 or tcp port 80`. The exit is where Tor traffic becomes
   normal traffic again:
   - port 9001 side (towards the middle relay): Tor cells, the same kind of data as at the Guards, so the
     fairest comparison with onion;
   - port 80 side (towards the web server): plain HTTP, what someone watching the website's network would see,
     so the most realistic attacker.
   One capture records both; the analysis can pick a side by IP and compare the two.
   **Agree this with Daniel**: clearnet is his part and his README does not fix it yet.

### Changes per file

| File | Change |
|---|---|
| `shared-base/lab.sh` | new command, e.g. `clearnet-setup`: test files on the clearnet server, pin the exit, conflux off, restart the client |
| `run_experiment.py` | `--scenario clearnet`: find the exit and the client Guard from the client's circuits (the circuit ending at the pinned exit), capture filter `tcp port 9001 or tcp port 80` at the exit, URL `http://172.30.0.10/<page>`, server log from `docker logs`, results in `results/clearnet/<test>/` |
| `analyze.py` | the exit has **two IPs** (one per network): "sent/received by the observed relay" must check both |
| `run_test.sh` | pass the scenario through, analyse `results/clearnet/<test>` |

File names stay the same: "service" means "the second observation point"; `run_info.json` records what it is.

### Order of work

1. Check whether conflux is active (below), paste the output.
2. `lab.sh clearnet-setup`, test it.
3. `run_experiment.py --scenario clearnet`, smoke test with 5 requests.
4. `analyze.py` with the two IPs of the exit.
5. Talk to Daniel: which side of the exit to analyse, and whether he wants to use these scripts.

### Conflux check

After one clearnet request, list the client's circuits with their purpose and path:

```bash
CLIENT=$(docker ps --filter name=testing-tor-client- --format '{{.Names}}')
curl -s --socks5-hostname 127.0.0.1:9050 http://172.30.0.10/ >/dev/null
docker exec $CLIENT python3 -c "
from stem.control import Controller
with Controller.from_port(port=9051) as tor:
    tor.authenticate()
    for c in tor.get_circuits():
        if c.status == 'BUILT':
            print(c.purpose, ' -> '.join(n for _, n in c.path))
"
```

Conflux is active if some lines have purpose `CONFLUX_LINKED` (pairs of circuits ending at the same exit
but starting at different first relays).

## Lab problems already solved

- **Client circuits start at several relays**: directory downloads and conflux use other first relays.
  The Guard to watch is the first relay of the circuit that carries the traffic (for onion: the rendezvous
  circuit, `HS_CLIENT_REND` / `HS_SERVICE_REND`). `run_experiment.py` does this.
- **Client and onion service use the same Guard** (~1 in 8 with the lab's few relays): run
  `./shared-base/lab.sh separate-guards`. It adds `ExcludeNodes <relay>` to the client's torrc and restarts it.
  Recorded in each run's `run_info.json` (`guard_options_in_torrc`).
- **After a WSL / Docker restart**: `./shared-base/lab.sh status`; if containers are `exited`,
  `python cli/main.py network start`; then `./shared-base/lab.sh onion-web` (the web server does not survive a
  restart) and `./shared-base/lab.sh separate-guards`.
- **A run interrupted halfway** leaves a folder without `run_info.json`: delete it, then rerun the same
  `run_test.sh` command (complete runs are skipped).
