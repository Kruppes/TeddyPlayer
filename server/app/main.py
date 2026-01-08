import asyncio
import logging
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime
import time
from pathlib import Path

import os
from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from urllib.parse import quote  # Added this import

from .services.transcoding import (
    transcode_stream,
    get_content_type,
    check_ffmpeg,
    get_or_serve_cached_mp3,  # Returns multi-track concat or legacy cache
    get_cache_stats,
    clear_cache,
    get_encoding_status,
    # Multi-track support (all encoding uses this now)
    get_or_encode_tracks,
    get_cached_tracks,
    get_tonie_cache_dir,
    get_track_cache_path,
    set_encoding_status,
    get_tonie_cache_key,
    # Progressive encoding - first track then background
    encode_first_track,
    continue_encoding_remaining_tracks,
)

from .config import (
    get_settings,
    get_editable_settings,
    update_settings,
    get_local_ip,
    get_preferences,
    update_preferences,
)
from .services.teddycloud import TeddyCloudClient
from .services import devices as device_service
import re
import unicodedata

# Feature flags from environment variables
ESPUINO_ENABLED = os.environ.get("ESPUINO_ENABLED", "false").lower() in (
    "true",
    "1",
    "yes",
)


def sanitize_filename(name: str, max_length: int = 50) -> str:
    """Make a string safe for use as a filename on any filesystem."""
    if not name:
        return "unknown"
    # Normalize unicode and remove accents
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    # Replace unsafe characters with underscore
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    # Replace spaces and multiple underscores
    name = re.sub(r"\s+", "_", name)
    name = re.sub(r"_+", "_", name)
    # Remove leading/trailing underscores and dots
    name = name.strip("_.")
    # Truncate to max length
    if len(name) > max_length:
        name = name[:max_length].rstrip("_")
    return name or "unknown"


def _is_virtual_reader(reader_ip: str) -> bool:
    """Check if reader_ip is a virtual/web-based reader (not a physical ESPuino)."""
    return reader_ip in ("manual-stream", "browser-session") or reader_ip.startswith(
        "web-"
    )


def build_espuino_dest_path(
    uid: str, series: str, episode: str, track_index: int = None, track_name: str = None
) -> tuple[str, str]:
    """
    Build human-readable destination path for ESPuino SD card.

    Returns (folder_path, file_path) tuple.
    Format: /teddycloud/{series}_{episode}/{track_num}_{track_name}.mp3
    """
    folder_name = sanitize_filename(
        f"{series}_{episode}" if series and episode else series or episode or "unknown"
    )
    folder_path = f"/teddycloud/{folder_name}"

    if track_index is not None:
        track_num = f"{track_index + 1:02d}"
        if track_name:
            file_name = f"{track_num}_{sanitize_filename(track_name, 40)}.mp3"
        else:
            file_name = f"{track_num}.mp3"
        return folder_path, f"{folder_path}/{file_name}"
    else:
        # Single file (legacy)
        return folder_path, f"{folder_path}/full.mp3"


def _uid_suffix_from_uid(uid: str) -> str:
    """Return ESPuino UID suffix (last 4 bytes) like 0E-F4-BA-91."""
    if not uid:
        return ""
    raw = uid.upper()
    if ":" in raw:
        parts = [p for p in raw.split(":") if p]
        if len(parts) >= 4:
            return "-".join(parts[-4:])
    # Fallback: strip non-hex and take last 8 chars
    hex_only = re.sub(r"[^0-9A-F]", "", raw)
    if len(hex_only) >= 8:
        tail = hex_only[-8:]
        return "-".join([tail[i : i + 2] for i in range(0, 8, 2)])
    return ""


def build_espuino_uid_map_path(uid: str) -> str:
    """Build UID mapping path for ESPuino SD card (suffix-based)."""
    suffix = _uid_suffix_from_uid(uid)
    safe_uid = suffix if suffix else (uid or "unknown").upper().replace(":", "-")
    return f"/teddycloud/uids/{safe_uid}.json"


def uid_to_espuino_tag_id(uid: str) -> str:
    """Convert UID (e.g., 0E:F4:D7:AC) to ESPuino decimal triplet tag ID."""
    if not uid:
        return ""
    raw = uid.upper()
    if ":" in raw:
        parts = [p for p in raw.split(":") if p]
        if len(parts) >= 4:
            # ESPuino tag id uses reversed byte order in decimal triplets
            parts = parts[-4:][::-1]
        else:
            return ""
    else:
        hex_only = re.sub(r"[^0-9A-F]", "", raw)
        if len(hex_only) < 8:
            return ""
        parts = [
            hex_only[i : i + 2] for i in range(len(hex_only) - 8, len(hex_only), 2)
        ][::-1]
    try:
        return "".join([f"{int(p, 16):03d}" for p in parts])
    except ValueError:
        return ""


async def check_espuino_active_tag(ip: str, expected_uid: str) -> bool | None:
    """Check if ESPuino is actively playing the expected tag.

    Pings ESPuino /settings endpoint and compares rfidTagId.

    Returns:
        True: Tag matches (still playing our content)
        False: Different tag or no tag active
        None: ESPuino unreachable (network error)
    """
    if not ip or not expected_uid:
        return None

    expected_tag_id = uid_to_espuino_tag_id(expected_uid)
    if not expected_tag_id:
        logger.warning(f"Could not convert UID {expected_uid} to ESPuino tag ID")
        return None

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"http://{ip}/settings", timeout=aiohttp.ClientTimeout(total=5)
            ) as resp:
                if resp.status != 200:
                    logger.debug(f"ESPuino {ip} /settings returned {resp.status}")
                    return None

                data = await resp.json()
                current = data.get("current", {})
                active_tag_id = current.get("rfidTagId", "")

                if not active_tag_id:
                    # No tag currently active
                    return False

                # Compare tag IDs
                matches = active_tag_id == expected_tag_id
                if not matches:
                    logger.debug(
                        f"ESPuino {ip} tag mismatch: active={active_tag_id}, expected={expected_tag_id}"
                    )
                return matches

    except asyncio.TimeoutError:
        logger.debug(f"ESPuino {ip} /settings timeout")
        return None
    except aiohttp.ClientError as e:
        logger.debug(f"ESPuino {ip} /settings error: {e}")
        return None
    except Exception as e:
        logger.warning(f"ESPuino {ip} /settings unexpected error: {e}")
        return None


# Background task handle for smart ping
_smart_ping_task: asyncio.Task | None = None


async def smart_ping_espuino_readers():
    """Background task to check if ESPuino readers are still playing tracked content.

    Runs every 60 seconds. For each ESPuino with an active stream:
    - Ping /settings to get current rfidTagId
    - If tag matches our tracked UID → update last_seen (keep stream alive)
    - If mismatch or offline → leave for stale check to clean up
    """
    while True:
        try:
            await asyncio.sleep(60)  # Run every 60 seconds

            for ip, state in list(reader_states.items()):
                current = state.get("current_tag")
                if not current:
                    continue

                # Skip virtual/web readers
                if _is_virtual_reader(ip):
                    continue

                # Only check ESPuino devices
                device = state.get(
                    "current_device"
                ) or device_service.get_device_for_reader(ip)
                if device.get("type") != "espuino":
                    continue

                # Check if ESPuino is still playing our tag
                uid = current.get("uid", "")
                if not uid:
                    continue

                result = await check_espuino_active_tag(ip, uid)

                if result is True:
                    # Tag matches - update last_seen to keep stream alive
                    if ip in connected_readers:
                        connected_readers[ip]["last_seen"] = datetime.now().isoformat()
                        logger.debug(
                            f"Smart ping: ESPuino {ip} still playing {uid[:16]}..."
                        )
                elif result is False:
                    # Different tag or no tag - let stale check handle cleanup
                    logger.info(
                        f"Smart ping: ESPuino {ip} no longer playing {uid[:16]}..."
                    )
                # result is None (unreachable) - don't update, let stale check decide

        except asyncio.CancelledError:
            logger.info("Smart ping task cancelled")
            break
        except Exception as e:
            logger.error(f"Smart ping error: {e}")
            # Continue running despite errors


def build_upload_metadata(
    uid: str, series: str, episode: str, tracks: list[dict], audio_url: str
) -> dict:
    """
    Build metadata for ESPuino upload folder.

    This metadata.json file allows the system to:
    - Match folders to Tonie UIDs
    - Verify all tracks are present
    - Check file integrity via sizes/hashes
    """
    import hashlib

    track_files = []
    for i, track in enumerate(tracks):
        track_name = track.get("name", f"Track {i + 1}")
        _, file_path = build_espuino_dest_path(uid, series, episode, i, track_name)
        cache_path = get_track_cache_path(audio_url, i)
        size = cache_path.stat().st_size if cache_path.exists() else 0
        track_files.append(
            {
                "index": i,
                "name": track_name,
                "file": file_path.split("/")[-1],  # Just the filename
                "duration": track.get("duration", 0),
                "size": size,
            }
        )

    return {
        "uid": uid,
        "series": series,
        "episode": episode,
        "title": f"{series} - {episode}"
        if series and episode
        else series or episode or "Unknown",
        "audio_url": audio_url,
        "tracks": track_files,
        "total_tracks": len(tracks),
        "uploaded_at": datetime.now().isoformat(),
    }


# Custom log handler to capture recent logs
class LogCapture(logging.Handler):
    def __init__(self, maxlen=100):
        super().__init__()
        self.logs = deque(maxlen=maxlen)

    def emit(self, record):
        self.logs.append(
            {
                "time": datetime.fromtimestamp(record.created).isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "message": self.format(record),
            }
        )


log_capture = LogCapture(maxlen=100)
log_capture.setFormatter(logging.Formatter("%(message)s"))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Add capture handler to root logger
logging.getLogger().addHandler(log_capture)

# Global client instance
teddycloud_client: TeddyCloudClient | None = None

# Track connected readers and recent scans
connected_readers: dict[str, dict] = {}  # ip -> {last_seen, name, scan_count}
recent_scans: deque = deque(maxlen=50)  # Store last 50 scans

# Per-reader playback state
reader_states: dict[str, dict] = {}  # ip -> state dict


def get_reader_state(reader_ip: str) -> dict:
    """Get or initialize the playback state for a reader."""
    if reader_ip not in reader_states:
        reader_states[reader_ip] = {
            "current_tag": None,
            "current_started_at": None,
            "current_offset": 0.0,
            "resume": None,  # {"uid": str, "position": float}
            "last_reported_position": 0.0,
            "current_device": None,
        }
    return reader_states[reader_ip]


def build_audio_url(tonie: dict | None, uid: str, settings) -> str:
    """Build the source audio URL from TeddyCloud data."""
    from urllib.parse import quote

    tc_base = settings.teddycloud.url.rstrip("/")
    if tc_base.endswith("/web"):
        tc_base = tc_base[:-4]

    source = tonie.get("source", "") if tonie else ""

    if source.startswith("lib://"):
        lib_path = source[6:]
        # URL-encode path (preserve slashes) and use /content/ for proper OGG conversion
        encoded_path = quote(lib_path, safe="/")
        return f"{tc_base}/content/{encoded_path}?ogg=true&special=library"
    if tonie and tonie.get("audio_path"):
        return f"{tc_base}{tonie['audio_path']}"
    return teddycloud_client.get_audio_url(uid) if teddycloud_client else ""


def build_playback_url(audio_url: str, device_type: str, settings) -> str:
    """Build the URL used for playback.

    All devices use MP3 (CBR 192kbps, ~30s encoding) for best compatibility
    and stable streaming.
    """
    from urllib.parse import quote

    # Browser playback needs relative URL to avoid mixed content errors (HTTP IP on HTTPS site)
    if device_type == "browser":
        return f"/transcode.mp3?url={quote(audio_url)}"

    if settings.server_url:
        server_base = settings.server_url.rstrip("/")
    else:
        server_ip = get_local_ip()
        server_base = f"http://{server_ip}:8754"

    # All devices use MP3 for best compatibility and seeking support
    return f"{server_base}/transcode.mp3?url={quote(audio_url)}"


def build_cover_url(picture: str, settings) -> str:
    """Build absolute cover URL from TeddyCloud picture path."""
    if not picture:
        return ""
    if picture.startswith("http://") or picture.startswith("https://"):
        return picture
    tc_base = settings.teddycloud.internal_url or settings.teddycloud.url
    tc_base = tc_base.rstrip("/")
    if tc_base.endswith("/web"):
        tc_base = tc_base[:-4]
    if picture.startswith("/"):
        return f"{tc_base}{picture}"
    return f"{tc_base}/{picture}"


def build_playlist_url(audio_url: str, settings) -> str | None:
    """Build M3U playlist URL for multi-track playback.

    Returns M3U URL if multi-track cache exists, None otherwise.
    ESPuino can use this with LOCAL_M3U mode for track skipping support.
    """
    metadata = get_cached_tracks(audio_url)
    if not metadata:
        return None
    tracks = (
        metadata.tracks if hasattr(metadata, "tracks") else metadata.get("tracks", [])
    )
    if len(tracks) <= 1:
        return None

    if settings.server_url:
        server_base = settings.server_url.rstrip("/")
    else:
        server_ip = get_local_ip()
        server_base = f"http://{server_ip}:8754"

    cache_key = get_tonie_cache_key(audio_url)
    return f"{server_base}/playlist/{cache_key}.m3u"


def build_track_urls(audio_url: str, settings, absolute: bool = True) -> list[str]:
    """Build URLs for all tracks of a cached Tonie.

    Returns list of track URLs if multi-track is cached, empty list otherwise.
    """
    metadata = get_cached_tracks(audio_url)
    if not metadata:
        return []

    if not absolute:
        # Relative URLs for browser/frontend
        cache_key = get_tonie_cache_key(audio_url)
        return [
            f"/tracks/{cache_key}/{track.index + 1:02d}.mp3"
            for track in metadata.tracks
        ]

    if settings.server_url:
        server_base = settings.server_url.rstrip("/")
    else:
        server_ip = get_local_ip()
        server_base = f"http://{server_ip}:8754"

    cache_key = get_tonie_cache_key(audio_url)
    return [
        f"{server_base}/tracks/{cache_key}/{track.index + 1:02d}.mp3"
        for track in metadata.tracks
    ]


async def get_resume_position(reader_ip: str, device: dict[str, str]) -> float:
    """Compute resume position for a reader/device.

    For browser: Use last_reported_position from JS audio element only
    For other devices: Try device position, then fall back to time-based calc
    """
    state = get_reader_state(reader_ip)

    # Browser playback: only use position reported by the JS audio element
    # Never use wall-clock time calculation for browser (causes false progress)
    if device.get("type") == "browser":
        return max(0.0, float(state.get("last_reported_position", 0)))

    # For non-browser devices, try to get position from the device
    position = await device_service.get_device_position(device)
    if position is not None and position > 0:
        return position

    # Fall back to time-based calculation for devices that don't report position
    if state.get("current_started_at") is not None:
        elapsed = time.time() - state["current_started_at"]
        return max(0.0, state.get("current_offset", 0.0) + elapsed)

    return 0.0


def get_active_stream_count() -> int:
    """Count currently active reader streams."""
    return sum(1 for state in reader_states.values() if state.get("current_tag"))


async def stop_reader_playback(
    reader_ip: str, save_resume: bool = True, pause_only: bool = False
) -> None:
    """Stop playback for a reader and optionally store resume position.

    Args:
        reader_ip: The reader IP address
        save_resume: Whether to save position for resume
        pause_only: If True, pause device and mark as resumable (tag removal).
                   If False, fully stop device (X button).
    """
    state = get_reader_state(reader_ip)
    current = state.get("current_tag")
    if not current:
        return

    device = state.get("current_device") or device_service.get_device_for_reader(
        reader_ip
    )
    if save_resume:
        position = await get_resume_position(reader_ip, device)
        state["resume"] = {
            "uid": current.get("uid", ""),
            "position": position,
            "device": device,
            "paused": pause_only,  # True for tag removal (resumable), False for manual stop
        }

    if pause_only:
        # Tag removed - pause device but keep current_tag so UI shows paused state
        await device_service.pause_device(device)
        logger.info(
            f"Paused playback for reader {reader_ip} on device {device} (tag removed)"
        )
        # Don't clear current_tag - keep Now Playing visible in paused state
    else:
        # Manual stop (X button) - fully stop and clear everything
        state["current_tag"] = None
        state["current_started_at"] = None
        state["current_offset"] = 0.0
        state["last_reported_position"] = 0.0
        state["current_device"] = None
        await device_service.stop_device(device)
        logger.info(f"Stopped playback for reader {reader_ip} on device {device}")


