# chuwi-autorotate

User-level automatic screen rotation for a CHUWI MiniBook running Linux Mint Cinnamon or Xfce on X11.

## Hardware companion upgrade

The [implementation guide](docs/hardware-companion.md) describes compatibility, calibration, recovery and the [separate optional packages](optional/README.md). See the [validation record](docs/validation/README.md) for what was tested and what still needs physical approval. **Factory refresh rate remains 50 Hz; refresh-rate controls are excluded.**

```bash
./install.sh --dry-run                # Read-only; no configuration or sensor claims
chuwi-autorotate audit --json
chuwi-autorotate status --json
chuwi-autorotate doctor --json
```

N150 systems hold calibrated HOME until manual tablet selection or a validated hinge provider is available. Older-machine autorotation is preserved. The new Hardware section works without EC drivers. Automatic tablet operation is opt-in and requires the touchscreen recovery trial; it may continue after closing the main window while the floating recovery window remains available.

## MiniBook X Control Center

The new native GTK panel combines rotation controls, live device health, links to Cinnamon's power/display settings, and the earlier MiniBook projects. Launch **CHUWI Control Center** from the Mint menu or run `~/.local/bin/chuwi-control-panel`. It uses the existing `chuwi-autorotate` service and configuration; there is only one rotation agent.

- Auto rotate follows one claimed `iio-sensor-proxy` D-Bus connection, with posture precedence and a configurable settling delay.
- **Display & rotation → Rotation sensitivity** controls the required turn angle and steady-posture delay. Higher angles require more deliberate turns. For example, 65° requires the next upright posture to be within about 25° of vertical before starting the delay; brief movements reset that delay. Settings are saved and take effect immediately.
- Rotation lock persists across sign-in. Manual orientation buttons also enable the lock.
- Start at sign-in applies calibrated HOME, respecting an explicit manual rotation lock, before following the sensor policy. It works without opening the panel. The desktop autostart entry imports the current X11 session environment before starting the user service.
- HOME calibration measures the current sensor posture and display rotation together. Old-machine offsets and input IDs are not copied.
- Touch mapping excludes touchpads, keyboard nodes and pen-only devices, and accounts for the internal panel's position within a multi-monitor desktop.
- The agent consumes RandR changes and rechecks geometry at most every five seconds as fallback. Sensor service replacements are reclaimed over D-Bus.
- **Tablet mode** manually locks the verified internal keyboard and touchpad, with a touchscreen-only trial and independent automatic recovery. External input, touch and power buttons remain available.
- **Screen keyboard** opens or hides Cinnamon's built-in keyboard. It appears beside Tablet mode and in the floating recovery controls, so it stays available while other apps are open.
- Orientation, laptop view, calibration, diagnostics and settings use illustrated action tiles. Smaller matching pictures accompany Save, navigation, project links and tablet recovery controls; tooltips and accessible names retain the action descriptions. These static vector pictures require no external assets.

Install from your normal Cinnamon/X11 desktop, in your usual upright laptop position, with the display already correct. `./install.sh` runs read-only dependency and capability checks, backs up affected files and installs the panel. Fresh installation starts disabled: calibrate HOME and select sign-in startup explicitly. Updates preserve existing rotation settings and migrate configuration with a reversible backup. Then use the temporary rotation tests below before `chuwi-autorotate on`. Manual tablet mode remains available; automatic input switching requires the separate trial and validated sensor extension. No LightDM, kernel, brightness, suspend or firmware settings are changed.

Additional commands:

```bash
chuwi-autorotate lock on             # Hold the current view
chuwi-autorotate lock off            # Resume sensor rotation
chuwi-autorotate rotate right        # Manual view, with rotation lock
chuwi-autorotate startup off         # Disable future sign-in startup
chuwi-autorotate startup on          # Enable future sign-in startup
chuwi-autorotate configure debounce_seconds 0.8
chuwi-autorotate sensitivity 65 1.5   # Required turn and steady-posture delay
chuwi-autorotate sensitivity 45 1     # Previous sensor-based behavior
```

