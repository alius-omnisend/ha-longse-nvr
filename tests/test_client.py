"""Tests for `longse_nvr.client` — fully mocked HTTP, no real NVR needed.

All fixtures use fake credentials/cookies — nothing here is a real secret.
"""

from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace

import aiohttp
import longse_nvr.client as client_mod
import pytest
from longse_nvr.client import (
    ApiError,
    CannotConnect,
    DeviceInfo,
    InvalidAuth,
    LongseNvrClient,
    SessionExpired,
    UnsupportedDevice,
)

pytestmark = pytest.mark.asyncio

HOST, PORT, USERNAME, PASSWORD = "nvr.example.local", 80, "admin", "test-password-123"

# Fixture matching the real device flow: dynamic cookie name, never a real token.
DYNAMIC_COOKIE = "sessionID_9f3bd2e1=fake-session-token"
CAPS_BODY = {
    "code": 0,
    "data": {
        "encryptionType": ["sha256-1"],
        "sessionID": "caps-session-id",
        "param": {
            "challenge": "cc" * 16,
            "salt": "aa" * 16,
            "iterations": 100,
            "enableIteration": True,
        },
    },
}
LOGIN_OK = {"code": 0, "data": {"cookie": DYNAMIC_COOKIE}}
DEVICE_INFO_OK = {
    "code": 0,
    "data": {"deviceType": "NVR", "softwareVersion": "V2.1.3-test"},
}


class FakeResponse:
    """Minimal stand-in for `aiohttp.ClientResponse` (async CM + json())."""

    def __init__(self, status: int = 200, body=None, json_exc: Exception | None = None):
        self.status = status
        self._body = body
        self._json_exc = json_exc

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc) -> bool:
        return False

    async def json(self, content_type=None):
        if self._json_exc is not None:
            raise self._json_exc
        return self._body


class FakeSession:
    """Records requests and returns queued FakeResponses (or raises queued
    exceptions from `post()` itself, e.g. connect errors/timeouts)."""

    def __init__(self) -> None:
        self.requests: list[SimpleNamespace] = []
        self._queue: list = []

    def queue(self, *items) -> None:
        self._queue.extend(items)

    def post(self, url, *, json=None, headers=None, timeout=None, **kw):
        self.requests.append(
            SimpleNamespace(url=url, payload=json, headers=dict(headers or {}))
        )
        item = self._queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def close(self) -> None:
        pass

    def bodies(self) -> list[dict]:
        return [r.payload for r in self.requests]

    def headers(self) -> list[dict]:
        return [r.headers for r in self.requests]


@pytest.fixture
def session(monkeypatch) -> FakeSession:
    fake = FakeSession()
    monkeypatch.setattr(client_mod.aiohttp, "ClientSession", lambda *a, **kw: fake)
    return fake


@pytest.fixture
def frozen_time(monkeypatch):
    """Freeze `datetime.now()` inside the client for deterministic hashes."""

    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return (
                cls(2026, 1, 2, 3, 4, 5, tzinfo=tz) if tz else cls(2026, 1, 2, 3, 4, 5)
            )

    monkeypatch.setattr(client_mod, "datetime", _Frozen)


def make_client() -> LongseNvrClient:
    return LongseNvrClient(HOST, PORT, USERNAME, PASSWORD)


def queue_login(sess: FakeSession, cookie: str = DYNAMIC_COOKIE) -> None:
    sess.queue(
        FakeResponse(200, CAPS_BODY),
        FakeResponse(200, {"code": 0, "data": {"cookie": cookie}}),
    )


# --- request shape ---------------------------------------------------------


async def test_login_capabilities_request_shape(session, frozen_time):
    session.queue(FakeResponse(200, CAPS_BODY), FakeResponse(200, LOGIN_OK))
    await make_client().login()

    caps_req = session.requests[0]
    assert caps_req.url == f"http://{HOST}:{PORT}/api/session/login-capabilities"
    assert caps_req.payload == {"action": "get", "data": {"username": USERNAME}}
    assert caps_req.headers["Api-Version"] == "v4.0.0"
    assert "Timestamp" in caps_req.headers
    # pre-login requests must not carry a session cookie
    assert "Cookie" not in caps_req.headers


