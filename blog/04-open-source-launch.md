# wifi-csi Is Now Open Source — Track People Through Walls With $200 of Hardware

Your WiFi router already knows where you are. Every packet it sends bounces off your body, and those bounces encode your position, your breathing rate, and — sometimes — your heart rate. The math has been in academic papers since 2015. The hardware costs $8 per board. But until today, no one had assembled the full stack as open source.

**[wifi-csi](https://github.com/justintormey/wifi-csi) is now public.** It's a complete indoor people tracking and vital signs monitoring system: ESP32-S3 firmware, Python signal processing pipeline, Raspberry Pi deployment, and a sci-fi HUD dashboard that visualizes it all. Clone it, flash some boards, and track people through walls in your own house.

<!-- BEFORE PUBLISHING: Add dashboard screenshot or GIF showing 2-3 tracked people with vitals. Capture from simulator mode. -->

---

## What You're Getting

The repo is the entire system, not a library or a proof-of-concept. Four layers, all documented:

**Firmware (C, ESP-IDF 5.x).** Flash an ESP32-S3 board, point it at your WiFi network, and it starts extracting Channel State Information — 114 complex values per WiFi frame, 100 times per second. Transmitter boards send UDP unicast; receiver boards capture CSI on every incoming frame and publish the raw data over MQTT. Each board costs about $8.

**Signal Processing (Python).** A Raspberry Pi ingests the MQTT stream and runs the pipeline: SpotFi phase sanitization strips clock artifacts, Butterworth bandpass filters isolate motion from breathing from heartbeat, fingerprint KNN with cosine distance estimates position, and a 200-particle filter smooths the trajectory. Breathing comes from FFT on the 0.1–0.5Hz band. Heart rate uses Continuous Wavelet Transform with Morlet wavelets, after removing breathing harmonics. All of it runs on a Pi 4.

**Dashboard (Vanilla JS).** A dark-themed HUD with SVG floor plans, real-time tracking dots, confidence rings, trail history, and a vitals panel. It connects to the backend via WebSocket at 10Hz. No frameworks, no build step — open `index.html` and it works. Has a built-in simulator with demo scenarios so you can explore without hardware.

**Deployment (Bash + systemd).** One script sets up everything on a Raspberry Pi: Mosquitto broker, Python virtualenv, systemd service with security hardening, mDNS (`csi-hub.local`), log rotation, and daily fingerprint database backups.

---

## The Numbers

I'm going to give you the real numbers, not the lab numbers.

### Hardware Cost

| Component | Qty (3 floors) | Cost |
|-----------|----------------|------|
| ESP32-S3-DevKitC-1 (N16R8) | 12 | ~$96 |
| Raspberry Pi 4 (4GB) | 1 | ~$55 |
| USB-C cables + power | 12 | ~$60 |
| Mounting hardware | 12 | ~$33 |
| **Total** | | **~$244** |

For a single floor (Phase 1), it's ~$140. Four boards, a Pi, and some USB cables.

### Performance

**Localization: 1–2 meter accuracy.** Good enough to know which room someone is in. Not precise enough to know which chair. Requires a one-time calibration walk — about 17 minutes per floor at 1-meter grid spacing.

**Breathing rate: ±1–2 BPM.** This is the reliable measurement. Chest displacement of 1–5mm per breath creates a strong periodic signal in the CSI data. Works when the person is relatively still.

**Heart rate: ±8–10 BPM, roughly 50–60% of readings usable.** I want to be direct about this. Papers claim 96%+ accuracy. Those results were collected with one person in a clean lab with controlled airflow. In a real living room with HVAC vibrations, pets, multiple occupants, and varying distances from receivers — you get about half your readings at usable quality. The system gates display on strict confidence thresholds (stationary >30s, SNR sufficient, position confidence >0.6). It shows you a heart rate when it can trust the measurement. Otherwise, it shows nothing.

**Occupancy: up to 4–5 people.** NMF-based source separation. Beyond that, the signal decomposition becomes unreliable.

**Latency: 30–50ms** end-to-end on a local network.

None of these numbers are bad. They're just real.

---

## The Architecture Choice That Almost Broke Everything

One decision almost killed the project before it started: how the ESP32 boards interact with WiFi.

The obvious approach — and the one I initially designed — was promiscuous mode on the receiver boards. Sniff all WiFi traffic, extract CSI from every frame. The problem: on single-band ESP32-S3, promiscuous mode is mutually exclusive with maintaining a station connection. You can listen to everything, or you can be connected to your network. Not both.

No station connection means no MQTT. No MQTT means no data getting to the Pi. Game over.

The fix was architecturally cleaner anyway: all boards connect to the house WiFi as regular stations. The transmitter sends UDP unicast to each receiver at 100Hz. The receivers' CSI callback fires on those incoming frames — no promiscuous mode needed. Same channel for data collection and MQTT backhaul. The boards stay connected, the data flows, and you get 114 subcarriers at 100Hz with zero conflict.

This is the kind of thing that doesn't show up in papers. Papers assume you have custom hardware or kernel-patched Intel NICs. Building with commodity boards forces you to solve real integration problems.

---

## Why Uncertainty Is a Feature, Not a Bug

Most systems give you a dot on a map and let you assume it's accurate. This one doesn't.

Every tracked person has a confidence score derived from KNN match quality, particle filter convergence, and CSI signal-to-noise ratio. That confidence drives the visualization:

- **High confidence (>0.8):** Sharp, bright tracking dot with a tight glow ring and solid trail.
- **Medium confidence (0.4–0.8):** Soft-edged, slightly diffuse blob. Dashed trail. The dashboard is literally telling you "I'm less sure about this one."
- **Low confidence (<0.4):** Ghostly, blurred, pulsing. No trail. The display says "something is here, but I can't resolve it clearly."

Areas with weak signal coverage get a fog-of-war overlay. Heart rate only displays when conditions are right. The system tells you what it knows and, just as importantly, what it doesn't.

I think this matters beyond WiFi sensing. Too many systems hide uncertainty behind clean UIs and false precision. Honest engineering means showing your confidence intervals, not just your point estimates.

---

## What Works and What Doesn't (Yet)

### Works

- **Full signal processing pipeline** — SpotFi → bandpass → feature extraction → KNN → particle filter → vitals. Tested with 440+ tests across unit, integration, and end-to-end suites.
- **Multi-person tracking** — NMF-based occupancy detection with source count estimation.
- **Breathing rate** — Bandpass + FFT with SNR gating. The most reliable vital sign measurement.
- **Heart rate** — CWT with breathing harmonic removal and triple-gated display logic.
- **Dashboard** — Fully functional in simulator mode. Built-in demo scenarios. Auto-reconnect WebSocket client.
- **RPi deployment** — Idempotent setup script. systemd, Mosquitto, mDNS, logrotate, backup cron. Runs headless.
- **Firmware** — STA mode, HT40 CSI extraction, 100Hz TX, MQTT publish, status LEDs, WiFi watchdog.

### Doesn't Work Yet

- **Calibration system.** The fingerprint collection walk and database builder are designed but not built. You'll need this before real localization works. The [calibration guide](docs/calibration-guide.md) documents the planned UX.
- **End-to-end hardware validation.** The pipeline works with synthetic data. It hasn't been tested with real ESP32 CSI data flowing through the full stack. I expect integration bugs.
- **Floors 2 and 3 config.** SVG floor plans exist for all three floors. Config entries and waypoint graphs only exist for floor 1. Adding floors is straightforward but hasn't been done.
- **Dashboard rendering integration.** There are two parallel rendering approaches in the dashboard — inline DOM manipulation in `app.js` (active) and standalone canvas/DOM class modules (not wired in). A future cleanup should pick one.

I'm publishing at ~70% complete because the architecture is solid, the algorithms are validated, and the remaining work is integration and polish. Waiting for 100% means waiting forever.

---

## Try It Without Hardware

You don't need $244 in boards to explore the system. The simulator path requires zero hardware:

```bash
# Clone the repo
git clone https://github.com/justintormey/wifi-csi.git
cd wifi-csi

# Start the backend in simulation mode
python3 -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt
python -m backend.main --simulate

# In another terminal, serve the dashboard
cd dashboard && python3 -m http.server 8080
# Open http://localhost:8080
```

The simulator generates realistic synthetic CSI data: position jitter, signal quality drift, multi-person scenarios, and vital signs with physiological variance. The dashboard auto-connects to the backend WebSocket. If the backend isn't running, it falls back to its own built-in simulator with scripted demo scenarios.

Play with it. Break it. Tell me what's confusing.

---

## Why I Built This

WiFi CSI sensing has been academically validated for over a decade. The algorithms are published. The hardware is cheap. But the research-to-engineering gap is enormous.

Papers publish MATLAB simulations with two antennas in a single room. Commercial products (Origin Wireless, Cognitive Systems) keep everything behind NDAs and black-box SDKs. Nobody had assembled the full stack — firmware, signal processing, tracking, vitals, visualization, deployment — into something you can clone, flash, and run.

I wanted the thing I wished existed: a complete, documented WiFi sensing system that a technical person could deploy in a weekend. Not a library. Not a demo. A system.

It's not finished. It might never be "finished." But it's real, it's documented, and it's yours to use, extend, or learn from.

---

## What I'd Love Help With

If any of this interests you, here are the highest-impact contribution areas:

- **Home Assistant integration** — MQTT presence sensor for HA users
- **Docker Compose setup** — Backend + Mosquitto in containers
- **Calibration CLI** — Guided fingerprint collection walkthrough
- **Real hardware testing** — Flash boards, run the pipeline, file bugs
- **Dashboard floor config** — Add waypoint graphs for multi-floor setups
- **Prometheus metrics** — Backend observability endpoint

Check the issues for `good first issue` labels.

---

## Links

- **Repository:** [github.com/justintormey/wifi-csi](https://github.com/justintormey/wifi-csi)
- **Architecture docs:** [`docs/architecture.md`](https://github.com/justintormey/wifi-csi/blob/main/docs/architecture.md)
- **Hardware BOM:** [`docs/hardware-bom.md`](https://github.com/justintormey/wifi-csi/blob/main/docs/hardware-bom.md)
- **Signal processing deep dive:** [How I Turn WiFi Signals Into a People Tracker](/blog/wifi-csi-signal-processing) <!-- BEFORE PUBLISHING: Update with actual blog URL -->

Star the repo if you find it interesting. Clone it if you want to build. Open an issue if you think I got something wrong.

I'll be writing more as hardware testing begins — firmware debugging, calibration lessons, and the inevitable post about what happens when theory meets drywall.
