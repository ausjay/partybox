from __future__ import annotations

import os
import re
import time
from typing import Dict, List, Tuple

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

MEDIA_MODES = ("partybox", "spotify", "airplay", "bluetooth", "tv", "mute")
EXTERNAL_META_MODES = ("airplay", "bluetooth")
TOP_ITEM_MODES = ("partybox", "spotify")
TOP_ITEM_LIMIT_DEFAULT = 25
START_TIME = time.time()
_TOP_PREVIOUS_LABELS: Dict[Tuple[str, str], Tuple[str, str, str]] = {}


def _clean_label(value: str, fallback: str = "unknown", max_len: int = 64) -> str:
    raw = (value or "").strip().lower()
    if not raw:
        return fallback
    out = re.sub(r"[^a-z0-9_./:-]+", "_", raw)
    out = re.sub(r"_+", "_", out).strip("_")
    return out[:max_len] or fallback


def _clean_text(value: str, max_len: int = 160) -> str:
    txt = (value or "").replace("\n", " ").replace("\r", " ").strip()
    return txt[:max_len]


def _normalize_route(route: str) -> str:
    if not route:
        return "unknown"
    return _clean_label(route, fallback="unknown", max_len=80)


def _normalize_method(method: str) -> str:
    return _clean_label(method, fallback="get", max_len=12)


def _normalize_status(status: int | str) -> str:
    try:
        return str(int(status))
    except Exception:
        return "0"


def _normalize_spotify_endpoint(endpoint: str) -> str:
    path = (endpoint or "").strip()
    if not path:
        return "unknown"
    if not path.startswith("/"):
        path = f"/{path}"
    return _clean_label(path.split("?", 1)[0], fallback="unknown", max_len=80)


def _rank_label(rank: int) -> str:
    return f"{max(1, int(rank)):02d}"


def _track_id_label(track_id: str) -> str:
    val = (track_id or "").strip()
    if not val:
        return "(none)"
    return _clean_label(val, fallback="(none)", max_len=96)


def _mode_for_top(mode: str) -> str:
    m = (mode or "").strip().lower()
    return m if m in TOP_ITEM_MODES else "partybox"


partybox_http_requests_total = Counter(
    "partybox_http_requests_total",
    "Total HTTP requests served by PartyBox.",
    ("method", "route", "status"),
)

partybox_http_exceptions_total = Counter(
    "partybox_http_exceptions_total",
    "Total uncaught HTTP exceptions in PartyBox request handling.",
    ("route", "exc_type"),
)

partybox_http_request_duration_seconds = Histogram(
    "partybox_http_request_duration_seconds",
    "HTTP request duration in seconds.",
    ("method", "route"),
)

partybox_mode = Gauge(
    "partybox_mode",
    "Active PartyBox mode. Exactly one mode should be 1.",
    ("mode",),
)

partybox_queue_depth = Gauge(
    "partybox_queue_depth",
    "Current number of queued/playing items.",
)

partybox_queue_add_total = Counter(
    "partybox_queue_add_total",
    "Total queue add operations.",
    ("source",),
)

partybox_queue_play_total = Counter(
    "partybox_queue_play_total",
    "Total queue item play starts.",
)

partybox_spotify_ok = Gauge(
    "partybox_spotify_ok",
    "Spotify backend health from current cached/live state (1=ok, 0=not ok).",
)

partybox_spotify_api_requests_total = Counter(
    "partybox_spotify_api_requests_total",
    "Spotify API request count by endpoint and status.",
    ("endpoint", "status"),
)

partybox_spotify_rate_limited_total = Counter(
    "partybox_spotify_rate_limited_total",
    "Number of Spotify 429 responses seen (legacy name).",
)

partybox_spotify_rate_limit_total = Counter(
    "partybox_spotify_rate_limit_total",
    "Number of Spotify 429 responses seen.",
)

partybox_spotify_api_errors_total = Counter(
    "partybox_spotify_api_errors_total",
    "Spotify API error responses by status.",
    ("status",),
)

partybox_spotify_last_rate_limit_retry_after_seconds = Gauge(
    "partybox_spotify_last_rate_limit_retry_after_seconds",
    "Last seen Spotify retry-after (seconds) when rate limited.",
)

partybox_spotify_device_visible = Gauge(
    "partybox_spotify_device_visible",
    "Whether a Spotify playback device is visible in current state.",
)

partybox_tv_ok = Gauge(
    "partybox_tv_ok",
    "TV integration health (heartbeat/status based).",
)

partybox_tv_commands_total = Counter(
    "partybox_tv_commands_total",
    "TV command count.",
    ("cmd",),
)

partybox_tv_errors_total = Counter(
    "partybox_tv_errors_total",
    "TV error count.",
    ("type",),
)

partybox_db_ok = Gauge(
    "partybox_db_ok",
    "SQLite DB health for metrics/play-history operations (1=ok, 0=error).",
)

