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
🚧 **Software Complete / Hardware Blocked** — 653 tests passing. All code (firmware, backend, dashboard, calibration, vitals) is written and tested. Awaiting physical ESP32-S3 deployment.

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
- ✅ Dashboard WebSocket scenario tests — 23 integration tests using scripted WS message sequences: walk-through with confidence transitions, signal quality degradation/recovery, disconnect/reconnect state continuity, floor switching, demo mode replay, sustained 10Hz load + frame budget (2026-03-15, HAL-168)

### In Progress
- Research: signal processing validation document written

### RuView Research Findings (half-bakery #60, 2026-04-12)

**Subject:** https://github.com/ruvnet/RuView — WiFi DensePose platform for home/edge sensing.
**Stars:** 46,488 (high community signal; actively maintained — last push 2026-04-12)
**Hardware:** ESP32-S3 (identical to wifi-csi)
**Primary language:** Rust (ruvector stack), plus Python, JavaScript/TypeScript, C firmware
**Scope:** Presence detection, vital signs (breathing + heart rate), pose estimation (17 COCO keypoints via WiFlow), activity recognition, multi-frequency mesh, WASM edge modules, spiking neural networks, camera-supervised training

#### Overall Verdict
**High relevance for algorithm and firmware reference; not a drop-in dependency.** RuView's architecture is fundamentally different (Rust, UDP direct, DensePose-class localization) and far more complex than wifi-csi's Python/MQTT/KNN home-monitoring approach. Do NOT attempt wholesale integration. Select specific techniques.

#### Directly Applicable to wifi-csi

**1. QEMU Firmware Testing (ADR-061) — CRITICAL for hardware-blocked status**
RuView solved our exact problem: all firmware testing required physical hardware. They built an ESP32-S3 QEMU emulation setup using Espressif's official QEMU fork that emulates dual-core Xtensa LX7, UART, FreeRTOS, flash, and lwIP networking. Testable modules in QEMU: NVS config, edge DSP, frame serialization, UDP streaming, WASM runtime. Non-testable in QEMU: WiFi CSI callback (requires RF PHY), channel hopping. This directly addresses wifi-csi's hardware deployment blocker — firmware logic could be validated before physical boards arrive.
- ADR: `docs/adr/ADR-061-qemu-esp32s3-firmware-testing.md`
- Action: Evaluate adopting QEMU test infrastructure for `firmware/esp32-csi-node/`

**2. Firmware Build Guard (csi_collector.c, ADR-057)**
RuView added a compile-time `#error` if `CONFIG_ESP_WIFI_CSI_ENABLED` is not set in sdkconfig. Without it, firmware compiles but crashes at runtime with a cryptic error. wifi-csi's firmware should adopt this guard — it's a single `#ifndef` block and prevents a painful debugging session at first hardware deployment.
- File: `firmware/esp32-csi-node/main/csi_collector.c` (lines ~28-35)
- Action: Add identical guard to wifi-csi `firmware/main/csi_handler.c`

**3. Conjugate Multiplication for Phase Cleaning (ADR-014)**
RuView adopted this as their primary phase cleaning method over raw phase unwrapping. The math: `CSI_ratio[k] = H_1[k] * conj(H_2[k])` cancels hardware CFO/SFO/PDD offsets that corrupt raw ESP32-S3 phase, leaving only environment-caused phase changes. wifi-csi's `phase_sanitizer.py` currently uses Z-score outlier removal + unwrapping. Conjugate multiplication is more robust for multi-antenna setups and would pair well with HT40 mode.
- Reference: SpotFi (SIGCOMM 2015), IndoTrack (MobiCom 2017)
- Applicability: Medium — requires two antenna paths; needs validation with actual hardware data
- ADR: `docs/adr/ADR-014-sota-signal-processing.md`

**4. Hampel Filter for Outlier Detection (ADR-014)**
RuView replaced Z-score outlier detection with the Hampel filter (median ± 1.4826 × MAD). Rationale: Z-score uses mean/std, which are themselves corrupted by outliers (masking effect). Hampel filter uses median/MAD, resistant to 50% contamination. wifi-csi's `amplitude_filter.py` uses Z-score. Hampel filter would improve robustness to burst interference and multipath spikes — especially relevant for multi-floor deployment where interference is higher.
- Math: `median = med(x[i-w..i+w])`, `MAD = med(|x[j] - median|)`, replace outliers > t×1.4826×MAD with median
- Applicability: HIGH — pure Python, drop-in replacement for existing Z-score logic

