/**
 * Floor Plan Editor — WiFi CSI Dashboard
 *
 * Single-module editor: draw rooms, walls, doors, windows, labels, and sensors
 * on an SVG canvas, then export valid SVG floor plan files and config.js entries.
 *
 * Coordinate system: 1 meter = 100 SVG units, origin at top-left.
 */

// ─────────────────────────────────────────────────
// EditorState
// ─────────────────────────────────────────────────

const SCALE = 100; // SVG units per meter
const SNAP_UNIT = 10; // 0.1m snap grid

let nextId = 1;
function genId() { return `el_${nextId++}`; }

const state = {
  floorName: 'New Floor',
  floorWidth: 18.0,
  floorHeight: 10.5,
  elements: [],       // {id, type, subtype?, data}
  selectedId: null,
  activeTool: 'select',
  wallType: 'exterior',
  sensorType: 'tx',
  snapEnabled: true,
  undoStack: [],
  redoStack: [],
  // Viewport transform
  panX: 0,
  panY: 0,
  zoom: 1,
};

function snap(v) {
  return state.snapEnabled ? Math.round(v / SNAP_UNIT) * SNAP_UNIT : v;
}

function toMeters(svgUnits) {
  return +(svgUnits / SCALE).toFixed(2);
}

function toSvg(meters) {
  return meters * SCALE;
}

function pushUndo() {
  state.undoStack.push(JSON.stringify(state.elements));
  state.redoStack.length = 0;
  if (state.undoStack.length > 100) state.undoStack.shift();
}

function undo() {
  if (!state.undoStack.length) return;
  state.redoStack.push(JSON.stringify(state.elements));
  state.elements = JSON.parse(state.undoStack.pop());
  nextId = Math.max(nextId, ...state.elements.map(e => parseInt(e.id.split('_')[1]) || 0)) + 1;
  state.selectedId = null;
  render();
  refreshPropertyPanel();
  updateElementCount();
}

function redo() {
  if (!state.redoStack.length) return;
  state.undoStack.push(JSON.stringify(state.elements));
  state.elements = JSON.parse(state.redoStack.pop());
  nextId = Math.max(nextId, ...state.elements.map(e => parseInt(e.id.split('_')[1]) || 0)) + 1;
  state.selectedId = null;
  render();
  refreshPropertyPanel();
  updateElementCount();
}

function addElement(el) {
  pushUndo();
  state.elements.push(el);
  render();
  updateElementCount();
}

function removeElement(id) {
  pushUndo();
  state.elements = state.elements.filter(e => e.id !== id);
  if (state.selectedId === id) state.selectedId = null;
  render();
  refreshPropertyPanel();
  updateElementCount();
}

function updateElement(id, updates) {
  const el = state.elements.find(e => e.id === id);
  if (!el) return;
  pushUndo();
  Object.assign(el, updates);
  if (updates.data) Object.assign(el.data, updates.data);
  render();
}

function getSelected() {
  return state.elements.find(e => e.id === state.selectedId) || null;
}

// ─────────────────────────────────────────────────
// SVGCanvas
// ─────────────────────────────────────────────────

const svg = document.getElementById('editor-svg');
const viewport = document.getElementById('viewport');
const gridLayer = document.getElementById('grid-layer');
const previewLayer = document.getElementById('preview-layer');
const container = document.getElementById('canvas-container');

const layers = {
  rooms: document.getElementById('layer-rooms'),
  walls: document.getElementById('layer-walls'),
  doors: document.getElementById('layer-doors'),
  windows: document.getElementById('layer-windows'),
  labels: document.getElementById('layer-labels'),
  sensors: document.getElementById('layer-sensors'),
};

function updateViewBox() {
  const w = state.floorWidth * SCALE;
  const h = state.floorHeight * SCALE;
  // Add padding (1m on each side)
  const pad = SCALE;
  svg.setAttribute('viewBox', `${-pad + state.panX} ${-pad + state.panY} ${(w + pad * 2) / state.zoom} ${(h + pad * 2) / state.zoom}`);
  drawGrid();
}

