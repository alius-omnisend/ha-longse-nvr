"""Platforma camera dla Longse NVR — jedna encja per kanał, strumień przez RTSP.

Format URL RTSP potwierdzony WIZUALNIE na żywym sprzęcie (nie tylko "200 OK",
patrz PROTOCOL_NOTES.md — wiele generycznych ścieżek zwraca 200 OK, ale zawsze
ten sam kanał 0):
    rtsp://<user>:<pass>@<host>:554/XY
gdzie X = numer kanału liczony OD 0, Y = typ strumienia (0=main, 1=sub).
To jest oficjalnie udokumentowany format Longse, INNY niż Hikvision/Dahua/
ONVIF/XMEye-style wzorce.

Dostępność encji: `coordinator.last_update_success` (czy w ogóle da się
mówić z API urządzenia) ORAZ `coordinator.configured_channels` (z
`channel/channel-list`, odblokowanego dodatkowym "reauth" — patrz
`client._secure_channel_params`; DUŻO bardziej wiarygodne niż
`channel/online-status`, które zgłasza wszystkie kanały online
niezależnie od realnego stanu, patrz PROTOCOL_NOTES.md). UWAGA: to nadal
tylko "kanał ma przypisaną kamerę w konfiguracji", NIE "kamera właśnie
dostarcza obraz" — realny brak sygnału mimo `enable:true` (zweryfikowane
na kanale 8/index 7 tego urządzenia) i tak ujawni się dopiero jako błąd
samego strumienia RTSP/ffmpeg, tak jak we wbudowanej platformie
`generic`.

WAŻNE — transport RTSP musi być wymuszony na TCP (zweryfikowane empirycznie
2026-07-03 w kontenerze HA): domyślny UDP kończy się błędem ffmpeg
"Operation not permitted" (środowisko Docker/sieć nie pozwala na surowe
gniazda UDP potrzebne RTSP-owi w trybie domyślnym), 0 klatek, zdjęcie/stream
milczą. Dwa ODDZIELNE mechanizmy, bo dwie ścieżki kodu w HA różnie
konsumują RTSP:
  - snapshot (`async_camera_image`, haffmpeg/CLI ffmpeg): `rtsp_transport`
    MUSI być częścią pojedynczego stringa `input_source` PRZED `-i` (haffmpeg
    `_put_input()` robi `shlex.split()` i jeśli wynik ma >1 token, wkleja go
    do argv w całości - to jedyny sposób wymusić opcję input-only; przekazanie
    przez `extra_cmd` NIE zadziała, bo `extra_cmd` ląduje w argv PO `-i`,
    czyli jako opcja output/global, a `-rtsp_transport` tam nie działa).
  - live view (komponent `stream`, PyAV `av.open()`): `stream_source()` MUSI
    zwracać CZYSTY URL (PyAV nie parsuje CLI-owych flag ffmpeg) — transport
    wymuszamy przez `self.stream_options[CONF_RTSP_TRANSPORT]`, które
    `Camera.create_stream()` przekazuje dalej jako `pyav_options`.
"""
from __future__ import annotations

from urllib.parse import quote

from homeassistant.components import ffmpeg
from homeassistant.components.camera import Camera
from homeassistant.components.stream.const import CONF_RTSP_TRANSPORT
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_SERIAL,
    CONF_STREAM_TYPE,
    DEFAULT_STREAM_TYPE,
    DOMAIN,
    RTSP_PORT,
    STREAM_TYPE_TO_RTSP_PARAM,
)
from .coordinator import LongseNvrCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Utwórz encje kamer, jedną per kanał rejestratora."""
    coordinator: LongseNvrCoordinator = hass.data[DOMAIN][entry.entry_id].coordinator
    async_add_entities(
        LongseNvrCamera(coordinator, entry, channel)
        for channel in range(1, coordinator.channel_count + 1)
    )


class LongseNvrCamera(CoordinatorEntity[LongseNvrCoordinator], Camera):
    """Kamera per kanał (RTSP: stream_source dla live view, ffmpeg dla snapshotu)."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: LongseNvrCoordinator, entry: ConfigEntry, channel: int
    ) -> None:
        super().__init__(coordinator)
        Camera.__init__(self)
        self._entry = entry
        self._channel = channel  # 1-based (konwencja dokumentacji Longse)
        serial = entry.data[CONF_SERIAL]
        self._attr_unique_id = f"{serial}_channel_{channel}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, serial)},
            name=entry.title,
        )
        # Wymuszenie TCP dla live view przez komponent `stream` — patrz
        # docstring modułu. Snapshot (async_camera_image) wymusza to
        # niezależnie, innym mechanizmem (prefiks w input_source).
        self.stream_options[CONF_RTSP_TRANSPORT] = "tcp"

    @property
    def available(self) -> bool:
        if not super().available:
            return False
        configured = self.coordinator.configured_channels
        if configured is None:
            # Jeszcze nie pobrane (pierwszy poll w toku) - nie blokuj encji.
            return True
        return (self._channel - 1) in configured

    @property
    def name(self) -> str:
        """Nazwa ustawiona przez użytkownika w panelu web
        (`channel/priview-channel-name`), jeśli niepusta — inaczej "Kanał N".
        Pobierana raz przez coordinator (patrz coordinator.py), rzadko się
        zmienia."""
        custom_name = self.coordinator.channel_names.get(self._channel - 1)
        return custom_name or f"Kanał {self._channel}"

    async def stream_source(self) -> str | None:
        return self._rtsp_url()

    def _rtsp_url(self) -> str:
        user = quote(self.coordinator.username, safe="")
        password = quote(self.coordinator.password, safe="")
        stream_type = self._entry.options.get(CONF_STREAM_TYPE, DEFAULT_STREAM_TYPE)
        stream_param = STREAM_TYPE_TO_RTSP_PARAM[stream_type]
        channel_0based = self._channel - 1
        return (
            f"rtsp://{user}:{password}@{self.coordinator.host}:{RTSP_PORT}/"
            f"{channel_0based}{stream_param}"
        )

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Zrzut pojedynczej klatki przez ffmpeg z tego samego RTSP co live view
        (ten sam wzorzec co wbudowana platforma `generic`).

        UWAGA: `-rtsp_transport tcp` musi być PRZED `-i` w jednym stringu
        `input_source` (haffmpeg wkleja go do argv w całości, gdy ma >1
        token) — nie działa jako `extra_cmd` (ląduje po `-i`). Patrz
        docstring modułu."""
        if not self.available:
            return None
        input_source = f"-rtsp_transport tcp -i {self._rtsp_url()}"
        return await ffmpeg.async_get_image(
            self.hass, input_source, width=width, height=height
        )
