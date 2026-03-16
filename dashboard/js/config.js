/**
 * WiFi CSI Dashboard Configuration
 * Floor layout, room definitions, and connection topology for simulation and rendering.
 * Based on actual floor plans of 14 Charleston Drive.
 *
 * Coordinate system: meters from top-left origin.
 * House is portrait-oriented: ~10.5m wide × 18.0m deep.
 * SVG viewBox: 1050 × 1800 (100px = 1 meter).
 */

export const CONFIG = {
  // WebSocket endpoint (backend or simulator)
  wsUrl: 'ws://localhost:8000/ws/tracking',

  // Simulation defaults
  simulation: {
    tickRateHz: 10,        // Payload output rate
    speedMultiplier: 1.0,  // Time acceleration (2.0 = double speed)
    defaultPersonCount: 2,
    maxPersonCount: 4,
  },

  // Floor definitions — all 3 floors of 14 Charleston Drive
  // Coordinate system: meters from top-left origin.
  // House footprint: ~10.5m wide × 18.0m deep (portrait orientation).
  floors: [
    {
      id: 1,
      name: '1st Floor',
      width: 10.5,
      height: 18.0,
      svgPath: 'assets/floorplans/floor1.svg',
      rooms: {
        garage:       { x: 0,    y: 0,    w: 10.5, h: 5.5,  label: 'Garage' },
        family_room:  { x: 0,    y: 5.5,  w: 10.5, h: 3.5,  label: 'Family Room' },
        dining:       { x: 0,    y: 9.0,  w: 5.5,  h: 4.0,  label: 'Dining Room' },
        kitchen:      { x: 5.5,  y: 9.0,  w: 5.0,  h: 2.5,  label: 'Kitchen' },
        utility:      { x: 8.0,  y: 11.5, w: 2.5,  h: 2.0,  label: 'Utility' },
        hallway:      { x: 3.5,  y: 13.0, w: 4.5,  h: 1.5,  label: 'Hallway' },
        parlor:       { x: 0,    y: 13.0, w: 4.0,  h: 5.0,  label: 'Parlor' },
        office:       { x: 5.5,  y: 13.5, w: 5.0,  h: 4.5,  label: 'Office' },
      },
      waypoints: {
        garage_center:  { x: 5.25, y: 2.75,  connections: ['garage_door'] },
        garage_door:    { x: 5.25, y: 5.25,  connections: ['garage_center', 'family_center'] },
        family_center:  { x: 5.25, y: 7.25,  connections: ['garage_door', 'dining_door', 'kitchen_door', 'hall_mid'] },
        dining_door:    { x: 2.75, y: 9.25,  connections: ['family_center', 'dining_center'] },
        dining_center:  { x: 2.75, y: 11.0,  connections: ['dining_door'] },
        kitchen_door:   { x: 7.75, y: 9.25,  connections: ['family_center', 'kitchen_center'] },
        kitchen_center: { x: 7.75, y: 10.25, connections: ['kitchen_door', 'utility_door'] },
        utility_door:   { x: 8.5,  y: 11.5,  connections: ['kitchen_center', 'utility_center', 'hall_mid'] },
        utility_center: { x: 9.25, y: 12.5,  connections: ['utility_door'] },
        hall_mid:       { x: 5.75, y: 13.5,  connections: ['family_center', 'utility_door', 'parlor_door', 'office_door'] },
        parlor_door:    { x: 3.5,  y: 13.25, connections: ['hall_mid', 'parlor_center'] },
        parlor_center:  { x: 2.0,  y: 15.5,  connections: ['parlor_door'] },
        office_door:    { x: 5.75, y: 13.75, connections: ['hall_mid', 'office_center'] },
        office_center:  { x: 8.0,  y: 15.75, connections: ['office_door'] },
      },
      baseSignalQuality: {
        garage:      0.45,
        family_room: 0.85,
        kitchen:     0.70,
        hallway:     0.88,
        dining:      0.55,
        utility:     0.60,
        office:      0.50,
        parlor:      0.48,
      },
    },
    {
      id: 2,
      name: '2nd Floor',
      width: 9.0,     // ~30 ft (sits above living area, no garage)
      height: 11.0,   // ~36 ft
      svgPath: 'assets/floorplans/floor2.svg',
      rooms: {
        bedroom1:       { x: 0,    y: 0,    w: 4.5,  h: 4.0,  label: 'Bedroom #1' },
        bedroom2:       { x: 4.5,  y: 0,    w: 4.5,  h: 4.0,  label: 'Bedroom #2' },
        guest_bedroom:  { x: 0,    y: 4.0,  w: 3.3,  h: 3.5,  label: 'Guest Bedroom' },
        hallway:        { x: 3.3,  y: 4.0,  w: 2.0,  h: 3.5,  label: 'Hallway' },
        bathroom:       { x: 5.3,  y: 4.0,  w: 3.7,  h: 3.5,  label: 'Bathroom' },
        master_bedroom: { x: 0,    y: 7.5,  w: 5.3,  h: 3.5,  label: 'Master Bedroom' },
        closet:         { x: 5.3,  y: 7.5,  w: 3.7,  h: 3.5,  label: 'Closet' },
      },
      waypoints: {
        bedroom1_center: { x: 2.25, y: 2.0,  connections: ['bedroom1_door'] },
        bedroom1_door:   { x: 1.85, y: 4.0,  connections: ['bedroom1_center', 'hall_north'] },
        bedroom2_center: { x: 6.75, y: 2.0,  connections: ['bedroom2_door'] },
        bedroom2_door:   { x: 7.15, y: 4.0,  connections: ['bedroom2_center', 'hall_north'] },
        hall_north:      { x: 4.3,  y: 4.5,  connections: ['bedroom1_door', 'bedroom2_door', 'hall_mid'] },
        hall_mid:        { x: 4.3,  y: 5.75, connections: ['hall_north', 'hall_south', 'guest_door', 'bathroom_door'] },
        guest_door:      { x: 3.3,  y: 6.0,  connections: ['hall_mid', 'guest_center'] },
        guest_center:    { x: 1.65, y: 5.75, connections: ['guest_door'] },
        bathroom_door:   { x: 5.3,  y: 6.0,  connections: ['hall_mid', 'bathroom_center'] },
        bathroom_center: { x: 7.15, y: 5.75, connections: ['bathroom_door'] },
        hall_south:      { x: 4.3,  y: 7.5,  connections: ['hall_mid', 'master_center', 'closet_door'] },
        master_center:   { x: 2.65, y: 9.25, connections: ['hall_south'] },
        closet_door:     { x: 5.3,  y: 9.5,  connections: ['hall_south', 'closet_center'] },
        closet_center:   { x: 7.15, y: 9.25, connections: ['closet_door'] },
      },
      baseSignalQuality: {
        bedroom1:       0.65,
        bedroom2:       0.75,
        guest_bedroom:  0.55,
        hallway:        0.85,
        master_bedroom: 0.50,
        bathroom:       0.45,
        closet:         0.35,
      },
    },
    {
      id: 3,
      name: 'Basement',
      width: 9.0,     // ~30 ft
      height: 12.0,   // ~40 ft
      svgPath: 'assets/floorplans/floor3.svg',
      rooms: {
        recreation: { x: 0,    y: 0,    w: 4.3,  h: 7.0,  label: 'Recreation Area' },
        workshop:   { x: 4.3,  y: 0,    w: 4.7,  h: 3.2,  label: 'Workshop' },
        bar_area:   { x: 4.3,  y: 3.2,  w: 4.7,  h: 2.8,  label: 'Bar Area' },
        hallway:    { x: 3.8,  y: 6.0,  w: 1.7,  h: 2.0,  label: 'Hallway' },
        storage:    { x: 0,    y: 7.0,  w: 3.8,  h: 5.0,  label: 'Storage' },
        art_studio: { x: 4.3,  y: 6.0,  w: 4.7,  h: 6.0,  label: 'Art Studio' },
      },
      waypoints: {
        rec_center:       { x: 2.15, y: 3.5,  connections: ['rec_workshop_door'] },
        rec_workshop_door:{ x: 4.3,  y: 2.65, connections: ['rec_center', 'workshop_center'] },
        workshop_center:  { x: 6.65, y: 1.6,  connections: ['rec_workshop_door', 'bar_door'] },
        bar_door:         { x: 6.65, y: 3.2,  connections: ['workshop_center', 'bar_center'] },
        bar_center:       { x: 6.65, y: 4.6,  connections: ['bar_door', 'art_door'] },
        art_door:         { x: 6.65, y: 6.0,  connections: ['bar_center', 'art_center', 'hall_east'] },
        art_center:       { x: 6.65, y: 9.0,  connections: ['art_door'] },
        hall_east:        { x: 4.65, y: 7.0,  connections: ['art_door', 'hall_west'] },
        hall_west:        { x: 3.9,  y: 7.0,  connections: ['hall_east', 'rec_south', 'storage_door'] },
        rec_south:        { x: 3.6,  y: 6.5,  connections: ['hall_west', 'rec_center'] },
        storage_door:     { x: 3.6,  y: 9.0,  connections: ['hall_west', 'storage_center'] },
        storage_center:   { x: 1.9,  y: 9.5,  connections: ['storage_door'] },
      },
      baseSignalQuality: {
        workshop:   0.55,
        bar_area:   0.75,
        art_studio: 0.50,
        hallway:    0.82,
        recreation: 0.60,
        storage:    0.40,
      },
    },
  ],
};