function drawGrid() {
  gridLayer.innerHTML = '';
  const w = state.floorWidth * SCALE;
  const h = state.floorHeight * SCALE;

  // 1m major grid + 0.1m minor implied by snap
  for (let x = 0; x <= w; x += SCALE) {
    const line = svgEl('line', { x1: x, y1: 0, x2: x, y2: h, class: 'grid-major' });
    gridLayer.appendChild(line);
  }
  for (let y = 0; y <= h; y += SCALE) {
    const line = svgEl('line', { x1: 0, y1: y, x2: w, y2: y, class: 'grid-major' });
    gridLayer.appendChild(line);
  }
  // Minor grid (every 0.5m)
  for (let x = 0; x <= w; x += SCALE / 2) {
    if (x % SCALE === 0) continue;
    gridLayer.appendChild(svgEl('line', { x1: x, y1: 0, x2: x, y2: h }));
  }
  for (let y = 0; y <= h; y += SCALE / 2) {
    if (y % SCALE === 0) continue;
    gridLayer.appendChild(svgEl('line', { x1: 0, y1: y, x2: w, y2: y }));
  }

  // Floor boundary rect
  const border = svgEl('rect', {
    x: 0, y: 0, width: w, height: h,
    fill: 'none', stroke: 'rgba(0,255,255,0.15)', 'stroke-width': 2,
    'stroke-dasharray': '8 4'
  });
  gridLayer.appendChild(border);
}

function svgEl(tag, attrs) {
  const el = document.createElementNS('http://www.w3.org/2000/svg', tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v !== undefined && v !== null) el.setAttribute(k, v);
  }
  return el;
}

function svgPoint(clientX, clientY) {
  const pt = svg.createSVGPoint();
  pt.x = clientX;
  pt.y = clientY;
  const ctm = svg.getScreenCTM();
  if (!ctm) return { x: 0, y: 0 };
  const svgPt = pt.matrixTransform(ctm.inverse());
  return { x: snap(svgPt.x), y: snap(svgPt.y) };
}

function render() {
  // Clear layers
  for (const layer of Object.values(layers)) layer.innerHTML = '';

  for (const el of state.elements) {
    const node = createSvgNode(el);
    if (!node) continue;
    node.dataset.id = el.id;
    if (el.id === state.selectedId) node.classList.add('selected');
    const layerName = layerForType(el.type);
    if (layers[layerName]) layers[layerName].appendChild(node);
  }
}

function layerForType(type) {
  switch (type) {
    case 'room': return 'rooms';
    case 'wall': return 'walls';
    case 'door': return 'doors';
    case 'window': return 'windows';
    case 'label': return 'labels';
    case 'sensor': return 'sensors';
    default: return 'rooms';
  }
}

function createSvgNode(el) {
  const d = el.data;
  switch (el.type) {
    case 'room': {
      const rect = svgEl('rect', {
        x: d.x, y: d.y, width: d.w, height: d.h,
        class: 'room',
        'data-room': d.key || ''
      });
      return rect;
    }
    case 'wall': {
      const cls = `wall wall-${el.subtype || 'exterior'}`;
      return svgEl('line', { x1: d.x1, y1: d.y1, x2: d.x2, y2: d.y2, class: cls });
    }
    case 'door': {
      let cls = 'door';
      if (el.subtype === 'exterior') cls += ' door-exterior';
      else if (el.subtype === 'garage') cls += ' door-garage';
      return svgEl('line', { x1: d.x1, y1: d.y1, x2: d.x2, y2: d.y2, class: cls });
    }
    case 'window': {
      return svgEl('line', { x1: d.x1, y1: d.y1, x2: d.x2, y2: d.y2, class: 'window' });
    }
    case 'label': {
      const text = svgEl('text', { x: d.x, y: d.y, class: 'label' });
      text.textContent = d.text || 'Label';
      return text;
    }
    case 'sensor': {
      const cls = `sensor sensor-${el.subtype || 'tx'}`;
      const r = el.subtype === 'tx' ? 8 : 6;
      return svgEl('circle', { cx: d.cx, cy: d.cy, r, class: cls });
    }
    default: return null;
  }
}

// ─── Mouse interaction ───

