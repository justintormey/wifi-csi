# Deployment Runbook — Phase 1 (Floor 1)

Single-page checklist for issue #56: flash firmware, deploy hardware, validate end-to-end pipeline. Consolidates steps from `installation.md`, `hardware-setup.md`, `firmware/README.md`, and `calibration-guide.md`.

**Estimated time:** 2-3 hours (firmware flashing + physical mounting + calibration walk).

---

## Pre-Flight

- [ ] 4x ESP32-S3-DevKitC-1 (N16R8) boards in hand
- [ ] 1x Raspberry Pi 4 (4GB+) with MicroSD flashed (RPi OS Lite 64-bit Bookworm)
- [ ] 4x USB-C power adapters (5V/2A) + 4x USB-C cables (3m max)
- [ ] Mounting hardware: Command Strips, cable clips
- [ ] Know your house WiFi SSID + password (2.4GHz band)
- [ ] RPi has static IP or DHCP reservation (record it: `___________`)
- [ ] ESP-IDF v5.x installed on dev machine: `. $HOME/esp/esp-idf/export.sh`
- [ ] WiFi channel selected (use WiFi analyzer; pick least congested of 1/6/11): `___`

---

## Step 1: RPi Setup (~20 min)

```bash
ssh pi@csi-hub.local   # or use IP
git clone https://github.com/justintormey/wifi-csi.git
cd wifi-csi/deploy
sudo ./setup-rpi.sh
```

Verify:
```bash
sudo systemctl status mosquitto    # Active (running)
mosquitto_pub -t test -m hello && mosquitto_sub -t test -C 1   # Receives "hello"
```

---

## Step 2: Flash Firmware (4 boards, ~15 min)

Source ESP-IDF, then flash each board with its config. **Record MAC addresses** — you'll need them for `sensors.yaml`.

```bash
. $HOME/esp/esp-idf/export.sh
cd wifi-csi/firmware
idf.py set-target esp32s3
```

### Board 1: Floor 1 TX
```bash
idf.py menuconfig
# WiFi CSI Configuration:
#   Board role: TX
#   WiFi SSID: <your SSID>
#   WiFi Password: <your password>
#   WiFi Channel: <chosen channel>
#   Floor ID: 0
#   MQTT Broker IP: <RPi IP>
#   Board ID: f1-tx
#   TX Target IP: 255.255.255.255
idf.py build flash monitor -p /dev/ttyUSB0
# >>> Record MAC address from serial output: ___________________
# >>> Verify: solid LED = WiFi connected. Ctrl+] to exit monitor.
```

### Board 2: Floor 1 RX-A
```bash
idf.py menuconfig
# Change: Board role → RX, Board ID → f1-rx-a
idf.py build flash monitor -p /dev/ttyUSB0
# >>> Record MAC: ___________________
```

### Board 3: Floor 1 RX-B
```bash
idf.py menuconfig
# Change: Board ID → f1-rx-b
idf.py build flash monitor -p /dev/ttyUSB0
# >>> Record MAC: ___________________
```

### Board 4: Floor 1 RX-C
```bash
idf.py menuconfig
# Change: Board ID → f1-rx-c
idf.py build flash monitor -p /dev/ttyUSB0
# >>> Record MAC: ___________________
```

**Troubleshooting flash failures:** Try different cable, hold BOOT button during flash, install CP210x driver.

---

## Step 3: Update Config with Real MACs

Edit `backend/config/sensors.yaml` — replace placeholder MACs with the real ones recorded above.

---

## Step 4: Mount Hardware (~30 min)

Refer to placement diagram in `hardware-setup.md`. Summary:

| Board | Location | Height | Antenna Orientation |
|-------|----------|--------|-------------------|
| f1-tx | Ceiling center | Ceiling (~2.5m) | Antenna edge DOWN |
| f1-rx-a | NW corner wall | ~1.5m (chest) | Antenna edge INTO room |
| f1-rx-b | NE corner wall | ~1.5m (chest) | Antenna edge INTO room |
| f1-rx-c | South wall center | ~1.5m (chest) | Antenna edge INTO room |

