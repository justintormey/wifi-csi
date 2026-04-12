# Hardware Setup Guide

Everything you need to buy, configure, mount, and troubleshoot the physical hardware for the WiFi CSI People Tracking system.

**Audience:** Technically capable hobbyist — comfortable with USB cables, WiFi settings, and a terminal. No electrical engineering background needed.

**Companion docs:**
- [`installation.md`](installation.md) — Software setup (RPi, firmware flashing, backend)
- [`architecture.md`](architecture.md) — System design and data flow
- [`hardware-bom.md`](hardware-bom.md) — Detailed BOM tables and cost breakdowns

---

## How It Works (30-Second Version)

One ESP32-S3 board on each floor's ceiling broadcasts WiFi frames 100 times per second. Three receiver boards on the walls listen and measure how the signal changes — when a person walks through the room, their body distorts the signal in a measurable way. The receivers stream this data over MQTT to a Raspberry Pi, which runs the tracking algorithms and serves a live dashboard.

---

## Bill of Materials

### Phase 1 — Single Floor (~$153)

Start with one floor to validate the system before scaling.

| Item | Qty | ~Cost | What to Buy |
|------|-----|-------|-------------|
| ESP32-S3-DevKitC-1 (N16R8) | 4 | $32 | [Espressif ESP32-S3-DevKitC-1](https://www.espressif.com/en/products/devkits/esp32-s3-devkitc-1) — **must be N16R8 variant** (16MB flash, 8MB PSRAM) |
| Raspberry Pi 4 Model B (4GB) | 1 | $80 | [Raspberry Pi 4 Model B](https://www.raspberrypi.com/products/raspberry-pi-4-model-b/) — 4GB RAM minimum |
| MicroSD Card (32GB, A1-rated) | 1 | $8 | Samsung EVO Select or SanDisk Ultra A1 |
| USB-C Power Adapter (5V/2A) | 4 | $12 | Any UL-listed 5V/2A USB-C adapter |
| USB-C Cable (3m / 10ft) | 4 | $8 | USB-C to USB-A or USB-C, 3m max length |
| 3M Command Strips (medium) | 4 | $4 | For mounting boards to ceiling/walls |
| Adhesive cable clips | 12 | $3 | Route cables along walls and ceiling edges |
| **Phase 1 Total** | | **~$147** | |

### Full House — All 3 Floors (~$270)

| Item | Qty | ~Cost |
|------|-----|-------|
| ESP32-S3-DevKitC-1 (N16R8) | 12 | $96 |
| Raspberry Pi 4 | 1 | $80 |
| MicroSD Card | 1 | $8 |
| USB-C Power Adapters | 12 | $36 |
| USB-C Cables (3m) | 12 | $24 |
| Mounting hardware | — | $26 |
| **Full System Total** | | **~$270** |

> **Ordering tip:** Buy 4 boards first for Phase 1. Once you've confirmed tracking works on one floor, order the remaining 8 for floors 2 and 3.

---

## ESP32-S3 DevKit Selection

### Why the S3-DevKitC-1 (N16R8)?

The ESP32-S3 is the only Espressif chip with a mature, documented CSI API in ESP-IDF 5.x. Specifically:

- **`esp_wifi_set_csi_rx_cb()`** extracts per-packet Channel State Information — 114 subcarriers in HT40 mode, giving fine-grained signal detail.
- **N16R8** means 16MB flash + 8MB PSRAM. The extra PSRAM buffers CSI frames during MQTT backpressure. The N8R2 variant works but has tighter margins.
- **Built-in USB-C** for flashing and powering — no separate UART adapter needed.
- **IPEX/U.FL antenna footprint** for optional external 2.4GHz antennas (not needed for v1).

### Boards That Won't Work

| Board | Why Not |
|-------|---------|
| ESP32 (original) | CSI exists but poorly documented; fewer subcarriers |
| ESP32-C3 | No documented CSI extraction API |
| ESP32-C6 | WiFi 6 capable but CSI APIs not mature |
| ESP32-S2 | Single-core; limited CSI support |

---

## Power Delivery

### What Each Board Needs

| Board | Typical Draw | Peak Draw |
|-------|-------------|-----------|
| ESP32-S3 (TX mode) | ~350mA | ~500mA |
| ESP32-S3 (RX mode) | ~280mA | ~400mA |
| Raspberry Pi 4 | ~600mA | ~1200mA |

A standard 5V/2A USB-C adapter is more than sufficient for each ESP32 board.

### Cable Length Matters

| Length | Verdict | Notes |
|--------|---------|-------|
| **3m (10ft)** | Safe | Negligible voltage drop at 2A with 24AWG USB-C |
| 5m (16ft) | Marginal | Voltage drop may cause brownouts during WiFi TX peaks. Use a 5V/3A adapter if you must. |
| >5m | Not recommended | Use a powered USB hub at the board's location instead |

### Cable Routing

1. Run USB-C cables along ceiling edges (for TX boards) or down walls (for RX boards)
2. Secure with adhesive cable clips every ~30cm
3. Avoid running cables parallel to high-voltage wiring where possible — it creates noise on some USB cables
4. Each board needs its own outlet/adapter — USB hubs are acceptable but add a failure point

```
Wall Outlet (120V AC)
    │
    ▼
USB-C Adapter (5V / 2A)
    │
    ▼
USB-C Cable (≤3m)
    │
    ▼
ESP32-S3 DevKit USB-C Port
    ├── 5V → LDO → 3.3V (ESP32 core + WiFi radio)
    └── USB serial (used for flashing; not needed at runtime)
```

---

## Board Placement

### The Layout: 1 TX + 3 RX Per Floor

Each floor has exactly 4 boards: one transmitter on the ceiling and three receivers on the walls arranged in a triangle.

```
    NW Corner                                    NE Corner
    ┌────────────────────────────────────────────────┐
    │                                                │
    │   [RX-A]                                       │
    │   Wall mount                     [RX-B]        │
    │   ~1.5m height                   Wall mount    │
    │                                  ~1.5m height  │
    │                                                │
    │              [TX]                              │
    │              Ceiling mount                     │
    │              Central position                  │
    │                                                │
    │                                                │
    │                    [RX-C]                      │
    │                    South wall center           │
    │                    ~1.5m height                │
    └────────────────────────────────────────────────┘
    SW Corner                                    SE Corner
```

### Why This Arrangement

- **TX on the ceiling center** maximizes line-of-sight to all receivers. Crucially, the signal travels *down through* people's bodies on its way to wall-mounted receivers — this creates the strongest CSI perturbation.
- **RX at ~1.5m (chest height)** captures maximum body disruption. Floor-level mounting picks up ground-bounce multipath instead. Ceiling-level RX would miss torso-level perturbations.
- **Three RX in a triangle** provides spatial diversity for fingerprint-based triangulation. Fewer than 3 significantly degrades localization accuracy.

### Exact Positions (Default Config)

These positions are defined in `backend/config/sensors.yaml`. Adjust them to match your actual mounting locations.

**Floor 1 (15m × 12m, Channel 1):**

| Board | Position (x, y) | Location |
|-------|-----------------|----------|
| TX | (7.5, 6.0) | Ceiling center |
| RX-A | (1.0, 1.0) | Kitchen corner |
| RX-B | (14.0, 1.0) | Living room corner |
| RX-C | (7.5, 11.0) | Garage wall |

**Floor 2 (15m × 12m, Channel 6):**

| Board | Position (x, y) | Location |
|-------|-----------------|----------|
| TX | (7.5, 6.0) | Ceiling center |
| RX-A | (1.0, 1.0) | Master bedroom corner |
| RX-B | (14.0, 1.0) | Bedroom 2 corner |
| RX-C | (7.5, 11.0) | Hallway end |

**Floor 3 (12m × 10m, Channel 11):**

| Board | Position (x, y) | Location |
|-------|-----------------|----------|
| TX | (6.0, 5.0) | Ceiling center |
| RX-A | (1.0, 1.0) | Office corner |
| RX-B | (11.0, 1.0) | Media room corner |
| RX-C | (6.0, 9.0) | Back wall |

### Placement Rules

- **30cm minimum from wall corners** — corners concentrate multipath reflections and degrade CSI quality.
- **3m minimum from microwave ovens** — microwaves radiate at ~2.45GHz and corrupt CSI readings while active.
- **50cm from LED light fixtures** — some LED drivers emit 2.4GHz noise.
- **Avoid large metal surfaces** — filing cabinets, refrigerators, and metal shelving create CSI dead zones by reflecting or blocking the signal.

---

## Antenna Orientation

The ESP32-S3-DevKitC-1 has a PCB trace antenna along one edge of the board. It radiates roughly omnidirectionally perpendicular to the board plane. Proper orientation matters for CSI quality.

### TX (Ceiling Mount)

Mount the board flat against the ceiling with the **antenna edge pointing down** into the room.

```
Ceiling surface
│
│  ┌──────────────┐
│  │  ESP32-S3    │
│  │              │
│  │ [antenna ▼]  │
│  └──────────────┘
│    Antenna points DOWN
│    into the room
```

### RX (Wall Mount)

Mount the board flat against the wall with the **antenna edge facing into the room** (not flush against the wall surface — that attenuates the signal).

```
Wall surface
│
│  ┌──────────────┐
│  │  ESP32-S3    │
│  │              │
│  │ [antenna →]  │
│  └──────────────┘
│    Antenna points INTO room
```

### External Antenna (Optional Upgrade)

For improved CSI quality in larger rooms (>20m²), you can attach an external 2.4GHz antenna:

- Use an IPEX/U.FL connector (the DevKitC-1 has a footprint — may need soldering)
- A small 2dBi omni-directional whip antenna is sufficient
- Mount perpendicular to the mounting surface

This is **not required for v1**. The PCB antenna works well in typical residential rooms.

---

## Channel Configuration

### Why Channels 1, 6, and 11?

These are the only three non-overlapping channels in the 2.4GHz WiFi band. Using different channels per floor prevents the TX board on one floor from creating CSI interference on another floor's receivers.

| Floor | WiFi Channel | Firmware Floor ID | Backend Floor ID |
|-------|-------------|-------------------|-----------------|
| Ground (Floor 1) | **1** | 0 | 1 |
| Second (Floor 2) | **6** | 1 | 2 |
| Third (Floor 3) | **11** | 2 | 3 |

> **Note:** The firmware uses 0-based floor IDs; the backend uses 1-based. The conversion is automatic — just set the correct floor ID in menuconfig and the system handles the rest.

### Cross-Floor Isolation

Signal attenuates ~12 dB per floor of separation (through typical residential construction). This natural attenuation, combined with non-overlapping channels, gives each floor an independent CSI measurement space. The backend uses this attenuation model for floor detection — the strongest CSI perturbation tells the system which floor a person is on.

### Choosing Your Channel

If you're only deploying one floor (Phase 1), any of the three channels works. Pick whichever has the least traffic from neighbors:

1. Install a WiFi analyzer app on your phone (e.g., "WiFi Analyzer" on Android, "WiFi Explorer" on macOS)
2. Scan the 2.4GHz band in each area of the house
3. Choose the channel with the fewest competing networks
4. Set that channel in the firmware menuconfig for all 4 boards on that floor

---

## Interference Mitigation

WiFi CSI operates on the 2.4GHz band, which is crowded. These steps significantly improve CSI signal quality.

### 1. Move Other Devices to 5GHz

The single highest-impact action. Most modern devices (phones, laptops, smart TVs, smart speakers) support 5GHz WiFi. Configure them to prefer the 5GHz band in your router settings. This clears the 2.4GHz spectrum for CSI.

Many routers have a "band steering" feature that can push capable devices to 5GHz automatically.

### 2. Microwave Oven Distance

Microwave ovens radiate at ~2.45GHz — right in the middle of the WiFi band. When the microwave is running, it *will* corrupt CSI readings from any board within range. Keep RX boards at least 3m away. The effect is temporary (only while cooking), but it will cause tracking dropouts.

### 3. Bluetooth

The ESP32-S3 has Bluetooth capability, but this firmware disables it to prevent coexistence interference. Other Bluetooth devices in your house (headphones, speakers, smart locks) are generally fine — Bluetooth uses different modulation and power levels.

### 4. Neighboring WiFi

If neighbors use the same 2.4GHz channel, their traffic adds noise. Use the WiFi analyzer approach above to pick the cleanest channel. In dense apartment buildings, CSI quality may be lower than in detached houses — you may need the external antenna upgrade.

### 5. USB 3.0

USB 3.0 ports and cables are known to emit broadband 2.4GHz noise. If the Raspberry Pi uses USB 3.0 peripherals (external drives, etc.), keep them physically away from the ESP32 boards. This is mostly relevant if the RPi and an RX board are in the same room.

---

## Mounting Instructions

### Tools Needed

- 3M Command Strips (medium)
- Adhesive cable clips
- A step stool or short ladder (for ceiling mounting)
- A tape measure (to verify board positions match config)

### Ceiling Mount (TX Board)

1. Measure to find the approximate center of the floor's ceiling area
2. Clean the mounting spot with a dry cloth (Command Strips adhere better to clean surfaces)
3. Attach a Command Strip to the back of the ESP32 board (the flat, non-component side)
4. Press the board firmly against the ceiling for 30 seconds, antenna edge pointing **down**
5. Route the USB-C cable along the ceiling edge to the nearest outlet
6. Secure cable with adhesive clips every ~30cm
7. Plug into a USB-C adapter

### Wall Mount (RX Board)

1. Measure 1.5m up from the floor at the designated wall position
2. Clean the spot, attach Command Strip to the board's back
3. Press firmly against the wall for 30 seconds, antenna edge facing **into the room**
4. Route the USB-C cable down the wall to the nearest outlet
5. Secure cable with clips

### Verification After Mounting

After mounting all 4 boards on a floor:

1. Power all boards via USB-C
2. Watch the status LED on each board:
   - **Fast blink (200ms):** Connecting to WiFi — wait 5-10 seconds
   - **Solid on:** Connected and operational
   - **Triple flash:** Error — see [Troubleshooting](#troubleshooting)
3. On the Raspberry Pi, verify MQTT data is flowing:
   ```bash
   mosquitto_sub -t 'csi/#' -v
   ```
   You should see binary data arriving on topics like `csi/0/a4:cf:12:xx:xx:xx`.
4. Check board heartbeats:
   ```bash
   mosquitto_sub -t 'status/#' -v
   ```
   Each board publishes a JSON status message every 10 seconds with packet counts, heap usage, and uptime.

---

## Network Requirements

### WiFi

- **2.4GHz required.** All ESP32-S3 boards connect via the 2.4GHz band (STA mode). Your router must have 2.4GHz active.
- **All boards on a floor use the same SSID.** They connect to your house WiFi just like any other device.
- **Same channel.** All 4 boards on a floor must be set to the same WiFi channel in menuconfig. Different floors use different channels (1, 6, 11).

### Raspberry Pi

- **Same LAN.** The RPi must be on the same network as the ESP32 boards. Wired Ethernet is preferred for reliability but WiFi works.
- **Static IP recommended.** The ESP32 boards publish MQTT to the RPi's IP address (set in firmware menuconfig). If the RPi's IP changes, the boards can't reach the broker. Set a static IP via:
  - Router DHCP reservation (preferred — set once in your router's admin page), or
  - Static IP on the RPi itself (edit `/etc/dhcpcd.conf`)
- **mDNS.** The setup script configures `csi-hub.local` via Avahi. Some ESP32 configurations support mDNS resolution, but using a static IP is more reliable.

### Bandwidth

The system generates ~550 KB/s of MQTT traffic per floor at 100Hz (3 RX boards × 478 bytes × 100 packets/s). This is negligible on any modern LAN but worth noting if you're monitoring bandwidth.

### No Internet Required

The entire system operates on the local network. No cloud services, no external APIs, no subscriptions. Once set up, it runs fully offline.

---

## Troubleshooting

### Status LED Reference

| LED Pattern | Meaning | Duration |
|-------------|---------|----------|
| Fast blink (200ms on/off) | Connecting to WiFi | Should resolve in 5-10s |
| Solid on | Connected and operational | Normal state |
| Triple flash (3×100ms, 700ms off) | Error condition | Persistent until resolved |

### Common Issues

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| LED stays fast-blinking | Wrong SSID or password | Re-flash with correct WiFi credentials via menuconfig |
| LED triple-flashes | MQTT broker unreachable | Verify RPi IP in firmware config; check `sudo systemctl status mosquitto` on RPi |
| Board reboots every ~30s | WiFi watchdog triggered | WiFi signal too weak — move board closer to router or add a WiFi extender |
| No CSI data in MQTT | TX and RX on different channels | Verify all boards on a floor have the same channel in menuconfig |
| CSI data intermittent | WiFi congestion on 2.4GHz | Move other devices to 5GHz; try a less congested channel |
| Board won't flash | USB port or driver issue | Try a different cable; install [CP210x driver](https://www.silabs.com/developers/usb-to-uart-bridge-vcp-drivers); hold BOOT button during flash |
| Tracking accuracy poor | Board positions wrong in config | Measure actual (x, y) positions and update `sensors.yaml` |
| One RX has weak signal | Too far from TX, or obstacle | Reposition the board; avoid large metal objects between TX and RX |
| Dashboard shows no data | Backend not running | `sudo systemctl start wifi-csi-backend` on RPi |
| Brownouts / random resets | Cable too long or adapter underpowered | Use 3m cable max; ensure 5V/2A adapter |

### Diagnostic Commands (Run on RPi)

```bash
# Check MQTT broker
sudo systemctl status mosquitto

# Watch all CSI traffic
mosquitto_sub -t 'csi/#' -v

# Watch board heartbeats (JSON, every 10s per board)
mosquitto_sub -t 'status/#' -v

# Watch a specific floor (firmware floor ID 0 = ground)
mosquitto_sub -t 'csi/0/#' -v

# Check backend service
sudo systemctl status wifi-csi-backend
journalctl -u wifi-csi-backend -f

# Check Mosquitto logs
journalctl -u mosquitto -f
```

### WiFi Watchdog

Each board runs a watchdog that checks WiFi connectivity every 5 seconds. If WiFi stays disconnected for 30 seconds (configurable, 10–300s in menuconfig), the board automatically reboots. This recovers from permanent WiFi stalls without physical intervention.

---

## Multi-Floor Deployment Strategy

If you're deploying across all three floors, follow this phased approach to validate each floor before expanding.

### Recommended Order

1. **Deploy Floor 1 first (Channel 1).** This is your validation floor — confirm tracking, MQTT data flow, and dashboard rendering before touching Floors 2 and 3.
2. **Add Floor 2 (Channel 6).** Flash the 4 new boards with `floor_id=1` and `channel=6`. Mount, verify MQTT topics appear under `csi/1/#`, and confirm the dashboard switches between floors.
3. **Add Floor 3 (Channel 11).** Same process with `floor_id=2` and `channel=11`. Verify `csi/2/#` topics.

### Multi-Floor Wiring Considerations

- **Centralize power near stairwells.** Stairwell areas often have outlets accessible from multiple floors. Running USB-C cables from a central power strip simplifies cable management.
- **Label every cable and adapter.** With 12 boards across 3 floors, a single mislabeled cable creates a debugging nightmare. Use colored tape or labels: one color per floor.
- **One RPi serves all floors.** A single Raspberry Pi 4 handles MQTT brokering and signal processing for all 12 boards. The backend's `FloorPipeline` class maintains independent state per floor, so there's no need for multiple RPis.
- **Bandwidth sanity check.** At 100Hz × 3 RX boards × 478 bytes, each floor generates ~140 KB/s of MQTT traffic. All three floors combined: ~420 KB/s — negligible on any modern LAN.

### Cross-Floor Verification

After all three floors are deployed, verify cross-floor isolation:

```bash
# On the RPi — confirm data arrives on all three floor topics
mosquitto_sub -t 'csi/0/#' -v   # Floor 1 (firmware ID 0)
mosquitto_sub -t 'csi/1/#' -v   # Floor 2 (firmware ID 1)
mosquitto_sub -t 'csi/2/#' -v   # Floor 3 (firmware ID 2)

# Verify all 12 board heartbeats
mosquitto_sub -t 'status/#' -v
# You should see 12 distinct MAC addresses reporting every 10 seconds
```

Check that floor detection works by walking between floors:
1. Start on Floor 1 — the dashboard should show your position on Floor 1
2. Walk up the main stairwell — the tracker should transition you to Floor 2 as you enter the stairwell zone
3. Walk back down — the tracker should return you to Floor 1

If floor transitions are sluggish, verify that stairwell zones in `house.yaml` match the physical stairwell locations. The floor detector uses 3-frame hysteresis (relaxed to 1 frame inside transition zones).

---

## Next Steps

Once all boards are mounted, powered, and publishing CSI data:

1. **Update MAC addresses** in `backend/config/sensors.yaml` — replace the placeholder MACs with the real ones you recorded during flashing (see [`installation.md`](installation.md))
2. **Adjust house dimensions** in `backend/config/house.yaml` to match your floor plan
3. **Run calibration** — walk the system through its calibration procedure to build fingerprint databases (see `calibration-guide.md` when available)
4. **Start the backend** — `sudo systemctl start wifi-csi-backend`
5. **Open the dashboard** — the web dashboard auto-connects to the backend via WebSocket
