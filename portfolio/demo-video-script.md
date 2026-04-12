# WiFi CSI Demo Video Script

**Duration:** 2:30–3:00
**Format:** Screen capture of dashboard + voiceover, with brief hardware shots
**Tone:** Conversational, technically confident, no hype

---

## COLD OPEN (0:00–0:15)

*[Screen: Dashboard in dark mode, empty — no tracking data yet. Quiet.]*

**VO:** "Your WiFi router is already watching you. Every signal it sends bounces off your body before reaching your phone. Those bounces carry information — where you are, how fast you're breathing, and sometimes, your heart rate."

*[Dashboard lights up — two tracking dots appear on the floor plan, trails start drawing]*

**VO:** "This is what that looks like when you decode it."

---

## WHAT YOU'RE SEEING (0:15–0:45)

*[Screen: Dashboard running with 2 simulated people. Point out UI elements as mentioned.]*

**VO:** "This is a real-time people tracking system built on WiFi Channel State Information — CSI. Twelve ESP32 boards spread across three floors of my house capture how WiFi signals distort when people move through them."

*[Highlight tracking dot with glow ring]*

**VO:** "Each dot is a person. The glow ring shows confidence — tight and bright means the system is sure. Fuzzy and faded means it's less certain."

*[Person moves to a weak signal area, dot blurs]*

**VO:** "When signal quality drops, the visualization shows it. No fake precision — the system tells you what it knows."

---

## THE SIGNAL PROCESSING (0:45–1:15)

*[Screen: Brief architecture diagram overlay or side panel showing pipeline stages]*

**VO:** "Under the hood, raw CSI comes in at 100 samples per second — 114 frequency subcarriers per sample. That's a 114-dimensional snapshot of the electromagnetic environment, three hundred times a second across the floor."

*[Animate or highlight pipeline: Phase sanitization → Filtering → KNN → Particle filter]*

**VO:** "The pipeline strips clock artifacts with SpotFi, rejects outliers with Hampel filters, selects the most responsive subcarriers, and feeds everything into a fingerprint-based KNN localization backed by a 200-particle filter."

*[Show vitals panel — breathing rate ticking]*

**VO:** "For vital signs, it's a different pipeline. Separate subcarriers, selected for in-band signal-to-noise ratio. Butterworth bandpass isolation. FFT for breathing, continuous wavelet transform for heart rate."

---

## THE HONEST NUMBERS (1:15–1:35)

*[Screen: Stats overlay or clean text cards]*

**VO:** "Let me give you the real numbers — not lab numbers."

**VO:** "Localization: 1 to 1.5 meters. Room-level, not chair-level."

**VO:** "Breathing rate: plus or minus 1 to 2 BPM. Reliable when the person is still."

**VO:** "Heart rate: plus or minus 8 to 10 BPM, with about 50 to 60 percent of readings usable. That's why heart rate only displays when the person has been stationary for 30 seconds, the signal-to-noise ratio is sufficient, and position confidence exceeds 60 percent. Otherwise, it's hidden entirely."

*[Heart rate appears on vitals panel, then fades when person moves]*

---

## THE HARDWARE (1:35–1:55)

*[Cut to: Brief shot of ESP32-S3 boards on desk, or clean hardware photo]*

**VO:** "The hardware is commodity. ESP32-S3 dev boards — about eight dollars each. A Raspberry Pi 4 runs the Python backend. Mosquitto MQTT ties it together. Total cost: under two hundred dollars."

*[Cut back to dashboard]*

**VO:** "No cameras. No wearables. No cloud. Everything runs locally on your network."

---

## MULTI-FLOOR (1:55–2:10)

*[Screen: Click through floor tabs — Floor 1, Floor 2, Floor 3]*

**VO:** "Each floor gets its own WiFi channel — 1, 6, and 11 — so there's no interference between floors. The system detects which floor a person is on by comparing signal perturbation levels. Switch floors, and you see the tracking for that level."

---

## WHY I BUILT THIS (2:10–2:35)

*[Screen: Back to full dashboard view, both people tracked]*

**VO:** "WiFi CSI sensing has been in academic papers since 2015. The algorithms work. The hardware is cheap. But nobody's assembled the full stack as open source — firmware, signal processing, localization, vital signs, and a real dashboard."

**VO:** "I wanted to build the thing I wish existed. A complete system someone could clone, flash, and deploy."

*[Screen: GitHub repo landing page]*

**VO:** "The full codebase is on GitHub. Over 500 tests. Complete documentation — architecture, hardware BOM, calibration guide, installation guide. It works in simulator mode right now, no hardware required."

---

## CTA (2:35–2:45)

*[Screen: GitHub URL, star count]*

**VO:** "Star it, clone it, or tell me what I got wrong. Links in the description."

*[End card: github.com/justintormey/wifi-csi]*

---

## PRODUCTION NOTES

- **Screen capture:** Record dashboard at 60fps in Chrome, dark room for HUD aesthetic
- **Hardware shots:** Clean desk, good lighting, ESP32 boards arranged showing the form factor (brief, 5-10 seconds total)
- **Architecture diagram:** Either a static overlay or animate the pipeline stages in sequence
- **Music:** None, or very minimal ambient — let the dashboard aesthetic speak for itself
- **Captions:** Add burned-in subtitles for accessibility and silent autoplay
- **Thumbnail:** Dashboard screenshot with text overlay "WiFi Sees Through Walls"
