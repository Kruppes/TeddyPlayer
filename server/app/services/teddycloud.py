"""TeddyCloud API client.

Based on patterns from teddycloud-custom-tonie-manager.
"""

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class TeddyCloudClient:
    """Async HTTP client for TeddyCloud API."""

    def __init__(
        self,
        base_url: str = "http://docker",
        api_base: str = "/api",
        timeout: int = 30,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_base = api_base
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None

    def _build_url(self, endpoint: str) -> str:
        """Build full URL for endpoint."""
        endpoint = endpoint.lstrip("/")
        base = self.base_url.rstrip("/")
        # Handle /web suffix from some TeddyCloud URLs
        if base.endswith("/web"):
            base = base[:-4]
        return f"{base}{self.api_base}/{endpoint}"

    async def check_connection(self) -> bool:
        """Test if TeddyCloud is accessible."""
        try:
            client = await self._get_client()
            base = self.base_url.rstrip("/")
            if not base.endswith("/web"):
                url = f"{base}/web"
            else:
                url = base
            response = await client.get(url, timeout=5)
            return response.status_code == 200
        except Exception as e:
            logger.warning(f"TeddyCloud not accessible: {e}")
            return False

    async def get_tonies(self) -> list[dict[str, Any]]:
        """Fetch all tonies from TeddyCloud.

        Returns combined list from toniesJson and toniesCustomJson.
        """
        try:
            client = await self._get_client()

            # Fetch official tonies
            url = self._build_url("toniesJson")
            response = await client.get(url)
            response.raise_for_status()
            official = response.json()
            logger.info(f"Fetched {len(official)} official tonies")

            # Fetch custom tonies
            url = self._build_url("toniesCustomJson")
            response = await client.get(url)
            response.raise_for_status()
            custom = response.json()
            logger.info(f"Fetched {len(custom)} custom tonies")

            return official + custom
        except Exception as e:
            logger.error(f"Failed to fetch tonies: {e}")
            return []

    async def find_tonie_by_uid(self, uid: str) -> dict[str, Any] | None:
        """Find a tonie by its UID.

        First checks the tag index for registered tags, then falls back to catalog.

        Args:
            uid: Tag UID in format "E0:04:03:50:13:16:80:4B"

        Returns:
            Tonie data dict or None if not found
        """
        # Normalize UID for comparison (remove colons, uppercase)
        normalized_uid = uid.replace(":", "").upper()

        # First check the tag index (registered tags on Tonieboxes)
        tags = await self.get_tag_index()
        for tag in tags:
            tag_uid = tag.get("uid", "").replace(":", "").upper()
            # ESPuino only sends 4 bytes (8 hex chars) of the UID, so do suffix match
            if tag_uid == normalized_uid or tag_uid.endswith(normalized_uid):
                logger.info(f"Found tag in index: {tag}")
                # Tag found - get associated tonie info
                result = {
                    "uid": uid,
                    "source": tag.get("source", ""),
                    "valid": tag.get("valid", False),
                    "exists": tag.get("exists", False),
                    "audio_path": tag.get("audioUrl", ""),  # TeddyCloud's audio path
                }

                # Parse track info - trackSeconds is at root level in TeddyCloud API
                track_seconds = tag.get("trackSeconds", [])
                num_tracks = max(0, len(track_seconds) - 1) if track_seconds else 0
                duration = track_seconds[-1] if track_seconds else 0

                logger.info(f"Tag {uid[:16]} trackSeconds: {len(track_seconds)} entries, num_tracks={num_tracks}, duration={duration}s")

                # Get track names from tonieInfo if available
                tonie_info = tag.get("tonieInfo", {})
                track_names = tonie_info.get("tracks", [])

                # Build tracks array with individual durations and names
                tracks = []
                for i in range(num_tracks):
                    track_start = track_seconds[i]
                    track_end = track_seconds[i + 1] if i + 1 < len(track_seconds) else duration
                    track_duration = track_end - track_start
                    # Use track name from tonieInfo if available
                    track_name = track_names[i] if i < len(track_names) else f"Track {i + 1}"
                    tracks.append({
                        "name": track_name,
                        "duration": track_duration,
                        "start": track_start,
                    })

                result["duration"] = duration
                result["num_tracks"] = num_tracks
                result["tracks"] = tracks

                logger.info(f"Tag {uid[:16]} built {len(tracks)} tracks with start/duration data")

                # If there's a tonieInfo, use it
                if tonie_info:
                    result.update({
                        "model": tonie_info.get("model", ""),
                        "series": tonie_info.get("series", ""),
                        "episode": tonie_info.get("episode", ""),
                        "title": tonie_info.get("title", ""),
                        "picture": tonie_info.get("picture", ""),
                    })
                return result

        # Fall back to catalog search
        tonies = await self.get_tonies()

        for tonie in tonies:
            tonie_uid = tonie.get("uid", "").replace(":", "").upper()
            # ESPuino only sends 4 bytes (8 hex chars) of the UID, so do suffix match
            if tonie_uid == normalized_uid or tonie_uid.endswith(normalized_uid):
                return tonie

            # Also check model field (some tonies use this)
            model = tonie.get("model", "").upper()
            if model == normalized_uid or model.endswith(normalized_uid):
                return tonie

        logger.info(f"Tonie not found for UID: {uid}")
        return None

    async def get_tag_index(self, box_id: str = "") -> list[dict[str, Any]]:
        """Get RFID tags for a specific Toniebox.

        Args:
            box_id: Toniebox ID (overlay), empty for default

        Returns:
            List of tag entries
        """
        try:
            client = await self._get_client()
            url = self._build_url(f"getTagIndex?overlay={box_id}")
            response = await client.get(url)
            response.raise_for_status()
            data = response.json()
            return data.get("tags", [])
        except Exception as e:
            logger.error(f"Failed to fetch tag index: {e}")
            return []

    async def get_tonieboxes(self) -> list[dict[str, Any]]:
        """Get list of registered Tonieboxes."""
        try:
            client = await self._get_client()
            url = self._build_url("tonieboxes")
            response = await client.get(url)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Failed to fetch tonieboxes: {e}")
            return []

    def get_audio_url(self, uid: str) -> str:
        """Get the audio stream URL for a Tonie UID.

        TeddyCloud serves audio at /content/download with the UID path.
        For library items (lib:path), use /content/path with OGG conversion.
        """
        from urllib.parse import quote
        base = self.base_url.rstrip("/")
        if base.endswith("/web"):
            base = base[:-4]

        # Handle library items (lib:path format)
        if uid.startswith("lib:"):
            lib_path = uid[4:]  # Remove "lib:" prefix
            encoded_path = quote(lib_path, safe="/")
            return f"{base}/content/{encoded_path}?ogg=true&special=library"

        # Regular Tonie UIDs - remove colons for hex path
        uid_path = uid.replace(":", "")
        return f"{base}/content/{uid_path}"

    async def get_library_files(self, path: str = "/") -> list[dict[str, Any]]:
        """Get TAF files from the library, recursively scanning subdirectories.

        Args:
            path: Directory path to scan (relative to library root)

        Returns:
            List of TAF file entries with metadata
        """
        all_files = []

        async def scan_directory(dir_path: str):
            try:
                client = await self._get_client()
                # Use fileIndexV2 with special=library
                url = self._build_url(f"fileIndexV2?path={dir_path}&special=library")
                response = await client.get(url)
                response.raise_for_status()
                data = response.json()

                for item in data.get("files", []):
                    name = item.get("name", "")

                    # Skip parent directory entry
                    if name == "..":
                        continue

                    if item.get("isDir"):
                        # Recursively scan subdirectory
                        subdir = f"{dir_path}/{name}".lstrip("/")
                        await scan_directory(subdir)
                    elif name.lower().endswith(".taf"):
                        # Build full path for TAF file
                        full_path = f"{dir_path}/{name}".lstrip("/")

                        # Get tonie info if available
                        tonie_info = item.get("tonieInfo", {})
                        taf_header = item.get("tafHeader", {})

                        # Parse track info from trackSeconds array
                        track_seconds = taf_header.get("trackSeconds", [])
                        num_tracks = max(0, len(track_seconds) - 1) if track_seconds else 0
                        duration = track_seconds[-1] if track_seconds else 0

                        # Build tracks array with individual durations
                        tracks = []
                        for i in range(num_tracks):
                            track_start = track_seconds[i]
                            track_end = track_seconds[i + 1] if i + 1 < len(track_seconds) else duration
                            track_duration = track_end - track_start
                            tracks.append({
                                "name": f"Track {i + 1}",
                                "duration": track_duration,
                                "start": track_start,
                            })

                        # Size in MB
                        size_bytes = item.get("size", 0)
                        size_mb = round(size_bytes / 1024 / 1024, 1) if size_bytes else 0

                        all_files.append({
                            "name": name,
                            "path": full_path,
                            "folder": dir_path.lstrip("/") if dir_path != "/" else "",
                            "size": size_bytes,
                            "size_mb": size_mb,
                            "date": item.get("date", 0),
                            "series": tonie_info.get("series", ""),
                            "episode": tonie_info.get("episode", ""),
                            "title": tonie_info.get("episode") or tonie_info.get("series") or name.replace(".taf", ""),
                            "picture": tonie_info.get("picture", ""),
                            "model": tonie_info.get("model", ""),
                            "language": tonie_info.get("language", ""),
                            "valid": taf_header.get("valid", False),
                            "audio_id": taf_header.get("audioId", 0),
                            "duration": duration,
                            "num_tracks": num_tracks,
                            "tracks": tracks,
                        })
            except Exception as e:
                logger.error(f"Failed to scan library directory {dir_path}: {e}")

        await scan_directory(path)
        logger.info(f"Found {len(all_files)} TAF files in library")
        return sorted(all_files, key=lambda x: (x.get("series", "").lower(), x.get("title", "").lower()))
