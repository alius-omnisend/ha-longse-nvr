"""Integracja Longse NVR/DVR (platforma web z longseSha256.js, sprzedawane
też pod marką Cantonk) dla Home Assistant."""
from __future__ import annotations

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.core import HomeAssistant

from .const import CONF_CHANNEL_COUNT, DOMAIN
from .coordinator import LongseNvrCoordinator, LongseNvrEventCoordinator

PLATFORMS: list[str] = ["camera", "sensor", "binary_sensor"]


@dataclass
class LongseNvrRuntimeData:
    """Dwa coordinatory per rejestrator: firmware/kanały (wolny poll, 60s)
    i zdarzenia (szybki poll, patrz `const.EVENT_POLL_INTERVAL_SECONDS`) —
    współdzielą jeden `LongseNvrClient`/sesję (`coordinator.client`)."""

    coordinator: LongseNvrCoordinator
    event_coordinator: LongseNvrEventCoordinator


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Ustaw config entry: jeden klient/sesja, dwa coordinatory per rejestrator."""
    data = entry.data
    coordinator = LongseNvrCoordinator(
        hass,
        entry,
        host=data[CONF_HOST],
        port=data[CONF_PORT],
        username=data[CONF_USERNAME],
        password=data[CONF_PASSWORD],
        channel_count=data[CONF_CHANNEL_COUNT],
    )
    # Rzuca ConfigEntryNotReady automatycznie, jeśli pierwszy update się nie uda
    # (np. rejestrator chwilowo offline) — HA sam ponowi próbę setupu później.
    await coordinator.async_config_entry_first_refresh()

    # Kanały do pollingu zdarzeń: te faktycznie skonfigurowane (z channel-list,
    # patrz coordinator.py) - fallback na wszystkie, gdyby lista była pusta.
    channels = sorted(coordinator.configured_channels or range(data[CONF_CHANNEL_COUNT]))
    event_coordinator = LongseNvrEventCoordinator(hass, coordinator.client, channels)
    await event_coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = LongseNvrRuntimeData(coordinator, event_coordinator)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Zmiana opcji (Main/Sub stream) — przeładuj cały entry, żeby `camera.py`
    odczytał nową wartość (stream_source() czyta ją przy starcie)."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Zdejmij config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        runtime_data: LongseNvrRuntimeData = hass.data[DOMAIN].pop(entry.entry_id)
        await runtime_data.event_coordinator.async_shutdown()
        # Zamyka klienta/sesję aiohttp (współdzieloną z event_coordinator) -
        # musi być ostatnie.
        await runtime_data.coordinator.async_shutdown()
    return unload_ok
