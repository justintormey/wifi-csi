# WiFi CSI — Communication Plan & Content Strategy

**Goal:** Position Justin Tormey as a WiFi sensing expert and thoughtful builder. Drive open source engagement. Serve as a portfolio piece for engineering leadership.

**Audience segments:**
1. **Engineers & makers** — Want to learn and build. Care about signal processing, firmware, system design.
2. **Hiring managers & peers** — Evaluating technical depth and communication ability. Care about judgment calls, tradeoffs, scope management.
3. **Open source community** — Potential contributors and users. Care about documentation, approachability, and whether the project is real.

---

## Phase 1: Pre-Launch Content (Before GitHub Goes Public)

### Blog Post 1: Project Announcement ✅ DRAFTED
**File:** `blog-wifi-csi-announcement.md`
**Hook:** "I'm Building a People Tracker That Sees Through Walls — With WiFi"
**Angle:** What WiFi CSI is, what the system does, why it doesn't exist as open source, honest performance expectations.
**Status:** Draft complete. Needs screenshot when dashboard is running, GitHub link when repo is public.

### Blog Post 2: Signal Processing Deep Dive ✅ DRAFTED
**File:** `blog-wifi-csi-signal-processing.md`
**Hook:** "How I Turn WiFi Signals Into a People Tracker"
**Angle:** Full pipeline walkthrough — SpotFi, Hampel, subcarrier selection, KNN, particle filter, breathing/HR extraction. Heavy on math, light on hype.
**Status:** Draft complete. Ready to publish after Post 1.

### LinkedIn Post 1: Teaser
**Timing:** 1-2 weeks before open source launch
**Format:** Short personal post (~150 words)
**Content direction:**
- "I've been building something weird in my spare time."
- Quick hook on WiFi-as-a-sensor concept
- One surprising technical detail (the 114 subcarriers / $8 boards angle)
- "Blog post coming soon" CTA
- No link yet — build curiosity