async def test_login_request_body_sha256_1(session, frozen_time):
    session.queue(
        FakeResponse(
            200,
            {
                "code": 0,
                "data": {
                    "encryptionType": ["sha256-1"],
                    "sessionID": "caps-session-id",
                    "param": {
                        "challenge": "bb" * 16,
                        "salt": "aa" * 16,
                        "iterations": 100,
                        "enableIteration": True,
                    },
                },
            },
        ),
        FakeResponse(200, LOGIN_OK),
    )
    await make_client().login()

    login_req = session.requests[1]
    assert login_req.url.endswith("/api/session/login")
    data = login_req.payload["data"]
    assert login_req.payload["action"] == "set"
    assert data["username"] == USERNAME
    assert data["loginEncryptionType"] == "sha256-1"
    assert data["sessionID"] == "caps-session-id"
    assert data["datetime"] == "2026-01-02T03:04:05"
    # Golden vector (independent reimplementation of the JS algorithm):
    assert data["password"] == (
        "a74fe62e41e671b144078063858188750b0812fa6c9881d5a6b6be740d4e7e80"
    )
    # the plaintext password must never appear in the request
    assert PASSWORD not in json.dumps(login_req.payload)


async def test_login_honors_iteration_flag(session, frozen_time):
    caps = {
        "code": 0,
        "data": {
            **CAPS_BODY["data"],
            "param": {**CAPS_BODY["data"]["param"], "enableIteration": False},
        },
    }
    session.queue(FakeResponse(200, caps), FakeResponse(200, LOGIN_OK))
    await make_client().login()
    sent = session.requests[1].payload["data"]["password"]
    # value differs from the iterated golden vector -> flag actually honored
    assert sent != ("a74fe62e41e671b144078063858188750b0812fa6c9881d5a6b6be740d4e7e80")


# --- cookie handling (the actual bug) --------------------------------------


async def test_login_preserves_dynamic_cookie_name(session, frozen_time):
    queue_login(session)
    client = make_client()
    await client.login()
    # full `name=value` pair, not just the token
    assert client._cookie == DYNAMIC_COOKIE


async def test_authed_request_sends_verbatim_cookie(session, frozen_time):
    queue_login(session, cookie="sessionID_zzz=some-token")
    session.queue(FakeResponse(200, DEVICE_INFO_OK))
    await make_client().get_device_info()

    authed_headers = session.requests[2].headers
    assert authed_headers["Cookie"] == "sessionID_zzz=some-token"


async def test_cookie_attributes_are_stripped(session, frozen_time):
    queue_login(session, cookie=f"{DYNAMIC_COOKIE}; Path=/; HttpOnly")
    client = make_client()
    await client.login()
    assert client._cookie == DYNAMIC_COOKIE


@pytest.mark.parametrize(
    "cookie",
    [None, "no-equals-sign", "=value-without-name", "sessionID_9f3b="],
)
async def test_login_bad_cookie_raises_invalid_auth(session, frozen_time, cookie):
    session.queue(
        FakeResponse(200, CAPS_BODY),
        FakeResponse(200, {"code": 0, "data": {"cookie": cookie}}),
    )
    with pytest.raises(InvalidAuth):
        await make_client().login()


async def test_login_failed_code_raises_invalid_auth(session, frozen_time):
    session.queue(
        FakeResponse(200, CAPS_BODY),
        FakeResponse(200, {"code": 5001, "data": {}}),
    )
    with pytest.raises(InvalidAuth):
        await make_client().login()


async def test_login_capabilities_missing_fields_unsupported(session, frozen_time):
    session.queue(FakeResponse(200, {"code": 0, "data": {"sessionID": "x"}}))
    with pytest.raises(UnsupportedDevice):
        await make_client().login()


async def test_login_capabilities_no_sha256_unsupported(session, frozen_time):
    caps = {
        "code": 0,
        "data": {
            **CAPS_BODY["data"],
            "encryptionType": ["aes128-1"],
        },
    }
    session.queue(FakeResponse(200, caps))
    with pytest.raises(UnsupportedDevice):
        await make_client().login()


