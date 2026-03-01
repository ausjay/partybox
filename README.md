<p align="center">
  <img src="/data/images/partybox_t.png" width="320" />
</p>

<p align="center">
  Self-hosted crowd-powered media appliance
</p>
# PartyBox

PartyBox turns any TV + Linux box into a self-hosted, crowd-powered music and video appliance.

It runs on your LAN.
It lives behind a TV.
It lets the room influence what plays — without losing control.

Originally built for a backyard kitchen project, it evolved into a properly engineered, mode-switching media system that behaves like a real appliance.

It’s what plays while:
- people are building, cooking, arguing, or drinking
- a football game is on but music still matters
- someone insists “this song fits right now”
- the vibe needs to shift without resetting the system

It’s not radio.  
It’s not just Spotify.  
It’s not a rented bar jukebox.  

It’s your system.

---

## The Idea

Let the crowd shape the vibe —  
while the host keeps control.

Guests request from their phones.  
The TV becomes the shared surface.  
The system runs locally and reliably.

No cloud dependency required.  
No recurring fees.  
No vendor lock-in.

---

## Multi-Mode by Design

The room doesn’t always need the same thing.

PartyBox switches modes instantly:

- `partybox` — curated, queue-driven music videos
- `spotify` — controlled Spotify Connect sessions
- `airplay` — direct streaming when needed
- `bluetooth` — fallback receiver mode
- `tv` — game or event mode
- `mute` — because sometimes the boil needs to be heard

This isn’t a playlist app.  
It’s a mode-switching media surface.

---

## Why It Works in Real Spaces

**Radio** is passive and generic.  
**Raw Spotify** gets chaotic in a group.  
**Bar jukeboxes** are expensive and locked down.

PartyBox:

- Makes the queue visible.
- Lets guests participate.
- Keeps the host in control.
- Prevents off-brand or bad content.
- Avoids 12-minute live jam detours.
- Shifts energy without tearing down the system.

Music videos change the room.  
The TV becomes part of the gathering.

---

## What People Can Do With It

### Guests

- Open `/u` (or `/user`) on the same LAN.
- Browse approved media.
- Add to queue.
- Participate without touching the TV remote.

### Host / Admin

- Use `/admin?key=...` as control center.
- Pause, resume, skip, clear queue.
- Lock requests or switch modes.
- Review play history with mode and device attribution.
- Monitor health and metrics.

### TV

- Run `/tv` as fullscreen display.
- Show active mode and now-playing state.
- Stay stable as an always-on surface.

---

## Feature Highlights

- Curated guest-request flow (approved catalog only).
- Multiple media modes with instant switching.
- Unified play history across all modes.
- Device/user attribution (where detectable).
- Prometheus metrics (`/metrics`).
- Health + readiness endpoints (`/healthz`, `/readyz`).
- Grafana-ready dashboards and metric catalog.
- Appliance-style operation via systemd services.
- LAN-first design (no HTTPS required by default).

Core stack:

- Flask
- SQLite
- MPV
- systemd
- nginx

---

## How It’s Used In Practice

1. Start in `partybox` mode for visible crowd participation.
2. Switch to `spotify` for tighter control.
3. Use `airplay` or `bluetooth` for direct streams.
4. Flip to `tv` mode during games or events.
5. Review admin history + metrics afterward.

It never feels like switching systems.  
Just switching modes.

---

## Public URL Contract

Primary public base:

`http://partybox.local`

- `/` → redirect to `/user`
- `/user` → redirect to `/u`
- `/u` → guest request page
- `/tv` → TV page
- `/admin?key=JBOX` → admin page (example key)

No HTTPS is required in current deployment (LAN-first model).

---

## Runtime Architecture

- `partybox.service`
  - Runs Flask app as user `partybox`
  - `ExecStart=/home/user/projects/partybox/.venv/bin/python -m partybox.app`
  - Binds to `127.0.0.1:5000`

- `partybox-player.service`
  - Runs MPV controller loop as user `partybox`

- `nginx`
  - Listens on HTTP `:80`
  - Reverse-proxies to `127.0.0.1:5000`
  - Provides LAN URL contract

It boots.
It survives reboots.
It exposes observability.
It behaves like something mounted behind a TV.

---

## Documentation Map

- Deployment and nginx contract: `docs/DEPLOYMENT.md`
- Appliance build/install: `docs/INSTALL_APPLIANCE.md`
- Audio modes: `docs/audio_modes.md`
- Metrics + Grafana notes: `docs/metrics.md`
- Media-mode observability: `docs/observability_and_media_modes.md`
- Ops observability endpoints: `docs/ops/observability.md`
- Quick install/run notes: `INSTALL.md`
- UI styling notes: `docs/design-system.md`

Grafana dashboard assets and metric dumps live in `ops/grafana/`.

---

## Health Check

```bash
./tools/health_check.sh
```

Checks:

- `nginx` service state
- `partybox` service state
- endpoint behavior (`/tv`, `/u`, `/admin`)
- direct Flask reachability
- kiosk autostart presence

---

## Operational Commands

```bash
sudo systemctl status partybox
sudo systemctl status partybox-player
sudo systemctl status nginx
sudo nginx -t && sudo systemctl reload nginx
```

---

## Future Direction

- Stronger admin authentication model
- Optional HTTPS for internet exposure
- Improved ingestion + catalog tooling
- Backup/restore automation
- Expanded observability

---

Built as a hobby.

Engineered like it might need to survive a venue someday.