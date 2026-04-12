# WiFi CSI Open Source Launch Strategy

**Task:** [HAL-183](/HAL/issues/HAL-183)
**Goal:** Coordinate a launch that gets attention across HackerNews, Reddit, Twitter/X, and LinkedIn.
**Timing:** Execute 1 week before planned launch date. All dates below are relative to **Launch Day (L)**.

---

## Launch Timeline

| Day | Channel | Action |
|-----|---------|--------|
| L-14 | LinkedIn | Publish teaser post (already drafted: `linkedin-teaser-post.md`) |
| L-7 | Blog | Publish Blog Post 1: "I'm Building a People Tracker That Sees Through Walls — With WiFi" |
| L-7 | LinkedIn | Publish announcement post linking to Blog 1 |
| L-3 | GitHub | Final repo prep: README polish, badges, social preview, license, "good first issue" labels |
| L-1 | Blog | Publish Blog Post 2: "How I Turn WiFi Signals Into a People Tracker" (signal processing deep dive) |
| **L** | GitHub | Flip repo to public |
| **L** | Blog | Publish Blog Post 3: Open source launch post |
| **L** | HackerNews | Submit "Show HN" post (morning, ~10am ET Tuesday) |
| **L** | Reddit | Cross-post to target subreddits (stagger by 2-3 hours) |
| **L** | Twitter/X | Publish launch thread |
| **L** | LinkedIn | Publish launch day post with GitHub link |
| L+1 | All | Monitor and respond to comments. Post follow-up replies where engagement is high. |
| L+3 | LinkedIn | Post first "building in public" update with early traction metrics |
| L+7 | Blog | Publish Blog Post 4 or confidence-design thought piece, depending on what resonated |

---

## HackerNews Strategy

### Title Options (Ranked)

1. **"Show HN: Track people through walls with $200 of WiFi hardware (open source)"**
2. "Show HN: Open-source WiFi CSI — people tracking and vital signs with ESP32 boards"
3. "Show HN: I built an open-source people tracker using WiFi signal distortion"

**Why option 1:** "Track people through walls" is the visceral hook. "$200 of WiFi hardware" makes it tangible and accessible. "open source" signals it's not a product pitch.

### Timing

- **Day:** Tuesday or Wednesday. Avoid Monday (backlog noise) and Friday (low weekend engagement).
- **Time:** 10:00-11:00 AM Eastern. HN traffic peaks mid-morning US time. European readers are still online (afternoon there).
- **Never:** Weekend, holiday, or day of major tech news.

### Post Format

```
Show HN: Track people through walls with $200 of WiFi hardware (open source)

URL: https://github.com/justintormey/wifi-csi
```

HN "Show HN" posts link directly to the repo or blog post. The repo README is strong enough to stand alone — it has the architecture diagram, algorithm table, quick start, and honest limitations section. Link to GitHub, not the blog.

### First Comment (Post Immediately After Submission)

Post a top-level comment within 60 seconds of submission. This sets the framing before anyone else does:

```
Hey HN — I'm Justin. I've been building this for [X weeks].

WiFi CSI (Channel State Information) lets you detect people, breathing, and heart rate
from the signal distortion that human bodies cause in standard WiFi packets. The algorithms
have been in academic papers since 2015, but nobody's assembled the full stack as open source.

What's here:
- ESP32-S3 firmware for CSI extraction via MQTT
- Python signal processing pipeline (SpotFi phase sanitization, Butterworth filters,
  fingerprint KNN, particle filter tracking, FFT/CWT vital signs)
- Sci-fi HUD dashboard that works standalone with a built-in simulator
- RPi deployment scripts (systemd, Mosquitto, mDNS)
- 440+ tests

Honest limitations: heart rate is ~50-60% reliable (not the 96% papers claim),
localization is 1-2 meters (room-level, not chair-level), and it hasn't been
tested end-to-end with real hardware yet.

Happy to answer questions about the signal processing, hardware choices, or
why I think WiFi sensing is underexplored as open source.
```

### Response Strategy for Common HN Questions

