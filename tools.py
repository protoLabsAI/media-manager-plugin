"""Agent-facing tools for the media manager.

All ingestion is asynchronous: media_ingest enqueues a job (or a playlist's child
jobs) and returns immediately with the job id(s). Use media_job_status to poll.
"""

from __future__ import annotations

import json

from langchain_core.tools import tool

from . import ingest, jobs


@tool
def media_ingest(url: str, audio_only: bool = False, subtitles: bool = True, single: bool = False) -> str:
    """Ingest a video into the media library from a YouTube or direct video URL.

    Downloads the video (or audio), generates Jellyfin metadata (NFO + poster),
    files it into the library, and triggers a Jellyfin scan. A playlist/channel
    URL expands into one job per entry. Runs in the background — returns the
    job id(s) immediately; poll with media_job_status.

    Args:
        url: A YouTube URL, playlist/channel URL, or a direct video/MP4 link.
        audio_only: Extract audio only (into the audio library) instead of video.
        subtitles: Fetch subtitles as .srt sidecars (video only).
        single: Treat a playlist URL as a single video (ingest just the one).
    Returns the queued job id and a note, or a readable error.
    """
    url = (url or "").strip()
    if not url.startswith(("http://", "https://")):
        return "Error: provide an http(s) URL."
    opts = {"audio_only": audio_only, "subtitles": subtitles, "single": single}
    job_id = jobs.enqueue("url", url, options=opts)
    return f"Queued ingest job {job_id} for {url}. Poll media_job_status('{job_id}'). Playlists expand into child jobs."


@tool
def media_job_status(job_id: str = "") -> str:
    """Check ingest job status. With no id, lists the 15 most recent jobs.

    Args:
        job_id: A specific job id, or empty for the recent list.
    Returns a status summary, or a readable error.
    """
    if job_id.strip():
        j = jobs.get(job_id.strip())
        if not j:
            return f"No job {job_id}."
        return (f"{j['id']} [{j['status']}] {round(j['progress'])}% — "
                f"{j['title'] or j['source']}\n  {j['message']}\n  → {j['output_path'] or '(pending)'}")
    rows = jobs.recent(15)
    if not rows:
        return "No ingest jobs yet."
    lines = [f"{r['id']} [{r['status']:9}] {round(r['progress']):3}% {r['title'] or r['source']}" for r in rows]
    return "\n".join(lines)


@tool
def media_probe(url: str) -> str:
    """Inspect a URL without downloading — is it a playlist, how many items, titles.

    Args:
        url: A YouTube/playlist/channel/direct URL.
    Returns a summary of what an ingest would pull, or a readable error.
    """
    url = (url or "").strip()
    if not url.startswith(("http://", "https://")):
        return "Error: provide an http(s) URL."
    try:
        p = ingest.probe_url(url)
    except Exception as exc:  # noqa: BLE001
        return f"Error: {type(exc).__name__}: {exc}"
    if p["is_playlist"]:
        sample = "; ".join(e["title"] for e in p["entries"][:5] if e.get("title"))
        return f"Playlist '{p['title']}' — {len(p['entries'])} item(s). First: {sample}"
    return f"Single video: {p['title'] or url}"


def get_tools() -> list:
    return [media_ingest, media_job_status, media_probe]