partybox_last_mode_change_timestamp_seconds = Gauge(
    "partybox_last_mode_change_timestamp_seconds",
    "Unix timestamp for the last successful media mode change.",
)

partybox_spotify_last_success_timestamp_seconds = Gauge(
    "partybox_spotify_last_success_timestamp_seconds",
    "Unix timestamp of last successful Spotify state fetch.",
)

partybox_last_queue_add_timestamp_seconds = Gauge(
    "partybox_last_queue_add_timestamp_seconds",
    "Unix timestamp of most recent queue add operation.",
)

partybox_last_error_timestamp_seconds = Gauge(
    "partybox_last_error_timestamp_seconds",
    "Unix timestamp of most recent application error event.",
)

partybox_external_stream_active = Gauge(
    "partybox_external_stream_active",
    "Whether external receiver stream appears active by mode (airplay/bluetooth).",
    ("mode",),
)

partybox_external_metadata_available = Gauge(
    "partybox_external_metadata_available",
    "Whether metadata is currently available for external receiver mode.",
    ("mode",),
)

partybox_external_last_play_timestamp_seconds = Gauge(
    "partybox_external_last_play_timestamp_seconds",
    "Unix timestamp of last logged external play event by mode.",
    ("mode",),
)

partybox_play_history_events_total = Counter(
    "partybox_play_history_events_total",
    "Total play-history events persisted by mode.",
    ("mode",),
)

partybox_top_item_plays = Gauge(
    "partybox_top_item_plays",
    "Current top item play counts by mode/rank.",
    ("mode", "rank", "track_id"),
)

partybox_top_item_info = Gauge(
    "partybox_top_item_info",
    "Current top item metadata by mode/rank.",
    ("mode", "rank", "track_id", "title", "artist"),
)

partybox_uptime_seconds = Gauge(
    "partybox_uptime_seconds",
    "PartyBox process uptime in seconds.",
)

partybox_build_info = Gauge(
    "partybox_build_info",
    "Build metadata for PartyBox process.",
    ("version", "git_sha"),
)


def observe_http_request(method: str, route: str, status: int, duration_seconds: float) -> None:
    partybox_http_requests_total.labels(
        method=_normalize_method(method),
        route=_normalize_route(route),
        status=_normalize_status(status),
    ).inc()
    partybox_http_request_duration_seconds.labels(
        method=_normalize_method(method),
        route=_normalize_route(route),
    ).observe(max(0.0, float(duration_seconds)))


def observe_http_exception(route: str, exc_type: str) -> None:
    partybox_http_exceptions_total.labels(
        route=_normalize_route(route),
        exc_type=_clean_label(exc_type, fallback="exception", max_len=64),
    ).inc()


def set_mode(active_mode: str) -> None:
    current = (active_mode or "").strip().lower()
    for mode in MEDIA_MODES:
        partybox_mode.labels(mode=mode).set(1 if mode == current else 0)


def set_queue_depth(depth: int) -> None:
    partybox_queue_depth.set(max(0, int(depth or 0)))


def inc_queue_add(source: str) -> None:
    partybox_queue_add_total.labels(
        source=_clean_label(source, fallback="unknown", max_len=32)
    ).inc()


def inc_queue_play() -> None:
    partybox_queue_play_total.inc()


def set_spotify_ok(ok: bool) -> None:
    partybox_spotify_ok.set(1 if bool(ok) else 0)


def observe_spotify_api_request(endpoint: str, status: int | str) -> None:
    partybox_spotify_api_requests_total.labels(
        endpoint=_normalize_spotify_endpoint(endpoint),
        status=_normalize_status(status),
    ).inc()


def observe_spotify_rate_limited() -> None:
    partybox_spotify_rate_limited_total.inc()
    partybox_spotify_rate_limit_total.inc()


def observe_spotify_api_error(status: int | str) -> None:
    s = _normalize_status(status)
    if s == "429":
        return
    partybox_spotify_api_errors_total.labels(status=s).inc()


def set_spotify_last_rate_limit_retry_after_seconds(seconds: int | float) -> None:
    partybox_spotify_last_rate_limit_retry_after_seconds.set(max(0.0, float(seconds or 0)))


def set_spotify_device_visible(visible: bool) -> None:
    partybox_spotify_device_visible.set(1 if bool(visible) else 0)


def set_tv_ok(ok: bool) -> None:
    partybox_tv_ok.set(1 if bool(ok) else 0)


def inc_tv_command(cmd: str) -> None:
    partybox_tv_commands_total.labels(
        cmd=_clean_label(cmd, fallback="unknown", max_len=40)
    ).inc()


def inc_tv_error(error_type: str) -> None:
    partybox_tv_errors_total.labels(
        type=_clean_label(error_type, fallback="unknown", max_len=40)
    ).inc()


def set_db_ok(ok: bool) -> None:
    partybox_db_ok.set(1 if bool(ok) else 0)


def set_last_mode_change_timestamp(ts_seconds: int | float) -> None:
    partybox_last_mode_change_timestamp_seconds.set(max(0.0, float(ts_seconds or 0)))


