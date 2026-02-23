# PartyBox Networking, HTTPS, and Spotify OAuth

This document describes the current LAN deployment model for PartyBox on host `partybox`.

## Why Nginx Fronts Flask

Nginx sits in front of Flask to provide:

- Stable LAN hostname routing (`partybox.local`)
- Standard ports (`80`/`443`) while Flask stays on `127.0.0.1:5000`
- Clean public URLs (no `:5000` in user/admin/TV links)
- HTTPS termination needed for Spotify OAuth callback redirect URI

## Hostname and Routes

- LAN hostname: `partybox.local` (mDNS/LAN only, no public DNS required)
- Flask backend: `http://127.0.0.1:5000`

Key routes:

- `/u` (public user page)
- `/user` (redirect to `/u`)
- `/tv`
- `/admin?key=...`
- `/api/tv/status`
- `/health`

## Secrets and Runtime Environment

Secrets are **not** committed in Git. They live in:

- `/etc/partybox.env`

`partybox.service` should include:

```ini
EnvironmentFile=/etc/partybox.env
```

Spotify-related keys in `/etc/partybox.env`:

```bash
SPOTIFY_CLIENT_ID=...
SPOTIFY_CLIENT_SECRET=...
SPOTIFY_REFRESH_TOKEN=...
SPOTIFY_DEVICE_NAME=PartyBox
SPOTIFY_DEVICE_ID=
SPOTIFY_POLL_SECONDS=2
SPOTIFY_REDIRECT_URI=https://partybox.local/spotify/callback
```

## HTTPS Certificate (LAN Self-Signed)

Self-signed cert is acceptable for this LAN deployment.

Commands used:

```bash
sudo mkdir -p /etc/nginx/certs
sudo openssl req -x509 -nodes -days 3650 -newkey rsa:2048 \
  -keyout /etc/nginx/certs/partybox.local.key \
  -out /etc/nginx/certs/partybox.local.crt \
  -subj "/CN=partybox.local"
```

Paths used by nginx:

- `/etc/nginx/certs/partybox.local.crt`
- `/etc/nginx/certs/partybox.local.key`

Browser note: self-signed cert will show warning unless trust is configured.

## Nginx Site Config (HTTP -> HTTPS Redirect)

File:

- `/etc/nginx/sites-available/partybox`
- Repo template: `ops/nginx/partybox.conf`

Recommended config:

```nginx
server {
  listen 80;
  server_name partybox.local;

  return 302 https://$host$request_uri;
}

server {
  listen 443 ssl;
  server_name partybox.local;

  ssl_certificate     /etc/nginx/certs/partybox.local.crt;
  ssl_certificate_key /etc/nginx/certs/partybox.local.key;

  client_max_body_size 10m;

  location / {
    proxy_pass http://127.0.0.1:5000;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto https;
  }
}
```

Validate and reload:

```bash
sudo nginx -t
sudo systemctl reload nginx
```

Verify:

```bash
curl -Ik http://partybox.local/
curl -Ik https://partybox.local/
```

## Spotify OAuth Callback in PartyBox

Spotify redirect URI must exactly match Spotify Developer Dashboard settings.

Use:

- `https://partybox.local/spotify/callback`

Routes:

- `GET /spotify/auth` starts Spotify OAuth redirect
- `GET /spotify/callback` receives `code`/`state`, exchanges token, and updates refresh token

Required scopes:

- `user-read-playback-state`
- `user-read-currently-playing`

### Re-auth Procedure

1. Ensure env has valid `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET`, `SPOTIFY_REDIRECT_URI`.
2. Open `https://partybox.local/spotify/auth` in browser.
3. Approve Spotify consent.
4. Callback exchanges code and attempts to persist `SPOTIFY_REFRESH_TOKEN` to `/etc/partybox.env`.
5. Restart PartyBox:

```bash
sudo systemctl restart partybox.service
```

6. Verify Spotify payload:

```bash
curl -s https://partybox.local/api/tv/status -k | jq '.state.spotify'
```

### Discover `SPOTIFY_DEVICE_ID`

1. Start Spotify playback from phone.
2. Select output device `PartyBox` in Spotify Connect.
3. Run:

```bash
curl -s https://partybox.local/api/tv/status -k | jq -r '.state.spotify.device.id'
```

4. Set `SPOTIFY_DEVICE_ID` in `/etc/partybox.env` and restart `partybox.service`.

Device detection priority is:

1. `SPOTIFY_DEVICE_ID` (preferred)
2. `SPOTIFY_DEVICE_NAME` (normalized case/whitespace compare)

## Service Restart / Verify Cheatsheet

```bash
sudo systemctl daemon-reload
sudo systemctl restart partybox.service
sudo systemctl restart nginx
sudo systemctl restart librespot

systemctl is-active partybox nginx librespot
curl -s http://127.0.0.1:5000/health
curl -s https://partybox.local/api/tv/status -k | jq '.ok, .state.spotify'
```
