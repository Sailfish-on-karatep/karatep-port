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
