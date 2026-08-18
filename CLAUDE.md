# wifi-csi — Claude Code Brief

## Product Vision

A passive WiFi-based people tracking and vital signs monitoring system for indoor spaces (3-story house, 3500 sq ft). Uses WiFi Channel State Information (CSI) extracted from ESP32-S3 boards to track room-level occupancy, breathing rate (±1–2 bpm), and heart rate (conditional, ~50–60% reliability). Zero cameras. Zero wearables.

**Status:** Software complete and tested (653 tests passing). Hardware deployment pending. The codebase is production-ready; this project needs **QA validation, a crystal-clear Saturday-project setup guide (hardware + firmware + backend deployment), and rough-edge polish.**

## Tech Stack

| Layer | Tech | Key Files |
|-------|------|-----------|
| **Firmware** | ESP-IDF 5.x (C) — CSI extraction + MQTT | `firmware/main/main.c`, `csi_handler` |
| **Backend** | Python 3.9+, FastAPI, MQTT (Mosquitto) | `backend/main.py`, `backend/server/app.py` |
| **Signal Processing** | scipy, numpy — phase sanitization, KNN, FFT, CWT | `backend/processor/`, `backend/tracker/`, `backend/vitals/` |
| **Frontend** | Vanilla HTML/CSS/JS — sci-fi HUD | `dashboard/index.html`, `dashboard/js/app.js` |

## Architecture & Data Flow

```
ESP32 TX (100Hz) ──WiFi────> ESP32 RX (CSI callback)
                              ├─ Phase, subcarrier I/Q data
                              └─ Publish binary (MQTT)
                                  │
                    Mosquitto (RPi 4)
                                  │
         ┌─────────────────────────┼─────────────────────────┐
         │                         │                         │
    Collector              Processor                  Tracker
    (MQTT binary)     (Phase sanitizer,         (KNN + particle
                      bandpass, subcarrier      filter, floor
                      selection)                detection, NMF)
         │                         │                         │
         └─────────────────────────┼─────────────────────────┘
                                  │
                              Vitals
                         (Breathing: FFT,
                          Heart: CWT Morlet,
                          Motion detection)
                                  │
                    FastAPI WebSocket @ 10Hz
                                  │
                          Dashboard (HUD)
```

**End-to-end latency:** ~30–50ms on local network.

## Project Structure

```
wifi-csi/
├── firmware/                  # ESP-IDF — CSI extraction + MQTT publish
│   └── main/main.c            # CSI callback, STA mode, MQTT client
├── backend/
│   ├── main.py                # CLI entry point, FloorPipeline orchestration
│   ├── collector/
│   │   ├── mqtt_listener.py    # Subscribe, binary packet RX
│   │   └── csi_packet.py       # Deserialize binary CSI (ts, macs, rssi, 114×I,Q)
│   ├── processor/
│   │   ├── phase_sanitizer.py  # SpotFi linear-fit artifact removal
│   │   ├── amplitude_filter.py # Bandpass (tracking, breathing, heart)
│   │   ├── feature_extractor.py# Per-MAC feature vectors
│   │   └── subcarrier_selector.py
│   ├── tracker/
│   │   ├── fingerprint_db.py   # Load/build calibration DB
│   │   ├── localization.py     # KNN (cosine distance, K=5)
│   │   ├── particle_filter.py  # Trajectory smoothing (200 particles)
│   │   ├── floor_detector.py   # Cross-floor CSI energy comparison
│   │   └── occupancy.py        # NMF multi-person detection
│   ├── vitals/
│   │   ├── breathing.py        # Bandpass 0.1–0.5Hz → FFT peak (per-MAC)
│   │   ├── heartrate.py        # Bandpass 0.8–2Hz → CWT; gated display
│   │   ├── motion_detector.py  # Stationarity check
│   │   └── windowed_fft.py     # FFT + CWT utilities
│   ├── server/
│   │   ├── app.py              # FastAPI, GET /health, WS /ws
│   │   ├── ws_manager.py       # Broadcast per-floor state @ 10Hz
│   │   └── schemas.py          # Pydantic output models
│   ├── calibration/            # Fingerprint collection & recalibration
│   │   ├── builder.py
│   │   ├── collector.py
│   │   └── zone_recal.py
│   ├── config/
│   │   ├── house.yaml          # Zone layout, floor definitions
│   │   ├── sensors.yaml        # MAC → board role/floor mapping
│   │   └── vitals.yaml         # BPM ranges, display thresholds
│   └── tests/                  # 440+ tests, 21 test files
└── dashboard/
    ├── index.html              # Main HUD, WebSocket client
    ├── editor.html             # Fingerprint zone editor
    ├── css/                    # Dark theme, cyan/green glow, scanlines
    └── js/
        ├── app.js              # Main app loop, state management
        ├── simulator.js        # Synthetic data + demo scenarios
        ├── floorplan.js        # SVG rendering
        ├── tracker-overlay.js  # Position viz (dots, uncertainty, trails)
        ├── vitals-panel.js     # BPM gauges, confidence display
        ├── noise-overlay.js    # Ambient noise cloud
        └── ws_client.js        # WebSocket connection + fallback
```

