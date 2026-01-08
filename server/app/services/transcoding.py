"""Audio transcoding service with multi-track MP3 caching for seekable playback."""

import asyncio
import hashlib
import json
import logging
import os
import subprocess
import tempfile
import shutil
import time
from pathlib import Path
from typing import AsyncGenerator
from dataclasses import dataclass, asdict

logger = logging.getLogger(__name__)

# Track encoding status for live feedback
# Key: tonie_key (hash of source URL), Value: dict with status info
_encoding_status: dict[str, dict] = {}

# Locks to prevent concurrent encoding of the same Tonie
_encoding_locks: dict[str, asyncio.Lock] = {}

# HTTP client for sending progress to ESPuino
import httpx

_espuino_client: httpx.AsyncClient | None = None


async def _get_espuino_client() -> httpx.AsyncClient:
    """Get or create HTTP client for ESPuino communication."""
    global _espuino_client
    if _espuino_client is None:
        _espuino_client = httpx.AsyncClient(timeout=5)
    return _espuino_client


async def notify_espuino_progress(ip: str, progress: int) -> bool:
    """Send encoding progress update to ESPuino device.

    Args:
        ip: ESPuino IP address
        progress: Progress percentage (0-100)

    Returns:
        True if successful, False otherwise
    """
    try:
        client = await _get_espuino_client()
        url = f"http://{ip}/cacheprogress?progress={progress}"
        response = await client.post(url)
        if response.status_code == 200:
            logger.debug(f"Sent progress {progress}% to ESPuino {ip}")
            return True
        else:
            logger.warning(f"ESPuino {ip} returned {response.status_code}")
            return False
    except Exception as e:
        logger.debug(f"Failed to send progress to ESPuino {ip}: {e}")
        return False


async def fetch_cover_image(cover_url: str, cache_dir: Path) -> Path | None:
    """Download cover image into cache dir and return local path."""
    if not cover_url:
        return None

    cache_dir.mkdir(parents=True, exist_ok=True)
    candidates = [
        cache_dir / "cover.jpg",
        cache_dir / "cover.jpeg",
        cache_dir / "cover.png",
    ]
    for path in candidates:
        if path.exists() and path.stat().st_size > 0:
            return path

    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            resp = await client.get(cover_url)
            if resp.status_code != 200:
                logger.warning(f"Cover fetch failed: {resp.status_code}")
                return None
            content_type = resp.headers.get("content-type", "").lower()
            if not content_type.startswith("image/"):
                logger.warning(f"Cover fetch invalid content-type: {content_type}")
                return None
            data = resp.content
            if len(data) == 0:
                return None
            if len(data) > 5 * 1024 * 1024:
                logger.warning("Cover fetch too large, skipping")
                return None

            if "png" in content_type:
                out_path = cache_dir / "cover.png"
            elif "jpeg" in content_type or "jpg" in content_type:
                out_path = cache_dir / "cover.jpg"
            else:
                out_path = cache_dir / "cover.jpg"

            out_path.write_bytes(data)
            return out_path
    except Exception as e:
        logger.warning(f"Cover fetch failed: {e}")
        return None


def _get_encoding_lock(cache_key: str) -> asyncio.Lock:
    """Get or create a lock for a specific cache key."""
    if cache_key not in _encoding_locks:
        _encoding_locks[cache_key] = asyncio.Lock()
    return _encoding_locks[cache_key]


# Cache directory (set from config on init)
CONFIG_DIR = Path(os.getenv("CONFIG_DIR", "/app/config"))
CACHE_DIR = CONFIG_DIR / "audio_cache"


@dataclass
class TrackInfo:
    """Information about a single track."""

    index: int
    name: str
    start_seconds: float
    duration_seconds: float
    filename: str  # e.g., "01.mp3"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class TonieMetadata:
    """Metadata for a Tonie album."""

    title: str  # Album title (series - episode)
    artist: str  # Series name
    album: str  # Episode name
    year: str
    tracks: list[TrackInfo]
    source_url: str
    total_duration: float

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "artist": self.artist,
            "album": self.album,
            "year": self.year,
            "total_duration": self.total_duration,
            "source_url": self.source_url,
            "tracks": [t.to_dict() for t in self.tracks],
        }


def check_ffmpeg() -> bool:
    """Check if FFmpeg is available."""
    try:
        result = subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=5)
        return result.returncode == 0
    except Exception:
        return False


def has_embedded_cover(mp3_path: Path) -> bool:
    """Return True if the MP3 has an attached picture stream."""
    if not mp3_path.exists():
        return False
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v",
                "-show_entries",
                "stream=disposition",
                "-of",
                "json",
                str(mp3_path),
            ],
            capture_output=True,
            timeout=5,
            text=True,
        )
        if result.returncode != 0:
            return False
        data = json.loads(result.stdout or "{}")
        for stream in data.get("streams", []):
            disposition = stream.get("disposition", {})
            if disposition.get("attached_pic") == 1:
                return True
        return False
    except Exception:
        return False


def get_tonie_cache_key(source_url: str) -> str:
    """Generate a cache key from source URL for folder naming."""
    return hashlib.sha256(source_url.encode()).hexdigest()[:16]


def get_tonie_cache_dir(source_url: str) -> Path:
    """Get the cache directory for a Tonie (contains multiple track MP3s)."""
    return CACHE_DIR / get_tonie_cache_key(source_url)


