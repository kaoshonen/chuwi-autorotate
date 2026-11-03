# Hardware companion implementation

This upgrade retains the application, commands, GTK3 interface, HOME calibration, X11 rotation and independent tablet guardian. Hardware dependencies remain separate. No installer or runtime command sets refresh rate, modelines, VBT, DSI clocks, fan speed or thermal limits. The observed factory mode remains **50.00 Hz**.

## Baseline and upstream comparison

The implementation started from the full unpublished working tree, not just Git HEAD `62bf481d6d1f961220593f8b8475be5c8139b0d6`. The immutable archive and per-file hashes are at `../implementation-backups/20261028T224351Z/`. It includes the previously untracked control center, guardian, recovery code and tests. Restore individual files from that archive into a separate directory before comparing; do not overwrite an active guardian before restoring input.

Upstream is pinned to `fstanis/chuwi-minibook` commit `e37d4f821ba2eda1279e79a15d517590697b7e39`. Its archive checksum and dependency decisions are in `optional/components.lock.json`. The original 103-test baseline had 99 passes and four desktop-dependent skips.

| Capability | Application baseline | Upstream | Integration |
|---|---|---|---|
| Rotation | HOME, offset, angle threshold, delay, blacklist and manual lock | Dual accelerometer filtering | Keep application policy; consume one D-Bus connection |
| Posture | Manual tablet selection | Hinge calculation and hysteresis | Versioned structured snapshot; never infer posture from `right-up` |
| Input | XInput journal, touch trial, lease and watchdog | ACPI writes and synthetic tablet switch | Suppress both upstream mechanisms; one backend per transaction |
| Recovery | Separate guardian and floating Unlock | Sensor unbind not restored | Keep guardian; optional root lease and independently journaled sensor restoration |
| Hardware UI | Basic system information | EC/sysfs, hwmon, thermal and LED interfaces | Read-only dashboard without EC dependency; bounded optional backlight controls |
| Power | Existing suspend/session callbacks | Sleep recommendations and modified thermald/DPTF | Temporary approved sleep workflow; retain stock thermald |
| Installation | User services and backups | Broad bootstrap scripts | No bootstrap use; individual source-verified packages |

The audit found a stock 3.5 proxy reporting `right-up`, proving that this label does not identify the fork. Upstream's D-Bus API did not expose hinge or real display gravity. Its EC module lacked a hardware gate and retained thermal error pointers; Goodix used moving sources and ignored patch failures; the I²C and DPTF modules did not undo all their changes on unload. These findings are the basis of the separate patches and unsupported-component gates.

## Compatibility policy

| Machine/session | Standard proxy | Extended proxy |
|---|---|---|
| MiniBook X N150, Cinnamon or Xfce X11 | HOME until protected manual tablet selection or explicit rotation | HOME in laptop posture; calibrated tablet rotation; automatic input opt-in |
| Older MiniBook, Cinnamon or Xfce X11 | Preserve calibrated orientation, threshold and settling behavior | Core interface supported; N150 dependencies are not approved for this machine |
| Other X11 hardware | Existing orientation behavior; hardware-specific controls unavailable | No hardware guarantee |
| Wayland | Read-only audit; XRandR/XInput unsupported | Read-only audit; XRandR/XInput unsupported |

An unmodified upstream proxy owns input. Application input locking refuses a detected virtual tablet switch or unverified/competing provider. Discovery uses extension introspection and executable/package provenance; versions and orientation labels alone are insufficient. Provenance is evidence of the installed package, not a cryptographic authenticity attestation.

## Runtime boundaries

- `chuwi.sensors`: one Gio claim, owner replacement, generation invalidation, release and optional snapshot parsing. Calibration reads the running controller. A temporary claim is allowed only while it is stopped.
- `chuwi.policy`: effective laptop/tablet/unknown/inhibited state; HOME, manual override, stale-data and calibration precedence. Existing guardian transaction states (`off`, `trial`, `locked`, `recovering`, `error`) remain compatible with the existing client; they correspond to idle/trial/locked/restoring/fault.
- `chuwi.display` and `chuwi.events`: internal connector selection, EDID identity or DSI connector fallback, affine composition, RandR notifications and a five-second geometry fallback. Repeated unexplained changes pause writes after three observations in 15 seconds.
- `chuwi_tablet`: exclusive backend transactions, preserved original state, ten-second touchscreen confirmation, five-second lease, and restoration journal. Identity changes invalidate prior trial confirmation. EC snapshots record both input states before writing either.
- `chuwi.profiles`, `chuwi.inputs`, `chuwi.osk`: Cinnamon/Xfce locker/settings selection, explicit internal HID enrollment, and tablet-scoped keyboard preference ownership. A later user setting is preserved when it differs from the value set by the application. An Onboard process started by this scope is identified by PID and process start time before cleanup; an existing process is retained.
- `chuwi.hardware`: named thermal/hwmon discovery, CPU-die versus estimated EC-area readings, ambiguous zero fan RPM, read-only PWM, battery, RAPL and sleep evidence. Thermals refresh every five seconds while the Hardware section is visible; battery/power summaries cache for 30 seconds. Static inventory is cached by the panel. Selected s2idle is not proof of S0ix residency.
- `chuwi.ec` and the separately packaged root helper: authenticated system-bus identity, active local X11 session, polkit authorization, fixed sysfs attributes, five-second lease and ExecStopPost recovery. No arbitrary paths, register access or tuning methods. Authorization precedes the watchdog-controlled transaction.

