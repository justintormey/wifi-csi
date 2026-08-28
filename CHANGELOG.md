# Changelog

This project follows [Semantic Versioning](https://semver.org/).

## [0.8.1] - 2026-08-28

Pre-deployment firmware hardening (RuView backlog items 1 and 2).

- Added compile-time guard in `firmware/main/csi_handler.c`: build now fails with a clear `#error` if `CONFIG_ESP_WIFI_CSI_ENABLED` is missing from sdkconfig, instead of producing firmware that boots but never receives CSI callbacks. Verified both ways in the ESP-IDF v5.2 container: clean build with the option set, hard failure at the guard with it stripped.
- Confirmed the send-rate limiter (RuView item 2) was already implemented: the CSI callback is capped at 100Hz and never touches the network; a ring buffer plus a dedicated FreeRTOS publish task already isolates lwIP from callback bursts. No change needed.
- Added `firmware/.gitignore` for generated build artifacts (`build/`, `sdkconfig`).

## [0.8.0] - 2026-08-18

SemVer baseline established.

- 2026-03-15 through 2026-03-19: intensive build sprint — firmware (ESP32-S3 CSI extraction, STA mode, HT40), backend (phase sanitization, KNN localization, particle filter, floor detection, NMF occupancy, breathing/heart-rate vitals), and dashboard (sci-fi HUD, simulator, floor plans, calibration UI) all built out with 653 passing tests
- 2026-03-19: RPi deployment tooling (Mosquitto, systemd, mDNS) and S3/CloudFront dashboard deploy infrastructure added
- 2026-04-12: MIT license added; RuView competitive/technique research completed (half-bakery #60)
- Status: software feature-complete for Phase 1 (single floor) but never validated against real hardware or live CSI data — everything to date has run against synthetic/simulated data; hardware deployment is the remaining blocker before a 1.0 release
