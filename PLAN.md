# WiFi CSI People Tracking & Vital Signs Monitoring

## Context

Build a real-time people tracking and vital signs monitoring system for a 3500 sq ft, three-story house using WiFi Channel State Information (CSI). WiFi CSI captures how wireless signals are distorted by the environment — human bodies cause measurable perturbations in amplitude and phase across subcarriers. Movement causes large CSI variance; breathing (~1-5mm chest displacement) and heartbeat (~0.1mm) cause detectable periodic patterns in CSI amplitude.

**Project philosophy:** Build it for yourself. Make it work beautifully. Open source it when it's polished. Write about the journey. The commercial WiFi sensing market is a B2B licensing game (Origin Wireless, Cognitive Systems) — this isn't a product play. It's a technically impressive portfolio piece and open source contribution that doesn't exist yet: a complete, integrated, working WiFi CSI tracking system with a beautiful dashboard.

---

## Architecture Decisions

### ✅ Validated by Research Analyst (2026-03-15)

- **ESP32-S3 for CSI** — S3 variant has documented CSI support in esp-idf 5.x, up to 114 subcarriers (HT40).
- **RPi 4 as compute hub** — Sufficient CPU for Python signal processing; low power; runs Mosquitto, FastAPI, and ML pipeline.
- **MQTT backhaul** — Standard, reliable IoT pattern.
- **Fingerprint KNN + particle filter** — Best accuracy-to-effort ratio for home deployment.
- **SpotFi phase sanitization** — Required for phase-based algorithms.
- **Channel separation per floor (1/6/11)** — Non-overlapping 2.4GHz channels prevent inter-floor interference.
- **Simulator-first dashboard** — Allows UX development without hardware.
- **Sci-fi HUD visual theme** — The visualization IS the product.

### 🔴 Critical Fix: Radio Architecture (STA Mode)

The original plan used promiscuous mode on RX boards — **mutually exclusive** with maintaining a station connection on single-band ESP32-S3. Resolved:

**All boards connect to house WiFi as stations (Option A).** TX sends UDP unicast to RX at 100Hz. CSI callback fires on normal received frames — no promiscuous mode needed. Both TX and RX use house WiFi for MQTT backhaul. Same channel = no conflict.

### HT40 Mode

Upgraded from 52 subcarriers (HT20) to 114 subcarriers (HT40) at no hardware cost — better localization accuracy and more reliable vital sign extraction.

---

## Confidence-Driven Visualization

**Core UX principle: uncertainty is visible, not hidden.**

Every tracked person has a `position_confidence` score (0.0–1.0) derived from:
- KNN match quality (cosine similarity to nearest fingerprint)
- Particle filter convergence (spatial spread of particle cloud)
- CSI signal quality (SNR, subcarrier variance stability)

### Visual Encoding

| Confidence | Person Blob | Circle Radius | Trail | Label |
|-----------|-------------|---------------|-------|-------|
| **High** (>0.8) | Sharp, crisp dot with tight glow | Small (proportional to ~1m uncertainty) | Solid trail, bright | "Person 1" |
| **Medium** (0.4–0.8) | Soft-edged, slightly diffuse | Medium (proportional to ~2-3m uncertainty) | Dashed trail, dimmer | "~Person" |
| **Low** (<0.4) | Blurred, ghostly, pulsing opacity | Large (proportional to ~5m+ uncertainty) | No trail | "?" |

### Signal Noise Visualization

When CSI signal quality is poor (interference, multi-person ambiguity, environmental noise):

- **Ambient noise clouds** — Soft, slowly drifting translucent patches overlaid on the floor plan in affected zones. Opacity scales with noise level. Think: fog of war.
- **Wave distortion lines** — Subtle ripple/wave animations radiating from areas of high signal variance. Indicates "something is happening here but we can't resolve it."
- **Zone confidence overlay** — Each room/zone can dim or brighten based on aggregate signal quality. Well-covered zones are crisp; poorly-covered zones fade toward grey.

