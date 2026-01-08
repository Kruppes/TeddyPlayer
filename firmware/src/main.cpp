#include <Arduino.h>
#include <WiFi.h>
// OTA removed to save flash space (was using 97.5%)
#include <HTTPClient.h>
#include <PN5180.h>
#include <PN5180ISO15693.h>
#include <Adafruit_NeoPixel.h>
#include <ESPAsyncWebServer.h>
#include <LittleFS.h>

// Configuration - copy config.example.h to config.h and fill in your values
#include "config.h"

// Firmware version
#define FIRMWARE_VERSION "2.1.5"

// Heap monitoring
#define MIN_FREE_HEAP 20000  // Reboot if heap drops below 20KB
#define HEAP_CHECK_INTERVAL_MS 10000
#define HEAP_LOG_INTERVAL_MS 60000  // Log heap every 60 seconds for debugging
unsigned long lastHeapCheck = 0;
unsigned long lastHeapLog = 0;
uint32_t minFreeHeapEver = 0xFFFFFFFF;  // Track lowest heap for diagnostics

// ============== Settings Management ==============
struct Settings {
    char serverUrl[128];
    char deviceName[32];
    char playbackDevice[64];
    uint8_t ledBrightness;
    char wifiSsid[64];
    char wifiPassword[64];
};

Settings settings;
bool apMode = false;

void defaultSettings() {
    strlcpy(settings.serverUrl, SERVER_URL, sizeof(settings.serverUrl));
    strlcpy(settings.deviceName, DEVICE_HOSTNAME, sizeof(settings.deviceName));
    settings.playbackDevice[0] = '\0';  // Empty = use server default
    settings.ledBrightness = 50;
    strlcpy(settings.wifiSsid, WIFI_SSID, sizeof(settings.wifiSsid));
    strlcpy(settings.wifiPassword, WIFI_PASSWORD, sizeof(settings.wifiPassword));
}

bool loadSettings() {
    if (!LittleFS.exists("/settings.bin")) {
        Serial.println("No settings file, using defaults");
        defaultSettings();
        return false;
    }

    File f = LittleFS.open("/settings.bin", "r");
    if (!f) {
        Serial.println("Failed to open settings");
        defaultSettings();
        return false;
    }

    size_t read = f.read((uint8_t*)&settings, sizeof(Settings));
    f.close();

    if (read != sizeof(Settings)) {
        Serial.println("Settings file corrupted");
        defaultSettings();
        return false;
    }

    Serial.println("Settings loaded");
    return true;
}

bool saveSettings() {
    File f = LittleFS.open("/settings.bin", "w");
    if (!f) {
        Serial.println("Failed to save settings");
        return false;
    }

    f.write((uint8_t*)&settings, sizeof(Settings));
    f.close();
    Serial.println("Settings saved");
    return true;
}

void factoryReset() {
    LittleFS.remove("/settings.bin");
    defaultSettings();
    Serial.println("Factory reset complete");
}

// ============== Hardware ==============
PN5180ISO15693 nfc(PN5180_NSS, PN5180_BUSY, PN5180_RST);
Adafruit_NeoPixel pixel(NEOPIXEL_COUNT, NEOPIXEL_PIN, NEO_GRB + NEO_KHZ800);
AsyncWebServer webServer(80);

// Server URL functions removed - using snprintf with static buffers instead

// Timing constants - use config.h values if defined, otherwise defaults
#ifndef TAG_DEBOUNCE_MS
#define TAG_DEBOUNCE_MS 350
#endif
#ifndef TAG_REMOVAL_MS
#define TAG_REMOVAL_MS 400
#endif
#ifndef TAG_COOLDOWN_MS
#define TAG_COOLDOWN_MS 1500
#endif
#ifndef MIN_EMPTY_FOR_REMOVAL
#define MIN_EMPTY_FOR_REMOVAL 5
#endif
#ifndef WIFI_CHECK_INTERVAL_MS
#define WIFI_CHECK_INTERVAL_MS 5000
#endif
#ifndef NFC_RESET_INTERVAL_MS
#define NFC_RESET_INTERVAL_MS 30000
#endif
#ifndef HTTP_TIMEOUT_MS
#define HTTP_TIMEOUT_MS 5000
#endif
#ifndef MIN_CONSISTENT_READS
#define MIN_CONSISTENT_READS 3
#endif
#ifndef MAX_EMPTY_READS_RESET
#define MAX_EMPTY_READS_RESET 3
#endif
#ifndef RF_CYCLE_OFF_MS
#define RF_CYCLE_OFF_MS 50
#endif
#ifndef RF_CYCLE_ON_MS
#define RF_CYCLE_ON_MS 10
#endif
#ifndef LOG_INTERVAL_MS
#define LOG_INTERVAL_MS 5000
#endif
#ifndef PRESENCE_VALIDATE_INTERVAL_MS
#define PRESENCE_VALIDATE_INTERVAL_MS 800
#endif
#ifndef PRESENCE_VALIDATE_OFF_MS
#define PRESENCE_VALIDATE_OFF_MS 80
#endif
#ifndef PRESENCE_VALIDATE_ON_MS
#define PRESENCE_VALIDATE_ON_MS 20
#endif
#ifndef PRESENCE_VALIDATE_FAILS
#define PRESENCE_VALIDATE_FAILS 2
#endif
#ifndef HEARTBEAT_INTERVAL_MS
#define HEARTBEAT_INTERVAL_MS 30000
#endif