### LinkedIn Post 2: Announcement Post
**Timing:** Day of Blog Post 1 publish
**Format:** Medium post (~250 words) with dashboard screenshot
**Content direction:**
- Lead with the problem: WiFi sensing exists in papers and behind NDAs, not as open source
- "I built the full stack" — firmware to dashboard
- Link to Blog Post 1
- Tag relevant communities (#SignalProcessing, #IoT, #OpenSource, #ESP32)

---

## Phase 2: Launch Content (Open Source Release)

### Blog Post 3: Early Hardware Results ✅ SKELETON
**File:** `blog/03-early-hardware-results.md`
**Hook:** "First Real Data: What Happens When WiFi CSI Theory Meets Drywall"
**Angle:** Real CSI data from deployed boards — actual accuracy numbers, surprises, tuning changes. Compare predictions vs reality.
**Status:** Skeleton with section outlines. **Blocked on hardware deployment (#56).**

### Blog Post 4: The Open Source Launch Post ✅ DRAFTED
**File:** `blog/04-open-source-launch.md`
**Hook:** "wifi-csi Is Now Open Source — Track People With $200 of Hardware"
**Angle:**
- What's in the repo and how to use it
- Quick start: what you need, what you get
- Architecture overview with diagram
- Contribution areas and roadmap
- Honest "what works / what doesn't yet" section
**Status:** Draft complete. **Blocked on hardware validation and repo going public.**
**Timing:** Day of GitHub public flip

### LinkedIn Post 3: Launch Day
**Format:** Punchy announcement (~200 words)
**Content direction:**
- "Today I'm open-sourcing wifi-csi."
- 3 bullet points: what it does, what it costs, what's unique
- Dashboard screenshot or short GIF
- GitHub link
- "Star it, clone it, or tell me what I got wrong"

### GitHub README Optimization
**Status:** Already drafted (`README.md`)
**Launch checklist:**
- [ ] Add dashboard screenshot/GIF at top
- [ ] Verify quick start instructions work on clean machine
- [ ] Add "Contributing" section with good first issues
- [ ] Add badges (build status, license, Python version)
- [ ] Social preview image for link sharing

---

## Phase 3: Depth Content (Post-Launch, Ongoing)

### Blog Post 4: Firmware Development
**Hook:** "Flashing 12 ESP32 Boards and What I Learned About WiFi CSI at the Metal"
**Angle:**
- ESP-IDF CSI API — what it gives you and what it doesn't
- STA mode vs promiscuous mode (the critical architecture decision)
- HT40 for 114 subcarriers at no extra cost
- Practical firmware lessons: watchdog, LED feedback, MAC registration
- What you discover when theory meets real RF environments
**Timing:** After firmware is battle-tested

### Blog Post 5: Calibration & Deployment
**Hook:** "17 Minutes Per Floor: What Calibrating a WiFi Sensing System Actually Looks Like"
**Angle:**
- The calibration walk: what it is, why it matters, how long it takes
- Fingerprint database internals
- Multi-floor channel separation (channels 1/6/11)
- Dead zones, interference sources, and the bathroom problem
- Before/after accuracy comparison
**Timing:** After first successful full-house deployment

### Blog Post 6: Confidence-Driven Design
**Hook:** "Why My Dashboard Shows 'I Don't Know' Instead of Wrong Answers"
**Angle:**
- Uncertainty as a first-class UX element
- The display gating system for heart rate
- Particle filter convergence → visual confidence
- Fog-of-war for weak signal zones
- Broader lesson: honest engineering vs false precision
- This is the thought-leadership piece — goes beyond WiFi CSI into product philosophy
**Timing:** Flexible — this is the evergreen piece

### LinkedIn Post Series: "Building in Public" Updates
**Frequency:** Every 2-3 weeks during active development
**Format:** Short updates (100-150 words each)
**Topics:**
- First real CSI data from hardware (with plot/screenshot)
- Calibration day — walking the grid
- First successful room-level tracking
- Heart rate working (or not working — honest either way)
- Multi-floor tracking online
- Contributor spotlight (if/when contributions arrive)

---

## Phase 4: Portfolio & Career Positioning

### Portfolio Presentation
**Format:** 1-page project summary for resume/portfolio site
**Key elements:**
- System architecture diagram (hardware → firmware → backend → dashboard)
- Key metrics: $180 hardware, 114 subcarriers, 1-1.5m accuracy, <50ms latency
- Technology depth markers: DSP, embedded systems, real-time web, full-stack
- Link to GitHub repo + blog series
- Screenshot of dashboard

### LinkedIn Profile Updates
- Add WiFi CSI project to "Featured" section
- Update headline to include signal processing / IoT if not present
- Add relevant skills: Signal Processing, ESP32, MQTT, Real-Time Systems

### Conference / Meetup Talk (Optional)
**Title:** "WiFi Sensing With $200 of Hardware: Building an Open Source People Tracker"
**Format:** 20-30 minute technical talk
**Venues:** Local IoT/embedded meetups, PyCon lightning talks, hardware hack nights
**Angle:** Live demo of dashboard + signal processing walkthrough
**Timing:** After system is deployed and stable

---

## Content Calendar Summary

| Phase | Content | File | Status | Depends On |
|-------|---------|------|--------|------------|
| 1 | Blog 1: Announcement | `blog/01-announcement.md` | ✅ **Ready to publish** | Dashboard screenshot |
| 1 | Blog 2: Signal Processing | `blog/02-signal-processing.md` | ✅ **Ready to publish** | Blog 1 published |
| 1 | LinkedIn Teaser | `blog/linkedin-teaser.md` | ✅ **Ready to publish** | Nothing |
| 1 | LinkedIn Announcement | — | 📝 To write | Blog 1 |
| 2 | Blog 3: Early Hardware Results | `blog/03-early-hardware-results.md` | 🔲 Skeleton | Hardware deployment (#56) |
| 2 | Blog 4: Open Source Launch | `blog/04-open-source-launch.md` | ✅ Drafted | Hardware validated, repo public |
| 2 | LinkedIn Launch | — | 📝 To write | Blog 4 |
| 2 | README polish | `README.md` | 🟡 Partially done | Screenshots |
| 3 | Blog 5: Firmware | — | 📝 To write | Firmware battle-tested |
| 3 | Blog 6: Calibration | — | 📝 To write | Full deployment |
| 3 | Blog 7: Confidence Design | — | 📝 To write | Nothing (evergreen) |
| 3 | LinkedIn series | — | 📝 Ongoing | Active development |
| 4 | Portfolio page | — | 📝 To write | System stable |
| 4 | Profile updates | — | 📝 To do | Blog series started |

---

## Voice & Style Guide (WiFi CSI Content)

- **Lead with the interesting thing.** "Your WiFi router is already watching you" beats "In this blog post I will discuss..."
- **Show the math, but explain the intuition.** Include equations. Precede them with plain English.
- **Be honest about limitations.** Heart rate accuracy is 50-60%. Say so. This builds more credibility than hiding it.
- **No buzzwords.** Don't say "AI-powered" (it's DSP, not ML). Don't say "revolutionary" (it's engineering). Don't say "disrupting" anything.
- **Specific numbers over vague claims.** "$8 boards," "114 subcarriers," "±1-2 BPM" — precision signals competence.
- **Builder talking to builders.** Assume the reader is technical. Don't over-explain basics. Do explain non-obvious choices.

---

## Immediate Next Actions

1. **Publish LinkedIn teaser** — `blog/linkedin-teaser.md` is ready, ship now
2. **Publish Blog Post 1** (announcement) — choose platform, add dashboard screenshot, publish
3. **Publish Blog Post 2** (signal processing) — publish 1-2 days after Post 1
4. **Add dashboard screenshots** — run simulator mode, capture for Posts 1 and 4
5. **Draft Blog Post 7** — confidence-driven design thought piece (no engineering dependencies)
6. **Deploy hardware (#56)** — unblocks Posts 3 and 4
