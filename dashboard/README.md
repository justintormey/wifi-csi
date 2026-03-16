# WiFi CSI Dashboard

Real-time people tracking and vital signs visualization for the WiFi CSI system. A sci-fi HUD-themed web dashboard that renders tracked positions, breathing rates, conditional heart rate readings, and signal quality — all with confidence-driven visual fidelity.

## Quick Start

Open `index.html` in a browser served by any static file server:

```bash
# Python
python3 -m http.server 8000 --directory .

# Node
npx serve .

# Then open http://localhost:8000
```

The dashboard starts in **demo mode** automatically — no backend or hardware required. The built-in simulator generates realistic tracking data at 10Hz.

> **Note:** Opening `index.html` directly via `file://` will fail because `fetch()` is used to load SVG floor plans. A local server is required.

## File Structure

```
dashboard/
├── index.html                  # Full-screen dashboard layout (entry point)
├── css/
│   ├── dashboard.css           # Layout, HUD theme, sidebar panels, status bar
│   └── floorplan.css           # SVG floor plan styles, glow effects, sensor markers
├── js/
│   ├── app.js                  # Init, WebSocket dispatch, rendering pipeline ← sole entry point
│   ├── config.js               # Floor layout, rooms, waypoints, signal baselines, demo scenarios
│   ├── simulator.js            # CSI data simulator (people movement + vitals at 10Hz)
│   ├── websocket-client.js     # Auto-reconnect WebSocket with simulator fallback
│   ├── floorplan.js            # [Standalone] Floor plan renderer with floor switching
│   ├── tracker-overlay.js      # [Standalone] Canvas-based tracking visualization
│   ├── vitals-panel.js         # [Standalone] Vitals panel with sparklines
│   └── noise-overlay.js        # [Standalone] Signal quality noise/fog visualization
└── assets/floorplans/
    ├── floor1.svg              # 1st Floor (garage, family room, kitchen, dining, office, parlor, utility)
    ├── floor2.svg              # 2nd Floor (master bedroom, bedrooms #1/#2, guest bedroom, bathroom, closet)
    └── floor3.svg              # Basement (workshop, bar area, art studio, recreation area, storage)
```

### Architecture Note: Two Rendering Layers

The codebase contains two parallel implementations of the visualization:

1. **Active layer** — `app.js` contains inline DOM-based rendering for tracking dots, vitals, and floor plan loading. This is what currently runs.
2. **Standalone modules** — `floorplan.js`, `tracker-overlay.js`, `vitals-panel.js`, and `noise-overlay.js` are fully implemented canvas/DOM-based class modules that are **not imported by `app.js`**. They represent a richer rendering layer (60fps canvas, sparklines, noise fog) built in parallel but not yet wired in.

Future development should decide which layer to use or integrate the standalone modules into `app.js`.

---

## Demo Mode

### Using the UI

Demo mode is active by default (the "Demo" checkbox in the header is checked). Use the sidebar controls to:

- **Switch scenarios:** Morning Routine, Family Evening, Full House, or Random
- **Adjust speed:** 0.5x, 1x, 2x, or 5x real-time
- **Restart:** Reset the current scenario from the beginning
- **Switch floors:** Click Floor 1/2/3 tabs (only Floor 1 has config; see "Known Limitations")

### Using the Simulator API

```js
import { Simulator } from './js/simulator.js';

// Demo mode — runs a scripted scenario that loops
const sim = new Simulator({ mode: 'demo', scenario: 'morning_routine' });
sim.onPayload = (payload) => console.log(payload);
sim.start();

// Random mode — random movement for N people
const sim2 = new Simulator({ mode: 'random', personCount: 3 });
sim2.onPayload = (payload) => { /* render */ };
sim2.start();

// Controls
sim.setSpeed(2.0);                        // 2x real-time
sim.setMode('demo', 'family_evening');    // Switch scenario
sim.reset({ personCount: 4 });            // Reset with new config
sim.stop();                               // Pause
sim.start();                              // Resume
sim.generatePayload();                    // Single payload, no loop (for testing)
```

