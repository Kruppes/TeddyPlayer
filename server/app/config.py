"""Application configuration with JSON file persistence."""

from __future__ import annotations

import json
import os
import socket
from pathlib import Path
from typing import Any, Dict

from pydantic import BaseModel
from pydantic_settings import BaseSettings


def get_local_ip() -> str:
    """Auto-detect the server's local IP address."""
    try:
        # Create a socket and connect to an external address
        # This doesn't actually send data, just determines which interface would be used
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "localhost"


# Config file location (can be overridden by CONFIG_DIR env var)
CONFIG_DIR = Path(os.getenv("CONFIG_DIR", "/app/config"))
SETTINGS_FILE = CONFIG_DIR / "settings.json"


class TeddyCloudConfig(BaseModel):
    url: str = "http://localhost:80"  # External URL (UI/proxy)
    internal_url: str = ""  # Internal URL (audio fetching) - empty = use url
    api_base: str = "/api"
    timeout: int = 30


class SpotifyConfig(BaseModel):
    client_id: str = ""
    client_secret: str = ""
    redirect_uri: str = "http://localhost:8000/callback"


class Settings(BaseSettings):
    # TeddyCloud settings
    teddycloud_url: str = "http://localhost:80"  # External URL (for UI/proxy)
    teddycloud_internal_url: str = ""  # Internal URL (for audio fetching) - empty = use teddycloud_url
    teddycloud_api_base: str = "/api"
    teddycloud_timeout: int = 30

    # Spotify settings
    spotify_client_id: str = ""
    spotify_client_secret: str = ""
    spotify_redirect_uri: str = "http://localhost:8000/callback"

    # Playback settings
    default_playback_target: str = "sonos"
    default_device_type: str = ""
    default_device_id: str = ""
    reader_devices: Dict[str, Dict[str, str]] = {}

    # Server URL for external devices (Sonos needs to reach transcoding endpoint)
    # Leave empty to auto-detect from request, or set explicitly like "http://your-server-ip:8754"
    server_url: str = ""

    # Audio cache settings (for pre-encoded M4A files)
    audio_cache_max_mb: int = 500  # Maximum cache size in MB

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False

    @property
    def teddycloud(self) -> TeddyCloudConfig:
        return TeddyCloudConfig(
            url=self.teddycloud_url,
            internal_url=self.teddycloud_internal_url,
            api_base=self.teddycloud_api_base,
            timeout=self.teddycloud_timeout,
        )

    @property
    def spotify(self) -> SpotifyConfig:
        return SpotifyConfig(
            client_id=self.spotify_client_id,
            client_secret=self.spotify_client_secret,
            redirect_uri=self.spotify_redirect_uri,
        )


_settings: Settings | None = None


def load_settings_from_file() -> dict[str, Any]:
    """Load settings from JSON file if it exists."""
    if SETTINGS_FILE.exists():
        try:
            with open(SETTINGS_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def save_settings_to_file(settings: dict[str, Any]) -> bool:
    """Save settings to JSON file."""
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with open(SETTINGS_FILE, "w") as f:
            json.dump(settings, f, indent=2)
        return True
    except IOError:
        return False


def get_settings() -> Settings:
    """Get settings, merging env vars with JSON file (JSON takes precedence)."""
    global _settings
    if _settings is None:
        # Load base settings from env
        _settings = Settings()

        # Override with JSON file settings
        file_settings = load_settings_from_file()
        if file_settings:
            for key, value in file_settings.items():
                if hasattr(_settings, key):
                    setattr(_settings, key, value)

    return _settings


def update_settings(updates: dict[str, Any]) -> Settings:
    """Update settings and persist to JSON file."""
    global _settings
    settings = get_settings()

    # Load existing file settings
    file_settings = load_settings_from_file()

    # Apply updates
    for key, value in updates.items():
        if hasattr(settings, key):
            setattr(settings, key, value)
            file_settings[key] = value

    # Save to file
    save_settings_to_file(file_settings)

    return settings


def get_editable_settings() -> dict[str, Any]:
    """Get settings that can be edited via the UI."""
    settings = get_settings()
    return {
        "teddycloud_url": settings.teddycloud_url,
        "server_url": settings.server_url,
        "default_playback_target": settings.default_playback_target,
        "default_device_type": settings.default_device_type,
        "default_device_id": settings.default_device_id,
        "spotify_client_id": settings.spotify_client_id,
        "spotify_client_secret": settings.spotify_client_secret,
        "audio_cache_max_mb": settings.audio_cache_max_mb,
    }


# =============================================
# User Preferences (stored in preferences.json)
# =============================================
PREFERENCES_FILE = CONFIG_DIR / "preferences.json"

_preferences: dict[str, Any] | None = None


def get_preferences() -> dict[str, Any]:
    """Get user preferences from file."""
    global _preferences
    if _preferences is None:
        _preferences = {
            "recentlyPlayed": [],
            "hiddenItems": [],
            "starredDevices": ["browser|web"],
        }
        if PREFERENCES_FILE.exists():
            try:
                with open(PREFERENCES_FILE) as f:
                    loaded = json.load(f)
                    _preferences.update(loaded)
            except (json.JSONDecodeError, IOError):
                pass
    return _preferences


def update_preferences(updates: dict[str, Any]) -> dict[str, Any]:
    """Update preferences and persist to file."""
    global _preferences
    prefs = get_preferences()

    # Apply updates
    for key, value in updates.items():
        prefs[key] = value

    # Save to file
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with open(PREFERENCES_FILE, "w") as f:
            json.dump(prefs, f, indent=2)
    except IOError:
        pass

    return prefs
