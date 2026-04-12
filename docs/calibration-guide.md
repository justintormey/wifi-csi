# Calibration Guide

How to calibrate the WiFi CSI system for accurate position tracking. Calibration builds a fingerprint database that maps CSI signal patterns to known physical locations — without it, the tracker has nothing to match against.

**Audience:** Someone who has completed hardware setup and wants to start tracking.

**Companion docs:**
- [`hardware-setup.md`](hardware-setup.md) — Board placement and mounting
- [`installation.md`](installation.md) — Software setup (RPi, firmware flashing)
- [`architecture.md`](architecture.md) — How the tracking algorithms work

---

## Why Calibration Is Needed

The tracking system uses **fingerprint-based localization** — it compares live CSI readings against a database of CSI readings taken at known positions. This fingerprint database must be built by walking through each room while the system records.

Every home is different. Wall materials, furniture, floor plan geometry, and sensor placement all affect how WiFi signals propagate. The fingerprint database captures your home's unique signal environment.

### When to Recalibrate

| Event | Action Needed |
|-------|--------------|
| **Initial setup** | Full calibration required |
| **Moved furniture significantly** | Recalibrate affected rooms |
| **Moved a sensor board** | Recalibrate the entire floor |
| **Added/removed large metal objects** | Recalibrate nearby rooms |
| **Seasonal changes** | Optional — accuracy may drift slightly over months |
| **Software update** | Not needed (fingerprint format is stable) |

---

## Prerequisites

Before calibrating, confirm:

- [ ] All boards on the target floor are mounted, powered, and showing solid status LEDs
- [ ] MQTT data is flowing: `mosquitto_sub -t 'csi/#' -v` shows binary data
- [ ] Board heartbeats are arriving: `mosquitto_sub -t 'status/#' -v` shows JSON every 10s
- [ ] MAC addresses in `sensors.yaml` match the actual boards
- [ ] House dimensions in `house.yaml` match your floor plan
- [ ] The backend is running: `sudo systemctl status wifi-csi-backend`

---

## Calibration Concepts

### The Fingerprint Grid

Calibration walks a **1-meter grid** across the floor. At each grid point, the system records CSI amplitude patterns from all 3 RX boards for ~3 seconds (300 frames at 100Hz). These recordings become the fingerprint database entries.

```
    0   1   2   3   4   5   6   7   8   9  10  11  12  13  14   (meters)
  0 ·   ·   ·   ·   ·   ·   ·   ·   ·   ·   ·   ·   ·   ·   ·
  1 ·   ·   ·   ·   ·   ·   ·   ·   ·   ·   ·   ·   ·   ·   ·
  2 ·   ·   ·   ·   ·   ·   ·   ·   ·   ·   ·   ·   ·   ·   ·
  3 ·   ·   ·   ·   ·   ·   ·   ·   ·   ·   ·   ·   ·   ·   ·
  ...
 11 ·   ·   ·   ·   ·   ·   ·   ·   ·   ·   ·   ·   ·   ·   ·

Each · = one calibration point (3 seconds of CSI recording)
```

For a 15m × 12m floor: ~15 × 12 = **~180 grid points** (less for rooms with obstacles). At 3 seconds per point, that's roughly **9–12 minutes per floor**.

### What Gets Recorded

At each grid point, the system computes a **feature vector** from the averaged CSI data:

1. **Amplitude mean** across top-30 subcarriers (selected by variance)
2. **Amplitude variance** — how much the signal fluctuates at this position
3. **Phase mean** (after SpotFi sanitization)
4. **Phase standard deviation**

This produces a 120-dimensional feature vector `[mean_amp(30) | var_amp(30) | mean_phase(30) | std_phase(30)]` that uniquely characterizes each position.

### Fingerprint Database Files

Each floor's fingerprint database is saved as a `.npz` file:

```
/opt/wifi-csi/data/fingerprints/
├── floor_1.npz    # Ground floor fingerprints
├── floor_2.npz    # Second floor fingerprints
└── floor_3.npz    # Third floor fingerprints
```

