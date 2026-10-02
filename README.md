# Spark Fan Governor (D1)

Predictive additive fan-floor governor for **ASUS GX10** (Ascent GX10) nodes — the ASUS
partner SKU of NVIDIA GB10 / DGX Spark.

> **Tested on ASUS GX10.** The upstream `dgx-spark-fan-control` driver stock-gates its DMI
> match to NVIDIA `P4242` (DGX Spark) and Lenovo `30KL0005GF` (ThinkStation PGX). On an ASUS
> GX10 it does **not** load without the included `apply_asus_patch.py` /
> `driver-patch/0001-platform-match-asus-gx10.patch`. This repo is validated end-to-end on
> two ASUS GX10 nodes; if you are on an NVIDIA DGX Spark or Lenovo PGX you do not need the
> patch, but the governor itself has only ever been exercised on ASUS hardware.

**Repository:** public at <https://github.com/chunkyen/spark-fan-governor>
(Apache-2.0). The driver dependency is GPL-2.0-only and is **not**
redistributed here — see Licence below.

Built 2026-10-02 as a standalone alternative to `Spark_Energy_Management`'s
`energy_control` service, whose fixed safety contract is unusable on a live
EXL3 deck:

- its guard aborts below **12 GiB** MemAvailable (our nodes sit at ~6 GiB with
  the deck loaded) and above **GPU 85 °C** (routine bursts reach 86–93 °C);
- neither limit can be raised by configuration — they are deliberately fixed;
- it also arms a 1700 MHz GPU entry ceiling and flips all 20 CPU policies from
  `performance` to `conservative` at start.

D1 keeps the one wanted feature — predictive fan anticipation — and owns
**exactly one actuator**: the additive fan floor. No GPU clocks, no CPU
governors, no cpufreq maxima. The floor is a lower clamp only; firmware may
always add more cooling.

## Contents

| path | what |
| --- | --- |
| `fan_governor.py` | the governor (Python 3.12+, stdlib only) |
| `spark-fan-governor.service` | systemd unit — boot-enabled, `Restart=on-failure` |
| `install-module.sh` | installs + boot-persists the EC fan-floor driver |
| `apply_asus_patch.py` | restores the ASUS GX10 DMI branch in the driver source |
| `driver-patch/0001-platform-match-asus-gx10.patch` | DMI patch against the pristine upstream driver |
| `driver-patch/LICENSE-GPL-2.0-upstream-driver` | the upstream driver's licence (GPL-2.0-only) |

## Licence

This repository's own code (`fan_governor.py`, the systemd unit, scripts) is
**Apache-2.0**, matching the licence GitHub attached to this repository.

The driver dependency is **not** covered by it: `dgx_ec_fan_control` is
GPL-2.0-only and is **not redistributed here**. Only a patch against the
pristine upstream source is shipped, and you obtain the source yourself:

- **Upstream:** <https://github.com/christopherowen/dgx-spark-fan-control>
- **Base commit:** `deb2ea1` (8 September 2026)

## The driver dependency

The governor drives a cooling device provided by the kernel module
`dgx_ec_fan_control` (upstream `christopherowen/dgx-spark-fan-control`,
GPL-2.0-only, v0.1.3). Its stock DMI gate accepts only NVIDIA `P4242`; our ASUS
GX10 reports vendor `ASUSTeK COMPUTER INC.` and product/board `GX10`, so an
additional branch is required.

`driver-patch/0001-platform-match-asus-gx10.patch` adds it and is verified to
apply cleanly with `patch -p1` against the upstream base commit. It also carries
a Lenovo ThinkStation PGX branch (`LENOVO` / `30KL0005GF`) inherited from the
`Spark_Energy_Management` copy of this driver (CC BY-NC 4.0); that branch is not
needed on ASUS hardware and can be dropped if you only want the ASUS change.

Historical note: on the machine this was developed on, the originally patched
source had **not been preserved** — only the compiled module carried the change
(its rodata held `ASUSTeK COMPUTER INC.`, `GX10` and an `asus_gx10` local while
every `.c` on disk lacked them). The branch above was recovered from those
strings, and the rebuilt module verified to probe on real hardware: packet
service `0x8003`, properties `0x109`, FF-A 1.2, capabilities fan0 1260–9000 /
fan1 1890–13500 RPM, floor device registered in automatic state.

`srcversion` is **not** a useful integrity check here — it hashes struct
layout, so it is byte-identical between the patched and unpatched builds. Probe
success on real GX10 hardware is the real evidence.