let drawState = null; // {type, startX, startY}
let isPanning = false;
let panStart = null;
let spaceDown = false;

container.addEventListener('mousedown', (e) => {
  if (e.button !== 0) return;

  if (spaceDown) {
    isPanning = true;
    panStart = { x: e.clientX, y: e.clientY, panX: state.panX, panY: state.panY };
    container.classList.add('panning');
    return;
  }

  const pt = svgPoint(e.clientX, e.clientY);

  switch (state.activeTool) {
    case 'select':
      handleSelect(e, pt);
      break;
    case 'room':
      drawState = { type: 'room', startX: pt.x, startY: pt.y };
      break;
    case 'wall':
    case 'door':
    case 'window':
      drawState = { type: state.activeTool, startX: pt.x, startY: pt.y };
      break;
    case 'label':
      handlePlaceLabel(pt);
      break;
    case 'sensor':
      handlePlaceSensor(pt);
      break;
  }
});

container.addEventListener('mousemove', (e) => {
  const pt = svgPoint(e.clientX, e.clientY);
  updateCursorPos(pt);

  if (isPanning && panStart) {
    const ctm = svg.getScreenCTM();
    if (!ctm) return;
    const dx = (e.clientX - panStart.x) / ctm.a;
    const dy = (e.clientY - panStart.y) / ctm.d;
    state.panX = panStart.panX - dx * state.zoom;
    state.panY = panStart.panY - dy * state.zoom;
    updateViewBox();
    return;
  }

  if (drawState) {
    previewLayer.innerHTML = '';
    if (drawState.type === 'room') {
      const x = Math.min(drawState.startX, pt.x);
      const y = Math.min(drawState.startY, pt.y);
      const w = Math.abs(pt.x - drawState.startX);
      const h = Math.abs(pt.y - drawState.startY);
      if (w > 0 && h > 0) {
        previewLayer.appendChild(svgEl('rect', { x, y, width: w, height: h }));
      }
    } else {
      previewLayer.appendChild(svgEl('line', {
        x1: drawState.startX, y1: drawState.startY,
        x2: pt.x, y2: pt.y
      }));
    }
  }
});

container.addEventListener('mouseup', (e) => {
  if (isPanning) {
    isPanning = false;
    panStart = null;
    container.classList.remove('panning');
    return;
  }

  if (!drawState) return;
  const pt = svgPoint(e.clientX, e.clientY);
  previewLayer.innerHTML = '';

  if (drawState.type === 'room') {
    const x = Math.min(drawState.startX, pt.x);
    const y = Math.min(drawState.startY, pt.y);
    const w = Math.abs(pt.x - drawState.startX);
    const h = Math.abs(pt.y - drawState.startY);
    if (w >= SNAP_UNIT && h >= SNAP_UNIT) {
      const roomNum = state.elements.filter(e => e.type === 'room').length + 1;
      addElement({
        id: genId(), type: 'room',
        data: { x, y, w, h, key: `room_${roomNum}`, label: `Room ${roomNum}` }
      });
      setStatus(`Room added: ${toMeters(w)}×${toMeters(h)}m`);
    }
  } else {
    const dx = pt.x - drawState.startX;
    const dy = pt.y - drawState.startY;
    const len = Math.sqrt(dx * dx + dy * dy);
    if (len >= SNAP_UNIT) {
      const el = {
        id: genId(), type: drawState.type,
        data: { x1: drawState.startX, y1: drawState.startY, x2: pt.x, y2: pt.y }
      };
      if (drawState.type === 'wall') el.subtype = state.wallType;
      else if (drawState.type === 'door') el.subtype = 'interior';
      addElement(el);
      setStatus(`${drawState.type} added: ${toMeters(len)}m`);
    }
  }
  drawState = null;
});

container.addEventListener('wheel', (e) => {
  e.preventDefault();
  const factor = e.deltaY > 0 ? 0.9 : 1.1;
  const newZoom = Math.max(0.2, Math.min(5, state.zoom * factor));

  // Zoom toward mouse position
  const pt = svgPoint(e.clientX, e.clientY);
  state.panX += pt.x * (1 - 1 / factor) * (newZoom / state.zoom - 1) * 0;
  state.zoom = newZoom;
  updateViewBox();
  document.getElementById('zoom-level').textContent = `${Math.round(state.zoom * 100)}%`;
}, { passive: false });

