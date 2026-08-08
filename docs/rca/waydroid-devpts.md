# The Waydroid container never starts: no private devpts instance

## Symptom

`waydroid init` completes, `waydroid-container.service` comes up, both images are
in place — and starting a session does nothing. The Waydroid app closes itself,
`waydroid status` says `Session: STOPPED`, and `lxc-info` agrees the container
never ran.

`/var/lib/waydroid/waydroid.log`:

```
lxc-start: waydroid: ../src/lxc/conf.c: lxc_setup_devpts_child: 1654 Failed to bind mount "/dev/pts/ptmx" to "/dev/ptmx"
lxc-start: waydroid: ../src/lxc/conf.c: lxc_setup_devpts_child: 1664 No such file or directory - Failed to create symlink from "/dev/ptmx" to "/dev/pts/ptmx"
lxc-start: waydroid: ../src/lxc/conf.c: lxc_setup: 3969 Failed to prepare new devpts instance
lxc-start: waydroid: ../src/lxc/start.c: do_start: 1273 Failed to setup container "waydroid"
lxc-start: waydroid: ../src/lxc/start.c: __lxc_start: 2114 Failed to spawn container "waydroid"
```

then, ten seconds later, from `container_manager.py`:

```
  File "/usr/lib/waydroid/tools/helpers/lxc.py", line 395, in wait_for_running
    raise OSError("container failed to start")
OSError: container failed to start
```

## Diagnosis

```
$ CONFIG=/run/kconfig lxc-checkconfig
Multiple /dev/pts instances: missing
```

(`lxc-checkconfig` shells out to `zgrep`, which the device does not have. Run
`zcat /proc/config.gz > /run/kconfig` first and point `CONFIG=` at that, or every
line reports "missing".)

`/dev/pts/ptmx` genuinely does not exist on the device — the file LXC is trying
to bind mount is simply absent, which is why the error is `No such file or
directory` rather than a permission problem.

## Root cause

`CONFIG_DEVPTS_MULTIPLE_INSTANCES` was not set.

LXC gives each container its own devpts by mounting one with `newinstance` and
`ptmxmode`, then linking `/dev/ptmx` to the instance's own `ptmx` node. On 3.18
all of that lives behind one `#ifdef`, in `fs/devpts/inode.c`:

| Line | What the `#ifdef` hides |
|---|---|
| 119 | `ptmxmode=`, `newinstance` and `max=` in the mount-option match table |
| 211 | the same three in `parse_mount_options()` — anything else returns `-EINVAL` |
| 238 | **`mknod_ptmx()`** — the only code that ever creates `/dev/pts/ptmx` |

So without it the node cannot exist and the mount options are rejected. The
symbol is `bool`, `default n`, `depends on UNIX98_PTYS` (`drivers/tty/Kconfig:123`).
It became unconditional in 4.7, which is why no port on a newer base runs into
this.

Enabling it changes nothing for the host: without `newinstance` every devpts
mount still shares the single initial instance, exactly as before. It also
restores `lxc.pty.max`, which is the same `max=` mount option.

## Prior art

None. The `#sailfishos-porters` archive has zero hits for
`Failed to prepare new devpts instance`, `Multiple /dev/pts`, or `waydroid
container failed to start`. The only `DEVPTS_MULTIPLE_INSTANCES` mentions are
Thaodan (2021-11-02) noting the option was removed in 4.6, and vrutkovs
(2014-07-19) in an unrelated context.

## Fix

`karatep_defconfig`, commit `7b25d38ec43e`:

```
CONFIG_DEVPTS_MULTIPLE_INSTANCES=y
```

## Also found, same investigation

`waydroid.log` a few lines earlier:

```
Mounting overlays failed. The feature has been disabled.
```

`images.py:167` catches the failure, writes `mount_overlays = False` into
`waydroid.cfg` and carries on — so `/system` and `/vendor` are read-only and the
overlay directories under `/var/lib/waydroid` are dead weight. `CONFIG_OVERLAY_FS`
was off.

Turning it on is necessary but **not sufficient**: `mount.py:160` joins the lower
layers with `:`, and this kernel's overlayfs has a single `config.lowerdir` and
calls `ovl_mount_dir()` once (`fs/overlayfs/super.c:695`). Stacking several lower
layers arrived in 4.0. `lowerdir=a:b` is therefore looked up as one literal path
and always fails here.

Both halves of the fix are in place: `CONFIG_OVERLAY_FS=y` in the same defconfig
commit, and patch `0003-overlayfs-single-lowerdir.patch` in
[`Sailfish-on-karatep/waydroid`](https://github.com/Sailfish-on-karatep/waydroid),
which folds the extra lower layers into the upper layer when the kernel is older
than 4.0. Precedence survives the fold — upper outranks lower just as an earlier
`lowerdir` outranks a later one, and the merge never overwrites what the upper
layer already holds.

Note that `waydroid.cfg` keeps the `mount_overlays = False` that was written the
first time it failed. Set it back to `True` by hand after installing the fixed
kernel; nothing re-enables it on its own.
