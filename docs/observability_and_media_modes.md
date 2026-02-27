# Observability and Media Modes

This doc describes now-playing detection, play-history logging, and Spotify idle behavior.

## Spotify State and Idle UX

PartyBox classifies Spotify UI state without changing OAuth/token flows:

- `playing_on_partybox`: active playback on the PartyBox Connect device.
- `playing_elsewhere`: active playback, but on another device.
- `idle`: no active playback on PartyBox.
- `waiting`: Spotify unavailable/not authenticated yet.
- `rate_limited`: API returned `429`, PartyBox honors retry-after/cooldown.

When `idle`/`waiting`, UI shows:

- `Waiting for Spotify Connect...` (default)
- configurable hint via `PARTYBOX_SPOTIFY_CONNECT_HINT`

Rate limits are backoff-gated by Spotify client cooldown (no tight polling loops).

## AirPlay Metadata (Best-Effort)

Primary integration path is `shairport-sync` + optional metadata file.

- Required service: `partybox-airplay.service` or `shairport-sync.service`
- Stream detection: Pulse/pipewire sink-input inspection (`pactl list sink-inputs short`)
- Metadata detection:
  - Optional env: `PARTYBOX_AIRPLAY_METADATA_PATH` (JSON or JSONL file)
  - If unavailable, PartyBox reports `metadata unavailable` and logs `Unknown` titles best-effort

Recommended shairport config (example):

- enable metadata output in `/etc/partybox/shairport-sync.conf`
- write metadata to a file path used by `PARTYBOX_AIRPLAY_METADATA_PATH`

## Bluetooth Metadata (Best-Effort)

Bluetooth is treated as A2DP sink mode if BlueZ + helper service are configured.

- Required services: `bluetooth.service`, `partybox-bluetooth.service`
- Connection detection: `bluetoothctl devices Connected`
- Stream detection: `pactl list sink-inputs short` (bluez/a2dp sink inputs)
- Metadata attempt: BlueZ DBus `MediaControl1` / `MediaPlayer1` track properties

If AVRCP metadata is unavailable, PartyBox still exposes active status and logs `Unknown` play events on stream transitions.

## Play History

Table: `play_history`

- stores `ts`, `mode`, `title`, `artist`, `album`, `provider_id`, `uri`, `extra_json`
- API: `GET /api/history?limit=25`
- Admin UI panel: “Last 25 Plays”

Play counting:

- `partybox`: queue item starts playing
- `spotify`: track changes while playing on PartyBox device
- `airplay` / `bluetooth`: best-effort stream/track transition detection

Retention: currently unbounded (future cleanup/retention policy can be added).

## Metrics and Events

See also `docs/metrics.md`.

Relevant metrics for external modes:

- `partybox_external_stream_active{mode}`
- `partybox_external_metadata_available{mode}`
- `partybox_external_last_play_timestamp_seconds{mode}`
- `partybox_play_history_events_total{mode}`

Top-25 metrics remain bounded and exported only for `partybox` and `spotify`.

## House Account Preparation

Config knobs added for house-account UX wording:

- `PARTYBOX_SPOTIFY_IDLE_MESSAGE`
- `PARTYBOX_SPOTIFY_CONNECT_HINT`

TODOs in code mark where to switch messaging/flows when moving to a permanent Spotify account model.

## Manual Test Checklist

1. Switch to `partybox`, play 2 queued items, confirm entries in `/api/history`.
2. Switch to `spotify`, start playback on PartyBox device, confirm no `Unknown track` idle card.
3. Pause/stop Spotify, confirm UI shows waiting/idle message (not unknown track).
4. If AirPlay is configured, stream audio and confirm mode banner/admin now-playing updates.
5. If Bluetooth sink is configured, stream audio and confirm best-effort metadata/unknown fallback.
6. Check metrics:
   - `partybox_external_stream_active`
   - `partybox_play_history_events_total`
   - `partybox_top_item_plays`
