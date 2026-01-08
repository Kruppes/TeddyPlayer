# Hardware Bill of Materials (BOM)

Parts list for building a TeddyPlayer RFID reader.

## Required Components

| Component | Quantity | Description | Approx. Cost |
|-----------|----------|-------------|--------------|
| ESP32 DevKit | 1 | ESP-WROOM-32 development board | ~$5-10 |
| PN5180 NFC Module | 1 | ISO15693 NFC reader (required for Tonie tags) | ~$8-15 |
| Micro USB Cable | 1 | For power and programming | ~$2 |

**Minimum total: ~$15-25**

## Optional Components

| Component | Quantity | Description | Approx. Cost |
|-----------|----------|-------------|--------------|
| WS2812B LED | 1 | Status indicator (Neopixel) | ~$1 |
| Prototype PCB | 1 | For permanent assembly | ~$1 |
| Pin Headers | 1 set | For socketed connections | ~$1 |
| Enclosure | 1 | 3D printed or project box | varies |
| 5V Power Supply | 1 | If not using USB power | ~$3-5 |

## Component Details

### ESP32 DevKit

Any ESP32-WROOM-32 based board works. Popular options:
- ESP32 DevKit V1
- ESP32 DevKit V4
- NodeMCU-32S

**Requirements:**
- USB programming support
- At least 6 available GPIO pins
- 5V and 3.3V power output

### PN5180 NFC Module

The PN5180 is specifically required because:
- Supports ISO15693 (Tonie tags use this protocol)
- Better range than cheaper modules (RC522 won't work)
- Handles high-frequency 13.56MHz tags

**Warning:** The cheaper RC522/MFRC522 modules do NOT support Tonie tags.

### Status LED (Optional)

A single WS2812B/Neopixel LED provides visual feedback:
- Connection status
- Tag detection
- Error indication

Any WS2812B-based LED or strip works. For a single status light, get individual addressable LEDs.

## Wiring Summary

```
ESP32 Pin    →    Component
─────────────────────────────
GPIO 5       →    PN5180 NSS (Chip Select)
GPIO 2       →    PN5180 BUSY
GPIO 4       →    PN5180 RST
GPIO 18      →    PN5180 SCK (SPI Clock)
GPIO 19      →    PN5180 MISO
GPIO 23      →    PN5180 MOSI
GPIO 13      →    Neopixel DIN
VIN (5V)     →    PN5180 5V, Neopixel V+
3V3          →    PN5180 3.3V
GND          →    All grounds
```

See [HARDWARE.md](HARDWARE.md) for detailed wiring diagrams and important notes.

## Sourcing

These components are widely available from:
- AliExpress (cheapest, 2-4 week shipping)
- Amazon (faster shipping, slightly higher prices)
- Local electronics stores

**Tip:** Buy a couple of each component - they're cheap and having spares is useful for debugging.

## Alternative Builds

### ESPuino-Based Reader

For a more feature-rich build with local playback capability, consider the [ESPuino](https://github.com/biologist79/ESPuino) project which supports:
- Built-in audio amplifier and speaker
- SD card for local caching
- Physical buttons and rotary encoder
- LED ring for status indication

This requires additional hardware but provides standalone audio playback.

### Commercial Readers

Some commercial NFC readers may work if they:
- Support ISO15693 protocol
- Can be configured to send HTTP requests
- Provide the full tag UID

However, the ESP32 + PN5180 combination is the most tested and reliable option for TeddyPlayer.