The threshold ranges from 45° to 85°. At 45° the existing sensor proxy decides when to switch. Higher thresholds consume actual display gravity from the extension, or use the read-only IIO sampler with the stock proxy. They honor the provider coordinate frame. Flat screens, partial turns and stale orientation labels cannot qualify a new rotation. An unavailable/ambiguous angle sensor holds the current view and reports an error; manual rotation and rotation lock still work. Touch geometry repairs retain the current accepted orientation while a new turn is pending.

`on` starts/restarts the agent now; sign-in persistence is controlled separately by `startup`. The user service intentionally stays disabled at `default.target`, preventing an early start with stale graphical credentials. Closing the panel leaves the agent running. Auto rotation off returns to HOME; sign-in startup still applies HOME if enabled.

Installation backups are under `~/.local/state/chuwi-autorotate/backups/`. The installer prints the exact restore command. `python3 restore-install.py BACKUP_DIRECTORY` restores affected files and previous service state; logs and backups remain. `./uninstall.sh` also removes the new panel and autostart entry, preserving config/logs unless `--purge` is explicitly selected. Turning off startup and auto rotation is the immediate reversible stop:

```bash
~/.local/bin/chuwi-autorotate startup off
~/.local/bin/chuwi-autorotate off
```

The legacy panel remains available as `chuwi-autorotate-panel`; its tablet button opens the protected controls in Control Center. Greeter changes require a separate hardware assessment. Brightness and suspend workarounds remain in their respective component repositories.

## Protected tablet mode

Open **CHUWI Control Center → Display & rotation → Tablet mode** before folding the display back. First activation starts a 10-second test. Tap **Tap here to keep tablet mode** with the touchscreen to keep the internal keyboard and touchpad locked; without that touch they automatically return. Mouse clicks and keyboard activation cannot confirm. A new panel connection repeats the trial; a confirmed connection may reactivate without repeating it while the guardian remains running.

The square illustrated tile previews laptop → fold → tablet on hover or keyboard focus. Active mode uses a teal locked-tablet illustration and **Unlock inputs** caption. The vector artwork scales cleanly, needs no external image files, and animation stops when hidden or when desktop animations are disabled. Accessible names retain the full action descriptions.

The floating **Unlock keyboard & touchpad** button stays above other apps, including fullscreen windows, and moves into the screen's workarea after rotation. Unlock also appears in the main panel. Closing the main window restores manual tablet locks. With automatic operation enabled, it keeps the separate recovery window alive; closing recovery controls releases input. Rotation actions and background jobs do not share the tablet controls' worker queue.

**Screen keyboard** is a manual show/hide control using Cinnamon's native `ToggleKeyboard` session D-Bus method. The floating window does not accept focus, keeping the app's text field ready for typing. It sits on the opposite edge from Cinnamon's configured keyboard position so the keyboard does not cover Unlock. Requests are asynchronous and time-limited; keyboard failure leaves input recovery available. No extra keyboard package is needed on Cinnamon.

Tap a text field, then tap the floating **Screen keyboard** button to type. Tap again to hide it. Cinnamon's command enables its keyboard when first opened; it preserves the existing activation mode, position and size. This button does not install an auto-popup rule or restore that native enable preference on leaving tablet mode. Use Cinnamon's Accessibility → Virtual keyboard settings to change those desktop preferences. The manual route remains available for apps whose automatic text-focus reporting is unreliable.

The independent, on-demand `chuwi-tablet-guard.service` owns the lock over session D-Bus. Only the starting panel connection may renew or confirm it. Missing heartbeats start recovery after five seconds; panel disconnect, screen lock and suspend/resume restore input. A five-second systemd watchdog kills a stuck guardian and its independent `ExecStopPost` command restores the recorded states. The guardian refuses activation when these protections, a supported Cinnamon/Xfce screen-lock monitor or an enabled internal touchscreen are unavailable.

