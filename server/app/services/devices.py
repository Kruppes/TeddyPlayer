"""Device discovery service for Sonos, AirPlay, Chromecast, and Spotify Connect."""

from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

# Cache file location
CONFIG_DIR = Path(os.getenv("CONFIG_DIR", "/app/config"))
DEVICE_CACHE_FILE = CONFIG_DIR / "device_cache.json"
READER_CACHE_FILE = CONFIG_DIR / "reader_cache.json"

# Limit ESPuino upload bandwidth to reduce stream dropouts (0 = unlimited)
ESPUINO_UPLOAD_MAX_KBPS_ACTIVE = int(os.getenv("ESPUINO_UPLOAD_MAX_KBPS_ACTIVE", "200"))
ESPUINO_UPLOAD_MAX_KBPS_IDLE = int(os.getenv("ESPUINO_UPLOAD_MAX_KBPS_IDLE", "0"))
ESPUINO_UPLOAD_MAX_KBPS = int(
    os.getenv("ESPUINO_UPLOAD_MAX_KBPS", str(ESPUINO_UPLOAD_MAX_KBPS_ACTIVE))
)

# In-memory device cache (loaded from file on startup)
# Structure: {"sonos": [...], "airplay": [...], "chromecast": [...], "spotify": [...], "espuino": [...]}
# Each device has: name, ip/id, online, first_seen, last_seen, plus type-specific fields
_device_cache: dict[str, list[dict]] = {
    "sonos": [],
    "airplay": [],
    "chromecast": [],
    "spotify": [],
    "espuino": [],
}

# Temporary discovery results (not persisted directly)
discovered_devices: dict[str, list] = {
    "sonos": [],
    "airplay": [],
    "chromecast": [],
    "spotify": [],
    "espuino": [],
}

# Manually configured devices (merged into cache)
manual_devices: dict[str, list] = {
    "sonos": [],
    "airplay": [],
    "chromecast": [],
    "spotify": [],
    "espuino": [],
}

# ESPuino SD upload status tracking
# Key: "{espuino_ip}:{dest_path}" -> status dict
_upload_status: dict[str, dict] = {}
# Cancel flags for active uploads (keyed by ESPuino IP).
_upload_cancel: dict[str, float] = {}

# Persistent upload queue file (survives server restarts)
UPLOAD_QUEUE_FILE = CONFIG_DIR / "upload_queue.json"
_pending_uploads: dict[str, dict] = {}  # Key: espuino_ip -> upload intent


def _load_upload_queue() -> dict:
    """Load pending uploads from persistent storage."""
    global _pending_uploads
    if UPLOAD_QUEUE_FILE.exists():
        try:
            with open(UPLOAD_QUEUE_FILE) as f:
                _pending_uploads = json.load(f)
                logger.info(
                    f"Loaded {len(_pending_uploads)} pending uploads from queue"
                )
        except Exception as e:
            logger.warning(f"Failed to load upload queue: {e}")
            _pending_uploads = {}
    return _pending_uploads


def _save_upload_queue():
    """Save pending uploads to persistent storage."""
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with open(UPLOAD_QUEUE_FILE, "w") as f:
            json.dump(_pending_uploads, f, indent=2)
    except Exception as e:
        logger.warning(f"Failed to save upload queue: {e}")


def queue_upload(espuino_ip: str, upload_intent: dict):
    """
    Queue an upload intent for an ESPuino device.

    upload_intent should contain:
    - uid: Tonie UID
    - series, episode: Tonie info
    - folder_path: destination folder on SD
    - tracks: list of track info with source_path, dest_path, name
    - audio_url: source audio URL
    """
    _pending_uploads[espuino_ip] = {
        **upload_intent,
        "queued_at": datetime.now().isoformat(),
        "status": "pending",
    }
    _save_upload_queue()
    logger.info(
        f"Queued upload for ESPuino {espuino_ip}: {upload_intent.get('folder_path')}"
    )


def get_pending_upload(espuino_ip: str) -> dict | None:
    """Get pending upload intent for an ESPuino."""
    return _pending_uploads.get(espuino_ip)


def clear_pending_upload(espuino_ip: str):
    """Clear pending upload for an ESPuino (upload complete)."""
    if espuino_ip in _pending_uploads:
        del _pending_uploads[espuino_ip]
        _save_upload_queue()
        logger.info(f"Cleared pending upload for ESPuino {espuino_ip}")


def get_all_pending_uploads() -> dict:
    """Get all pending uploads."""
    return _pending_uploads.copy()


# Load queue on module import
_load_upload_queue()


def get_upload_status(
    espuino_ip: str, dest_path: str | None = None
) -> dict | list[dict]:
    """Get upload status for an ESPuino device.

    If dest_path is provided, returns status for that specific upload.
    Otherwise, returns all active uploads for the device.

    Returns dict with:
    - status: "uploading", "complete", "error"
    - progress: float (0-100)
    - bytes_uploaded: int
    - total_bytes: int
    - transfer_rate: float (bytes/sec)
    - eta_seconds: float - estimated time remaining
    - started_at: float - timestamp
    - filename: str
    """
    if dest_path:
        key = f"{espuino_ip}:{dest_path}"
        return _upload_status.get(key, {"status": "unknown"})

    # Return all uploads for this device
    prefix = f"{espuino_ip}:"
    return [
        {**status, "dest_path": key.split(":", 1)[1]}
        for key, status in _upload_status.items()
        if key.startswith(prefix)
    ]


def get_all_upload_status() -> list[dict]:
    """Get all active upload statuses."""
    return [{**status, "key": key} for key, status in _upload_status.items()]


def set_upload_status(espuino_ip: str, dest_path: str, status: str, **kwargs) -> None:
    """Update upload status for an ESPuino upload."""
    key = f"{espuino_ip}:{dest_path}"

    # Calculate transfer rate and ETA
    bytes_uploaded = kwargs.get("bytes_uploaded", 0)
    is_aux = kwargs.get("is_aux", _upload_status.get(key, {}).get("is_aux", False))
    total_bytes = kwargs.get("total_bytes", 0)
    started_at = kwargs.get(
        "started_at", _upload_status.get(key, {}).get("started_at", time.time())
    )
    elapsed = time.time() - started_at

    transfer_rate = bytes_uploaded / elapsed if elapsed > 0 else 0
    remaining_bytes = total_bytes - bytes_uploaded
    eta_seconds = remaining_bytes / transfer_rate if transfer_rate > 0 else 0

    progress = (bytes_uploaded / total_bytes * 100) if total_bytes > 0 else 0

    track_index = kwargs.get("track_index", 0)
    total_tracks = kwargs.get("total_tracks", 1)

    _upload_status[key] = {
        "status": status,
        "progress": round(progress, 1),
        "bytes_uploaded": bytes_uploaded,
        "total_bytes": total_bytes,
        "transfer_rate": round(transfer_rate, 0),
        "eta_seconds": round(eta_seconds, 1),
        "started_at": started_at,
        "elapsed_seconds": round(elapsed, 1),
        "filename": Path(dest_path).name,
        "espuino_ip": espuino_ip,
        "is_aux": is_aux,
        # Frontend-compatible field names
        "bytes_sent": bytes_uploaded,
        "current_track": track_index + 1 if track_index is not None else 1,
        "total_tracks": total_tracks,
        "rate_kbps": round(transfer_rate / 1024, 1) if transfer_rate > 0 else 0,
        "device_id": espuino_ip,
        "device_name": f"ESPuino {espuino_ip}",
        **kwargs,
    }
    logger.debug(f"Upload status [{espuino_ip}]: {status} {progress:.1f}%")


def clear_upload_status(espuino_ip: str, dest_path: str) -> None:
    """Clear upload status for an ESPuino upload."""
    key = f"{espuino_ip}:{dest_path}"
    if key in _upload_status:
        del _upload_status[key]


def clear_uploads_for_espuino(espuino_ip: str) -> int:
    """Clear all upload statuses for a specific ESPuino. Returns count cleared."""
    prefix = f"{espuino_ip}:"
    to_remove = [key for key in _upload_status if key.startswith(prefix)]
    for key in to_remove:
        del _upload_status[key]
    return len(to_remove)


def clear_all_uploads() -> int:
    """Clear all upload statuses. Returns count cleared."""
    count = len(_upload_status)
    _upload_status.clear()
    return count


def request_cancel_uploads(espuino_ip: str) -> None:
    """Request cancellation for all active uploads of an ESPuino."""
    _upload_cancel[espuino_ip] = time.time()

    # Clear the persistent pending queue so upload doesn't restart
    clear_pending_upload(espuino_ip)

    # Mark current uploads as cancelled for UI clarity
    prefix = f"{espuino_ip}:"
    for key, status in list(_upload_status.items()):
        if key.startswith(prefix):
            status["status"] = "error"
            status["error"] = "Cancelled by user"
            status["progress"] = status.get("progress", 0.0)
            status["transfer_rate"] = 0.0
            status["eta_seconds"] = 0
            _upload_status[key] = status

            async def cleanup_status(k=key):
                await asyncio.sleep(5)
                _upload_status.pop(k, None)

            try:
                asyncio.get_running_loop().create_task(cleanup_status())
            except RuntimeError:
                pass
    try:
        asyncio.get_running_loop().create_task(_clear_cancel_flag_later(espuino_ip))
    except RuntimeError:
        pass


def _should_cancel_upload(espuino_ip: str) -> bool:
    return espuino_ip in _upload_cancel


def _clear_cancel_flag(espuino_ip: str) -> None:
    _upload_cancel.pop(espuino_ip, None)


async def _clear_cancel_flag_later(espuino_ip: str, delay: float = 15.0) -> None:
    await asyncio.sleep(delay)
    _clear_cancel_flag(espuino_ip)


def get_failed_uploads(espuino_ip: str | None = None) -> list[dict]:
    """Get all failed uploads, optionally filtered by ESPuino IP."""
    failed = []
    for key, status in _upload_status.items():
        if status.get("status") == "error":
            if espuino_ip is None or key.startswith(f"{espuino_ip}:"):
                failed.append(
                    {
                        "key": key,
                        "espuino_ip": status.get("espuino_ip"),
                        "dest_path": key.split(":", 1)[1] if ":" in key else "",
                        "source_path": status.get("source_path"),
                        "title": status.get("title"),
                        "error": status.get("error"),
                    }
                )
    return failed


def _load_device_cache() -> dict[str, list[dict]]:
    """Load device cache from file."""
    # Default structure with all device types
    default_cache = {
        "sonos": [],
        "airplay": [],
        "chromecast": [],
        "spotify": [],
        "espuino": [],
    }

    if DEVICE_CACHE_FILE.exists():
        try:
            with open(DEVICE_CACHE_FILE) as f:
                data = json.load(f)
                # Ensure all required keys exist (for backwards compatibility)
                for key in default_cache:
                    if key not in data:
                        data[key] = []
                logger.info(
                    f"Loaded device cache: {sum(len(v) for v in data.values())} devices"
                )
                return data
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"Failed to load device cache: {e}")
    return default_cache


def _save_device_cache() -> bool:
    """Save device cache to file."""
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with open(DEVICE_CACHE_FILE, "w") as f:
            json.dump(_device_cache, f, indent=2)
        return True
    except IOError as e:
        logger.error(f"Failed to save device cache: {e}")
        return False


