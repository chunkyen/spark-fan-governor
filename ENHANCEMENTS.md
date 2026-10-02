# Enhancements (not scheduled)

Candidate improvements for `fan_governor.py`. None of these are implemented.
Order is rough value, not commitment.

---

## 1. Hysteresis + dwell on the hot path (highest value)

**Problem.** The hot escalation (`floor 8`, 9000 RPM) triggers on a *single*
2 s sample above the threshold, and releases on the very next sample below it.
There is no dwell and no release margin, so a transient ACPI spike can flap the
fans 6300 → 9000 → 6300.

**Observed live** (2026-10-02, node B, showcase run):

```
11:31:27 floor 8 (9000 RPM) set: HOT gpu=74.0C acpi=88.9C   fan=6300/6210
11:31:29 floor 5 (6300 RPM) set: LOAD power 60W + util 95%  fan=8100/8910
```

88.9 °C for one sample, back to 83.8 °C two seconds later. Benign that time, but
unbounded. P-cluster ACPI zones are documented to spike ~9 °C/s for fractions of
a second, so transients are expected, not exceptional.

**Proposed.**

- `--hot-dwell-s` (default ~6): the hot condition must hold continuously for
  this long before escalating. A single spiking sample no longer escalates.
- `--hot-release-c` (default 85): after escalation, stay at `--hot-state` until
  every zone falls below this margin, not merely below the trigger.
- Keep the *raw* trigger behaviour available for a genuine emergency: an
  immediate escalate above a hard ceiling (e.g. 92 °C) regardless of dwell, so
  dwell never delays a real thermal event.

**Note.** The upstream `energy_control` guard solves the same class of problem
with `PREDICTION_CONFIRM_S = 1.0` (a projected breach must persist before it
aborts) and a separate immediate band `PREDICTION_IMMEDIATE_C = 3.0`. That is a
good shape to borrow: confirm ordinary excursions, act at once near the limit.

---

## 2. Sample faster than the decision threshold needs

**Problem.** The governor samples every 2 s; `gpu-logger` samples every 5 s.
A real 88.9 °C excursion **never appeared in the logger** — the logger's daily
max for node B is 86 °C. Any thermal audit based on gpu-log CSV alone therefore
undercounts hot events, and the governor's own decisions look unmotivated in the
logs.

**Proposed.** Either lower `--sample-s` (1 s is affordable: one `nvidia-smi`
query measured ~35 ms), or have the governor append its own JSONL trace (it
already supports `--log-file`) and treat that as the decision record of truth.
At minimum, document that gpu-log is *not* sufficient to audit governor
behaviour.

---

## 3. Log *which* zone tripped the hot path

**Problem.** The log line reports `acpi=88.9C` — the maximum across all ACPI
zones — without naming the zone. On GB10 the zones have very different
characters (P-clusters spike fast, TSOC is the slow bulk indicator), so the
identity matters for tuning the threshold.

**Proposed.** Include the zone name and its path, e.g.
`HOT acpi=88.9C zone=\_TZ_.TS1P`. Cheap; makes future threshold decisions
evidence-based instead of guesswork.

---

## 4. Derive the floor from load, not a fixed step

**Problem.** `--load-state` is one fixed value (5). The upstream predictive
policy computes a *steady-state* fan speed from a cooler model plus measured
power, so light load gets a light floor and heavy load gets a heavy one.

**Proposed.** Scale the floor between `--load-state` and `--hot-state` from
measured GPU power (the physical heat proxy — already read every sample). The
digital twin in `simulation/fan_twin.py` upstream is Lenovo-PGX-fitted and
would start as a *prior*, not a calibration; a full port is a much larger job
than this repository's current scope.

---

## 5. Make the "hot" threshold load-aware

**Problem.** 88 °C means something different at 60 W (an excursion) than at
150 W sustained (an expected steady state). A fixed threshold cannot express
that, so a future heavier workload may sit permanently against the escalation
ladder.

**Proposed.** Raise the hot threshold when measured power is high and steady,
keeping the absolute ceiling for genuine emergencies. Needs real data from a
heavier workload first — do not guess the curve.

---

## 6. Watchdog: detect a stale or dead EC mailbox

**Problem.** The kernel module reads fan RPM via EC command `0x07` and can
return `telemetry_valid=0` (documented: reads may drain a late completion). The
governor currently treats an unreadable RPM as `None` and logs it, but takes no
action. If the EC mailbox wedges, floors may be written without effect and
nothing would notice.

**Proposed.** Alert when floor state has been non-zero for N consecutive
samples while measured RPM stays at its idle value — i.e. the floor is not
being honoured. Do not auto-raise further (that is guesswork); raise visibility.
