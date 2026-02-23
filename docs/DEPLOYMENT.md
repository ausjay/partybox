# PartyBox Deployment (HTTP + nginx)

This document captures the deployed contract for exposing PartyBox on LAN at `http://partybox.local` using nginx.

## Current Host Model

- Hostname/mDNS: `partybox.local` (Avahi-enabled LAN discovery)
- Flask app:
  - systemd service: `partybox.service`
  - runs as: `User=partybox` (not root)
  - command: `/home/user/projects/partybox/.venv/bin/python -m partybox.app`
  - bind: `127.0.0.1:5000`
- nginx:
  - system service (root-managed)
  - listens on HTTP port `80`
  - reverse proxies to Flask on `127.0.0.1:5000`
- HTTPS:
  - not required for this deployment

## Stable Public URL Contract

- Public landing: `http://partybox.local/` -> `/user`
- Public user page: `http://partybox.local/user` -> `/u`
- Guest page (app route): `http://partybox.local/u`
- TV page: `http://partybox.local/tv`
- Admin page: `http://partybox.local/admin?key=JBOX`

Use `http://partybox.local/user` in QR codes/signage.

## TV Paused UI

- TV paused state now uses a bottom "glass" status bar instead of a centered modal.
- While paused, `/tv` applies a dim + subtle blur overlay to the background so status is clear but the center logo remains unobstructed.
- The top-left TV header and idle QR prompt behavior are unchanged.

## Spotify Now Playing (Connect via phone)

PartyBox can display Spotify Now Playing metadata when playback is cast to the local librespot Connect device.

### 1) Create Spotify app credentials

1. Go to Spotify Developer Dashboard and create an app.
2. Add a redirect URI (example used by helper): `http://127.0.0.1:8888/callback`
3. Collect:
   - `SPOTIFY_CLIENT_ID`
   - `SPOTIFY_CLIENT_SECRET`

### 2) Get a refresh token

Use helper script:

```bash
./tools/spotify_auth.py \
  --client-id YOUR_CLIENT_ID \
  --client-secret YOUR_CLIENT_SECRET \
  --redirect-uri http://127.0.0.1:8888/callback
```

Scope used: `user-read-playback-state user-read-currently-playing`

Copy resulting:

- `SPOTIFY_REFRESH_TOKEN`

### 3) Configure partybox.service env vars

Set these in `/etc/systemd/system/partybox.service` (or a referenced EnvironmentFile):

```ini
Environment=SPOTIFY_CLIENT_ID=...
Environment=SPOTIFY_CLIENT_SECRET=...
Environment=SPOTIFY_REFRESH_TOKEN=...
Environment=SPOTIFY_DEVICE_NAME=UJ-PartyBox
Environment=SPOTIFY_POLL_SECONDS=2
```

`SPOTIFY_DEVICE_NAME` must match librespot Connect name (for example librespot `--name UJ-PartyBox`).
If Spotify is playing on another device, PartyBox reports Spotify as inactive for TV metadata display.

Reload and restart:

```bash
sudo systemctl daemon-reload
sudo systemctl restart partybox.service
```

### 4) Verify API payload

```bash
curl -s http://127.0.0.1:5000/api/tv/status | jq '.state.spotify'
```

Expected keys include:

- `ok`, `state`, `spotify_on_partybox`
- `track.name`, `track.artists`, `track.album`
- `progress_ms`, `track.duration_ms`
- `images.small|medium|large`
- `device.name`

Note: PartyBox does not identify which phone/user is currently controlling Spotify playback. It only shows current playback metadata and optional authenticated Spotify account display name.

## nginx Configuration

Edited file:

```text
/etc/nginx/sites-available/partybox
```

Expected contents:

```nginx
server {
  listen 80;
  server_name partybox.local;

  client_max_body_size 10m;

  location = / {
    return 302 /user;
  }

  location = /user {
    return 302 /u;
  }

  location / {
    proxy_pass http://127.0.0.1:5000;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
  }
}
```

Enabled by symlink:

```bash
sudo ln -s /etc/nginx/sites-available/partybox /etc/nginx/sites-enabled/partybox
```

Default nginx site removed:

```bash
sudo rm -f /etc/nginx/sites-enabled/default
```

Validate and reload nginx:

```bash
sudo nginx -t && sudo systemctl reload nginx
```

## Verification Commands

Run these exact checks:

```bash
curl -I http://partybox.local/
curl -I http://partybox.local/user
curl -I http://partybox.local/u
curl -I http://partybox.local/tv
curl -I "http://partybox.local/admin?key=JBOX"
```

Expected:

- `/` -> `302` to `/user`
- `/user` -> `302` to `/u`
- `/u` -> `200`
- `/tv` -> `200`
- `/admin?key=JBOX` -> `200`

## Health Check Script

Use:

```bash
./tools/health_check.sh
```

The script checks:

1. `systemctl is-active nginx`
2. `systemctl is-active partybox`
3. Endpoint health on `http://127.0.0.1` via nginx:
   - `/tv` (`200`)
   - `/u` (`200`)
   - `/user` (`302`)
   - `/admin?key=JBOX` (`200`)
4. Direct backend reachability:
   - `http://127.0.0.1:5000/tv` (`200`)

The script exits non-zero if any check fails.
