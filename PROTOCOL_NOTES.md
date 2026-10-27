# Longse NVR/DVR protocol notes

Technical notes on the Longse web/API platform (also sold under brands such
as **Cantonk**, and used with the **BitVision** mobile/cloud app) referenced
from code comments throughout this integration. These are general findings
about the protocol/firmware family, confirmed against real hardware during
development — not specific to any single device (no IPs, serial numbers or
credentials here; those live in a private development journal, not in this
repo).

## Transport: plain HTTP/JSON, not a binary protocol

Unlike some other NVR/DVR families (e.g. XMEye/DVRIP), this platform exposes
a modern, fully JSON REST-like API over plain HTTP (default port 80, also
available over HTTPS):

```
POST /api/<endpoint>
Content-Type: application/json
Api-Version: v4.0.0
{"action": "get"|"set"|"delete"|"test", "data": {...}}
```

(The device's own web client sends `application/x-www-form-urlencoded` as
the jQuery-default content type with the same raw JSON-text body, plus
`Api-Version`/`Timestamp` headers on every request; the firmware accepts
`application/json` equally — verified on live hardware.)

After login, subsequent requests carry the session cookie **verbatim**, as
returned in the login response's `data.cookie` — a full `name=value` pair
whose *name* is dynamic (`sessionID_<suffix>`, e.g. how the device's own
`login.js` handles it: `a.cookie.split("=")` → `setCookie(name, value)`).
Reconstructing a fixed `Cookie: sessionID=<token>` header discards the
suffix and is rejected with `HTTP 401` on firmware that issues suffixed
names. This lets the integration talk to the device natively over
`aiohttp`, with no executor jobs needed for the control channel (only
RTSP/ffmpeg needs one).

## Login: salted challenge-response SHA-256, not a bare hash

Reverse-engineered from the device's own web panel JavaScript. Summarized:

1. `POST /api/session/login-capabilities` with the username returns a
   `sessionID`, `salt`, `challenge`, and an iteration count.
2. `g = sha256(username + salt + base64(datetime) + password)`
3. `f = sha256(raw_bytes(g) + challenge)`
4. `f = sha256(raw_bytes(f))`, repeated `iterations` times.
5. `POST /api/session/login` with `f` as `password`, plus the same
   `sessionID` and `datetime`, returns a new session cookie.

The hashing operates on raw bytes (mirroring the original JavaScript's
`charCodeAt`-based string handling), not UTF-8 — the Python port encodes
with `latin-1` to match byte-for-byte.

This is meaningfully better than a bare unsalted hash: a network observer
or another logged-in account cannot simply replay a captured value to log
in as a different user, since salt/challenge/datetime change per attempt.

## `channel/online-status` is unreliable — use `channel/channel-list` instead

`POST /api/channel/online-status` reports every channel as online
regardless of whether a camera is actually connected — confirmed by visual,
frame-level comparison against channels known (by physical inspection) to
have no camera attached. Do not trust this endpoint for entity availability.

`channel/channel-list` gives a much more accurate `enable`/`ip`/`port` per
channel, but requires an extra "reauth" payload beyond the normal session
cookie — a second challenge-response derived from a different capability
endpoint (`/api/security/capability`, AES-flavored, password not part of the
computation). See `client.py::_secure_channel_params` for the verified,
working derivation. Even with `channel-list`, `enable: true` only means "a
camera is assigned to this channel in configuration" — not "this channel is
currently delivering video"; that can only be confirmed by an actual RTSP
connection attempt.

## RTSP accepts almost any path — verify channels by frame content, not HTTP status

The RTSP server on this device family responds `200 OK` with a working video
stream for a wide range of made-up/generic paths (Hikvision-style,
Dahua-style, ONVIF-style, etc.) — it silently falls back to channel 0/main
instead of returning an error for an unrecognized path. Confirmed by
comparing decoded frames (perceptual hash) across supposedly-different
channel URLs: many of them returned the identical scene.

**The verified, correct format is:**

```
rtsp://<user>:<pass>@<host>:554/XY
```

where `X` = channel number, **0-based**, and `Y` = stream type (`0` = main/
high-res, `1` = sub). Any integration or script targeting this device family
must verify channel numbering visually (e.g. comparing frames against a
known physical scene per channel) rather than trusting a `200 OK` response.

## Push doesn't work — events are detected by polling saved event photos

No working push/webhook mechanism was found for motion or Smart Event
alerts on this device family (a local WebSocket exists but only carries raw
video, not event notifications). The only reliable way found to detect real
events is polling `POST /api/picture/picture-list`, which returns photos the
device saves at the moment of a motion/Smart Event trigger, each with a
`channel`, a `pictureType` bitmask (motion / face / intrusion / line-
crossing / loitering / people-gathering), and a `pictureTime` (Unix epoch in
**microseconds** — a different unit than the epoch-in-seconds used in the
request's `startTime`/`endTime`, easy to mix up).

Because this is discrete "photo was saved" data rather than a continuous
on/off state, `binary_sensor` entities consider an event type "active" for
a short window after the most recent matching photo, then fall back to off.

## Sub stream is meaningfully less reliable than Main

Corrupted/undecodable frames (`Error parsing NAL unit`, consistent with
damaged H.265 SPS/PPS at the start of capture) occur noticeably more often
on the Sub stream than on Main on this device family — roughly an order of
magnitude more frequent in repeated testing. `Main` is therefore the
integration's default stream choice; `Sub` remains available as an option
(e.g. for bandwidth-constrained setups) but is more likely to occasionally
produce a bad snapshot or a dropped live-view frame.

## Session lifetime is short — expect periodic re-authentication

Sessions on this device family can expire well before any obviously
"long" polling interval — confirmed by an `HTTP 401` response on a
previously-valid session cookie after a few minutes of otherwise normal
polling. Unlike a JSON-level `code != 0` error, this is a bare HTTP status,
so a client must check for **both** error shapes and treat either as
"session invalid, log in again" — checking only one silently wedges the
client in a permanently failed state (each subsequent request repeats the
same 401 without ever refreshing credentials). `client.py` retries exactly
once with a fresh login on either error shape.

## No PTZ support in this integration

This integration does not implement any PTZ (pan/tilt/zoom) service. It was
out of scope for the hardware used during development (fixed cameras only)
and was not reverse-engineered. If your device has a physically motorized
channel and you'd like to help add this, please open an issue — a
protocol-level starting point would need to be identified first (unlike the
`OPMonitor`/RTSP/event-photo surfaces documented above, no PTZ endpoint has
been investigated on this platform yet).
