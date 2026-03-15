# Particle Filter and Tracking Optimization — Research Brief

**Issue:** HAL-125
**Date:** 2026-03-15
**Questions answered:** Particle count, velocity model, wall-awareness, multi-person source separation, track birth/death

---

## Summary of Recommendations

| Parameter | Recommended Value | Rationale |
|---|---|---|
| Particles per person (tracking) | 150–200 | Sufficient for 1–1.5 m accuracy; fits 10 Hz budget on RPi 4 |
| Particles per person (initialization) | 400–500 | Wide search during track birth; KLD-sampling reduces post-convergence |
| Motion σ_v | 0.4 m/s | Covers realistic indoor acceleration at 10 Hz update rate |
| v_max hard cap | 1.5 m/s | Covers brisk indoor walk; truncate Gaussian above this |
| Velocity persistence α | 0.75 | Adds momentum without rigidity |
| Wall constraint method | Rejection/retry (K=5) | Simplest correct approach; line-segment intersection |
| Source separation | NMF (k=n_people) | Best accuracy (56%) on commodity ESP32; matches CSI amplitude math |
| Multi-person fallback | AoA geometric separation | More reliable than blind source separation for 3+ people |
| Track confirmation | M=3 of N=5 frames | 0.3–0.5 s to confirm; standard across radar/vision tracking |
| Track deletion (confirmed) | 6 consecutive misses | ~0.6 s grace period for brief occlusion |
| Track deletion (tentative) | 2 consecutive misses | Fast false-positive pruning |
| Track birth anomaly threshold | z-score > 2.5 sustained for 1–2 s | Avoids ghost tracks from multipath spikes |

---

## 1. Optimal Particle Count for RPi 4

### Literature findings

The quantitative picture from published indoor WiFi particle filter work:

| Particle Count | Accuracy | Notes |
|---|---|---|
| 50 (MLPF) | ~0.50 m MSE | Maximum Likelihood Particle Filter — outperforms standard PF at 1000 |
| 300 | 0.76 m mean error | Standard SIR PF with WiFi RSS |
| 1000 | 0.15 m mean error | Standard SIR PF; ~80% improvement over 300 |

The key insight from MLPF (MDPI Sensors 2021): better accuracy with 50 particles than a conventional filter achieves with 1000, by folding the measurement likelihood into the proposal distribution. Your KNN fingerprint lookup *is* a likelihood function — the cosine distance to the nearest match can directly weight particle proposals. This is directly applicable.

### RPi 4 estimate

No paper directly benchmarks NumPy particle filters on Cortex-A72, but extrapolating from related work:
- NumPy vectorized operations on a 200-particle × 2D state array are fast; the bottleneck is weight computation (CSI similarity lookup), not propagation
- At 10 Hz with 2 persons, 400 active particles total are well within RPi 4 capacity
- C++ LiDAR-based particle filters achieve 10–20 ms per update at 100–200 particles on an Intel i5; Python overhead on RPi 4 means budget ~40–50 ms per update, which 200 particles fit

### Recommendation

**Default: 200 particles per tracked person.**

- Drop to 100 if CPU exceeds 70% with 2+ persons
- Use **adaptive KLD-sampling**: start at 500 during initialization (global position uncertainty), reduce to 150–200 post-convergence
  - KLD parameters: `kld_err=0.05`, `kld_z=0.99` (from ROS AMCL, proven baseline)
- Avoid a static 1000-particle filter; the accuracy improvement doesn't justify the cost when a good likelihood model is used

**Library:** `pfilter` (github.com/johnhw/pfilter) — minimal NumPy-only API, clean for custom likelihood functions. `filterpy` (github.com/rlabbe/filterpy) is more feature-rich with built-in resampling methods.

---

## 2. Velocity Constraint Model for Indoor Movement

### Human indoor walking speed data

| Gait | Speed |
|---|---|
| Slow walk | 0.82 ± 0.17 m/s |
| Normal | 1.10–1.33 m/s (mean ~1.33 m/s, σ ≈ 0.26 m/s) |
| Brisk | 1.40–1.65 m/s |
| Practical hard cap | 2.0 m/s (indoor; 1.5 m/s for a house) |

