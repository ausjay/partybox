# PartyBox Audio Modes

PartyBox supports six media modes:

1. `partybox` - local queue playback via `partybox-player.service` (MPV).
2. `spotify` - Spotify Connect via `librespot.service`.
3. `airplay` - AirPlay receiver via `partybox-airplay.service` (`shairport-sync`).
4. `bluetooth` - Bluetooth audio sink mode (service template added separately).
5. `tv` - TV channel audio mode (PartyBox local playback paused/stopped).
6. `mute` - force all managed playback modes silent.

## Mode Switching Contract

Mode switching is handled by `partybox.audio_mode.AudioModeManager`.

- Only one mode is active at a time.
- Switching away from a mode stops its service(s).
- Switching to `mute` stops all managed services and mutes the system sink.
- If a target mode start fails, PartyBox attempts automatic fallback to `mute`.
- Mode changes are logged with `[audio_mode]` entries in `partybox.service` logs.

Mode state is persisted in DB setting `media_mode`, with diagnostics in:

- `media_mode_last_switch_ts`
- `media_mode_last_error`
- `media_mode_last_actions_json`

## Dependencies

Install core audio packages (PipeWire stack + AirPlay + Bluetooth):

```bash
sudo apt update
sudo apt install -y pipewire wireplumber pipewire-pulse pulseaudio-utils shairport-sync bluez
```

`pipewire-pulse` allows apps using PulseAudio APIs to route into PipeWire.

## AirPlay Service Setup

Template files in repo:

- `deploy/systemd/partybox-airplay.service`
- `deploy/config/shairport-sync.conf`

Install:

```bash
sudo mkdir -p /etc/partybox
sudo cp /home/user/projects/partybox/deploy/config/shairport-sync.conf /etc/partybox/shairport-sync.conf
sudo cp /home/user/projects/partybox/deploy/systemd/partybox-airplay.service /etc/systemd/system/partybox-airplay.service
sudo systemctl daemon-reload
sudo systemctl enable partybox-airplay.service
```

Note: this service is mode-managed. It can be enabled at boot, but PartyBox mode switching will stop/start it as needed.

## Bluetooth Service Setup

Template files in repo:

- `deploy/systemd/partybox-bluetooth.service`
- `deploy/bin/partybox-bluetooth-helper.sh`

Install:

```bash
sudo cp /home/user/projects/partybox/deploy/bin/partybox-bluetooth-helper.sh /usr/local/bin/partybox-bluetooth-helper.sh
sudo chmod +x /usr/local/bin/partybox-bluetooth-helper.sh
sudo cp /home/user/projects/partybox/deploy/systemd/partybox-bluetooth.service /etc/systemd/system/partybox-bluetooth.service
sudo systemctl daemon-reload
sudo systemctl enable partybox-bluetooth.service
```

The helper intentionally does **not** clear pairings. It only toggles discoverable/pairable and adapter alias/power.

Optional discoverable window refresh (admin API):

```bash
curl -sS -X POST "http://127.0.0.1:5000/api/admin/bluetooth_discoverable?key=JBOX" \
  -H "Content-Type: application/json" \
  -d '{"seconds":300}'
```

## AirPlay Troubleshooting

AirPlay target not visible:

```bash
systemctl status partybox-airplay.service
journalctl -u partybox-airplay.service -n 120 --no-pager
```

No audio output:

```bash
wpctl status
pactl list short sinks
```

Bluetooth pairing issues:

```bash
systemctl status partybox-bluetooth.service
journalctl -u partybox-bluetooth.service -n 120 --no-pager
bluetoothctl show
bluetoothctl devices
bluetoothctl devices Connected
```

Mode conflicts:

```bash
curl -sS "http://127.0.0.1:5000/api/admin/media_mode_status?key=JBOX" | jq .
journalctl -u partybox -n 200 --no-pager | rg audio_mode
```

Shairport config parse issues:

```bash
shairport-sync -V
sudo /usr/bin/shairport-sync -c /etc/partybox/shairport-sync.conf --displayConfig
```

## Security Notes

- AirPlay receiver is LAN-visible. Restrict untrusted clients at network level.
- Bluetooth discoverable mode should be time-limited for public spaces.
- Pairing data is preserved between mode switches unless manually removed.
