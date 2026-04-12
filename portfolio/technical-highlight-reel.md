# WiFi CSI — Technical Highlight Reel

A curated list of the most impressive engineering decisions and implementations in the project. Use for technical conversations, portfolio review, or conference talk material.

---

## Highlight 1: The Subcarrier Selection Split

**The insight:** Tracking and vital signs need different subcarriers — and using the same set costs ~20% breathing detection accuracy.

**Why it's interesting:** Intuitively, you'd expect the most responsive subcarriers (highest temporal variance) to be best for everything. They're not. High-variance subcarriers are dominated by macro-motion and environmental drift — exactly what drowns out the 0.1mm heartbeat signal. Vital sign subcarriers need high *in-band* SNR, not high total variance.

**Implementation:**
- Tracking: Top 35 subcarriers by temporal variance over a 1-second window
- Vitals: Top 15 subcarriers by in-band SNR (breathing: 0.1–0.5 Hz, heart rate: 0.8–2.0 Hz)
- Two parallel selection paths running on the same CSI stream

**What it demonstrates:** Willingness to challenge assumptions, systematic experimentation, domain understanding.

---

## Highlight 2: Three-Gate Heart Rate Display

**The insight:** Heart rate accuracy matters less than heart rate honesty.

**Why it's interesting:** Lab papers claim 96%+ heart rate accuracy from WiFi CSI. Real-world accuracy is ~50–60%. Instead of showing bad data and hoping users don't notice, the system requires three simultaneous conditions before displaying any heart rate:

1. Position confidence > 0.6 (we know where the person is)
2. Stationary > 30 seconds (motion overwhelms the 0.1mm cardiac signal)
3. In-band SNR ≥ 3 dB (signal is detectable above noise)

When any gate fails, heart rate is *hidden entirely* — not dimmed, not shown with a warning. Hidden.

**What it demonstrates:** Product thinking applied to a technical system. Prioritizing user trust over feature completeness. Understanding that false precision erodes credibility faster than missing data.

---

## Highlight 3: CWT Over FFT for Heart Rate

**The insight:** Heart rate drifts (heart rate variability is real), and FFT smears drifting frequencies.

**Why it's interesting:** FFT treats a 30-second analysis window uniformly. If the heart rate shifts from 72 to 78 BPM during that window, the FFT peak broadens and SNR drops. CWT (Continuous Wavelet Transform) with a Morlet wavelet provides better time-frequency localization — it tracks a shifting frequency without losing the peak.

Additionally, the breathing fundamental and harmonics overlap the heart rate band (person breathing at 0.3 Hz has harmonics at 0.6, 0.9, 1.2 Hz — right in the 0.8–2.0 Hz heart rate range). The system notches breathing harmonics before CWT analysis, but only when the breathing peak exceeds 5× median in-band power — to avoid false-positive notching that would remove actual heart rate frequencies.

**What it demonstrates:** Deep signal processing knowledge. Understanding when standard tools (FFT) fail and when to reach for specialized alternatives (CWT). Defensive engineering around edge cases.

---

## Highlight 4: STA Mode Architecture Pivot

**The insight:** ESP32-S3 can't do promiscuous mode and station mode simultaneously — forcing a complete rethink of the radio architecture.

**Why it's interesting:** The original plan used promiscuous mode (sniffing all WiFi traffic) for CSI extraction. But ESP-IDF makes promiscuous mode mutually exclusive with WiFi station connection — meaning the board couldn't be on the network while capturing CSI.

The fix: all boards connect as normal WiFi stations. The TX board sends 100Hz UDP unicast packets to the RX boards. CSI is extracted from the callback on received frames. This is simpler, more reliable, and gives controlled packet timing.

**What it demonstrates:** Ability to recognize when a fundamental assumption is wrong and pivot the architecture rather than hack around it. Embedded systems pragmatism.

---

## Highlight 5: Confidence-Driven Visualization

**The insight:** Uncertainty should be a first-class visual element, not metadata in a tooltip.

**Why it's interesting:** The particle filter's convergence score (exponential decay of spatial spread) feeds directly into rendering parameters:

- **High convergence (>0.8):** Sharp, bright tracking dot with tight glow ring
- **Medium convergence (0.5–0.8):** Softer dot, wider glow, reduced opacity
- **Low convergence (<0.5):** Ghostly, blurred blob — visually communicating "I'm not sure"
- **Weak signal zones:** Fog-of-war overlay on the floor plan

The math-to-pixel pipeline is direct: particle cloud spread → exponential decay → opacity/blur parameters. No intermediate abstraction.

**What it demonstrates:** UX design informed by algorithm internals. The visualization isn't styled *on top of* the math — it *is* the math, rendered.

---

## Highlight 6: The Full Pipeline on a Raspberry Pi

**The insight:** Classical DSP can outperform ML for structured signal processing problems — and run on a $60 single-board computer.

**Why it's interesting:** The complete pipeline processes 300 CSI packets per second (3 receivers × 100 Hz) through:
- SpotFi phase sanitization: 1 linear regression per frame
- Hampel outlier rejection: O(n × window_size) per subcarrier
- Butterworth bandpass filtering: SOS-form IIR, O(n × filter_order)
- KNN localization: 35D cosine search over 350 fingerprints (<1ms)
- Particle filter: 200 particles updated per frame (<1ms)
- FFT/CWT vital signs: Periodic on 30-second windows

Total compute budget: comfortably within a Pi 4's capacity. No GPU. No cloud offload. No training phase.

**What it demonstrates:** Right-sizing the solution. Not every signal processing problem needs deep learning. Sometimes the right filter, the right search algorithm, and the right spectral estimator are sufficient — and they're interpretable, debuggable, and deployable on constrained hardware.

---

## Highlight 7: NMF Occupancy Detection

**The insight:** When individual positions can't be cleanly separated, matrix factorization can still count the number of independent signal sources.

**Why it's interesting:** Non-negative Matrix Factorization decomposes the CSI variance matrix into independent components — each representing a person. The system sweeps component counts k=1..6 and uses an elbow criterion (stop when reconstruction error improvement drops below 10%) to estimate occupancy.

The key design choice: when occupancy is ambiguous (people within 2m of each other), the dashboard renders overlapping fuzzy blobs rather than making a hard assignment. This is the same "honest uncertainty" principle applied at the occupancy level.

**What it demonstrates:** Applying the right mathematical tool (NMF, not clustering or thresholding) and gracefully degrading when the math reaches its limits.

---

## Highlight 8: 566+ Tests With Realistic Signal Processing Coverage

**The insight:** Signal processing code is notoriously undertested. This project treats DSP like production backend code.

**Why it's interesting:** The test suite includes:
- **Edge cases for every filter:** What happens when all subcarriers have zero variance? When the Hampel window is larger than the data? When phase wraps at boundaries?
- **Realistic CSI fixtures:** Generated data with known ground truth — injected breathing signals at specific frequencies, controlled noise floors, deliberate interference patterns
- **Integration tests:** Full pipeline from raw bytes to WebSocket output, validating that stages compose correctly
- **Performance tests:** Frame budget validation ensuring the dashboard maintains render timing under load
- **WebSocket scenario tests:** 23 scripted scenarios covering reconnection, floor switching, signal degradation, and sustained load

**What it demonstrates:** Engineering discipline. Testing DSP code isn't just "does the FFT run" — it's "does the FFT find a 15 BPM breathing signal buried in 6 dB of noise with Hampel-filtered outliers on selected subcarriers?"

---

## Summary Table

| Highlight | Domain | Core Skill Demonstrated |
|-----------|--------|------------------------|
| Subcarrier split | Signal processing | Domain insight, experimentation |
| Heart rate gating | Product/UX | User trust, honest design |
| CWT over FFT | Signal processing | Tool selection, edge case handling |
| STA mode pivot | Embedded systems | Architecture adaptability |
| Confidence viz | Frontend/UX | Math-to-pixel design integration |
| Pi deployment | Systems engineering | Right-sizing, constraint optimization |
| NMF occupancy | Applied math | Graceful degradation |
| Test suite | Engineering discipline | DSP testing methodology |