### Multi-Person Ambiguity

When NMF detects multiple people but can't cleanly separate them:
- Show overlapping fuzzy blobs instead of discrete dots
- Display `occupancy_estimate` ("~2-3 people") rather than precise count
- Affected zone gets a subtle interference pattern overlay

---

## Vital Signs: Breathing & Heart Rate

### Breathing Rate (Core Feature)

- Bandpass 0.1–0.5Hz → FFT on 30s window → peak frequency × 60 = breaths/min
- Accuracy: ±1-2 bpm when person is stationary
- Displayed in vitals panel when person detected and reasonably still
- `breathing_confidence` score gated by motion level and SNR

### Heart Rate (Experimental — Conditional Display Only)

Heart rate via WiFi CSI is unreliable in real home conditions. Display rules:

1. **Person must be detected** with position_confidence > 0.6
2. **Person must be stationary** for >30 continuous seconds (motion_detector confirms)
3. **SNR must exceed threshold** — heartbeat signal (0.8–2.0Hz) must be distinguishable from noise floor
4. **Only then** display heart rate with `heartrate_confidence` indicator
5. If any condition fails → hide heart rate entirely (no stale/uncertain readings)

Algorithm: Remove breathing harmonics → bandpass 0.8–2Hz → CWT (Morlet) → peak. Realistic accuracy: ±8-10 bpm, ~50-60% usable readings in home conditions.

**UI treatment:** Heart rate appears as a subtle, secondary reading below breathing — never prominent. Confidence shown as a small signal-strength indicator. If confidence drops, the reading fades out gracefully rather than disappearing abruptly.

### No Temperature

Temperature is out of scope. WiFi CSI cannot measure body temperature, and ambient temperature sensors add hardware complexity for minimal value. Eliminated from the plan entirely.

---

## Hardware Architecture

### Phase 1 — Single Floor Validation (~$125)

**Start with one floor. Get it working perfectly before expanding.**

| Item | Count | Unit Cost | Total | Notes |
|------|-------|-----------|-------|-------|
| ESP32-S3 DevKit | 4 | ~$8 | ~$32 | 1 TX + 3 RX for one floor |
| Raspberry Pi 4 (4GB) | 1 | ~$80 | ~$80 | Compute hub, runs all backend |
| MicroSD (32GB) | 1 | ~$8 | ~$8 | RPi OS + fingerprint DBs |
| USB-C power adapters | 4 | ~$3 | ~$12 | Ceiling/wall mounting power |
| USB-C cables (3m) | 4 | ~$2 | ~$8 | To nearest outlet |
| **Total** | | | **~$140** | |

**Per-floor arrangement (STA Mode):**

| Board | Role | Connection | Placement |
|-------|------|-----------|-----------|
| ESP32-S3 | TX — sends UDP at 100Hz | STA to house WiFi | Central ceiling |
| ESP32-S3 | RX #1 — extracts CSI | STA to house WiFi | NW corner wall |
| ESP32-S3 | RX #2 — extracts CSI | STA to house WiFi | NE corner wall |
| ESP32-S3 | RX #3 — extracts CSI | STA to house WiFi | South wall center |

### Calibration: 1m Grid (High Resolution)

Starting with a single floor means we can afford higher calibration resolution:

- **1m grid** — ~350 points for one floor (~1170 sq ft)
- At 3 seconds per point = ~17 minutes of walking
- Much better localization accuracy than 1.5m grid
- Visual grid overlay on floor plan SVG guides the calibration walk
- Zone-recalibrate mode for partial updates after furniture moves

### Phase 2 — Full House (~$120 additional)

Only after Phase 1 is validated and polished:

| Item | Count | Unit Cost | Total |
|------|-------|-----------|-------|
| ESP32-S3 DevKit | 8 | ~$8 | ~$64 |
| USB-C power adapters | 8 | ~$3 | ~$24 |
| USB-C cables (3m) | 8 | ~$2 | ~$16 |
| **Total** | | | **~$104** |