function handleSelect(e, pt) {
  // Hit test: find nearest element under point
  const target = e.target.closest('[data-id]');
  if (target) {
    state.selectedId = target.dataset.id;
  } else {
    state.selectedId = null;
  }
  render();
  refreshPropertyPanel();
}

function handlePlaceLabel(pt) {
  const text = prompt('Label text:', 'Room Name');
  if (!text) return;
  addElement({
    id: genId(), type: 'label',
    data: { x: pt.x, y: pt.y, text }
  });
  setStatus(`Label placed: "${text}"`);
}

function handlePlaceSensor(pt) {
  addElement({
    id: genId(), type: 'sensor', subtype: state.sensorType,
    data: { cx: pt.x, cy: pt.y }
  });
  setStatus(`Sensor (${state.sensorType.toUpperCase()}) placed`);
}

// ─────────────────────────────────────────────────
// PropertyPanel
// ─────────────────────────────────────────────────

const noSelection = document.getElementById('no-selection');
const selectionProps = document.getElementById('selection-props');
const propGroups = {
  key: document.getElementById('prop-group-key'),
  label: document.getElementById('prop-group-label'),
  rect: document.getElementById('prop-group-rect'),
  line: document.getElementById('prop-group-line'),
  circle: document.getElementById('prop-group-circle'),
  subtype: document.getElementById('prop-group-subtype'),
  sensorType: document.getElementById('prop-group-sensor-type'),
};

function refreshPropertyPanel() {
  const el = getSelected();
  if (!el) {
    noSelection.hidden = false;
    selectionProps.hidden = true;
    return;
  }

  noSelection.hidden = true;
  selectionProps.hidden = false;

  document.getElementById('prop-type').textContent = el.type + (el.subtype ? ` (${el.subtype})` : '');

  // Hide all optional groups
  for (const g of Object.values(propGroups)) g.hidden = true;

  switch (el.type) {
    case 'room':
      propGroups.key.hidden = false;
      propGroups.label.hidden = false;
      propGroups.rect.hidden = false;
      document.getElementById('prop-key').value = el.data.key || '';
      document.getElementById('prop-label').value = el.data.label || '';
      document.getElementById('prop-x').value = toMeters(el.data.x);
      document.getElementById('prop-y').value = toMeters(el.data.y);
      document.getElementById('prop-w').value = toMeters(el.data.w);
      document.getElementById('prop-h').value = toMeters(el.data.h);
      break;
    case 'wall':
    case 'door':
    case 'window':
      propGroups.line.hidden = false;
      document.getElementById('prop-x1').value = toMeters(el.data.x1);
      document.getElementById('prop-y1').value = toMeters(el.data.y1);
      document.getElementById('prop-x2').value = toMeters(el.data.x2);
      document.getElementById('prop-y2').value = toMeters(el.data.y2);
      if (el.type === 'wall' || el.type === 'door') {
        propGroups.subtype.hidden = false;
        document.getElementById('prop-subtype').value = el.subtype || 'exterior';
      }
      break;
    case 'label':
      propGroups.label.hidden = false;
      propGroups.rect.hidden = false;
      document.getElementById('prop-label').value = el.data.text || '';
      document.getElementById('prop-x').value = toMeters(el.data.x);
      document.getElementById('prop-y').value = toMeters(el.data.y);
      // Hide W/H for labels
      document.getElementById('prop-w').parentElement.hidden = true;
      document.getElementById('prop-h').parentElement.hidden = true;
      break;
    case 'sensor':
      propGroups.circle.hidden = false;
      propGroups.sensorType.hidden = false;
      document.getElementById('prop-cx').value = toMeters(el.data.cx);
      document.getElementById('prop-cy').value = toMeters(el.data.cy);
      document.getElementById('prop-sensor-type').value = el.subtype || 'tx';
      break;
  }
}

