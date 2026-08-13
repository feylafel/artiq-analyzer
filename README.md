# artiq-analyzer

Prototype static analyzer for ARTIQ RTIO sequence errors (SED lane congestion).

**Status:** Exploratory learning prototype. Not intended for production use.

## Overview

This tool walks the AST of an ARTIQ kernel (Python source) and predicts potential RTIO sequence errors. It tracks the timeline cursor (`now_mu`), converts timestamps to coarse buckets (`coarse_ts = now_mu >> 3`), and counts how many output events fall into each bucket. If more than 8 events share the same coarse bucket, it raises a warning.

## Components

| File | Purpose |
| :--- | :--- |
| `analyzer.py` | AST walker. Tracks cursor and counts events per coarse bucket. Outputs warnings and an event stream. |
| `simulator.py` | Dynamic lane simulator. Takes a stream of coarse timestamps and places them into the first available lane (linear scan). |
| `test_sequence.py` | 14 synthetic test kernels. Validates the analyzer against the simulator. All tests pass under the current simplified model. |

## Known Limitation

The current model uses a **linear scan** for lane placement: for each event, it starts at lane 0 and places the event in the first free lane.

The actual ARTIQ SED hardware uses a **stateful `current_lane` pointer** that persists across events:
- If `ts > last_ts[current_lane]`, the event stays in the current lane.
- Otherwise, it advances to `(current_lane + 1) % lanes` (wrapping 7 → 0).
- If the target lane already holds a timestamp `>= ts`, a sequence error occurs.

This means the current simulator may produce different lane assignments (and potentially miss some sequence errors) compared to real hardware.

## Additional Gaps

- Collision detection (same channel, same coarse timestamp) — not implemented.
- Underflow detection — not implemented.
- Parallel block handling — currently sums delays instead of taking the max on exit.

## Run

```bash
python test_sequence.py