- Floor 2: channel 6, Floor 3: channel 11
- Each floor calibrated independently (1m grid per floor, ~17 min each)
- Stairwell transition zones defined in `house.yaml`

---

## Directory Structure

```
wifi-csi/
├── firmware/                        # ESP-IDF project for ESP32-S3
│   ├── CMakeLists.txt
│   ├── sdkconfig.defaults           # HT40 mode, STA config
│   └── main/
│       ├── main.c                   # WiFi init (STA mode), CSI config, role dispatch
│       ├── csi_handler.c/h          # CSI callback (114 subcarriers HT40), I/Q → binary
│       ├── mqtt_client.c/h          # Publish CSI data to RPi via MQTT
│       └── config.h                 # Board role (TX/RX), channel, STA credentials
│
├── backend/                         # Python (runs on RPi)
│   ├── requirements.txt             # paho-mqtt, numpy, scipy, fastapi, uvicorn, pyyaml
│   ├── main.py                      # Entry point
│   ├── config/
│   │   ├── house.yaml               # Floor dimensions, sensor positions, stairwell zones
│   │   └── sensors.yaml             # Sensor MACs, roles, floor assignments
│   ├── collector/
│   │   ├── mqtt_listener.py         # Subscribe csi/#, deserialize
│   │   └── csi_packet.py            # CsiPacket dataclass (114 I/Q pairs)
│   ├── processor/
│   │   ├── phase_sanitizer.py       # SpotFi linear offset removal
│   │   ├── amplitude_filter.py      # Butterworth bandpass, Hampel outlier filter
│   │   ├── subcarrier_selector.py   # Top-K by variance (from 114 subcarriers)
│   │   └── feature_extractor.py     # Build fingerprint feature vectors
│   ├── tracker/
│   │   ├── fingerprint_db.py        # Build/query fingerprint DB (.npz)
│   │   ├── localization.py          # Weighted KNN position estimation
│   │   ├── floor_detector.py        # Cross-floor CSI energy comparison
│   │   ├── particle_filter.py       # Trajectory smoothing (200 particles)
│   │   └── occupancy.py             # Multi-person detection via NMF (with confidence)
│   ├── vitals/
│   │   ├── motion_detector.py       # Static vs moving classification
│   │   ├── breathing.py             # 0.1-0.5Hz bandpass + FFT peak
│   │   ├── heartrate.py             # 0.8-2.0Hz + CWT (Morlet); confidence-gated
│   │   └── windowed_fft.py          # FFT/CWT utilities
│   ├── server/
│   │   ├── app.py                   # FastAPI: REST + WS /ws/tracking
│   │   ├── ws_manager.py            # WebSocket broadcast at 10Hz
│   │   └── schemas.py               # Pydantic models (confidence fields throughout)
│   ├── calibration/
│   │   ├── collector.py             # Guided walk calibration (1m grid, visual overlay)
│   │   ├── builder.py               # Build fingerprint DB from collected data
│   │   └── zone_recal.py            # Quick recalibrate a single zone
│   └── tests/
│       ├── test_amplitude_filter.py
│       ├── test_localization.py
│       ├── test_vitals.py
│       └── fixtures/sample_csi.json
│
├── dashboard/                       # Static web frontend (sci-fi HUD theme)
│   ├── index.html                   # Full-screen dashboard layout
│   ├── css/
│   │   ├── dashboard.css            # Sci-fi HUD: dark bg, cyan/green accents, scanlines, glow
│   │   └── floorplan.css            # SVG floor plan styles + confidence-driven effects
│   ├── js/
│   │   ├── app.js                   # Init, WebSocket dispatch, demo mode toggle
│   │   ├── floorplan.js             # Load/render SVG floor plans
│   │   ├── tracker-overlay.js       # Confidence-driven blobs, trails, pulse animations
│   │   ├── noise-overlay.js         # Noise clouds, wave distortion, zone confidence
│   │   ├── vitals-panel.js          # Breathing + conditional HR display
│   │   ├── websocket-client.js      # Auto-reconnect WebSocket
│   │   ├── simulator.js             # Data simulator (people, vitals, varying confidence)
│   │   └── config.js                # Backend URL, floor definitions
│   └── assets/floorplans/
│       ├── floor1.svg               # Ground floor (start here — Phase 1)
│       ├── floor2.svg               # Second floor (Phase 2)
│       └── floor3.svg               # Third floor (Phase 2)
│
└── docs/
    ├── hardware-setup.md            # Power delivery, channel config, interference mitigation
    ├── calibration-guide.md         # 1m grid walk procedure, zone recal
    └── architecture.md              # Data flow, algorithms, known limitations
```