Targets require XInput device nodes, udev properties and internal sysfs paths: an i8042 keyboard and an I²C touchpad. Device names alone never qualify, and each change rediscovers the physical device's current XInput ID. Disabled devices' floating-slave entries remain identifiable. Original enabled states are journaled with private permissions in `$XDG_RUNTIME_DIR/chuwi-tablet` before any disable; failed recovery retains that record for retry. A shared transaction lock prevents simultaneous activation and emergency restoration. Touchscreen keyboard entries, USB/Bluetooth input and power buttons are excluded.

Status and emergency recovery are available from a terminal:

```bash
~/.local/bin/chuwi-autorotate tablet-mode status
~/.local/bin/chuwi-autorotate tablet-mode off
```

There is no terminal command to enable an unprotected lock. Tablet mode is never saved as enabled across sign-in or reboot. Automatic fold detection and tablet-scoped automatic keyboard popups are deferred. Updates, uninstall and installation rollback restore input before replacing or removing recovery files, and stop on restoration errors.

Mocked tests cover device identity, recycled IDs, floating disabled devices, partial failures, trial/lease deadlines, ownership, screen-lock/sleep callbacks and canceled activation. Hardware acceptance should also include touchscreen confirmation and Unlock taps, external input, actual lock/suspend/logout, and every screen orientation. Automated command checks alone cannot establish physical touch usability.

On a desktop with a touchscreen, the suite also exercises real GTK button event dispatch with an inert tablet client. Confirmation observes touch in the capture phase and accepts touchscreen-origin pointer emulation as well as native touch events; both still require a touchscreen source. This prevents GtkButton's touch-to-pointer translation from swallowing valid confirmation taps. These dispatch tests skip when the desktop or required devices are unavailable.

Development checks: `python3 -m unittest discover -s tests -v`, Python compilation, and `bash -n install.sh uninstall.sh`. Physical sensor postures, touch corner taps, reboot and suspend/resume require testing on the target laptop; command success alone does not establish those results.

This tool uses Gio/D-Bus with `iio-sensor-proxy` for accelerometer orientation, `xrandr` for display rotation, and `xinput` for touchscreen coordinate mapping. It does not assume Cinnamon, GNOME, Wayland, BIOS rotation, or root access for normal use.

## Files and Paths

- Command: `~/.local/bin/chuwi-autorotate`
- Control panel: `~/.local/bin/chuwi-autorotate-panel`
- Desktop launcher: `~/.local/share/applications/chuwi-autorotate-panel.desktop`
- Panel icon: `~/.local/share/icons/hicolor/scalable/apps/chuwi-autorotate-panel.svg`
- Config: `~/.config/chuwi-autorotate/config.conf`
- User service: `~/.config/systemd/user/chuwi-autorotate.service`
- Log: `~/.local/state/chuwi-autorotate/chuwi-autorotate.log`

## License

This project is licensed under the GNU Affero General Public License v3.0. See `LICENSE`.

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

The installer copies the command to `~/.local/bin`, creates config/log directories, installs the user systemd service, reloads the user systemd manager, and leaves a fresh configuration disabled. Calibrate HOME explicitly with:

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

This sets `enabled=true`, starts/restarts the user service, and prints its status. The desktop autostart entry controls future sign-ins separately.

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
XDG_SESSION_TYPE=x11 DISPLAY=:0 XAUTHORITY="$HOME/.Xauthority" ~/.local/bin/chuwi-autorotate reset
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

`doctor` reports hardware, session, display, calibration, proxy provenance, input, sensors, power, sleep, thermald, module and DKMS evidence. JSON output is versioned and read-only. Optional capability failures do not prevent core installation.

## Control Panel

Open the control panel from the Mint menu as `CHUWI Autorotate`, or run:

```bash
chuwi-autorotate-panel
```

The panel is an on-demand GTK window. It shows service/display/sensor/login-screen status, includes a live sensor-orientation indicator, toggles the auto-rotation service, resets or calibrates HOME, runs `doctor`, edits safe config values, installs or removes the LightDM login-screen rotation hook, provides a manual tablet input lock, and can restart the sensor stack when rotation stops responding.