Each file contains:
- `positions`: (N, 2) array of (x, y) coordinates in meters
- `features`: (N, 120) array of feature vectors
- `metadata`: floor ID, timestamp, grid resolution, sensor config hash

Daily backups of these files run automatically at 3 AM (see `deploy/backup-fingerprints.sh`).

---

## Calibration Procedure

### Step 1 — Prepare the Space

1. **Remove temporary obstacles** from walkable areas (shoes, bags, boxes). Permanent furniture should stay — the system needs to learn the signal environment as it normally is.
2. **Close all doors and windows** to the positions they're normally in. If a door is usually open, leave it open during calibration.
3. **Ensure only the calibrator is present** on the floor being calibrated. Other people will distort the CSI readings and corrupt the fingerprints. Pets should also be removed if possible.
4. **Mark your grid** (optional but helpful). Use painter's tape or small stickers at 1-meter intervals along two perpendicular walls to create visual reference points.

### Step 2 — Start Calibration Mode

Trigger calibration for the target floor via the REST API:

```bash
# Start calibration for floor 1
curl -X POST "http://csi-hub.local:8000/api/calibration/start?floor=1"
```

The backend enters calibration mode for the specified floor. Tracking is paused on that floor during calibration.

Check calibration status:

```bash
curl "http://csi-hub.local:8000/api/calibration/status"
```

### Step 3 — Walk the Grid

Starting from one corner of the floor (typically position (0, 0)):

1. **Stand at the grid point** — face the center of the room, stand naturally
2. **Hold still for 3 seconds** — the system collects 300 CSI frames
3. **Listen for the confirmation** (or watch the calibration status endpoint) — the system signals when it has enough data for that point
4. **Move to the next grid point** (1 meter away) — walk naturally, don't rush
5. **Repeat** until the entire floor is covered

**Walking pattern:** Serpentine (back and forth) is most efficient:

```
START → → → → → → → → → → → → → → END of row
                                      ↓
      ← ← ← ← ← ← ← ← ← ← ← ← ←
      ↓
      → → → → → → → → → → → → → → →
      ...
```

### Step 4 — Handle Obstacles

Skip grid points that are:
- Inside walls or permanent fixtures
- Under heavy furniture that nobody walks through (e.g., center of a large couch)
- In areas with no sensor line-of-sight

The KNN algorithm interpolates between nearby fingerprints, so missing a few points in low-traffic areas is fine.

**Do calibrate:**
- Doorways and transitions between rooms
- High-traffic paths (hallways, kitchen-to-living-room routes)
- Stairwell transition zones (critical for floor detection)
- Near walls where you might stand (kitchen counters, desks)

### Step 5 — Verify and Save

After walking the entire floor:

```bash
# Check the calibration result
curl "http://csi-hub.local:8000/api/calibration/status"
```

The system validates the fingerprint database:
- Sufficient spatial coverage (≥80% of expected grid points)
- Feature vector quality (no degenerate fingerprints from sensor dropout)
- Sensor config hash matches current `sensors.yaml`

If validation passes, the fingerprint database is saved automatically to `/opt/wifi-csi/data/fingerprints/floor_{N}.npz`.

### Step 6 — Repeat for Each Floor (Multi-Floor Deployments)

For multi-floor deployments, each floor is calibrated independently with its own fingerprint database. The recommended order:

1. **Floor 1 (Channel 1)** — Calibrate first. This is typically the most-trafficked floor and provides the baseline for validating the system.
2. **Floor 2 (Channel 6)** — Calibrate second. Verify floor detection works when walking from Floor 1 to Floor 2 via the stairwell before proceeding.
3. **Floor 3 / Basement (Channel 11)** — Calibrate last.

