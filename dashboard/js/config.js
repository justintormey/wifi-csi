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
  // All floors share same footprint: 10.5m wide × 18.0m deep (portrait).
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
      width: 10.5,
      height: 18.0,
      svgPath: 'assets/floorplans/floor2.svg',
      rooms: {
        bedroom1:       { x: 0,    y: 0,    w: 5.5,  h: 5.5,  label: 'Bedroom #1' },
        bedroom2:       { x: 5.5,  y: 0,    w: 5.0,  h: 5.5,  label: 'Bedroom #2' },
        guest_bedroom:  { x: 0,    y: 5.5,  w: 5.5,  h: 5.0,  label: 'Guest Bedroom' },
        hallway:        { x: 3.5,  y: 5.5,  w: 4.0,  h: 2.0,  label: 'Hallway' },
        bathroom:       { x: 7.0,  y: 5.5,  w: 3.5,  h: 3.0,  label: 'Bathroom' },
        closet:         { x: 7.0,  y: 8.5,  w: 3.5,  h: 2.0,  label: 'Closet' },
        master_bedroom: { x: 0,    y: 10.5, w: 10.5, h: 7.5,  label: 'Master Bedroom' },
      },
      waypoints: {
        bedroom1_center: { x: 2.75, y: 2.75, connections: ['bedroom1_door'] },
        bedroom1_door:   { x: 4.5,  y: 5.5,  connections: ['bedroom1_center', 'hall_west'] },
        bedroom2_center: { x: 8.0,  y: 2.75, connections: ['bedroom2_door'] },
        bedroom2_door:   { x: 6.5,  y: 5.5,  connections: ['bedroom2_center', 'hall_east'] },
        hall_west:       { x: 4.5,  y: 6.5,  connections: ['bedroom1_door', 'hall_mid', 'guest_door'] },
        hall_mid:        { x: 5.5,  y: 6.5,  connections: ['hall_west', 'hall_east'] },
        hall_east:       { x: 6.5,  y: 6.5,  connections: ['hall_mid', 'bedroom2_door', 'bathroom_door'] },
        guest_door:      { x: 3.5,  y: 7.0,  connections: ['hall_west', 'guest_center'] },
        guest_center:    { x: 2.75, y: 8.0,  connections: ['guest_door', 'master_door'] },
        master_door:     { x: 2.75, y: 10.5, connections: ['guest_center', 'master_center'] },
        master_center:   { x: 5.25, y: 14.25, connections: ['master_door'] },
        bathroom_door:   { x: 7.0,  y: 6.5,  connections: ['hall_east', 'bathroom_center'] },
        bathroom_center: { x: 8.75, y: 7.0,  connections: ['bathroom_door', 'closet_door'] },
        closet_door:     { x: 8.75, y: 8.5,  connections: ['bathroom_center', 'closet_center'] },
        closet_center:   { x: 8.75, y: 9.5,  connections: ['closet_door'] },
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
      width: 10.5,
      height: 18.0,
      svgPath: 'assets/floorplans/floor3.svg',
      rooms: {
        workshop:   { x: 4.0,  y: 0,    w: 6.5,  h: 5.0,  label: 'Workshop' },
        recreation: { x: 0,    y: 0,    w: 4.0,  h: 11.0, label: 'Recreation Area' },
        bar_area:   { x: 4.0,  y: 5.0,  w: 6.5,  h: 4.5,  label: 'Bar Area' },
        hallway:    { x: 3.0,  y: 9.5,  w: 4.5,  h: 1.5,  label: 'Hallway' },
        art_studio: { x: 4.5,  y: 11.0, w: 6.0,  h: 7.0,  label: 'Art Studio' },
        storage:    { x: 0,    y: 11.0, w: 4.5,  h: 7.0,  label: 'Storage' },
      },
      waypoints: {
        workshop_center: { x: 7.25, y: 2.5,  connections: ['workshop_door'] },
        workshop_door:   { x: 4.25, y: 2.5,  connections: ['workshop_center', 'rec_north'] },
        rec_north:       { x: 2.0,  y: 2.5,  connections: ['workshop_door', 'rec_center'] },
        rec_center:      { x: 2.0,  y: 5.5,  connections: ['rec_north', 'bar_door'] },
        bar_door:        { x: 4.25, y: 7.25, connections: ['rec_center', 'bar_center', 'hall_mid'] },
        bar_center:      { x: 7.25, y: 7.25, connections: ['bar_door'] },
        hall_mid:        { x: 5.25, y: 10.0, connections: ['bar_door', 'storage_door', 'studio_door'] },
        storage_door:    { x: 2.25, y: 11.0, connections: ['hall_mid', 'storage_center'] },
        storage_center:  { x: 2.25, y: 14.5, connections: ['storage_door'] },
        studio_door:     { x: 7.25, y: 11.0, connections: ['hall_mid', 'studio_center'] },
        studio_center:   { x: 7.5,  y: 14.5, connections: ['studio_door'] },
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
