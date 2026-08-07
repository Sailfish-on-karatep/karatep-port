# Fingerprints from a previous install survive a reflash

## Symptom

After a full reflash — new rootfs, new `hybris-boot.img`, first-boot wizard run
again — the device still unlocks with the **previous owner's** fingerprint.

`fpd` reports it with no name:

```
$ dbus-send --system --print-reply --dest=org.sailfishos.fingerprint1 \
      /org/sailfishos/fingerprint1 org.sailfishos.fingerprint1.GetAll
   array [
      string "Unknown 1445599621"
   ]
```

## Root cause

The template store and the name map live on opposite sides of the install
boundary.

| | Path | Wiped by a reflash? |
|---|---|---|
| Templates | `/data/system/users/100000/fpdata/user.db` | **no** |
| Finger name map | `/var/lib/sailfish-fpd-community/100000/fingerprints.db` | yes |

(Do not confuse the map with `/usr/share/lipstick/devicelock/sailfish-fpd/`,
which belongs to Jolla's `encsfa-fpd` device lock plugin, not to this daemon.)

`100000` is Sailfish's `defaultuser` uid. The installer's `updater-unpack.sh`
does `rm -rf /data/.stowaways/sailfishos`, which replaces the rootfs — the map
is inside it, the templates are not.

Confirmed on the device after this reflash: `fingerprints.db` had been created
fresh during that first boot, while `user.db` still held the previous
installation's template.

`loadFingers()` then finds templates the map does not know about, and adopts
them (`src/fpdcommunity.cpp:212-217`):

```cpp
for (uint32_t k: enumeratedFingers) {
    if (!mapped.contains(k)) {
        qWarning() << "Unknown fingerprint found, adding to the list:" << k;
        m_fingerMap[k] = QStringLiteral("Unknown %1").arg(k);
    }
}
```

That is where `Unknown 1445599621` comes from, and why the old finger unlocks
the new install.

## Why the templates cannot simply be moved into the rootfs

`src/androidfp.cpp:75` hardcodes an Android path:

```cpp
return QStringLiteral("/data/system/users/%1/fpdata").arg(uid);   // API >= 28
return QStringLiteral("/data/vendor_de/%1/fpdata").arg(uid);      // older
```

This is deliberate. `rinigus` tried a Sailfish-side path and it worked, then
found out why it is wrong the next day — `#sailfishos-porters`, 2020-06-02 and
2020-06-03:

> `<rinigus>` so I could use `/var/lib/sailfishos-fpd-community/100000/fpdata/user.db` to enroll, identify and so on

> `<rinigus>` when grepping aosp sources for fpdata, I found `/data/system/users/[0-9]+/fpdata(/.*)` and `/data/vendor_de/[0-9]+/fpdata(/.*)` in `system/sepolicy/private/file_contexts`

Those are the only paths AOSP labels for fingerprint data. Relocating the store
works only where SELinux is permissive — which this port happens to be
(`/sys/fs/selinux/enforce` is `0`) — and breaks anywhere enforcing. The store
path stays where it is.

## Fix

`sailfish-fpd-community` is the only component that owns both halves, so the
invariant belongs there:

> Templates must not outlive the rootfs that enrolled them.

When the finger map **file does not exist**, this rootfs has never enrolled
anything, so any template the HAL reports predates the install and belongs to no
user here. Remove them instead of adopting them.

File-absent is the right test, and it is unambiguous:

- *File absent* happens only on a rootfs that has never enrolled.
- *File present but missing an entry* is a desync within one install, and keeps
  the existing "adopt as `Unknown N`" behaviour.

## Why not fix it in the installer

Deleting `fpdata` from `updater-unpack.sh` was considered and rejected:

- It only covers the **zip** path. The HADK manual install and
  `scripts/flash.sh` extract the rootfs directly and never run it.
- The installer would have to hardcode uid `100000`.
- A loose glob over `/data/system/users/*/fpdata` would delete **LineageOS's**
  fingerprints at `/data/system/users/0/fpdata`. LineageOS is still installed on
  this device — the port runs on top of its vendor partition.

The daemon-side fix covers every install path and needs no uid guessing.

## Upstream

Submitted to `sailfishos-open/sailfish-fpd-community` as three separate PRs,
in merge order. Each is a self-contained fix; none rewrites an earlier one,
though #42 and #43 build on the API #41 introduces.

| PR | |
|---|---|
| [#41](https://github.com/sailfishos-open/sailfish-fpd-community/pull/41) | Only trust an enumeration that came back from the HAL |
| [#42](https://github.com/sailfishos-open/sailfish-fpd-community/pull/42) | Confirm a removal before dropping the name |
| [#43](https://github.com/sailfishos-open/sailfish-fpd-community/pull/43) | Do not adopt templates left by a previous installation |

#41 also fixes a fault of its own: because an empty store could never be
confirmed on a HAL that stays silent when it holds nothing, removing the *last*
enrolled fingerprint always reported a failure although the template was gone.

## Scope

This does **not** fix Settings → device reset, which erases nothing at all for
an unrelated reason — see
[factory-reset-does-nothing.md](factory-reset-does-nothing.md). It does mean a
reflash is now a complete wipe.
