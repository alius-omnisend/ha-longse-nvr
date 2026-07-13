"""Platforma binary_sensor — ruch i Smart Event (linia/strefa/tłum/twarz),
przez POLLING `picture/picture-list` co `EVENT_POLL_INTERVAL_SECONDS`.

Push nie działa na tym urządzeniu (WebSocket na porcie 80 to czysty kanał
wideo, porty 5000/6000 to zamknięty protokół natywnej wtyczki — patrz
PROTOCOL_NOTES.md). `picture/picture-list` zwraca zdjęcia zapisane przy
zdarzeniach, ZE znacznikiem czasu — to jedyny sposób na wykrycie realnych
zdarzeń na tym urządzeniu.

Encja per (skonfigurowany kanał, typ zdarzenia) — tworzona STATYCZNIE przy
starcie dla wszystkich kombinacji (w przeciwieństwie do poprzedniego
projektu XMeye, gdzie push wymuszał tworzenie dynamiczne "na pierwsze
zdarzenie" — tu, skoro i tak pollujemy, znamy z góry pełen zestaw kanałów
z `channel/channel-list`). `is_on` = zdjęcie danego typu na danym kanale
pojawiło się w ciągu ostatnich `EVENT_OFF_DELAY_SECONDS` (auto-wyłączenie
po ciszy — urządzenie daje dyskretne zdjęcia, nie ciągły stan start/stop).

UWAGA: samo `enable:true` w konfiguracji danego typu zdarzenia (np.
Loitering/People Gathering/Regional Intrusion) NIE gwarantuje, że
faktycznie coś wykryje — jeśli strefa/linia detekcji nie została
narysowana w panelu web (współrzędne `0,0,0,0`), zdarzenie nigdy się nie
pojawi mimo włączonego przełącznika (zweryfikowane na kanale 2, patrz
PROTOCOL_NOTES.md). Encje i tak są tworzone dla wszystkich typów na wszystkich
skonfigurowanych kanałach — po prostu zostaną trwale "off", jeśli dany typ
nie jest realnie aktywny.
"""
from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_SERIAL,
    DOMAIN,
    EVENT_AREA_DETECT,
    EVENT_CROSS_DETECT,
    EVENT_FACE_DETECT,
    EVENT_MOTION,
    EVENT_PEOPLE_GATHERING_DETECT,
    EVENT_PEOPLE_STAY_DETECT,
    EVENT_TYPE_KEYS,
    EVENT_TYPE_LABELS,
)
from .coordinator import LongseNvrEventCoordinator

EVENT_DEVICE_CLASS: dict[int, BinarySensorDeviceClass] = {
    EVENT_MOTION: BinarySensorDeviceClass.MOTION,
    EVENT_FACE_DETECT: BinarySensorDeviceClass.OCCUPANCY,
    EVENT_AREA_DETECT: BinarySensorDeviceClass.OCCUPANCY,
    EVENT_CROSS_DETECT: BinarySensorDeviceClass.OCCUPANCY,
    EVENT_PEOPLE_STAY_DETECT: BinarySensorDeviceClass.OCCUPANCY,
    EVENT_PEOPLE_GATHERING_DETECT: BinarySensorDeviceClass.OCCUPANCY,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Utwórz encje: jedna per (skonfigurowany kanał, typ zdarzenia)."""
    runtime_data = hass.data[DOMAIN][entry.entry_id]
    event_coordinator = runtime_data.event_coordinator
    async_add_entities(
        LongseNvrEventBinarySensor(event_coordinator, entry, channel, event_bit)
        for channel in event_coordinator.channels
        for event_bit in EVENT_TYPE_KEYS
    )


class LongseNvrEventBinarySensor(
    CoordinatorEntity[LongseNvrEventCoordinator], BinarySensorEntity
):
    """Jedna encja per (kanał 0-based, typ zdarzenia)."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: LongseNvrEventCoordinator,
        entry: ConfigEntry,
        channel: int,
        event_bit: int,
    ) -> None:
        super().__init__(coordinator)
        self._channel = channel  # 0-based (konwencja channel-list/picture-list)
        self._event_bit = event_bit
        serial = entry.data[CONF_SERIAL]
        key = EVENT_TYPE_KEYS[event_bit]
        self._attr_unique_id = f"{serial}_channel_{channel + 1}_{key}"
        self._attr_name = f"Kanał {channel + 1} {EVENT_TYPE_LABELS[event_bit]}"
        self._attr_device_class = EVENT_DEVICE_CLASS.get(event_bit)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, serial)}, name=entry.title
        )

    @property
    def is_on(self) -> bool:
        return self.coordinator.is_active(self._channel, self._event_bit)
