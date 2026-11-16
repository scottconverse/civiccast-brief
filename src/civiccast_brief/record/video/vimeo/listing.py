"""List a public Vimeo account over RSS, then resolve each video's media.

The RSS feed supplies the public video id, title, publication time, duration
and player URL with its unlisted hash. yt-dlp is tried first for media because
it already knows Vimeo's current formats. When that fails, the public player
page is read and its ``window.playerConfig`` JSON is inspected for progressive
MP4, HLS and text tracks. No login and no cookies are used.
"""

from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as ET
from collections.abc import Iterator, Mapping
from datetime import date, datetime
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import parse_qs, urljoin, urlsplit

import httpx

from civiccast_brief.record import proc
from civiccast_brief.record.video.youtube.listing import (
    SOURCE_VIMEO_PLAYER,
    SOURCE_VIMEO_RSS,
    SOURCE_VIMEO_YTDLP,
    VideoError,
    VideoListing,
)
from civiccast_brief.record.video.youtube.ytdlp import (
    YTDLP_MODULE,
    YTDLP_TIMEOUT_S,
    Runner,
)

DEFAULT_ACCOUNT = "svvsd"
VIMEO_RSS_URL = "https://vimeo.com/{account}/videos/rss"
VIMEO_PLAYER_URL = "https://player.vimeo.com/video/{video_id}"
PLAYER_CONFIG_MARKER = "window.playerConfig = "
MEDIA_NS = "http://search.yahoo.com/mrss/"


def vimeo_rss_url(account: str = DEFAULT_ACCOUNT) -> str:
    return VIMEO_RSS_URL.format(account=account.strip())


def vimeo_player_url(video_id: str, privacy_hash: str = "") -> str:
    url = VIMEO_PLAYER_URL.format(video_id=video_id)
    return f"{url}?h={privacy_hash}" if privacy_hash else url


def vimeo_ytdlp_argv(interpreter: str, watch_url: str) -> list[str]:
    """The exact yt-dlp media-information command, with no cookies or login."""
    return [
        interpreter,
        "-m",
        YTDLP_MODULE,
        "--no-playlist",
        "--dump-single-json",
        "--no-warnings",
        watch_url,
    ]


def _published(text: str) -> datetime | None:
    try:
        return parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None


def _published_day(text: str) -> date | None:
    published = _published(text)
    return published.date() if published is not None else None


def _video_id(link: str) -> str:
    path = urlsplit(link).path.rstrip("/")
    return path.rsplit("/", 1)[-1] if path else ""


def _privacy_hash(player_url: str) -> str:
    return (parse_qs(urlsplit(player_url).query).get("h") or [""])[0]


