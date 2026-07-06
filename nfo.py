"""Jellyfin-friendly metadata: a Kodi/Emby movie .nfo + a poster image.

Jellyfin reads local .nfo sidecars (movie schema) and folder images when the
library's "NFO" metadata provider is enabled and local metadata is preferred.
For ingested YouTube/uploaded videos we emit a movie NFO so Jellyfin uses our
title/plot/studio rather than mis-matching against TMDB.
"""

from __future__ import annotations

import html
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Optional

_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_name(name: str, fallback: str = "video") -> str:
    """Filesystem-safe folder/file base name."""
    name = (name or "").strip()
    name = _UNSAFE.sub("", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    return name[:150] or fallback


def _tag(name: str, value: Any) -> str:
    if value is None or value == "":
        return ""
    return f"  <{name}>{html.escape(str(value))}</{name}>\n"


def build_movie_nfo(info: dict) -> str:
    """Build a movie .nfo from a yt-dlp info dict (or a minimal upload dict)."""
    title = info.get("title") or "Untitled"
    plot = info.get("description") or ""
    uploader = info.get("uploader") or info.get("channel") or info.get("creator") or ""
    upload_date = info.get("upload_date") or ""      # YYYYMMDD
    year = upload_date[:4] if len(upload_date) >= 4 else (str(info.get("release_year")) if info.get("release_year") else "")
    premiered = ""
    if len(upload_date) == 8:
        premiered = f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:8]}"
    source_url = info.get("webpage_url") or info.get("original_url") or ""
    runtime_min = int(info.get("duration", 0) // 60) if info.get("duration") else ""

    genres = ""
    for cat in (info.get("categories") or [])[:5]:
        genres += _tag("genre", cat)
    tags = ""
    for tg in (info.get("tags") or [])[:15]:
        tags += _tag("tag", tg)

    parts = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n',
        "<movie>\n",
        _tag("title", title),
        _tag("originaltitle", title),
        _tag("plot", plot),
        _tag("outline", plot[:300]),
        _tag("studio", uploader),
        _tag("director", uploader),
        _tag("year", year),
        _tag("premiered", premiered),
        _tag("runtime", runtime_min),
        genres,
        tags,
        _tag("source", source_url),
        # A stable-ish unique id so Jellyfin doesn't merge distinct clips.
        _tag("uniqueid", info.get("id") or safe_name(title)),
        "</movie>\n",
    ]
    return "".join(p for p in parts if p)


def write_poster(thumb_path: Optional[Path], dest_dir: Path) -> Optional[Path]:
    """Convert/copy a thumbnail to poster.jpg next to the video. Returns the path or None."""
    if not thumb_path or not Path(thumb_path).exists():
        return None
    poster = dest_dir / "poster.jpg"
    src = Path(thumb_path)
    if src.suffix.lower() in (".jpg", ".jpeg"):
        shutil.copyfile(src, poster)
        return poster
    # Convert (webp/png) → jpg via ffmpeg (present in the image).
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(src), str(poster)],
            capture_output=True, timeout=60, check=True,
        )
        return poster if poster.exists() else None
    except Exception:  # noqa: BLE001
        return None