# --- error mapping / session refresh ---------------------------------------


async def test_api_error_triggers_relogin_and_retry(session, frozen_time):
    queue_login(session)
    session.queue(
        FakeResponse(200, {"code": 5009, "data": {}}),  # device-info fails
    )
    queue_login(session)
    session.queue(FakeResponse(200, DEVICE_INFO_OK))

    result = await make_client().get_device_info()
    assert result["softwareVersion"] == "V2.1.3-test"
    # caps, login, device-info(fail), caps, login, device-info(ok)
    assert len(session.requests) == 6
    assert session.requests[5].headers["Cookie"] == DYNAMIC_COOKIE


async def test_401_triggers_relogin_and_retry(session, frozen_time):
    queue_login(session)
    session.queue(FakeResponse(401))
    queue_login(session)
    session.queue(FakeResponse(200, DEVICE_INFO_OK))

    result = await make_client().get_device_info()
    assert result["deviceType"] == "NVR"
    assert len(session.requests) == 6


async def test_persistent_401_raises_session_expired(session, frozen_time):
    queue_login(session)
    session.queue(FakeResponse(401))
    queue_login(session)
    session.queue(FakeResponse(401))
    with pytest.raises(SessionExpired):
        await make_client().get_device_info()


async def test_http_404_raises_unsupported_device(session, frozen_time):
    session.queue(FakeResponse(404))
    with pytest.raises(UnsupportedDevice):
        await make_client().login()


async def test_http_500_raises_cannot_connect(session, frozen_time):
    session.queue(FakeResponse(500))
    with pytest.raises(CannotConnect):
        await make_client().login()


async def test_timeout_raises_cannot_connect(session, frozen_time):
    session.queue(TimeoutError())
    with pytest.raises(CannotConnect):
        await make_client().login()


async def test_connection_error_raises_cannot_connect(session, frozen_time):
    session.queue(aiohttp.ClientConnectionError("boom"))
    with pytest.raises(CannotConnect):
        await make_client().login()


async def test_malformed_json_raises_unsupported(session, frozen_time):
    session.queue(FakeResponse(200, json_exc=json.JSONDecodeError("x", "doc", 0)))
    with pytest.raises(UnsupportedDevice):
        await make_client().login()


async def test_non_dict_json_raises_unsupported(session, frozen_time):
    session.queue(FakeResponse(200, ["not", "a", "dict"]))
    with pytest.raises(UnsupportedDevice):
        await make_client().login()


async def test_api_error_outside_login_propagates(session, frozen_time):
    """`ApiError` on a non-login call is retried once, then surfaces."""
    queue_login(session)
    session.queue(FakeResponse(200, {"code": 5009, "data": {}}))
    queue_login(session)
    session.queue(FakeResponse(200, {"code": 5009, "data": {}}))
    with pytest.raises(ApiError):
        await make_client().get_device_info()


# --- summary / higher level -------------------------------------------------


async def test_get_summary(session, frozen_time):
    queue_login(session)
    session.queue(
        FakeResponse(200, DEVICE_INFO_OK),
        FakeResponse(200, {"code": 0, "data": {"serialNum": "TESTSERIAL123"}}),
        FakeResponse(200, {"code": 0, "data": {"ipChannelNum": 8}}),
    )
    info = await make_client().get_summary()
    assert info == DeviceInfo(
        serial="TESTSERIAL123",
        device_type="NVR",
        software_version="V2.1.3-test",
        channel_count=8,
    )
    # every authed call carried the full dynamic cookie
    for req in session.requests[2:]:
        assert req.headers["Cookie"] == DYNAMIC_COOKIE


async def test_get_summary_missing_serial_raises(session, frozen_time):
    queue_login(session)
    session.queue(
        FakeResponse(200, DEVICE_INFO_OK),
        FakeResponse(200, {"code": 0, "data": {}}),
        FakeResponse(200, {"code": 0, "data": {"ipChannelNum": 8}}),
    )
    from longse_nvr.client import NvrError

    with pytest.raises(NvrError):
        await make_client().get_summary()


async def test_client_close(session):
    client = make_client()
    await client.close()  # FakeSession.close is a no-op; just exercise the path