// State tracking - using fixed char arrays to avoid heap fragmentation
#define UID_BUFFER_SIZE 24
char confirmedTag[UID_BUFFER_SIZE] = "";
char pendingTag[UID_BUFFER_SIZE] = "";
char lastRemovedTag[UID_BUFFER_SIZE] = "";
char currentUidBuf[UID_BUFFER_SIZE] = "";  // Reusable buffer for NFC reads
unsigned long pendingTagSince = 0;
unsigned long lastTagSeen = 0;
unsigned long lastTagRemoved = 0;
unsigned long lastWifiCheck = 0;
unsigned long lastNfcReset = 0;
int consecutiveHttpErrors = 0;
int pendingTagReadCount = 0;
int emptyReadCount = 0;
int consecutiveEmptyForRemoval = 0;
unsigned long lastLog = 0;
unsigned long lastPresenceValidate = 0;
int presenceValidateFailures = 0;
unsigned long lastHeartbeat = 0;
bool tagFound = false;
bool tagEncoding = false;
unsigned long encodingStartTime = 0;
bool webServerStarted = false;

// LED State
enum LedState { LED_CONNECTING, LED_IDLE, LED_DETECTING, LED_ENCODING, LED_PLAYING, LED_NOT_FOUND, LED_ERROR, LED_AP_MODE };
LedState currentLedState = LED_IDLE;

void setPixelColor(uint8_t r, uint8_t g, uint8_t b) {
    pixel.setPixelColor(0, pixel.Color(r, g, b));
    pixel.show();
}

void ledConnecting()   { setPixelColor(255, 165, 0); currentLedState = LED_CONNECTING; }
void ledIdle()         { setPixelColor(0, 0, 255); currentLedState = LED_IDLE; }
void ledDetecting()    { setPixelColor(128, 0, 255); currentLedState = LED_DETECTING; }
void ledEncoding()     { currentLedState = LED_ENCODING; encodingStartTime = millis(); }
void ledPlaying()      { setPixelColor(0, 255, 0); currentLedState = LED_PLAYING; tagEncoding = false; }
void ledNotFound()     { setPixelColor(255, 180, 0); currentLedState = LED_NOT_FOUND; }
void ledError()        { setPixelColor(255, 0, 0); currentLedState = LED_ERROR; }
void ledApMode()       { setPixelColor(255, 0, 255); currentLedState = LED_AP_MODE; } // Magenta for AP mode

void updatePulsingLed() {
    if (currentLedState == LED_ENCODING) {
        unsigned long elapsed = millis() - encodingStartTime;
        float phase = (elapsed % 1000) / 1000.0;
        float brightness = 0.3 + 0.7 * (0.5 + 0.5 * sin(phase * 2 * 3.14159));
        setPixelColor(0, (uint8_t)(255 * brightness), 0);
    } else if (currentLedState == LED_AP_MODE) {
        // Slow pulse magenta in AP mode
        unsigned long elapsed = millis();
        float phase = (elapsed % 2000) / 2000.0;
        float brightness = 0.3 + 0.7 * (0.5 + 0.5 * sin(phase * 2 * 3.14159));
        setPixelColor((uint8_t)(255 * brightness), 0, (uint8_t)(255 * brightness));
    }
}

// ============== NFC Functions ==============
// Read tag UID into provided buffer (avoids heap allocation)
// Returns true if tag was read, false otherwise
bool readTagUidOnce(char* uidBuf, size_t bufSize);

void resetNfc() {
    Serial.println("NFC reset");
    nfc.reset();
    delay(100);
    nfc.setupRF();
}

void cycleRfField() {
    nfc.setRF_off();
    delay(RF_CYCLE_OFF_MS);
    nfc.setRF_on();
    delay(RF_CYCLE_ON_MS);
}

