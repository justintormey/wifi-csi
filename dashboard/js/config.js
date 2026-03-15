/**
 * WiFi CSI Dashboard Configuration
 * Floor layout, room definitions, and connection topology for simulation and rendering.
 */

export const CONFIG = {
  // WebSocket endpoint (backend or simulator)
  wsUrl: 'ws://localhost:8080/ws/tracking',

  // Simulation defaults
  simulation: {
    tickRateHz: 10,        // Payload output rate
    speedMultiplier: 1.0,  // Time acceleration (2.0 = double speed)
    defaultPersonCount: 2,
    maxPersonCount: 4,
  },

  // Floor definitions — Phase 1 focuses on floor 1
  floors: [
    {
      id: 1,
      name: 'Ground Floor',
      // Coordinate system: meters from top-left origin
      width: 18.0,   // ~60 ft
      height: 10.5,  // ~35 ft
      svgPath: 'assets/floorplans/floor1.svg',
      rooms: {
        living_room:  { x: 0,    y: 0,    w: 7.0,  h: 5.5,  label: 'Living Room' },
        kitchen:      { x: 7.0,  y: 0,    w: 5.5,  h: 5.5,  label: 'Kitchen' },
        dining:       { x: 12.5, y: 0,    w: 5.5,  h: 5.5,  label: 'Dining Room' },
        hallway:      { x: 5.0,  y: 5.5,  w: 8.0,  h: 2.0,  label: 'Hallway' },
        garage:       { x: 0,    y: 5.5,  w: 5.0,  h: 5.0,  label: 'Garage' },
        bathroom:     { x: 13.0, y: 5.5,  w: 3.0,  h: 2.5,  label: 'Bathroom' },
        laundry:      { x: 13.0, y: 8.0,  w: 3.0,  h: 2.5,  label: 'Laundry' },
        entry:        { x: 16.0, y: 5.5,  w: 2.0,  h: 5.0,  label: 'Entry' },
      },
      // Waypoints: navigable points (doorways, room centers) for pathfinding
      // Each has a position and list of connected waypoint IDs
      waypoints: {
        living_center:  { x: 3.5,  y: 2.75, connections: ['living_door'] },
        living_door:    { x: 6.5,  y: 2.75, connections: ['living_center', 'kitchen_door', 'hall_west'] },
        kitchen_door:   { x: 7.5,  y: 2.75, connections: ['living_door', 'kitchen_center', 'hall_mid'] },
        kitchen_center: { x: 9.75, y: 2.75, connections: ['kitchen_door', 'dining_door'] },
        dining_door:    { x: 12.0, y: 2.75, connections: ['kitchen_center', 'dining_center', 'hall_east'] },
        dining_center:  { x: 15.25,y: 2.75, connections: ['dining_door'] },
        hall_west:      { x: 6.0,  y: 6.5,  connections: ['living_door', 'garage_door', 'hall_mid'] },
        hall_mid:       { x: 9.0,  y: 6.5,  connections: ['hall_west', 'hall_east', 'kitchen_door'] },
        hall_east:      { x: 12.5, y: 6.5,  connections: ['hall_mid', 'bathroom_door', 'entry_door', 'dining_door'] },
        garage_door:    { x: 4.5,  y: 6.0,  connections: ['hall_west', 'garage_center'] },
        garage_center:  { x: 2.5,  y: 8.0,  connections: ['garage_door'] },
        bathroom_door:  { x: 13.5, y: 6.0,  connections: ['hall_east', 'bathroom_center', 'laundry_door'] },
        bathroom_center:{ x: 14.5, y: 6.75, connections: ['bathroom_door'] },
        laundry_door:   { x: 13.5, y: 8.5,  connections: ['bathroom_door', 'laundry_center'] },
        laundry_center: { x: 14.5, y: 9.25, connections: ['laundry_door'] },
        entry_door:     { x: 16.5, y: 6.0,  connections: ['hall_east', 'entry_center'] },
        entry_center:   { x: 17.0, y: 8.0,  connections: ['entry_door'] },
      },
      // Base signal quality per zone (modified by simulator noise)
      baseSignalQuality: {
        living_room: 0.88,
        kitchen:     0.82,
        dining:      0.75,
        hallway:     0.60,
        garage:      0.45,
        bathroom:    0.55,
        laundry:     0.50,
        entry:       0.65,
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
    description: 'One person wakes up (simulated on ground floor), walks to kitchen, makes coffee, sits at dining table.',
    people: [
      {
        id: 'p1',
        startWaypoint: 'living_center',
        actions: [
          { type: 'idle', duration: 5, activity: 'sleeping' },
          { type: 'move', to: 'kitchen_center' },
          { type: 'idle', duration: 8, activity: 'standing' },
          { type: 'move', to: 'dining_center' },
          { type: 'idle', duration: 20, activity: 'sitting' },
          { type: 'move', to: 'bathroom_center' },
          { type: 'idle', duration: 5, activity: 'standing' },
          { type: 'move', to: 'living_center' },
          { type: 'idle', duration: 30, activity: 'sitting' },
        ],
      },
    ],
  },
  family_evening: {
    name: 'Family Evening',
    description: 'Two people: one cooking in kitchen, one in living room. They converge in dining room.',
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
        startWaypoint: 'living_center',
        actions: [
          { type: 'idle', duration: 12, activity: 'sitting' },
          { type: 'move', to: 'dining_center' },
          { type: 'idle', duration: 20, activity: 'sitting' },
          { type: 'move', to: 'living_center' },
          { type: 'idle', duration: 15, activity: 'sitting' },
        ],
      },
    ],
  },
  full_house: {
    name: 'Full House',
    description: 'Four people scattered across the ground floor doing different activities.',
    people: [
      {
        id: 'p1',
        startWaypoint: 'living_center',
        actions: [
          { type: 'idle', duration: 20, activity: 'sitting' },
          { type: 'move', to: 'kitchen_center' },
          { type: 'idle', duration: 10, activity: 'standing' },
          { type: 'move', to: 'living_center' },
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
          { type: 'move', to: 'bathroom_center' },
          { type: 'idle', duration: 5, activity: 'standing' },
          { type: 'move', to: 'dining_center' },
        ],
      },
      {
        id: 'p4',
        startWaypoint: 'entry_center',
        actions: [
          { type: 'move', to: 'hall_east' },
          { type: 'move', to: 'living_center' },
          { type: 'idle', duration: 30, activity: 'sitting' },
        ],
      },
    ],
  },
};
