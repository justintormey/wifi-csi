# Custom Floor Plans

How to create your own floor plans for the WiFi CSI dashboard. Three files must be updated in sync: the SVG visual, the JavaScript config, and the backend house config.

**Audience:** Anyone deploying the system in their own home or building.

---

## Overview

The dashboard renders floor plans from three coupled sources:

| File | Purpose |
|------|---------|
| `dashboard/assets/floorplans/floor{N}.svg` | Visual rendering (walls, doors, windows, labels) |
| `dashboard/js/config.js` | Room bounding boxes, waypoints, signal quality hints |
| `backend/config/house.yaml` | Room list, TX channels, transition zones |

All three must agree on room names, dimensions, and coordinate systems.

## Coordinate System

- **Origin:** top-left corner of the floor plan
- **Units:** meters (1 meter = 100 SVG units)
- **Orientation:** landscape recommended — width > height
- **SVG viewBox:** `0 0 {width_m × 100} {height_m × 100}`

Example: a 12m × 8m apartment uses `viewBox="0 0 1200 800"`.

---

## Step 1: Measure Your Space

You need a floor plan with room dimensions in meters. Sources:

- **Existing blueprints/PDFs** — most accurate
- **Tape measure** — measure each room's width and depth
- **Laser measure** — faster for large spaces
- **Phone apps** (MagicPlan, RoomScan) — good enough for initial setup

Determine the overall bounding rectangle (total width × total depth) of each floor.

## Step 2: Create the SVG

Create `dashboard/assets/floorplans/floor{N}.svg`. Use the structure below as a template.

### Required Structure

```xml
<svg xmlns="http://www.w3.org/2000/svg"
     viewBox="0 0 {W} {H}"
     class="floorplan"
     data-floor="{N}">

  <!-- Background grid (optional, cosmetic) -->
  <defs>
    <pattern id="grid{N}" width="100" height="100" patternUnits="userSpaceOnUse">
      <path d="M 100 0 L 0 0 0 100" fill="none" stroke="rgba(0,255,255,0.04)" stroke-width="0.5"/>
    </pattern>
  </defs>
  <rect width="{W}" height="{H}" fill="url(#grid{N})" class="floor-bg"/>

  <!-- Rooms — one rect per room -->
  <g class="rooms">
    <rect x="0" y="0" width="500" height="400" class="room" data-room="living_room"/>
    <rect x="500" y="0" width="300" height="400" class="room" data-room="kitchen"/>
    <!-- ... more rooms ... -->
  </g>

  <!-- Exterior walls -->
  <g class="walls">
    <line x1="0" y1="0" x2="{W}" y2="0" class="wall wall-exterior"/>
    <line x1="{W}" y1="0" x2="{W}" y2="{H}" class="wall wall-exterior"/>
    <line x1="0" y1="{H}" x2="{W}" y2="{H}" class="wall wall-exterior"/>
    <line x1="0" y1="0" x2="0" y2="{H}" class="wall wall-exterior"/>

    <!-- Interior walls (leave gaps for doors) -->
    <line x1="500" y1="0" x2="500" y2="150" class="wall wall-interior"/>
    <line x1="500" y1="250" x2="500" y2="400" class="wall wall-interior"/>
  </g>

  <!-- Doors (drawn in the wall gaps) -->
  <g class="doors">
    <line x1="500" y1="150" x2="500" y2="250" class="door"/>
  </g>

  <!-- Windows (on exterior walls) -->
  <g class="windows">
    <line x1="100" y1="0" x2="300" y2="0" class="window"/>
  </g>

  <!-- Room labels -->
  <g class="room-labels">
    <text x="250" y="200" class="label">Living Room</text>
    <text x="650" y="200" class="label label-small">Kitchen</text>
  </g>

  <!-- Sensor positions -->
  <g class="sensors">
    <circle cx="400" cy="200" r="8" class="sensor sensor-tx"/>
    <circle cx="50" cy="50" r="6" class="sensor sensor-rx"/>
  </g>
</svg>
```

### CSS Classes Reference

| Element | Class | Notes |
|---------|-------|-------|
| Room rect | `room` | Must have `data-room="room_key"` attribute |
| Hallway | `room room-hallway` | Dimmer fill |
| Garage | `room room-garage` | Gray-tinted fill |
| Exterior wall | `wall wall-exterior` | Thick cyan line |
| Interior wall | `wall wall-interior` | Thin cyan line |
| Door | `door` | Dashed green line in wall gap |
| Exterior door | `door door-exterior` | Dashed yellow line |
| Window | `window` | Blue line on exterior wall |
| Stairwell | `stairwell` | Dashed rect, set `data-stairwell="up"/"down"/"both"` |
| Label | `label` | Large centered text. Use `label-small` or `label-tiny` for tight rooms |
| TX sensor | `sensor sensor-tx` | Red glow circle |
| RX sensor | `sensor sensor-rx` | Green glow circle |

### Tips

