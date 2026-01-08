# Hardware Setup

## Components

| Component | Model | Notes |
|-----------|-------|-------|
| Microcontroller | ESP32 DevKit (ESP-WROOM-32) | Any ESP32 board works |
| NFC Reader | PN5180 | ISO15693 support required for Tonie tags |
| Status LED | WS2812B (Neopixel) | Single pixel for status indication |

## Wiring

### PN5180 → ESP32

| PN5180 Pin | ESP32 GPIO | Function |
|------------|------------|----------|
| 5V | VIN | Power (5V required for antenna) |
| 3.3V | 3V3 | Logic supply (required for stable reads) |
| GND | GND | Ground |
| SCK | GPIO 18 | SPI Clock |
| MISO | GPIO 19 | SPI Data Out |
| MOSI | GPIO 23 | SPI Data In |
| NSS | GPIO 5 | SPI Chip Select |
| BUSY | GPIO 2 | Busy Signal |
| RST | GPIO 4 | Reset |

**Not connected:** IRQ, AUX, REQ (not needed for basic tag reading)

**Important:** The PN5180 needs both 5V (RF/antenna) and 3.3V (logic). Running only on 5V leads to ghost reads, stuck LEDs, or no tag detection.

### Neopixel → ESP32

| Neopixel Pin | ESP32 | Notes |
|--------------|-------|-------|
| V+ | VIN (5V) | Neopixel needs 5V |
| GND | GND | Ground |
| DIN | GPIO 13 | Data (3.3V logic OK) |

## Status LED Colors

The status LED indicates the current state:
- **Blue/Purple** - Idle, waiting for tag
- **Yellow/Green** - Tag detected, processing/streaming
- **Red** - Error (e.g., WiFi disconnected)

## Schematic

```
                    ESP32 DevKit
                   ┌───────────┐
                   │           │
    PN5180 NSS ────┤ GPIO 5    │
    PN5180 BUSY ───┤ GPIO 2    │
    PN5180 RST ────┤ GPIO 4    │
    PN5180 MOSI ───┤ GPIO 23   │
    PN5180 MISO ───┤ GPIO 19   │
    PN5180 SCK ────┤ GPIO 18   │
                   │           │
    Neopixel DIN ──┤ GPIO 13   │
                   │           │
                   │    VIN ───┼──── 5V (PN5180, Neopixel)
                   │    GND ───┼──── GND (all)
                   └───────────┘
```
