# How the analysis works and how to read its table

Plain explanation of what `analyze.py` computes and of every column of its table. The technical details (steps,
options, file formats) are in the description at the top of `analyze.py`.

## The question

The attacker sees only two relays: the Guard of the target client and the Guard of the onion service. From the
encrypted Tor packets it gets only time, size and IP addresses, never the content. The question: can the traffic
at the two points be linked by timing alone, clearly more often than by luck?

## Four ideas behind the numbers

1. **Burst**: packets that pass through a Guard one after another, in the same direction (into the Guard or out of
   it), with gaps of at most 0.1 s. Only bursts of at least 1000 bytes are kept, and only their **start time** is
   used. A burst is "a moment when a chunk of data went through".
2. **Pair**: a burst at the client's Guard and a burst at the service's Guard that start within **50 ms** of each
   other. Each burst can be used in only one pair. A pair is the attacker's guess: "these two are the same traffic".
3. **Request interval**: from the target's own log, the time from the start of a request to its end, plus 50 ms on
   each side. The attacker never sees this log: it is used only to check the attacker's guesses.
4. **Control**: the same pairing, but the client bursts of this run against the service bursts of a *different*
   run, placed at a random moment (the first requests lined up, then the other run moved by a random amount up to
   the average time between two requests). No real link exists there, so every pair is pure luck. Each other run
   is placed 10 times at random and everything is averaged, which makes the control steadier; the random seed is
   fixed, so the same data always gives the same control.

## The table, column by column

What was observed (plain counts):

| Column | Meaning |
|---|---|
| `run` | which run; `ALL` = all runs together |
| `clients` | clients sending at the same time, target included (1 = no background clients) |
| `requests` | successful requests of the target |
| `client_bursts` | bursts at the client's Guard (with `--target-only`, only the target's) |
| `service_bursts` | bursts at the service's Guard (everyone's traffic) |
| `pairs` | pairs found by the attacker |

Question 1, how many client bursts find a partner?

| Column | Calculation |
|---|---|
| `paired_%` | pairs / client_bursts |
| `control_%` | lucky pairs in the control / client_bursts |

Question 2, do the pairs fall during the target's requests?

| Column | Calculation |
|---|---|
| `pairs_in_req_%` | pairs that fall inside a target request / pairs |
| `control_pairs_in_req_%` | the same for the control |

With `--target-only` both are ~100% by construction (the client side holds only the target's traffic, which
happens during its requests). They say something only **without** the filter: in the smoke test with 5 clients
they gave 30%, because most "pairs" were another client's traffic at the same Guard.

Question 3, how many requests would the attacker notice? (the main result)

| Column | Calculation |
|---|---|
| `covered` | requests with at least one pair inside them, e.g. `5/5` |
| `covered_%` | the same as a percentage |
| `covered_control_%` | the same for the control |

Multi-client tests only (empty, NaN, with one client):