def init_device_cache():
    """Initialize device cache from file on startup."""
    global _device_cache
    _device_cache = _load_device_cache()
    # Mark all devices as offline initially (will be updated on discovery)
    for dtype in _device_cache:
        for device in _device_cache[dtype]:
            device["online"] = False


# Reader cache functions
_reader_cache: dict[str, dict] = {}


def _load_reader_cache() -> dict[str, dict]:
    """Load reader cache from file."""
    if READER_CACHE_FILE.exists():
        try:
            with open(READER_CACHE_FILE) as f:
                data = json.load(f)
                logger.info(f"Loaded reader cache: {len(data)} readers")
                return data
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"Failed to load reader cache: {e}")
    return {}


def _save_reader_cache() -> bool:
    """Save reader cache to file."""
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with open(READER_CACHE_FILE, "w") as f:
            json.dump(_reader_cache, f, indent=2)
        return True
    except IOError as e:
        logger.error(f"Failed to save reader cache: {e}")
        return False


def init_reader_cache():
    """Initialize reader cache from file on startup."""
    global _reader_cache
    _reader_cache = _load_reader_cache()
    # Mark all readers as offline initially
    for ip in _reader_cache:
        _reader_cache[ip]["online"] = False
    logger.info(f"Initialized {len(_reader_cache)} cached readers")


def get_cached_readers() -> dict[str, dict]:
    """Get all cached readers."""
    return _reader_cache


def update_reader_cache(ip: str, data: dict) -> dict:
    """Update or add a reader to the cache."""
    now = datetime.now().isoformat()
    if ip in _reader_cache:
        # Update existing
        _reader_cache[ip].update(data)
        _reader_cache[ip]["last_seen"] = now
        _reader_cache[ip]["online"] = True
    else:
        # Add new
        _reader_cache[ip] = {
            **data,
            "first_seen": now,
            "last_seen": now,
            "online": True,
        }
    _save_reader_cache()
    return _reader_cache[ip]


def rename_reader(ip: str, name: str) -> bool:
    """Rename a reader."""
    if ip in _reader_cache:
        _reader_cache[ip]["name"] = name
        _save_reader_cache()
        return True
    return False


def remove_reader(ip: str) -> bool:
    """Remove a reader from the cache."""
    if ip in _reader_cache:
        del _reader_cache[ip]
        _save_reader_cache()
        return True
    return False


def _get_device_key(device: dict, dtype: str) -> str:
    """Get a unique key for a device to detect duplicates."""
    if dtype == "sonos":
        return device.get("ip", "") or device.get("uid", "")
    elif dtype == "airplay":
        return device.get("id", "") or device.get("address", "") or device.get("ip", "")
    elif dtype == "chromecast":
        return device.get("id", "") or device.get("ip", "")
    elif dtype == "spotify":
        return device.get("id", "")
    elif dtype == "espuino":
        return device.get("ip", "") or device.get("id", "")
    return device.get("ip", "") or device.get("id", "")


def _merge_device_into_cache(device: dict, dtype: str, online: bool = True) -> dict:
    """Merge a discovered device into the cache, preserving history."""
    now = datetime.now().isoformat()
    key = _get_device_key(device, dtype)

    if not key:
        return device

    # Find existing device in cache
    existing = None
    for cached in _device_cache[dtype]:
        if _get_device_key(cached, dtype) == key:
            existing = cached
            break

    if existing:
        # Update existing device
        existing.update(device)
        existing["online"] = online
        existing["last_seen"] = now
        return existing
    else:
        # Add new device
        device["online"] = online
        device["first_seen"] = now
        device["last_seen"] = now
        _device_cache[dtype].append(device)
        return device


def update_cache_from_discovery(dtype: str, devices: list[dict]):
    """Update cache with discovered devices (marks them online)."""
    discovered_keys = set()

    for device in devices:
        _merge_device_into_cache(device, dtype, online=True)
        discovered_keys.add(_get_device_key(device, dtype))

    # Mark devices not found in this discovery as offline
    for cached in _device_cache[dtype]:
        if _get_device_key(cached, dtype) not in discovered_keys:
            cached["online"] = False

    _save_device_cache()


def get_cached_devices_with_status() -> dict[str, list[dict]]:
    """Get all cached devices with their online/offline status."""
    return _device_cache


def remove_cached_device(dtype: str, device_key: str) -> bool:
    """Remove a device from the cache permanently."""
    before = len(_device_cache[dtype])
    _device_cache[dtype] = [
        d for d in _device_cache[dtype] if _get_device_key(d, dtype) != device_key
    ]
    if len(_device_cache[dtype]) < before:
        _save_device_cache()
        return True
    return False


def get_sonos_ip_from_uid(uid_or_ip: str) -> str | None:
    """Look up Sonos IP address from UID or return IP directly.

    Sonos devices use UID (RINCON_...) as the device ID but need IP for playback.
    ESPuino stream mode may pass IP directly instead of UID.
    """
    # If it looks like an IP address, return it directly
    if uid_or_ip and "." in uid_or_ip and not uid_or_ip.startswith("RINCON"):
        return uid_or_ip

    # Otherwise look up by UID
    for device in _device_cache.get("sonos", []):
        if device.get("uid") == uid_or_ip:
            return device.get("ip")
    return None


def get_device_name(device_type: str, device_id: str) -> str | None:
    """Look up friendly device name from cache.

    Args:
        device_type: Device type (sonos, airplay, chromecast, espuino)
        device_id: Device ID (uid for Sonos, IP for stream mode, id for others)

    Returns:
        Friendly device name or None if not found
    """
    devices = _device_cache.get(device_type, [])
    for device in devices:
        # Sonos uses uid as the ID, but stream mode may use IP
        if device_type == "sonos":
            if device.get("uid") == device_id or device.get("ip") == device_id:
                return device.get("name")
        else:
            if device.get("id") == device_id:
                return device.get("name")
    return None


# Default device (persisted to settings.json)
default_device: dict[str, str] = {
    "type": "",
    "id": "",
}

# Current device (temporary override, not persisted)
current_device: dict[str, str] | None = None

# Per-reader temporary device override (not persisted)
reader_current_devices: dict[str, dict[str, str]] = {}

# Active AirPlay connections (keep alive during playback)
_airplay_connections: dict[str, Any] = {}
_airplay_stream_tasks: dict[str, asyncio.Task] = {}


async def _scan_airplay(timeout: int = 5) -> list[Any]:
    """Scan for AirPlay devices with pyatv version compatibility."""
    import pyatv

    loop = asyncio.get_running_loop()
    try:
        return await pyatv.scan(loop, timeout=timeout)
    except TypeError:
        return await pyatv.scan(timeout=timeout)


async def _connect_airplay_device(device: Any) -> Any:
    """Connect to an AirPlay device with pyatv using RAOP protocol.

    RAOP (Remote Audio Output Protocol) is more reliable than AirPlay 2
    for simple audio streaming - doesn't require complex authentication.
    """
    import pyatv
    from pyatv.const import Protocol

    loop = asyncio.get_running_loop()
    try:
        # Use RAOP protocol specifically - more reliable for audio streaming
        return await pyatv.connect(device, loop, protocol=Protocol.RAOP)
    except TypeError:
        # Fallback for older pyatv versions
        return await pyatv.connect(device, protocol=Protocol.RAOP)


def _match_airplay_device(device: Any, device_id: str) -> bool:
    """Match an AirPlay device by identifier, address, or name."""
    return (
        str(getattr(device, "identifier", "")) == device_id
        or str(getattr(device, "address", "")) == device_id
        or getattr(device, "name", "") == device_id
    )


async def _find_airplay_device(device_id: str) -> Any | None:
    """Find an AirPlay device by identifier, address, or name."""
    logger.info(f"Scanning for AirPlay device: {device_id}")
    devices = await _scan_airplay(timeout=10)
    logger.debug(f"Found {len(devices)} AirPlay devices during scan")
    for device in devices:
        if _match_airplay_device(device, device_id):
            logger.info(f"Found matching AirPlay device: {device.name}")
            return device
    logger.warning(f"AirPlay device not found: {device_id}")
    return None


def init_default_device():
    """Load default device from settings on startup."""
    global default_device
    from ..config import get_settings

    settings = get_settings()
    if settings.default_device_type and settings.default_device_id:
        default_device = {
            "type": settings.default_device_type,
            "id": settings.default_device_id,
        }
        logger.info(f"Loaded default device: {default_device}")


async def discover_sonos() -> list[dict[str, Any]]:
    """Discover Sonos speakers on the network."""
    try:
        import soco

        # Run discovery in thread pool (soco is synchronous)
        loop = asyncio.get_event_loop()
        speakers = await loop.run_in_executor(None, soco.discover, 5)

        if not speakers:
            logger.info("No Sonos speakers found")
            return []

        devices = []
        for speaker in speakers:
            try:
                devices.append(
                    {
                        "name": speaker.player_name,
                        "ip": speaker.ip_address,
                        "model": speaker.get_speaker_info().get("model_name", ""),
                        "uid": speaker.uid,
                        "is_coordinator": speaker.is_coordinator,
                    }
                )
            except Exception as e:
                logger.warning(f"Error getting speaker info: {e}")

        logger.info(f"Found {len(devices)} Sonos speakers")
        return devices

    except ImportError:
        logger.error("soco not installed")
        return []
    except Exception as e:
        logger.error(f"Sonos discovery failed: {e}")
        return []


async def discover_airplay() -> list[dict[str, Any]]:
    """Discover AirPlay devices on the network."""
    try:
        logger.info("Scanning for AirPlay devices...")
        atvs = await _scan_airplay(timeout=5)

        if not atvs:
            logger.info("No AirPlay devices found")
            return []

        result = []
        for device in atvs:
            protocols = [str(s.protocol) for s in device.services]
            result.append(
                {
                    "name": device.name,
                    "id": str(device.identifier),
                    "address": str(device.address),
                    "ip": str(device.address),
                    "model": str(device.device_info.model)
                    if device.device_info
                    else "",
                    "services": protocols,
                }
            )
            logger.info(
                f"Found AirPlay device: {device.name} ({device.address}) - {protocols}"
            )

        logger.info(f"Found {len(result)} AirPlay devices")
        return result

    except ImportError:
        logger.error("pyatv not installed")
        return []
    except Exception as e:
        logger.error(f"AirPlay discovery failed: {e}", exc_info=True)
        return []


async def discover_chromecast() -> list[dict[str, Any]]:
    """Discover Chromecast devices on the network."""
    try:
        import pychromecast

        logger.info("Scanning for Chromecast devices...")
        loop = asyncio.get_event_loop()

        # pychromecast discovery is synchronous, run in executor
        def do_discovery():
            chromecasts, browser = pychromecast.get_chromecasts(timeout=5)
            # Stop the browser to clean up
            browser.stop_discovery()
            return chromecasts

        chromecasts = await loop.run_in_executor(None, do_discovery)

        if not chromecasts:
            logger.info("No Chromecast devices found")
            return []

        result = []
        for cc in chromecasts:
            result.append(
                {
                    "name": cc.cast_info.friendly_name,
                    "id": str(cc.cast_info.uuid),
                    "ip": cc.cast_info.host,
                    "port": cc.cast_info.port,
                    "model": cc.cast_info.model_name,
                    "type": cc.cast_info.cast_type,
                }
            )
            logger.info(
                f"Found Chromecast: {cc.cast_info.friendly_name} ({cc.cast_info.host})"
            )

        logger.info(f"Found {len(result)} Chromecast devices")
        return result

    except ImportError:
        logger.error("pychromecast not installed")
        return []
    except Exception as e:
        logger.error(f"Chromecast discovery failed: {e}", exc_info=True)
        return []


