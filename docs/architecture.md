# Architecture & Algorithms

Technical reference for the WiFi CSI people tracking and vital signs monitoring system. Covers system architecture, data flow, signal processing algorithms, confidence scoring, performance characteristics, and known limitations.

**Audience:** Developers and researchers wanting to understand, extend, or replicate the system.

---

## System Architecture

```
                         ┌─────────────────────────────────┐
                         │          House WiFi AP           │
                         │     (2.4GHz, channels 1/6/11)   │
                         └──────┬────────────┬─────────────┘
                                │            │
              ┌─────────────────┘            └──────────────────┐
              │                                                 │
    ┌─────────┴──────────┐                          ┌───────────┴──────────┐
    │   ESP32-S3 (TX)    │  UDP unicast @ 100Hz     │   ESP32-S3 (RX) ×3  │
    │   STA mode         │ ──────────────────────── │   STA mode           │
    │   Central ceiling  │                          │   Walls/corners      │
    └────────────────────┘                          └───────────┬──────────┘
                                                                │
                                                     WiFi CSI callback
                                                     114 subcarriers (HT40)
                                                     I/Q complex values
                                                                │
                                                         MQTT publish
                                                     topic: csi/{floor}/{rx_mac}
                                                                │
                                                                ▼
    ┌───────────────────────────────────────────────────────────────────────┐
    │                        Raspberry Pi 4 (4GB)                          │
    │                                                                       │
    │  ┌──────────────┐    ┌────────────────┐    ┌───────────────────────┐  │
    │  │  Mosquitto    │───▶│  collector/     │───▶│  processor/           │  │
    │  │  MQTT Broker  │    │  mqtt_listener  │    │  phase_sanitizer      │  │
    │  └──────────────┘    │  csi_packet     │    │  amplitude_filter     │  │
    │                       └────────────────┘    │  subcarrier_selector  │  │
    │                                             │  feature_extractor    │  │
    │                                             └──────────┬────────────┘  │
    │                                                        │               │
    │                                        ┌───────────────┼────────────┐  │
    │                                        ▼               ▼            │  │
    │                                  ┌──────────┐   ┌────────────┐     │  │
    │                                  │ tracker/  │   │  vitals/   │     │  │
    │                                  │ KNN       │   │  breathing │     │  │
    │                                  │ particle  │   │  heartrate │     │  │
    │                                  │ filter    │   │  motion    │     │  │
    │                                  │ NMF       │   │  detector  │     │  │
    │                                  └─────┬─────┘   └──────┬─────┘     │  │
    │                                        └────────┬───────┘           │  │
    │                                                 ▼                   │  │
    │                                        ┌────────────────┐          │  │
    │                                        │  server/       │          │  │
    │                                        │  FastAPI       │          │  │
    │                                        │  WebSocket     │          │  │
    │                                        │  @ 10Hz        │          │  │
    │                                        └───────┬────────┘          │  │
    └────────────────────────────────────────────────┼──────────────────┘  │
                                                     │                     │
                                                     ▼
                                            ┌────────────────┐
                                            │  Browser       │
                                            │  Dashboard     │
                                            │  (sci-fi HUD)  │
                                            └────────────────┘
```

### Hardware Layout (Per Floor)

Each floor has 4 ESP32-S3 boards on a dedicated WiFi channel:

| Board | Role | Placement | Channel |
|-------|------|-----------|---------|
| TX | Sends UDP frames at 100Hz | Central ceiling | Floor-specific (1, 6, or 11) |
| RX #1 | Extracts CSI from TX frames | NW corner wall | Same as TX |
| RX #2 | Extracts CSI from TX frames | NE corner wall | Same as TX |
| RX #3 | Extracts CSI from TX frames | South wall center | Same as TX |

All boards connect to house WiFi as **stations (STA mode)** — not promiscuous mode. This is a critical architectural decision: promiscuous mode is mutually exclusive with maintaining a station connection on single-band ESP32-S3 hardware. STA mode allows each board to both send/receive CSI frames and use the house WiFi for MQTT backhaul.

Floors use non-overlapping 2.4GHz channels (1, 6, 11) to prevent inter-floor interference. Cross-floor attenuation (~10-15 dB/floor) enables floor discrimination.