| Likely Question | Prepared Response Direction |
|----------------|---------------------------|
| "Privacy implications?" | This runs entirely local — no cloud, no cameras. CSI data never leaves your network. But yes, passive RF sensing raises real questions. I think open source is better than black-box commercial products here — you can audit exactly what it does. |
| "How is this different from [commercial product]?" | Origin Wireless, Cognitive Systems exist but are NDA'd black boxes. This is the full stack, documented, and you can flash it yourself for $200. |
| "Why not use cameras/LiDAR/UWB?" | Different tradeoffs. WiFi penetrates walls (whole-home from one setup), has zero marginal cost (uses existing infrastructure), and is passive. Cameras need line-of-sight and raise privacy concerns. UWB needs dedicated hardware on every person. |
| "Does heart rate really work?" | At ~50-60% reliability with strict gating, yes — but only when stationary >30s with good SNR. I'm gating display on confidence rather than showing garbage data. The blog post on signal processing goes into the math. |
| "What about multi-path / NLOS?" | That's actually the signal source, not noise. CSI measures how multi-path changes when bodies move. NLOS is a feature — it's why this works through walls. |
| "ESP32 CSI quality?" | ESP32-S3 with HT40 gives 114 subcarriers. Not as clean as Intel 5300 or Atheros, but dramatically cheaper and sufficient for room-level tracking. The research note covers the tradeoffs. |

---

## Reddit Strategy

### Target Subreddits (Ordered by Fit)

| Subreddit | Subscribers | Post Angle | Format |
|-----------|------------|------------|--------|
| r/homeautomation | ~800K | "Track occupancy and presence without cameras" | Link post to GitHub |
| r/esp32 | ~120K | "ESP32-S3 WiFi CSI extraction — open source people tracking" | Link post + technical summary comment |
| r/dsp | ~45K | "Full WiFi CSI signal processing pipeline — SpotFi, Butterworth, particle filter" | Self post with algorithm details |
| r/machinelearning | ~3M | "WiFi CSI people tracking: when classical DSP beats ML" | Self post, frame as DSP vs ML discussion |
| r/raspberry_pi | ~850K | "RPi-powered WiFi sensing hub — real-time people tracking dashboard" | Link post with deployment focus |
| r/homeassistant | ~600K | "WiFi-based presence detection — open source alternative to mmWave sensors" | Self post, frame as HA integration potential |
| r/selfhosted | ~500K | "Self-hosted people tracking using WiFi signals — no cloud, no cameras" | Link post, emphasize local-only |
| r/electronics | ~1M | "12x ESP32-S3 board deployment for WiFi CSI sensing" | Link post, hardware focus |

### Reddit Posting Rules

- **Stagger posts by 2-3 hours.** Don't carpet-bomb. Start with r/homeautomation and r/esp32 (highest fit), then branch out.
- **Customize each post title and body for the subreddit.** r/dsp wants algorithm details. r/homeautomation wants "what can this do for my house." r/selfhosted wants the no-cloud angle.
- **Respond to every comment in the first 6 hours.** This is where Reddit engagement lives or dies.
- **Never say "my project" in more than 2 subreddits.** Cross-posting is fine, but if it reads like self-promotion spam, mods will nuke it.
- **Check each subreddit's self-promotion rules before posting.** Some require a history of non-promotional participation.

---

## Twitter/X Thread Script

### Thread (7 tweets)

**Tweet 1 (Hook):**
```
Your WiFi router can already see you.

Every signal it sends bounces off your body. Those bounces encode where you
are, how fast you're breathing, and (sometimes) your heart rate.

I built an open-source system that decodes all of it. 🧵
```

**Tweet 2 (What it is):**
```
WiFi CSI (Channel State Information) measures signal distortion across 114
frequency subcarriers at 100Hz.

A person walking through a room? Massive CSI shift.
Standing still and breathing? 1-5mm chest displacement, detectable.
Heartbeat? 0.1mm — detectable when conditions are right.
```

**Tweet 3 (Hardware):**
```
The hardware:
- 12x ESP32-S3 boards (~$8 each)
- 1x Raspberry Pi 4
- USB cables

Total cost: ~$200

Each floor gets 1 transmitter + 3 receivers on separate WiFi channels.
The Pi runs the signal processing pipeline.
```

