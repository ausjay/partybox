# Partybox Appliance Setup Guide
Ubuntu 24.04 LTS + HDMI TV + MPV + USB Audio (UGREEN)

Target machine: HP EliteDesk  
Purpose: Dedicated Partybox video appliance  
Display: HDMI TV  
Audio: USB sound adapter (UGREEN)  
Playback engine: MPV (not browser-based)

---

# Overview

This machine runs:

- `partybox.service` → Flask API (port 5000)
- `partybox-player.service` → MPV controller
- `nginx` → HTTP :80 reverse proxy to Flask (`127.0.0.1:5000`)
- XFCE desktop (lightweight)
- LightDM (auto-login)
- Always-on HDMI output
- MPV fullscreen takeover when video plays
- Idle screen when nothing is playing
- Spotify mode indicator (no browser playback)

This is designed to behave like an appliance.

---

# 1. Base OS Install

Install:

- Ubuntu 24.04 LTS
- Minimal install
- Enable OpenSSH

After first boot:

```bash
sudo apt update
sudo apt upgrade -y
sudo apt install -y git curl jq python3-venv
```

---

# 2. Install Desktop (XFCE + LightDM)

We use XFCE because it's lightweight and stable.

```bash
sudo apt install -y xfce4 xfce4-goodies lightdm
```

If prompted to choose display manager, select:

```
lightdm
```

Verify:

```bash
cat /etc/X11/default-display-manager
```

Expected:

```
/usr/sbin/lightdm
```

---

# 3. Create Dedicated User

```bash
sudo adduser partybox
sudo usermod -aG video,audio partybox
```

---

# 4. Enable Auto Login (LightDM)

Edit:

```bash
sudo nano /etc/lightdm/lightdm.conf
```

Add:

```ini
[Seat:*]
autologin-user=partybox
autologin-user-timeout=0
user-session=xfce
```

Reboot and confirm it logs directly into desktop.

---

# 5. Install MPV

```bash
sudo apt install -y mpv
```

Test HDMI playback:

```bash
mpv --fs --force-window=yes /path/to/test.mp4
```

If video appears on HDMI display, MPV is working.

---

# 6. Configure USB Audio (UGREEN)

Plug in the USB audio adapter.

Check devices:

```bash
aplay -l
```

Find the card name (example: `Device` or `USB`).

Set as default:

```bash
mkdir -p /home/partybox/.config/pipewire
```

Create:

```bash
nano /home/partybox/.config/pipewire/pipewire.conf.d/99-default-audio.conf
```

(Alternatively configure via `pavucontrol` if installed.)

Install control tool:

```bash
sudo apt install -y pavucontrol
```

Log in as `partybox` and set USB device as default output.

---

# 7. Clone Partybox

As `partybox` user:

```bash
cd ~
git clone https://github.com/YOUR_REPO/partybox.git
cd partybox
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

---

# 8. Create Flask Service

Create:

```bash
sudo nano /etc/systemd/system/partybox.service
```

If your repo lives at `/home/user/projects/partybox` (current `partybox` host reality), use that path in `WorkingDirectory` and `ExecStart`.

```ini
[Unit]
Description=Partybox (Flask)
After=network.target

[Service]
User=partybox
WorkingDirectory=/home/partybox/partybox
ExecStart=/home/partybox/partybox/.venv/bin/python -m partybox.app
Restart=always
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

Enable:

```bash
sudo systemctl daemon-reload
sudo systemctl enable partybox.service
sudo systemctl start partybox.service
```

Test:

```bash
curl http://127.0.0.1:5000/api/state | jq .
```

---

# 9. Configure nginx Public HTTP Endpoint

Create:

```bash
sudo nano /etc/nginx/sites-available/partybox
```

Add:

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

Enable site and remove default:

```bash
sudo ln -s /etc/nginx/sites-available/partybox /etc/nginx/sites-enabled/partybox
sudo rm -f /etc/nginx/sites-enabled/default
```

Validate and reload:

```bash
sudo nginx -t && sudo systemctl reload nginx
```

Verification:

```bash
curl -I http://partybox.local/
curl -I http://partybox.local/user
curl -I http://partybox.local/u
curl -I http://partybox.local/tv
curl -I "http://partybox.local/admin?key=JBOX"
```

Expected status:

- `/` -> `302` to `/user`
- `/user` -> `302` to `/u`
- `/u` -> `200`
- `/tv` -> `200`
- `/admin?key=JBOX` -> `200`

Use `http://partybox.local/user` for QR codes and signage.
No HTTPS required.

---

# 10. Create Player Service (MPV Controller)

Create:

```bash
sudo nano /etc/systemd/system/partybox-player.service
```

```ini
[Unit]
Description=Partybox Player (MPV)
After=network-online.target partybox.service
Wants=network-online.target

[Service]
User=partybox
WorkingDirectory=/home/partybox/partybox
ExecStartPre=/bin/bash -c 'for i in {1..60}; do curl -fsS http://127.0.0.1:5000/api/state && exit 0; sleep 0.25; done; exit 1'
ExecStart=/home/partybox/partybox/.venv/bin/python -m partybox.tv_player
Restart=always
RestartSec=2
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

Enable:

```bash
sudo systemctl daemon-reload
sudo systemctl enable partybox-player.service
sudo systemctl start partybox-player.service
```

---

# 11. Ensure Display Never Sleeps

As `partybox` user:

```bash
xfce4-power-manager-settings
```

Disable:

- Display sleep
- DPMS
- Blank after inactivity

Also install:

```bash
sudo apt install -y caffeine
```

Add to XFCE startup if needed.

---

# 12. Configure TV Autostart (Open `/tv` on Login)

Install a kiosk startup entry so desktop login always opens the TV page:

```bash
cd /home/user/projects/partybox
./tools/install_kiosk_autostart.sh partybox
```

Default launcher behavior enables XFCE "Do Not Disturb" at startup so kiosk display does not show desktop notifications (e.g., Chromium update banners).
To keep desktop notifications enabled, run with:

```bash
PARTYBOX_KIOSK_DISABLE_DESKTOP_NOTIFICATIONS=0 ./tools/install_kiosk_autostart.sh partybox
```

Verify:

```bash
cat /home/partybox/.config/autostart/partybox-tv.desktop
```

If your preferred URL is `http://partybox.local/tv`, set:

```bash
PARTYBOX_TV_URL=http://partybox.local/tv ./tools/install_kiosk_autostart.sh partybox
```

---

# 13. Appliance Behavior Rules

HDMI display must ALWAYS show:

- Idle screen (nothing playing)
- Fullscreen MPV video
- Spotify mode screen

Never blank.
Never drop to console.
Never show terminal.

---

# 14. Access Local Console

If needed:

- Press `Ctrl + Alt + F3`
- Login
- Return with `Ctrl + Alt + F2`

---

# 15. Service Debug Commands

Check Flask:

```bash
sudo journalctl -u partybox.service -n 100 --no-pager
```

Check Player:

```bash
sudo journalctl -u partybox-player.service -n 100 --no-pager
```

Restart both:

```bash
sudo systemctl restart partybox.service
sudo systemctl restart partybox-player.service
```

nginx:

```bash
sudo systemctl status nginx
sudo nginx -t && sudo systemctl reload nginx
```

---

# 16. Media Directory

Expected location:

```
/home/partybox/partybox/data/media
```

Rescan:

```bash
curl -X POST http://127.0.0.1:5000/api/admin/media_scan?key=YOURKEY
```

---

# 17. Appliance Checklist

- [ ] Auto-login works
- [ ] Desktop opens `/tv` automatically on login
- [ ] HDMI always active
- [ ] USB audio default
- [ ] Flask reachable
- [ ] nginx active and serving `partybox.local`
- [ ] Public URL contract works (`/`, `/user`, `/u`, `/tv`, `/admin`)
- [ ] Player service running
- [ ] MPV fullscreen playback works
- [ ] Idle screen visible when paused
- [ ] `./tools/health_check.sh` passes

---

# Version

Partybox Appliance Spec v1  
Ubuntu 24.04  
MPV-based Rendering Architecture