---

## Data Flow

```
ESP32-TX (STA) ──UDP @ 100Hz──→ ESP32-RX (STA, same channel)
                                      │
                               WiFi CSI callback fires
                               (114 subcarriers I/Q)
                                      │
                               MQTT publish to RPi
                               topic: csi/{floor}/{rx_mac}
                                      │
                                      ▼
                           Mosquitto on RPi
                                      │
                                      ▼
                           collector/mqtt_listener.py
                                │ parse I/Q → amplitude + phase (114 sub)
                                ▼
                           processor/
                                │ phase sanitize → bandpass → top-K subcarriers
                                ▼
                   ┌────────────┴────────────┐
                   ▼                         ▼
              tracker/                  vitals/
              KNN + particle            breathing: 0.1-0.5Hz FFT
              filter → (x,y,floor)      heartrate: 0.8-2Hz CWT
              + position_confidence     (conditional, confidence-gated)
              occupancy: NMF
              + occupancy_confidence
                   └────────────┬────────────┘
                                ▼
                           server/app.py
                           WebSocket @ 10Hz
                           payload: positions[], confidence scores,
                                    vitals[], signal_quality per zone
                                ▼
                           Browser dashboard
                           Confidence-driven visualization:
                           sharp/fuzzy blobs, noise clouds, vitals panels
```

End-to-end latency: ~30-50ms on local network.

---

## WebSocket Payload Schema

```json
{
  "timestamp": 1710500000.0,
  "floor": 1,
  "people": [
    {
      "id": "p1",
      "x": 5.2,
      "y": 3.8,
      "position_confidence": 0.85,
      "uncertainty_radius_m": 1.2,
      "is_stationary": true,
      "stationary_duration_s": 45.0,
      "breathing": {
        "rate_bpm": 16,
        "confidence": 0.78
      },
      "heartrate": {
        "rate_bpm": 72,
        "confidence": 0.55,
        "display": true
      }
    }
  ],
  "occupancy_estimate": 1,
  "occupancy_confidence": 0.9,
  "zone_signal_quality": {
    "living_room": 0.88,
    "kitchen": 0.72,
    "hallway": 0.45
  }
}
```

`heartrate.display` is `true` only when all conditions are met (stationary >30s, SNR sufficient, person detected with confidence >0.6). Clients should hide heart rate entirely when `display` is `false`.

`zone_signal_quality` drives the noise cloud / fog-of-war visualization per zone.

---

## Key Algorithms

**CSI Extraction**: ESP32 `esp_wifi_set_csi_rx_cb()` on RX boards. HT40 mode = 114 subcarriers. Convert to amplitude (`√(I²+Q²)`) and phase (`atan2(Q,I)`). TX sends UDP unicast at 100Hz.

**Phase Sanitization** (SpotFi): Fit linear model `phase[k] = a·k + b` across 114 subcarriers, subtract to remove clock offset artifacts.

**Localization**: Fingerprint-based weighted KNN (K=5, cosine distance) against calibrated CSI profiles at 1m grid. Smoothed by particle filter (200 particles, velocity-constrained random walk). Position confidence derived from KNN match quality + particle spread.

**Floor Detection**: Compare CSI energy variance from each floor's TX — strongest perturbation indicates which floor. Stairwell transition zones in `house.yaml`.

