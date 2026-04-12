# How I Turn WiFi Signals Into a People Tracker: A Signal Processing Deep Dive

Your WiFi router transmits across 114 frequency subcarriers. Each one takes a different path through your house — bouncing off walls, diffracting around doors, and passing through your body. When that signal arrives at a receiver, it carries a fingerprint of everything it touched along the way. That fingerprint is called Channel State Information (CSI), and it changes when you move, when you breathe, and — if you squint hard enough at the math — when your heart beats.

I'm building an open-source system that extracts all of this from commodity $8 ESP32-S3 boards. This post walks through the signal processing pipeline: how raw I/Q samples become room-level position estimates and breathing rate measurements running on a Raspberry Pi.

---

## What CSI Actually Looks Like

Every WiFi frame carries training symbols — known signal patterns that the receiver uses to estimate the channel. The ESP32-S3's `esp_wifi_set_csi_rx_cb()` callback gives you 114 complex values (HT40 mode), one per subcarrier, each represented as an in-phase (I) and quadrature (Q) component.

From each I/Q pair you extract two things:

```
amplitude[k] = √(I[k]² + Q[k]²)
phase[k]     = atan2(Q[k], I[k])
```

At 100 Hz sample rate across 3 receivers per floor, that's 300 packets per second — each one a 114-dimensional snapshot of the electromagnetic environment.

The amplitude tells you *how much* the signal was attenuated on each subcarrier. The phase tells you *how far* it traveled. A person walking through the room shifts both: amplitude drops on subcarriers whose paths they block, and phase rotates as the dominant reflection path length changes.

Here's the critical intuition: **a person standing still also changes CSI** — their chest displaces 1-5mm with each breath, and their heartbeat moves the body surface by ~0.1mm. Both are periodic signals riding on top of the CSI measurements, buried in noise.

---

## Step 1: Phase Sanitization (SpotFi)

Raw CSI phase is almost useless. The ESP32's transmitter and receiver clocks aren't synchronized, introducing two artifacts:

- **Sampling Frequency Offset (SFO):** A linear phase slope across subcarriers
- **Sampling Time Offset (STO):** A constant phase offset

These dwarf the actual environmental phase information by orders of magnitude. The fix is elegantly simple — fit a line and subtract it:

```
φ_raw[k] = φ_true[k] + a·k + b

# Unwrap phase to handle 2π discontinuities
φ_unwrapped = numpy.unwrap(φ_raw)

# Least-squares fit: find a, b
a, b = polyfit(subcarrier_indices, φ_unwrapped, degree=1)

# Subtract the linear component
φ_sanitized[k] = φ_unwrapped[k] - (a·k + b)
```

This is the SpotFi algorithm from Kotaru et al. (SIGCOMM 2015). It's computationally trivial — one linear regression per CSI frame — and well-validated on commodity hardware. More sophisticated alternatives exist (TSFR, D-MUSIC, synchronized antenna arrays like ESPARGOS), but they solve problems we don't have. We're not doing Angle-of-Arrival estimation, so SpotFi's known limitations with Cyclic Shift Diversity don't affect us.

The result: phase measurements that actually reflect the physical environment rather than clock drift.

---

## Step 2: Amplitude Extraction and Outlier Rejection

### Rotational Projection

Instead of using raw amplitude `√(I² + Q²)`, we project the complex CSI onto a data-driven axis that captures both amplitude and phase variations:

```python
angle = arctan2(mean(Q), mean(I))
projected = I * cos(angle) + Q * sin(angle)
```

This fuses amplitude and phase information into a single scalar per subcarrier, yielding ~10-15% improvement in vital sign SNR over amplitude alone. The idea comes from Kim et al. (Sensors 2024) — they showed that breathing-induced chest displacement modulates both the magnitude and angle of the CSI vector, and projecting onto the principal axis captures both effects.

### Hampel Outlier Filter

WiFi is a noisy medium. Packet collisions, microwave ovens, and Bluetooth interference create impulse spikes in CSI that would wreck any downstream frequency analysis. We need to remove these spikes without smearing the underlying signal.

The Hampel filter uses a sliding window of Median Absolute Deviation (MAD) to detect outliers:

```
For each sample x[i]:
    window = x[i-w : i+w]
    med = median(window)
    MAD = 1.4826 × median(|window - med|)
    if |x[i] - med| > 3.0 × MAD:
        x[i] = med  # replace with local median
```

The `1.4826` constant converts MAD to standard deviation for normally distributed data. We use a window of 7 samples (70ms at 100Hz) for breathing preprocessing and 5 samples (50ms) for heart rate — shorter windows give faster transient response for the higher-frequency heartbeat signal.

**Why Hampel over alternatives?** We tested three approaches:

- **IQR clipping:** Too aggressive — clips actual breathing peaks
- **Z-score:** Assumes Gaussian noise, which CSI isn't
- **Median filter:** Blurs breathing cycles at typical filter lengths

Hampel preserves periodic signal structure while cleanly removing impulse artifacts. It's the right tool for this noise profile.

---

## Step 3: Picking the Right Subcarriers

Not all 114 subcarriers are equal. Some are highly responsive to human motion. Others are dominated by static multipath or hardware noise. And critically, **the best subcarriers for tracking are different from the best ones for vital signs.**

### For Tracking: Top 35 by Variance

High temporal variance means a subcarrier responds strongly to changes in the environment — i.e., people moving. We compute variance over a 1-second sliding window (100 samples at 100Hz) and keep the top 35. This reduces dimensionality by ~70% while preserving the most motion-sensitive channels.

### For Vital Signs: Top 15 by In-Band SNR

High-variance subcarriers are often dominated by environmental drift, HVAC vibrations, and macro-motion artifacts — exactly what you don't want when looking for a 0.1mm heartbeat signal. Instead, we select based on in-band SNR:

```
SNR[k] = power_inband[k] / (power_total[k] - power_inband[k])
```

Where `power_inband` is computed within the breathing band (0.1–0.5 Hz) or heart rate band (0.8–2.0 Hz). This finds subcarriers where the signal of interest is strongest relative to everything else.

**This separation matters.** Using the same subcarriers for both tracking and vitals degrades vital sign accuracy by ~20%. It's not a minor optimization — it's the difference between detecting a breathing signal and not.

---

## Step 4: Bandpass Filtering

With outliers removed and subcarriers selected, we isolate the frequency bands of interest using Butterworth bandpass filters:

| Target | Passband | Physiological Range |
|--------|----------|---------------------|
| Movement | 0.5–5.0 Hz | Walking, gestures |
| Breathing | 0.1–0.5 Hz | 6–30 breaths/min |
| Heart rate | 0.8–2.0 Hz | 48–120 BPM |

We use order 4 filters implemented in SOS (second-order sections) form. Bidirectional application via `sosfiltfilt` gives zero phase distortion and an effective order of 8 (~48 dB/octave roll-off).

**Why not higher order?** We tried order 6 early on. It introduced group delay artifacts that corrupted FFT peak detection in our 30-second analysis windows. Order 4 provides adequate stopband rejection without numerical stability concerns — though we use SOS form regardless, because `lfilter` (transfer function form) is a landmine waiting to go off at order 4+.

---

## Step 5: Localization — Where Are You?

### Fingerprint Database

Position estimation uses fingerprint-based KNN. During a one-time calibration (~17 minutes per floor), a user walks a 1-meter grid across the floor. At each of ~350 grid points, the system records an averaged CSI feature vector — a 140-dimensional vector built from the top 35 subcarriers:

```
feature = [mean_amp | var_amp | mean_phase | std_phase]
         = 4 × 35 = 140 dimensions
```

Each feature vector is L2-normalized for cosine distance comparison. The calibration produces a `.npz` file per floor mapping positions to their fingerprints.

### Weighted KNN

At runtime, the system computes the live feature vector and finds the K=5 nearest neighbors in the fingerprint database using cosine similarity:

```
similarity = (fingerprints @ query) / (||fingerprints|| × ||query||)
distance = 1.0 - similarity
```

Position is estimated as a softmax-weighted average of the nearest neighbors:

```
weights = softmax(similarities / temperature)   # temperature = 5.0
position = Σ(weight_i × position_i)
```

The softmax temperature controls how sharply the closest match dominates. At temperature 5.0, a neighbor with 0.9 similarity gets roughly 2× the weight of one with 0.7 — enough to pull toward the best match without ignoring the others entirely.

**Accuracy:** 1–1.5 meters with a 1-meter calibration grid. Good enough to know which room someone is in. Not precise enough to know which chair.

---

## Step 6: Particle Filter — Smoothing the Noise

Raw KNN estimates jump around. A person standing still might get placed in the kitchen one frame and the living room the next, because CSI is noisy and fingerprint matching is probabilistic. The particle filter turns these noisy point estimates into smooth, physically plausible trajectories.

### The Setup

200 particles, each representing a hypothesis about where the person is and how fast they're moving. Each particle has state `(x, y, vx, vy)`.

### Predict

Each particle moves according to a velocity-constrained random walk:

```
v_new = v_old + N(0, σ=0.3 m/s)     # add Gaussian noise
||v_new|| = min(||v_new||, 1.5 m/s)  # clamp to max walking speed
p_new = p_old + v_new × dt
```

Positions are clamped to floor boundaries — if a particle hits a wall, its velocity component normal to the wall is zeroed.

### Update

