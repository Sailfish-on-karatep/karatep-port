#!/usr/bin/env python3
"""
Inject taps through a virtual uinput touchscreen.

Used to make the wltouch.py xdg-vs-wl_shell experiment repeatable instead of
depending on a human tapping inside a timing window. Run as root; /dev/uinput is
uhid:uhid 0660 on this device.

Usage:  inject_touch.py [delay_before_first_tap] [n_taps] [x] [y]
"""
import fcntl
import os
import struct
import sys
import time

UINPUT = "/dev/uinput"

# _IOW('U', n, int) == 0x40045500 | n ; _IO('U', n) == 0x5500 | n
UI_DEV_CREATE = 0x5501
UI_DEV_DESTROY = 0x5502
UI_SET_EVBIT = 0x40045564    # 100
UI_SET_KEYBIT = 0x40045565   # 101
UI_SET_ABSBIT = 0x40045567   # 103
UI_SET_PROPBIT = 0x4004556E  # 110

EV_SYN, EV_KEY, EV_ABS = 0x00, 0x01, 0x03
SYN_REPORT = 0
BTN_TOUCH = 0x14A
ABS_X, ABS_Y = 0x00, 0x01
ABS_MT_SLOT = 0x2F
ABS_MT_POSITION_X = 0x35
ABS_MT_POSITION_Y = 0x36
ABS_MT_TRACKING_ID = 0x39
INPUT_PROP_DIRECT = 0x01

ABS_CNT = 64
MAXX, MAXY = 1080, 1920


def emit(fd, etype, code, value):
    # struct input_event: timeval(2 * long) + u16 type + u16 code + s32 value
    os.write(fd, struct.pack("@llHHi", 0, 0, etype, code, value))


def syn(fd):
    emit(fd, EV_SYN, SYN_REPORT, 0)


def main():
    delay = float(sys.argv[1]) if len(sys.argv) > 1 else 3.0
    taps = int(sys.argv[2]) if len(sys.argv) > 2 else 4
    x = int(sys.argv[3]) if len(sys.argv) > 3 else MAXX // 2
    y = int(sys.argv[4]) if len(sys.argv) > 4 else MAXY // 2

    fd = os.open(UINPUT, os.O_WRONLY | os.O_NONBLOCK)

    for ev in (EV_SYN, EV_KEY, EV_ABS):
        fcntl.ioctl(fd, UI_SET_EVBIT, ev)
    fcntl.ioctl(fd, UI_SET_KEYBIT, BTN_TOUCH)
    fcntl.ioctl(fd, UI_SET_PROPBIT, INPUT_PROP_DIRECT)
    for ab in (ABS_X, ABS_Y, ABS_MT_SLOT, ABS_MT_POSITION_X,
               ABS_MT_POSITION_Y, ABS_MT_TRACKING_ID):
        fcntl.ioctl(fd, UI_SET_ABSBIT, ab)

    absmax = [0] * ABS_CNT
    absmin = [0] * ABS_CNT
    for ax in (ABS_X, ABS_MT_POSITION_X):
        absmax[ax] = MAXX
    for ax in (ABS_Y, ABS_MT_POSITION_Y):
        absmax[ax] = MAXY
    absmax[ABS_MT_SLOT] = 9
    absmax[ABS_MT_TRACKING_ID] = 65535

    dev = (b"wltouch-virtual".ljust(80, b"\0")
           + struct.pack("<HHHH", 0x03, 0x1234, 0x5678, 1)   # bus USB, ids
           + struct.pack("<I", 0)                            # ff_effects_max
           + struct.pack("<%di" % ABS_CNT, *absmax)
           + struct.pack("<%di" % ABS_CNT, *absmin)
           + struct.pack("<%di" % ABS_CNT, *([0] * ABS_CNT))  # absfuzz
           + struct.pack("<%di" % ABS_CNT, *([0] * ABS_CNT)))  # absflat
    os.write(fd, dev)
    fcntl.ioctl(fd, UI_DEV_CREATE)
    print("virtual touchscreen created; settling for udev/libinput hotplug")
    sys.stdout.flush()

    time.sleep(delay)

    for i in range(taps):
        emit(fd, EV_ABS, ABS_MT_SLOT, 0)
        emit(fd, EV_ABS, ABS_MT_TRACKING_ID, 100 + i)
        emit(fd, EV_ABS, ABS_MT_POSITION_X, x)
        emit(fd, EV_ABS, ABS_MT_POSITION_Y, y)
        emit(fd, EV_KEY, BTN_TOUCH, 1)
        emit(fd, EV_ABS, ABS_X, x)
        emit(fd, EV_ABS, ABS_Y, y)
        syn(fd)
        time.sleep(0.08)

        emit(fd, EV_ABS, ABS_MT_SLOT, 0)
        emit(fd, EV_ABS, ABS_MT_TRACKING_ID, -1)
        emit(fd, EV_KEY, BTN_TOUCH, 0)
        syn(fd)
        print("tap %d at %d,%d" % (i + 1, x, y))
        sys.stdout.flush()
        time.sleep(0.7)

    time.sleep(1)
    fcntl.ioctl(fd, UI_DEV_DESTROY)
    os.close(fd)
    print("virtual touchscreen destroyed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