### Recommended motion model: Truncated Gaussian random walk with persistence

```python
# Per particle propagation (at 10 Hz, dt = 0.1 s)
SIGMA_V   = 0.4    # m/s noise per step
V_MAX     = 1.5    # m/s hard cap (brisk indoor walk)
ALPHA     = 0.75   # velocity persistence / momentum

# Each step:
noise = np.random.normal(0, SIGMA_V, (n_particles, 2))
velocity = ALPHA * velocity_prev + (1 - ALPHA) * noise
velocity = np.clip(velocity, -V_MAX, V_MAX)
position = position_prev + velocity * dt
```

**Why not constant velocity (CV)?** CV models underestimate maneuverability — people stop abruptly, sit down, turn corners. The Gaussian random walk with persistence is standard in indoor PDR particle filter literature and handles all these transitions naturally.

**Parameter note:** The MLPF paper reports `σ²_v = 0.36` (σ_v ≈ 0.6 m/s) and `σ²_ω = 0.072` for heading noise, consistent with these recommendations. At 10 Hz, a person moves at most ~15 cm between updates at normal walk — this bounds the effective search radius.

---

## 3. Wall-Aware Particle Filter

### Three approaches (ordered by implementation effort)

**Approach A: Weight zeroing (simplest, not recommended)**
- Assign weight = 0 to particles inside wall polygons
- Pros: trivial to implement
- Cons: wastes particles, can cause filter degeneracy near walls

**Approach B: Propagation rejection with retry (recommended)**
- If a particle transition crosses a wall segment, reject and regenerate (up to K=5 tries)
- Use line-segment intersection test against wall geometry loaded at startup
- Papers: "Cost-effective constrained particle filter for indoor localization" (Mirowski et al.), "Floor Map-Aware Particle Filtering" (Ghaoui et al., 2022)

```python
from shapely.geometry import LineString, Point

WALLS = [LineString([p1, p2]) for p1, p2 in house_walls]  # load once

def propagate_with_walls(pos_prev, pos_new, max_retries=5):
    move = LineString([pos_prev, pos_new])
    for wall in WALLS:
        if move.intersects(wall):
            for _ in range(max_retries):
                # resample from motion model and retry
                candidate = sample_motion_model(pos_prev)
                move = LineString([pos_prev, candidate])
                if not any(move.intersects(w) for w in WALLS):
                    return candidate
            return pos_prev  # stay put if all retries fail
    return pos_new
```

**Approach C: Map-constrained occupancy grid (most principled)**
- Pre-compute a "Certainly Empty Space" (CES) binary grid at 10 cm resolution
- Draw new particles only from CES cells; use Bresenham ray marching to cap at walls
- Reference: `mit-racecar/particle_filter` (GitHub) — 2500 particles at 40 Hz in C++ with map constraints

### Recommendation

Start with **Approach B** for Phase 3. Your floor plan is a known static 3500 sq ft house — represent walls as a list of `shapely.LineString` segments loaded at startup. At 200 particles × ~10 wall segments per room × 100 ms, the collision check is negligible CPU cost.

**Upgrade to Approach C** if you want more robust behavior near dense wall clusters (e.g., bathrooms, stairwells). A 10 cm resolution occupancy grid for ~3500 sq ft is ~10,000–15,000 cells — trivially small for NumPy boolean indexing.

**Useful implementation:** NumPy ray/line-segment intersection — `gist.github.com/danieljfarrell/faf7c4cafd683db13cbc`

---

## 4. NMF vs Other Source Separation Methods for Multi-Person CSI

### Benchmark: Same ESP32 hardware, 2026 paper (arXiv 2601.02177)

