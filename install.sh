#!/usr/bin/env bash
set -euo pipefail

script_dir="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

bin_dir="$HOME/.local/bin"
config_dir="$HOME/.config/chuwi-autorotate"
state_dir="$HOME/.local/state/chuwi-autorotate"
service_dir="$HOME/.config/systemd/user"
applications_dir="$HOME/.local/share/applications"
icon_dir="$HOME/.local/share/icons/hicolor/scalable/apps"
desktop_file="$applications_dir/chuwi-autorotate-panel.desktop"

install -d "$bin_dir" "$config_dir" "$state_dir" "$service_dir" "$applications_dir" "$icon_dir"
install -m 0755 "$script_dir/chuwi-autorotate" "$bin_dir/chuwi-autorotate"
install -m 0755 "$script_dir/chuwi-autorotate-panel" "$bin_dir/chuwi-autorotate-panel"
install -m 0644 "$script_dir/chuwi-autorotate.service" "$service_dir/chuwi-autorotate.service"
install -m 0644 "$script_dir/chuwi-autorotate-panel.desktop" "$desktop_file"
sed -i "s|^Exec=.*|Exec=$bin_dir/chuwi-autorotate-panel|" "$desktop_file"
install -m 0644 "$script_dir/icons/hicolor/scalable/apps/chuwi-autorotate-panel.svg" "$icon_dir/chuwi-autorotate-panel.svg"

"$bin_dir/chuwi-autorotate" calibrate-home

path_contains() {
  local needle="$1"
  local part
  local -a path_parts

  IFS=: read -r -a path_parts <<< "${PATH:-}"
  for part in "${path_parts[@]}"; do
    if [ "$part" = "$needle" ]; then
      return 0
    fi
  done
  return 1
}

if path_contains "$bin_dir"; then
  chuwi_cmd="chuwi-autorotate"
  path_note=""
else
  chuwi_cmd="$bin_dir/chuwi-autorotate"
  path_note="
Note: $bin_dir is not in your current PATH.
Use the full command path below, or enable the short command in this terminal with:
  export PATH=\"$bin_dir:\$PATH\"
"
fi

if command -v systemctl >/dev/null 2>&1; then
  systemctl --user import-environment DISPLAY XAUTHORITY XDG_SESSION_TYPE || true
  systemctl --user daemon-reload || true
fi

if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database "$applications_dir" >/dev/null 2>&1 || true
fi

if command -v gtk-update-icon-cache >/dev/null 2>&1; then
  gtk-update-icon-cache -q "$HOME/.local/share/icons/hicolor" >/dev/null 2>&1 || true
fi

cat <<EOF

Installed chuwi-autorotate.
$path_note

Recalibrate HOME if needed:
  $chuwi_cmd calibrate-home

Enable auto-rotation:
  $chuwi_cmd on

Disable auto-rotation and reset to HOME:
  $chuwi_cmd off

Emergency reset to HOME:
  $chuwi_cmd reset

Open the control panel:
  chuwi-autorotate-panel
EOF
