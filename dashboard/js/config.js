/**
 * WiFi CSI Dashboard Configuration
 * Floor layout, room definitions, and connection topology for simulation and rendering.
 * Based on actual floor plans of 14 Charleston Drive.
 *
 * Coordinate system: meters from top-left origin.
 * House is landscape-oriented: ~18.0m wide × 10.5m deep.
 * SVG viewBox: 1800 × 1050 (100px = 1 meter).
 */

export const CONFIG = {
  // WebSocket endpoint — auto-detects protocol from page (wss:// on HTTPS, ws:// on HTTP)
  wsUrl: typeof location !== 'undefined'
    ? `${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/ws/tracking`
    : 'ws://localhost:8000/ws/tracking',

  // Simulation defaults
  simulation: {
    tickRateHz: 10,        // Payload output rate
    speedMultiplier: 1.0,  // Time acceleration (2.0 = double speed)
    defaultPersonCount: 2,
    maxPersonCount: 4,
  },

  // Floor definitions — all 3 floors of 14 Charleston Drive
  // Coordinate system: meters from top-left origin.
  // All floors share same footprint: 18.0m wide × 10.5m deep (landscape).
  //
  // Room order matters for findRoom() — it returns the first bounding-box match.
  // For L-shaped rooms (e.g. Family Room wrapping around Garage), define the
  // inner room first so it matches before the outer room's larger bounding box.
  floors: [
    {
      id: 1,
      name: '1st Floor',
      width: 18.0,
      height: 10.5,
      svgPath: 'assets/floorplans/floor1.svg',
      rooms: {
        parlor:       { x: 0,    y: 0,    w: 4.5,  h: 4.5,   label: 'Parlor' },
        office:       { x: 0,    y: 5.5,  w: 4.5,  h: 5.0,   label: 'Office' },
        hallway:      { x: 4.5,  y: 3.5,  w: 1.5,  h: 3.0,   label: 'Hallway' },
        utility:      { x: 4.5,  y: 6.5,  w: 1.5,  h: 2.0,   label: 'Utility' },
        dining:       { x: 6.0,  y: 0,    w: 3.5,  h: 5.5,   label: 'Dining Room' },
        kitchen:      { x: 6.0,  y: 5.5,  w: 3.0,  h: 5.0,   label: 'Kitchen' },
        garage:       { x: 13.0, y: 3.5,  w: 5.0,  h: 7.0,   label: 'Garage' },
        family_room:  { x: 9.5,  y: 0,    w: 3.5,  h: 10.5,  label: 'Family Room' },
      },
      waypoints: {
        parlor_center:  { x: 2.25,  y: 2.25,  connections: ['parlor_door'] },
        parlor_door:    { x: 3.5,   y: 4.5,   connections: ['parlor_center', 'hall_mid'] },
        office_center:  { x: 2.25,  y: 8.0,   connections: ['office_door'] },
        office_door:    { x: 3.5,   y: 5.5,   connections: ['office_center', 'hall_mid'] },
        hall_mid:       { x: 5.25,  y: 5.0,   connections: ['parlor_door', 'office_door', 'dining_door', 'utility_center'] },
        utility_center: { x: 5.25,  y: 7.5,   connections: ['hall_mid'] },
        dining_door:    { x: 6.0,   y: 4.25,  connections: ['hall_mid', 'dining_center'] },
        dining_center:  { x: 7.75,  y: 2.75,  connections: ['dining_door', 'family_door_n'] },
        kitchen_center: { x: 7.5,   y: 8.0,   connections: ['family_door_s'] },
        family_door_n:  { x: 9.5,   y: 3.75,  connections: ['dining_center', 'family_center'] },
        family_door_s:  { x: 9.5,   y: 6.25,  connections: ['kitchen_center', 'family_center'] },
        family_center:  { x: 11.25, y: 5.25,  connections: ['family_door_n', 'family_door_s', 'garage_door'] },
        garage_door:    { x: 15.0,  y: 3.5,   connections: ['family_center', 'garage_center'] },
        garage_center:  { x: 15.5,  y: 7.0,   connections: ['garage_door'] },
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
      width: 18.0,
      height: 10.5,
      svgPath: 'assets/floorplans/floor2.svg',
      rooms: {
        master_bedroom: { x: 0,    y: 0,    w: 7.5,  h: 6.5,  label: 'Master Bedroom' },
        closet:         { x: 0,    y: 6.5,  w: 5.0,  h: 4.0,  label: 'Closet' },
        bathroom:       { x: 5.0,  y: 6.5,  w: 3.5,  h: 4.0,  label: 'Bathroom' },
        guest_bedroom:  { x: 7.5,  y: 0,    w: 5.0,  h: 5.5,  label: 'Guest Bedroom' },
        hallway:        { x: 8.5,  y: 5.5,  w: 4.0,  h: 5.0,  label: 'Hallway' },
        bedroom1:       { x: 13.0, y: 0,    w: 5.0,  h: 5.5,  label: 'Bedroom #1' },
        bedroom2:       { x: 13.0, y: 5.5,  w: 5.0,  h: 5.0,  label: 'Bedroom #2' },
      },
      waypoints: {
        master_center:   { x: 3.75, y: 3.25,  connections: ['master_door'] },
        master_door:     { x: 7.5,  y: 3.75,  connections: ['master_center', 'guest_door', 'closet_door'] },
        closet_door:     { x: 4.25, y: 6.5,   connections: ['master_door', 'closet_center'] },
        closet_center:   { x: 2.5,  y: 8.5,   connections: ['closet_door'] },
        bathroom_center: { x: 6.75, y: 8.5,   connections: ['closet_door'] },
        guest_door:      { x: 9.5,  y: 5.0,   connections: ['master_door', 'guest_center', 'hall_mid'] },
        guest_center:    { x: 10.0, y: 2.75,  connections: ['guest_door'] },
        hall_mid:        { x: 10.5, y: 7.5,   connections: ['guest_door', 'bedroom1_door', 'bedroom2_door'] },
        bedroom1_door:   { x: 13.0, y: 3.25,  connections: ['hall_mid', 'bedroom1_center'] },
        bedroom1_center: { x: 15.5, y: 2.75,  connections: ['bedroom1_door'] },
        bedroom2_door:   { x: 13.0, y: 7.75,  connections: ['hall_mid', 'bedroom2_center'] },
        bedroom2_center: { x: 15.5, y: 8.0,   connections: ['bedroom2_door'] },
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
      width: 18.0,
      height: 10.5,
      svgPath: 'assets/floorplans/floor3.svg',
      rooms: {
        storage:    { x: 0,    y: 0,    w: 6.5,  h: 5.0,  label: 'Storage' },
        art_studio: { x: 0,    y: 5.5,  w: 8.5,  h: 5.0,  label: 'Art Studio' },
        hallway:    { x: 6.5,  y: 4.0,  w: 2.0,  h: 2.0,  label: 'Hallway' },
        recreation: { x: 8.5,  y: 0,    w: 9.5,  h: 5.5,  label: 'Recreation Area' },
        bar_area:   { x: 8.5,  y: 5.5,  w: 5.0,  h: 5.0,  label: 'Bar Area' },
        workshop:   { x: 13.5, y: 5.5,  w: 4.5,  h: 5.0,  label: 'Workshop' },
      },
      waypoints: {
        storage_center:  { x: 3.25, y: 2.5,   connections: ['storage_door'] },
        storage_door:    { x: 5.0,  y: 5.0,   connections: ['storage_center', 'hall_mid'] },
        hall_mid:        { x: 7.5,  y: 5.0,   connections: ['storage_door', 'studio_door', 'rec_door', 'bar_door'] },
        studio_door:     { x: 7.0,  y: 6.0,   connections: ['hall_mid', 'studio_center'] },
        studio_center:   { x: 4.25, y: 8.0,   connections: ['studio_door'] },
        rec_door:        { x: 8.5,  y: 2.75,  connections: ['hall_mid', 'rec_center'] },
        rec_center:      { x: 13.25, y: 2.75, connections: ['rec_door'] },
        bar_door:        { x: 8.5,  y: 8.0,   connections: ['hall_mid', 'bar_center'] },
        bar_center:      { x: 11.0, y: 8.0,   connections: ['bar_door', 'workshop_door'] },
        workshop_door:   { x: 13.5, y: 8.0,   connections: ['bar_center', 'workshop_center'] },
        workshop_center: { x: 15.75, y: 8.0,  connections: ['workshop_door'] },
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
