# Factory reset from Settings does nothing

Status: **root-caused, deliberately not fixed.** See [Why no fix yet](#why-no-fix-yet).

## Symptom

Settings → device reset completes and reboots, but nothing is erased. The
device lock code and the enrolled fingerprint both still work afterwards.

## Root cause

Sailfish's factory reset is a **btrfs snapshot rollback**, not a wipe.
`/usr/sbin/clear-device`:

```sh
ROOT=$(grep "/dev/mmc.* / " /proc/mounts | cut -d " " -f1)
mount -o subvolid=0 $ROOT /mnt/ || { echo "Can't mount /mnt" && exit 1; }
if [ -d /mnt/factory-\@ ] && [ -d /mnt/factory-\@home ] &&
   [ 0 -lt "$(grep -c "FACTORY_CLEAN_CAPABLE_IMAGE_V2" /mnt/factory-\@/sbin/preinit)" ] &&
   [ 0 -lt "$(grep -c "subvol=\@home" /mnt/factory-\@/etc/fstab)" ]; then
    umount /mnt
else
    echo "Flashed recovery image is too old and does not support phone clearing. Can't do it"
    umount /mnt
    exit 1
fi
```

It needs a btrfs root mountable at `subvolid=0`, carrying `factory-@` and
`factory-@home` subvolumes, with a `FACTORY_CLEAN_CAPABLE_IMAGE_V2` preinit.
The reset then restores those subvolumes.

On this port the root filesystem is **ext4 on `/dev/mmcblk0p54`** — the rootfs is
a stowaway under `/data/.stowaways/sailfishos`. There are no subvolumes and no
factory snapshot, so the mechanism cannot run.

`mal`, `#sailfishos-porters`, 2025-03-14:

> `<mal>` depends whether there is suitable partition for factory reset
> `<Mister_Magister>` but ports do factory reset without such partition?

## The misleading second symptom

The journal fills with:

```
encsfa-fpd[1223]: Device /dev/sailfish/home does not exist or access denied.
```

`encsfa-fpd` (symlinked `encpartition`) is the device lock plugin. On an
official device it clears by destroying the LUKS volume at
`/dev/sailfish/home`. Here `encrypt_home=false` and no such device exists.

This is easy to mistake for the cause. It is not — it is the same missing
infrastructure seen from the device lock side. Patching the plugin would
achieve nothing, because there is still nothing to roll back to.

`devicelock_settings.conf` already reports the true capability:

```
nemo\devicelock\encrypt_home=false
nemo\devicelock\supported_device_reset_options=Reboot
```

What *does* happen on reset: the plugin writes the flag files
`/usr/share/lipstick/devicelock/.clear-device` and `.clear-device-enable-reboot`,
then the device reboots. On an official device `preinit` acts on the flag; here
nothing consumes it.

## What survives, and where

| Credential | Location | Survives a reset? | Survives a reflash? |
|---|---|---|---|
| Device lock code | `/usr/share/lipstick/devicelock/.devicelock.enc` (rootfs) | yes — nothing is wiped | no |
| Fingerprint templates | `/data/system/users/100000/fpdata/user.db` | yes | **yes** — see [stale-fingerprint-templates.md](stale-fingerprint-templates.md) |
| Finger name map | `/usr/share/lipstick/devicelock/sailfish-fpd/<uid>/` (rootfs) | yes | no |

The fingerprint case is a separate defect with its own fix. Everything else in
this document is one problem: no reset implementation exists for this port.

## Why no fix yet

A reset here cannot be restorative — there is no factory snapshot to restore.
It would have to be constructive: delete `/home/defaultuser`, the device lock
state and the fingerprint templates, then let the first-boot wizard run again.

That is a hack, and it is the wrong shape of solution. The intended direction
for this port is a full port with home encryption on LUKS, at which point the
stock mechanism has something real to operate on and works as designed. Writing
a bespoke destructive wipe now would be thrown away then, while carrying the
risk that any bug in it destroys installs.

Until then the supported way to clear the device is a **reflash**, which
replaces the rootfs. With the fingerprint fix in place that also clears the
templates, so a reflash is a complete wipe.

## Notes for whoever picks this up

- `mmcblk0p54` is userdata and holds both the stowaway rootfs and Android's
  `/data`. A recovery "format data" clears everything including the OS.
- LineageOS is still installed alongside; its own fingerprint templates live at
  `/data/system/users/0/fpdata`. Anything that wipes must never glob
  `/data/system/users/*`.
- The flag files are the natural trigger for a future implementation; they are
  written before the reboot and nothing clears them.
