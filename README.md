# chuwi-autorotate

User-level automatic screen rotation for a CHUWI MiniBook running Linux Mint Xfce on X11.

This tool uses `monitor-sensor` from `iio-sensor-proxy` for accelerometer orientation, `xrandr` for display rotation, and `xinput` for touchscreen coordinate mapping. It does not assume Cinnamon, GNOME, Wayland, BIOS rotation, or root access for normal use.

## Files and Paths

- Command: `~/.local/bin/chuwi-autorotate`
- Control panel: `~/.local/bin/chuwi-autorotate-panel`
- Desktop launcher: `~/.local/share/applications/chuwi-autorotate-panel.desktop`
- Panel icon: `~/.local/share/icons/hicolor/scalable/apps/chuwi-autorotate-panel.svg`
- Config: `~/.config/chuwi-autorotate/config.conf`
- User service: `~/.config/systemd/user/chuwi-autorotate.service`
- Log: `~/.local/state/chuwi-autorotate/chuwi-autorotate.log`

## Install

Install required packages if needed:

```bash
sudo apt install iio-sensor-proxy x11-xserver-utils xinput python3-gi gir1.2-gtk-3.0
```

Install the utility:

```bash
chmod +x install.sh uninstall.sh chuwi-autorotate
./install.sh
```

The installer copies the command to `~/.local/bin`, creates config/log directories, installs the user systemd service, reloads the user systemd manager, and runs the equivalent of:

```bash
chuwi-autorotate calibrate-home
```

If `chuwi-autorotate` is not found right after installation, your current shell does not have `~/.local/bin` in `PATH` yet. Use the full path:

```bash
~/.local/bin/chuwi-autorotate calibrate-home
```

Or enable the short command in the current terminal:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

Run `calibrate-home` from the correct landscape laptop mode. The current display output and current `xrandr` rotation become HOME.

## Enable

```bash
chuwi-autorotate on
```

This sets `enabled=true`, starts and enables the user service, and prints the service status.

## Disable

```bash
chuwi-autorotate off
```

This is the OFF switch. It stops and disables the user service, writes `enabled=false`, and resets the display/touchscreen mapping to HOME.

## Emergency Reset

```bash
chuwi-autorotate reset
```

This restores the saved HOME rotation and touchscreen mapping even when auto-rotation is disabled.

If the display is awkward or unreadable, try the reset command from a terminal. From a TTY, this may be needed:

```bash
DISPLAY=:0 XAUTHORITY="$HOME/.Xauthority" ~/.local/bin/chuwi-autorotate reset
```

## Safe Rotation Tests

Each test rotates for 8 seconds and then automatically returns to HOME:

```bash
chuwi-autorotate test-rotation left
chuwi-autorotate test-rotation right
chuwi-autorotate test-rotation normal
chuwi-autorotate test-rotation inverted
```

The command prints the exact `xrandr` command it uses before applying it.

## Status and Doctor

```bash
chuwi-autorotate status
chuwi-autorotate doctor
```

`status` shows enabled state, user service state, detected output, current rotation, saved HOME rotation, LightDM greeter rotation status, `iio-sensor-proxy` status, last sensor orientation, and touchscreen devices.

`doctor` checks for `xrandr`, `xinput`, `monitor-sensor`, `iio-sensor-proxy`, an active X11 graphical session, an internal display, and a touchscreen.

## Control Panel

Open the control panel from the Mint menu as `CHUWI Autorotate`, or run:

```bash
chuwi-autorotate-panel
```

The panel is an on-demand GTK window. It shows service/display/sensor/login-screen status, includes a live sensor-orientation indicator, toggles the auto-rotation service, resets or calibrates HOME, runs `doctor`, edits safe config values, and installs or removes the LightDM login/lock screen rotation hook.

The `Status`, `Controls`, `Settings`, and `Login/Lock Screen` sections are collapsible. The sensor indicator reads the existing state file written by the autorotate service; it does not start a second accelerometer monitor.

