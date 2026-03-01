# PartyBox Metrics Catalog

Source: `http://127.0.0.1:5000/metrics`
Generated: `1772165869` (unix epoch)
Metric families: `46`

| Metric | Type | Labels | Series | Help |
|---|---|---|---:|---|
| `partybox_build_info` | `gauge` | `git_sha, version` | 1 | Build metadata for PartyBox process. |
| `partybox_db_ok` | `gauge` | `` | 1 | SQLite DB health for metrics/play-history operations (1=ok, 0=error). |
| `partybox_external_last_play_timestamp_seconds` | `gauge` | `mode` | 2 | Unix timestamp of last logged external play event by mode. |
| `partybox_external_metadata_available` | `gauge` | `mode` | 2 | Whether metadata is currently available for external receiver mode. |
| `partybox_external_stream_active` | `gauge` | `mode` | 2 | Whether external receiver stream appears active by mode (airplay/bluetooth). |
| `partybox_http_exceptions_total` | `counter` | `` | 0 | Total uncaught HTTP exceptions in PartyBox request handling. |
| `partybox_http_request_duration_seconds` | `histogram` | `le, method, route` | 204 | HTTP request duration in seconds. |
| `partybox_http_request_duration_seconds_created` | `gauge` | `method, route` | 12 | HTTP request duration in seconds. |
| `partybox_http_requests_created` | `gauge` | `method, route, status` | 12 | Total HTTP requests served by PartyBox. |
| `partybox_http_requests_total` | `counter` | `method, route, status` | 12 | Total HTTP requests served by PartyBox. |
| `partybox_last_error_timestamp_seconds` | `gauge` | `` | 1 | Unix timestamp of most recent application error event. |
| `partybox_last_mode_change_timestamp_seconds` | `gauge` | `` | 1 | Unix timestamp for the last successful media mode change. |
| `partybox_last_queue_add_timestamp_seconds` | `gauge` | `` | 1 | Unix timestamp of most recent queue add operation. |
| `partybox_mode` | `gauge` | `mode` | 6 | Active PartyBox mode. Exactly one mode should be 1. |
| `partybox_play_history_events_total` | `counter` | `` | 0 | Total play-history events persisted by mode. |
| `partybox_queue_add_total` | `counter` | `` | 0 | Total queue add operations. |
| `partybox_queue_depth` | `gauge` | `` | 1 | Current number of queued/playing items. |
| `partybox_queue_play_created` | `gauge` | `` | 1 | Total queue item play starts. |
| `partybox_queue_play_total` | `counter` | `` | 1 | Total queue item play starts. |
| `partybox_spotify_api_errors_total` | `counter` | `` | 0 | Spotify API error responses by status. |
| `partybox_spotify_api_requests_total` | `counter` | `` | 0 | Spotify API request count by endpoint and status. |
| `partybox_spotify_device_visible` | `gauge` | `` | 1 | Whether a Spotify playback device is visible in current state. |
| `partybox_spotify_last_rate_limit_retry_after_seconds` | `gauge` | `` | 1 | Last seen Spotify retry-after (seconds) when rate limited. |
| `partybox_spotify_last_success_timestamp_seconds` | `gauge` | `` | 1 | Unix timestamp of last successful Spotify state fetch. |
| `partybox_spotify_ok` | `gauge` | `` | 1 | Spotify backend health from current cached/live state (1=ok, 0=not ok). |
| `partybox_spotify_rate_limit_created` | `gauge` | `` | 1 | Number of Spotify 429 responses seen. |
| `partybox_spotify_rate_limit_total` | `counter` | `` | 1 | Number of Spotify 429 responses seen. |
| `partybox_spotify_rate_limited_created` | `gauge` | `` | 1 | Number of Spotify 429 responses seen (legacy name). |
| `partybox_spotify_rate_limited_total` | `counter` | `` | 1 | Number of Spotify 429 responses seen (legacy name). |
| `partybox_top_item_info` | `gauge` | `artist, mode, rank, title, track_id` | 81 | Current top item metadata by mode/rank. |
| `partybox_top_item_plays` | `gauge` | `mode, rank, track_id` | 81 | Current top item play counts by mode/rank. |
| `partybox_tv_commands_created` | `gauge` | `cmd` | 1 | TV command count. |
| `partybox_tv_commands_total` | `counter` | `cmd` | 1 | TV command count. |
| `partybox_tv_errors_total` | `counter` | `` | 0 | TV error count. |
| `partybox_tv_ok` | `gauge` | `` | 1 | TV integration health (heartbeat/status based). |
| `partybox_uptime_seconds` | `gauge` | `` | 1 | PartyBox process uptime in seconds. |
| `process_cpu_seconds_total` | `counter` | `` | 1 | Total user and system CPU time spent in seconds. |
| `process_max_fds` | `gauge` | `` | 1 | Maximum number of open file descriptors. |
| `process_open_fds` | `gauge` | `` | 1 | Number of open file descriptors. |
| `process_resident_memory_bytes` | `gauge` | `` | 1 | Resident memory size in bytes. |
| `process_start_time_seconds` | `gauge` | `` | 1 | Start time of the process since unix epoch in seconds. |
| `process_virtual_memory_bytes` | `gauge` | `` | 1 | Virtual memory size in bytes. |
| `python_gc_collections_total` | `counter` | `generation` | 3 | Number of times this generation was collected |
| `python_gc_objects_collected_total` | `counter` | `generation` | 3 | Objects collected during gc |
| `python_gc_objects_uncollectable_total` | `counter` | `generation` | 3 | Uncollectable objects found during GC |
| `python_info` | `gauge` | `implementation, major, minor, patchlevel, version` | 1 | Python platform information |

