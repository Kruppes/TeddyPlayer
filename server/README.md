# TeddyPlayer Server

Web server for streaming Tonies from TeddyCloud to ESPuino and other audio players.

## DEPLOYMENT INSTRUCTIONS

**DO NOT deploy on Mac!** This server requires `network_mode: host` which does NOT work on Docker Desktop for Mac.
Deploy on a Linux Docker host instead.

See [DOCKER_DEPLOYMENT.md](../docs/DOCKER_DEPLOYMENT.md) for detailed deployment instructions.

## Features

- Multi-track MP3 encoding (splits Tonies into chapters)
- Cached transcoding for seekable playback
- Support for network players (Sonos, AirPlay, Chromecast)
- Browser-based playback
- **ESPuino-specific** (optional): Upload to ESPuino SD card for local caching

## Configuration

Settings are stored in `/app/config/settings.json` inside the container:

- TeddyCloud URL
- Default playback mode
- Encoding settings

Access the web UI at `http://<host>:8754`