def get_track_cache_path(source_url: str, track_index: int) -> Path:
    """Get the MP3 cache file path for a specific track."""
    return get_tonie_cache_dir(source_url) / f"{track_index + 1:02d}.mp3"


def get_metadata_path(source_url: str) -> Path:
    """Get the metadata JSON file path for a Tonie."""
    return get_tonie_cache_dir(source_url) / "metadata.json"


# Legacy single-file support
def get_cache_key(source_url: str) -> str:
    """Generate a cache key from source URL (legacy single-file)."""
    return hashlib.sha256(source_url.encode()).hexdigest()[:16]


def get_mp3_cache_path(source_url: str) -> Path:
    """Get the MP3 cache file path for a source URL (legacy single-file)."""
    return CACHE_DIR / f"{get_cache_key(source_url)}.mp3"


def get_cache_size() -> int:
    """Get total size of cached files in bytes."""
    if not CACHE_DIR.exists():
        return 0
    total = 0
    for item in CACHE_DIR.rglob("*.mp3"):
        total += item.stat().st_size
    return total


def get_cache_stats() -> dict:
    """Get cache statistics."""
    if not CACHE_DIR.exists():
        return {"files": 0, "folders": 0, "size_mb": 0, "max_mb": 500}

    from ..config import get_settings

    settings = get_settings()

    mp3_files = list(CACHE_DIR.rglob("*.mp3"))
    folders = [d for d in CACHE_DIR.iterdir() if d.is_dir()]
    total_size = sum(f.stat().st_size for f in mp3_files)

    return {
        "files": len(mp3_files),
        "folders": len(folders),
        "size_mb": round(total_size / (1024 * 1024), 1),
        "max_mb": settings.audio_cache_max_mb,
    }


def get_encoding_status(source_url: str) -> dict:
    """Get encoding status for a source URL (multi-track aware).

    Returns dict with:
    - status: "cached", "encoding", "ready", "error", "unknown"
    - cached: bool - whether all tracks are cached
    - progress: float (0-100) - overall encoding progress
    - current_track: int - track currently being encoded (1-indexed)
    - total_tracks: int - total number of tracks
    - tracks_completed: int - number of tracks finished
    - started_at: float - timestamp when encoding started
    - elapsed_seconds: float - how long encoding has been running
    - file_size_mb: float - total size of cached files
    - error: str - error message (if status is "error")
    """
    cache_key = get_tonie_cache_key(source_url)
    cache_dir = get_tonie_cache_dir(source_url)
    metadata_path = get_metadata_path(source_url)

    # Check if fully cached (metadata exists = all tracks done)
    if metadata_path.exists():
        try:
            with open(metadata_path) as f:
                metadata = json.load(f)

            total_size = sum(
                (cache_dir / t["filename"]).stat().st_size
                for t in metadata["tracks"]
                if (cache_dir / t["filename"]).exists()
            )

            return {
                "status": "cached",
                "cached": True,
                "progress": 100,
                "total_tracks": len(metadata["tracks"]),
                "tracks_completed": len(metadata["tracks"]),
                "file_size_mb": round(total_size / (1024 * 1024), 2),
                "metadata": metadata,
            }
        except Exception as e:
            logger.warning(f"Error reading metadata: {e}")

    # Check if currently encoding
    if cache_key in _encoding_status:
        status_info = _encoding_status[cache_key].copy()
        if status_info.get("started_at"):
            elapsed = time.time() - status_info["started_at"]
            status_info["elapsed_seconds"] = round(elapsed, 1)

            # Detect stale encoding (stuck for >10 minutes with no progress updates)
            # This can happen if the encoding task crashes silently
            if status_info.get("status") == "encoding" and elapsed > 600:
                logger.warning(
                    f"Encoding appears stuck for {cache_key[:8]} ({elapsed / 60:.1f} min)"
                )
                # Mark as error and clean up
                status_info["status"] = "error"
                status_info["error"] = "Encoding timed out (>10 min)"
                # Clear the stale status
                del _encoding_status[cache_key]
        return status_info

    # Check for partial cache (some tracks exist)
    if cache_dir.exists():
        existing_tracks = list(cache_dir.glob("*.mp3"))
        if existing_tracks:
            total_size = sum(f.stat().st_size for f in existing_tracks)
            return {
                "status": "partial",
                "cached": False,
                "progress": 0,
                "tracks_completed": len(existing_tracks),
                "file_size_mb": round(total_size / (1024 * 1024), 2),
            }

    # Unknown - not cached, not encoding
    return {
        "status": "unknown",
        "cached": False,
        "progress": 0,
    }


def set_encoding_status(source_url: str, status: str, **kwargs) -> None:
    """Update encoding status for a source URL."""
    cache_key = get_tonie_cache_key(source_url)
    _encoding_status[cache_key] = {
        "status": status,
        "cached": False,
        "progress": kwargs.get("progress", 0),
        "started_at": kwargs.get("started_at", time.time()),
        **kwargs,
    }
    logger.debug(
        f"Encoding status [{cache_key[:8]}]: {status} - {kwargs.get('current_track', '?')}/{kwargs.get('total_tracks', '?')}"
    )