## Key Algorithms

| Algorithm | Purpose | File | Method |
|-----------|---------|------|--------|
| **Phase Sanitization** | Remove WiFi clock artifacts | `processor/phase_sanitizer.py` | SpotFi linear-fit subtraction |
| **Localization** | (x, y, z) position | `tracker/localization.py` | Fingerprint KNN (K=5, cosine distance) |
| **Trajectory Smoothing** | Reduce jitter | `tracker/particle_filter.py` | Particle filter (200 particles) |
| **Floor Detection** | Which floor (1/2/3) | `tracker/floor_detector.py` | Cross-floor CSI energy ratio |
| **Occupancy** | Multi-person presence | `tracker/occupancy.py` | Non-negative Matrix Factorization |
| **Breathing** | Respiratory rate | `vitals/breathing.py` | Bandpass 0.1–0.5Hz → FFT peak |
| **Heart Rate** | Cardiac rate (conditional) | `vitals/heartrate.py` | Bandpass 0.8–2Hz → CWT Morlet |

## Hardware

**Phase 1 (single floor):**
- 4 × ESP32-S3-DevKitC-1 (N16R8): 1 TX + 3 RX (ceiling center + wall/corner triangulation)
- 1 × Raspberry Pi 4 (4GB): MQTT broker + Python backend
- Total cost: ~$150

**Full system (3 floors):** 12 × ESP32-S3 + 1 × RPi = ~$270

See `docs/hardware-bom.md` for BOM with links and mounting details.

## Current Status

**🚧 Software Complete / Hardware Awaiting Deployment**

- ✅ All modules built and tested (653 passing tests)
- ✅ Dashboard complete with simulator fallback
- ✅ Tracker (KNN, particle filter, floor detection, NMF occupancy)
- ✅ Vitals (breathing FFT, heart rate CWT, motion detection)
- ✅ Backend → FastAPI WebSocket pipeline
- ⏳ **Hardware ESP32 flashing & RPi deployment** — not yet done
- ⏳ **Integration testing on live hardware** — not yet done
- ⏳ **Setup guide for Saturday project** — needs writing

## Key Decisions & Non-Obvious Choices

1. **ESP32-S3 in STA mode** — All boards connect to house WiFi as stations. Original plan used promiscuous mode, but it's mutually exclusive with station connection. TX sends CSI feedback via UDP unicast @ 100Hz.

2. **HT40 mode (114 subcarriers)** — Upgraded from HT20 (52 subcarriers) at no cost for better localization accuracy.

3. **Fingerprint KNN + particle filter** — Chosen for accuracy-to-engineering-effort ratio. Not AoA/ToF (requires phased arrays or specialized hardware).

4. **Confidence-driven visualization** — Core UX principle: uncertainty is visible, not hidden. Blobs grow/shrink based on confidence; noise clouds indicate low SNR.

5. **Heart rate is gated & conditional** — Only displayed when position confidence > 0.6, stationary > 30s, and SNR sufficient. Hidden otherwise.

6. **Channel separation (1/6/11 per floor)** — Non-overlapping 2.4GHz channels prevent inter-floor interference.

7. **Simulator-first development** — Dashboard and backend were built and tested with synthetic data. Dashboard auto-detects missing WebSocket and falls back to `simulator.js`.

8. **Phase 1 = single floor** — Validate on one floor, then expand to full house. Most code is floor-agnostic; `sensors.yaml` + `house.yaml` + floor IDs in firmware config.

## Agent Notes

### What Needs Doing

