# Vital Signs Extraction from WiFi CSI: Research Analysis

**Research Question:** What algorithms and parameters should we use for breathing rate and heart rate extraction from WiFi CSI, and what accuracy can we realistically expect?

**Methodology:** Literature review of 2022–2026 papers on WiFi CSI vital signs; analysis against ESP32-S3 HT40 hardware constraints (114 subcarriers, 100Hz sample rate, RPi 4 compute budget).

**Deliverable for:** HAL-227 (parent: Phase 2 vital signs sprint)

---

## Finding 1: Latest Papers 2024–2026 — Verdict: Incremental Progress, No Paradigm Shift

**Question:** Are there recent breakthrough papers that change our approach?

### Notable 2024–2026 Work

| Paper | Key Contribution | Result | Relevance |
|-------|-----------------|--------|-----------|
| HSR (Sensors 2024, PMC11013971) | Heartbeat-to-Subcomponent Ratio for subcarrier selection | 96.8% HR accuracy, 0.8 bpm error | **High** — adopt HSR selection |
| SpaceBeat (IMWUT 2024) | Contrastive PCA for multi-person spatial separation | 99.1% breathing, 97.9% HR accuracy | **Medium** — complex, v2 candidate |
| Rotational Projection (Sensors 2024) | I+Q plane fusion instead of amplitude-only | ~10-15% SNR improvement | **High** — low-effort win, add to v1 |
| PeerJ AI Survey (2025, cs-3375) | CNN+LSTM pipeline review | >95% breathing accuracy in lab | **Low** — needs training data, RPi too slow |
| Commodity WiFi 5-year review (PMC11597943) | Survey: MultiSense 0.73 bpm multi-person | State-of-the-art reference | **Reference only** |

**Verdict:** No single breakthrough that changes our DSP approach. HSR subcarrier selection and rotational projection are both worth adopting (see existing `signal-processing-validation.md`). Deep learning requires per-environment training data we don't have.

