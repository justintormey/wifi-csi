# ESP32-S3 CSI Deep Dive — Technical Brief

**Task:** HAL-238 | **Date:** 2026-03-15 | **Priority:** High

---

## Question

Deep research on ESP32-S3 CSI capabilities and ESP-IDF 5.x API for Phase 6 firmware development. Covers API details, subcarrier counts, STA mode compatibility, achievable frame rates, hardware selection, errata, and power budget.

---

## Findings

### 1. ESP-IDF 5.x CSI API

Three-call setup sequence:

```c
// Step 1: Configure CSI parameters
wifi_csi_config_t csi_config = {
    .lltf_en           = true,   // Legacy LTF (52 subcarriers HT20, 52 HT40)
    .htltf_en          = true,   // HT LTF (56 subcarriers HT20, 112 HT40)
    .stbc_htltf2_en    = true,   // STBC HT LTF (for STBC frames only)
    .ltf_merge_en      = true,   // Average LLTF+HT-LTF for smoother output
    .channel_filter_en = true,   // Smooth adjacent subcarriers (recommended)
    .manu_scale        = false,  // Auto-scale CSI data
    .shift             = 0,
    .dump_ack_en       = false,  // Don't capture ACK frames (reduces noise)
};
esp_wifi_set_csi_config(&csi_config);

// Step 2: Register callback
esp_wifi_set_csi_rx_cb(csi_callback, NULL);

// Step 3: Enable
esp_wifi_set_csi(true);
```

**Callback signature:**
```c
void csi_callback(void *ctx, wifi_csi_info_t *data) {
    // RUNS IN WIFI TASK — never do blocking work here
    // Copy data->buf (CSI bytes) and post to a FreeRTOS queue
    // data->buf memory is freed after callback returns
}
```

**Critical rule:** The CSI callback runs in the WiFi task. Any blocking operation (MQTT publish, UDP send, `printf`) will delay the WiFi stack and degrade CSI rate or cause packet loss. Always copy data to a queue and process in a separate lower-priority task.

**Function signatures (ESP-IDF 5.x):**

| Function | Purpose |
|---|---|
| `esp_wifi_set_csi_rx_cb(cb, ctx)` | Register CSI callback |
| `esp_wifi_set_csi_config(&config)` | Configure which LTF fields to capture |
| `esp_wifi_set_csi(true)` | Enable CSI collection |
| `esp_wifi_set_bandwidth(ESP_IF_WIFI_STA, WIFI_BW_HT40)` | Force HT40 mode |
| `esp_wifi_get_csi_config(&config)` | Read back current config |

---

### 2. HT40 Mode and Subcarrier Count

**The project description's "52 subcarriers" figure is for HT20, not HT40.**

Correct subcarrier counts per the ESP-IDF documentation:

| Mode | LLTF | HT-LTF | Total subcarriers | Raw bytes (2 bytes/subcarrier) |
|---|---|---|---|---|
| HT20 | 52 | 56 | 108 | 216 bytes |
| **HT40** | **52** | **112** | **164** | **328 bytes** |
| HT40 + STBC | 52 | 112 | 256 | 512 bytes |

**Note on the "114 subcarriers" figure:** This does not appear in Espressif documentation. The correct HT40 HT-LTF count is **112** (56+56 for the two 20MHz halves). The total with LLTF is **164**. The 114 figure may be from an older community reference and should not be relied upon.

**Configuring HT40:**
```c
// In WiFi init, after esp_wifi_start():
esp_wifi_set_bandwidth(ESP_IF_WIFI_STA, WIFI_BW_HT40);
// Default for ESP32-S3 is already HT40 — verify explicitly
```

The ESP32-S3 default bandwidth is HT40 for both STA and AP interfaces. Explicitly setting it in code is recommended to ensure consistent behavior regardless of AP negotiation.

**Hardware limitation (`first_word_invalid`):**
Check `wifi_csi_info_t.first_word_invalid`. When `true`, the first 4 bytes of CSI data are invalid due to an ESP32-S3 hardware limitation. Always skip these bytes in processing:

```c
if (data->rx_ctrl.first_word_invalid) {
    // Skip bytes 0-3 of data->buf
    process_csi(data->buf + 4, data->len - 4);
} else {
    process_csi(data->buf, data->len);
}
```

---

### 3. STA Mode + CSI Callback Compatibility

