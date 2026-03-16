/**
 * WiFi CSI Dashboard — Main Application
 *
 * Sole entry point for the dashboard. Initializes the WebSocket client
 * with simulator fallback, wires up all UI controls, and dispatches
 * payloads to the floor plan renderer and vitals panel.
 *
 * Architecture decision: This file contains inline rendering for tracking
 * dots, vitals, and floor plan loading rather than importing the standalone
 * class modules (floorplan.js, tracker-overlay.js, vitals-panel.js,
 * noise-overlay.js). Those modules exist as a richer canvas-based layer
 * built in parallel but not yet integrated. The inline approach here uses
 * DOM elements with CSS transitions for smooth 10Hz updates, which is
 * sufficient for the current use case and avoids the complexity of
 * synchronizing two rendering pipelines.
 */

import { CONFIG, DEMO_SCENARIOS } from './config.js';
import { Simulator } from './simulator.js';
import { WebSocketClient, bindStatusIndicator } from './websocket-client.js';

// ── HTML escaping (XSS prevention) ──────────────────────────

const _escapeMap = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
function esc(str) {
  if (str == null) return '';
  return String(str).replace(/[&<>"']/g, c => _escapeMap[c]);
}

// ── Application State ────────────────────────────────────────

export const appState = {
  client: null,
  currentFloor: 1,
  demoMode: true,
  latestPayload: null,
  trails: {},         // personId -> [{px, py}] last N pixel positions
  trailMaxLength: 40,
  tickCount: 0,
  tickRateDisplay: 0,
  lastTickTime: 0,
};

// ── DOM References ───────────────────────────────────────────

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

let dom = {};

function cacheDom() {
  dom = {
    connectionStatus:    $('#connection-status'),
    floorplanContainer:  $('#floorplan-container'),
    noiseCanvas:         $('#noise-canvas'),
    trackingOverlay:     $('#tracking-overlay'),
    occupancyCount:      $('#occupancy-count'),
    occupancyConfidence: $('#occupancy-confidence'),
    vitalsList:          $('#vitals-list'),
    signalQualityList:   $('#signal-quality-list'),
    scenarioSelect:      $('#scenario-select'),
    simRestart:          $('#sim-restart'),
    simControlsPanel:    $('#sim-controls-panel'),
    demoToggle:          $('#demo-toggle'),
    statusDot:           $('#status-dot'),
    statusLabel:         $('#status-label'),
    statusFloor:         $('#status-floor'),
    statusTickRate:      $('#status-tick-rate'),
    statusTimestamp:     $('#status-timestamp'),
  };
}

// ── Floor Plan Loading ───────────────────────────────────────

let floorplanSVG = null;
let floorConfig = null;

async function loadFloorPlan(floorId) {
  const floor = CONFIG.floors.find(f => f.id === floorId);
  if (!floor) return;
  floorConfig = floor;
  appState.currentFloor = floorId;

  try {
    const resp = await fetch(floor.svgPath);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const svgText = await resp.text();
    const parser = new DOMParser();
    const doc = parser.parseFromString(svgText, 'image/svg+xml');
    const parsedSVG = doc.documentElement;
    // Strip any embedded scripts from SVG
    parsedSVG.querySelectorAll('script').forEach(s => s.remove());
    dom.floorplanContainer.innerHTML = '';
    dom.floorplanContainer.appendChild(document.importNode(parsedSVG, true));
    floorplanSVG = dom.floorplanContainer.querySelector('svg');

    if (floorplanSVG) {
      floorplanSVG.setAttribute('preserveAspectRatio', 'xMidYMid meet');
      floorplanSVG.style.width = '100%';
      floorplanSVG.style.height = '100%';
    }
  } catch (err) {
    console.warn('Failed to load floor plan SVG, using placeholder:', err.message);
    dom.floorplanContainer.innerHTML = buildPlaceholderFloorPlan(floor);
    floorplanSVG = dom.floorplanContainer.querySelector('svg');
  }

  // Reset tracking overlay and trails
  dom.trackingOverlay.innerHTML = '';
  clearActiveDots();
  appState.trails = {};

  // Size noise canvas to match
  resizeNoiseCanvas();

  // Update floor tabs
  $$('.floor-tab').forEach(tab => {
    tab.classList.toggle('active', parseInt(tab.dataset.floor) === floorId);
  });

  // Update status bar
  if (dom.statusFloor) {
    dom.statusFloor.innerHTML = `Floor ${floorId} &mdash; ${floor.name}`;
  }

  // Re-render with current payload if available
  if (appState.latestPayload && appState.latestPayload.floor === floorId) {
    renderPayload(appState.latestPayload);
  }
}

function buildPlaceholderFloorPlan(floor) {
  const w = floor.width * 100;
  const h = floor.height * 100;
  let svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${w} ${h}" class="floorplan" preserveAspectRatio="xMidYMid meet" style="width:100%;height:100%">`;
  svg += `<defs><pattern id="pgrid" width="100" height="100" patternUnits="userSpaceOnUse">`;
  svg += `<path d="M 100 0 L 0 0 0 100" fill="none" stroke="rgba(0,255,255,0.04)" stroke-width="0.5"/>`;
  svg += `</pattern></defs>`;
  svg += `<rect width="${w}" height="${h}" fill="url(#pgrid)" class="floor-bg"/>`;

  for (const [name, room] of Object.entries(floor.rooms)) {
    const rx = room.x * 100, ry = room.y * 100, rw = room.w * 100, rh = room.h * 100;
    svg += `<rect x="${rx}" y="${ry}" width="${rw}" height="${rh}" class="room" data-room="${name}"/>`;
    svg += `<rect x="${rx}" y="${ry}" width="${rw}" height="${rh}" fill="none" stroke="rgba(0,255,255,0.35)" stroke-width="3"/>`;
    const fontSize = rw < 300 || rh < 200 ? 'label-small' : 'label';
    svg += `<text x="${rx + rw / 2}" y="${ry + rh / 2}" class="label ${fontSize}">${room.label}</text>`;
  }

  svg += `<rect x="0" y="0" width="${w}" height="${h}" fill="none" stroke="rgba(0,255,255,0.7)" stroke-width="6"/>`;
  svg += `</svg>`;
  return svg;
}

// ── Coordinate Conversion ────────────────────────────────────
// SVGs use preserveAspectRatio="xMidYMid meet", which letterboxes/pillarboxes
// the content within the element. We must calculate the actual rendered area
// and offset to correctly position tracking dots over the floor plan.

function metersToPixels(x, y) {
  if (!floorplanSVG || !floorConfig) return null;

  const svgRect = floorplanSVG.getBoundingClientRect();
  const containerRect = dom.trackingOverlay.getBoundingClientRect();

  const vbWidth = floorConfig.width * 100;
  const vbHeight = floorConfig.height * 100;
  const svgAspect = vbWidth / vbHeight;
  const elemAspect = svgRect.width / svgRect.height;

  let renderWidth, renderHeight, offsetX, offsetY;

  if (elemAspect > svgAspect) {
    renderHeight = svgRect.height;
    renderWidth = renderHeight * svgAspect;
    offsetX = (svgRect.width - renderWidth) / 2;
    offsetY = 0;
  } else {
    renderWidth = svgRect.width;
    renderHeight = renderWidth / svgAspect;
    offsetX = 0;
    offsetY = (svgRect.height - renderHeight) / 2;
  }

  const px = (svgRect.left - containerRect.left) + offsetX + (x / floorConfig.width) * renderWidth;
  const py = (svgRect.top - containerRect.top) + offsetY + (y / floorConfig.height) * renderHeight;

  return { px, py };
}

function metersToOverlayPx(meters) {
  if (!floorplanSVG || !floorConfig) return 0;
  const svgRect = floorplanSVG.getBoundingClientRect();
  const vbWidth = floorConfig.width * 100;
  const vbHeight = floorConfig.height * 100;
  const svgAspect = vbWidth / vbHeight;
  const elemAspect = svgRect.width / svgRect.height;

  let renderWidth;
  if (elemAspect > svgAspect) {
    renderWidth = svgRect.height * svgAspect;
  } else {
    renderWidth = svgRect.width;
  }
  return (meters / floorConfig.width) * renderWidth;
}

// ── Noise Canvas ─────────────────────────────────────────────

function resizeNoiseCanvas() {
  if (!dom.noiseCanvas) return;
  const rect = dom.noiseCanvas.parentElement.getBoundingClientRect();
  dom.noiseCanvas.width = rect.width - 24;
  dom.noiseCanvas.height = rect.height - 24;
}

// ── Payload Handler ──────────────────────────────────────────

function handlePayload(payload) {
  appState.latestPayload = payload;

  // Track tick rate (payloads per second)
  const now = performance.now();
  appState.tickCount++;
  if (now - appState.lastTickTime >= 1000) {
    appState.tickRateDisplay = appState.tickCount;
    appState.tickCount = 0;
    appState.lastTickTime = now;
  }

  // Only render if payload matches current floor
  if (payload.floor !== appState.currentFloor) return;

  renderPayload(payload);
}

function renderPayload(payload) {
  if (!payload) return;
  updateTrackingDots(payload.people);
  updateVitalsPanel(payload.people);
  updateOccupancy(payload.occupancy_estimate, payload.occupancy_confidence);
  updateSignalQuality(payload.zone_signal_quality);
  updateStatusBar(payload);
}

// ── Tracking Dots ────────────────────────────────────────────

// DOM element cache per tracked person. Elements are created on first
// appearance and removed when the person leaves the payload. This avoids
// DOM churn at 10Hz — elements are repositioned via CSS left/top with
// a short transition for smooth interpolation between ticks.
const activeDots = {};  // personId -> { dot, ring, label, trailSvg }
const PERSON_COLORS = {
  p1: '#00fff7', p2: '#00ff88', p3: '#ff88ff', p4: '#ffaa00',
};

function clearActiveDots() {
  for (const id of Object.keys(activeDots)) {
    const els = activeDots[id];
    els.dot.remove();
    els.ring.remove();
    els.label.remove();
    els.trailSvg.remove();
    delete activeDots[id];
  }
}

function updateTrackingDots(people) {
  if (!people) return;
  const seenIds = new Set();

  for (const person of people) {
    seenIds.add(person.id);
    const pos = metersToPixels(person.x, person.y);
    if (!pos) continue;

    // Update trail history
    if (!appState.trails[person.id]) appState.trails[person.id] = [];
    const trail = appState.trails[person.id];
    trail.push({ px: pos.px, py: pos.py });
    if (trail.length > appState.trailMaxLength) trail.shift();

    // Confidence class — thresholds 0.5/0.75 map to CSS classes that control
    // opacity and blur filter. Note: standalone tracker-overlay.js uses
    // different thresholds (0.4/0.8) for its canvas rendering.
    let confClass = 'confidence-high';
    if (person.position_confidence < 0.5) confClass = 'confidence-low';
    else if (person.position_confidence < 0.75) confClass = 'confidence-medium';

    // Uncertainty radius in pixels
    const radiusPx = metersToOverlayPx(person.uncertainty_radius_m);

    if (!activeDots[person.id]) {
      // Create dot element
      const dot = document.createElement('div');
      dot.className = `tracking-dot ${confClass}`;
      dot.dataset.person = person.id;
      dom.trackingOverlay.appendChild(dot);

      // Create uncertainty ring
      const ring = document.createElement('div');
      ring.className = 'uncertainty-ring';
      ring.dataset.person = person.id;
      dom.trackingOverlay.appendChild(ring);

      // Create ID label
      const label = document.createElement('div');
      label.className = 'tracking-label';
      label.textContent = person.id.toUpperCase();
      dom.trackingOverlay.appendChild(label);

      // Create trail SVG
      const trailSvg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
      trailSvg.setAttribute('class', 'trail-path');
      trailSvg.dataset.person = person.id;
      trailSvg.style.cssText = 'position:absolute;inset:0;pointer-events:none;z-index:10;width:100%;height:100%;overflow:visible;';
      const polyline = document.createElementNS('http://www.w3.org/2000/svg', 'polyline');
      trailSvg.appendChild(polyline);
      dom.trackingOverlay.appendChild(trailSvg);

      activeDots[person.id] = { dot, ring, label, trailSvg };
    }

    const els = activeDots[person.id];

    // Position the dot
    els.dot.style.left = pos.px + 'px';
    els.dot.style.top = pos.py + 'px';
    els.dot.className = `tracking-dot ${confClass}`;
    els.dot.dataset.person = person.id;
    if (person.is_stationary) els.dot.classList.add('stationary');

    // Position and size the uncertainty ring
    const ringSize = Math.max(radiusPx * 2, 20);
    els.ring.style.left = pos.px + 'px';
    els.ring.style.top = pos.py + 'px';
    els.ring.style.width = ringSize + 'px';
    els.ring.style.height = ringSize + 'px';

    // Position the label
    els.label.style.left = pos.px + 'px';
    els.label.style.top = pos.py + 'px';

    // Update the trail polyline
    const polyline = els.trailSvg.querySelector('polyline');
    if (trail.length > 1) {
      const points = trail.map(p => `${p.px},${p.py}`).join(' ');
      polyline.setAttribute('points', points);
    }
  }

  // Remove dots for people no longer present
  for (const id of Object.keys(activeDots)) {
    if (!seenIds.has(id)) {
      const els = activeDots[id];
      els.dot.remove();
      els.ring.remove();
      els.label.remove();
      els.trailSvg.remove();
      delete activeDots[id];
      delete appState.trails[id];
    }
  }
}

// ── Vitals Panel ─────────────────────────────────────────────

function findRoom(x, y) {
  const floor = CONFIG.floors.find(f => f.id === appState.currentFloor);
  if (!floor) return null;
  for (const [, room] of Object.entries(floor.rooms)) {
    if (x >= room.x && x <= room.x + room.w && y >= room.y && y <= room.y + room.h) {
      return room.label;
    }
  }
  return 'Hallway';
}

function formatDuration(seconds) {
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const mins = Math.floor(seconds / 60);
  const secs = Math.round(seconds % 60);
  return `${mins}m${secs}s`;
}

// Rebuilds the entire vitals panel innerHTML on every tick (10Hz).
// This is a deliberate simplification — no incremental DOM diffing.
// At 10Hz the browser reflows are fast enough for the panel's simple
// structure, and full replacement avoids stale-state bugs.
function updateVitalsPanel(people) {
  if (!dom.vitalsList) return;

  if (!people || people.length === 0) {
    dom.vitalsList.innerHTML = '<div class="vitals-empty">No tracking data</div>';
    return;
  }

  dom.vitalsList.innerHTML = people.map(person => {
    const pid = esc(person.id);
    const color = PERSON_COLORS[person.id] || '#00fff7';
    const room = esc(findRoom(person.x, person.y));
    const activity = person.is_stationary ? 'stationary' : 'moving';
    const stationaryInfo = person.is_stationary && person.stationary_duration_s > 0
      ? ` (${esc(formatDuration(person.stationary_duration_s))})` : '';

    // Breathing
    const brConf = person.breathing?.confidence ?? 0;
    const brVal = person.breathing?.rate_bpm;
    const brDisplay = brVal != null
      ? `<span class="vital-value${brConf < 0.3 ? ' low-confidence' : ''}">${Math.round(brVal)}</span>`
      : '<span class="vital-value unavailable">--</span>';

    // Heart rate (only shown when heartrate.display is true)
    const hr = person.heartrate;
    let hrDisplay;
    if (hr?.display && hr.confidence > 0.15 && hr.rate_bpm) {
      hrDisplay = `<span class="vital-value experimental">${Math.round(hr.rate_bpm)}</span>`;
    } else {
      hrDisplay = '<span class="vital-value unavailable">--</span>';
    }

    const posConf = Math.round((person.position_confidence ?? 0) * 100);
    const xVal = typeof person.x === 'number' ? person.x.toFixed(1) : '?';
    const yVal = typeof person.y === 'number' ? person.y.toFixed(1) : '?';

    return `
      <div class="person-card">
        <div class="person-card-header">
          <span class="person-id" style="color:${esc(color)}">Target ${pid.toUpperCase()}</span>
          <span class="person-activity">${esc(activity)}${stationaryInfo}</span>
        </div>
        <div class="person-location">${room || 'Unknown zone'}</div>
        <div class="position-info">
          <span class="coord">X: <span class="val">${esc(xVal)}m</span></span>
          <span class="coord">Y: <span class="val">${esc(yVal)}m</span></span>
          <span class="coord">Conf: <span class="val">${posConf}%</span></span>
        </div>
        <div class="confidence-bar"><div class="confidence-bar-fill" style="width:${posConf}%"></div></div>
        <div class="vitals-row">
          <div class="vital">
            <span class="vital-label">Breath</span>
            ${brDisplay}<span class="vital-label">bpm</span>
          </div>
          <div class="vital">
            <span class="vital-label">Heart</span>
            ${hrDisplay}<span class="vital-label">bpm</span>
          </div>
        </div>
      </div>`;
  }).join('');
}

// ── Occupancy ────────────────────────────────────────────────

function updateOccupancy(estimate, confidence) {
  if (dom.occupancyCount) {
    dom.occupancyCount.textContent = estimate != null ? estimate : '--';
  }
  if (dom.occupancyConfidence && confidence != null) {
    const confPct = Math.round(confidence * 100);
    const confClass = confidence < 0.5 ? 'low-confidence' : (confidence < 0.75 ? 'experimental' : '');
    dom.occupancyConfidence.innerHTML =
      `<span class="vital-label">Confidence</span><span class="vital-value ${confClass}">${confPct}%</span>`;
  }
}

// ── Signal Quality ───────────────────────────────────────────

const ZONE_LABEL_MAP = {
  // Floor 1
  garage: 'Garage',
  family_room: 'Family',
  kitchen: 'Kitchen',
  hallway: 'Hallway',
  dining: 'Dining',
  utility: 'Utility',
  office: 'Office',
  parlor: 'Parlor',
  // Floor 2
  bedroom1: 'Bed #1',
  bedroom2: 'Bed #2',
  guest_bedroom: 'Guest',
  master_bedroom: 'Master',
  bathroom: 'Bath',
  closet: 'Closet',
  // Floor 3 (Basement)
  workshop: 'Workshop',
  bar_area: 'Bar',
  art_studio: 'Studio',
  recreation: 'Rec Area',
  storage: 'Storage',
};

function updateSignalQuality(zoneQualities) {
  if (!dom.signalQualityList || !zoneQualities) return;

  let html = '';
  for (const [zone, quality] of Object.entries(zoneQualities)) {
    const pct = Math.round(quality * 100);
    const label = esc(ZONE_LABEL_MAP[zone] || zone);
    let fillClass = 'good';
    if (quality < 0.5) fillClass = 'poor';
    else if (quality < 0.7) fillClass = 'fair';

    html += `
      <div class="signal-bar">
        <span class="signal-bar-label">${label}</span>
        <div class="signal-bar-track">
          <div class="signal-bar-fill ${fillClass}" style="width:${pct}%"></div>
        </div>
      </div>`;
  }

  dom.signalQualityList.innerHTML = html;
}

// ── Status Bar ───────────────────────────────────────────────

function updateStatusBar(payload) {
  if (dom.statusTickRate) {
    dom.statusTickRate.textContent = appState.tickRateDisplay + 'Hz';
  }
  if (dom.statusTimestamp && payload && payload.timestamp) {
    const date = new Date(payload.timestamp * 1000);
    dom.statusTimestamp.textContent = date.toLocaleTimeString('en-US', { hour12: false });
  }
}

function updateConnectionDisplay(status) {
  const dotClassMap = {
    disconnected: 'offline',
    connecting:   'demo',
    connected:    'online',
    reconnecting: 'demo',
    simulator:    'demo',
  };
  const labelMap = {
    disconnected: 'Offline',
    connecting:   'Connecting...',
    connected:    'Live',
    reconnecting: 'Reconnecting...',
    simulator:    'Demo Mode',
  };

  if (dom.statusDot) {
    dom.statusDot.className = 'status-dot ' + (dotClassMap[status] || 'offline');
  }
  if (dom.statusLabel) {
    dom.statusLabel.textContent = labelMap[status] || 'Unknown';
  }
}

// ── Floor Tab Handlers ───────────────────────────────────────

function initFloorTabs() {
  $$('.floor-tab').forEach(tab => {
    tab.addEventListener('click', () => {
      loadFloorPlan(parseInt(tab.dataset.floor));
    });
  });
}

// ── Demo Mode Toggle ─────────────────────────────────────────

function setDemoMode(enabled) {
  appState.demoMode = enabled;

  if (dom.simControlsPanel) {
    dom.simControlsPanel.style.display = enabled ? '' : 'none';
  }

  if (enabled) {
    const scenarioKey = dom.scenarioSelect ? dom.scenarioSelect.value : 'morning_routine';
    if (scenarioKey === 'random') {
      appState.client.startSimulator('random');
    } else {
      appState.client.startSimulator('demo', scenarioKey);
    }
  } else {
    appState.client.stopSimulator();
  }
}

// ── Simulation Controls ──────────────────────────────────────

function initSimControls() {
  // Scenario selector
  if (dom.scenarioSelect) {
    dom.scenarioSelect.addEventListener('change', () => {
      if (appState.demoMode) {
        // Clear tracking visuals and restart
        dom.trackingOverlay.innerHTML = '';
        clearActiveDots();
        appState.trails = {};
        setDemoMode(true);
      }
    });
  }

  // Speed buttons
  $$('.speed-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      $$('.speed-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      const speed = parseFloat(btn.dataset.speed);
      if (appState.client && appState.client._simulator) {
        appState.client._simulator.setSpeed(speed);
      }
    });
  });

  // Restart button
  if (dom.simRestart) {
    dom.simRestart.addEventListener('click', () => {
      if (appState.demoMode) {
        dom.trackingOverlay.innerHTML = '';
        clearActiveDots();
        appState.trails = {};
        setDemoMode(true);
      }
    });
  }
}

