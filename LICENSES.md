# Licences

## This repository

Everything authored here — `fan_governor.py`, `spark-fan-governor.service`,
`install-module.sh`, `apply_asus_patch.py`, documentation — is licensed
**Apache-2.0**, matching the licence GitHub attached to this repository.

## The upstream driver (not redistributed)

The governor depends on the kernel module `dgx_ec_fan_control`, which is
**GPL-2.0-only** and is **not** redistributed in this repository. Obtain it
yourself:

- Upstream: <https://github.com/christopherowen/dgx-spark-fan-control>
- Base commit: `deb2ea1` (8 September 2026)

`driver-patch/0001-platform-match-asus-gx10.patch` is an independent patch
against that source. Applying it produces a derivative work of a GPL-2.0-only
program, so the resulting module is GPL-2.0-only. The patch text itself is
provided under GPL-2.0-only to match.

The upstream licence text is included verbatim at
`driver-patch/LICENSE-GPL-2.0-upstream-driver` for reference.

## Third-party notices

The patch also carries a Lenovo ThinkStation PGX DMI branch
(`LENOVO` / `30KL0005GF`) inherited from the copy of this driver distributed in
**Spark_Energy_Management** (<https://github.com/djmad/Spark_Energy_Management>,
Copyright (c) 2026 Rene Marhold, CC BY-NC 4.0). That branch is optional on ASUS
hardware and may be removed from the patch if only the ASUS change is wanted.
