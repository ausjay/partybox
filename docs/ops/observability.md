# PartyBox Observability

PartyBox exposes a Prometheus metrics baseline directly from the Flask app.

Detailed metric semantics (including top-25 track strategy and play counting rules) are in `docs/metrics.md`.
Media-mode now-playing detection details are in `docs/observability_and_media_modes.md`.

## Endpoints

- `GET /metrics`: Prometheus exposition format (`text/plain; version=0.0.4`)
- `GET /healthz`: process liveness (no external calls)
- `GET /readyz`: readiness (DB accessibility + no declared fatal state)

Existing routes remain unchanged.

## Metrics

### HTTP

- `partybox_http_requests_total{method,route,status}`
- `partybox_http_exceptions_total{route,exc_type}`
- `partybox_http_request_duration_seconds{method,route}`

Route labels use Flask route rules (for example `/api/state`) instead of raw paths.

### Media Mode

- `partybox_mode{mode="partybox|spotify|airplay|bluetooth|tv|mute"}`

Exactly one mode should be `1`.

### Queue

- `partybox_queue_depth`
- `partybox_queue_add_total{source}`
- `partybox_queue_play_total`
- `partybox_play_history_events_total{mode}`

### Spotify

- `partybox_spotify_ok`
- `partybox_spotify_api_requests_total{endpoint,status}`
- `partybox_spotify_rate_limited_total`
- `partybox_spotify_last_rate_limit_retry_after_seconds`
- `partybox_spotify_device_visible`

No tokens/secrets are emitted as labels or values.

### TV

- `partybox_tv_ok`
- `partybox_tv_commands_total{cmd}`
- `partybox_tv_errors_total{type}`

### AirPlay / Bluetooth

- `partybox_external_stream_active{mode}`
- `partybox_external_metadata_available{mode}`
- `partybox_external_last_play_timestamp_seconds{mode}`

### Process

- `partybox_uptime_seconds`
- `partybox_build_info{version,git_sha}` (always `1`)

## Local Validation

```bash
curl -s http://127.0.0.1:5000/healthz
curl -s http://127.0.0.1:5000/readyz
curl -s http://127.0.0.1:5000/metrics | head -n 40
```

## Prometheus Scrape Example

```yaml
scrape_configs:
  - job_name: "partybox"
    metrics_path: /metrics
    static_configs:
      - targets: ["partybox.local:5000"]
```

## Security Note

`/metrics` can reveal operational details. Keep PartyBox on a trusted LAN, or gate access via firewall/reverse proxy if needed.