| Method | Accuracy | F1 | Notes |
|---|---|---|---|
| **NMF** | **56.0%** | **48.0%** | Best across all metrics |
| FastICA | 49.3% | 36.5% | |
| SOBI | 48.0% | 35.5% | |
| Tensor Decomposition | 47.3% | 40.2% | |
| Wavelet | 42.3% | 29.3% | |
| PCA | 39.4% | 31.4% | |

**Why NMF wins:** CSI amplitudes are always non-negative (they are magnitudes of complex channel coefficients: `|I + jQ|`). NMF's non-negativity constraint aligns with this physical property. ICA assumes statistical independence; PCA assumes orthogonality — both are weaker fits for CSI amplitude data.

### The honest ceiling

Even NMF at 56% is not production-reliable. The paper identifies a fundamental intra-subject variability vs inter-subject distinguishability ratio of 73–1,266,000× — environmental noise completely swamps person-to-person differences with commodity hardware. Separation quality degrades sharply above 2 people.

**Your ESP32-S3 advantage:** HT40 mode gives 114 subcarriers vs 52 on standard ESP32 — ~2.2× more spectral information. This modestly improves separation but won't overcome the fundamental ceiling.

### Computational cost on RPi 4

| Method | RPi 4 latency |
|---|---|
| PCA | ~1 ms (sklearn SVD) |
| NMF | ~5–20 ms (sklearn, k=2–3, max_iter=100) |
| FastICA | ~10 ms (sklearn) |
| Tensor Decomposition | 50–200 ms — marginal |

### Recommendation

**Use NMF as primary.** Configure:

```python
from sklearn.decomposition import NMF

# k = number of currently tracked persons
model = NMF(n_components=k, max_iter=100, init='nndsvd', random_state=42)
W = model.fit_transform(amplitude_matrix)  # shape: (n_samples, k)
H = model.components_                      # shape: (k, n_subcarriers)
```

- `init='nndsvd'` gives deterministic, warm-started decomposition (faster convergence than random init)
- Apply to the amplitude matrix stacked across all 4 ESP32-S3 receivers

**For 3+ people:** Fall back to **AoA geometric separation** — with 4 receivers at known positions, the position estimates from each receiver can be fused geometrically to separate people spatially. This is more reliable than blind source separation when 3+ people are present. Widar 2.0 (MobiSys 2018) and IndoTrack achieve 35–55 cm median error with a single WiFi link using joint AoA+Doppler.

**In visualization:** Treat NMF output for 3+ people as a probabilistic hint with low confidence. The dashboard already supports confidence-driven display.

---

## 5. Track Birth and Death (People Entering/Leaving)

### Standard approaches

**Approach A: M-of-N heuristic (recommended)**

```python
# Track lifecycle parameters
CONFIRM_M              = 3   # hits required to confirm
CONFIRM_N              = 5   # frames window for confirmation
DELETE_TENTATIVE_MISSES = 2  # delete unconfirmed after N consecutive misses
DELETE_CONFIRMED_MISSES = 6  # delete confirmed after N consecutive misses

# Birth trigger: CSI anomaly + no matching existing track
BIRTH_ANOMALY_THRESHOLD = 2.5   # z-score above background rolling mean
BIRTH_ANOMALY_DURATION  = 1.5   # seconds of sustained anomaly required
BIRTH_POSITION_SEPARATION = 1.5 # meters from nearest existing track centroid
```

At 10 Hz:
- Confirmation: 0.3–0.5 seconds (M=3 hits in N=5 frames)
- Confirmed track deletion: ~0.6 seconds grace after last detection
- Tentative track deletion: 0.2 seconds (fast false-positive pruning)

**Approach B: Existence probability (JPDA-style)**

Each track maintains `r ∈ [0,1]`. Confirm at `r ≥ 0.8`, delete at `r ≤ 0.1`. More principled but more complex — suitable for Phase 4+.

**Approach C: PHD filter** — theoretically rigorous but overkill for a home system.

### Birth detection for WiFi CSI

The CSI anomaly triggering track creation should be a **sustained** amplitude change, not an instantaneous spike:

