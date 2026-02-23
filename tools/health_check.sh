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
  local expected="$3"
  local status

  # Use -I for HEAD request and parse first response line status code.
  status="$(curl -fsS -I "$url" 2>/dev/null | awk 'NR==1 {print $2}')"
  if [[ -z "${status}" ]]; then
    fail "${label} (${url}) no HTTP status"
    return
  fi

  if [[ "${status}" == "${expected}" ]]; then
    ok "${label} (${url}) -> ${status}"
  else
    fail "${label} (${url}) expected ${expected}, got ${status}"
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

check_http_status "nginx /tv" "http://127.0.0.1/tv" "200"
check_http_status "nginx /u" "http://127.0.0.1/u" "200"
check_http_status "nginx /user redirect" "http://127.0.0.1/user" "302"
check_http_status "nginx /admin?key=JBOX" "http://127.0.0.1/admin?key=JBOX" "200"

check_http_status "flask direct /tv" "http://127.0.0.1:5000/tv" "200"
if [[ "${PARTYBOX_HEALTH_CHECK_DESKTOP_AUTOSTART:-1}" == "1" ]]; then
  check_kiosk_autostart
fi

if [[ "$failures" -gt 0 ]]; then
  echo "Health check failed (${failures} checks)."
  exit 1
fi

echo "Health check passed."
exit 0