**Breathing Rate**: Bandpass 0.1–0.5Hz → FFT on 30s window → peak frequency × 60 = breaths/min. ±1-2 bpm when stationary.

**Heart Rate** (Experimental): Remove breathing harmonics → bandpass 0.8–2Hz → CWT (Morlet) → peak. Confidence-gated: stationary >30s, SNR above threshold, position_confidence >0.6. ±8-10 bpm, ~50-60% usable.

---

## Multi-Floor Strategy (Phase 2)

* 3 non-overlapping 2.4GHz channels (1, 6, 11) — one per floor
* All TX/RX boards on a floor use the same channel (STA mode)
* Cross-floor attenuation (~10-15dB/floor) enables floor discrimination
* Each floor calibrated independently with its own fingerprint DB (`.npz` per floor)
* Stairwell transition zones allow particle filter to expect floor changes

---

## Known Limitations

| Limitation | Detail |
|---|---|
| Multi-person accuracy | Reliable for 1-2 people in different rooms; degrades with proximity. Shown as overlapping fuzzy blobs. |
| Heart rate | Experimental; requires 30s+ stationary, ~50-60% useful readings. Hidden when unreliable. |
| Localization accuracy | 1-1.5m typical with 1m grid; walls/metal create dead zones. Shown via confidence radius. |
| Re-calibration needed | After significant furniture rearrangement |
| No identity tracking | Positions are anonymous; cannot distinguish "who" |
| WiFi interference | 2.4GHz congestion degrades quality. Dedicate band to CSI boards; move other devices to 5GHz. |

---

## Implementation Order

1. **CSI simulator** — `dashboard/js/simulator.js`; fake people with varying confidence, movement patterns, vitals; drives all frontend work
2. **Web dashboard** — Full HUD with sci-fi theme, confidence-driven visualization (sharp/fuzzy blobs, noise clouds, zone overlays), floor plan SVG, vitals panels; all working against simulator
3. **Backend algorithms** — processor, tracker, vitals modules; validated against synthetic CSI data
4. **API server** — FastAPI + WebSocket; tested end-to-end with backend + simulator
5. **ESP32 firmware** — STA mode, HT40 CSI, MQTT publish
6. **Backend collector** — MQTT listener wired to real firmware
7. **Calibration sprint** — Walk 1m grid on floor 1; build fingerprint DB
8. **Vital signs tuning** — Tune bandpass/CWT parameters against real CSI data; set confidence thresholds
9. **Tests** — Unit + integration test suite
10. **Docs** — Hardware setup, calibration guide, architecture
11. **Phase 2 hardware** — Expand to floors 2-3 after Phase 1 validated

---

## Verification

* **Unit tests**: Synthetic sinusoids through bandpass, synthetic fingerprint DB (KNN accuracy), breathing signal extraction, 114-subcarrier I/Q parsing, confidence score calculation
* **Integration**: Record real CSI from 1 TX + 1 RX, replay through full pipeline
* **Dashboard**: Mock WebSocket with pre-recorded data + varying confidence levels; verify confidence visualization (fuzzy→sharp transitions), noise overlays, floor switching, reconnection, demo mode
* **Hardware validation — Phase 1**: Flash 1 TX + 3 RX (floor 1) → verify MQTT → walk room → observe CSI variation → calibrate 1m grid → verify tracking + confidence rendering
* **Hardware validation — Phase 2**: Roll out floors 2-3 after Phase 1 confirmed

---

## Open Source Strategy

1. Build the full system for your house — make it work end-to-end
2. Polish the dashboard — the visualization is what makes this project shareable
3. Document thoroughly — hardware setup, calibration, architecture decisions
4. Publish when it works — not before. A working system with a beautiful dashboard goes viral; a half-finished repo gets ignored
5. Write about the journey — signal processing challenges, calibration lessons, multi-floor problem. This is content marketing that positions you as a genuine expert in a space where very few people have hands-on experience