Each particle is weighted by how well it matches the KNN observation:

```
likelihood_i = exp(-dist_i² / (2σ²))
```

Where `dist_i` is the distance between particle `i` and the KNN position estimate, and `σ = max(uncertainty_radius, 0.5m)`. Particles near the observation get high weight; distant particles get low weight.

### Resample

When the effective sample size drops below 50% (i.e., a few particles dominate the weight distribution), we resample using low-variance systematic resampling. This prunes unlikely particles and duplicates promising ones, preventing particle depletion without introducing unnecessary jitter.

The effective sample size:

```
N_eff = 1 / Σ(w_i²)
```

If all 200 particles have equal weight: `N_eff = 200`. If one particle has all the weight: `N_eff = 1`. Resampling triggers at `N_eff < 100`.

### Output

The position estimate is the weighted mean of all particles. The convergence score — an exponential decay function of the particle cloud's spatial spread — feeds directly into the dashboard's confidence visualization. Tight cluster = bright, sharp tracking dot. Scattered cloud = fuzzy, ghostly blob.

---

## Step 7: Breathing — The Easy Vital Sign

Breathing is the approachable vital sign from WiFi CSI. A 1-5mm chest displacement at 0.1–0.5 Hz creates a strong, periodic signal in the CSI amplitude.

### Pipeline

1. **Select top 15 subcarriers** by in-band SNR (0.1–0.5 Hz)
2. **Hampel filter** (window=7) to remove impulse noise
3. **Butterworth bandpass** 0.1–0.5 Hz, order 4
4. **Average** across selected subcarriers → 1D waveform
5. **FFT** on 30-second sliding window (3000 samples)
6. **Peak frequency** × 60 = breaths per minute

### Confidence Gating

Not every FFT peak is a breathing signal. We gate on two metrics:

**SNR:** Peak power divided by median noise floor (excluding guard bins ±2 around the peak). Minimum threshold: 3 dB. Full confidence at 20 dB.

**Spectral concentration:** What fraction of the in-band power lives within ±1 bin of the peak? Real breathing concentrates power in a narrow band. Random noise spreads flat. Minimum threshold: 15%.

Combined confidence: `snr_confidence × concentration_confidence`, both linearly ramped from their respective thresholds.

**Accuracy:** ±1-2 BPM when the person is stationary. Reliable enough to display with confidence. The 30-second window gives tight FFT frequency resolution — at 100Hz sampling, that's 3000 points, so each FFT bin is 0.033 Hz (2 BPM). Plenty precise for breathing.

---

## Step 8: Heart Rate — The Hard Problem

Heart rate from WiFi CSI is where the physics fights you. The cardiac-induced body displacement is ~0.1mm — fifty times smaller than breathing. At that scale, you're competing with HVAC vibrations, building sway, and the thermal expansion of your walls.

### Why CWT Instead of FFT

The heartbeat band (0.8–2.0 Hz) overlaps with the second and third harmonics of breathing. A person breathing at 18 BPM (0.3 Hz) has harmonics at 0.6, 0.9, and 1.2 Hz — right in the heart rate band.

FFT treats the entire 30-second window uniformly. If the heart rate drifts (heart rate variability is real), the FFT peak broadens and SNR drops. The Continuous Wavelet Transform (CWT) with a Morlet wavelet provides better time-frequency localization — it can track a shifting frequency without smearing the peak:

```python
# Morlet wavelet at frequency f, sample rate fs
scale = omega0 * fs / (2π * f)   # omega0 = 6.0 (standard)
wavelet_length = 10 * scale
t = linspace(-5*scale, 5*scale, wavelet_length)
psi = (π^-0.25) * exp(1j * omega0 * t/scale) * exp(-t²/(2*scale²))

# Convolve with signal at each target frequency
coefficients[f] = |signal ⊛ psi_f|²
```

We sweep 64 frequency bins across 0.8–2.0 Hz and take the peak.

### Breathing Harmonic Removal

Before CWT analysis, we notch out the breathing fundamental and its first 3 harmonics:

```
If breathing at 0.3 Hz is detected:
    Notch: 0.3 Hz ± 0.05 Hz
    Notch: 0.6 Hz ± 0.05 Hz
    Notch: 0.9 Hz ± 0.05 Hz
    Notch: 1.2 Hz ± 0.05 Hz
```

But only if the breathing peak is > 5× the median in-band power. False-positive notching (removing actual heart rate frequencies because we misidentified breathing) is worse than leaving the harmonics in.

### The Display Gates

Heart rate is only shown when ALL of these conditions are met:

1. **Position confidence > 0.6** — we need to know where the person is
2. **Stationary for > 30 seconds** — any motion overwhelms the 0.1mm signal
3. **In-band SNR ≥ 3 dB** — signal must be detectable above noise