def set_spotify_last_success_timestamp(ts_seconds: int | float) -> None:
    partybox_spotify_last_success_timestamp_seconds.set(max(0.0, float(ts_seconds or 0)))


def set_last_queue_add_timestamp(ts_seconds: int | float) -> None:
    partybox_last_queue_add_timestamp_seconds.set(max(0.0, float(ts_seconds or 0)))


def set_last_error_timestamp(ts_seconds: int | float) -> None:
    partybox_last_error_timestamp_seconds.set(max(0.0, float(ts_seconds or 0)))


def set_external_stream_active(mode: str, active: bool) -> None:
    m = _clean_label(mode, fallback="unknown", max_len=20)
    partybox_external_stream_active.labels(mode=m).set(1 if bool(active) else 0)


def set_external_metadata_available(mode: str, available: bool) -> None:
    m = _clean_label(mode, fallback="unknown", max_len=20)
    partybox_external_metadata_available.labels(mode=m).set(1 if bool(available) else 0)


def set_external_last_play_timestamp(mode: str, ts_seconds: int | float) -> None:
    m = _clean_label(mode, fallback="unknown", max_len=20)
    partybox_external_last_play_timestamp_seconds.labels(mode=m).set(max(0.0, float(ts_seconds or 0)))


def inc_play_history_event(mode: str) -> None:
    m = _clean_label(mode, fallback="unknown", max_len=20)
    partybox_play_history_events_total.labels(mode=m).inc()


def set_top_items(mode: str, items: List[Dict[str, object]], limit: int = TOP_ITEM_LIMIT_DEFAULT) -> None:
    m = _mode_for_top(mode)
    safe_limit = max(1, min(50, int(limit or TOP_ITEM_LIMIT_DEFAULT)))
    normalized: List[Dict[str, object]] = []
    for row in items[:safe_limit]:
        normalized.append(
            {
                "track_id": _track_id_label(str(row.get("track_id") or "")),
                "title": _clean_text(str(row.get("title") or ""), max_len=180),
                "artist": _clean_text(str(row.get("artist") or ""), max_len=120),
                "plays": int(row.get("plays") or 0),
            }
        )
    while len(normalized) < safe_limit:
        normalized.append({"track_id": "(none)", "title": "", "artist": "", "plays": 0})

    for idx, row in enumerate(normalized, start=1):
        rank = _rank_label(idx)
        track_id = str(row["track_id"])
        title = str(row["title"])
        artist = str(row["artist"])
        plays = max(0, int(row["plays"]))
        key = (m, rank)
        prev = _TOP_PREVIOUS_LABELS.get(key)
        curr = (track_id, title, artist)
        if prev and prev != curr:
            prev_track, prev_title, prev_artist = prev
            partybox_top_item_plays.labels(mode=m, rank=rank, track_id=prev_track).set(0)
            partybox_top_item_info.labels(
                mode=m,
                rank=rank,
                track_id=prev_track,
                title=prev_title,
                artist=prev_artist,
            ).set(0)

        partybox_top_item_plays.labels(mode=m, rank=rank, track_id=track_id).set(plays)
        partybox_top_item_info.labels(
            mode=m,
            rank=rank,
            track_id=track_id,
            title=title,
            artist=artist,
        ).set(1)
        _TOP_PREVIOUS_LABELS[key] = curr


def update_uptime() -> None:
    partybox_uptime_seconds.set(max(0.0, time.time() - START_TIME))


def render_metrics() -> bytes:
    update_uptime()
    return generate_latest()


for _mode in MEDIA_MODES:
    partybox_mode.labels(mode=_mode).set(0)
for _top_mode in TOP_ITEM_MODES:
    set_top_items(_top_mode, [], limit=TOP_ITEM_LIMIT_DEFAULT)

partybox_queue_depth.set(0)
partybox_spotify_ok.set(0)
partybox_spotify_last_rate_limit_retry_after_seconds.set(0)
partybox_spotify_device_visible.set(0)
partybox_tv_ok.set(0)
partybox_db_ok.set(1)
partybox_last_mode_change_timestamp_seconds.set(0)
partybox_spotify_last_success_timestamp_seconds.set(0)
partybox_last_queue_add_timestamp_seconds.set(0)
partybox_last_error_timestamp_seconds.set(0)
for _mode in EXTERNAL_META_MODES:
    partybox_external_stream_active.labels(mode=_mode).set(0)
    partybox_external_metadata_available.labels(mode=_mode).set(0)
    partybox_external_last_play_timestamp_seconds.labels(mode=_mode).set(0)
partybox_uptime_seconds.set(0)
partybox_build_info.labels(
    version=(os.getenv("PARTYBOX_VERSION", "unknown") or "unknown").strip() or "unknown",
    git_sha=(os.getenv("PARTYBOX_GIT_SHA", "unknown") or "unknown").strip() or "unknown",
).set(1)
