# Before & After: Raw CSI → Beautiful Dashboard

A visual narrative showing the transformation from raw electromagnetic noise to actionable human-readable output. Use this as a portfolio visual, blog supplement, or presentation slide sequence.

---

## The Transformation Pipeline

### STAGE 1: Raw CSI Data

**What it looks like:** A wall of numbers. 114 complex values (I/Q pairs) arriving 100 times per second. Completely unreadable.

```
Raw CSI Frame (478 bytes, binary):
02 03 FF FE 12 34 00 8A C0 A8 01 65 00 00 00 01
F4 FF 03 00 E8 FF 05 00 DA FF 08 00 CC FF 0B 00
BE FF 0E 00 B0 FF 11 00 A2 FF 14 00 94 FF 17 00
...
(114 subcarriers × I,Q × 2 bytes = 456 bytes of CSI data)
```

**The problem:** This is what the ESP32-S3 hardware gives you. A stream of bytes encoding how 114 frequency channels were distorted by the environment. Somewhere in this noise is the position of every person in the room, their breathing rate, and maybe their heart rate. But it's buried under clock drift, interference spikes, and hardware artifacts.

**Human interpretation:** Nothing. Completely opaque.

---

### STAGE 2: Phase-Sanitized Amplitude

**What happened:** SpotFi removed clock artifacts. Hampel filter rejected outlier spikes. Amplitudes extracted from I/Q pairs.

```
Subcarrier amplitudes (114 values, cleaned):
[23.4, 21.8, 22.1, 19.7, 24.3, 25.1, 18.9, 20.4, 22.7, 23.0, ...]

Phase (114 values, sanitized):
[0.42, 0.38, 0.45, 0.31, 0.52, 0.48, 0.29, 0.41, 0.44, 0.39, ...]
```

**What you could see (if plotted):** A jagged line graph across 114 subcarriers. Some subcarriers have high amplitude (clear signal paths), others are attenuated (blocked or reflected). The pattern shifts when a person moves.

**Human interpretation:** "Something is happening on subcarriers 20-45 — they're fluctuating more than the others." Still not actionable.

---

### STAGE 3: Subcarrier Selection + Feature Extraction

**What happened:** Top 35 subcarriers selected by temporal variance (for tracking). Top 15 selected by in-band SNR (for vitals). Feature vectors computed: mean amplitude, variance, mean phase, std phase → 140 dimensions for tracking.

```
Tracking feature vector (140D):
[0.82, 0.15, 0.43, 0.08, 0.91, 0.22, 0.38, 0.12, ...]

Vital sign subcarriers (15 channels, bandpass filtered):
Breathing band (0.1-0.5 Hz): Periodic signal visible
Heart rate band (0.8-2.0 Hz): Faint signal, noisy
```

**What you could see (if plotted):** The breathing-band signal now shows a clean sinusoidal wave — each peak is one breath. The heart rate band is noisier but shows periodic structure.

**Human interpretation:** "There's a periodic signal at about 0.25 Hz — that's 15 breaths per minute." Getting warmer, but still requires expertise to read.

---

### STAGE 4: KNN Localization + Particle Filter

**What happened:** The 140D feature vector is compared against 350 calibration fingerprints using cosine similarity. Top 5 neighbors → softmax-weighted position estimate. Particle filter (200 particles) smooths the trajectory and quantifies uncertainty.

```
KNN estimate: (4.2m, 6.8m) — kitchen area, confidence 0.73
Particle filter: (4.1m, 6.7m) — smoothed, spread = 0.8m
Convergence score: 0.81 (good)
```

**What you could see:** A point on a floor plan, surrounded by a cloud of particles. The cloud is tight — high confidence. If the person were in a dead zone, the cloud would scatter.

**Human interpretation:** "Someone is in the kitchen. The system is fairly sure." Now we're getting somewhere.

---

### STAGE 5: Vital Signs Extraction

**What happened:** FFT on 30-second breathing window → peak at 0.25 Hz (15 BPM), SNR 12 dB, spectral concentration 42%. CWT on heart rate window → peak at 1.2 Hz (72 BPM), but SNR only 4 dB with person stationary for 45 seconds.

```
Breathing: 15 BPM, confidence 0.87 ✓ Display
Heart rate: 72 BPM, confidence 0.52
  → Position confidence: 0.81 ✓
  → Stationary 45s: ✓
  → SNR 4 dB ≥ 3 dB: ✓
  → All gates pass → Display (with low confidence indicator)
```

**Human interpretation:** Clear breathing rate. Heart rate displayed but marked as less certain.

---

### STAGE 6: The Dashboard

**What you see:** A dark sci-fi HUD showing a floor plan of the house. A bright cyan dot in the kitchen with a tight glow ring. Trail shows movement path over the last 30 seconds. Side panel shows:

```
┌─────────────────────────┐
│  OCCUPANCY: 2 detected  │
│                         │
│  Person 1 — Kitchen     │
│  🫁 15 BPM              │
│  ❤️ 72 BPM (low conf)   │
│                         │
│  Person 2 — Living Room │
│  🫁 18 BPM              │
│  ❤️ — (moving)          │
│                         │
│  Signal Quality: ████░  │
│  Latency: 34ms          │
└─────────────────────────┘
```

**Human interpretation:** Instant. Glanceable. Anyone can read it. Two people at home. One in the kitchen, breathing normally, heart rate visible. One in the living room, moving (so no heart rate shown). System is confident and responsive.

---

## The Journey in One Line

```
478 bytes of electromagnetic noise
        ↓
"Two people at home. One in the kitchen, breathing 15 times a minute."
```

---

## Visual Assets Needed

To create the actual before/after visual for portfolio/presentations:

1. **Raw data panel:** Screenshot or mockup of hex dump / binary stream (chaotic, overwhelming)
2. **Amplitude plot:** Line chart of 114 subcarrier amplitudes (technical but still noisy)
3. **Breathing waveform:** Clean sinusoidal signal after bandpass filtering (the "aha" moment)
4. **Particle cloud:** Scatter plot of 200 particles converging on a floor plan location
5. **Dashboard screenshot:** Full HUD in simulator mode with 2+ people tracked, vitals visible

**Layout suggestion:** Horizontal strip or diagonal cascade, left-to-right, raw → processed → visualized. Dark background matching the dashboard theme. Each stage labeled with the algorithm applied.

**Alternative:** Split-screen. Left half: terminal showing scrolling hex data. Right half: dashboard with tracking dots and vitals. Caption: "Same data. Different presentation."