### Cost

| Phase | Hardware | Cost |
|-------|----------|------|
| Phase 1 (single floor) | 4x ESP32-S3 + 1x RPi 4 + power/cables | ~$140 |
| Phase 2 (full house) | +8x ESP32-S3 + power/cables | ~$104 additional |

---

## Data Flow

### 1. CSI Extraction (Firmware)

The TX board sends UDP unicast packets at 100Hz. Each RX board registers an `esp_wifi_set_csi_rx_cb()` callback that fires on every received frame, providing:

- **114 subcarriers** (HT40 mode) of complex I/Q values
- RSSI, timestamp, TX/RX MAC addresses
- Channel and noise floor

The callback serializes I/Q data into a compact binary packet and publishes via MQTT.

**MQTT topic:** `csi/{floor_id}/{rx_mac}`
**Payload:** `timestamp | tx_mac | rx_mac | rssi | 114×(I, Q)` (binary)

### 2. Collection (Backend)

`collector/mqtt_listener.py` subscribes to `csi/#` and deserializes binary packets into `CsiPacket` dataclass instances. Each packet contains:

- 114 complex values (I + jQ per subcarrier)
- Converted to amplitude: `√(I² + Q²)` per subcarrier
- Converted to phase: `atan2(Q, I)` per subcarrier

### 3. Signal Processing (Backend)

The processor pipeline runs on every incoming CSI frame:

```
Raw I/Q (114 subcarriers)
    │
    ▼
Phase Sanitization (SpotFi)
    │
    ▼
Amplitude Extraction (rotational projection)
    │
    ▼
Outlier Rejection (Hampel filter)
    │
    ▼
Subcarrier Selection
    ├── Tracking: top 35 by variance
    └── Vitals: top 15 by in-band SNR
    │
    ▼
Bandpass Filtering (Butterworth order 4, SOS)
    ├── Breathing: 0.1–0.5 Hz
    └── Heart rate: 0.8–2.0 Hz
```

### 4. Tracking & Vitals (Backend)

Processed CSI feeds two parallel subsystems:

- **Tracker:** Fingerprint KNN → particle filter → position + confidence
- **Vitals:** FFT (breathing) and CWT (heart rate) → rates + confidence

Both produce outputs merged into a single WebSocket payload.

### 5. API & Dashboard

`server/app.py` (FastAPI) broadcasts merged tracking + vitals data over WebSocket at 10Hz. The browser dashboard renders a sci-fi HUD with confidence-driven visualization.

**End-to-end latency:** ~30-50ms on local network.

---

## Algorithms

### SpotFi Phase Sanitization

**Purpose:** Remove Sampling Time Offset (STO) and Sampling Frequency Offset (SFO) artifacts from raw CSI phase.

**Method:** Fit a linear model across all 114 subcarriers and subtract:

```
φ_raw[k] = φ_true[k] + a·k + b     (per-packet linear offset)
φ_sanitized[k] = φ_raw[k] - (a·k + b)
```

Where `k` is the subcarrier index, `a` captures SFO, and `b` captures STO. Solved via ordinary least squares on each CSI frame independently.

**Why SpotFi:** It's computationally trivial and well-validated for commodity hardware. The alternatives (TSFR, D-MUSIC, hardware-synchronized arrays like ESPARGOS) solve problems we don't have — we aren't doing Angle-of-Arrival estimation, so SpotFi's known limitation with Cyclic Shift Diversity doesn't affect us.

**Module:** `processor/phase_sanitizer.py`

**Reference:** Kotaru et al., "SpotFi: Decimeter Level Localization Using WiFi," ACM SIGCOMM 2015.

---

### Rotational Projection (I/Q Fusion)

**Purpose:** Extract more vital-sign information than amplitude alone by fusing amplitude and phase.

**Method:** Instead of using `amplitude = √(I² + Q²)`, project the complex CSI vector onto a data-driven axis:

```python
angle = arctan2(mean(Q), mean(I))
projected = I * cos(angle) + Q * sin(angle)
```

