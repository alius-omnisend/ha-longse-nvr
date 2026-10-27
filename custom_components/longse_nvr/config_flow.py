"""Config flow dla integracji Longse NVR.

Jeden rejestrator = jeden config entry. `unique_id` opiera się o `serialNum`
zwracany przez `/api/network/p2p` (NIE o IP — może się zmienić przy DHCP,
i NIE o `serialNo` z `/api/system/device-info`, które jest bezużytecznym
literałem "Network Video Recorder" — patrz PROTOCOL_NOTES.md).
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector

from .client import (
    CannotConnect,
    InvalidAuth,
    LongseNvrClient,
    NvrError,
    UnsupportedDevice,
)
from .const import (
    CONF_CHANNEL_COUNT,
    CONF_SERIAL,
    CONF_STREAM_TYPE,
    DEFAULT_PORT,
    DEFAULT_STREAM_TYPE,
    DEFAULT_USERNAME,
    DOMAIN,
    STREAM_TYPE_MAIN,
    STREAM_TYPE_SUB,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
        vol.Required(CONF_USERNAME, default=DEFAULT_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Obsługa config flow dla longse_nvr."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Pierwszy (jedyny na razie) krok: host/port/user/hasło."""
        errors: dict[str, str] = {}

        if user_input is not None:
            client = LongseNvrClient(
                user_input[CONF_HOST],
                user_input[CONF_PORT],
                user_input[CONF_USERNAME],
                user_input[CONF_PASSWORD],
            )
            try:
                await client.login()
                info = await client.get_summary()
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except UnsupportedDevice:
                errors["base"] = "unsupported_device"
            except (CannotConnect, NvrError):
                errors["base"] = "cannot_connect"
            except Exception:  # nieznany błąd, logujemy pełny traceback
                _LOGGER.exception("Nieoczekiwany błąd podczas konfiguracji longse_nvr")
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(info.serial)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"{info.device_type} ({user_input[CONF_HOST]})",
                    data={
                        **user_input,
                        CONF_SERIAL: info.serial,
                        CONF_CHANNEL_COUNT: info.channel_count,
                    },
                )
            finally:
                await client.close()

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> OptionsFlow:
        return OptionsFlow(config_entry)


class OptionsFlow(config_entries.OptionsFlow):
    """Wybór strumienia Main/Sub dla `camera.py` po dodaniu integracji."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        current = self._config_entry.options.get(CONF_STREAM_TYPE, DEFAULT_STREAM_TYPE)
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_STREAM_TYPE, default=current
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[STREAM_TYPE_MAIN, STREAM_TYPE_SUB],
                        translation_key=CONF_STREAM_TYPE,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
