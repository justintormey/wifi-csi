/**
 * WiFi CSI Floor Plan Renderer
 *
 * Loads SVG floor plans, scales to viewport, handles floor switching.
 * Coordinate system: meters from origin (matches backend).
 * SVG viewBox uses 100x scale (1 meter = 100 SVG units).
 *
 * Usage:
 *   import { FloorPlanRenderer } from './floorplan.js';
 *   const renderer = new FloorPlanRenderer(document.getElementById('floorplan-container'));
 *   await renderer.init();
 *   renderer.switchFloor(1);
 */

import { CONFIG } from './config.js';

const SVG_SCALE = 100; // 1 meter = 100 SVG units

export class FloorPlanRenderer {
  /**
   * @param {HTMLElement} container - DOM element to render floor plans into
   */
  constructor(container) {
    this.container = container;
    this.currentFloor = 1;
    this.svgElements = {};  // floor id → SVG element
    this.loaded = false;

    // Coordinate conversion cache
    this._containerRect = null;
    this._svgRect = null;
  }

  /** Load all floor plan SVGs and build floor switcher */
  async init() {
    this.container.classList.add('floorplan-container');

    // Load all floor SVGs in parallel
    const loadPromises = CONFIG.floors.map(floor => this._loadFloorSVG(floor));
    await Promise.all(loadPromises);

    // Build floor switcher UI
    this._buildSwitcher();

    // Show first floor
    this.switchFloor(CONFIG.floors[0]?.id || 1);
    this.loaded = true;

    // Update container rect on resize
    window.addEventListener('resize', () => {
      this._containerRect = null;
      this._svgRect = null;
    });
  }

  /**
   * Switch to a different floor
   * @param {number} floorId - Floor ID to display
   */
  switchFloor(floorId) {
    if (this.currentFloor === floorId && this.svgElements[floorId]?.classList.contains('floorplan')) {
      return; // Already showing this floor
    }

    // Hide all floors
    for (const [id, svg] of Object.entries(this.svgElements)) {
      svg.classList.add('floor-hidden');
    }

    // Show requested floor
    const svg = this.svgElements[floorId];
    if (svg) {
      svg.classList.remove('floor-hidden');
      this.currentFloor = floorId;
      this._containerRect = null;
      this._svgRect = null;
    }

    // Update switcher buttons
    const buttons = this.container.querySelectorAll('.floor-switcher button');
    buttons.forEach(btn => {
      btn.classList.toggle('active', parseInt(btn.dataset.floor) === floorId);
    });
  }

  /**
   * Convert backend coordinates (meters) to pixel position within the container.
   * @param {number} x - X position in meters
   * @param {number} y - Y position in meters
   * @param {number} [floorId] - Floor ID (defaults to current)
   * @returns {{ px: number, py: number } | null}
   */
  metersToPixels(x, y, floorId) {
    const fId = floorId || this.currentFloor;
    const svg = this.svgElements[fId];
    if (!svg) return null;

    const floor = CONFIG.floors.find(f => f.id === fId);
    if (!floor) return null;

    // Get the SVG's rendered bounding box
    const svgRect = svg.getBoundingClientRect();
    const containerRect = this.container.getBoundingClientRect();

    // SVG viewBox dimensions
    const vbWidth = floor.width * SVG_SCALE;
    const vbHeight = floor.height * SVG_SCALE;

    // The SVG preserves aspect ratio (default preserveAspectRatio="xMidYMid meet").
    // Calculate the actual rendered area within the SVG element.
    const svgAspect = vbWidth / vbHeight;
    const elemAspect = svgRect.width / svgRect.height;

    let renderWidth, renderHeight, offsetX, offsetY;

    if (elemAspect > svgAspect) {
      // Element is wider than SVG content — bars on left/right
      renderHeight = svgRect.height;
      renderWidth = renderHeight * svgAspect;
      offsetX = (svgRect.width - renderWidth) / 2;
      offsetY = 0;
    } else {
      // Element is taller — bars on top/bottom
      renderWidth = svgRect.width;
      renderHeight = renderWidth / svgAspect;
      offsetX = 0;
      offsetY = (svgRect.height - renderHeight) / 2;
    }

    // Convert meters to pixel position relative to container
    const px = (svgRect.left - containerRect.left) + offsetX + (x / floor.width) * renderWidth;
    const py = (svgRect.top - containerRect.top) + offsetY + (y / floor.height) * renderHeight;

    return { px, py };
  }

  /**
   * Convert pixel position to meters.
   * @param {number} px - Pixel X relative to container
   * @param {number} py - Pixel Y relative to container
   * @param {number} [floorId] - Floor ID (defaults to current)
   * @returns {{ x: number, y: number } | null}
   */
  pixelsToMeters(px, py, floorId) {
    const fId = floorId || this.currentFloor;
    const svg = this.svgElements[fId];
    if (!svg) return null;

    const floor = CONFIG.floors.find(f => f.id === fId);
    if (!floor) return null;

    const svgRect = svg.getBoundingClientRect();
    const containerRect = this.container.getBoundingClientRect();

    const vbWidth = floor.width * SVG_SCALE;
    const vbHeight = floor.height * SVG_SCALE;
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

    const relX = px - (svgRect.left - containerRect.left) - offsetX;
    const relY = py - (svgRect.top - containerRect.top) - offsetY;

    const x = (relX / renderWidth) * floor.width;
    const y = (relY / renderHeight) * floor.height;

    return { x, y };
  }

  /**
   * Get the floor config for a given floor ID.
   * @param {number} floorId
   * @returns {object|undefined}
   */
  getFloorConfig(floorId) {
    return CONFIG.floors.find(f => f.id === (floorId || this.currentFloor));
  }

  /** @returns {number} Current floor ID */
  getCurrentFloor() {
    return this.currentFloor;
  }

  /** @returns {SVGElement|null} Current floor SVG element */
  getCurrentSVG() {
    return this.svgElements[this.currentFloor] || null;
  }

  // ── Private ──────────────────────────────────────────────────

  async _loadFloorSVG(floor) {
    try {
      const response = await fetch(floor.svgPath);
      if (!response.ok) throw new Error(`HTTP ${response.status} loading ${floor.svgPath}`);

      const svgText = await response.text();
      const parser = new DOMParser();
      const doc = parser.parseFromString(svgText, 'image/svg+xml');
      const svg = doc.documentElement;

      // Ensure proper attributes
      svg.setAttribute('class', 'floorplan floor-hidden');
      svg.setAttribute('data-floor', floor.id);
      svg.setAttribute('preserveAspectRatio', 'xMidYMid meet');

      this.container.appendChild(svg);
      this.svgElements[floor.id] = svg;
    } catch (err) {
      console.error(`Failed to load floor plan for floor ${floor.id}:`, err);
    }
  }

  _buildSwitcher() {
    const switcher = document.createElement('div');
    switcher.className = 'floor-switcher';

    // Reverse so top floor is at top of button list
    const sortedFloors = [...CONFIG.floors].sort((a, b) => b.id - a.id);

    for (const floor of sortedFloors) {
      const btn = document.createElement('button');
      btn.textContent = `F${floor.id}`;
      btn.title = floor.name;
      btn.dataset.floor = floor.id;
      btn.addEventListener('click', () => this.switchFloor(floor.id));
      switcher.appendChild(btn);
    }

    this.container.appendChild(switcher);
  }
}