This captures both amplitude and phase variations caused by chest displacement, yielding ~10-15% improvement in vital sign SNR over amplitude-only extraction.

**Module:** `processor/amplitude_filter.py`

**Reference:** Kim et al., "Human Daily Breathing Monitoring Using WiFi CSI I/Q Plane," Sensors 2024.

---

### Butterworth Bandpass Filtering

**Purpose:** Isolate frequency bands of interest from the CSI time series.

**Parameters:**

| Target | Passband | Order | Effective Order | Notes |
|--------|----------|-------|-----------------|-------|
| Breathing | 0.1–0.5 Hz | 4 | 8 (bandpass doubles) | ~48 dB/octave roll-off |
| Heart rate | 0.8–2.0 Hz | 4 | 8 | After breathing harmonic removal |

**Why order 4, not higher:**
- Order 6+ introduces group delay artifacts that corrupt FFT peak detection in 30-second analysis windows
- Order 4 provides adequate stopband rejection for our noise sources
- Higher orders risk numerical instability with float32 arithmetic — mitigated by using SOS (second-order sections) form regardless, but order 4 stays well clear of stability issues

**Implementation:** Always use `scipy.signal.sosfilt` (SOS form), never `lfilter` (direct form). SOS cascades second-order sections and is numerically stable for order 4+.

**Module:** `processor/amplitude_filter.py`

---

### Hampel Outlier Filter

**Purpose:** Reject impulsive noise from packet collisions, WiFi interference, and multipath bursts without distorting the underlying signal.

**Method:** Sliding window MAD (Median Absolute Deviation) filter. For each sample, compute the median and MAD of the surrounding window. Replace the sample with the window median if it deviates by more than `n_sigma × MAD`:

```
MAD = 1.4826 × median(|x_window - median(x_window)|)
if |x[i] - median| > 3.0 × MAD → replace x[i] with median
```

**Parameters:**

| Context | Window Size | n_sigma | Rationale |
|---------|-------------|---------|-----------|
| Breathing preprocessing | 7 (70ms at 100Hz) | 3.0 | Catches packet-loss spikes without smearing ~5-10s breathing cycles |
| Heart rate preprocessing | 5 (50ms at 100Hz) | 3.0 | Shorter window for faster heartbeat signal |

**Why Hampel over alternatives:** IQR clipping is too aggressive on breathing peaks. Z-score fails on non-Gaussian CSI noise. Median filtering blurs breathing cycles. Hampel preserves periodic signal structure while removing impulse artifacts.

**Performance:** Run after subcarrier selection (on 35 selected subcarriers, not all 114) to reduce compute by ~3×.

**Module:** `processor/amplitude_filter.py`

---

### Subcarrier Selection

**Purpose:** Reduce dimensionality from 114 subcarriers to the most informative subset. Different tasks need different subcarriers.

#### For Localization (Tracking)

Select the **top 35 subcarriers by temporal variance**. High-variance subcarriers respond most strongly to human movement, making them ideal for fingerprint matching.

A stability filter excludes subcarriers that are "always noisy" — those where variance during calibration exceeds some threshold even in an empty room.

**Module:** `processor/subcarrier_selector.py` → `select_for_tracking(csi_matrix, k=35)`

#### For Vital Signs

Select the **top 15 subcarriers by in-band SNR** — the ratio of bandpass-filtered power to total power. High-variance subcarriers are often dominated by environmental drift and macro-motion, making them poor for vital signs.

```
SNR[k] = power_inband[k] / (power_total[k] - power_inband[k])
```

This separation is critical: using the same subcarriers for both tracking and vitals degrades vital sign accuracy by ~20%.

**Module:** `processor/subcarrier_selector.py` → `select_for_vitals(csi_matrix, band, k=15)`

**Reference:** Park et al., "Non-Contact Heart Rate Monitoring via WiFi CSI," Sensors 2024 (HSR method).

---

### Fingerprint KNN Localization

**Purpose:** Estimate a person's (x, y) position by comparing live CSI to a pre-calibrated fingerprint database.

**Calibration:** A user walks a 1-meter grid across the floor (~350 points for one floor, ~17 minutes at 3 seconds per point). At each grid point, the system records averaged CSI amplitude vectors from all 3 RX boards, creating a fingerprint — a feature vector associated with a known position.

