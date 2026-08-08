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

## Status: partially fixed

Writing valid values into the poisoned cpusets removes the `ENOSPC`:

```sh
for d in /sys/fs/cgroup/cpuset/*/; do
    case "$d" in *lxc.*) continue;; esac
    echo 0   > "$d/cpuset.mems"
    echo 0-7 > "$d/cpuset.cpus"
done
```

`0-7` rather than the device tree's `camera-daemon 4-7` / `background 4-6` on
purpose: an all-CPUs cpuset is semantically identical to the "no cpuset exists"
state that works today, so this restores the join without changing host
scheduling behaviour.

That is confirmed to stop the libprocessgroup error — but the provider **still**
exits 1 afterwards, now without a cpuset complaint, and `init` eventually gives
up retrying. Both `provider@2.4-service` and `mm-qcamera-daemon` are left as
unreaped zombies parented to droid-hal-init:

```
1497 camerase [provider@2.4-se]     State: Z (zombie)   PPid: 1159
1629 camera   [mm-qcamera-daem]     State: Z (zombie)   PPid: 1159
```

with `init` looping on `Failed to kill process cgroup uid 1047 pid N in 208ms,
1 processes remain`. So the cpuset is the *first* fault, not the only one — the
camera stack cannot be brought back within a boot once Waydroid has run. Only a
reboot restores it.

The remaining failure is not yet root-caused. Candidates, untested:

- the container's own `logd` (a second instance, seen alongside the host's) and
  its Android services competing for `/dev/socket/*` or hwservicemanager names;
- state left in the camera hardware by the container's camera HAL bridge;
- droid-hal-init wedged in `KillProcessGroup`, unable to reap, so it will not
  cleanly restart the service pair.

## What a real fix has to do

Populating cpusets after the fact is a workaround for a containment failure. The
container should not be able to write the host's cgroup hierarchy at all. The
proper fix is to give the container its own cgroup namespace
(`lxc.namespace.clone = cgroup`, LXC ≥ 3.0 — the device has 6.0.3), so its
`mount -t cgroup` sees a namespaced root and its `mkdir`s land in
`lxc.payload.waydroid/` where they belong. That needs testing: Android's init may
not tolerate a namespaced cgroup root, and waydroid's own cgroup handling assumes
the current layout.

## See also

- `waydroid-devpts.md` — the kernel config work that got the container starting.
- `camera-dies-on-record.md` — an unrelated, earlier camera fault.
