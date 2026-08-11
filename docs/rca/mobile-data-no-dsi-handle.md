# Root cause: mobile data never connects — `unable to get dsi hndl`

Device: **lenovo/karatep** — Lenovo Vibe K6 Note / Plus, **MSM8937 / Snapdragon 430**, Adreno 505.
Base: LineageOS 18.1 (Android 11) / `hybris-18.1`, aarch64, Sailfish OS 5.1.0.11.

Status: **fixed, verified on hardware.**
Fix is `external/stub_netd` (`Sailfish-on-karatep/stub_netd`, branch `hybris-18.1`) plus the
`netd-stub.rc` shipped by `droid-config-karatep`.

Diagnosed against a **live BSNL SIM** (MCC 404 / MNC 80) — the first time this was testable,
since every earlier attempt had only a dummy SIM.

---

## Symptom

Mobile data never connects. Activating the context does nothing:

```sh
dbus-send --system --print-reply --dest=org.ofono /ril_0/context1 \
    org.ofono.ConnectionContext.SetProperty string:Active variant:boolean:true
# Error org.ofono.Error.Failed: Operation failed
```

`ofono` logs:

```
ofonod: Activating context: 1
ofonod: Unexpected data call status 4100
```

No `rmnet_data` interface ever comes up, and connman's `cellular_404800123368911_context1`
stays `State = idle`.

This is **not** an ofono misconfiguration. The APN is provisioned correctly and the request
that reaches the RIL is well formed:

```
RILQ: RIL[0] onRequest: UI --- RIL_REQUEST_SETUP_DATA_CALL (27) ---> RIL [token 576, data len 120]
RILQ: RIL[0] qcril_data_request_setup_data_call: RIL APN [bsnlnet]
RILQ: RIL[0] qcril_data_store_call_params: copying ril_ipfamily=IPV4V6
RILQ: RIL[0] qcril_data_store_call_params: copying ril_auth_pref=0
RILQ: RIL[0] qcril_data_store_call_params: copying ril_tech=16
```

and then dies immediately:

```
E RILQ: RIL[0] qcril_data_request_setup_data_call: unable to get dsi hndl
E RILQ: RIL[0] qcril_data_request_setup_data_call: EXIT with FAILURE
```

---

## What actually happens

### 1. It is not a missing daemon

The obvious suspicion — that the Qualcomm data-path daemons are not running — is wrong.
All of them are up:

```
init.svc.netmgrd: running        /vendor/bin/netmgrd        pid 1775
init.svc.vendor.ipacm: running   /system/vendor/bin/ipacm   pid 1761
init.svc.vendor.dataqti: running /vendor/bin/qti            pid 1778
init.svc.vendor.cnd: running     /system/vendor/bin/cnd     pid 1894
```

`/dev/ipa` exists, `rmt_storage` is writing `modem_fs2`, and the modem is otherwise healthy
(registered on LTE, SIM read, calls-capable).

### 2. `netmgrd` on Android 11 is a client of the **Netd HAL**

`netmgrd` logs exactly two lines for its whole lifetime and then goes quiet:

```
I QC-NETMGR-LIB: NetmgrNetdClientInit(): Looking for Netd service
D QC-NETMGR-LIB: registerServerNotification(): Successfully registered for Netd HAL service
```

That second line is not success — it is `netmgrd` registering for a *service notification*
and then waiting for the service to appear. Its symbol table says exactly what it wants:

```
android::system::net::netd::V1_1::INetd::getService(...)
android::system::net::netd::V1_1::INetd::registerForNotifications(...)
android.system.net.netd@1.1.so
"%s(): INetd discovered"
```

So this vendor's `netmgrd` will not bring the data path up until something publishes
`android.system.net.netd@1.1::INetd`.

### 3. Sailfish deliberately disables `netd`, so nothing ever publishes it

`droid-hal-configs` ships `disabled_services.rc`, which renames the service out of existence
(`droid-configs-device/sparse-11/usr/libexec/droid-hybris/system/etc/init/disabled_services.rc:1`):

```
service netd netd_HYBRIS_DISABLED
```

This is upstream mer-hybris and it is **intentional** — connman owns routing and iptables on
Sailfish, and a live Android `netd` would fight it. droid-hal-init duly reports:

```
droid-hal-init: Parsing file /system/etc/init/netd.rc...
droid-hal-init: /system/etc/init/netd.rc: 1: ignored duplicate definition of service 'netd'
droid-hal-init: Cannot find 'netd_HYBRIS_DISABLED': No such file or directory
```

Confirmation from the running system:

* `lshal` lists 223 registered HALs and **no `INetd` among them**.
* `/dev/socket/dnsproxyd`, `/dev/socket/mdns` and `/dev/socket/fwmarkd` do not exist.
* `/system/bin/netd` is present on disk but has no `init.svc.netd` property.

**Ruled out:** the `updatable` keyword in `netd.rc` (which defers a service until APEX is
ready) is *not* the blocker — `apexd.status` is `ready` and all 20 APEXes are mounted. The
disable is what stops it.

### 4. The chain to `dsi`

With `netmgrd` parked, the data ports it would have mapped are never mapped, so
`libdsi_netctrl` inside `rild` can never hand out a service handle. `qcril_data` asks for
one on every `SETUP_DATA_CALL` and gets nothing:

```
netd disabled
  -> nothing publishes android.system.net.netd@1.1::INetd
    -> netmgrd waits forever on the service notification
      -> data ports never mapped
        -> dsi_netctrl has no handle to give rild
          -> qcril_data_request_setup_data_call: unable to get dsi hndl
            -> ofono: Unexpected data call status 4100
              -> no mobile data
```

---

## The fix

Publish the interface without running real `netd`. `external/stub_netd` is a fork of
[erfanoabdi/stub_netd](https://github.com/erfanoabdi/stub_netd) (UBports, LGPLv3): a small
`cc_binary` implementing `android.system.net.netd@1.1::INetd` where every method is a no-op
returning `StatusCode::OK`. That satisfies `netmgrd`'s dependency while leaving routing
entirely to connman.

This is the established approach on `#sailfishos-porters` — mal asks "do you have dummy
netd?" (2022-03-07), and erfanoabdi wrote this variant specifically to return *valid* values
where the earlier dummy netd returned invalid ones (2020-06-07).

Three changes were needed for Android 11 and this rootfs layout:

* **`libhidltransport` dropped.** It still exists in `system/libhidl/Android.bp`, but only as
  an empty deprecated shim with `visibility: [":__subpackages__"]` — depending on it from
  `external/` is a hard Soong visibility error. Its contents moved into `libhidlbase` in
  Android 10.
* **`init_rc` dropped.** `droid-hal-device` copies only a fixed list of `.rc` files out of
  `out/.../system/etc/init` (`servicemanager.rc`, `init.rc`, `apexd.rc`, `hybris_extras.rc`),
  so an rc installed by Soong would never reach the Sailfish rootfs.
  `droid-config-karatep` ships `netd-stub.rc` instead.
* **Service path repointed** to `/usr/libexec/droid-hybris/system/bin/hw/`. On this port
  `/system` is the read-only LineageOS partition (`/dev/mmcblk0p52`); droid-hal-device copies
  `out/target/product/karatep/system/bin` wholesale into
  `/usr/libexec/droid-hybris/system/bin`. Same convention as `minimediaservice` and
  `minisfservice`.

Build with `bin/build-stubnetd.sh` from inside HABUILD; the binary is then picked up by the
next `build_packages.sh -d`.

---

## Prior art

The porter archive is unusually thin here — `unable to get dsi hndl` has only **two hits in
eleven years** (2014-11-26 morphis, 2018-05-03 birdzhang), neither resolved. The netd angle
is documented only indirectly:

* ghosalmartin, 2017-01-16 — *"for whatever reason on bullhead, netmgrd has to connect to
  /dev/netd and thats provided by netd"*
* ghosalmartin, 2017-02-08 — *"my netmgrd was causing issues and wouldnt start without a
  socket created by netd"*
* erfanoabdi, 2020-06-07 — *"Dummy netd is hal for netd on gnu/linux side ... I made another
  package ... called stub netd and doing same thing as dummy netd but only returning valid
  values"*
* mal, 2022-03-07 — *"do you have dummy netd?"*

---

## Verification

Verified on hardware, 2026-08-11, on the live BSNL SIM.

droid-hal-init picks the service up and starts it:

```
droid-hal-init: Parsing file /usr/libexec/droid-hybris/system/etc/init/netd-stub.rc...
droid-hal-init: starting service 'netd-hal-1-1-stub'...
```

`lshal` shows it registered, with `netmgrd` (pid 1791) among its clients:

```
FM    Y android.system.net.netd@1.0::INetd/default   1610   1791 1215
DC,FM Y android.system.net.netd@1.1::INetd/default   1610   1791 1215
```

`netmgrd` now walks the whole path it previously could not start:

```
QC-NETMGR-LIB: getServiceImpl(): INetd discovered
QC-NETMGR-LIB: registerLinkToDeath(): Success registerLinkToDeath!
QC-NETMGR-LIB: NetmgrNetdClientInit(): Created netd client
QC-NETMGR-LIB: registerNetwork(): createOemNetwork succeeded [packet mark : 0x0] [net id : 0] [network handle : 0x1]
```

`unable to get dsi hndl` no longer appears anywhere in the logs, and ofono gets to
`setting up data call`. The context activates with a real carrier address:

```
Active   = true
Settings = Interface rmnet_data0, Method static,
           Address 100.103.37.103, Netmask 255.255.255.240
```

connman brings it to `State = online` (gateway `100.103.37.104`, nameservers `61.1.1.1`,
`1.1.1.1`), and traffic flows:

```
ping -c 3 -I rmnet_data0 8.8.8.8
  3 packets transmitted, 3 packets received, 0% packet loss
  round-trip min/avg/max = 68.718/71.182/74.977 ms

curl --interface rmnet_data0 http://deb.debian.org/
  http_code=200 time=0.668561s dl=1876B
```

### Two things that are not bugs

* **Cellular data only comes up while WiFi is off.** connman keeps a single default route and
  prefers WiFi, so with WiFi associated it clears the context (`Clearing active context` in
  the ofono journal) moments after activation. That is ordinary connman policy, not a
  regression — the first test after the fix looked like a failure purely for this reason.
* The address is in `100.64.0.0/10`, which is carrier-grade NAT. Normal for this operator.

### Still to do

The verified fix was **hand-installed** onto the device for this test. To make it survive an
image build, `build_packages.sh -d` must be re-run so `droid-hal-karatep` picks the binary up
out of `out/target/product/karatep/system/bin/hw/`; `droid-config-karatep` already ships
`netd-stub.rc`.