| Column | Calculation | Meaning |
|---|---|---|
| `req_overlap_%` | target requests overlapping in time with a background request / requests | how busy it was |
| `pairs_overlap_%` | pairs made while a background request was in progress / pairs | pairs that **may** belong to someone else: an upper bound on wrong pairs, not a count (bursts have no label, and at the service's Guard all clients arrive through the same connection) |

The `ALL` row adds up the counts of all runs and computes the percentages from the totals: it is not an average
of the per-run percentages.

## Worked example

Smoke test `smoke_clients`, run_01, `--target-only` (5 requests only: it checks the pipeline, it is not a result):

| Column | Value | Reading |
|---|---|---|
| clients | 5 | the target + 4 background clients |
| requests | 5 | the target made 5 requests |
| client_bursts | 5 | one burst per request on the target's link |
| service_bursts | 128 | the service's Guard is busy with all 5 clients |
| pairs / paired_% | 5 / 100% | every target burst found a partner |
| control_% | 28% | placed against other runs, 28% of them also found one, by luck (1.4 of 5 on average) |
| covered | 5/5 | all 5 requests noticed |
| covered_control_% | 28% | luck alone "notices" 1.4 of 5 on average |
| req_overlap_% | 80% | 4 of the 5 requests happened while another client was active |
| pairs_overlap_% | 60% | 3 of the 5 pairs could have matched another client's traffic |

## How to read a result

- The result is always the **gap between real and control**, never a percentage alone.
- The main number is `covered_%` against `covered_control_%` (Elena's onion result: 85.8% against 3.1%).
- **Count requests, not bursts.** `paired_%` and `control_%` count bursts, `covered_%` counts requests. In the lab
  a request is one short moment at both Guards whatever its size (see the 1 MB findings below): 1 burst for the
  small page, ~190 for `1m.bin`. Burst percentages grow with the page size without the attacker doing any better,
  so tests with different pages are compared only on `covered_%` and its control.
- Tests with different numbers of clients are compared only when all of them, the baseline included, are analysed
  with `--target-only`. Without the option the results are comparable with `experiments/onion-correlation/`.
  `run_test.sh` produces both tables.

## Limits of the metrics

1. **Timing, not content.** The attacker can link (who talks to whom, and when), not read what is transported.
   Linking is the goal of the attack (de-anonymisation = connecting a user to a service), so this is not a gap of
   the method. Sizes can hint at *which* page was fetched (website fingerprinting), a different attack, not done here.
2. **`covered` needs one pair per request.** In the lab this is the right unit: every request, even `1m.bin`, is one
   moment of less than 0.1 s at both Guards, so one pair means "the two moments line up". Bigger requests are
   easier to hit by luck (more bursts in the moment), and the control measures that: in `smoke_1m` (requests 1-2 s
   apart) luck covered 24% of the requests, against 1% with the small page. On real Tor, with real network delays,
   a 1 MB download lasts seconds; there one pair per request would be too generous, and the share of a request's
   bursts that pair would be the better measure.
3. **`pairs_in_req_%` is ~100% by construction with `--target-only`** (see above).
4. **`pairs_overlap_%` is an upper bound**, not a count of wrong pairs.
5. **Timing statistics, not precision/recall**: a burst has no label saying which request or client it belongs to.
6. **Small lab**: one machine and one clock (no network delay), few clients (2-5 against thousands of users on a
   real relay), thresholds (0.1 s, 1000 B, 50 ms) chosen by hand.

## Known issues (found in the smoke tests of 2026-11-23)

- **A. The control always matched the first request (fixed on 2026-11-26).** The control used to line up two runs
  exactly on their first request, so those two requests coincided by construction and always paired. In `smoke2`
  the control covered exactly request 1 of each run (20% = 1 of 5); in `smoke_1m` all ~190 bursts of request 1
  paired, which alone made `control_%` 20%. With 100 requests it added about 1 point to `covered_control_%`.
  Elena's notebook aligns runs the same way, so her control (3.1%) probably includes about 1 point of it.
  Fix: the other run is placed at a random moment (idea 4 above). `smoke2`'s control went from 20% to 1%, and in
  `smoke_1m` the lucky hits are now spread over all the requests instead of always falling on request 1.
- **B. With the small page a third of the requests leave no burst (decided on 2026-11-26: the real tests use
  `--page 10k.bin`).** On the target's link the small page's request cells never reach 1000 B in one direction
  (the two directions keep taking turns); the burst that survives is the response, and only when two cells travel
  together in one 1122-byte packet. That happens when TCP waits ~40 ms before sending ("slow" requests, ~0.054 s).
  When it does not wait ("fast" requests, ~0.014 s) the cells travel separately, nothing reaches 1000 B, and the
  request is invisible to the method, for a reason that has nothing to do with the attack.
  Measured in `check_small` (3 runs x 20 requests): 22 of 60 requests were not covered; all 22 left no burst at all
  and all 22 were fast; no slow request was missed. The three options, measured on the same kind of runs:

  | Setup | covered | control | gap |
  |---|---|---|---|
  | small page, 1000 B (default) | 63.3% | 1.8% | 61.5 |
  | small page, `--min-bytes 500` | 100% | 9.8% | 90.2 |
  | **`10k.bin`, 1000 B** | **100%** | **3.1%** | **96.9** |
  | `10k.bin`, `--min-bytes 500` | 100% | 7.4% | 92.6 |

  A lower threshold recovers every request but makes the control ~5 times higher (many small unrelated bursts
  start counting). `10k.bin` (about 20 cells per response) makes real bursts whatever TCP does, and keeps the
  control low: it is the page of the real tests. For the report: Elena's tests used the small page, so part of her
  uncovered requests (14%) may be this effect (not checked on her data).

Also seen in `check_small`, not an issue: the first 2-3 requests of a run get slightly more lucky hits in the
control. In the 5 s before each run's first request the service's Guard carries about 4 times more unrelated
traffic than during the run (probably Tor activity left after the warm-up request, not confirmed), and the random
placement puts the end of that period near the other run's first requests. It is real unrelated traffic, so
genuine luck, and its weight shrinks with 100 requests per run.

## Findings from the 1 MB smoke test (`smoke_1m`, 2026-11-26)

- **A 1 MB request lasts 0.06-0.12 s** in the lab (no network delay, no bandwidth limit), about as short as the
  small page. So in the lab a request is always one short moment at both Guards, whatever its size.
- **It makes ~410 bursts, ~190 of them kept (at least 1000 B)**, each about 2 packets / 4.2 KB. The download
  flows in one direction, but the client keeps sending small TCP ACKs back (72 B, ~80% of its packets): each one is
  a change of direction, so it ends a burst.
- **Packets in the captures are not the packets on the wire**: 45% of the captured frames are bigger than the
  network's 1514 B maximum (up to 19 KB), because Linux glues packets together on Docker's virtual network cards
  before tcpdump sees them. Timing is not affected; sizes should not be read as real packet sizes.
- Consequence: in the lab, the page size changes how many bursts a request has, not how many moments the attacker
  can match. A page-size test is expected to change `covered_%` little; the report should say why (no network
  delay in the lab), and that on real Tor a big download is spread over seconds. Adding artificial delay or
  bandwidth limits (Linux `tc netem`) would make the size test meaningful; not done, and the containers would have
  to allow it.

## Possible improvements (not implemented)

- **Per-request share**: for each request, the share of its client bursts that are paired; a request counts as
  recognised only above a threshold. Not useful in the lab (a request is one moment, so the share is ~100%),
  useful only with real delays (limit 2).
- **Decision per run**: for each run, does its real score beat the best score it gets against the other runs?
  Gives "identified in X of N runs" and the margin between real and best fake. Cheap to add to `analyze.py`.
- **A decision per time window**, the closest to a real attacker. Split each run into windows (e.g. 30 s); the
  score of a window is the share of client bursts paired in it. Windows paired with the same run are real links,
  windows paired with another run (as in the control) are not: at this level the labels exist. Choosing a threshold
  on the score gives a detection rate and a false-alarm rate (a ROC curve), i.e. numbers like precision and recall
  that the burst level cannot give (limit 5). With background clients the false alarms should grow, which is what
  the multi-client test wants to measure.