### Available Demo Scenarios

| Scenario | People | Description |
|----------|--------|-------------|
| `morning_routine` | 1 | Family room → kitchen → dining room → utility → family room (loops) |
| `family_evening` | 2 | One cooking in kitchen, one in family room, converge at dining room |
| `full_house` | 4 | Four people scattered across the 1st floor doing different activities |
| `random` | 1–4 | Random waypoint navigation with random idle periods |

---

## WebSocket Payload Format

The simulator (and the real backend) emits JSON payloads at 10Hz. Each payload describes the state of one floor:

```json
{
  "timestamp": 1710500000.0,
  "floor": 1,
  "_simulated": true,
  "people": [
    {
      "id": "p1",
      "x": 5.20,
      "y": 3.80,
      "position_confidence": 0.85,
      "uncertainty_radius_m": 1.2,
      "is_stationary": true,
      "stationary_duration_s": 45.0,
      "breathing": {
        "rate_bpm": 16,
        "confidence": 0.78
      },
      "heartrate": {
        "rate_bpm": 72,
        "confidence": 0.55,
        "display": true
      }
    }
  ],
  "occupancy_estimate": 1,
  "occupancy_confidence": 0.90,
  "zone_signal_quality": {
    "living_room": 0.88,
    "kitchen": 0.72,
    "hallway": 0.45
  }
}
```

### Field Reference

| Field | Type | Description |
|-------|------|-------------|
| `timestamp` | float | Unix epoch seconds (`Date.now() / 1000`) |
| `floor` | int | Floor ID (1-indexed, matches `CONFIG.floors[n].id`) |
| `_simulated` | bool | Present and `true` only on simulator payloads; absent from real backend |
| `people[].id` | string | Stable person identifier (`"p1"`–`"p4"` in demo) |
| `people[].x`, `y` | float | Position in meters from top-left origin (2 decimal places) |
| `people[].position_confidence` | float 0–1 | How certain the system is about this position |
| `people[].uncertainty_radius_m` | float | Uncertainty circle radius in meters (inversely proportional to confidence) |
| `people[].is_stationary` | bool | Whether the person is currently still |
| `people[].stationary_duration_s` | float | Seconds the person has been stationary (0 if moving) |
| `people[].breathing.rate_bpm` | int | Estimated breathing rate in breaths per minute |
| `people[].breathing.confidence` | float 0–1 | Reliability of breathing measurement |
| `people[].heartrate.rate_bpm` | int | Estimated heart rate in BPM (always present; ignore unless `display` is `true`) |
| `people[].heartrate.confidence` | float 0–1 | Reliability of heart rate measurement |
| `people[].heartrate.display` | bool | **Authoritative gate for rendering HR.** Only show heart rate when `true`. |
| `occupancy_estimate` | int | Estimated number of people on this floor |
| `occupancy_confidence` | float 0–1 | Reliability of occupancy count (degrades when people are close together) |
| `zone_signal_quality` | object | Per-room signal quality scores (0–1). Keys match `CONFIG.floors[n].rooms` |

### Heart Rate Display Conditions

The `heartrate.display` boolean encodes all of these simultaneously:

1. Person is **stationary** (`is_stationary: true`)
2. Stationary for **>30 continuous seconds** (`stationary_duration_s >= 30`)
3. Heart rate confidence **>0.15**
4. Position confidence **>0.6**

UI consumers should check `heartrate.display` and not independently re-evaluate these conditions.

### WebSocket Connection

Default endpoint: `ws://localhost:8080/ws/tracking` (configured in `config.js`).

