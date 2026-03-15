# WiFi CSI People Tracking & Vital Signs — Project History

## Project Overview
Real-time people tracking and vital signs monitoring system for a 3500 sq ft, three-story house using WiFi Channel State Information (CSI). ESP32-S3 boards extract CSI from WiFi signals; a Raspberry Pi 4 runs Python signal processing; a sci-fi HUD web dashboard visualizes positions, occupancy, and vital signs with confidence-driven rendering.

**Philosophy:** Build it for yourself, polish it, open source it. Portfolio piece and open source contribution — not a product play.

## Key Context & Decisions

### Architecture
- **Platform:** ESP32-S3 (firmware) + Raspberry Pi 4 (backend) + Static web frontend (dashboard)
- **Languages:** C (firmware), Python (backend), HTML/CSS/JS (dashboard)
- **Key Frameworks:** ESP-IDF 5.x, FastAPI, MQTT (Mosquitto), scipy/numpy
- **Managed via:** Paperclip (HAL company, wifi-csi project)

### Major Decisions
1. **ESP32-S3 in STA mode** — Original plan used promiscuous mode but it's mutually exclusive with station connection. All boards connect to house WiFi as stations; TX sends UDP unicast to RX at 100Hz. CSI callback fires on normal received frames.
2. **HT40 mode (114 subcarriers)** — Upgraded from HT20 (52 subcarriers) at no hardware cost for better accuracy.
3. **Fingerprint KNN + particle filter** — Best accuracy-to-effort ratio for home deployment. Not AoA/ToF.
4. **Confidence-driven visualization** — Core UX principle: uncertainty is visible, not hidden. Sharp/fuzzy blobs, noise clouds, zone overlays.
5. **Heart rate is experimental/conditional** — Only displayed when stationary >30s, SNR sufficient, position confidence >0.6. Hidden otherwise.
6. **No temperature sensing** — Out of scope; WiFi CSI cannot measure body temperature.
7. **Channel separation per floor (1/6/11)** — Non-overlapping 2.4GHz channels prevent inter-floor interference.
8. **Simulator-first development** — Dashboard can be built and polished without hardware.
9. **Phase 1 = single floor** — Validate on one floor before expanding to full house.

## Current Status
🚧 **In Progress** — Dashboard frontend complete (Phase 1), backend scaffolding exists, firmware not started.

### Completed
- ✅ Project plan written and revised (PLAN.md)
- ✅ Architecture validated by research analyst (2026-03-15)
- ✅ Radio architecture critical fix identified and resolved (STA mode)
- ✅ All Paperclip tasks created and assigned
- ✅ Dashboard frontend complete: index.html, CSS theme, SVG floor plans (all 3 floors), all JS modules (2026-03-15)
- ✅ Simulator engine with demo scenarios and random mode (10Hz, OU-process vitals)
- ✅ WebSocket client with auto-reconnect and simulator fallback
- ✅ Confidence-driven visualization (tracking dots, uncertainty rings, trails, vitals panel)
- ✅ Standalone rendering modules built (floorplan.js, tracker-overlay.js, vitals-panel.js, noise-overlay.js) — not yet wired into app.js
- ✅ Backend tracker modules complete: fingerprint_db, localization (KNN), floor_detector, particle_filter
- ✅ Backend processor modules complete: phase_sanitizer, amplitude_filter, subcarrier_selector, feature_extractor
- ✅ Backend vitals: windowed_fft (FFT/CWT utilities)
- ✅ Backend occupancy detection (`occupancy.py`) — NMF-based multi-person detection with source count estimation (2026-03-15, HAL-124)
- ✅ Backend breathing rate extraction (`breathing.py`) — Bandpass + FFT with in-band SNR and spectral concentration gating, 36 tests (2026-03-15, HAL-129)
- ✅ Backend heart rate extraction (`heartrate.py`) — CWT (Morlet wavelet) with breathing harmonic removal and 3-gate display logic (position confidence, stationarity, SNR), 48 tests (2026-03-15, HAL-130)
- ✅ Full pipeline integration (`main.py`) — MQTT collector → processor → tracker → vitals → WebSocket broadcast, per-floor FloorPipeline state, synthetic data fallback, health monitoring, graceful shutdown, CLI entry point, 22 integration tests (2026-03-15, HAL-138)
- ✅ MQTT listener (`collector/mqtt_listener.py`) — paho-mqtt v2 async bridge with thread-to-asyncio queue and backpressure handling (2026-03-15, HAL-138)
- ✅ Sensor config (`config/sensors.yaml`) — 12 ESP32-S3 boards across 3 floors, MQTT broker config (2026-03-15, HAL-139)
- ✅ Requirements and project setup — pinned deps + dev deps, Python 3.9 compat fix (2026-03-15, HAL-140)
- ✅ Firmware project skeleton and build system — ESP-IDF project with CMake, Kconfig menuconfig, sdkconfig.defaults (HT40/CSI/STA), config.h, main.c (WiFi init + TX/RX dispatch), csi_handler (CSI callback + 478-byte binary serialization), mqtt_client (QoS 0 publish), README with build instructions (2026-03-15, HAL-144)
- ✅ WiFi STA initialization and CSI config — status LED driver (3 blink patterns via FreeRTOS task), WiFi watchdog (auto-restart after 30s disconnect), MAC address logging for sensor registration, Kconfig options for LED GPIO and watchdog timeout (2026-03-15, HAL-145)
- ✅ Firmware TX mode — esp_timer-based 100Hz UDP unicast with non-blocking socket, rate logging (actual pps every 10s), sequence counter payload (2026-03-15, HAL-148)
- ✅ RPi deployment setup — Idempotent setup script (Mosquitto, Python venv, systemd service with security hardening, Avahi mDNS csi-hub.local, logrotate, fingerprint backup/restore cron, monitoring tools), full deploy/ directory with README (2026-03-15, HAL-153)

