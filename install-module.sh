#!/usr/bin/env bash
# Install the dgx_ec_fan_control module system-wide and make it load at boot.
# Run as root, on each node. Idempotent.
#
# This script does NOT redistribute the driver. It expects a source checkout
# that already contains the built module:
#
#   git clone https://github.com/christopherowen/dgx-spark-fan-control
#   cd dgx-spark-fan-control
#   git checkout deb2ea1                      # the base this patch targets
#   patch -p1 < /path/to/0001-platform-match-asus-gx10.patch
#   cd kernel && make
#
# then point DRIVER_KERNEL_DIR at that kernel/ directory, e.g.
#   sudo DRIVER_KERNEL_DIR=$PWD/kernel bash install-module.sh
set -euo pipefail

KERNEL=$(uname -r)
# Where the built dgx_ec_fan_control.ko lives. Override with DRIVER_KERNEL_DIR.
BUILD="${DRIVER_KERNEL_DIR:-}"
DEST="/lib/modules/${KERNEL}/extra/dgx_ec_fan_control.ko"
MODDIR="/etc/modules-load.d"
CONFDIR="/etc/modprobe.d"

[ "$(id -u)" -eq 0 ] || { echo "run as root" >&2; exit 2; }
if [ -z "$BUILD" ]; then
  echo "set DRIVER_KERNEL_DIR to the driver's kernel/ directory (see header)" >&2
  exit 2
fi
[ -f "$BUILD/dgx_ec_fan_control.ko" ] || {
  echo "no built module at $BUILD/dgx_ec_fan_control.ko (run 'make' in the driver's kernel/ first)" >&2
  exit 2
}

echo "kernel: $KERNEL"
echo "source: $BUILD"
install -d -m 0755 "$(dirname "$DEST")"
install -m 0644 "$BUILD/dgx_ec_fan_control.ko" "$DEST"
echo "installed $DEST"

# depmod so modprobe can resolve it (also builds modules.dep for the extra/ dir)
depmod -a "$KERNEL"
echo "depmod done"

# Load at boot, in order, after the FF-A transport is up.
install -d -m 0755 "$MODDIR"
cat > "$MODDIR/dgx-ec-fan.conf" <<'EOF'
# GPU/EC fan-floor driver (ASUS GX10 / GB10). Requires the arm-ffa transport,
# which the module declares as a softdep; modprobe resolves the order.
dgx_ec_fan_control
EOF
echo "wrote $MODDIR/dgx-ec-fan.conf"

# Declare the FF-A ordering dependency so the probe retries correctly at boot.
cat > "$CONFDIR/dgx-ec-fan.conf" <<'EOF'
softdep dgx_ec_fan_control pre: arm-ffa
EOF
echo "wrote $CONFDIR/dgx-ec-fan.conf"

# Verify it can now be resolved and loaded by name.
modinfo dgx_ec_fan_control >/dev/null && echo "modinfo resolves by name: OK"
if lsmod | grep -q '^dgx_ec_fan_control'; then
  echo "module currently loaded"
else
  modprobe dgx_ec_fan_control && echo "modprobe load: OK"
fi

echo "current state:"
lsmod | grep dgx_ec_fan_control || true
for d in /sys/class/thermal/cooling_device*; do
  [ "$(cat "$d/type" 2>/dev/null)" = "dgx_ec_fan_floor" ] && echo "  floor device: $(basename "$d") cur=$(cat "$d/cur_state") max=$(cat "$d/max_state")"
done