```python
# Rolling z-score on subcarrier variance (window = 2 seconds at 10 Hz = 20 frames)
baseline_mean = rolling_mean(subcarrier_variance, window=20)
baseline_std  = rolling_std(subcarrier_variance, window=20)
z_score = (current_variance - baseline_mean) / (baseline_std + 1e-6)

# Require sustained anomaly before creating track
if z_score > BIRTH_ANOMALY_THRESHOLD:
    sustained_frames += 1
    if sustained_frames >= int(BIRTH_ANOMALY_DURATION * fps):
        create_tentative_track()
else:
    sustained_frames = 0
```

### Death detection

A person leaving triggers a sustained *reduction* in CSI variation (room returns toward empty baseline). Fuse this signal with miss count for more reliable deletion than miss count alone:

```python
# CSI returns to empty-room baseline → boost deletion confidence
if csi_variance < EMPTY_ROOM_THRESHOLD:
    confirmed_misses += DEATH_CSI_BONUS  # e.g., +2 per frame when CSI quiet
```

### Track initialization

At birth, initialize particles:
1. **Wide initialization (default):** Uniform across the floor's CES (certainly empty space)
2. **AoA-guided initialization (better):** Gaussian cloud centered on AoA intersection estimate

Wide initialization is safer; the filter converges within 2–5 seconds given good fingerprints. AoA-guided initialization cuts convergence time to ~1 second at the cost of requiring a reliable initial AoA estimate.

---

## Key Libraries

| Library | Use |
|---|---|
| `pfilter` | Minimal NumPy particle filter, clean custom likelihood API |
| `filterpy` | Full-featured PF with SIR, resampling, KLD sampling |
| `mit-racecar/particle_filter` | Production map-constrained PF with ray casting (C++, reference) |
| `sklearn.decomposition.NMF` | Source separation, drop-in, `init='nndsvd'` |
| `shapely` | Floor plan as polygon/line geometry for wall intersection tests |

---

## Sources

- [Dynamic Indoor Localization Using Maximum Likelihood Particle Filtering (MDPI Sensors 2021)](https://pmc.ncbi.nlm.nih.gov/articles/PMC7915836/)
- [SWiLoc: WiFi CSI Indoor Localization (PMC 2024)](https://pmc.ncbi.nlm.nih.gov/articles/PMC11479186/)
- [Why Commodity WiFi Sensors Fail at Multi-Person Gait Identification (arXiv 2601.02177, Jan 2026)](https://arxiv.org/html/2601.02177)
- [NMF vs ICA for Blind Source Separation (Springer)](https://link.springer.com/article/10.1007/s11634-014-0192-4)
- [Cost-effective Constrained Particle Filter for Indoor Localization (ResearchGate)](https://www.researchgate.net/publication/224190518_Cost-effective_constrained_particle_filter_for_indoor_localization)
- [Floor Map-Aware Particle Filtering (HAL/IEEE 2022)](https://hal.science/hal-03916103/document)
- [JPDA Filter with Unknown Detection Probability (PMC)](https://pmc.ncbi.nlm.nih.gov/articles/PMC5795933/)
- [Benchmarking Particle Filter Algorithms for Velodyne-Based Localization (PMC)](https://pmc.ncbi.nlm.nih.gov/articles/PMC6679322/)
- [MIT Racecar Particle Filter (GitHub)](https://github.com/mit-racecar/particle_filter)
- [Widar 2.0: Passive Human Tracking with Single WiFi Link (MobiSys 2018)](https://www.cswu.me/papers/mobisys18_widar2.0_paper.pdf)
- [IndoTrack: Device-Free Indoor Human Tracking (ACM)](https://dl.acm.org/doi/10.1145/3130940)
- [Voronoi Approach for Floor Plan Particle Constraints (Wiley 2018)](https://onlinelibrary.wiley.com/doi/10.1155/2018/5303616)
- [Adaptive Monte Carlo Localization — KLD Sampling (Robotics Knowledgebase)](https://roboticsknowledgebase.com/wiki/state-estimation/adaptive-monte-carlo-localization/)
