"""Platforma sensor — wersja firmware i liczba skonfigurowanych kanałów IP.

Celowo BEZ sensora uptime: `/api/system/device-info` nie zwraca żadnego pola
czasu działania (sprawdzone empirycznie), a zgadywanie nieistniejącego pola
byłoby dokładnie tym błędem, przed którym ostrzega CLAUDE.md (nie ufaj
niesprawdzonym polom). Do dodania później, jeśli znajdzie się faktyczny
endpoint z uptime (patrz PROTOCOL_NOTES.md, otwarte pytania).

Wszystkie encje tu to `CoordinatorEntity` — dane z tego samego pollingu
firmware/typu urządzenia co `coordinator.py`, bez dodatkowych połączeń.
"""
from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_SERIAL, DOMAIN
from .coordinator import LongseNvrCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Utwórz encje sensor: firmware, liczba kanałów IP."""
    coordinator: LongseNvrCoordinator = hass.data[DOMAIN][entry.entry_id].coordinator
    async_add_entities(
        [
            LongseNvrFirmwareSensor(coordinator, entry),
            LongseNvrChannelCountSensor(coordinator, entry),
        ]
    )


class _LongseNvrSensorBase(CoordinatorEntity[LongseNvrCoordinator], SensorEntity):
    """Wspólny device_info/unique_id dla sensorów tego rejestratora."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: LongseNvrCoordinator, entry: ConfigEntry, key: str) -> None:
        super().__init__(coordinator)
        serial = entry.data[CONF_SERIAL]
        self._attr_unique_id = f"{serial}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, serial)}, name=entry.title
        )


class LongseNvrFirmwareSensor(_LongseNvrSensorBase):
    """Wersja firmware (`softwareVersion` z `/api/system/device-info`)."""

    _attr_name = "Wersja firmware"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: LongseNvrCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "firmware")

    @property
    def native_value(self) -> str | None:
        if not self.coordinator.data:
            return None
        return self.coordinator.data.software_version


class LongseNvrChannelCountSensor(_LongseNvrSensorBase):
    """Liczba kanałów IP skonfigurowanych na rejestratorze (`ipChannelNum`/
    `channelTotalNum` z `/api/system/device-capabilities`, odczytane raz przy
    dodaniu integracji — patrz config_flow.py). UWAGA: to liczba KANAŁÓW
    LICENCJONOWANYCH przez urządzenie, NIE liczba faktycznie podłączonych
    kamer — tej drugiej nie da się obecnie wiarygodnie ustalić (patrz
    ostrzeżenie o `channel/online-status` w PROTOCOL_NOTES.md)."""

    _attr_name = "Kanały IP (skonfigurowane)"
    _attr_icon = "mdi:cctv"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: LongseNvrCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "channel_count")

    @property
    def native_value(self) -> int:
        return self.coordinator.channel_count
