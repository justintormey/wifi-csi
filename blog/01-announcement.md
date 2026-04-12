# I'm Building a People Tracker That Sees Through Walls — With WiFi

Your WiFi router is already watching you. Every signal it sends bounces off walls, furniture, and your body before reaching your phone. Those reflections carry information — a lot of it. Enough to know where you are in the house, which room you're in, and whether you're breathing.

This is WiFi Channel State Information (CSI), and I'm building an open-source system that turns it into real-time people tracking and vital signs monitoring for my three-story house.

## What WiFi CSI Actually Is

When a WiFi frame travels from transmitter to receiver, it doesn't take one path. It takes dozens — bouncing off every surface in the room. The receiver measures how each of these paths distorted the signal across 114 individual frequency sub-channels (called subcarriers). That measurement is CSI.

A person walking through the room changes these paths. The signal distortion shifts. A person standing still changes them too — their chest moves 1-5mm with each breath, and their heartbeat displaces about 0.1mm. Both are detectable in the CSI data if you know what to look for.

The key insight: **WiFi already penetrates your entire home.** You don't need cameras. You don't need wearables. You don't need line-of-sight. The infrastructure is already there — you just need to listen to what it's telling you.

## What I'm Building

The system has three layers:

**Hardware.** 12 ESP32-S3 boards (~$8 each) spread across three floors. Each floor gets one transmitter on the ceiling and three receivers on the walls, all on separate WiFi channels (1, 6, 11) to avoid interference. A Raspberry Pi 4 in the closet runs the brain.

**Signal processing.** The Pi ingests raw CSI at 100Hz, strips clock artifacts with the SpotFi algorithm, runs Butterworth bandpass filters to isolate motion bands from breathing bands from heartbeat bands, and feeds everything into a fingerprint-based localization engine backed by a particle filter. Total hardware cost: under $200.

**Dashboard.** A sci-fi HUD-style web interface that renders tracking positions on SVG floor plans with real-time vital signs. Dark background, cyan accents, scanline overlays. The kind of thing you'd see in a movie — except it's running on a Pi in your house.

<!-- BEFORE PUBLISHING: Add dashboard screenshot — simulator mode showing two people tracked across the ground floor with breathing rate displays. Run `python -m backend.main --simulate` + serve dashboard, then capture. -->

## Why Uncertainty Is the Feature

Here's the thing about WiFi sensing that most research papers gloss over: **the accuracy varies wildly depending on conditions.** A person standing 2 meters from a receiver in a clear room? Great signal. Someone in the garage behind two walls? Noisy. Heart rate while walking? Forget it.

Most systems hide this. I'm making it the core design principle.

High-confidence positions render as sharp, bright dots with tight glow rings. Low-confidence positions blur out — literally becoming fuzzy, ghostly blobs. Areas with weak signal coverage show a "fog of war" overlay. Heart rate only displays when the person has been stationary for 30+ seconds, the signal-to-noise ratio is sufficient, and position confidence exceeds 60%. Otherwise, it's simply not shown.

This isn't a limitation. It's honest engineering. The system tells you what it knows and how well it knows it.

## Why This Doesn't Exist as Open Source

WiFi CSI sensing has been in academic papers since at least 2015. The algorithms work. The hardware is cheap. So why can't you download a working system today?

**The research-to-engineering gap is enormous.** Papers publish MATLAB demos with two antennas in a single room. Nobody assembles the full stack: firmware that extracts CSI from commodity hardware, a signal processing pipeline that handles real-world noise, a tracking engine that works across multiple rooms and floors, and a frontend that actually visualizes all of it.

**Commercial players keep it locked down.** Companies like Origin Wireless and Cognitive Systems have working products, but they're black-box SDKs under NDA. You can't learn from them, extend them, or run them on your own hardware.

**Integration complexity compounds.** Each piece — CSI extraction, phase sanitization, subcarrier selection, fingerprint matching, particle filtering, vital signs extraction — comes from a different paper, a different research group, with different assumptions. Stitching them together into something that runs on a Raspberry Pi is its own engineering challenge.

I want to build the thing I wish existed: a complete, documented, open-source WiFi sensing system that someone with a USB cable and a weekend could deploy.

## Where Things Stand

The dashboard is functional in simulator mode — you can watch simulated people walk between rooms, see their breathing rates change with activity level, and watch the confidence visualization system in action. The simulator generates realistic data: position jitter, signal quality drift, occasional occupancy miscounts when people are close together.

On the backend, the core signal processing primitives are in place (SpotFi phase sanitization, CSI packet deserialization). The tracking engine and vital signs extraction are designed but not yet implemented. The ESP32 firmware hasn't been written yet.

I'm building this in the open. The full codebase, including the dashboard simulator, signal processing pipeline, and eventually the firmware, will be on GitHub.

## Honest Expectations

I want to be upfront about what this system will and won't do:

**Localization accuracy: 1-1.5 meters.** Good enough to know which room someone is in, not precise enough to know which chair. This requires a one-time calibration walk (~17 minutes per floor).

**Breathing rate: ±1-2 BPM.** Reliable when the person is relatively still. The 1-5mm chest displacement per breath is a strong signal.

**Heart rate: ±8-10 BPM, roughly 50-60% of readings usable.** This is the honest number. Lab papers claim 96%+ accuracy, but those results don't survive a real living room with HVAC vibrations, multiple occupants, and varying distances. I'm gating display on strict confidence thresholds rather than showing garbage data with false precision.

**Multi-person tracking: up to 4-5 people.** Beyond that, the signal separation math gets unreliable.

None of these numbers are bad. They're just real.

## Follow Along

I'll be writing more as the project progresses — deep dives into the signal processing, firmware development for the ESP32-S3, lessons from calibrating a three-story house, and the inevitable debugging when theory meets drywall.

If you're interested in WiFi sensing, home automation, signal processing, or just like watching someone build something weird and useful from scratch, stick around.

**The code lives at [github.com/justintormey/wifi-csi](https://github.com/justintormey/wifi-csi).** Star it, clone it, or just lurk. More to come.