## Label Value Samples

### `partybox_build_info`
- `git_sha`: `unknown`
- `version`: `unknown`

### `partybox_external_last_play_timestamp_seconds`
- `mode`: `airplay`, `bluetooth`

### `partybox_external_metadata_available`
- `mode`: `airplay`, `bluetooth`

### `partybox_external_stream_active`
- `mode`: `airplay`, `bluetooth`

### `partybox_http_request_duration_seconds`
- `le`: `+Inf`, `0.005`, `0.01`, `0.025`, `0.05`, `0.075`, `0.1`, `0.25`, `0.5`, `0.75`, `1.0`, `10.0`, `2.5`, `5.0`, `7.5`
- `method`: `get`, `head`, `post`
- `route`: `/admin`, `/api/admin/health`, `/api/admin/media_mode_status`, `/api/history`, `/api/queue`, `/api/state`, `/api/tv/heartbeat`, `/api/tv/mark_done`, `/api/tv/status`, `/metrics`, `/tv`, `/u`

### `partybox_http_request_duration_seconds_created`
- `method`: `get`, `head`, `post`
- `route`: `/admin`, `/api/admin/health`, `/api/admin/media_mode_status`, `/api/history`, `/api/queue`, `/api/state`, `/api/tv/heartbeat`, `/api/tv/mark_done`, `/api/tv/status`, `/metrics`, `/tv`, `/u`

### `partybox_http_requests_created`
- `method`: `get`, `head`, `post`
- `route`: `/admin`, `/api/admin/health`, `/api/admin/media_mode_status`, `/api/history`, `/api/queue`, `/api/state`, `/api/tv/heartbeat`, `/api/tv/mark_done`, `/api/tv/status`, `/metrics`, `/tv`, `/u`
- `status`: `200`

### `partybox_http_requests_total`
- `method`: `get`, `head`, `post`
- `route`: `/admin`, `/api/admin/health`, `/api/admin/media_mode_status`, `/api/history`, `/api/queue`, `/api/state`, `/api/tv/heartbeat`, `/api/tv/mark_done`, `/api/tv/status`, `/metrics`, `/tv`, `/u`
- `status`: `200`

### `partybox_mode`
- `mode`: `airplay`, `bluetooth`, `mute`, `partybox`, `spotify`, `tv`

