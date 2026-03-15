# WiFi CSI People Tracking & Vital Signs Monitoring

## Context

Build a real-time people tracking and vital signs monitoring system for a 3500 sq ft, three-story house using WiFi Channel State Information (CSI). WiFi CSI captures how wireless signals are distorted by the environment — human bodies cause measurable perturbations in amplitude and phase across subcarriers. Movement causes large CSI variance; breathing (~1-5mm chest displacement) and heartbeat (~0.1mm) cause detectable periodic patterns in CSI amplitude.

---

## Hardware Architecture

**12x ESP32-S3 boards (~$96) + 1x Raspberry Pi 4 (~$80)**

| Per Floor | Role                                | Count | Placement                       |
| --------- | ----------------------------------- | ----- | ------------------------------- |
| ESP32-S3  | TX (SoftAP, sends frames at 100Hz)  | 1     | Central ceiling                 |
| ESP32-S3  | RX (promiscuous mode, extracts CSI) | 3     | Walls/corners for triangulation |

* Each floor's TX on a different channel (1, 6, 11) to avoid interference
* RX units connect to house WiFi for MQTT backhaul to RPi
* RPi runs Mosquitto MQTT broker, Python backend, and FastAPI WebSocket server
* Total: 12 ESP32-S3 + 1 RPi 4 = ~$180-260

---

## Directory Structure

```
wifi-csi/
├── firmware/                        # ESP-IDF project for ESP32-S3
│   ├── CMakeLists.txt
│   ├── sdkconfig.defaults
│   └── main/
│       ├── main.c                   # WiFi init, CSI config, role dispatch
│       ├── csi_handler.c/h          # CSI callback, I/Q → binary packet
│       ├── mqtt_client.c/h          # Publish CSI to RPi via MQTT
│       └── config.h                 # Board role (TX/RX), channel, IDs
│
├── backend/                         # Python (runs on RPi)
│   ├── requirements.txt             # paho-mqtt, numpy, scipy, fastapi, uvicorn
│   ├── main.py                      # Entry point
│   ├── config/
│   │   ├── house.yaml               # Floor dimensions, sensor positions
│   │   └── sensors.yaml             # Sensor MACs, roles
│   ├── collector/
│   │   ├── mqtt_listener.py         # Subscribe csi/#, deserialize
│   │   └── csi_packet.py            # CsiPacket dataclass
│   ├── processor/
│   │   ├── phase_sanitizer.py       # SpotFi linear offset removal
│   │   ├── amplitude_filter.py      # Butterworth bandpass, Hampel outlier filter
│   │   ├── subcarrier_selector.py   # Top-K by variance
│   │   └── feature_extractor.py     # Build fingerprint feature vectors
│   ├── tracker/
│   │   ├── fingerprint_db.py        # Build/query fingerprint DB (.npz)
│   │   ├── localization.py          # Weighted KNN position estimation
│   │   ├── floor_detector.py        # Cross-floor CSI energy comparison
│   │   ├── particle_filter.py       # Trajectory smoothing (200 particles)
│   │   └── occupancy.py             # Multi-person detection via NMF
│   ├── vitals/
│   │   ├── motion_detector.py       # Static vs moving classification
│   │   ├── breathing.py             # 0.1-0.5Hz bandpass + FFT peak
│   │   ├── heartrate.py             # 0.8-2.0Hz + CWT (Morlet wavelet)
│   │   └── windowed_fft.py          # FFT/CWT utilities
│   ├── server/
│   │   ├── app.py                   # FastAPI: REST + WS /ws/tracking
│   │   ├── ws_manager.py            # WebSocket broadcast at 10Hz
│   │   └── schemas.py               # Pydantic models
│   ├── calibration/
│   │   ├── collector.py             # Guided walk calibration
│   │   └── builder.py               # Build fingerprint DB from data
│   └── tests/
│       ├── test_amplitude_filter.py
│       ├── test_localization.py
│       ├── test_vitals.py
│       └── fixtures/sample_csi.json
│
├── dashboard/                       # Static web frontend → S3 (sci-fi HUD theme)
│   ├── index.html                   # Full-screen dashboard layout
│   ├── css/
│   │   ├── dashboard.css            # Sci-fi HUD theme: dark bg, cyan/green accents, grid overlays
│   │   └── floorplan.css            # SVG floor plan styles with glow effects
│   ├── js/
│   │   ├── app.js                   # Init, WebSocket dispatch, demo mode toggle
│   │   ├── floorplan.js             # Load/render SVG floor plans
│   │   ├── tracker-overlay.js       # Tracking dots, trails, pulse animations
│   │   ├── vitals-panel.js          # Breathing/HR display + sparklines
│   │   ├── websocket-client.js      # Auto-reconnect WebSocket
│   │   ├── simulator.js             # CSI data simulator (generates fake people/vitals)
│   │   └── config.js                # Backend URL, floor definitions
│   └── assets/floorplans/
│       ├── floor1.svg               # Realistic 3-story house: ground floor (kitchen, living, garage)
│       ├── floor2.svg               # Second floor (bedrooms, bathrooms, hallway)
│       └── floor3.svg               # Third floor / attic (office, media room)
│
└── docs/
    ├── hardware-setup.md
    ├── calibration-guide.md
    └── architecture.md
```