The `WebSocketClient` handles connection lifecycle:
- **Exponential backoff** on disconnect: 1s → 2s → 4s → 8s → ... → 30s max
- **Auto-fallback**: If still disconnected after 3 seconds, activates the simulator while continuing reconnect attempts in the background
- **Heartbeat**: Sends `{"type": "ping", "timestamp": ...}` every 15s; any incoming message resets the 10s stale timer
- **Seamless upgrade**: If a real WebSocket connects while simulator is running, the simulator stops and live data takes over

---

## Confidence Visualization System

The dashboard's core UX principle: **uncertainty is visible, not hidden.** Every measurement carries a confidence score that directly drives how it renders.

### Position Confidence → Visual Fidelity (Active Implementation)

| Confidence | CSS Class | Dot Style | Uncertainty Ring | Trail |
|-----------|-----------|-----------|-----------------|-------|
| **High** (≥0.75) | `confidence-high` | Full opacity, no filter | Small ring | Solid polyline (40 points) |
| **Medium** (0.5–0.75) | `confidence-medium` | 85% opacity, 1px blur | Medium ring | Solid polyline (40 points) |
| **Low** (<0.5) | `confidence-low` | 55% opacity, 3px blur | Large ring | Solid polyline (40 points) |

Each person also has a dot animation:
- **Moving**: `dot-ping` — expanding ring pulse (scale 1→2.5, 2s cycle)
- **Stationary**: `dot-breathe` — gentle pulse (scale 1→1.5, 3s cycle)

### Position Confidence → Visual Fidelity (Standalone `tracker-overlay.js`)

The standalone canvas module uses different thresholds (0.4/0.8) and richer rendering:

| Confidence | Blob | Trail | Label |
|-----------|------|-------|-------|
| **High** (>0.8) | Crisp dot, 3-layer radial glow, white highlight | Solid 2px cyan | `"p1"` |
| **Medium** (0.4–0.8) | Soft diffuse dot, dimmer glow | Dashed 1.5px | `"~p1"` (60% opacity) |
| **Low** (<0.4) | Ghostly, pulsing opacity at 3 rad/sec | None | `"?"` (pulsing) |

### Signal Quality → Zone Visualization

`zone_signal_quality` values drive per-room effects in the sidebar via color-coded progress bars:
- `≥ 0.7` → Green ("good")
- `0.5–0.7` → Amber ("fair")
- `< 0.5` → Red ("poor")

The standalone `noise-overlay.js` module (not yet wired in) provides additional visual effects:
- **Zone dimming**: Rooms with low signal quality get a translucent grey overlay
- **Noise clouds**: Fog-of-war particles that drift within degraded zones
- **Ripple rings**: Cyan expanding rings spawn in noisy zones

### Person Colors

Hardcoded per person ID:
- `p1`: `#00fff7` (cyan)
- `p2`: `#00ff88` (green)
- `p3`: `#ff88ff` (magenta)
- `p4`: `#ffaa00` (amber)

---

## Floor Plan System

### Coordinate System

- **Origin:** Top-left corner of the floor plan
- **Units:** Meters
- **Ground floor:** 18.0m × 10.5m (~60ft × 35ft)
- **SVG scale:** 1 meter = 100 SVG units (e.g., `viewBox="0 0 1800 1050"` for 18×10.5m)
- **Aspect ratio:** SVGs use `preserveAspectRatio="xMidYMid meet"` — the coordinate conversion accounts for letterboxing/pillarboxing

### SVG Structure Requirements

Floor plan SVGs must follow this structure:

