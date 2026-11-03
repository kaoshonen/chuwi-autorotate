# Trackpad filtering and guided checks

The **Trackpad** page follows **Display & rotation** in the MiniBook X Control
Center. It manages the existing whole-pad hardware-database correction for the
internal I²C `0911:5288` touchpad. It supports both the original name ending in
`Touchpad` and the newer name without that suffix, and excludes the identically
named relative mouse interface. Missing, ambiguous, or unsupported hardware
disables Apply.

## Source preview (no installation)

From this component checkout, run:

```sh
./chuwi-trackpad-preview
```

This has a unique application identity and uses simulated trackpad status,
Apply, and measurement results. Other hardware pages are disabled; Unlock only
displays a preview message. It does not connect to the tablet guardian, start
rotation, change desktop settings, authenticate, or open an input device.
The installed application is unaffected. Preview results exported as JSON carry
`"preview": true` and must not be interpreted as measurements.

## Tuning

The slider and numeric field select an integer strength from 1 to 64. The page
loads the saved value, or starts at **8** when no saved configuration exists.
**Reset to recommended (8)** only changes the selection; **Apply** saves it.
Refreshing status preserves an unsaved selection.

Stronger filtering suppresses small movements everywhere on the pad and can
reduce fine positioning. There is no separate top/bottom filter. Increase it
gradually and compare normal pointer movement as well as lingering-finger jitter.
Measurements never adjust strength automatically.

Apply authenticates only the `chuwi-trackpad` helper through `pkexec`. The GTK
application remains unprivileged. The helper repeats strength, machine and
device checks after authentication. Cancelling authentication leaves the settings
unchanged. Trackpad work has its own worker, independent of rotation and tablet
Unlock. An authenticated save completes its transaction even if the window closes.

**Reboot after Apply.** Updating udev properties does not prove the running
desktop has reopened the input device. Each save records the boot ID and a
SHA-256 configuration digest. The page shows **Reboot needed** for that boot;
only a later boot with the same saved file clears that requirement. An older
standalone fix or a file changed outside the helper shows an unknown reboot
status. This feature never restarts the desktop, unbinds a device, or reboots.

## Guided jitter check

1. Run the check while the intermittent jitter is visible. Unlock tablet inputs
   first; the page disables capture while tablet locking/recovery is active.
2. Choose **Check jitter** and authenticate once for read-only access.
3. For each highlighted region, lift all fingers, choose **I’m ready**, then put
   one finger in that region and hold still. The helper waits up to 15 seconds
   for a single contact, excludes two seconds of settling, and measures eight
   seconds. Readiness expires after 45 seconds; the whole helper is bounded to
   210 seconds.
4. Compare the top, middle and bottom movement spreads. Export JSON if useful.

The helper reads only the discovered touchpad, never grabs it, and does not
inject events. Lifts and new contact IDs form separate segments. Multitouch
rejects the contaminated contact and requires all fingers up before another
segment. Each new segment excludes two seconds of settling within the fixed
trial window. Lost events (`SYN_DROPPED`) or device removal stop the check.
Cancel, window closure and stdin/stdout disconnection end capture and close the
device. Entering tablet locking also cancels the check.

Results show the largest per-contact X/Y peak-to-peak spread, in millimetres
when device resolution is available and in device units otherwise. JSON also
includes per-contact p5–p95 spread, rejected-contact details and axis metadata.
At least ten samples spanning six seconds in one valid contact are required for
the “sufficient samples” label. The mean Y coordinate must fall in the labelled
third; otherwise the result asks for correct placement. This is a conservative
evidence threshold, not a calibrated sensor-quality score.

Emitted events already include active kernel filtering. Sparse events mean
insufficient evidence, not zero noise. Deliberate finger movement cannot be
distinguished from jitter. Millimetres use device-reported resolution, which may
be inaccurate. A quiet check cannot rule out an intermittent problem.

## Command line and protocol

Read-only status requires no authentication:

```sh
./chuwi-trackpad status
pkexec /usr/bin/python3 -I "$PWD/chuwi-trackpad" apply 8
```

Output is JSON, including hardware discovery, saved strength, whether all four
`EVDEV_ABS_*` and `LIBINPUT_FUZZ_*` properties match, reboot state and diagnostics.
Errors have `"type":"error"` and a nonzero exit status.

The GUI starts `pkexec /usr/bin/python3 -I …/chuwi-trackpad measure` once. The
measurement protocol uses one JSON object per line on stdin/stdout. After
`{"type":"ready","region":"top",…}`, send
`{"command":"ready","region":"top"}` (and similarly middle/bottom).
Send `{"command":"cancel"}` or close stdin to stop. Output types are `started`,
`ready`, `progress` (place_finger/settling/measuring), `region_result`, `result`,
`cancelled`, and `error`. Export the final `result` object, not the progress stream.

## Persistence, rollback and removal

The helper manages only:

- `/etc/udev/hwdb.d/63-chuwi-minibook-x-jitter.hwdb`
- `/var/lib/chuwi-trackpad/applied.json` and private timestamp-independent UUID backups

It recognizes the exact previously generated standalone file and its own file
format. Unrelated content, symlinks and mismatched axes are refused. The older
`61-chuwi-minibook-x-touchpad.hwdb` and `62-chuwi-minibook-x-multitouch.hwdb` are
preserved. No dependency on the separate diagnosis workspace remains.

Each save keeps the prior file, apply record and device properties in a root-only
backup, writes atomically, rebuilds the hardware database, reloads only the
discovered touchpad and verifies all four axes. Verification or write failure
restores the prior file and record, rebuilds and verifies rollback. If recovery
itself fails, the error includes the backup identifier; it does not claim success.

The successful Apply response and status diagnostics report `backup` /
`last_backup`. To undo that save (replace `BACKUP_ID` with the reported identifier):

```sh
pkexec /usr/bin/python3 -I "$PWD/chuwi-trackpad" restore BACKUP_ID
```

Restoration also authenticates, validates the current file and backup, makes an
undo backup, and requires reboot. If the prior file was absent, restoration
removes only the managed 63 file and preserves older overrides. Backup paths
cannot be supplied by callers; only a 32-character hexadecimal identifier is
accepted.

The user installer now includes the helper and all component modules, and its
installation snapshots/restore utility cover the helper. Uninstall removes the
user application/helper but deliberately preserves the system filter and root
backups, like the existing preservation of user configuration. Restore the
desired backup **before** uninstalling, or use the source helper afterward.
Preview launch and source integration do not run the installer or modify the
published component pin.

## Validation

```sh
python3 -m unittest discover -s tests -v
bash -n install.sh uninstall.sh
```

Tests use disposable hardware-database paths and installer homes, plus an inert
GTK client. They cover identity changes and duplicate names, missing/ambiguous
devices, input bounds, old-file adoption, foreign files, authentication
cancellation, write/rebuild/verification failure, rollback and reboot tracking.
The original seven recorder protocol tests are retained. Guided-session tests
exercise readiness, progress, cancellation, contact sufficiency, lost events and
device removal. GTK tests cover linked controls, dirty refreshes, tablet Unlock,
and layouts at 760×570 and 1000×800. GTK and private D-Bus tests need a desktop
session that permits local test sockets.

Source tests do not establish hardware effectiveness. Deployment and a real
authenticated measurement/reboot comparison remain separate acceptance steps.
