from __future__ import annotations

import base64
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional, Tuple


class SpotifyClient:
    TOKEN_URL = "https://accounts.spotify.com/api/token"
    API_BASE = "https://api.spotify.com/v1"

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        device_name: str,
        device_id: str = "",
        cache_seconds: float = 15.0,
        timeout_seconds: float = 2.0,
    ) -> None:
        self.client_id = (client_id or "").strip()
        self.client_secret = (client_secret or "").strip()
        self.refresh_token = (refresh_token or "").strip()
        self.device_name = (device_name or "PartyBox").strip()
        self.device_id = (device_id or "").strip()
        self.cache_seconds = max(1.0, float(cache_seconds or 15.0))
        self.timeout_seconds = max(0.5, float(timeout_seconds or 2.0))

        self.enabled = bool(self.client_id and self.client_secret and self.refresh_token)

        self._access_token: str = ""
        self._access_token_expires_at: float = 0.0

        self._cache_state: Optional[Dict[str, Any]] = None
        self._cache_ts: float = 0.0
        self._last_fetch_ts: float = 0.0

        self._me_cache: Optional[Dict[str, Any]] = None
        self._me_cache_ts: float = 0.0

        self._cooldown_until: float = 0.0
        self._cooldown_reason: str = ""

    @classmethod
    def from_env(cls) -> "SpotifyClient":
        cache_raw = os.getenv("SPOTIFY_CACHE_SECONDS", "").strip()
        if not cache_raw:
            # Backward-compatible fallback if older env key is still used.
            cache_raw = os.getenv("SPOTIFY_POLL_SECONDS", "15").strip() or "15"
        return cls(
            client_id=os.getenv("SPOTIFY_CLIENT_ID", ""),
            client_secret=os.getenv("SPOTIFY_CLIENT_SECRET", ""),
            refresh_token=os.getenv("SPOTIFY_REFRESH_TOKEN", ""),
            device_name=os.getenv("SPOTIFY_DEVICE_NAME", "PartyBox"),
            device_id=os.getenv("SPOTIFY_DEVICE_ID", ""),
            cache_seconds=float(cache_raw or "15"),
            timeout_seconds=float(os.getenv("SPOTIFY_TIMEOUT_SECONDS", "2") or "2"),
        )

    def _cooldown_remaining(self) -> int:
        return max(0, int(self._cooldown_until - time.time()))

    def _metrics(self) -> Dict[str, Any]:
        age: Optional[int] = None
        if self._last_fetch_ts > 0:
            age = max(0, int(time.time() - self._last_fetch_ts))
        return {
            "last_fetch_age_s": age,
            "cooldown_remaining_s": self._cooldown_remaining(),
        }

    def _decorate(self, payload: Dict[str, Any], cached: bool) -> Dict[str, Any]:
        out = dict(payload or {})
        out["cached"] = bool(cached)
        out.update(self._metrics())
        return out

    def get_state(self, force: bool = False, allow_fetch: bool = True) -> Dict[str, Any]:
        if not self.enabled:
            return self._decorate(self._empty_state(ok=False, state="disabled", error="spotify_not_configured"), cached=True)

        remaining = self._cooldown_remaining()
        if remaining > 0:
            if self._cache_state is not None:
                stale = dict(self._cache_state)
                stale["state"] = "cooldown"
                stale["cooldown_remaining_s"] = remaining
                if self._cooldown_reason:
                    stale["error"] = self._cooldown_reason
                return self._decorate(stale, cached=True)

            return self._decorate(
                self._empty_state(
                    ok=False,
                    state="cooldown",
                    error=self._cooldown_reason or f"Too Many Requests (retry in {remaining}s)",
                ),
                cached=True,
            )

        if not allow_fetch:
            if self._cache_state is not None:
                return self._decorate(dict(self._cache_state), cached=True)
            return self._decorate(self._empty_state(ok=True, state="idle"), cached=True)

        now = time.time()
        if (not force) and self._cache_state is not None and (now - self._cache_ts) < self.cache_seconds:
            return self._decorate(dict(self._cache_state), cached=True)

        state = self._fetch_live_state()
        self._last_fetch_ts = now

        if not bool(state.get("ok")) and self._cache_state is not None:
            stale = dict(self._cache_state)
            stale["state"] = "cooldown" if self._cooldown_remaining() > 0 else str(state.get("state") or "error")
            if state.get("error"):
                stale["error"] = str(state.get("error"))
            self._cache_state = dict(stale)
            self._cache_ts = now
            return self._decorate(stale, cached=True)

        self._cache_state = dict(state)
        self._cache_ts = now
        return self._decorate(dict(state), cached=False)

    def _empty_state(self, ok: bool, state: str, error: str = "") -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "ok": bool(ok),
            "state": state,
            "spotify_on_partybox": False,
            "device": {"name": "", "id": "", "volume_percent": 0},
            "track": {"name": "", "artists": [], "album": "", "duration_ms": 0, "id": ""},
            "progress_ms": 0,
            "images": {"small": "", "medium": "", "large": ""},
            "shuffle_state": False,
            "repeat_state": "off",
            "ts": int(time.time()),
        }
        if error:
            payload["error"] = error
        return payload

    @staticmethod
    def _to_headers_map(headers_obj: Any) -> Dict[str, str]:
        out: Dict[str, str] = {}
        try:
            for k, v in dict(headers_obj.items()).items():
                out[str(k).lower()] = str(v)
        except Exception:
            pass
        return out

    def _http_json(
        self,
        method: str,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        data: Optional[bytes] = None,
    ) -> Tuple[int, Optional[Dict[str, Any]], str, Dict[str, str]]:
        req = urllib.request.Request(url, method=method, headers=headers or {}, data=data)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                status = int(getattr(resp, "status", 0) or 0)
                hdrs = self._to_headers_map(resp.headers)
                body = resp.read() or b""
                if not body:
                    return status, None, "", hdrs
                try:
                    return status, json.loads(body.decode("utf-8", errors="ignore")), "", hdrs
                except Exception:
                    return status, None, "invalid_json", hdrs
        except urllib.error.HTTPError as e:
            status = int(getattr(e, "code", 0) or 0)
            hdrs = self._to_headers_map(e.headers)
            raw = b""
            try:
                raw = e.read() or b""
            except Exception:
                pass

            payload = None
            if raw:
                try:
                    payload = json.loads(raw.decode("utf-8", errors="ignore"))
                except Exception:
                    payload = None
            return status, payload, str(e.reason or "http_error"), hdrs
        except Exception as e:
            return 0, None, str(e), {}

    def _log_http_call(self, endpoint: str, status: int, retry_after: int, cached: bool) -> None:
        print(
            f"[spotify_http] endpoint={endpoint} status={status} retry_after={retry_after} "
            f"cached={str(cached).lower()} cooldown_until={int(self._cooldown_until)}",
            flush=True,
        )

    def _refresh_access_token(self) -> Tuple[bool, str]:
        if not self.enabled:
            return False, "spotify_not_configured"

        body = urllib.parse.urlencode(
            {
                "grant_type": "refresh_token",
                "refresh_token": self.refresh_token,
            }
        ).encode("utf-8")

        basic_raw = f"{self.client_id}:{self.client_secret}".encode("utf-8")
        basic_auth = base64.b64encode(basic_raw).decode("ascii")

        status, payload, err, _ = self._http_json(
            "POST",
            self.TOKEN_URL,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Authorization": f"Basic {basic_auth}",
            },
            data=body,
        )
        if status != 200 or not payload:
            if payload:
                detail = str(payload.get("error_description") or payload.get("error") or "").strip()
                if detail:
                    return False, detail
            return False, err or f"token_refresh_status_{status}"

        token = str(payload.get("access_token") or "")
        expires_in = int(payload.get("expires_in") or 3600)
        if not token:
            return False, "token_missing"

        self._access_token = token
        self._access_token_expires_at = time.time() + max(30, expires_in - 30)
        return True, ""

    def _get_access_token(self) -> Tuple[Optional[str], str]:
        if self._access_token and time.time() < self._access_token_expires_at:
            return self._access_token, ""
        ok, err = self._refresh_access_token()
        if not ok:
            return None, err
        return self._access_token, ""

    @staticmethod
    def _parse_retry_after(headers: Dict[str, str]) -> int:
        ra = (headers or {}).get("retry-after", "").strip()
        return int(ra) if ra.isdigit() else 0

    def _enter_cooldown(self, retry_after: int) -> int:
        fallback = int(os.getenv("SPOTIFY_RATE_LIMIT_BACKOFF_SECONDS", "30") or "30")
        max_backoff = int(os.getenv("SPOTIFY_RATE_LIMIT_MAX_BACKOFF_SECONDS", "300") or "300")
        wait_s = retry_after if retry_after > 0 else fallback
        wait_s = max(5, wait_s)
        wait_s = min(wait_s, max(5, max_backoff))
        self._cooldown_until = time.time() + wait_s
        self._cooldown_reason = f"Too Many Requests (retry in {wait_s}s)"
        return wait_s

    def _api_get(self, path: str, retry_401: bool = True) -> Tuple[int, Optional[Dict[str, Any]], str]:
        remaining = self._cooldown_remaining()
        if remaining > 0:
            return 429, None, self._cooldown_reason or f"Too Many Requests (retry in {remaining}s)"

        token, err = self._get_access_token()
        if not token:
            return 0, None, err or "token_unavailable"

        status, payload, req_err, hdrs = self._http_json(
            "GET",
            f"{self.API_BASE}{path}",
            headers={"Authorization": f"Bearer {token}"},
            data=None,
        )

        retry_after = self._parse_retry_after(hdrs)
        if status == 429:
            retry_after = self._enter_cooldown(retry_after)
            req_err = self._cooldown_reason

        self._log_http_call(path, status, retry_after, cached=False)

        if status == 401 and retry_401:
            self._access_token = ""
            self._access_token_expires_at = 0.0
            token2, err2 = self._get_access_token()
            if not token2:
                return 401, None, err2 or "token_refresh_failed"
            status, payload, req_err, hdrs = self._http_json(
                "GET",
                f"{self.API_BASE}{path}",
                headers={"Authorization": f"Bearer {token2}"},
                data=None,
            )
            retry_after = self._parse_retry_after(hdrs)
            if status == 429:
                retry_after = self._enter_cooldown(retry_after)
                req_err = self._cooldown_reason
            self._log_http_call(path, status, retry_after, cached=False)

        return status, payload, req_err

    def _get_me(self) -> Optional[Dict[str, Any]]:
        now = time.time()
        if self._me_cache is not None and (now - self._me_cache_ts) < 300:
            return self._me_cache

        status, payload, _ = self._api_get("/me")
        if status == 200 and payload:
            self._me_cache = payload
            self._me_cache_ts = now
            return payload
        return self._me_cache

    @staticmethod
    def _normalize_name(name: str) -> str:
        return re.sub(r"\\s+", " ", (name or "").strip()).casefold()

    def _device_matches(self, device_name: str, device_id: str) -> bool:
        if self.device_id:
            return (device_id or "").strip() == self.device_id
        return self._normalize_name(device_name) == self._normalize_name(self.device_name)

    @staticmethod
    def _pick_images(item: Dict[str, Any]) -> Dict[str, str]:
        album = item.get("album") if isinstance(item, dict) else None
        images = album.get("images") if isinstance(album, dict) else None
        if not isinstance(images, list):
            return {"small": "", "medium": "", "large": ""}

        sorted_images = sorted(
            [im for im in images if isinstance(im, dict) and im.get("url")],
            key=lambda x: int(x.get("width") or 0),
        )
        if not sorted_images:
            return {"small": "", "medium": "", "large": ""}

        small = sorted_images[0].get("url") or ""
        medium = sorted_images[len(sorted_images) // 2].get("url") or ""
        large = sorted_images[-1].get("url") or ""
        return {"small": small, "medium": medium, "large": large}

    def _fetch_live_state(self) -> Dict[str, Any]:
        me = self._get_me()

        status, payload, err = self._api_get("/me/player")
        if status == 204:
            out = self._empty_state(ok=True, state="inactive")
            if me and me.get("display_name"):
                out["account"] = {"display_name": str(me.get("display_name"))}
            return out

        if status == 429:
            out = self._empty_state(ok=False, state="cooldown", error=err or "Too Many Requests")
            if me and me.get("display_name"):
                out["account"] = {"display_name": str(me.get("display_name"))}
            out["cooldown_remaining_s"] = self._cooldown_remaining()
            return out

        if status not in (200, 204):
            status2, payload2, err2 = self._api_get("/me/player/currently-playing")
            if status2 == 204:
                out = self._empty_state(ok=True, state="inactive")
                if me and me.get("display_name"):
                    out["account"] = {"display_name": str(me.get("display_name"))}
                return out
            if status2 == 429:
                out = self._empty_state(ok=False, state="cooldown", error=err2 or "Too Many Requests")
                if me and me.get("display_name"):
                    out["account"] = {"display_name": str(me.get("display_name"))}
                out["cooldown_remaining_s"] = self._cooldown_remaining()
                return out
            if status2 != 200 or not payload2:
                return self._empty_state(ok=False, state="error", error=err2 or err or f"player_status_{status}")
            payload = payload2

        if not isinstance(payload, dict):
            return self._empty_state(ok=False, state="error", error="invalid_player_payload")

        device = payload.get("device") if isinstance(payload.get("device"), dict) else {}
        device_name = str(device.get("name") or "")
        device_id = str(device.get("id") or "")

        if not device_name and not device_id:
            out = self._empty_state(ok=True, state="inactive")
            if me and me.get("display_name"):
                out["account"] = {"display_name": str(me.get("display_name"))}
            return out

        on_partybox = self._device_matches(device_name, device_id)

        item = payload.get("item") if isinstance(payload.get("item"), dict) else {}
        artists = []
        for a in (item.get("artists") or []):
            if isinstance(a, dict) and a.get("name"):
                artists.append(str(a.get("name")))

        state = "playing" if bool(payload.get("is_playing")) else "paused"

        out: Dict[str, Any] = {
            "ok": True,
            "state": state,
            "spotify_on_partybox": bool(on_partybox),
            "device": {
                "name": device_name,
                "id": device_id,
                "volume_percent": int(device.get("volume_percent") or 0),
            },
            "track": {
                "name": str(item.get("name") or ""),
                "artists": artists,
                "album": str(((item.get("album") or {}) if isinstance(item.get("album"), dict) else {}).get("name") or ""),
                "duration_ms": int(item.get("duration_ms") or 0),
                "id": str(item.get("id") or ""),
            },
            "progress_ms": int(payload.get("progress_ms") or 0),
            "images": self._pick_images(item),
            "shuffle_state": bool(payload.get("shuffle_state")),
            "repeat_state": str(payload.get("repeat_state") or "off"),
            "ts": int(time.time()),
        }
        if me and me.get("display_name"):
            out["account"] = {"display_name": str(me.get("display_name"))}
        return out
