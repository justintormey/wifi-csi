# WiFi CSI Dashboard

Real-time people tracking and vital signs visualization for the WiFi CSI system. A sci-fi HUD-themed web dashboard that renders tracked positions, breathing rates, conditional heart rate readings, and signal quality — all with confidence-driven visual fidelity.

## Status

**Phase 1 — In Progress.** The simulator engine and floor plan configuration are complete. HTML layout, CSS theme, SVG floor plans, and visualization modules are still being built.

### What exists today

| File | Purpose |
|------|---------|
| `js/config.js` | Floor layout, room geometry, waypoint graph, signal quality baselines, demo scenarios |
| `js/simulator.js` | Core simulation engine — generates realistic tracking + vitals data at 10Hz |

### Planned (not yet built)

| File | Purpose |
|------|---------|
| `index.html` | Full-screen dashboard layout |
| `css/dashboard.css` | Sci-fi HUD theme (dark bg, cyan/green accents, scanline overlays) |
| `css/floorplan.css` | SVG floor plan styles with glow effects |
| `js/app.js` | Init, WebSocket dispatch, demo mode toggle |
| `js/floorplan.js` | Load/render SVG floor plans |
| `js/tracker-overlay.js` | Tracking dots, trails, pulse animations |
| `js/vitals-panel.js` | Breathing/HR display + sparklines |
| `js/websocket-client.js` | Auto-reconnect WebSocket client |
| `assets/floorplans/floor1.svg` | Ground floor plan |
| `assets/floorplans/floor2.svg` | Second floor plan |
| `assets/floorplans/floor3.svg` | Third floor / attic plan |

---

## Running in Demo Mode

The simulator generates realistic data without any hardware or backend. Once the full dashboard is built, open `index.html` in a browser — it will detect the absence of a WebSocket backend and fall back to the built-in simulator.

### Using the simulator directly (API)

```js
import { Simulator } from './js/simulator.js';

// Demo mode — runs a scripted scenario
const sim = new Simulator({ mode: 'demo', scenario: 'morning_routine' });
sim.onPayload = (payload) => console.log(payload);
sim.start();

// Random mode — random movement for N people
const sim2 = new Simulator({ mode: 'random', personCount: 3 });
sim2.onPayload = (payload) => { /* render */ };
sim2.start();
```

### Available demo scenarios

| Scenario | Description |
|----------|-------------|
| `morning_routine` | One person: living room → kitchen → dining → bathroom → living room |
| `family_evening` | Two people: one cooking, one relaxing, converging at dining table |
| `full_house` | Four people scattered across the ground floor |

### Simulator controls

```js
sim.setSpeed(2.0);                         // 2x real-time
sim.setMode('demo', 'family_evening');     // Switch scenario
sim.reset({ personCount: 4 });             // Reset with new config
sim.stop();                                // Pause
sim.start();                               // Resume
```

---

## WebSocket Payload Format

The simulator (and the real backend) emits JSON payloads at 10Hz over WebSocket (`ws://localhost:8080/ws/tracking`). Every payload describes the state of one floor:

```json
{
  "timestamp": 1710500000.0,
  "floor": 1,
  "people": [
    {
      "id": "p1",
      "x": 5.2,
      "y": 3.8,
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
  "occupancy_confidence": 0.9,
  "zone_signal_quality": {
    "living_room": 0.88,
    "kitchen": 0.72,
    "hallway": 0.45
  }
}
```

### Field reference

| Field | Type | Description |
|-------|------|-------------|
| `timestamp` | float | Unix epoch seconds |
| `floor` | int | Floor ID (1 = ground, 2 = second, 3 = attic) |
| `people[].id` | string | Stable person identifier for this session |
| `people[].x`, `y` | float | Position in meters from top-left origin of floor plan |
| `people[].position_confidence` | float 0–1 | How certain the system is about this position |
| `people[].uncertainty_radius_m` | float | Radius of uncertainty circle in meters (inversely proportional to confidence) |
| `people[].is_stationary` | bool | Whether the person is currently still |
| `people[].stationary_duration_s` | float | Seconds the person has been stationary (0 if moving) |
| `people[].breathing.rate_bpm` | int | Estimated breathing rate in breaths per minute |
| `people[].breathing.confidence` | float 0–1 | Reliability of breathing measurement |
| `people[].heartrate.rate_bpm` | int | Estimated heart rate in BPM |
| `people[].heartrate.confidence` | float 0–1 | Reliability of heart rate measurement |
| `people[].heartrate.display` | bool | **Only render HR when `true`.** Gated by: stationary >30s, SNR sufficient, position confidence >0.6 |
| `occupancy_estimate` | int | Estimated number of people on this floor |
| `occupancy_confidence` | float 0–1 | Reliability of occupancy count (degrades when people are close together) |
| `zone_signal_quality` | object | Per-room signal quality scores (0–1). Drives noise/fog visualization |

---

## Confidence Visualization System

The dashboard's core UX principle: **uncertainty is visible, not hidden.** Every measurement carries a confidence score that directly drives how it renders.

### Position confidence → visual fidelity

| Confidence | Person blob | Uncertainty circle | Trail | Label |
|-----------|-------------|-------------------|-------|-------|
| **High** (>0.8) | Sharp, crisp dot with tight glow | Small (~1m radius) | Solid, bright | "Person 1" |
| **Medium** (0.4–0.8) | Soft-edged, slightly diffuse | Medium (~2-3m radius) | Dashed, dimmer | "~Person" |
| **Low** (<0.4) | Blurred, ghostly, pulsing opacity | Large (~5m+ radius) | None | "?" |

