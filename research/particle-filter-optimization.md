# Particle Filter & Tracking Optimization

**Research Question:** What parameters should we use for the indoor particle filter tracking engine given RPi 4 constraints and WiFi CSI input?

**Methodology:** Literature review of indoor particle filter localization papers (2017–2024), IEEE tracking literature, and WiFi CSI multi-person separation benchmarks.

**Deliverable for:** HAL-125 (parent: Phase 2 tracking sprint)

---

## Finding 1: Particle Count — Verdict: 300 PER PERSON, MAX 500

**Question:** Optimal particle count for RPi 4 performance vs accuracy?

### What the Numbers Say

| Particle Count | Mean Error | Notes |
|---------------|-----------|-------|
| 300 | 0.76 m | Real-time deployment threshold; WiFi RSSI fingerprint study |
| 500 | 0.70 m | Good middle ground; 818 m path tracked at this count |
| 1000 | ~0.15 m | Diminishing returns; ~3× CPU cost vs 300 |

**Practical starting point: 300 particles per tracked person.** For 1–3 people simultaneously, total is 300–900 particles — well within RPi 4 capacity.

### CPU Budget Reality Check

At 100Hz CSI input with 300 particles per person running on RPi 4 (quad-core ARM Cortex-A72):
- CSI demodulation: ~1 core
- FastAPI + WebSocket: ~0.5 core
- MQTT broker: ~0.2 core
- Tracker at 5 Hz update rate (decimated from 100 Hz): 300 particles × 3 people = 900 particles/update = negligible

**Recommendation:** Run the tracker at **5 Hz** (decimate from 100 Hz CSI). CSI data is inherently slow (localization changes on 0.1–1 s timescales). This gives the tracker headroom and allows 300-particle counts to remain comfortable.

### MLPF Alternative

The **Maximum Likelihood Particle Filter (MLPF)** collapses predict and update steps so every particle is useful — matching 1000-particle accuracy with as few as 10–50 particles. If 300 particles aren't tracking well in early testing, consider MLPF before increasing particle count. Reference: [MDPI Sensors 2021](https://www.mdpi.com/1424-8220/21/4/1090).

