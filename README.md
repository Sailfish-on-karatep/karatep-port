# Sailfish OS for the Lenovo Vibe K6 Note / Plus (`karatep`)

An unofficial Sailfish OS **5.1.0.11 (Pispala)** hardware adaptation for the Lenovo Vibe K6
Note / Plus — Qualcomm **MSM8937** (Snapdragon 430), Adreno 505 — built on the
**LineageOS 18.1 / Android 11** hybris base, `aarch64` userspace, Linux 3.18.

> This is the documentation hub for the [Sailfish-on-karatep](https://github.com/Sailfish-on-karatep)
> organisation. Start here.

---

## Build status

**Boots to UI.** The device reaches the Sailfish OS home screen unaided.

### Currently working on

Power management and the unclean shutdown. Mobile data, calls/SMS and SIM 2 are parked until a
real SIM is available.

### Recently fixed

| | |
|---|---|
| **The camera died the moment video recording started** | Two stacked faults. With no `jolla-camera-hw.txt` on this port the app picked the largest size the HAL advertises — 3840x2160 — which no encoder here can take: `media_codecs.xml` caps the hardware AVC encoder at 1920x1088 and the Codec2 fallback returns `-EINVAL`, so `droidcamsrc` failed inside `DroidMediaRecorder` and the buffer queue stalled. It never *recovered* for an unrelated reason: `minimediaservice` waits forever for `media.audio_flinger`, a binder service Sailfish does not have, and since `CameraService` is hosted in that same process its binder threads parked and every later camera call queued behind them. Fixed by capping video at 1080p and requiring `audiosystem-passthrough-dummy-af`. Both cameras verified recording 1080p H.264 + AAC on hardware. → [full analysis](docs/rca/camera-dies-on-record.md) |
| **Enrolled fingerprints "lost" after a reboot; re-enrolment errors out** | karatep's FPC HAL fails `enumerate()` precisely when templates exist — the vendor library returns the count and the HIDL service treats non-zero as an error. The daemon therefore never loaded its finger map and reported nothing enrolled, while the TEE still held the templates, so re-enrolling the same finger was refused with `do_enroll finger already enrolled`. Nothing was ever lost. Fixed in the `sailfish-fpd-community` fork; `GetAll` returns both fingers again. → [details](docs/porting-notes.md#enrolled-fingerprints-vanish-after-a-reboot-and-re-enrolment-fails) |
| **No audio at all, on any output, plus no microphone** | Three independent faults stacked. A 21-byte `xpolicy.conf.d/fmradio.conf` stub broke `module-policy-enforcement`, so with `arm_droid_default.pa` defaulting to `sink.null`/`source.null` nothing was ever routed off them. This HAL has no `create_audio_patch`, so routing changes after stream open failed with `-ENOSYS` and streams stayed on the device they opened with — which is why only the loudspeaker ever worked. And the vendor policy config omits the built-in mics from the `primary input` route, so capture was opened as `AUDIO_SOURCE_VOICE_CALL` and refused. Loudspeaker, earpiece, 3.5 mm and all mics verified on hardware; Fluence dual-mic noise suppression enabled (+21 dB SNR, measured). → [details](docs/porting-notes.md#audio) |
| **Headset mic dead; 4-pole headsets seen as 3-pole** | The TS3A227E accessory-detection chip never ran a detection, so MBHC classified every headset as a headphone. An inherited LineageOS regression (`8d2f38f67c27`) had braced `-Wmisleading-indentation` around the wrong statements, leaving the `DET_TRIGGER` write unreachable after a `return`. → [details](docs/porting-notes.md#headset-detected-as-headphone-the-ts3a227e-never-ran-a-detection) |
| **No UI until `killall vndservicemanager`** | Early vendor services linked the system copy of `libbinder` (`SYST`) instead of the VNDK copy (`VNDR`), so every `/dev/vndbinder` lookup was rejected and the graphics composer crash-looped. Verified on hardware: composer starts once, no `Parcel` errors, lipstick comes up unaided. → [full analysis](docs/rca/vndservicemanager-libbinder.md) |
| **WLAN did not come up** | Two independent causes: `wlan.ko` must match the running kernel exactly (so droid-hal has to be rebuilt after *any* kernel change), and it must be loaded early by `wlan-module-load.service` — a late `modprobe` always returns `ENODEV`. → [details](docs/porting-notes.md#wlan) |
| **Build died on `external/chromium-webview`** | LineageOS' manifest links a file that no longer exists upstream, leaving a dangling symlink. The project is now dropped in [`manifests/local_manifests.xml`](manifests/local_manifests.xml). |
| **No boot splash** (black screen between the Lenovo logo and the UI) | hybris-boot's `HYBRIS_BOOTLOGO` draws the splash with `zcat > /dev/fb0`, which on this panel *always* fails with `ENODEV` — a device-tree/driver mismatch leaves `fb0` with no backing memory until something `mmap()`s it, and MDP5 scans out nothing without an explicit commit. Replaced with `hybris-boot/fbsplash.c`, a 4.4 KB freestanding helper doing `mmap` → `read` → `FBIOPAN_DISPLAY` that then holds the fd (closing it blanks the panel). It must **not** be linked against bionic. Verified on hardware. → [details](docs/porting-notes.md#boot-splash-needs-a-helper-hybris_bootlogos-own-mechanism-cannot-work) |

### Feature status

Every row states what its status rests on. **✅ means verified on this device** — a hands-on test
or a measurement, recorded in the linked write-up. **❓ means there is no test on record**, not that
something is known to be broken.

> **Cellular is now testable.** As of 2026-08-11 a live **BSNL** SIM (MCC 404 / MNC 80) is in
> slot 1 and a deactivated **Airtel** SIM in slot 2, so registration, mobile data and the SIM 2
> status are measured rather than guessed. Calls and SMS are still untested. The old note here
> said cellular could not be finished because only a dummy SIM existed; that is no longer the
> constraint.

| Subsystem | Status | Notes |
|---|---|---|
| Display | ✅ | pixel ratio 1.6. Includes the boot splash, which needs `hybris-boot/fbsplash.c` — `HYBRIS_BOOTLOGO`'s own `zcat > /dev/fb0` cannot work on this panel → [details](docs/porting-notes.md#boot-splash-needs-a-helper-hybris_bootlogos-own-mechanism-cannot-work) |
| Touch | ✅ | the same controller also emits the three capacitive keys — see *Keys — home, nav* |
| Graphics / UI | ✅ | `hwcomposer-2-1` + lipstick; comes up unaided since the `libbinder`/VNDK fix → [full analysis](docs/rca/vndservicemanager-libbinder.md) |
| Cameras — stills | ✅ | both cameras; full sensor resolution (rear 4632x3474 / 4320x2432, front 3264x2448 / 3264x1836) |
| Cameras — video | ✅ | both cameras verified recording 1080p H.264 Baseline + AAC-LC 48 kHz stereo, decoded clean end to end. **Capped at 1080p deliberately** — the HAL offers 3840x2160 but the AVC encoder's real limit is 1920x1088. One rough edge left: the frame rate floats with exposure (12.5–16.7 fps measured indoors, 30 fps in good light) because nothing pins `preview-fps-range`, and the video track ends ~0.3–0.7 s before the audio track → [details](docs/rca/camera-dies-on-record.md) |
| Camera flash / torch | ⚠️ | rear HAL advertises `off, auto, on, torch` and `CameraService` torch state changes were observed on hardware; not yet verified against an actual exposure. Front camera has no flash — no `flash-mode-values` key at all |
| Camera — other controls | ⚠️ | white balance, exposure compensation, flash and focus modes come straight from the HAL at runtime — jolla-camera derives them itself, so there is nothing to configure and nothing that can be mis-transcribed. The front camera correctly offers no flash and no focus (`focus-mode-values=fixed`, `max-num-focus-areas=0`). **No ISO** — neither camera exposes an `iso-values` key. The HAL also reports `max-zoom=99` and `max-num-detected-faces-hw=10`, neither wired up → [details](docs/porting-notes.md#camera) |
| Audio — loudspeaker | ✅ | verified on hardware |
| Audio — 3.5 mm | ✅ | verified, both channels; needs `use_legacy_stream_set_parameters` |
| Audio — earpiece | ✅ | verified; no call needed, force `output-earpiece` via `output-parking` |
| Audio — Bluetooth | ✅ | A2DP verified by hand |
| Mic — built-in (AMIC1) | ✅ | `68: handset-mic` |
| Mic — secondary (AMIC3) | ✅ | `77: speaker-mic`; dual-mic Fluence enabled |
| Mic — headset (AMIC2) | ✅ | `85: headset-mic`, after the TS3A227E kernel fix |
| Bluetooth | ✅ | pairing + A2DP verified. The old "controller init failed" reading was wrong — the HAL logs `Init succeded`; the 61 s cycle was `bluebinder` racing WLAN for the shared WCNSS SoC. Fixed by `Type=oneshot` on `wlan-module-load.service` plus an ordering drop-in; `bluebinder` no longer masked, `NRestarts=0` |
| WLAN | ✅ | `wlan0` up at boot with the device's real MAC, associates, holds an IP and reaches the internet. **2.4 GHz only — hardware limit**, not a config gap. DNS needed a fix of its own: paranoid networking denied systemd-resolved (uid 997) any socket → [details](docs/porting-notes.md#associated-ip-address-gateway-reachable--and-every-name-lookup-fails) |
| WLAN — WPA3 | ⚠️ | WPA3-**transition** APs work (WPA2-PSK + PMF) after the prima `MFPEnabled` fix. WPA3-**only** cannot work: SAE needs `NL80211_CMD_EXTERNAL_AUTH` (Linux 4.17), absent on 3.18 → [details](docs/porting-notes.md#wpa3-transition-aps-ctrl-event-assoc-reject-status_code1) |
| WLAN hotspot | ❓ | never exercised. `jolla-settings-networking-tethering` is in the image and connman runs with only `jolla_rfkill` disabled, so there is nothing known to be missing — but no one has switched it on. (The earlier ❌ here had no test behind it.) |
| Cellular — SIM 1 / RIL | ✅ | **Registers on the live BSNL SIM.** `/ril_0` is `Online`/`Powered`, IMEI read, SIM `Present`, `PinRequired: none`, registered `auto` on LTE with `Attached: true`. Both modems connect to `android.hardware.radio@1.1::IRadio` (slot1 and slot2) 5 s after ofono starts. `ofono` still sometimes needs `systemctl restart ofono` after boot; expected to improve with the move off the legacy grilio stack |
| Cellular — bands | ✅ | **No band restriction is imposed by the port.** `TechnologyPreference: any`, `AvailableTechnologies: gsm, umts, lte` on both slots, and ofono cannot filter bands at all — that lives in modem NV and the vendor `mcfg` set. Observed roaming freely between **B1 (EARFCN 515, 2161.5 MHz)** and **B41 (EARFCN 40140, 2545 MHz)** across three cells over 311 samples. Weak indoor signal is hardware: this handset has no **B28**, which is BSNL's 700 MHz coverage layer, and B1/B41 penetrate buildings poorly. RSRP bottoms out near −101 dBm indoors |
| Cellular — calls / SMS | ❓ | still untested, but no longer blocked — a live SIM is now available |
| Cellular — mobile data | ✅ | **Works — fixed and verified on hardware.** It was broken because Sailfish disables Android's `netd`, while this vendor's `netmgrd` is a client of the Netd *HAL* and waited forever for `android.system.net.netd@1.1::INetd`; `dsi_netctrl` therefore never handed `rild` a handle and every `SETUP_DATA_CALL` died at `unable to get dsi hndl` → ofono `Unexpected data call status 4100`. `external/stub_netd` publishes that interface with a no-op implementation, leaving routing to connman. Verified: `rmnet_data0` up at `100.103.37.103/28`, connman `online`, ping 8.8.8.8 3/3 at 71 ms, HTTP 200 over cellular. Note cellular data only activates while WiFi is off — connman prefers WiFi, which is ordinary policy, not a fault → [rca](docs/rca/mobile-data-no-dsi-handle.md) |
| Cellular — SIM 2 | ✅ | **Works.** `/ril_1` comes up alongside `/ril_0` and reads its card correctly — Airtel, MCC 404 / MNC 10, own IMEI, `PinRequired: none`. It reports `searching` only because the test SIM is deactivated. The old "Network: Denied" note was describing the wrong problem |
| VoLTE | ❌ | **Everything the modem needs is now configured and accepted, and it still does not register.** Four layers were fixed to get here, all verified on hardware. (1) Our fork of `ofono-binder-plugin-ext-qti` (`hybris-18.1`) no longer reports a refused `getImsRegistrationState` as success, and implements `setServiceStatus` — the call Android's `ims.apk` uses, where `requestRegistrationChange` is wired by this RIL to QMI IMSS *set IMS test mode*. (2) No MBN carrier config was ever loaded: `init.qcom.sh` copies from a tree this firmware lacks, then sets `mbn_copy_completed=1` anyway. (3) Nothing on the device matches **MCC 404**, so a BSNL SIM falls through to the generic `ROW_Generic_3GPP`, which sets `ims/IMS_enable = 0` and `voice_domain_pref = CsVoiceOnly` — IMS switched off in NV for every non-Jio SIM. The configs are unsigned, so `scripts/patch-mbn-ims.sh` builds a corrected one (no carrier impersonation) and the modem has loaded, selected and activated it, surviving reboots; two hidden qcril gates had to be opened first (`sw_mbn_volte`/`sw_mbn_openmkt` ship empty, and an unchanged MCFG version is silently skipped). (4) The modem then refused to enable IMS until the generic IMS parameter set was imported — bisected to **one item**, `qipcall_config_items`, carrying `VolteDisabled = 0`. The fork also now sends `setConfig` (transaction 12, recovered from `ims.apk`), the provisioning half BSNL users force open on Xiaomi with `*#*#86583#*#*`: `VLT_SETTING_ENABLED` is accepted. The network is not at fault — the same SIM registers IMS and calls immediately in another handset at the same location. Every QMI service the voice path needs is live (`imss` `0x12`, `imsp` `0x1f`, `imsa` `0x21`; only `imsvt` `0x20`, video telephony, is absent, and qcril's `lte` client times out because that service `0x46` does not exist here — both harmless). An IMS bearer can be established on the imported profile by activating ofono's `/ril_0/context3`. Still: `state:1 radiotech:15 error_code:0` — the IMS stack never starts, and reports no error doing it. Reading a working handset (stock Xiaomi, same SIM) shows why: on SIM insert its qcril calls `set_ims_service_enable_config` and IMS registers **eight seconds later**. Our qcril has that function and has **never called it** — `setServiceStatus` routes to `set_qipcall_config`/`set_reg_mgr_config`, and our `setConfig` items map to client-provisioning and presence instead. `CONFIG_ITEM_MOBILE_DATA_ENABLED` (26) is the untried item in that family → [rca](docs/rca/volte-registration-change-is-test-mode.md) |
| GPS | ✅ | Fixes on hardware, indoors: 8 satellites, ±9.6 m. Assistance was silently never fetched — XTRA and NTP injection were gated on ConnMan `online`, which its captive-portal probe never granted, so every fix was a cold start → [rca](docs/rca/gps-cold-start-no-assistance.md). Also bridged into Waydroid, position and satellites → [design](docs/waydroid-gps-bluetooth.md) |
| Sensors — accelerometer | ✅ | display rotation follows the device, so the accelerometer reaches Qt through `sensorfw` and `hybris-libsensorfw-qt5-binder` |
| Sensors — ambient light | ✅ | verified indirectly but by measurement: mce scales LED brightness by an ALS-derived level, and the LED measured 6% of full in a dark room against 40% in ordinary room light with the stock lux ladder — so lux readings do reach mce → [details](docs/porting-notes.md#the-current-setting-is-only-half-of-it--mce-throttles-the-led-by-ambient-light). Display auto-brightness itself has not been checked separately |
| Sensors — proximity | ❓ | never read. The obvious test — blanking during a call — is now possible, since a live SIM is available |
| Sensors — gyroscope, magnetometer | ❓ | the hardware is there: the vendor SSC config declares accel, gyro and mag axes (`karate-common/configs/sensors/sensor_def_qcomdev.conf`, `CONFIG_SENSORS_SSC=y`). Neither has been read on Sailfish |
| Fingerprint (FPC 1020) | ✅ | enrolment and unlock verified on hardware (two templates enrolled, daemon cycles `IDENTIFYING`). Uses `sailfish-fpd-community` (Jolla's `sailfish-fpd` is unusable — no `sailfish-fpd-slave` exists for karatep) built from our fork (`Sailfish-on-karatep/sailfish-fpd-community`, `hybris-18.1`): this vendor HIDL 2.1 HAL never calls the enumerate callback when no templates exist, leaving the daemon wedged in `FPSTATE_ENUMERATING` forever, so `AndroidFP::enumerate()` arms a 3 s timeout and treats silence as "nothing enrolled". The same HAL quirk from the other side — `enumerate()` fails outright once templates *do* exist — made enrolled fingerprints appear lost after a reboot and blocked re-enrolment; also fixed in the fork. Enrolment, unlock and **removal** all verified. Root cause of every fingerprint fault here: the FPC vendor library returns the template *count* where `fingerprint.h` specifies 0, and the LineageOS HIDL adapter treated that as an error, so it never synthesised the `onEnumerate` callbacks and the daemon never learned a template id. Fixed in our fork of `android_hardware_lineage_interfaces`. **The rebuilt service must be installed onto `/vendor` by hand** — it is the one change an image build cannot deliver. One caveat worth knowing: templates live on `/data` and so **survive a reflash and a factory reset**, which is how a previous owner's finger can still unlock a freshly installed system → [details](docs/porting-notes.md#fingerprint-fpc-1020), [reflash caveat](docs/rca/stale-fingerprint-templates.md) |
| Vibration | ✅ | `ngfd-plugin-native-vibrator` + `libhybris-libvibrator`; haptics work in the UI. No write-up — this row rests on use, not on a measurement |
| Notification LED | ⚠️ | Lights for every pattern and breathes correctly; charging/charged and notification patterns verified on hardware. Brightness took three separate fixes, only two of which were the obvious ones: the `white` mce backend pinned to `/sys/class/leds/green` (autoprobe otherwise matches `redgreen` and leaves the real LED dark), the MPP sink raised from Lenovo's 5 mA to the 40 mA hardware ceiling, and — the dominant effect, and the one that made the first two look like they had failed — mce's `[BrightnessLed]` ALS curve, which floors at 6% and gave only 40% in room light. Measured peak on the node was 101/255 with the stock curve against 255/255 without it. Still ⚠️ because the 40 mA sink is not yet confirmed on hardware, and it cannot be tested without a reflash (`MSM_SPMI_DEBUGFS_RO` compiles debugfs register writes out). `PatternDeviceOn` does **not** light this LED despite being configured — measured, don't "fix" it. → [details](docs/porting-notes.md#notification-led) |
| Keys — power, volume | ✅ | power blanks/unblanks and long-press reaches the power menu; volume adjusts |
| Keys — back | ✅ | an ordinary `Qt::Key_Back`, handled by applications; worked throughout |
| Keys — home, nav | ✅ | home → app switcher, square → top menu. The keys come from the touch controller and always worked; lipstick just needed them declared to `ssu-sysinfo` → [details](docs/porting-notes.md#hardware-keys) |
| USB — networking | ✅ | RNDIS works in both the recovery and the booted system; the whole flashing procedure and every debug session run over it → [flashing.md](docs/flashing.md) |
| USB — MTP / data | ❓ | `buteo-mtp-qt5` and `mtp-vendor-configuration-sailfish` are installed; never connected to a host as MTP |
| Battery / charging | ⚠️ | charges over USB, and charge state is reported well enough that mce's charging/charged LED patterns fire from it. `jolla-actdead-charging` is installed but **act-dead** (charging with the device switched off) has not been tested |
| Power off / reboot | ⚠️ | powers off cleanly much of the time, and intermittently wedges with the screen off, needing a hard reset. Ramoops shows userspace getting well into shutdown, the modem then failing to halt (`wait_for_shutdown_ack` timeout, `Port … halt timeout`), and PS_HOLD never reached. Not root-caused; the modem is the leading suspect, and the porter archive has zero prior art. A *graceful* `reboot` always hangs — `droid-hal-init.service` never stops — so use `reboot -f` → [investigation](docs/rca/shutdown-hang.md) |
| Suspend / resume | ❌ | **measured, and it barely suspends.** 25 successes against 4423 failures; 4125 of those are `suspend_noirq` vetoed by `qpnp-vadc-14` with `-EBUSY`, plus 256 `failed_freeze`. mce put it at 181 s suspended across 34 786 s of uptime — 0.5%. Taken on USB, which holds `msm_otg`/`smbchg` wakelocks and explains why few attempts *start*, but not the noirq vetoes: a wakelock aborts before that stage, so those are a driver fault. A clean figure needs a run on battery. No idle-drain number on record yet, and `iphb` (socket `/dev/shm/iphb`, dsme's `iphb.so`) is present but cannot be exercised until this is fixed → [checklist](docs/hadk-compliance.md) |
| RTC alarms | ❓ | `CONFIG_RTC_DRV_QPNP` is built in; no alarm has been tested, and a meaningful test depends on suspend/resume above |
| FM radio | ⚠️ | **tuner works; audio needs a headset to test.** The image was never missing the app: `jolla-mediaplayer-radio` and the `irisradio` plugin have always been in the pattern — it is a plugin *inside* the Media app, not its own icon. What was missing was permission. `radio-iris-transport` is one `module_param_call` with no `module_init`, so the SMD channel opens only when something writes `fmsmd_set`; the plugin has done that write itself since 0.6.0, but `init.qcom.rc` leaves the parameter `system:system` and the failure was swallowed, so every ioctl returned `ENODEV`. `droid-fm-up.service` now hands it to the `audio` group (waiting for droid-hal-init's `on boot` rather than racing it), `999-droid-fm.rules` pins `/dev/radio0`, and `fmradio.conf` is back as a real symlink — the old "bogus stub" turns out to have been that same symlink, flattened. Verified end to end as `defaultuser` on a fresh boot: band 87.5–108.0 MHz read from the chip, tuning to 91.1/98.3/104.0 MHz confirmed by readback. **A second kernel bug then surfaced once a headset was attached: the app would not tune and hung on exit.** The iris event queue (256 entries) was never flushed — `iris_fops` had no `->open`, and the driver's only `kfifo_reset()` skips the event buffer by construction — so stale events accumulated across sessions until `kfifo_in_locked()` began silently dropping new ones without waking anybody. The plugin gates its whole UI on `IRIS_EVT_RADIO_READY` and is woken out of a blocking `VIDIOC_DQBUF` by `IRIS_EVT_RADIO_DISABLED`, so one dropped byte killed both tuning and teardown, permanently until reboot. Fixed in `b5cc23ebd72a`: the queue now evicts its oldest entry rather than discarding the newest, and is reset on open. **That was real but was not why the app failed.** The app runs under sailjail, where `/sys` is read-only and `/sys/module` is absent entirely, so the plugin's write to `fmsmd_set` — guarded by `QFile::exists()` — is silently skipped. `radio-iris-transport` has no `module_init`, so that write is the *only* thing that opens the APPS_FM channel; without it every ioctl returns `ENODEV` (`Failed to set control (id: 134217732)` in the journal), the worker blocks in `VIDIOC_DQBUF` and the GUI thread deadlocks behind it in `QThread::wait()`. Opening `/dev/radio0` *is* permitted in the sandbox, so `b5ea3d9bd494` opens the channel from the v4l `->open` instead, and waits for `SMD_EVENT_OPEN` so the open is synchronous. Verified without ever touching `fmsmd_set`: 93.5 MHz signal 168 stereo, 106.4 MHz signal 163, teardown wakes cleanly. Audio routing is still untested → [enablement](docs/rca/fm-radio-enablement.md), [event queue](docs/rca/fm-event-queue-overflow.md), [sandbox](docs/rca/fm-sandbox-cannot-open-transport.md) |
| Module parameters | ✅ | **was an intermittent system-wide deadlock; fixed in the kernel.** On some boots `thermal-engine` writing `msm_thermal`'s `enabled` parameter deadlocks in `kthread_stop()` on a thread that sleeps in `wait_for_completion_interruptible()` and so never sees `kthread_should_stop()`. `kernel/params.c` guards every module parameter with one global mutex, so that wedges **all** `/sys/module/*/parameters/*` reads and writes for the rest of the boot. Casualties: FM radio, MTP's `mtp_rx_req_len`, and `init.qcom.post_boot.sh`, which dies partway through on `lpm_levels/sleep_disabled` and silently skips every tuning step after it. Also leaves `droid-hal-init` unkillable in `D` — a candidate explanation for the graceful-reboot hang, though not proven. Fixed twice over. `07b2c9ff34ff` orders a stop flag before the `complete()` so the thread cannot miss the wake, making the deadlock impossible; `3a06ccbcfede` removes the trigger entirely — the TSENS read failed because an inherited regression (`0498e1415d62`, a *strlcpy hardening* commit) copied the per-CPU sensor name with `sizeof()` on a `const char *`, truncating `tsens_tz_sensor0..7` to `tsens_t`. Verified across eight reboots: no deadlock, and `Unable to read TSENS` is gone from dmesg entirely. Bonus — CPU thermal mitigation now has working sensors for the first time (real temperatures, 8/8 CPUs online, no spurious offlining). On older kernels, `cat /sys/module/msm_thermal/parameters/enabled` hanging identifies an affected boot → [analysis](docs/rca/msm-thermal-param-lock-deadlock.md) |
| Factory reset | ❌ | by design, not a regression: Sailfish's reset is a **btrfs snapshot rollback** needing `factory-@`/`factory-@home` subvolumes, and this port's rootfs is ext4 in a stowaway under `/data`. Settings reports success and erases nothing. Deliberately not worked around → [full analysis](docs/rca/factory-reset-does-nothing.md) |
| Android apps (Waydroid) | ⚠️ | **usable, with gaps.** Touch works: Waydroid preferred xdg-shell, which lipstick only grew in 5.1 and does not route touch on, so our `android_hardware_waydroid` fork takes the `wl_shell` path Sailfish speaks natively. Touch also survives a display-size change now (`do_hotplug()` re-arms the touch FIFO). The container no longer kills the host's camera (`cgroup.clone_children`), and its own camera provider loads the host HAL instead of crash-looping. Hardware video decode works now too — the container has its own `/vendor`, so it lost the vendor property that disables split DPB/OPB, which this Venus firmware cannot do, and *no* video played. GPU acceleration is the real Adreno driver, the microphone records, and all eight sensors are bridged. GPS is bridged through the host's Geoclue, so both stacks can position at once. The battery is real now — the health HAL was overwriting the sysfs reading with a hardcoded 85 %-and-charging mock. Android is also on the host's time zone instead of GMT, and `~/Pictures`, `~/Videos`, `~/Music`, `~/Downloads` and `~/Documents` are the same directories on both sides. The system image is now the **GAPPS** build — Play Services, Play Store and GSF are installed and running — on the same `HALIUM_11` vendor. Still open: `configureStreams` rejects the JPEG stream because the install pairs an Android 13 system image with an Android 11 vendor; Android has no thermal HAL at all (`HAL Ready: false`), so it cannot throttle; per-app network accounting is permanently unavailable because Android 13's netd needs eBPF that a 3.18 kernel does not have; and **Android cannot reclaim memory inside the container** — `lmkd` has no pressure source (no `/dev/memcg`, read-only memory cgroup, no PSI on 3.18) and never kills anything, so the device thrashes instead, which surfaces as touch freezing for tens of seconds → [memory rca](docs/rca/waydroid-touch-anr-thrashing.md) → [feature status](docs/waydroid.md#feature-status), [touch rca](docs/rca/waydroid-touch-xdg-shell.md), [cgroup rca](docs/rca/waydroid-poisons-host-cgroups.md), [camera rca](docs/rca/waydroid-camera-hal-name.md), [video rca](docs/rca/waydroid-video-decode-split-mode.md), [battery rca](docs/rca/waydroid-battery-mocked.md), [waydroid.md](docs/waydroid.md) |
| NFC | ➖ | no hardware |

✅ verified on hardware · ⚠️ partial, or verified with a caveat · ❌ broken or not possible ·
❓ no test on record · ➖ not applicable

---

## Where to find what

### In this repo

| Path | What it is |
|---|---|
| [`docs/flashing.md`](docs/flashing.md) | **Start here to install.** Step-by-step flashing guide, plus troubleshooting for the errors this device actually produces. |
| [`docs/los-recovery.md`](docs/los-recovery.md) | The LineageOS recovery this port builds and uses: how it is built, the karatep changes it carries, and the traps it hides. |
| [`docs/porting-notes.md`](docs/porting-notes.md) | Accumulated device knowledge: partition map, fixes already in the tree, known workarounds, how to debug the boot. |
| [`docs/rca/`](docs/rca/) | Root-cause write-ups for bugs that were properly diagnosed. |
| [`docs/waydroid.md`](docs/waydroid.md) | Running Android apps via Waydroid: the kernel options it needs, how the packages are built, and the Sailfish OS 5.1 regression to be aware of first. |
| [`docs/rca/waydroid-devpts.md`](docs/rca/waydroid-devpts.md) | Why the Waydroid container refused to start: `CONFIG_DEVPTS_MULTIPLE_INSTANCES`, plus the overlayfs limitation found alongside it. |
| [`docs/rca/waydroid-poisons-host-cgroups.md`](docs/rca/waydroid-poisons-host-cgroups.md) | Why running Waydroid kills the Sailfish camera until the next reboot: the container writes the host's cgroup v1 cpuset hierarchy. |
| [`docs/rca/waydroid-touch-xdg-shell.md`](docs/rca/waydroid-touch-xdg-shell.md) | Why Waydroid touch is dead under lipstick: SFOS 5.1 added xdg_shell, Waydroid prefers it over wl_shell, and lipstick's xdg path does not route touch. |
| [`docs/rca/waydroid-camera-hal-name.md`](docs/rca/waydroid-camera-hal-name.md) | Why Waydroid's camera provider crash-looped every 5 s: the HAL is probed as camera.waydroid.so, not camera.qcom.so. |
| [`docs/rca/gps-cold-start-no-assistance.md`](docs/rca/gps-cold-start-no-assistance.md) | Why every GPS fix was a cold start: XTRA and NTP injection were gated on ConnMan reaching "online", which its captive-portal probe never granted. |
| [`docs/rca/waydroid-touch-after-unlock.md`](docs/rca/waydroid-touch-after-unlock.md) | Touch dead after an unlock — unresolved, with the two theories already eliminated so they are not re-run. |
| [`docs/rca/waydroid-touch-anr-thrashing.md`](docs/rca/waydroid-touch-anr-thrashing.md) | Why touch stops for ~20 s and then comes back: not the input path at all — the device thrashes at 13 MB free with zram full, and neither `lmkd` nor the host's `lowmemorykiller` can fire. |
| [`docs/waydroid-gps-bluetooth.md`](docs/waydroid-gps-bluetooth.md) | Why neither GPS nor Bluetooth reaches the Waydroid container, and why only one of the two is worth fixing. |
| [`docs/rca/waydroid-video-decode-split-mode.md`](docs/rca/waydroid-video-decode-split-mode.md) | Why no video plays inside Waydroid: the container's own `/vendor` drops the device's video tuning properties, so the decoder asks Venus for split DPB/OPB and the firmware rejects the session. |
| [`docs/rca/waydroid-battery-mocked.md`](docs/rca/waydroid-battery-mocked.md) | Why Android always showed 85 % and charging: the container could read the real battery all along, and Waydroid's health HAL overwrote it with constants. |
| [`docs/rca/sphal-hidl-memory.md`](docs/rca/sphal-hidl-memory.md) | Why the camera app aborts at the end of video playback: the sphal namespace cannot reach the VNDK APEX, so HIDL shared memory never loads. |
| [`docs/hadk-compliance.md`](docs/hadk-compliance.md) | Chapter-by-chapter audit of this port against the HADK: what complies, what was fixed, and which deviations are deliberate. |
| [`docs/useful-commands.md`](docs/useful-commands.md) | Short command reference (rebooting to fastboot/recovery from Sailfish, etc.). |
| [`manifests/local_manifests.xml`](manifests/local_manifests.xml) | The `repo` local manifest. Copy to `$ANDROID_ROOT/.repo/local_manifests/`. |
| [`scripts/flash.sh`](scripts/flash.sh) | Semi-automated flasher; discovers the USB network address itself. |

### Other repos in the organisation

| Repo | Role |
|---|---|
| [`karatep-patches`](https://github.com/Sailfish-on-karatep/karatep-patches) | The four Android-source patches that **cannot** be carried as a fork, because they are written against the tree *after* `mer-hybris/hybris-patches` has been applied. Applied as a second pass. Upstream's series is synced normally and is not forked. |
| [`hybris-boot`](https://github.com/Sailfish-on-karatep/hybris-boot) | Fork of `mer-hybris/hybris-boot` carrying the karatep entry in `fixup-mountpoints`. |
| [`android_device_lenovo_karatep`](https://github.com/Sailfish-on-karatep/android_device_lenovo_karatep) | Device tree. |
| [`android_device_lenovo_karate-common`](https://github.com/Sailfish-on-karatep/android_device_lenovo_karate-common) | Shared `karate` family device tree (`fstab.qcom` lives here). |
| [`android_kernel_lenovo_msm8937`](https://github.com/Sailfish-on-karatep/android_kernel_lenovo_msm8937) | Kernel 3.18 + `karatep_defconfig`. |
| [`proprietary_vendor_lenovo`](https://github.com/Sailfish-on-karatep/proprietary_vendor_lenovo) | Vendor blobs. |
| [`droid-hal-karatep`](https://github.com/Sailfish-on-karatep/droid-hal-karatep) | `droid-hal` packaging (`rpm/`). |
| [`droid-config-karatep`](https://github.com/Sailfish-on-karatep/droid-config-karatep) | Sailfish-side device configuration, `sparse/` overlay, patterns, kickstart. |
| [`droid-hal-version-karatep`](https://github.com/Sailfish-on-karatep/droid-hal-version-karatep) | Version package. |

---

## Building

Follow the [HADK](https://hadk.sailfishos.org/) — this port adds nothing unusual to the
procedure. In outline, with `$ANDROID_ROOT` set:

```sh
# 1. Sources. local_manifests.xml is the only karatep-specific step: it repins the
#    device trees, kernel, vendor blobs, droid-hal/-config and hybris-boot at the
#    Sailfish-on-karatep forks, and adds karatep-patches.
repo init -u https://github.com/mer-hybris/android.git -b hybris-18.1
cp manifests/local_manifests.xml $ANDROID_ROOT/.repo/local_manifests/karatep.xml
repo sync

# 2. Patches, in this order. Nothing is ever edited in the tree by hand.
hybris-patches/apply-patches.sh --mb       # mer-hybris' series (plain HADK step)
karatep-patches/apply-patches.sh --mb      # the four karatep patches, on top

# 3. Android HAL (HABUILD SDK). HADK specifies `breakfast`, not `lunch`.
source build/envsetup.sh
export USE_CCACHE=1
breakfast karatep
make -j$(nproc --all) hybris-hal droidmedia

# 4. Droid HAL packaging (PLATFORM SDK, from $ANDROID_ROOT), in this order
rpm/dhd/helpers/build_packages.sh --droid-hal
rpm/dhd/helpers/build_packages.sh --configs
rpm/dhd/helpers/build_packages.sh --mw
rpm/dhd/helpers/build_packages.sh --gg
rpm/dhd/helpers/build_packages.sh --version

# 5. Root filesystem image. RELEASE must be exported or --mic refuses to run.
export RELEASE=5.1.0.11
rpm/dhd/helpers/build_packages.sh --mic
```

> **Check the boot image before flashing.** `Install: … hybris-boot.img` at the end of the
> Android build is not proof of success. hybris-boot builds its ramdisk with
> `find … | cpio -H newc -o | gzip -9`, and **the Jolla Ubuntu chroot does not ship `cpio`** —
> the pipeline then emits nothing, `gzip` still exits 0, and make happily writes an image with
> no `init`. Flashing it drops the device into Qualcomm bulk mode (`05c6:900e`) with no USB
> networking, so the installer waits forever for a recovery shell that never appears.
>
> ```sh
> ls -l out/target/product/karatep/{kernel,hybris-boot.img}
> # good: hybris-boot.img is ~1.5 MB larger than kernel
> # bad:  the difference is a few kilobytes -> no initramfs
> ```
>
> Fix once per chroot with `sudo apt-get install -y cpio`, then delete
> `out/target/product/karatep/obj/ROOT/hybris-{boot,recovery}_intermediates` — a 20-byte
> `boot-initramfs.gz` is otherwise considered up to date. Details in
> [`docs/porting-notes.md`](docs/porting-notes.md#cause-cpio-is-missing-from-the-habuild-chroot).

Everything device-specific is either a fork repinned in `local_manifests.xml` (the HADK's
"Contribute your mods back" pattern) or one of the four patches in `karatep-patches`. There is
no other local state, so the same two files reproduce the tree on any machine.

Then see [`docs/flashing.md`](docs/flashing.md).

---

## Credits

Built on the work of the Sailfish OS porters community — particularly **@mal** and
**@elros34** on `#sailfishos-porters`, whose advice is cited throughout
[`docs/porting-notes.md`](docs/porting-notes.md).

Sailfish OS is a product of [Jolla](https://jolla.com/). This is an unofficial community
adaptation with no affiliation or warranty.
