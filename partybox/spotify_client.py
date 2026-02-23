from __future__ import annotations

import json
import os
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
        poll_seconds: float = 2.0,
        timeout_seconds: float = 2.0,
    ) -> None:
        self.client_id = (client_id or "").strip()
        self.client_secret = (client_secret or "").strip()
        self.refresh_token = (refresh_token or "").strip()
        self.device_name = (device_name or "UJ-PartyBox").strip()
        self.poll_seconds = max(0.5, float(poll_seconds or 2.0))
        self.timeout_seconds = max(0.5, float(timeout_seconds or 2.0))

        self.enabled = bool(self.client_id and self.client_secret and self.refresh_token)

        self._access_token: str = ""
        self._access_token_expires_at: float = 0.0

        self._cache_state: Optional[Dict[str, Any]] = None
        self._cache_ts: float = 0.0

        self._me_cache: Optional[Dict[str, Any]] = None
        self._me_cache_ts: float = 0.0

    @classmethod
    def from_env(cls) -> "SpotifyClient":
        return cls(
            client_id=os.getenv("SPOTIFY_CLIENT_ID", ""),
            client_secret=os.getenv("SPOTIFY_CLIENT_SECRET", ""),
            refresh_token=os.getenv("SPOTIFY_REFRESH_TOKEN", ""),
            device_name=os.getenv("SPOTIFY_DEVICE_NAME", "UJ-PartyBox"),
            poll_seconds=float(os.getenv("SPOTIFY_POLL_SECONDS", "2") or "2"),
            timeout_seconds=2.0,
        )

    def get_state(self, force: bool = False) -> Dict[str, Any]:
        now = time.time()
        if not self.enabled:
            return self._empty_state(ok=False, state="inactive", error="spotify_not_configured")

        if (not force) and self._cache_state is not None and (now - self._cache_ts) < self.poll_seconds:
            return dict(self._cache_state)

        state = self._fetch_live_state()
        self._cache_state = dict(state)
        self._cache_ts = now
        return state

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

    def _http_json(
        self,
        method: str,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        data: Optional[bytes] = None,
    ) -> Tuple[int, Optional[Dict[str, Any]], str]:
        req = urllib.request.Request(url, method=method, headers=headers or {}, data=data)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                status = int(getattr(resp, "status", 0) or 0)
                body = resp.read() or b""
                if not body:
                    return status, None, ""
                try:
                    return status, json.loads(body.decode("utf-8", errors="ignore")), ""
                except Exception:
                    return status, None, "invalid_json"
        except urllib.error.HTTPError as e:
            status = int(getattr(e, "code", 0) or 0)
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
            return status, payload, str(e.reason or "http_error")
        except Exception as e:
            return 0, None, str(e)

    def _refresh_access_token(self) -> Tuple[bool, str]:
        if not self.enabled:
            return False, "spotify_not_configured"

        body = urllib.parse.urlencode(
            {
                "grant_type": "refresh_token",
                "refresh_token": self.refresh_token,
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            }
        ).encode("utf-8")

        status, payload, err = self._http_json(
            "POST",
            self.TOKEN_URL,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data=body,
        )
        if status != 200 or not payload:
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

    def _api_get(self, path: str, retry_401: bool = True) -> Tuple[int, Optional[Dict[str, Any]], str]:
        token, err = self._get_access_token()
        if not token:
            return 0, None, err or "token_unavailable"

        status, payload, req_err = self._http_json(
            "GET",
            f"{self.API_BASE}{path}",
            headers={"Authorization": f"Bearer {token}"},
            data=None,
        )

        if status == 401 and retry_401:
            self._access_token = ""
            self._access_token_expires_at = 0.0
            token2, err2 = self._get_access_token()
            if not token2:
                return 401, None, err2 or "token_refresh_failed"
            status, payload, req_err = self._http_json(
                "GET",
                f"{self.API_BASE}{path}",
                headers={"Authorization": f"Bearer {token2}"},
                data=None,
            )

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
    def _device_matches(device_name: str, expected_name: str) -> bool:
        return (device_name or "").strip().lower() == (expected_name or "").strip().lower()

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

        if status not in (200, 204):
            status2, payload2, err2 = self._api_get("/me/player/currently-playing")
            if status2 == 204:
                out = self._empty_state(ok=True, state="inactive")
                if me and me.get("display_name"):
                    out["account"] = {"display_name": str(me.get("display_name"))}
                return out
            if status2 != 200 or not payload2:
                return self._empty_state(ok=False, state="error", error=err2 or err or f"player_status_{status}")
            payload = payload2

        if not isinstance(payload, dict):
            return self._empty_state(ok=False, state="error", error="invalid_player_payload")

        device = payload.get("device") if isinstance(payload.get("device"), dict) else {}
        device_name = str(device.get("name") or "")
        on_partybox = self._device_matches(device_name, self.device_name)

        item = payload.get("item") if isinstance(payload.get("item"), dict) else {}
        artists = []
        for a in (item.get("artists") or []):
            if isinstance(a, dict) and a.get("name"):
                artists.append(str(a.get("name")))

        state = "playing" if bool(payload.get("is_playing")) else "paused"
        if not on_partybox:
            state = "inactive"

        out: Dict[str, Any] = {
            "ok": True,
            "state": state,
            "spotify_on_partybox": bool(on_partybox),
            "device": {
                "name": device_name,
                "id": str(device.get("id") or ""),
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