/**
 * Scripted demo scenarios for demo mode.
 * Each scenario defines initial person placements and a sequence of actions.
 */
export const DEMO_SCENARIOS = {
  morning_routine: {
    name: 'Morning Routine',
    description: 'One person walks from family room to kitchen, makes coffee, sits in dining room.',
    people: [
      {
        id: 'p1',
        startWaypoint: 'family_center',
        actions: [
          { type: 'idle', duration: 5, activity: 'sleeping' },
          { type: 'move', to: 'kitchen_center' },
          { type: 'idle', duration: 8, activity: 'standing' },
          { type: 'move', to: 'dining_center' },
          { type: 'idle', duration: 20, activity: 'sitting' },
          { type: 'move', to: 'utility_center' },
          { type: 'idle', duration: 5, activity: 'standing' },
          { type: 'move', to: 'family_center' },
          { type: 'idle', duration: 30, activity: 'sitting' },
        ],
      },
    ],
  },
  family_evening: {
    name: 'Family Evening',
    description: 'Two people: one cooking in kitchen, one in family room. They converge in dining room.',
    people: [
      {
        id: 'p1',
        startWaypoint: 'kitchen_center',
        actions: [
          { type: 'idle', duration: 15, activity: 'standing' },
          { type: 'move', to: 'dining_center' },
          { type: 'idle', duration: 20, activity: 'sitting' },
          { type: 'move', to: 'kitchen_center' },
          { type: 'idle', duration: 10, activity: 'standing' },
        ],
      },
      {
        id: 'p2',
        startWaypoint: 'family_center',
        actions: [
          { type: 'idle', duration: 12, activity: 'sitting' },
          { type: 'move', to: 'dining_center' },
          { type: 'idle', duration: 20, activity: 'sitting' },
          { type: 'move', to: 'family_center' },
          { type: 'idle', duration: 15, activity: 'sitting' },
        ],
      },
    ],
  },
  full_house: {
    name: 'Full House',
    description: 'Four people scattered across the 1st floor doing different activities.',
    people: [
      {
        id: 'p1',
        startWaypoint: 'family_center',
        actions: [
          { type: 'idle', duration: 20, activity: 'sitting' },
          { type: 'move', to: 'kitchen_center' },
          { type: 'idle', duration: 10, activity: 'standing' },
          { type: 'move', to: 'family_center' },
        ],
      },
      {
        id: 'p2',
        startWaypoint: 'garage_center',
        actions: [
          { type: 'idle', duration: 15, activity: 'standing' },
          { type: 'move', to: 'hall_mid' },
          { type: 'move', to: 'kitchen_center' },
          { type: 'idle', duration: 10, activity: 'standing' },
          { type: 'move', to: 'garage_center' },
        ],
      },
      {
        id: 'p3',
        startWaypoint: 'dining_center',
        actions: [
          { type: 'idle', duration: 25, activity: 'sitting' },
          { type: 'move', to: 'parlor_center' },
          { type: 'idle', duration: 10, activity: 'sitting' },
          { type: 'move', to: 'dining_center' },
        ],
      },
      {
        id: 'p4',
        startWaypoint: 'office_center',
        actions: [
          { type: 'idle', duration: 20, activity: 'sitting' },
          { type: 'move', to: 'family_center' },
          { type: 'idle', duration: 30, activity: 'sitting' },
        ],
      },
    ],
  },
};
