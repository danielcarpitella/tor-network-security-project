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
   run, aligned on the first request. No real link exists there, so every pair is pure luck. Averaged over all the
   other runs of the test.

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
| control_% | 40% | 2 of 5 also found one in another run, by luck |
| covered | 5/5 | all 5 requests noticed |
| covered_control_% | 40% | luck alone "notices" 2 of 5 |
| req_overlap_% | 80% | 4 of the 5 requests happened while another client was active |
| pairs_overlap_% | 60% | 3 of the 5 pairs could have matched another client's traffic |

## How to read a result

- The result is always the **gap between real and control**, never a percentage alone.
- The main number is `covered_%` against `covered_control_%` (Elena's onion result: 85.8% against 3.1%).
- Tests with different numbers of clients are compared only when all of them, the baseline included, are analysed
  with `--target-only`. Without the option the results are comparable with `experiments/onion-correlation/`.
  `run_test.sh` produces both tables.

## Limits of the metrics

1. **Timing, not content.** The attacker can link (who talks to whom, and when), not read what is transported.
   Linking is the goal of the attack (de-anonymisation = connecting a user to a service), so this is not a gap of
   the method. Sizes can hint at *which* page was fetched (website fingerprinting), a different attack, not done here.
2. **`covered` is generous.** One pair is enough, whatever the length of the request. With the small page this is
   right: each request leaves one burst on the target's link, so one pair is all the evidence there can be. With
   long requests (e.g. `1m.bin`) it is too generous: a request with many bursts and a single pair counts like one
   where every burst pairs, and a long request has more chances of a lucky pair. The control has the same
   advantage, so the real-vs-control gap stays fair on average, but a single covered request does not mean the
   attacker would really have recognised it.
3. **`pairs_in_req_%` is ~100% by construction with `--target-only`** (see above).
4. **`pairs_overlap_%` is an upper bound**, not a count of wrong pairs.
5. **Timing statistics, not precision/recall**: a burst has no label saying which request or client it belongs to.
6. **Small lab**: one machine and one clock (no network delay), few clients (2-5 against thousands of users on a
   real relay), thresholds (0.1 s, 1000 B, 50 ms) chosen by hand.

## Known issues (found in the smoke tests of 2026-11-23, not fixed yet)

- **A. The control always matches the first request.** The control aligns two runs on their first request, so
  those two requests coincide by construction and always pair. In `smoke2` the control covered exactly request 1 of
  each run (20% = 1 of 5). With 100 requests it adds about 1 point to `covered_control_%`. Elena's notebook aligns
  runs the same way. Possible fixes: leave the first request out of the control's coverage, or align the runs with
  an offset so that the first requests do not coincide.
- **B. With the small page a request depends on one packet.** On the target's link the request cells never reach
  1000 B in one direction; the burst that survives is the response, and only when two cells travel together in one
  1122-byte packet. In `smoke2` run_02 two fast requests (0.010 s instead of 0.053 s) had the cells travelling
  separately, left no burst, and were not covered (3/5). Not a bug, but a limit: with the small page, detection
  depends on how TCP packs the cells. Options: a sensitivity check with `--min-bytes 500`, or tests with
  `--page 10k.bin`.

## Possible improvements (not implemented)

- **Per-request share**: for each request, the share of its client bursts that are paired; a request counts as
  recognised only above a threshold (e.g. half of its bursts). The same as `covered` for the small page, stricter
  for long requests (limit 2).
- **A decision per time window**, the closest to a real attacker. Split each run into windows (e.g. 30 s); the
  score of a window is the share of client bursts paired in it. Windows paired with the same run are real links,
  windows paired with another run (as in the control) are not: at this level the labels exist. Choosing a threshold
  on the score gives a detection rate and a false-alarm rate (a ROC curve), i.e. numbers like precision and recall
  that the burst level cannot give (limit 5). With background clients the false alarms should grow, which is what
  the multi-client test wants to measure.