def clear_encoding_status(source_url: str) -> None:
    """Clear encoding status for a source URL."""
    cache_key = get_tonie_cache_key(source_url)
    if cache_key in _encoding_status:
        del _encoding_status[cache_key]


def cleanup_cache(target_bytes: int) -> int:
    """Delete oldest items until cache is under target_bytes. Returns bytes freed."""
    if not CACHE_DIR.exists():
        return 0

    # Collect all cache items (folders and loose mp3 files)
    items = []
    for item in CACHE_DIR.iterdir():
        if item.is_dir():
            mp3s = list(item.glob("*.mp3"))
            if mp3s:
                oldest_time = min(f.stat().st_atime for f in mp3s)
                item_size = sum(f.stat().st_size for f in mp3s)
                items.append((item, oldest_time, item_size, True))
        elif item.suffix == ".mp3":
            stat = item.stat()
            items.append((item, stat.st_atime, stat.st_size, False))

    if not items:
        return 0

    # Sort by access time (oldest first)
    items.sort(key=lambda x: x[1])

    current_size = get_cache_size()
    freed = 0

    while current_size > target_bytes and items:
        item, _, item_size, is_folder = items.pop(0)
        if is_folder:
            shutil.rmtree(item)
            logger.info(f"Cache evict folder: {item.name} ({item_size // 1024} KB)")
        else:
            item.unlink()
            logger.info(f"Cache evict file: {item.name} ({item_size // 1024} KB)")
        current_size -= item_size
        freed += item_size

    return freed


def ensure_cache_space(needed_bytes: int) -> None:
    """Ensure there's room for a new Tonie of needed_bytes."""
    from ..config import get_settings

    settings = get_settings()
    max_bytes = settings.audio_cache_max_mb * 1024 * 1024

    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    current = get_cache_size()
    if current + needed_bytes > max_bytes:
        target = max_bytes - needed_bytes
        freed = cleanup_cache(target)
        logger.info(f"Cache cleanup: freed {freed // 1024} KB")


def clear_cache() -> int:
    """Clear all cached files. Returns number of folders deleted."""
    if not CACHE_DIR.exists():
        return 0

    folders = [d for d in CACHE_DIR.iterdir() if d.is_dir()]
    for folder in folders:
        shutil.rmtree(folder)

    # Also clean legacy single files
    for f in CACHE_DIR.glob("*.mp3"):
        f.unlink()

    logger.info(f"Cache cleared: {len(folders)} folders")
    return len(folders)


async def encode_track_to_mp3(
    source_url: str,
    output_path: Path,
    start_seconds: float,
    duration_seconds: float,
    track_index: int,
    track_name: str,
    album: str,
    artist: str,
    total_tracks: int,
    year: str = "",
    cover_path: Path | None = None,
) -> bool:
    """
    Encode a single track from source URL to MP3 file with ID3 tags.

    Uses CBR 192kbps for stable streaming on all devices.
    FFmpeg seeks to start_seconds and encodes duration_seconds.
    Returns True on success.
    """
    temp_fd, temp_path = tempfile.mkstemp(suffix=".mp3")
    os.close(temp_fd)
    temp_path = Path(temp_path)

    # Build FFmpeg command with seeking and duration
    # IMPORTANT: All inputs must come before output options to avoid ffmpeg errors
    ffmpeg_cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "warning",
        "-threads",
        "0",
        "-y",
        "-ss",
        str(start_seconds),  # Seek to start (input option)
        "-t",
        str(duration_seconds),  # Duration to encode (input option)
        "-i",
        source_url,
    ]

    # Add cover image as second input if available
    if cover_path and cover_path.exists():
        ffmpeg_cmd.extend(["-i", str(cover_path)])

    # Now add output options (audio codec, bitrate, etc.)
    ffmpeg_cmd.extend(
        [
            "-c:a",
            "libmp3lame",
            "-b:a",
            "192k",  # CBR 192kbps
            "-ar",
            "44100",
            "-ac",
            "2",
            # ID3v2 metadata
            "-id3v2_version",
            "3",
            "-metadata",
            f"title={track_name}",
            "-metadata",
            f"artist={artist}",
            "-metadata",
            f"album={album}",
            "-metadata",
            f"track={track_index + 1}/{total_tracks}",
        ]
    )

    if year:
        ffmpeg_cmd.extend(["-metadata", f"date={year}"])

    # Add cover mapping if cover was added
    if cover_path and cover_path.exists():
        ffmpeg_cmd.extend(
            [
                "-map",
                "0:a",
                "-map",
                "1:v",
                "-c:v",
                "mjpeg",
                "-disposition:v",
                "attached_pic",
                "-metadata:s:v",
                "title=Album cover",
                "-metadata:s:v",
                "comment=Cover (front)",
            ]
        )

    ffmpeg_cmd.append(str(temp_path))

    logger.info(
        f"Encoding track {track_index + 1}/{total_tracks}: {track_name} ({duration_seconds:.1f}s)"
    )

    try:
        process = await asyncio.create_subprocess_exec(
            *ffmpeg_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=120,  # 2 min per track max
        )

        if process.returncode != 0:
            stderr_text = stderr.decode().strip() if stderr else "Unknown error"
            logger.error(f"Track {track_index + 1} encoding failed: {stderr_text}")
            temp_path.unlink(missing_ok=True)
            return False

        file_size = temp_path.stat().st_size
        logger.info(f"Track {track_index + 1} complete: {file_size // 1024} KB")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(temp_path), str(output_path))

        return True

    except asyncio.TimeoutError:
        logger.error(f"Track {track_index + 1} encoding timed out")
        temp_path.unlink(missing_ok=True)
        return False
    except Exception as e:
        logger.error(f"Track {track_index + 1} encoding error: {e}")
        temp_path.unlink(missing_ok=True)
        return False


