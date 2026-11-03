# Optional components: build and review, then approve separately

The core installer never installs these components. `build.py` only verifies sources, applies exact patches and builds staging packages. It does not run upstream installers, remove packages, register DKMS modules, enable services, load drivers, enroll keys or modify boot settings.

Sources are pinned in `components.lock.json`. Maintain external GPL patches under `patches/`; do not copy kernel implementations into the AGPL application. The EC patch removes BIOS unlock, adds an exact vendor/model, N150 CPU and DNN20 firmware-family gate plus `hardware_validated=1`, and fixes thermal registration/enable failure cleanup. The proxy patch preserves the standard API and adds the extension described below. AGPL helper/application sources, GPL proxy sources and GPL kernel sources are separately included/archived with their notices. Do not redistribute the unverified Goodix blob.

## Reproduce and stage

```
python3 optional/build.py prepare .build/upstream.tar.gz .build/reproduction
make -C /lib/modules/7.0.0-38-generic/build M="$PWD/.build/reproduction/modules/minibook_ec" modules
meson setup .build/proxy-build .build/reproduction/iio-sensor-proxy -Dgtk-tests=false -Dgtk_doc=false -Dtests=false -Dssc-support=disabled
meson compile -C .build/proxy-build
meson test -C .build/proxy-build --print-errorlogs
python3 optional/build.py package ec --source .build/reproduction --output dist
python3 optional/build.py package proxy --source .build/reproduction --output dist --proxy-build .build/proxy-build
python3 optional/build.py package helper --source .build/reproduction --output dist
```

Use fresh destination/staging directories; the builder refuses existing package roots. `-Dtests=false` disables upstream's Python integration suite requiring dbusmock/umockdev; the four native C test executables are still built and run. The application has an additional isolated Gio/D-Bus integration test. Local build dependencies were downloaded/extracted under `.build/sysroot`, with no package installation; see the validation build-environment record.

The signed module in `dist/kernel-7.0.0-38-generic/` uses a **staging-only, unenrolled certificate**. It is not a trusted production module and has not been loaded. Keep Secure Boot enabled. Before an approved deployment, sign with a key actually trusted by the target kernel, verify trust and `modinfo` for that exact kernel, and record the result. Never package the private signing key. DKMS sources ship with `AUTOINSTALL=no`, no maintainer-script build and no autoload configuration. Build/sign/verify each intended kernel individually and retain the currently working boot option.

## Package preview and rollback material

```
python3 optional/preview.py package /absolute/path/to/component.deb
python3 optional/preview.py preserve-stock /new/workspace/rollback-directory
```

The package preview runs `apt-get --simulate` and shows dependencies, replacements, service implications and a checksum. It does not install. Capturing rollback material downloads the exact installed stock proxy version and copies unit configuration plus identified bindings into the requested directory; no installed state is changed. A failed exact-version download blocks replacement. The simulation for the proxy will propose removing stock `iio-sensor-proxy`; review that concrete output before separate authorization. Keep the captured package, checksums and original unit/drop-ins through physical acceptance.

After approval, installation must be followed by verification of the installed files, systemd units and package status. Keep automatic input switching off and observe sensor snapshots first. No maintainer script in these packages activates hardware. The optional helper is D-Bus activated only by a requested operation. The proxy requires its explicit enrollment and service activation steps. Check the removal simulation before uninstalling; restore input and sensor bindings first. A partially installed package is not permission to proceed with hardware activation.

## EC activation prerequisites

Physical verification of the exact DMI/BIOS/EC combination, trusted signing, external recovery access and separate module-load approval are required. A brand match is insufficient. After an approved load, the helper still refuses writes without a root-owned, non-writable `/etc/chuwi-autorotate/hardware-approved.json` containing `enabled: true` and exact `vendor`, `model`, `bios`, `ec_version` values. No approval manifest is shipped. Do not set it merely because the module built successfully.

Helper D-Bus name/path/interface are `io.github.kaoshonen.ChuwiHardware1`, `/io/github/kaoshonen/ChuwiHardware1` and the same interface name. Methods are Capabilities, Authorize, Acquire, SetInputs(bb), Renew, Restore, SetBacklight(u), SetBacklightTimer(b). Results are JSON strings. A successful Authorize is limited to the active user for five minutes; Acquire creates a five-second unique-caller lease. Renewal and writes recheck the active local X11 session. Root recovery survives user-service loss through the helper watchdog and ExecStopPost. Both original input values are journaled; keyboard writes precede touchpad writes because LTSM can affect both. The journal is removed only after readback verifies restoration.

