# Every GPS fix was a cold start: assistance was gated behind ConnMan "online"

## Symptom

The receiver works, but takes minutes to fix where a phone sitting next to it
takes seconds. Indoors it often never fixes at all.

Not a hardware fault: the same device, indoors on a desk, reaches a 7-satellite
fix with HDOP 1.4 once it has had long enough, tracking GPS, GLONASS and BeiDou
at C/N₀ 20–30.

## Diagnosis

The difference between seconds and minutes is **assistance**. A receiver that
already holds current ephemeris, an accurate clock and a coarse position only
has to *correlate*, which works at low signal. Without those it must *demodulate*
the navigation message off each satellite at 50 bit/s — that needs a much higher
C/N₀ and tens of seconds of clean signal per satellite, and indoors it usually
cannot be done at all.

`geoclue-providers-hybris` supplies all three legs, and on karatep none of them
ran. A full acquisition produced **1044 lines** of provider debug containing not
one mention of xtra, ntp, agps or a data connection.

The three legs and what gates them:

| Leg | What it gives | Gate |
|---|---|---|
| XTRA / LTO | predicted orbits — removes the need to demodulate ephemeris | forced inject, needs plain HTTP |
| NTP | accurate time | forced inject, needs plain HTTP |
| SUPL | ephemeris + coarse position from the network | `startDataConnection()` requires a **cellular** technology to be connected |

SUPL is out on a device with no data modem, and that is expected. XTRA and NTP
are ordinary HTTP GETs and should have worked over any network. They did not,
because of the condition guarding them:

```cpp
if (m_networkManager->globalState() == NetworkManager::OnlineState) {
    if (m_useForcedXtraInject) gnssXtraDownloadRequest();
    if (m_useForcedNtpInject)  injectUtcTime();
}
```

ConnMan reaches `OnlineState` only once its captive-portal probe succeeds:

```
$ connmanctl state
  State = ready                                   <- not "online"
  Ipv4StatusUrl = http://ipv4.jolla.com/return_204
```

The device routes traffic perfectly well and sits in `ready` indefinitely
because that probe does not return 204. Any firewalled LAN, tethered link, or
network where the check host is blocked produces the same thing — and the
failure is silent, since nothing logs "not fetching XTRA".

## Fix

`>= NetworkManager::ReadyState` instead of `== OnlineState`, at both call sites
(session start and `stateChanged`), in
[`Sailfish-on-karatep/geoclue-providers-hybris`](https://github.com/Sailfish-on-karatep/geoclue-providers-hybris).

Both are plain HTTP GETs whose failure is harmless and retried, so making them
wait on a captive-portal verdict buys nothing and costs every fix.

## Verified on hardware

With the fix, both fire immediately at session start:

```
Forcing XTRA data injection / Forcing NTP injection
xtra download requested
XTRA servers (QUrl("https://gllto.glpals.com/7day/v5/latest/lto2.dat"), ...)
Send NTP request. Servers: ("0.sailfishos.pool.ntp.org", ...)
```

The download and the injection into the GNSS engine were then proved end to end
by serving a real `lto2.dat` from the build host over the USB link, since the
device had no DNS at the time:

```
XTRA servers (QUrl("http://192.168.2.4:8000/lto2.dat"))
XTRA download finished
injected  181423  bytes of xtra data        <- exactly the file's size
```

So the whole path works: request → download → inject. Against the real servers
it additionally needs working DNS, which the device did not have during testing
— its default route went out the Waydroid veth on a link-local address. That is
an environment artefact, not part of this bug.

**Not yet measured:** the actual improvement in time-to-first-fix with XTRA
loaded. The mechanism is proven; the payoff is not quantified.

## See also

- [`waydroid-gps-bluetooth.md`](../waydroid-gps-bluetooth.md) — how position
  reaches the Waydroid container, which is a separate concern from acquiring it.
