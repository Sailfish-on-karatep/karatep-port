# Port tooling

The scripts that drive this port's builds, images and flashing, plus the
environment files the three nested shells rely on.

These live in the workspace at `/opencloud/bin` and `/opencloud`, which is **not
under version control** — everything here existed only there until it was
rescued into this repo. Treat this directory as the canonical copy: change it
here, then copy out to the workspace.

## Which shell each script belongs to

Getting this wrong is the most common failure in this workspace, because the
same file has a different absolute path in each environment. See the port
README for the full explanation.

| Script | Run from |
|---|---|
| `build-hal.sh`, `build-hybris-boot.sh`, `build-fpservice.sh`, `build-recoveryimage.sh`, `build-los-recovery.sh`, `waydroid/build-waydroid-hwc.sh` | **HABUILD SDK** |
| `build-hal-packages.sh`, `build-image.sh`, `build-geoclue.sh`, `build-geoclue-inplace.sh`, `build-fpd-rpm.sh`, `build-extqti.sh`, `waydroid/build-waydroid-rpm.sh` | **PLATFORM SDK** |
| `sfossdk`, `habuild`, `mb2`, `sdk-assistant`, `ircgrep.sh`, `sign-installer-zip.sh`, `stage-boot-img.sh`, `install-clean-updater.sh`, `flash.sh`, `make-bootsplash.py`, `devshell.py`, `hidl-from-apk.py`, `patch-mbn-ims.sh` | **HOST** |

`sfossdk`, `habuild`, `mb2` and `sdk-assistant` are thin wrappers whose only job
is to force `PLATFORM_SDK_ROOT=/opencloud/SailfishOS`, so nothing resolves into
`$HOME`.

## Environment

`env/hadk.env` is this port's equivalent of the HADK's `$HOME/.hadk.env`,
relocated because `~` is off-limits here. It is **shell-aware**: it detects which
of the three environments it is running in and exports the right form of
`ANDROID_ROOT`, so `source /opencloud/hadk.env` is correct everywhere. That
matters because scratchbox2 does not resolve the `/opencloud` symlink, and an
`ANDROID_ROOT` in the symlinked form makes every `mb2` package build fail with
`Unable to open $ANDROID_ROOT/.mb2/spec`.

`env/mersdkubu.profile` is the `$HOME/.mersdkubu.profile` equivalent. Because it
is not in `$HOME` it is **not** auto-sourced by `ubu-chroot`; source `hadk.env`
explicitly instead.

## Not kept here

- `repo` — Google's own tool, fetched rather than carried.
- `ubu-chroot` — the workspace copy is a **broken artifact**: 14 bytes
  containing the literal text `404: Not Found` from a failed download. The real
  one lives inside the Platform SDK; use `habuild`.

## Notes

`sign-installer-zip.sh` signs with AOSP's **testkey**, which ships in the
Android tree and is public. No private key material is kept in this repo.

`update-binary` is the shell-fallback recovery installer, which unzips to
`/data` first to work around the device's RAM limits — see
`docs/rca/broken-update-binary.md`.

`hidl-from-apk.py` answers "what is this vendor HIDL interface actually shaped
like on *this* device" by reading the Java classes hidl-gen generated into an
Android APK — enum values, struct fields, method signatures, and the transaction
codes, which hidl-gen assigns by declaration order and which the DEX layout does
not preserve. It needs no apktool, no baksmali and no network. Written for the
VoLTE work against `/system/system_ext/priv-app/ims/ims.apk`; see
`docs/rca/volte-registration-change-is-test-mode.md`. Always sanity-check a
`codes` run against transaction codes your existing client already hardcodes —
if it reproduces those, the rest can be trusted.

`patch-mbn-ims.sh` turns IMS on in a Qualcomm MBN modem configuration. On
karatep every SIM that is not Jio falls through to the generic `row.mbn`
(`ROW_Generic_3GPP`), which sets `IMS_enable = 0` and pins voice to the
circuit-switched domain — so the modem never publishes its IMS QMI services and
VoLTE cannot work at any layer above. The script flips those two NV items and
repacks, using `sbaresearch/mbn-mcfg-tools`, which it fetches into
`/opencloud/work/telephony` on first run. These files carry three SHA-256 hashes
and no signature, and this modem checks only the hashes, so a repacked config is
accepted; the tool round-trips karatep's own configs byte for byte. The script
re-extracts what it wrote and asserts the values rather than trusting the edit.
See `docs/rca/volte-registration-change-is-test-mode.md`.
