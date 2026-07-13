"""DataUpdateCoordinator dla Longse NVR — polling firmware/typu urządzenia.

Jeden coordinator = jeden rejestrator = jeden `LongseNvrClient` (własna,
dedykowana sesja aiohttp — patrz client.py, `force_close=True`), nie
współdzielony między config entries.

Zakres pollingu jest celowo minimalny (firmware/typ urządzenia) —
`channel/online-status` jest ZNANY JAKO NIEWIARYGODNY (zweryfikowane na
żywo: zgłasza wszystkie kanały jako online, mimo że część nie ma
podłączonej kamery — patrz PROTOCOL_NOTES.md), więc NIE jest używany.
Zamiast tego `channel/channel-list` (odblokowany dodatkowym mechanizmem
"reauth", patrz `client._secure_channel_params`) daje dużo bardziej
wiarygodne `enable`/`ip` per kanał — pobierane RAZ (rzadko się zmienia,
tak jak nazwy kanałów) i używane przez `camera.py` do dostępności encji.
UWAGA: `enable:true` w `channel-list` oznacza "kanał ma przypisaną kamerę
w konfiguracji", NIE "kamera faktycznie dostarcza obraz teraz" — realny
brak sygnału i tak ujawni się dopiero przy próbie streamu RTSP/ffmpeg
(dokładnie jak we wbudowanej platformie `generic`).
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .client import CannotConnect, InvalidAuth, LongseNvrClient, NvrError
from .const import (
    EVENT_OFF_DELAY_SECONDS,
    EVENT_POLL_INTERVAL_SECONDS,
    EVENT_TYPE_KEYS,
)

_LOGGER = logging.getLogger(__name__)

# Firmware/typ urządzenia zmieniają się rzadko (aktualizacja/reboot) - nie ma
# potrzeby pollować częściej niż raz na minutę.
UPDATE_INTERVAL = timedelta(seconds=60)


@dataclass
class LongseNvrData:
    """Dane pollowane co cykl."""

    software_version: str | None
    device_type: str


class LongseNvrCoordinator(DataUpdateCoordinator[LongseNvrData]):
    """Koordynator dla jednego rejestratora Longse."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        host: str,
        port: int,
        username: str,
        password: str,
        channel_count: int,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"longse_nvr ({host})",
            update_interval=UPDATE_INTERVAL,
        )
        self.entry = entry
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.channel_count = channel_count
        self.client = LongseNvrClient(host, port, username, password)
        # Nazwy kanałów i przypisanie kamer zmieniają się rzadko - pobierz raz,
        # nie przy każdym pollu. Puste/None, dopóki nie odświeżone.
        self.channel_names: dict[int, str] = {}
        self.configured_channels: set[int] | None = None

    async def _async_update_data(self) -> LongseNvrData:
        try:
            info = await self.client.get_device_info()
            if not self.channel_names:
                self.channel_names = await self.client.get_channel_names()
            if self.configured_channels is None:
                channel_list = await self.client.get_channel_list()
                self.configured_channels = {
                    int(ch["channel"]) for ch in channel_list if ch.get("enable")
                }
        except InvalidAuth as err:
            # Złe hasło/konto usunięte nie naprawi się samo przez ponawianie co
            # UPDATE_INTERVAL — ConfigEntryAuthFailed każe HA poprosić usera o
            # reauth zamiast bić się bez końca o zamknięte drzwi.
            raise ConfigEntryAuthFailed(
                f"Logowanie do rejestratora {self.host} odrzucone — sprawdź dane dostępowe"
            ) from err
        except (CannotConnect, NvrError) as err:
            raise UpdateFailed(f"Błąd komunikacji z rejestratorem {self.host}: {err}") from err

        return LongseNvrData(
            software_version=info.get("softwareVersion"),
            device_type=str(info.get("deviceType") or "NVR"),
        )

    async def async_shutdown(self) -> None:
        await self.client.close()
        await super().async_shutdown()


EVENT_POLL_INTERVAL = timedelta(seconds=EVENT_POLL_INTERVAL_SECONDS)
# Margines na jitter pollingu - zapytanie o zdarzenia zawsze sięga trochę
# dalej wstecz niż sam próg wygaszenia, żeby nie zgubić zdarzenia przez
# spóźniony/nierówny cykl pollingu.
_LOOKBACK_MARGIN_SECONDS = EVENT_POLL_INTERVAL_SECONDS * 2


class LongseNvrEventCoordinator(DataUpdateCoordinator[dict[tuple[int, int], float]]):
    """Polling zdarzeń (ruch/Smart Event) przez `picture/picture-list` —
    JEDYNY znaleziony sposób na wykrycie realnych zdarzeń na tym
    urządzeniu (push nie działa, patrz PROTOCOL_NOTES.md). Osobny coordinator
    (szybszy interwał niż `LongseNvrCoordinator`) współdzieli ten sam
    `LongseNvrClient`/sesję — nie tworzy drugiego połączenia.

    Dane: `{(channel_0based, event_bit): unix_timestamp_ostatniego_zdjęcia}`.
    `binary_sensor.py` uznaje zdarzenie za aktywne, dopóki
    `now - ostatni_timestamp < EVENT_OFF_DELAY_SECONDS` (auto-wyłączenie po
    okresie ciszy — urządzenie zwraca dyskretne zdjęcia, nie stan
    ciągły/start-stop, więc to najbliższy odpowiednik "motion detected")."""

    def __init__(
        self, hass: HomeAssistant, client: LongseNvrClient, channels: list[int]
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name="longse_nvr events",
            update_interval=EVENT_POLL_INTERVAL,
        )
        self.client = client
        self.channels = channels
        self._last_seen: dict[tuple[int, int], float] = {}

    async def _async_update_data(self) -> dict[tuple[int, int], float]:
        if not self.channels:
            return self._last_seen
        since = int(time.time()) - EVENT_OFF_DELAY_SECONDS - _LOOKBACK_MARGIN_SECONDS
        try:
            items = await self.client.get_recent_events(self.channels, since)
        except (CannotConnect, NvrError) as err:
            raise UpdateFailed(f"Błąd pobierania zdarzeń: {err}") from err

        for item in items:
            channel = int(item["channel"])
            picture_type = int(item["pictureType"])
            timestamp = int(item["pictureTime"]) / 1_000_000
            # `pictureType` bywa sumą kilku rozłącznych bitów naraz (nie
            # zaobserwowane empirycznie na tym urządzeniu, ale bitflagi z
            # definicji na to pozwalają) - rozbij na poszczególne typy.
            for event_bit in EVENT_TYPE_KEYS:
                if picture_type & event_bit:
                    key = (channel, event_bit)
                    if timestamp > self._last_seen.get(key, 0):
                        self._last_seen[key] = timestamp
        return dict(self._last_seen)

    def is_active(self, channel: int, event_bit: int) -> bool:
        """`channel` 0-based. Aktywne, jeśli ostatnie zdjęcie tego typu na
        tym kanale jest młodsze niż `EVENT_OFF_DELAY_SECONDS`."""
        last = self._last_seen.get((channel, event_bit))
        if last is None:
            return False
        return (time.time() - last) < EVENT_OFF_DELAY_SECONDS

    async def async_shutdown(self) -> None:
        # Klienta (i jego sesję aiohttp) zamyka `LongseNvrCoordinator` -
        # ten coordinator go tylko współdzieli, nie jest właścicielem.
        await super().async_shutdown()
