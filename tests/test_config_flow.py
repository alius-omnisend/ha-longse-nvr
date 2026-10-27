"""Config-flow tests — require Home Assistant + pytest-homeassistant-custom-
component (`pip install -r requirements_test.txt`). Skipped without them.

All credentials used here are fake fixtures.
"""

from __future__ import annotations

from typing import ClassVar

import pytest

pytest.importorskip("homeassistant")

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.data_entry_flow import FlowResultType

from custom_components.longse_nvr import config_flow
from custom_components.longse_nvr.client import (
    ApiError,
    CannotConnect,
    DeviceInfo,
    InvalidAuth,
    NvrError,
    SessionExpired,
    UnsupportedDevice,
)
from custom_components.longse_nvr.const import (
    CONF_CHANNEL_COUNT,
    CONF_SERIAL,
    DOMAIN,
)

USER_INPUT = {
    CONF_HOST: "nvr.example.local",
    CONF_PORT: 80,
    CONF_USERNAME: "admin",
    CONF_PASSWORD: "test-password-123",
}

FAKE_INFO = DeviceInfo(
    serial="TESTSERIAL123",
    device_type="NVR",
    software_version="V2.1.3-test",
    channel_count=8,
)


class _FakeClient:
    """Stands in for LongseNvrClient — records ctor args, can be made to
    fail at `login()` or `get_summary()` via class attributes."""

    login_exc: ClassVar[Exception | None] = None
    summary_exc: ClassVar[Exception | None] = None
    instances: ClassVar[list[_FakeClient]] = []

    def __init__(self, host, port, username, password) -> None:
        self.host, self.port = host, port
        self.username, self.password = username, password
        self.closed = False
        type(self).instances.append(self)

    async def login(self) -> None:
        if self.login_exc:
            raise self.login_exc

    async def get_summary(self) -> DeviceInfo:
        if self.summary_exc:
            raise self.summary_exc
        return FAKE_INFO

    async def close(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def patch_client(monkeypatch):
    _FakeClient.login_exc = None
    _FakeClient.summary_exc = None
    _FakeClient.instances = []
    monkeypatch.setattr(config_flow, "LongseNvrClient", _FakeClient)
    yield


async def test_flow_success_creates_entry(hass):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "NVR (nvr.example.local)"
    assert result["data"][CONF_SERIAL] == "TESTSERIAL123"
    assert result["data"][CONF_CHANNEL_COUNT] == 8
    # configured host/port/credentials actually reached the client
    inst = _FakeClient.instances[0]
    assert (inst.host, inst.port, inst.username) == (
        USER_INPUT[CONF_HOST],
        USER_INPUT[CONF_PORT],
        USER_INPUT[CONF_USERNAME],
    )
    assert inst.closed


@pytest.mark.parametrize(
    ("exc", "error_key"),
    [
        (InvalidAuth("bad creds"), "invalid_auth"),
        (CannotConnect("unreachable"), "cannot_connect"),
        (UnsupportedDevice("not this platform"), "unsupported_device"),
        (ApiError("/api/x", 5009), "cannot_connect"),
        (SessionExpired("401"), "cannot_connect"),
        (NvrError("no serial"), "cannot_connect"),
        (RuntimeError("boom"), "unknown"),
    ],
)
async def test_flow_error_mapping(hass, exc, error_key):
    _FakeClient.login_exc = exc

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": error_key}


async def test_flow_summary_error_also_maps(hass):
    """Login succeeds but a later call fails (e.g. authed 401 loop)."""
    _FakeClient.summary_exc = SessionExpired("401 after login")

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_flow_duplicate_serial_aborts(hass):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**USER_INPUT, CONF_HOST: "nvr2.example.local"}
    )
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"