When any gate fails, heart rate is hidden entirely. Not dimmed, not shown with a warning — hidden. Displaying ±10 BPM readings with 50-60% reliability requires aggressive gating to avoid misleading users.

**Accuracy:** ±8-10 BPM when all gates pass, with roughly 50-60% of readings being usable. Lab papers claim 96%+ accuracy, but those results don't survive a real living room with HVAC, multiple occupants, and varying distances. This is the honest number.

---

## Multi-Floor: Why Three WiFi Channels

Each floor gets its own TX board on a non-overlapping 2.4GHz channel (1, 6, 11). Cross-floor building materials attenuate the signal by ~10-15 dB per floor.

Floor detection is simple: compare CSI amplitude variance from each floor's TX. The floor where the person is standing shows the highest perturbation — because the signal path is shorter and less attenuated, so body movement creates larger CSI changes.

Hysteresis prevents noisy single-frame floor flips: the system requires 3 consecutive frames showing a new floor before switching. In stairwell transition zones (defined in config), this drops to 1 frame for faster detection.

---

## Occupancy: How Many People?

When positions overlap or can't be cleanly separated, we fall back to occupancy estimation using Non-negative Matrix Factorization (NMF).

NMF decomposes the time × subcarrier CSI variance matrix `V` into `W × H`, where each column of `W` represents an independent source (person). We sweep `k = 1..6` and use an elbow criterion: stop adding components when the reconstruction error improvement drops below 10%.

**Limitations are real:** Reliable for 1-2 people in separate rooms. Accuracy degrades with proximity (<2m) because the CSI signatures become correlated. When ambiguous, the dashboard renders overlapping fuzzy blobs rather than discrete dots — honest about what it knows.

---

## The Numbers That Matter

| Metric | Value |
|--------|-------|
| CSI sample rate | 100 Hz per receiver |
| Subcarriers | 114 (HT40 mode) |
| End-to-end latency | ~30-50ms |
| Localization accuracy | 1–1.5m |
| Breathing accuracy | ±1-2 BPM (stationary) |
| Heart rate accuracy | ±8-10 BPM (~50-60% usable) |
| Calibration time | ~17 min/floor |
| Total hardware cost | ~$180-260 |
| Compute platform | Raspberry Pi 4 (no GPU) |

The entire pipeline — phase sanitization, filtering, KNN, particle filter, FFT, CWT — runs comfortably on a Pi 4. Phase sanitization is one linear regression per frame. KNN is a 35-dimensional cosine search over 350 entries (<1ms). The particle filter updates 200 particles in <1ms. FFT and CWT run periodically on 30-second windows, not per-frame.

No deep learning. No GPU. Just DSP done right.

---

## What I Learned

**The subcarrier selection split was the single biggest accuracy gain.** Separating tracking subcarriers (by variance) from vital sign subcarriers (by in-band SNR) improved breathing detection by ~20%. It's counterintuitive — you'd expect the most responsive subcarriers to be best for everything. But high-variance channels are dominated by macro-motion and environmental drift, which overwhelms the tiny periodic signals from breathing and heartbeat.

**Hampel filters are underrated.** Every other outlier rejection method either distorted the signal or missed the worst spikes. Hampel's median-based approach threads the needle perfectly for periodic signals in impulsive noise.

**Heart rate honesty matters more than heart rate accuracy.** The display gating system — requiring position confidence, stationarity, and SNR thresholds before showing any heart rate number — is more important than the CWT extraction itself. A system that shows garbage data with false precision is worse than one that says "I don't know."

**Particle filters are worth the complexity.** Raw KNN estimates are too noisy for a usable dashboard. The 200-particle filter turns jittery point estimates into smooth, physically plausible trajectories with built-in uncertainty quantification. The convergence metric feeds directly into visualization — you can *see* the system's confidence.

---

## References

1. Kotaru, M. et al. "SpotFi: Decimeter Level Localization Using WiFi." ACM SIGCOMM, 2015.
2. Park, J. et al. "Non-Contact Heart Rate Monitoring via WiFi Channel State Information." Sensors, 2024.
3. Kim, S. et al. "Human Daily Breathing Monitoring Using WiFi CSI I/Q Plane." Sensors, 2024.
4. Tsinghua University. "CSI Sanitization Tutorial." tns.thss.tsinghua.edu.cn/wst/docs/sanitization/
5. Lee, D. & Seung, H. "Algorithms for Non-negative Matrix Factorization." NeurIPS, 2001.

---

*The full codebase is at [github.com/justintormey/wifi-csi](https://github.com/justintormey/wifi-csi). This is the second post in a series — the first covers what the project is and why it exists. Next up: firmware development for the ESP32-S3 and lessons from flashing 12 boards.*
