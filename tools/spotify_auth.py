#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import secrets
import urllib.parse
import urllib.request

AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"


def build_authorize_url(client_id: str, redirect_uri: str, scope: str, state: str) -> str:
    q = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": scope,
        "state": state,
        "show_dialog": "true",
    }
    return f"{AUTH_URL}?{urllib.parse.urlencode(q)}"


def exchange_code(client_id: str, client_secret: str, redirect_uri: str, code: str) -> dict:
    body = urllib.parse.urlencode(
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "client_secret": client_secret,
        }
    ).encode("utf-8")

    req = urllib.request.Request(
        TOKEN_URL,
        method="POST",
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        payload = resp.read() or b"{}"
    return json.loads(payload.decode("utf-8", errors="ignore"))


def main() -> None:
    p = argparse.ArgumentParser(description="Spotify OAuth helper for PartyBox")
    p.add_argument("--client-id", required=True)
    p.add_argument("--client-secret", required=True)
    p.add_argument("--redirect-uri", default="http://127.0.0.1:8888/callback")
    p.add_argument(
        "--scope",
        default="user-read-playback-state user-read-currently-playing",
        help="Spotify OAuth scopes",
    )
    args = p.parse_args()

    state = secrets.token_urlsafe(12)
    auth_url = build_authorize_url(args.client_id, args.redirect_uri, args.scope, state)

    print("1) Open this URL in a browser and approve access:\n")
    print(auth_url)
    print("\n2) Paste the full redirected callback URL:")
    redirected = input().strip()

    parsed = urllib.parse.urlparse(redirected)
    query = urllib.parse.parse_qs(parsed.query)
    code = (query.get("code") or [""])[0]
    got_state = (query.get("state") or [""])[0]

    if not code:
        raise SystemExit("No 'code' found in callback URL.")
    if got_state and got_state != state:
        raise SystemExit("State mismatch. Aborting.")

    token = exchange_code(args.client_id, args.client_secret, args.redirect_uri, code)
    refresh_token = token.get("refresh_token")

    if not refresh_token:
        print(json.dumps(token, indent=2))
        raise SystemExit("No refresh_token returned.")

    print("\nSPOTIFY_REFRESH_TOKEN=")
    print(refresh_token)


if __name__ == "__main__":
    main()
