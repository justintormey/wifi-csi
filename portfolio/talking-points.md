# WiFi CSI — Key Talking Points

For interviews, conversations, and impromptu discussions about the project.

---

## The 30-Second Pitch

"I built an open-source system that tracks people through walls using WiFi signals. Twelve $8 ESP32 boards spread across my house capture how WiFi distorts when bodies move through it. A Raspberry Pi runs the signal processing — classical DSP, no machine learning — and a real-time dashboard shows where people are, how fast they're breathing, and sometimes their heart rate. Total cost under $200. No cameras, no wearables, no cloud."

---

## Core Talking Points

### 1. The Technology Gap

**Point:** WiFi CSI sensing has been proven in academic papers since 2015, but nobody's built a usable open-source system.

**Detail:** Commercial products (Origin Wireless, Cognitive Systems) keep it locked behind NDAs. Research code is MATLAB demos with two antennas in one room. Nobody has stitched together the full stack — firmware, signal processing, tracking, vital signs, and a real frontend — into something someone could actually deploy.

**Why it matters:** "I built the system I wish existed."

### 2. Full-Stack Engineering Depth

**Point:** This project spans four distinct engineering domains in one integrated system.

**Detail:**
- **Embedded firmware** — C on ESP-IDF, FreeRTOS tasks, WiFi CSI extraction, MQTT serialization
- **Signal processing** — SpotFi phase sanitization, Butterworth filters, KNN localization, particle filtering, FFT/CWT spectral analysis
- **Backend systems** — Python async pipeline, MQTT ingestion, WebSocket broadcasting, systemd deployment
- **Frontend visualization** — Real-time canvas rendering, SVG floor plans, confidence-driven UX

**Why it matters:** Demonstrates ability to work at every layer of a system, from RF physics to pixel rendering.

### 3. Honest Engineering Over Hype

**Point:** The system shows uncertainty instead of hiding it.

**Detail:** Heart rate is only displayed when three conditions are met: the person has been still for 30+ seconds, signal-to-noise is sufficient, and position confidence exceeds 60%. Otherwise, it's hidden entirely. Tracking dots blur when confidence drops. Weak signal zones show fog-of-war.

**Why it matters:** "A system that shows garbage data with false precision is worse than one that says 'I don't know.' Most products optimize for looking impressive in a demo. I optimized for being useful in a house."

### 4. Classical DSP, Not ML

**Point:** The entire pipeline runs on a Raspberry Pi 4 with no GPU.

**Detail:** Phase sanitization is one linear regression per frame. KNN is a 35-dimensional cosine search over 350 entries — under 1ms. The particle filter updates 200 particles in under 1ms. FFT and CWT run on 30-second windows periodically. No training data, no model weights.

**Why it matters:** "Not every signal processing problem needs a neural network. Sometimes the right Butterworth filter and a good particle filter are better than a GPU cluster."

### 5. Hardware Accessibility

**Point:** Under $200 in commodity parts. No specialized equipment.

**Detail:** ESP32-S3 boards are ~$8 each. The Pi is ~$60. Everything connects over standard WiFi. Calibration takes 17 minutes per floor — walk a grid, and the system learns your house.

**Why it matters:** Democratizes technology that was previously either academic or commercially locked down.

---

## Anticipated Questions & Answers

### "How accurate is it really?"

"Room-level — 1 to 1.5 meters. You'll know someone's in the kitchen, not which counter they're at. Breathing is solid at ±1-2 BPM when they're still. Heart rate is the hard one — ±8-10 BPM with about half the readings being usable. That's why I gate display on strict confidence thresholds. The honest number is more useful than a lab number."

### "What about privacy?"

"Everything runs locally. CSI data never leaves your network — there's no cloud, no accounts, no external APIs. I think open-sourcing this is actually better for privacy than the alternative, where only companies with NDAs have the technology. At least here, you can audit every line of code."

### "Why not use cameras or UWB?"

"Different tradeoffs. Cameras need line-of-sight and raise obvious privacy concerns. UWB needs dedicated hardware on every person — it's great for tracking devices, not people. WiFi penetrates walls, uses infrastructure that already exists in every home, and is completely passive. The person being tracked doesn't need to carry or wear anything."

### "Could this be a product?"

"It's a portfolio piece and an open-source contribution, not a product play. The calibration requirement alone is too much for a consumer product. But the core technology is real — it's what companies like Origin Wireless sell behind NDAs. I wanted to show that the full stack can be built by one person with commodity hardware."

### "What was the hardest part?"

"Integration. Every piece of the signal processing pipeline comes from a different paper, a different research group, with different assumptions about the hardware and environment. Making SpotFi talk to Butterworth filters talk to a particle filter talk to a CWT heart rate extractor — all running in real-time on a Pi — that's where the engineering happened. The individual algorithms are well-documented. The system is not."

### "What would you do differently?"

"I'd start with the calibration UX earlier. The fingerprint database is the linchpin — everything downstream depends on it. I also underestimated how much the subcarrier selection strategy matters. Separating tracking subcarriers from vital sign subcarriers improved breathing detection by 20%. That's not in most papers because they don't build integrated systems."

---

## For Technical Audiences

- SpotFi phase sanitization eliminates SFO/STO clock artifacts with a single linear regression per CSI frame
- Separate subcarrier selection for tracking (top 35 by variance) vs. vitals (top 15 by in-band SNR) — this separation alone is a ~20% accuracy gain for breathing
- 200-particle filter with velocity-constrained random walk and systematic resampling at N_eff < 50%
- CWT (Morlet wavelet, ω₀=6.0) for heart rate instead of FFT — better time-frequency localization for drifting heart rate
- Breathing harmonic removal before HR extraction — notch breathing fundamental + 3 harmonics, but only when breathing peak is > 5× median in-band power
- NMF-based occupancy estimation with automatic source count selection via elbow criterion

## For Non-Technical Audiences

- "It's like radar, but using your existing WiFi router instead of a dedicated sensor."
- "Think of it as sonar for WiFi — the signals bounce off people, and the math figures out where they are."
- "The same physics that lets WiFi go through walls also lets it detect what's behind them."
- "It can tell you which room someone is in and how fast they're breathing — without a camera or a wearable."