**Tweet 4 (The pipeline):**
```
Signal processing stack:
→ SpotFi phase sanitization
→ Butterworth bandpass filters
→ Fingerprint KNN localization (K=5)
→ Particle filter smoothing (200 particles)
→ FFT breathing extraction
→ CWT heart rate estimation

All Python. All documented. All open source.
```

**Tweet 5 (Honest limitations):**
```
Honest numbers (not lab numbers):

📍 Localization: 1-2m accuracy (room-level, not chair-level)
🫁 Breathing: ±1-2 BPM (reliable when still)
❤️ Heart rate: ±8-10 BPM, ~50-60% usable (gated on confidence)
👥 Multi-person: up to 4-5 people

The dashboard shows uncertainty as a visual — low-confidence positions literally blur out.
```

**Tweet 6 (Dashboard + screenshot):**
```
The dashboard is a sci-fi HUD that works standalone with a built-in simulator.

Dark theme. Cyan glow. Scanline overlays. SVG floor plans for all 3 floors.
Tracking dots with trails, breathing rates, heart rate (when confident enough to show).

[SCREENSHOT]
```

**Tweet 7 (CTA):**
```
The repo is live:
github.com/justintormey/wifi-csi

- 440+ tests
- Full docs (architecture, hardware BOM, calibration guide)
- Works in simulator mode — no hardware needed to explore

Star it, clone it, or tell me what I got wrong.

Blog deep dive: [link to Blog Post 1]
```

### Hashtags (Use Sparingly)
`#OpenSource` `#WiFi` `#ESP32` `#SignalProcessing` `#IoT`

Only on tweet 7. Hashtag-heavy threads look spammy.

---

## GitHub Repo Preparation Checklist

### Before Launch (L-3)

- [ ] **License:** Add MIT license (most permissive, lowest friction for adoption). Create `LICENSE` file and update README badge.
- [ ] **Repository description:** "Real-time indoor people tracking and vital signs monitoring using WiFi Channel State Information (CSI). ESP32-S3 + Raspberry Pi. Open source."
- [ ] **Topics:** `wifi-csi`, `esp32`, `signal-processing`, `iot`, `people-tracking`, `vital-signs`, `raspberry-pi`, `indoor-localization`, `python`, `dsp`
- [ ] **Social preview image:** Create a 1280x640 image for link previews. Should show: dashboard screenshot overlaid with project name and the tagline "Track people through walls with WiFi." Dark background matching the HUD theme.
- [ ] **Dashboard screenshot:** Capture simulator mode with 2-3 people tracked, vitals visible, confidence indicators active. Use for README hero image, social preview, and all launch posts.
- [ ] **Badges in README:** Build/test status (GitHub Actions), license, Python version, code coverage if available.
- [ ] **"Good first issue" labels:** Create 5-8 issues tagged `good first issue` for newcomers:
  - Add Home Assistant integration (MQTT presence sensor)
  - Implement fingerprint database export/import CLI
  - Add dark/light theme toggle to dashboard
  - Create Docker Compose setup for backend + Mosquitto
  - Write ESP32-S3 board auto-detection script
  - Add CSV export for CSI data recordings
  - Dashboard: add floor switching keyboard shortcuts
  - Backend: add Prometheus metrics endpoint
- [ ] **CONTRIBUTING.md:** Short guide — dev setup, test commands, PR guidelines. Keep it under 50 lines.
- [ ] **Issue templates:** Bug report and feature request templates.
- [ ] **GitHub Actions:** Basic CI that runs `pytest` on push. Green badge on README.

---

## Response Plan

### First 24 Hours (Critical Window)

**Staffing:** Justin monitors all channels. Budget 4-6 hours of active engagement on launch day.

**Priority order:**
1. HackerNews comments (highest leverage — top comments shape perception)
2. GitHub issues and stars (signal quality of the repo)
3. Reddit threads (respond to all top-level comments)
4. Twitter/X replies (engage with quote tweets and substantive replies)
5. LinkedIn comments (least time-sensitive)