**Per-floor differences to note:**
- Each floor uses its own WiFi channel and TX board, so CSI signatures are floor-specific by design. You cannot reuse Floor 1 fingerprints on Floor 2.
- Floor dimensions may differ (check `house.yaml`). The default config uses 18.0m × 10.5m for Floors 1–2 and the same for Floor 3 (Basement), but ceiling heights vary (2.7m vs 2.4m).
- Basement environments often have more metal (ductwork, pipes, support columns) and concrete walls, which create stronger multipath. Expect more calibration points to be needed in metal-heavy areas and slightly lower accuracy (~2m vs ~1.5m).

**Important:** Only one person should be present on the floor being calibrated. People on *other floors* do not affect calibration — the non-overlapping channel design ensures cross-floor CSI isolation.

### Step 7 — Calibrate Stairwell Transition Zones

Stairwell zones are the most critical areas for multi-floor accuracy. The floor detector (`tracker/floor_detector.py`) uses these zones to decide when a person is changing floors.

**Transition zones are defined in `house.yaml`:**

```yaml
transition_zones:
  - name: "Main Stairwell (1st→2nd)"
    floors: [1, 2]
    x_min: 4.0
    x_max: 6.5
    y_min: 3.5
    y_max: 6.5

  - name: "Basement Stairwell (Basement→1st)"
    floors: [3, 1]
    x_min: 6.0
    x_max: 9.0
    y_min: 3.5
    y_max: 6.5
```

**How to calibrate stairwell zones:**

1. **Calibrate the stairwell area on each connected floor.** For the main stairwell (Floors 1↔2), collect fingerprints at the stairwell entrance on Floor 1 *and* at the stairwell exit on Floor 2.
2. **Walk the actual transition path.** Stand at the bottom of the stairs, hold for 3 seconds. Move up 2–3 steps, hold. Continue to the landing and top. Do this during both Floor 1 and Floor 2 calibration sessions.
3. **Cover the full bounding box.** The transition zone is a rectangle — collect fingerprints at the corners and center, not just the stairway path.
4. **Test after calibration.** Walk between floors naturally. The floor detector should transition within 1–3 steps of entering the stairwell. If it's sluggish, check that your `house.yaml` transition zone coordinates match the physical stairwell location.

**Why this matters:** Outside transition zones, the floor detector applies 3-frame hysteresis (it must see 3 consecutive frames of stronger signal from another floor before switching). Inside transition zones, hysteresis drops to 1 frame, allowing rapid floor changes. Bad transition zone boundaries mean the system either misses real floor changes or generates spurious ones.

---

## Zone Recalibration

If only part of a floor needs recalibration (e.g., after rearranging one room), you can recalibrate specific zones without redoing the entire floor.

The zone recalibration process:
1. Walks only the grid points within the specified room boundaries
2. Replaces the affected fingerprints in the existing database
3. Preserves all other fingerprints unchanged

This is significantly faster than full-floor calibration — a single room typically takes 1–3 minutes.

---

## Tips for Good Calibration

### Signal Quality

- **Minimize WiFi traffic** during calibration. Pause downloads, streaming, and other heavy 2.4GHz usage. The cleaner the signal environment, the more distinctive the fingerprints.
- **Don't hold your phone** near your body while calibrating — it can affect CSI readings. Leave it on a table or in your pocket.
- **Calibrate during typical conditions** — if the microwave runs daily at dinner time, that's fine. But don't run it during calibration. The system should learn the "normal" state.

### Body Position

- **Face the same direction** at every point if possible (e.g., always face the center of the room). Body orientation affects CSI — consistency reduces noise in the fingerprint database.
- **Stand naturally** — don't lean, crouch, or hold your arms out. The fingerprints should represent a normal standing person.
- **Stay still** for the full 3-second collection window. Fidgeting adds motion artifacts.

### Coverage

- **Don't skip doorways.** Transitions between rooms are the hardest for the tracker — these fingerprints are the most valuable.
- **Calibrate stairwell zones.** Even a few points at the top and bottom of stairs dramatically improve floor detection.
- **Extra points in high-traffic areas** are better than perfect coverage of rarely-visited spots.