```svg
<svg viewBox="0 0 {width*100} {height*100}" class="floorplan" data-floor="{id}"
     xmlns="http://www.w3.org/2000/svg">
  <defs>
    <!-- Grid pattern ID must be unique per floor (e.g., grid1, grid2) -->
    <pattern id="grid{id}" width="100" height="100" patternUnits="userSpaceOnUse">
      <path d="M 100 0 L 0 0 0 100" fill="none" stroke="rgba(0,255,255,0.04)" stroke-width="0.5"/>
    </pattern>
  </defs>

  <rect class="floor-bg" width="..." height="..." fill="url(#grid{id})"/>

  <g class="rooms">
    <rect class="room" data-room="room_key" .../>
    <!-- data-room must match CONFIG.floors[n].rooms keys exactly -->
  </g>

  <g class="walls">
    <line class="wall wall-exterior" .../>
    <line class="wall wall-interior" .../>
    <rect class="stairwell" data-stairwell="up|down|both" .../>
  </g>

  <g class="doors">
    <line class="door" .../>             <!-- interior door (green dashed) -->
    <line class="door door-exterior" .../> <!-- exterior door (yellow dashed) -->
  </g>

  <g class="windows">
    <line class="window" .../>
  </g>

  <g class="room-labels">
    <text class="label">Room Name</text>
    <text class="label label-small">Small Room</text>
  </g>

  <g class="sensors">
    <circle class="sensor sensor-tx" .../>  <!-- transmitter (amber/red glow) -->
    <circle class="sensor sensor-rx" .../>  <!-- receiver (green glow) -->
  </g>
</svg>
```

### Adding a New Floor Plan

#### Step 1 — Create the SVG

Place at `assets/floorplans/floor{N}.svg`. The SVG must:
- Have `data-floor="{N}"` matching the config `id`
- Use `viewBox="0 0 {width*100} {height*100}"` (e.g., `0 0 1800 1050` for 18×10.5m)
- Have `class="floorplan"` on the root `<svg>`
- Use a unique grid pattern ID (e.g., `grid4`) — duplicate IDs across floors cause rendering bugs
- Use `data-room` attributes matching the keys in `CONFIG.floors[n].rooms`

#### Step 2 — Add config entry

In `js/config.js`, add an entry to `CONFIG.floors`:

```js
{
  id: N,
  name: 'Floor Name',
  width: 18.0,    // meters — must match SVG viewBox
  height: 10.5,
  svgPath: 'assets/floorplans/floorN.svg',
  rooms: {
    room_key: { x: 0, y: 0, w: 6.0, h: 5.0, label: 'Room Name' },
    // Every room key must also appear in baseSignalQuality
  },
  waypoints: {
    room_center: { x: 3.0, y: 2.5, connections: ['room_door'] },
    room_door:   { x: 6.0, y: 2.5, connections: ['room_center', 'hall_mid'] },
    // Graph must be fully connected — BFS has no recovery for disconnected nodes
  },
  baseSignalQuality: {
    room_key: 0.80,
    // One entry per room key
  },
}
```

**Constraints:**
- Every key in `rooms` must also appear in `baseSignalQuality`
- The waypoint graph must be fully connected (every waypoint reachable from every other via BFS). Disconnected nodes cause people to teleport.
- Room boundaries should not overlap; `findRoom()` returns the first match

#### Step 3 — Add floor tab button

In `index.html`, the tab already exists:

```html
<button class="floor-tab" data-floor="N">Floor N</button>
```

If adding beyond Floor 3, add a new `<button>` inside `#floor-tabs`.

#### Step 4 — Add demo scenarios (optional)

In `js/config.js`, add entries to `DEMO_SCENARIOS` using waypoint IDs from the new floor's config.

### Signal Quality Baselines

`baseSignalQuality` per room represents WiFi CSI coverage quality at rest. Values 0.0–1.0, determined during calibration. The simulator adds Ornstein-Uhlenbeck drift (θ=0.1, σ=0.03) for realistic fluctuation. Typical values:

| Zone | Quality | Notes |
|------|---------|-------|
| Hallway | 0.88 | Central, near TX, excellent coverage |
| Family Room | 0.85 | Main living area, good coverage |
| Kitchen | 0.70 | Good, some appliance interference |
| Utility | 0.60 | Small room, moderate |
| Dining Room | 0.55 | Farther from TX |
| Office | 0.50 | Corner room, walls attenuate |
| Parlor | 0.48 | Far from TX |
| Garage | 0.45 | Far from sensors, metal interference |

