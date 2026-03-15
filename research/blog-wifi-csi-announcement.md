# Tracking People Through Walls With WiFi — No Cameras Required

*Your WiFi router already sees everyone in your house. I'm building a system that makes sense of what it sees.*

---

Every WiFi packet your router sends bounces off walls, furniture, and people before reaching your phone. Those bounces leave fingerprints — measurable distortions in the signal's amplitude and phase across dozens of frequency subcarriers. This is called **Channel State Information (CSI)**, and it's been hiding in plain sight inside every WiFi chip for years.

When a person walks through a room, CSI changes dramatically. When someone sits still and breathes, their chest moves 1-5mm per breath — enough to create a periodic ripple in CSI amplitude. Even a heartbeat's ~0.1mm of chest displacement leaves a detectable trace, if you know where to look.

I'm building an open-source system that uses CSI to track people across all three floors of my house in real time, estimate breathing rates, and — when conditions are right — detect heart rate. No cameras. No wearables. Just WiFi signals you're already broadcasting.

## What I'm actually building

The hardware is almost comically simple: **12 ESP32-S3 boards (~$8 each) and a Raspberry Pi 4.** Each floor gets one transmitter on the ceiling sending packets at 100Hz, and three receivers on the walls extracting CSI from those packets. Total hardware cost: around $180.

The interesting part is the software pipeline:

**Signal processing** — Raw CSI from the ESP32-S3 gives you 114 subcarriers of complex I/Q data per packet. That's 114 noisy measurements of how the wireless channel looks right now, 100 times per second. Phase sanitization (SpotFi algorithm) strips out clock artifacts. Butterworth bandpass filters isolate the frequency bands that matter: 0.1-0.5Hz for breathing, 0.8-2Hz for heart rate.

**Localization** — A fingerprint database maps "what CSI looks like from this spot" to physical coordinates. K-nearest-neighbors matching gives a position estimate. A particle filter smooths the trajectory so people don't teleport between frames. Target accuracy: 1-2 meters — not GPS-precise, but enough to know which room someone is in and roughly where they're standing.

**Vital signs** — Once someone is stationary, the system switches from tracking mode to vital signs extraction. FFT on a 30-second sliding window pulls out the dominant breathing frequency. Continuous wavelet transform (Morlet) picks up the subtler heart rate signal. The system is honest about confidence — heart rate only shows up when the person has been still for 30+ seconds and the signal-to-noise ratio is actually sufficient.

**Dashboard** — A sci-fi HUD-style web interface with floor plans, tracking dots, and vital signs panels. The visualization encodes uncertainty directly: high-confidence tracks are crisp dots with solid trails; low-confidence detections are ghostly blurs with pulsing opacity. Signal noise shows up as fog-of-war clouds over poorly-covered zones. Uncertainty is visible, not hidden.

## Why this doesn't exist yet

WiFi sensing is a real, active research field. Hundreds of papers. Companies like Origin Wireless and Cognitive Systems have commercial products. So why isn't there a complete, open-source implementation you can clone and run?

**Research code is throwaway code.** Academic papers publish results, not systems. The code behind a SIGCOMM paper was written to produce a figure, not to run 24/7 in someone's house. It's MATLAB scripts with hardcoded paths, not a deployable pipeline.

**Commercial systems are black boxes.** Origin, Cognitive, and others license WiFi sensing to ISPs and enterprise customers. The algorithms are proprietary. The SDKs are under NDA. If you're not a Qualcomm partner, you're not getting access.

**The gap is integration.** The individual pieces — CSI extraction on ESP32, phase sanitization, fingerprint localization, vital signs FFT — each exist in some form across scattered repos and papers. But nobody has stitched them together into a working system with a real UI, real multi-floor support, and real confidence-aware visualization.

That's the project: take the research, do the engineering, ship it as something any technical person can actually deploy.

## Where it stands

The project plan and signal processing pipeline are validated. The dashboard simulator is working — it generates realistic multi-person tracking data with proper vital signs physics, so I can build and iterate on the frontend without touching hardware. The simulator models activity-dependent vital ranges, confidence decay, signal quality zones, and multi-person ambiguity. It's not a toy demo; it's a realistic preview of what the real system will produce.

Phase 1 is the dashboard and simulator. Phase 2 is backend signal processing. Then firmware, then the full hardware deployment.

## Follow along

I'll be writing about the technical details as I build each layer — the signal processing, the localization algorithms, the firmware quirks, the inevitable debugging of why Floor 2's bathroom is a dead zone. The full project will be open-sourced when it's polished.

If you're interested in WiFi sensing, signal processing, or just enjoy watching someone build something unnecessarily ambitious with $180 worth of microcontrollers, stay tuned.

<!-- TODO: Add dashboard screenshot gallery when simulator mode is running -->
<!-- TODO: Add GitHub repo link when public -->
<!-- TODO: Add newsletter/RSS link for follow-along -->

---

*Tags: WiFi CSI, signal processing, ESP32, IoT, open source, people tracking*