def _duration_seconds(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    seconds = int(value)
    return seconds if seconds > 0 else None


def _subtitle_url(info: Mapping[str, Any]) -> str | None:
    candidates: list[tuple[int, int, int, str]] = []
    for automatic, group in ((1, "automatic_captions"), (0, "subtitles")):
        tracks = info.get(group)
        if not isinstance(tracks, Mapping):
            continue
        for language, entries in tracks.items():
            if not isinstance(entries, list):
                continue
            language_rank = 0 if str(language).casefold().startswith("en") else 1
            for entry in entries:
                if not isinstance(entry, Mapping):
                    continue
                url = str(entry.get("url") or "").strip()
                if not url:
                    continue
                ext = str(entry.get("ext") or "").casefold()
                format_rank = 0 if ext == "vtt" else 1
                candidates.append((automatic, language_rank, format_rank, url))
    if not candidates:
        return None
    return min(candidates, key=lambda item: (item[0], item[1], item[2]))[3]


def media_from_info(info: Mapping[str, Any]) -> tuple[str | None, str | None, int | None]:
    """Read the preferred media URL, VTT/CAPTION URL and duration from yt-dlp JSON."""
    if not isinstance(info, Mapping):
        raise VideoError("yt-dlp's Vimeo answer was not a JSON object")
    duration = _duration_seconds(info.get("duration"))
    media_url = ""
    direct = str(info.get("url") or "").strip()
    ext = str(info.get("ext") or "").casefold()
    if direct and (ext == "mp4" or urlsplit(direct).path.casefold().endswith(".mp4")):
        media_url = direct

    formats = info.get("formats")
    candidates: list[tuple[int, str]] = []
    for entry in formats if isinstance(formats, list) else []:
        if not isinstance(entry, Mapping):
            continue
        url = str(entry.get("url") or "").strip()
        if not url:
            continue
        protocol = str(entry.get("protocol") or "").casefold()
        mime = str(entry.get("mime_type") or entry.get("mime") or "").casefold()
        entry_ext = str(entry.get("ext") or "").casefold()
        if protocol not in ("http", "https") or str(entry.get("vcodec") or "") == "none":
            continue
        if entry_ext == "mp4" or "video/mp4" in mime:
            height = entry.get("height")
            rank = int(height) if isinstance(height, int) and not isinstance(height, bool) else 0
            candidates.append((rank, url))
        elif not media_url and ("mpegurl" in mime or entry_ext in ("m3u8", "m3u")):
            media_url = url
    if candidates:
        media_url = max(candidates, key=lambda item: item[0])[1]
    return media_url or None, _subtitle_url(info), duration


def player_config_from_html(html: str) -> dict[str, Any]:
    """Decode the inline ``window.playerConfig`` object from a Vimeo player page."""
    at = html.find(PLAYER_CONFIG_MARKER)
    if at < 0:
        raise VideoError("the Vimeo player page held no playerConfig JSON")
    start = at + len(PLAYER_CONFIG_MARKER)
    try:
        payload, _ = json.JSONDecoder().raw_decode(html[start:])
    except ValueError as exc:
        raise VideoError(f"the Vimeo playerConfig JSON could not be read: {exc}") from exc
    if not isinstance(payload, dict):
        raise VideoError("the Vimeo playerConfig was not a JSON object")
    return payload


def _progressive_url(files: Mapping[str, Any]) -> str | None:
    progressive = files.get("progressive")
    entries = progressive if isinstance(progressive, list) else [progressive]
    candidates: list[tuple[int, str]] = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        url = str(entry.get("url") or "").strip()
        mime = str(entry.get("mime") or entry.get("type") or "").casefold()
        if not url or not (mime == "video/mp4" or urlsplit(url).path.casefold().endswith(".mp4")):
            continue
        height = entry.get("height")
        rank = int(height) if isinstance(height, int) and not isinstance(height, bool) else 0
        candidates.append((rank, url))
    return max(candidates, key=lambda item: item[0])[1] if candidates else None


def _hls_url(files: Mapping[str, Any]) -> str | None:
    hls = files.get("hls")
    if not isinstance(hls, Mapping):
        return None
    direct = str(hls.get("url") or hls.get("avc_url") or "").strip()
    if direct:
        return direct
    cdns = hls.get("cdns")
    if not isinstance(cdns, Mapping):
        return None
    default = str(hls.get("default_cdn") or "")
    chosen = cdns.get(default) if default else None
    if not isinstance(chosen, Mapping):
        chosen = next((value for value in cdns.values() if isinstance(value, Mapping)), None)
    if not isinstance(chosen, Mapping):
        return None
    return str(chosen.get("url") or chosen.get("avc_url") or "").strip() or None


def _track_lists(node: Any) -> Iterator[list[Any]]:
    if isinstance(node, Mapping):
        for key, value in node.items():
            if str(key).casefold() in ("text_tracks", "texttracks") and isinstance(value, list):
                yield value
            yield from _track_lists(value)
    elif isinstance(node, list):
        for value in node:
            yield from _track_lists(value)


def _text_track_url(config: Mapping[str, Any]) -> str | None:
    candidates: list[tuple[int, int, int, str]] = []
    for tracks in _track_lists(config):
        for track in tracks:
            if not isinstance(track, Mapping):
                continue
            url = str(track.get("url") or "").strip()
            if not url:
                continue
            language = str(track.get("lang") or track.get("language") or "").casefold()
            kind = str(track.get("kind") or "").casefold()
            format_rank = 0 if urlsplit(url).path.casefold().endswith(".vtt") else 1
            language_rank = 0 if language.startswith("en") else 1
            kind_rank = 0 if kind in ("captions", "subtitles") else 1
            candidates.append((language_rank, kind_rank, format_rank, url))
    return min(candidates)[3] if candidates else None


def media_from_player_config(
    config: Mapping[str, Any],
) -> tuple[str | None, str | None, int | None]:
    """Read progressive MP4, HLS and text-track URLs from player config JSON."""
    request = config.get("request")
    files = request.get("files") if isinstance(request, Mapping) else None
    files = files if isinstance(files, Mapping) else {}
    media_url = _progressive_url(files) or _hls_url(files)
    captions_url = _text_track_url(config)
    video = config.get("video")
    duration = _duration_seconds(video.get("duration")) if isinstance(video, Mapping) else None
    return media_url, captions_url, duration


class VimeoLister:
    """Reads one public Vimeo account over a caller-owned HTTP client and process runner."""

    def __init__(
        self,
        client: httpx.Client,
        account: str = DEFAULT_ACCOUNT,
        *,
        runner: Runner = proc.run,
        interpreter: str | None = None,
        timeout_s: float = YTDLP_TIMEOUT_S,
    ) -> None:
        self.client = client
        self.account = account.strip()
        self.runner = runner
        self.interpreter = interpreter or sys.executable
        self.timeout_s = timeout_s

    @property
    def feed_url(self) -> str:
        return vimeo_rss_url(self.account)

    def parse_feed(self, body: bytes) -> list[VideoListing]:
        """Parse Vimeo RSS into the shared listing shape, newest item first."""
        try:
            root = ET.fromstring(body)
        except ET.ParseError as exc:
            raise VideoError(f"the Vimeo RSS feed is not XML this reader can read: {exc}") from exc
        videos: list[VideoListing] = []
        for item in root.findall(".//item"):
            link = (item.findtext("link") or "").strip()
            video_id = _video_id(link)
            if not video_id:
                continue
            player = item.find(f"{{{MEDIA_NS}}}content/{{{MEDIA_NS}}}player")
            player_url = player.get("url", "").strip() if player is not None else ""
            content = item.find(f"{{{MEDIA_NS}}}content")
            duration = _duration_seconds(content.get("duration")) if content is not None else None
            videos.append(
                VideoListing(
                    video_id=video_id,
                    title=(item.findtext("title") or "").strip(),
                    published=(item.findtext("pubDate") or "").strip() or None,
                    channel_id=self.account,
                    source_method=SOURCE_VIMEO_RSS,
                    duration_s=duration,
                    media_url=player_url or None,
                )
            )
        return videos

    def _ytdlp_media(self, video: VideoListing) -> tuple[str, str | None, int | None]:
        watch_url = f"https://vimeo.com/{video.video_id}"
        argv = vimeo_ytdlp_argv(self.interpreter, watch_url)
        try:
            result = self.runner(argv, timeout_s=self.timeout_s, env=proc.allowed_environment())
        except (proc.ProcessTimedOut, proc.ProcessFailed, OSError) as exc:
            raise VideoError(f"yt-dlp failed for Vimeo {video.video_id}: {exc}") from exc
        if result.returncode != 0:
            detail = result.last_stderr_line() or "no reason given"
            raise VideoError(f"yt-dlp exited {result.returncode} for Vimeo {video.video_id}: {detail}")
        try:
            info = json.loads(result.stdout.decode("utf-8", errors="replace"))
        except ValueError as exc:
            raise VideoError(f"yt-dlp's Vimeo answer was not JSON: {exc}") from exc
        media_url, captions_url, duration = media_from_info(info)
        if media_url is None:
            raise VideoError(f"yt-dlp found no media URL for Vimeo {video.video_id}")
        return media_url, captions_url, duration

    def _player_config(self, player_url: str) -> dict[str, Any]:
        try:
            response = self.client.get(player_url)
        except httpx.HTTPError as exc:
            raise VideoError(f"the Vimeo player page could not be reached: {exc}") from exc
        if response.status_code != 200:
            raise VideoError(
                f"the Vimeo player page answered HTTP {response.status_code} for {player_url}"
            )
        return player_config_from_html(response.text)

    def _player_media(self, video: VideoListing) -> tuple[str, str | None, int | None]:
        player_url = video.media_url or vimeo_player_url(video.video_id)
        config = self._player_config(player_url)
        media_url, captions_url, duration = media_from_player_config(config)
        request = config.get("request")
        refresh_url = (
            str(request.get("config_refresh_url") or "").strip()
            if isinstance(request, Mapping)
            else ""
        )
        if refresh_url and (media_url is None or captions_url is None):
            try:
                response = self.client.get(urljoin(player_url, refresh_url))
            except httpx.HTTPError as exc:
                raise VideoError(f"the Vimeo config refresh could not be reached: {exc}") from exc
            if response.status_code == 200:
                try:
                    refreshed = response.json()
                except ValueError:
                    refreshed = None
                if isinstance(refreshed, Mapping):
                    more_media, more_captions, more_duration = media_from_player_config(
                        {"request": refreshed, "video": config.get("video")}
                    )
                    media_url = media_url or more_media
                    captions_url = captions_url or more_captions
                    duration = duration or more_duration
        if media_url is None:
            raise VideoError(f"the Vimeo player config held no media URL for {video.video_id}")
        return media_url, captions_url, duration

    def list_videos(self, first: date, last: date) -> list[VideoListing]:
        """Vimeo items published in the inclusive date range, with media resolved."""
        if first > last:
            raise ValueError("the first date must be on or before the last date")
        try:
            response = self.client.get(self.feed_url)
        except httpx.HTTPError as exc:
            raise VideoError(f"the Vimeo RSS feed could not be reached: {exc}") from exc
        if response.status_code != 200:
            raise VideoError(
                f"the Vimeo RSS feed answered HTTP {response.status_code} for {self.feed_url}"
            )
        videos = [
            video
            for video in self.parse_feed(response.content)
            if (day := _published_day(video.published or "")) is not None
            and first <= day <= last
        ]
        resolved: list[VideoListing] = []
        problems: list[str] = []
        for video in videos:
            source_method = SOURCE_VIMEO_YTDLP
            try:
                media_url, captions_url, duration = self._ytdlp_media(video)
            except VideoError as ytdlp_error:
                source_method = SOURCE_VIMEO_PLAYER
                try:
                    media_url, captions_url, duration = self._player_media(video)
                except VideoError as player_error:
                    problems.append(f"{video.video_id}: {ytdlp_error}; {player_error}")
                    continue
            resolved.append(
                VideoListing(
                    video_id=video.video_id,
                    title=video.title,
                    published=video.published,
                    channel_id=self.account,
                    source_method=source_method,
                    duration_s=duration or video.duration_s,
                    live_status="finished",
                    media_url=media_url,
                    captions_url=captions_url,
                )
            )
        if videos and not resolved:
            raise VideoError("no Vimeo video's media could be resolved: " + "; ".join(problems))
        return resolved


def vimeo_lister(
    client: httpx.Client,
    account: str = DEFAULT_ACCOUNT,
    *,
    runner: Runner = proc.run,
    interpreter: str | None = None,
) -> VimeoLister:
    """The lister for a real run."""
    return VimeoLister(client, account=account, runner=runner, interpreter=interpreter)