// Validate tag presence using RF cycle, writes to provided buffer
bool validatePresence(char* uidBuf, size_t bufSize) {
    nfc.setRF_off();
    delay(PRESENCE_VALIDATE_OFF_MS);
    nfc.setRF_on();
    delay(PRESENCE_VALIDATE_ON_MS);
    return readTagUidOnce(uidBuf, bufSize);
}

bool readTagUidOnce(char* uidBuf, size_t bufSize) {
    uidBuf[0] = '\0';  // Clear buffer on entry

    uint8_t uid[8];
    ISO15693ErrorCode rc = nfc.getInventory(uid);
    if (rc != ISO15693_EC_OK) return false;

    bool allZeros = true;
    for (int i = 0; i < 8; i++) if (uid[i] != 0) { allZeros = false; break; }
    if (allZeros) return false;

    if (uid[7] != 0xE0 || uid[6] != 0x04) return false;

    snprintf(uidBuf, bufSize, "%02X:%02X:%02X:%02X:%02X:%02X:%02X:%02X",
        uid[7], uid[6], uid[5], uid[4], uid[3], uid[2], uid[1], uid[0]);
    return true;
}

// ============== Network Functions ==============
// Static buffers to avoid heap fragmentation
static char httpUrlBuf[256];
static char httpPayloadBuf[256];
static char webBodyBuf[512];  // Shared buffer for web POST handlers

void sendHeartbeat() {
    if (WiFi.status() != WL_CONNECTED || apMode) return;

    HTTPClient http;
    http.setTimeout(HTTP_TIMEOUT_MS);
    http.setReuse(false);

    // Build IP string without using String class
    IPAddress ip = WiFi.localIP();
    char ipBuf[20];
    snprintf(ipBuf, sizeof(ipBuf), "%d.%d.%d.%d", ip[0], ip[1], ip[2], ip[3]);

    // Build URL in static buffer
    snprintf(httpUrlBuf, sizeof(httpUrlBuf), "%s/readers/%s/heartbeat",
             settings.serverUrl, ipBuf);

    if (http.begin(httpUrlBuf)) {
        http.addHeader("Content-Type", "application/json");
        snprintf(httpPayloadBuf, sizeof(httpPayloadBuf), "{\"name\":\"%s\"}", settings.deviceName);
        int code = http.POST(httpPayloadBuf);
        if (code > 0) Serial.println("Heartbeat OK");
        http.end();
    }
    lastHeartbeat = millis();
}

bool sendToServer(const char* uid) {
    if (WiFi.status() != WL_CONNECTED || apMode) return false;

    HTTPClient http;
    http.setTimeout(HTTP_TIMEOUT_MS);
    http.setReuse(false);

    // Build URL in static buffer
    snprintf(httpUrlBuf, sizeof(httpUrlBuf), "%s/tonie", settings.serverUrl);

    if (!http.begin(httpUrlBuf)) {
        consecutiveHttpErrors++;
        return false;
    }

    http.addHeader("Content-Type", "application/json");

    if (uid == nullptr) {
        snprintf(httpPayloadBuf, sizeof(httpPayloadBuf), "{\"uid\":null}");
    } else if (strlen(settings.playbackDevice) > 0) {
        char devType[32] = "";
        char devId[64] = "";
        const char* sep = strchr(settings.playbackDevice, '|');
        if (sep) {
            size_t typeLen = sep - settings.playbackDevice;
            if (typeLen < sizeof(devType)) {
                memcpy(devType, settings.playbackDevice, typeLen);
                devType[typeLen] = '\0';
            }
            strlcpy(devId, sep + 1, sizeof(devId));
        }
        snprintf(httpPayloadBuf, sizeof(httpPayloadBuf),
            "{\"uid\":\"%s\",\"mode\":\"stream\",\"target_device\":{\"type\":\"%s\",\"id\":\"%s\"}}",
            uid, devType, devId);
    } else {
        snprintf(httpPayloadBuf, sizeof(httpPayloadBuf), "{\"uid\":\"%s\",\"mode\":\"stream\"}", uid);
    }

    Serial.print("TX: "); Serial.println(httpPayloadBuf);

    int httpCode = http.POST(httpPayloadBuf);
    bool found = false;
    tagEncoding = false;

    if (httpCode > 0) {
        consecutiveHttpErrors = 0;
        if (httpCode == HTTP_CODE_OK) {
            // Read response into static buffer
            WiFiClient* stream = http.getStreamPtr();
            int len = http.getSize();
            if (len > 0 && len < (int)sizeof(httpPayloadBuf)) {
                stream->readBytes(httpPayloadBuf, len);
                httpPayloadBuf[len] = '\0';
                Serial.print("RX: "); Serial.println(httpPayloadBuf);
                found = strstr(httpPayloadBuf, "\"found\":true") != nullptr || strstr(httpPayloadBuf, "\"found\": true") != nullptr;
                tagEncoding = strstr(httpPayloadBuf, "\"encoding\":true") != nullptr || strstr(httpPayloadBuf, "\"encoding\": true") != nullptr;
            }
        }
    } else {
        consecutiveHttpErrors++;
    }

    http.end();
    return found;
}

