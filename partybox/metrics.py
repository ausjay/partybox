from __future__ import annotations

import os
import re
import time
from typing import Optional

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

MEDIA_MODES = ("partybox", "spotify", "airplay", "bluetooth", "tv", "mute")
START_TIME = time.time()


def _clean_label(value: str, fallback: str = "unknown", max_len: int = 64) -> str:
    raw = (value or "").strip().lower()
    if not raw:
        return fallback
    out = re.sub(r"[^a-z0-9_./-]+", "_", raw)
    out = re.sub(r"_+", "_", out).strip("_")
    return out[:max_len] or fallback


def _normalize_route(route: str) -> str:
    if not route:
        return "unknown"
    # Keep route labels low cardinality and free of params.
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
    # Ensure no query string leakage.
    return _clean_label(path.split("?", 1)[0], fallback="unknown", max_len=80)


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
    "Number of Spotify 429 responses seen.",
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


def update_uptime() -> None:
    partybox_uptime_seconds.set(max(0.0, time.time() - START_TIME))


def render_metrics() -> bytes:
    update_uptime()
    return generate_latest()


# Initialize static/default gauges.
for _mode in MEDIA_MODES:
    partybox_mode.labels(mode=_mode).set(0)
partybox_queue_depth.set(0)
partybox_spotify_ok.set(0)
partybox_spotify_last_rate_limit_retry_after_seconds.set(0)
partybox_spotify_device_visible.set(0)
partybox_tv_ok.set(0)
partybox_uptime_seconds.set(0)
partybox_build_info.labels(
    version=(os.getenv("PARTYBOX_VERSION", "unknown") or "unknown").strip() or "unknown",
    git_sha=(os.getenv("PARTYBOX_GIT_SHA", "unknown") or "unknown").strip() or "unknown",
).set(1)

