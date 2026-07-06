"""Trigger a Jellyfin library scan after a new item lands on disk.

Uses httpx (a protoAgent core dep). Auth is the Jellyfin API key via the
X-Emby-Token header. A full-library refresh is cheap and reliably picks up the
new folder; targeting a single library by id is possible but brittle across
Jellyfin versions, so we keep it simple.
"""

from __future__ import annotations

import httpx


def trigger_scan(base_url: str, api_key: str) -> str:
    """POST /Library/Refresh. Returns a short status string (never raises)."""
    base = (base_url or "").rstrip("/")
    if not base:
        return "skip: JELLYFIN_URL not set"
    if not api_key:
        return "skip: JELLYFIN_API_KEY not set (scan not triggered — Jellyfin will pick it up on its next scheduled scan)"
    try:
        with httpx.Client(timeout=20.0) as client:
            r = client.post(
                f"{base}/Library/Refresh",
                headers={"X-Emby-Token": api_key},
            )
            if r.status_code in (200, 204):
                return "scan triggered"
            return f"scan HTTP {r.status_code}"
    except Exception as exc:  # noqa: BLE001
        return f"scan error: {type(exc).__name__}: {exc}"


def server_info(base_url: str) -> str:
    """Public server info (no auth) — used by the view's health check."""
    base = (base_url or "").rstrip("/")
    if not base:
        return ""
    try:
        with httpx.Client(timeout=8.0) as client:
            r = client.get(f"{base}/System/Info/Public")
            if r.status_code == 200:
                d = r.json()
                return f"{d.get('ServerName', '?')} (Jellyfin {d.get('Version', '?')})"
    except Exception:  # noqa: BLE001
        pass
    return ""