async def discover_spotify(
    client_id: str = "", client_secret: str = ""
) -> list[dict[str, Any]]:
    """Get available Spotify Connect devices."""
    if not client_id or not client_secret:
        logger.warning("Spotify credentials not configured")
        return []

    try:
        import spotipy
        from spotipy.oauth2 import SpotifyOAuth

        # This requires user authentication - return empty for now
        # TODO: Implement OAuth flow for Spotify
        logger.info("Spotify discovery requires OAuth authentication")
        return []

    except ImportError:
        logger.error("spotipy not installed")
        return []
    except Exception as e:
        logger.error(f"Spotify discovery failed: {e}")
        return []


async def discover_all_devices(
    spotify_client_id: str = "", spotify_client_secret: str = ""
) -> dict[str, list]:
    """Discover all playback devices concurrently and update cache."""
    global discovered_devices

    # Run all discoveries in parallel
    sonos_task = discover_sonos()
    airplay_task = discover_airplay()
    chromecast_task = discover_chromecast()
    spotify_task = discover_spotify(spotify_client_id, spotify_client_secret)

    sonos, airplay, chromecast, spotify = await asyncio.gather(
        sonos_task, airplay_task, chromecast_task, spotify_task, return_exceptions=True
    )

    # Handle exceptions
    if isinstance(sonos, Exception):
        logger.error(f"Sonos discovery error: {sonos}")
        sonos = []
    if isinstance(airplay, Exception):
        logger.error(f"AirPlay discovery error: {airplay}")
        airplay = []
    if isinstance(chromecast, Exception):
        logger.error(f"Chromecast discovery error: {chromecast}")
        chromecast = []
    if isinstance(spotify, Exception):
        logger.error(f"Spotify discovery error: {spotify}")
        spotify = []

    discovered_devices = {
        "sonos": sonos,
        "airplay": airplay,
        "chromecast": chromecast,
        "spotify": spotify,
    }

    # Update persistent cache with discovery results
    update_cache_from_discovery("sonos", sonos)
    update_cache_from_discovery("airplay", airplay)
    update_cache_from_discovery("chromecast", chromecast)
    update_cache_from_discovery("spotify", spotify)

    logger.info(
        f"Discovery complete: {len(sonos)} Sonos, {len(airplay)} AirPlay, {len(chromecast)} Chromecast, {len(spotify)} Spotify"
    )

    return get_cached_devices_with_status()


def get_cached_devices() -> dict[str, list]:
    """Get previously discovered devices (legacy, returns in-memory only)."""
    return discovered_devices


def set_default_device(device_type: str, device_id: str) -> bool:
    """Set the default playback device and persist to settings."""
    global default_device
    default_device = {"type": device_type, "id": device_id}
    logger.info(f"Default device set to {device_type}: {device_id}")

    # Persist to settings
    from ..config import update_settings

    update_settings(
        {
            "default_device_type": device_type,
            "default_device_id": device_id,
        }
    )
    return True


def get_default_device() -> dict[str, str]:
    """Get the default device."""
    return default_device


def set_current_device(device_type: str, device_id: str) -> bool:
    """Set temporary current device (not persisted)."""
    global current_device
    current_device = {"type": device_type, "id": device_id}
    logger.info(f"Current device set to {device_type}: {device_id}")
    return True


def clear_current_device():
    """Clear temporary current device, falling back to default."""
    global current_device
    current_device = None
    logger.info("Current device cleared, using default")


def get_active_device() -> dict[str, str]:
    """Get the active device (current if set, otherwise default)."""
    if current_device:
        return current_device
    return default_device


def get_reader_device_override(reader_ip: str) -> dict[str, str] | None:
    """Get a reader-specific device override, if configured."""
    from ..config import get_settings

    settings = get_settings()
    mapping = settings.reader_devices or {}
    device = mapping.get(reader_ip)
    if device and device.get("type") and device.get("id"):
        return device
    return None


def get_device_for_reader(reader_ip: str) -> dict[str, str]:
    """Resolve the playback device for a reader (override or active default)."""
    logger.info(f"Resolving device for reader {reader_ip}")

    if reader_ip in reader_current_devices:
        device = reader_current_devices[reader_ip]
        logger.info(f"Using temp device for {reader_ip}: {device}")
        return device

    override = get_reader_device_override(reader_ip)
    if override:
        logger.info(f"Using saved device for {reader_ip}: {override}")
        return override

    device = get_active_device()
    logger.info(f"Using default device for {reader_ip}: {device}")
    return device


def set_reader_device(
    reader_ip: str, device_type: str, device_id: str
) -> dict[str, str]:
    """Persist a reader-specific device override."""
    from ..config import get_settings, update_settings

    settings = get_settings()
    mapping = dict(settings.reader_devices or {})
    mapping[reader_ip] = {"type": device_type, "id": device_id}
    update_settings({"reader_devices": mapping})
    return mapping[reader_ip]


def clear_reader_device(reader_ip: str) -> bool:
    """Remove a reader-specific device override."""
    from ..config import get_settings, update_settings

    settings = get_settings()
    mapping = dict(settings.reader_devices or {})
    if reader_ip in mapping:
        del mapping[reader_ip]
        update_settings({"reader_devices": mapping})
        return True
    return False


def set_reader_current_device(
    reader_ip: str, device_type: str, device_id: str
) -> dict[str, str]:
    """Set a temporary device override for a reader (not persisted)."""
    reader_current_devices[reader_ip] = {"type": device_type, "id": device_id}
    logger.info(f"Set temp device for reader {reader_ip}: {device_type} / {device_id}")
    return reader_current_devices[reader_ip]


def clear_reader_current_device(reader_ip: str) -> bool:
    """Clear a temporary device override for a reader."""
    if reader_ip in reader_current_devices:
        del reader_current_devices[reader_ip]
        return True
    return False


async def add_sonos_by_ip(ip: str) -> dict[str, Any] | None:
    """Add a Sonos speaker by IP address (for when discovery doesn't work)."""
    try:
        import soco

        loop = asyncio.get_event_loop()
        speaker = await loop.run_in_executor(None, lambda: soco.SoCo(ip))

        # Try to get speaker info to verify it's valid
        info = await loop.run_in_executor(None, speaker.get_speaker_info)

        device = {
            "name": speaker.player_name,
            "ip": ip,
            "model": info.get("model_name", ""),
            "uid": speaker.uid,
            "is_coordinator": speaker.is_coordinator,
            "manual": True,
        }

        # Add to cache (marks as online since we just verified it)
        _merge_device_into_cache(device, "sonos", online=True)
        _save_device_cache()
        logger.info(f"Added Sonos speaker: {device['name']} at {ip}")

        return device

    except Exception as e:
        logger.error(f"Failed to add Sonos at {ip}: {e}")
        return None


async def add_manual_device(
    device_type: str, name: str, ip: str, **kwargs
) -> dict[str, Any]:
    """Add a device manually to the persistent cache."""
    device = {
        "name": name,
        "ip": ip,
        "manual": True,
        **kwargs,
    }

    if device_type == "sonos":
        # Try to probe for more info
        probed = await add_sonos_by_ip(ip)
        if probed:
            return probed
    elif device_type == "airplay":
        # Try to resolve identifier from IP
        try:
            device_info = await _find_airplay_device(ip)
            if device_info:
                device = {
                    "name": device_info.name,
                    "ip": ip,
                    "address": str(device_info.address),
                    "id": str(device_info.identifier),
                    "model": device_info.device_info.model
                    if device_info.device_info
                    else "",
                    "services": [str(s.protocol) for s in device_info.services],
                    "manual": True,
                }
        except Exception as e:
            logger.warning(f"AirPlay probe failed for {ip}: {e}")

    # Add to persistent cache (marks as online since we just verified/added it)
    _merge_device_into_cache(device, device_type, online=True)
    _save_device_cache()

    return device


def get_all_devices() -> dict[str, list]:
    """Get all devices from persistent cache (includes online/offline status)."""
    return get_cached_devices_with_status()


def remove_manual_device(device_type: str, ip: str) -> bool:
    """Remove a device from the cache."""
    return remove_cached_device(device_type, ip)


async def _get_airplay_connection(device_id: str) -> Any | None:
    """Get or create an AirPlay connection, keeping it alive."""
    import pyatv

    # Check if we have an existing connection
    if device_id in _airplay_connections:
        atv = _airplay_connections[device_id]
        # Check if still connected by trying to get device info
        try:
            # Just check if the connection is still valid
            if atv.device_info:
                logger.debug(f"Reusing existing AirPlay connection for {device_id}")
                return atv
        except Exception as e:
            # Connection is stale, remove it
            logger.warning(f"Stale AirPlay connection for {device_id}: {e}")
            try:
                atv.close()
            except Exception:
                pass
            del _airplay_connections[device_id]

    # Create a new connection
    device = await _find_airplay_device(device_id)
    if not device:
        logger.error(f"AirPlay device {device_id} not found during scan")
        return None

    # Modern pyatv: don't pass loop parameter (deprecated)
    try:
        logger.info(f"Connecting to AirPlay device: {device.name}")
        atv = await _connect_airplay_device(device)

        # Verify we have stream interface
        if not hasattr(atv, "stream") or atv.stream is None:
            logger.error(f"Device {device_id} does not support streaming")
            atv.close()
            return None

        _airplay_connections[device_id] = atv
        logger.info(f"Successfully connected to AirPlay device: {device.name}")
        return atv
    except Exception as e:
        logger.error(
            f"Failed to connect to AirPlay device {device_id}: {e}", exc_info=True
        )
        return None


def _get_cached_airplay_connection(device_id: str) -> Any | None:
    """Return an existing AirPlay connection without scanning."""
    return _airplay_connections.get(device_id)


async def _close_airplay_connection(device_id: str):
    """Close and remove an AirPlay connection."""
    if device_id in _airplay_connections:
        try:
            _airplay_connections[device_id].close()
            logger.debug(f"Closed AirPlay connection for {device_id}")
        except Exception as e:
            logger.warning(f"Error closing AirPlay connection for {device_id}: {e}")
        del _airplay_connections[device_id]


def _cancel_airplay_stream(device_id: str):
    """Cancel any active AirPlay stream task for a device."""
    task = _airplay_stream_tasks.pop(device_id, None)
    if task and not task.done():
        task.cancel()


