#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${PARTYBOX_BASE_URL:-http://127.0.0.1:5000}"
ADMIN_KEY="${PARTYBOX_ADMIN_KEY:-JBOX}"

if [[ "$#" -gt 0 ]]; then
  MODES=("$@")
else
  MODES=(partybox airplay bluetooth spotify tv mute partybox)
fi

has_jq=0
if command -v jq >/dev/null 2>&1; then
  has_jq=1
fi

echo "PartyBox mode switch test"
echo "BASE_URL=${BASE_URL}"
echo "MODES=${MODES[*]}"
echo

for mode in "${MODES[@]}"; do
  echo "==> Switching to mode: ${mode}"
  response="$(curl -fsS -X POST "${BASE_URL}/api/admin/media_mode?key=${ADMIN_KEY}" \
    -H "Content-Type: application/json" \
    -d "{\"mode\":\"${mode}\"}")"

  if [[ "$has_jq" -eq 1 ]]; then
    echo "$response" | jq '{ok, mode, error, status: {mode: .status.mode, last_error: .status.last_error, audio_muted: .status.audio_muted, bt_connected: .status.bluetooth_connected_devices}}'
  else
    echo "$response"
  fi

  state="$(curl -fsS "${BASE_URL}/api/state")"
  if [[ "$has_jq" -eq 1 ]]; then
    echo "$state" | jq '{media_mode, av_mode, paused, muted, mode, media_mode_status: {mode: .media_mode_status.mode, audio_muted: .media_mode_status.audio_muted}}'
  else
    echo "$state"
  fi

  echo
  sleep 1
done

echo "Mode switch test completed."
