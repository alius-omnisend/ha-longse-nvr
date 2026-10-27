"""Async klient HTTP JSON API (`/api/*`) dla rejestratorów Longse.

Algorytm logowania (challenge/salt/SHA-256 z iteracjami) zreverse-
engineerowany z `/statics/lib/utils/common/encryption/longseSha256.js` i
zweryfikowany na żywym sprzęcie testowym — pełne wyprowadzenie kroków
(roundOne..roundFour) opisane w PROTOCOL_NOTES.md w katalogu głównym repo.
Transport to zwykłe HTTP/JSON (nie binarny protokół jak DVRIP/XMEye), więc
całość działa natywnie na `aiohttp` bez potrzeby executor jobów.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import aiohttp

from .const import EVENT_ALL_MASK

_LOGGER = logging.getLogger(__name__)

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=10)

# Te same wartości co w webowym `callHttp()` z public.js na urządzeniu —
# wysyłane na KAŻDYM requeście przez oryginalny klient web.
API_VERSION = "v4.0.0"


class NvrError(Exception):
    """Błąd bazowy komunikacji z urządzeniem."""


class CannotConnect(NvrError):
    """Nie udało się połączyć z urządzeniem (sieć/timeout/zły status HTTP)."""


class ApiError(NvrError):
    """Urządzenie odpowiedziało poprawnie na poziomie HTTP, ale `code != 0`."""

    def __init__(self, path: str, code: Any) -> None:
        super().__init__(f"{path} zwróciło code={code}")
        self.code = code


class SessionExpired(NvrError):
    """`HTTP 401` — sesja wygasła/została unieważniona przez urządzenie.

    ZNALEZIONE NA ŻYWO 2026-07-03: zweryfikowane w logach HA po ~kilku
    minutach działania integracji (`/api/system/device-info -> HTTP 401`).
    To jest INNY tryb błędu niż `ApiError` (JSON `code != 0` przy HTTP 200)
    — trzeba było dodać osobną obsługę, bo `_authed_post` pierwotnie łapał
    tylko `ApiError` i nie resetował `_cookie`, więc PO WYGAŚNIĘCIU SESJI
    KAŻDY kolejny poll na zawsze kończył się tym samym błędem (cookie
    nigdy nie było czyszczone, więc `_authed_post` myślało, że wciąż jest
    zalogowane) — to zablokowało WSZYSTKIE encje integracji jako
    `unavailable` (nie tylko kamery: `DataUpdateCoordinator` oznacza
    `last_update_success=False` dla całego coordinatora, gdy `_async_update_data`
    rzuci nieobsłużony wyjątek, a każda `CoordinatorEntity`, łącznie z
    `sensor.*`, dziedziczy z tego swoją `available`)."""


class InvalidAuth(NvrError):
    """Urządzenie odrzuciło login (zły użytkownik/hasło)."""


class UnsupportedDevice(NvrError):
    """Urządzenie odpowiada po HTTP, ale nie mówi oczekiwanym API —
    brak endpointów `/api/*` (HTTP 404), nie-JSONowa/niekompletna odpowiedź
    albo brak typu szyfrowania `sha256-1` w login-capabilities (np. zwykły
    serwer web pod tym IP albo całkiem inna platforma/firmware)."""


@dataclass
class DeviceInfo:
    """Dane identyfikujące urządzenie, zebrane przy pierwszym połączeniu."""

    serial: str
    device_type: str
    software_version: str | None
    channel_count: int


def _sha256_hex(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _hex_to_raw(hex_str: str) -> bytes:
    """`hexCharCodeToStr` z longseSha256.js: każda para hex-cyfr -> jeden bajt."""
    return bytes.fromhex(hex_str)


class LongseNvrClient:
    """Cienki klient jednego rejestratora. Bezstanowy poza cookie sesji —
    bezpiecznie tworzyć jeden na config entry (patrz coordinator.py).

    UWAGA: celowo NIE używa współdzielonej sesji HA
    (`aiohttp_client.async_get_clientsession`) — serwer Boa na tym
    urządzeniu nie obsługuje poprawnie keep-alive dla `/api/*` (każda
    odpowiedź faktycznie zrywa połączenie mimo braku jawnego `Connection:
    close` na część requestów), co przy współdzielonym/poolowanym
    connectorze aiohttp objawia się `ServerDisconnectedError` na DRUGIM
    requeście w danej sesji (zweryfikowane empirycznie 2026-07-03: te same
    wywołania z `TCPConnector(force_close=True)` działają bez zarzutu).
    Stąd własna, dedykowana sesja z `force_close=True`, zamykana jawnie
    przez `close()`."""

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
    ) -> None:
        self._session = aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(force_close=True)
        )
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self._cookie: str | None = None

    async def close(self) -> None:
        await self._session.close()

    @property
    def _base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    async def _post(
        self, path: str, action: str, data: dict | None = None, *, authed: bool = True
    ) -> dict:
        payload: dict[str, Any] = {"action": action}
        if data is not None:
            payload["data"] = data
        # Headery identyczne jak w webowym `callHttp()` (public.js). Firmware
        # ich formalnie nie wymaga (zweryfikowane na żywo — samo JSON body
        # wystarcza), ale wysyłanie ich jak oryginalny klient web nic nie
        # kosztuje i chroni przed wariantami firmware, które mogłyby ich
        # wymagać.
        headers = {
            "Api-Version": API_VERSION,
            "Timestamp": datetime.now(UTC).isoformat(),
        }
        if authed and self._cookie:
            # `_cookie` to kompletna para `nazwa=wartość` zwrócona przez login
            # (nazwa jest dynamiczna, np. `sessionID_<suffix>` — patrz login()).
            headers["Cookie"] = self._cookie

        try:
            async with self._session.post(
                f"{self._base_url}{path}",
                json=payload,
                headers=headers,
                timeout=REQUEST_TIMEOUT,
            ) as resp:
                if resp.status == 401:
                    raise SessionExpired(f"{path} -> HTTP 401")
                if resp.status == 404:
                    raise UnsupportedDevice(
                        f"{path} -> HTTP 404 (endpoint nie istnieje)"
                    )
                if resp.status != 200:
                    raise CannotConnect(f"{path} -> HTTP {resp.status}")
                try:
                    result = await resp.json(content_type=None)
                except ValueError as err:
                    raise UnsupportedDevice(
                        f"{path} -> nieprawidłowa odpowiedź JSON"
                    ) from err
        except aiohttp.ClientError as err:
            raise CannotConnect(str(err)) from err
        except TimeoutError as err:
            raise CannotConnect(str(err)) from err

        if not isinstance(result, dict):
            raise UnsupportedDevice(f"{path} -> nieprawidłowa odpowiedź JSON")
        if result.get("code") != 0:
            raise ApiError(path, result.get("code"))
        return result.get("data") or {}

    async def login(self) -> None:
        """Pełny handshake challenge/salt/SHA-256 — kroki (roundOne..roundFour)
        opisane szczegółowo w PROTOCOL_NOTES.md. Krótko:
        1. pobierz salt/challenge/iterations z login-capabilities
        2. g = sha256(username + salt + base64(datetime) + password)
        3. f = sha256(hexToRaw(g) + challenge)
        4. powtórz f = sha256(hexToRaw(f)) `iterations` razy
        5. wyślij f jako "password" do /api/session/login razem z tym samym
           `datetime` (czysty string, NIE base64) i `sessionID` z kroku 1
        """
        caps = await self._post(
            "/api/session/login-capabilities",
            "get",
            {"username": self.username},
            authed=False,
        )
        try:
            param = caps["param"]
            salt = param["salt"]
            challenge = param["challenge"]
            iterations = param["iterations"]
            enable_iteration = param["enableIteration"]
            session_id = caps["sessionID"]
            enc_types = caps["encryptionType"]
        except (KeyError, TypeError) as err:
            raise UnsupportedDevice(
                "login-capabilities bez oczekiwanych pól — to raczej nie jest "
                "rejestrator na platformie Longse/BitVision"
            ) from err
        if "sha256-1" not in enc_types:
            raise UnsupportedDevice(
                f"login-capabilities nie oferuje sha256-1 (dostępne: {enc_types})"
            )
        enc_type = "sha256-1"

        now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        datetime_b64 = base64.b64encode(now.encode()).decode()

        # UWAGA: SHA-256 tu operuje na surowych bajtach (jak JS `charCodeAt`),
        # nie UTF-8 — stąd `latin-1`, patrz PROTOCOL_NOTES.md.
        g = _sha256_hex(
            (self.username + salt + datetime_b64 + self.password).encode("latin-1")
        )
        f = _sha256_hex(_hex_to_raw(g) + challenge.encode("latin-1"))
        if enable_iteration:
            for _ in range(iterations):
                f = _sha256_hex(_hex_to_raw(f))

        try:
            result = await self._post(
                "/api/session/login",
                "set",
                {
                    "username": self.username,
                    "loginEncryptionType": enc_type,
                    "password": f,
                    "sessionID": session_id,
                    "datetime": now,
                },
                authed=False,
            )
        except ApiError as err:
            raise InvalidAuth("logowanie odrzucone przez urządzenie") from err

        # `data.cookie` to kompletna para `nazwa=wartość` z DYNAMICZNĄ nazwą
        # (`sessionID_<suffix>`) — web UI robi `cookie.split("=")` i
        # `setCookie(name, value)` (patrz login.js na urządzeniu). Wysyłamy ją
        # z powrotem pod DOKŁADNIE tą samą nazwą — stare zachowanie (zawsze
        # `sessionID=<wartość>`) odrzucało suffix i kończyło się HTTP 401 na
        # każdym uwierzytelnionym requeście na firmware zwracającym suffix.
        # Uwaga: NIGDY nie logować/wstawiać tej wartości do wyjątków.
        cookie = result.get("cookie")
        if not isinstance(cookie, str):
            raise InvalidAuth("odpowiedź logowania bez cookie sesji")
        # Odrzuć ewentualne atrybuty (`; Path=/...`) — zostaje czysta para.
        cookie = cookie.split(";", 1)[0].strip()
        name, sep, value = cookie.partition("=")
        if not sep or not name.strip() or not value.strip():
            raise InvalidAuth("odpowiedź logowania z nieprawidłowym cookie sesji")
        self._cookie = cookie

    async def _authed_post(
        self, path: str, action: str, data: dict | None = None
    ) -> dict:
        """POST uwierzytelniony, z jednorazowym ponownym logowaniem przy
        `ApiError` lub `SessionExpired` (wygasła sesja — ZWERYFIKOWANE
        NA ŻYWO 2026-07-03, patrz docstring `SessionExpired`: urządzenie
        unieważnia sesję po jakimś czasie i odpowiada `HTTP 401`, nie
        JSON-owym `code != 0`). Przy `SessionExpired` jawnie czyścimy
        `_cookie`, żeby `login()` na pewno zbudował nową sesję od zera
        zamiast (teoretycznie) próbować cokolwiek ze starym stanem.
        `CannotConnect` (sieć/timeout) celowo NIE jest tu łapane — ponowne
        logowanie nie naprawi zerwanej sieci."""
        if self._cookie is None:
            await self.login()
        try:
            return await self._post(path, action, data)
        except (ApiError, SessionExpired):
            self._cookie = None
            await self.login()
            return await self._post(path, action, data)

    async def _secure_channel_params(self) -> dict:
        """Dodatkowy "reauth" payload wymagany przez `channel/channel-list`
        (i prawdopodobnie inne "wrażliwe" endpointy list — `system/log-maneger`,
        `system/user-manager`, `picture/picture-list` dają identyczny wzorzec
        błędów co `channel-list` bez tego payloadu, ALE dokładnie ten sam
        przepis NIE odblokował ich — potrzebują czegoś jeszcze, nieznane).
        Zreverse-engineerowane z fragmentu `public.js` (kontekst OSD/privacy-
        mask), zweryfikowane na żywo 2026-07-03 — patrz PROTOCOL_NOTES.md.

        Używa OSOBNEGO capability endpointu (`/api/security/capability`, nie
        `/api/session/login-capabilities`) z INNYM schematem szyfrowania
        (`aes128-1`, bez `challenge`, `param` kluczowany typem szyfrowania) i
        INNEGO (3-argumentowego) wariantu "roundTwo" niż logowanie — hasło
        NIE wchodzi do tego obliczenia wcale (potwierdzone: JS przekazuje 4
        argumenty do 3-parametrowej funkcji, nadmiarowy po prostu ignorowany).
        `iv` w zwracanym payloadzie to STAŁA `"afe13ds34cdjk08c"`, a
        wyliczona tu `datetime_key` (16 znaków) jest najpewniej kluczem
        AES-128 do odszyfrowania wrażliwych pól w odpowiedzi (np. `username`
        w `channelList` przychodzi jako base64-podobny ciąg) — deszyfrowanie
        NIE jest zaimplementowane (nie było potrzebne do ustalenia
        `enable`/`ip`/`port`, które przychodzą jawnym tekstem)."""
        cap = await self._authed_post(
            "/api/security/capability", "get", {"username": self.username}
        )
        try:
            enc_type = cap["encryptionType"][0]
            param = cap["param"][enc_type]
            salt = param["salt"]
            iterations = param["iterations"]
        except (KeyError, IndexError, TypeError) as err:
            raise UnsupportedDevice("security/capability bez oczekiwanych pól") from err

        now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        datetime_b64 = base64.b64encode(now.encode()).decode()

        # roundTwo(b,c,a) = sha256(b+c+a) -- hasło celowo pominięte, patrz wyżej
        s1 = _sha256_hex((self.username + salt + datetime_b64).encode("latin-1"))
        # reverseRoundThree(a) = sha256(hexToRaw(a) + "World!@##$")
        s2 = _sha256_hex(_hex_to_raw(s1) + "World!@##$".encode("latin-1"))
        s3 = s2
        for _ in range(iterations):
            s3 = _sha256_hex(_hex_to_raw(s3))
        # roundFive nieużywane tu dalej (byłby to klucz AES do deszyfrowania
        # pól typu `username` w odpowiedzi — nie implementowane, patrz wyżej)

        return {
            "encryptionType": enc_type,
            "loginName": self.username,
            "datetime": now,
            "iv": "afe13ds34cdjk08c",
        }

    async def get_channel_list(self) -> list[dict]:
        """`/api/channel/channel-list` — DUŻO bardziej wiarygodne niż
        `channel/online-status` (które zgłasza wszystkie kanały jako online
        niezależnie od realnego stanu, patrz PROTOCOL_NOTES.md). Zwraca listę
        słowników z m.in. `channel` (0-based), `enable` (czy kanał ma
        przypisaną kamerę IP), `ip`, `port`. UWAGA: `enable:true` oznacza
        "kanał ma przypisaną kamerę w konfiguracji", NIE "kamera aktualnie
        dostarcza obraz" — to nadal trzeba zweryfikować przez faktyczne
        połączenie RTSP (zweryfikowane empirycznie: kanał z `enable:true`
        też może nie mieć realnego wideo, jeśli fizyczna kamera jest
        niedostępna)."""
        params = await self._secure_channel_params()
        result = await self._authed_post("/api/channel/channel-list", "get", params)
        return result.get("channelList", [])

    async def get_recent_events(
        self, channels: list[int], since_epoch: int, until_epoch: int | None = None
    ) -> list[dict]:
        """`/api/picture/picture-list` — zdjęcia zapisane przy zdarzeniach
        (ruch/Smart Event). Zreverse-engineerowane z `onBtnSearchClk` w
        `/views/picture/picture.js` — w przeciwieństwie do `channel-list`
        NIE wymaga dodatkowego "reauth" payloadu, zwykłe cookie sesji
        wystarcza (zweryfikowane na żywo 2026-07-03).

        `startTime`/`endTime`: Unix epoch w SEKUNDACH jako string. Pole
        `pictureTime` w odpowiedzi to epoch w MIKROSEKUNDACH (string) —
        INNA jednostka niż w zapytaniu, łatwo to pomylić.

        To jedyny znaleziony sposób na wykrycie realnych zdarzeń na tym
        urządzeniu (push nie działa, patrz PROTOCOL_NOTES.md) — używane jako
        źródło danych dla `binary_sensor` przez polling
        (`LongseNvrEventCoordinator` w coordinator.py)."""
        if until_epoch is None:
            until_epoch = int(time.time())
        payload = {
            "channelList": channels,
            "pictureType": EVENT_ALL_MASK,
            "pictureOffset": 0,
            "requestNum": 500,
            "sortType": "time",
            "sortRule": "desc",
            "startTime": str(since_epoch),
            "endTime": str(until_epoch),
        }
        result = await self._authed_post("/api/picture/picture-list", "get", payload)
        return result.get("itemList", [])

    async def get_device_info(self) -> dict:
        """`/api/system/device-info` — firmware/typ urządzenia. UWAGA: pole
        `serialNo` tutaj jest bezużyteczne (literał "Network Video Recorder"),
        prawdziwy numer seryjny jest w `get_p2p_info()`."""
        return await self._authed_post("/api/system/device-info", "get")

    async def get_p2p_info(self) -> dict:
        """`/api/network/p2p` — jedyne sprawdzone miejsce z prawdziwym numerem
        seryjnym (`serialNum`), potrzebnym do `unique_id`."""
        return await self._authed_post("/api/network/p2p", "get")

    async def get_device_capabilities(self) -> dict:
        """`/api/system/device-capabilities` — m.in. `ipChannelNum`,
        `channelTotalNum`, `alarmInNum`, `alarmOutNum`, `supportIntercom`."""
        return await self._authed_post("/api/system/device-capabilities", "get")

    async def get_channel_names(self) -> dict[int, str]:
        """`/api/channel/priview-channel-name` — nazwy kanałów ustawione
        przez użytkownika w panelu web. Nie wymaga żadnych parametrów w
        `data` (w przeciwieństwie do `channel/channel-list`, które ZAWSZE
        odrzuca próby z niewłaściwym kształtem `data` — patrz PROTOCOL_NOTES.md).
        Zwraca `{channel_0based: nazwa}`; nazwa bywa pustym stringiem, jeśli
        użytkownik nic nie ustawił (zweryfikowane na testowym rejestratorze
        — wszystkie 16 kanałów miało `channelName: ""`)."""
        result = await self._authed_post("/api/channel/priview-channel-name", "get")
        names: dict[int, str] = {}
        for entry in result if isinstance(result, list) else []:
            names[int(entry["channel"])] = entry.get("channelName") or ""
        return names

    async def get_summary(self) -> DeviceInfo:
        """Zbiorcze dane identyfikujące urządzenie — do użycia w config_flow
        (walidacja połączenia) i przy pierwszym odświeżeniu coordinatora."""
        info = await self.get_device_info()
        p2p = await self.get_p2p_info()
        caps = await self.get_device_capabilities()

        serial = p2p.get("serialNum")
        if not serial:
            raise NvrError(
                "urządzenie nie zwróciło numeru seryjnego (network/p2p.serialNum)"
            )

        channel_count = caps.get("ipChannelNum") or caps.get("channelTotalNum") or 0

        return DeviceInfo(
            serial=str(serial),
            device_type=str(info.get("deviceType") or "NVR"),
            software_version=info.get("softwareVersion"),
            channel_count=int(channel_count),
        )
