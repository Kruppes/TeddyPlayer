# TeddyPlayer Firmware

Optional ESP32-based RFID reader for standalone Tonie figure reading.

## Overview

This firmware turns an ESP32 + PN5180 NFC reader into a Tonie tag scanner that communicates with the TeddyPlayer server. When a Tonie figure is placed on the reader, it sends the tag's UID to the server, which then streams the audio to your chosen playback device.

## Features

- PN5180 NFC reader support (ISO15693 for Tonie tags)
- WiFi connectivity with web-based configuration
- Status LED indication (WS2812B/Neopixel)
- Automatic reconnection
- OTA updates (optional)

## Hardware Requirements

See [HARDWARE.md](HARDWARE.md) for detailed wiring diagrams.

For a complete parts list with purchase links, see [HARDWARE_BOM.md](HARDWARE_BOM.md).

### Minimum Components

| Component | Description |
|-----------|-------------|
| ESP32 DevKit | Any ESP32-WROOM-32 board |
| PN5180 NFC Module | ISO15693 support required |
| WS2812B LED | Status indicator (optional) |
| USB Cable | For power and flashing |

## Quick Start

### 1. Install PlatformIO

```bash
# Install PlatformIO CLI
pip install platformio

# Or use VS Code extension
```

### 2. Configure

```bash
# Copy example config
cp src/config.example.h src/config.h

# Edit with your settings
nano src/config.h
```

Required settings in `config.h`:
```cpp
#define WIFI_SSID "your-wifi-name"
#define WIFI_PASSWORD "your-wifi-password"
#define SERVER_URL "http://your-server-ip:8754"
```

### 3. Build and Flash

```bash
# Build
pio run

# Flash (adjust port as needed)
pio run --target upload --upload-port /dev/ttyUSB0

# Monitor serial output
pio device monitor --baud 115200
```

## Configuration Options

All configuration is in `src/config.h`:

| Setting | Default | Description |
|---------|---------|-------------|
| `WIFI_SSID` | "" | Your WiFi network name |
| `WIFI_PASSWORD` | "" | Your WiFi password |
| `SERVER_URL` | "" | TeddyPlayer server URL |
| `DEVICE_HOSTNAME` | "teddyplayer" | mDNS hostname |
| `PN5180_NSS` | 5 | SPI chip select pin |
| `PN5180_BUSY` | 2 | Busy signal pin |
| `PN5180_RST` | 4 | Reset pin |
| `NEOPIXEL_PIN` | 13 | Status LED pin |

## LED Status Colors

The status LED indicates the current state:
- **Blue/Purple** - Idle, waiting for tag
- **Yellow/Green** - Tag detected, processing/streaming
- **Red** - Error (e.g., WiFi disconnected)

## ESPuino Integration

For more advanced features like local playback, SD card caching, and multi-room control, consider using an [ESPuino](https://github.com/biologist79/ESPuino)-based setup.

A custom ESPuino firmware with TeddyPlayer integration exists but is not yet publicly released. It provides:

- Local audio playback on the ESP32
- SD card caching for offline play
- Stream mode (ESP32 as remote control for Sonos/etc.)
- Physical button controls

## Troubleshooting

### No tag detection
- Verify PN5180 wiring (both 5V and 3.3V required)
- Check SPI connections
- Monitor serial output for errors

### WiFi connection issues
- Verify credentials in config.h
- Check serial output for connection status
- Device creates AP "TeddyPlayer-Setup" if WiFi fails

### Server communication errors
- Verify SERVER_URL is correct
- Check that TeddyPlayer server is running
- Test with `curl http://your-server:8754/health`

## Development

### Project Structure

```
firmware/
├── src/
│   ├── main.cpp           # Main firmware code
│   ├── config.h           # Your configuration (gitignored)
│   └── config.example.h   # Configuration template
├── platformio.ini         # PlatformIO config
├── HARDWARE.md            # Wiring diagrams
└── HARDWARE_BOM.md        # Parts list
```

### Serial Debugging

Enable TCP logging in config.h to stream logs over the network:

```cpp
#define LOG_PORT 9876
#define LOG_ENABLED true
```

Then connect with netcat:
```bash
nc <device-ip> 9876
```