async def encode_tonie_tracks(
    source_url: str,
    tracks: list[dict],
    series: str = "",
    episode: str = "",
    year: str = "",
    on_track_complete: callable = None,
    espuino_ip: str = None,
    cover_url: str = "",
) -> TonieMetadata | None:
    """
    Encode all tracks of a Tonie to separate MP3 files.

    Args:
        source_url: TeddyCloud content URL (with ?ogg=true&special=library)
        tracks: List of track dicts with 'name', 'start', 'duration'
        series: Series/artist name for ID3 tags
        episode: Episode/album name for ID3 tags
        year: Release year for ID3 tags
        on_track_complete: Optional callback(track_index, track_path) called after each track
        espuino_ip: Optional ESPuino IP to send encoding progress updates

    Returns:
        TonieMetadata with all track info, or None on failure
    """
    if not tracks:
        logger.error("No tracks provided for encoding")
        return None

    cache_key = get_tonie_cache_key(source_url)
    cache_dir = get_tonie_cache_dir(source_url)
    metadata_path = get_metadata_path(source_url)
    cover_path = await fetch_cover_image(cover_url, cache_dir)

    # Check if already fully cached
    if metadata_path.exists():
        try:
            with open(metadata_path) as f:
                data = json.load(f)
            logger.info(f"Cache hit (multi-track): {cache_dir.name}")
            set_encoding_status(
                source_url,
                "cached",
                progress=100,
                total_tracks=len(data.get("tracks", [])),
                tracks_completed=len(data.get("tracks", [])),
            )
            return TonieMetadata(
                title=data["title"],
                artist=data["artist"],
                album=data["album"],
                year=data.get("year", ""),
                total_duration=data["total_duration"],
                source_url=data["source_url"],
                tracks=[TrackInfo(**t) for t in data["tracks"]],
            )
        except Exception as e:
            logger.warning(f"Error reading cached metadata, re-encoding: {e}")

    # Acquire lock to prevent concurrent encoding
    lock = _get_encoding_lock(cache_key)
    async with lock:
        # Check again after acquiring lock
        if metadata_path.exists():
            try:
                with open(metadata_path) as f:
                    data = json.load(f)
                logger.info(f"Cache hit (multi-track, after wait): {cache_dir.name}")
                set_encoding_status(
                    source_url,
                    "cached",
                    progress=100,
                    total_tracks=len(data.get("tracks", [])),
                    tracks_completed=len(data.get("tracks", [])),
                )
                return TonieMetadata(
                    title=data["title"],
                    artist=data["artist"],
                    album=data["album"],
                    year=data.get("year", ""),
                    total_duration=data["total_duration"],
                    source_url=data["source_url"],
                    tracks=[TrackInfo(**t) for t in data["tracks"]],
                )
            except Exception:
                pass

        # Create cache directory
        cache_dir.mkdir(parents=True, exist_ok=True)

        # Estimate needed space (~10 MB per 10 minutes of audio)
        total_duration = sum(t.get("duration", 0) for t in tracks)
        estimated_size = int(total_duration / 60 * 10 * 1024 * 1024)
        ensure_cache_space(needed_bytes=estimated_size)

        # Build metadata
        album = (
            f"{series} - {episode}"
            if series and episode
            else episode or series or "Unknown"
        )
        artist = series or "Tonie"
        title = album

        start_time = time.time()
        set_encoding_status(
            source_url,
            "encoding",
            progress=0,
            current_track=0,
            total_tracks=len(tracks),
            tracks_completed=0,
            started_at=start_time,
        )

        track_infos = []

        for i, track in enumerate(tracks):
            track_name = track.get("name", f"Track {i + 1}")
            start_seconds = track.get("start", 0)
            duration_seconds = track.get("duration", 0)

            if duration_seconds <= 0:
                logger.warning(f"Skipping track {i + 1} with zero duration")
                continue

            filename = f"{i + 1:02d}.mp3"
            output_path = cache_dir / filename

            # Update progress
            progress = int((i / len(tracks)) * 100)
            set_encoding_status(
                source_url,
                "encoding",
                progress=progress,
                current_track=i + 1,
                total_tracks=len(tracks),
                tracks_completed=i,
                started_at=start_time,
            )

            # Send progress to ESPuino if IP provided
            if espuino_ip:
                await notify_espuino_progress(espuino_ip, progress)

            # Encode track
            success = await encode_track_to_mp3(
                source_url=source_url,
                output_path=output_path,
                start_seconds=start_seconds,
                duration_seconds=duration_seconds,
                track_index=i,
                track_name=track_name,
                album=album,
                artist=artist,
                total_tracks=len(tracks),
                year=year,
                cover_path=cover_path,
            )

            if not success:
                set_encoding_status(
                    source_url, "error", error=f"Failed to encode track {i + 1}"
                )
                return None

            track_infos.append(
                TrackInfo(
                    index=i,
                    name=track_name,
                    start_seconds=start_seconds,
                    duration_seconds=duration_seconds,
                    filename=filename,
                )
            )

            # Callback for progressive playback/upload
            if on_track_complete:
                try:
                    await on_track_complete(i, output_path)
                except Exception as e:
                    logger.warning(f"on_track_complete callback error: {e}")

        # Create metadata file
        metadata = TonieMetadata(
            title=title,
            artist=artist,
            album=album,
            year=year,
            total_duration=total_duration,
            source_url=source_url,
            tracks=track_infos,
        )

        with open(metadata_path, "w") as f:
            json.dump(metadata.to_dict(), f, indent=2)

        # Calculate total size
        total_size = sum((cache_dir / t.filename).stat().st_size for t in track_infos)

        logger.info(
            f"Multi-track encoding complete: {len(track_infos)} tracks, {total_size // 1024} KB"
        )

        # Send 100% progress to ESPuino
        if espuino_ip:
            await notify_espuino_progress(espuino_ip, 100)

        set_encoding_status(
            source_url,
            "ready",
            progress=100,
            total_tracks=len(track_infos),
            tracks_completed=len(track_infos),
            file_size_mb=round(total_size / (1024 * 1024), 2),
        )
        await asyncio.sleep(1)
        clear_encoding_status(source_url)

        return metadata