---

## Simulator Architecture

### Tick Loop

10Hz base rate (100ms per tick). Speed multiplier adjusts both tick interval and simulated time delta:
- At 2x speed: ticks every 50ms with `dt = 0.2s` simulated time per tick
- Minimum tick interval clamped at 10ms (max effective speed ~10x)

### Movement: Waypoint-Based Pathfinding

People navigate via BFS on the waypoint graph rather than random walks. This produces realistic room-to-room movement through doorways. Walk speed is 1.2 m/s with Gaussian wobble (σ=0.05m) during movement and position jitter (σ=0.02m) while idle.

### Vitals: Ornstein-Uhlenbeck Process

All continuous values (breathing rate, heart rate, signal quality) use the Ornstein-Uhlenbeck mean-reverting process:

```
new_value = current + θ * (target - current) * dt + σ * √dt * gaussian_noise
```

This produces smooth, natural-looking drift around activity-dependent targets without jarring jumps.

**Activity-dependent vital ranges:**

| Activity | Breathing (bpm) | Heart Rate (bpm) |
|----------|----------------|-------------------|
| Sleeping | 12–14 | 58–68 |
| Sitting | 14–17 | 62–78 |
| Standing | 15–18 | 70–85 |
| Walking | 16–20 | — (HR requires stationarity) |

### Occupancy Uncertainty

When simulated people are within 2m of each other:
- Occupancy confidence drops by 0.15 per close pair
- 10% chance of ±1 miscount per close pair

---

## CSS Theme

### Color Palette

| Variable | Value | Usage |
|----------|-------|-------|
| `--bg-primary` | `#0a0a1a` | Main background |
| `--cyan` | `#00ffff` | Primary accent, tracking dots, borders |
| `--green` | `#00ff88` | Secondary accent, doors, breathing vitals |
| `--amber` | `#ffaa00` | Warnings, heart rate, TX sensors |
| `--red` | `#ff3366` | Errors, low confidence |

### Layout

CSS Grid with three rows and two columns:
```
header  header
main    sidebar (280px, 240px below 1280px)
status  status
```

### Visual Effects

- **Scanlines**: `body::after` pseudo-element with a repeating 2px/2px linear gradient at 3% opacity, drifting downward over 8s
- **Corner brackets**: `#floorplan-container::after` with 8 CSS linear gradients positioned at corners (20px L-shapes)
- **Glow**: `text-shadow` and `box-shadow` with cyan/green alpha values throughout

---

## Global API

`app.js` exposes a debug/integration API on `window.CSIDashboard`:

```js
window.CSIDashboard.appState       // Application state (current floor, trails, etc.)
window.CSIDashboard.loadFloorPlan  // Switch floor programmatically
window.CSIDashboard.setDemoMode    // Toggle demo mode
window.CSIDashboard.renderPayload  // Manually inject a payload for rendering
```

---

## Known Limitations

- **Floors 2 and 3**: SVG files and config entries exist for all 3 floors (1st Floor, 2nd Floor, Basement) with waypoints and signal quality baselines.
- **No sparklines in active implementation**: The inline vitals panel in `app.js` does not include sparkline charts. Sparklines exist only in the standalone `VitalsPanel` class.
- **Noise canvas unused**: The `<canvas id="noise-canvas">` is sized on resize but nothing draws to it. The `NoiseOverlay` module is implemented but not wired in.
- **Confidence threshold inconsistency**: `app.js` uses 0.5/0.75 for confidence tiers; `tracker-overlay.js` uses 0.4/0.8.
- **Zone label mapping**: `app.js` has a hardcoded `ZONE_LABEL_MAP` that maps room keys to short display labels. Updated to match actual 14 Charleston Drive rooms.
