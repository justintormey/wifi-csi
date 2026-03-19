# WiFi CSI People Tracking & Vital Signs

Real-time indoor people tracking and vital signs monitoring using WiFi Channel State Information (CSI). Track occupants across a three-story house with meter-level accuracy, detect breathing rates, and estimate heart rates — all from passive WiFi signal analysis.

<!-- TODO: Add dashboard screenshot/GIF here -->
<!-- ![Dashboard](docs/assets/dashboard-screenshot.png) -->

## What It Does

WiFi signals are distorted by everything in their path — walls, furniture, and human bodies. This system measures those distortions (CSI) across 114 WiFi subcarriers at 100Hz to:

- **Track people** room-by-room across three floors (1–2m accuracy)
- **Count occupants** per zone using Non-negative Matrix Factorization
- **Measure breathing rate** (±1–2 bpm) via 0.1–0.5Hz CSI amplitude oscillations
- **Estimate heart rate** (±8–10 bpm, ~50–60% reliability) when a person is stationary >30s
- **Visualize everything** on a sci-fi HUD dashboard with confidence-driven rendering

No cameras. No wearables. Just WiFi.

## How It Works

```
ESP32-S3 TX ──WiFi frames @ 100Hz──→ ESP32-S3 RX (CSI extraction)
                                          │
                                     MQTT publish (binary: ts|macs|rssi|114×I,Q)
                                          │
                                          ▼
                                 Mosquitto on Raspberry Pi
                                          │
                                          ▼
                              Python Signal Processing Pipeline
                                │                          │
                           Tracker                     Vitals
                           KNN + particle filter       Breathing: bandpass + FFT
                           → (x, y, floor)             Heart rate: CWT (Morlet)
                                │                          │
                                └──────────┬───────────────┘
                                           ▼
                                  FastAPI WebSocket @ 10Hz
                                           ▼
                                  Browser Dashboard (HUD)
```

**End-to-end latency:** ~30–50ms on local network.

### Key Algorithms

| Algorithm | Purpose | Method |
|-----------|---------|--------|
| **Phase Sanitization** | Remove clock offset artifacts | SpotFi linear fit subtraction |
| **Localization** | Position estimation | Fingerprint KNN (K=5, cosine distance) |
| **Trajectory Smoothing** | Reduce jitter | Particle filter (200 particles) |
| **Floor Detection** | Which floor is the person on | Cross-floor CSI energy comparison |
| **Occupancy** | Multi-person detection | NMF source separation |
| **Breathing** | Respiratory rate | Bandpass 0.1–0.5Hz → FFT peak |
| **Heart Rate** | Cardiac rate (conditional) | Bandpass 0.8–2Hz → CWT Morlet wavelet |

## Hardware Requirements

| Component | Qty | Per-Unit Cost | Purpose |
|-----------|-----|---------------|---------|
| ESP32-S3-DevKitC-1 (N16R8) | 4 (Phase 1) / 12 (full) | ~$8 | CSI extraction (1 TX + 3 RX per floor) |
| Raspberry Pi 4 (4GB) | 1 | ~$55 | MQTT broker + Python backend |
| USB-C cables + power supplies | Per board | ~$5 | Power delivery |
| Mounting hardware | Per board | ~$3 | Ceiling/wall mount |

**Phase 1 cost (single floor):** ~$153
**Full system (3 floors):** ~$270

Each floor uses a dedicated WiFi channel (1, 6, 11) to avoid interference. TX boards mount on the ceiling center; RX boards mount on walls/corners for triangulation.

See [`docs/hardware-bom.md`](docs/hardware-bom.md) for the complete bill of materials with links and mounting details.

## Quick Start

### 1. Clone and set up the backend

```bash
git clone https://github.com/justintormey/wifi-csi.git
cd wifi-csi
python3 -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt
```

### 2. Run the backend (simulation mode)

```bash
python -m backend.main --simulate
```

This starts the FastAPI server with synthetic CSI data — no hardware needed.

### 3. View the dashboard

```bash
cd dashboard
python3 -m http.server 8080
# Open http://localhost:8080
```

The dashboard auto-connects to the backend WebSocket. If the backend isn't running, it falls back to its built-in simulator with demo scenarios.

### 4. Flash firmware (when you have hardware)

```bash
# Install ESP-IDF 5.x first: https://docs.espressif.com/projects/esp-idf/en/latest/esp32s3/get-started/
source $HOME/esp/esp-idf/export.sh
cd firmware
idf.py set-target esp32s3
idf.py menuconfig   # Set board role, WiFi credentials, floor ID, MQTT broker IP
idf.py build && idf.py -p /dev/ttyUSB0 flash monitor
```

### 5. Deploy to Raspberry Pi

```bash
# On the RPi:
sudo bash deploy/setup-rpi.sh
```