async def get_or_encode_tracks(
    source_url: str,
    tracks: list[dict],
    series: str = "",
    episode: str = "",
    year: str = "",
    espuino_ip: str = None,
    cover_url: str = "",
) -> TonieMetadata | None:
    """
    Get cached tracks or encode them if needed.

    This is the main entry point for multi-track encoding.
    Returns TonieMetadata with all track info.

    Args:
        espuino_ip: Optional ESPuino IP to send encoding progress updates
    """
    return await encode_tonie_tracks(
        source_url=source_url,
        tracks=tracks,
        series=series,
        episode=episode,
        year=year,
        espuino_ip=espuino_ip,
        cover_url=cover_url,
    )


def is_first_track_ready(source_url: str) -> bool:
    """Check if the first track is already encoded and cached."""
    first_track_path = get_track_cache_path(source_url, 0)
    return first_track_path.exists() and first_track_path.stat().st_size > 0


async def encode_first_track(
    source_url: str,
    tracks: list[dict],
    series: str = "",
    episode: str = "",
    year: str = "",
    espuino_ip: str = None,
    cover_url: str = "",
) -> Path | None:
    """
    Encode only the first track. Returns path to the encoded MP3.

    This allows playback to start immediately after the first track is ready,
    while remaining tracks are encoded in background.
    """
    if not tracks:
        logger.error("No tracks provided for encoding")
        return None

    logger.info(
        f"encode_first_track called with {len(tracks)} tracks: {[t.get('name', 'unnamed') for t in tracks[:3]]}..."
    )

    cache_key = get_tonie_cache_key(source_url)
    cache_dir = get_tonie_cache_dir(source_url)
    first_track_path = get_track_cache_path(source_url, 0)
    cover_path = await fetch_cover_image(cover_url, cache_dir)

    # Check if already cached (re-encode check removed for performance)
    if first_track_path.exists() and first_track_path.stat().st_size > 0:
        logger.info(f"First track already cached: {first_track_path}")
        set_encoding_status(source_url, "cached", progress=100)
        return first_track_path

    # Acquire lock to prevent concurrent encoding
    lock = _get_encoding_lock(cache_key)
    async with lock:
        # Check again after acquiring lock
        if first_track_path.exists() and first_track_path.stat().st_size > 0:
            logger.info(f"First track cached after wait: {first_track_path}")
            set_encoding_status(source_url, "cached", progress=100)
            return first_track_path

        # Create cache directory
        cache_dir.mkdir(parents=True, exist_ok=True)

        # Estimate space needed for first track
        first_track = tracks[0]
        duration = first_track.get("duration", 60)
        estimated_size = int(duration / 60 * 10 * 1024 * 1024)
        ensure_cache_space(needed_bytes=estimated_size)

        # Build metadata for ID3 tags
        album = (
            f"{series} - {episode}"
            if series and episode
            else episode or series or "Unknown"
        )
        artist = series or "Tonie"

        # Set encoding status
        start_time = time.time()
        set_encoding_status(
            source_url,
            "encoding",
            progress=0,
            current_track=1,
            total_tracks=len(tracks),
            tracks_completed=0,
            started_at=start_time,
        )

        # Send initial progress to ESPuino
        if espuino_ip:
            await notify_espuino_progress(espuino_ip, 0)

        # Encode first track
        track_name = first_track.get("name", "Track 1")
        start_seconds = first_track.get("start", 0)
        duration_seconds = first_track.get("duration", 0)

        logger.info(f"Encoding first track: {track_name} ({duration_seconds:.1f}s)")

        success = await encode_track_to_mp3(
            source_url=source_url,
            output_path=first_track_path,
            start_seconds=start_seconds,
            duration_seconds=duration_seconds,
            track_index=0,
            track_name=track_name,
            album=album,
            artist=artist,
            total_tracks=len(tracks),
            year=year,
            cover_path=cover_path,
        )

        if not success:
            set_encoding_status(
                source_url, "error", error="Failed to encode first track"
            )
            return None

        # Update status - first track complete
        progress = int((1 / len(tracks)) * 100)
        set_encoding_status(
            source_url,
            "encoding",
            progress=progress,
            current_track=1,
            total_tracks=len(tracks),
            tracks_completed=1,
            started_at=start_time,
        )

        if espuino_ip:
            await notify_espuino_progress(espuino_ip, progress)

        logger.info(
            f"First track encoded: {first_track_path} ({first_track_path.stat().st_size // 1024} KB)"
        )
        return first_track_path


