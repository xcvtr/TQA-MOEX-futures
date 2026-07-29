#!/bin/bash
# Entrypoint for FINAM MT5 container
# Starts Xvfb, x11vnc (5901), XFCE, then launches MT5 terminal + bridges
set -e

VNC_DISPLAY="${VNC_DISPLAY:-99}"
VNC_PORT="${VNC_PORT:-5901}"
VNC_PASS="${VNC_PASS:-finam}"
VNC_RESOLUTION="${VNC_RESOLUTION:-1920x1080x16}"

rm -f "/tmp/.X${VNC_DISPLAY}-lock" "/tmp/.X11-unix/X${VNC_DISPLAY}"

echo "Starting Xvfb on display :${VNC_DISPLAY} (${VNC_RESOLUTION})..."
Xvfb ":${VNC_DISPLAY}" -screen 0 "${VNC_RESOLUTION}" -ac &
sleep 2

if [ -n "$VNC_PASS" ]; then
    x11vnc -noshm -display ":${VNC_DISPLAY}" -forever -shared -noxdamage -rfbport "$VNC_PORT" -passwd "$VNC_PASS" &
else
    x11vnc -noshm -display ":${VNC_DISPLAY}" -forever -nopw -shared -noxdamage -rfbport "$VNC_PORT" &
fi

mkdir -p /run/dbus 2>/dev/null || true
dbus-daemon --system --fork 2>/dev/null || true
sleep 2

startxfce4 &>/dev/null &
sleep 3
DISPLAY=:${VNC_DISPLAY} xfwm4 &>/dev/null &
DISPLAY=:${VNC_DISPLAY} xfdesktop &>/dev/null &

echo "VNC: 10.0.0.60:${VNC_PORT} | pass: ${VNC_PASS}"

# Fix wine prefix ownership (bind mount from host may have wrong uid)
if [ -d "$WINEPREFIX" ] && [ "$(stat -c '%u' "$WINEPREFIX")" != "0" ]; then
    echo "Fixing wine prefix ownership..."
    chown -R root:root "$WINEPREFIX" 2>/dev/null || true
fi

# Check that wine prefix exists
if [ ! -f "$WINEPREFIX/system.reg" ]; then
    echo "ERROR: WINEPREFIX ($WINEPREFIX) not initialized!"
    echo "Mount your FINAM wine prefix to $WINEPREFIX"
    tail -f /dev/null
    exit 1
fi

echo "Starting FINAM MT5 terminal + bridge..."
bash /app/starter.sh &

# Keep container alive
tail -f /dev/null
