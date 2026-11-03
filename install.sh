#!/usr/bin/env bash
set -euo pipefail

script_dir="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

if [[ "${1:-}" == --dry-run || "${1:-}" == audit ]]; then
  exec python3 "$script_dir/chuwi-autorotate" audit --json
elif [[ $# -gt 0 ]]; then
  echo "usage: ./install.sh [--dry-run|audit]" >&2
  exit 2
fi

if [[ "$EUID" == 0 || "${XDG_SESSION_TYPE:-}" != x11 || -z "${DISPLAY:-}" ]]; then
  echo "Install as your normal user from the Cinnamon/Xfce X11 desktop." >&2
  exit 1
fi
for dependency in python3 xrandr xinput udevadm systemctl; do
  command -v "$dependency" >/dev/null || { echo "Missing: $dependency" >&2; exit 1; }
done
python3 -c "import gi; gi.require_version('Gtk','3.0'); from gi.repository import Gtk"
"$script_dir/chuwi-autorotate" doctor
bin_dir="$HOME/.local/bin"
config_dir="$HOME/.config/chuwi-autorotate"
state_dir="$HOME/.local/state/chuwi-autorotate"
service_dir="$HOME/.config/systemd/user"
applications_dir="$HOME/.local/share/applications"
icon_dir="$HOME/.local/share/icons/hicolor/scalable/apps"
desktop_file="$applications_dir/chuwi-autorotate-panel.desktop"

prior_active=$(systemctl --user is-active chuwi-autorotate.service || true)
prior_enabled=$(systemctl --user is-enabled chuwi-autorotate.service || true)
systemctl --user stop chuwi-autorotate.service || true
# Updates always restore input before replacing guardian/recovery code.
if systemctl --user cat chuwi-tablet-guard.service >/dev/null 2>&1; then
  systemctl --user stop chuwi-tablet-guard.service
fi
if [[ -x "$bin_dir/chuwi-tablet-guard" ]]; then
  "$bin_dir/chuwi-tablet-guard" --restore
fi

backup_dir="$state_dir/backups/$(date +%Y%m%d-%H%M%S)-$$"
mkdir -p "$backup_dir"
python3 - "$backup_dir" "$HOME" "$prior_active" "$prior_enabled" <<'PY'
import json, pathlib, shutil, subprocess, sys
backup, home = map(pathlib.Path, sys.argv[1:3])
paths = [".local/bin/chuwi-autorotate", ".local/bin/chuwi-autorotate-panel",
         ".local/bin/chuwi-control-panel", ".local/bin/chuwi-trackpad", ".config/chuwi-autorotate/config.conf", ".local/bin/chuwi",
         ".config/systemd/user/chuwi-autorotate.service",
         ".config/autostart/chuwi-autorotate.desktop",
         ".local/share/applications/chuwi-autorotate-panel.desktop",
         ".local/share/applications/chuwi-control-panel.desktop",
         ".local/bin/chuwi-tablet-guard", ".local/bin/chuwi_tablet.py", ".local/bin/chuwi_tablet_client.py",
         ".config/systemd/user/chuwi-tablet-guard.service",
         ".local/share/icons/hicolor/scalable/apps/chuwi-autorotate-panel.svg"]
manifest = []
for index, relative in enumerate(paths):
    target = home / relative
    manifest.append({'path': relative, 'existed': target.exists(), 'backup': str(index)})
    if target.exists():
        if target.is_dir(): shutil.copytree(target, backup / str(index))
        else: shutil.copy2(target, backup / str(index))
states = {'is-active':sys.argv[3], 'is-enabled':sys.argv[4]}
(backup/'manifest.json').write_text(json.dumps({'files':manifest,'service':states},indent=2)+'\n')
PY
echo "Installation backup: $backup_dir"
rollback() {
  trap - ERR INT TERM
  echo "Upgrade interrupted; restoring installation snapshot." >&2
  python3 "$script_dir/restore-install.py" "$backup_dir" || echo "Recovery requires attention: $backup_dir" >&2
  exit 1
}
trap rollback ERR INT TERM
systemctl --user stop chuwi-autorotate.service || true

install -d "$bin_dir" "$config_dir" "$state_dir" "$service_dir" "$applications_dir" "$icon_dir"
install -m 0755 "$script_dir/chuwi-autorotate" "$bin_dir/chuwi-autorotate"
install -m 0755 "$script_dir/chuwi-autorotate-panel" "$bin_dir/chuwi-autorotate-panel"
install -m 0755 "$script_dir/chuwi-control-panel" "$bin_dir/chuwi-control-panel"
install -m 0755 "$script_dir/chuwi-trackpad" "$bin_dir/chuwi-trackpad"
install -m 0755 "$script_dir/chuwi-tablet-guard" "$bin_dir/chuwi-tablet-guard"
install -d "$bin_dir/chuwi"
install -m 0644 "$script_dir"/chuwi/*.py "$bin_dir/chuwi/"
install -m 0644 "$script_dir/chuwi_tablet.py" "$script_dir/chuwi_tablet_client.py" "$bin_dir/"
install -m 0644 "$script_dir/chuwi-tablet-guard.service" "$service_dir/chuwi-tablet-guard.service"
install -m 0644 "$script_dir/chuwi-autorotate.service" "$service_dir/chuwi-autorotate.service"
install -m 0644 "$script_dir/chuwi-autorotate-panel.desktop" "$desktop_file"
install -m 0644 "$script_dir/chuwi-control-panel.desktop" "$applications_dir/chuwi-control-panel.desktop"
sed -i "s|^Exec=.*|Exec=$bin_dir/chuwi-control-panel|" "$applications_dir/chuwi-control-panel.desktop"
sed -i "s|^Exec=.*|Exec=$bin_dir/chuwi-autorotate-panel|" "$desktop_file"
install -m 0644 "$script_dir/icons/hicolor/scalable/apps/chuwi-autorotate-panel.svg" "$icon_dir/chuwi-autorotate-panel.svg"

python3 - "$script_dir" "$config_dir/config.conf" <<'PYCONFIG'
import pathlib, sys
sys.path.insert(0, sys.argv[1])
from chuwi.config import migrate
path = pathlib.Path(sys.argv[2])
if path.exists():
    migrate(path)
    print("Preserved calibration, sensitivity and startup settings.")
else:
    import runpy
    engine = runpy.run_path(str(pathlib.Path(sys.argv[1]) / "chuwi-autorotate"))
    config = engine['read_config'](create=False)
    config['enabled'] = 'false'
    engine['write_config'](config)
    print("Installed with automatic rotation disabled. Calibrate HOME before enabling it.")
PYCONFIG

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
  systemctl --user import-environment DISPLAY XAUTHORITY XDG_SESSION_TYPE XDG_CURRENT_DESKTOP DESKTOP_SESSION || true
  systemctl --user daemon-reload || true
fi

if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database "$applications_dir" >/dev/null 2>&1 || true
fi

if command -v gtk-update-icon-cache >/dev/null 2>&1; then
  gtk-update-icon-cache -q "$HOME/.local/share/icons/hicolor" >/dev/null 2>&1 || true
fi

if python3 - "$backup_dir/manifest.json" <<'PYSTATE'
import json, sys
sys.exit(0 if json.load(open(sys.argv[1]))['service']['is-active'] == 'active' else 1)
PYSTATE
then
  systemctl --user start chuwi-autorotate.service
fi
trap - ERR INT TERM

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
  $bin_dir/chuwi-control-panel

Restore the previous installation:
  python3 "$script_dir/restore-install.py" "$backup_dir"
EOF