Placement rules:
- 30cm min from wall corners
- 3m min from microwave
- 50cm from LED fixtures
- Avoid large metal surfaces

Mount with Command Strips. Route + clip cables.

---

## Step 5: Validate MQTT Data Flow

On the RPi:
```bash
# Verify all 4 heartbeats (JSON every 10s)
mosquitto_sub -t 'status/#' -v
# Should see 4 distinct MACs

# Verify CSI data streaming (binary, 100Hz per RX)
mosquitto_sub -t 'csi/0/#' -v
# Should see rapid binary data on 3 topics (one per RX)
```

If no data: check LED patterns on boards (fast blink = WiFi issue, triple flash = MQTT issue). See troubleshooting table in `hardware-setup.md`.

---

## Step 6: Start Backend & Validate Pipeline

```bash
sudo systemctl start wifi-csi-backend
sudo systemctl status wifi-csi-backend   # Active (running)
journalctl -u wifi-csi-backend -f        # Watch for errors
```

Open dashboard in browser: `http://csi-hub.local:8000` (or RPi IP:8000).

**End-to-end validation:** ESP32 → MQTT → backend → WebSocket → dashboard
- [ ] Dashboard connects (WebSocket indicator green)
- [ ] Live CSI data appears (not simulator fallback)
- [ ] Walking in front of RX boards causes visible signal change

---

## Step 7: Run Calibration (~20 min)

Prerequisites check:
```bash
mosquitto_sub -t 'csi/#' -v     # Binary data flowing
mosquitto_sub -t 'status/#' -v  # 4 heartbeats
sudo systemctl status wifi-csi-backend  # Running
```

Start calibration via REST API:
```bash
# Start a calibration session for floor 1
curl -X POST http://csi-hub.local:8000/api/calibration/start \
  -H 'Content-Type: application/json' \
  -d '{"floor_id": 1}'

# Follow the guided walk — system generates serpentine grid points
# At each point: stand still for ~3 seconds, then submit
curl -X POST http://csi-hub.local:8000/api/calibration/point/submit

# Skip inaccessible points (furniture, etc.)
curl -X POST http://csi-hub.local:8000/api/calibration/point/skip
```

After completing the walk (~17 min for 1m grid on 15x12m floor):
```bash
# Build fingerprint database from collected data
curl -X POST http://csi-hub.local:8000/api/calibration/build
```

---

## Step 8: Tune Vital Signs Parameters

Infrastructure exists in `backend/config/vitals.yaml` (~25 tunable parameters). With real data flowing:

```bash
# Run benchmark with real data
python tools/vitals_benchmark.py

# Or with synthetic self-test first
python tools/vitals_benchmark.py --synthetic
```

Adjust `vitals.yaml` parameters based on benchmark output. Key parameters:
- Breathing: bandpass filter bounds, FFT window size, SNR threshold
- Heart rate: CWT wavelet scale range, breathing harmonic removal, 3-gate thresholds

---

## Step 9: Run QA Test Suite

```bash
cd wifi-csi
python -m pytest backend/tests/ -v
# Expect: 653+ tests passing

# Integration test with real hardware data
python -m pytest backend/tests/ -v -k "integration"
```

---

## Completion Checklist

- [ ] 4 boards flashed and mounted
- [ ] MAC addresses recorded in `sensors.yaml`
- [ ] MQTT broker receiving data from all 4 boards
- [ ] Backend processing real CSI data (not simulator fallback)
- [ ] Dashboard displaying live tracking
- [ ] Calibration walk completed, fingerprint DB built
- [ ] Vitals benchmark run against real data
- [ ] QA test suite passes with real hardware data
- [ ] Actual board positions measured and updated in `sensors.yaml`
- [ ] House dimensions verified in `house.yaml`

---

## What's Next (After Phase 1 Validated)

- Multi-floor expansion (Floors 2-3): 8 more boards, channels 6 and 11
- Cross-floor tracking validation
- Long-running stability test (24h+)
- External antenna upgrade if CSI quality is insufficient

---

*Created: 2026-04-12 — Consolidates deployment steps for issue #56*
