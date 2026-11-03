# Control Center publication checks — 2026-11-02

The integrated Control Center and Trackpad page passed **174 regression tests**
with no skips in the Cinnamon/X11 desktop session. This includes disposable
installer/rollback fixtures, private D-Bus checks, the original seven recorder
protocol checks and six GTK checks in an isolated process. GTK layouts were
verified at 760×570 and 1000×800 with simulated hardware actions. Shell syntax
and Python compilation passed. Trackpad discovery recognized the internal I²C
interface and existing strength-8 configuration without changing either.

Raw logs, build records, machine observations and package previews are local
artifacts excluded from Git. The filenames mentioned below identify local
historical evidence; they are not distributed with the source. These checks do
not establish physical effectiveness of a new filter, post-reboot acceptance,
or acceptance of the optional hardware components.

# Hardware companion validation — 2026-10-28

Implementation is complete in the working tree with optional artifacts staged. The installed application, kernel modules, sensor bindings, stock proxy and thermald have not been replaced. Physical acceptance and optional activation remain **Requires attention**.

## Results by verification level

| Level | Result | Evidence |
|---|---|---|
| Application unit/mock and isolated installer checks | 142 passed | Application test log (`application-tests.log`, local artifact) |
| Actual Gio lifecycle on a private mock D-Bus | 1 passed: claim, service-name loss/replacement, coherent property updates, stale sample, release | `tests/sensor_bus_scenario.py`; private bus address replaces the system bus only in its child process |
| Desktop integration with inert input client | 4 passed: real GTK touchscreen-origin dispatch, rejected mouse/plain clicks, keyboard and Unlock responsiveness | Same test log; guardian/input methods mocked; no physical input lock or rotation performed |
| Overall application suite | **147 passed; no skips** | `python3 -m unittest discover -s tests -q` |
| Upstream proxy native tests | **4 test targets passed**: mount matrices, orientation, repeated claims/application-managed hinge hysteresis, extension snapshots | Native test log (`proxy-native-tests.log`, local artifact) |
| EC kernel build | Built against exact `7.0.0-38-generic` headers | Kernel build log (`ec-build.log`, local artifact) |
| Module signing | SHA256 signature present; staging certificate **not enrolled** | Build and checksum record (`build-record.json`, local artifact); no module load |
| Source verification/patching | Pinned archive SHA256 verified; both patch series reapplied to a clean extraction with zero fuzz | `optional/build.py prepare` |
| Packaging | Three individual Debian packages, source archives, notices and checksums staged under `dist/` | Build record (`build-record.json`, local artifact) |
| Package actions | Read-only apt simulations; proxy would remove stock `iio-sensor-proxy`; EC/helper add only their respective packages | Proxy (`proxy-package-preview.json`, local artifact), EC (`ec-package-preview.json`, local artifact), helper (`helper-package-preview.json`, local artifact) |
| Syntax/static checks | Python compilation, Bash syntax, policy XML parsing and `git diff --check` passed | Commands recorded below |
| Live hardware | Read-only observations only; internal DSI-1 remains **50.00 Hz**, rotation right, panel Normal | Sanitized observation (`n150-observation.json`, local artifact) |

The test log includes a deliberately rejected missing-watchdog case and GTK deprecation/accessibility warnings; these are not test failures. Calibration tests mock writes even where their informational output includes the ordinary configuration filename. Installer tests run the actual shell scripts inside disposable HOME/runtime directories with system commands stubbed; they verify fresh installation, preserving configuration on uninstall, and file/config restoration after an interrupted upgrade.

The kernel build reports the same GCC 13.3 toolchain with different executable prefixes and skips BTF because matching `vmlinux`/pahole is unavailable. This establishes compilation, not runtime or Secure Boot compatibility. Development dependencies were extracted into the workspace; no development package was installed on the host. Exact downloaded build-package versions and hashes are recorded in `build-record.json`.

## Phase boundaries

