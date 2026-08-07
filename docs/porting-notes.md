# Porting notes — lenovo/karatep

Accumulated device-specific knowledge for the Sailfish OS port of the Lenovo Vibe K6 Note /
Plus (`karatep`, MSM8937 / Snapdragon 430, Adreno 505), based on LineageOS 18.1 /
`hybris-18.1`.

These are working notes. Anything with a firm root cause gets promoted to its own write-up
under [`rca/`](rca/).

---

## Device facts

| | |
|---|---|
| Android codename | `karatep` |
| SoC | Qualcomm MSM8937, Snapdragon 430, Adreno 505 |
| Kernel | 3.18, defconfig `arch/arm64/configs/karatep_defconfig` |
| Android base | LineageOS 18.1 (Android 11), `hybris-18.1` |
| Port arch | `aarch64` (full 64-bit userspace) |
| Sailfish OS | 5.1.0.11 (Pispala) |
| Display pixel ratio | **1.6** |
| boot partition | `/dev/mmcblk0p34` |
| system partition | `/dev/mmcblk0p52` |
| userdata partition | `/dev/mmcblk0p54` |

The recovery does **not** expose Android's `by-name` symlinks, which is why the flashing
instructions use raw `mmcblk0p*` nodes.

---

## Fixes that are in the tree

* **`/system` must map to `/` in `fstab.qcom`.** The partition labelled `system` is really the
  Android *root* partition; the actual system lives at `/system/system`. File:
  `device/lenovo/karate-common/rootdir/etc/fstab.qcom`. (Thanks @mal)
* **Kernel bootflags go in `BoardConfig.mk`**, not the defconfig — `selinux=1` (permissive) and
  `audit=0` (silences audit spam). (Thanks @elros34 for `audit=0`)
* **SELinux files must be copied from the device's `/vendor`, never symlinked.** (Thanks @mal)
* **Sailjail needs namespaces**: `CONFIG_UTS_NS`, `CONFIG_IPC_NS`, `CONFIG_USER_NS`,
  `CONFIG_PID_NS`, `CONFIG_NET_NS`, `CONFIG_NF_CONNTRACK_NETBIOS_NS` in `karatep_defconfig`.
* **`CONFIG_ANDROID_BINDER_DEVICES` is a string, not a bool.** It must read
  `CONFIG_ANDROID_BINDER_DEVICES="binder,hwbinder,vndbinder"`. Writing `=y` is silently
  discarded and the Kconfig default is used instead — it happens to be the same value, so the
  mistake is invisible until a Kconfig change drops a binder node.
* **Flattened-APEX linker spam** (`Expecting header 0x53595354 but found 0x564e4452`) — see
  [rca/vndservicemanager-libbinder.md](rca/vndservicemanager-libbinder.md). Fixed properly by
  the `karatep-patches` `system/core/0040..0042`; the older advice to "apply the linkerconfig patch" only
  addressed half of it.
* **`TLS_SLOT_ART_THREAD_SELF` needs a value.** mer-hybris' slot-shifting patch leaves the
  macro empty, but ART's headers (`thread-current-inl.h`, `thread.cc`, `thread_list.cc`) expand
  it and Soong pulls parts of `art/` into the hybris-hal build, so the tree fails to compile
  with *"expected expression"*. `karatep-patches` `bionic/0009` aliases it to
  `TLS_SLOT_SANITIZER` (7). That is an alias, not a spare slot; it is only safe because ART is
  never *executed* on a Sailfish port — there is no zygote and no app process. Revisit if that
  ever changes, because `Thread::Current()` and the sanitizer slot would collide.
* **Chromium WebView is not synced.** LineageOS' manifest links `patches/Android.mk`, which no
  longer exists upstream, leaving a dangling symlink that kills the build with
  `build/make/core/prebuilt.mk:53: error: external/chromium-webview/Android.mk: No such file
  or directory`. `manifests/local_manifests.xml` drops the project.

---

## Verify the boot image before flashing

`make hybris-hal` ending in

```
Install: out/target/product/karatep/hybris-boot.img
```

is **not** proof of a usable image. hybris-boot builds its initramfs by copying
`hybris/hybris-boot/initramfs/` into a staging directory; if that source directory is empty,
it packs a 20-byte empty gzip and produces a boot image with no `init` at all. The build
reports success.

Flashing such an image does not brick the device, but it does not boot either: the kernel
comes up with no initramfs, panics, and the device drops off USB and re-enumerates as
`05c6:900e Qualcomm QHSUSB__BULK` before returning to fastboot. There is no RNDIS, so the
recovery shell never appears and any network-based installer just waits forever.

Cheap pre-flight check — a sound image is roughly **1.5 MB larger** than the bare kernel:

```sh
cd $ANDROID_ROOT/out/target/product/karatep
ls -l kernel hybris-boot.img
# good: kernel 10,691,936   hybris-boot.img 12,283,904   (~1.59 MB of initramfs)
# bad:  kernel 10,691,936   hybris-boot.img 10,696,704   (4,768 bytes — no initramfs)
```

Or directly:

```sh
ls -l out/target/product/karatep/obj/ROOT/hybris-boot_intermediates/boot-initramfs.gz
# 20 bytes == an empty gzip stream == no initramfs
```

### Cause: `cpio` is missing from the HABUILD chroot

`hybris/hybris-boot/Android.mk` builds the ramdisk with

```make
@(cd $(BOOT_INTERMEDIATE)/initramfs && find . -printf '%P\n' | cpio -H newc -o ) | gzip -9 > $@
```

The Jolla Ubuntu chroot (`ubuntu-focal-20210531-android-rootfs.tar.bz2`) **does not ship
`cpio`** — no binary, no dpkg record. `cpio` is also absent from Soong's PATH sandbox
(`prebuilts/build-tools/path/linux-x86/`, which carries ~60 host tools). mer-hybris'
`build/soong/0001-hybris-Add-cpio-to-allowed-commands.patch` marks it `Allowed`, but that only
permits pass-through to the *host* binary — it does not provide one.

With `cpio` absent the pipeline emits nothing, and because the recipe's exit status is `gzip`'s
(which succeeds on empty input), **make reports success**. You get
`Install: … hybris-boot.img` and an image with no `init`.

Fix, once per chroot:

```sh
/opencloud/bin/habuild -u $USER /bin/bash -c 'sudo apt-get update && sudo apt-get install -y cpio'
```

Then delete the stale intermediates so the ramdisk is regenerated — a 20-byte
`boot-initramfs.gz` is otherwise considered up to date:

```sh
rm -rf $ANDROID_ROOT/out/target/product/karatep/obj/ROOT/hybris-{boot,recovery}_intermediates
```

> An earlier revision of this note blamed an empty `hybris/hybris-boot/initramfs/` source
> directory after a partial `repo sync`. That was wrong: a later build had the staging
> directory fully populated and still produced a 20-byte ramdisk. The missing `cpio` is the
> actual cause.

Separately — and still true — `repo sync` resets `hybris/hybris-boot` unless the local manifest
pins it to a fork; the HADK warns about this in *Configure Mountpoint Information*, and
`manifests/local_manifests.xml` now pins `Sailfish-on-karatep/hybris-boot`. HADK's *Common
Pitfalls* also says a `--force-sync` should be followed by a full `repo sync`.

---

## Build environment gotchas

Three problems that stayed hidden because the original build environment had accumulated
state by hand. None would survive a rebuild on another machine, so they are worth knowing.

### scratchbox2 does not follow the `/opencloud` symlink

In the Platform SDK the workspace is reachable as `/opencloud` (a symlink to
`/parentroot/opencloud`). Ordinary shell commands follow it; **sb2 does not**:

```sh
sb2 -t lenovo-karatep-aarch64 ls /opencloud/hadk/.mb2/spec              # No such file or directory
sb2 -t lenovo-karatep-aarch64 ls /parentroot/opencloud/hadk/.mb2/spec   # works
```

`mb2` runs `rpmbuild` under sb2, so with the symlinked form every package build fails with

```
error: Unable to open $ANDROID_ROOT/.mb2/spec: No such file or directory
!! building of package failed
```

`$ANDROID_ROOT` must be `/parentroot/opencloud/hadk` inside the Platform SDK. The droid-config
kickstart already uses that form for the local repo. A build target created *before* the
symlink existed keeps working with the wrong form, which is why this only appeared after the
target was recreated.

### A stale `repodata/` silently disables the local RPM repo

`build_packages.sh` adds the locally built RPMs with `zypper --plus-repo dir:$LOCAL_REPO`. A
`dir:` URI is a **plaindir** repo — zypper scans the `.rpm` files directly. The helper
deliberately writes `createrepo_c` output to `$LOCAL_REPO/repo`, with the comment *"so that
`zypper --plus-repo` does not pick it up"*.

If anything leaves a `repodata/` directory in `$LOCAL_REPO` itself (for example a `createrepo`
run by hand), zypper treats the repo as rpm-md instead, fails the signature check on the
unsigned metadata, and drops the repo entirely:

```
Repository 'dir:.../droid-local-repo/karatep' is invalid.
 - Signature verification failed for repomd.xml
No provider of 'libhybris-libEGL' found.
```

