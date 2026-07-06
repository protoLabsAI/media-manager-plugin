"""The ingest pipeline: URL (YouTube / direct video) + upload → Jellyfin library.

One yt-dlp code path handles YouTube *and* generic direct-video URLs (yt-dlp's
generic extractor). Uploads skip the download and go straight to finalize. A
playlist/channel URL is expanded into one child job per entry.

Each job downloads into a per-job staging dir, then finalize() moves the media +
subtitles + poster into a clean library folder, writes an NFO, and triggers a
Jellyfin scan.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Optional

from . import jellyfin, jobs, nfo

# ── module config (set from register() via configure()) ─────────────────────
_CFG: dict[str, Any] = {
    "library_path": "/library/movies",
    "audio_path": "/library/music",
    "staging_dir": "/sandbox/media-manager/staging",
    "jellyfin_url": "http://jellyfin:8096",
    "jellyfin_api_key": "",
    "subtitle_langs": ["en"],
    "audio_format": "mp3",
    "allow_playlist": True,
    "max_playlist_items": 50,
}

_VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v"}
_AUDIO_EXTS = {".mp3", ".m4a", ".opus", ".flac", ".wav", ".aac", ".ogg"}
_THUMB_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
_SUB_EXTS = {".srt", ".vtt", ".ass"}


def configure(cfg: dict[str, Any]) -> None:
    _CFG.update({k: v for k, v in (cfg or {}).items() if v is not None})
    os.makedirs(_CFG["staging_dir"], exist_ok=True)


def _opt(job_opts: dict, key: str) -> Any:
    """Job option overrides module default."""
    if key in job_opts and job_opts[key] is not None:
        return job_opts[key]
    return _CFG.get(key)


# ── probing (playlist detection, no download) ───────────────────────────────
def probe_url(url: str) -> dict:
    """Return {is_playlist, title, entries:[{url,title}]} without downloading."""
    import yt_dlp

    opts = {"quiet": True, "no_warnings": True, "extract_flat": "in_playlist", "skip_download": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    if info.get("_type") == "playlist" or info.get("entries"):
        entries = []
        for e in (info.get("entries") or []):
            if not e:
                continue
            entries.append({"url": e.get("url") or e.get("webpage_url") or e.get("id"),
                            "title": e.get("title") or ""})
        return {"is_playlist": True, "title": info.get("title") or "playlist", "entries": entries}
    return {"is_playlist": False, "title": info.get("title") or "", "entries": []}


# ── the worker entry point ──────────────────────────────────────────────────
def process_job(job: dict) -> None:
    opts = json.loads(job.get("options") or "{}")
    if job["kind"] == "upload":
        _finalize_upload(job, opts)
        return
    # kind == url
    url = job["source"]
    # Expand a playlist/channel into child jobs (unless the job is already a child
    # or playlists are disabled or the user asked to treat the URL as a single item).
    if _opt(opts, "allow_playlist") and not opts.get("single") and not job.get("parent_id"):
        try:
            probe = probe_url(url)
        except Exception:  # noqa: BLE001
            probe = {"is_playlist": False}
        if probe.get("is_playlist") and probe.get("entries"):
            entries = probe["entries"][: int(_opt(opts, "max_playlist_items"))]
            child_ids = []
            for e in entries:
                child_ids.append(
                    jobs.enqueue("url", e["url"], title=e.get("title", ""),
                                 options={**opts, "single": True}, parent_id=job["id"])
                )
            jobs.update(job["id"], status=jobs.DONE, progress=100,
                        message=f"expanded playlist '{probe['title']}' into {len(child_ids)} job(s)")
            return
    _download_and_finalize(job, opts, url)


# ── download (yt-dlp) ───────────────────────────────────────────────────────
def _progress_hook_factory(job_id: str):
    def hook(d: dict) -> None:
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            got = d.get("downloaded_bytes") or 0
            pct = (got / total * 100.0) if total else 0.0
            jobs.update(job_id, progress=round(min(pct, 99.0), 1), message="downloading")
        elif d.get("status") == "finished":
            jobs.update(job_id, progress=99, message="processing (merge/transcode)")
    return hook


def _download_and_finalize(job: dict, opts: dict, url: str) -> None:
    import yt_dlp

    job_id = job["id"]
    audio_only = bool(opts.get("audio_only"))
    stage = Path(_CFG["staging_dir"]) / job_id
    stage.mkdir(parents=True, exist_ok=True)
    jobs.update(job_id, message="starting download")

    langs = opts.get("subtitle_langs") or _CFG["subtitle_langs"]
    want_subs = bool(opts.get("subtitles", True)) and not audio_only

    ydl_opts: dict[str, Any] = {
        "outtmpl": str(stage / "%(title).180s.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "writethumbnail": True,
        "restrictfilenames": False,
        "progress_hooks": [_progress_hook_factory(job_id)],
        "postprocessors": [],
    }
    if audio_only:
        ydl_opts["format"] = "ba/b"
        ydl_opts["postprocessors"].append(
            {"key": "FFmpegExtractAudio", "preferredcodec": _opt(opts, "audio_format"), "preferredquality": "0"}
        )
        ydl_opts["postprocessors"].append({"key": "EmbedThumbnail"})
    else:
        ydl_opts["format"] = "bv*+ba/b"
        ydl_opts["merge_output_format"] = "mp4"
        if opts.get("embed_chapters", True):
            # FFmpegMetadata with add_chapters embeds YouTube chapter markers.
            ydl_opts["postprocessors"].append({"key": "FFmpegMetadata", "add_chapters": True})
        if want_subs:
            ydl_opts["writesubtitles"] = True
            ydl_opts["writeautomaticsub"] = True
            ydl_opts["subtitleslangs"] = langs
            ydl_opts["subtitlesformat"] = "srt/best"
            ydl_opts["postprocessors"].append({"key": "FFmpegSubtitlesConvertor", "format": "srt"})

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
    if info.get("entries"):  # safety: a playlist slipped through
        info = next((e for e in info["entries"] if e), info)

    _finalize_from_stage(job, opts, info, stage, audio_only)


# ── uploads ─────────────────────────────────────────────────────────────────
def _ffprobe_duration(path: Path) -> float:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=30,
        )
        return float(out.stdout.strip() or 0)
    except Exception:  # noqa: BLE001
        return 0.0


def _finalize_upload(job: dict, opts: dict) -> None:
    src = Path(job["source"])
    if not src.exists():
        jobs.update(job["id"], status=jobs.ERROR, message=f"uploaded file missing: {src}")
        return
    audio_only = src.suffix.lower() in _AUDIO_EXTS
    title = job.get("title") or src.stem
    info = {"title": title, "id": job["id"], "duration": _ffprobe_duration(src),
            "description": opts.get("description", ""), "uploader": opts.get("uploader", "")}
    # Move the upload into a per-job stage so finalize logic is shared.
    stage = Path(_CFG["staging_dir"]) / job["id"]
    stage.mkdir(parents=True, exist_ok=True)
    staged = stage / src.name
    shutil.move(str(src), str(staged))
    _finalize_from_stage(job, opts, info, stage, audio_only)


# ── finalize: stage dir → library folder + NFO + poster + scan ──────────────
def _scan_stage(stage: Path) -> dict:
    found: dict[str, Optional[Path]] = {"media": None, "thumb": None, "subs": []}
    for p in sorted(stage.iterdir()):
        ext = p.suffix.lower()
        if ext in _VIDEO_EXTS or ext in _AUDIO_EXTS:
            # prefer the largest media file (merged output)
            if not found["media"] or p.stat().st_size > found["media"].stat().st_size:
                found["media"] = p
        elif ext in _THUMB_EXTS:
            found["thumb"] = p
        elif ext in _SUB_EXTS:
            found["subs"].append(p)
    return found


def _fix_perms(root: Path, gid: int) -> None:
    """Make an ingested item manageable by the library's owning group.

    Files land owned by the agent's uid; without this the operator can't delete
    them from the host shell. We chgrp the whole item to the library dir's group
    (the agent is a member via group_add) and make it group-writable. Jellyfin
    (root) reads it either way.
    """
    if gid < 0:
        return
    try:
        os.chown(root, -1, gid)
        os.chmod(root, 0o2775)  # setgid so anything added later inherits the group
        for p in root.rglob("*"):
            try:
                os.chown(p, -1, gid)
                os.chmod(p, 0o775 if p.is_dir() else 0o664)
            except OSError:
                pass
    except OSError:
        pass


def _finalize_from_stage(job: dict, opts: dict, info: dict, stage: Path, audio_only: bool) -> None:
    job_id = job["id"]
    jobs.update(job_id, message="organizing into library")
    found = _scan_stage(stage)
    media = found["media"]
    if not media:
        jobs.update(job_id, status=jobs.ERROR, message="no media file produced")
        return

    title = info.get("title") or job.get("title") or media.stem
    base = nfo.safe_name(title)
    upload_date = info.get("upload_date") or ""
    year = upload_date[:4] if len(upload_date) >= 4 else ""
    folder_name = f"{base} ({year})" if year else base

    root = Path(_CFG["audio_path"] if audio_only else _CFG["library_path"])
    dest_dir = root / folder_name
    dest_dir.mkdir(parents=True, exist_ok=True)

    media_dest = dest_dir / f"{folder_name}{media.suffix.lower()}"
    shutil.move(str(media), str(media_dest))

    # subtitles → <base>.<lang>.srt sidecars
    for sub in found["subs"]:
        # yt-dlp names like "<title>.en.srt" — keep the language token.
        lang = sub.stem.split(".")[-1] if "." in sub.stem else "und"
        shutil.move(str(sub), str(dest_dir / f"{folder_name}.{lang}{sub.suffix.lower()}"))

    # poster + NFO (video only; NFO is a movie schema)
    if not audio_only:
        nfo.write_poster(found["thumb"], dest_dir)
        (dest_dir / f"{folder_name}.nfo").write_text(nfo.build_movie_nfo(info), encoding="utf-8")

    # tidy the stage dir
    shutil.rmtree(stage, ignore_errors=True)

    # Make the item owned by the library's group + group-writable so the operator
    # (a member of that group) can manage it from the host, not just via Jellyfin.
    try:
        _fix_perms(dest_dir, os.stat(root).st_gid)
    except OSError:
        pass

    scan = jellyfin.trigger_scan(_CFG["jellyfin_url"], _CFG.get("jellyfin_api_key", ""))
    jobs.update(job_id, status=jobs.DONE, progress=100, title=title,
                output_path=str(media_dest), message=f"done · {scan}")