// ============== Web Interface ==============
// Minimal pages to reduce memory usage
const char MAIN_PAGE[] PROGMEM = R"html(<!DOCTYPE html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>ToniePlayer</title>
<style>*{box-sizing:border-box}body{font-family:sans-serif;margin:20px;background:#1a1a2e;color:#eee}h1{color:#0da}
.c{background:#16213e;padding:15px;border-radius:8px;margin:10px 0}label{display:block;color:#aaa;margin:5px 0}
input,select{width:100%;padding:8px;border:1px solid #333;border-radius:4px;background:#0f0f23;color:#eee;margin-bottom:8px}
button{background:#0da;color:#000;padding:10px;border:none;border-radius:4px;cursor:pointer;width:100%;margin:5px 0}
.d{background:#e74c3c}.s{background:#333;color:#eee}</style></head><body>
<h1>ToniePlayer</h1><div id="st" class="c">Loading...</div>
<div class="c"><h3>Settings</h3><form id="f">
<label>Server URL</label><input id="su" name="serverUrl">
<label>Device Name</label><input id="dn" name="deviceName">
<label>Playback Device <button type="button" onclick="loadDevices()" style="width:auto;padding:4px 8px;margin-left:10px">↻ Refresh</button></label><select id="pd" name="playbackDevice"><option value="">Server default</option></select>
<label>LED Brightness: <span id="bv">50</span>%</label><input type="range" id="br" name="ledBrightness" min="10" max="100">
<button type="submit">Save</button></form></div>
<div class="c"><button class="s" onclick="fetch('/reboot',{method:'POST'})">Reboot</button>
<button class="d" onclick="if(confirm('Reset?'))fetch('/reset',{method:'POST'})">Factory Reset</button></div>
<script>
var srvUrl='',curDev='';
function status(){fetch('/status').then(r=>r.json()).then(d=>{
document.getElementById('st').innerHTML='<b>'+d.deviceName+'</b> ('+d.ip+')<br>Tag: '+(d.tag||'None')+'<br>RSSI: '+d.wifiRssi+'dBm | Heap: '+Math.round(d.freeHeap/1024)+'K (min:'+Math.round(d.minHeap/1024)+'K)<br>v'+d.version+' | Up: '+d.uptime+'s';});}
function init(){fetch('/settings').then(r=>r.json()).then(d=>{
document.getElementById('su').value=srvUrl=d.serverUrl;document.getElementById('dn').value=d.deviceName;
curDev=d.playbackDevice;document.getElementById('br').value=d.ledBrightness;document.getElementById('bv').textContent=d.ledBrightness;
loadDevices();});}
function loadDevices(){var sel=document.getElementById('pd');
if(!srvUrl){sel.innerHTML='<option value="">Enter server URL first</option>';return;}
sel.innerHTML='<option value="">Loading...</option>';
fetch(srvUrl+'/preferences').then(function(r){return r.json();}).then(function(prefs){
var starred=prefs.starredDevices||[];
fetch(srvUrl+'/devices').then(function(r){return r.json();}).then(function(data){
sel.innerHTML='<option value="">Server default</option>';
var types={sonos:'Sonos',airplay:'AirPlay',chromecast:'Chromecast'};var n=0;
for(var t in types){if(data[t]){data[t].forEach(function(d){
var k1=t+'|'+(d.uid||d.id),k2=d.ip?t+'|'+d.ip:'';
var ok=starred.indexOf(k1)>=0||(k2&&starred.indexOf(k2)>=0);if(!ok)return;n++;
var o=document.createElement('option');o.value=k1;o.textContent=d.name+' ('+types[t]+')';
if(k1===curDev||k2===curDev)o.selected=true;sel.appendChild(o);});}}
if(n===0)sel.innerHTML='<option value="">No starred devices</option>';
});}).catch(function(e){sel.innerHTML='<option value="">Error</option>';});}
document.getElementById('br').oninput=function(){document.getElementById('bv').textContent=this.value;};
document.getElementById('f').onsubmit=function(e){e.preventDefault();
var d={serverUrl:document.getElementById('su').value,deviceName:document.getElementById('dn').value,
playbackDevice:document.getElementById('pd').value,ledBrightness:parseInt(document.getElementById('br').value)};
fetch('/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(d)}).then(()=>{srvUrl=d.serverUrl;init();});return false;};
init();status();setInterval(status,5000);</script></body></html>)html";

const char AP_PAGE[] PROGMEM = R"html(<!DOCTYPE html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>ToniePlayer Setup</title>
<style>body{font-family:sans-serif;margin:20px;background:#1a1a2e;color:#eee;max-width:400px}h1{color:#f0f;text-align:center}
.c{background:#16213e;padding:20px;border-radius:8px}label{display:block;color:#aaa;margin:5px 0}
input{width:100%;padding:10px;border:1px solid #333;border-radius:4px;background:#0f0f23;color:#eee;margin-bottom:10px}
button{background:#f0f;color:#fff;padding:12px;border:none;border-radius:4px;width:100%}</style></head><body>
<h1>ToniePlayer Setup</h1><div class="c"><form id="f">
<label>WiFi SSID</label><input id="ss" required>
<label>WiFi Password</label><input type="password" id="pw">
<label>Server URL</label><input id="su" required value="http://192.168.1.100:8754">
<label>Device Name</label><input id="dn" value="tonieplayer">
<button type="submit">Save & Connect</button></form></div>
<script>document.getElementById('f').onsubmit=function(e){e.preventDefault();
fetch('/save-wifi',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
ssid:document.getElementById('ss').value,password:document.getElementById('pw').value,
serverUrl:document.getElementById('su').value,deviceName:document.getElementById('dn').value})});return false;};</script></body></html>)html";

void setupWebServer() {
    // Main page - serve directly from PROGMEM
    webServer.on("/", HTTP_GET, [](AsyncWebServerRequest *request) {
        if (apMode) {
            request->send(200, "text/html", AP_PAGE);
        } else {
            request->send(200, "text/html", MAIN_PAGE);
        }
    });

    // Status endpoint - use static buffer to avoid heap fragmentation
    static char statusJson[512];
    static char statusIp[20];
    webServer.on("/status", HTTP_GET, [](AsyncWebServerRequest *request) {
        // Copy IP to static buffer (avoids WiFi.localIP().toString() String allocation)
        if (apMode) {
            strcpy(statusIp, "192.168.4.1");
        } else {
            IPAddress ip = WiFi.localIP();
            snprintf(statusIp, sizeof(statusIp), "%d.%d.%d.%d", ip[0], ip[1], ip[2], ip[3]);
        }

        snprintf(statusJson, sizeof(statusJson),
            "{\"deviceName\":\"%s\",\"ip\":\"%s\",\"tag\":\"%s\",\"tagFound\":%s,\"uptime\":%lu,\"wifiRssi\":%d,\"version\":\"%s\",\"apMode\":%s,\"freeHeap\":%u,\"minHeap\":%u}",
            settings.deviceName,
            statusIp,
            confirmedTag,  // Now a char array, no .c_str() needed
            tagFound ? "true" : "false",
            millis() / 1000,
            apMode ? 0 : WiFi.RSSI(),
            FIRMWARE_VERSION,
            apMode ? "true" : "false",
            ESP.getFreeHeap(),
            minFreeHeapEver
        );
        request->send(200, "application/json", statusJson);
    });

    // Settings endpoint
    webServer.on("/settings", HTTP_GET, [](AsyncWebServerRequest *request) {
        char json[384];
        snprintf(json, sizeof(json),
            "{\"serverUrl\":\"%s\",\"deviceName\":\"%s\",\"playbackDevice\":\"%s\",\"ledBrightness\":%d}",
            settings.serverUrl,
            settings.deviceName,
            settings.playbackDevice,
            settings.ledBrightness
        );
        request->send(200, "application/json", json);
    });

    // Save settings (normal mode) - using global static buffer to avoid heap fragmentation
    webServer.on("/save", HTTP_POST, [](AsyncWebServerRequest *request) {}, NULL,
        [](AsyncWebServerRequest *request, uint8_t *data, size_t len, size_t index, size_t total) {
            // Copy to global static buffer
            size_t copyLen = (len < sizeof(webBodyBuf) - 1) ? len : sizeof(webBodyBuf) - 1;
            memcpy(webBodyBuf, data, copyLen);
            webBodyBuf[copyLen] = '\0';

            // Simple JSON parsing using C string functions
            char* ptr;
            if ((ptr = strstr(webBodyBuf, "\"serverUrl\":\"")) != NULL) {
                ptr += 13;
                char* end = strchr(ptr, '"');
                if (end && end > ptr) {
                    size_t fieldLen = (end - ptr < (int)sizeof(settings.serverUrl) - 1) ? end - ptr : sizeof(settings.serverUrl) - 1;
                    memcpy(settings.serverUrl, ptr, fieldLen);
                    settings.serverUrl[fieldLen] = '\0';
                }
            }
            if ((ptr = strstr(webBodyBuf, "\"deviceName\":\"")) != NULL) {
                ptr += 14;
                char* end = strchr(ptr, '"');
                if (end && end > ptr) {
                    size_t fieldLen = (end - ptr < (int)sizeof(settings.deviceName) - 1) ? end - ptr : sizeof(settings.deviceName) - 1;
                    memcpy(settings.deviceName, ptr, fieldLen);
                    settings.deviceName[fieldLen] = '\0';
                }
            }
            if ((ptr = strstr(webBodyBuf, "\"playbackDevice\":\"")) != NULL) {
                ptr += 18;
                char* end = strchr(ptr, '"');
                if (end && end > ptr) {
                    size_t fieldLen = (end - ptr < (int)sizeof(settings.playbackDevice) - 1) ? end - ptr : sizeof(settings.playbackDevice) - 1;
                    memcpy(settings.playbackDevice, ptr, fieldLen);
                    settings.playbackDevice[fieldLen] = '\0';
                }
            }
            if ((ptr = strstr(webBodyBuf, "\"ledBrightness\":")) != NULL) {
                ptr += 16;
                settings.ledBrightness = atoi(ptr);
                pixel.setBrightness(settings.ledBrightness);
                pixel.show();
            }

            saveSettings();
            request->send(200, "application/json", "{\"success\":true,\"message\":\"Settings saved\"}");
        }
    );

    // Save WiFi (AP mode) - reuse the same global buffer
    webServer.on("/save-wifi", HTTP_POST, [](AsyncWebServerRequest *request) {}, NULL,
        [](AsyncWebServerRequest *request, uint8_t *data, size_t len, size_t index, size_t total) {
            // Copy to global static buffer
            size_t copyLen = (len < sizeof(webBodyBuf) - 1) ? len : sizeof(webBodyBuf) - 1;
            memcpy(webBodyBuf, data, copyLen);
            webBodyBuf[copyLen] = '\0';

            char* ptr;
            if ((ptr = strstr(webBodyBuf, "\"ssid\":\"")) != NULL) {
                ptr += 8;
                char* end = strchr(ptr, '"');
                if (end && end > ptr) {
                    size_t fieldLen = (end - ptr < (int)sizeof(settings.wifiSsid) - 1) ? end - ptr : sizeof(settings.wifiSsid) - 1;
                    memcpy(settings.wifiSsid, ptr, fieldLen);
                    settings.wifiSsid[fieldLen] = '\0';
                }
            }
            if ((ptr = strstr(webBodyBuf, "\"password\":\"")) != NULL) {
                ptr += 12;
                char* end = strchr(ptr, '"');
                if (end && end > ptr) {
                    size_t fieldLen = (end - ptr < (int)sizeof(settings.wifiPassword) - 1) ? end - ptr : sizeof(settings.wifiPassword) - 1;
                    memcpy(settings.wifiPassword, ptr, fieldLen);
                    settings.wifiPassword[fieldLen] = '\0';
                }
            }
            if ((ptr = strstr(webBodyBuf, "\"serverUrl\":\"")) != NULL) {
                ptr += 13;
                char* end = strchr(ptr, '"');
                if (end && end > ptr) {
                    size_t fieldLen = (end - ptr < (int)sizeof(settings.serverUrl) - 1) ? end - ptr : sizeof(settings.serverUrl) - 1;
                    memcpy(settings.serverUrl, ptr, fieldLen);
                    settings.serverUrl[fieldLen] = '\0';
                }
            }
            if ((ptr = strstr(webBodyBuf, "\"deviceName\":\"")) != NULL) {
                ptr += 14;
                char* end = strchr(ptr, '"');
                if (end && end > ptr) {
                    size_t fieldLen = (end - ptr < (int)sizeof(settings.deviceName) - 1) ? end - ptr : sizeof(settings.deviceName) - 1;
                    memcpy(settings.deviceName, ptr, fieldLen);
                    settings.deviceName[fieldLen] = '\0';
                }
            }

            saveSettings();
            request->send(200, "application/json", "{\"success\":true,\"message\":\"Settings saved! Rebooting...\"}");
            delay(1000);
            ESP.restart();
        }
    );

    // Reboot
    webServer.on("/reboot", HTTP_POST, [](AsyncWebServerRequest *request) {
        request->send(200, "application/json", "{\"success\":true}");
        delay(500);
        ESP.restart();
    });

    // Factory reset
    webServer.on("/reset", HTTP_POST, [](AsyncWebServerRequest *request) {
        factoryReset();
        request->send(200, "application/json", "{\"success\":true}");
        delay(500);
        ESP.restart();
    });

    webServer.begin();
    webServerStarted = true;
    Serial.println("Web server started");
}

// ============== WiFi Connection ==============
bool connectWiFi() {
    if (strlen(settings.wifiSsid) == 0) {
        Serial.println("No WiFi configured");
        return false;
    }

    Serial.print("WiFi: "); Serial.println(settings.wifiSsid);
    ledConnecting();

    // Initialize WiFi mode first, then disconnect any stale connection
    WiFi.mode(WIFI_STA);
    WiFi.disconnect(false);  // Don't erase config, just disconnect
    delay(100);
    WiFi.setAutoReconnect(true);
    WiFi.begin(settings.wifiSsid, settings.wifiPassword);

    int attempts = 0;
    while (WiFi.status() != WL_CONNECTED && attempts < 40) {
        delay(250);
        attempts++;
        setPixelColor(attempts % 2 ? 255 : 128, attempts % 2 ? 165 : 82, 0);
    }

    if (WiFi.status() == WL_CONNECTED) {
        Serial.print("IP: "); Serial.println(WiFi.localIP());
        return true;
    }

    Serial.println("WiFi FAILED");
    return false;
}

void startApMode() {
    Serial.println("Starting AP mode...");
    apMode = true;

    WiFi.mode(WIFI_AP);
    // Build AP name without String class
    char apName[32];
    snprintf(apName, sizeof(apName), "ToniePlayer-%08X", (uint32_t)ESP.getEfuseMac());
    WiFi.softAP(apName);

    Serial.print("AP: "); Serial.println(apName);
    Serial.print("IP: "); Serial.println(WiFi.softAPIP());

    ledApMode();
    setupWebServer();
}

// ============== Setup & Loop ==============
void logHeap(const char* label) {
    Serial.printf("[HEAP] %s: %u free, largest block: %u\n",
                  label, ESP.getFreeHeap(), ESP.getMaxAllocHeap());
}

void setup() {
    Serial.begin(115200);
    delay(1000);
    Serial.println("\n=== ToniePlayer v" FIRMWARE_VERSION " ===");
    logHeap("Boot");

    // Initialize LittleFS
    if (!LittleFS.begin(true)) {
        Serial.println("LittleFS FAILED");
    }
    logHeap("After LittleFS");

    // Load settings
    loadSettings();

    // Initialize NeoPixel with saved brightness
    pixel.begin();
    pixel.setBrightness(settings.ledBrightness);
    ledConnecting();
    logHeap("After NeoPixel");

    // Initialize NFC
    nfc.begin();
    resetNfc();
    Serial.println("NFC OK");
    logHeap("After NFC");

    // Try to connect to WiFi
    if (connectWiFi()) {
        apMode = false;
        logHeap("After WiFi connect");
        ledIdle();

        setupWebServer();
        logHeap("After WebServer setup");
        sendHeartbeat();

        Serial.println("Ready");
    } else {
        // Start AP mode for configuration
        startApMode();
    }

    logHeap("Setup complete");
    minFreeHeapEver = ESP.getFreeHeap();  // Initialize after setup
    lastNfcReset = millis();
}

void loop() {
    unsigned long now = millis();

    // Update pulsing LEDs
    updatePulsingLed();

    // Heap watchdog - reboot if memory is critically low
    if (now - lastHeapCheck > HEAP_CHECK_INTERVAL_MS) {
        lastHeapCheck = now;
        uint32_t freeHeap = ESP.getFreeHeap();

        // Track minimum heap ever seen
        if (freeHeap < minFreeHeapEver) {
            minFreeHeapEver = freeHeap;
        }

        // Periodic heap logging for debugging
        if (now - lastHeapLog > HEAP_LOG_INTERVAL_MS) {
            lastHeapLog = now;
            Serial.printf("HEAP: %u free, %u min ever, uptime %lu sec\n",
                          freeHeap, minFreeHeapEver, now / 1000);
        }

        if (freeHeap < MIN_FREE_HEAP) {
            Serial.printf("HEAP CRITICAL: %u bytes (min ever: %u) - rebooting!\n", freeHeap, minFreeHeapEver);
            delay(100);
            ESP.restart();
        }
    }

    // In AP mode, just handle web requests
    if (apMode) {
        delay(50);
        return;
    }

    // Periodic WiFi check
    if (now - lastWifiCheck > WIFI_CHECK_INTERVAL_MS) {
        lastWifiCheck = now;
        if (WiFi.status() != WL_CONNECTED) {
            Serial.println("WiFi reconnect...");
            if (connectWiFi()) {
                if (confirmedTag[0] != '\0') ledPlaying();
                else ledIdle();
            } else {
                ledError();
            }
        }
        if (consecutiveHttpErrors > 3) {
            connectWiFi();
            consecutiveHttpErrors = 0;
        }
    }

    // Heartbeat
    if (now - lastHeartbeat > HEARTBEAT_INTERVAL_MS && WiFi.status() == WL_CONNECTED) {
        sendHeartbeat();
    }

    // NFC reset
    if (now - lastNfcReset > NFC_RESET_INTERVAL_MS) {
        lastNfcReset = now;
        resetNfc();
    }

    // Read NFC into reusable buffer (no heap allocation)
    bool tagRead = readTagUidOnce(currentUidBuf, sizeof(currentUidBuf));

    if (tagRead) {
        consecutiveEmptyForRemoval = 0;
        bool isGhostRead = (strcmp(currentUidBuf, lastRemovedTag) == 0 && (now - lastTagRemoved) < TAG_COOLDOWN_MS);

        if (isGhostRead) {
            // Ignore
        } else if (strcmp(currentUidBuf, confirmedTag) == 0) {
            lastTagSeen = now;
            emptyReadCount = 0;
            pendingTag[0] = '\0';
            pendingTagReadCount = 0;
            presenceValidateFailures = 0;
        } else if (strcmp(currentUidBuf, pendingTag) == 0) {
            lastTagSeen = now;
            emptyReadCount = 0;
            pendingTagReadCount++;

            if (pendingTagReadCount >= MIN_CONSISTENT_READS && now - pendingTagSince >= TAG_DEBOUNCE_MS) {
                Serial.print("TAG ON: "); Serial.println(currentUidBuf);
                strlcpy(confirmedTag, currentUidBuf, sizeof(confirmedTag));
                pendingTag[0] = '\0';
                pendingTagReadCount = 0;
                lastRemovedTag[0] = '\0';
                presenceValidateFailures = 0;

                tagFound = sendToServer(confirmedTag);
                if (tagFound) {
                    if (tagEncoding) ledEncoding();
                    else ledPlaying();
                } else {
                    ledNotFound();
                }
            }
        } else if (confirmedTag[0] == '\0') {
            lastTagSeen = now;
            emptyReadCount = 0;
            strlcpy(pendingTag, currentUidBuf, sizeof(pendingTag));
            pendingTagSince = now;
            pendingTagReadCount = 1;
            ledDetecting();
        }
    } else {
        emptyReadCount++;
        consecutiveEmptyForRemoval++;

        if (pendingTag[0] != '\0') {
            pendingTag[0] = '\0';
            pendingTagReadCount = 0;
            if (confirmedTag[0] == '\0') ledIdle();
        }

        if (emptyReadCount >= MAX_EMPTY_READS_RESET && confirmedTag[0] != '\0') {
            resetNfc();
            emptyReadCount = 0;
            lastNfcReset = now;
        }

        if (confirmedTag[0] != '\0' && now - lastTagSeen >= TAG_REMOVAL_MS && consecutiveEmptyForRemoval >= MIN_EMPTY_FOR_REMOVAL) {
            Serial.println("TAG OFF");
            strlcpy(lastRemovedTag, confirmedTag, sizeof(lastRemovedTag));
            sendToServer(nullptr);
            confirmedTag[0] = '\0';
            tagFound = false;
            lastTagRemoved = now;
            ledIdle();
            cycleRfField();
            presenceValidateFailures = 0;
        }

        if (lastRemovedTag[0] != '\0' && (now - lastTagRemoved) >= TAG_COOLDOWN_MS) {
            lastRemovedTag[0] = '\0';
        }
    }

    // Presence validation - use a local buffer to avoid reusing currentUidBuf
    static char verifyUidBuf[UID_BUFFER_SIZE];
    if (confirmedTag[0] != '\0' && now - lastPresenceValidate >= PRESENCE_VALIDATE_INTERVAL_MS) {
        bool verified = validatePresence(verifyUidBuf, sizeof(verifyUidBuf));
        if (!verified || strcmp(verifyUidBuf, confirmedTag) != 0) {
            presenceValidateFailures++;
        } else {
            presenceValidateFailures = 0;
        }

        if (presenceValidateFailures >= PRESENCE_VALIDATE_FAILS) {
            Serial.println("TAG OFF (validate)");
            strlcpy(lastRemovedTag, confirmedTag, sizeof(lastRemovedTag));
            sendToServer(nullptr);
            confirmedTag[0] = '\0';
            tagFound = false;
            lastTagRemoved = now;
            ledIdle();
            cycleRfField();
            presenceValidateFailures = 0;
        }
        lastPresenceValidate = now;
    }

    // Encoding timeout
    if (currentLedState == LED_ENCODING && (now - encodingStartTime) > 60000) {
        ledPlaying();
    }

    delay(50);
}
