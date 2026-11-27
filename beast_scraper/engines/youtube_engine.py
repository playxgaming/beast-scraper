"""YouTube metadata, search, and transcript extraction using yt-dlp and youtube-transcript-api."""

import asyncio
import re
from typing import Any
from urllib.parse import parse_qs, urlparse

import yt_dlp
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api.formatters import SRTFormatter, TextFormatter

from beast_scraper.core.proxy_manager import ProxyManager

YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be", "www.youtu.be"}


def validate_youtube_url(value: str) -> str:
    raw = value.strip()
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", raw):
        return f"https://www.youtube.com/watch?v={raw}"
    parsed = urlparse(raw)
    if (
        parsed.scheme not in {"https", "http"}
        or parsed.hostname not in YOUTUBE_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in {None, 80, 443}
    ):
        raise ValueError("A YouTube URL or 11-character video ID is required.")
    if parsed.path != "/playlist":
        extract_video_id(raw)
    elif not parse_qs(parsed.query).get("list"):
        raise ValueError("A playlist ID is required.")
    return parsed._replace(scheme="https").geturl()


def extract_video_id(url_or_id: str) -> str:
    raw = url_or_id.strip()
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", raw):
        return raw
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in YOUTUBE_HOSTS or parsed.username is not None:
        raise ValueError("A YouTube video URL or 11-character ID is required.")
    parts = parsed.path.strip("/").split("/")
    candidate = (
        parts[0]
        if parsed.hostname in {"youtu.be", "www.youtu.be"}
        else parts[1]
        if len(parts) == 2 and parts[0] in {"shorts", "embed", "live"}
        else parse_qs(parsed.query).get("v", [""])[0]
        if parsed.path == "/watch"
        else ""
    )
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", candidate):
        raise ValueError("A YouTube video URL or 11-character ID is required.")
    return candidate


def _format_ts(seconds: float) -> str:
    total = int(seconds)
    hrs, rem = divmod(total, 3600)
    mins, secs = divmod(rem, 60)
    if hrs:
        return f"{hrs:02d}:{mins:02d}:{secs:02d}"
    return f"{mins:02d}:{secs:02d}"


class YouTubeEngine:
    """Scrapes YouTube metadata, search results, and captions without downloading video files."""

    def __init__(self, proxy_manager: ProxyManager) -> None:
        self.proxy_manager = proxy_manager

    async def info(self, url_or_id: str, country: str | None = None) -> dict[str, Any]:
        proxy_url = self.proxy_manager.acquire(country=country)
        target = validate_youtube_url(url_or_id)

        opts: dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "extract_flat": "in_playlist",
            "playlistend": 50,
            "socket_timeout": 20,
            "retries": 1,
            "extractor_retries": 1,
        }
        if proxy_url:
            opts["proxy"] = proxy_url

        def _run() -> dict[str, Any]:
            with yt_dlp.YoutubeDL(opts) as ydl:
                data = ydl.extract_info(target, download=False)
                if data.get("_type") == "playlist":
                    return {
                        "type": "playlist",
                        "id": data.get("id"),
                        "title": data.get("title"),
                        "channel": data.get("channel") or data.get("uploader"),
                        "entries_count": len(data.get("entries") or []),
                        "entries": [
                            {
                                "id": e.get("id"),
                                "title": e.get("title"),
                                "url": e.get("url") or f"https://www.youtube.com/watch?v={e.get('id')}",
                                "duration": e.get("duration"),
                            }
                            for e in (data.get("entries") or [])[:50]
                        ],
                    }
                return {
                    "type": "video",
                    "id": data.get("id"),
                    "title": data.get("title"),
                    "channel": data.get("channel") or data.get("uploader"),
                    "channel_url": data.get("channel_url"),
                    "duration": data.get("duration"),
                    "view_count": data.get("view_count"),
                    "like_count": data.get("like_count"),
                    "upload_date": data.get("upload_date"),
                    "thumbnail": data.get("thumbnail"),
                    "description": data.get("description"),
                    "tags": data.get("tags") or [],
                    "categories": data.get("categories") or [],
                    "chapters": data.get("chapters") or [],
                    "webpage_url": data.get("webpage_url"),
                }

        return await asyncio.to_thread(_run)

    async def search(self, query: str, limit: int = 10, country: str | None = None) -> dict[str, Any]:
        proxy_url = self.proxy_manager.acquire(country=country)
        opts: dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "extract_flat": True,
            "socket_timeout": 20,
            "retries": 1,
            "extractor_retries": 1,
        }
        if proxy_url:
            opts["proxy"] = proxy_url

        def _run() -> list[dict[str, Any]]:
            with yt_dlp.YoutubeDL(opts) as ydl:
                data = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
                return [
                    {
                        "id": e.get("id"),
                        "title": e.get("title"),
                        "channel": e.get("channel") or e.get("uploader"),
                        "duration": e.get("duration"),
                        "view_count": e.get("view_count"),
                        "url": f"https://www.youtube.com/watch?v={e.get('id')}",
                    }
                    for e in (data.get("entries") or [])
                ]

        videos = await asyncio.to_thread(_run)
        return {"query": query, "count": len(videos), "videos": videos}

    async def transcript(
        self,
        url_or_id: str,
        languages: list[str] | None = None,
        fmt: str = "json",
    ) -> dict[str, Any]:
        vid = extract_video_id(url_or_id)
        langs = languages or ["en"]

        def _run() -> dict[str, Any]:
            ytt = YouTubeTranscriptApi()
            available = [
                {
                    "language": t.language,
                    "language_code": t.language_code,
                    "is_generated": t.is_generated,
                }
                for t in ytt.list(vid)
            ]
            fetched = ytt.fetch(vid, languages=langs)
            segments = [{"start": s.start, "duration": s.duration, "text": s.text} for s in fetched]
            plain_text = TextFormatter().format_transcript(fetched)
            if fmt == "srt":
                formatted = SRTFormatter().format_transcript(fetched)
            elif fmt == "markdown":
                formatted = "\n".join(f"- `[{_format_ts(s['start'])}]` {s['text']}" for s in segments)
            elif fmt == "text":
                formatted = plain_text
            else:
                formatted = None

            return {
                "video_id": vid,
                "url": f"https://www.youtube.com/watch?v={vid}",
                "language": getattr(fetched, "language_code", langs[0]),
                "available_languages": available,
                "segment_count": len(segments),
                "text": plain_text,
                "formatted": formatted,
                "segments": segments if fmt == "json" else None,
            }

        return await asyncio.to_thread(_run)