// Property input change handlers
function bindPropInputs() {
  const wire = (id, fn) => {
    const el = document.getElementById(id);
    el.addEventListener('change', () => {
      const sel = getSelected();
      if (!sel) return;
      pushUndo();
      fn(sel, el.value);
      render();
      refreshPropertyPanel();
    });
  };

  wire('prop-key', (sel, v) => { sel.data.key = v; });
  wire('prop-label', (sel, v) => {
    if (sel.type === 'label') sel.data.text = v;
    else sel.data.label = v;
  });
  wire('prop-x', (sel, v) => {
    if (sel.type === 'label') sel.data.x = toSvg(parseFloat(v));
    else sel.data.x = toSvg(parseFloat(v));
  });
  wire('prop-y', (sel, v) => {
    if (sel.type === 'label') sel.data.y = toSvg(parseFloat(v));
    else sel.data.y = toSvg(parseFloat(v));
  });
  wire('prop-w', (sel, v) => { sel.data.w = toSvg(parseFloat(v)); });
  wire('prop-h', (sel, v) => { sel.data.h = toSvg(parseFloat(v)); });
  wire('prop-x1', (sel, v) => { sel.data.x1 = toSvg(parseFloat(v)); });
  wire('prop-y1', (sel, v) => { sel.data.y1 = toSvg(parseFloat(v)); });
  wire('prop-x2', (sel, v) => { sel.data.x2 = toSvg(parseFloat(v)); });
  wire('prop-y2', (sel, v) => { sel.data.y2 = toSvg(parseFloat(v)); });
  wire('prop-cx', (sel, v) => { sel.data.cx = toSvg(parseFloat(v)); });
  wire('prop-cy', (sel, v) => { sel.data.cy = toSvg(parseFloat(v)); });
  wire('prop-subtype', (sel, v) => { sel.subtype = v; });
  wire('prop-sensor-type', (sel, v) => { sel.subtype = v; });
}

// ─────────────────────────────────────────────────
// Exporter
// ─────────────────────────────────────────────────

function generateSvg() {
  const w = state.floorWidth * SCALE;
  const h = state.floorHeight * SCALE;

  const lines = [];
  lines.push(`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${w} ${h}" class="floorplan" data-floor="1">`);

  // Defs — grid pattern
  lines.push('  <defs>');
  lines.push(`    <pattern id="grid1" width="100" height="100" patternUnits="userSpaceOnUse">`);
  lines.push(`      <path d="M 100 0 L 0 0 0 100" fill="none" stroke="rgba(0,255,255,0.04)" stroke-width="0.5"/>`);
  lines.push('    </pattern>');
  lines.push('  </defs>');
  lines.push('');
  lines.push(`  <rect width="${w}" height="${h}" fill="url(#grid1)" class="floor-bg"/>`);
  lines.push('');

  // Groups in canonical order
  const groups = [
    { type: 'room', tag: 'rooms', comment: 'Rooms' },
    { type: 'wall', tag: 'walls', comment: 'Walls' },
    { type: 'door', tag: 'doors', comment: 'Doors' },
    { type: 'window', tag: 'windows', comment: 'Windows' },
    { type: 'label', tag: 'room-labels', comment: 'Room Labels' },
    { type: 'sensor', tag: 'sensors', comment: 'Sensor positions' },
  ];

  for (const group of groups) {
    const elems = state.elements.filter(e => e.type === group.type);
    lines.push(`  <!-- ═══ ${group.comment} ═══ -->`);
    lines.push(`  <g class="${group.tag}">`);

    for (const el of elems) {
      const d = el.data;
      switch (el.type) {
        case 'room':
          lines.push(`    <rect x="${d.x}" y="${d.y}" width="${d.w}" height="${d.h}" class="room" data-room="${esc(d.key || '')}"/>`);
          break;
        case 'wall': {
          const cls = `wall wall-${el.subtype || 'exterior'}`;
          lines.push(`    <line x1="${d.x1}" y1="${d.y1}" x2="${d.x2}" y2="${d.y2}" class="${cls}"/>`);
          break;
        }
        case 'door': {
          let cls = 'door';
          if (el.subtype === 'exterior') cls += ' door-exterior';
          else if (el.subtype === 'garage') cls += ' door-garage';
          lines.push(`    <line x1="${d.x1}" y1="${d.y1}" x2="${d.x2}" y2="${d.y2}" class="${cls}"/>`);
          break;
        }
        case 'window':
          lines.push(`    <line x1="${d.x1}" y1="${d.y1}" x2="${d.x2}" y2="${d.y2}" class="window"/>`);
          break;
        case 'label':
          lines.push(`    <text x="${d.x}" y="${d.y}" class="label">${esc(d.text || '')}</text>`);
          break;
        case 'sensor': {
          const cls = `sensor sensor-${el.subtype || 'tx'}`;
          const r = el.subtype === 'tx' ? 8 : 6;
          lines.push(`    <circle cx="${d.cx}" cy="${d.cy}" r="${r}" class="${cls}"/>`);
          break;
        }
      }
    }

    lines.push('  </g>');
    lines.push('');
  }

  lines.push('</svg>');
  return lines.join('\n');
}

