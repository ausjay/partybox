# PartyBox

PartyBox is a local-LAN party video appliance for a TV.

Guests use a simple request page, the TV runs a fullscreen playback surface, and an admin page controls queue/state. The stack is intentionally small: Flask + SQLite + MPV controller + systemd + nginx.

## What It Does Today

- Serves a guest request experience (`/u`, public alias via nginx at `/user`)
- Serves a TV playback page (`/tv`)
- Serves an admin panel (`/admin?key=...`)
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

Script exits non-zero on failure.

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

