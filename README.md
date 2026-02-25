# PartyBox

PartyBox is a local-LAN music video appliance for bars, patios, lounges, and house parties.

When people get together, they usually want one of two things:
- Chill, hang out, and keep the room energy right.
- Watch a game or event without killing the vibe.

PartyBox is built for both. It keeps ambient music and visuals running, while still giving guests a fun way to pick what plays next.

Guests use a simple request page, the TV runs a fullscreen playback surface, and an admin page controls queue/state. The stack is intentionally small and reliable: Flask + SQLite + MPV controller + systemd + nginx.

## Why PartyBox

- Music videos trigger nostalgia and shared memories in a way audio-only playlists often do not.
- A visible queue makes the room feel participatory, like a modern jukebox.
- Admin curation prevents low-quality, too-long, or off-brand content from getting through.
- One tap can switch into Spotify mode when a single person should control the vibe (opening, closing, special moments, game focus).

YouTube has millions of videos, but not all are right for a public venue. PartyBox lets administrators whitelist the good ones and keep the experience bar-appropriate: no NSFW surprises, no 15-minute live jam detours, no long dead-air intros.

Result: guests still choose what they want to hear and see, but inside rules that keep the room flowing.

## What It Does Today

- Serves a guest request experience (`/u`, public alias via nginx at `/user`)
- Serves a TV playback page (`/tv`)
- Serves an admin panel (`/admin?key=...`)
- Supports curated request mode (guest picks from approved catalog)
- Supports Spotify mode (single-operator control)
- Manages queue/catalog state in SQLite
- Supports local files and YouTube-backed entries
- Exposes admin health APIs and an ops health-check script
- Runs as long-lived appliance services under systemd

## Runtime Architecture

- `partybox.service`
  - Runs Flask app as user `partybox`
  - `ExecStart=/home/user/projects/partybox/.venv/bin/python -m partybox.app`
  - Binds to `127.0.0.1:5000`
- `partybox-player.service`
  - Runs MPV controller loop as user `partybox`
- `nginx` (system service, root-managed)
  - Listens on HTTP `:80`
  - Reverse-proxies to `127.0.0.1:5000`
  - Provides public URL contract at `http://partybox.local`

## Public URL Contract

Primary public base: `http://partybox.local`

- `http://partybox.local/` -> redirect to `/user`
- `http://partybox.local/user` -> redirect to `/u`
- `http://partybox.local/u` -> guest request page
- `http://partybox.local/tv` -> TV page
- `http://partybox.local/admin?key=JBOX` -> admin page (example key)

No HTTPS is required in current deployment.

## Documentation Map

- Deployment and nginx contract: `docs/DEPLOYMENT.md`
- Appliance build/install (Ubuntu/desktop/services): `docs/INSTALL_APPLIANCE.md`
- Audio mode switching (PartyBox/Spotify/AirPlay/Bluetooth/TV/Mute): `docs/audio_modes.md`
- Quick install/run notes: `INSTALL.md`
- UI styling notes: `docs/design-system.md`

## Health Check

Use the repo script:

```bash
./tools/health_check.sh
```

It checks:

- `nginx` service state
- `partybox` service state
- nginx endpoint behavior (`/tv`, `/u`, `/user`, `/admin?key=JBOX`)
- direct Flask reachability (`127.0.0.1:5000/tv`)
- kiosk desktop autostart entry for `/tv`

Script exits non-zero on failure.

Install kiosk desktop autostart:

```bash
./tools/install_kiosk_autostart.sh partybox
```

Kiosk launcher defaults to suppress desktop notification banners (including Chromium update popups).  
Set `PARTYBOX_KIOSK_DISABLE_DESKTOP_NOTIFICATIONS=0` before running installer if you want banners enabled.

## Operational Commands

```bash
sudo systemctl status partybox
sudo systemctl status partybox-player
sudo systemctl status nginx
sudo nginx -t && sudo systemctl reload nginx
```

## Future Upgrades (Planned/Ideas)

- Replace querystring admin key auth with stronger auth/session model
- Optional HTTPS and certificate automation for non-local deployments
- Better observability (structured logs, persistent metrics snapshot)
- Flask-first route parity for all public aliases (reduce nginx-only coupling)
- Backup/restore tooling and migration automation
- Improved media ingestion workflows (batch imports, validation, metadata)
