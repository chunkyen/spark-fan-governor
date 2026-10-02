# Spark Fan Governor (D1)

Predictive additive fan-floor governor for ASUS GX10 / NVIDIA GB10 nodes.

**Local-only repository: no remote is configured and nothing is ever pushed.**

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
| `driver-patch/dgx_ec_fan_control.c.patched` | the reconstructed patched driver source |

## The driver dependency

The governor drives a cooling device provided by the kernel module
`dgx_ec_fan_control` (upstream: `djmad/Spark_Energy_Management`, GPL-2.0,
v0.1.3). Its stock DMI gate accepts only NVIDIA `P4242` and Lenovo
`30KL0005GF`. Our ASUS GX10 reports vendor `ASUSTeK COMPUTER INC.` and product
`GX10`, so a third branch is required.

The originally patched source was **not preserved** — only the compiled module
carried the change (verified: the `.ko` contained `ASUSTeK COMPUTER INC.`,
`GX10` and the `asus_gx10` local, while every `.c` on disk lacked them). The
branch was recovered from those rodata strings and is reapplied by
`apply_asus_patch.py`. The rebuilt module was verified to probe on both nodes:
packet service `0x8003`, properties `0x109`, FF-A 1.2, capabilities
fan0 1260–9000 / fan1 1890–13500 RPM, floor device registered in automatic
state.

`srcversion` is **not** a useful integrity check here — it hashes struct
layout, so it is byte-identical between the patched and unpatched builds.
Probe success on GX10 is the real evidence.

## Floor ladder (driver constants)

State 0 = auto/off, then 2700, 3600, 4500, 5400, 6300, 7200, 8100, 9000,
10125, 11250, 12375, 13500 RPM. fan0 tops out at 9000 RPM, so **state 8 is the
practical ceiling**; states 9–12 can only be satisfied by fan1.

## Behaviour

- Load (GPU power ≥ 30 W and/or util ≥ 40 %, or vLLM queue > 0 when
  `--queue-url` is set) → floor raised to `--load-state` **immediately**.
  Feed-forward by design: the EC's own curve was measured reacting 20+ s late
  at 79 °C.
- Load held → floor held. Load gone → held for `--idle-delay-s`, then released
  to 0 (EC auto).
- GPU ≥ `--hot-gpu-c` or any ACPI zone ≥ `--hot-acpi-c` → `--hot-state`.
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

```sh
sudo bash install-module.sh                  # driver -> /lib/modules, boot-persist
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
