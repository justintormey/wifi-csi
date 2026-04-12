# WiFi CSI: Real-Time People Tracking Through Walls

## One-Line Summary

Full-stack WiFi sensing system that tracks people and monitors vital signs through walls using $200 of commodity hardware — firmware, signal processing, and a real-time sci-fi dashboard.

---

## The Problem

WiFi Channel State Information (CSI) sensing has been in academic papers since 2015. The algorithms work. The hardware costs $8 per board. But you can't download a working system anywhere — commercial products are locked behind NDAs, and research code never leaves MATLAB.

I built the system I wish existed: a complete, documented, open-source WiFi sensing stack that someone with a USB cable and a weekend could deploy.

## What It Does

The system detects and tracks people across a three-story house using nothing but WiFi signal distortions — no cameras, no wearables, no cloud services.

- **Room-level positioning** (1–1.5m accuracy) across multiple floors
- **Breathing rate monitoring** (±1–2 BPM) for stationary occupants
- **Heart rate estimation** (±8–10 BPM, ~50–60% usable) — gated on strict confidence thresholds rather than showing false precision
- **Multi-person occupancy detection** (up to 4–5 people)
- **Real-time visualization** via a sci-fi HUD dashboard with confidence-driven rendering

## Architecture

```
ESP32-S3 Boards (12x, ~$8 each)
    │
    │  WiFi CSI @ 100Hz, 114 subcarriers (HT40)
    │  478-byte binary packets over MQTT
    │
    ▼
Raspberry Pi 4  ─── Mosquitto MQTT Broker
    │
    │  Python Signal Processing Pipeline:
    │  SpotFi phase sanitization → Hampel outlier rejection
    │  → Butterworth bandpass filters → Subcarrier selection
    │  → KNN fingerprint localization → Particle filter (200 particles)
    │  → FFT breathing extraction → CWT heart rate estimation
    │  → NMF occupancy detection
    │
    ▼
Web Dashboard  ─── FastAPI + WebSocket
    │
    │  SVG floor plans (3 floors)
    │  Confidence-driven rendering
    │  Real-time vitals display
    │  Built-in simulator for hardware-free development
```

## Technical Stack

| Layer | Technology |
|-------|-----------|
| Firmware | C / ESP-IDF 5.x (FreeRTOS, HT40 CSI, MQTT QoS 0) |
| Backend | Python 3.9+ / FastAPI / scipy / numpy / paho-mqtt |
| Frontend | Vanilla JS / HTML5 Canvas / SVG / WebSocket |
| Infrastructure | Mosquitto MQTT / systemd / Avahi mDNS / logrotate |
| Testing | pytest (543 tests) + Vitest (23 scenario tests) |

## Key Metrics

| Metric | Value |
|--------|-------|
| Hardware cost | ~$200 total |
| CSI sample rate | 100 Hz × 3 receivers per floor |
| Subcarriers | 114 (HT40 mode) |
| End-to-end latency | ~30–50ms |
| Localization accuracy | 1–1.5m |
| Breathing accuracy | ±1–2 BPM |
| Heart rate | ±8–10 BPM, ~50–60% usable |
| Calibration time | ~17 min per floor |
| Compute | Raspberry Pi 4 (no GPU required) |

## Design Philosophy

**Uncertainty is the feature.** High-confidence positions render as sharp, bright dots. Low-confidence positions blur into ghostly blobs. Weak signal zones show fog-of-war overlays. Heart rate is hidden — not dimmed, hidden — when confidence thresholds aren't met.

The system tells you what it knows and how well it knows it. No false precision.

**Classical DSP over ML.** The entire pipeline — phase sanitization, bandpass filtering, KNN localization, particle filtering, FFT/CWT spectral analysis — runs on a Raspberry Pi without a GPU. No training data, no model weights, no black boxes. Just math done right.

## What I Built

- **ESP32-S3 firmware** — WiFi STA mode, 100Hz CSI extraction, binary MQTT serialization, status LED driver, WiFi watchdog
- **25 Python backend modules** — Full signal processing pipeline from raw CSI bytes to position estimates and vital signs
- **8 JavaScript dashboard modules** — Real-time visualization with SVG floor plans, confidence rendering, WebSocket auto-reconnect, built-in simulator
- **Raspberry Pi deployment** — Idempotent setup script with systemd, Mosquitto, mDNS, logrotate, fingerprint backup cron
- **566+ tests** — Backend pytest suite + frontend Vitest scenario tests covering signal processing edge cases, WebSocket resilience, and rendering performance
- **Complete documentation** — Architecture guide, hardware BOM, calibration guide, installation guide, hardware setup guide

## What I Learned

**The subcarrier selection split was the biggest accuracy gain.** Tracking subcarriers (selected by variance) and vital sign subcarriers (selected by in-band SNR) must be separate. Using the same set degrades breathing detection by ~20%.

**Heart rate honesty matters more than heart rate accuracy.** The display gating system — requiring position confidence, stationarity, and SNR thresholds before showing any reading — is more important than the CWT extraction algorithm itself.

**Integration is the hard part.** Each signal processing technique comes from a different paper, a different research group. Stitching them into a system that runs in real-time on a Pi — that's the engineering contribution.

## Links

- **GitHub:** [github.com/justintormey/wifi-csi](https://github.com/justintormey/wifi-csi)
- **Blog Series:** Signal processing deep dive, architecture walkthrough, firmware development
- **Built with:** ESP-IDF, FastAPI, scipy, numpy, vanilla JS