The `Status`, `Controls`, `Troubleshooting`, `Tablet Mode`, `Settings`, and `Login/Lock Screen` sections are collapsible. The sensor indicator reads the existing state file written by the autorotate service; it does not start a second accelerometer monitor.

The `Restart Sensor Stack` troubleshooting button runs `pkexec systemctl restart iio-sensor-proxy.service`, then restarts the user `chuwi-autorotate.service`. Use it when the service is running but the screen stops responding to rotation; it may prompt for administrator authentication.

The legacy tablet action opens the protected controls in **CHUWI Control Center**. Its historical Onboard routines are not used by that action. The current identity checks, recovery guarantees and Cinnamon screen-keyboard button are described under Protected tablet mode above.

The About dialog says `Made by Kyle Auchterlonie 2026` and links to:

```text
https://github.com/kaoshonen/chuwi-autorotate
```

## Login-screen orientation

On Linux Mint Cinnamon or Xfce using LightDM, the user rotation service starts after sign-in and cannot correct the initial login screen. The optional LightDM helper applies a fixed HOME orientation before the greeter appears. Sensor-based rotation resumes in the user's desktop session.

Install from your normal desktop with the display already correctly calibrated:

```bash
chuwi-autorotate greeter-install
```

This installs:

- `/usr/local/bin/chuwi-autorotate-greeter`
- `/etc/chuwi-autorotate/greeter.conf`
- `/etc/lightdm/lightdm.conf.d/80-chuwi-autorotate.conf`
- `/etc/chuwi-autorotate/greeter-originals.json` (private backup reference)

Administrator authentication is required for these system files. The installer verifies LightDM, refuses conflicting setup hooks, and backs up every affected file (including absence) under `/var/backups/chuwi-autorotate/`. Updates retain the original baseline for uninstall and also create a separate rollback snapshot. A failed installation restores its snapshot.

The helper rotates only the selected internal display. Touch alignment uses device-node, udev and sysfs identity to select enabled internal I²C touchscreen pointers; touchscreen keyboard entries, touchpads and external input are excluded. It never disables input. Configuration is read as data, commands have time limits, and a missing device or helper failure lets the login screen continue.

The change applies after the next reboot or LightDM restart. **Do not restart LightDM with unsaved work open: it ends the graphical session.** Installing the helper leaves the current desktop running. Test the actual login screen and touchscreen after reboot before marking the feature accepted.

Cinnamon's screen locker is separate from LightDM. Xfce installations using `light-locker` may also use the LightDM greeter when locking. This helper does not change the boot splash or provide sensor rotation before sign-in.

Check it, or restore the setup that preceded the first installation:

```bash
chuwi-autorotate greeter-status
chuwi-autorotate greeter-uninstall
```

To restore a particular installer snapshot instead, use the backup directory printed by installation:

```bash
chuwi-autorotate greeter-restore /var/backups/chuwi-autorotate/greeter-TIMESTAMP
```

Both restoration commands require administrator authentication. Remove the login helper before uninstalling the user tools. Override the detected internal display and HOME orientation when needed (detect these on each machine):

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
tablet_keyboard_enabled=true
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

Touchscreen devices are detected with `xinput`. Only slave pointer entries qualify; touchpads and keyboard nodes are excluded. Names must identify a touchscreen using:

- `touchscreen` or `touch screen`
- `goodix`
- `silead`
- `wacom` together with `touch` (pen-only devices are excluded)

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

## Trackpad jitter controls

The Control Center includes a Trackpad page for whole-pad filter strength (1–64,
recommended 8), explicit authenticated Apply with reboot tracking, and a guided
top/middle/bottom jitter check. See [Trackpad usage and rollback](docs/TRACKPAD.md).
Run `./chuwi-trackpad-preview` for an isolated source preview with simulated
hardware actions; it does not install or change the running control center.