1. **QA & Code Review** — Run test suite (`pytest backend/tests/`), verify all 440+ tests pass on your machine.
2. **Setup Guide (Critical)** — Write a step-by-step "Saturday project" guide:
   - Hardware: Which pins on ESP32, how to mount on ceiling/walls, which USB port on RPi
   - Firmware: How to flash firmware to each board, config.h edits (SSID, MQTT IP, floor ID), menuconfig options
   - Backend: RPi setup (Mosquitto, Python venv, systemd service), firewall rules, mDNS check
   - Dashboard: Start backend + frontend server, what to expect on first run
   - Troubleshooting: Common issues (MQTT connection, CSI callback, WebSocket), logs to check
   - Include wiring diagrams and photos (if available)
3. **Polish** — Check for TODOs in code, remove dead imports, tidy any rough edges

### Where Simulation Happens

- **Backend without hardware:** `python -m backend.main --simulate` generates synthetic CSI
- **Dashboard without backend:** `dashboard/js/simulator.js` provides demo scenarios and random walk mode
- **Full-stack simulation:** Backend in `--simulate` mode + dashboard via `python3 -m http.server 8080` in `dashboard/`

### Key File Conventions

- **Config:** `backend/config/house.yaml` (zone geometry), `backend/config/sensors.yaml` (MAC → floor mapping), `backend/config/vitals.yaml` (BPM thresholds)
- **Firmware config:** `firmware/main/config.h` (SSID, MQTT broker, floor ID, board role)
- **Tests:** Organized by module; use `fixtures/sample_csi.json` for synthetic packet data
- **Logging:** Backend logs to stdout (JSON format from Pydantic models); firmware uses UART (`esp_log`)

### Pitfalls & Non-Obvious

- **Fingerprint calibration:** Must be done per-floor, per-channel. See `backend/calibration/builder.py`. Initial fingerprint DB stored in `backend/tracker/fingerprint_db.py` (hardcoded for demo; upgrade to YAML/SQLite if expanding)
- **Motion detection:** Currently simple (stationarity window < 0.1m/s). May need tuning based on hardware ground-truth
- **Heart rate reliability:** ~50–60% accuracy when motion-gated. Expect high false-negatives if person is moving. This is documented; don't over-promise
- **Dashboard rendering:** SVG floor plans in `index.html` are hardcoded for the 3-story demo house. Edit SVG viewBox and zone polygons if deploying to different layout
- **WebSocket reconnection:** Auto-retry every 3s with exponential backoff (see `dashboard/js/ws_client.js`). Falls back to simulator after 5 failed attempts
- **MQTT binary format:** Defined in `backend/collector/csi_packet.py`. Firmware must match exactly (byte order, struct layout). Verify via `test_csi_packet.py`

### Testing

```bash
# Run all tests
pytest backend/tests/

# Run specific module
pytest backend/tests/test_tracker.py -v

# Simulation mode
python -m backend.main --simulate --verbose
```

All tests use synthetic data; no hardware required.
---

## Versioning — Semantic Versioning (mandatory)

This project follows [Semantic Versioning 2.0.0](https://semver.org/): `MAJOR.MINOR.PATCH`. Any agent/LLM making changes here MUST bump the version automatically as part of the change — never wait to be asked.

- **MAJOR** — breaking change: removed/renamed capability, incompatible API/CLI/schema/data-format/UX change
- **MINOR** — new backward-compatible functionality
- **PATCH** — backward-compatible bug fix, perf tweak, copy correction
- Docs-only or internal-refactor changes with no behavior change: no bump
- Pre-1.0 (`0.y.z`): breaking → MINOR, everything else → PATCH; new projects start at `0.1.0`

In the SAME commit as the change, update the version everywhere it appears:
1. **Source of truth** — whatever this repo uses (`package.json`, `VERSION`, `Info.plist`/`project.yml` `MARKETING_VERSION`, `pyproject.toml`, site footer constant). If none exists yet, create a root `VERSION` file at `0.1.0`.
2. **Documentation** — add a `CHANGELOG.md` entry (create the file if missing); update README/docs anywhere a version is stated.
3. **User interface** — not every UI displays a version and that's fine; never add one where none exists. Any surface that already shows a version (About screen, footer, settings, CLI `--version`) must be correct — reading from the single source of truth, never a second hardcoded copy.
4. **GitHub** — tag the release commit `vX.Y.Z` and push the tag with the branch (GitHub Releases for MAJOR/MINOR on repos that use them).
