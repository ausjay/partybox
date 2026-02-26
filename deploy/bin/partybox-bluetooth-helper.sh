#!/usr/bin/env bash
set -euo pipefail

ACTION="${1:-}"
ALIAS="${PARTYBOX_BT_ALIAS:-PartyBox BT}"
DISCOVERABLE_TIMEOUT="${PARTYBOX_BT_DISCOVERABLE_TIMEOUT:-300}"

log() {
  printf '[partybox-bluetooth-helper] %s\n' "$1"
}

bt() {
  bluetoothctl "$@" >/dev/null
}

start_mode() {
  if ! systemctl is-active --quiet bluetooth.service; then
    log "starting bluetooth.service"
    systemctl start bluetooth.service
  fi

  log "configuring adapter power/pairable/discoverable"
  bt power on || true
  bt pairable on || true
  bt discoverable-timeout "$DISCOVERABLE_TIMEOUT" || true
  bt discoverable on || true
  bt system-alias "$ALIAS" || true
}

stop_mode() {
  log "disconnecting active bluetooth devices"
  while read -r _ dev _; do
    [ -n "${dev:-}" ] || continue
    bt disconnect "$dev" || true
  done < <(bluetoothctl devices Connected 2>/dev/null || true)

  log "disabling discoverable and pairable"
  bt discoverable off || true
  bt pairable off || true
  bt power off || true
}

status_mode() {
  bluetoothctl show || true
  bluetoothctl devices Connected || true
}

case "$ACTION" in
  start)
    start_mode
    ;;
  stop)
    stop_mode
    ;;
  status)
    status_mode
    ;;
  *)
    echo "Usage: $0 {start|stop|status}" >&2
    exit 2
    ;;
esac