### In Progress
- Research: signal processing validation document written

### Not Started
- Firmware MQTT client implementation (binary publish + reconnect)
- Calibration system (guided walk, fingerprint DB builder)
- Wiring standalone rendering modules into app.js (floor 2/3 config, noise overlay, sparklines)

## Unfinished Work

### Immediate Next Steps (Engineering — not Documentarian scope)
1. Build CSI simulator (`dashboard/js/simulator.js`)
2. Build web dashboard with sci-fi HUD theme
3. Implement backend algorithms (processor, tracker, vitals)
4. Build FastAPI + WebSocket server
5. Write ESP32-S3 firmware (STA mode, HT40 CSI, MQTT)

### Documentation Tasks
- [x] HAL-205: Dashboard README — completed 2026-03-15
- [x] HAL-150/HAL-245: ESP32-S3 schematics and BOM — completed 2026-03-15 (`docs/hardware-bom.md`)
- [x] HAL-268/HAL-173: Architecture and algorithms doc — completed 2026-03-15 (`docs/architecture.md`)
- [x] HAL-110: Dashboard README + code commentary — completed 2026-03-15 (comprehensive README rewrite, architecture comments in all 7 JS modules)
- [ ] HAL-171/HAL-266: Hardware setup guide — todo
- [ ] HAL-172/HAL-267: Calibration guide — todo
- [ ] HAL-174/HAL-269: GitHub README — depends on dashboard screenshots
- [ ] HAL-175/HAL-270: Installation guide (RPi + ESP32 flashing) — todo
- [ ] HAL-176/HAL-271: Code commentary for all modules — blocked on backend code completion
- [ ] HAL-191/HAL-286: Multi-floor documentation updates (Phase 2) — blocked on Phase 2 engineering

**Note:** Many tasks exist as duplicates under two different parent issues. Closed duplicates with cross-references.

## Important Notes
- HAL-110 and HAL-205 had overlapping scope. Both now addressed: comprehensive README rewrite + code comments across all JS modules.
- Dashboard has two parallel rendering implementations: inline DOM in app.js (active) and standalone canvas/DOM class modules (not wired in). Future work should integrate or choose one.
- Floors 2 and 3 have SVG files but no CONFIG entries — need config + waypoints to function.
- Hardware BOM and architecture docs were completable from PLAN.md + research docs without needing code.
- Remaining doc tasks (calibration guide, installation guide, backend code commentary) require engineering work to be further along.

## Technical Details
- **Hardware cost:** ~$153 for Phase 1 (with mounting), ~$117 additional for Phase 2, ~$270 total
- **CSI specs:** 114 subcarriers (HT40), 100Hz sample rate, ~30-50ms end-to-end latency
- **Localization:** 1-1.5m accuracy with 1m calibration grid
- **Vital signs:** Breathing ±1-2 bpm (reliable), heart rate ±8-10 bpm (~50-60% usable)
- **Calibration:** ~17 min per floor at 1m grid (~350 points/floor)
- **Recommended board:** ESP32-S3-DevKitC-1 (N16R8) — 16MB flash, 8MB PSRAM

---
*Last updated: 2026-03-15 — HAL-153 completed: RPi deployment setup (Mosquitto, systemd, mDNS, backup, monitoring). 440 total tests pass.*