**Verdict: STA mode and CSI callback are fully compatible. Promiscuous mode is also compatible with STA mode and recommended for this project.**

Details:
- CSI callback works in STA mode, but **only triggers on packets received from the connected AP** unless promiscuous mode is also enabled
- In promiscuous mode, CSI triggers on **any received WiFi frame** on the current channel
- STA mode and promiscuous mode are **not mutually exclusive** on ESP32-S3

For our architecture (RX board connected to house WiFi for MQTT backhaul, also capturing CSI from the TX board's SoftAP channel):

```c
// RX board init sequence:
esp_wifi_set_mode(WIFI_MODE_STA);
esp_wifi_start();
esp_wifi_connect();  // Connect to house WiFi for MQTT

// ALSO enable promiscuous to capture CSI from TX board's frames
esp_wifi_set_promiscuous(true);
esp_wifi_set_csi_config(&csi_config);
esp_wifi_set_csi_rx_cb(csi_callback, NULL);
esp_wifi_set_csi(true);
```

**Important:** The RX board and TX board must be on the **same WiFi channel** for the RX to receive the TX's frames and generate CSI. If the house WiFi AP is on a different channel than the TX SoftAP, the RX cannot simultaneously serve both purposes on one radio. This is a **critical architectural constraint**.

**Recommended resolution:** Each floor has a TX SoftAP on channels 1, 6, or 11. The RX boards on each floor should connect to the house WiFi AP **only if it's on the same channel as that floor's TX**, or use a dedicated MQTT uplink channel (e.g., use the SoftAP's own network for MQTT if the TX also runs an MQTT broker, or use Ethernet for backhaul).

---

### 4. UDP Frame Rate — Can We Sustain 100Hz?

**Verdict: 100Hz is achievable via UDP but requires careful implementation. The 20Hz figure from reference implementations reflects serial (USB) bandwidth limits, not WiFi UDP limits.**

Analysis:
- HT40 CSI frame: 328 bytes payload + ~50 bytes overhead ≈ 380 bytes/packet
- At 100Hz: 100 × 380 = **38 KB/s UDP** — trivially within WiFi capacity
- ESP32-S3 UDP throughput: up to **30 MBit/s** — 100Hz CSI uses <1% of this

The 20Hz verified rate in the ADR-018 reference used serial output at limited baud rates. With UDP, the serial bottleneck disappears entirely.

**To achieve 100Hz TX:**

```c
// In TX board task — use usleep() not vTaskDelay()
// vTaskDelay(pdMS_TO_TICKS(10)) has ~10ms jitter due to FreeRTOS tick resolution
// usleep(10000) is more precise

while (1) {
    send_wifi_frame();   // Send the probe/data frame
    usleep(10000);       // 10ms = 100Hz
}
```

**Known issue — MQTT in CSI callback:** Issue [esp-csi #169](https://github.com/espressif/esp-csi/issues/169) reports that calling MQTT publish directly in the CSI callback suppresses the configured ping interval, resulting in a runaway rate. **Solution:** Always use a FreeRTOS queue between callback and MQTT/UDP task.

```c
// Pattern:
QueueHandle_t csi_queue;

void csi_callback(void *ctx, wifi_csi_info_t *data) {
    csi_packet_t pkt;
    memcpy(pkt.data, data->buf, data->len);
    pkt.len = data->len;
    pkt.rx_ctrl = data->rx_ctrl;
    xQueueSendFromISR(csi_queue, &pkt, NULL);
}

void udp_task(void *arg) {
    csi_packet_t pkt;
    while (1) {
        if (xQueueReceive(csi_queue, &pkt, portMAX_DELAY)) {
            udp_send(pkt.data, pkt.len);
        }
    }
}
```

**Realistic expectation:** 100Hz is achievable in ideal RF conditions. In a 3-story house with walls, expect 60-100Hz effective rate depending on channel congestion and retransmission behavior. Buffer for this in the pipeline (e.g., accept 50Hz as minimum viable and design filters accordingly).

---

### 5. Best ESP32-S3 DevKit Variant

**Recommendation: ESP32-S3-DevKitC-1-N8R8**

| Variant | Flash | PSRAM | Notes |
|---|---|---|---|
| N8R2 | 8MB | 2MB | Insufficient PSRAM for CSI buffering |
| **N8R8** | **8MB** | **8MB** | **Recommended — sweet spot for CSI** |
| N16R8 | 16MB | 8MB | Extra flash not needed; community reports occasional WiFi init issues |

**Why N8R8:**
- 8MB PSRAM is more than enough for CSI ring buffers + FreeRTOS tasks
- 8MB flash fits ESP-IDF + firmware with margin
- No reported WiFi connectivity issues (N16R8 has some community-reported WiFi initialization delays)
- Available on Amazon ~$10-12 per board (3-packs available)
- Espressif's own reference implementation uses N8R8

**Alternative:** Waveshare ESP32-S3-DEV-KIT-N8R8 (~$12) includes USB-C and exposes all GPIO — good for breadboard prototyping before finalizing PCB layout.

---

### 6. Known Issues and Errata

**From official ESP32-S3 errata (v0.1 and v0.2):**
- No WiFi-specific errata documented for ESP32-S3
- No CSI-specific errata documented

**Practical known issues from community:**

| Issue | Details | Mitigation |
|---|---|---|
| `first_word_invalid` | First 4 bytes of CSI buffer invalid (hardware) | Skip first 4 bytes in processing |
| MQTT in CSI callback | Disrupts CSI timing, ping interval ignored | Use queue pattern (see above) |
| HT40 channel width negotiation | AP may downgrade to HT20 if AP is HT20-only | Set TX AP explicitly to HT40; verify with `esp_wifi_get_bandwidth()` |
| Promiscuous + STA coexistence | ESP32-S3 WiFi radio captures on one channel only | TX and RX must be on same channel as house WiFi, or use Ethernet/ESP-NOW for backhaul |
| CSI callback memory | `data->buf` freed after callback returns | Always `memcpy` within callback |
| CSI on ACK frames | ACK frames can trigger callbacks at high rate (200+Hz) | Set `dump_ack_en = false` in config |

---

### 7. Reference Implementations

| Repo | Stars | Notes |
|---|---|---|
| [espressif/esp-csi](https://github.com/espressif/esp-csi) | Official | `csi_recv_router`, `csi_send`, human activity detection. Best starting point. |
| [StevenMHernandez/ESP32-CSI-Tool](https://github.com/StevenMHernandez/ESP32-CSI-Tool) | ~1k | Active STA, active AP, passive modes. Includes CSV output. |
| [Rui-Chun/ESP32-CSI-Collection-and-Display](https://github.com/Rui-Chun/ESP32-CSI-Collection-and-Display) | ~200 | UDP-based CSI streaming (closest to our architecture) |
| [aiot-lab/HKU-COMP3516-ESP32-CSI-Tool](https://github.com/aiot-lab/HKU-COMP3516-ESP32-CSI-Tool) | ~100 | ESP32-S3 specific, STA-AP mode, good reference for our dual-role boards |

**Recommended starting point:** `espressif/esp-csi` → `examples/get-started/csi_recv_router` for the basic callback pattern, then adapt to UDP output using the Rui-Chun architecture.

---

### 8. Power Consumption in Continuous CSI Mode

CSI is metadata extracted from received WiFi frames — it adds negligible CPU overhead to standard WiFi receive operations. Power is dominated by the WiFi RF frontend.

| State | Current | Notes |
|---|---|---|
| WiFi active (transmitting) | 160-240 mA peak | ~0.4W burst |
| WiFi active (receiving/associated) | 80-120 mA average | ~0.25W sustained |
| **Continuous CSI (typical)** | **~120 mA average** | **~0.3W** |
| Deep sleep | <0.1 mA | Not applicable for continuous CSI |

**For 12 boards (4 TX + 8 RX per floor × 3 floors, corrected: 4 TX + 12 RX = 12 total):**
- 12 boards × 0.3W = **~3.6W total system draw** (excluding RPi)
- RPi 4 under load: ~5-7W
- **Total system: ~9-11W** — standard 15W USB-C supply per floor works; house wiring is not a concern

**No special thermal management needed** for continuous operation at room temperature.

---

## Summary of Critical Corrections to Project Plan

| Plan Assumption | Correct Value | Impact |
|---|---|---|
| "52 subcarriers" | HT20=108, **HT40=164** subcarriers | MQTT packet size and buffer sizing need update |
| "114 subcarriers for HT40" | **HT40 HT-LTF = 112** (164 total with LLTF) | Minor; use 164 for total or 112 for HT-LTF only |
| "No promiscuous mode needed" | **Promiscuous mode recommended** for broader CSI capture | Architecture fine; ensure `esp_wifi_set_promiscuous(true)` in RX init |
| "100Hz via UDP" | **Achievable but requires usleep() not vTaskDelay()** | Use FreeRTOS queue between callback and UDP task |
| CSI callback does direct work | **Must use queue pattern** | Critical for stability; direct work in callback kills CSI rate |

---

## Recommended Firmware Skeleton (RX Board)

```c
#include "esp_wifi.h"
#include "esp_event.h"
#include "freertos/queue.h"

#define CSI_QUEUE_SIZE  100
#define UDP_PORT        5005

static QueueHandle_t s_csi_queue;

typedef struct {
    wifi_pkt_rx_ctrl_t rx_ctrl;
    uint8_t data[512];
    uint32_t len;
} csi_pkt_t;

static void csi_cb(void *ctx, wifi_csi_info_t *info) {
    if (!info || !info->buf) return;
    csi_pkt_t pkt = {
        .rx_ctrl = info->rx_ctrl,
        .len = info->len,
    };
    // Skip first_word_invalid bytes if needed
    uint8_t skip = info->rx_ctrl.first_word_invalid ? 4 : 0;
    memcpy(pkt.data, info->buf + skip, info->len - skip);
    pkt.len = info->len - skip;
    xQueueSendFromISR(s_csi_queue, &pkt, NULL);
}

static void udp_task(void *arg) {
    // ... create UDP socket ...
    csi_pkt_t pkt;
    while (1) {
        if (xQueueReceive(s_csi_queue, &pkt, portMAX_DELAY)) {
            // Serialize and send via UDP to RPi
        }
    }
}

void init_csi(void) {
    s_csi_queue = xQueueCreate(CSI_QUEUE_SIZE, sizeof(csi_pkt_t));
    xTaskCreate(udp_task, "udp_csi", 4096, NULL, 5, NULL);

    // Enable promiscuous for richer CSI
    esp_wifi_set_promiscuous(true);

    // Explicitly set HT40
    esp_wifi_set_bandwidth(ESP_IF_WIFI_STA, WIFI_BW_HT40);

    wifi_csi_config_t cfg = {
        .lltf_en           = true,
        .htltf_en          = true,
        .stbc_htltf2_en    = false,  // Only needed for STBC frames
        .ltf_merge_en      = true,
        .channel_filter_en = true,
        .manu_scale        = false,
        .dump_ack_en       = false,  // Suppress ACK CSI
    };
    esp_wifi_set_csi_config(&cfg);
    esp_wifi_set_csi_rx_cb(csi_cb, NULL);
    esp_wifi_set_csi(true);
}
```

---

## Sources

- [ESP32-S3 Wi-Fi Driver Guide (ESP-IDF v5.5.3)](https://docs.espressif.com/projects/esp-idf/en/stable/esp32s3/api-guides/wifi.html)
- [ESP32-S3 Wi-Fi API Reference (ESP-IDF v5.5.3)](https://docs.espressif.com/projects/esp-idf/en/stable/esp32s3/api-reference/network/esp_wifi.html)
- [espressif/esp-csi GitHub](https://github.com/espressif/esp-csi)
- [esp-csi Issue #114 — Subcarrier selection](https://github.com/espressif/esp-csi/issues/114)
- [esp-csi Issue #169 — Ping interval and MQTT in callback](https://github.com/espressif/esp-csi/issues/169)
- [StevenMHernandez/ESP32-CSI-Tool](https://github.com/StevenMHernandez/ESP32-CSI-Tool)
- [ESP32-CSI-Tool Issue #21 — Inconsistent packet rate](https://github.com/StevenMHernandez/ESP32-CSI-Tool/issues/21)
- [ESP32-S3 Errata v0.2](https://docs.espressif.com/projects/esp-chip-errata/en/latest/esp32s3/_tags/v0-2.html)
- [ruvnet/wifi-densepose ADR-018](https://github.com/ruvnet/wifi-densepose/issues/34)
- [ESP32-S3-DevKitC-1 Hardware Docs](https://docs.espressif.com/projects/esp-dev-kits/en/latest/esp32s3/esp32-s3-devkitc-1/index.html)
- [Current Consumption Measurement (ESP-IDF v5.5.3)](https://docs.espressif.com/projects/esp-idf/en/stable/esp32s3/api-guides/current-consumption-measurement-modules.html)
