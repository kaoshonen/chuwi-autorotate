#!/usr/bin/env bash
set -euo pipefail

purge=false
if [[ "${1:-}" == "--purge" ]]; then
  purge=true
elif [[ $# -gt 0 ]]; then
  echo "usage: ./uninstall.sh [--purge]" >&2
  exit 2
fi

if command -v systemctl >/dev/null 2>&1; then
  systemctl --user disable --now chuwi-autorotate.service || true
fi

if [[ -e /etc/lightdm/lightdm.conf.d/80-chuwi-autorotate.conf || -e /usr/local/bin/chuwi-autorotate-greeter ]]; then
  echo "LightDM greeter rotation files are still installed."
  echo "Remove them before uninstalling with:"
  echo "  $HOME/.local/bin/chuwi-autorotate greeter-uninstall"
fi

rm -f "$HOME/.config/systemd/user/chuwi-autorotate.service"
rm -f "$HOME/.local/bin/chuwi-autorotate"

if command -v systemctl >/dev/null 2>&1; then
  systemctl --user daemon-reload || true
fi

if [[ "$purge" == true ]]; then
  rm -rf "$HOME/.config/chuwi-autorotate"
  rm -rf "$HOME/.local/state/chuwi-autorotate"
  echo "Uninstalled chuwi-autorotate and purged config/state."
else
  echo "Uninstalled chuwi-autorotate. Config preserved at ~/.config/chuwi-autorotate."
  echo "Run ./uninstall.sh --purge to remove config and logs too."
fi
