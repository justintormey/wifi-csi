# Changelog

This project follows [Semantic Versioning](https://semver.org/).

## [0.8.0] - 2026-08-18

SemVer baseline established.

- 2026-03-15 through 2026-03-19: intensive build sprint — firmware (ESP32-S3 CSI extraction, STA mode, HT40), backend (phase sanitization, KNN localization, particle filter, floor detection, NMF occupancy, breathing/heart-rate vitals), and dashboard (sci-fi HUD, simulator, floor plans, calibration UI) all built out with 653 passing tests
- 2026-03-19: RPi deployment tooling (Mosquitto, systemd, mDNS) and S3/CloudFront dashboard deploy infrastructure added
- 2026-04-12: MIT license added; RuView competitive/technique research completed (half-bakery #60)
- Status: software feature-complete for Phase 1 (single floor) but never validated against real hardware or live CSI data — everything to date has run against synthetic/simulated data; hardware deployment is the remaining blocker before a 1.0 release
