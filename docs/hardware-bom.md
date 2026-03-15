# ESP32-S3 Hardware: Schematics and Bill of Materials

This document covers the hardware requirements, wiring, antenna placement, and mounting for the WiFi CSI People Tracking system.

---

## Bill of Materials

### Phase 1 — Single Floor Validation (~$140)

| Item | Qty | Unit Cost | Total | Specific Product | Notes |
|------|-----|-----------|-------|-----------------|-------|
| ESP32-S3-DevKitC-1 (N16R8) | 4 | ~$8 | ~$32 | [Espressif ESP32-S3-DevKitC-1-N16R8](https://www.espressif.com/en/products/devkits/esp32-s3-devkitc-1) | 16MB Flash, 8MB PSRAM. 1 TX + 3 RX. The **S3 variant** is required — it has documented CSI support in ESP-IDF 5.x with up to 114 subcarriers (HT40). ESP32-C3/C6 do NOT have equivalent CSI APIs. |
| Raspberry Pi 4 Model B (4GB) | 1 | ~$80 | ~$80 | [Raspberry Pi 4 Model B](https://www.raspberrypi.com/products/raspberry-pi-4-model-b/) | 4GB RAM minimum. Runs Mosquitto MQTT broker, Python backend, and FastAPI server. 2GB works but may be tight under load. |
| MicroSD Card (32GB, A1) | 1 | ~$8 | ~$8 | Samsung EVO Select or SanDisk Ultra A1 | A1-rated for better random I/O. Holds RPi OS + fingerprint databases (~50MB per floor). |
| USB-C Power Adapter (5V/2A) | 4 | ~$3 | ~$12 | Any UL-listed 5V/2A USB-C adapter | One per ESP32-S3. 2A is sufficient — the S3 DevKit draws ~350mA typical during WiFi TX. |
| USB-C Cable (3m / 10ft) | 4 | ~$2 | ~$8 | Any USB-C to USB-C or USB-A to USB-C, 3m length | Route to nearest outlet. Longer cables (5m) may cause voltage drop — stay at 3m or under. |
| **Phase 1 Total** | | | **~$140** | | |

### Phase 2 — Full House Expansion (~$104 additional)

| Item | Qty | Unit Cost | Total | Notes |
|------|-----|-----------|-------|-------|
| ESP32-S3-DevKitC-1 (N16R8) | 8 | ~$8 | ~$64 | 4 boards per additional floor (1 TX + 3 RX each) |
| USB-C Power Adapter (5V/2A) | 8 | ~$3 | ~$24 | |
| USB-C Cable (3m / 10ft) | 8 | ~$2 | ~$16 | |
| **Phase 2 Total** | | | **~$104** | |

### Full System Total: ~$244

- 12 ESP32-S3 boards (3 floors x 4 boards)
- 1 Raspberry Pi 4
- 12 USB-C power adapters
- 12 USB-C cables
- 1 MicroSD card

### Optional Mounting Hardware

No 3D-printed enclosures for v1 — just functional mounting.

| Item | Qty | Est. Cost | Notes |
|------|-----|-----------|-------|
| 3M Command Strips (medium) | 12 | ~$8 | Ceiling and wall mounting. Removable, no damage. |
| Cable clips (adhesive) | 24 | ~$5 | Route USB-C cables along walls/ceiling edges. |
| **Mounting Total** | | **~$13** | |

---

## ESP32-S3 DevKit Selection Guide

### Why ESP32-S3-DevKitC-1 (N16R8)?

- **CSI support:** The ESP32-S3 has documented WiFi CSI APIs in ESP-IDF 5.x (`esp_wifi_set_csi_rx_cb()`). This extracts per-packet Channel State Information with up to 114 subcarriers in HT40 mode.
- **N16R8 variant:** 16MB flash + 8MB PSRAM. The extra PSRAM allows buffering multiple CSI frames if MQTT backpressure occurs. The N8R2 variant works but has tighter memory margins.
- **Built-in USB-C:** No external UART adapter needed — flash and power over a single USB-C connection.
- **Dual external antenna connectors:** The DevKitC-1 has a PCB antenna by default. For better CSI quality, you can attach external 2.4GHz antennas (not required for v1).

### Boards to Avoid

| Board | Why Not |
|-------|---------|
| ESP32 (original) | CSI API exists but poorly documented; fewer subcarriers than S3 |
| ESP32-C3 | No documented CSI extraction API |
| ESP32-C6 | WiFi 6 capable but CSI APIs not mature in ESP-IDF |
| ESP32-S2 | Single-core; limited CSI support |

---

## Power Delivery

### Schematic

```
Wall Outlet (120V AC)
    │
    ▼
USB-C Power Adapter (5V / 2A)
    │
    ▼
USB-C Cable (3m max)
    │
    ▼
ESP32-S3 DevKit USB-C Port
    ├── 5V rail → LDO → 3.3V (ESP32-S3 core + WiFi radio)
    └── USB JTAG/Serial (used for flashing; not needed at runtime)
```

### Power Requirements

| Component | Typical Draw | Peak Draw | Notes |
|-----------|-------------|-----------|-------|
| ESP32-S3 (WiFi TX active) | ~350mA | ~500mA | TX board sending UDP at 100Hz |
| ESP32-S3 (WiFi RX + CSI) | ~280mA | ~400mA | RX board extracting CSI + MQTT publish |
| Raspberry Pi 4 (4GB) | ~600mA | ~1200mA | Under Python signal processing load |

### Cable Length Considerations

- **3m (10ft):** Safe. Voltage drop is negligible at 2A over 3m with 24AWG USB-C.
- **5m (16ft):** Marginal. Voltage drop may cause brownouts on the ESP32 during WiFi TX peaks. Use only if you have a 5V/3A adapter.
- **>5m:** Not recommended. Use a powered USB hub at the board's location instead.

---

## Board Placement

### Per-Floor Arrangement (Phase 1: Ground Floor)

```
    NW Corner                                    NE Corner
    ┌────────────────────────────────────────────────┐
    │                                                │
    │   [RX #1]                                      │
    │   Wall mount                     [RX #2]       │
    │   ~1.5m height                   Wall mount    │
    │                                  ~1.5m height  │
    │                                                │
    │              [TX]                               │
    │              Ceiling mount                      │
    │              Central position                   │
    │                                                │
    │                                                │
    │                                                │
    │                    [RX #3]                      │
    │                    South wall center            │
    │                    ~1.5m height                 │
    └────────────────────────────────────────────────┘
    SW Corner                                    SE Corner
```

| Board | Role | Placement | Height | Connection |
|-------|------|-----------|--------|------------|
| ESP32-S3 #1 | **TX** — sends UDP unicast at 100Hz | Central ceiling | Ceiling (~2.5m) | STA to house WiFi |
| ESP32-S3 #2 | **RX #1** — extracts CSI from TX frames | NW corner wall | ~1.5m (chest height) | STA to house WiFi |
| ESP32-S3 #3 | **RX #2** — extracts CSI from TX frames | NE corner wall | ~1.5m (chest height) | STA to house WiFi |
| ESP32-S3 #4 | **RX #3** — extracts CSI from TX frames | South wall center | ~1.5m (chest height) | STA to house WiFi |

### Placement Rationale

- **TX on ceiling, center:** Maximizes line-of-sight to all RX boards. Ceiling placement means the signal passes *through* the human body on its way to wall-mounted RX boards — this creates the strongest CSI perturbation.
- **RX at chest height (~1.5m):** Human body creates maximum CSI disturbance at torso level. Floor-level mounting picks up ground-bounce multipath instead of body perturbation.
- **Three RX in a triangle:** Spatial diversity from 3 receivers enables triangulation via fingerprint matching. Fewer than 3 RX boards significantly degrades localization accuracy.
- **Corner/edge placement for RX:** Maximizes the angular spread between TX-RX paths.

### Multi-Floor Layout (Phase 2)

Each floor gets the same 1 TX + 3 RX arrangement, with non-overlapping WiFi channels:

| Floor | Channel | TX Position | RX Positions |
|-------|---------|-------------|--------------|
| Ground (Floor 1) | **Channel 1** | Central ceiling | NW wall, NE wall, S wall |
| Second (Floor 2) | **Channel 6** | Central ceiling | NW wall, NE wall, S wall |
| Third (Floor 3) | **Channel 11** | Central ceiling | NW wall, NE wall, S wall |

Channels 1, 6, and 11 are the only non-overlapping channels in the 2.4GHz band. This prevents inter-floor CSI interference.

---

## Antenna Orientation

### Default (PCB Antenna)

The ESP32-S3-DevKitC-1 has a built-in PCB trace antenna along one edge of the board. For optimal CSI:

- **TX (ceiling):** Mount board flat against ceiling with antenna edge pointing **down** (toward the room). The PCB antenna radiates in a roughly omnidirectional pattern perpendicular to the board plane.
- **RX (wall):** Mount board flat against wall with antenna edge pointing **into the room** (away from wall). Avoid placing the antenna side flush against a wall — it attenuates the signal.

### Orientation Diagram

```
Ceiling TX (top view):          Wall RX (side view):

┌──────────────┐               Wall surface
│  ESP32-S3    │               │
│              │               │ ┌──────────────┐
│ [antenna ▼]  │               │ │  ESP32-S3    │
└──────────────┘               │ │              │
  Antenna points DOWN          │ │ [antenna →]  │
  into the room                │ └──────────────┘
                               │   Antenna points INTO room
```

### External Antenna (Optional Upgrade)

For improved CSI quality, replace the PCB antenna with an external 2.4GHz antenna:

- Use an antenna with an IPEX/U.FL connector (the DevKitC-1 has an IPEX footprint — may need to solder the connector)
- A small 2dBi omni-directional whip antenna is sufficient
- Mount the antenna perpendicular to the mounting surface for best coverage

This is NOT required for v1. The PCB antenna is adequate for a single floor in a residential setting.

---

## Interference Mitigation

WiFi CSI operates on the 2.4GHz band. To minimize interference:

1. **Move other devices to 5GHz.** Phones, laptops, smart TVs — anything that can use 5GHz WiFi should be configured to prefer it. This keeps the 2.4GHz band clear for CSI boards.

2. **Avoid microwave oven proximity.** Microwave ovens radiate at ~2.45GHz and will completely corrupt CSI readings while active. Keep RX boards at least 3m from the microwave.

3. **Bluetooth coexistence.** The ESP32-S3 has Bluetooth, but we disable it in firmware to avoid coexistence interference. Other Bluetooth devices in the house are generally fine.

4. **Neighboring WiFi networks.** If neighbors use the same 2.4GHz channel, it adds noise to CSI readings. The channel selection (1/6/11) should match a channel with minimal neighborhood traffic. Use a WiFi analyzer app to check.

5. **USB 3.0 interference.** USB 3.0 ports and cables emit 2.4GHz noise. If the RPi uses USB 3.0 peripherals, keep them away from ESP32 boards.

---

## Mounting Instructions

### Ceiling Mount (TX Board)

1. Find the approximate center of the floor's ceiling
2. Attach a 3M Command Strip to the back of the ESP32-S3 board (the non-component side)
3. Press firmly to ceiling with antenna edge pointing down
4. Route USB-C cable along ceiling edge to nearest outlet
5. Secure cable with adhesive cable clips every 30cm

### Wall Mount (RX Boards)

1. Choose the designated corner/wall position (see placement diagram)
2. Mount at ~1.5m height (roughly chest level)
3. Attach with 3M Command Strip, antenna edge facing into the room
4. Route USB-C cable down the wall to the nearest outlet
5. Secure cable with adhesive cable clips

### Tips

- Avoid mounting near large metal objects (filing cabinets, refrigerators) — metal reflects WiFi and creates CSI dead zones
- Keep boards at least 30cm from the wall corner to reduce multipath reflections
- If the ceiling mount is near a light fixture, offset by ~50cm — some LED drivers create 2.4GHz noise

---

## Cost Breakdown by Phase

### Phase 1 (One Floor)

| Category | Cost |
|----------|------|
| ESP32-S3 boards (4x) | $32 |
| Raspberry Pi 4 | $80 |
| MicroSD card | $8 |
| Power adapters (4x) | $12 |
| USB-C cables (4x) | $8 |
| Mounting hardware | $13 |
| **Total** | **~$153** |

### Phase 2 (Add Floors 2 + 3)

| Category | Cost |
|----------|------|
| ESP32-S3 boards (8x) | $64 |
| Power adapters (8x) | $24 |
| USB-C cables (8x) | $16 |
| Mounting hardware | $13 |
| **Total** | **~$117** |

### Full System (3 Floors)

| Category | Cost |
|----------|------|
| ESP32-S3 boards (12x) | $96 |
| Raspberry Pi 4 | $80 |
| MicroSD card | $8 |
| Power adapters (12x) | $36 |
| USB-C cables (12x) | $24 |
| Mounting hardware | $26 |
| **Total** | **~$270** |

---

## Network Requirements

- **WiFi Router:** Must support 2.4GHz (all ESP32-S3 boards connect via 2.4GHz STA mode)
- **WiFi SSID/Password:** All boards on a floor use the same SSID and connect to the same channel
- **RPi Network:** The Raspberry Pi should be on the same LAN (wired Ethernet preferred for reliability, WiFi works)
- **Static IP for RPi:** Recommended — the ESP32 boards publish MQTT to the RPi's IP address. Use router DHCP reservation or set a static IP on the RPi.
- **No internet required:** The system operates entirely on the local network. No cloud services, no external dependencies.