### `partybox_top_item_info`
- `artist`: ``, `Brent Cobb`, `Charlie Robison`, `Clay Street Unit`, `Drive-By Truckers`, `James McMurtry`, `Kaitlin Butts`, `Robert Earl Keen`, `The Marcus King Band`, `The Red Clay Strays`, `The Steeldrivers`, `Trampled by Turtles`, `Trout Steak Revival`, `Whiskey Myers`, `Zach Bryan`
- `mode`: `partybox`, `spotify`
- `rank`: `01`, `02`, `03`, `04`, `05`, `06`, `07`, `08`, `09`, `10`, `11`, `12`, `13`, `14`, `15`, `16`, `17`, `18`, `19`, `20`
- `title`: ``, `Big City Blues`, `Charlie Robison - My Hometown (Video)`, `Copper Canteen`, `Engine Trouble`, `Gravity's Gone`, `Heart of Stone`, `How Lucky Am I`, `Life Is Good on the Open Road`, `Lonely Boy`, `Lyle Lovett:  Church`, `People Hatin'`, `Quittin' Time`, `RYAN BINGHAM - "Nobody Knows My Trouble" (Live in West Hollywood, CA) #JAMINTHEVAN`, `She's No Good`, `The Outskirts`, `The Price`, `The Toadies - Backslider`, `The Toadies - Tyler`, `When I Come Back Again`
- `track_id`: `(none)`, `09n5oiitfoyyl5s4ztn9pe`, `1glzqflonwqij3hijdxmxo`, `1rihxz9g1xmfsxszfekmaq`, `2jg8qg7kdywkm9smycxz1i`, `2upni0lioo6trcttrrtvsx`, `3ilu5gmuqs73z4c2u43xoj`, `50emgupm363qirybe20fr3`, `54md82rohcsrlc2ns3xchq`, `5q5cew3fetzrqyiyzxir8l`, `5z8koliqhnzz2vnjyd3qp7`, `65djcr0by2s8w1mdl270yj`, `738oxpa234cxaqkh9bpha4`, `7dhnvfxljnwvllglr6lmlc`, `9zl27aiw9_i`, `ekbk7bpxba4`, `file:lonely_boy.mp4`, `pd656b8w9mk`, `wg1pytowl6c`, `zzi0zo2ts1y`

### `partybox_top_item_plays`
- `mode`: `partybox`, `spotify`
- `rank`: `01`, `02`, `03`, `04`, `05`, `06`, `07`, `08`, `09`, `10`, `11`, `12`, `13`, `14`, `15`, `16`, `17`, `18`, `19`, `20`
- `track_id`: `(none)`, `09n5oiitfoyyl5s4ztn9pe`, `1glzqflonwqij3hijdxmxo`, `1rihxz9g1xmfsxszfekmaq`, `2jg8qg7kdywkm9smycxz1i`, `2upni0lioo6trcttrrtvsx`, `3ilu5gmuqs73z4c2u43xoj`, `50emgupm363qirybe20fr3`, `54md82rohcsrlc2ns3xchq`, `5q5cew3fetzrqyiyzxir8l`, `5z8koliqhnzz2vnjyd3qp7`, `65djcr0by2s8w1mdl270yj`, `738oxpa234cxaqkh9bpha4`, `7dhnvfxljnwvllglr6lmlc`, `9zl27aiw9_i`, `ekbk7bpxba4`, `file:lonely_boy.mp4`, `pd656b8w9mk`, `wg1pytowl6c`, `zzi0zo2ts1y`

### `partybox_tv_commands_created`
- `cmd`: `mark_done`

### `partybox_tv_commands_total`
- `cmd`: `mark_done`

### `python_gc_collections_total`
- `generation`: `0`, `1`, `2`

### `python_gc_objects_collected_total`
- `generation`: `0`, `1`, `2`

### `python_gc_objects_uncollectable_total`
- `generation`: `0`, `1`, `2`

### `python_info`
- `implementation`: `CPython`
- `major`: `3`
- `minor`: `12`
- `patchlevel`: `3`
- `version`: `3.12.3`

