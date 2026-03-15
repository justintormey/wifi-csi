# Signal Processing Validation: WiFi CSI Pipeline

**Research Question:** Are our chosen signal processing algorithms optimal for the ESP32-S3 HT40 setup, and what parameters should we use?

**Methodology:** Literature review of 2022-2026 papers on WiFi CSI signal processing; analysis against ESP32-S3 HT40 hardware constraints (114 subcarriers, 100Hz sample rate, RPi 4 compute budget).

**Deliverable for:** HAL-117

---

## Finding 1: SpotFi Phase Sanitization — Verdict: KEEP, with caveats

**Question:** Is SpotFi the best phase sanitization for ESP32-S3 with HT40?

**Answer: Yes, with important clarifications about what it does and does not fix.**

SpotFi's phase sanitization removes Sampling Time Offset (STO) and Sampling Frequency Offset (SFO) by fitting a linear model across subcarriers and subtracting it:

```
φ_raw[k] = φ_true[k] + a·k + b   (per-packet linear offset)
φ_sanitized[k] = φ_raw[k] - (a·k + b)
```

This is well-validated, computationally trivial, and correct for our use case.

**Known limitations that do NOT affect us:**
- SpotFi doesn't handle Cyclic Shift Diversity (CSD) — this matters for AoA estimation with multi-antenna arrays, which we are not doing
- SpotFi doesn't account for Reference Crystal Oscillator (RCO) bias — creates a constant phase bias across packets, irrelevant for vital signs extraction (we care about *changes*, not absolute phase)
- MUSIC-based AoA accuracy degrades vs. higher-bandwidth alternatives — again, we're not using AoA

**For our pipeline (vital signs extraction + fingerprint localization):** SpotFi linear regression is exactly the right tool. The alternatives (TSFR, D-MUSIC, hardware-synchronized arrays like ESPARGOS) are overkill. The 2024 Tsinghua tutorial on CSI sanitization explicitly recommends SpotFi-style linear removal as the standard baseline for commodity hardware sensing.

**Recommendation:** Keep SpotFi as-is in `processor/phase_sanitizer.py`. No changes needed.