function generateConfigSnippet() {
  const rooms = state.elements.filter(e => e.type === 'room');
  const lines = [];
  lines.push('{');
  lines.push(`  id: 1,`);
  lines.push(`  name: '${state.floorName}',`);
  lines.push(`  width: ${state.floorWidth},`);
  lines.push(`  height: ${state.floorHeight},`);
  lines.push(`  svgPath: 'assets/floorplans/floorN.svg',`);
  lines.push('  rooms: {');

  for (const room of rooms) {
    const d = room.data;
    const key = d.key || 'unnamed';
    const x = toMeters(d.x);
    const y = toMeters(d.y);
    const w = toMeters(d.w);
    const h = toMeters(d.h);
    const pad = '    ';
    lines.push(`${pad}${key}: { x: ${x}, y: ${y}, w: ${w}, h: ${h}, label: '${d.label || key}' },`);
  }
  lines.push('  },');

  // Auto-generate center waypoints
  lines.push('  waypoints: {');
  for (const room of rooms) {
    const d = room.data;
    const key = d.key || 'unnamed';
    const cx = toMeters(d.x + d.w / 2);
    const cy = toMeters(d.y + d.h / 2);
    lines.push(`    ${key}_center: { x: ${cx}, y: ${cy}, connections: [] },`);
  }
  lines.push('  },');

  // Default signal quality
  lines.push('  baseSignalQuality: {');
  for (const room of rooms) {
    const key = room.data.key || 'unnamed';
    lines.push(`    ${key}: 0.70,`);
  }
  lines.push('  },');
  lines.push('}');

  return lines.join('\n');
}

