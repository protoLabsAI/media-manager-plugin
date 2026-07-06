# media-manager-plugin

A [protoAgent](https://github.com/protoLabsAI/protoAgent) plugin that turns any
agent into a media librarian: paste a **YouTube / direct video / MP4 URL** or
**upload a file**, and it downloads the media, generates **Jellyfin metadata**
(NFO + poster), files it into a library folder, and triggers a **Jellyfin scan**.

Shareable and self-contained — drop it into any protoAgent whose host has
`ffmpeg` + `yt-dlp` and a read-write mount into a Jellyfin library.

## Features

- **Sources:** single YouTube URL, YouTube **playlist/channel** (expands to one
  job per entry), direct video/MP4 links, and **file uploads** (drag-drop).
- **Audio-only extraction** (mp3/m4a/opus/flac) into an audio library.
- **Subtitles** as `.srt` sidecars + embedded **chapter** markers.
- **Jellyfin-ready metadata:** movie `.nfo` (title / plot / studio / year / tags)
  + `poster.jpg`, then a library scan so it appears immediately.
- **Background job queue** (SQLite, survives restarts) with a live status view.

## Surfaces

- **Tools:** `media_ingest(url, audio_only, subtitles, single)`,
  `media_job_status(job_id)`, `media_probe(url)`.
- **View:** a **Media Manager** console (URL box + options, drag-drop upload,
  live ingest queue) at `/plugins/media_manager/view`.

## Config (`media_manager:` section)

| Key | Default | Notes |
|---|---|---|
| `library_path` | `/library/movies` | Container mount → a Jellyfin video library folder |
| `audio_path` | `/library/music` | For audio-only extractions |
| `jellyfin_url` | `http://jellyfin:8096` | Jellyfin base URL |
| `jellyfin_api_key` | *(secret)* | Triggers a scan post-ingest; empty → scheduled scan only. Env: `JELLYFIN_API_KEY` |
| `subtitle_langs` | `[en]` | `.srt` languages to fetch |
| `audio_format` | `mp3` | Audio-only codec |
| `allow_playlist` / `max_playlist_items` | `true` / `50` | Playlist expansion cap |

## Requirements

- `ffmpeg` + `ffprobe` on PATH (yt-dlp shells out for merge/transcode/thumbnails).
- `yt-dlp` importable (`requires_pip`; install with `python -m server plugin install-deps media_manager`).
- A read-write bind mount into the target Jellyfin library, and the plugin on the
  same Docker network as Jellyfin.

## Enable

```yaml
plugins:
  enabled: [media_manager]
media_manager:
  library_path: /library/movies
  jellyfin_url: http://jellyfin:8096
```

First deployed inside **Frank** (`protoLabsAI/frank`) — see
`homelab-iac/stacks/frank`.