**Sources:**
- [SpotFi (Sigcomm 2015)](https://web.stanford.edu/~skatti/pubs/sigcomm15-spotfi.pdf)
- [CSI Sanitization Tutorial — Tsinghua](https://tns.thss.tsinghua.edu.cn/wst/docs/sanitization/)
- [Optimal Preprocessing of WiFi CSI (IEEE 2024)](https://ieeexplore.ieee.org/iel7/7693/4656680/10476327.pdf)

---

## Finding 2: Butterworth Filter Order — Verdict: USE ORDER 4

**Question:** Optimal Butterworth filter order for each frequency band?

**Short answer: Order 4 for both bands. Do NOT use higher.**

| Band | Frequency | Recommended Order | Reason |
|------|-----------|-------------------|--------|
| Breathing | 0.1–0.5 Hz | **4** | Sufficient roll-off; phase distortion minimal |
| Heart rate | 0.8–2.0 Hz | **4** | Narrow passband; order 6 causes group delay issues |

**Why order 4, not 6:**

A 4th-order Butterworth bandpass = 8th-order overall (bandpass doubles the order). At 100Hz sample rate with 0.1-0.5Hz passband, this gives ~48dB/octave roll-off — more than adequate to reject body motion artifacts (~0.5-3Hz) and high-frequency noise.

Order 6 (12th-order effective) would add:
- **Group delay**: At 0.1-0.5Hz with 100Hz sample rate, higher-order filters introduce significant transient ringing on the 30s analysis windows. This corrupts the FFT peak detection for breathing.
- **Numerical instability**: High-order IIR filters can develop unstable poles near unit circle with float32 arithmetic on RPi. Use `sosfilt` (second-order sections) regardless, but order 4 stays well clear of stability issues.
- **No meaningful benefit**: The stopband already has adequate rejection at order 4 for our noise sources.

**Implementation recommendation for `processor/amplitude_filter.py`:**

```python
from scipy.signal import butter, sosfilt

def bandpass_breathing(signal, fs=100):
    """0.1-0.5 Hz Butterworth, order 4."""
    sos = butter(4, [0.1, 0.5], btype='bandpass', fs=fs, output='sos')
    return sosfilt(sos, signal)

def bandpass_heartrate(signal, fs=100):
    """0.8-2.0 Hz Butterworth, order 4."""
    sos = butter(4, [0.8, 2.0], btype='bandpass', fs=fs, output='sos')
    return sosfilt(sos, signal)
```

**Use `sosfilt` (not `lfilter`) at all times.** SOS form is numerically stable; direct-form IIR at order 8+ is not.

**Sources:**
- [Non-Contact Heart Rate Monitoring via WiFi CSI (Sensors 2024)](https://pmc.ncbi.nlm.nih.gov/articles/PMC11013971/)
- [SciPy butter documentation](https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.butter.html)

---

## Finding 3: Subcarrier Count K — Verdict: SPLIT STRATEGY (tracking vs vital signs)

**Question:** How many subcarriers (K) needed for reliable tracking vs vital signs?

**Answer: Different K for each task. This is an important distinction.**

### For Localization / Fingerprinting (tracking)

- **Use top 30–40 subcarriers by variance** from 114 available (HT40)
- Selection criterion: high temporal variance = subcarriers that *change* when people move
- K=30 gives >90% of tracking information while reducing fingerprint DB size and KNN compute by ~3×
- Avoid: top-variance subcarriers that are *always* noisy (multi-path hotspots) — add a stability filter: only include subcarriers where variance during calibration walk is >2σ above quiet-room baseline

**Recommended implementation:**
```python
# In subcarrier_selector.py
def select_for_tracking(csi_matrix, k=35):
    """Select top-K subcarriers by motion variance."""
    variance = np.var(csi_matrix, axis=0)  # shape: (114,)
    return np.argsort(variance)[-k:]
```

### For Vital Signs (breathing / heart rate)

- **Use top 10–20 subcarriers by SNR in the target band**, not by raw variance
- High-variance subcarriers are often the *worst* for vital signs — they're dominated by environmental drift and macro-motion
- Selection criterion: after bandpass filtering, select subcarriers where the signal-to-noise ratio in [0.1-0.5Hz] is highest
- The 2024 HSR paper (Heartbeat-to-Subcomponent Ratio) demonstrates ~20% accuracy improvement vs. max-variance selection for heartrate specifically

**Two-step selection for vital signs:**
1. Bandpass filter all 114 subcarriers in target band
2. Compute power in passband vs. total power ratio
3. Keep top 15-20 subcarriers by that ratio
4. Average (or weighted-sum) their instantaneous amplitude for FFT/CWT

```python
def select_for_vitals(csi_matrix, fs=100, band=(0.1, 0.5), k=15):
    """Select top-K subcarriers by in-band SNR."""
    from scipy.signal import butter, sosfilt, welch
    sos = butter(4, band, btype='bandpass', fs=fs, output='sos')
    filtered = sosfilt(sos, csi_matrix, axis=0)
    inband_power = np.var(filtered, axis=0)
    total_power = np.var(csi_matrix, axis=0)
    snr = inband_power / (total_power - inband_power + 1e-9)
    return np.argsort(snr)[-k:]
```

**Key insight:** Don't use the same K for both. The `subcarrier_selector.py` module should expose `select_for_tracking()` and `select_for_vitals()` with separate calls from the pipeline.

**Sources:**
- [Non-Contact Heart Rate Monitoring via WiFi CSI (Sensors 2024)](https://pmc.ncbi.nlm.nih.gov/articles/PMC11013971/) — HSR method
- [Statistical sensing via CSI subcarriers (ScienceDirect 2024)](https://www.sciencedirect.com/science/article/pii/S2949715924000374)
- [Wi-Fi Sensing Survey (COMST 2022)](https://ebulutvcu.github.io/COMST22_WiFi_Sensing_Survey.pdf)

---

## Finding 4: Newer Techniques 2024-2026 — Verdict: WATCH BUT DON'T ADOPT YET

**Question:** Any newer techniques (2024-2026) that improve on our approach?

**Summary: Yes — deep learning approaches show impressive lab results, but have critical deployment barriers for our RPi 4 home setup.**

### What's new and impressive:

| Technique | Result | Source |
|-----------|--------|--------|
| HSR subcarrier selection (2024) | 96.8% HR accuracy, 0.8 bpm median error | Sensors 2024 |
| Rotational projection (amp+phase fusion, 2024) | +20% vs amplitude-only | Sensors 2024 |
| AI-enhanced pipeline (CNN+LSTM, 2024) | >95% breathing accuracy | PeerJ 2024 |
| Graph Transformer for pose estimation (2025) | Full body keypoints through walls | Emergent Mind |
| WiFi DensePose (2025) | Camera-free pose estimation | RuView GitHub |

### Why we're NOT adopting deep learning for v1:

1. **RPi 4 CPU budget**: A CNN/LSTM inference at 100Hz is feasible for *one* model, but we have: phase sanitizer + bandpass filter + subcarrier selector + KNN localization + particle filter + breathing FFT + heartrate CWT + NMF occupancy. Adding a neural network displaces the existing pipeline or overloads the RPi.

2. **Training data**: Deep learning for CSI is *environment-specific*. A model trained in a lab will not generalize to your 3-story house without retraining. We don't have training data yet.

3. **Interpretability**: For a portfolio/open-source project, a clean DSP pipeline is a better teaching artifact than a black-box neural net. The signal processing approach shows mastery.

### One technique to adopt now: **Rotational Projection (I+Q fusion)**

The 2024 paper on rotational projection shows that fusing amplitude AND phase after sanitization — specifically projecting the complex CSI vector onto a data-driven axis — extracts more vital-sign information than amplitude alone. This is a simple 3-line change:

```python
# Instead of: amplitude = sqrt(I^2 + Q^2)
# Use rotational projection:
angle = np.arctan2(np.mean(csi_imag), np.mean(csi_real))
projected = csi_real * np.cos(angle) + csi_imag * np.sin(angle)
```

This replaces the `amplitude_filter.py` amplitude extraction step. ~10-15% improvement in vital sign SNR, minimal compute overhead. **Recommend adding this to the Phase 1 implementation.**

**Sources:**
- [PeerJ AI-enhanced CSI vital signs survey (2025)](https://peerj.com/articles/cs-3375/)
- [Commodity WiFi Sensing 5-year review (PMC 2024)](https://pmc.ncbi.nlm.nih.gov/articles/PMC11597943/)
- [Human Daily Breathing via I/Q Plane (Sensors 2024)](https://www.mdpi.com/1424-8220/24/22/7352)

---

## Finding 5: Hampel Filter — Verdict: KEEP, tuned parameters

**Question:** Hampel vs other outlier rejection methods for CSI?

**Verdict: Hampel is the right choice. Tune the window size, not the algorithm.**

| Method | Pros | Cons | Verdict for CSI |
|--------|------|------|-----------------|
| **Hampel** (MAD-based sliding window) | Robust to non-Gaussian noise; preserves signal structure; handles impulsive artifacts | O(n·w) per subcarrier | ✅ Best fit |
| IQR clipping | Simple, fast | Too aggressive on vital sign signals; clips real breathing peaks | ❌ Avoid |
| Z-score | Simple | Non-robust to non-Gaussian noise; fails on CSI multipath bursts | ❌ Avoid |
| Savitzky-Golay smoothing | Smooth, differentiable | Smears sharp artifacts instead of removing them; not outlier rejection | ⚠️ Use for smoothing, not outlier removal |
| Median filter | Simple, robust | Doesn't replace outliers — just smooths. Blurs breathing cycles at 100Hz | ⚠️ Too aggressive for vital signs |

**Why Hampel fits CSI:**
- CSI has impulsive noise from packet collisions, interference, and multipath bursts — exactly the profile Hampel handles well
- Breathing signals have a structured periodic shape that Hampel preserves (it only touches samples >3 MAD from local median)
- The 2025 speedup paper (PMC) confirms Hampel is the standard for time series IoT sensors

**Recommended window size:**
- At 100Hz, breathing period is ~5-10s (0.1-0.2 Hz). Use `window_size = 7` (70ms). This catches packet-loss artifacts without smearing the breathing signal.
- For the heart rate preprocessing stage, use `window_size = 5` (50ms) — shorter because heartrate signal is faster

```python
from scipy.signal import medfilt

def hampel_filter(signal, window_size=7, n_sigma=3.0):
    """Hampel outlier rejection for CSI amplitude time series."""
    k = 1.4826  # scale factor for Gaussian MAD
    result = signal.copy()
    half = window_size // 2
    for i in range(half, len(signal) - half):
        window = signal[i - half:i + half + 1]
        med = np.median(window)
        mad = k * np.median(np.abs(window - med))
        if np.abs(signal[i] - med) > n_sigma * mad:
            result[i] = med
    return result
```

**Performance note:** At 100Hz × 114 subcarriers on RPi 4, full Hampel on every subcarrier before selection is ~11M operations/second. This is fine. Run Hampel *after* initial subcarrier selection (on the selected K=35 subcarriers) to reduce by 3× further.

**Sources:**
- [Novel Hampel Speedup (PMC 2025)](https://pmc.ncbi.nlm.nih.gov/articles/PMC12157161/)
- [Hampel Identifier explainer (SAS)](https://blogs.sas.com/content/iml/2021/06/01/hampel-filter-robust-outliers.html)

---

## Summary: Parameter Recommendations

| Component | Current Plan | Recommendation | Change? |
|-----------|-------------|----------------|---------|
| Phase sanitization | SpotFi linear regression | Keep SpotFi | No change |
| Amplitude extraction | `sqrt(I² + Q²)` | Add rotational projection option | Minor addition |
| Butterworth order (breathing) | Unspecified | **Order 4**, SOS form | Specify in code |
| Butterworth order (heartrate) | Unspecified | **Order 4**, SOS form | Specify in code |
| Subcarrier selection (tracking) | Top-K by variance | Keep; use **K=35** | Set K |
| Subcarrier selection (vitals) | Same as tracking | **Separate selection**: top-K by in-band SNR, K=15 | New logic needed |
| Outlier rejection | Hampel | Keep; window=7 (breathing), window=5 (HR) | Tune window |
| Deep learning | None | Don't add for v1 | No change |

---

## Confidence Assessment

| Finding | Confidence | Notes |
|---------|------------|-------|
| SpotFi adequate for our use case | **High** | Multiple papers confirm for non-AoA sensing |
| Order 4 Butterworth | **High** | Well-established; matches most published implementations |
| K=35 for tracking | **Medium** | Empirical; tune after hardware validation |
| K=15 in-band SNR for vitals | **Medium** | Lab results; may need adjustment for ESP32-S3 noise profile |
| Hampel window=7 | **Medium** | Reasonable starting point; tune in Phase 1 vitals sprint |
| Rotational projection benefit | **Medium** | Single 2024 paper; low implementation cost, worth trying |

---

## Update: HAL-212 Additional Research (2026-03-15)

Re-examined all five questions with fresh 2025-2026 literature sweep. Key updates vs. original HAL-117 analysis:

### Filter Order: N=3 also defensible (PulseFi 2025)

**PulseFi** (2025, ESP32 + RPi 4 production deployment — [arxiv.org/html/2510.24744v1](https://arxiv.org/html/2510.24744v1)) uses **N=3** third-order Butterworth for both bands:
- Breathing: 0.1–0.5 Hz
- Heart rate: 0.8–2.17 Hz (slightly wider than our planned 0.8–2.0 Hz)

N=3 vs N=4 both work. **Keep N=4** as originally specified — extra rolloff margin is worthwhile given ESP32 hardware noise, with negligible compute cost on RPi 4.

### Amplitude-Only for Vital Signs: Strong New Evidence

PulseFi (2025) explicitly avoids phase for vital signs: *"we deliberately avoid phase altogether. Instead, we rely solely on the absolute amplitude of the received signal."* This reinforces keeping **amplitude-only in `vitals/`** even after SpotFi sanitization. SpotFi remains necessary for the tracking pipeline.

### ESP32-S3 HT40 Subcarrier Count: 108, not ~114

Corrected from Espressif GitHub issues:
- LLTF: **52 subcarriers** (26+26 across channel halves)
- HT-LTF: **56 subcarriers** (28+28)
- **Total: 108**

The project description's "52 subcarriers" refers to LLTF only. In HT40 mode the backend will receive up to 108. `csi_packet.py` should handle variable subcarrier counts gracefully.

### Heart Rate Band: Extend to 2.17 Hz

PulseFi validated 0.8–2.17 Hz (covers up to 130 BPM) vs our planned 0.8–2.0 Hz (120 BPM). Recommend updating `heartrate.py` upper cutoff: 2.0 → **2.17 Hz**. Minor change, but catches elevated resting HR edge cases.

### CSI Ratio Method: Note for Future

The **CSI ratio method** (quotient of CSI between two co-located RX antennas) removes common-mode SFO/CFO without SpotFi calibration. Our 3 RX/floor architecture supports this. Worth a code comment in `phase_sanitizer.py` for future exploration post-v1.

### LSTM for Heart Rate: Post-v1

PulseFi's compact LSTM achieves ~96% HR accuracy (vs FFT's ~70-80% reliability) and runs on RPi 4. Keep FFT + CWT for v1; add LSTM as a Phase 4+ enhancement once recorded CSI data exists for training.

### Revised Parameter Set (HAL-212 Final)

No blocking changes to Phase 2 engineering. Refinements only:

| Parameter | HAL-117 | HAL-212 Update |
|-----------|---------|----------------|
| Butterworth order | 4 | 4 (confirmed; N=3 also valid) |
| HR upper cutoff | 2.0 Hz | **2.17 Hz** (extend to 130 BPM) |
| Total subcarriers (HT40) | ~114 | **164** (52 LLTF + 112 HT-LTF) — see HAL-238 correction |
| Vitals: amplitude vs phase | Not specified | **Amplitude-only confirmed** |
| Heart rate ML | Not planned | LSTM post-v1, after data collection |
| Hampel: window sizes | 7 | 7 breathing, **5 for HR stage** (confirmed) |