// ── Initialize ───────────────────────────────────────────────

function init() {
  cacheDom();

  // Create WebSocket client with auto-fallback to simulator
  appState.client = new WebSocketClient({
    autoFallback: true,
    simulatorMode: 'demo',
    simulatorScenario: 'morning_routine',
  });

  // Bind payload handler
  appState.client.onPayload = handlePayload;

  // Bind connection status to header badge using the WebSocket client helper
  bindStatusIndicator(appState.client, dom.connectionStatus);

  // Also wire status changes to the status bar display
  // (bindStatusIndicator sets onStatusChange, so we wrap it)
  const boundHandler = appState.client.onStatusChange;
  appState.client.onStatusChange = (newStatus, oldStatus) => {
    if (boundHandler) boundHandler(newStatus, oldStatus);
    updateConnectionDisplay(newStatus);
  };

  // Wire up floor tabs
  initFloorTabs();

  // Wire up simulation controls
  initSimControls();

  // Wire up demo toggle
  if (dom.demoToggle) {
    dom.demoToggle.addEventListener('change', (e) => {
      setDemoMode(e.target.checked);
    });
    dom.demoToggle.checked = appState.demoMode;
  }

  // Handle window resize
  window.addEventListener('resize', resizeNoiseCanvas);

  // Initialize tick rate tracking
  appState.lastTickTime = performance.now();

  // Load floor 1
  loadFloorPlan(1);

  // Start in demo mode (toggle is checked by default)
  setDemoMode(true);
}

// ── Public API for other modules ─────────────────────────────
// Exposed on window for console debugging and potential integration
// with external tools or the standalone rendering modules.

window.CSIDashboard = {
  appState,
  loadFloorPlan,
  setDemoMode,
  renderPayload,
};

// Boot
document.addEventListener('DOMContentLoaded', init);
