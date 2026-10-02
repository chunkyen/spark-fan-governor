#!/usr/bin/env bash
# Install the dgx_ec_fan_control module system-wide and make it load at boot.
# Run as root on each node. Idempotent.
set -euo pipefail

KERNEL=$(uname -r)
BUILD=/home/toor/spark-energy/drivers/dgx-spark-fan-control/kernel
DEST="/lib/modules/${KERNEL}/extra/dgx_ec_fan_control.ko"
MODDIR="/etc/modules-load.d"
CONFDIR="/etc/modprobe.d"

[ "$(id -u)" -eq 0 ] || { echo "run as root" >&2; exit 2; }
[ -f "$BUILD/dgx_ec_fan_control.ko" ] || { echo "missing built .ko at $BUILD" >&2; exit 2; }

echo "kernel: $KERNEL"
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

# Recover from a failed FF-A probe with one retry at boot.
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