**Sources:**
- [WaP: WiFi-Assisted Particle Filter (ResearchGate)](https://www.researchgate.net/publication/286669860)
- [Indoor Localization via WiFi + IMU Particle Filter (ScienceDirect)](https://www.sciencedirect.com/science/article/pii/S1000936115001995)
- [MLPF: Dynamic Indoor Localization (MDPI Sensors 2021)](https://www.mdpi.com/1424-8220/21/4/1090)

---

## Finding 2: Velocity Constraint Model — Verdict: CV WITH OU DECAY + HARD VELOCITY CAP

**Question:** Best velocity constraint model for indoor movement?

### Motion Model: Constant Velocity (CV) + Ornstein-Uhlenbeck Decay

**State vector:** `[x, y, vx, vy]`

**Motion update:**
```python
# At each particle, per tracker update (dt = 0.2s at 5 Hz):
vx_new = theta * vx + np.random.normal(0, sigma_v)  # OU decay on velocity
vy_new = theta * vy + np.random.normal(0, sigma_v)
x_new = x + vx_new * dt + np.random.normal(0, sigma_x)
y_new = y + vy_new * dt + np.random.normal(0, sigma_x)

# Hard velocity cap
speed = sqrt(vx_new**2 + vy_new**2)
if speed > v_max:
    vx_new, vy_new = vx_new * v_max / speed, vy_new * v_max / speed
```

**Recommended parameters:**

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| `theta` (OU decay) | **0.9** | Velocity decays naturally when person slows/stops; prevents ghost drift |
| `sigma_v` (velocity noise std) | **0.2 m/s** | Typical step-to-step speed variation; tune after hardware testing |
| `sigma_x` (position noise std) | **0.1 m** | Position jitter per update |
| `v_max` (hard cap) | **2.0 m/s** | Indoor sprint; eliminates teleporting particles |
| `a_max` (accel cap) | **2.5 m/s²** | Optional; reject if `|v_new - v_old|/dt > a_max` |

**Why OU decay over pure CV:** Outdoor CV is fine for straight walking. Indoor walking involves frequent stops (at furniture, walls, rooms). OU decay naturally brings velocity toward zero over ~2-3 steps when no motion is detected — preventing stale-velocity ghost tracks from wandering at walking speed after a person has sat down.

**Typical indoor walking speeds (from literature):**
- Preferred indoor walking speed: 0.9–1.3 m/s (most papers use ~1.1 m/s)
- Maximum credible: 2.0 m/s (fast walk/jog)
- Typical during monitoring scenarios (home, office): 0.5–1.2 m/s

**Sources:**
- [A Novel Particle Filter for Indoor Localization (ResearchGate)](https://www.researchgate.net/publication/337627122)
- [Preferred Walking Speed (Wikipedia)](https://en.wikipedia.org/wiki/Preferred_walking_speed)

---

## Finding 3: Wall-Aware Particle Filter — Verdict: WALL REJECTION DURING PROPAGATION

**Question:** Wall-aware particle filter implementations — recommended approaches?

### Three Approaches (Increasing Complexity)

**Strategy 1: Weight Zeroing (Simple but flawed)**
After propagation, set weight=0 for any particle in a non-navigable cell. Fast but can cause particle depletion near walls — too many particles die, filter collapses in narrow corridors.

**Strategy 2: Rejection During Propagation (Recommended)**
Check whether the line segment from old→new position crosses any wall. If it does, keep particle at old position (or resample the motion). Prevents particle depletion.

```python
def propagate_wall_aware(particle, floor_map, dt):
    """Propagate particle; reject move if it crosses a wall."""
    x0, y0 = particle.x, particle.y
    # Apply motion model to get proposed new position
    x1, y1 = apply_motion_model(particle, dt)

    # Check wall crossing via Bresenham line
    if line_crosses_wall(floor_map, x0, y0, x1, y1, resolution=0.1):
        # Keep old position, decay velocity
        particle.vx *= 0.5
        particle.vy *= 0.5
        return particle.x, particle.y
    return x1, y1
```

**Strategy 3: Voronoi/Graph-Constrained (Over-engineering for v1)**
Model corridors as graph edges, rooms as polygons. Good accuracy but complex — skip for Phase 1.

### Floor Plan Representation

**Practical approach:** Render floor plan as PNG bitmap. Pre-compute navigability array at 0.1 m/pixel:

```python
from PIL import Image
import numpy as np

floor_png = Image.open("floor_plan_floor1.png").convert("L")
nav_map = np.array(floor_png) > 128  # True = navigable (white), False = wall (dark)
# Check if (x, y) in meters is navigable:
# nav_map[int(y / 0.1), int(x / 0.1)]
```

Bresenham's line algorithm (O(max(Δx, Δy)) operations) is fast enough for 300–500 particles at 5 Hz.

**Accuracy improvement:** Map-constrained particle filters show ≥20% accuracy improvement over unconstrained, with mean errors under 1.5 m. This is a high-impact, low-complexity addition.

**Sources:**
- [Floor Map-Aware Particle Filter Navigation (Academia)](https://www.academia.edu/74189920)
- [Map Constraint Method for Particle Filter (IEEE)](https://ieeexplore.ieee.org/document/6843284/)
- [Voronoi Floor Plan Approach (Wiley 2018)](https://onlinelibrary.wiley.com/doi/10.1155/2018/5303616)

---

## Finding 4: NMF vs ICA for Multi-Person Separation — Verdict: NMF FOR LOCATION, ICA FOR VITALS

**Question:** NMF vs other source separation methods for multi-person WiFi CSI?

### Benchmark Results (ESP32 Commodity WiFi, 2025)

The arxiv paper [2601.02177](https://arxiv.org/abs/2601.02177) benchmarked 6 methods on ESP32 hardware with 1–10 people:

| Method | Accuracy | Signal Overlap |
|--------|----------|---------------|
| **NMF** | **56.0%** | **11%** (lowest — best) |
| FastICA | 49.3% | — |
| SOBI | 48.0% | — |
| Wavelet | — | 18% |
| PCA | 39.4% | 20% |

**Why NMF wins for location/movement:** Non-negativity constraint matches CSI amplitude data (which is also non-negative). Better sparse representation of person-specific movement signatures.

**Why ICA wins for vital signs:** ICA's statistical independence assumption matches breathing across multiple people (independent oscillators rarely synchronize). The MultiSense paper achieves 0.73 bpm mean error for 4-person breathing using ICA on 3-antenna WiFi.

### NMF Parameters

```python
from sklearn.decomposition import NMF

# n_components = n_people + 1 (extra component = environmental reflections)
nmf = NMF(
    n_components=n_people + 1,
    init='nndsvd',          # deterministic, good convergence for sparse signals
    max_iter=300,           # typically converges by 100
    solver='cd'             # coordinate descent (faster than multiplicative for large matrices)
)
W = nmf.fit_transform(csi_amplitude_matrix)  # (time × subcarriers) → (time × components)
H = nmf.components_                          # (components × subcarriers)
```

### ICA Parameters (for vital signs separation)

```python
from sklearn.decomposition import FastICA

# n_components = number of people (ICA requires n_antennas ≥ n_people)
ica = FastICA(
    n_components=n_people,
    algorithm='parallel',
    fun='logcosh',          # most stable for breathing/quasi-periodic signals
    max_iter=500,
    random_state=42
)
breathing_sources = ica.fit_transform(rx_csi_matrix)  # (time × n_rx_boards) → (time × n_people)
```

**Hard constraint for ICA:** Requires at least as many independent receivers as people. Our Phase 1 setup has 3 RX boards → max 2-person breathing separation. Phase 2 expansion allows more.

### Use Case Summary

| Use Case | Method | k/n_components |
|----------|--------|---------------|
| Occupancy counting | NMF on subcarrier variance | k = 1..max_people (scan) |
| Location fingerprint disambiguation | NMF on CSI amplitude | k = n_people + 1 |
| Motion artifact removal | NMF | k = 2 (signal + artifact) |
| Multi-person breathing separation | ICA | n_components = n_people |

**Sources:**
- [Why Commodity WiFi Fails at Multi-Person Gait ID (arxiv 2601.02177)](https://arxiv.org/abs/2601.02177)
- [MultiSense: Multi-person Respiration via WiFi (ACM UbiComp)](https://dl.acm.org/doi/abs/10.1145/3411816)
- [NMF vs ICA for Blind Source Separation (Springer)](https://link.springer.com/article/10.1007/s11634-014-0192-4)

---

## Finding 5: Track Birth and Death — Verdict: M-OF-N RULE WITH STATE MACHINE

**Question:** How to handle people entering/leaving the space (birth/death of tracks)?

### State Machine

```
TENTATIVE  --[M/N confirmed]--> ACTIVE
ACTIVE     --[P misses]-------> COASTING
COASTING   --[new detection]--> ACTIVE
COASTING   --[timeout]---------> DELETED
```

### Track Birth (Initiation)

**M-of-N Rule:** A tentative track is confirmed when it receives detections in M of the last N updates.

| Environment | M | N | Time to Confirm (at 5 Hz) |
|-------------|---|---|--------------------------|
| Low noise (our home) | 3 | 5 | ~1.0 second |
| High noise | 4 | 6 | ~1.2 seconds |

**Parameters:**
- **M=3, N=5** for Phase 1 (medium noise tolerance — better than M=2,N=3 which triggers on CSI noise spikes)
- Any detection not assigned to an existing track → spawn tentative track at that location
- Tentative track uses same particle filter with fewer particles (50) until confirmed, then expand to 300

### Track Death (Termination)

**Miss-count threshold:** Kill an active track after **5 consecutive missed detections** = ~1.0 second at 5 Hz.

**Coasting period:** Before hard deletion, allow up to **3 seconds of coasting** (dead reckoning via motion model, growing uncertainty). This handles:
- Person walking temporarily out of CSI coverage (NLOS, far corner)
- Momentary signal dropout (packet loss, collision)

**Not a miss if:** Person is in a known low-coverage zone (door threshold, far corner). Flag these zones from the floor plan and increase the miss tolerance in those areas.

### Implementation

```python
class TrackState:
    TENTATIVE = "tentative"
    ACTIVE = "active"
    COASTING = "coasting"
    DELETED = "deleted"

class Track:
    def __init__(self):
        self.state = TrackState.TENTATIVE
        self.recent_detections = []  # ring buffer, last N updates (True/False)
        self.miss_count = 0
        self.coast_frames = 0
        self.particles = ParticleCloud(n=50)  # small until confirmed

    def update(self, detected: bool):
        self.recent_detections.append(detected)
        if len(self.recent_detections) > N: self.recent_detections.pop(0)

        if self.state == TrackState.TENTATIVE:
            if sum(self.recent_detections[-N:]) >= M:
                self.state = TrackState.ACTIVE
                self.particles.expand(n=300)  # promote to full particle count

        elif self.state == TrackState.ACTIVE:
            if not detected:
                self.miss_count += 1
                if self.miss_count >= P:  # P = 5 misses
                    self.state = TrackState.COASTING
                    self.miss_count = 0
            else:
                self.miss_count = 0

        elif self.state == TrackState.COASTING:
            if detected:
                self.state = TrackState.ACTIVE
                self.coast_frames = 0
            else:
                self.coast_frames += 1
                if self.coast_frames > COAST_LIMIT:  # 15 frames = 3 seconds at 5 Hz
                    self.state = TrackState.DELETED
```

**Sources:**
- [Multi-Target Tracking Introduction (MathWorks)](https://www.mathworks.com/help/fusion/ug/introduction-to-multiple-target-tracking.html)
- [Improved Particle Filter for Multi-Target Tracking (MDPI Sensors 2024)](https://www.mdpi.com/1424-8220/24/14/4708)

---

## Summary: Parameter Recommendations

| Component | Recommendation | Key Values |
|-----------|---------------|-----------|
| Particle count | 300/person (real-time), 500 for accuracy | Max 900 total for 3 people |
| Tracker update rate | 5 Hz (decimate from 100 Hz CSI) | Leaves CPU headroom |
| Motion model | CV + OU velocity decay | theta=0.9, v_max=2.0 m/s |
| Velocity noise | Gaussian per step | sigma_v=0.2 m/s, sigma_x=0.1 m |
| Wall awareness | Rejection during propagation | Binary PNG, 0.1 m/pixel, Bresenham check |
| Multi-person location | NMF | k = n_people + 1, nndsvd init |
| Multi-person breathing | ICA | n_components = n_people, logcosh |
| Track birth | M-of-N | M=3, N=5 (~1.0 sec at 5 Hz) |
| Track death | Miss count + coast | P=5 misses, 3 sec coast limit |

---

## Confidence Assessment

| Finding | Confidence | Notes |
|---------|------------|-------|
| 300 particles sufficient for RPi 4 | **High** | Multiple papers validate; direct WiFi fingerprint reference |
| OU decay theta=0.9 | **Medium** | Reasonable indoor pedestrian model; tune after hardware testing |
| v_max=2.0 m/s | **High** | Well-established indoor walking speed cap |
| Wall rejection during propagation | **High** | Superior to weight-zeroing; standard in navigable-map PDR systems |
| NMF for location, ICA for vitals | **High** | Supported by 2025 ESP32 benchmark + MultiSense paper |
| M=3, N=5 birth | **Medium** | Standard value; CSI noise profile may require tuning |
| 5 miss death threshold | **Medium** | Conservative; adjust based on observed false-death rate in Phase 1 |

---

*Research completed: 2026-03-15 | Analyst: Research Analyst agent (HAL-125)*