The About dialog says `Made by Kyle Auchterlonie 2026` and links to:

```text
https://github.com/kaoshonen/chuwi-autorotate
```

## Login and Lock Screen Rotation

Linux Mint Xfce uses LightDM for the login screen, and `light-locker` uses the LightDM greeter path for locking. The user-level autorotate service starts after login, so it cannot correct the greeter by itself.

The recommended fix is to install a LightDM setup script that applies the saved HOME rotation before the greeter appears:

```bash
chuwi-autorotate greeter-install
```

This installs:

- `/usr/local/bin/chuwi-autorotate-greeter`
- `/etc/chuwi-autorotate/greeter.conf`
- `/etc/lightdm/lightdm.conf.d/80-chuwi-autorotate.conf`

The greeter setup is static by design. It uses the saved HOME display and rotation, then the normal user autorotate service takes over after login.

Check or remove it with:

```bash
chuwi-autorotate greeter-status
chuwi-autorotate greeter-uninstall
```

Override the detected values if needed:

```bash
chuwi-autorotate greeter-install --display DSI-1 --rotation right
```

## Config

The default config is:

```ini
enabled=true
display=auto
home_rotation=auto
orientation_offset=right
touch_enabled=true
debounce_seconds=1.0
startup_wait_seconds=90
blacklist_rotations=
log_file=~/.local/state/chuwi-autorotate/chuwi-autorotate.log
```

After calibration, `display` and `home_rotation` are set to the current working landscape state.

## Orientation Offset

The CHUWI panel is portrait-native, so sensor orientation does not necessarily equal display rotation. The default offset is:

```ini
orientation_offset=right
```

Default mapping with `orientation_offset=right`:

- sensor `normal` -> display `right`
- sensor `bottom-up` -> display `left`
- sensor `left-up` -> display `normal`
- sensor `right-up` -> display `inverted`

Alternative mapping with `orientation_offset=left`:

- sensor `normal` -> display `left`
- sensor `bottom-up` -> display `right`
- sensor `left-up` -> display `inverted`
- sensor `right-up` -> display `normal`

Edit `~/.config/chuwi-autorotate/config.conf` and restart with:

```bash
chuwi-autorotate off
chuwi-autorotate on
```

## Blacklist a Bad Rotation

To prevent a rotation during automatic operation:

```ini
blacklist_rotations=right
```

Multiple values can be comma or space separated:

```ini
blacklist_rotations=right inverted
```

`test-rotation` ignores the blacklist because it always auto-reverts after 8 seconds.

## Touchscreen Mapping

Touchscreen devices are detected with `xinput`. Names containing these strings are treated as touch devices:

- `touch`
- `touchscreen`
- `goodix`
- `silead`
- `wacom`

If no touchscreen is detected, display rotation still works.

## Troubleshooting

Check the environment:

```bash
chuwi-autorotate doctor
```

Watch raw sensor output:

```bash
monitor-sensor
```

View logs:

```bash
tail -f ~/.local/state/chuwi-autorotate/chuwi-autorotate.log
```

If rotation gets weird:

```bash
chuwi-autorotate off
```

If you only need to return to landscape HOME:

```bash
chuwi-autorotate reset
```

If the service does not see your display after login, run this from the Xfce desktop session:

```bash
systemctl --user import-environment DISPLAY XAUTHORITY XDG_SESSION_TYPE
chuwi-autorotate on
```

When launched early by systemd, the service waits up to `startup_wait_seconds` for Xfce to import `DISPLAY`, `XAUTHORITY`, and `XDG_SESSION_TYPE` into the user systemd environment before starting the sensor loop.

## Uninstall

```bash
./uninstall.sh
```

This stops/disables the user service and removes the installed command and service. Config and logs are preserved.

To remove config and logs too:

```bash
./uninstall.sh --purge
```