async def play_on_airplay(
    device_id: str,
    audio_url: str,
    title: str = "Tonie",
) -> bool:
    """Play audio on an AirPlay device using stream_file.

    Uses RAOP protocol with stream_file (pushing audio from server) which is
    more reliable than play_url (device fetches URL) on modern macOS devices.

    The audio_url is expected to be a transcode URL like:
    http://server:8754/transcode.mp3?url=<source_url>

    This function extracts the source URL, ensures it's cached, and streams
    the cached MP3 file to the device.
    """
    try:
        import pyatv
        from urllib.parse import urlparse, parse_qs
        from .transcoding import get_or_serve_cached_mp3, get_or_encode_tracks

        # Extract source URL from transcode URL
        parsed = urlparse(audio_url)
        query_params = parse_qs(parsed.query)
        source_url = query_params.get("url", [None])[0]

        if not source_url:
            logger.error(f"Could not extract source URL from: {audio_url}")
            return False

        # Get cached MP3 file, or encode if not cached
        logger.info(f"AirPlay: Ensuring cached MP3 for source: {source_url[:60]}...")
        cache_path = await get_or_serve_cached_mp3(source_url)
        if not cache_path:
            # Not cached - encode with pseudo-track
            logger.info(f"AirPlay: No cache, encoding...")
            pseudo_tracks = [{"name": "Full Audio", "duration": 7200, "start": 0}]
            await get_or_encode_tracks(source_url=source_url, tracks=pseudo_tracks)
            cache_path = await get_or_serve_cached_mp3(source_url)
        if not cache_path:
            logger.error("Failed to get cached MP3 for AirPlay")
            return False

        atv = await _get_airplay_connection(device_id)
        if not atv:
            return False

        # Check if stream_file is available
        try:
            if hasattr(atv, "features") and hasattr(pyatv, "const"):
                stream_available = atv.features.in_state(
                    pyatv.const.FeatureState.Available,
                    pyatv.const.FeatureName.StreamFile,
                )
                if not stream_available:
                    logger.warning(
                        "AirPlay device may not support stream_file, attempting anyway..."
                    )
        except Exception as check_err:
            logger.debug(f"Could not check stream feature: {check_err}")

        logger.info(f"Streaming to AirPlay via stream_file: {cache_path}")
        stream = atv.stream

        _cancel_airplay_stream(device_id)

        async def _run_stream():
            try:
                # Use stream_file - pushes audio from server to device
                # More reliable than play_url on modern macOS
                await stream.stream_file(str(cache_path))
                logger.info("AirPlay stream completed")
            except asyncio.CancelledError:
                logger.info(f"AirPlay stream cancelled for {device_id}")
                raise
            except Exception as e:
                logger.error(
                    f"AirPlay stream error for {device_id}: {e}", exc_info=True
                )
                await _close_airplay_connection(device_id)

        _airplay_stream_tasks[device_id] = asyncio.create_task(_run_stream())
        logger.info("AirPlay stream started")
        return True

    except Exception as e:
        logger.error(f"Failed to play on AirPlay {device_id}: {e}", exc_info=True)
        await _close_airplay_connection(device_id)
        return False


async def stop_airplay(device_id: str) -> bool:
    """Stop playback on an AirPlay device."""
    _cancel_airplay_stream(device_id)
    atv = _get_cached_airplay_connection(device_id)
    if not atv:
        logger.debug(f"No active AirPlay connection for {device_id}")
        return True  # Already stopped

    try:
        logger.info(f"Stopping AirPlay playback on {device_id}")
        try:
            await atv.remote_control.stop()
            logger.debug(f"AirPlay playback stopped on {device_id}")
        except Exception as stop_error:
            logger.warning(f"AirPlay stop failed for {device_id}: {stop_error}")
            await atv.remote_control.pause()
            logger.debug(f"AirPlay playback paused on {device_id}")
        # Close connection after stop
        await _close_airplay_connection(device_id)
        return True

    except Exception as e:
        logger.error(f"Failed to stop AirPlay {device_id}: {e}", exc_info=True)
        await _close_airplay_connection(device_id)
        return False


async def pause_airplay(device_id: str) -> bool:
    """Pause playback on an AirPlay device."""
    try:
        # AirPlay streaming is not reliably pausable; stop instead.
        return await stop_airplay(device_id)
    except Exception as e:
        logger.error(f"Failed to pause AirPlay {device_id}: {e}")
        return False


async def resume_airplay(device_id: str) -> bool:
    """Resume playback on an AirPlay device."""
    try:
        atv = _get_cached_airplay_connection(device_id)
        if not atv:
            return False

        await atv.remote_control.play()
        return True
    except Exception as e:
        logger.error(f"Failed to resume AirPlay {device_id}: {e}")
        return False


def _parse_time_to_seconds(position: str) -> float:
    """Parse a Sonos-style time string (HH:MM:SS) into seconds."""
    if not position:
        return 0.0
    parts = position.split(":")
    if len(parts) == 2:
        parts = ["0"] + parts
    try:
        hours, minutes, seconds = [int(p) for p in parts]
        return float(hours * 3600 + minutes * 60 + seconds)
    except ValueError:
        return 0.0


async def get_sonos_position(ip: str) -> float | None:
    """Get current playback position from a Sonos speaker in seconds."""
    try:
        import soco

        loop = asyncio.get_event_loop()
        speaker = await loop.run_in_executor(None, lambda: soco.SoCo(ip))
        info = await loop.run_in_executor(None, speaker.get_current_track_info)
        return _parse_time_to_seconds(info.get("position", ""))
    except Exception as e:
        logger.error(f"Failed to get Sonos position for {ip}: {e}")
        return None


async def get_sonos_transport_state(ip: str) -> dict | None:
    try:
        import soco

        loop = asyncio.get_event_loop()
        speaker = await loop.run_in_executor(None, lambda: soco.SoCo(ip))

        transport = await loop.run_in_executor(None, speaker.get_current_transport_info)
        state = transport.get("current_transport_state", "UNKNOWN")

        track_info = await loop.run_in_executor(None, speaker.get_current_track_info)
        position = _parse_time_to_seconds(track_info.get("position", ""))
        duration = _parse_time_to_seconds(track_info.get("duration", ""))
        title = track_info.get("title", "")
        uri = track_info.get("uri", "")

        queue_position = 1
        try:
            playlist_pos = track_info.get("playlist_position", "1")
            queue_position = int(playlist_pos) if playlist_pos else 1
        except (ValueError, TypeError):
            pass

        return {
            "state": state,
            "position": position,
            "duration": duration,
            "title": title,
            "uri": uri,
            "queue_position": queue_position,
        }
    except Exception as e:
        logger.error(f"Failed to get Sonos transport state for {ip}: {e}")
        return None


async def seek_sonos(ip: str, position: float) -> bool:
    """Seek to a position on a Sonos speaker."""
    try:
        import soco

        loop = asyncio.get_event_loop()
        speaker = await loop.run_in_executor(None, lambda: soco.SoCo(ip))
        seek_target = time.strftime("%H:%M:%S", time.gmtime(position))
        await loop.run_in_executor(None, lambda: speaker.seek(seek_target))
        return True
    except Exception as e:
        logger.error(f"Failed to seek Sonos {ip}: {e}")
        return False


async def next_track_sonos(ip: str) -> bool:
    try:
        import soco

        loop = asyncio.get_event_loop()
        speaker = await loop.run_in_executor(None, lambda: soco.SoCo(ip))
        await loop.run_in_executor(None, lambda: speaker.next())
        return True
    except Exception as e:
        logger.error(f"Failed to skip to next track on Sonos {ip}: {e}")
        return False


async def prev_track_sonos(ip: str) -> bool:
    try:
        import soco

        loop = asyncio.get_event_loop()
        speaker = await loop.run_in_executor(None, lambda: soco.SoCo(ip))
        await loop.run_in_executor(None, lambda: speaker.previous())
        return True
    except Exception as e:
        logger.error(f"Failed to skip to previous track on Sonos {ip}: {e}")
        return False


async def play_on_sonos(
    ip: str, audio_url: str, title: str = "Tonie", start_position: float | None = None
) -> bool:
    """Play audio URL on a Sonos speaker."""
    try:
        import soco

        loop = asyncio.get_event_loop()
        speaker = await loop.run_in_executor(None, lambda: soco.SoCo(ip))

        # Play the URI and explicitly start playback (some Sonos units only queue)
        logger.info(f"Playing on Sonos {ip}: {audio_url}")
        await loop.run_in_executor(
            None, lambda: speaker.play_uri(audio_url, title=title)
        )
        await loop.run_in_executor(None, speaker.play)
        if start_position and start_position > 0:
            seek_target = time.strftime("%H:%M:%S", time.gmtime(start_position))
            try:
                await loop.run_in_executor(None, lambda: speaker.seek(seek_target))
                await loop.run_in_executor(None, speaker.play)
            except Exception as seek_error:
                logger.warning(f"Sonos seek failed, continuing playback: {seek_error}")

        return True
    except Exception as e:
        logger.error(f"Failed to play on Sonos {ip}: {e}")
        return False


async def play_playlist_on_sonos(
    ip: str, track_urls: list[str], title: str = "Tonie"
) -> bool:
    """Play a playlist of tracks on a Sonos speaker.

    Clears the queue and adds all tracks, then starts playback.
    """
    if not track_urls:
        return False

    try:
        import soco

        loop = asyncio.get_event_loop()
        speaker = await loop.run_in_executor(None, lambda: soco.SoCo(ip))

        # Clear the queue first
        logger.info(f"Clearing Sonos queue on {ip}")
        await loop.run_in_executor(None, speaker.clear_queue)

        # Add all tracks to the queue
        for i, url in enumerate(track_urls, 1):
            track_title = f"{title} - Track {i}"
            logger.info(
                f"Adding track {i}/{len(track_urls)} to Sonos queue: {track_title}"
            )
            await loop.run_in_executor(
                None,
                lambda u=url, t=track_title: speaker.add_uri_to_queue(
                    u, position=0, as_next=False
                ),
            )

        # Start playback from the beginning of the queue
        logger.info(f"Starting Sonos playlist playback: {len(track_urls)} tracks")
        await loop.run_in_executor(None, lambda: speaker.play_from_queue(0))
        # Explicitly start playback (play_from_queue may only set position)
        await loop.run_in_executor(None, speaker.play)

        return True
    except Exception as e:
        logger.error(f"Failed to play playlist on Sonos {ip}: {e}")
        return False


async def queue_track_on_sonos(
    ip: str, track_url: str, track_title: str = "Track"
) -> bool:
    """Add a single track to the end of the Sonos queue (for progressive playback)."""
    try:
        import soco

        loop = asyncio.get_event_loop()
        speaker = await loop.run_in_executor(None, lambda: soco.SoCo(ip))

        await loop.run_in_executor(
            None, lambda: speaker.add_uri_to_queue(track_url, position=0, as_next=False)
        )
        logger.info(f"Queued track on Sonos {ip}: {track_title}")
        return True
    except Exception as e:
        logger.error(f"Failed to queue track on Sonos {ip}: {e}")
        return False


async def stop_sonos(ip: str) -> bool:
    """Stop playback on a Sonos speaker."""
    try:
        import soco

        loop = asyncio.get_event_loop()
        speaker = await loop.run_in_executor(None, lambda: soco.SoCo(ip))
        await loop.run_in_executor(None, speaker.stop)

        return True
    except Exception as e:
        logger.error(f"Failed to stop Sonos {ip}: {e}")
        return False