### Validation

After calibration, test the system:
1. Walk through the floor naturally
2. Watch the dashboard — tracking dots should follow your movement
3. Note any rooms where tracking is poor — recalibrate those zones
4. Check that floor detection works when moving between floors

---

## Accuracy Expectations

| Scenario | Expected Accuracy | Notes |
|----------|------------------|-------|
| Open rooms, good coverage | 1–1.5 meters | Best case with 1m grid |
| Hallways and transitions | 1.5–2 meters | Fewer spatial features to differentiate |
| Near walls | 2–3 meters | Multipath reflections reduce fingerprint distinctiveness |
| Far from sensors | 2–4 meters | Weaker signal → noisier fingerprints |
| After furniture change | Degraded until recalibrated | Fingerprints no longer match environment |

Position accuracy is reported as `uncertainty_radius_m` in the tracking data and visualized as the uncertainty ring on the dashboard. High-confidence positions have small rings; low-confidence positions have large rings.

---

## Troubleshooting

| Problem | Likely Cause | Fix |
|---------|-------------|-----|
| Tracking jumps between rooms | Missing doorway fingerprints | Recalibrate doorway transitions |
| Consistently wrong room | Board position in `sensors.yaml` doesn't match reality | Measure actual board positions and update config |
| Poor accuracy everywhere | Insufficient calibration points | Re-walk with denser grid (0.5m spacing in problem areas) |
| Accuracy degrades over weeks | Environmental drift (furniture, seasonal) | Recalibrate affected areas |
| Floor detection incorrect | Missing stairwell zone fingerprints | Calibrate the transition zone boundaries |
| "Sensor config mismatch" error | Board was replaced or moved since calibration | Full floor recalibration required |
| Tracking lost in one room | Sensor dropout in that area | Check board heartbeats; verify RX has line-of-sight to TX |

---

## Fingerprint Database Management

### Backup and Restore

```bash
# Manual backup
/opt/wifi-csi/deploy/backup-fingerprints.sh backup

# List available backups
/opt/wifi-csi/deploy/backup-fingerprints.sh list

# Restore a previous calibration
/opt/wifi-csi/deploy/backup-fingerprints.sh restore fingerprints-20260315-030000.tar.gz
```

The restore command automatically backs up the current database before overwriting, so you can always roll back.

### Inspecting the Database

The fingerprint database is a standard NumPy `.npz` archive:

```python
import numpy as np

db = np.load('/opt/wifi-csi/data/fingerprints/floor_1.npz', allow_pickle=True)
print(f"Points: {db['positions'].shape[0]}")
print(f"Feature dimensions: {db['features'].shape[1]}")
print(f"Metadata: {db['metadata'].item()}")

# Check spatial coverage
positions = db['positions']
print(f"X range: {positions[:,0].min():.1f} – {positions[:,0].max():.1f} m")
print(f"Y range: {positions[:,1].min():.1f} – {positions[:,1].max():.1f} m")
```

### Database Size

Each fingerprint entry is small (~500 bytes). A full floor calibration at 1m resolution:
- ~180 grid points × ~500 bytes = **~90 KB per floor**
- Full 3-floor system: **~270 KB total**

Disk space is not a concern — even a 32GB MicroSD card has room for thousands of recalibrations.

---

## Next Steps

After successful calibration:

1. **Start tracking** — the backend automatically loads the fingerprint database and begins position estimation
2. **Open the dashboard** — tracking dots should appear and follow movement
3. **Observe vital signs** — stand still for >30 seconds in a well-covered zone to see breathing rate; stay stationary >30s for heart rate (when conditions are met)
4. **Fine-tune placement** — if certain rooms have poor accuracy, consider adjusting RX board positions and recalibrating

> **Note:** The calibration system is a planned feature. The walkthrough procedure described here reflects the designed system architecture. Check project status for implementation progress.