**Online localization:**
1. Compute the live CSI feature vector (top-35 subcarrier amplitudes across all RX boards)
2. Find the K=5 nearest fingerprints using **cosine distance**
3. Estimate position as the **distance-weighted average** of the 5 nearest fingerprint positions

```
position = Σ (w_i × pos_i) / Σ w_i
w_i = 1 / (distance_i + ε)
```

**Accuracy:** 1–1.5 meters typical with 1m calibration grid. Degrades near walls, metal objects, and in areas with fewer RX line-of-sight paths.

**Module:** `tracker/localization.py`, `tracker/fingerprint_db.py`

---

### Particle Filter Tracking

**Purpose:** Smooth raw KNN position estimates into coherent trajectories and provide a measure of position uncertainty.

**Configuration:**
- 200 particles
- Velocity-constrained random walk motion model
- Observation model: likelihood weighted by KNN match quality

**Process (per update cycle):**
1. **Predict:** Move each particle according to a random walk, constrained by maximum human walking speed
2. **Update:** Weight each particle by the likelihood of the current CSI observation given the particle's position (using the KNN distance metric)
3. **Resample:** Draw new particles proportional to weights (systematic resampling)
4. **Estimate:** Weighted mean of particle positions = estimated position; spatial spread of particles = uncertainty

The particle spread directly feeds the **position confidence** score — tightly clustered particles indicate high confidence, scattered particles indicate ambiguity.

**Module:** `tracker/particle_filter.py`

---

### Floor Detection

**Purpose:** Determine which floor a person is on in the multi-floor deployment.

**Method:** Compare CSI energy variance from each floor's TX board. The floor whose TX signal shows the strongest perturbation (highest variance in amplitude) is most likely the floor where the person is located, because:
- Same-floor signals are attenuated less and perturbed more by nearby human bodies
- Cross-floor signals are attenuated ~12 dB per floor (configurable in `house.yaml`), reducing perturbation magnitude

**Algorithm detail (`FloorDetector`):**

1. **Per-floor energy computation.** For each floor's RX boards, compute the rolling variance of CSI amplitude over a short window. This captures how much the signal is being disturbed by nearby bodies.
2. **Floor ranking.** Rank floors by perturbation strength. The floor with the highest CSI energy variance is the candidate floor.
3. **Hysteresis.** To prevent noisy floor flips from momentary signal fluctuations, the detector requires **3 consecutive frames** agreeing on a new floor before switching. This means brief signal anomalies (e.g., a door opening that temporarily changes multipath) don't trigger false floor changes.
4. **Transition zone relaxation.** When the tracker's position estimate falls within a stairwell transition zone (defined in `house.yaml`), hysteresis drops to **1 frame**, allowing rapid floor transitions as a person walks between floors.
5. **Confidence scoring.** The floor detector outputs a confidence value based on the energy ratio between the top-ranked floor and the second-ranked floor. A large ratio (e.g., 10:1) produces high confidence; a close ratio (e.g., 2:1) produces low confidence, indicating the person may be between floors.

**Transition zones** are axis-aligned bounding boxes in `house.yaml`:

```yaml
transition_zones:
  - name: "Main Stairwell (1st→2nd)"
    floors: [1, 2]
    x_min: 4.0
    x_max: 6.5
    y_min: 3.5
    y_max: 6.5
```

Each zone connects exactly two floors. A person entering the zone on Floor 1 can transition to Floor 2 (and vice versa) with minimal hysteresis delay.

**Cross-floor tracking:** The backend maintains independent `FloorPipeline` instances for each floor. When the floor detector identifies a floor change, the person's tracking state (particle filter, vitals history) is handed off from the source floor's pipeline to the destination floor's pipeline. The particle filter is re-initialized near the transition zone exit on the new floor, preserving velocity estimates but resetting position particles to the stairwell area.

**Module:** `tracker/floor_detector.py`

---

### NMF Occupancy Detection

**Purpose:** Estimate the number of people in a space when positions overlap or cannot be cleanly separated.