async def pause_sonos(ip: str) -> bool:
    """Pause playback on a Sonos speaker."""
    try:
        import soco

        loop = asyncio.get_event_loop()
        speaker = await loop.run_in_executor(None, lambda: soco.SoCo(ip))
        await loop.run_in_executor(None, speaker.pause)

        return True
    except Exception as e:
        logger.error(f"Failed to pause Sonos {ip}: {e}")
        return False


async def resume_sonos(ip: str) -> bool:
    """Resume playback on a Sonos speaker."""
    try:
        import soco

        loop = asyncio.get_event_loop()
        speaker = await loop.run_in_executor(None, lambda: soco.SoCo(ip))
        await loop.run_in_executor(None, speaker.play)

        return True
    except Exception as e:
        logger.error(f"Failed to resume Sonos {ip}: {e}")
        return False


# Chromecast connections (keep alive during playback)
_chromecast_connections: dict[str, Any] = {}
_chromecast_disabled = False  # Disable Chromecast if it keeps failing
_chromecast_fail_count = 0


async def _get_chromecast_connection(device_id: str) -> Any | None:
    """Get or create a Chromecast connection."""
    global _chromecast_disabled, _chromecast_fail_count

    # If Chromecast has failed too many times, skip it
    if _chromecast_disabled:
        logger.warning("Chromecast disabled due to repeated failures")
        return None

    try:
        import pychromecast

        # Check existing connection
        if device_id in _chromecast_connections:
            cc = _chromecast_connections[device_id]
            try:
                if cc.socket_client.is_connected:
                    _chromecast_fail_count = 0  # Reset on success
                    return cc
            except Exception:
                # Connection check failed, remove and retry
                del _chromecast_connections[device_id]

        # Find and connect to device with timeout protection
        loop = asyncio.get_event_loop()

        def find_device():
            browser = None
            try:
                chromecasts, browser = pychromecast.get_chromecasts(timeout=5)
                for cc in chromecasts:
                    if (
                        str(cc.cast_info.uuid) == device_id
                        or cc.cast_info.host == device_id
                    ):
                        # Wait for connection BEFORE stopping discovery
                        # (zeroconf instance is needed for socket connection)
                        cc.wait(timeout=10)
                        if browser:
                            browser.stop_discovery()
                        return cc
                # No matching device found
                if browser:
                    browser.stop_discovery()
            except Exception as e:
                logger.debug(f"Chromecast discovery error: {e}")
                if browser:
                    try:
                        browser.stop_discovery()
                    except Exception:
                        pass
            return None

        try:
            cc = await asyncio.wait_for(
                loop.run_in_executor(None, find_device), timeout=10
            )
        except asyncio.TimeoutError:
            logger.error("Chromecast connection timed out")
            _chromecast_fail_count += 1
            if _chromecast_fail_count >= 3:
                _chromecast_disabled = True
                logger.error(
                    "Chromecast disabled after 3 failures - restart server to re-enable"
                )
            return None

        if cc:
            _chromecast_connections[device_id] = cc
            _chromecast_fail_count = 0
            logger.info(f"Connected to Chromecast: {cc.cast_info.friendly_name}")
            return cc

        logger.error(f"Chromecast device not found: {device_id}")
        _chromecast_fail_count += 1
        if _chromecast_fail_count >= 3:
            _chromecast_disabled = True
            logger.error(
                "Chromecast disabled after 3 failures - restart server to re-enable"
            )
        return None

    except Exception as e:
        logger.error(f"Failed to connect to Chromecast {device_id}: {e}", exc_info=True)
        return None


async def play_on_chromecast(
    device_id: str,
    audio_url: str,
    title: str = "Tonie",
    start_position: float | None = None,
) -> bool:
    """Play audio URL on a Chromecast device."""
    try:
        cc = await _get_chromecast_connection(device_id)
        if not cc:
            return False

        loop = asyncio.get_event_loop()

        # Determine MIME type from URL
        if ".mp3" in audio_url:
            mime_type = "audio/mpeg"
        elif ".ogg" in audio_url or "vorbis" in audio_url:
            mime_type = "audio/ogg"
        elif ".m4a" in audio_url or ".aac" in audio_url:
            mime_type = "audio/mp4"
        else:
            mime_type = "audio/mpeg"  # Default to MP3 for our transcoded files

        def do_play():
            mc = cc.media_controller
            mc.play_media(audio_url, mime_type, title=title)
            mc.block_until_active(timeout=10)
            if start_position and start_position > 0:
                mc.seek(start_position)

        await loop.run_in_executor(None, do_play)
        logger.info(f"Playing on Chromecast {device_id}: {title} (mime: {mime_type})")
        return True

    except Exception as e:
        logger.error(f"Failed to play on Chromecast {device_id}: {e}", exc_info=True)
        return False


async def play_playlist_on_chromecast(
    device_id: str, track_urls: list[str], title: str = "Tonie"
) -> bool:
    """Play a playlist of tracks on a Chromecast device.

    Uses queueing to play multiple tracks in sequence.
    """
    if not track_urls:
        return False

    try:
        cc = await _get_chromecast_connection(device_id)
        if not cc:
            return False

        loop = asyncio.get_event_loop()

        def do_queue_play():
            mc = cc.media_controller

            # Play the first track
            logger.info(f"Playing first track on Chromecast: {title} - Track 1")
            mc.play_media(track_urls[0], "audio/mpeg", title=f"{title} - Track 1")
            mc.block_until_active(timeout=10)

            # Queue the remaining tracks
            for i, url in enumerate(track_urls[1:], 2):
                track_title = f"{title} - Track {i}"
                logger.info(
                    f"Queueing track {i}/{len(track_urls)} on Chromecast: {track_title}"
                )
                # Use play_media with enqueue=True for queuing
                try:
                    mc.play_media(url, "audio/mpeg", title=track_title, enqueue=True)
                except TypeError:
                    # Older pychromecast versions may not support enqueue
                    logger.warning(
                        f"Chromecast queueing not supported, only first track will play"
                    )
                    break

        await loop.run_in_executor(None, do_queue_play)
        logger.info(f"Started Chromecast playlist: {len(track_urls)} tracks")
        return True

    except Exception as e:
        logger.error(
            f"Failed to play playlist on Chromecast {device_id}: {e}", exc_info=True
        )
        return False


async def queue_track_on_chromecast(
    device_id: str, track_url: str, track_title: str = "Track"
) -> bool:
    """Add a single track to the Chromecast queue (for progressive playback)."""
    try:
        cc = await _get_chromecast_connection(device_id)
        if not cc:
            return False

        loop = asyncio.get_event_loop()

        def do_enqueue():
            mc = cc.media_controller
            try:
                mc.play_media(track_url, "audio/mpeg", title=track_title, enqueue=True)
            except TypeError:
                # Older pychromecast versions may not support enqueue
                logger.warning(f"Chromecast queueing not supported for {track_title}")
                return False
            return True

        result = await loop.run_in_executor(None, do_enqueue)
        if result:
            logger.info(f"Queued track on Chromecast {device_id}: {track_title}")
        return result

    except Exception as e:
        logger.error(f"Failed to queue track on Chromecast {device_id}: {e}")
        return False


async def stop_chromecast(device_id: str) -> bool:
    """Stop playback on a Chromecast device."""
    try:
        if device_id not in _chromecast_connections:
            return True  # Already stopped

        cc = _chromecast_connections[device_id]
        loop = asyncio.get_event_loop()

        def do_stop():
            mc = cc.media_controller
            mc.stop()

        await loop.run_in_executor(None, do_stop)
        logger.info(f"Stopped Chromecast {device_id}")
        return True

    except Exception as e:
        logger.error(f"Failed to stop Chromecast {device_id}: {e}")
        return False


async def pause_chromecast(device_id: str) -> bool:
    """Pause playback on a Chromecast device."""
    try:
        cc = await _get_chromecast_connection(device_id)
        if not cc:
            return False

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, cc.media_controller.pause)
        return True

    except Exception as e:
        logger.error(f"Failed to pause Chromecast {device_id}: {e}")
        return False


async def resume_chromecast(device_id: str) -> bool:
    """Resume playback on a Chromecast device."""
    try:
        cc = await _get_chromecast_connection(device_id)
        if not cc:
            return False

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, cc.media_controller.play)
        return True

    except Exception as e:
        logger.error(f"Failed to resume Chromecast {device_id}: {e}")
        return False


