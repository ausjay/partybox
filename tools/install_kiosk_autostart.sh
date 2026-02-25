#!/usr/bin/env bash
set -euo pipefail

TARGET_USER="${1:-partybox}"
TARGET_HOME="$(getent passwd "$TARGET_USER" | cut -d: -f6 || true)"
if [[ -z "$TARGET_HOME" || ! -d "$TARGET_HOME" ]]; then
  echo "ERROR: could not resolve home directory for user '$TARGET_USER'" >&2
  exit 1
fi

if [[ "$(id -u)" -ne 0 && "$(id -un)" != "$TARGET_USER" ]]; then
  echo "ERROR: run as root (sudo) or as target user '$TARGET_USER'" >&2
  exit 1
fi

AUTOSTART_DIR="$TARGET_HOME/.config/autostart"
BIN_DIR="$TARGET_HOME/.local/bin"
LAUNCH_SCRIPT="$BIN_DIR/partybox-open-tv.sh"
DESKTOP_FILE="$AUTOSTART_DIR/partybox-tv.desktop"
TV_URL="${PARTYBOX_TV_URL:-http://127.0.0.1:5000/tv}"

mkdir -p "$AUTOSTART_DIR" "$BIN_DIR"

cat > "$LAUNCH_SCRIPT" <<'SCRIPT'
#!/usr/bin/env bash
set -euo pipefail

TV_URL="${PARTYBOX_TV_URL:-http://127.0.0.1:5000/tv}"
# Snap Chromium cannot write many hidden paths under $HOME; default to a visible dir.
KIOSK_PROFILE_DIR="${PARTYBOX_KIOSK_PROFILE_DIR:-$HOME/partybox-kiosk-profile}"
DISABLE_DESKTOP_NOTIFICATIONS="${PARTYBOX_KIOSK_DISABLE_DESKTOP_NOTIFICATIONS:-1}"

for _ in $(seq 1 60); do
  if curl -fsS "$TV_URL" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

if command -v pgrep >/dev/null 2>&1; then
  if pgrep -fa "(chromium|chrome|firefox).*(/tv|partybox.local/tv|127.0.0.1/tv|127.0.0.1:5000/tv)" >/dev/null 2>&1; then
    exit 0
  fi
fi

mkdir -p "$KIOSK_PROFILE_DIR"

# Chromium can leave stale singleton lock files behind after crashes.
if ! pgrep -u "$(id -u)" -f "(chromium|chrome)" >/dev/null 2>&1; then
  rm -f "$KIOSK_PROFILE_DIR/SingletonLock" \
        "$KIOSK_PROFILE_DIR/SingletonCookie" \
        "$KIOSK_PROFILE_DIR/SingletonSocket"
fi

# Suppress desktop notification banners on kiosk displays (e.g., Chromium update prompts).
if [[ "$DISABLE_DESKTOP_NOTIFICATIONS" == "1" ]]; then
  if command -v xfconf-query >/dev/null 2>&1; then
    xfconf-query -c xfce4-notifyd -p /do-not-disturb -n -t bool -s true >/dev/null 2>&1 || true
    xfconf-query -c xfce4-notifyd -p /log-level -n -t int -s 0 >/dev/null 2>&1 || true
  fi
fi

CHROME_FLAGS=(
  --user-data-dir="$KIOSK_PROFILE_DIR"
  --kiosk
  --incognito
  --no-first-run
  --disable-notifications
  --disable-component-update
  --disable-infobars
  --no-default-browser-check
)

if command -v chromium-browser >/dev/null 2>&1; then
  exec chromium-browser "${CHROME_FLAGS[@]}" "$TV_URL"
elif command -v chromium >/dev/null 2>&1; then
  exec chromium "${CHROME_FLAGS[@]}" "$TV_URL"
elif command -v google-chrome >/dev/null 2>&1; then
  exec google-chrome "${CHROME_FLAGS[@]}" "$TV_URL"
elif command -v firefox >/dev/null 2>&1; then
  exec firefox --kiosk "$TV_URL"
else
  exec xdg-open "$TV_URL"
fi
SCRIPT

cat > "$DESKTOP_FILE" <<EOF_DESKTOP
[Desktop Entry]
Type=Application
Version=1.0
Name=PartyBox TV Kiosk
Comment=Open PartyBox TV page at desktop login
Exec=$LAUNCH_SCRIPT
Terminal=false
X-GNOME-Autostart-enabled=true
EOF_DESKTOP

if [[ "$(id -u)" -eq 0 ]]; then
  chown "$TARGET_USER:$TARGET_USER" "$LAUNCH_SCRIPT" "$DESKTOP_FILE"
fi
chmod 0755 "$LAUNCH_SCRIPT"
chmod 0644 "$DESKTOP_FILE"

echo "Installed kiosk autostart for '$TARGET_USER'"
echo "- launcher: $LAUNCH_SCRIPT"
echo "- desktop:  $DESKTOP_FILE"
echo "- url:      $TV_URL"
