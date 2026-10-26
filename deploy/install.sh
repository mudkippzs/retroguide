#!/usr/bin/env bash
# Install RetroGuide's always-on user services (stream server + nightly
# schedule rebuild) and print the Tailscale HTTPS command for remote access.
# Safe to re-run.
set -euo pipefail
cd "$(dirname "$0")/.."

UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
mkdir -p "$UNIT_DIR"
cp deploy/retroguide-stream.service \
   deploy/retroguide-schedule.service \
   deploy/retroguide-schedule.timer \
   "$UNIT_DIR/"

systemctl --user daemon-reload
systemctl --user enable --now retroguide-stream.service
systemctl --user enable --now retroguide-schedule.timer

# Linger keeps the user manager (and these services) alive across logouts
# and starts them at boot.
if [ "$(loginctl show-user "$USER" -p Linger --value 2>/dev/null)" != "yes" ]; then
  echo "Enabling linger for $USER (services start at boot, survive logout)..."
  loginctl enable-linger "$USER"
fi

echo
echo "Services up. Next steps:"
echo "  stream:  systemctl --user status retroguide-stream"
echo "  timer:   systemctl --user list-timers retroguide-schedule.timer"
echo "  health:  curl -s http://127.0.0.1:8722/api/health"
echo
echo "Tailscale HTTPS (run once; persists in tailscaled):"
echo "  tailscale serve --bg --https=8443 http://127.0.0.1:8722"
echo "  then from your phone: https://\$(hostname -s).\$TAILNET:8443/m"
