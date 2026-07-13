"""Stałe dla integracji Longse NVR (platforma web z longseSha256.js, sprzedawane
też pod marką Cantonk) — patrz PROTOCOL_NOTES.md w katalogu głównym repo."""

DOMAIN = "longse_nvr"

CONF_SERIAL = "serial"
CONF_CHANNEL_COUNT = "channel_count"
CONF_STREAM_TYPE = "stream_type"

DEFAULT_PORT = 80
DEFAULT_USERNAME = "admin"

RTSP_PORT = 554

STREAM_TYPE_MAIN = "main"
STREAM_TYPE_SUB = "sub"
DEFAULT_STREAM_TYPE = STREAM_TYPE_MAIN
# Format RTSP potwierdzony wizualnie na żywym sprzęcie (PROTOCOL_NOTES.md):
# rtsp://user:pass@host:554/XY, X=kanał 0-based, Y=stream (0=main,1=sub).
STREAM_TYPE_TO_RTSP_PARAM = {STREAM_TYPE_MAIN: 0, STREAM_TYPE_SUB: 1}

# Bitflagi zdarzeń z `/statics/js/variable.js` — potwierdzone na żywo
# 2026-07-03 jako wartości pola `pictureType` w `/api/picture/picture-list`
# (patrz `client.get_recent_events` i PROTOCOL_NOTES.md). Brak osobnej flagi dla
# "Humanoid Detection" — to WARTOŚĆ pola `detectionMode` w `event/face-detect`,
# nie osobny bit; pominięte tu (patrz PROTOCOL_NOTES.md, sekcja Smart Event).
EVENT_MOTION = 2
EVENT_FACE_DETECT = 32
EVENT_AREA_DETECT = 64  # Regional intrusion
EVENT_CROSS_DETECT = 128  # Line Crossing
EVENT_PEOPLE_STAY_DETECT = 4096  # Loitering
EVENT_PEOPLE_GATHERING_DETECT = 8192

EVENT_TYPE_KEYS: dict[int, str] = {
    EVENT_MOTION: "motion",
    EVENT_FACE_DETECT: "face",
    EVENT_AREA_DETECT: "intrusion",
    EVENT_CROSS_DETECT: "line_crossing",
    EVENT_PEOPLE_STAY_DETECT: "loitering",
    EVENT_PEOPLE_GATHERING_DETECT: "people_gathering",
}
EVENT_TYPE_LABELS: dict[int, str] = {
    EVENT_MOTION: "Ruch",
    EVENT_FACE_DETECT: "Twarz/sylwetka",
    EVENT_AREA_DETECT: "Wtargnięcie w strefę",
    EVENT_CROSS_DETECT: "Przekroczenie linii",
    EVENT_PEOPLE_STAY_DETECT: "Zbyt długie przebywanie",
    EVENT_PEOPLE_GATHERING_DETECT: "Zgromadzenie osób",
}
# Bity rozłączne, więc suma == OR bitowe - maska "wszystkie typy naraz" do
# jednego zapytania /api/picture/picture-list zamiast osobnego per typ.
EVENT_ALL_MASK = sum(EVENT_TYPE_KEYS)

# Push nie działa na tym urządzeniu (patrz PROTOCOL_NOTES.md) - binary_sensor
# polluje picture-list co EVENT_POLL_INTERVAL_SECONDS i uznaje zdarzenie za
# "aktywne" (is_on=True), dopóki od ostatniego zdjęcia danego typu/kanału
# nie minie EVENT_OFF_DELAY_SECONDS. Wartości dobrane empirycznie: krótki
# odstęp, żeby wykrywanie było responsywne, ale z zapasem na jitter pollingu.
EVENT_POLL_INTERVAL_SECONDS = 15
EVENT_OFF_DELAY_SECONDS = 30