# ESPuino playback functions
async def play_on_espuino(
    ip: str, audio_url: str, title: str = "Tonie", start_position: float | None = None
) -> bool:
    """Play audio URL on an ESPuino device.

    ESPuino accepts HTTP stream URLs via its /exploreraudio endpoint.
    The URL should be a ToniePlayer transcode URL for best compatibility.
    """
    import aiohttp
    from urllib.parse import quote

    try:
        # ESPuino expects the URL as 'path' parameter, playmode=8 for webstream
        espuino_url = (
            f"http://{ip}/exploreraudio?path={quote(audio_url, safe='')}&playmode=8"
        )

        logger.info(f"Playing on ESPuino {ip}: {title}")
        logger.debug(f"ESPuino URL: {espuino_url}")

        async with aiohttp.ClientSession() as session:
            async with session.post(
                espuino_url, timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status == 200:
                    logger.info(f"ESPuino {ip} playback started: {title}")
                    return True
                else:
                    text = await resp.text()
                    logger.error(f"ESPuino {ip} returned {resp.status}: {text}")
                    return False
    except asyncio.TimeoutError:
        logger.error(f"ESPuino {ip} connection timeout")
        return False
    except Exception as e:
        logger.error(f"Failed to play on ESPuino {ip}: {e}")
        return False


async def play_espuino_from_sd(ip: str, folder_path: str, title: str = "Tonie") -> bool:
    """Play audio from local SD card folder on ESPuino.

    For multi-track Tonies stored on SD card. ESPuino will play all MP3s in folder.
    Uses playmode=3 (all tracks in folder, random order off).
    """
    import aiohttp
    from urllib.parse import quote

    try:
        # SD card path format: /sd/teddycloud/Disney_Dumbo/
        # playmode=3: play all files in directory
        sd_path = f"/sd{folder_path}"
        espuino_url = (
            f"http://{ip}/exploreraudio?path={quote(sd_path, safe='')}&playmode=3"
        )

        logger.info(f"Playing from SD on ESPuino {ip}: {sd_path}")
        logger.debug(f"ESPuino SD URL: {espuino_url}")

        async with aiohttp.ClientSession() as session:
            async with session.post(
                espuino_url, timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status == 200:
                    logger.info(f"ESPuino {ip} SD playback started: {title}")
                    return True
                else:
                    text = await resp.text()
                    logger.error(
                        f"ESPuino {ip} SD playback failed {resp.status}: {text}"
                    )
                    return False
    except asyncio.TimeoutError:
        logger.error(f"ESPuino {ip} connection timeout")
        return False
    except Exception as e:
        logger.error(f"Failed to play SD on ESPuino {ip}: {e}")
        return False


async def check_espuino_sd_ready(
    ip: str, folder_path: str, expected_tracks: int = 0
) -> dict:
    """
    Check if a Tonie folder on ESPuino SD is ready for local playback.

    Simple check: just count MP3 files in folder. Avoids parsing metadata.json
    which can fail due to ESPuino's buggy web server responses.

    Returns:
        {
            "ready": bool - True if folder has MP3 files
            "folder_exists": bool
            "tracks_complete": int - number of MP3 files found
            "tracks_total": int - expected total tracks
            "play_path": str - SD path to use for playback (if ready)
        }
    """
    import aiohttp
    from urllib.parse import quote
    import json as json_lib

    result = {
        "ready": False,
        "folder_exists": False,
        "tracks_complete": 0,
        "tracks_total": expected_tracks,
        "play_path": None,
    }

    try:
        url = f"http://{ip}/explorer?path={quote(folder_path, safe='')}"

        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status != 200:
                    return result

                # ESPuino sometimes returns garbage after JSON - extract valid JSON
                try:
                    raw_text = await resp.text()
                    bracket_count = 0
                    json_end = 0
                    for i, char in enumerate(raw_text):
                        if char == "[":
                            bracket_count += 1
                        elif char == "]":
                            bracket_count -= 1
                            if bracket_count == 0:
                                json_end = i + 1
                                break
                    if json_end > 0:
                        files = json_lib.loads(raw_text[:json_end])
                    else:
                        files = json_lib.loads(raw_text)
                except (json_lib.JSONDecodeError, ValueError):
                    # Can't parse - assume not ready, will stream instead
                    return result

        result["folder_exists"] = True

        # Count MP3 files
        mp3_count = sum(1 for f in files if f.get("name", "").lower().endswith(".mp3"))
        result["tracks_complete"] = mp3_count

        # Ready if we have all expected tracks (or at least some if expected is 0)
        if expected_tracks > 0:
            result["ready"] = mp3_count >= expected_tracks
        else:
            result["ready"] = mp3_count > 0

        if result["ready"]:
            result["play_path"] = folder_path

        return result

    except Exception as e:
        logger.debug(f"Failed to check SD ready on ESPuino {ip}: {e}")
        return result


async def stop_espuino(ip: str) -> bool:
    """Stop playback on an ESPuino device via WebSocket command."""
    import aiohttp
    import json

    logger.info(f"Attempting to stop ESPuino at {ip}")

    try:
        # ESPuino uses WebSocket for commands
        # CMD_STOP = 182 (from values.h)
        ws_url = f"ws://{ip}/ws"
        stop_cmd = json.dumps({"controls": {"action": 182}})

        logger.debug(f"Connecting to WebSocket: {ws_url}")
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(ws_url, timeout=5) as ws:
                await ws.send_str(stop_cmd)
                logger.info(f"Sent stop command to ESPuino {ip}: {stop_cmd}")
                return True
    except Exception as e:
        logger.error(f"Failed to stop ESPuino {ip}: {e}")
        return False


async def pause_espuino(ip: str) -> bool:
    """Pause playback on an ESPuino device."""
    import aiohttp

    try:
        # ESPuino pause/play toggle
        url = f"http://{ip}/cmd?cmd=pauseplay"

        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status == 200:
                    logger.info(f"Toggled pause on ESPuino {ip}")
                    return True
                return False
    except Exception as e:
        logger.error(f"Failed to pause ESPuino {ip}: {e}")
        return False


async def resume_espuino(ip: str) -> bool:
    """Resume playback on an ESPuino device (same as pause - toggle)."""
    return await pause_espuino(ip)


class ProgressFileReader(io.BufferedReader):
    """Buffered reader that tracks read progress for upload monitoring."""

    def __init__(
        self,
        file_path: Path,
        callback,
        max_bytes_per_sec: int = 0,
        chunk_size: int = 64 * 1024,
    ):
        raw = open(file_path, "rb")
        super().__init__(raw)
        self.total_size = file_path.stat().st_size
        self.bytes_read = 0
        self.callback = callback
        self.last_callback_time = 0.0
        self.max_bytes_per_sec = max_bytes_per_sec
        self.chunk_size = chunk_size
        self.start_time = time.time()

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0 or size > self.chunk_size:
            size = self.chunk_size
        data = super().read(size)
        if not data:
            return data
        self.bytes_read += len(data)
        if self.max_bytes_per_sec > 0:
            expected_elapsed = self.bytes_read / self.max_bytes_per_sec
            actual_elapsed = time.time() - self.start_time
            if expected_elapsed > actual_elapsed:
                time.sleep(expected_elapsed - actual_elapsed)
        # Throttle callbacks to avoid overwhelming (every 100ms or completion)
        now = time.time()
        if now - self.last_callback_time > 0.1 or self.bytes_read >= self.total_size:
            self.callback(self.bytes_read, self.total_size)
            self.last_callback_time = now
        return data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


async def upload_to_espuino(
    ip: str,
    file_path: Path,
    dest_path: str,
    title: str = "",
    max_retries: int = 3,
    track_index: int | None = None,
    total_tracks: int | None = None,
    max_kbps: int | None = None,
    is_aux: bool = False,
) -> dict:
    """Upload a file to ESPuino SD card with progress tracking and retry logic.

    Args:
        ip: ESPuino IP address
        file_path: Local path to the file to upload
        dest_path: Destination path on ESPuino SD card (e.g., "/teddycloud/abc123.mp3")
        title: Optional title for display in progress UI
        max_retries: Number of retry attempts on failure (default 3)

    Returns:
        dict with status and details
    """
    import aiohttp
    from urllib.parse import quote

    if not file_path.exists():
        logger.error(f"File not found for upload: {file_path}")
        return {"success": False, "error": "File not found"}

    if _should_cancel_upload(ip):
        logger.info(f"Upload cancelled before start for ESPuino {ip}: {dest_path}")
        set_upload_status(
            ip,
            dest_path,
            "error",
            bytes_uploaded=0,
            total_bytes=0,
            error="Cancelled by user",
            track_index=track_index,
            total_tracks=total_tracks,
            is_aux=is_aux,
        )
        return {"success": False, "error": "Cancelled by user"}

    file_size = file_path.stat().st_size
    start_time = time.time()
    last_progress_time = time.time()

    # Initialize upload status (include source_path for retry)
    set_upload_status(
        ip,
        dest_path,
        "uploading",
        bytes_uploaded=0,
        total_bytes=file_size,
        started_at=start_time,
        title=title or Path(dest_path).name,
        source_path=str(file_path),
        track_index=track_index,
        total_tracks=total_tracks,
        is_aux=is_aux,
    )

    # ESPuino expects POST /explorer?path=<dest_dir> with multipart file upload
    dest_dir = str(Path(dest_path).parent)
    if dest_dir == ".":
        dest_dir = "/"

    url = f"http://{ip}/explorer?path={quote(dest_dir, safe='')}"

    # Ensure destination directory exists (create parents if needed)
    async def ensure_dir(path: str) -> None:
        if not path or path == "/":
            return
        parts = [p for p in path.split("/") if p]
        current = ""
        async with aiohttp.ClientSession() as session:
            for part in parts:
                current += f"/{part}"
                dir_url = f"http://{ip}/explorer?path={quote(current, safe='')}"
                try:
                    async with session.put(
                        dir_url, timeout=aiohttp.ClientTimeout(total=5)
                    ) as resp:
                        if resp.status != 200:
                            logger.debug(
                                f"ESPuino {ip} mkdir {current} returned {resp.status}"
                            )
                except Exception as e:
                    logger.debug(f"ESPuino {ip} mkdir {current} failed: {e}")

    await ensure_dir(dest_dir)

    # Retry loop with exponential backoff
    last_error = None
    for attempt in range(max_retries):
        if _should_cancel_upload(ip):
            logger.info(f"Upload cancelled for ESPuino {ip}: {dest_path}")
            set_upload_status(
                ip,
                dest_path,
                "error",
                bytes_uploaded=0,
                total_bytes=file_size,
                error="Cancelled by user",
                track_index=track_index,
                total_tracks=total_tracks,
                is_aux=is_aux,
            )
            return {"success": False, "error": "Cancelled by user"}

        if attempt > 0:
            delay = 5 * (2 ** (attempt - 1))  # 5s, 10s, 20s
            logger.info(
                f"Retry {attempt + 1}/{max_retries} for {file_path.name} after {delay}s delay..."
            )
            set_upload_status(
                ip,
                dest_path,
                "retrying",
                bytes_uploaded=0,
                total_bytes=file_size,
                started_at=start_time,
                title=f"{title} (retry {attempt + 1})",
                track_index=track_index,
                total_tracks=total_tracks,
                is_aux=is_aux,
            )
            await asyncio.sleep(delay)

        try:
            stale_threshold = 10.0
            cancel_task = asyncio.current_task()

            async def watchdog():
                while True:
                    await asyncio.sleep(1)
                    if _should_cancel_upload(ip):
                        if cancel_task:
                            cancel_task.cancel()
                        return
                    if time.time() - last_progress_time > stale_threshold:
                        if cancel_task:
                            cancel_task.cancel()
                        return

            logger.info(
                f"Uploading to ESPuino {ip}: {file_path.name} ({file_size / 1024 / 1024:.1f}MB) -> {dest_path}"
            )

            # Update progress to show we're uploading
            set_upload_status(
                ip,
                dest_path,
                "uploading",
                bytes_uploaded=0,
                total_bytes=file_size,
                started_at=time.time(),
                title=title or Path(dest_path).name,
                track_index=track_index,
                total_tracks=total_tracks,
                is_aux=is_aux,
            )

            async with aiohttp.ClientSession() as session:
                # Stream file content for upload with progress tracking (legacy style).
                # ESPuino is sensitive to chunked transfer, so use FormData + ProgressFileReader.
                def on_progress(bytes_read: int, total: int) -> None:
                    nonlocal last_progress_time
                    set_upload_status(
                        ip,
                        dest_path,
                        "uploading",
                        bytes_uploaded=bytes_read,
                        total_bytes=total,
                        title=title or Path(dest_path).name,
                        track_index=track_index,
                        total_tracks=total_tracks,
                        is_aux=is_aux,
                    )
                    last_progress_time = time.time()
                    if _should_cancel_upload(ip) and cancel_task:
                        cancel_task.cancel()

                effective_kbps = (
                    ESPUINO_UPLOAD_MAX_KBPS if max_kbps is None else max_kbps
                )
                max_bytes_per_sec = effective_kbps * 1024 if effective_kbps > 0 else 0

                content_type = (
                    "application/json"
                    if file_path.suffix.lower() == ".json"
                    else "audio/mpeg"
                )
                with ProgressFileReader(
                    file_path, on_progress, max_bytes_per_sec=max_bytes_per_sec
                ) as reader:
                    data = aiohttp.FormData()
                    data.add_field(
                        "file",
                        reader,
                        filename=Path(dest_path).name,
                        content_type=content_type,
                    )

                    watchdog_task = asyncio.create_task(watchdog())
                    # Set a generous timeout for large files (90 seconds per MB, min 180s)
                    # ESPuino SD writes are slow (~300-500KB/s typical)
                    timeout_seconds = max(180, int(file_size / 1024 / 1024 * 90))

                    try:
                        async with session.post(
                            url,
                            data=data,
                            timeout=aiohttp.ClientTimeout(total=timeout_seconds),
                        ) as resp:
                            if resp.status == 200:
                                elapsed = time.time() - start_time
                                rate_mbps = (
                                    (file_size / 1024 / 1024) / elapsed
                                    if elapsed > 0
                                    else 0
                                )
                                logger.info(
                                    f"Upload complete to ESPuino {ip}: {dest_path} "
                                    f"({file_size / 1024 / 1024:.1f}MB in {elapsed:.1f}s, {rate_mbps:.2f} MB/s)"
                                )

                                # Mark as complete (keep status for a few seconds for UI)
                                set_upload_status(
                                    ip,
                                    dest_path,
                                    "complete",
                                    bytes_uploaded=file_size,
                                    total_bytes=file_size,
                                    started_at=start_time,
                                    title=title or Path(dest_path).name,
                                    track_index=track_index,
                                    total_tracks=total_tracks,
                                    is_aux=is_aux,
                                )

                                # Schedule cleanup after 5 seconds
                                async def cleanup_status():
                                    await asyncio.sleep(5)
                                    clear_upload_status(ip, dest_path)

                                asyncio.create_task(cleanup_status())

                                return {
                                    "success": True,
                                    "path": dest_path,
                                    "size": file_size,
                                }
                            else:
                                text = await resp.text()
                                last_error = f"HTTP {resp.status}: {text}"
                                logger.warning(
                                    f"ESPuino {ip} upload attempt {attempt + 1} failed: {last_error}"
                                )
                    finally:
                        watchdog_task.cancel()

        except asyncio.TimeoutError:
            last_error = "Timeout"
            logger.warning(f"Upload to ESPuino {ip} attempt {attempt + 1} timed out")
        except asyncio.CancelledError:
            if _should_cancel_upload(ip):
                last_error = "Cancelled by user"
            else:
                last_error = "Stalled: no progress for 10s"
            set_upload_status(
                ip,
                dest_path,
                "error",
                bytes_uploaded=0,
                total_bytes=file_size,
                error=last_error,
                track_index=track_index,
                total_tracks=total_tracks,
                is_aux=is_aux,
            )
            logger.warning(f"Upload to ESPuino {ip} aborted: {last_error}")
            return {"success": False, "error": last_error}
        except Exception as e:
            last_error = str(e)
            logger.warning(f"Upload to ESPuino {ip} attempt {attempt + 1} failed: {e}")

    # All retries exhausted
    logger.error(
        f"Upload to ESPuino {ip} failed after {max_retries} attempts: {last_error}"
    )
    set_upload_status(
        ip,
        dest_path,
        "error",
        bytes_uploaded=0,
        total_bytes=file_size,
        error=last_error[:100] if last_error else "Unknown error",
        track_index=track_index,
        total_tracks=total_tracks,
        is_aux=is_aux,
    )
    return {"success": False, "error": last_error}


async def check_espuino_file_exists(ip: str, file_path: str) -> bool:
    """Check if a file exists on ESPuino SD card.

    Args:
        ip: ESPuino IP address
        file_path: Path to check on ESPuino SD card

    Returns:
        True if file exists, False otherwise
    """
    import aiohttp
    from urllib.parse import quote

    try:
        # ESPuino /explorer endpoint returns directory listing
        # We check if the parent directory contains the file
        parent_dir = str(Path(file_path).parent)
        if parent_dir == ".":
            parent_dir = "/"

        url = f"http://{ip}/explorer?path={quote(parent_dir, safe='')}"

        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status == 200:
                    files = await resp.json()
                    target_name = Path(file_path).name
                    return any(f.get("name") == target_name for f in files)
                return False
    except Exception as e:
        logger.debug(f"Failed to check file on ESPuino {ip}: {e}")
        return False


async def delete_espuino_file(ip: str, file_path: str) -> bool:
    """Delete a file on ESPuino SD card."""
    import aiohttp
    from urllib.parse import quote

    try:
        url = f"http://{ip}/explorer?path={quote(file_path, safe='')}"
        async with aiohttp.ClientSession() as session:
            async with session.delete(
                url, timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                return resp.status == 200
    except Exception as e:
        logger.debug(f"Failed to delete file on ESPuino {ip}: {e}")
        return False


async def set_espuino_rfid_mapping(
    ip: str, tag_id: str, folder_path: str, play_mode: int = 5
) -> bool:
    """Create/update an ESPuino RFID mapping (e.g., play all tracks in folder sorted)."""
    import aiohttp

    if not folder_path:
        logger.warning(f"Skipping RFID mapping for {ip}: empty folder_path")
        return False

    payload = {
        "id": tag_id,
        "fileOrUrl": folder_path,
        "playMode": play_mode,
    }
    try:
        url = f"http://{ip}/rfid"
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url, json=payload, timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                return resp.status == 200
    except Exception as e:
        logger.debug(f"Failed to set RFID mapping on ESPuino {ip}: {e}")
        return False


async def get_espuino_file_size(ip: str, file_path: str) -> int | None:
    """Get the size of a file on ESPuino SD card.

    Returns file size in bytes, or None if file doesn't exist or error.
    """
    import aiohttp
    from urllib.parse import quote

    try:
        parent_dir = str(Path(file_path).parent)
        if parent_dir == ".":
            parent_dir = "/"

        url = f"http://{ip}/explorer?path={quote(parent_dir, safe='')}"

        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status == 200:
                    files = await resp.json()
                    target_name = Path(file_path).name
                    for f in files:
                        if f.get("name") == target_name:
                            return f.get("size", 0)
                return None
    except Exception as e:
        logger.debug(f"Failed to get file size on ESPuino {ip}: {e}")
        return None


async def verify_espuino_upload(
    ip: str, folder_path: str, uid_map_path: str | None = None
) -> dict:
    """
    Verify upload completeness by checking metadata.json and file sizes.

    Returns:
        {
            "complete": bool,
            "total_tracks": int,
            "verified_tracks": int,
            "missing_tracks": list[int],
            "size_mismatch": list[int],
            "metadata": dict or None
        }
    """
    import aiohttp
    from urllib.parse import quote
    import json as json_lib

    result = {
        "complete": False,
        "total_tracks": 0,
        "verified_tracks": 0,
        "missing_tracks": [],
        "size_mismatch": [],
        "metadata": None,
        "folder": None,
    }

    def extract_json_blob(raw_text: str) -> dict | list | None:
        start = None
        stack = []
        for i, char in enumerate(raw_text):
            if char in "[{":
                start = i
                stack.append(char)
                break
        if start is None:
            return None
        for j in range(start + 1, len(raw_text)):
            char = raw_text[j]
            if char in "[{":
                stack.append(char)
            elif char == "]":
                if stack and stack[-1] == "[":
                    stack.pop()
                if not stack:
                    try:
                        return json_lib.loads(raw_text[start : j + 1])
                    except json_lib.JSONDecodeError:
                        return None
            elif char == "}":
                if stack and stack[-1] == "{":
                    stack.pop()
                if not stack:
                    try:
                        return json_lib.loads(raw_text[start : j + 1])
                    except json_lib.JSONDecodeError:
                        return None
        return None

    try:
        # First, check if metadata.json exists and read it
        metadata_path = f"{folder_path}/metadata.json"
        url = f"http://{ip}/explorer?path={quote(folder_path, safe='')}"

        async with aiohttp.ClientSession() as session:
            # Get directory listing
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status != 200:
                    logger.debug(f"Folder not found on ESPuino: {folder_path}")
                    return result
                # ESPuino sometimes returns garbage after JSON - try to extract valid JSON
                try:
                    raw_text = await resp.text()
                    # Find the end of the JSON array
                    bracket_count = 0
                    json_end = 0
                    for i, char in enumerate(raw_text):
                        if char == "[":
                            bracket_count += 1
                        elif char == "]":
                            bracket_count -= 1
                            if bracket_count == 0:
                                json_end = i + 1
                                break
                    if json_end > 0:
                        files = json_lib.loads(raw_text[:json_end])
                    else:
                        files = json_lib.loads(raw_text)
                except (json_lib.JSONDecodeError, ValueError) as e:
                    logger.warning(f"Failed to parse ESPuino explorer response: {e}")
                    return result

            # Build file index by name
            file_index = {f.get("name"): f for f in files}

            # Check for metadata.json
            if "metadata.json" in file_index:
                # Download and parse metadata.json
                metadata_url = (
                    f"http://{ip}/explorerdownload?path={quote(metadata_path, safe='')}"
                )
                async with session.get(
                    metadata_url, timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    if resp.status == 200:
                        raw = await resp.text()
                        parsed = extract_json_blob(raw)
                        if isinstance(parsed, dict):
                            result["metadata"] = parsed
                        else:
                            logger.warning("Failed to parse metadata.json payload")
                            return result
                    else:
                        logger.warning(
                            f"Failed to read metadata.json: HTTP {resp.status}"
                        )
                        return result
            elif uid_map_path:
                uid_url = (
                    f"http://{ip}/explorerdownload?path={quote(uid_map_path, safe='')}"
                )
                async with session.get(
                    uid_url, timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    if resp.status == 200:
                        raw = await resp.text()
                        parsed = extract_json_blob(raw)
                        if isinstance(parsed, dict):
                            files = parsed.get("files", [])
                            result["metadata"] = {
                                "tracks": [
                                    {
                                        "index": f.get("index", i),
                                        "file": f.get("name", ""),
                                        "size": f.get("size", 0),
                                    }
                                    for i, f in enumerate(files)
                                ]
                            }
                            result["metadata"]["uid"] = parsed.get("uid")
                            result["folder"] = parsed.get("folder")
                        else:
                            logger.warning("Failed to parse UID map payload")
                            return result
                    else:
                        logger.warning(f"Failed to read UID map: HTTP {resp.status}")
                        return result
            else:
                logger.debug(f"No metadata.json found in {folder_path}")
                return result

        metadata = result["metadata"]
        tracks = metadata.get("tracks", [])
        result["total_tracks"] = len(tracks)

        # Verify each track
        for track in tracks:
            track_file = track.get("file")
            expected_size = track.get("size", 0)
            track_index = track.get("index", 0)

            if track_file not in file_index:
                result["missing_tracks"].append(track_index)
            else:
                actual_size = file_index[track_file].get("size", 0)
                if expected_size > 0 and actual_size != expected_size:
                    result["size_mismatch"].append(track_index)
                else:
                    result["verified_tracks"] += 1

        result["complete"] = (
            result["verified_tracks"] == result["total_tracks"]
            and len(result["missing_tracks"]) == 0
            and len(result["size_mismatch"]) == 0
        )

        return result

    except Exception as e:
        logger.error(f"Failed to verify upload on ESPuino {ip}: {e}")
        return result


async def play_on_default_device(audio_url: str, title: str = "Tonie") -> bool:
    """Play audio on the active device (current or default)."""
    device = get_active_device()
    if not device.get("type") or not device.get("id"):
        logger.warning("No device set")
        return False

    device_type = device["type"]
    device_id = device["id"]

    if device_type == "browser":
        # Browser playback is handled client-side
        logger.info(f"Browser playback requested: {title}")
        return True
    elif device_type == "sonos":
        sonos_ip = get_sonos_ip_from_uid(device_id)
        if not sonos_ip:
            logger.warning(f"Could not find IP for Sonos UID {device_id}")
            return False
        return await play_on_sonos(sonos_ip, audio_url, title)
    elif device_type == "airplay":
        return await play_on_airplay(device_id, audio_url, title)
    elif device_type == "chromecast":
        return await play_on_chromecast(device_id, audio_url, title)
    elif device_type == "espuino":
        return await play_on_espuino(device_id, audio_url, title)
    else:
        logger.warning(f"Playback not implemented for {device_type}")
        return False


async def play_on_device(
    device: dict[str, str],
    audio_url: str,
    title: str = "Tonie",
    start_position: float | None = None,
) -> bool:
    """Play audio on a specific device config."""
    if not device.get("type") or not device.get("id"):
        logger.warning("No device set")
        return False

    device_type = device["type"]
    device_id = device["id"]

    if device_type == "browser":
        logger.info(f"Browser playback requested: {title}")
        return True
    if device_type == "sonos":
        sonos_ip = get_sonos_ip_from_uid(device_id)
        if not sonos_ip:
            logger.warning(f"Could not find IP for Sonos UID {device_id}")
            return False
        return await play_on_sonos(
            sonos_ip, audio_url, title, start_position=start_position
        )
    if device_type == "airplay":
        return await play_on_airplay(device_id, audio_url, title)
    if device_type == "chromecast":
        return await play_on_chromecast(
            device_id, audio_url, title, start_position=start_position
        )
    if device_type == "espuino":
        return await play_on_espuino(
            device_id, audio_url, title, start_position=start_position
        )
    logger.warning(f"Playback not implemented for {device_type}")
    return False


async def play_playlist_on_device(
    device: dict[str, str],
    track_urls: list[str],
    title: str = "Tonie",
) -> bool:
    """Play a playlist of tracks on a specific device config.

    Falls back to playing only the first track if playlist not supported.
    """
    if not device.get("type") or not device.get("id"):
        logger.warning("No device set")
        return False

    if not track_urls:
        logger.warning("No tracks to play")
        return False

    device_type = device["type"]
    device_id = device["id"]

    if device_type == "browser":
        # Browser handles playlist via JavaScript
        logger.info(f"Browser playlist requested: {title} ({len(track_urls)} tracks)")
        return True
    if device_type == "sonos":
        sonos_ip = get_sonos_ip_from_uid(device_id)
        if not sonos_ip:
            logger.warning(f"Could not find IP for Sonos UID {device_id}")
            return False
        return await play_playlist_on_sonos(sonos_ip, track_urls, title)
    if device_type == "chromecast":
        return await play_playlist_on_chromecast(device_id, track_urls, title)
    if device_type == "airplay":
        # AirPlay doesn't support playlists well, play first track only
        logger.info(f"AirPlay: playing first track only (playlist not supported)")
        return await play_on_airplay(device_id, track_urls[0], f"{title} - Track 1")
    if device_type == "espuino":
        # ESPuino plays from SD card folder, not direct URLs
        # For now, play first track via streaming
        logger.info(
            f"ESPuino: playing first track (SD folder playback handled separately)"
        )
        return await play_on_espuino(device_id, track_urls[0], f"{title} - Track 1")

    # Fallback: play first track only
    logger.warning(f"Playlist not implemented for {device_type}, playing first track")
    return await play_on_device(device, track_urls[0], f"{title} - Track 1")


async def queue_track_on_device(
    device: dict[str, str],
    track_url: str,
    track_title: str = "Track",
) -> bool:
    """Queue a single track on a device (for progressive playlist building).

    Only supported on Sonos and Chromecast. Other devices return False.
    """
    if not device.get("type") or not device.get("id"):
        return False

    device_type = device["type"]
    device_id = device["id"]

    if device_type == "sonos":
        sonos_ip = get_sonos_ip_from_uid(device_id)
        if not sonos_ip:
            logger.warning(f"Could not find IP for Sonos UID {device_id}")
            return False
        return await queue_track_on_sonos(sonos_ip, track_url, track_title)
    if device_type == "chromecast":
        return await queue_track_on_chromecast(device_id, track_url, track_title)

    # Other device types don't support progressive queueing
    return False


async def stop_default_device() -> bool:
    """Stop playback on the active device."""
    device = get_active_device()
    if not device.get("type") or not device.get("id"):
        return False

    device_type = device["type"]
    device_id = device["id"]

    if device_type == "browser":
        return True  # Handled client-side
    elif device_type == "sonos":
        sonos_ip = get_sonos_ip_from_uid(device_id)
        if not sonos_ip:
            logger.warning(f"Could not find IP for Sonos UID {device_id}")
            return False
        return await stop_sonos(sonos_ip)
    elif device_type == "airplay":
        return await stop_airplay(device_id)
    elif device_type == "chromecast":
        return await stop_chromecast(device_id)
    elif device_type == "espuino":
        return await stop_espuino(device_id)
    return False


async def stop_device(device: dict[str, str]) -> bool:
    """Stop playback on a specific device config."""
    if not device.get("type") or not device.get("id"):
        return False

    device_type = device["type"]
    device_id = device["id"]

    if device_type == "browser":
        return True
    if device_type == "sonos":
        sonos_ip = get_sonos_ip_from_uid(device_id)
        if not sonos_ip:
            logger.warning(f"Could not find IP for Sonos UID {device_id}")
            return False
        return await stop_sonos(sonos_ip)
    if device_type == "airplay":
        return await stop_airplay(device_id)
    if device_type == "chromecast":
        return await stop_chromecast(device_id)
    if device_type == "espuino":
        return await stop_espuino(device_id)
    return False


async def pause_device(device: dict[str, str]) -> bool:
    """Pause playback on a specific device config."""
    if not device.get("type") or not device.get("id"):
        return False

    device_type = device["type"]
    device_id = device["id"]

    if device_type == "browser":
        return True
    if device_type == "sonos":
        sonos_ip = get_sonos_ip_from_uid(device_id)
        if not sonos_ip:
            logger.warning(f"Could not find IP for Sonos UID {device_id}")
            return False
        return await pause_sonos(sonos_ip)
    if device_type == "airplay":
        return await pause_airplay(device_id)
    if device_type == "chromecast":
        return await pause_chromecast(device_id)
    if device_type == "espuino":
        return await pause_espuino(device_id)
    return False


async def resume_device(device: dict[str, str]) -> bool:
    """Resume playback on a specific device config."""
    if not device.get("type") or not device.get("id"):
        return False

    device_type = device["type"]
    device_id = device["id"]

    if device_type == "browser":
        return True
    if device_type == "sonos":
        sonos_ip = get_sonos_ip_from_uid(device_id)
        if not sonos_ip:
            logger.warning(f"Could not find IP for Sonos UID {device_id}")
            return False
        return await resume_sonos(sonos_ip)
    if device_type == "airplay":
        return await resume_airplay(device_id)
    if device_type == "chromecast":
        return await resume_chromecast(device_id)
    if device_type == "espuino":
        return await resume_espuino(device_id)
    return False


async def get_device_position(device: dict[str, str]) -> float | None:
    """Get current playback position in seconds for a device."""
    if not device.get("type") or not device.get("id"):
        return None

    device_type = device["type"]
    device_id = device["id"]

    if device_type == "sonos":
        sonos_ip = get_sonos_ip_from_uid(device_id)
        if not sonos_ip:
            logger.warning(f"Could not find IP for Sonos UID {device_id}")
            return None
        return await get_sonos_position(sonos_ip)
    return None


async def is_device_playing(device: dict[str, str]) -> bool:
    """Check if a device is currently playing audio."""
    state = await get_device_transport_state(device)
    if state:
        return state.get("state") == "playing"
    return False


async def get_device_transport_state(device: dict[str, str]) -> dict | None:
    """Get full transport state for a device.

    Returns dict with:
        - state: "playing", "paused", "stopped", "transitioning", "unknown"
        - position: current position in seconds
        - duration: total duration in seconds (if available)
    """
    if not device.get("type") or not device.get("id"):
        return None

    device_type = device["type"]
    device_id = device["id"]

    if device_type == "sonos":
        # Sonos needs IP, but device ID is UID - look up the IP
        sonos_ip = get_sonos_ip_from_uid(device_id)
        if not sonos_ip:
            logger.warning(f"Could not find IP for Sonos UID {device_id}")
            return None
        state = await get_sonos_transport_state(sonos_ip)
        if state:
            state_map = {
                "PLAYING": "playing",
                "PAUSED_PLAYBACK": "paused",
                "STOPPED": "stopped",
                "TRANSITIONING": "transitioning",
            }
            return {
                "state": state_map.get(state["state"], "unknown"),
                "position": state["position"],
                "duration": state["duration"],
                "uri": state.get("uri", ""),
            }
    elif device_type == "chromecast":
        # Get Chromecast state if connected
        if device_id in _chromecast_connections:
            try:
                cc = _chromecast_connections[device_id]
                mc = cc.media_controller
                if mc.status:
                    state_map = {
                        "PLAYING": "playing",
                        "PAUSED": "paused",
                        "IDLE": "stopped",
                        "BUFFERING": "transitioning",
                    }
                    return {
                        "state": state_map.get(mc.status.player_state, "unknown"),
                        "position": mc.status.current_time or 0,
                        "duration": mc.status.duration or 0,
                    }
            except Exception as e:
                logger.debug(f"Failed to get Chromecast state: {e}")

    return None


async def seek_device(device: dict[str, str], position: float) -> bool:
    """Seek to a position on a device."""
    if not device.get("type") or not device.get("id"):
        return False

    device_type = device["type"]
    device_id = device["id"]

    if device_type == "sonos":
        sonos_ip = get_sonos_ip_from_uid(device_id)
        if not sonos_ip:
            logger.warning(f"Could not find IP for Sonos UID {device_id}")
            return False
        return await seek_sonos(sonos_ip, position)
    elif device_type == "chromecast":
        if device_id in _chromecast_connections:
            try:
                cc = _chromecast_connections[device_id]
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(
                    None, lambda: cc.media_controller.seek(position)
                )
                return True
            except Exception as e:
                logger.error(f"Failed to seek Chromecast: {e}")
    return False


async def pause_default_device() -> bool:
    """Pause playback on the active device."""
    device = get_active_device()
    if not device.get("type") or not device.get("id"):
        return False

    device_type = device["type"]
    device_id = device["id"]

    if device_type == "browser":
        return True  # Handled client-side
    elif device_type == "sonos":
        sonos_ip = get_sonos_ip_from_uid(device_id)
        if not sonos_ip:
            logger.warning(f"Could not find IP for Sonos UID {device_id}")
            return False
        return await pause_sonos(sonos_ip)
    elif device_type == "airplay":
        return await pause_airplay(device_id)
    elif device_type == "chromecast":
        return await pause_chromecast(device_id)
    return False


async def play_default_device() -> bool:
    """Resume playback on the active device."""
    device = get_active_device()
    if not device.get("type") or not device.get("id"):
        return False

    device_type = device["type"]
    device_id = device["id"]

    if device_type == "browser":
        return True  # Handled client-side
    elif device_type == "sonos":
        sonos_ip = get_sonos_ip_from_uid(device_id)
        if not sonos_ip:
            logger.warning(f"Could not find IP for Sonos UID {device_id}")
            return False
        return await resume_sonos(sonos_ip)
    elif device_type == "airplay":
        return await resume_airplay(device_id)
    elif device_type == "chromecast":
        return await resume_chromecast(device_id)
    return False