## Sensor extension and binding ownership

The optional service exports read-only `Snapshot: a{sv}` on `io.github.kaoshonen.ChuwiSensor1`, path `/net/hadess/SensorProxy`. Fields:

| Field | Type and meaning |
|---|---|
| version | uint32, currently 1 |
| valid | bool; false after suspension, loss, stop or read failure |
| sample_monotonic_us | int64, CLOCK_MONOTONIC timestamp; reject older than three seconds |
| mode | laptop, tablet or unknown |
| hinge_valid / hinge_degrees | bool / double; only meaningful with adequate gravity projection from both sensors |
| display_gravity | three doubles in g, actual calibrated display acceleration; never a synthetic orientation vector |
| orientation | filtered orientation label, including undefined until available |
| frame | `mxc6655-display-v1`; calibrated display coordinates before DRM panel compensation; normal has negative Y, right-up negative X |
| panel_compensation_degrees | metadata describing compensation used only by the standard upstream interface |
| input_owner | application with `--application-managed`; upstream otherwise |

Snapshots are coherent, mode/validity transitions are immediate, continuous gravity updates are limited to five per second and stationary freshness to one per second. Polling is claim-driven and lid-gated. The driver retains upstream hinge calculations and thresholds; weak projection from either accelerometer now invalidates posture. Suspension gaps reset classification state.

Application-managed mode suppresses both ACPI LTSM input writes and creation/emission of the virtual tablet switch. See [libinput's tablet-switch behavior](https://wayland.freedesktop.org/libinput/doc/latest/switches.html). The kernel lid switch remains read-only.

The driver never broadly probes I²C adapters or unbinds devices itself. `chuwi-sensor-bindings prepare` requires a root-owned `/etc/chuwi-sensor-proxy/sensors.json`: `enabled`, exact vendor/model/bios, and a two-element `sensors` array of verified `i2c-MDA6655:NN` identities, **display first, base second**. It resolves their actual adapter ancestry, records only original `mxc4005` bindings before unbinding, and gives the daemon a private runtime bus list. Missing or ambiguous identities fail closed. This machine currently exposes only one such identity, so approval and identification work remain outstanding.

ExecStopPost restores recorded bindings after normal exit, startup failure or termination. Enable the separately supplied `chuwi-sensor-restore.service` only as part of the approved proxy switch so a persistent journal is handled at the next boot too. A restoration failure preserves its journal. Verify binding recovery with process-kill tests before automatic input switching. To roll back, stop the extended service, restore bindings, reinstall the exact saved stock package and unit/drop-ins, then verify the standard service and restart the controller. No driver binding is presumed restored merely because a package was removed.

## Temporary deep-sleep workflow

The optional `chuwi-sleep-test.service` is a one-shot **separately approved disruptive trial**, not an enabled service or a boot setting. It records the selected mode, chooses deep temporarily, requests suspend, waits for logind's full PrepareForSleep true/false cycle, and restores the previous mode with readback on completion, error or cancellation. ExecStopPost supplies independent restoration after a process failure. Keep the journal if readback fails. Inspect kernel/resume diagnostics afterward; a notification alone does not prove reliable sleep or S0ix residency.

Waiting for the resume signal matters because [`systemctl suspend` is asynchronous](https://www.freedesktop.org/software/systemd/man/255/systemctl.html). The selected mode is exposed in [`/sys/power/mem_sleep`](https://docs.kernel.org/admin-guide/pm/sleep-states.html). This service has not been run on the real machine.

## Reliability and thermal assessment

Keep distro Goodix, stock thermald and the currently working kernel. Use bounded kernel logs, suspend history, thermal zone names, battery energy/power, readable RAPL limits, throttle-counter deltas and fan observations. Compare equal workloads, duration, ambient conditions, AC/battery state and brightness; record measurement permissions and unavailable sensors. No workload or suspend experiment was run during implementation. No evidence presently establishes a benefit from the thermal fork on BIOS V2.50. Goodix/I²C/DPTF packages are deliberately unavailable from `build.py`; the lock file records why. An approved future source comparison must reject any failed/partial patch and record the exact Ubuntu source/version/checksums before a candidate can be built.