This installs Mosquitto, creates a Python venv, configures systemd auto-start, sets up mDNS (`csi-hub.local`), log rotation, and daily fingerprint backups.

See [`deploy/README.md`](deploy/README.md) for the full deployment guide.

## Project Structure

```
wifi-csi/
├── firmware/               # ESP-IDF (C) — ESP32-S3 CSI extraction + MQTT
│   └── main/               #   main.c, csi_handler, mqtt_client, config.h
├── backend/                # Python 3.9+ — Signal processing pipeline
│   ├── collector/          #   MQTT listener, binary packet deserializer
│   ├── processor/          #   Phase sanitizer, bandpass filter, subcarrier selector
│   ├── tracker/            #   KNN localization, particle filter, floor detection, occupancy
│   ├── vitals/             #   Breathing (FFT), heart rate (CWT), motion detection
│   ├── server/             #   FastAPI + WebSocket broadcast at 10Hz
│   ├── config/             #   house.yaml, sensors.yaml
│   └── tests/              #   440+ tests across 21 test files
├── dashboard/              # Vanilla JS — Sci-fi HUD web frontend
│   ├── css/                #   Dark theme, cyan/green glow, scanline effects
│   ├── js/                 #   App, simulator, WebSocket client, floor plans
│   └── assets/floorplans/  #   SVG floor plans (3 floors)
├── deploy/                 # RPi deployment (systemd, Mosquitto, backups)
├── docs/                   # Architecture and hardware documentation
│   ├── architecture.md     #   System design, algorithms, data flow
│   └── hardware-bom.md     #   Bill of materials with costs and links
└── research/               # Research notes on CSI, signal processing, algorithms
```

## Configuration

**Backend** — Edit `backend/config/house.yaml` for your floor dimensions, room definitions, and stairwell zones. Edit `backend/config/sensors.yaml` for your board MAC addresses and positions.

**Firmware** — Use `idf.py menuconfig` to set board role (TX/RX), WiFi channel (1/6/11), floor ID, MQTT broker IP, and TX rate.

**Dashboard** — Edit `dashboard/js/config.js` for floor layout, room zones, and waypoint graph.

## Current Status

**~70% complete** — Backend algorithms, dashboard, firmware skeleton, and deployment infrastructure are done. Hardware integration testing and calibration system are next.

### What Works

- Full signal processing pipeline (phase sanitization → bandpass → KNN → particle filter)
- Multi-person occupancy detection via NMF
- Breathing rate extraction with SNR gating
- Heart rate extraction with confidence-gated display
- Sci-fi HUD dashboard with built-in simulator (works standalone)
- RPi deployment with systemd, mDNS, MQTT, and automated backups
- 440+ passing tests across unit, integration, and E2E suites

### Known Limitations

- Heart rate accuracy is experimental (~50–60% reliability, requires stationary subject)
- Calibration system not yet built (fingerprint collection + DB builder)
- Not yet tested with real ESP32 hardware end-to-end

## Running Tests

```bash
source .venv/bin/activate
pytest backend/tests/ -v
```

## Documentation

| Document | Description |
|----------|-------------|
| [`docs/installation.md`](docs/installation.md) | **Start here** — Full setup guide (RPi + ESP32 flashing) |
| [`docs/architecture.md`](docs/architecture.md) | System architecture, algorithms, and data flow |
| [`docs/hardware-bom.md`](docs/hardware-bom.md) | Bill of materials, power delivery, board placement |
| [`docs/hardware-setup.md`](docs/hardware-setup.md) | Board placement, antenna orientation, interference mitigation, multi-floor deployment |
| [`docs/calibration-guide.md`](docs/calibration-guide.md) | Fingerprint calibration, per-floor procedure, stairwell zone calibration |
| [`dashboard/README.md`](dashboard/README.md) | Dashboard features, simulator API, WebSocket schema |
| [`firmware/README.md`](firmware/README.md) | Firmware build, flash, and configuration |
| [`deploy/README.md`](deploy/README.md) | Raspberry Pi deployment and service management |

## Contributing

Contributions welcome. This project is in active development — check the issues and current status before starting work.

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/your-feature`)
3. Run the test suite (`pytest backend/tests/ -v`)
4. Submit a pull request

## References

- [ESP32 CSI Documentation](https://docs.espressif.com/projects/esp-idf/en/latest/esp32s3/api-guides/wifi.html#wi-fi-channel-state-information)
- [SpotFi: Decimeter Level Localization Using WiFi](https://web.stanford.edu/~skatti/pubs/sigcomm15-spotfi.pdf) — Phase sanitization algorithm
- [WiFi CSI-based Vital Signs Monitoring](https://ieeexplore.ieee.org/document/9296754) — Breathing and heart rate extraction from CSI

## License

<!-- TODO: Choose MIT or Apache 2.0 and add LICENSE file -->

License TBD. See [LICENSE](LICENSE) when added.
