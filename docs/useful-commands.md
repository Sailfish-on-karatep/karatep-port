# Sailfish OS Useful Commands

## Rebooting from Sailfish OS Environment
If you are inside the Sailfish OS environment (either via SSH, Telnet, or the local terminal application) and need to reboot to Fastboot or Recovery, you can use the underlying Android `init` daemon to trigger the reboot gracefully.

Switch to root first:
```bash
devel-su
```

### Reboot to Fastboot (Bootloader)
```bash
setprop sys.powerctl reboot,bootloader
```
*(Alternative: `/system/bin/reboot bootloader`)*

### Reboot to Recovery (TWRP / hybris-recovery)
```bash
setprop sys.powerctl reboot,recovery
```
*(Alternative: `/system/bin/reboot recovery`)*

## Restarting the camera stack

If the camera app stops responding, or `CameraService` starts rejecting connections with
`rejected (too many other clients connecting)`, restart the two services that hold it — as root:

```bash
setprop ctl.restart qcamerasvr    # mm-qcamera-daemon
setprop ctl.restart minimedia     # minimediaservice; CameraService lives inside it
```

Takes a couple of seconds. **A reboot is not needed**, despite what earlier notes said. This is
also the way out of the state a `SIGKILL`ed `gst-launch` probe leaves behind, since the client
never disconnects from `CameraService`.

## Dumping the camera HAL's parameters

Every capability the camera HAL advertises — resolutions, flash, focus, white balance, the
exposure range — with nothing to install:

```bash
GST_DEBUG=droidcamsrc:9 <pipeline> 2>&1 | grep "param .* = "
```

`:9` is required; the dump is at `GST_LOG` level. `droid-camres` is the "official" tool for this
and is broken against current gst-droid — see
[rca/camera-dies-on-record.md](rca/camera-dies-on-record.md).
