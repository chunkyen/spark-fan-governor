#!/usr/bin/env python3
"""D1 standalone predictive fan-floor governor for GB10 (ASUS GX10).

Owns exactly ONE actuator: the additive fan floor exposed by the
dgx_ec_fan_control kernel module (cooling device "dgx_ec_fan_floor", states
0..12 -> 0(auto), 2700, 3600, 4500, 5400, 6300, 7200, 8100, 9000, ... RPM).

It never touches GPU clocks, CPU governors or cpufreq maxima. The floor is a
LOWER clamp only: firmware/EC may always add more cooling on top.

Behaviour (feed-forward, not reactive):
  load detected  -> floor RAISED IMMEDIATELY (anticipation; the EC's own curve
                    was measured to react 20+ s late at 79 C)
  load sustained -> floor held
  load gone      -> floor held for --idle-delay-s, then released to 0 (auto)
  hot GPU / hot ACPI zone -> floor raised to --hot-state regardless of load
  thermal safety -> release only when cool again

Load signal: GPU power draw (physical heat proxy) OR GPU utilisation, plus an
optional vLLM queue probe when the metrics endpoint is reachable.

Runs per node, unprivileged except for the single sysfs write, which requires
root. Install as a DISABLED systemd user unit; start on demand.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

DEVICE_TYPE = "dgx_ec_fan_floor"
THERMAL_ROOT = Path("/sys/class/thermal")
HWMON_ROOT = Path("/sys/class/hwmon")
MAX_STATE = 12
# state -> RPM (driver ladder); used for logging only.
LADDER_RPM = {0: 0, 1: 2700, 2: 3600, 3: 4500, 4: 5400, 5: 6300, 6: 7200,
              7: 8100, 8: 9000, 9: 10125, 10: 11250, 11: 12375, 12: 13500}
GPU_QUERY = ("--query-gpu=utilization.gpu,power.draw,temperature.gpu,clocks.gr",
             "--format=csv,noheader,nounits")

_stop = False


def log(msg: str) -> None:
    print(f"{time.strftime('%Y-%m-%dT%H:%M:%S%z')} {msg}", flush=True)


def _signal(_signum, _frame):
    global _stop
    _stop = True


def find_floor_device() -> Path:
    matches = []
    for cand in sorted(THERMAL_ROOT.glob("cooling_device*")):
        try:
            if (cand / "type").read_text(encoding="ascii").strip() == DEVICE_TYPE:
                matches.append(cand)
        except (OSError, UnicodeError):
            continue
    if len(matches) != 1:
        raise RuntimeError(f"expected exactly one {DEVICE_TYPE} device, found {len(matches)}")
    return matches[0]


def read_floor(device: Path) -> int:
    return int((device / "cur_state").read_text(encoding="ascii").strip())


def write_floor(device: Path, state: int) -> int:
    """Write the floor and verify by readback. Retries the EBUSY-ish EC races."""
    if not 0 <= state <= MAX_STATE:
        raise ValueError(f"floor state out of range: {state}")
    if read_floor(device) == state:
        return state
    delays = (0.05, 0.1, 0.2, 0.4, None)
    last = None
    for delay in delays:
        try:
            (device / "cur_state").write_text(f"{state}\n", encoding="ascii")
            after = read_floor(device)
            if after != state:
                raise RuntimeError(f"readback mismatch: wanted {state}, got {after}")
            return after
        except OSError as exc:
            last = exc
            if delay is None:
                raise
            time.sleep(delay)
    raise RuntimeError(f"floor write failed: {last}")


def read_fan_rpm() -> tuple[int | None, int | None]:
    for hw in sorted(HWMON_ROOT.glob("hwmon*")):
        try:
            if (hw / "name").read_text(encoding="ascii").strip() != "dgx_ec_fan":
                continue
        except (OSError, UnicodeError):
            continue
        out = []
        for node in ("fan1_input", "fan2_input"):
            try:
                out.append(int((hw / node).read_text(encoding="ascii").strip()))
            except (OSError, ValueError, UnicodeError):
                out.append(None)
        return out[0], out[1]
    return None, None


def read_hottest_acpi() -> float | None:
    hottest = None
    for z in sorted(THERMAL_ROOT.glob("thermal_zone*")):
        try:
            if (z / "type").read_text(encoding="ascii").strip() != "acpitz":
                continue
            c = int((z / "temp").read_text(encoding="ascii").strip()) / 1000.0
        except (OSError, ValueError, UnicodeError):
            continue
        if hottest is None or c > hottest:
            hottest = c
    return hottest


def read_gpu() -> tuple[float | None, float | None, float | None, float | None]:
    try:
        r = subprocess.run(["nvidia-smi", "-i", "0", *GPU_QUERY], capture_output=True,
                           text=True, timeout=10, check=False)
        if r.returncode != 0:
            return None, None, None, None
        parts = [p.strip() for p in r.stdout.strip().splitlines()[0].split(",")]
        return (float(parts[0]), float(parts[1]), float(parts[2]), float(parts[3]))
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return None, None, None, None


def read_vllm_running(url: str | None) -> int | None:
    if not url:
        return None
    try:
        r = subprocess.run(["curl", "-s", "-m", "2", url], capture_output=True,
                           text=True, timeout=5, check=False)
        if r.returncode != 0:
            return None
        total = 0
        seen = False
        for line in r.stdout.splitlines():
            if line.startswith("vllm:num_requests_running"):
                try:
                    total += float(line.rsplit(" ", 1)[1])
                    seen = True
                except (ValueError, IndexError):
                    continue
        return int(total) if seen else None
    except (OSError, subprocess.SubprocessError):
        return None


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="D1 predictive fan-floor governor")
    p.add_argument("--sample-s", type=float, default=2.0)
    p.add_argument("--load-power-w", type=float, default=30.0,
                   help="GPU power above this counts as load")
    p.add_argument("--load-util-pct", type=float, default=40.0,
                   help="GPU utilisation above this counts as load")
    p.add_argument("--load-state", type=int, default=5, help="floor while loaded (5 = 6300 RPM)")
    p.add_argument("--idle-delay-s", type=float, default=120.0,
                   help="hold the load floor this long after load ends")
    # Two independent hot trips, spaced by the measured acpitz-leads-die gap
    # (zones run 7-17 C hotter than the GPU die and are what the EC acts on):
    # die >= 80 C catches fast silicon spikes; hottest ACPI zone >= 88 C is an
    # early warning that the package is approaching the EC ~98 C cutoff. See
    # README "Thermal sensor semantics".
    p.add_argument("--hot-gpu-c", type=float, default=80.0)
    p.add_argument("--hot-acpi-c", type=float, default=88.0)
    p.add_argument("--hot-state", type=int, default=8, help="floor when hot (8 = 9000 RPM, cap)")
    p.add_argument("--max-state", type=int, default=8,
                   help="never request a floor above this (fan0 ceiling is state 8)")
    p.add_argument("--queue-url", type=str, default=None,
                   help="optional vLLM /metrics URL for earliest anticipation")
    p.add_argument("--log-file", type=str, default="")
    p.add_argument("--dry-run", action="store_true", help="log decisions, write nothing")
    args = p.parse_args(argv)

    if os.geteuid() != 0 and not args.dry_run:
        log("error: floor writes require root (or pass --dry-run)")
        return 2
    if not 0 <= args.load_state <= args.max_state <= MAX_STATE:
        log(f"error: bad states load={args.load_state} max={args.max_state}")
        return 2

    signal.signal(signal.SIGTERM, _signal)
    signal.signal(signal.SIGINT, _signal)

    device = find_floor_device()
    log(f"start: device={device} sample={args.sample_s}s load>={args.load_power_w}W "
        f"or >={args.load_util_pct}% -> state {args.load_state}; hot>={args.hot_gpu_c}C/"
        f"{args.hot_acpi_c}C -> state {args.hot_state}; idle release after {args.idle_delay_s}s"
        + (" [DRY-RUN]" if args.dry_run else ""))

    applied = read_floor(device)
    loaded_since: float | None = None
    idle_since: float | None = None
    ever_loaded = False
    wrote_initial = False

    while not _stop:
        tick = time.monotonic()
        util, power, gpu_c, clock = read_gpu()
        queue = read_vllm_running(args.queue_url)
        acpi_c = read_hottest_acpi()
        fan0, fan1 = read_fan_rpm()

        gpu_known = power is not None and util is not None
        loaded = False
        reason = "no-signal"
        if gpu_known:
            if power >= args.load_power_w and util >= args.load_util_pct:
                loaded, reason = True, f"power {power:.0f}W + util {util:.0f}%"
            elif power >= args.load_power_w:
                loaded, reason = True, f"power {power:.0f}W"
            elif util >= args.load_util_pct:
                loaded, reason = True, f"util {util:.0f}%"
        if queue is not None and queue > 0:
            loaded, reason = True, f"vllm queue {queue}"

        hot = False
        if gpu_c is not None and gpu_c >= args.hot_gpu_c:
            hot = True
        if acpi_c is not None and acpi_c >= args.hot_acpi_c:
            hot = True

        now = time.monotonic()
        if loaded:
            ever_loaded = True
            idle_since = None
            if loaded_since is None:
                loaded_since = now
        else:
            loaded_since = None
            if idle_since is None:
                idle_since = now

        if hot:
            target = args.hot_state
            why = f"HOT gpu={gpu_c}C acpi={acpi_c}C"
        elif loaded:
            target = args.load_state
            why = f"LOAD {reason}"
        elif ever_loaded and idle_since is not None and (now - idle_since) < args.idle_delay_s:
            target = args.load_state
            why = f"hold {(args.idle_delay_s - (now - idle_since)):.0f}s left"
        else:
            target = 0
            why = "idle release"

        target = min(target, args.max_state)

        if target != applied or not wrote_initial:
            if args.dry_run:
                log(f"DRYRUN would set {applied} -> {target} ({why}) "
                    f"gpu={gpu_c}C util={util} p={power}W acpi={acpi_c}C fan={fan0}/{fan1}")
                applied = target
            else:
                try:
                    applied = write_floor(device, target)
                    fan0, fan1 = read_fan_rpm()
                    log(f"floor {target} ({LADDER_RPM[target]} RPM) set: {why}; "
                        f"gpu={gpu_c}C util={util} p={power}W acpi={acpi_c}C "
                        f"clock={clock} fan={fan0}/{fan1}")
                except (OSError, RuntimeError, ValueError) as exc:
                    log(f"floor write failed ({type(exc).__name__}: {exc}); keeping {applied}")
            wrote_initial = True
        else:
            log(f"floor {applied} held: {why}; gpu={gpu_c}C util={util} p={power}W "
                f"acpi={acpi_c}C fan={fan0}/{fan1}")

        if args.log_file:
            try:
                with open(args.log_file, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps({
                        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                        "floor": applied, "target": target, "why": why,
                        "gpu_c": gpu_c, "util": util, "power_w": power,
                        "acpi_c": acpi_c, "clock_mhz": clock,
                        "fan0": fan0, "fan1": fan1, "queue": queue,
                    }) + "\n")
            except OSError:
                pass

        elapsed = time.monotonic() - tick
        remaining = max(0.2, args.sample_s - elapsed)
        deadline = time.monotonic() + remaining
        while not _stop and time.monotonic() < deadline:
            time.sleep(min(0.2, deadline - time.monotonic()))

    # Clean handback: release the floor so the EC owns the fans again.
    if not args.dry_run:
        try:
            final = write_floor(device, 0)
            log(f"stop: floor released to {final} (EC auto)")
        except (OSError, RuntimeError, ValueError) as exc:
            log(f"stop: release failed: {type(exc).__name__}: {exc}")
    else:
        log("stop: dry-run, nothing written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