**5. Min-Cut Person Separation (ADR-075)**
wifi-csi uses NMF-based multi-person detection in `occupancy.py`. RuView's ADR-075 documents a more physically grounded approach: Stoer-Wagner min-cut on a subcarrier correlation graph. People moving independently create separate correlated subcarrier clusters; min-cut finds the partition boundaries. Key insight: when two people move independently, they create two groups of subcarriers correlated internally but not across groups. This eliminates the calibration-dependency of NMF and doesn't require assuming a person count.
- Reference: Stoer-Wagner algorithm (O(VE + V² log V))
- Applicability: MEDIUM — significant implementation work; more relevant after Phase 1 validation with real hardware shows NMF limitations
- ADR: `docs/adr/ADR-075-mincut-person-separation.md`

**6. Fresnel Zone Breathing Model (ADR-014)**
wifi-csi's `breathing.py` uses bandpass + FFT with in-band SNR gating. RuView augments zero-crossing detection with Fresnel zone geometry: chest motion at 5mm amplitude is modeled against TX-RX-body geometry to predict expected signal variation amplitude. This improves detection reliability in multipath-rich environments where FFT peak can be masked.
- Math: `ΔΦ = 2π × 2Δd / λ`; expected amplitude `A = |sin(ΔΦ/2)|`
- Reference: FarSense (MobiCom 2019), Wi-Sleep (UbiComp 2021)
- Applicability: MEDIUM — useful for tuning once real hardware data available; adds a confidence scoring mechanism

**7. Multi-Frequency Mesh / Neighbor APs as Free Illuminators (ADR-073)**
wifi-csi already uses channels 1/6/11 for floor separation. RuView's ADR-073 documents a more aggressive strategy: deliberately hop across 6 channels (1,3,5,6,9,11) to exploit neighbor APs as passive illuminators, eliminating null subcarriers caused by frequency-selective fading. A single channel had 19% null subcarriers from metal objects. Multi-channel reduced this. Relevant for wifi-csi Phase 2 (multi-floor) where deploying 5 GHz channels alongside 2.4 GHz would further extend coverage.
- Applicability: Phase 2 / future — not needed for Phase 1 single-floor validation

**8. CSI Rate Limiting in Firmware (ADR-039)**
RuView's csi_collector.c limits UDP sends to one per 20ms (50Hz max) via `s_last_send_us` timestamp gating, preventing lwIP packet buffer exhaustion (ENOMEM) under high callback rates. wifi-csi targets 100Hz but may face the same ENOMEM issue under load. This is a defensive measure worth adding to firmware before hardware deployment.
- Applicability: HIGH — single constant + timestamp check, prevents silent data loss

#### What to Ignore / Not Adopt

- **Rust/ruvector/rvdna stack** — too complex, different ecosystem, no benefit over existing Python implementation for home monitoring scale
- **WiFlow / DensePose pose estimation** — 17-keypoint pose is massively more complex; requires training data and camera ground truth; overkill for room occupancy and vital signs
- **Cognitum Seed** — proprietary external hardware dependency
- **WASM edge modules** — no benefit given wifi-csi's fixed Raspberry Pi backend
- **Spiking Neural Networks (ADR-074)** — academic interest but not needed for Phase 1
- **Camera-supervised training (ADR-079)** — only relevant if pursuing full pose estimation (not a goal)

#### Documentation Quality Assessment
RuView's ADR system is exceptional — 80+ ADRs with: context (what exists + gaps), decision (what was chosen + math), rationale (why this vs alternatives), and implementation status. Serves as a reliable reference for algorithm decisions and engineering tradeoffs. The firmware README is thorough and well-tested. Signal processing ADRs cite specific papers with page-level math that can be replicated independently.

#### Algorithm References Extracted (no RuView dependency needed)
These papers are available independently and directly applicable to wifi-csi:
- SpotFi (SIGCOMM 2015) — conjugate multiplication phase cleaning
- IndoTrack (MobiCom 2017) — conjugate multiplication + AoA
- FarSense (MobiCom 2019) — Fresnel zone breathing model
- Wi-Sleep (UbiComp 2021) — Fresnel zone, sleep vital signs
- WiGest (SenSys 2015), WiDance (MobiCom 2017) — Hampel filter validation

