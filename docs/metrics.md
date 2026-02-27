# PartyBox Metrics and Top Tracks

This document describes PartyBox Prometheus metrics and how play history feeds Grafana.

## Play History Model

PartyBox writes media play events into SQLite table `play_history` and computes top tracks from DB.

`play_history` columns:

- `id INTEGER PRIMARY KEY`
- `ts INTEGER NOT NULL` (unix seconds)
- `mode TEXT NOT NULL` (`partybox|spotify|airplay|bluetooth|tv|mute|unknown`)
- `title TEXT`
- `artist TEXT`
- `album TEXT`
- `provider_id TEXT` (spotify track id / youtube id / best-effort hash/id)
- `uri TEXT`
- `extra_json TEXT`

Indexes:

- `(ts)`
- `(mode, ts)`
- `(mode, provider_id)`

Logging rules:

- `partybox`: logged when a queued item transitions to playing (`/api/tv/mark_playing`), deduped by queue id.
- `spotify`: logged only while media mode is `spotify`, Spotify state is OK, playback is on PartyBox device, and track id changed.
- `airplay` / `bluetooth`: logged best-effort while those modes are active when a stream/track transition is detected. If metadata is unavailable, title is `Unknown`.

## Why Top-25 Is Bounded

Prometheus label cardinality is bounded by fixed ranks and limited modes.

- `25` ranks for `partybox`
- `25` ranks for `spotify`

Metrics:

- `partybox_top_item_plays{mode,rank,track_id}`
- `partybox_top_item_info{mode,rank,track_id,title,artist}`

Ranks are always emitted as `01..25`.
Missing rows emit `track_id="(none)"`, `plays=0`, `title=""`, `artist=""`.

## Caching Strategy

Top-25 DB queries are cached in memory for `PARTYBOX_TOP_CACHE_TTL_SECONDS` (default `30`).

## Core Metrics

### Health and Process

- `partybox_db_ok`
- `partybox_uptime_seconds`
- `partybox_build_info{version,git_sha}`

### HTTP

- `partybox_http_requests_total{method,route,status}`
- `partybox_http_request_duration_seconds{method,route}`
- `partybox_http_exceptions_total{route,exc_type}`

### Mode and Queue

- `partybox_mode{mode}`
- `partybox_queue_depth`
- `partybox_queue_add_total{source}`
- `partybox_queue_play_total`

### Timestamps

- `partybox_last_mode_change_timestamp_seconds`
- `partybox_spotify_last_success_timestamp_seconds`
- `partybox_last_queue_add_timestamp_seconds`
- `partybox_last_error_timestamp_seconds`

### Spotify

- `partybox_spotify_ok`
- `partybox_spotify_device_visible`
- `partybox_spotify_api_requests_total{endpoint,status}`
- `partybox_spotify_rate_limited_total`
- `partybox_spotify_rate_limit_total`
- `partybox_spotify_api_errors_total{status}`
- `partybox_spotify_last_rate_limit_retry_after_seconds`

### AirPlay / Bluetooth Observability

- `partybox_external_stream_active{mode="airplay|bluetooth"}`
- `partybox_external_metadata_available{mode="airplay|bluetooth"}`
- `partybox_external_last_play_timestamp_seconds{mode="airplay|bluetooth"}`

### Play History Counters

- `partybox_play_history_events_total{mode}`

## Query Notes for Grafana

Use `or vector(0)` to avoid no-data gaps:

```promql
sum by (status) (rate(partybox_http_requests_total[5m])) or vector(0)
```

```promql
partybox_top_item_plays{mode="spotify"} or vector(0)
```

```promql
partybox_external_stream_active{mode="bluetooth"} or vector(0)
```

## Local Validation

```bash
curl -s http://127.0.0.1:5000/readyz
curl -s http://127.0.0.1:5000/metrics | rg '^partybox_(top_item|last_|spotify_|external_|play_history|db_ok)'
```
