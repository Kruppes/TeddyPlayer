# Docker Deployment Guide

How to deploy TeddyPlayer using Docker.

## Prerequisites

- Docker and Docker Compose installed
- [TeddyCloud](https://github.com/toniebox-reverse-engineering/teddycloud) running and accessible
- Network access to your playback devices (Sonos, Chromecast, etc.)

## Quick Start

### Option 1: Docker Compose (Recommended)

```bash
# Clone the repository
git clone https://github.com/Kruppes/TeddyPlayer.git
cd TeddyPlayer/server

# Start the server
docker compose up -d

# View logs
docker compose logs -f
```

Access the web UI at `http://your-server-ip:8754`

### Option 2: Docker Run

```bash
docker run -d \
  --name teddyplayer \
  --network host \
  -v /path/to/config:/app/config \
  -e ESPUINO_ENABLED=false \
  --restart unless-stopped \
  ghcr.io/kruppes/teddyplayer:latest
```

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PORT` | 8754 | Server port |
| `ESPUINO_ENABLED` | false | Enable ESP32 reader features |
| `CONFIG_DIR` | /app/config | Configuration directory |
| `PYTHONUNBUFFERED` | 1 | Disable Python output buffering |

### Volume Mounts

| Container Path | Purpose |
|----------------|---------|
| `/app/config` | Settings, cache, preferences |

The config directory stores:
- `settings.json` - Server settings (TeddyCloud URL, etc.)
- `preferences.json` - UI preferences
- `audio_cache/` - Transcoded audio cache

## Docker Compose Options

### Basic (docker-compose.yml)

```yaml
services:
  teddyplayer:
    build: .
    container_name: teddyplayer
    network_mode: host
    environment:
      - ESPUINO_ENABLED=false
    volumes:
      - ./config:/app/config
    restart: unless-stopped
```

### With Version Info

Build with version tracking:

```bash
docker compose build \
  --build-arg GIT_COMMIT=$(git rev-parse --short HEAD) \
  --build-arg BUILD_TIME=$(date -u +%Y-%m-%dT%H:%M:%SZ)
docker compose up -d
```

### Portainer Stack

See `portainer-stack.yml` for a Portainer-compatible stack definition.

## Network Mode

TeddyPlayer uses `network_mode: host` because:

1. **Device Discovery**: Sonos, Chromecast, and AirPlay devices are discovered via mDNS/UPnP which requires direct network access
2. **Performance**: Avoids Docker NAT overhead for audio streaming
3. **Simplicity**: No port mapping needed

### Alternative: Bridge Mode

If host mode isn't available (e.g., macOS Docker Desktop), you'll need to:

1. Use bridge mode with port mapping
2. Configure devices manually by IP (no auto-discovery)

```yaml
services:
  teddyplayer:
    build: .
    ports:
      - "8754:8754"
    # ... other config
```

**Note:** Device discovery won't work in bridge mode. You'll need to add devices manually.

## Building from Source

### Build the Image

```bash
cd server
docker build -t teddyplayer:latest \
  --build-arg GIT_COMMIT=$(git rev-parse --short HEAD) \
  --build-arg BUILD_TIME=$(date -u +%Y-%m-%dT%H:%M:%SZ) \
  .
```

### Dockerfile Overview

The Dockerfile:
1. Uses Python 3.11 slim base
2. Installs FFmpeg for audio transcoding
3. Installs Python dependencies
4. Runs Uvicorn ASGI server

## Updating

### With Docker Compose

```bash
cd TeddyPlayer/server
git pull
docker compose build
docker compose up -d
```

### With Watchtower

If using Watchtower for automatic updates, add the label:

```yaml
labels:
  - "com.centurylinklabs.watchtower.enable=true"
```

## Troubleshooting

### Container won't start

```bash
# Check logs
docker logs teddyplayer

# Check if port is in use
netstat -tlnp | grep 8754
```

### Device discovery not working

1. Ensure `network_mode: host` is set
2. Check firewall allows mDNS (port 5353 UDP)
3. Verify devices are on the same network

### TeddyCloud connection errors

1. Verify TeddyCloud URL in settings
2. Test connectivity: `curl http://your-teddycloud:80/api/toniesJson`
3. Check Docker network can reach TeddyCloud

### Audio not playing

1. Check playback device is online in web UI
2. Verify FFmpeg is working: `docker exec teddyplayer ffmpeg -version`
3. Check audio cache permissions: `docker exec teddyplayer ls -la /app/config/audio_cache`

## Resource Usage

Typical resource usage:
- **Memory**: 100-200 MB idle, up to 500 MB during transcoding
- **CPU**: Low idle, spikes during FFmpeg encoding
- **Disk**: Depends on cache size (audio files cached for faster playback)

## Security Notes

- TeddyPlayer has no authentication by default
- Only expose on trusted networks
- Consider reverse proxy with auth for remote access