| Phase | Implemented/verified | Remaining gate |
|---|---|---|
| A — preservation/audit | Immutable unpublished source snapshot; comparison and sanitized observations | No system mutation was needed |
| B — core compatibility | Profiles, single sensor subscription, calibration metadata, HOME precedence, affine mapping, desktop/recovery integration; regressions pass | N150 physical rotation/touch/lock/greeter workflow and older MiniBook/Xfce physical regression |
| C — EC/dashboard | Read-only dashboard; bounded helper, lease/authorization/recovery mocks; corrected external EC patch; exact-header build and staged signature | Individual hardware/register validation, trusted signing and separate load approval |
| D — dual sensors | Pinned extension, actual gravity, ownership suppression, claims/restart/stale tests, binding journal/recovery | Only one sensor is currently identified. Establish second identity/order/calibration, approve replacement and run observation-only validation before input switching |
| E — reliability/power | Bounded evidence gathering; temporary sleep journal and independent recovery; documented benefit/risk assessment and unsupported components | Reproduced Goodix fault/exact distro source comparison if warranted; separately approved real sleep/workload experiments |
| F — delivery | Read-only audit, migration/backups, failure rollback, packages/previews, documentation and acceptance forms | Optional installation and physical acceptance are not claimed |

## Physical acceptance form — not yet executed

Record operator, machine/BIOS, kernel, desktop, source/package hashes, starting input states and native display mode before each group. Keep external recovery access and a working kernel boot option. Check **50.00 Hz before and after every group**. Stop on an unverified recovery or changed display mode.

| Group | Required observations | Recorded outcome |
|---|---|---|
| N150 stock baseline | Startup HOME; manual tablet trial; manual overrides; blacklist/sensitivity; GUI close/reopen; four orientations; touch corners and center | Requires attention — not run |
| External equipment | Connect/disconnect monitor and external input; internal panel stays selected; external devices remain untouched | Requires attention — not run |
| Desktop lifecycle | Actual Cinnamon lock; lid closure; logout/login and greeter; approved suspend/resume; fresh recovery controls after resume | Requires attention — not run |
| Sensor extension, observation only | Two identified sensors; display/base order; HOME calibration/frame; hinge hysteresis; four quadrants; flat/invalid geometry; restart and stale data | Requires attention — not run |
| XInput recovery | Touch-only confirmation, lease expiry, recovery-window/controller/guardian kill or hang, hotplug, original states, Emergency Unlock rearm | Requires attention — not run |
| EC recovery | Separate approved backend; coupled keyboard/touchpad transitions; helper failure and partial write; lost ownership; retained journal on failure; both states verified | Requires attention — not run |
| Optional proxy rollback | Failed startup, normal stop, abnormal termination; original driver binding restoration; saved exact stock package/unit restore; standard service recheck | Requires attention — not run |
| Temporary deep sleep | Explicit one-shot approval; selected mode readback; full logind cycle; actual resume usability; success/failure/cancellation restoration | Requires attention — not run |
| Older MiniBook/Xfce | Physical identity enrollment where needed; original calibrated autorotation; touch, locker, Onboard and independent recovery | Requires attention — machine not available |
| Thermal comparison | Equal workloads/environment/AC state; temperatures, battery consumption, fan observations, counter deltas and thermald logs | Requires attention — no workload/write experiment run |

Goodix, I²C spike-suppression and DPTF remain Unsupported for installation in this delivery. Readable startup thermald warnings and missing EC sensors alone are not evidence that replacing thermald improves this machine. Selected s2idle alone is not evidence of S0ix residency.

## Recheck commands

```
python3 -m unittest discover -s tests -q
python3 -m py_compile chuwi-autorotate chuwi-control-panel chuwi-tablet-guard chuwi_tablet.py chuwi_tablet_client.py chuwi/*.py
bash -n install.sh uninstall.sh
git diff --check
./install.sh --dry-run
```

In restricted sandboxes, the private D-Bus and systemd datagram tests need permission for local Unix sockets. Do not substitute the real system bus. Desktop tests skip when the required desktop or input-source types are unavailable. Do not infer physical acceptance from this test suite.