async def play_tonie_for_reader(
    reader_ip: str,
    uid: str,
    device_override: dict[str, str] | None = None,
    record_scan: bool = True,
    skip_sd_upload: bool = False,
    metadata_override: dict[str, str] | None = None,
) -> "TonieResponse":
    """Handle a Tonie playback request for a specific reader."""
    if reader_ip not in connected_readers:
        # Give readers friendly names based on source
        if reader_ip == "manual-stream":
            name = "Web Stream"  # Triggered from web UI (legacy)
        elif reader_ip == "browser-session":
            name = "Browser"  # Browser-based playback
        elif reader_ip.startswith("web-"):
            name = "Web"  # Browser-initiated stream to specific device
        else:
            # Physical tag scan - use ESPuino name if available
            cached = device_service.get_cached_readers().get(reader_ip, {})
            name = cached.get("name") or f"Tag Scan ({reader_ip})"
        connected_readers[reader_ip] = {
            "first_seen": datetime.now().isoformat(),
            "last_seen": datetime.now().isoformat(),
            "scan_count": 0,
            "name": name,
        }
        # Save to persistent cache (skip virtual readers)
        if not _is_virtual_reader(reader_ip):
            device_service.update_reader_cache(
                reader_ip, {"name": name, "scan_count": 0}
            )
    else:
        connected_readers[reader_ip]["last_seen"] = datetime.now().isoformat()
        # Update cache last_seen for real readers
        if not _is_virtual_reader(reader_ip):
            device_service.update_reader_cache(
                reader_ip, {"last_seen": datetime.now().isoformat()}
            )

    state = get_reader_state(reader_ip)

    current = state.get("current_tag")
    resume = state.get("resume")
    if current and current.get("uid") == uid:
        # Same tag re-scanned - check if we should resume (tag was removed and returned)
        if resume and resume.get("uid") == uid and resume.get("paused"):
            # Tag was removed and placed back - resume playback
            device = state.get(
                "current_device"
            ) or device_service.get_device_for_reader(reader_ip)
            resumed = await device_service.resume_device(device)
            if resumed:
                state["resume"] = None  # Clear resume state
                logger.info(f"Resumed playback for {reader_ip} - tag returned")
            return TonieResponse(
                uid=uid,
                series=current.get("series"),
                episode=current.get("episode"),
                title=current.get("title"),
                picture=current.get("picture"),
                found=True,
                playback_started=resumed,
                playback_url=current.get("playback_url"),
            )
        # Same tag, already playing - return current state
        return TonieResponse(
            uid=uid,
            series=current.get("series"),
            episode=current.get("episode"),
            title=current.get("title"),
            picture=current.get("picture"),
            found=True,
            playback_started=False,
            playback_url=current.get(
                "playback_url"
            ),  # Include URL so ESPuino doesn't play "null"
        )

    if current and current.get("uid") != uid:
        await stop_reader_playback(reader_ip, save_resume=False)

    if not teddycloud_client:
        raise HTTPException(status_code=503, detail="TeddyCloud client not initialized")

    tonie = await teddycloud_client.find_tonie_by_uid(uid)
    overrides = metadata_override or {}

    response = TonieResponse(
        uid=uid,
        series=overrides.get("series") or (tonie.get("series") if tonie else None),
        episode=overrides.get("episode")
        or (tonie.get("episodes") or tonie.get("episode") if tonie else None),
        title=overrides.get("title") or (tonie.get("title") if tonie else None),
        picture=overrides.get("picture") or (tonie.get("picture") if tonie else None),
        found=tonie is not None,
        playback_started=False,
    )

    settings = get_settings()
    audio_url = build_audio_url(tonie, uid, settings)

    reader_device = device_override or device_service.get_device_for_reader(reader_ip)
    device_type = reader_device.get("type", "")
    logger.info(
        f"Reader {reader_ip} using device type: {device_type}, id: {reader_device.get('id', 'none')}"
    )
    playback_url = build_playback_url(audio_url, device_type, settings)
    response.playback_url = playback_url  # ESPuino needs this to play the stream

    # Add playlist URL and track count for multi-track support (skip for ESPuino; use single-file stream)
    if device_type != "espuino":
        playlist_url = build_playlist_url(audio_url, settings)
        if playlist_url:
            response.playlist_url = playlist_url
            metadata = get_cached_tracks(audio_url)
            if metadata:
                tracks = (
                    metadata.tracks
                    if hasattr(metadata, "tracks")
                    else metadata.get("tracks", [])
                )
                response.track_count = len(tracks)
            else:
                response.track_count = 1
        else:
            response.track_count = 1
    else:
        response.track_count = 1

    start_position = 0.0
    resume = state.get("resume")
    resume_device = resume.get("device") if resume else None
    should_resume = bool(resume and resume.get("uid") == uid and resume.get("paused"))
    same_device = bool(
        resume_device
        and resume_device.get("type") == reader_device.get("type")
        and resume_device.get("id") == reader_device.get("id")
    )
    if resume and resume.get("uid") == uid:
        start_position = float(resume.get("position", 0.0))
        state["resume"] = None

    if tonie:
        state["current_tag"] = {
            "uid": uid,
            "series": response.series,
            "episode": response.episode,
            "title": response.title,
            "picture": tonie.get("picture"),
            "audio_url": audio_url,
            "playback_url": playback_url,
            "placed_at": datetime.now().isoformat(),
            "start_position": start_position,
            "duration": tonie.get("duration"),
            "tracks": tonie.get("tracks", []),
        }
        state["current_started_at"] = time.time()
        state["current_offset"] = start_position
        state["current_device"] = reader_device
        state["last_reported_position"] = (
            start_position if device_type == "browser" else 0.0
        )

        title = response.title or response.series or "Tonie"

        # Get track info for multi-track encoding
        # Use tracks array which contains start/duration data from trackSeconds
        tonie_tracks = tonie.get("tracks", []) if tonie else []
        num_tracks = tonie.get("num_tracks", 0) if tonie else 0
        duration = tonie.get("duration", 0) if tonie else 0

        # Always use multi-track encoding (no single-file cache)
        # If no track info, create pseudo single-track with full duration
        if not tonie_tracks:
            # Use actual duration, or 7200 (2 hours) as safe maximum
            full_duration = duration if duration > 0 else 7200
            tonie_tracks = [
                {"name": "Full Audio", "duration": full_duration, "start": 0}
            ]
            logger.info(
                f"No track info for {uid[:16]}, using single pseudo-track ({full_duration}s)"
            )

        has_tracks = True  # Always use multi-track path
        logger.info(f"Track detection for {uid[:16]}: {len(tonie_tracks)} track(s)")

        # Update state with actual track list (may have been modified above)
        state["current_tag"]["tracks"] = tonie_tracks
        state["current_tag"]["track_count"] = len(tonie_tracks)

        # Update response track count from TeddyCloud data (not just cached metadata)
        response.track_count = len(tonie_tracks)

        series = tonie.get("series", "") if tonie else ""
        episode = tonie.get("episode", "") if tonie else ""
        cover_url = build_cover_url(tonie.get("picture", ""), settings) if tonie else ""

        # For browser playback, start multi-track encoding in background
        if device_type == "browser":
            if has_tracks:
                # Multi-track encoding
                cache_dir = get_tonie_cache_dir(audio_url)
                metadata_path = cache_dir / "metadata.json"
                if not metadata_path.exists():
                    set_encoding_status(
                        audio_url,
                        "encoding",
                        progress=0,
                        total_tracks=len(tonie_tracks),
                    )

                async def encode_tracks_for_browser():
                    logger.info(
                        f"Starting multi-track encoding for browser: {audio_url[:60]}... ({len(tonie_tracks)} tracks)"
                    )
                    try:
                        metadata = await get_or_encode_tracks(
                            source_url=audio_url,
                            tracks=tonie_tracks,
                            series=series,
                            episode=episode,
                            cover_url=cover_url,
                        )
                        if metadata:
                            logger.info(
                                f"Multi-track encoding complete: {len(metadata.tracks)} tracks for browser"
                            )
                    except Exception as e:
                        logger.error(f"Multi-track encoding failed: {e}")

                asyncio.create_task(encode_tracks_for_browser())

            playback_started = True  # Browser handles actual playback via web UI
        elif device_type in ["sonos", "airplay", "chromecast", "espuino"]:
            # Network devices need pre-encoding, but do it in background to avoid ESP32 timeout
            # ESP32 has 5s HTTP timeout, encoding takes ~40s for new files
            # Check if cached (metadata.json = fully encoded)
            cache_dir = get_tonie_cache_dir(audio_url)
            metadata_path = cache_dir / "metadata.json"
            is_cached = metadata_path.exists()

            if not is_cached:
                set_encoding_status(
                    audio_url,
                    "encoding",
                    progress=0,
                    total_tracks=len(tonie_tracks) if has_tracks else 1,
                )

            async def encode_and_play():
                nonlocal playback_started
                try:
                    mp3_path = None  # Only used for legacy single-file upload
                    if has_tracks:
                        # Multi-track: encode first track only, start playback, then continue in background
                        logger.info(
                            f"Encoding first track for {device_type}: {audio_url[:60]}... (1/{len(tonie_tracks)} tracks)"
                        )
                        espuino_ip_for_progress = (
                            reader_device.get("id")
                            if device_type == "espuino"
                            else None
                        )

                        # Encode ONLY the first track - allows playback to start quickly
                        first_track_path = await encode_first_track(
                            source_url=audio_url,
                            tracks=tonie_tracks,
                            series=series,
                            episode=episode,
                            espuino_ip=espuino_ip_for_progress,
                            cover_url=cover_url,
                        )
                        if not first_track_path:
                            logger.error("First track encoding failed")
                            return
                        logger.info(
                            f"First track ready, continuing encode in background"
                        )

                        # Continue encoding remaining tracks in background
                        # For Sonos/Chromecast: queue tracks progressively as they encode
                        # For ESPuino: just encode (it needs full.mp3 concatenated file)
                        progressive_device = (
                            reader_device
                            if device_type in ["sonos", "chromecast"]
                            else None
                        )
                        server_base = (
                            settings.server_url.rstrip("/")
                            if settings.server_url
                            else f"http://{get_local_ip()}:8754"
                        )

                        async def encode_remaining():
                            try:
                                await continue_encoding_remaining_tracks(
                                    source_url=audio_url,
                                    tracks=tonie_tracks,
                                    series=series,
                                    episode=episode,
                                    espuino_ip=espuino_ip_for_progress,
                                    cover_url=cover_url,
                                    playback_device=progressive_device,
                                    server_base_url=server_base
                                    if progressive_device
                                    else None,
                                )
                                logger.info(
                                    f"Background encoding complete: all {len(tonie_tracks)} tracks ready"
                                )
                            except Exception as e:
                                logger.error(f"Background encoding failed: {e}")

                        asyncio.create_task(encode_remaining())

                    logger.info(f"Starting playback on {device_type}")

                    # Now start playback
                    sd_playback = False  # Track if we're playing from SD

                    if has_tracks and device_type in ["sonos", "chromecast"]:
                        # Progressive playback: start with track 1 immediately
                        # Background encoder will queue remaining tracks as they complete
                        cache_key = get_tonie_cache_key(audio_url)
                        first_track_url = f"{server_base}/tracks/{cache_key}/01.mp3"

                        logger.info(
                            f"Starting progressive playback on {device_type}: track 1 of {len(tonie_tracks)}"
                        )

                        # Clear queue and start with first track
                        if device_type == "sonos":
                            sonos_ip = device_service.get_sonos_ip_from_uid(
                                reader_device.get("id")
                            )
                            if sonos_ip:
                                started = await device_service.play_playlist_on_sonos(
                                    sonos_ip, [first_track_url], title
                                )
                            else:
                                logger.warning(
                                    f"Could not find Sonos IP for {reader_device.get('id')}"
                                )
                                started = False
                        else:  # chromecast
                            started = await device_service.play_playlist_on_chromecast(
                                reader_device.get("id"), [first_track_url], title
                            )

                        if started:
                            logger.info(
                                f"Playback started, remaining {len(tonie_tracks) - 1} tracks will be queued as they encode"
                            )
                    elif has_tracks and device_type == "espuino":
                        # ESPuino with multi-track: check if files are on SD card
                        espuino_ip = reader_device.get("id")
                        dest_folder, _ = build_espuino_dest_path(uid, series, episode)

                        # Check SD with expected track count for reliable matching
                        sd_status = await device_service.check_espuino_sd_ready(
                            espuino_ip, dest_folder, expected_tracks=len(tonie_tracks)
                        )
                        if sd_status.get("ready"):
                            # Files are complete on SD - play locally!
                            logger.info(
                                f"Playing from SD card: {dest_folder} ({sd_status.get('tracks_total')} tracks)"
                            )
                            started = await device_service.play_espuino_from_sd(
                                espuino_ip,
                                dest_folder,
                                title,
                            )
                            sd_playback = True
                        else:
                            # Files not ready - stream single file and upload split tracks in background
                            logger.info(
                                f"SD not ready ({sd_status.get('tracks_complete')}/{sd_status.get('tracks_total')} tracks), streaming..."
                            )
                            started = await device_service.play_on_device(
                                reader_device,
                                playback_url,
                                title,
                                start_position=start_position,
                            )
                    elif should_resume and same_device:
                        started = await device_service.resume_device(reader_device)
                        if not started:
                            started = await device_service.play_on_device(
                                reader_device,
                                playback_url,
                                title,
                                start_position=start_position,
                            )
                    else:
                        started = await device_service.play_on_device(
                            reader_device,
                            playback_url,
                            title,
                            start_position=start_position,
                        )
                    if started:
                        logger.info(
                            f"Playback started on {device_type}: {title}"
                            + (" (from SD)" if sd_playback else "")
                        )

                        # ESPuino SD card upload: only for physical tags (real readers)
                        # Skip if already playing from SD (files complete)
                        # Skip for virtual readers (web-initiated streams)
                        # Skip in stream mode (playing on Sonos/etc, not ESPuino)
                        is_physical_tag = not _is_virtual_reader(reader_ip)
                        if (
                            device_type == "espuino"
                            and is_physical_tag
                            and not sd_playback
                            and not skip_sd_upload
                        ):

                            async def upload_to_sd():
                                try:
                                    espuino_ip = reader_device.get("id")
                                    import json
                                    import tempfile

                                    # Wait for encoding to fully complete before starting upload
                                    # Status must be "cached" or "ready" - NOT just "not encoding"
                                    # "partial" status means only some tracks exist, sizes would be 0
                                    if has_tracks:
                                        encoding_status = get_encoding_status(audio_url)
                                        wait_count = 0
                                        # Wait until status is "cached" (all tracks done) or "ready" or "error"
                                        final_statuses = ("cached", "ready", "error")
                                        while (
                                            encoding_status.get("status")
                                            not in final_statuses
                                            and wait_count < 300
                                        ):
                                            await asyncio.sleep(2)
                                            encoding_status = get_encoding_status(
                                                audio_url
                                            )
                                            wait_count += 1
                                            if wait_count % 10 == 0:
                                                status = encoding_status.get(
                                                    "status", "?"
                                                )
                                                progress = encoding_status.get(
                                                    "progress", 0
                                                )
                                                logger.info(
                                                    f"Waiting for encoding: {status} {progress:.0f}%"
                                                )
                                        final_status = encoding_status.get("status")
                                        if final_status == "error":
                                            logger.error(
                                                f"Encoding failed: {encoding_status.get('error', 'unknown')}, skipping upload"
                                            )
                                            return
                                        elif final_status not in ("cached", "ready"):
                                            logger.warning(
                                                f"Encoding timeout (status={final_status}) - proceeding with partial upload"
                                            )
                                        else:
                                            logger.info(
                                                f"Encoding complete (status={final_status}), starting upload"
                                            )

                                    if has_tracks:
                                        # Build human-readable folder name
                                        dest_folder, _ = build_espuino_dest_path(
                                            uid, series, episode
                                        )

                                        # Queue upload intent for persistence (survives restarts)
                                        upload_intent = {
                                            "uid": uid,
                                            "series": series,
                                            "episode": episode,
                                            "folder_path": dest_folder,
                                            "audio_url": audio_url,
                                            "tracks": [
                                                {
                                                    "index": i,
                                                    "name": t.get(
                                                        "name", f"Track {i + 1}"
                                                    ),
                                                    "source_path": str(
                                                        get_track_cache_path(
                                                            audio_url, i
                                                        )
                                                    ),
                                                    "dest_path": build_espuino_dest_path(
                                                        uid,
                                                        series,
                                                        episode,
                                                        i,
                                                        t.get("name"),
                                                    )[1],
                                                }
                                                for i, t in enumerate(tonie_tracks)
                                            ],
                                        }
                                        device_service.queue_upload(
                                            espuino_ip, upload_intent
                                        )

                                        # Build metadata with file sizes
                                        metadata = build_upload_metadata(
                                            uid,
                                            series,
                                            episode,
                                            tonie_tracks,
                                            audio_url,
                                        )

                                        # Clear any previous errors for this upload folder
                                        # This handles the "cancelled upload, errors persist" case
                                        device_service.clear_uploads_for_espuino(
                                            espuino_ip
                                        )

                                        # Check for partial uploads first - use size verification
                                        # This detects broken/incomplete files from cancelled uploads
                                        existing_check = (
                                            await device_service.verify_espuino_upload(
                                                espuino_ip,
                                                dest_folder,
                                                uid_map_path=build_espuino_uid_map_path(
                                                    uid
                                                ),
                                            )
                                        )
                                        if existing_check.get("metadata"):
                                            # Folder exists - resume partial upload
                                            verified = existing_check.get(
                                                "verified_tracks", 0
                                            )
                                            total = existing_check.get(
                                                "total_tracks", 0
                                            )
                                            missing = existing_check.get(
                                                "missing_tracks", []
                                            )
                                            mismatch = existing_check.get(
                                                "size_mismatch", []
                                            )
                                            needs_upload = missing + mismatch
                                            if needs_upload:
                                                logger.info(
                                                    f"Resuming partial upload: {verified}/{total} tracks OK, {len(needs_upload)} need upload"
                                                )
                                            else:
                                                logger.info(
                                                    f"Upload already complete: {verified}/{total} tracks verified"
                                                )
                                                uid_map_path = (
                                                    build_espuino_uid_map_path(uid)
                                                )
                                                uid_map_exists = await device_service.check_espuino_file_exists(
                                                    espuino_ip, uid_map_path
                                                )
                                                if not uid_map_exists:
                                                    logger.info(
                                                        f"UID map missing for {uid}, uploading: {uid_map_path}"
                                                    )
                                                    active_kbps = int(
                                                        os.getenv(
                                                            "ESPUINO_UPLOAD_MAX_KBPS_ACTIVE",
                                                            "200",
                                                        )
                                                    )
                                                    uid_map = {
                                                        "uid": uid,
                                                        "folder": dest_folder,
                                                        "title": title,
                                                        "series": series,
                                                        "episode": episode,
                                                        "files": [
                                                            {
                                                                "index": i,
                                                                "name": build_espuino_dest_path(
                                                                    uid,
                                                                    series,
                                                                    episode,
                                                                    i,
                                                                    t.get("name"),
                                                                )[1].split("/")[-1],
                                                                "size": get_track_cache_path(
                                                                    audio_url, i
                                                                )
                                                                .stat()
                                                                .st_size
                                                                if get_track_cache_path(
                                                                    audio_url, i
                                                                ).exists()
                                                                else 0,
                                                            }
                                                            for i, t in enumerate(
                                                                tonie_tracks
                                                            )
                                                        ],
                                                    }
                                                    import tempfile
                                                    import json

                                                    with tempfile.NamedTemporaryFile(
                                                        mode="w",
                                                        suffix=".json",
                                                        delete=False,
                                                    ) as f:
                                                        json.dump(uid_map, f, indent=2)
                                                        temp_uid_map = Path(f.name)
                                                    try:
                                                        await device_service.upload_to_espuino(
                                                            espuino_ip,
                                                            temp_uid_map,
                                                            uid_map_path,
                                                            title=f"{title} - uid-map",
                                                            total_tracks=len(
                                                                tonie_tracks
                                                            ),
                                                            max_kbps=active_kbps,
                                                            is_aux=True,
                                                        )
                                                    finally:
                                                        temp_uid_map.unlink(
                                                            missing_ok=True
                                                        )
                                                device_service.clear_pending_upload(
                                                    espuino_ip
                                                )
                                                return
                                        else:
                                            # Fresh upload - all tracks need uploading
                                            needs_upload = list(
                                                range(len(tonie_tracks))
                                            )

                                        active_kbps = int(
                                            os.getenv(
                                                "ESPUINO_UPLOAD_MAX_KBPS_ACTIVE", "200"
                                            )
                                        )
                                        idle_kbps = int(
                                            os.getenv(
                                                "ESPUINO_UPLOAD_MAX_KBPS_IDLE", "0"
                                            )
                                        )
                                        throttle_kbps = active_kbps
                                        uid_map_path = build_espuino_uid_map_path(uid)
                                        uid_map_success = False
                                        uid_map = {
                                            "uid": uid,
                                            "folder": dest_folder,
                                            "title": title,
                                            "series": series,
                                            "episode": episode,
                                            "files": [
                                                {
                                                    "index": i,
                                                    "name": build_espuino_dest_path(
                                                        uid,
                                                        series,
                                                        episode,
                                                        i,
                                                        t.get("name"),
                                                    )[1].split("/")[-1],
                                                    "size": get_track_cache_path(
                                                        audio_url, i
                                                    )
                                                    .stat()
                                                    .st_size
                                                    if get_track_cache_path(
                                                        audio_url, i
                                                    ).exists()
                                                    else 0,
                                                }
                                                for i, t in enumerate(tonie_tracks)
                                            ],
                                        }
                                        with tempfile.NamedTemporaryFile(
                                            mode="w", suffix=".json", delete=False
                                        ) as f:
                                            json.dump(uid_map, f, indent=2)
                                            temp_uid_map = Path(f.name)
                                        try:
                                            result = (
                                                await device_service.upload_to_espuino(
                                                    espuino_ip,
                                                    temp_uid_map,
                                                    uid_map_path,
                                                    title=f"{title} - uid-map",
                                                    total_tracks=len(tonie_tracks),
                                                    max_kbps=throttle_kbps,
                                                    is_aux=True,
                                                )
                                            )
                                            if result.get("success"):
                                                uid_map_success = True
                                                logger.info(
                                                    f"Uploaded UID map (pre-upload): {uid_map_path}"
                                                )
                                            else:
                                                logger.warning(
                                                    f"UID map pre-upload failed: {result.get('error')}"
                                                )
                                        finally:
                                            temp_uid_map.unlink(missing_ok=True)
                                        for i, track in enumerate(tonie_tracks):
                                            track_path = get_track_cache_path(
                                                audio_url, i
                                            )
                                            if track_path.exists():
                                                metadata["tracks"][i]["size"] = (
                                                    track_path.stat().st_size
                                                )

                                        upload_indices = [
                                            i
                                            for i in needs_upload
                                            if i < len(tonie_tracks)
                                        ]
                                        uploaded_count = 0
                                        skipped_count = max(
                                            0, len(tonie_tracks) - len(upload_indices)
                                        )
                                        for seq, i in enumerate(
                                            upload_indices, start=1
                                        ):
                                            track = tonie_tracks[i]
                                            track_path = get_track_cache_path(
                                                audio_url, i
                                            )
                                            if not track_path.exists():
                                                logger.warning(
                                                    f"Track {i + 1} missing from cache, cannot upload"
                                                )
                                                continue

                                            track_name = track.get(
                                                "name", f"Track {i + 1}"
                                            )
                                            _, dest_path = build_espuino_dest_path(
                                                uid, series, episode, i, track_name
                                            )

                                            logger.info(
                                                f"Uploading track {seq}/{len(upload_indices)} to ESPuino: {dest_path}"
                                            )
                                            result = (
                                                await device_service.upload_to_espuino(
                                                    espuino_ip,
                                                    track_path,
                                                    dest_path,
                                                    title=f"{title} - Track {i + 1}",
                                                    track_index=seq,
                                                    total_tracks=len(upload_indices),
                                                    max_kbps=throttle_kbps,
                                                )
                                            )
                                            if result.get("success"):
                                                uploaded_count += 1
                                                # Small delay between uploads to let ESPuino process
                                                if seq < len(upload_indices):
                                                    await asyncio.sleep(2)
                                            else:
                                                logger.warning(
                                                    f"Track {i + 1} upload failed: {result.get('error')}"
                                                )

                                        if skipped_count > 0:
                                            logger.info(
                                                f"Skipped {skipped_count} already-verified tracks"
                                            )

                                        # Upload metadata.json after all tracks
                                        if uploaded_count > 0:
                                            metadata_path = (
                                                f"{dest_folder}/metadata.json"
                                            )
                                            # Create temp file with metadata
                                            with tempfile.NamedTemporaryFile(
                                                mode="w", suffix=".json", delete=False
                                            ) as f:
                                                json.dump(metadata, f, indent=2)
                                                temp_metadata = Path(f.name)
                                            try:
                                                result = await device_service.upload_to_espuino(
                                                    espuino_ip,
                                                    temp_metadata,
                                                    metadata_path,
                                                    title=f"{title} - metadata",
                                                    total_tracks=max(
                                                        1, len(upload_indices)
                                                    ),
                                                    max_kbps=throttle_kbps,
                                                    is_aux=True,
                                                )
                                                if result.get("success"):
                                                    logger.info(
                                                        f"Uploaded metadata.json to {metadata_path}"
                                                    )
                                                else:
                                                    logger.warning(
                                                        f"Metadata upload failed: {result.get('error')}"
                                                    )
                                            finally:
                                                temp_metadata.unlink(missing_ok=True)

                                        logger.info(
                                            f"ESPuino SD upload complete: {uploaded_count}/{len(upload_indices)} tracks"
                                        )

                                        # Verify upload and re-upload missing/corrupt files
                                        verification = (
                                            await device_service.verify_espuino_upload(
                                                espuino_ip,
                                                dest_folder,
                                                uid_map_path=build_espuino_uid_map_path(
                                                    uid
                                                ),
                                            )
                                        )
                                        if not verification.get("complete"):
                                            missing = verification.get(
                                                "missing_tracks", []
                                            )
                                            mismatch = verification.get(
                                                "size_mismatch", []
                                            )
                                            retry_indices = [
                                                i
                                                for i in (missing + mismatch)
                                                if i < len(tonie_tracks)
                                            ]
                                            if retry_indices:
                                                logger.warning(
                                                    f"Verification failed: {len(retry_indices)} tracks need re-upload"
                                                )
                                                # Delete corrupted files before re-upload
                                                for i in mismatch:
                                                    if i < len(tonie_tracks):
                                                        track = tonie_tracks[i]
                                                        track_name = track.get(
                                                            "name", f"Track {i + 1}"
                                                        )
                                                        _, bad_path = (
                                                            build_espuino_dest_path(
                                                                uid,
                                                                series,
                                                                episode,
                                                                i,
                                                                track_name,
                                                            )
                                                        )
                                                        if await device_service.delete_espuino_file(
                                                            espuino_ip, bad_path
                                                        ):
                                                            logger.info(
                                                                f"Deleted corrupted file: {bad_path}"
                                                            )
                                                        else:
                                                            logger.warning(
                                                                f"Failed to delete corrupted file: {bad_path}"
                                                            )
                                                for seq, i in enumerate(
                                                    retry_indices, start=1
                                                ):
                                                    track = tonie_tracks[i]
                                                    track_path = get_track_cache_path(
                                                        audio_url, i
                                                    )
                                                    track_name = track.get(
                                                        "name", f"Track {i + 1}"
                                                    )
                                                    _, dest_path = (
                                                        build_espuino_dest_path(
                                                            uid,
                                                            series,
                                                            episode,
                                                            i,
                                                            track_name,
                                                        )
                                                    )
                                                    if track_path.exists():
                                                        metadata["tracks"][i][
                                                            "size"
                                                        ] = track_path.stat().st_size
                                                        logger.info(
                                                            f"Re-uploading track {seq}/{len(retry_indices)}: {track_name}"
                                                        )
                                                        await device_service.upload_to_espuino(
                                                            espuino_ip,
                                                            track_path,
                                                            dest_path,
                                                            title=f"{title} - {track_name}",
                                                            track_index=seq,
                                                            total_tracks=len(
                                                                retry_indices
                                                            ),
                                                            max_kbps=throttle_kbps,
                                                        )
                                                # Re-upload metadata
                                                with tempfile.NamedTemporaryFile(
                                                    mode="w",
                                                    suffix=".json",
                                                    delete=False,
                                                ) as f:
                                                    json.dump(metadata, f, indent=2)
                                                    temp_metadata = Path(f.name)
                                                try:
                                                    await device_service.upload_to_espuino(
                                                        espuino_ip,
                                                        temp_metadata,
                                                        metadata_path,
                                                        title=f"{title} - metadata",
                                                        total_tracks=len(retry_indices),
                                                        max_kbps=throttle_kbps,
                                                        is_aux=True,
                                                    )
                                                finally:
                                                    temp_metadata.unlink(
                                                        missing_ok=True
                                                    )
                                        else:
                                            logger.info(
                                                f"Upload verified: all {verification.get('total_tracks')} tracks OK"
                                            )
                                            if not uid_map_success:
                                                logger.warning(
                                                    f"UID map missing or failed, retrying upload: {uid_map_path}"
                                                )
                                                with tempfile.NamedTemporaryFile(
                                                    mode="w",
                                                    suffix=".json",
                                                    delete=False,
                                                ) as f:
                                                    json.dump(uid_map, f, indent=2)
                                                    temp_uid_map = Path(f.name)
                                                try:
                                                    retry = await device_service.upload_to_espuino(
                                                        espuino_ip,
                                                        temp_uid_map,
                                                        uid_map_path,
                                                        title=f"{title} - uid-map",
                                                        total_tracks=len(tonie_tracks),
                                                        max_kbps=throttle_kbps,
                                                        is_aux=True,
                                                    )
                                                    uid_map_success = retry.get(
                                                        "success", False
                                                    )
                                                finally:
                                                    temp_uid_map.unlink(missing_ok=True)
                                            if uid_map_success:
                                                # Auto-link RFID to local folder after verification
                                                folder_for_link = (
                                                    verification.get("folder")
                                                    or dest_folder
                                                )
                                                tag_id = uid_to_espuino_tag_id(uid)
                                                if tag_id:
                                                    if await device_service.set_espuino_rfid_mapping(
                                                        espuino_ip,
                                                        tag_id,
                                                        folder_for_link,
                                                        play_mode=5,
                                                    ):
                                                        logger.info(
                                                            f"RFID mapping updated for {tag_id} -> {folder_for_link}"
                                                        )
                                                    else:
                                                        logger.warning(
                                                            f"Failed to update RFID mapping for {tag_id}"
                                                        )
                                                # Clear pending upload queue - upload complete!
                                                device_service.clear_pending_upload(
                                                    espuino_ip
                                                )
                                            else:
                                                logger.warning(
                                                    f"UID map still missing; keeping pending upload for retry on next heartbeat"
                                                )
                                    elif mp3_path:
                                        # Legacy single-file upload (use human-readable path)
                                        _, dest_path = build_espuino_dest_path(
                                            uid, series, episode
                                        )
                                        if await device_service.check_espuino_file_exists(
                                            espuino_ip, dest_path
                                        ):
                                            logger.info(
                                                f"File already on ESPuino SD: {dest_path}"
                                            )
                                            return
                                        logger.info(
                                            f"Uploading MP3 to ESPuino {espuino_ip} SD card: {dest_path}"
                                        )
                                        result = await device_service.upload_to_espuino(
                                            espuino_ip,
                                            mp3_path,
                                            dest_path,
                                            title=title,
                                            track_index=1,
                                            total_tracks=1,
                                            max_kbps=throttle_kbps,
                                        )
                                        if result.get("success"):
                                            logger.info(
                                                f"ESPuino SD upload complete: {dest_path}"
                                            )
                                            uid_map_path = build_espuino_uid_map_path(
                                                uid
                                            )
                                            uid_map = {
                                                "uid": uid,
                                                "folder": str(Path(dest_path).parent),
                                                "title": title,
                                                "series": series,
                                                "episode": episode,
                                                "files": [
                                                    {
                                                        "index": 0,
                                                        "name": Path(dest_path).name,
                                                        "size": mp3_path.stat().st_size
                                                        if mp3_path.exists()
                                                        else 0,
                                                    }
                                                ],
                                            }
                                            with tempfile.NamedTemporaryFile(
                                                mode="w", suffix=".json", delete=False
                                            ) as f:
                                                json.dump(uid_map, f, indent=2)
                                                temp_uid_map = Path(f.name)
                                            try:
                                                map_result = await device_service.upload_to_espuino(
                                                    espuino_ip,
                                                    temp_uid_map,
                                                    uid_map_path,
                                                    title=f"{title} - uid-map",
                                                    total_tracks=1,
                                                    max_kbps=throttle_kbps,
                                                    is_aux=True,
                                                )
                                                if map_result.get("success"):
                                                    logger.info(
                                                        f"Uploaded UID map: {uid_map_path}"
                                                    )
                                                else:
                                                    logger.warning(
                                                        f"UID map upload failed: {map_result.get('error')}"
                                                    )
                                            finally:
                                                temp_uid_map.unlink(missing_ok=True)
                                        else:
                                            logger.warning(
                                                f"ESPuino SD upload failed: {result.get('error')}"
                                            )
                                except Exception as e:
                                    logger.error(f"ESPuino SD upload error: {e}")

                            # Run upload in background (don't block playback)
                            asyncio.create_task(upload_to_sd())
                except Exception as e:
                    logger.error(f"Encode/play failed for {device_type}: {e}")

            if is_cached:
                # File already cached - can await immediately (fast)
                logger.info(
                    f"{'Tracks' if has_tracks else 'File'} cached, playing immediately on {device_type}"
                )
                await encode_and_play()
            else:
                # File needs encoding - run in background, return immediately to ESP32
                logger.info(
                    f"{'Tracks' if has_tracks else 'File'} NOT cached, encoding in background for {device_type}"
                )
                asyncio.create_task(encode_and_play())
                response.encoding = True  # Tell ESP32 encoding is in progress

            playback_started = True  # Return success to ESP32 immediately
        elif should_resume and same_device:
            playback_started = await device_service.resume_device(reader_device)
            if not playback_started:
                playback_started = await device_service.play_on_device(
                    reader_device,
                    playback_url,
                    title,
                    start_position=start_position,
                )
        else:
            playback_started = await device_service.play_on_device(
                reader_device,
                playback_url,
                title,
                start_position=start_position,
            )
        response.playback_started = playback_started
        response.target = reader_device.get("id", "")
        if playback_started:
            logger.info(f"Playback started: {title} on {response.target}")
    else:
        # Library items or unknown tags - still need to set up playback
        logger.info(
            f"Library/unknown item playback. uid={uid[:30]}... device_type={device_type}"
        )

        # Use provided overrides if metadata is missing (e.g. from frontend)
        final_series = response.series or (metadata_override or {}).get("series")
        final_title = (
            response.title
            or (metadata_override or {}).get("title")
            or final_series
            or "Unknown Tag"
        )
        cover_url = response.picture or (metadata_override or {}).get("picture", "")

        # For library items, get tracks from frontend (passed via metadata_override)
        lib_tracks = (metadata_override or {}).get("tracks") or []
        if not lib_tracks:
            # Fallback: create pseudo-track if no track info provided
            lib_tracks = [{"name": "Full Audio", "duration": 7200, "start": 0}]
            logger.warning(f"No track info for library item, using pseudo-track")

        state["current_tag"] = {
            "uid": uid,
            "series": final_series,
            "episode": response.episode or (metadata_override or {}).get("episode"),
            "title": final_title,
            "picture": cover_url,
            "audio_url": audio_url,
            "playback_url": playback_url,
            "placed_at": datetime.now().isoformat(),
            "start_position": start_position,
            "tracks": lib_tracks,
            "track_count": len(lib_tracks),
        }
        state["current_started_at"] = time.time()
        state["current_offset"] = start_position
        state["current_device"] = reader_device
        state["last_reported_position"] = (
            start_position if device_type == "browser" else 0.0
        )

        # Check if already cached (metadata.json exists = fully encoded)
        cache_dir = get_tonie_cache_dir(audio_url)
        metadata_path = cache_dir / "metadata.json"
        is_cached = metadata_path.exists()

        playback_started = False

        if device_type == "browser":
            # Browser handles playback via web UI - just trigger encoding
            if not is_cached:
                set_encoding_status(
                    audio_url,
                    "encoding",
                    progress=0,
                    total_tracks=len(lib_tracks),
                )

                async def encode_for_browser_lib():
                    logger.info(
                        f"Starting library item encoding for browser: {audio_url[:60]}... ({len(lib_tracks)} tracks)"
                    )
                    try:
                        metadata = await get_or_encode_tracks(
                            source_url=audio_url,
                            tracks=lib_tracks,
                            series=final_series or "",
                            episode=(metadata_override or {}).get("episode") or "",
                            cover_url=cover_url,
                        )
                        if metadata:
                            logger.info(
                                f"Library item encoding complete: {len(metadata.tracks)} tracks"
                            )
                        else:
                            logger.error("Library item encoding returned no metadata")
                    except Exception as e:
                        logger.error(f"Library item encoding failed: {e}")

                asyncio.create_task(encode_for_browser_lib())
            playback_started = True  # Browser handles actual playback
        elif device_type in ["sonos", "airplay", "chromecast"]:
            # Network devices: use progressive encoding like regular Tonie playback
            # Encode first track, start playback, then queue remaining tracks as they encode
            if not is_cached:
                set_encoding_status(
                    audio_url,
                    "encoding",
                    progress=0,
                    total_tracks=len(lib_tracks),
                )

            async def encode_and_play_lib():
                nonlocal playback_started
                try:
                    # Encode ONLY first track first
                    logger.info(
                        f"Encoding first track for library {device_type}: {audio_url[:60]}... (1/{len(lib_tracks)} tracks)"
                    )
                    first_track_path = await encode_first_track(
                        source_url=audio_url,
                        tracks=lib_tracks,
                        series=final_series or "",
                        episode=(metadata_override or {}).get("episode") or "",
                        cover_url=cover_url,
                    )
                    if not first_track_path:
                        logger.error("Library first track encoding failed")
                        return

                    # Start playback immediately with first track
                    cache_key = get_tonie_cache_key(audio_url)
                    server_base = (
                        settings.server_url.rstrip("/")
                        if settings.server_url
                        else f"http://{get_local_ip()}:8754"
                    )
                    first_track_url = f"{server_base}/tracks/{cache_key}/01.mp3"

                    logger.info(
                        f"Starting progressive library playback on {device_type}: track 1 of {len(lib_tracks)}"
                    )

                    if device_type == "sonos":
                        sonos_ip = device_service.get_sonos_ip_from_uid(
                            reader_device.get("id")
                        )
                        if sonos_ip:
                            started = await device_service.play_playlist_on_sonos(
                                sonos_ip, [first_track_url], final_title
                            )
                        else:
                            logger.warning(
                                f"Could not find Sonos IP for {reader_device.get('id')}"
                            )
                            started = False
                    elif device_type == "chromecast":
                        started = await device_service.play_playlist_on_chromecast(
                            reader_device.get("id"), [first_track_url], final_title
                        )
                    else:
                        started = await device_service.play_on_device(
                            reader_device, first_track_url, final_title
                        )

                    if started:
                        logger.info(
                            f"Library playback started: {final_title} on {device_type}"
                        )

                    # Continue encoding remaining tracks in background
                    # For Sonos/Chromecast: queue tracks progressively as they encode
                    progressive_device = (
                        reader_device
                        if device_type in ["sonos", "chromecast"]
                        else None
                    )

                    async def encode_remaining_lib():
                        try:
                            await continue_encoding_remaining_tracks(
                                source_url=audio_url,
                                tracks=lib_tracks,
                                series=final_series or "",
                                episode=(metadata_override or {}).get("episode") or "",
                                cover_url=cover_url,
                                playback_device=progressive_device,
                                server_base_url=server_base
                                if progressive_device
                                else None,
                            )
                            logger.info(
                                f"Library background encoding complete: all {len(lib_tracks)} tracks ready"
                            )
                        except Exception as e:
                            logger.error(f"Library background encoding failed: {e}")

                    asyncio.create_task(encode_remaining_lib())

                except Exception as e:
                    logger.error(f"Library item encode/play failed: {e}")

            asyncio.create_task(encode_and_play_lib())
            playback_started = (
                True  # Mark as started (async will handle actual playback)
            )

        response.playback_started = playback_started
        response.target = reader_device.get("id", "")

    if record_scan:
        recent_scans.appendleft(
            {
                "time": datetime.now().isoformat(),
                "uid": uid,
                "reader_ip": reader_ip,
                "found": response.found,
                "title": response.title or response.series or "Unknown",
            }
        )

    return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown lifecycle."""
    global teddycloud_client, _smart_ping_task
    settings = get_settings()

    # Initialize TeddyCloud client with internal URL (for audio fetching)
    # Falls back to external URL if internal not set
    internal_url = settings.teddycloud.internal_url or settings.teddycloud.url
    teddycloud_client = TeddyCloudClient(
        base_url=internal_url,
        api_base=settings.teddycloud.api_base,
        timeout=settings.teddycloud.timeout,
    )

    # Load default device from settings
    device_service.init_default_device()

    # Load device cache from file
    device_service.init_device_cache()

    # Load reader cache from file
    device_service.init_reader_cache()

    # Check connection
    if await teddycloud_client.check_connection():
        logger.info(f"Connected to TeddyCloud at {settings.teddycloud.url}")
    else:
        logger.warning(f"TeddyCloud not accessible at {settings.teddycloud.url}")

    # Start smart ping background task for ESPuino readers
    _smart_ping_task = asyncio.create_task(smart_ping_espuino_readers())
    logger.info("Started smart ping background task for ESPuino readers")

    yield

    # Cleanup
    if _smart_ping_task:
        _smart_ping_task.cancel()
        try:
            await _smart_ping_task
        except asyncio.CancelledError:
            pass

    if teddycloud_client:
        await teddycloud_client.close()


app = FastAPI(
    title="ToniePlayer API",
    description="ESP32 NFC reader backend for Tonie playback control",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS middleware - allow all origins for self-hosted deployment
# For production with reverse proxy, configure your proxy's CORS instead
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static files for web UI (Svelte SPA)
STATIC_DIR = Path(__file__).parent / "static"

# Mount assets directory for Vite-built JS/CSS bundles
ASSETS_DIR = STATIC_DIR / "assets"
if ASSETS_DIR.exists():
    app.mount("/assets", StaticFiles(directory=ASSETS_DIR), name="assets")

# Also mount root static for any additional static files
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
async def root():
    """Serve the Svelte SPA."""
    return FileResponse(STATIC_DIR / "index.html")


class TargetDevice(BaseModel):
    """Target device for stream mode playback."""

    type: str  # "sonos", "chromecast", "airplay", "espuino"
    id: str  # Device IP or ID


class TonieRequest(BaseModel):
    uid: str | None  # e.g. "E0:04:03:50:13:16:80:4B" or null for removal
    mode: str = "local"  # "local" = play on ESPuino, "stream" = play on target_device
    target_device: TargetDevice | None = None  # Required for stream mode
    espuino_ip: str | None = None  # ESPuino IP address (for progress notifications)
    title: str | None = None
    series: str | None = None
    episode: str | None = None
    picture: str | None = None  # Cover image path
    tracks: list[dict] | None = (
        None  # Track info for library items [{name, duration, start}]
    )
    audio_url: str | None = None  # Direct audio URL for library items


class TonieResponse(BaseModel):
    uid: str = ""
    series: str | None = None
    episode: str | None = None
    title: str | None = None
    picture: str | None = None  # Cover image path
    found: bool = False
    playback_started: bool = False
    encoding: bool = False  # True if audio needs encoding before playback
    playback_url: str | None = None  # Stream URL for ESPuino to play (single file)
    playlist_url: str | None = (
        None  # M3U playlist URL for multi-track playback with skip support
    )
    track_count: int = 1  # Number of tracks (1 for single file, >1 for multi-track)
    target: str | None = None


class HealthResponse(BaseModel):
    status: str
    teddycloud_connected: bool


@app.get("/health", response_model=HealthResponse)
async def health():
    """Health check endpoint."""
    connected = False
    if teddycloud_client:
        connected = await teddycloud_client.check_connection()
    return HealthResponse(status="ok", teddycloud_connected=connected)


@app.get("/version")
async def version():
    """Return build version info."""
    return {
        "version": "1.0.0",
        "git_commit": os.getenv("GIT_COMMIT", "dev"),
        "build_time": os.getenv("BUILD_TIME", "unknown"),
    }


@app.get("/debug")
async def debug_info():
    """Debug endpoint showing full system state."""
    settings = get_settings()
    tc_connected = False
    tc_url = ""
    audio_url_example = ""

    if teddycloud_client:
        tc_connected = await teddycloud_client.check_connection()
        tc_url = teddycloud_client.base_url
        audio_url_example = teddycloud_client.get_audio_url("E0040350131680AB")

    # Check if transcoding would be used
    active_device = device_service.get_active_device()
    transcode_enabled = active_device.get("type") in [
        "sonos",
        "airplay",
        "chromecast",
        "espuino",
    ]

    detected_ip = get_local_ip()
    effective_server_url = settings.server_url or f"http://{detected_ip}:8754"

    return {
        "server": {
            "status": "running",
            "time": datetime.now().isoformat(),
            "detected_ip": detected_ip,
            "server_url": settings.server_url or "(auto-detected)",
            "effective_url": effective_server_url,
        },
        "teddycloud": {
            "url": tc_url,
            "api_base": settings.teddycloud.api_base,
            "connected": tc_connected,
            "audio_url_format": audio_url_example,
        },
        "transcoding": {
            "ffmpeg_available": check_ffmpeg(),
            "enabled_for_device": transcode_enabled,
            "active_device_type": active_device.get("type", "none"),
            "cache": get_cache_stats(),
        },
        "current_tags": {
            ip: state.get("current_tag") for ip, state in reader_states.items()
        },
        "default_device": device_service.get_default_device(),
        "reader_devices": get_settings().reader_devices,
        "readers": {
            "count": len(connected_readers),
            "list": [{"ip": ip, **data} for ip, data in connected_readers.items()],
        },
        "recent_scans": list(recent_scans)[:10],
        "devices": device_service.get_all_devices(),
        "logs": list(log_capture.logs)[-30:],
    }


@app.post("/tonie", response_model=TonieResponse)
async def handle_tonie(request: TonieRequest, req: Request):
    """
    Handle a scanned Tonie tag or tag removal from ESPuino.

    Supports two modes:
    - "local" (default): Stream to ESPuino + upload to SD card
    - "stream": Stream to target_device (Sonos/etc), ESPuino acts as remote

    Request fields:
    - uid: Tag UID (null for removal)
    - mode: "local" or "stream"
    - target_device: {type, id} for stream mode
    - espuino_ip: ESPuino's IP (for progress notifications)
    """
    logger.info(
        f"Received tonie request: uid={request.uid} mode={request.mode} target={request.target_device}"
    )

    # Use espuino_ip from request if provided, otherwise use client IP
    reader_ip = request.espuino_ip or (req.client.host if req.client else "unknown")
    now = datetime.now()

    if reader_ip not in connected_readers:
        cached = device_service.get_cached_readers().get(reader_ip, {})
        default_name = (
            "Web Interface"
            if _is_virtual_reader(reader_ip)
            else f"ESPuino ({reader_ip})"
        )
        name = cached.get("name") or default_name
        connected_readers[reader_ip] = {
            "first_seen": now.isoformat(),
            "last_seen": now.isoformat(),
            "scan_count": 0,
            "name": name,
        }
        logger.info(f"New reader connected: {reader_ip}")

    connected_readers[reader_ip]["last_seen"] = now.isoformat()
    if not _is_virtual_reader(reader_ip):
        device_service.update_reader_cache(
            reader_ip,
            {
                "name": connected_readers[reader_ip]["name"],
                "last_seen": connected_readers[reader_ip]["last_seen"],
                "scan_count": connected_readers[reader_ip]["scan_count"],
            },
        )

    # Handle tag removal - pause and save position for resume
    if request.uid is None:
        logger.info(f"Tag removed from {reader_ip}")
        await stop_reader_playback(reader_ip, save_resume=True, pause_only=True)
        return TonieResponse(uid="", found=False)

    connected_readers[reader_ip]["scan_count"] += 1

    # Check if this reader has a configured device override (non-ESPuino readers)
    # Non-ESPuino readers always stream to their configured device, ignoring mode=local
    reader_configured_device = device_service.get_reader_device_override(reader_ip)
    is_espuino_reader = (
        reader_configured_device is None
        or reader_configured_device.get("type") == "espuino"
    )

    # Determine device override based on mode
    device_override = None
    if request.mode == "stream" and request.target_device:
        # Stream mode with explicit target: use specified target device (Sonos, etc.)
        device_override = {
            "type": request.target_device.type,
            "id": request.target_device.id,
        }
        logger.info(
            f"Stream mode: {reader_ip} -> {device_override['type']}:{device_override['id']}"
        )
    elif request.mode == "local" and is_espuino_reader:
        # Local mode: ESPuino plays audio directly on itself
        device_override = {
            "type": "espuino",
            "id": reader_ip,
        }
        logger.info(f"Local mode: {reader_ip} will play audio directly")
    elif reader_configured_device:
        # Non-ESPuino reader with configured device: always stream to that device
        device_override = reader_configured_device
        logger.info(
            f"Non-ESPuino reader {reader_ip} -> streaming to {device_override['type']}:{device_override['id']}"
        )

    # Pass optional metadata from request to playback function
    overrides = {
        "title": request.title,
        "series": request.series,
        "episode": request.episode,
        "picture": request.picture,
        "tracks": request.tracks,
        "audio_url": request.audio_url,
    }

    # Store stream mode info in reader state for remote control
    state = get_reader_state(reader_ip)
    state["mode"] = request.mode
    state["target_device"] = device_override if request.mode == "stream" else None
    state["espuino_ip"] = reader_ip

    response = await play_tonie_for_reader(
        reader_ip,
        request.uid,
        device_override=device_override,
        record_scan=True,
        skip_sd_upload=(request.mode == "stream"),  # Don't upload to SD in stream mode
        metadata_override=overrides,
    )
    logger.info(
        f"Scan from {reader_ip} (mode={request.mode}): {request.uid} -> {'found' if response.found else 'not found'}"
    )
    return response


@app.get("/current")
async def get_current_tag():
    """Get current tags for all readers."""
    return {
        "readers": {ip: state.get("current_tag") for ip, state in reader_states.items()}
    }


@app.get("/streams")
async def get_streams():
    """Get all active streams with encoding/cache status and device transport state.

    Returns detailed status info for each stream including:
    - Encoding progress (if currently encoding)
    - Cache status (cached or not)
    - File size (if cached)
    - Error messages (if encoding failed)
    - Device transport state (playing/paused, position, duration) for Sonos/Chromecast
    """
    streams = []

    for ip, state in list(reader_states.items()):
        current = state.get("current_tag")
        if not current:
            continue

        device = state.get("current_device") or device_service.get_device_for_reader(ip)

        # Check for stale ESPuino readers (no heartbeat/smart-ping for 180+ seconds)
        # Smart ping task updates last_seen every 60s if ESPuino is still playing our tag
        # 180s timeout allows 3 ping cycles before cleanup (handles temporary network issues)
        if device.get("type") == "espuino" and not _is_virtual_reader(ip):
            reader_info = connected_readers.get(ip, {})
            last_seen_str = reader_info.get("last_seen")
            if last_seen_str:
                try:
                    last_seen = datetime.fromisoformat(last_seen_str)
                    seconds_since = (datetime.now() - last_seen).total_seconds()
                    if seconds_since > 180:
                        logger.info(
                            f"Cleaning up stale ESPuino stream: {ip} (no activity for {seconds_since:.0f}s)"
                        )
                        state["current_tag"] = None
                        continue
                except (ValueError, TypeError):
                    pass

        # Get encoding status for this stream's audio URL
        audio_url = current.get("audio_url", "")
        encoding_info = get_encoding_status(audio_url) if audio_url else {}

        reader_info = connected_readers.get(ip, {})

        # Get device transport state (play/pause status, position) for non-browser devices
        transport_state = None
        if device.get("type") not in ("browser", None, ""):
            transport_state = await device_service.get_device_transport_state(device)

        # Look up friendly device name from cache
        device_name = None
        device_type = device.get("type", "")
        device_id = device.get("id", "")
        if device_type == "browser":
            device_name = "Browser"
        elif device_type and device_id:
            device_name = device_service.get_device_name(device_type, device_id)

        # Build track URLs if multi-track is cached
        settings = get_settings()
        # Use relative URLs if the active device is "browser" to avoid mixed content errors
        is_browser = device.get("type") == "browser"
        track_urls = (
            build_track_urls(audio_url, settings, absolute=not is_browser)
            if audio_url
            else []
        )
        cached_metadata = get_cached_tracks(audio_url) if audio_url else None

        # Get track info from cached metadata or from state (for progressive playback)
        state_tracks = current.get("tracks", [])
        state_track_count = current.get("track_count", len(state_tracks))

        # Build track metadata from cache if available, otherwise from state
        if cached_metadata and cached_metadata.tracks:
            track_metadata = [
                {
                    "index": t.index,
                    "name": t.name,
                    "duration": t.duration_seconds,
                }
                for t in cached_metadata.tracks
            ]
        elif state_tracks:
            # Use state tracks for progressive playback (before cache is complete)
            track_metadata = [
                {
                    "index": i,
                    "name": t.get("name", f"Track {i + 1}"),
                    "duration": t.get("duration", 0),
                }
                for i, t in enumerate(state_tracks)
            ]
        else:
            track_metadata = []

        streams.append(
            {
                "reader_ip": ip,
                "reader_name": reader_info.get("name", ip),
                "tag": {
                    "uid": current.get("uid"),
                    "title": current.get("title") or current.get("series"),
                    "series": current.get("series"),
                    "episode": current.get("episode"),
                    "picture": current.get("picture"),
                    "placed_at": current.get("placed_at"),
                    "start_position": current.get("start_position", 0),
                    "duration": current.get("duration"),
                    "tracks": state_tracks,
                },
                "audio": {
                    "source_url": audio_url,
                    "playback_url": current.get("playback_url", ""),
                    # Multi-track info
                    "track_urls": track_urls,
                    "is_multi_track": state_track_count > 1,
                    "track_count": state_track_count
                    if state_track_count > 0
                    else len(track_urls),
                    "track_metadata": track_metadata,
                },
                "device": {
                    "type": device.get("type"),
                    "id": device.get("id"),
                    "name": device_name,
                },
                "encoding": encoding_info,
                "transport": transport_state,  # Play/pause state, position, duration for Sonos/Chromecast
            }
        )

    # Get all active uploads
    uploads = device_service.get_all_upload_status()

    # Get pending uploads
    pending = device_service.get_all_pending_uploads()
    pending_list = [
        {
            "espuino_ip": ip,
            "uid": data.get("uid"),
            "series": data.get("series"),
            "episode": data.get("episode"),
            "folder_path": data.get("folder_path"),
            "queued_at": data.get("queued_at"),
            "status": data.get("status"),
            "tracks_total": len(data.get("tracks", [])),
        }
        for ip, data in pending.items()
    ]

    return {
        "count": len(streams),
        "streams": streams,
        "cache": get_cache_stats(),
        "uploads": uploads,
        "pending_uploads": pending_list,
    }


@app.get("/uploads")
async def get_uploads():
    """Get all active ESPuino SD card uploads with progress info.

    Returns upload status including:
    - bytes_uploaded / total_bytes
    - transfer_rate (bytes/sec)
    - eta_seconds
    - progress (0-100)
    """
    uploads = device_service.get_all_upload_status()
    return {
        "count": len(uploads),
        "uploads": uploads,
    }


@app.get("/uploads/pending")
async def get_pending_uploads():
    """Get all pending uploads in the queue.

    These are uploads queued for ESPuino devices that haven't been
    completed yet (may resume on heartbeat or be waiting for device).
    """
    pending = device_service.get_all_pending_uploads()
    return {
        "count": len(pending),
        "pending": [
            {
                "espuino_ip": ip,
                "uid": data.get("uid"),
                "series": data.get("series"),
                "episode": data.get("episode"),
                "folder_path": data.get("folder_path"),
                "queued_at": data.get("queued_at"),
                "status": data.get("status"),
                "tracks_total": len(data.get("tracks", [])),
            }
            for ip, data in pending.items()
        ],
    }


@app.delete("/uploads/pending")
async def clear_pending_upload(espuino_ip: str):
    """Clear a pending upload from the queue."""
    device_service.request_cancel_uploads(espuino_ip)
    device_service.clear_pending_upload(espuino_ip)
    device_service.clear_uploads_for_espuino(espuino_ip)
    return {"status": "ok", "cleared": espuino_ip}


@app.delete("/uploads")
async def clear_uploads(espuino_ip: str | None = None):
    """Clear upload status. Optionally filter by ESPuino IP."""
    if espuino_ip:
        count = device_service.clear_uploads_for_espuino(espuino_ip)
    else:
        count = device_service.clear_all_uploads()
    return {"status": "ok", "cleared": count}


@app.post("/uploads/wipe")
async def wipe_all_uploads(espuino_ip: str | None = None):
    """Completely wipe all upload state - both status AND pending queue.

    Use this to get a clean slate after cancelled/failed uploads.
    When tag is placed again, it will start fresh.
    """
    cleared_status = 0
    cleared_pending = 0

    if espuino_ip:
        # Clear for specific ESPuino
        cleared_status = device_service.clear_uploads_for_espuino(espuino_ip)
        if device_service.get_pending_upload(espuino_ip):
            device_service.clear_pending_upload(espuino_ip)
            cleared_pending = 1
    else:
        # Clear everything
        cleared_status = device_service.clear_all_uploads()
        pending = device_service.get_all_pending_uploads()
        for ip in list(pending.keys()):
            device_service.clear_pending_upload(ip)
        cleared_pending = len(pending)

    logger.info(
        f"Wiped upload state: {cleared_status} status entries, {cleared_pending} pending uploads"
    )
    return {
        "status": "ok",
        "cleared_status": cleared_status,
        "cleared_pending": cleared_pending,
    }


@app.post("/uploads/retry")
async def retry_failed_uploads(espuino_ip: str | None = None):
    """Retry all failed uploads. Optionally filter by ESPuino IP."""
    from pathlib import Path

    failed = device_service.get_failed_uploads(espuino_ip)
    if not failed:
        return {"status": "ok", "retried": 0, "message": "No failed uploads to retry"}

    retried = 0
    for upload in failed:
        source_path = upload.get("source_path")
        dest_path = upload.get("dest_path")
        ip = upload.get("espuino_ip")
        title = upload.get("title", "")

        if not source_path or not dest_path or not ip:
            logger.warning(f"Missing info for retry: {upload}")
            continue

        source = Path(source_path)
        if not source.exists():
            logger.warning(f"Source file missing for retry: {source_path}")
            # Clear this failed upload since file is gone
            device_service.clear_upload_status(ip, dest_path)
            continue

        # Clear the error status before retrying
        device_service.clear_upload_status(ip, dest_path)

        # Retry upload in background
        async def do_retry(ip=ip, source=source, dest_path=dest_path, title=title):
            idle_kbps = int(os.getenv("ESPUINO_UPLOAD_MAX_KBPS_IDLE", "0"))
            result = await device_service.upload_to_espuino(
                ip, source, dest_path, title=title, max_kbps=idle_kbps
            )
            if result.get("success"):
                logger.info(f"Retry successful: {dest_path}")
            else:
                logger.warning(f"Retry failed: {dest_path} - {result.get('error')}")

        asyncio.create_task(do_retry())
        retried += 1

    return {"status": "ok", "retried": retried}


@app.get("/readers")
async def list_readers():
    """List all readers (cached + connected)."""
    result = []

    # Merge cached readers with connected readers
    all_reader_ips = set(connected_readers.keys()) | set(
        device_service.get_cached_readers().keys()
    )

    for ip in all_reader_ips:
        # Get data from connected (live) or cache
        if ip in connected_readers:
            data = connected_readers[ip].copy()
            data["online"] = True
        else:
            cached = device_service.get_cached_readers().get(ip, {})
            data = cached.copy()
            data["online"] = False

        state = get_reader_state(ip)
        current_tag = state.get("current_tag")
        # Use the actual playing device if there's an active stream, otherwise use default
        playing_device = state.get("current_device") if current_tag else None
        default_device = device_service.get_device_for_reader(ip)

        result.append(
            {
                "ip": ip,
                **data,
                "current_tag": current_tag,
                "device": playing_device or default_device,  # Actual device being used
                "default_device": default_device,  # Configured default
                "device_override": device_service.get_reader_device_override(ip)
                is not None,
                "device_temp": ip in device_service.reader_current_devices,
            }
        )

    # Sort: online first, then by last_seen
    result.sort(
        key=lambda r: (not r.get("online", False), r.get("last_seen", "") or ""),
        reverse=True,
    )

    return {"count": len(result), "readers": result}


class ReaderRenameRequest(BaseModel):
    name: str


@app.put("/readers/{reader_ip}/name")
async def rename_reader(reader_ip: str, request: ReaderRenameRequest):
    """Rename a reader."""
    # Update in memory
    if reader_ip in connected_readers:
        connected_readers[reader_ip]["name"] = request.name
    # Update in cache
    device_service.rename_reader(reader_ip, request.name)
    return {"status": "ok", "reader_ip": reader_ip, "name": request.name}


@app.delete("/readers/{reader_ip}")
async def remove_reader(reader_ip: str):
    """Remove a reader from the cache."""
    if reader_ip in connected_readers:
        del connected_readers[reader_ip]
    device_service.remove_reader(reader_ip)
    return {"status": "ok", "reader_ip": reader_ip}


@app.get("/api/features")
async def get_feature_flags():
    """Get feature flags for the frontend.

    Used to conditionally show/hide features based on deployment configuration.
    """
    return {
        "espuino_enabled": ESPUINO_ENABLED,
    }


@app.get("/api/logs")
async def get_server_logs(
    level: str | None = None,
    limit: int = 100,
):
    """Get recent server logs for debugging.

    Args:
        level: Filter by log level (DEBUG, INFO, WARNING, ERROR)
        limit: Maximum number of logs to return (default 100, max 500)
    """
    limit = min(limit, 500)
    logs = list(log_capture.logs)

    # Filter by level if specified
    if level:
        level_upper = level.upper()
        logs = [log for log in logs if log["level"] == level_upper]

    # Return most recent logs (reversed so newest first)
    return {
        "logs": list(reversed(logs[-limit:])),
        "total": len(log_capture.logs),
        "filtered": len(logs),
    }


@app.get("/api/devices")
async def get_all_playback_devices():
    """Get all known playback devices for ESPuino stream mode configuration.

    Returns a flat list of all devices (Sonos, Chromecast, AirPlay, ESPuino)
    that can be used as playback targets. Used by ESPuino web UI to populate
    the stream mode device selection dropdown.
    """
    all_devices = device_service.get_all_devices()
    devices = []

    for device_type, device_list in all_devices.items():
        for device in device_list:
            # Get stable ID for the device
            if device_type == "sonos":
                device_id = device.get("ip", "")
            elif device_type == "chromecast":
                device_id = device.get("id", "") or device.get("ip", "")
            elif device_type == "airplay":
                device_id = device.get("id", "") or device.get("address", "")
            elif device_type == "espuino":
                device_id = device.get("ip", "") or device.get("id", "")
            else:
                device_id = device.get("id", "") or device.get("ip", "")

            if device_id:
                devices.append(
                    {
                        "type": device_type,
                        "id": device_id,
                        "name": device.get("name", device_id),
                        "online": device.get("online", False),
                    }
                )

    return {"devices": devices}


@app.post("/readers/{reader_ip}/heartbeat")
async def reader_heartbeat(reader_ip: str, req: Request):
    """Heartbeat endpoint for ESP32 readers to announce presence.

    Called on boot and periodically to maintain visibility in the UI.
    Accepts optional JSON body with {"name": "device_name"} to update the reader name.
    """
    now = datetime.now()

    # Parse optional name from request body
    reader_name = None
    try:
        body = await req.json()
        reader_name = body.get("name")
        logger.info(f"Reader heartbeat body: {body}, parsed name: {reader_name}")
    except Exception as e:
        logger.debug(f"Reader heartbeat: no body or invalid JSON: {e}")
        pass  # No body or invalid JSON is fine

    if reader_ip not in connected_readers:
        # Check cache for existing name
        cached = device_service.get_cached_readers().get(reader_ip, {})
        connected_readers[reader_ip] = {
            "first_seen": now.isoformat(),
            "last_seen": now.isoformat(),
            "scan_count": cached.get("scan_count", 0),
            "name": reader_name or cached.get("name") or f"Reader ({reader_ip})",
        }
        logger.info(
            f"Reader heartbeat (new): {reader_ip} - {connected_readers[reader_ip]['name']}"
        )
    else:
        connected_readers[reader_ip]["last_seen"] = now.isoformat()
        # Update name if provided
        if reader_name:
            old_name = connected_readers[reader_ip].get("name", "unknown")
            connected_readers[reader_ip]["name"] = reader_name
            if old_name != reader_name:
                logger.info(
                    f"Reader {reader_ip} name updated: '{old_name}' -> '{reader_name}'"
                )

    # Update persistent cache
    device_service.update_reader_cache(
        reader_ip,
        {
            "name": connected_readers[reader_ip]["name"],
            "last_seen": now.isoformat(),
            "scan_count": connected_readers[reader_ip]["scan_count"],
        },
    )

    # Check for pending uploads and resume if ESPuino just came online
    # (ESPuino IP is often same as reader IP for built-in readers)
    pending = device_service.get_pending_upload(reader_ip)
    if pending:
        logger.info(
            f"ESPuino {reader_ip} online - checking pending upload: {pending.get('folder_path')}"
        )
        asyncio.create_task(resume_pending_upload(reader_ip, pending))

    return {"status": "ok", "reader_ip": reader_ip}


async def resume_pending_upload(espuino_ip: str, pending: dict):
    """
    Resume a pending upload for an ESPuino that just came online.

    Verifies what's on SD card and uploads only missing/corrupt files.
    """
    import tempfile
    import json

    folder_path = pending.get("folder_path")
    if not folder_path:
        logger.warning(f"No folder path in pending upload for {espuino_ip}")
        device_service.clear_pending_upload(espuino_ip)
        return

    # Verify current state on SD card
    uid = pending.get("uid", "")
    uid_map_path = build_espuino_uid_map_path(uid) if uid else None
    verification = await device_service.verify_espuino_upload(
        espuino_ip,
        folder_path,
        uid_map_path=uid_map_path,
    )

    if verification.get("complete"):
        logger.info(f"Pending upload already complete for {espuino_ip}: {folder_path}")
        device_service.clear_pending_upload(espuino_ip)
        return

    # Determine what needs to be uploaded
    missing = verification.get("missing_tracks", [])
    mismatch = verification.get("size_mismatch", [])
    tracks = pending.get("tracks", [])

    # If no metadata.json exists, we need to upload everything
    if verification.get("metadata") is None:
        logger.info(
            f"No metadata found, uploading all {len(tracks)} tracks to {espuino_ip}"
        )
        retry_indices = list(range(len(tracks)))
    else:
        retry_indices = missing + mismatch

    retry_indices = [i for i in retry_indices if i < len(tracks)]

    if not retry_indices:
        logger.info(f"All tracks present for {espuino_ip}, upload verified")
        device_service.clear_pending_upload(espuino_ip)
        return

    logger.info(
        f"Resuming upload to {espuino_ip}: {len(retry_indices)} tracks to upload"
    )

    idle_kbps = int(os.getenv("ESPUINO_UPLOAD_MAX_KBPS_IDLE", "0"))

    # Upload missing/corrupt tracks
    title = pending.get("series", "") or pending.get("episode", "") or "Tonie"
    # Delete corrupted files before re-upload
    for i in mismatch:
        if i < len(tracks):
            bad_path = tracks[i].get("dest_path", "")
            if bad_path:
                if await device_service.delete_espuino_file(espuino_ip, bad_path):
                    logger.info(f"Deleted corrupted file: {bad_path}")
                else:
                    logger.warning(f"Failed to delete corrupted file: {bad_path}")
    for seq, i in enumerate(retry_indices, start=1):
        track = tracks[i]
        source_path = Path(track.get("source_path", ""))
        dest_path = track.get("dest_path", "")
        track_name = track.get("name", f"Track {i + 1}")

        if source_path.exists():
            logger.info(f"Uploading track {seq}/{len(retry_indices)}: {track_name}")
            result = await device_service.upload_to_espuino(
                espuino_ip,
                source_path,
                dest_path,
                title=f"{title} - {track_name}",
                track_index=seq,
                total_tracks=len(retry_indices),
                max_kbps=idle_kbps,
            )
            if not result.get("success"):
                logger.warning(
                    f"Resume upload failed for track {i + 1}: {result.get('error')}"
                )
        else:
            logger.warning(f"Source file missing for track {i + 1}: {source_path}")

    # Re-upload metadata
    metadata = build_upload_metadata(
        pending.get("uid", ""),
        pending.get("series", ""),
        pending.get("episode", ""),
        tracks,
        pending.get("audio_url", ""),
    )
    # Add file sizes
    for i, track in enumerate(tracks):
        source_path = Path(track.get("source_path", ""))
        if source_path.exists():
            metadata["tracks"][i]["size"] = source_path.stat().st_size

    metadata_path = f"{folder_path}/metadata.json"
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(metadata, f, indent=2)
        temp_metadata = Path(f.name)
    try:
        await device_service.upload_to_espuino(
            espuino_ip,
            temp_metadata,
            metadata_path,
            title=f"{title} - metadata",
            total_tracks=len(retry_indices),
            max_kbps=idle_kbps,
            is_aux=True,
        )
    finally:
        temp_metadata.unlink(missing_ok=True)

    # Upload UID mapping for local cache lookup
    uid_map_path = build_espuino_uid_map_path(pending.get("uid", ""))
    uid_map = {
        "uid": pending.get("uid", ""),
        "folder": folder_path,
        "title": pending.get("series", "") or pending.get("episode", "") or "Tonie",
        "series": pending.get("series", ""),
        "episode": pending.get("episode", ""),
        "files": [
            {
                "index": i,
                "name": Path(t.get("dest_path", "")).name,
                "size": Path(t.get("source_path", "")).stat().st_size
                if Path(t.get("source_path", "")).exists()
                else 0,
            }
            for i, t in enumerate(tracks)
        ],
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(uid_map, f, indent=2)
        temp_uid_map = Path(f.name)
    try:
        await device_service.upload_to_espuino(
            espuino_ip,
            temp_uid_map,
            uid_map_path,
            title=f"{title} - uid-map",
            total_tracks=len(retry_indices),
            max_kbps=idle_kbps,
            is_aux=True,
        )
    finally:
        temp_uid_map.unlink(missing_ok=True)

    # Verify again
    verification = await device_service.verify_espuino_upload(
        espuino_ip,
        folder_path,
        uid_map_path=uid_map_path,
    )
    if verification.get("complete"):
        logger.info(f"Resume upload complete for {espuino_ip}")
        # Auto-link RFID to local folder after verification
        folder_for_link = verification.get("folder") or folder_path
        tag_id = uid_to_espuino_tag_id(pending.get("uid", ""))
        if tag_id:
            if await device_service.set_espuino_rfid_mapping(
                espuino_ip, tag_id, folder_for_link, play_mode=5
            ):
                logger.info(f"RFID mapping updated for {tag_id} -> {folder_for_link}")
            else:
                logger.warning(f"Failed to update RFID mapping for {tag_id}")
        device_service.clear_pending_upload(espuino_ip)
    else:
        logger.warning(f"Resume upload incomplete for {espuino_ip}: {verification}")


@app.get("/scans")
async def list_scans(limit: int = 20):
    """Get recent tag scans from all readers."""
    scans = list(recent_scans)[:limit]
    return {"count": len(scans), "scans": scans}


@app.get("/tonies")
async def list_tonies():
    """List all known tonies from TeddyCloud."""
    if not teddycloud_client:
        raise HTTPException(status_code=503, detail="TeddyCloud client not initialized")

    tonies = await teddycloud_client.get_tonies()
    return {"count": len(tonies), "tonies": tonies}


@app.get("/tags")
async def list_tags():
    """List all RFID tags with their linked TAF files."""
    if not teddycloud_client:
        raise HTTPException(status_code=503, detail="TeddyCloud client not initialized")

    settings = get_settings()
    tc_base = settings.teddycloud.url.rstrip("/")
    if tc_base.endswith("/web"):
        tc_base = tc_base[:-4]

    tags = await teddycloud_client.get_tag_index()

    # Enrich tags with audio URLs
    result = []
    for tag in tags:
        source = tag.get("source", "")
        info = tag.get("tonieInfo", {})

        # Build audio URL based on source type
        if source.startswith("lib://"):
            from urllib.parse import quote

            lib_path = source[6:]
            # URL-encode path (preserve slashes) and use /content/ for OGG conversion
            encoded_path = quote(lib_path, safe="/")
            audio_url = f"{tc_base}/content/{encoded_path}?ogg=true&special=library"
        elif tag.get("audioUrl"):
            audio_url = f"{tc_base}{tag['audioUrl']}"
        else:
            audio_url = ""

        result.append(
            {
                "uid": tag.get("uid", ""),
                "source": source,
                "taf_file": source[6:] if source.startswith("lib://") else "",
                "series": info.get("series", ""),
                "episode": info.get("episode", ""),
                "model": info.get("model", ""),
                "picture": info.get("picture", ""),
                "audio_url": audio_url,
                "valid": tag.get("valid", False),
                "exists": tag.get("exists", False),
            }
        )

    return {"count": len(result), "tags": result}


@app.get("/library")
async def list_library():
    """List all TAF files in the TeddyCloud library.

    Recursively scans subdirectories and returns all TAF files with metadata.
    This shows available audio content regardless of whether it's linked to a tag.
    """
    if not teddycloud_client:
        raise HTTPException(status_code=503, detail="TeddyCloud client not initialized")

    settings = get_settings()
    tc_base = settings.teddycloud.url.rstrip("/")
    if tc_base.endswith("/web"):
        tc_base = tc_base[:-4]

    files = await teddycloud_client.get_library_files()

    # Build audio URLs for each file and check cache status
    from urllib.parse import quote

    for f in files:
        path = f.get("path", "")
        # URL-encode the path (but preserve slashes for directory structure)
        encoded_path = quote(path, safe="/")
        # Use /content/ endpoint with special=library for OGG conversion
        # Note: /library/ endpoint does NOT convert, only /content/ does
        audio_url = f"{tc_base}/content/{encoded_path}?ogg=true&special=library"
        f["audio_url"] = audio_url
        # Generate a unique ID based on path (for UI consistency)
        f["uid"] = f"lib:{path}"
        # Check if this item is cached
        cache_dir = get_tonie_cache_dir(audio_url)
        f["cached"] = (cache_dir / "metadata.json").exists()

    return {"count": len(files), "files": files}


class PrefetchRequest(BaseModel):
    audio_url: str
    title: str = ""
    tracks: list[dict] | None = None


@app.get("/cache/prefetch")
async def get_prefetch_status(audio_url: str):
    """Get encoding status for a prefetch operation.

    Returns current encoding progress including track info.
    """
    encoding_status = get_encoding_status(audio_url)
    cache_dir = get_tonie_cache_dir(audio_url)
    cached = (cache_dir / "metadata.json").exists()

    return {
        "audio_url": audio_url,
        "cached": cached,
        "status": encoding_status.get("status", "unknown"),
        "progress": encoding_status.get("progress", 0),
        "current_track": encoding_status.get("current_track", 0),
        "total_tracks": encoding_status.get("total_tracks", 0),
    }


@app.post("/cache/prefetch")
async def prefetch_cache(request: PrefetchRequest):
    """Pre-cache an audio file without starting playback.

    Triggers background encoding so the file is ready for instant playback later.
    """
    audio_url = request.audio_url

    # Check if already cached
    cache_dir = get_tonie_cache_dir(audio_url)
    if (cache_dir / "metadata.json").exists():
        return {"status": "already_cached", "audio_url": audio_url}

    # Get tracks from request or create pseudo-track
    tracks = request.tracks or [{"name": "Full Audio", "duration": 7200, "start": 0}]

    # Set encoding status
    set_encoding_status(audio_url, "encoding", progress=0, total_tracks=len(tracks))

    # Start background encoding
    async def encode_prefetch():
        logger.info(f"Prefetch encoding: {audio_url[:60]}... ({len(tracks)} tracks)")
        try:
            metadata = await get_or_encode_tracks(
                source_url=audio_url,
                tracks=tracks,
                series="",
                episode=request.title,
            )
            if metadata:
                logger.info(f"Prefetch complete: {len(metadata.tracks)} tracks cached")
            else:
                logger.error("Prefetch encoding returned no metadata")
        except Exception as e:
            logger.error(f"Prefetch encoding failed: {e}")

    asyncio.create_task(encode_prefetch())

    return {"status": "encoding", "audio_url": audio_url, "tracks": len(tracks)}


@app.get("/proxy/image")
async def proxy_teddycloud_image(path: str):
    """Proxy images from TeddyCloud to avoid mixed content issues.

    When accessed via HTTPS reverse proxy, the browser blocks HTTP images.
    This endpoint fetches images from TeddyCloud and serves them through
    the same origin as the page.
    """
    import httpx

    settings = get_settings()
    tc_base = settings.teddycloud.url.rstrip("/")
    if tc_base.endswith("/web"):
        tc_base = tc_base[:-4]

    # Construct full URL
    # Encode path to handle spaces/special chars, but preserve slashes
    encoded_path = quote(path, safe="/")

    if encoded_path.startswith("/"):
        image_url = f"{tc_base}{encoded_path}"
    else:
        image_url = f"{tc_base}/{encoded_path}"

    try:
        # follow_redirects=True because TeddyCloud redirects /cache/*.jpg to /library/own/pics/*
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            response = await client.get(image_url)
            if response.status_code == 200:
                content_type = response.headers.get("content-type", "image/jpeg")
                return StreamingResponse(
                    iter([response.content]),
                    media_type=content_type,
                    headers={
                        "Cache-Control": "public, max-age=86400",
                        "X-Content-Type-Options": "nosniff",
                    },
                )
            # Return 404 for any non-200 response
            from fastapi.responses import JSONResponse

            return JSONResponse(
                status_code=404,
                content={"detail": "Image not found"},
                headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
            )
    except httpx.RequestError as e:
        logger.error(f"Proxy image error: {e}")
        raise HTTPException(status_code=502, detail=f"Failed to fetch image: {e}")


class PlayUrlRequest(BaseModel):
    audio_url: str
    title: str = "Tonie"
    device_type: str | None = None
    device_id: str | None = None


class PlayTonieRequest(BaseModel):
    uid: str
    reader_ip: str
    device_type: str | None = None
    device_id: str | None = None


class PositionUpdate(BaseModel):
    uid: str
    position: float


@app.post("/playback/tonie")
async def play_tonie(request: PlayTonieRequest):
    """Play a Tonie by UID without a physical tag."""
    device_override = None
    if request.device_type and request.device_id:
        device_override = {"type": request.device_type, "id": request.device_id}

    response = await play_tonie_for_reader(
        request.reader_ip,
        request.uid,
        device_override=device_override,
        record_scan=False,
    )
    return response


@app.post("/readers/{reader_ip}/position")
async def update_reader_position(reader_ip: str, update: PositionUpdate):
    """Update playback position for browser-based playback."""
    state = get_reader_state(reader_ip)
    current = state.get("current_tag")
    if not current or current.get("uid") != update.uid:
        return {"status": "ignored"}
    state["last_reported_position"] = max(0.0, float(update.position))
    return {"status": "ok"}


@app.post("/playback/url")
async def play_url(request: PlayUrlRequest, req: Request):
    """Play a specific audio URL on a device."""
    from urllib.parse import quote

    # Use provided device or fall back to active device
    if request.device_type and request.device_id:
        active_device = {"type": request.device_type, "id": request.device_id}
    else:
        active_device = device_service.get_active_device()
    device_type = active_device.get("type", "")
    settings = get_settings()

    if device_type in ["sonos", "airplay", "chromecast", "browser", "espuino"]:
        # Use MP3 for seekable playback (cached files, ~40s encoding)
        if settings.server_url:
            server_base = settings.server_url.rstrip("/")
        else:
            server_ip = get_local_ip()
            server_base = f"http://{server_ip}:8754"
        playback_url = f"{server_base}/transcode.mp3?url={quote(request.audio_url)}"

        # For network devices, encoding happens when /transcode.mp3 is requested
        # Don't pre-set encoding status - let transcode endpoint handle it
        if device_type in ["sonos", "airplay", "chromecast", "espuino"]:
            cache_dir = get_tonie_cache_dir(request.audio_url)
            metadata_path = cache_dir / "metadata.json"
            if metadata_path.exists():
                logger.info(f"Using cached MP3 for {device_type} playback")
            else:
                logger.info(f"MP3 will be encoded when {device_type} requests it")
        elif device_type == "browser":
            # Start background encoding for browser playback
            # Use multi-track encoding with pseudo-track for arbitrary URLs
            cache_dir = get_tonie_cache_dir(request.audio_url)
            metadata_path = cache_dir / "metadata.json"

            if not metadata_path.exists():
                set_encoding_status(
                    request.audio_url, "encoding", progress=0, total_tracks=1
                )

                async def encode_for_browser():
                    logger.info(
                        f"Starting background MP3 encoding for browser: {request.audio_url[:60]}..."
                    )
                    try:
                        pseudo_tracks = [
                            {"name": "Full Audio", "duration": 7200, "start": 0}
                        ]
                        metadata = await get_or_encode_tracks(
                            source_url=request.audio_url,
                            tracks=pseudo_tracks,
                            series="",
                            episode=request.title,
                        )
                        if metadata:
                            logger.info(
                                "Background MP3 encoding complete for browser playback"
                            )
                        else:
                            logger.error("Background MP3 encoding returned no metadata")
                    except Exception as e:
                        logger.error(f"Background MP3 encoding failed: {e}")

                asyncio.create_task(encode_for_browser())
    else:
        playback_url = request.audio_url

    # Track stream in reader_states so Now Playing UI shows it
    # /playback/url is only for web UI - ESPuino uses /tonie endpoint
    # Use device-specific reader ID for multi-stream support
    reader_ip = (
        f"web-{active_device.get('type', 'browser')}-{active_device.get('id', 'web')}"
    )
    state = get_reader_state(reader_ip)
    state["current_tag"] = {
        "uid": f"url:{hash(request.audio_url) % 10000000}",  # Synthetic UID for URL
        "series": None,
        "episode": None,
        "title": request.title,
        "picture": None,
        "audio_url": request.audio_url,
        "playback_url": playback_url,
        "placed_at": datetime.now().isoformat(),
        "start_position": 0,
    }
    state["current_started_at"] = time.time()
    state["current_offset"] = 0.0
    state["current_device"] = active_device
    state["last_reported_position"] = 0.0

    # For non-browser devices, actually start playback
    if device_type != "browser":
        success = await device_service.play_on_device(
            active_device, playback_url, request.title
        )
    else:
        success = True  # Browser handles playback via audio element in UI

    return {
        "status": "ok" if success else "error",
        "audio_url": request.audio_url,
        "playback_url": playback_url,
    }


@app.get("/transcode")
@app.get("/transcode.{ext}")  # Allow /transcode.mp3, /transcode.flac, etc.
async def transcode_audio(
    url: str, format: str = "mp3", ext: str | None = None, stream: bool = False
):
    """
    Transcode audio from TeddyCloud URL to a device-compatible format.

    All devices use MP3 (CBR 192kbps, ~30s encoding) for stable streaming.

    Args:
        url: Source audio URL (OGG/Opus from TeddyCloud)
        format: Output format (mp3, flac, wav). Default: mp3
        ext: File extension from URL path (overrides format param)
        stream: If true, use legacy streaming instead of cached file
    """
    # Use extension from path if provided (e.g., /transcode.mp3)
    if ext:
        format = ext

    # MP3 cached file for seekable playback
    if format == "mp3" and not stream:
        logger.info(f"MP3 request: url={url[:80]}...")
        settings = get_settings()
        cover_url = ""

        try:
            # First check if we have cached files (multi-track or legacy)
            cache_path = await get_or_serve_cached_mp3(url)
            if cache_path:
                logger.info(f"Serving cached MP3: {cache_path.name}")
                return FileResponse(
                    path=cache_path,
                    media_type="audio/mpeg",
                    filename="audio.mp3",
                    headers={
                        "Accept-Ranges": "bytes",
                        "Cache-Control": "public, max-age=3600",
                    },
                )

            # Check if encoding is in progress - wait for it
            encoding_status = get_encoding_status(url)
            if encoding_status.get("status") == "encoding":
                logger.info(
                    f"Encoding in progress, waiting... ({encoding_status.get('progress', 0):.0f}%)"
                )
                # Wait for encoding to complete (poll every 2 seconds, max 5 mins)
                for _ in range(150):
                    await asyncio.sleep(2)
                    encoding_status = get_encoding_status(url)
                    if encoding_status.get("status") != "encoding":
                        break
                    logger.debug(
                        f"Still encoding... {encoding_status.get('progress', 0):.0f}%"
                    )

                # Check cache again
                cache_path = await get_or_serve_cached_mp3(url)
                if cache_path:
                    logger.info(f"Serving freshly encoded MP3: {cache_path.name}")
                    return FileResponse(
                        path=cache_path,
                        media_type="audio/mpeg",
                        filename="audio.mp3",
                        headers={
                            "Accept-Ranges": "bytes",
                            "Cache-Control": "public, max-age=3600",
                        },
                    )

            # No cache and no encoding in progress - encode multi-track
            logger.info(f"No cache found, encoding multi-track...")

            matching_tag = None
            tracks = []
            series = ""
            episode = ""

            if teddycloud_client:
                # Try to find this Tonie in the tag index
                tags = await teddycloud_client.get_tag_index()
                for tag in tags:
                    audio_path = tag.get("audio_path", "")
                    if audio_path and audio_path in url:
                        matching_tag = tag
                        break

                if matching_tag:
                    cover_url = build_cover_url(
                        matching_tag.get("picture", ""), settings
                    )
                    tracks = matching_tag.get("tracks", [])
                    series = matching_tag.get("series", "")
                    episode = matching_tag.get("episode", "")

            # If no track info, create single pseudo-track for full duration
            if not tracks:
                duration = matching_tag.get("duration", 0) if matching_tag else 0
                tracks = [{"name": "Full Audio", "duration": duration or 3600}]
                logger.info("No track info, using single pseudo-track")

            logger.info(f"Encoding {len(tracks)} track(s) to multi-track cache...")
            metadata = await get_or_encode_tracks(
                source_url=url,
                tracks=tracks,
                series=series,
                episode=episode,
                cover_url=cover_url,
            )

            if metadata:
                cache_path = await get_or_serve_cached_mp3(url)
                if cache_path:
                    return FileResponse(
                        path=cache_path,
                        media_type="audio/mpeg",
                        filename="audio.mp3",
                        headers={
                            "Accept-Ranges": "bytes",
                            "Cache-Control": "public, max-age=3600",
                        },
                    )

            raise HTTPException(status_code=500, detail="Failed to encode audio")
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"MP3 encoding failed: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    # Legacy streaming for other formats
    if format not in ["flac", "wav"]:
        raise HTTPException(
            status_code=400, detail="Invalid format. Use: mp3, flac, wav"
        )

    logger.info(f"Streaming transcode request: format={format}, url={url[:80]}...")

    try:
        return StreamingResponse(
            transcode_stream(url, output_format=format),
            media_type=get_content_type(format),
            headers={
                "Content-Disposition": f'inline; filename="audio.{format}"',
                "Accept-Ranges": "none",
                "Cache-Control": "no-cache",
            },
        )
    except Exception as e:
        logger.error(f"Transcoding failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/tracks/{cache_key}/{track_num}.mp3")
async def get_track(cache_key: str, track_num: int):
    """Serve an individual track MP3 file from the cache.

    Args:
        cache_key: Cache folder name (hash of source URL)
        track_num: Track number (1-indexed)
    """
    from .services.transcoding import CACHE_DIR

    track_path = CACHE_DIR / cache_key / f"{track_num:02d}.mp3"

    if not track_path.exists():
        raise HTTPException(status_code=404, detail=f"Track {track_num} not found")

    return FileResponse(
        path=track_path,
        media_type="audio/mpeg",
        filename=f"{track_num:02d}.mp3",
        headers={
            "Accept-Ranges": "bytes",
            "Cache-Control": "public, max-age=3600",
        },
    )


@app.get("/tracks/{cache_key}/metadata.json")
async def get_track_metadata(cache_key: str):
    """Get metadata for a cached Tonie including track list.

    Args:
        cache_key: Cache folder name (hash of source URL)
    """
    from .services.transcoding import CACHE_DIR
    import json

    metadata_path = CACHE_DIR / cache_key / "metadata.json"

    if not metadata_path.exists():
        raise HTTPException(status_code=404, detail="Metadata not found")

    try:
        with open(metadata_path) as f:
            return json.load(f)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/playlist/{cache_key}.m3u")
async def get_playlist_m3u(cache_key: str, request: Request):
    """Return an M3U playlist with all track URLs for ESPuino LOCAL_M3U mode.

    This allows ESPuino to play multiple tracks with skip support.

    Args:
        cache_key: Cache folder name (hash of source URL)
    """
    from .services.transcoding import CACHE_DIR
    import json

    metadata_path = CACHE_DIR / cache_key / "metadata.json"

    if not metadata_path.exists():
        raise HTTPException(
            status_code=404, detail="Playlist not found - encoding may not be complete"
        )

    try:
        with open(metadata_path) as f:
            metadata = json.load(f)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    tracks = metadata.get("tracks", [])
    if not tracks:
        raise HTTPException(status_code=404, detail="No tracks in playlist")

    # Build server base URL
    server_base = str(request.base_url).rstrip("/")

    # Generate M3U content
    m3u_lines = ["#EXTM3U"]
    for track in tracks:
        track_num = track.get("index", 0) + 1
        track_name = track.get("name", f"Track {track_num}")
        duration = track.get("duration", -1)

        # Add EXTINF line with duration and title
        m3u_lines.append(f"#EXTINF:{duration},{track_name}")
        # Add track URL
        m3u_lines.append(f"{server_base}/tracks/{cache_key}/{track_num:02d}.mp3")

    m3u_content = "\n".join(m3u_lines) + "\n"

    return Response(
        content=m3u_content,
        media_type="audio/x-mpegurl",
        headers={
            "Content-Disposition": f'attachment; filename="{cache_key}.m3u"',
            "Cache-Control": "no-cache",
        },
    )


@app.get("/transcode/test")
async def test_transcode():
    """Test transcoding with a simple audio file."""
    settings = get_settings()
    tc_base = settings.teddycloud.url.rstrip("/")

    # Get a sample audio URL from tags
    if teddycloud_client:
        tags = await teddycloud_client.get_tag_index()
        if tags:
            tag = tags[0]
            source = tag.get("source", "")
            if source.startswith("lib://"):
                from urllib.parse import quote as url_quote

                lib_path = source[6:]
                # URL-encode path and use /content/ with special=library for OGG conversion
                encoded_path = url_quote(lib_path, safe="/")
                test_url = f"{tc_base}/content/{encoded_path}?ogg=true&special=library"
            else:
                test_url = f"{tc_base}{tag.get('audioUrl', '')}"

            server_ip = get_local_ip()
            from urllib.parse import quote

            # Use MP3 for seekable playback (default)
            transcode_url = (
                f"http://{server_ip}:8754/transcode.mp3?url={quote(test_url)}"
            )
            # Legacy streaming URL
            stream_url = f"http://{server_ip}:8754/transcode.flac?url={quote(test_url)}&stream=true"

            return {
                "source_url": test_url,
                "transcode_url": transcode_url,
                "stream_url": stream_url,
                "cache_stats": get_cache_stats(),
                "test_with_curl": f"curl -I '{transcode_url}'",
                "test_with_vlc": f"vlc '{transcode_url}'",
            }

    return {"error": "No tags available for testing"}


@app.get("/cache")
async def cache_stats():
    """Get audio cache statistics."""
    return get_cache_stats()


@app.delete("/cache")
async def cache_clear():
    """Clear all cached audio files."""
    deleted = clear_cache()
    return {"status": "ok", "files_deleted": deleted}


@app.get("/devices")
async def list_devices():
    """List all playback devices (discovered + manual)."""
    return device_service.get_all_devices()


class DeviceDefaultRequest(BaseModel):
    type: str  # sonos, airplay, spotify
    id: str  # device identifier


@app.post("/devices/discover")
async def discover_devices():
    """Discover all playback devices on the network."""
    settings = get_settings()
    devices = await device_service.discover_all_devices(
        spotify_client_id=settings.spotify_client_id,
        spotify_client_secret=settings.spotify_client_secret,
    )
    return devices


@app.post("/devices/default")
async def set_default_device(request: DeviceDefaultRequest):
    """Set the default playback device."""
    device_service.set_default_device(request.type, request.id)
    return {"status": "ok", "type": request.type, "id": request.id}


@app.get("/devices/default")
async def get_default_device():
    """Get the current default playback device."""
    return device_service.get_default_device()


@app.post("/devices/current")
async def set_current_device(request: DeviceDefaultRequest):
    """Set temporary current device (not persisted)."""
    device_service.set_current_device(request.type, request.id)
    return {"status": "ok", "type": request.type, "id": request.id, "temporary": True}


@app.delete("/devices/current")
async def clear_current_device():
    """Clear temporary device, fall back to default."""
    device_service.clear_current_device()
    return {"status": "ok"}


@app.get("/devices/active")
async def get_active_device():
    """Get the active device (current if set, otherwise default)."""
    active = device_service.get_active_device()
    current = device_service.current_device
    return {
        **active,
        "is_temporary": current is not None,
    }


@app.get("/readers/{reader_ip}/device")
async def get_reader_device(reader_ip: str):
    """Get the reader-specific playback device override."""
    device = device_service.get_reader_device_override(reader_ip)
    if device:
        return {"type": device.get("type"), "id": device.get("id")}
    return {"type": None, "id": None}


@app.post("/readers/{reader_ip}/device")
async def set_reader_device(reader_ip: str, request: DeviceDefaultRequest):
    """Set a reader-specific playback device override."""
    device = device_service.set_reader_device(reader_ip, request.type, request.id)
    return {"status": "ok", "reader_ip": reader_ip, "device": device}


@app.delete("/readers/{reader_ip}/device")
async def clear_reader_device(reader_ip: str):
    """Clear a reader-specific playback device override."""
    cleared = device_service.clear_reader_device(reader_ip)
    return {"status": "ok" if cleared else "not_found", "reader_ip": reader_ip}


@app.post("/readers/{reader_ip}/device/current")
async def set_reader_current_device(reader_ip: str, request: DeviceDefaultRequest):
    """Set a temporary reader-specific playback device override.

    If a tag is currently playing, stops current playback and restarts on new device.
    """
    # Set the new device
    device = device_service.set_reader_current_device(
        reader_ip, request.type, request.id
    )

    # If a tag is currently playing, restart playback on new device
    state = get_reader_state(reader_ip)
    current = state.get("current_tag")
    if current and current.get("uid"):
        logger.info(f"Switching playback to new device for {reader_ip}")
        # Stop current playback
        old_device = state.get("current_device")
        if old_device:
            await device_service.stop_device(old_device)

        # Update current device
        state["current_device"] = device

        # Rebuild playback URL for new device (browser uses original, others need transcode)
        audio_url = current.get("audio_url")
        if audio_url:
            settings = get_settings()
            playback_url = build_playback_url(audio_url, request.type, settings)
            current["playback_url"] = playback_url

            # Pre-encode for network devices (they timeout waiting for encoding)
            if request.type in ["sonos", "airplay", "chromecast"]:
                logger.info(f"Pre-encoding MP3 for device switch to {request.type}...")
                try:
                    cover_url = (
                        build_cover_url(current.get("picture", ""), settings)
                        if current
                        else ""
                    )
                    # Use multi-track encoding with pseudo-track
                    pseudo_tracks = [
                        {"name": "Full Audio", "duration": 7200, "start": 0}
                    ]
                    await get_or_encode_tracks(
                        source_url=audio_url,
                        tracks=pseudo_tracks,
                        cover_url=cover_url,
                    )
                except Exception as e:
                    logger.error(f"Pre-encoding failed: {e}")

            title = current.get("title") or current.get("series") or "Tonie"
            await device_service.play_on_device(device, playback_url, title)

    return {"status": "ok", "reader_ip": reader_ip, "device": device, "temporary": True}


@app.delete("/readers/{reader_ip}/device/current")
async def clear_reader_current_device(reader_ip: str):
    """Clear a temporary reader-specific playback device override."""
    cleared = device_service.clear_reader_current_device(reader_ip)
    return {"status": "ok" if cleared else "not_found", "reader_ip": reader_ip}


# Playback control endpoints
@app.post("/playback/play")
async def playback_play():
    """Resume playback on default device."""
    success = await device_service.play_default_device()
    return {"status": "ok" if success else "error", "action": "play"}


@app.post("/playback/pause")
async def playback_pause():
    """Pause playback on default device."""
    success = await device_service.pause_default_device()
    return {"status": "ok" if success else "error", "action": "pause"}


@app.post("/playback/stop")
async def playback_stop():
    """Stop playback on default device."""
    success = await device_service.stop_default_device()
    return {"status": "ok" if success else "error", "action": "stop"}


@app.post("/readers/{reader_ip}/playback/play")
async def reader_playback_play(reader_ip: str):
    """Resume playback for a reader."""
    state = get_reader_state(reader_ip)
    # Use the actual playing device, not the default
    device = state.get("current_device") or device_service.get_device_for_reader(
        reader_ip
    )
    current = state.get("current_tag")
    resume = state.get("resume") or {}
    resume_device = resume.get("device")
    same_device = bool(
        resume_device
        and resume_device.get("type") == device.get("type")
        and resume_device.get("id") == device.get("id")
    )
    should_resume = bool(
        current
        and resume.get("uid") == current.get("uid")
        and resume.get("paused")
        and same_device
    )

    success = False
    if should_resume:
        success = await device_service.resume_device(device)
        if not success:
            playback_url = current.get("playback_url", "")
            title = current.get("title") or current.get("series") or "Tonie"
            start_position = float(resume.get("position", 0.0))
            success = await device_service.play_on_device(
                device,
                playback_url,
                title,
                start_position=start_position,
            )
        if success:
            state["current_started_at"] = time.time()
            state["current_offset"] = float(resume.get("position", 0.0))
            state["last_reported_position"] = float(resume.get("position", 0.0))
            state["resume"] = None
    else:
        success = await device_service.resume_device(device)

    return {
        "status": "ok" if success else "error",
        "action": "play",
        "reader_ip": reader_ip,
    }


@app.post("/readers/{reader_ip}/playback/pause")
async def reader_playback_pause(reader_ip: str):
    """Pause playback for a reader."""
    state = get_reader_state(reader_ip)
    # Use the actual playing device, not the default
    device = state.get("current_device") or device_service.get_device_for_reader(
        reader_ip
    )
    current = state.get("current_tag")
    if current:
        position = await get_resume_position(reader_ip, device)
        state["resume"] = {
            "uid": current.get("uid", ""),
            "position": position,
            "device": device,
            "paused": True,
        }
        state["current_offset"] = position
        state["current_started_at"] = None
        state["last_reported_position"] = position
    success = await device_service.pause_device(device)
    return {
        "status": "ok" if success else "error",
        "action": "pause",
        "reader_ip": reader_ip,
    }


@app.post("/readers/{reader_ip}/playback/stop")
async def reader_playback_stop(reader_ip: str):
    """Stop playback for a reader and save resume position."""
    await stop_reader_playback(reader_ip, save_resume=True)
    return {"status": "ok", "action": "stop", "reader_ip": reader_ip}


class SeekRequest(BaseModel):
    position: float  # Position in seconds


@app.post("/readers/{reader_ip}/playback/seek")
async def reader_playback_seek(reader_ip: str, request: SeekRequest):
    """Seek to a position in the current playback for a reader."""
    state = get_reader_state(reader_ip)
    device = state.get("current_device") or device_service.get_device_for_reader(
        reader_ip
    )

    if device.get("type") == "browser":
        # Browser seek is handled client-side
        return {
            "status": "ok",
            "action": "seek",
            "position": request.position,
            "reader_ip": reader_ip,
        }

    success = await device_service.seek_device(device, request.position)
    if success:
        state["current_offset"] = request.position
        state["current_started_at"] = time.time()
        state["last_reported_position"] = request.position

    return {
        "status": "ok" if success else "error",
        "action": "seek",
        "position": request.position,
        "reader_ip": reader_ip,
    }


@app.post("/readers/{reader_ip}/playback/next")
async def reader_playback_next(reader_ip: str):
    state = get_reader_state(reader_ip)
    device = state.get("current_device") or device_service.get_device_for_reader(
        reader_ip
    )

    if not device or device.get("type") == "browser":
        return {"status": "error", "error": "Next track not supported for browser"}

    success = False
    device_type = device.get("type")
    device_id = device.get("id", "")

    if device_type == "sonos":
        ip = device_service.get_sonos_ip_from_uid(device_id)
        if ip:
            success = await device_service.next_track_sonos(ip)

    return {
        "status": "ok" if success else "error",
        "action": "next",
        "reader_ip": reader_ip,
    }


@app.post("/readers/{reader_ip}/playback/prev")
async def reader_playback_prev(reader_ip: str):
    state = get_reader_state(reader_ip)
    device = state.get("current_device") or device_service.get_device_for_reader(
        reader_ip
    )

    if not device or device.get("type") == "browser":
        return {"status": "error", "error": "Prev track not supported for browser"}

    success = False
    device_type = device.get("type")
    device_id = device.get("id", "")

    if device_type == "sonos":
        ip = device_service.get_sonos_ip_from_uid(device_id)
        if ip:
            success = await device_service.prev_track_sonos(ip)

    return {
        "status": "ok" if success else "error",
        "action": "prev",
        "reader_ip": reader_ip,
    }


class ControlRequest(BaseModel):
    """Remote control command from ESPuino in stream mode."""

    action: str  # "play", "pause", "stop", "skip", "prev", "volume_up", "volume_down"
    reader_ip: str  # ESPuino IP that sent the command


@app.post("/control")
async def handle_control_command(request: ControlRequest):
    """
    Handle playback control from ESPuino acting as remote in stream mode.

    When ESPuino is in stream mode (playing on Sonos/etc), button presses
    are forwarded here to control the actual playback device.
    """
    reader_ip = request.reader_ip
    state = get_reader_state(reader_ip)

    # Check if this reader has an active stream
    current_tag = state.get("current_tag")
    if not current_tag:
        logger.warning(f"Control command from {reader_ip} but no active stream")
        return {"status": "error", "error": "No active stream"}

    # Get the target device (either from stream mode or default)
    device = state.get("target_device") or state.get("current_device")
    if not device:
        device = device_service.get_device_for_reader(reader_ip)

    if not device or not device.get("type"):
        logger.warning(f"Control command from {reader_ip} but no device configured")
        return {"status": "error", "error": "No device configured"}

    action = request.action.lower()
    success = False

    logger.info(
        f"Control command from {reader_ip}: {action} -> {device['type']}:{device['id']}"
    )

    if action == "play":
        # Toggle play/pause - check current state and do opposite
        is_playing = await device_service.is_device_playing(device)
        if is_playing:
            success = await device_service.pause_device(device)
        else:
            success = await device_service.resume_device(device)
    elif action == "pause":
        success = await device_service.pause_device(device)
    elif action == "stop":
        success = await device_service.stop_device(device)
        # Also clear the stream state
        state["current_tag"] = None
        state["mode"] = "local"
        state["target_device"] = None
    elif action == "skip":
        # Skip forward 60 seconds
        current_pos = state.get("last_reported_position", 0)
        duration = current_tag.get("duration", 0)
        if duration > 0:
            new_pos = min(current_pos + 60, duration - 1)  # Skip 60 seconds forward
            success = await device_service.seek_device(device, new_pos)
            if success:
                state["last_reported_position"] = new_pos
        else:
            success = False
    elif action == "prev":
        # Skip back 60 seconds (minimum 0)
        current_pos = state.get("last_reported_position", 0)
        new_pos = max(current_pos - 60, 0)  # Skip 60 seconds back
        success = await device_service.seek_device(device, new_pos)
        if success:
            state["last_reported_position"] = new_pos
    elif action in ("volume_up", "volume_down"):
        # Volume control - not all devices support this
        logger.info(f"Volume control not implemented for {device['type']}")
        success = True  # Acknowledge but don't fail
    else:
        logger.warning(f"Unknown control action: {action}")
        return {"status": "error", "error": f"Unknown action: {action}"}

    return {
        "status": "ok" if success else "error",
        "action": action,
        "reader_ip": reader_ip,
    }


class ManualDeviceRequest(BaseModel):
    type: str  # sonos, airplay, spotify
    ip: str  # IP address
    name: str = ""  # Optional name


@app.post("/devices/add")
async def add_manual_device(request: ManualDeviceRequest):
    """Manually add a device by IP (for when discovery doesn't work)."""
    device = await device_service.add_manual_device(
        request.type, request.name or f"{request.type} ({request.ip})", request.ip
    )
    if device:
        return {"status": "ok", "device": device}
    raise HTTPException(status_code=400, detail="Failed to add device")


@app.delete("/devices/{device_type}/{ip}")
async def remove_device(device_type: str, ip: str):
    """Remove a manually added device."""
    device_service.remove_manual_device(device_type, ip)
    return {"status": "ok"}


@app.get("/tonieboxes")
async def list_tonieboxes():
    """List registered Tonieboxes from TeddyCloud."""
    if not teddycloud_client:
        raise HTTPException(status_code=503, detail="TeddyCloud client not initialized")

    boxes = await teddycloud_client.get_tonieboxes()
    return {"count": len(boxes), "tonieboxes": boxes}


class SettingsUpdate(BaseModel):
    teddycloud_url: str | None = None
    server_url: str | None = None
    default_playback_target: str | None = None
    spotify_client_id: str | None = None
    spotify_client_secret: str | None = None
    audio_cache_max_mb: int | None = None


@app.get("/settings")
async def get_current_settings():
    """Get current application settings."""
    return get_editable_settings()


@app.put("/settings")
async def update_current_settings(updates: SettingsUpdate):
    """Update application settings (persisted to settings.json)."""
    global teddycloud_client

    # Filter out None values
    changes = {k: v for k, v in updates.model_dump().items() if v is not None}

    if not changes:
        return {"status": "no changes"}

    # Update settings
    settings = update_settings(changes)

    # If TeddyCloud URL changed, reinitialize the client
    if "teddycloud_url" in changes:
        if teddycloud_client:
            await teddycloud_client.close()
        teddycloud_client = TeddyCloudClient(
            base_url=settings.teddycloud.url,
            api_base=settings.teddycloud.api_base,
            timeout=settings.teddycloud.timeout,
        )
        connected = await teddycloud_client.check_connection()
        logger.info(f"Reconnected to TeddyCloud: {connected}")

    return {"status": "ok", "settings": get_editable_settings()}


# =============================================
# User Preferences (persisted to preferences.json)
# =============================================
class PreferencesUpdate(BaseModel):
    recentlyPlayed: list[str] | list[dict] | None = (
        None  # Accept both UIDs and full objects
    )
    hiddenItems: list[str] | None = None
    starredDevices: list[str] | None = None


@app.get("/preferences")
async def get_user_preferences():
    """Get user preferences (recently played, hidden items, starred devices)."""
    return get_preferences()


@app.put("/preferences")
async def update_user_preferences(updates: PreferencesUpdate):
    """Update user preferences (persisted to preferences.json)."""
    # Filter out None values
    changes = {k: v for k, v in updates.model_dump().items() if v is not None}

    if not changes:
        return {"status": "no changes"}

    prefs = update_preferences(changes)
    return {"status": "ok", "preferences": prefs}


@app.post("/preferences/recently-played")
async def add_to_recently_played(item: dict):
    """Add an item to recently played list."""
    prefs = get_preferences()
    recently = prefs.get("recentlyPlayed", [])

    # Remove existing entry with same UID
    recently = [r for r in recently if r.get("uid") != item.get("uid")]

    # Add to front
    recently.insert(0, item)

    # Limit to 12 items
    recently = recently[:12]

    update_preferences({"recentlyPlayed": recently})
    return {"status": "ok", "count": len(recently)}


@app.post("/preferences/hidden/{uid}")
async def hide_item(uid: str):
    """Add an item to hidden list."""
    prefs = get_preferences()
    hidden = prefs.get("hiddenItems", [])

    if uid not in hidden:
        hidden.append(uid)
        update_preferences({"hiddenItems": hidden})

    return {"status": "ok", "hidden": True}


@app.delete("/preferences/hidden/{uid}")
async def unhide_item(uid: str):
    """Remove an item from hidden list."""
    prefs = get_preferences()
    hidden = prefs.get("hiddenItems", [])

    if uid in hidden:
        hidden.remove(uid)
        update_preferences({"hiddenItems": hidden})

    return {"status": "ok", "hidden": False}


# =============================================
# Cache Management
# =============================================


@app.delete("/cache/{uid:path}")
async def delete_tonie_cache(uid: str):
    """Delete ToniePlayer cache for a specific Tonie UID.

    This forces re-encoding on next play. Use when cache is corrupted
    or you want to re-upload files to ESPuino.

    UID format: "E0:04:03:50:13:16:80:4B" or URL-encoded
    """
    import shutil
    from urllib.parse import unquote

    uid = unquote(uid)
    logger.info(f"Cache delete request for UID: {uid}")

    # Find the tonie by UID to get audio_url
    if not teddycloud_client:
        raise HTTPException(status_code=503, detail="TeddyCloud client not initialized")

    tags = await teddycloud_client.get_tag_index()
    matching_tag = None
    for tag in tags:
        tag_uid = tag.get("uid", "")
        if tag_uid == uid or tag_uid.replace(":", "") == uid.replace(":", ""):
            matching_tag = tag
            break

    if not matching_tag:
        raise HTTPException(status_code=404, detail=f"Tonie not found: {uid}")

    # Get audio URL and cache directory
    settings = get_settings()
    audio_url = build_audio_url(matching_tag, uid, settings)
    cache_dir = get_tonie_cache_dir(audio_url)

    if not cache_dir.exists():
        return {"status": "not_found", "message": "No cache exists for this Tonie"}

    # Delete cache directory
    shutil.rmtree(cache_dir)
    logger.info(f"Deleted cache for {uid}: {cache_dir}")

    return {
        "status": "ok",
        "message": f"Cache deleted for {matching_tag.get('series', '')} - {matching_tag.get('episode', '')}",
        "cache_dir": str(cache_dir.name),
    }


class ReuploadRequest(BaseModel):
    espuino_ip: str


@app.post("/cache/{uid:path}/reupload")
async def reupload_to_espuino(uid: str, request: ReuploadRequest):
    """Force re-upload of cached files to ESPuino SD card.

    Use when ESPuino lost its local files or mapping but ToniePlayer
    still has the cache. This triggers upload without re-encoding.

    UID format: "E0:04:03:50:13:16:80:4B" or URL-encoded
    """
    import json
    import tempfile
    from urllib.parse import unquote

    uid = unquote(uid)
    espuino_ip = request.espuino_ip
    logger.info(f"Re-upload request for UID: {uid} to ESPuino: {espuino_ip}")

    # Find the tonie by UID
    if not teddycloud_client:
        raise HTTPException(status_code=503, detail="TeddyCloud client not initialized")

    tags = await teddycloud_client.get_tag_index()
    matching_tag = None
    for tag in tags:
        tag_uid = tag.get("uid", "")
        if tag_uid == uid or tag_uid.replace(":", "") == uid.replace(":", ""):
            matching_tag = tag
            break

    if not matching_tag:
        raise HTTPException(status_code=404, detail=f"Tonie not found: {uid}")

    # Get audio URL and check cache
    settings = get_settings()
    audio_url = build_audio_url(matching_tag, uid, settings)
    cache_dir = get_tonie_cache_dir(audio_url)
    metadata_path = cache_dir / "metadata.json"

    if not metadata_path.exists():
        raise HTTPException(
            status_code=404, detail="No cache exists - play the Tonie first to encode"
        )

    # Load metadata
    with open(metadata_path) as f:
        metadata = json.load(f)

    tracks = metadata.get("tracks", [])
    if not tracks:
        raise HTTPException(status_code=400, detail="No tracks in cache metadata")

    series = matching_tag.get("series", "")
    episode = matching_tag.get("episode", "")
    title = (
        f"{series} - {episode}" if series and episode else series or episode or "Tonie"
    )

    # Build destination path
    dest_folder, uid_clean = build_espuino_dest_path(uid, series, episode)

    # Upload each track
    uploaded = 0
    for i, track in enumerate(tracks):
        track_path = cache_dir / f"{i + 1:02d}.mp3"
        if track_path.exists():
            dest_path = (
                f"{dest_folder}/{i + 1:02d}_{track.get('name', f'Track_{i + 1}')}.mp3"
            )
            logger.info(f"Uploading track {i + 1}/{len(tracks)}: {dest_path}")

            result = await device_service.upload_to_espuino(
                espuino_ip,
                track_path,
                dest_path,
                title=f"{title} - track {i + 1}",
                total_tracks=len(tracks),
            )
            if result.get("success"):
                uploaded += 1
            else:
                logger.warning(f"Track upload failed: {result.get('error')}")

    # Upload UID mapping
    uid_map_path = f"/teddycloud/uids/{uid_clean}.json"
    temp_uid_map = Path(tempfile.gettempdir()) / f"uid_map_{uid_clean}.json"
    uid_map_content = {"uid": uid, "path": f"/{dest_folder}"}

    try:
        with open(temp_uid_map, "w") as f:
            json.dump(uid_map_content, f)

        map_result = await device_service.upload_to_espuino(
            espuino_ip,
            temp_uid_map,
            uid_map_path,
            title=f"{title} - uid-map",
            total_tracks=1,
            is_aux=True,
        )
        if map_result.get("success"):
            logger.info(f"Uploaded UID map: {uid_map_path}")
        else:
            logger.warning(f"UID map upload failed: {map_result.get('error')}")
    finally:
        temp_uid_map.unlink(missing_ok=True)

    return {
        "status": "ok",
        "message": f"Uploaded {uploaded}/{len(tracks)} tracks to {espuino_ip}",
        "dest_folder": dest_folder,
        "tracks_uploaded": uploaded,
        "tracks_total": len(tracks),
    }


@app.get("/cache")
async def get_cache_info():
    """Get cache statistics and list of cached Tonies."""
    from .services.transcoding import CACHE_DIR, get_cache_stats
    import json

    stats = get_cache_stats()

    # List cached Tonies with metadata
    cached_tonies = []
    if CACHE_DIR.exists():
        for folder in CACHE_DIR.iterdir():
            if folder.is_dir():
                metadata_path = folder / "metadata.json"
                if metadata_path.exists():
                    try:
                        with open(metadata_path) as f:
                            metadata = json.load(f)
                        mp3_files = list(folder.glob("*.mp3"))
                        cached_tonies.append(
                            {
                                "cache_key": folder.name,
                                "series": metadata.get("series", ""),
                                "episode": metadata.get("episode", ""),
                                "tracks": len(metadata.get("tracks", [])),
                                "files": len(mp3_files),
                                "size_mb": round(
                                    sum(f.stat().st_size for f in mp3_files)
                                    / 1024
                                    / 1024,
                                    1,
                                ),
                            }
                        )
                    except Exception:
                        pass

    return {
        "stats": stats,
        "cached_tonies": cached_tonies,
    }


@app.get("/{full_path:path}", include_in_schema=False)
async def serve_spa(full_path: str):
    """Serve Svelte SPA - all non-API routes return index.html"""
    # Check if it's a static asset request that wasn't caught
    file_path = STATIC_DIR / full_path
    if file_path.exists() and file_path.is_file():
        return FileResponse(file_path)

    # Otherwise return index.html for client-side routing
    return FileResponse(STATIC_DIR / "index.html")
