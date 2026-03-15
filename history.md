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
🚧 **In Progress** — Planning complete, no code written yet. All engineering tasks created in Paperclip.

### Completed
- ✅ Project plan written and revised (PLAN.md)
- ✅ Architecture validated by research analyst (2026-03-15)
- ✅ Radio architecture critical fix identified and resolved (STA mode)
- ✅ All Paperclip tasks created and assigned

### In Progress
- Dashboard simulator engine (`js/simulator.js`) and config (`js/config.js`) — complete
- Dashboard HTML, CSS, SVG floor plans, visualization modules — not yet built
- Backend scaffolding exists (empty `__init__.py` files, `csi_packet.py`, `requirements.txt`)
- Research: signal processing validation document written

### Not Started
- Firmware, backend algorithms, FastAPI server, calibration system

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
- [ ] HAL-110: Dashboard code commentary — **blocked**, remaining JS modules not built yet
- [ ] HAL-171/HAL-266: Hardware setup guide — todo
- [ ] HAL-172/HAL-267: Calibration guide — todo
- [ ] HAL-174/HAL-269: GitHub README — depends on dashboard screenshots
- [ ] HAL-175/HAL-270: Installation guide (RPi + ESP32 flashing) — todo
- [ ] HAL-176/HAL-271: Code commentary for all modules — blocked on code completion
- [ ] HAL-191/HAL-286: Multi-floor documentation updates (Phase 2) — blocked on Phase 2 engineering

**Note:** Many tasks exist as duplicates under two different parent issues. Closed duplicates with cross-references.

## Important Notes
- HAL-110 and HAL-205 have overlapping scope. HAL-205 README completed; HAL-110 should focus on code commentary for remaining modules when they're built.
- Hardware BOM and architecture docs were completable from PLAN.md + research docs without needing code.
- Remaining doc tasks (calibration guide, installation guide, code commentary) require engineering work to be further along.

## Technical Details
- **Hardware cost:** ~$153 for Phase 1 (with mounting), ~$117 additional for Phase 2, ~$270 total
- **CSI specs:** 114 subcarriers (HT40), 100Hz sample rate, ~30-50ms end-to-end latency
- **Localization:** 1-1.5m accuracy with 1m calibration grid
- **Vital signs:** Breathing ±1-2 bpm (reliable), heart rate ±8-10 bpm (~50-60% usable)
- **Calibration:** ~17 min per floor at 1m grid (~350 points/floor)
- **Recommended board:** ESP32-S3-DevKitC-1 (N16R8) — 16MB flash, 8MB PSRAM

---
*Last updated: 2026-03-15 — Hardware BOM doc and architecture doc completed. Multiple duplicate tasks closed.*