**Response principles:**
- Respond to technical questions with specific, cited answers (link to code, papers, or docs)
- Acknowledge valid criticism immediately ("Good point — I should document that")
- Convert good suggestions into GitHub issues on the spot ("Filed as #42, thanks")
- Don't argue with trolls. One factual correction, then move on.
- If someone finds a bug, thank them and fix it live if possible. Nothing builds credibility faster.

### Prepared Quick Responses

**"Cool project, starred"** → "Thanks! If you try the simulator mode, I'd love to hear what you think of the dashboard."

**"Have you seen [competitor/similar project]?"** → Acknowledge it, explain what's different. Never trash other projects.

**"This is a privacy nightmare"** → "Totally understand the concern. This runs 100% local — no cloud, no data leaves the network. I think open-sourcing it is better than letting only companies with NDAs have this technology. At least here you can audit every line."

**"Why not just use [cameras/mmWave/BLE]?"** → Brief tradeoff comparison. Don't argue superiority — explain the niche (whole-home coverage, passive, no per-person hardware, through-wall).

---

## Metrics to Track

### Week 1 Targets

| Metric | Source | Target | Stretch |
|--------|--------|--------|---------|
| GitHub stars | GitHub | 200 | 500+ |
| GitHub forks | GitHub | 25 | 50+ |
| GitHub issues opened | GitHub | 10 | 20+ |
| Blog page views (Post 1) | Analytics | 2,000 | 5,000+ |
| HN points | HackerNews | 100 | 300+ |
| HN comments | HackerNews | 30 | 80+ |
| Reddit total upvotes (all subs) | Reddit | 300 | 1,000+ |
| Twitter/X thread impressions | Twitter/X | 10,000 | 50,000+ |
| LinkedIn post impressions | LinkedIn | 5,000 | 15,000+ |
| LinkedIn post engagement rate | LinkedIn | 3% | 5%+ |
| New LinkedIn connections from post | LinkedIn | 20 | 50+ |

### Month 1 Targets

| Metric | Target |
|--------|--------|
| GitHub stars | 500+ |
| Pull requests received | 5+ |
| Blog series total views | 10,000+ |
| Contributors (non-Justin) | 3+ |

### Tracking Tools

- **GitHub:** Native insights (traffic, clones, referring sites)
- **Blog:** Whatever analytics Justin uses (Google Analytics, Plausible, or Cloudflare)
- **HN:** Manual check or use hnrankings.info
- **Reddit:** Native post analytics per subreddit
- **Twitter/X:** Native analytics
- **LinkedIn:** Native post analytics

---

## Pre-Launch Checklist (Owner: Justin)

### Content Ready (L-7)
- [ ] Blog Post 1 finalized with dashboard screenshot
- [ ] Blog Post 2 finalized
- [ ] Blog Post 3 drafted (fill in GitHub link on L-day)
- [ ] LinkedIn teaser published (L-14)
- [ ] LinkedIn announcement published (L-7)
- [ ] Twitter/X thread drafted in thread composer

### Repo Ready (L-3)
- [ ] README has hero screenshot
- [ ] LICENSE file added (MIT)
- [ ] Badges working (CI green)
- [ ] Social preview image uploaded
- [ ] Topics set
- [ ] Good first issues created and labeled
- [ ] CONTRIBUTING.md added
- [ ] Issue templates added
- [ ] CI pipeline passing
- [ ] Quick start instructions verified on clean machine

### Launch Day (L)
- [ ] Flip repo to public
- [ ] Publish Blog Post 3
- [ ] Submit HN "Show HN" post + first comment
- [ ] Post to Reddit (staggered)
- [ ] Publish Twitter/X thread
- [ ] Publish LinkedIn launch post
- [ ] Block 4-6 hours for engagement

### Post-Launch (L+1 to L+7)
- [ ] Respond to all HN/Reddit/Twitter comments
- [ ] Convert feedback into GitHub issues
- [ ] Fix any bugs found by community
- [ ] Post follow-up LinkedIn update with traction metrics
- [ ] Publish next blog post based on what resonated