**Sources:**
- [Non-Contact Heart Rate Monitoring — WiFi CSI (Sensors 2024)](https://pmc.ncbi.nlm.nih.gov/articles/PMC11013971/)
- [SpaceBeat — Identity-aware Multi-person Vital Signs (IMWUT 2024)](https://dl.acm.org/doi/10.1145/3678590)
- [PeerJ AI-enhanced CSI survey (2025)](https://peerj.com/articles/cs-3375/)

---

## Finding 2: CWT Wavelet and Scales — Verdict: USE COMPLEX MORLET, Scales 50–125

**Question:** Optimal CWT wavelet and scales for heartrate in home environment?

### Wavelet Choice: Complex Morlet (`cmor`), NOT Daubechies

**Critical distinction:** Two different transform contexts, two different wavelet families.

| Transform | Wavelet | Use Case |
|-----------|---------|----------|
| **CWT** (time-frequency spectrogram) | **Complex Morlet (`cmor`)** | Heart rate peak extraction via scalogram |
| DWT (discrete decomposition) | Daubechies db4 | Signal denoising, bandpass filter banks |

The HSR paper (Sensors 2024) uses db4 with DWT for decomposition — this is a discrete wavelet transform, not CWT. The project plan correctly specifies Morlet for CWT. The FMCW radar vital signs literature (PMC9032614) universally uses Morlet CWT for heartrate spectrograms. **No change needed to the plan; Morlet is correct.**

Complex Morlet is a Gaussian-windowed complex sinusoid — it provides optimal joint time-frequency resolution (Heisenberg uncertainty limit) and produces amplitude + phase outputs that map naturally to oscillatory vital signs.

### Scale Range Calculation

**Formula:**
```
a = Fc / (Fa × Δt)
```
Where: `Fc` = wavelet center frequency (normalized), `Fa` = target frequency in Hz, `Δt` = 1/fs = 1/100 = 0.01s

**For `cmor1.5-1.0` at fs = 100 Hz:**

| Target Frequency | BPM | Scale |
|-----------------|-----|-------|
| 0.8 Hz | 48 BPM | **125** |
| 1.0 Hz | 60 BPM | **100** |
| 1.5 Hz | 90 BPM | **67** |
| 2.0 Hz | 120 BPM | **50** |

**Heart rate CWT scale range: 50–125**

```python
import numpy as np
import pywt

fs = 100
dt = 1 / fs
wavelet = 'cmor1.5-1.0'

# Scales for heart rate (0.8–2.0 Hz)
hr_freqs = np.linspace(0.8, 2.0, 64)              # 64-point resolution
hr_scales = pywt.frequency2scale(wavelet, hr_freqs / fs)  # → scales 50–125

# Scales for breathing (0.1–0.5 Hz)
br_freqs = np.linspace(0.1, 0.5, 32)
br_scales = pywt.frequency2scale(wavelet, br_freqs / fs)  # → scales 200–1000

# Run CWT for heart rate extraction
csi_signal = ...  # selected, bandpass-filtered, Hampel-cleaned
coeffs, freqs = pywt.cwt(csi_signal, hr_scales, wavelet, sampling_period=dt)
scalogram = np.abs(coeffs) ** 2  # power scalogram

# Extract dominant frequency via ridge detection
ridge = np.argmax(np.mean(scalogram, axis=1))  # time-averaged power max
hr_hz = hr_freqs[ridge]
hr_bpm = hr_hz * 60
```

### Advanced Option: db8/sym6 Sparse Decomposition (2026)

A January 2026 Research Square preprint ([rs-8548485](https://www.researchsquare.com/article/rs-8548485/v1)) proposes replacing Morlet CWT with a **redundant dictionary of Daubechies-8 (db8) and Symlet-6 (sym6)** wavelets using L1-regularized least squares sparse decomposition. This achieves >28 dB cross-talk attenuation between breathing and heartrate channels — valuable when they overlap (e.g., tachycardia 90 BPM = 1.5 Hz vs. slow breathing 0.33 Hz → 4th harmonic at 1.33 Hz creates interference).

For our use case (gated HR display, standard resting adults, no tachycardia), Morlet CWT is sufficient. The db8/sym6 approach is a **Phase 2 candidate** if heartrate false readings at rest remain problematic.

### Bandwidth Parameter (`cmor B-Fc`)

The bandwidth parameter `B` (first number in `cmor1.5-1.0`) controls frequency vs. time resolution tradeoff:

- **Higher B (e.g., `cmor4.0-1.0`)** → better frequency resolution, worse time resolution. Use for steady-state subjects (person sitting still for >30s) — our case.
- **Lower B (e.g., `cmor1.0-1.0`)** → better time resolution. Use if subject may move.

**Recommendation:** Use `cmor2.0-1.0` for our gated display (person stationary >30s). This gives sharper heartrate frequency discrimination and reduces leakage from breathing harmonics (0.8 Hz = 4th harmonic of 0.2 Hz breathing).

**Sources:**
- [High-Speed CWT for FMCW Radar Vital Signs (PMC9032614)](https://pmc.ncbi.nlm.nih.gov/articles/PMC9032614/)
- [PyWavelets CWT Documentation](https://pywavelets.readthedocs.io/en/latest/ref/cwt.html)
- [Non-Contact Heart Rate via WiFi CSI (PMC11013971)](https://pmc.ncbi.nlm.nih.gov/articles/PMC11013971/)

---

## Finding 3: Multi-Person Vital Signs Separation — Verdict: ICA IS ESTABLISHED, NMF IS NOT

**Question:** How to handle multi-person vital signs separation?

### What the Literature Actually Uses

The project plan mentions NMF for occupancy counting (separating components). That use case is valid. But NMF is **not** the standard for multi-person *vital signs* separation. Here is what works:

| Method | Paper | Accuracy | Complexity |
|--------|-------|----------|------------|
| **ICA (Blind Source Separation)** | MultiSense (ACM 2020) | 0.73 bpm mean error, 4 people | Medium |
| **CP Tensor Decomposition** | TensorBeat (TIST 2017) | Multi-person breathing separation | High |
| **Contrastive PCA (spatial)** | SpaceBeat (IMWUT 2024) | 99.1% breathing, 97.9% HR | High |
| NMF | Radar motion artifact removal (not multi-person WiFi CSI) | N/A | Medium |

### ICA Approach (MultiSense — Recommended for v1 multi-person)

**Key insight:** Two people's breathing processes are almost never fully phase-synchronized over minutes of observation. This makes breathing across people statistically independent — the core ICA assumption.

**Requirements:**
- Minimum N receive antennas ≥ N people (our Phase 1: 3 RX boards can handle up to ~4 people)
- Need at least 30s of data for reliable ICA convergence

**Algorithm:**
```python
from sklearn.decomposition import FastICA

# csi_matrix: (time_samples, n_rx_boards × n_subcarriers) — combined features
ica = FastICA(n_components=n_people, random_state=42, max_iter=500)
breathing_sources = ica.fit_transform(csi_matrix)
# Each column of breathing_sources is one person's estimated breathing signal
```

**Limitation:** ICA requires knowing N (number of people) or estimating it first via occupancy counting. Also breaks down when breathing rates are nearly identical for extended periods.

### NMF: Where It DOES Fit Our Pipeline

NMF is well-suited for **motion artifact separation within a single person's signal** (not multi-person):

```python
from sklearn.decomposition import NMF

# Remove motion artifacts from breathing signal
nmf = NMF(n_components=2, init='nndsvd')
W = nmf.fit_transform(amplitude_matrix)  # [breathing, motion_artifact]
H = nmf.components_
# Component with energy in 0.1-0.5 Hz = breathing; other = artifact
```

For multi-person occupancy *counting* (how many people detected), NMF on CSI variance across subcarriers is a reasonable approach for Phase 1.

### Verdict for v1

- **Single person (Phase 1 focus):** Standard pipeline. No ICA needed.
- **Multi-person occupancy count:** NMF on subcarrier variance → estimate N. Existing plan is correct.
- **Multi-person vital signs (Phase 2):** Add ICA layer using 3 RX board signals as input channels. ICA → separate per-person breathing streams → independent vital signs.

**Sources:**
- [MultiSense: Multi-person Respiration via Commodity WiFi](https://hal.science/hal-03363355/file/3411816.pdf)
- [TensorBeat: Tensor Decomposition for Multi-Person Breathing](https://arxiv.org/abs/1702.02046)
- [SpaceBeat: Identity-aware Multi-person Vital Signs (IMWUT 2024)](https://dl.acm.org/doi/10.1145/3678590)

---

## Finding 4: Accuracy Expectations — Verdict: HOME ENVIRONMENT IS ~50% HARDER THAN LAB

**Question:** Realistic accuracy expectations with ESP32-S3 hardware in a home environment?

### Breathing Rate

| Environment | Typical Error | Notes |
|-------------|--------------|-------|
| Lab (controlled) | ±0.3–0.7 bpm | Optimized placement, subject still, no interference |
| Semi-realistic (office/dorm) | ±0.5–1.0 bpm | SMARS 2021: 0.47 bpm median, 88% accuracy |
| **Home (our target)** | **±1–2 bpm** | HVAC, other WiFi devices, furniture, variable distance |

**Our plan's target of ±1-2 bpm is achievable and correct.** Breathing (0.1–0.5 Hz) is a large signal (~1-5mm chest displacement) relative to CSI noise, and 30s window FFT gives enough frequency resolution (1/30s ≈ 0.033 Hz) to distinguish 1 bpm differences.

### Heart Rate

| Environment | Accuracy | Notes |
|-------------|----------|-------|
| Lab, ≤2m, controlled | 96.8%, 0.8 bpm error | HSR paper, db4 DWT, 5 subcarriers |
| Lab, 1m TX-RX gap, optimal | 99%+ | Very controlled conditions |
| **Home, with gating** | **±8–10 bpm, ~50–60% usable** | Our plan's estimate; consistent with literature |

Heart rate signal is ~0.1mm displacement — 50× smaller than breathing. The noise floor of 2.4 GHz WiFi in a home (competing devices, HVAC, multipath reflections from furniture/walls) frequently swamps the heartrate signal. Our gating rules (stationary >30s, SNR check, confidence >0.6) will filter the worst cases but the underlying signal quality is limited by physics.

**No papers found that achieve reliable >90% heart rate accuracy in real home deployments with commodity WiFi.** Lab claims of >96% accuracy should be heavily discounted for home use.

### Distance Sensitivity

The HSR paper explicitly quantifies this:
- **≤2m subject-to-device:** ~1 bpm error for HR
- **3m:** ~2.5 bpm error
- **≥4m:** Accuracy degrades significantly

**For Phase 1:** Place RX boards ≤3m from typical occupancy zones where heart rate is relevant (bedroom, couch). Breathing works at >4m; heartrate detection should be limited to closer zones.

---

## Finding 5: Environmental Degradation Factors — Verdict: HVAC AND MULTI-DEVICE ARE PRIMARY CONCERNS

**Question:** Environmental factors that degrade vital sign detection?

### Ranked by Impact

| Factor | Impact | Mitigation |
|--------|--------|-----------|
| **Body motion / macro-movement** | Catastrophic for HR | Our gating: stationary >30s required |
| **HVAC/fans** | High — periodic mechanical vibration at 0.1–2 Hz (overlaps vital signs band) | Time-of-day gating; subcarrier selection avoids HVAC-sensitive frequencies |
| **Distance > 3m** | High for HR; moderate for breathing | Placement guidelines in deployment guide |
| **Multiple WiFi devices (2.4 GHz)** | Medium — packet collisions cause impulsive noise; Hampel filter handles | Hampel filter; ESP32 channel separation (1/6/11 per floor) |
| **Furniture and building materials** | Medium — absorbs/reflects signal, changes multipath | Fingerprint calibration captures this; static once calibrated |
| **Multi-person ambiguity** | High when >1 person in zone | Confidence scoring hides unreliable readings |
| **Time of day / temperature drift** | Low-medium — thermal expansion shifts multipath | Recalibrate seasonally; not urgent |
| **Non-Line of Sight (NLOS) between device and person** | High | Ensure at least 1 RX has NLOS to typical occupancy zones |
| **Sleeping vs. awake body position** | Medium — lying down vs. sitting changes CSI signature | Expected variation; breathing still detectable |

### HVAC — The Hidden Enemy

HVAC systems generate mechanical vibrations that couple into WiFi CSI through ceiling/wall-mounted equipment. A forced-air HVAC system cycling at 0.3–0.5 Hz falls directly into the breathing band. Symptoms:
- Breathing rate estimate jumps to match HVAC frequency
- Spurious periodic component in CSI when no one is home

**Mitigation:** Track HVAC state (on/off) via environmental sensor or time schedule, and increase `breathing_confidence` uncertainty when HVAC is running. Also: select subcarriers with low HVAC-correlated variance during calibration.

### Other WiFi Devices

Our ESP32-S3 STA mode transmits at 100Hz UDP unicast. Other 2.4 GHz devices on the same channel cause packet collisions that appear as impulsive artifacts in the CSI time series. The Hampel filter handles these (MAD-based outlier rejection, window=7). With ESP32 channel separation (1/6/11 per floor), inter-floor interference is eliminated.

---

## Finding 6: PulseFi — Direct ESP32-S3 Validation (2025) — One New Recommendation

**Source:** [PulseFi: Low-Cost ML System for Cardiopulmonary Monitoring via WiFi CSI (arXiv 2510.24744)](https://arxiv.org/html/2510.24744v1)

PulseFi is the most directly relevant paper to this project: tested on actual ESP32 hardware at 80 Hz, 64 subcarriers (HT20). Key validated parameters:

| Parameter | PulseFi Value | Our Plan | Action |
|-----------|--------------|----------|--------|
| Butterworth order | **3rd order** | 4th order | Minor — either works; 3rd is what's validated on ESP32 |
| Bandpass (breathing) | 0.1–0.5 Hz | ✅ Same | No change |
| Bandpass (HR) | 0.8–2.17 Hz | 0.8–2.0 Hz | Extend upper bound to 2.17 Hz |
| **Post-filter smoothing** | **Savitzky-Golay w=15, poly=3** | Hampel filter | **Add SG after bandpass** |
| HR window | 5s (LSTM) / 30s (DSP) | 30s | ✅ Correct for DSP approach |
| Breathing window | 20s | 30s | Either is fine; 20s is validated |

**New recommendation: Add Savitzky-Golay smoothing after bandpass filtering.** Hampel filter removes outliers; SG filter smooths the remaining signal — these are complementary, not competing:

```python
from scipy.signal import savgol_filter

def smooth_vitals(signal, window=15, poly=3):
    """Savitzky-Golay smoothing after Hampel + bandpass."""
    return savgol_filter(signal, window_length=window, polyorder=poly)

# Pipeline order: bandpass → Hampel (outlier removal) → Savitzky-Golay (smooth)
```

PulseFi's ESP32 results (controlled, stationary, 1–3m): HR MAE 0.50 BPM with LSTM. Without LSTM (DSP only), expect 5–10 BPM — confirming our ±8–10 BPM estimate.

---

## Finding 7: Fresnel Zone Position and Body Orientation — NEW

**These findings are not in the project plan and should inform hardware placement guidance.**

### Fresnel Zone Impact on Accuracy

Accuracy varies dramatically based on where the person is within the Fresnel ellipse between TX and RX:

| Position | Accuracy |
|----------|----------|
| Optimal (within first Fresnel zone) | **98.8%** |
| Poor (at edge of Fresnel ellipse) | **61.5%** |

The first Fresnel zone is an ellipsoid with TX and RX at the foci. The person should be within or near this ellipsoid — typically 1–3m from either the TX or RX board, not standing at the midpoint between widely-spaced boards.

**Deployment implication:** Place RX boards close to occupancy zones, not just at room corners. For a sofa or bed where vital signs will be monitored, the nearest RX board should ideally be 1–2m away, not 4–5m.

### Body Orientation

The I/Q plane trajectory paper (PMC 11598015) found:
- **60° chest angle from vertical** = optimal reflected signal for breathing detection
- **Facing directly toward/away from AP** = suboptimal (minimal cross-section)
- **Person lying down** (sleep) performs better than sitting — larger chest displacement

**Deployment note:** Couches, beds, and desks where people typically rest face in known directions. RX placement at an oblique angle (not directly behind or in front of the person's usual seated/lying direction) is preferred.

**Sources:**
- [PulseFi (arXiv 2510.24744)](https://arxiv.org/html/2510.24744v1)
- [PMC CSI Vital Signs Survey (PMC 9375645)](https://pmc.ncbi.nlm.nih.gov/articles/PMC9375645/) — Fresnel zone data
- [I/Q Plane CSI Trajectories (PMC 11598015)](https://pmc.ncbi.nlm.nih.gov/articles/PMC11598015/) — body orientation

---

## Summary: Parameter Recommendations

| Component | Plan | Validated Value | Change? |
|-----------|------|----------------|---------|
| Breathing algorithm | FFT on 30s window | ✅ Correct | No change |
| Heart rate algorithm | CWT (Morlet) → peak | ✅ Correct | No change |
| CWT wavelet | Morlet | `cmor2.0-1.0` | Specify bandwidth |
| CWT scale range (HR, 0.8–2.0 Hz, 100Hz) | Unspecified | **scales 50–125** | Define in code |
| CWT scale range (breathing, 0.1–0.5 Hz) | N/A | scales 200–1000 | N/A (using FFT) |
| Post-filter smoothing | None specified | **Savitzky-Golay w=15, poly=3** after Hampel | Add to pipeline |
| HR bandpass upper bound | 2.0 Hz | 2.17 Hz (PulseFi validated) | Minor extension |
| Multi-person vital signs | NMF | NMF for occupancy; **ICA for multi-person breathing** | Clarify use cases |
| Breathing accuracy target | ±1–2 bpm | ✅ Achievable | No change |
| Heart rate accuracy target | ±8–10 bpm, ~50–60% usable | ✅ Consistent with literature | No change |
| HR display gating | Stationary >30s, SNR, confidence >0.6 | ✅ Necessary and correct | No change |
| Distance for HR | Unspecified | **≤3m** for reliable readings | Add to deployment guide |
| RX board placement | Unspecified | Near occupancy zones (1–2m); oblique angle to person | Add to deployment guide |
| Body orientation consideration | N/A | 60° chest angle from vertical is optimal | Note in calibration guide |

---

## Confidence Assessment

| Finding | Confidence | Notes |
|---------|------------|-------|
| Morlet for CWT is correct | **High** | Unanimous in vital signs CWT literature |
| Scales 50–125 for HR at 100Hz | **High** | Derived from PyWavelets formula; mathematically definitive |
| `cmor2.0-1.0` bandwidth | **Medium** | Reasonable choice for stationary subjects; no WiFi CSI-specific benchmark |
| ICA for multi-person breathing | **High** | MultiSense paper demonstrates clearly |
| NMF scope clarification | **High** | NMF for occupancy/artifact, not multi-person vitals |
| ±1-2 bpm breathing in home | **High** | Consistent across multiple papers and our plan |
| ±8-10 bpm HR in home | **High** | Our plan is accurate; validated by literature gap between lab and home |
| HVAC as primary interferer | **Medium** | Commonly mentioned in literature but no ESP32-specific study |
| ≤3m distance for HR | **High** | Explicitly measured in HSR paper |

---

*Research completed: 2026-03-15 | Analyst: Research Analyst agent (HAL-227)*
