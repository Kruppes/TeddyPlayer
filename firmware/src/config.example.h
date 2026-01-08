/**
 * ToniePlayer Configuration
 *
 * Copy this file to config.h and fill in your values.
 * config.h is gitignored and will not be committed.
 *
 * If WiFi credentials are left empty, the device will start
 * in AP mode for configuration via web interface.
 */

#ifndef CONFIG_H
#define CONFIG_H

// =============================================================================
// NETWORK CONFIGURATION
// =============================================================================

// WiFi credentials - leave empty to start in AP mode for web configuration
#define WIFI_SSID ""
#define WIFI_PASSWORD ""

// ToniePlayer server URL (your server's address)
// Example: "http://192.168.1.100:8754" or "http://tonieplayer.local:8754"
#define SERVER_URL "http://YOUR_SERVER_IP:8754"

// Hostname for this device (used for OTA updates and mDNS)
// Make unique if you have multiple readers, e.g., "tonieplayer-living-room"
#define DEVICE_HOSTNAME "tonieplayer"

// =============================================================================
// HARDWARE CONFIGURATION
// =============================================================================

// Uncomment the hardware features your build has:
// #define HAS_DISPLAY          // Enable if you have an OLED/TFT display
// #define HAS_BUTTONS          // Enable if you have physical buttons

// Display type (only if HAS_DISPLAY is defined)
// Options: DISPLAY_SSD1306, DISPLAY_ST7789, DISPLAY_TDISPLAY_S3
// #define DISPLAY_TYPE DISPLAY_SSD1306

// =============================================================================
// PIN DEFINITIONS - PN5180 NFC Reader
// =============================================================================

// Default pinout for ESP32 DevKit + PN5180
// Adjust if your wiring is different
#define PN5180_NSS   5    // SPI Chip Select
#define PN5180_BUSY  2    // Busy signal
#define PN5180_RST   4    // Reset

// =============================================================================
// PIN DEFINITIONS - Status LED (NeoPixel/WS2812)
// =============================================================================

#define NEOPIXEL_PIN    13
#define NEOPIXEL_COUNT  1
#define NEOPIXEL_BRIGHTNESS 50  // 0-255

// =============================================================================
// PIN DEFINITIONS - Buttons (only if HAS_BUTTONS defined)
// =============================================================================

#ifdef HAS_BUTTONS
#define BUTTON_PREV_PIN  0   // Previous speaker / volume down
#define BUTTON_NEXT_PIN  35  // Next speaker / volume up
#endif

// =============================================================================
// PIN DEFINITIONS - Display (only if HAS_DISPLAY defined)
// =============================================================================

#ifdef HAS_DISPLAY
  #if DISPLAY_TYPE == DISPLAY_SSD1306
    // I2C OLED (SSD1306)
    #define DISPLAY_SDA  21
    #define DISPLAY_SCL  22
    #define DISPLAY_WIDTH  128
    #define DISPLAY_HEIGHT 64
  #elif DISPLAY_TYPE == DISPLAY_TDISPLAY_S3
    // LilyGO T-Display-S3 (built-in display)
    // Pins are fixed on this board
  #endif
#endif

// =============================================================================
// ADVANCED SETTINGS (usually no need to change)
// =============================================================================

// Network timeouts
#define HTTP_TIMEOUT_MS      5000   // HTTP request timeout
#define WIFI_CONNECT_TIMEOUT 10000  // WiFi connection timeout

// Timing intervals
#define WIFI_CHECK_INTERVAL_MS   5000   // Check WiFi connection every N ms
#define NFC_RESET_INTERVAL_MS    30000  // Reset NFC reader every N ms
#define HEARTBEAT_INTERVAL_MS    30000  // Send heartbeat to server every N ms

// NFC detection tuning
#define TAG_DEBOUNCE_MS       350   // Must see same tag for this long
#define TAG_REMOVAL_MS        400   // Must NOT see tag for this long
#define TAG_COOLDOWN_MS       1500  // Wait after removal before accepting same tag
#define MIN_CONSISTENT_READS  3     // Must read same tag this many times

// TCP logging (for debugging via netcat)
#define LOG_PORT     9876
#define LOG_ENABLED  true

// AP Mode configuration (when WiFi credentials are empty or connection fails)
#define AP_SSID_PREFIX    "ToniePlayer-"  // Will append last 4 chars of MAC
#define AP_PASSWORD       ""              // Empty = open network
#define AP_TIMEOUT_MS     300000          // Return to AP mode after 5 min without connection

// WiFi retry before falling back to AP mode
#define WIFI_MAX_RETRIES  3

#endif // CONFIG_H