async def continue_encoding_remaining_tracks(
    source_url: str,
    tracks: list[dict],
    series: str = "",
    episode: str = "",
    year: str = "",
    espuino_ip: str = None,
    cover_url: str = "",
    playback_device: dict = None,
    server_base_url: str = None,
) -> TonieMetadata | None:
    """
    Continue encoding tracks 2+ in background after first track is done.

    This should be called as a background task after playback starts.
    Creates metadata.json when all tracks are complete.

    If playback_device is provided (Sonos/Chromecast), tracks are queued
    progressively as they're encoded - no need to wait for all tracks.
    """
    if len(tracks) <= 1:
        # Only one track, create metadata and return
        cache_dir = get_tonie_cache_dir(source_url)
        metadata_path = get_metadata_path(source_url)

        first_track = tracks[0]
        track_info = TrackInfo(
            index=0,
            name=first_track.get("name", "Track 1"),
            start_seconds=first_track.get("start", 0),
            duration_seconds=first_track.get("duration", 0),
            filename="01.mp3",
        )

        album = (
            f"{series} - {episode}"
            if series and episode
            else episode or series or "Unknown"
        )
        artist = series or "Tonie"

        metadata = TonieMetadata(
            title=album,
            artist=artist,
            album=album,
            year=year,
            total_duration=first_track.get("duration", 0),
            source_url=source_url,
            tracks=[track_info],
        )

        with open(metadata_path, "w") as f:
            json.dump(metadata.to_dict(), f, indent=2)

        set_encoding_status(
            source_url, "ready", progress=100, total_tracks=1, tracks_completed=1
        )
        await asyncio.sleep(1)
        clear_encoding_status(source_url)

        return metadata

    cache_key = get_tonie_cache_key(source_url)
    cache_dir = get_tonie_cache_dir(source_url)
    metadata_path = get_metadata_path(source_url)
    cover_path = await fetch_cover_image(cover_url, cache_dir)

    # Build metadata
    album = (
        f"{series} - {episode}"
        if series and episode
        else episode or series or "Unknown"
    )
    artist = series or "Tonie"
    total_duration = sum(t.get("duration", 0) for t in tracks)

    # Use lock for remaining tracks
    lock = _get_encoding_lock(f"{cache_key}_remaining")
    async with lock:
        start_time = time.time()
        track_infos = []

        # Add first track info (already encoded)
        first_track = tracks[0]
        track_infos.append(
            TrackInfo(
                index=0,
                name=first_track.get("name", "Track 1"),
                start_seconds=first_track.get("start", 0),
                duration_seconds=first_track.get("duration", 0),
                filename="01.mp3",
            )
        )

        # Encode remaining tracks (index 1+)
        for i in range(1, len(tracks)):
            track = tracks[i]
            track_name = track.get("name", f"Track {i + 1}")
            start_seconds = track.get("start", 0)
            duration_seconds = track.get("duration", 0)

            if duration_seconds <= 0:
                logger.warning(f"Skipping track {i + 1} with zero duration")
                continue

            filename = f"{i + 1:02d}.mp3"
            output_path = cache_dir / filename

            # Check if already cached (re-encode if cover missing)
            if output_path.exists() and output_path.stat().st_size > 0:
                if cover_path and not has_embedded_cover(output_path):
                    logger.info(
                        f"Track {i + 1} cached without cover, re-encoding: {output_path}"
                    )
                else:
                    logger.info(f"Track {i + 1} already cached: {output_path}")
                    track_infos.append(
                        TrackInfo(
                            index=i,
                            name=track_name,
                            start_seconds=start_seconds,
                            duration_seconds=duration_seconds,
                            filename=filename,
                        )
                    )
                    continue

            # Update progress
            progress = int(((i) / len(tracks)) * 100)
            set_encoding_status(
                source_url,
                "encoding",
                progress=progress,
                current_track=i + 1,
                total_tracks=len(tracks),
                tracks_completed=i,
                started_at=start_time,
            )

            if espuino_ip:
                await notify_espuino_progress(espuino_ip, progress)

            logger.info(f"Encoding track {i + 1}/{len(tracks)}: {track_name}")

            success = await encode_track_to_mp3(
                source_url=source_url,
                output_path=output_path,
                start_seconds=start_seconds,
                duration_seconds=duration_seconds,
                track_index=i,
                track_name=track_name,
                album=album,
                artist=artist,
                total_tracks=len(tracks),
                year=year,
                cover_path=cover_path,
            )

            if not success:
                logger.error(f"Failed to encode track {i + 1}")
                set_encoding_status(
                    source_url, "error", error=f"Failed to encode track {i + 1}"
                )
                return None

            track_infos.append(
                TrackInfo(
                    index=i,
                    name=track_name,
                    start_seconds=start_seconds,
                    duration_seconds=duration_seconds,
                    filename=filename,
                )
            )

            # Queue track on device if progressive playback is enabled
            if (
                playback_device
                and server_base_url
                and playback_device.get("type") in ["sonos", "chromecast"]
            ):
                try:
                    from . import devices as device_service

                    cache_key = get_tonie_cache_key(source_url)
                    track_url = f"{server_base_url}/tracks/{cache_key}/{i + 1:02d}.mp3"
                    track_title = f"{album} - Track {i + 1}"
                    queued = await device_service.queue_track_on_device(
                        playback_device, track_url, track_title
                    )
                    if queued:
                        logger.info(
                            f"Queued track {i + 1} on {playback_device.get('type')}"
                        )
                except Exception as e:
                    logger.warning(f"Failed to queue track {i + 1}: {e}")

        # Create metadata file
        metadata = TonieMetadata(
            title=album,
            artist=artist,
            album=album,
            year=year,
            total_duration=total_duration,
            source_url=source_url,
            tracks=track_infos,
        )

        with open(metadata_path, "w") as f:
            json.dump(metadata.to_dict(), f, indent=2)

        # Calculate total size
        total_size = sum(
            (cache_dir / t.filename).stat().st_size
            for t in track_infos
            if (cache_dir / t.filename).exists()
        )

        logger.info(
            f"All tracks encoded: {len(track_infos)} tracks, {total_size // 1024} KB"
        )

        if espuino_ip:
            await notify_espuino_progress(espuino_ip, 100)

        set_encoding_status(
            source_url,
            "ready",
            progress=100,
            total_tracks=len(track_infos),
            tracks_completed=len(track_infos),
            file_size_mb=round(total_size / (1024 * 1024), 2),
        )
        await asyncio.sleep(1)
        clear_encoding_status(source_url)

        return metadata