Display calibration records provider/frame, connector identity, DRM-derived panel orientation, reflection and affine transform. It preserves `orientation_offset`; a changed fingerprint requires HOME validation. The extended frame is before DRM panel compensation, so the application does not compensate a second time. Touch mapping composes desktop viewport, affine output transform, reflection, rotation and saved calibration. Singular/perspective transforms, panning or ambiguous internal touch devices produce a diagnostic rather than an overwrite. Physical corners/center validation remains required.

## User workflow

Run `chuwi-autorotate audit --json`, `doctor --json` or `status --json` for versioned, read-only records. `./install.sh --dry-run` does not create configuration, start services, claim sensors or change hardware. Diagnostic categories are exactly Working, Optional, Missing, Unsupported, Conflicting and Requires attention; each check has evidence, explanation and a recommended action.

The user-level `./install.sh` installs only application files. An existing configuration is migrated atomically with a byte-for-byte versioned backup. Fresh installations start disabled and require HOME calibration. Missing optional sensors are nonfatal. Upgrade failures restore the saved installation; `restore-install.py BACKUP` also restores saved service state after input recovery. Uninstall preserves configuration by default. Optional system packages are handled separately.

For ambiguous internal HID devices, inspect `udevadm info --query=property --name=/dev/input/eventN`, physically verify the device, then enroll its exact `ID_PATH`:

```
chuwi-autorotate enroll-input keyboard /dev/input/eventN --identity 'verified ID_PATH'
```

The role may be keyboard, touchpad or touchscreen. Generic USB/Bluetooth/virtual devices never qualify automatically or through this internal-device enrollment. Enrollment does not disable input and does not replace the touch trial.

Automatic tablet operation starts disabled. With a validated extended provider, select Automatic tablet mode and complete the ten-second touchscreen-only trial. Confirmation is tied to the client and identified backend hardware; a new recovery client requires a new trial. Closing the main window keeps the recovery window and its heartbeat alive. Keyboard preferences are optional. Emergency Unlock, a failed/expired trial, lost ownership, suspend or screen lock restores input and inhibits automatic relocking. Toggle automatic operation off/on or explicitly start a new protected trial to rearm. A recovery error retains its journal and prevents another disable attempt.

## Separately gated dependencies

See [optional component workflow](../optional/README.md). Packages are staging artifacts, not compatibility guarantees. The exact stock proxy package and unit/binding observations were preserved in `.build/stock-rollback/`; its Debian archive SHA256 is `5b17f67e97ae34e4b245b5303953ca2159d7ad95e2f273584e95365fa756e979`.

Only one MDA6655 device is presently enumerated on BIOS V2.50. The extended package refuses broad bus probing and requires two individually identified/enrolled sensors. Its real activation is therefore **Requires attention** until the second sensor's identity and display/base ordering are established. Default matrices are not proof of calibration on this BIOS.

Goodix replacement remains **Unsupported** without a reproduced fault, exact Ubuntu source comparison, fail-closed patch application and verified configuration-blob redistribution rights. I²C spike suppression remains **Unsupported** pending bus concurrency and complete restoration. DPTF changes remain **Unsupported** because unloading does not reverse firmware-memory changes. Stock thermald remains installed; no power-limit write test is part of this implementation.

## Recovery

| Condition | Procedure |
|---|---|
| Wrong orientation | `chuwi-autorotate reset`; `off` returns HOME and releases application input locks |
| Input disabled | Floating Unlock or `chuwi-autorotate tablet-mode off`; watchdog and lease recover independently |
| Recovery reports error | Keep the journal. Use external input or a TTY; retry restoration. Do not start another backend or remove the module until original states are verified |
| Proxy failure | Stop extended service, run its binding restore, reinstall the saved exact distro package, restore saved unit/drop-ins and validate the standard D-Bus interface before restarting the user controller |
| EC module failure | Restore helper input first. Remove only that component's persistence/DKMS registration, retaining the working kernel boot option. A reboot or module load/unload is a separately approved action |
| Temporary sleep test | Independent ExecStopPost restores the previous selected mode; failed restoration keeps its journal |
| Application upgrade failure | Restore the recorded installation snapshot after input restoration |

No procedure here changes refresh rate, BIOS settings, boot parameters, display firmware, thermal limits or fan control. Never use `rmmod` as a claimed reversal of I²C/DPTF firmware changes.

## Acceptance boundaries

[Validation record](validation/README.md) distinguishes mock, private-bus, inert desktop, kernel-build and physical results. A build or simulation cannot establish EC register compatibility, sensor calibration, lockdown behavior, suspend reliability, charger-sensor accuracy or the older machine's physical identities. No phase requiring those observations is marked physically accepted.