### Signal quality → zone visualization

`zone_signal_quality` values drive per-room visual treatment:

- **High quality zones** — Crisp rendering, full brightness
- **Degraded zones** — Ambient noise clouds (translucent fog-of-war), dimmed overlay
- **Wave distortion lines** — Ripple animations in areas of high signal variance
- **Zone confidence overlay** — Rooms dim/brighten based on aggregate signal quality

### Heart rate display rules

Heart rate is experimental and conditionally displayed. The `heartrate.display` boolean encodes these conditions:

1. Person detected with `position_confidence` > 0.6
2. Person stationary for > 30 continuous seconds
3. Zone signal quality (SNR) above threshold (0.6)

When displayed, HR appears as a subtle secondary reading below breathing rate, with a small signal-strength indicator. When confidence drops, the reading fades out gracefully rather than disappearing.

### Occupancy ambiguity

When the system detects multiple people but can't separate them cleanly:
- Overlapping fuzzy blobs instead of discrete dots
- Approximate count shown ("~2-3 people") rather than precise number
- Affected zone gets an interference pattern overlay

---

## Floor Plan Configuration

Floor plans are defined in `js/config.js`. Each floor entry specifies dimensions, rooms, navigable waypoints, and signal quality baselines.

### Coordinate system

- Origin: **top-left** corner of the floor plan
- Units: **meters**
- Ground floor example: 18.0m × 10.5m (~60ft × 35ft)

### Room definitions

Each room is an axis-aligned rectangle with position, size, and display label:

```js
rooms: {
  kitchen: { x: 7.0, y: 0, w: 5.5, h: 5.5, label: 'Kitchen' },
  // ...
}
```

Rooms are used for:
- Determining which zone a person is in (point-in-rectangle test)
- Looking up base signal quality for that zone
- Labeling zones in the UI

### Waypoint graph

Waypoints define navigable positions (doorways, room centers) connected by edges. The simulator uses BFS pathfinding on this graph to generate realistic movement:

```js
waypoints: {
  kitchen_center: { x: 9.75, y: 2.75, connections: ['kitchen_door', 'dining_door'] },
  kitchen_door:   { x: 7.5,  y: 2.75, connections: ['living_door', 'kitchen_center', 'hall_mid'] },
  // ...
}
```

### Adding a new floor plan

1. **Add the SVG file** to `assets/floorplans/` (e.g., `floor2.svg`). The SVG should have room outlines, walls, doors, and labels. Coordinate system must match the meters-based layout in config.

2. **Add a floor entry** in `CONFIG.floors` in `js/config.js`:

```js
{
  id: 2,
  name: 'Second Floor',
  width: 18.0,
  height: 10.5,
  svgPath: 'assets/floorplans/floor2.svg',
  rooms: {
    master_bedroom: { x: 0, y: 0, w: 6.0, h: 5.0, label: 'Master Bedroom' },
    // ... define all rooms
  },
  waypoints: {
    master_center: { x: 3.0, y: 2.5, connections: ['master_door'] },
    // ... define navigable points and connections
  },
  baseSignalQuality: {
    master_bedroom: 0.85,
    // ... signal quality per room (0.0-1.0)
  },
}
```

3. **Add demo scenarios** (optional) in `DEMO_SCENARIOS` that reference the new floor's waypoints.

4. **Ensure the waypoint graph is connected** — every waypoint must be reachable from every other waypoint via connections. The simulator uses BFS; disconnected subgraphs will cause people to teleport.

### Signal quality baselines

`baseSignalQuality` per room represents how well WiFi CSI covers that zone at rest (no interference). Values range from 0.0 (no coverage) to 1.0 (excellent). These are determined during calibration. Typical values:

- Living room: 0.88 (near TX, good coverage)
- Kitchen: 0.82
- Garage: 0.45 (far from sensors, metal interference)
- Hallway: 0.60 (transitional zone, low dwell time)

The simulator adds Ornstein-Uhlenbeck drift to these baselines to create realistic fluctuation.

---

## Architecture Notes

### Simulator engine (`simulator.js`)

The simulator generates data identical in structure to what the real backend produces. Key design choices:

- **10Hz tick rate** matching the real backend's WebSocket broadcast frequency
- **Waypoint-based pathfinding** (BFS) for realistic room-to-room movement instead of random walks
- **Ornstein-Uhlenbeck process** for smoothly drifting vitals (breathing rate, heart rate, signal quality) — avoids jarring jumps while maintaining natural variation
- **Activity-aware vital ranges** — sleeping, sitting, standing, and walking have distinct breathing and heart rate profiles
- **Confidence modeling** — position confidence degrades for moving targets; heart rate confidence requires prolonged stationarity and good signal quality
- **Occupancy uncertainty** — when simulated people are within 2m of each other, occupancy confidence drops and miscounts become possible

### Two operating modes

| Mode | Behavior |
|------|----------|
| **Demo** | Follows scripted scenarios from `DEMO_SCENARIOS` in config. People execute action queues (idle/move) then loop. Deterministic and repeatable for demos. |
| **Random** | People alternate between random idle periods and random waypoint navigation. Good for stress-testing the dashboard. |

### Noise and jitter

The simulator adds realistic noise:
- **Position wobble** (σ=0.05m) during movement to simulate tracking jitter
- **Position jitter** (σ=0.02m) while idle to simulate CSI measurement noise
- **Signal quality drift** via Ornstein-Uhlenbeck around base values
- **Occupancy miscounts** when people are within 2m proximity (10% chance)