def get_cached_tracks(source_url: str) -> TonieMetadata | None:
    """Get cached track metadata if available (sync version for quick checks)."""
    metadata_path = get_metadata_path(source_url)

    if not metadata_path.exists():
        return None

    try:
        with open(metadata_path) as f:
            data = json.load(f)
        return TonieMetadata(
            title=data["title"],
            artist=data["artist"],
            album=data["album"],
            year=data.get("year", ""),
            total_duration=data["total_duration"],
            source_url=data["source_url"],
            tracks=[TrackInfo(**t) for t in data["tracks"]],
        )
    except Exception as e:
        logger.warning(f"Error reading cached metadata: {e}")
        return None


def is_track_cached(source_url: str, track_index: int) -> bool:
    """Check if a specific track is cached."""
    track_path = get_track_cache_path(source_url, track_index)
    return track_path.exists()


def get_concatenated_mp3_path(source_url: str) -> Path | None:
    """
    Get path to concatenated MP3 (all tracks combined).
    Creates the file if it doesn't exist but tracks are cached.
    Returns None if no multi-track cache exists.
    """
    cache_dir = get_tonie_cache_dir(source_url)
    concat_path = cache_dir / "full.mp3"

    # If concatenated file exists, return it
    if concat_path.exists():
        concat_path.touch()
        return concat_path

    # Check if multi-track cache exists
    metadata = get_cached_tracks(source_url)
    if not metadata:
        return None

    # Collect all track files
    track_files = []
    for i, track in enumerate(metadata.tracks):
        track_path = get_track_cache_path(source_url, i)
        if not track_path.exists():
            logger.warning(f"Track {i + 1} missing, cannot concatenate")
            return None
        track_files.append(track_path)

    # Concatenate tracks using ffmpeg
    logger.info(f"Creating concatenated MP3 from {len(track_files)} tracks...")

    # Create file list for ffmpeg
    list_path = cache_dir / "concat_list.txt"
    with open(list_path, "w") as f:
        for track_path in track_files:
            f.write(f"file '{track_path.name}'\n")

    ffmpeg_cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "warning",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(list_path),
        "-c",
        "copy",  # Just copy, no re-encoding
        str(concat_path),
    ]

    try:
        import subprocess

        result = subprocess.run(ffmpeg_cmd, capture_output=True, cwd=cache_dir)
        if result.returncode != 0:
            logger.error(f"FFmpeg concat failed: {result.stderr.decode()}")
            return None
        logger.info(
            f"Created concatenated MP3: {concat_path.name} ({concat_path.stat().st_size / 1024 / 1024:.1f} MB)"
        )
        list_path.unlink(missing_ok=True)
        return concat_path
    except Exception as e:
        logger.error(f"Failed to concatenate tracks: {e}")
        list_path.unlink(missing_ok=True)
        return None


async def get_or_serve_cached_mp3(source_url: str) -> Path | None:
    """
    Get the best available cached MP3 for streaming.

    Priority:
    1. Multi-track cache (concatenated) - if available
    2. Legacy single-file cache

    Returns path to cached file, or None if nothing cached.
    """
    # First check multi-track cache
    concat_path = get_concatenated_mp3_path(source_url)
    if concat_path:
        logger.info(f"Using concatenated multi-track: {concat_path.name}")
        return concat_path

    # Fall back to legacy single-file cache
    legacy_path = get_mp3_cache_path(source_url)
    if legacy_path.exists():
        legacy_path.touch()
        logger.info(f"Using legacy single-file cache: {legacy_path.name}")
        return legacy_path

    return None


# ============================================================================
# Legacy single-file support (for backwards compatibility)
# ============================================================================