function esc(s) {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function downloadFile(content, filename, type) {
  const blob = new Blob([content], { type });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

// ─────────────────────────────────────────────────
// SVG Import
// ─────────────────────────────────────────────────

function importSvg(svgText) {
  const parser = new DOMParser();
  const doc = parser.parseFromString(svgText, 'image/svg+xml');
  const svgRoot = doc.querySelector('svg');
  if (!svgRoot) { setStatus('Import failed: no SVG root found'); return; }

  // Extract viewBox dimensions
  const vb = svgRoot.getAttribute('viewBox');
  if (vb) {
    const parts = vb.split(/\s+/).map(Number);
    if (parts.length >= 4) {
      state.floorWidth = parts[2] / SCALE;
      state.floorHeight = parts[3] / SCALE;
      document.getElementById('floor-width').value = state.floorWidth;
      document.getElementById('floor-height').value = state.floorHeight;
    }
  }

  pushUndo();
  state.elements = [];
  state.selectedId = null;

  // Import rooms
  for (const rect of doc.querySelectorAll('.rooms rect.room')) {
    const key = rect.getAttribute('data-room') || `room_${nextId}`;
    state.elements.push({
      id: genId(), type: 'room',
      data: {
        x: +rect.getAttribute('x'), y: +rect.getAttribute('y'),
        w: +rect.getAttribute('width'), h: +rect.getAttribute('height'),
        key, label: key.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())
      }
    });
  }

  // Import walls
  for (const line of doc.querySelectorAll('.walls line')) {
    const cls = line.getAttribute('class') || '';
    const subtype = cls.includes('wall-interior') ? 'interior' : 'exterior';
    state.elements.push({
      id: genId(), type: 'wall', subtype,
      data: {
        x1: +line.getAttribute('x1'), y1: +line.getAttribute('y1'),
        x2: +line.getAttribute('x2'), y2: +line.getAttribute('y2')
      }
    });
  }

  // Import doors
  for (const line of doc.querySelectorAll('.doors line')) {
    const cls = line.getAttribute('class') || '';
    let subtype = 'interior';
    if (cls.includes('door-exterior')) subtype = 'exterior';
    else if (cls.includes('door-garage')) subtype = 'garage';
    state.elements.push({
      id: genId(), type: 'door', subtype,
      data: {
        x1: +line.getAttribute('x1'), y1: +line.getAttribute('y1'),
        x2: +line.getAttribute('x2'), y2: +line.getAttribute('y2')
      }
    });
  }

  // Import windows
  for (const line of doc.querySelectorAll('.windows line')) {
    state.elements.push({
      id: genId(), type: 'window',
      data: {
        x1: +line.getAttribute('x1'), y1: +line.getAttribute('y1'),
        x2: +line.getAttribute('x2'), y2: +line.getAttribute('y2')
      }
    });
  }

  // Import labels
  for (const text of doc.querySelectorAll('.room-labels text')) {
    state.elements.push({
      id: genId(), type: 'label',
      data: {
        x: +text.getAttribute('x'), y: +text.getAttribute('y'),
        text: text.textContent
      }
    });
  }

  // Import sensors
  for (const circle of doc.querySelectorAll('.sensors circle')) {
    const cls = circle.getAttribute('class') || '';
    const subtype = cls.includes('sensor-rx') ? 'rx' : 'tx';
    state.elements.push({
      id: genId(), type: 'sensor', subtype,
      data: { cx: +circle.getAttribute('cx'), cy: +circle.getAttribute('cy') }
    });
  }

  updateViewBox();
  render();
  refreshPropertyPanel();
  updateElementCount();
  setStatus(`Imported ${state.elements.length} elements`);
}

// ─────────────────────────────────────────────────
// UI Wiring
// ─────────────────────────────────────────────────

function setStatus(msg) {
  document.getElementById('status-message').textContent = msg;
}

function updateCursorPos(pt) {
  document.getElementById('cursor-pos').textContent = `${toMeters(pt.x)}, ${toMeters(pt.y)} m`;
}

function updateElementCount() {
  document.getElementById('element-count').textContent = `${state.elements.length} elements`;
}

function setTool(tool) {
  state.activeTool = tool;
  document.querySelectorAll('.tool-btn').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.tool === tool);
  });
  container.className = `tool-${tool}`;
  setStatus(`Tool: ${tool}`);
}

