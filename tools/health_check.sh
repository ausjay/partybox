#!/usr/bin/env bash
set -u

failures=0

ok() {
  printf 'OK   %s\n' "$1"
}

fail() {
  printf 'FAIL %s\n' "$1"
  failures=$((failures + 1))
}

warn() {
  printf 'WARN %s\n' "$1"
}

check_service_active() {
  local svc="$1"
  local status
  if status="$(systemctl is-active "$svc" 2>/dev/null)"; then
    if [[ "$status" == "active" ]]; then
      ok "service ${svc} is active"
    else
      fail "service ${svc} is ${status}"
    fi
  else
    fail "service ${svc} status check failed"
  fi
}

check_http_status() {
  local label="$1"
  local url="$2"
  local expected_pattern="$3"
  local curl_extra="${4:-}"
  local status

  # Use -I for HEAD request and parse first response line status code.
  # shellcheck disable=SC2086
  status="$(curl -fsS -I $curl_extra "$url" 2>/dev/null | awk 'NR==1 {print $2}')"
  if [[ -z "${status}" ]]; then
    fail "${label} (${url}) no HTTP status"
    return
  fi

  if [[ "${status}" =~ ^(${expected_pattern})$ ]]; then
    ok "${label} (${url}) -> ${status}"
  else
    fail "${label} (${url}) expected ${expected_pattern}, got ${status}"
  fi
}

check_http_redirect() {
  local label="$1"
  local url="$2"
  local expected_location_prefix="$3"
  local status location location_path

  status="$(curl -fsS -I "$url" 2>/dev/null | awk 'NR==1 {print $2}')"
  location="$(curl -fsS -I "$url" 2>/dev/null | awk 'BEGIN{IGNORECASE=1} /^Location:/ {print $2; exit}' | tr -d '\r')"
  location_path="$(printf '%s' "$location" | sed -E 's#^[a-zA-Z]+://[^/]+##')"

  if [[ "$status" =~ ^(301|302)$ ]] && { [[ "$location" =~ ^${expected_location_prefix} ]] || [[ "$location_path" =~ ^${expected_location_prefix} ]]; }; then
    ok "${label} (${url}) -> ${status}, ${location}"
  else
    fail "${label} (${url}) expected 301/302 + ${expected_location_prefix}, got status=${status:-none}, location=${location:-none}"
  fi
}

check_tv_status_json() {
  local url="http://127.0.0.1/api/tv/status"
  local payload
  if ! payload="$(curl -fsS "$url" 2>/dev/null)"; then
    fail "tv status json fetch failed (${url})"
    return
  fi

  if command -v jq >/dev/null 2>&1; then
    if printf '%s' "$payload" | jq -e '.ok == true and has("status")' >/dev/null 2>&1; then
      ok "tv status json sanity (${url})"
    else
      fail "tv status json sanity failed (${url})"
      return
    fi

    local spotify_ok spotify_ts now age
    spotify_ok="$(printf '%s' "$payload" | jq -r '.state.spotify.ok // empty')"
    spotify_ts="$(printf '%s' "$payload" | jq -r '.state.spotify.ts // empty')"
    now="$(date +%s)"
    if [[ "$spotify_ok" == "true" && -n "$spotify_ts" && "$spotify_ts" =~ ^[0-9]+$ ]]; then
      age=$((now - spotify_ts))
      if [[ "$age" -gt 10 ]]; then
        warn "spotify status is stale (${age}s old)"
      else
        ok "spotify status freshness (${age}s)"
      fi
    elif [[ -n "$spotify_ok" ]]; then
      ok "spotify status present (not active)"
    else
      warn "spotify status not present in /api/tv/status (older API shape)"
    fi
  else
    if printf '%s' "$payload" | grep -q '"ok":[[:space:]]*true'; then
      ok "tv status json basic sanity (${url})"
    else
      fail "tv status json basic sanity failed (${url})"
    fi
    warn "jq not installed; skipped structured tv status checks"
  fi
}

check_kiosk_autostart() {
  local desktop_file="${PARTYBOX_HEALTH_KIOSK_AUTOSTART_FILE:-/home/partybox/.config/autostart/partybox-tv.desktop}"
  local expected_fragment="${PARTYBOX_HEALTH_KIOSK_EXPECTED_PATH_FRAGMENT:-/tv}"
  local exec_line=""
  local launcher=""

  if [[ ! -f "$desktop_file" ]]; then
    fail "kiosk autostart missing (${desktop_file})"
    return
  fi

  if grep -qiE '^Hidden=true|^X-GNOME-Autostart-enabled=false' "$desktop_file"; then
    fail "kiosk autostart disabled (${desktop_file})"
    return
  fi

  if grep -Fq "$expected_fragment" "$desktop_file"; then
    ok "kiosk autostart configured (${desktop_file})"
    return
  fi

  exec_line="$(awk 'BEGIN{IGNORECASE=1} /^Exec=/{print substr($0,6); exit}' "$desktop_file")"
  launcher="$(printf '%s' "$exec_line" | awk '{print $1}' | sed 's/^"//; s/"$//')"
  if [[ -n "$launcher" && -f "$launcher" ]] && grep -Fq "$expected_fragment" "$launcher"; then
    ok "kiosk autostart configured (${desktop_file} -> ${launcher})"
  else
    fail "kiosk autostart does not reference ${expected_fragment} (${desktop_file})"
  fi
}

echo "PartyBox health check"

check_service_active nginx
check_service_active partybox

check_http_redirect "nginx http / -> /user redirect" "http://partybox.local/" "/user"
check_http_redirect "nginx http /user -> /u redirect" "http://partybox.local/user" "/u"
check_http_status "nginx http /tv" "http://partybox.local/tv" "200"
check_http_status "nginx http /u" "http://partybox.local/u" "200"
check_http_status "nginx http /admin?key=JBOX" "http://partybox.local/admin?key=JBOX" "200"

check_http_status "flask direct /tv" "http://127.0.0.1:5000/tv" "200"
check_tv_status_json
if [[ "${PARTYBOX_HEALTH_CHECK_DESKTOP_AUTOSTART:-1}" == "1" ]]; then
  check_kiosk_autostart
fi

if [[ "$failures" -gt 0 ]]; then
  echo "Health check failed (${failures} checks)."
  exit 1
fi

echo "Health check passed."
exit 0