---

## Data Flow

```
ESP32-TX ──WiFi frames──→ ESP32-RX (CSI callback)
                              │
                         MQTT publish (binary: ts|tx|rx|rssi|52×I,Q)
                              │
                              ▼
                    Mosquitto on RPi
                              │
                              ▼
                    collector/mqtt_listener.py
                         │ parse I/Q → amplitude + phase
                         ▼
                    processor/
                         │ phase sanitize → bandpass filter → select subcarriers
                         ▼
              ┌──────────┴──────────┐
              ▼                     ▼
         tracker/              vitals/
         KNN + particle        breathing: 0.1-0.5Hz FFT
         filter → (x,y,floor)  heartrate: 0.8-2Hz CWT
              └──────────┬──────────┘
                         ▼
                    server/app.py
                    WebSocket broadcast @ 10Hz
                         ▼
                    Browser dashboard
                    SVG floor plan + tracking dots + vitals panels
```

End-to-end latency: ~30-50ms on local network.

---

## Key Algorithms

**CSI Extraction**: ESP32 `esp_wifi_set_csi_rx_cb()` provides 52 subcarriers of complex I/Q per frame. Convert to amplitude (`√(I²+Q²)`) and phase (`atan2(Q,I)`).

**Phase Sanitization** (SpotFi): Fit linear model `phase[k] = a·k + b` across subcarriers, subtract to remove clock offset artifacts.

**Localization**: Fingerprint-based weighted KNN (K=5, cosine distance) against calibrated CSI profiles. Smoothed by particle filter (200 particles, velocity-constrained random walk). Accuracy: 1-2 meters.

**Floor Detection**: Compare CSI energy from each floor's TX — strongest perturbation indicates which floor. Stairwell transition zones defined in config.

**Breathing Rate**: Bandpass 0.1-0.5Hz → FFT on 30s window → peak frequency × 60 = breaths/min. Accuracy: ±1-2 bpm.

**Heart Rate**: Remove breathing harmonics → bandpass 0.8-2Hz → CWT (Morlet) → peak. Lower confidence; only reported when person stationary and SNR sufficient. Accuracy: ±5 bpm at ~70-80% reliability.

---

## Multi-Floor Strategy

* 3 non-overlapping WiFi channels (1, 6, 11) — one per floor TX
* Cross-floor attenuation (~10-15dB/floor) enables floor discrimination
* Each floor calibrated independently with its own fingerprint DB
* Stairwell transition zones in `house.yaml` allow tracker to expect floor changes
* Particle filter handles smooth transitions between floors

---

## Implementation Order

1. **ESP32 firmware** — CSI extraction + MQTT publish (TX and RX modes)
2. **Backend collector + processor** — MQTT listener, phase/amplitude processing
3. **Tracking engine** — fingerprint DB, KNN localization, particle filter
4. **Vital signs engine** — breathing and heart rate extraction
5. **API server** — FastAPI with WebSocket streaming
6. **CSI simulator** — `dashboard/js/simulator.js` generates realistic fake tracking data (simulated people walking between rooms, pausing, breathing/heartbeat patterns) so the dashboard works standalone without hardware
7. **Web dashboard** — Sci-fi HUD theme (dark background, cyan/green accents, scanline overlays, glow effects), realistic SVG floor plans with rooms/walls/doors for all 3 floors, tracking dots with trails, vital signs panels with sparkline charts
8. **Dashboard deploy workflow** — `.github/workflows/deploy-dashboard.yml` syncing `dashboard/` to S3
9. **Tests** — unit tests with synthetic CSI data, integration with recorded data
10. **Docs** — hardware setup, calibration guide

---

## Verification

* **Unit tests**: Synthetic sinusoids through bandpass (verify frequency selection), synthetic fingerprint DB (verify KNN accuracy), synthetic breathing signal (verify rate extraction within 1 bpm)
* **Integration**: Record real CSI from 2 ESP32 boards, replay through pipeline offline
* **Dashboard**: Mock WebSocket server with pre-recorded tracking data; verify rendering, floor switching, reconnection
* **Hardware validation**: Flash 1 TX + 1 RX → verify MQTT packets arrive → walk room → observe CSI variation → calibrate → verify tracking
