# Longse NVR/DVR for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)

A custom [Home Assistant](https://www.home-assistant.io/) integration for
NVR/DVR devices built on the **Longse** web/API platform — also sold under
rebranded/OEM names such as **Cantonk**, and commonly paired with the
**BitVision** mobile/cloud app. These devices expose a modern JSON REST-like
API over plain HTTP (not a binary protocol like XMEye/DVRIP), identifiable
by `/statics/lib/utils/common/encryption/longseSha256.js` on the device's
web panel.

> **This is a fork** of [Noneawe/ha-longse-nvr](https://github.com/Noneawe/ha-longse-nvr)
> with a session-cookie compatibility fix (the device returns a dynamically
> named `sessionID_<suffix>` cookie; the client now sends it back verbatim
> instead of renaming it — this fixes `Failed to connect to the device` on
> firmware such as `NVR-N31_BV_V2.1.3.x`). Install and update **this**
> repository, not the upstream one, to get the fix.

Supports **multiple devices at once** — each registrator is added as its
own config entry, so you can mix e.g. a DVR at home and one at a family
member's place in the same Home Assistant instance.

## Features

- **`camera`** — live view per channel (RTSP), on-demand snapshot, and a
  choice between the Main and Sub RTSP stream (see below)
- **`binary_sensor`** — motion, face/silhouette, regional intrusion, line
  crossing, loitering, and people-gathering detection, per channel — via
  polling (see caveat below; push does not work on this device family)
- **`sensor`** — firmware version, number of configured IP channels

This integration intentionally does **not** include any archive/recording
download features (browsing or exporting past footage, periodic snapshot
scripts, etc.) — it focuses on live monitoring and alerting only. A
companion CLI tools repo for archive/event-clip retrieval is planned; check
[this account's repositories](https://github.com/Noneawe) for
**longse-nvr-tools** once published.

There is currently **no PTZ support** — see [PROTOCOL_NOTES.md](PROTOCOL_NOTES.md)
for why, and open an issue if you have PTZ-capable hardware and want to help
add it.

## Installation

### Via HACS (recommended)

This integration is not (yet) in the default HACS store, but you can add
this fork as a custom repository:

1. HACS → **Integrations** → the three-dot menu (⋮, top right) →
   **Custom repositories**.
2. Repository: `https://github.com/aliuspetraska/ha-longse-nvr`, category:
   **Integration** → **Add**.
3. Install **"Longse NVR/DVR (Cantonk / BitVision)"** from the HACS list.
4. Restart Home Assistant (Settings → System → Restart — required for HA to
   pick up the new integration code).
5. Add the integration: **Settings → Devices & services → Add integration →
   search for "Longse NVR"**.
6. Enter the device's IP address, port (default `80`), username and
   password.
7. Repeat step 5 for every additional device — each one becomes its own
   entry.

Future updates (including any fixes made in this fork) are delivered
through HACS from **this** repository — update via HACS like any other
custom integration. If you previously installed the upstream
`Noneawe/ha-longse-nvr` version, remove it first so only one copy of the
`longse_nvr` domain exists.

### Manual installation

Copy `custom_components/longse_nvr/` from this repository into your Home
Assistant `config` directory, so you end up with:

```
<config>/custom_components/longse_nvr/manifest.json
```

Then restart Home Assistant and continue from step 5 above.

### Dependencies

The integration relies on the built-in `ffmpeg` Home Assistant component
(for snapshots and stream handling) and the `ffmpeg` binary itself. Both
are already present on Home Assistant OS / Supervised / the official
container image; on a Core/venv install you may need to install it
manually (e.g. `apt install ffmpeg`).

### After installation — quick check

- The `camera.*` entities should show a live preview within a few seconds
  of adding the integration, and the snapshot button should work.
- If a channel has no camera physically connected/configured, its `camera`
  entity will correctly show as `unavailable` — this is expected, not a
  bug.
- If something looks wrong: Settings → System → Logs, filter for
  `longse_nvr`.

## Stream selection (Main / Sub)

Settings → Devices & services → Longse NVR/DVR → **Configure** lets you
switch the RTSP stream used for live view and snapshots:

- **Main** (default) — full resolution, and measurably more reliable on
  this device family (see [PROTOCOL_NOTES.md](PROTOCOL_NOTES.md)).
- **Sub** (substream) — lower resolution, noticeably more prone to
  occasional corrupted/undecodable frames on this device family.

Changing this option reloads the integration automatically — no need to
remove and re-add it.

## Security notes

Unlike some other NVR/DVR protocol families, login on this platform uses a
**salted challenge-response** (SHA-256, iterated), not a bare unsalted
hash — a captured or leaked login exchange cannot be trivially replayed to
authenticate as a different session, and the password itself is never sent
in the clear during the HTTP API login used by this integration.

That said, as general best practice we still recommend creating a
**dedicated account** for this integration rather than reusing your primary
admin login, so that if Home Assistant's stored credentials are ever
exposed (e.g. a backup, a compromised HA instance), the blast radius is
limited. No specific reduced-permission group has been verified to work for
every feature of this integration yet — if you test one, please open an
issue with your findings.

See [PROTOCOL_NOTES.md](PROTOCOL_NOTES.md) for further technical detail on
the login handshake and other protocol findings.

## Contributing / issues

Bug reports, hardware reports (different Longse-family models/firmware/
rebrands) and pull requests are welcome — please open an
[issue](https://github.com/aliuspetraska/ha-longse-nvr/issues).

## License

[MIT](LICENSE)
