# Running Waydroid kills the host's camera until the next reboot

## Symptom

Sailfish's own camera works normally from boot. Start Waydroid, open the Android
camera app inside it, let that app crash — and from then on the *Sailfish*
camera shows a black viewfinder. It stays broken for the rest of the boot. A
reboot fixes it; stopping Waydroid does not.

`journalctl` shows the host's camera provider respawning every five seconds:

```
libprocessgroup: Failed to add task into cpuset because all cpus in that cpuset are offline
libprocessgroup: Failed to apply CameraServiceCapacity task profile: No space left on device
init: Service 'vendor.camera-provider-2-4' (pid 1522) exited with status 1
init: Sending signal 9 to service 'vendor.camera-provider-2-4' (pid 1522) process group...
```

## Diagnosis

`CameraServiceCapacity` is a libprocessgroup task profile; on this device it is
`JoinCgroup{Controller: cpuset, Path: camera-daemon}`. Joining a cgroup v1 cpuset
whose `cpuset.cpus` is **empty** returns `ENOSPC`. libprocessgroup treats a
*missing* cgroup (`ENOENT`) as tolerable and carries on, but treats `ENOSPC` as
fatal, so the provider exits 1 and `init` restarts it forever.

The distinction is the whole bug: **absent is fine, present-but-empty is fatal.**

On a clean boot the cpuset hierarchy is completely flat — measured, with Waydroid
disabled:

```
$ ls /sys/fs/cgroup/cpuset/
cgroup.clone_children  cpuset.cpus  cpuset.mems  ...        # files only, no subdirectories
$ ps aux | grep provider@2.4
 1497 camerase /vendor/bin/hw/android.hardware.camera.provider@2.4-service    # healthy
```

`camera-daemon` does not exist, the join fails `ENOENT`, and the provider runs
unconfined. That is why the camera works until Waydroid is started.

Start a Waydroid *session* (not just `waydroid-container.service` — the LXC
container only starts with a session, and Android's init only runs then) and the
same listing becomes:

```
background/           cpus=[]     mems=[]
camera-daemon/        cpus=[]     mems=[]
foreground/           cpus=[]     mems=[]
restricted/           cpus=[]     mems=[]
system-background/    cpus=[]     mems=[]
top-app/              cpus=[]     mems=[]
lxc.monitor.waydroid/ cpus=[0-7]  mems=[0]
lxc.payload.waydroid/ cpus=[0-7]  mems=[0]
```

Six empty Android cpusets have appeared **on the host hierarchy**, created by the
container.

### Why the container can reach the host's cgroups

Waydroid's `lxc.mount.auto = cgroup:ro` and there is **no cgroup namespace** in
`/var/lib/waydroid/lxc/waydroid/config`. The read-only bind at `/sys/fs/cgroup`
only protects that path. cgroup v1 keeps one hierarchy per controller set for the
whole kernel, so when the container's Android init runs its own

```
mount cpuset none /dev/cpuset nodev noexec nosuid          # system/core/rootdir/init.rc
```

it gets a fresh, writable mount of *the same tree the host is using*. The
subsequent `mkdir /dev/cpuset/camera-daemon` in
`device/lenovo/karate-common/rootdir/etc/init.qcom.power.rc` therefore creates
`/sys/fs/cgroup/cpuset/camera-daemon` on the host. New cgroup v1 cpuset children
start with empty `cpus`/`mems`, and the container's follow-up `write .../cpus 0-7`
does not stick.

Note that `init.qcom.power.rc` targets `/dev/cpuset`, which **does not exist on
the host at all** — systemd owns the hierarchy and mounts cpuset at
`/sys/fs/cgroup/cpuset`. So none of the device tree's cpuset tuning has ever
applied on this port. That is a separate, pre-existing, harmless-until-now
condition: with the directories absent, every join was a tolerated `ENOENT`.
Waydroid is what turns it into a live grenade.

### Why it is a delayed fault

The host's camera provider joined `camera-daemon` at boot, when it did not exist.
It keeps running happily while the container poisons the hierarchy underneath it.
The failure only fires when the provider **restarts** — which is exactly what the
Android camera app crashing inside Waydroid causes. Reproduced deliberately:

```
$ kill -9 1497                       # the healthy boot-time provider
libprocessgroup: Failed to apply CameraServiceCapacity task profile: No space left on device
init: Service 'vendor.camera-provider-2-4' (pid 1522) exited with status 1
```

## A measurement trap: `kill -9` is not a valid reproduction

The obvious way to test a fix is to kill the provider and watch it come back.
**It never comes back on this port, with or without Waydroid.** Control run on a
clean boot, flat cpuset hierarchy, Waydroid never started:

```
$ kill -9 1549
$ ps aux | grep provider@2.4
 1549 camerase [provider@2.4-se]            # zombie
$ journalctl -b | grep camera-provider
                                            # nothing. init never even tried.
```

`SIGKILL` leaves an unreaped zombie parented to droid-hal-init and init does not
restart the service at all. Any conclusion drawn from a `kill -9` test about the
camera stack is therefore an artefact of the test.

The real fault path is a *graceful* restart through init — which is what the
user's original incident showed, the provider respawning every five seconds and
failing on the task profile each time. Use `setprop ctl.restart
vendor.camera-provider-2-4` to exercise it.

## Fix: `cgroup.clone_children` on the cpuset root

cgroup v1 has a per-cgroup flag that makes a newly created child inherit its
parent's `cpus`/`mems`, and the flag itself propagates to children. Setting it
once on the cpuset root means everything the container creates comes up
populated instead of empty — including cgroups nobody has enumerated:

```sh
echo 1 > /sys/fs/cgroup/cpuset/cgroup.clone_children
```

Verified on device. Before, a fresh child was born empty; with the flag set:

```
$ mkdir /sys/fs/cgroup/cpuset/zz-probe
cpus=[0-7] mems=[0] clone=[1]
```

and after starting a Waydroid session, every cgroup the container created came up
correct rather than empty:

```
background/ camera-daemon/ foreground/ restricted/ system-background/ top-app/
                                              all cpus=[0-7] mems=[0]
```

Inheriting `0-7` is deliberate, rather than the device tree's tuned values
(`camera-daemon 4-7`, `background 4-6`). An all-CPUs cpuset is semantically the
same as the "no cpuset exists" state this port has always run with, so the
failing join is restored without changing host scheduling behaviour.

Shipped as `android-cpuset-inherit.service` in droid-config, ordered
`Before=droid-hal-init.service` so the flag is set before any Android cgroup
exists.

## Why not contain the container instead

Populating cpusets defends the host; it does not stop the container reaching
into the host's hierarchy. The clean fix would be a cgroup namespace
(`lxc.namespace.clone = cgroup`), so the container's `mount -t cgroup` sees a
namespaced root and its `mkdir`s land inside `lxc.payload.waydroid/`.

**That is not available here.** `CLONE_NEWCGROUP` was added in Linux 4.6 and this
kernel is 3.18. LXC 6.0.3 on the device supports the option; the kernel cannot
honour it. So host-side defence is the only option on this port, and the
`clone_children` flag is the cheapest form of it.

## Still open

- The container's own `logd` runs alongside the host's — harmless so far, but
  noted.
- Inside Waydroid the camera app reports no cameras; the container's camera HAL
  bridge is a separate problem from this one.

## See also

- `waydroid-devpts.md` — the kernel config work that got the container starting.
- `camera-dies-on-record.md` — an unrelated, earlier camera fault.