`apply_asus_patch.py` reproduces the same change in-place if you already have a
driver checkout; the patch file is the canonical form.

## Floor ladder (driver constants)

State 0 = auto/off, then 2700, 3600, 4500, 5400, 6300, 7200, 8100, 9000,
10125, 11250, 12375, 13500 RPM. fan0 tops out at 9000 RPM (hardware limit);
states 9–12 push fan1 beyond that while fan0 saturates. The governor caps
requests at `--max-state 10` (fan1 = 11250 RPM): verified live on a GX10 —
the EC accepted `cur_state=10`, fan0 held 9000, fan1 ramped 10260 → 11205 RPM
within ~8 s. States 11–12 remain untested.

## Behaviour

- Load (GPU power ≥ 30 W and/or util ≥ 40 %, or vLLM queue > 0 when
  `--queue-url` is set) → floor raised to `--load-state` **immediately**.
  Feed-forward by design: the EC's own curve was measured reacting 20+ s late
  at 79 °C.
- Load held → floor held. Load gone → held for `--idle-delay-s`, then released
  to 0 (EC auto).
- GPU ≥ `--hot-gpu-c` or any ACPI zone ≥ `--hot-acpi-c` → `--hot-state`.
  Since 2026-10-02 (afternoon): `--hot-state 10 --max-state 10` — escalation
  drives fan1 to the 11250 RPM rung (fan0 saturates at its 9000 limit). This
  followed measured CPU-zone overshoot on gx10b: both 90 °C+ acpitz excursions
  peaked (92–94.6 °C) with fans already at the state-8 rung, i.e. fan1 had
  ~2 300 RPM of unused headroom above the old cap. Rationale and trade-off:
  the overshoot is seconds-scale thermal-mass lag, so state 10 pre-arms more
  airflow during the climb but does not eliminate the spike; it also trades
  acoustic headroom — states 11–12 stay reserved.
- `stop` always writes floor 0, so the EC regains full control.

## Measured 2026-10-02 (A governed vs B EC-auto, identical 84 s load, ~57 W)

| metric | A (floor 5) | B (EC auto) |
| --- | --- | --- |
| ACPI SoC peak | 85 °C | 86 °C |
| TGPU zone peak | 78 °C | 81 °C |
| GPU die peak | 72 °C | 75 °C |
| fan0 | 6300 held from load start | 3780 → 4320 (late step) |
| throughput | unchanged | unchanged |

A 1–3 °C win at 6300 RPM vs the EC's 3780–4320 RPM. A 35 s burst showed **no**
difference — thermal mass dominates short loads, so the floor pays off over
minutes, not showcase bursts.

**Node confound:** gx10a runs intrinsically hotter than gx10b (+4–7 °C at equal
role/load). Always compare role-matched or state the delta explicitly.

## Operate

```sh
sudo systemctl start spark-fan-governor      # also starts at boot
sudo systemctl stop spark-fan-governor       # floor -> 0, EC auto
journalctl -u spark-fan-governor -f
python3 fan_governor.py --dry-run            # no writes, no root needed
```

## Install on a fresh node

The driver is **not** bundled — get the source, patch it, build it, then
install. The patch in `driver-patch/` is verified to apply cleanly against the
upstream base commit.

```sh
# 1. driver: clone upstream, apply the ASUS GX10 patch, build
git clone https://github.com/christopherowen/dgx-spark-fan-control
cd dgx-spark-fan-control && git checkout deb2ea1
patch -p1 < /path/to/spark-fan-governor/driver-patch/0001-platform-match-asus-gx10.patch
cd kernel && make && cd ..

# 2. install the module + make it load at boot
sudo DRIVER_KERNEL_DIR=$PWD/kernel bash install-module.sh

# 3. the governor itself
sudo install -d -m 0755 /opt/spark-fan-governor
sudo install -m 0644 fan_governor.py README.md /opt/spark-fan-governor/
sudo install -m 0644 spark-fan-governor.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now spark-fan-governor
```

## Rollback

```sh
sudo systemctl disable --now spark-fan-governor
sudo rm -rf /opt/spark-fan-governor
sudo rm /etc/systemd/system/spark-fan-governor.service
sudo rm /etc/modules-load.d/dgx-ec-fan.conf
sudo systemctl daemon-reload
# optional: sudo rmmod dgx_ec_fan_control
```

`stop` always releases the floor to 0 first. Nothing else on the node is
modified.