async def encode_to_mp3(
    source_url: str, output_path: Path, cover_url: str = ""
) -> bool:
    """
    Encode audio from source URL to single MP3 file (legacy).

    Uses CBR 192kbps for stable streaming on all devices.
    Takes ~30 seconds for a typical 75-minute Tonie.
    Returns True on success.
    """
    set_encoding_status(source_url, "encoding", progress=5)

    temp_fd, temp_path = tempfile.mkstemp(suffix=".mp3")
    os.close(temp_fd)
    temp_path = Path(temp_path)

    cache_dir = output_path.parent
    cover_path = await fetch_cover_image(cover_url, cache_dir)

    ffmpeg_cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "warning",
        "-threads",
        "0",
        "-y",
        "-i",
        source_url,
        "-c:a",
        "libmp3lame",
        "-b:a",
        "192k",
        "-ar",
        "44100",
        "-ac",
        "2",
        "-id3v2_version",
        "3",
    ]

    if cover_path and cover_path.exists():
        ffmpeg_cmd.extend(
            [
                "-i",
                str(cover_path),
                "-map",
                "0:a",
                "-map",
                "1:v",
                "-c:v",
                "mjpeg",
                "-disposition:v",
                "attached_pic",
                "-metadata:s:v",
                "title=Album cover",
                "-metadata:s:v",
                "comment=Cover (front)",
            ]
        )

    ffmpeg_cmd.append(str(temp_path))

    logger.info(f"Encoding to MP3 (legacy): {source_url[:80]}...")
    set_encoding_status(source_url, "encoding", progress=10)

    try:
        process = await asyncio.create_subprocess_exec(
            *ffmpeg_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        start_time = time.time()

        async def update_progress():
            while True:
                await asyncio.sleep(2)
                elapsed = time.time() - start_time
                progress = min(90, 10 + (elapsed / 30) * 80)
                set_encoding_status(
                    source_url,
                    "encoding",
                    progress=int(progress),
                    started_at=start_time,
                )

        progress_task = asyncio.create_task(update_progress())

        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=300)
        finally:
            progress_task.cancel()
            try:
                await progress_task
            except asyncio.CancelledError:
                pass

        if process.returncode != 0:
            stderr_text = stderr.decode().strip() if stderr else "Unknown error"
            logger.error(f"MP3 encoding failed: {stderr_text}")
            set_encoding_status(source_url, "error", error=stderr_text[:200])
            temp_path.unlink(missing_ok=True)
            return False

        file_size = temp_path.stat().st_size
        logger.info(f"MP3 encoding complete: {file_size // 1024} KB")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(temp_path), str(output_path))

        set_encoding_status(
            source_url,
            "ready",
            progress=100,
            file_size_mb=round(file_size / (1024 * 1024), 2),
        )
        await asyncio.sleep(1)
        clear_encoding_status(source_url)

        return True

    except asyncio.TimeoutError:
        logger.error("MP3 encoding timed out")
        set_encoding_status(source_url, "error", error="Encoding timed out (5 min)")
        temp_path.unlink(missing_ok=True)
        return False
    except Exception as e:
        logger.error(f"MP3 encoding error: {e}")
        set_encoding_status(source_url, "error", error=str(e)[:200])
        temp_path.unlink(missing_ok=True)
        return False


async def transcode_stream(
    source_url: str,
    output_format: str = "mp3",
    timeout: int = 300,
) -> AsyncGenerator[bytes, None]:
    """
    Stream audio from source URL through FFmpeg transcoding (legacy).

    Note: This is the legacy streaming approach. For multi-track,
    use get_or_encode_tracks() instead.
    """
    if output_format == "flac":
        codec_args = ["-c:a", "flac"]
        format_arg = "flac"
    elif output_format == "mp3":
        codec_args = ["-c:a", "libmp3lame", "-b:a", "192k"]
        format_arg = "mp3"
    elif output_format == "wav":
        codec_args = ["-c:a", "pcm_s16le"]
        format_arg = "wav"
    else:
        raise ValueError(f"Unsupported format: {output_format}")

    ffmpeg_cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "warning",
        "-i",
        source_url,
        *codec_args,
        "-f",
        format_arg,
        "pipe:1",
    ]

    logger.info(f"Transcoding {source_url} to {output_format}")
    logger.debug(f"FFmpeg command: {' '.join(ffmpeg_cmd)}")

    process = await asyncio.create_subprocess_exec(
        *ffmpeg_cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    try:
        bytes_sent = 0
        while True:
            if process.stdout:
                chunk = await process.stdout.read(16384)
                if not chunk:
                    break
                bytes_sent += len(chunk)
                yield chunk
            else:
                break

        logger.info(f"Transcoding complete: {bytes_sent} bytes sent")

    except Exception as e:
        logger.error(f"Transcoding error: {e}")
        raise
    finally:
        if process.returncode is None:
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()

        if process.stderr:
            stderr = await process.stderr.read()
            if stderr:
                stderr_text = stderr.decode().strip()
                if stderr_text:
                    logger.warning(f"FFmpeg: {stderr_text}")


def get_content_type(output_format: str) -> str:
    """Get MIME type for output format."""
    return {
        "flac": "audio/flac",
        "mp3": "audio/mpeg",
        "wav": "audio/wav",
    }.get(output_format, "application/octet-stream")