**Method:** Non-negative Matrix Factorization (NMF) decomposes the combined CSI variance matrix into component sources. The number of significant components indicates the occupancy count.

**Output:**
- `occupancy_estimate`: integer count of detected people
- `occupancy_confidence`: 0.0–1.0 score

**Limitations:**
- Reliable for 1-2 people in different rooms
- Degrades when people are within ~2 meters (confidence drops ~15% per proximate pair)
- When ambiguous, the dashboard shows overlapping fuzzy blobs rather than discrete tracking dots

**Module:** `tracker/occupancy.py`

---

### Breathing Rate Extraction

**Purpose:** Measure breathing rate (breaths per minute) from CSI amplitude variations caused by chest displacement (~1-5mm).

**Pipeline:**
1. Select top 15 subcarriers by in-band SNR (0.1–0.5 Hz band)
2. Apply Hampel filter (window=7) to reject impulse noise
3. Butterworth bandpass 0.1–0.5 Hz (order 4, SOS form)
4. Compute FFT on a **30-second sliding window**
5. Peak frequency × 60 = breaths per minute

**Accuracy:** ±1-2 BPM when person is stationary. Degrades significantly during movement.

**Confidence gating:** `breathing_confidence` is derived from motion level and SNR. Displayed whenever a person is detected and reasonably still.

**Module:** `vitals/breathing.py`, `vitals/windowed_fft.py`

---

### Heart Rate Extraction

**Purpose:** Measure heart rate (BPM) from CSI amplitude variations caused by cardiac-induced body displacement (~0.1mm). This is experimental — WiFi CSI heart rate is unreliable in real home conditions.

**Pipeline:**
1. Select top 15 subcarriers by in-band SNR (0.8–2.0 Hz band)
2. Apply Hampel filter (window=5) to reject impulse noise
3. Remove breathing harmonics from the signal
4. Butterworth bandpass 0.8–2.0 Hz (order 4, SOS form)
5. Continuous Wavelet Transform (CWT) using **Morlet wavelet**
6. Peak frequency in the 0.8–2.0 Hz band × 60 = heart rate BPM

**Why CWT over FFT:** The Morlet CWT provides better time-frequency resolution for the narrow heart rate band, handling the non-stationarity of heartbeat intervals better than a fixed-window FFT.

**Display conditions (ALL must be true):**
1. Person detected with `position_confidence > 0.6`
2. Person stationary for > 30 continuous seconds
3. Heart rate band SNR exceeds threshold
4. If any condition fails → heart rate is hidden entirely (no stale readings)

**Accuracy:** ±8-10 BPM, ~50-60% usable readings in home conditions.

**Module:** `vitals/heartrate.py`, `vitals/motion_detector.py`, `vitals/windowed_fft.py`

**Reference:** Park et al., "Non-Contact Heart Rate Monitoring via WiFi CSI," Sensors 2024.

---

## Confidence Score Computation

Every tracked person has a `position_confidence` score (0.0–1.0) derived from three components:

### Components

| Component | Source | Weight | Meaning |
|-----------|--------|--------|---------|
| KNN match quality | Cosine similarity to nearest fingerprint | ~40% | How well current CSI matches calibration data |
| Particle filter convergence | Spatial spread of 200-particle cloud | ~35% | How certain the tracker is about position |
| CSI signal quality | Per-zone SNR and subcarrier variance stability | ~25% | Environmental signal conditions |

### Derived Metrics

- **`uncertainty_radius_m`**: Proportional to particle cloud spread. At high confidence (>0.8), ~1m; at low confidence (<0.4), ~5m+.
- **`zone_signal_quality`**: Per-room aggregate of CSI SNR. Drives the "fog of war" noise overlay on the dashboard — well-covered zones appear crisp, poorly-covered zones fade to grey.

### Vital Signs Confidence

- **`breathing_confidence`**: Gated by motion level and signal quality. Higher when person is still and in a well-covered zone.
- **`heartrate_confidence`**: Ramps up with stationary duration, capped by zone signal quality. Requires all display conditions to be met before any value is shown.

---

## WebSocket Payload Schema

The backend broadcasts at 10Hz over `ws://{host}/ws/tracking`:

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

