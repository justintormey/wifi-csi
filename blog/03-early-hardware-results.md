# First Real Data: What Happens When WiFi CSI Theory Meets Drywall

<!-- STATUS: BLOCKED on hardware deployment (half-bakery #56). This is an outline/skeleton. Fill in with real data once ESP32-S3 boards are deployed and producing CSI data. -->

Your WiFi router has been telling me where I am for [X] weeks now. Here's what the data actually looks like — the good, the surprising, and the humbling.

---

## The Setup

<!-- Describe the actual Phase 1 deployment: 4x ESP32-S3 boards on Floor 1, positions, the Pi running in the closet. Include photos of mounted boards. -->

**Hardware deployed:**
- 1x ESP32-S3 transmitter (ceiling center, Floor 1)
- 3x ESP32-S3 receivers (walls, chest height)
- 1x Raspberry Pi 4 (closet, running the backend)
- Channel 1, HT40 mode, 114 subcarriers at 100Hz

**Calibration:** [X]-minute walk across Floor 1, [X] grid points at 1m spacing.

---

## What the Raw CSI Looks Like

<!-- Include actual CSI amplitude plots over time. Show:
1. Empty room baseline (quiet)
2. Person walking through (dramatic shifts)
3. Person sitting still (subtle breathing oscillation)
4. Multiple people

Use matplotlib captures from the backend data. -->

---

## Localization: The Room-Level Promise

<!-- Report actual accuracy numbers from calibration validation:
- Mean error in meters
- Per-room accuracy (which rooms are easy vs hard)
- Where the dead zones are
- How it compares to the 1-1.5m prediction
-->

### What Worked

<!-- Specific rooms/scenarios where tracking was accurate -->

### What Didn't

<!-- Dead zones, multi-path confusion, areas where accuracy degraded -->

---

## Breathing Rate: The Reliable Vital Sign

<!-- Report actual breathing detection results:
- Accuracy vs reference (count manually or use a reference device)
- SNR distribution across subcarriers
- Which subcarriers were selected as "best" by the in-band SNR metric
- How quickly after sitting still does a reliable reading appear?
-->

---

## Heart Rate: The Hard Truth

<!-- Report actual heart rate results:
- What percentage of readings passed the triple gate?
- Accuracy of passing readings vs reference (smartwatch, pulse ox)
- Did the 50-60% usability prediction hold?
- Were there systematic biases?
- What was the biggest surprise?
-->

---

## The Calibration Walk

<!-- Describe the actual experience of calibrating:
- How long did it take? (Predicted: 17 min)
- What was the UX like?
- Were there areas that needed recalibration?
- Tips for anyone else doing this
-->

---

## Surprises

<!-- Things you didn't expect from real data:
- RF interference sources (microwave? Bluetooth? neighbors?)
- Environmental effects (HVAC, pets, furniture moved)
- Time-of-day variation
- Anything the simulator got wrong
-->

---

## Tuning Parameters

<!-- Which parameters in vitals.yaml needed adjustment from defaults?
- Hampel filter windows
- Subcarrier selection counts
- Particle filter settings
- Confidence thresholds
- Use the vitals_benchmark.py results
-->

---

## Updated Numbers

| Metric | Predicted | Actual |
|--------|-----------|--------|
| Localization accuracy | 1-1.5m | ? |
| Breathing rate | +/-1-2 BPM | ? |
| Heart rate | +/-8-10 BPM, ~50-60% usable | ? |
| Calibration time | ~17 min | ? |
| End-to-end latency | 30-50ms | ? |

---

## What I'm Changing

<!-- Based on real data, what needs to change in the pipeline or configuration?
- Algorithm adjustments
- Hardware placement changes
- New features needed
- Things to document differently
-->

---

## What's Next

Multi-floor expansion: 8 more boards on Floors 2 and 3. Channels 6 and 11. Cross-floor tracking. And the moment of truth for the floor detection algorithm.

The full codebase — including all the tuning changes from this deployment — is at [github.com/justintormey/wifi-csi](https://github.com/justintormey/wifi-csi).

---

*This is the third post in a series. Previously: [project announcement](/blog/wifi-csi-announcement), [signal processing deep dive](/blog/wifi-csi-signal-processing). Next: open source release with full system demo.*
