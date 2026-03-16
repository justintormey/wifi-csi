/**
 * WiFi CSI Dashboard Configuration
 * Floor layout, room definitions, and connection topology for simulation and rendering.
 * Based on actual floor plans of 14 Charleston Drive.
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

  // Floor definitions — Phase 1 focuses on floor 1 (1st Floor / main level)
  floors: [
    {
      id: 1,
      name: '1st Floor',
      // Coordinate system: meters from top-left origin
      width: 18.0,   // ~60 ft
      height: 10.5,  // ~35 ft
      svgPath: 'assets/floorplans/floor1.svg',
      rooms: {
        garage:       { x: 0,     y: 0,    w: 5.5,  h: 5.5,  label: 'Garage' },
        family_room:  { x: 5.5,   y: 0,    w: 7.0,  h: 5.5,  label: 'Family Room' },
        kitchen:      { x: 12.5,  y: 0,    w: 5.5,  h: 5.5,  label: 'Kitchen' },
        hallway:      { x: 5.5,   y: 5.5,  w: 7.0,  h: 2.0,  label: 'Hallway' },
        dining:       { x: 0,     y: 5.5,  w: 5.5,  h: 5.0,  label: 'Dining Room' },
        utility:      { x: 12.5,  y: 5.5,  w: 2.5,  h: 2.5,  label: 'Utility' },
        office:       { x: 12.5,  y: 8.0,  w: 2.5,  h: 2.5,  label: 'Office' },
        parlor:       { x: 15.0,  y: 5.5,  w: 3.0,  h: 5.0,  label: 'Parlor' },
      },
      // Waypoints: navigable points (doorways, room centers) for pathfinding
      waypoints: {
        garage_center:  { x: 2.75, y: 2.75, connections: ['garage_door'] },
        garage_door:    { x: 5.25, y: 2.75, connections: ['garage_center', 'family_center'] },
        family_center:  { x: 9.0,  y: 2.75, connections: ['garage_door', 'family_door', 'kitchen_door'] },
        kitchen_door:   { x: 12.25,y: 2.75, connections: ['family_center', 'kitchen_center'] },
        kitchen_center: { x: 15.25,y: 2.75, connections: ['kitchen_door', 'kitchen_hall'] },
        kitchen_hall:   { x: 11.0, y: 5.5,  connections: ['kitchen_center', 'hall_mid'] },
        family_door:    { x: 7.25, y: 5.5,  connections: ['family_center', 'hall_west'] },
        hall_west:      { x: 7.0,  y: 6.5,  connections: ['family_door', 'hall_mid', 'dining_door'] },
        hall_mid:       { x: 9.5,  y: 6.5,  connections: ['hall_west', 'hall_east', 'kitchen_hall'] },
        hall_east:      { x: 12.0, y: 6.5,  connections: ['hall_mid', 'utility_door', 'parlor_door'] },
        dining_door:    { x: 2.75, y: 5.5,  connections: ['hall_west', 'dining_center'] },
        dining_center:  { x: 2.75, y: 8.0,  connections: ['dining_door'] },
        utility_door:   { x: 12.75,y: 6.0,  connections: ['hall_east', 'utility_center', 'office_door'] },
        utility_center: { x: 13.75,y: 6.75, connections: ['utility_door'] },
        office_door:    { x: 13.5, y: 8.0,  connections: ['utility_door', 'office_center'] },
        office_center:  { x: 13.75,y: 9.25, connections: ['office_door'] },
        parlor_door:    { x: 15.25,y: 7.0,  connections: ['hall_east', 'parlor_center'] },
        parlor_center:  { x: 16.5, y: 8.0,  connections: ['parlor_door'] },
      },
      // Base signal quality per zone (modified by simulator noise)
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