### Recently Completed
- ✅ Blog series organized for publishing (half-bakery #57, 2026-04-12):
  - Created `blog/` directory with numbered, publish-ready posts
  - `blog/01-announcement.md` — project teaser (ready to publish, needs dashboard screenshot)
  - `blog/02-signal-processing.md` — signal processing deep dive (ready to publish)
  - `blog/03-early-hardware-results.md` — skeleton, blocked on hardware (#56)
  - `blog/04-open-source-launch.md` — full draft, blocked on hardware validation
  - `blog/linkedin-teaser.md` — LinkedIn teaser post (ready to publish)
  - `blog/README.md` — publishing checklist and status tracker
  - `content-strategy.md` — full content calendar, voice guide, audience segmentation
  - `launch-strategy.md` — cross-platform launch plan (HN, Reddit, Twitter/X, LinkedIn)
  - Posts 1, 2, and LinkedIn teaser are independently publishable now
- ✅ Phase 9 — Vital Signs Tuning infrastructure (HAL-161) (2026-03-19):
  - `backend/config/vitals.yaml` — Centralized ~25 tunable parameters for breathing, heart rate, and motion detection
  - `backend/config/vitals_config.py` — Config loader with typed dataclasses and factory methods for creating pre-configured extractors
  - `tools/vitals_benchmark.py` — Evaluation script: scores algorithms against recorded CSI data with ground truth, supports `--synthetic` self-test
  - `backend/tests/test_vitals_config.py` — 15 tests for config loading, defaults, custom overrides, factory propagation
  - Wired `VitalsConfig` into `FloorPipeline` and `Pipeline` in `main.py` — extractors created from config instead of hardcoded defaults
  - **Blocked on real hardware data** — tuning infrastructure ready, actual parameter optimization awaits ESP32-S3 deployment
  - 653 tests passing (15 new)
- ✅ Phase 8 — Calibration System (HAL-156) complete (2026-03-19):
  - `backend/calibration/collector.py` — Guided calibration walk collector with state machine (IDLE/COLLECTING/POINT_ACTIVE/PAUSED/COMPLETE), serpentine grid generation, inline feature extraction, save/load, progress tracking, house.yaml integration, 55 tests (HAL-157)
  - `backend/calibration/builder.py` — `FingerprintBuilder` class with global subcarrier selection, quality metrics (SNR/variance/confidence), low-quality flagging, LOO cross-validation, JSON loading, 26 tests (HAL-158)
  - `backend/calibration/zone_recal.py` — Zone recalibration with bounding box operations (find/remove/replace fingerprints), serpentine grid generation, 21 tests (HAL-159)
  - `backend/server/app.py` — Full calibration REST API: start/pause/resume/cancel sessions, submit/skip points, build fingerprint DB, zone recalibration, status queries
  - `backend/server/schemas.py` — Pydantic models for all calibration endpoints
  - 102 calibration tests passing, 405 total tests passing

### Not Started
- ⏳ **Hardware deployment** — half-bakery issue #56. Deployment runbook created (`docs/deployment-runbook.md`). Requires human with physical boards. (2026-04-12)
- Wiring standalone rendering modules into app.js (floor 2/3 config, noise overlay, sparklines)

## Unfinished Work

### Immediate Next Steps — Hardware Deployment (half-bakery #56)
> **Deployment runbook:** `docs/deployment-runbook.md` — single-page checklist for the full deployment.

1. **Flash firmware** to 4x ESP32-S3 boards (firmware ready, ESP-IDF 5.x) — record MAC addresses
2. **Deploy boards** on Floor 1: 1 TX (ceiling center) + 3 RX (walls, chest height)
3. **Update `sensors.yaml`** with real MAC addresses and measured board positions
4. **Validate MQTT** data flow: `mosquitto_sub -t 'csi/0/#' -v`
5. **Start backend**, confirm end-to-end pipeline: ESP32 → MQTT → backend → WebSocket → dashboard
6. **Run calibration walk** (~17 min) to build fingerprint database
7. **Tune vital signs** parameters against real data using `tools/vitals_benchmark.py`
8. **Run QA test suite** against real hardware data

### Algorithm Improvements (From RuView Research, half-bakery #60)
Priority order — do after hardware deployment and Phase 1 validation unless noted:

1. **[Pre-deployment, HIGH] Add CSI build guard to firmware** — `#ifndef CONFIG_ESP_WIFI_CSI_ENABLED / #error` in `firmware/main/csi_handler.c`. Prevents silent runtime crash if sdkconfig is misconfigured. 5-line change.
2. **[Pre-deployment, HIGH] Add send-rate limiter to firmware** — Cap UDP sends at 50Hz max via `s_last_send_us` timestamp guard. Prevents lwIP ENOMEM under high CSI callback rates. ~10-line change.
3. **[Post Phase 1, HIGH] Replace Z-score with Hampel filter in `amplitude_filter.py`** — Median/MAD-based outlier detection; more robust to burst interference than mean/std. Pure Python, drop-in.
4. **[Post Phase 1, MEDIUM] Evaluate Fresnel zone model for breathing confidence scoring** — Augment `breathing.py` SNR gating with geometry-based expected amplitude. Useful when real hardware shows FFT peak masking.
5. **[Post Phase 1, MEDIUM] Evaluate conjugate multiplication in `phase_sanitizer.py`** — Requires two antenna paths; more robust than unwrapping for ESP32-S3 phase data. Validate with real data first.
6. **[Phase 2, MEDIUM] Min-cut person separation** — Replace NMF in `occupancy.py` with Stoer-Wagner min-cut on subcarrier correlation graph. More physically grounded; no calibration dependency.
7. **[Phase 2, LOW] Multi-frequency channel hopping** — Exploit neighbor APs as illuminators; reduces null subcarrier rate. Relevant for floors 2-3 deployment.
8. **[Optional] QEMU firmware test setup** — Reference RuView ADR-061 to set up ESP32-S3 QEMU emulation for firmware CI without physical hardware.

### Future (After Phase 1 Validated)
- Multi-floor expansion: deploy 8 more boards on Floors 2-3 (channels 6, 11)
- Cross-floor tracking validation
- Long-running stability test (24h+)
- External antenna upgrade evaluation
- Wire standalone rendering modules into app.js

### Documentation Tasks (Phase 11 — HAL-170: COMPLETE as of 2026-03-16)
- [x] HAL-205: Dashboard README — completed 2026-03-15
- [x] HAL-150/HAL-245: ESP32-S3 schematics and BOM — completed 2026-03-15 (`docs/hardware-bom.md`)
- [x] HAL-268/HAL-173: Architecture and algorithms doc — completed 2026-03-15 (`docs/architecture.md`)
- [x] HAL-110: Dashboard README + code commentary — completed 2026-03-15 (comprehensive README rewrite, architecture comments in all 7 JS modules)
- [x] HAL-171/HAL-266: Hardware setup guide — completed 2026-03-15 (`docs/hardware-setup.md`)
- [x] HAL-172/HAL-267: Calibration guide — completed 2026-03-16 (`docs/calibration-guide.md`, verified against codebase — all sections present)
- [x] HAL-174/HAL-269: GitHub README — completed 2026-03-15 (`README.md`)
- [x] HAL-175/HAL-270: Installation guide — completed 2026-03-15 (`docs/installation.md`)
- [x] HAL-176/HAL-271: Code commentary — completed 2026-03-16 (module docstrings, `backend/README.md`)
- [x] HAL-177: Documentation review (QA) — completed 2026-03-16
- [x] HAL-191/HAL-286: Multi-floor documentation updates — completed 2026-03-19. Updated hardware-setup.md (multi-floor deployment strategy, cross-floor verification), calibration-guide.md (per-floor procedure, stairwell zone calibration), architecture.md (expanded floor detection algorithm with hysteresis, transition zones, cross-floor tracking), installation.md (Part 5: multi-floor configuration, multi-floor checklist), README.md (fixed cost figures, removed stale known limitation about missing floor 2/3 config)

**Note:** Many tasks existed as duplicates under two different parent issues. Closed duplicates with cross-references.

## Important Notes
- HAL-110 and HAL-205 had overlapping scope. Both now addressed: comprehensive README rewrite + code comments across all JS modules.
- Dashboard has two parallel rendering implementations: inline DOM in app.js (active) and standalone canvas/DOM class modules (not wired in). Future work should integrate or choose one.
- Floors 2 and 3 have SVG files AND config entries with full room/waypoint definitions in `dashboard/js/config.js`. (Note: previously recorded as missing — corrected 2026-03-19.)
- Hardware BOM and architecture docs were completable from PLAN.md + research docs without needing code.
- Calibration guide was written from design specs before calibration code existed — now validated against actual implementation.
- Hardware setup guide was already comprehensive but wasn't recorded as complete in prior history updates.

## Technical Details
- **Hardware cost:** ~$153 for Phase 1 (with mounting), ~$117 additional for Phase 2, ~$270 total
- **CSI specs:** 114 subcarriers (HT40), 100Hz sample rate, ~30-50ms end-to-end latency
- **Localization:** 1-1.5m accuracy with 1m calibration grid
- **Vital signs:** Breathing ±1-2 bpm (reliable), heart rate ±8-10 bpm (~50-60% usable)
- **Calibration:** ~17 min per floor at 1m grid (~350 points/floor)
- **Recommended board:** ESP32-S3-DevKitC-1 (N16R8) — 16MB flash, 8MB PSRAM

---
*Last updated: 2026-04-12 — half-bakery #60: RuView (github.com/ruvnet/RuView) researched and assessed. 8 actionable algorithm improvements identified. QEMU testing, build guard, rate limiter, Hampel filter, Fresnel zone model, conjugate multiplication, min-cut person counting, and multi-frequency mesh all documented with priority and effort estimates. See "RuView Research Findings" and "Algorithm Improvements" sections above.*