Packages you just built become invisible to the next package that needs them. Fix: remove
`$ANDROID_ROOT/droid-local-repo/$DEVICE/repodata` (keep `repo/` — the kickstart points at it).

### `cpio` is missing from the chroot

See [Verify the boot image before flashing](#verify-the-boot-image-before-flashing) above.

---

## WLAN

Working. `wlan0` comes up at boot and connman scans and associates normally:

```
wlan  3795573  0
wlan: WCNSS software version CNSS-PR-4-0-00325
wlan: WCNSS hardware version WCN v2.0 RadioPhy vUnknown with 19.2MHz XO
```

### The radio is 2.4 GHz only, and that is a hardware limit

`iw phy` reports **Band 1 only** — there is no 5 GHz band to enable. This is not a
configuration gap: `/vendor/firmware/wlan/prima/WCNSS_qcom_cfg.ini` already permits both
(`BandCapability=0`, `gDot11Mode=0`), and the band still does not appear. Lenovo specify
802.11 b/g/n 2.4 GHz single-band for this handset. The MSM8937 platform supports 802.11ac,
but this phone pairs it with the 2.4 GHz-only WCN companion part, so there is nothing to
turn on. Do not spend time trying to "enable" 5 GHz.

Note the driver still advertises VHT capabilities on Band 1; that is prima's static
capability table, not a statement about the silicon.

### The MAC address is read off the device, never stored

`wlan0` does **not** get a real MAC by itself. All three of prima's inputs are empty or
default on karatep, so `hdd_wlan_startup()` falls through to
`hdd_generate_iface_mac_addr_auto()` and invents a `00:0a:f5:xx:xx:xx` address derived from
the SoC serial. The real addresses live in `/mnt/vendor/persist/wlan_mac.bin`, which nothing
under Sailfish reads.

`droid-config-karatep`'s `sparse/usr/bin/droid/macaddrsetup.sh` feeds the real address to the
platform driver through `wcnss_mac_addr` **before** `modprobe wlan`, as an `ExecStartPre` of
`wlan-module-load.service` — prima reads that value exactly once, at module init. See the
script for the full derivation and for why `ip link set wlan0 address` is the wrong fix.

Addresses are per-device and are read at boot; none are committed to any repo, and none
should be pasted into these docs.

Two things matter, and both produce the same unhelpful symptom (`no wlan0`):

**1. The module must match the running kernel exactly.** `wlan.ko` lives in
`/lib/modules/$(uname -r)/`, shipped by `droid-hal-karatep-kernel-modules`. Any kernel change
alters the version string, so flashing a new `hybris-boot.img` **without** rebuilding
droid-hal and reinstalling the rootfs leaves the modules stranded under the old directory:

```
modprobe: FATAL: Module wlan not found in directory /lib/modules/3.18.124-perf-ga8b7042c2a84
```

Always rebuild `--droid-hal` (and the image) after touching the kernel — the boot partition and
the rootfs modules are a matched pair.

**2. The module must be loaded early, by `wlan-module-load.service`.** Loading it later always
fails, even with the correct module, WCNSS online and the platform device bound:

```
modprobe: ERROR: could not insert 'wlan': No such device
```

`dmesg` shows `wlan: loading driver v3.0.11.85.9` and then nothing — the probe finds no device
and the module unloads. The service exists precisely for this (HADK-HOT: *"if a particular
driver needs firmware before partitions are mounted, building the driver as a module and
creating a systemd service (like for wlan) which will modprobe it might help"*). Do not
diagnose WLAN by hand-modprobing after boot; reboot and let the unit do it.

Things that look suspicious here but are **not** the problem, all verified:
`droid.late_start=trigger_late_start` is set, `wcnss_service` is running, `subsys2: wcnss=ONLINE`,
the PIL firmware images (`wcnss.b00`…) are present, and `a000000.qcom,wcnss-wlan` is bound to
the `wcnss_wlan` platform driver.

### WPA3-transition APs: `CTRL-EVENT-ASSOC-REJECT status_code=1`

Any AP advertising PMF (802.11w) failed instantly, while plain WPA2 APs connected fine. The
give-away is the timing: the reject arrives in the *same second* as the connect call, with no
authentication or association frames on air — so the AP is never involved and the failure is
entirely inside the driver.

```
nl80211: Connect (ifindex=31)
  * IEs - hexdump(len=46): 30 1a 01 00 00 0f ac 04 ... 8c 00 00 00 00 0f ac 06 ...
nl80211: Connect event (status=1 ...)
wlan0: CTRL-EVENT-ASSOC-REJECT status_code=1
```

RSN capabilities `8c 00` mean MFPC set, MFPR clear — PMF capable, not required. A quick way to
spot such an AP in the scan results is the RSN IE length: `rsn_ie_len=28` against `24` for a
non-PMF AP, the extra 4 bytes being the group management cipher suite.

`hdd_SetGENIEToCsr()` sets `MFPCapable=1` from those bits, but `MFPEnabled` came from
`wlan_hdd_cfg80211_set_privacy()` as `(req->mfp == NL80211_MFP_REQUIRED)`. **On a 3.18 kernel
`req->mfp` cannot express "optional"**: `enum nl80211_mfp` has only `NL80211_MFP_NO` and
`NL80211_MFP_REQUIRED` (`OPTIONAL` was added upstream in 4.9), and `net/wireless/nl80211.c`
rejects any other value with `-EINVAL`. wpa_supplicant, asking for optional PMF, therefore
sends no `NL80211_ATTR_USE_MFP` at all and it defaults to `NL80211_MFP_NO`.

That leaves `MFPEnabled=0` with `MFPCapable=1`, which is exactly what
`csrIsPmfConnectionAllowed()` rejects when the AP is also PMF-capable — the AP is dropped as a
candidate before any association is attempted.

Fixed in the kernel fork by deriving `MFPEnabled` from the RSN capability bits the supplicant
actually sent, and by allowing the optional-PMF/AP-not-capable case in
`csrIsPmfConnectionAllowed()` (whose comment contradicted the `!MFPRequired` in its own
condition, and which would otherwise have dropped every legacy WPA2 AP).

**WPA3-SAE is a different matter and is not available.** prima does ship SAE
(`CONFIG_WLAN_FEATURE_SAE := y`), but every SAE path is gated on
`CFG80211_EXTERNAL_AUTH_SUPPORT`, which needs `NL80211_CMD_EXTERNAL_AUTH` — Linux 4.17, absent
here and never defined anywhere in this tree. So a WPA3-**transition** AP works via WPA2-PSK +
PMF; a WPA3-**only** AP cannot work without backporting external-auth into cfg80211.

### Associated, IP address, gateway reachable — and every name lookup fails

Symptom: WLAN connects, connman reports the service `online`, `ping 8.8.8.8` works, but nothing
resolves, so nothing in the UI can reach the internet. `getent hosts google.com` returns nothing
(`rc=2`) and `systemd-resolve google.com` says *"All attempts to contact name servers or networks
failed"*.

**Cause: `CONFIG_ANDROID_PARANOID_NETWORK` + systemd-resolved's own service user.** The kernel is
built with paranoid networking, and it must be — per
[hadk-faq](https://github.com/mer-hybris/hadk-faq/blob/master/README.rst), *"since hybris-12.1,
rild does not work without ANDROID_CONFIG_PARANOID_NETWORK"*, so turning it off would cost us
cellular. With it on, `socket(AF_INET, …)` fails with `EPERM` for any process whose groups do not
include `AID_INET` (gid 3003, `inet`) — the call never reaches the network at all. Verified
directly on device: as uid 997 socket creation raises `PermissionError`; with `setgroups([3003])`
first it succeeds.

The documented fix is to put the user in `inet`, which droid-hal-device autodetects and does
(`mer_verify_kernel_config` marks the flag `y,n` and says as much; `mal`, IRC 2017-01-15: *"devices
that need paranoid need to have nemo user in inet group, this is done in latest dhd submodule"*).
That covers `defaultuser` — and used to be the whole story, because connman resolved names itself.

**Sailfish 5.x moved DNS to systemd-resolved**, and dhd's autodetect does not follow it there.
connman is built with `src/dns-systemd-resolved.c` and hands the DHCP nameservers to
`org.freedesktop.resolve1` via `SetLinkDNS` (visible in `systemd-resolve --status` as per-link DNS
servers on `wlan0`), and `/etc/resolv.conf` points at the `127.0.0.53` stub. But
`systemd-resolved.service` runs as its own system user `systemd-resolve` (uid 997), which is in no
Android group, so it was denied every socket. In the debug log it shows as `Sending query packet`
followed instantly by `Switching to DNS server …`, cycling all servers forever. The unit's
`AmbientCapabilities=CAP_NET_RAW` cannot help: this is a group check, not a capability check.

Fix — `droid-config-karatep`,
`sparse/etc/systemd/system/systemd-resolved.service.d/99-android-inet-group.conf`:

```ini
[Service]
SupplementaryGroups=inet
```

Two dead ends worth not repeating: the stub symlink `/etc/resolv.conf → stub-resolv.conf` is
**correct**, not leftover cruft — deleting it does not make connman write a resolv.conf, because
this connman delegates to resolved. And running resolved as `User=root` does not fix it either;
it drops to uid 997 itself, so the socket is still denied (and clearing `CapabilityBoundingSet=`
empties the set rather than resetting it, which just makes it fail earlier with *"Failed to change
group ID"*).

Note this applies to **any** non-root Sailfish daemon that needs a socket, not just resolved.
Searching the IRC archive for `inet group` returns dozens of hits, but `127.0.0.53` and
`stub-resolv` return **zero** — the paranoid-network mechanism is old and well documented, this
particular victim of it is not.

---

## Audio

Audio was completely silent on every output, and capture returned digital silence. It was three
independent faults stacked on top of each other, each of which alone is enough to produce total
silence, and none of which logs an error.

### Everything silent: `module-policy-enforcement` never loaded

`droid-config`'s `sparse/etc/pulse/xpolicy.conf.d/fmradio.conf` was a 21-byte file whose entire
content was the literal text `fmradio.conf.disabled` — a botched attempt to disable the snippet,
added as an unrelated drive-by in `2125abf` ("Bluetooth", Sep 2024). The real snippet already
ships beside it as `fmradio.conf.disabled`, which is how upstream ships it switched off, so
nothing was ever meant to be at that path.

`module-policy-enforcement` parses `/etc/pulse/xpolicy.conf.d/*.conf` at init. That stray line is
not valid syntax, `pa__init()` failed, and the module never loaded. There is no error anywhere:
the only visible trace is a **gap at module index 12** in `pactl list short modules`.

That matters because `arm_droid_default.pa` deliberately ends with:

```
set-default-sink sink.null
set-default-source source.null
```

Moving streams off those is precisely policy enforcement's job. Without it every stream played
into a null sink and every capture read from a null source — no loudspeaker, no earpiece, no
3.5 mm, no microphone — while the droid card, the ACDB calibration, the ADSP routing
(`MM_DL5 -> PRI_MI2S_RX`), the codec DAPM path (`RX2 -> HPHR PA`) and the AW87319 speaker amp were
all healthy and silent about it.

> If audio is dead but `pactl list sinks` looks perfect, count the module indices.

### Only the loudspeaker worked: this HAL has no `create_audio_patch`

`pa_droid_stream_set_route()` changes an open stream's device with the HAL's
`create_audio_patch()` unless `DM_OPTION_USE_LEGACY_STREAM_SET_PARAMETERS` is set. karatep's HAL
does not implement it and returns `-ENOSYS`:

```
W: droid-util.c: Failed to create output audio patch "primary output"->"Wired Headset" (38)
W: droid-util.c: Failed to update output stream audio patch (38)
```

Every routing change *after* stream open therefore failed, and the stream stayed on whatever
device it was opened with. Streams open on the speaker — which is exactly why the loudspeaker was
the one output that ever worked. With a headset plugged in, PulseAudio moved its own port and the
kernel switch reported the jack, but the HAL was never told and kept `snd_device(2: speaker)`.
That was the long-standing "3.5 mm: jack detected, not routed" entry.

Fixed with `use_legacy_stream_set_parameters=true` in `sparse/etc/pulse/arm_droid_card_custom.pa`,
which falls back to `set_parameters("routing=...")` — which this HAL does implement.

### No microphone: the vendor policy config omits the built-in mics

`/vendor/etc/audio_policy_configuration.xml` routes `Built-In Mic` only to the `record_24` and
`voip_tx` mix ports. The route whose sink is `primary input` lists only Wired Headset Mic, BT SCO
Headset Mic, FM Tuner and Telephony Rx.

Android copes, because AudioPolicyManager may serve `AUDIO_SOURCE_MIC` from any mix port that
fits. `pulseaudio-modules-droid` may not: it builds `source.primary_input` from the primary input
mix port alone. So the source came up with **no `input-builtin_mic` port at all** and PulseAudio
selected the highest-priority available one instead — `input-voice_call`, at priority 200 because
Telephony Rx is in `attachedDevices`. Every capture then reached the HAL as
`source(4)=VOICE_CALL`, which it rejected:

```
E voice: voice_check_and_set_incall_rec_usecase: As voice call is not active,
         Incall rec usecase can't be selected for requested source:4
D audio_hw_primary: start_input_stream: exit: status(-22)
```

retried about every 100 ms for as long as the stream was open. No capture PCM was ever opened and
every recording was digital silence, while PulseAudio reported the source as `RUNNING`.

Fixed by pointing `module-droid-card` at our own copy of the config with the `config=` modarg
(`/etc/pulse/droid/audio_policy_configuration.xml`), which differs from the vendor file in that
one route and nothing else. `/vendor` is left untouched and the override is scoped to PulseAudio.

### Microphone topology

Two internal analog mics plus the headset mic, all on one PulseAudio source with selectable ports:

| PulseAudio port | HAL snd_device | mixer path | Codec input |
|---|---|---|---|
| `input-builtin_mic` | `68: handset-mic` | `adc1` | AMIC1 (primary, bottom) |
| `input-back_mic` | `77: speaker-mic` | `adc3` | AMIC3 (secondary) |
| `input-wired_headset` | `85: headset-mic` | `adc2` | AMIC2 (3.5 mm) |

A 2-channel capture from the built-in mic selects `115: handset-stereo-dmic-ef` and returns two
genuinely independent signals (high-frequency L/R correlation 0.05; one mic duplicated into both
channels would correlate ~1.0).

### Fluence dual-mic noise suppression

`ro.vendor.audio.sdk.fluencetype=fluence` already advertises `FLUENCE_DUAL_MIC`, but the vendor
ships every `persist` switch off, so capture ran single-mic and the second mic was unused.

**Read the property names off the shipped binary, not off `hardware/qcom/audio` in the tree** —
they are not the same code. The tree's `hal/msm8916/platform.c` reads unprefixed
`persist.audio.fluence.*` and has no `audiorec` switch at all, while
`/vendor/lib64/hw/audio.primary.msm8937.so` contains `persist.vendor.audio.fluence.{audiorec,
hfpcall,mode,speaker,voicecall,voicerec}`. The vendor-prefixed names are the ones that take
effect, and `.audiorec` covers ordinary `AUDIO_SOURCE_MIC` recording — all PulseAudio ever asks
for.

Measured, 8 s of speech over deliberate background noise, same conditions, only `.audiorec`
changed:

| | snd_device | speech | noise floor | SNR |
|---|---|---|---|---|
| off | `68: handset-mic` | -27.7 dBFS | -42.4 dBFS | 14.7 dB |
| on | `73: dmic-endfire` | **-25.8 dBFS** | **-61.8 dBFS** | **36.0 dB** |

Speech came out 1.9 dB *louder* while the floor fell 19.4 dB, which rules out the obvious
alternative explanation — that the quieter capture seen in a silent room was simply less gain.

Enabled in `sparse/usr/libexec/droid-hybris/system/etc/init/fluence.rc`. The trigger must be
`post-fs-data`, not `post-fs`: init loads persistent properties from `/data` during
`post-fs-data`, and a `persist.*` property set before that is overwritten by the stored value.
`.voicecall` and `.speaker` are deliberately left alone — they change in-call and speakerphone
routing, untestable with only a dummy SIM.

### The earpiece needs no call to test

`connected_port()` in `droid-util.c` hardcodes `output-earpiece` to `PA_AVAILABLE_NO`, and the
`xpolicy.conf.d/earpiece.conf` rules only fire on call/VoIP device types, so it looks untestable.
It is not — forcing the port works once routing changes actually reach the HAL (see
`create_audio_patch` above):

```sh
pactl set-sink-port sink.primary_output output-parking
pactl set-sink-port sink.primary_output output-earpiece
```

The HAL then moves to `snd_device(1: handset)` and the codec brings up `EAR / EAR PA / EAR_S /
EAR CP` with `Ext Spk` off. The `output-parking` step is not optional: the droid HAL applies a
mode change on the *next* routing change, and setting an already-active port is a no-op.

### Headset detected as headphone: the TS3A227E never ran a detection

The 3/4-pole decision on this board belongs to a TI **TS3A227E** accessory-detection chip on
`i2c_2` — `wcd-mbhc-v2`'s own cross-connection check is compiled out under
`CONFIG_SND_SOC_TS3A227E`. Its detection always timed out, so every 4-pole headset was classified
as a 3-pole headphone, the mic line was never enabled, and inline buttons never registered:

```
ts3a227e_auto_detect time out
wcd_correct_swch_plug: Valid plug found, plug type headphone
wcd_mbhc_btn_press_handler: Plug isn't headset, ignore button press
```

Capturing from the headset mic gave only the AMIC2 noise floor, about -68 dBFS, against roughly
-31 dBFS on both internal mics.

The cause is an inherited LineageOS regression, `8d2f38f67c27` ("ASoC: ts3a227e: Fix misleading
indentation", Jul 2021). It silenced `-Wmisleading-indentation` by adding braces around the wrong
statements, leaving `reinit_completion()` and the `DET_TRIGGER` register write unreachable after
`return -1` inside the null-guard:

```c
if (!ts3a_chip) {
    return -1;
    reinit_completion(&ts3a_chip->detect_compl);
    ts3a227e_update_bits(..., DET_TRIGGER, DET_TRIGGER);
}
rc = wait_for_completion_timeout(...);
```

So the chip was never told to detect anything and the driver then waited for a completion
interrupt that could not arrive. `/proc/interrupts` confirmed it — `msmgpio 25 TS3A227E`
registered, never fired, across the whole uptime. `karate_hp_switch_set()` was damaged the same
way. Fixed in our kernel fork; verified on hardware:

| | before | after |
|---|---|---|
| TS3A227E IRQ count | `0` | `1` on insertion |
| chip detection | `auto_detect time out` | `Detction Results: 0xc` |
| MBHC verdict | `plug type headphone` | `new_plug(headset)` |
| PulseAudio port | stayed `input-builtin_mic` | `input-wired_headset` |
| capture level | -68.2 dBFS (noise floor) | -56.0 dBFS |

> This almost certainly breaks the headset mic on stock LineageOS 18.1 for karate/karatep too.
> Searching the `#sailfishos-porters` archive for `"Plug isn't headset"` and
> `"wcd_correct_swch_plug"` returns **zero hits** in eleven years — no prior art at all.

---

## GPS (does not acquire)

The whole software chain works and a positioning session genuinely reaches the hardware. What
does not happen is acquisition: the receiver **never reports a single satellite in view**, with a
clear sky, over sessions lasting minutes.

### First: you cannot test this with `dbus-send`

`geoclue-hybris` is D-Bus activated and exits when idle, and geoclue's reference count is tied to
the calling connection's unique name. A one-shot tool drops the reference the instant it exits, so
every `GetPosition` spawns a fresh provider that asks for a fix and dies before the engine can
start. Polling in a loop makes it worse, not better: it just churns the client.

That produced a 30 s vote/unvote cycle in the HAL log that looks like a GNSS problem and is not —
it is client churn. Hours went into chasing it.

A session only stays up if something **holds the D-Bus connection open**. `csd` does (dial
`*#*#310#*#*`, GPS test), or use a client that connects, calls
`org.freedesktop.Geoclue.AddReference`, and then simply sleeps without closing the socket.

### Getting the logs that matter

Three separate switches, none of which is obvious:

* **The QCOM loc stack** honours `DEBUG_LEVEL` in `/vendor/etc/gps.conf`, and its own comment says
  that overrides Android's log levels. It ships at `2` (warnings and errors), which hides all
  satellite and session detail. `persist.vendor.sys.gps.loglevel` does **not** override it — that
  was tried and made no difference. Raise `DEBUG_LEVEL` to 4 or 5 and reboot; the log goes from
  ~2 lines per session to several hundred.
* **NMEA** only appears with Qt debug logging on the provider:

  ```sh
  mkdir -p /etc/systemd/user/geoclue-providers-hybris.service.d
  printf '[Service]\nEnvironment=QT_LOGGING_RULES=*.debug=true\n' \
      > /etc/systemd/user/geoclue-providers-hybris.service.d/debug.conf
  systemctl --user daemon-reload    # as the device user
  ```

* **journald retention here is minutes**, so capture while the session runs, not afterwards.

### What works

* `geoclue-provider-hybris-binder` is the right provider for this Android 11 base, and it
  initialises all four interfaces without error: GNSS, AGNSS, AGNSS RIL, GNSS Debug.
* A held session really starts. `mInSession: 1`, and the QMI trace at session start is exactly
  what it should be:

  ```
  1   QMI_LOC_START_REQ_V02
  2   QMI_LOC_SET_OPERATION_MODE_REQ_V02
  1   QMI_LOC_REG_EVENTS_REQ_V02
  48  QMI_LOC_EVENT_POSITION_REPORT_IND_V02
  ```

* NMEA flows at 1 Hz.
* The modem subsystem is healthy (`rmt_storage` writing `modem_fs1`/`modem_fs2`, no ramdumps).

### What does not work, precisely

The NMEA is empty and, decisively, **there is never a `$GPGSV` sentence**:

```
$GPGSA,A,1,,,,,,,,,,,,,,,,*32     fix type 1 = no fix, no satellites
$GPGGA,,,,,,0,,,,,,,,*66          fix quality 0
$GPRMC,,V,,,,,,,,,,N,V*29         V = void
```

GSV reports satellites *in view*. A receiver that is searching emits it within seconds, fix or no
fix, assistance or none. Its total absence means the engine is not searching at all — which the
QMI layer confirms:

* `mEngineOn: 0` throughout the session; `QMI_LOC_EVENT_ENGINE_STATE_IND` (ENGINE_ON) never
  arrives.
* No `QMI_LOC_EVENT_GNSS_SV_INFO_IND_V02`, ever.

The only positions reported are coarse and non-satellite:

```
flags: 1075   source: 2   latitude: 10.989997   longitude: 78.334102
uncertainty: 5000000   SV used in fix (gps/glo/bds/gal/qzss): (0x0/0x0/0x0/0x0/0x0/0x0)
```

roughly 150 km out, zero SVs -- a network/cell seed, not a fix.

### Assistance is also broken, but is not the cause

Worth recording because it is real, and worth fixing once acquisition works:

* The provider requests **MS-Based** mode (`gnssSetPositionMode (1 0 1000 ...)` ->
  `operationMode MSB`). The modem then asks for assistance -- `QMI_LOC_EVENT_INJECT_TIME_REQ_IND_V02`,
  `QMI_LOC_EVENT_WIFI_REQ_IND_V02` -- and nothing answers.
* `geoclue-hybris` gates that on the online aGPS setting; the binary contains
  *"Online aGPS not enabled, not sending NTP request."* and *"...not starting data connection."*
  `hybris\online_enabled` was `false` in `/etc/location/location.conf`.
* It also needs ofono's **default data modem** for the SUPL connection, which this device does not
  have (dummy SIM, mobile data broken).
* **XTRA never downloads.** Only an empty `xtra.sqlite`, no `lto2.dat`, although the server is
  reachable from the device (`curl http://gllto.glpals.com/.../lto2.dat` returns 180 KB).
  `XTRA_CA_PATH` in `/vendor/etc/gps.conf` pointed at `/usr/lib/ssl-1.1/certs`, which does not
  exist; `/etc/ssl/certs` does.
* `reportSv: At least one RF_LOSS is 0 in gps.conf, please configure it` -- RF loss compensation
  is uncalibrated for this device.

None of this explains zero satellites *in view*, which happens before assistance is relevant. It
would explain a slow or absent *fix*, not an engine that never searches.

### Fixed along the way

The QCOM loc stack looks for its configuration in `/etc` as well as `/vendor/etc` -- the same
place on Android, not on a Sailfish rootfs. All six were missing: `gps.conf`, `izat.conf`,
`lowi.conf`, `sap.conf`, `xtwifi.conf`, `flp.conf`. Now symlinked from droid-config's
`sparse/etc/`. Long-standing advice from mal on `#sailfishos-porters` (2016-07-08, 2016-09-15,
2019-06-27). It did **not** fix acquisition.

### Assistance was completely dead, and why

The vendor GNSS HAL **never asks for assistance**. The modem asks -- every session carries
`QMI_LOC_EVENT_INJECT_TIME_REQ_IND_V02` and `QMI_LOC_EVENT_WIFI_REQ_IND_V02` -- but the HAL does
not propagate it. `geoclue-providers-hybris` handles `GNSS_REQUEST_TIME_CB` properly and calls
`injectUtcTime()` from it; the callback simply never fires. `GNSS_REQUEST_LOCATION_CB` does fire,
and its handler only logs. So nothing was ever injected: no time, no ephemeris, no position seed.
Every attempt was a cold start with nothing to work from.

The provider can be told to inject anyway, from `/etc/gps_xtra.ini`, which did not exist here:

```ini
[ntp]
NTP_FORCE_INJECT=true

[xtra]
XTRA_FORCE_INJECT=true
XTRA_SERVER_0=https://gllto.glpals.com/7day/v5/latest/lto2.dat
```

The XTRA URLs have to be repeated in that file -- the provider reads them from its `[xtra]`
section, not from `gps.conf`. Use **https**: the vendor's `gps.conf` lists these as plain `http://`,
and XTRA is ephemeris injected straight into the GNSS engine, so an unauthenticated fetch is a
tamper vector. All three hosts serve the same 185775-byte file over TLS with a valid certificate,
and the provider fetches through Qt against the system CA store. Now shipped in droid-config's
`sparse/etc/`. Verified:

```
Forcing XTRA data injection / Forcing NTP injection
Got NTP response from QHostAddress("139.59.55.93")
injected 185775 bytes of xtra data
```

Time injection needed a code fix as well. `injectUtcTime()` gave up permanently when
`defaultRoute()` returned an unnamed service with no time servers, which is what a D-Bus activated
provider sees if it asks before connman has been queried:

```
Time injection requested
"" doesn't advertise time servers
```

The existing retry timer did not help because it re-sends to `m_ntpServers`, the list that was
never populated. Fixed in our fork (`8ba190f`) by arming the timer on those paths and re-running
`injectUtcTime()` while the list is empty. Note this was **not** what made time injection start
working -- the successful run succeeded first try, once `NTP_FORCE_INJECT` made the call happen at
all. It is insurance against a race that was observed, not the fix.

### Changes that were tried and backed out

Worth recording so they are not re-attempted:

* **Position mode.** `geoclue-providers-hybris` picks MS_BASED vs STANDALONE from
  `m_agpsEnabled`, which is assigned from `hybrisEnabled()` -- whether the provider is enabled at
  all, not whether assistance is available. A patch to key it off `m_agpsOnlineEnabled` was
  committed and then **reverted** (`7f3007b`). It was never validated -- the mode stayed MSB in
  every test, because `hybrisOnlineState()` does not track `hybris\online_enabled` in
  `location.conf` the way the patch assumed -- and once forced injection worked its premise was
  gone: assistance *is* available, so MS_BASED is right, and forced injection is driven by the
  gps_xtra.ini flags rather than by `m_agpsOnlineEnabled` anyway.
* **`/vendor/etc/gps.conf` edits.** `DEBUG_LEVEL=5` was diagnostic. `XTRA_CA_PATH` was pointed at
  `/etc/ssl/certs` because the stock `/usr/lib/ssl-1.1/certs` does not exist -- but it never
  mattered on our path: the XTRA download is done by geoclue-hybris through Qt against the system
  CA store, not by `xtra-daemon`. It is still broken for `xtra-daemon` if that is ever made to
  work. Both reverted; `/vendor` is stock again apart from the fingerprint
  service, which is genuinely required. `gps.conf.orig` is kept beside it.
* **`/etc` config symlinks** are kept, but they changed nothing measurable and remain unproven.

### Where it stands

The software chain is now doing everything it should: sessions start, the engine searches, both
constellations are tracked, and time and ephemeris are injected. What is left is signal level.

Under clear sky the receiver reaches only about **22 dBHz** on a satellite at 76-80 degrees
elevation -- essentially overhead, where open sky should give 35-45 dBHz -- and only one satellite
is heard at a time. Four are needed to fix. Nothing in software explains a ~20 dB deficit.

The kernel device tree has **no GPS entries at all** for karatep or karate-common, so no LNA
regulator or antenna GPIO is under our control; it is modem-controlled or always-on hardware.

The measurement to make next is a comparison, not another code change: on the same handset in the
same spot, an Android build should report 35-45 dBHz on an overhead satellite. If it reports ~22
as well, the port is doing its job and this is the unit's ceiling. If it reports 40+, something
switches the LNA on that we do not.

> Two false trails, recorded so they are not walked again: the 30 s session cycle is client churn,
> not GNSS behaviour; and `LocSvc_GnssInterface: serviceDied` is the HAL reporting that *its
> client* disconnected, not the GNSS service crashing.

---

## Fingerprint (FPC 1020)

The kernel driver and the vendor HIDL `android.hardware.biometrics.fingerprint@2.1` HAL were both
already running; what was missing was the Sailfish side. Jolla's `sailfish-fpd` is unusable here —
it needs a per-device `sailfish-fpd-slave`, and none exists for karatep — so the port uses
[`sailfish-fpd-community`](https://github.com/sailfishos-open/sailfish-fpd-community), pulled in by
`droid-config-karatep` together with `sailfish-devicelock-fpd`. That last one **Conflicts** with
`jolla-devicelock-daemon-encsfa`, so the old daemon has to come out of the pattern or `mic` fails
to resolve.

### The daemon wedges in `FPSTATE_ENUMERATING` and enrolment can never start

Symptom: Settings shows the fingerprint page, but no enrolment ever begins. `journalctl -u
sailfish-fpd-community` stops at `void AndroidFP::enumerate()` and

```sh
dbus-send --system --print-reply --dest=org.sailfishos.fingerprint1 \
  /org/sailfishos/fingerprint1 org.sailfishos.fingerprint1.GetState
```

returns `FPSTATE_ENUMERATING` forever. `enumerateCallback` never appears in the log.

**Cause:** on a fresh install with no templates, this vendor `@2.1` HAL simply stays silent —
`u_hardware_biometry_enumerate()` returns `SYS_OK` and no callback ever arrives, so `enumerated()`
is never emitted. Every other operation is gated on leaving that state, so the daemon is stuck.
Later HIDL revisions require one callback with `finger=0`/`remaining=0` to mean "no templates";
this one predates that. Upstream issue #31 looks similar but blames stale `fpdata` — not our case:
`/data/system/users/100000/fpdata` existed, correctly owned and empty. On IRC, Mister_Magister
(2026-03-18) hit the same silence and worked around it by skipping enumeration entirely.

**Fix:** arm a 3 s single-shot timer around the enumerate call and treat silence as "nothing
enrolled"; a HAL that does answer stops the timer in `enumerateCallback()`, so well-behaved
devices are unaffected. This is a source change to the middleware, so it lives on a fork, not as a
local edit:

| | |
|---|---|
| Fork | `Sailfish-on-karatep/sailfish-fpd-community`, branch `hybris-18.1` |
| Commits | `6c22a6f` — *Do not hang forever when the HAL never answers enumerate()*<br>`c9ad20c` — *Survive a HAL that fails enumerate() when templates exist* |
| Consumed at | `$ANDROID_ROOT/hybris/mw/sailfish-fpd-community` (`origin` = the fork, `upstream` = sailfishos-open) |

`hybris/mw` is not `repo`-managed, so there is no `local_manifests` line to repin — the clone
itself must come from the fork. Rebuild with
`rpm/dhd/helpers/build_packages.sh --mw=sailfish-fpd-community`. The helper parses options with
`getopt` and `--mw` takes an *optional* argument, so the value must be attached: `--mw=REPO`, or
`-mREPO` for the short form. `-m=REPO` passes `=REPO` as the name and the helper tries to clone
`https://github.com/mer-hybris/=REPO.git`; `-m REPO` leaves REPO unconsumed and dies with
`unknown option(s)`. Neither message points at the argument.

---

### Enrolled fingerprints vanish after a reboot, and re-enrolment fails

Both symptoms come from one HAL quirk: **karatep's FPC HAL fails `enumerate()` precisely when
there is something to enumerate.** The vendor library returns the template *count* and the HIDL
service treats any non-zero return as an error:

```
fpc_fingerprint_hal: fpc_enumerate
fpc_tac : fpc_tac_get_template_id_from_index begin/end   (x2)
fpc_fingerprint_hal: fpc_enumerate indices_count 2
...fingerprint@2.0-service: An unknown error returned from fingerprint vendor library: 2
```

which reaches the daemon as `u_hardware_biometry_enumerate()` returning `SYS_UNKNOWN`. This is the
same quirk seen from the other side above: with **no** templates the vendor library returns 0, the
service accepts it, and then nothing follows — hence the silent enumerate. With templates present
it returns the count and the call fails outright.

`setActiveGroup()` fails the same way, which is the first thing visible in the journal:

```
AndroidFP::setGroup(0, "/data/system/users/100000/fpdata")
setActiveGroup failed:  SYS_UNKNOWN
FPDCommunity::enumerate()  ->  slot_failed "SYS_UNKNOWN"
```

Two consequences, which together look exactly like "my fingerprints were lost":

* `enumerate()` bailed on the error without emitting `enumerated()`, so `FPDCommunity` never
  reached `loadFingers()` and `m_fingerMap` stayed empty. `GetAll` returned an empty array and the
  UI showed nothing enrolled.
* Believing nothing was enrolled, the user re-enrols the same finger — and the TEE, which still
  holds it, refuses:

  ```
  fpc_tac : fpc_tac_send_enrol_cmd failed with retval: 13
  fpc_fingerprint_hal: do_enroll finger already enrolled
  onError(8)  ->  fpd "ERROR_VENDOR: 1"
  ```

  so the fingerprint can be neither enrolled nor removed from the UI.

**Nothing is actually lost.** Check before assuming otherwise:

```sh
ls -l /data/system/users/100000/fpdata/user.db          # the templates, ~288 KB
od -c /var/lib/sailfish-fpd-community/100000/fingerprints.db  # the id -> name map
dbus-send --system --print-reply --dest=org.sailfishos.fingerprint1 \
    /org/sailfishos/fingerprint1 org.sailfishos.fingerprint1.GetAll
```

If `user.db` and `fingerprints.db` have content but `GetAll` is empty, this is the bug.

Fixed in the fork (`c9ad20c`): finish the enumeration round even when the call fails, and track
whether the list genuinely came back from the HAL. `loadFingers()` only reconciles the persisted
map against the HAL when that list is real; otherwise the stored fingers are kept as-is.

> That reconcile is the dangerous part. It prunes every finger not in the enumerated list and then
> `saveFingers()` writes the result back, so one failed enumeration would erase the names from disk
> permanently while the templates stayed in the trustlet.

### The real root cause: the HIDL wrapper reads a count as an error code

The FPC vendor library returns the **number of templates** and the LineageOS HIDL service treats
any non-zero return as a failure. The HAL says so in as many words:

```
E fpc_fingerprint_hal: fpc_set_active_group There are 3 fingerprints in userdb 0
E ...fingerprint@2.0-service: An unknown error returned from fingerprint vendor library: 3
```

`fpc_set_active_group` succeeded — it found the group and counted three fingerprints — and the
wrapper turned that into `SYS_UNKNOWN`. `enumerate()` behaves identically
(`fpc_enumerate indices_count 3` then "vendor library: 3"). This single mis-mapping explains every
fingerprint symptom on this port, including why everything looked fine with an empty store: zero
templates returns 0, which the wrapper accepts.

Nothing in `/vendor` can be patched, so the workarounds live in `sailfish-fpd-community`.

### Deleting a fingerprint does not delete it (security-relevant)

**A fingerprint removed in the UI still unlocks the device.** The daemon drops the name from its
own map and persists that, but the template stays in the trustlet:

| | |
|---|---|
| Templates in the TEE | 3 (`fpc_enumerate indices_count 3`) |
| Fingerprints listed by `GetAll` | 1 |
| `user.db` | grew 288 KB → 386 KB across enrol/delete cycles; never shrinks |

Observed directly: three fingers deleted through the UI stopped working, then **worked again after
a reboot**, because the HAL reloads `user.db`, which still contains them. Only the name mapping in
`fingerprints.db` was ever really removed.

This is almost certainly downstream of the mis-mapping above — the wrapper reports
`setActiveGroup` as failed, so removals are never persisted to the store the HAL is holding — but
that has not been proven yet, and no fix is in the tree.

> Fixed; see below. Before the fix the only way to be sure a fingerprint was gone was to clear the
> store by hand: stop `sailfish-fpd-community`, delete
> `/data/system/users/100000/fpdata/user.db` and
> `/var/lib/sailfish-fpd-community/100000/fingerprints.db`, reboot and enrol again.

---

### Root-caused and fixed: the HIDL adapter read a count as an error code

Everything above traces to one line in the HIDL adapter, which is built from
source in our tree (`hardware/lineage/interfaces/biometrics/fingerprint/2.0/`),
not shipped as a blob. The LineageOS 2.0 variant exists to adapt an old
fingerprint-HAL-2.0 vendor library to the 2.1 HIDL interface, and it already
does the right thing:

```c
int ret = enumerate(mDevice, results, &n);

if (ret == 0 && mClientCallback != nullptr) {
    for (uint32_t i = 0; i < n; i++)
        mClientCallback->onEnumerate(devId, fp.fid, fp.gid, n - i - 1);
}
return ErrorFilter(ret);
```

It calls the synchronous 2.0-style `enumerate()` and **synthesises** the
`onEnumerate` callbacks — but only when `ret == 0`. FPC's library returns the
*template count*. With three templates `ret == 3`, so the callbacks were never
synthesised and `ErrorFilter(3)` fell through to `default:` → `SYS_UNKNOWN`.
`fpc_set_active_group` answers the same way, and says so plainly:

```
E fpc_fingerprint_hal: fpc_set_active_group There are 3 fingerprints in userdb 0
E ...fingerprint@2.0-service: An unknown error returned from fingerprint
                              vendor library: 3
```

Because the callbacks never fired, the daemon never learned a single template
id, so it could only know about templates it had enrolled itself. `remove()`
then could not be aimed at the right template: the vendor library had nothing to
delete, reported the operation complete, and the template survived. That is the
whole story — the "lost" fingerprints, the "already enrolled" enrolment failures
and the deleted-but-still-unlocking fingers were all the same bug.

Fixed in `Sailfish-on-karatep/android_hardware_lineage_interfaces`, repinned in
`local_manifests.xml`: treat a positive return as success (`fingerprint.h`
specifies 0 or a negative errno, so a positive value is a count, not an errno),
and in `enumerate()` take the smaller of the returned count and `*max_size` so a
library that ignores the out-parameter cannot make us walk uninitialised
entries.

Verified on hardware. Before, `GetAll` returned one finger while the sensor held
three. After:

```
setActiveGroup to /data/system/users/100000/fpdata
enumerate_cb(4007450469, remaining 2)
enumerate_cb(532816712,  remaining 1)
enumerate_cb(520152970,  remaining 0)
Loaded finger map: QMap((520152970,"finger1")(532816712,"Unknown 532816712")
                        (4007450469,"Unknown 4007450469"))
```

The two orphans surfaced as `Unknown <id>` (upstream's existing handling for
templates present in the store with no name), which made them removable. After
removing both, the trustlet and the daemon agree:

```
fpc_set_active_group There are 1 fingerprints in userdb 0
fpc_enumerate indices_count 1
Loaded finger map: QMap((520152970, "finger1"))
```

### Deploying it: the service lives on the vendor partition

`repo sync --force-sync` is needed once when the project is repinned, because
its remote changes and repo will not otherwise overwrite the work tree.

Build just the service rather than all of hybris-hal:

```sh
make android.hardware.biometrics.fingerprint@2.0-service
```

It then has to reach `/vendor/bin/hw/`, which is a separate read-only partition
(`mmcblk0p53`) belonging to the LineageOS install, not to our image. That makes
this the one change in this port that an image build cannot deliver — it must be
installed onto `/vendor`, and it survives Sailfish reimages precisely because
`/vendor` is not touched by them.

```sh
mount -o remount,rw /vendor
B=/vendor/bin/hw/android.hardware.biometrics.fingerprint@2.0-service
cp -a "$B" "$B.orig"          # keep the original beside it
mv "$B" "$B.busy"             # rename: the running binary cannot be overwritten
cp /path/to/new "$B"          # ("Text file busy" otherwise, and ctl.stop does
chmod 755 "$B"; chown root:shell "$B"   #  not reliably stop it)
python3 -c 'import os; os.setxattr("'"$B"'", "security.selinux",
    b"u:object_r:hal_fingerprint_default_exec:s0\x00")'
rm -f "$B.busy"
reboot
```

`chcon` is not on the device, hence `setxattr`; a fresh `cp` lands as
`vendor_file` and the exec label has to be restored by hand. SELinux is
permissive here so it would likely run either way, but relying on that is not
worth it. The `ro` remount will fail while the old binary is still open — the
reboot settles it.

### Removals are only reported once confirmed

Two invariants were added to `sailfish-fpd-community` while chasing this, and
they are worth keeping regardless of the HAL fix:

* a credential is only reported removed once it has been observed to be gone.
  `onRemoved()` says the HAL considers the operation finished, not that the
  template left the store. Verification is fail-closed.
* persisted names are never pruned against an enumeration that did not actually
  come back, since `saveFingers()` would make the loss permanent.

One gotcha when testing: the lock screen holds a continuous `Identify` session,
so `Remove` returns `FPREPLY_ALREADY_BUSY` (`int32 3`) and re-arms within seconds
even after a daemon restart. Unlock the device and keep the screen awake.

---

### Do not restart `vendor.fps_hal`

It does not reload its trustlet. After `setprop ctl.restart vendor.fps_hal` every secure command
fails and the kernel says:

```
QSEECOM: __qseecom_send_cmd: app_id 5 (fpctzappfingerprint) is not found
QSEECOM: qseecom_ioctl: failed qseecom_send_cmd: -2
```

Only a reboot restores it. Restarting `sailfish-fpd-community` alone is safe and is enough to pick
up daemon-side changes.

> Debugging any of this is hampered by journald's tiny retention on this device — `Logs begin at`
> is routinely only a couple of minutes back, so the boot-time fpd log is gone before you look.
> Raise it before investigating.

---

## Notification LED

karatep has **one white LED**, which the kernel exposes as `/sys/class/leds/green` — PMI8950
MPP2 in PWM mode, `qcom,led_mpp_2` in
`arch/arm/boot/dts/qcom/karate-common/msm8937-lenovo-common.dtsi`. A second node, `red`, exists
under the charger PMIC (`/sys/devices/soc/qpnp-smbcharger-17/leds/red`) but drives no physical
LED.

`mce-plugin-libhybris` probes its sysfs backends in a fixed order, and the **`redgreen`** backend
— whose probe list is exactly `/sys/class/leds/red` + `/sys/class/leds/green` — is tried before
`white`. Both directories exist here, so autoprobe always matched `redgreen`, which maps pattern
colours through

```c
if (r || g) { red = r; green = g; } else { red = b; green = b; }
```

Any pattern with red set and green clear therefore drove only the phantom node and left the real
LED dark — including `PatternCommunication` (`ff00ff`), the generic "you have a notification"
pattern, plus `PatternPowerOff` and `PatternWebcamActive`.

`sparse/etc/mce/60-karatep-led.ini` pins the `white` backend to the green node. It maps colour as
`max(r,g,b)`, so every pattern lights the LED, and it sets `can_breathe = true`, so mce blinks in
software — the LED's `pwm_us` node returns `EIO` on read and is not needed; a plain `brightness`
write is enough.

The LED is also driven harder than Lenovo shipped it. The MPP sink is three bits, 5–40 mA in
5 mA steps (`QPNP_PIN_CS_OUT_5MA..40MA` in `include/linux/qpnp/pin.h`; Qualcomm's PMIC GPIO/MPP
guide 80-NV610-48 documents the same peripheral). The stock 5 mA is the lowest level the hardware
has and is barely visible indoors, so the dtsi now sets `qcom,current-setting = <30>` — a 6x
continuous sink, since mce holds `PatternDeviceOn` at full duty while the display is off — and
`qcom,max-current = <40>`, the true hardware ceiling. `max-current` is only range-checked for MPP
LEDs (`qpnp_mpp_init`) and does not feed `cdev.max_brightness` in PWM mode; that comes from
`MPP_MAX_LEVEL`.

---

## Hardware keys

The three capacitive keys come from the **touchscreen controller**, not `gpio-keys`.
`/proc/bus/input/devices` shows `fts_ts` with `KEY=400 0 0 100040008800 0 0`; decoding that word
(keycodes 128–191, bits 11/15/30/44) gives `KEY_MENU(139)`, `KEY_WAKEUP(143)`, `KEY_BACK(158)`
and `KEY_HOMEPAGE(172)`. The kernel was never the problem.

Home and the app-switcher key did nothing because lipstick gates its entire hardware key
handler on the device *declaring* the key — the tail of
`/usr/share/lipstick-jolla-home-qt5/compositor.qml`:

```qml
Loader {
    active: deviceInfo.hasHardwareKey(Qt.Key_HomePage)
    source: "compositor/HardwareKeyHandler.qml"
}
```

`ssu-sysinfo -k` printed nothing, so the handler was never instantiated. Back kept working
because it is an ordinary `Qt::Key_Back` that applications handle themselves.

`droid-config-karatep`'s `sparse/usr/share/csd/settings.d/50-karatep-hw-settings.ini` declares
them. Two traps:

- ssu-sysinfo globs `/usr/share/csd/settings.d/*hw-settings*.ini` — **the filename must contain
  `hw-settings`** or it is never read.
- The values must be **numeric** Qt key codes. `hw_key_parse()` uses `strtoul(pos, &end, 0)` and
  bails at the first non-numeric token, so a readable `Key_HomePage` silently yields an empty
  list.

Verified on hardware: home opens the app switcher, the square key opens the top menu, back is
unchanged.

---

## Bluetooth (broken)

`android.hardware.bluetooth@1.0-service-qti` aborts itself roughly every 61 s:

```
vendor.qti.bluetooth@1.0-data_handler: Aborting daemon to recover as controller init failed
libc: Fatal signal 6 (SIGABRT) in tid NNNN (bluetooth@1.0-s)
droid-hal-init: Service 'vendor.bluetooth-1-0-qti' (pid NNNN) received signal 6
hwservicemanager: Since android.hardware.bluetooth@1.0::IBluetoothHci/default is not
                  registered, trying to start it as a lazy HAL.
```

so `bluebinder` never activates and loops `Bluetooth binder service failed / Remote has died`,
climbing hci index each time (`Own hci index: 5`).

The HADK-FAQ prerequisites for binderized Bluetooth are already met — `CONFIG_BT_HCIVHCI=y` is
in the defconfig and bluebinder is installed — so this is device-specific controller bring-up,
not missing setup. The SMD transport nodes exist and are owned correctly
(`/dev/smd2`, `/dev/smd3`, `bluetooth:bluetooth`).

**Workaround:** `systemctl mask bluebinder`. With it masked the BT HAL starts once and stays
put instead of crash-looping.

**Also wrong regardless of the HAL bug:** `bluebinder.service` is `WantedBy=graphical.target`
*and* `Before=graphical.target`, so while it is stuck activating it delays the UI. A Bluetooth
proxy should not gate the display.

---

## Boot splash (needs a helper; `HYBRIS_BOOTLOGO`'s own mechanism cannot work)

Between the Lenovo bootloader logo and lipstick the screen is black for ~75 s, which reads as a
hang. The obvious fix — hybris-boot's `HYBRIS_BOOTLOGO`, which makes the initrd run
`zcat /bootsplash.gz > /dev/fb0` — **cannot work on this device**. The diagnosis is below; the
implemented replacement is [`hybris-boot/fbsplash.c`](https://github.com/Sailfish-on-karatep/hybris-boot),
which carries a short summary of it inline.

**No upstream guidance exists.** The [HADK](https://hadk.sailfishos.org/) (all chapters),
[`hadk-faq`](https://github.com/mer-hybris/hadk-faq) and
[HADK Hot](https://sailfishos.wiki/books/hardware/page/hadk-hot) contain **zero** mentions of a
boot splash, boot logo, `HYBRIS_BOOTLOGO` or `/dev/fb0`. HADK Hot's single mention of
`bootanimation` — `ANDROID_ROOT="/system" /system/bin/bootanimation` — is a *graphics-stack test
binary* listed beside `surfaceflinger` for use after `systemctl mask user@100000`. It is not a
splash mechanism. Do not go looking again.

Nor is there community guidance. The
[`#sailfishos-porters` archive](https://piggz.co.uk/sailfishos-porters-archive/) — greppable, and
the right first stop for anything like this — has **no hit at all** for `FBIOPAN` or `cont_splash`
in eleven years of logs, and what it does say about the splash is uniformly negative:

| | |
|---|---|
| `2015-02-13` sledges | *"nope, bootsplash was never implemented"* |
| `2016-04-01` alterego | *"there's no bootsplash for HADK port, so the framebuffer may get stuck until something clears it, or it may go black for a bit whilst Sailfish starts"* |
| `2019-04-12` r0kk3rz | *"there isnt a sailfish bootsplash"* — in reply to someone who had found `bootsplash()` in the init script and assumed it worked |
| `2018-01-25` lSDriim | *"I enabled bootlogo and when I tried do `zcat /bootsplash.gz > /dev/fb0` manualy it never returns"* |
| `2017-12-26` r0kk3rz | *"apparently the bootsplash was causing my device to hang at boot"* |
| `2014-08-12` energycsdx | *"bootlogo is disabled by default in hybris-boot"* |

So `HYBRIS_BOOTLOGO` is a vestigial feature that nobody has working, and on other devices it has
**hung the boot** rather than merely failing. That is why `fbsplash` forks into the background
before it touches the framebuffer and gives up on the first error: on this code path a cosmetic
splash must never be able to cost boot time, let alone block it. (Related, and worth remembering
if display power ever misbehaves after a splash: `2014-08-15` spiiroin, on a backlight bug —
*"current guess is: the boot logo leaves kernel side in some 'bootlogo mode'"*.)

**Why the write fails.** `/init.log` shows `zcat: write error: No such device` (ENODEV) on every
attempt. mdss defines no `.fb_write`, so generic `fb_write()` from `fbmem.c` runs, and it returns
`-ENODEV` whenever `info->screen_base` is NULL. On karatep it is always NULL, because of a
device-tree/driver mismatch in our own kernel:

```c
/* drivers/video/msm/mdss/mdss_fb.c — mdss_fb_alloc_fbmem_iommu() */
fbmem_pnode = of_parse_phandle(pdev->dev.of_node, "linux,contiguous-region", 0);
if (!fbmem_pnode) {
        mfd->fbi->screen_base = NULL;   /* no memory... */
        mfd->fbi->fix.smem_start = 0;
        return 0;                       /* ...and it reports success */
}
```

```dts
/* arch/arm64/boot/dts/qcom/msm8937-mdss.dtsi:193 */
mdss_fb0: qcom,mdss_fb_primary {
        compatible = "qcom,mdss-fb";
        qcom,cont-splash-memory {                       /* child node! */
                linux,contiguous-region = <&cont_splash_mem>;
        };
};
```

The property is one level below where the driver looks, so `of_parse_phandle()` returns NULL and
the framebuffer gets no memory — silently, with no warning. It is allocated lazily by
`mdss_fb_mmap()` under `if (!mfd->fbi->screen_base)`, and nothing in the initramfs ever mmaps fb0.

Reproduce it on a booted device (stop the UI first, per HADK Hot):

```sh
systemctl stop user@100000
python3 -c 'import os; fd=os.open("/dev/fb0", os.O_RDWR); os.write(fd, b"\xff"*4352*1920)'
# OSError: [Errno 19] No such device
systemctl start user@100000
```

**Second, independent blocker.** Even with memory, the panel is composited by MDP5: the visible
image comes from pipes reprogrammed only on an explicit commit (`mdss_mdp_overlay.c:3383` has a
dedicated "pan display is called before handoff is completed" branch), and `/proc/cmdline`
confirms continuous splash is on:

```
mdss_mdp.panel=1:dsi:0:qcom,mdss_dsi_nt35596_tm_1080p_video:1:none:cfg:single_dsi
                                                            ^ cont_splash_enabled
```

A `memcpy` into `smem` is never scanned out.

**Third blocker, found while writing the helper: closing the fd undoes the splash.**
`mdss_fb_release_all()` (`mdss_fb.c:3004`) runs on the *last* close of `/dev/fb0`, and at
refcount zero it does `mdss_fb_set_backlight(mfd, 0)` (`:3075`),
`mdss_fb_blank_sub(FB_BLANK_POWERDOWN, …)` (`:3077`) and `mdss_fb_free_fb_ion_memory()` (`:3085`)
— backlight off, panel off, buffer freed. A helper that exits after committing would show the
image for a few milliseconds and then go dark, which looks exactly like the bug being fixed.

### The fix: `fbsplash`

`hybris-boot/fbsplash.c` — a 4.4 KB **freestanding** aarch64 binary staged into the initramfs
beside busybox (`$(PRODUCT_OUT)/utilities/`, copied in by the ramdisk rules in `Android.mk`).
`bootsplash()` in `init-script` now runs:

```sh
zcat /bootsplash.gz | /bin/fbsplash
```

The sequence, and why each step is needed:

| Step | Why |
|---|---|
| `clone(SIGCHLD)`, parent exits | boot never waits on the display; the only cost is `zcat` filling the pipe |
| `open("/dev/fb0", O_RDWR)` | also unblanks: `mdss_fb_open()` (`:2951`) runs `FB_BLANK_UNBLANK` on first open, without which `mdss_fb_pan_display_ex()` bails at `mdss_fb_is_power_off()` (`:3423`) |
| `FBIOGET_{F,V}SCREENINFO` | `fix.smem_len` is populated at registration (`:2888`) even with no memory allocated |
| `mmap(smem_len)` | **the load-bearing step** — triggers the lazy ION allocation (`:2495` → `:2371`), which is what sets `screen_base` (`:2437`) *and* `mfd->fbmem_buf` (`:2400`), the dma_buf MDP scans out from. Larger than `smem_len` → `-EOVERFLOW` (`:2490`) |
| `read(stdin)` into the map | mapping is write-combine (`:1426`, `:2532`), so no cache maintenance and `read(2)` can land straight in it |
| `FBIOPAN_DISPLAY`, `yoffset = 0` | the commit. `mdss_mdp_overlay_pan_display()` sources the pipe at `xoffset*bpp + yoffset*line_length` (`mdss_mdp_overlay.c:3414`) and calls `mdss_mdp_overlay_start()`, whose kerneldoc (`:1342`) describes this exact splash→bootanimation handoff. `var.activate` is **not** consulted on this path (only `FBIOPUT_VSCREENINFO` reads it, `fbmem.c:1002`) |
| **do not close** — sleep forever holding the fd | see the third blocker above |

Because the pan offset is now explicit, the image only needs **one** screen
(4352 × 1920 = 8,355,840 bytes); `make-bootsplash.py` defaults to `--buffers 1`.

Escape hatch if the holder process is ever in the way: `killall fbsplash` (it releases the fd and
blanks the panel). Turning the whole thing off again is `HYBRIS_BOOTLOGO := 0` in `Android.mk`.

### Fourth blocker: do not link this against bionic

The first working version of the logic above still produced nothing, because it never reached
`main()`:

```
[    4.000389] random: fbsplash urandom read with 93 bits of entropy available
[    4.000674] Unhandled fault: alignment fault (0x92000021) at 0x9aab744d0267201b

+ zcat /bootsplash.gz
+ /bin/fbsplash
Bus error
```

The fault address is different on every run and carries full entropy, i.e. bionic's static startup
dereferences random bytes shortly after seeding itself from `/dev/urandom` — its TLS/main-thread
setup does not survive this environment. A 1.3 KB freestanding test binary runs in the same shell
without complaint, so the initramfs itself is fine.

The reason this has never bitten anyone before: **the busybox in the initramfs is not an Android
binary.** `file` calls it *"statically linked, for GNU/Linux 3.7.0"* — a glibc prebuilt. Nothing
else in the hybris-boot ramdisk is compiled against bionic.

So `fbsplash.c` is freestanding: raw `svc #0` syscalls, its own `_start`, `-nostdlib`, hand-copied
`fb_{fix,var}_screeninfo` (with `_Static_assert`s on their sizes, since a hand-copied UAPI struct's
size is the one thing that can silently drift). `Android.mk` therefore builds it with a direct
`clang` rule rather than `BUILD_EXECUTABLE`. **Do not convert it back to a normal module.**

Being freestanding also removed the last reason for a watchdog. An earlier draft armed `alarm(10)`
around the ioctls because `FBIOPAN_DISPLAY` can block for `WAIT_DISP_OP_TIMEOUT` = 30 s
(`mdss_fb.h:46`). That buys nothing here: the process forks before touching the display, so a hang
costs no boot time, and it is *meant* to live forever holding `fb0` — a hung pan and a successful
one cost exactly the same. Three syscalls of signal setup, deleted.

### Verified on hardware

`fastboot boot hybris-recovery.img`, then on the telnet shell at `192.168.2.15:23`:

```
# zcat /bootsplash.gz | /bin/fbsplash
fbsplash: fb0 xres=1080 yres=1920 yres_virtual=3840 bpp=32 line_length=4352 smem_len=16711680
fbsplash: copied 8355840 of 16711680 bytes
fbsplash: splash displayed, holding /dev/fb0 open
```

The wordmark appears on the panel and stays. The geometry line doubles as a check on the
hand-copied structs — it must match `/sys/class/graphics/fb0/{virtual_size,stride,bits_per_pixel}`.
Killing an older `fbsplash` while a newer one holds `fb0` leaves the image up, as the refcount
analysis predicts.

### The artwork

`assets/sfosboot.png` is the official Sailfish wordmark: **black artwork on a transparent
background**. Composited onto a black splash that is a black rectangle, so `make-bootsplash.py`
defaults to `--ink white`, which ignores the artwork's own colour and uses its **alpha channel as
ink coverage**, painted white on black — preserving the anti-aliasing. Scaling is a single
`min()` factor for both axes (never stretched), positioned by `--fit-width` / `--center-y`.

> **Dead ends, recorded so they are not retried:** writing the image into both framebuffer
> buffers (the offset is not the problem); correcting the image geometry to stride 4352 × 1920
> (the size was already right — it matches `stride * virtual_h` exactly); retrying the write
> up to 15 times (it can never succeed, and cost 15 s on every boot until reverted); and building
> the helper as an ordinary Android `BUILD_EXECUTABLE` (see the fourth blocker above).

---

## Known-good workarounds (not yet root-caused)

* **`bluebinder` and WLAN conflict at boot.** With `bluebinder` unmasked, `wlan0` never
  appears and `modprobe wlan` reports no such device; `bluebinder` itself hangs in
  "activating". Masking `bluebinder` makes WLAN work, and Bluetooth works if the service is
  started manually after boot. Possibly related to an incorrect WLAN MAC — unconfirmed.
* **`ofono` sometimes needs `systemctl restart ofono` after boot** for RIL to come up.
* **To escape a bootloop**, create `/data/.stowaways/sailfishos/init_enter_debug2`. init then
  halts before starting systemd and telnet is available on port 2323. (Thanks @mal)
* **Always reboot with `reboot -f`.** A graceful `reboot` hangs indefinitely — once observed
  wedged for ~36 minutes with `systemctl is-system-running` reporting `initializing` and 46 jobs
  queued. `droid-hal-init.service` never stops, so systemd waits for it forever. `-f` skips the
  shutdown transaction. This matters when a fix is applied over telnet: without `-f` the device
  appears to have crashed.

---

## Debugging the boot

The stock journald configuration on this rootfs destroys boot evidence:

```
Storage=volatile
RuntimeMaxUse=1M
RateLimitBurst=300
```

With anything crash-looping, `journalctl -b` and `dmesg` both wrap within ~500 s, so the first
two minutes of boot are gone by the time you look. Before debugging any boot problem:

```sh
cp /etc/systemd/journald.conf /etc/systemd/journald.conf.bak
printf '%s\n' '[Journal]' Storage=persistent SplitMode=none \
    RateLimitIntervalSec=0 RateLimitBurst=0 SystemMaxUse=300M RuntimeMaxUse=64M \
    > /etc/systemd/journald.conf
```

`/system/bin/logcat` exists but is **not on `PATH`** in the debug shell. The Android-side log
is where HAL and binder errors actually land, so invoke it by full path:

```sh
/system/bin/logcat -d | grep -iE "composer|binder|servicemanager"
```

Useful one-liner to see which `libbinder` each process actually linked — the fastest way to
spot a VNDK namespace problem:

```sh
for d in /proc/[0-9]*; do p=${d#/proc/}
  grep -o "/[^ ]*libbinder\.so" /proc/$p/maps 2>/dev/null | sort -u |
    sed "s|^|$(cat /proc/$p/comm 2>/dev/null) |"
done | sort -u
```

---

## Still to fix

* IMS daemons (`vendor.imsrcsservice`, `vendor.ims_rtp_daemon`, `vendor.imsdatadaemon`)
  crash-loop continuously, spamming the journal.
* `hwservicemanager` retries `android.hardware.bluetooth@1.0::IBluetoothHci` every ~61 s.
* Failed systemd units: `droid-bootctl.service`, `systemd-tmpfiles-setup.service`,
  `wlan-module-load.service`. (`dev-binderfs.mount` also fails, but that is expected — binderfs
  is Linux 5.0+ and this kernel is 3.18; the legacy `/dev/{,hw,vnd}binder` nodes are correct.)
* Mobile data does not work; SIM slot 2 reports "Network: Denied".
* Cameras and RIL are flaky.

---

## References

* HADK — <https://hadk.sailfishos.org/>
* HADK Hot — <https://sailfishos.wiki/books/hardware/page/hadk-hot>
* Sailfish OS wiki — <https://sailfishos.wiki/>
* `#sailfishos-porters` IRC logs — <https://irclogs.sailfishos.org/logs/%23sailfishos-porters/>
* ofono conflict discussion —
  <https://irclogs.sailfishos.org/logs/%23sailfishos-porters/%23sailfishos-porters.2023-02-03.log.html>
* SELinux permissive setup —
  <https://piggz.co.uk/sailfishos-porters-archive/index.php?log=2024-08-20.txt#line64>
* Reverse USB tethering —
  <https://forum.sailfishos.org/t/using-internet-over-rndis-usb-computer-reverse-tethering/10104/6>
* LineageOS device settings reference —
  <https://github.com/tanvirr007/CustomROM_build_guide_aosp>
