# PartyBox Install and Run

This file is the quick path. For full appliance setup details, use:

- `docs/INSTALL_APPLIANCE.md`
- `docs/DEPLOYMENT.md`

## Prerequisites

- Ubuntu 24.04+ (or similar Linux with systemd)
- Python 3.11+
- nginx
- (Appliance mode) MPV + desktop session + `partybox-player.service`

## Local App Setup

```bash
cd /home/user/projects/partybox
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Run app manually:

```bash
.venv/bin/python -m partybox.app
```

Default bind is local Flask endpoint:

```text
http://127.0.0.1:5000
```

## systemd Service (Current Host Reality)

`partybox.service` runs as user `partybox` and starts Flask with:

```text
/home/user/projects/partybox/.venv/bin/python -m partybox.app
```

Quick checks:

```bash
systemctl is-active partybox
systemctl status partybox
```

## nginx Exposure on LAN

Current deployment uses HTTP only (no HTTPS) on port 80 and exposes:

- `http://partybox.local/` -> `/user`
- `http://partybox.local/user` -> `/u`
- `http://partybox.local/tv`
- `http://partybox.local/admin?key=JBOX`

nginx validate/reload:

```bash
sudo nginx -t && sudo systemctl reload nginx
```

## Verification Commands

```bash
curl -I http://partybox.local/
curl -I http://partybox.local/user
curl -I http://partybox.local/u
curl -I http://partybox.local/tv
curl -I "http://partybox.local/admin?key=JBOX"
```

## Health Check Script

```bash
./tools/health_check.sh
```

This exits with non-zero status on failure.

## Kiosk Boot to `/tv`

Install desktop autostart for the appliance user:

```bash
./tools/install_kiosk_autostart.sh partybox
```

This creates:

- `/home/partybox/.local/bin/partybox-open-tv.sh`
- `/home/partybox/.config/autostart/partybox-tv.desktop`