function init() {
  // Tool buttons
  document.querySelectorAll('.tool-btn').forEach(btn => {
    btn.addEventListener('click', () => setTool(btn.dataset.tool));
  });

  // Wall type radio
  document.querySelectorAll('input[name="wall-type"]').forEach(r => {
    r.addEventListener('change', () => { state.wallType = r.value; });
  });

  // Sensor type radio
  document.querySelectorAll('input[name="sensor-type"]').forEach(r => {
    r.addEventListener('change', () => { state.sensorType = r.value; });
  });

  // Snap toggle
  document.getElementById('snap-toggle').addEventListener('change', (e) => {
    state.snapEnabled = e.target.checked;
  });

  // Floor dimension inputs
  document.getElementById('floor-name').addEventListener('change', (e) => {
    state.floorName = e.target.value;
  });
  document.getElementById('floor-width').addEventListener('change', (e) => {
    state.floorWidth = parseFloat(e.target.value) || 18;
    updateViewBox();
  });
  document.getElementById('floor-height').addEventListener('change', (e) => {
    state.floorHeight = parseFloat(e.target.value) || 10.5;
    updateViewBox();
  });

  // Action buttons
  document.getElementById('btn-undo').addEventListener('click', undo);
  document.getElementById('btn-redo').addEventListener('click', redo);
  document.getElementById('btn-delete').addEventListener('click', () => {
    if (state.selectedId) removeElement(state.selectedId);
  });
  document.getElementById('btn-clear').addEventListener('click', () => {
    if (!state.elements.length) return;
    if (!confirm('Clear all elements?')) return;
    pushUndo();
    state.elements = [];
    state.selectedId = null;
    render();
    refreshPropertyPanel();
    updateElementCount();
    setStatus('Canvas cleared');
  });

  // File operations
  document.getElementById('btn-import').addEventListener('click', () => {
    document.getElementById('file-import').click();
  });
  document.getElementById('file-import').addEventListener('change', (e) => {
    const file = e.target.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => importSvg(reader.result);
    reader.readAsText(file);
    e.target.value = '';
  });
  document.getElementById('btn-export-svg').addEventListener('click', () => {
    const svgContent = generateSvg();
    downloadFile(svgContent, `${state.floorName.toLowerCase().replace(/\s+/g, '_')}.svg`, 'image/svg+xml');
    setStatus('SVG exported');
  });
  document.getElementById('btn-export-config').addEventListener('click', () => {
    const config = generateConfigSnippet();
    document.getElementById('config-preview').value = config;
    setStatus('Config generated');
  });
  document.getElementById('btn-copy-config').addEventListener('click', () => {
    const textarea = document.getElementById('config-preview');
    if (textarea.value) {
      navigator.clipboard.writeText(textarea.value).then(
        () => setStatus('Config copied to clipboard'),
        () => { textarea.select(); document.execCommand('copy'); setStatus('Config copied'); }
      );
    }
  });

  // Keyboard shortcuts
  document.addEventListener('keydown', (e) => {
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.tagName === 'SELECT') return;

    if (e.key === ' ') { spaceDown = true; e.preventDefault(); return; }

    switch (e.key.toLowerCase()) {
      case 'v': setTool('select'); break;
      case 'r': setTool('room'); break;
      case 'w': setTool('wall'); break;
      case 'd': setTool('door'); break;
      case 'n': setTool('window'); break;
      case 'l': setTool('label'); break;
      case 's': setTool('sensor'); break;
      case 'z':
        if (e.ctrlKey || e.metaKey) { e.preventDefault(); undo(); }
        break;
      case 'y':
        if (e.ctrlKey || e.metaKey) { e.preventDefault(); redo(); }
        break;
      case 'delete':
      case 'backspace':
        if (state.selectedId) { e.preventDefault(); removeElement(state.selectedId); }
        break;
      case 'escape':
        drawState = null;
        previewLayer.innerHTML = '';
        state.selectedId = null;
        render();
        refreshPropertyPanel();
        break;
    }
  });

  document.addEventListener('keyup', (e) => {
    if (e.key === ' ') spaceDown = false;
  });

  // Bind property panel inputs
  bindPropInputs();

  // localStorage persistence
  loadFromStorage();

  // Initialize view
  updateViewBox();
  render();
  updateElementCount();
  setStatus('Ready — draw rooms, walls, doors, windows, labels, and sensors');

  // Auto-save
  setInterval(saveToStorage, 5000);
}

// ─────────────────────────────────────────────────
// localStorage Persistence
// ─────────────────────────────────────────────────

const STORAGE_KEY = 'floorplan-editor-state';

function saveToStorage() {
  try {
    const data = {
      floorName: state.floorName,
      floorWidth: state.floorWidth,
      floorHeight: state.floorHeight,
      elements: state.elements,
      nextId,
    };
    localStorage.setItem(STORAGE_KEY, JSON.stringify(data));
  } catch (e) { /* quota exceeded — ignore */ }
}

function loadFromStorage() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return;
    const data = JSON.parse(raw);
    state.floorName = data.floorName || 'New Floor';
    state.floorWidth = data.floorWidth || 18;
    state.floorHeight = data.floorHeight || 10.5;
    state.elements = data.elements || [];
    nextId = data.nextId || 1;
    document.getElementById('floor-name').value = state.floorName;
    document.getElementById('floor-width').value = state.floorWidth;
    document.getElementById('floor-height').value = state.floorHeight;
  } catch (e) { /* corrupt data — start fresh */ }
}

// Boot
init();