- **Draw walls as line segments** with gaps where doors go — doors are drawn in the gaps.
- **Room rects can overlap** if rooms are L-shaped. The tracking engine uses config.js bounding boxes for hit-testing, not SVG geometry.
- Sensor circles are cosmetic indicators — actual sensor positions are configured in `backend/config/sensors.yaml`.
- Use a text editor or [Inkscape](https://inkscape.org/) (set document units to px, 100px = 1m).
- The grid pattern ID must be unique per floor (`grid1`, `grid2`, etc.).

## Step 3: Update config.js

Edit `dashboard/js/config.js`. Add or modify a floor entry:

```js
{
  id: 1,                    // Floor number (matches data-floor in SVG)
  name: '1st Floor',
  width: 12.0,              // Total width in meters
  height: 8.0,              // Total height in meters
  svgPath: 'assets/floorplans/floor1.svg',

  // Room bounding boxes (meters from top-left origin)
  rooms: {
    living_room: { x: 0,   y: 0,   w: 5.0, h: 4.0, label: 'Living Room' },
    kitchen:     { x: 5.0, y: 0,   w: 3.0, h: 4.0, label: 'Kitchen' },
    bedroom:     { x: 8.0, y: 0,   w: 4.0, h: 4.0, label: 'Bedroom' },
    bathroom:    { x: 0,   y: 4.0, w: 3.0, h: 4.0, label: 'Bathroom' },
    hallway:     { x: 3.0, y: 4.0, w: 2.0, h: 4.0, label: 'Hallway' },
  },

  // Navigation graph for simulation/demo mode
  waypoints: {
    living_center:  { x: 2.5, y: 2.0, connections: ['living_door'] },
    living_door:    { x: 4.5, y: 4.0, connections: ['living_center', 'hall_mid'] },
    hall_mid:       { x: 4.0, y: 6.0, connections: ['living_door', 'kitchen_door', 'bedroom_door'] },
    kitchen_door:   { x: 5.0, y: 4.0, connections: ['hall_mid', 'kitchen_center'] },
    kitchen_center: { x: 6.5, y: 2.0, connections: ['kitchen_door'] },
    bedroom_door:   { x: 8.0, y: 2.0, connections: ['hall_mid', 'bedroom_center'] },
    bedroom_center: { x: 10.0, y: 2.0, connections: ['bedroom_door'] },
  },

  // Hint for signal quality display (0.0–1.0, higher = better WiFi coverage)
  baseSignalQuality: {
    living_room: 0.85,
    kitchen:     0.70,
    bedroom:     0.60,
    bathroom:    0.45,
    hallway:     0.90,
  },
},
```

### Room Order Matters

`findRoom(x, y)` returns the **first** room whose bounding box contains the point. If rooms overlap (L-shaped layouts), define the inner/smaller room first so it matches before the larger room's bounding box.

### Waypoints

Waypoints are only used by the **simulator/demo mode** — they define a navigation graph for simulated people to walk along. Each waypoint needs:

- `x`, `y` — position in meters
- `connections` — array of other waypoint names this one connects to (bidirectional)

Place waypoints at room centers and doorways. If you don't need demo mode, you can leave waypoints empty (`waypoints: {}`).

## Step 4: Update house.yaml

Edit `backend/config/house.yaml`:

```yaml
floors:
  1:
    name: "1st Floor"
    tx_channel: 1          # WiFi channel for this floor's TX board
    dimensions:
      width_m: 12.0
      depth_m: 8.0
      height_m: 2.7        # Ceiling height
    rooms:
      - name: "Living Room"
      - name: "Kitchen"
      - name: "Bedroom"
      - name: "Bathroom"
      - name: "Hallway"
```

If you have multiple floors with stairwells, add transition zones:

```yaml
transition_zones:
  - name: "Main Stairwell (1st→2nd)"
    floors: [1, 2]
    x_min: 3.0
    x_max: 5.0
    y_min: 3.5
    y_max: 5.5
```

## Step 5: Update sensors.yaml

Edit `backend/config/sensors.yaml` with your actual sensor board positions (meters from top-left of each floor). Place the TX sensor near the center of the floor and RX sensors at the perimeter.

## Step 6: Verify

1. **Start the dashboard** — `cd dashboard && python3 -m http.server 8080`
2. **Check rendering** — floor plan should display with correct room layout
3. **Test simulation** — click "Demo" to verify waypoint paths work
4. **Check signal panel** — all rooms should appear with signal quality bars

If the SVG fails to load, the dashboard falls back to an auto-generated placeholder that draws room rectangles from config.js — this is useful for initial layout testing before you've drawn the detailed SVG.

---

## Quick Start (Minimal Setup)

For the fastest path to a working floor plan:

1. Measure your floor's overall width and height
2. Measure each room's position and size
3. Add rooms to `config.js` (the placeholder renderer will draw them)
4. Add rooms to `house.yaml`
5. Skip the SVG — the auto-generated placeholder works for initial testing
6. Create a detailed SVG later when you want walls, doors, and windows

The system works fine with placeholder floor plans. The SVG is purely visual — tracking accuracy depends on sensor placement and calibration, not floor plan detail.

---

## Tools

- **Inkscape** (free) — set canvas to px, use 100px = 1m scale
- **Figma** (free tier) — export as SVG, ensure viewBox is set correctly
- **Text editor** — SVGs are XML; simple layouts can be typed by hand
- **Browser dev tools** — inspect the rendered SVG to debug positioning