- `heartrate.display` is `true` only when all display conditions are met. Clients must hide heart rate when `false`.
- `zone_signal_quality` drives per-zone noise visualization on the dashboard.

---

## Performance Characteristics

| Metric | Value | Notes |
|--------|-------|-------|
| CSI sample rate | 100 Hz | Per RX board; 3 RX = 300 packets/s per floor |
| Subcarriers per frame | 114 | HT40 mode |
| End-to-end latency | ~30-50 ms | CSI frame → dashboard update (LAN) |
| WebSocket broadcast rate | 10 Hz | Merged tracking + vitals payload |
| Localization accuracy | 1–1.5 m | With 1m calibration grid |
| Breathing accuracy | ±1-2 BPM | When stationary |
| Heart rate accuracy | ±8-10 BPM | When stationary >30s, ~50-60% usable readings |
| Particle filter | 200 particles | Velocity-constrained random walk |
| FFT analysis window | 30 seconds | Sliding window for breathing |
| Calibration time | ~17 min/floor | 1m grid, 3s/point, ~350 points |
| Fingerprint DB size | ~350 entries/floor | One per grid point |

### Compute Budget (RPi 4)

The full pipeline runs on a Raspberry Pi 4 (4GB). Key compute stages:

- Phase sanitization: trivial (linear regression per frame)
- Hampel filter: ~11M ops/s across 35 subcarriers (well within budget)
- Butterworth bandpass: negligible (SOS IIR filter)
- KNN (K=5, 35-dim, 350 entries): < 1ms per query
- Particle filter (200 particles): < 1ms per update
- FFT/CWT on 30s window: periodic, not per-frame

No GPU required. No deep learning in v1 — the DSP pipeline stays within RPi 4 CPU budget.

---

## Known Limitations

| Limitation | Detail | Mitigation |
|-----------|--------|-----------|
| Multi-person accuracy | Reliable for 1-2 people in different rooms; degrades with proximity (<2m) | Overlapping fuzzy blobs; NMF confidence score; occupancy estimate instead of precise count |
| Heart rate reliability | ~50-60% usable readings in real homes | Conditional display only; hidden when unreliable; never shown prominently |
| Localization accuracy | 1-1.5m typical; walls and metal create dead zones | Confidence radius visualization; zone quality overlay |
| Re-calibration required | After significant furniture rearrangement | Zone-recalibrate mode for partial updates |
| No identity tracking | Cannot distinguish who is who — positions are anonymous | By design: privacy-preserving |
| WiFi interference | 2.4GHz congestion degrades CSI quality | Dedicate 2.4GHz band to CSI boards; move other devices to 5GHz |
| Environmental sensitivity | Temperature, humidity, and door/window state affect CSI baseline | Periodic recalibration; Hampel filter handles transients |
| Single-person vitals only | Cannot separate breathing/heart rate signals from multiple people in the same zone | Only report vitals when occupancy_estimate = 1 for the zone |

---

## References

1. Kotaru, M. et al. "SpotFi: Decimeter Level Localization Using WiFi." ACM SIGCOMM, 2015.
2. Park, J. et al. "Non-Contact Heart Rate Monitoring via WiFi Channel State Information." Sensors, 2024. DOI: 10.3390/s24xxxxxx
3. Kim, S. et al. "Human Daily Breathing Monitoring Using WiFi CSI I/Q Plane." Sensors, 2024. DOI: 10.3390/s24227352
4. Tsinghua University. "CSI Sanitization Tutorial." tns.thss.tsinghua.edu.cn/wst/docs/sanitization/
5. "Optimal Preprocessing of WiFi CSI for Sensing Applications." IEEE, 2024. DOI: 10.1109/JIOT.2024.xxxxxxx
6. "Commodity WiFi Sensing: Challenges and Opportunities — A 5-Year Review." PMC, 2024.
7. "AI-Enhanced CSI Vital Signs Survey." PeerJ Computer Science, 2025.
8. "Novel Hampel Filter Speedup for IoT Time Series." PMC, 2025.
9. "Wi-Fi Sensing: Applications and Challenges — A Comprehensive Survey." IEEE COMST, 2022.
