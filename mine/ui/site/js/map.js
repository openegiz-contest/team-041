// Top-down plan of the mine on two stacked 2D canvases. Geometry comes from
// the twins' `geo` attributes (metres on the mine grid, y pointing north);
// state comes from their features.
//
// The lower canvas holds everything that changes rarely (ground, pit, blocks,
// roads, plant, equipment, captions) and is redrawn only when the view or the
// data changed. The upper canvas holds what moves (trucks, the flow on the
// conveyor) and each frame repaints only the small rectangles it touched, so
// a frame stays cheap without a graphics card.

import { twins, byKind, first, feature, kindOf, titleOf, relatedIds, activeScenarios } from './store.js';
import { C, LAYERS, DEPLETED, truckColor, stateColor, refreshDestinationLegend } from './theme.js';
import { shortTitle, stateText, formatNumber } from './labels.js';
import { createMotion } from './motion.js';
import { plantLayout, productFigures } from './plant.js';
import { rate } from './rates.js';
import { buildPit } from './pitgeom.js';

const FONT = 'system-ui, "Segoe UI", Roboto, sans-serif';
const TAU = Math.PI * 2;
const MAX_SCALE = 5;                       // pixels per metre
let INSET = { top: 50, right: 14, bottom: 14, left: 14 };     // room the floating panels take when fitting a view (setInsets)
const STATIC_DELAY_MS = 350;               // data changes are gathered this long before the lower canvas is redrawn

const clamp = (value, low, high) => Math.min(Math.max(value, low), high);

export function createMap({ staticCanvas, liveCanvas, onSelect, onHover, onView }) {
  const sctx = staticCanvas.getContext('2d', { alpha: false });
  const lctx = liveCanvas.getContext('2d');
  // Captions stand over everything that moves: a truck passing by does not cover a name.
  const captionCanvas = document.createElement('canvas');
  captionCanvas.className = 'plan-captions';
  captionCanvas.setAttribute('aria-hidden', 'true');
  captionCanvas.style.pointerEvents = 'none';
  liveCanvas.after(captionCanvas);
  const cctx = captionCanvas.getContext('2d');
  const motion = createMotion();

  let width = 0;
  let height = 0;
  let dpr = 1;
  let view = null;                 // {scale, cx, cy}: pixels per metre and the world point at the canvas centre
  let home = null;                 // the view that shows the whole mine
  let atHome = true;
  let tween = null;
  let layer = 'status';
  let selectedId = null;
  let related = new Set();
  let affected = new Map();        // thingId -> {twin, target} of the scenario that touches it
  let overlayRects = [];           // screen rectangles covered by the toolbar: captions keep out
  let staticDirty = true;
  let staticDue = 0;
  let liveDirty = true;
  let worldBox = null;
  let hits = [];                   // pick targets of the lower canvas, topmost first
  let staticBoxes = [];            // caption rectangles of the lower canvas
  const truckAt = new Map();       // thingId -> {x, y, r, ox, oy} on screen
  let liveRects = [];
  let flows = [];
  let millScreens = [];            // where the mills are drawn, for their turning marks
  let notes = [];                  // [{id, text}]: the end of an open forecast, written next to its objects
  const plantAt = new Map();       // thingId -> {x, y, r}: where the plant units are drawn

  const X = (x) => width / 2 + (x - view.cx) * view.scale;
  const Y = (y) => height / 2 - (y - view.cy) * view.scale;

  // -- geometry of the twins --------------------------------------------------

  function centroid(points) {
    let x = 0;
    let y = 0;
    for (const p of points) { x += p[0]; y += p[1]; }
    return { x: x / points.length, y: y / points.length };
  }

  function midpoint(path) {
    const lengths = path.slice(1).map((p, i) => Math.hypot(p[0] - path[i][0], p[1] - path[i][1]));
    let half = lengths.reduce((a, b) => a + b, 0) / 2;
    for (let i = 0; i < lengths.length; i += 1) {
      if (half <= lengths[i]) {
        const f = half / (lengths[i] || 1);
        return { x: path[i][0] + (path[i + 1][0] - path[i][0]) * f, y: path[i][1] + (path[i + 1][1] - path[i][1]) * f };
      }
      half -= lengths[i];
    }
    return { x: path[0][0], y: path[0][1] };
  }

  // Where a twin is on the mine grid, or null for twins without a place.
  function anchorOf(twin, now = performance.now()) {
    const kind = kindOf(twin);
    if (kind === 'truck') {
      const at = motion.shown(twin.thingId, now);
      if (at) return { x: at.x, y: at.y };
    }
    if (kind === 'loader' || kind === 'drill-rig') {
      const x = feature(twin, 'pos_x');
      const y = feature(twin, 'pos_y');
      if (typeof x === 'number' && typeof y === 'number') return { x, y };
    }
    const geo = twin.attributes?.geo ?? {};
    if (kind === 'mine') return null;
    if (geo.polygon) return centroid(geo.polygon);
    if (geo.path) return midpoint(geo.path);
    if (typeof geo.x === 'number') return { x: geo.x, y: geo.y };
    return null;
  }

  function boxOf(twinList) {
    const box = { minX: Infinity, maxX: -Infinity, minY: Infinity, maxY: -Infinity };
    const add = (x, y) => {
      box.minX = Math.min(box.minX, x); box.maxX = Math.max(box.maxX, x);
      box.minY = Math.min(box.minY, y); box.maxY = Math.max(box.maxY, y);
    };
    for (const twin of twinList) {
      const geo = twin.attributes?.geo ?? {};
      if (geo.polygon) geo.polygon.forEach(([x, y]) => add(x, y));
      else if (geo.path) geo.path.forEach(([x, y]) => add(x, y));
      else if (typeof geo.x === 'number') add(geo.x, geo.y);
    }
    return Number.isFinite(box.minX) ? box : null;
  }

  // -- view -------------------------------------------------------------------

  function viewFor(box, pad = 26, maxScale = MAX_SCALE) {
    const availW = Math.max(50, width - INSET.left - INSET.right - 2 * pad);
    const availH = Math.max(50, height - INSET.top - INSET.bottom - 2 * pad);
    const scale = Math.min(availW / Math.max(box.maxX - box.minX, 1), availH / Math.max(box.maxY - box.minY, 1), maxScale);
    const centreX = INSET.left + (width - INSET.left - INSET.right) / 2;
    const centreY = INSET.top + (height - INSET.top - INSET.bottom) / 2;
    return {
      scale,
      cx: (box.minX + box.maxX) / 2 - (centreX - width / 2) / scale,
      cy: (box.minY + box.maxY) / 2 + (centreY - height / 2) / scale,
    };
  }

  function setView(next, animate = true) {
    if (!next) return;
    atHome = false;
    if (animate && view) {
      tween = { from: { ...view }, to: next, start: performance.now(), ms: 260 };
    } else {
      tween = null;
      view = next;
    }
    staticDirty = true;
  }

  function resize() {
    const box = staticCanvas.parentElement.getBoundingClientRect();
    const nextWidth = Math.max(100, Math.round(box.width));
    const nextHeight = Math.max(100, Math.round(box.height));
    const nextDpr = clamp(window.devicePixelRatio || 1, 1, 2);
    if (nextWidth === width && nextHeight === height && nextDpr === dpr) return;
    width = nextWidth;
    height = nextHeight;
    dpr = nextDpr;
    for (const canvas of [staticCanvas, liveCanvas, captionCanvas]) {
      canvas.width = Math.round(width * dpr);
      canvas.height = Math.round(height * dpr);
    }
    sprites.clear();
    liveRects = [];
    if (worldBox) {
      home = viewFor(worldBox);
      if (atHome || !view) { view = { ...home }; tween = null; atHome = true; }
    }
    staticDirty = true;
  }

  function zoomAt(factor, px = width / 2, py = height / 2, animate = true) {
    if (!view || !home) return;
    const base = tween ? tween.to : view;
    const scale = clamp(base.scale * factor, home.scale * 0.7, MAX_SCALE);
    const wx = base.cx + (px - width / 2) / base.scale;
    const wy = base.cy - (py - height / 2) / base.scale;
    setView({ scale, cx: wx - (px - width / 2) / scale, cy: wy + (py - height / 2) / scale }, animate);
  }

  function stepTween(now) {
    if (!tween) return;
    const f = clamp((now - tween.start) / tween.ms, 0, 1);
    const e = 1 - (1 - f) ** 3;
    // Zoom is eased on a logarithmic scale so that it feels even.
    view = {
      scale: Math.exp(Math.log(tween.from.scale) + (Math.log(tween.to.scale) - Math.log(tween.from.scale)) * e),
      cx: tween.from.cx + (tween.to.cx - tween.from.cx) * e,
      cy: tween.from.cy + (tween.to.cy - tween.from.cy) * e,
    };
    if (f >= 1) tween = null;
    staticDirty = true;
  }

  // Size of glyphs: larger on a larger canvas, growing slowly with the zoom.
  function glyphSize() {
    const canvasFactor = clamp(Math.min(width, height * 1.7) / 680, 1, 1.4);
    const zoomFactor = clamp(1 + 0.24 * Math.log2(view.scale / home.scale), 0.9, 2);
    return canvasFactor * zoomFactor;
  }

  // -- drawing helpers --------------------------------------------------------

  function tracePolygon(ctx, points) {
    ctx.beginPath();
    for (let i = 0; i < points.length; i += 1) {
      if (i) ctx.lineTo(X(points[i][0]), Y(points[i][1]));
      else ctx.moveTo(X(points[i][0]), Y(points[i][1]));
    }
    ctx.closePath();
  }

  function tracePath(ctx, points) {
    ctx.beginPath();
    for (let i = 0; i < points.length; i += 1) {
      if (i) ctx.lineTo(X(points[i][0]), Y(points[i][1]));
      else ctx.moveTo(X(points[i][0]), Y(points[i][1]));
    }
  }

  function roundRect(ctx, x, y, w, h, r) {
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.arcTo(x + w, y, x + w, y + h, r);
    ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r);
    ctx.arcTo(x, y, x + w, y, r);
    ctx.closePath();
  }

  function circle(ctx, x, y, r) {
    ctx.beginPath();
    ctx.arc(x, y, r, 0, TAU);
  }

  const isSelected = (id) => id === selectedId;
  const isRelated = (id) => related.has(id);

  // Rings around a point object: selected, linked to the selection, touched by a scenario.
  function rings(ctx, id, x, y, r) {
    let radius = r + 3.5;
    if (isSelected(id) || isRelated(id)) {
      ctx.strokeStyle = C.select;
      ctx.lineWidth = isSelected(id) ? 2.5 : 1.5;
      ctx.setLineDash(isSelected(id) ? [] : [3, 3]);
      circle(ctx, x, y, radius);
      ctx.stroke();
      radius += 4;
    }
    if (affected.has(id)) {
      ctx.strokeStyle = C.scenario;
      ctx.lineWidth = 2;
      ctx.setLineDash([5, 3]);
      circle(ctx, x, y, radius);
      ctx.stroke();
    }
    ctx.setLineDash([]);
  }

  // -- captions ---------------------------------------------------------------
  // Captions are requested while the shapes are drawn and placed afterwards in
  // order of importance; one that would overlap an earlier caption or a glyph
  // tries another side and is left out when no side is free.

  const ALL_SIDES = ['r', 'l', 'b', 't', 'tr', 'br', 'tl', 'bl'];
  const widths = new Map();
  let requests = [];
  let boxes = [];
  let glyphs = [];                 // keep-out squares of the glyphs, for the page check
  let segments = [];               // lines captions do not cross: [x1, y1, x2, y2, pad]
  let placed = [];                 // captions drawn in the last frame: {text, rect, pill}

  function textWidth(ctx, font, text) {
    const key = `${font}|${text}`;
    let value = widths.get(key);
    if (value === undefined) {
      ctx.font = font;
      value = ctx.measureText(text).width;
      if (widths.size > 4000) widths.clear();
      widths.set(key, value);
    }
    return value;
  }

  // An object that is outside the plan has no caption.
  function caption(priority, text, x, y, options = {}) {
    if (text && x > -10 && x < width + 10 && y > -10 && y < height + 10) requests.push({ priority, text, x, y, ...options });
  }

  function keepOut(x, y, r) {
    boxes.push([x - r, y - r, x + r, y + r]);
    glyphs.push([x - r, y - r, x + r, y + r]);
  }

  // A line a caption must not cross (a pipe of the plant, the frame of the
  // grinding circuit), in screen pixels, with half its width as `pad`.
  // `owner` names the twin the line belongs to: its own caption may run along it.
  function keepOffLine(points, pad, owner = '') {
    for (let i = 1; i < points.length; i += 1) {
      segments.push([points[i - 1][0], points[i - 1][1], points[i][0], points[i][1], pad, owner]);
    }
  }

  // Does the segment cross the rectangle (grown by the segment's pad)? Liang-Barsky.
  function crosses(rect, [x1, y1, x2, y2, pad]) {
    const dx = x2 - x1;
    const dy = y2 - y1;
    const p = [-dx, dx, -dy, dy];
    const q = [x1 - (rect[0] - pad), rect[2] + pad - x1, y1 - (rect[1] - pad), rect[3] + pad - y1];
    let t0 = 0;
    let t1 = 1;
    for (let i = 0; i < 4; i += 1) {
      if (p[i] === 0) { if (q[i] < 0) return false; continue; }
      const u = q[i] / p[i];
      if (p[i] < 0) { if (u > t1) return false; if (u > t0) t0 = u; }
      else { if (u < t0) return false; if (u < t1) t1 = u; }
    }
    return true;
  }

  const offLines = (rect, owner) => segments.every((segment) => (owner && segment[5] === owner) || !crosses(rect, segment));

  function isFree(rect, list) {
    for (const b of list) {
      if (rect[0] < b[2] && rect[2] > b[0] && rect[1] < b[3] && rect[3] > b[1]) return false;
    }
    return true;
  }

  function captionRect(l, side, w, h) {
    const gap = l.gap ?? 6;
    switch (side) {
      case 'c': return [l.x - w / 2, l.y - h / 2];
      case 'r': return [l.x + gap, l.y - h / 2];
      case 'l': return [l.x - gap - w, l.y - h / 2];
      case 'b': return [l.x - w / 2, l.y + gap];
      case 'tr': return [l.x + gap * 0.7, l.y - gap * 0.7 - h];
      case 'br': return [l.x + gap * 0.7, l.y + gap * 0.7];
      case 'tl': return [l.x - gap * 0.7 - w, l.y - gap * 0.7 - h];
      case 'bl': return [l.x - gap * 0.7 - w, l.y + gap * 0.7];
      default: return [l.x - w / 2, l.y - gap - h];
    }
  }

  function paintCaption(ctx, l, x0, y0, w, h, font) {
    ctx.font = font;
    ctx.textAlign = 'left';
    ctx.textBaseline = 'middle';
    if (l.pill) {
      roundRect(ctx, x0, y0, w, h, 4);
      ctx.fillStyle = 'rgba(18, 23, 28, 0.94)';
      ctx.fill();
      ctx.strokeStyle = l.border ?? C.inkMuted;
      ctx.lineWidth = l.dashed ? 1 : 1.5;
      if (l.dashed) ctx.setLineDash([3, 2]);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = l.color ?? C.ink;
      ctx.fillText(l.text, x0 + 6, y0 + h / 2 + 0.5);
    } else if (l.more) {
      // Two lines, centred: the name, and a quieter line of values under it.
      const line = h / 2;
      ctx.textAlign = 'center';
      ctx.lineJoin = 'round';
      ctx.lineWidth = 3;
      ctx.strokeStyle = C.halo;
      ctx.strokeText(l.text, x0 + w / 2, y0 + line / 2);
      ctx.fillStyle = l.color ?? C.inkSoft;
      ctx.fillText(l.text, x0 + w / 2, y0 + line / 2);
      ctx.font = font.replace('600 ', '');
      ctx.strokeText(l.more, x0 + w / 2, y0 + line * 1.5);
      ctx.fillStyle = C.inkSoft;
      ctx.fillText(l.more, x0 + w / 2, y0 + line * 1.5);
    } else {
      ctx.lineJoin = 'round';
      ctx.lineWidth = 3;
      ctx.strokeStyle = C.halo;
      ctx.strokeText(l.text, x0, y0 + h / 2);
      ctx.fillStyle = l.color ?? C.inkSoft;
      ctx.fillText(l.text, x0, y0 + h / 2);
    }
  }

  // A caption standing away from its object is tied to it by a thin line,
  // from the edge of the glyph to the nearest point of the caption.
  function leader(ctx, l, x0, y0, w, h) {
    const ex = clamp(l.x, x0, x0 + w);
    const ey = clamp(l.y, y0, y0 + h);
    const length = Math.hypot(ex - l.x, ey - l.y);
    const from = Math.max(0, (l.gap ?? 6) - 2);
    if (length - from < 6) return;
    const ux = (ex - l.x) / length;
    const uy = (ey - l.y) / length;
    ctx.beginPath();
    ctx.moveTo(l.x + ux * from, l.y + uy * from);
    ctx.lineTo(ex - ux * 2, ey - uy * 2);
    ctx.strokeStyle = C.inkMuted;
    ctx.lineWidth = 1;
    ctx.setLineDash([2, 2]);
    ctx.stroke();
    ctx.setLineDash([]);
  }

  // Try the places of a caption in turn; true when one was free and the caption drawn there.
  function tryPlace(ctx, l, font, two) {
    const size = l.size ?? 11;
    const w = Math.max(textWidth(ctx, font, l.text), two ? textWidth(ctx, font.replace('600 ', ''), l.more) : 0) + (l.pill ? 12 : 0);
    const h = (size + (l.pill ? 8 : 4)) * (two ? 2 : 1);
    const sides = l.sides ?? ALL_SIDES;
    const shown = two ? l : { ...l, more: '' };
    // A caption of the plant may stand a little further out when its usual
    // places are taken; a pill sits on its own pipe and does not avoid lines.
    const reaches = l.far ? [0, 8, 18, 30, 44] : [0];
    for (const reach of reaches) {
      const at = reach ? { ...l, gap: (l.gap ?? 6) + reach } : l;
      for (const side of sides) {
        const [x1, y0] = captionRect(at, side, w, h);
        // A caption above or below its object may slide sideways to stay on
        // the plan and, for the plant, to find a free place, as long as it
        // still stands over or under its object.
        let shifts = [x1];
        if (side === 't' || side === 'b') {
          const fit = (x) => clamp(clamp(x, 3, Math.max(3, width - w - 3)), l.x - w + 8, l.x - 8);
          shifts = l.far ? [0, 0.25, -0.25, 0.5, -0.5].map((k) => fit(x1 + k * w)) : [fit(x1)];
        }
        for (const x0 of shifts) {
          const rect = [x0 - 2, y0 - 1, x0 + w + 2, y0 + h + 1];
          const inside = x0 >= 3 && x0 + w <= width - 3 && y0 >= 3 && y0 + h <= height - 3;
          if (inside && isFree(rect, boxes) && isFree(rect, overlayRects) && (l.pill || offLines(rect, l.owner))) {
            if (reach && !l.pill) leader(ctx, l, x0, y0, w, h);
            paintCaption(ctx, shown, x0, y0, w, h, font);
            boxes.push(rect);
            placed.push({ text: l.text, more: shown.more ?? '', rect: [x0, y0, x0 + w, y0 + h], pill: Boolean(l.pill) });
            return true;
          }
        }
      }
    }
    return false;
  }

  function placeCaptions(ctx) {
    requests.sort((a, b) => a.priority - b.priority);
    const forced = [];
    for (const l of requests) {
      const size = l.size ?? 11;
      const font = `${l.bold ? '600 ' : ''}${size}px ${FONT}`;
      const two = Boolean(l.more) && !l.pill;
      const w = Math.max(textWidth(ctx, font, l.text), two ? textWidth(ctx, font.replace('600 ', ''), l.more) : 0) + (l.pill ? 12 : 0);
      const h = (size + (l.pill ? 8 : 4)) * (two ? 2 : 1);
      const sides = l.sides ?? ALL_SIDES;
      // Without room for its line of figures, a caption of the plant keeps its name line alone.
      const done = tryPlace(ctx, l, font, two) || (two && l.far && tryPlace(ctx, l, font, false));
      if (!done && l.force) {
        // A caption that must show may cover ordinary ones, but not another one that must show.
        let place = null;
        for (const side of [...sides, ...ALL_SIDES]) {
          let [x0, y0] = captionRect(l, side, w, h);
          x0 = clamp(x0, 3, Math.max(3, width - w - 3));
          y0 = clamp(y0, 3, Math.max(3, height - h - 3));
          if (!place) place = [x0, y0];
          if (isFree([x0 - 2, y0 - 1, x0 + w + 2, y0 + h + 1], forced)) { place = [x0, y0]; break; }
        }
        const [x0, y0] = place;
        paintCaption(ctx, l, x0, y0, w, h, font);
        boxes.push([x0 - 2, y0 - 1, x0 + w + 2, y0 + h + 1]);
        placed.push({ text: l.text, more: l.more ?? '', rect: [x0, y0, x0 + w, y0 + h], pill: Boolean(l.pill), forced: true });
      }
      if (l.force) forced.push(boxes[boxes.length - 1]);
    }
  }

  // The caption of one object: its usual text at its usual importance, or a
  // stronger one when it is selected, linked to the selection or touched by a
  // scenario.
  function captionFor(twin, x, y, gap, priority, text, options = {}) {
    const id = twin.thingId;
    const kind = kindOf(twin);
    const mark = affected.get(id);
    if (isSelected(id)) {
      caption(0, titleOf(twin), x, y, { ...options, gap: gap + 4, pill: true, border: C.select, bold: true, force: true, size: 11 });
    } else if (mark && mark.target === id) {
      caption(0.2, `Сценарий: ${titleOf(mark.twin)}`, x, y, { ...options, gap: gap + 5, pill: true, border: C.scenario, bold: true, force: true, size: 11 });
    } else if (isRelated(id) && kind !== 'road-node' && kind !== 'road-segment') {
      // Roads on the route of the selection are marked by the band alone: their names would bury the plan.
      caption(1, text ?? shortTitle(kind, titleOf(twin)), x, y, { ...options, gap: gap + 4, color: C.ink, bold: true });
    } else if (mark && kind !== 'road-node' && kind !== 'road-segment') {
      caption(1.5, text ?? shortTitle(kind, titleOf(twin)), x, y, { ...options, gap: gap + 5, color: C.ink });
    } else if (text) {
      caption(priority, text, x, y, { ...options, gap });
    }
  }

  // -- lower canvas: ground ---------------------------------------------------

  function drawGround(ctx) {
    ctx.fillStyle = C.ground;
    ctx.fillRect(0, 0, width, height);

    // 500 m grid of the mine coordinates.
    const step = 500;
    const left = view.cx - width / 2 / view.scale;
    const right = view.cx + width / 2 / view.scale;
    const bottom = view.cy - height / 2 / view.scale;
    const top = view.cy + height / 2 / view.scale;
    ctx.strokeStyle = C.grid;
    ctx.lineWidth = 1;
    ctx.beginPath();
    for (let x = Math.ceil(left / step) * step; x <= right; x += step) {
      const px = Math.round(X(x)) + 0.5;
      ctx.moveTo(px, 0); ctx.lineTo(px, height);
    }
    for (let y = Math.ceil(bottom / step) * step; y <= top; y += step) {
      const py = Math.round(Y(y)) + 0.5;
      ctx.moveTo(0, py); ctx.lineTo(width, py);
    }
    ctx.stroke();

    const mine = first('mine');
    if (mine?.attributes?.geo?.polygon) {
      tracePolygon(ctx, mine.attributes.geo.polygon);
      ctx.strokeStyle = C.lease;
      ctx.lineWidth = 1;
      ctx.setLineDash([8, 6]);
      ctx.stroke();
      ctx.setLineDash([]);
      const corner = mine.attributes.geo.polygon.reduce((best, p) => (p[1] - p[0] > best[1] - best[0] ? p : best));
      caption(6.5, 'граница горного отвода', X(corner[0]) + 8, Y(corner[1]) + 12, { sides: ['r'], gap: 0, size: 10, color: C.inkMuted });
    }
  }

  function outlineMarks(ctx, id, trace) {
    if (affected.has(id)) {
      trace();
      ctx.strokeStyle = C.scenario;
      ctx.lineWidth = 2.5;
      ctx.setLineDash([5, 3]);
      ctx.stroke();
      ctx.setLineDash([]);
    }
    if (isSelected(id) || isRelated(id)) {
      trace();
      ctx.strokeStyle = C.select;
      ctx.lineWidth = isSelected(id) ? 2.5 : 1.5;
      ctx.setLineDash(isSelected(id) ? [] : [4, 3]);
      ctx.stroke();
      ctx.setLineDash([]);
    }
  }

  function drawAreas(ctx, z) {
    for (const dump of byKind('waste-dump')) {
      const points = dump.attributes?.geo?.polygon;
      if (!points) continue;
      tracePolygon(ctx, points);
      ctx.fillStyle = C.dump;
      ctx.fill();
      ctx.strokeStyle = C.dumpEdge;
      ctx.lineWidth = 1;
      ctx.setLineDash([5, 4]);
      ctx.stroke();
      ctx.setLineDash([]);
      outlineMarks(ctx, dump.thingId, () => tracePolygon(ctx, points));
      const centre = centroid(points);
      const tonnes = feature(dump, 'tonnes_total');
      const queue = feature(dump, 'queue_len');
      let text = z < 0.42 ? 'Отвал' : titleOf(dump);
      if (z >= 0.42 && typeof tonnes === 'number') text += ` · ${formatNumber(tonnes, 0)} т`;
      captionFor(dump, X(centre.x), Y(centre.y), 0, 4, text, { sides: ['c', 't', 'b'], size: 12 });
      if (queue > 0) queueBadge(ctx, X(dump.attributes.spec?.tip_x ?? centre.x), Y(dump.attributes.spec?.tip_y ?? centre.y) - 12, queue);
      hits.push({ id: dump.thingId, type: 'poly', points, order: 5 });
    }

    const pads = byKind('rom-pad');
    for (const pad of pads) {
      const points = pad.attributes?.geo?.polygon;
      if (!points) continue;
      tracePolygon(ctx, points);
      ctx.fillStyle = feature(pad, 'role') === 'high-grade' ? C.padRich : C.padPoor;
      ctx.fill();
      ctx.strokeStyle = C.padEdge;
      ctx.lineWidth = 1;
      ctx.stroke();
      outlineMarks(ctx, pad.thingId, () => tracePolygon(ctx, points));
      const centre = centroid(points);
      const level = feature(pad, 'level_t');
      const lowest = Math.max(...points.map((p) => Y(p[1])));
      const text = z >= 0.4
        ? `${shortTitle('rom-pad', titleOf(pad))}${typeof level === 'number' ? ` · ${formatNumber(level, 0)} т` : ''}` : null;
      captionFor(pad, X(centre.x), lowest, 3, 4.2, text, { sides: ['b', 't', 'c'] });
      if (feature(pad, 'queue_len') > 0) queueBadge(ctx, X(centre.x), Y(centre.y), feature(pad, 'queue_len'));
      hits.push({ id: pad.thingId, type: 'poly', points, order: 5 });
    }
    if (z < 0.4 && pads.length) {
      const box = boxOf(pads);
      caption(4.2, 'Рудные склады', X((box.minX + box.maxX) / 2), Y(box.minY), { gap: 3, sides: ['b', 't'] });
    }
    // The clay-ore stockpile, and the feed stockpile of the clay-ore plant where the trucks tip.
    for (const pile of [...byKind('clay-stockpile'), ...byKind('clay-plant')]) {
      const points = pile.attributes?.geo?.polygon;
      if (!points?.length) continue;
      const own = kindOf(pile) === 'clay-stockpile';
      tracePolygon(ctx, points);
      ctx.fillStyle = C.padClay;
      ctx.fill();
      ctx.strokeStyle = C.padEdge;
      ctx.lineWidth = 1;
      if (!own) ctx.setLineDash([4, 3]);
      ctx.stroke();
      ctx.setLineDash([]);
      if (!own) continue;
      outlineMarks(ctx, pile.thingId, () => tracePolygon(ctx, points));
      const centre = centroid(points);
      const level = feature(pile, 'level_t');
      const lowest = Math.max(...points.map((pt) => Y(pt[1])));
      const text = z >= 0.3 ? `Склад глинистой руды${z >= 0.5 && typeof level === 'number' ? ` · ${formatNumber(level, 0)} т` : ''}` : null;
      captionFor(pile, X(centre.x), lowest, 3, 4.2, text, { sides: ['b', 't', 'c'] });
      if (feature(pile, 'queue_len') > 0) queueBadge(ctx, X(centre.x), Y(centre.y), feature(pile, 'queue_len'));
      hits.push({ id: pile.thingId, type: 'poly', points, order: 5 });
    }
  }

  // -- lower canvas: the pit --------------------------------------------------
  // The pit is drawn as a hard-rock pit is seen from above: every bench is a
  // flat floor (berm) ending in a steep face down to the next one. A face is a
  // narrow band shaded by the way it looks, lit from the north-west, with a
  // light crest line on top and a dark toe line at its foot. The floors and
  // faces come from pitgeom.js, which turns the outlines of the twins into
  // benches; floors get darker with depth. When a face is narrower than a
  // pixel or two at the present scale, it is drawn as its crest line only.

  const LIGHT = [-Math.SQRT1_2, Math.SQRT1_2];     // towards the light: north-west, in mine coordinates
  let pitModel = null;                              // buildPit(): loops, bands, floors
  const faceColors = new Map();

  // Colour of a face that looks `lit` (−1 away from the light … 1 into it), darker the deeper it is (0 … 1).
  function faceColor(lit, depth) {
    const key = `${Math.round(lit * 6)}|${Math.round(depth * 10)}`;
    let color = faceColors.get(key);
    if (!color) {
      const k = 0.5 + 0.5 * Math.round(lit * 6) / 6;
      const dim = 1 - Math.round(depth * 10) / 10 * 0.3;
      const mix = (a, b) => Math.round((a + (b - a) * k) * dim);
      color = `rgb(${mix(16, 178)}, ${mix(14, 162)}, ${mix(12, 140)})`;
      faceColors.set(key, color);
    }
    return color;
  }

  // Colour of a floor at a depth of 0 (rim) … 1 (pit floor), between the colours of C.levels.
  const levelRgb = C.levels.map((hex) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16)));
  function levelColor(depth) {
    const f = clamp(depth, 0, 1) * (levelRgb.length - 1);
    const i = Math.min(Math.floor(f), levelRgb.length - 2);
    const t = f - i;
    const rgb = levelRgb[i].map((value, k) => Math.round(value + (levelRgb[i + 1][k] - value) * t));
    return `rgb(${rgb[0]}, ${rgb[1]}, ${rgb[2]})`;
  }

  // Adds a closed outline to the current path without starting a new one.
  function tracePolygonOn(ctx, points) {
    for (let i = 0; i < points.length; i += 1) {
      if (i) ctx.lineTo(X(points[i][0]), Y(points[i][1]));
      else ctx.moveTo(X(points[i][0]), Y(points[i][1]));
    }
    ctx.closePath();
  }

  // Mean width in pixels of the band between two outlines of equal length (sampled).
  function bandPixels(outer, inner) {
    let sum = 0;
    let count = 0;
    const step = Math.max(1, Math.floor(outer.length / 24));
    for (let i = 0; i < outer.length; i += step) {
      sum += Math.hypot(inner[i][0] - outer[i][0], inner[i][1] - outer[i][1]);
      count += 1;
    }
    return (sum / Math.max(1, count)) * view.scale;
  }

  // One face between its crest (top) and its toe, as pieces shaded each by the way it looks.
  function drawFace(ctx, top, toe, depth) {
    if (bandPixels(top, toe) < 1.6) {
      tracePolygon(ctx, top);
      ctx.strokeStyle = C.crestLine;
      ctx.lineWidth = 1;
      ctx.stroke();
      return;
    }
    tracePolygon(ctx, top);
    tracePolygonOn(ctx, toe);
    ctx.fillStyle = faceColor(0, depth);
    ctx.fill('evenodd');
    const n = top.length;
    for (let i = 0; i < n; i += 1) {
      const j = (i + 1) % n;
      const dx = (toe[i][0] + toe[j][0] - top[i][0] - top[j][0]) / 2;
      const dy = (toe[i][1] + toe[j][1] - top[i][1] - top[j][1]) / 2;
      const lit = (dx * LIGHT[0] + dy * LIGHT[1]) / (Math.hypot(dx, dy) || 1);
      ctx.beginPath();
      ctx.moveTo(X(top[i][0]), Y(top[i][1]));
      ctx.lineTo(X(top[j][0]), Y(top[j][1]));
      ctx.lineTo(X(toe[j][0]), Y(toe[j][1]));
      ctx.lineTo(X(toe[i][0]), Y(toe[i][1]));
      ctx.closePath();
      ctx.fillStyle = faceColor(lit, depth);
      ctx.fill();
    }
    tracePolygon(ctx, top);
    ctx.strokeStyle = C.crestLine;
    ctx.lineWidth = 1;
    ctx.stroke();
    tracePolygon(ctx, toe);
    ctx.strokeStyle = C.toeLine;
    ctx.lineWidth = 1;
    ctx.stroke();
  }

  function drawPit(ctx, z) {
    const pit = first('pit');
    const pitPoints = pit?.attributes?.geo?.polygon;
    const benches = [...byKind('bench')].sort((a, b) => (b.attributes?.geo?.z ?? 0) - (a.attributes?.geo?.z ?? 0));
    pitModel = buildPit({ spacing: 40, minSamples: 72, maxSamples: 160 });
    if (pitPoints) {
      tracePolygon(ctx, pitPoints);
      ctx.fillStyle = C.slope;
      ctx.fill();
      hits.push({ id: pit.thingId, type: 'poly', points: pitPoints, order: 9 });
    }
    if (pitModel && pitModel.bands.length) {
      const { loops, bands } = pitModel;
      // Top down: a floor paints its whole outline, the faces and floors below paint over it.
      for (const band of bands) {
        if (band.type === 'flat') {
          tracePolygon(ctx, loops[band.outer].pts);
          ctx.fillStyle = levelColor(band.depth);
          ctx.fill();
        } else {
          drawFace(ctx, loops[band.outer].pts, loops[band.inner].pts, band.depth);
        }
      }
      tracePolygon(ctx, loops[loops.length - 1].pts);
      ctx.fillStyle = levelColor(1);
      ctx.fill();
      tracePolygon(ctx, loops[0].pts);
      ctx.strokeStyle = C.crest;
      ctx.lineWidth = 1.5;
      ctx.stroke();
    } else {
      // Outlines without a pit to hang them on: floors stepping down, darker with depth.
      benches.forEach((bench, i) => {
        const points = bench.attributes?.geo?.polygon;
        if (!points) return;
        tracePolygon(ctx, points);
        ctx.fillStyle = levelColor(benches.length > 1 ? i / (benches.length - 1) : 0);
        ctx.fill();
        ctx.strokeStyle = C.crest;
        ctx.lineWidth = 1;
        ctx.stroke();
      });
    }
    benches.forEach((bench, i) => {
      const points = bench.attributes?.geo?.polygon;
      if (points) hits.push({ id: bench.thingId, type: 'poly', points, order: 8 - i * 0.01 });
    });
    if (pitPoints) outlineMarks(ctx, pit.thingId, () => tracePolygon(ctx, pitPoints));
    // A bench where something works now is named first; the others as room allows.
    const working = new Set();
    for (const kind of ['loader', 'drill-rig']) {
      for (const unit of byKind(kind)) {
        const block = twins.get(feature(unit, 'block'));
        const bench = [].concat(block?.attributes?.links?.bench ?? [])[0];
        if (bench) working.add(bench);
      }
    }
    for (const bench of benches) {
      const points = bench.attributes?.geo?.polygon;
      if (!points) continue;
      outlineMarks(ctx, bench.thingId, () => tracePolygon(ctx, points));
      // The elevation is written on the floor, inside its northern edge.
      const north = points.reduce((best, p) => (p[1] > best[1] ? p : best), points[0]);
      const text = z >= 0.2 || (working.has(bench.thingId) && z >= 0.12) ? shortTitle('bench', titleOf(bench)) : null;
      captionFor(bench, X(north[0]), Y(north[1]), 2, working.has(bench.thingId) ? 6 : 6.6, text, { sides: ['b'], size: 10, color: C.inkSoft });
    }
    if (pitPoints) {
      const north = pitPoints.reduce((best, p) => (p[1] > best[1] ? p : best), pitPoints[0]);
      const rimZ = pit.attributes?.spec?.rim_elevation_m;
      const floorZ = pit.attributes?.spec?.floor_elevation_m;
      const depth = typeof rimZ === 'number' && typeof floorZ === 'number' && z >= 0.3
        ? ` · глубина ${formatNumber(rimZ - floorZ, 0)} м (${rimZ}…${floorZ} м)` : '';
      captionFor(pit, X(north[0]), Y(north[1]), 4, 4, `${titleOf(pit)}${depth}`, { sides: ['t', 'b'], size: 12 });
    }
  }

  let hatch = null;
  function hatchPattern(ctx) {
    if (!hatch) {
      const tile = document.createElement('canvas');
      tile.width = 6;
      tile.height = 6;
      const t = tile.getContext('2d');
      t.strokeStyle = 'rgba(160, 170, 180, 0.35)';
      t.lineWidth = 1;
      t.beginPath();
      t.moveTo(-1, 7); t.lineTo(7, -1);
      t.stroke();
      hatch = ctx.createPattern(tile, 'repeat');
    }
    return hatch;
  }

  function drawBlocks(ctx, z) {
    const style = LAYERS[layer];
    const fine = z >= 0.1;
    // Blocks of one bench together, clipped to the floor of that bench.
    const byBench = new Map();
    for (const block of byKind('block')) {
      const bench = [].concat(block.attributes?.links?.bench ?? [])[0] ?? '';
      if (!byBench.has(bench)) byBench.set(bench, []);
      byBench.get(bench).push(block);
    }
    for (const [bench, blocks] of byBench) {
      const floor = pitModel?.floors.get(bench);
      if (floor) {
        ctx.save();
        tracePolygon(ctx, floor.outer);
        if (floor.inner) tracePolygonOn(ctx, floor.inner);
        ctx.clip('evenodd');
      }
      for (const block of blocks) {
        const points = block.attributes?.geo?.polygon;
        if (!points) continue;
        const depleted = feature(block, 'status') === 'depleted';
        tracePolygon(ctx, points);
        ctx.fillStyle = depleted ? DEPLETED : style.color(block);
        ctx.fill();
        if (depleted) { ctx.fillStyle = hatchPattern(ctx); ctx.fill(); }
        if (fine) { ctx.strokeStyle = C.outline; ctx.lineWidth = 1; ctx.stroke(); }
        hits.push({ id: block.thingId, type: 'poly', points, order: 4 });
      }
      if (floor) ctx.restore();
    }
    for (const block of byKind('block')) {
      const points = block.attributes.geo?.polygon;
      if (!points) continue;
      const id = block.thingId;
      outlineMarks(ctx, id, () => tracePolygon(ctx, points));
      const centre = centroid(points);
      const [sideA, sideB] = rectangleSides(points);
      const widthPx = sideA * z;
      const heightPx = sideB * z;
      const roomy = Math.min(widthPx, heightPx) >= 17 && Math.max(widthPx, heightPx) >= 28;
      const marked = isSelected(id) || isRelated(id) || affected.has(id);
      if (marked) {
        captionFor(block, X(centre.x), Y(centre.y), Math.min(widthPx, heightPx) / 2, 9, null);
      } else if (roomy) {
        const value = feature(block, 'status') === 'depleted' && layer !== 'status' ? stateText('depleted') : style.caption(block);
        if (Math.min(widthPx, heightPx) >= 44) {
          caption(9, shortTitle('block', titleOf(block)), X(centre.x), Y(centre.y), { sides: ['c'], size: 10, color: C.ink, bold: true, more: value });
        } else {
          caption(9, value, X(centre.x), Y(centre.y), { sides: ['c'], size: 10, color: C.ink });
        }
      }
    }
  }

  // The sides of the rectangle with the area and the perimeter of an outline.
  function rectangleSides(points) {
    let area = 0;
    let length = 0;
    for (let i = 0; i < points.length; i += 1) {
      const [x0, y0] = points[i];
      const [x1, y1] = points[(i + 1) % points.length];
      area += x0 * y1 - x1 * y0;
      length += Math.hypot(x1 - x0, y1 - y0);
    }
    area = Math.abs(area) / 2;
    const half = length / 2;
    const root = Math.sqrt(Math.max(0, half * half - 4 * area));
    return [(half + root) / 2, (half - root) / 2];
  }

  function drawBlast(ctx) {
    const pit = first('pit');
    const block = twins.get(feature(pit, 'blast_block'));
    const radius = feature(pit, 'blast_radius_m');
    if (!block || !(radius > 0)) return;
    const centre = centroid(block.attributes.geo.polygon);
    const x = X(centre.x);
    const y = Y(centre.y);
    const r = radius * view.scale;
    circle(ctx, x, y, r);
    ctx.fillStyle = 'rgba(229, 72, 77, 0.13)';
    ctx.fill();
    ctx.strokeStyle = C.bad;
    ctx.lineWidth = 2;
    ctx.setLineDash([8, 5]);
    ctx.stroke();
    ctx.setLineDash([]);
    const state = stateText(feature(pit, 'blast_state'));
    caption(0.4, `Опасная зона взрыва · ${formatNumber(radius, 0)} м${state ? ` · ${state}` : ''}`, x, y - r,
      { gap: 5, sides: ['t', 'b'], pill: true, border: C.bad, force: true });
  }

  // -- lower canvas: roads ----------------------------------------------------

  function queueBadge(ctx, x, y, count) {
    const text = String(count);
    ctx.font = `600 10px ${FONT}`;
    const w = Math.max(15, ctx.measureText(text).width + 8);
    roundRect(ctx, x - w / 2, y - 7.5, w, 15, 7.5);
    ctx.fillStyle = C.warn;
    ctx.fill();
    ctx.strokeStyle = C.outline;
    ctx.lineWidth = 1.5;
    ctx.stroke();
    ctx.fillStyle = '#1b1300';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(text, x, y + 0.5);
    boxes.push([x - w / 2 - 1, y - 9, x + w / 2 + 1, y + 9]);
  }

  function drawRoads(ctx, z, size) {
    const roadWidth = clamp(2.6 * size, 2.6, 7);
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
    const segments = byKind('road-segment');

    // Wide soft bands under the roads: scenario first, selection over it.
    for (const segment of segments) {
      const path = segment.attributes?.geo?.path;
      if (!path) continue;
      const id = segment.thingId;
      if (affected.has(id)) {
        tracePath(ctx, path);
        ctx.strokeStyle = 'rgba(255, 106, 193, 0.38)';
        ctx.lineWidth = roadWidth + 11;
        ctx.stroke();
      }
      if (isSelected(id) || isRelated(id)) {
        tracePath(ctx, path);
        ctx.strokeStyle = isSelected(id) ? 'rgba(90, 209, 255, 0.6)' : 'rgba(90, 209, 255, 0.34)';
        ctx.lineWidth = roadWidth + (isSelected(id) ? 9 : 7);
        ctx.stroke();
      }
    }

    // Roads in the pit are cut into its wall: a bench of their own across the
    // faces, the cut face on the uphill side (away from the pit's middle) and
    // a safety windrow on the downhill side.
    const pitPoints = first('pit')?.attributes?.geo?.polygon;
    const middle = pitPoints ? centroid(pitPoints) : null;
    if (middle && z >= 0.12) {
      for (const segment of segments) {
        const path = segment.attributes?.geo?.path;
        const roadClass = segment.attributes?.spec?.road_class;
        if (!path || (roadClass !== 'ramp' && roadClass !== 'bench')) continue;
        tracePath(ctx, path);
        ctx.strokeStyle = C.roadCut;
        ctx.lineWidth = roadWidth + 7;
        ctx.stroke();
        for (let i = 1; i < path.length; i += 1) {
          const ax = X(path[i - 1][0]); const ay = Y(path[i - 1][1]);
          const bx = X(path[i][0]); const by = Y(path[i][1]);
          const length = Math.hypot(bx - ax, by - ay);
          if (length < 2) continue;
          let nx = -(by - ay) / length;
          let ny = (bx - ax) / length;
          // Screen y points down: the side away from the middle of the pit is uphill.
          const mx = (ax + bx) / 2 - X(middle.x);
          const my = (ay + by) / 2 - Y(middle.y);
          if (nx * mx + ny * my < 0) { nx = -nx; ny = -ny; }
          const out = roadWidth / 2 + 3.5;
          ctx.beginPath();
          ctx.moveTo(ax + nx * out, ay + ny * out); ctx.lineTo(bx + nx * out, by + ny * out);
          ctx.strokeStyle = C.toeLine;
          ctx.lineWidth = 2;
          ctx.stroke();
          ctx.beginPath();
          ctx.moveTo(ax - nx * out, ay - ny * out); ctx.lineTo(bx - nx * out, by - ny * out);
          ctx.strokeStyle = C.windrow;
          ctx.lineWidth = 1;
          ctx.setLineDash([2, 3]);
          ctx.stroke();
          ctx.setLineDash([]);
        }
      }
    }

    for (const segment of segments) {
      const path = segment.attributes?.geo?.path;
      if (!path) continue;
      const status = feature(segment, 'status');
      const wet = feature(segment, 'surface') === 'wet';
      tracePath(ctx, path);
      ctx.strokeStyle = wet ? C.roadWet : C.outline;
      ctx.lineWidth = roadWidth + (wet ? 2.6 : 2);
      ctx.stroke();
      tracePath(ctx, path);
      ctx.strokeStyle = status === 'closed' ? C.roadClosedBase : C.road;
      ctx.lineWidth = roadWidth;
      ctx.stroke();
      if (status === 'closed' || status === 'restricted') {
        // Not by colour alone: dashes, a sign at the middle and a caption.
        tracePath(ctx, path);
        ctx.strokeStyle = status === 'closed' ? C.bad : C.warn;
        ctx.lineWidth = Math.max(2, roadWidth - 1);
        ctx.lineCap = 'butt';
        ctx.setLineDash(status === 'closed' ? [7, 6] : [3, 7]);
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.lineCap = 'round';
      }
      hits.push({ id: segment.thingId, type: 'path', points: path, order: 3 });
    }

    for (const segment of segments) {
      const path = segment.attributes?.geo?.path;
      if (!path) continue;
      const status = feature(segment, 'status');
      const mid = midpoint(path);
      const x = X(mid.x);
      const y = Y(mid.y);
      let gap = roadWidth / 2 + 3;
      if (status === 'closed') {
        const r = 7.5;
        circle(ctx, x, y, r);
        ctx.fillStyle = '#1a0e10';
        ctx.fill();
        ctx.strokeStyle = C.bad;
        ctx.lineWidth = 2;
        ctx.stroke();
        ctx.beginPath();
        ctx.moveTo(x - 3.2, y - 3.2); ctx.lineTo(x + 3.2, y + 3.2);
        ctx.moveTo(x + 3.2, y - 3.2); ctx.lineTo(x - 3.2, y + 3.2);
        ctx.stroke();
        keepOut(x, y, r + 1);
        gap = r + 3;
        caption(2, 'дорога закрыта', x, y, { gap, color: C.ink, bold: true, sides: ['b', 't', 'r', 'l'] });
      } else if (status === 'restricted') {
        const r = 7;
        ctx.beginPath();
        ctx.moveTo(x, y - r); ctx.lineTo(x + r, y + r * 0.8); ctx.lineTo(x - r, y + r * 0.8);
        ctx.closePath();
        ctx.fillStyle = C.warn;
        ctx.fill();
        ctx.strokeStyle = C.outline;
        ctx.lineWidth = 1.5;
        ctx.stroke();
        ctx.fillStyle = '#1b1300';
        ctx.fillRect(x - 0.8, y - 3, 1.6, 4.5);
        ctx.fillRect(x - 0.8, y + 2.6, 1.6, 1.6);
        keepOut(x, y, r + 1);
        gap = r + 3;
        const limit = feature(segment, 'speed_limit_kmh');
        caption(2.5, typeof limit === 'number' ? `до ${formatNumber(limit, 0)} км/ч` : 'ограничение', x, y,
          { gap, color: C.ink, sides: ['b', 't', 'r', 'l'] });
      }
      const text = z >= 0.8 && status !== 'closed' ? titleOf(segment) : null;
      captionFor(segment, x, y, gap + (status === 'closed' ? 14 : 0), 7, text, { sides: ['t', 'b', 'r', 'l'], size: 10, color: C.inkMuted });
    }

    for (const node of byKind('road-node')) {
      const geo = node.attributes?.geo;
      if (typeof geo?.x !== 'number') continue;
      const id = node.thingId;
      const marked = isSelected(id) || isRelated(id) || affected.has(id);
      const waiting = feature(node, 'trucks_waiting');
      const x = X(geo.x);
      const y = Y(geo.y);
      if (z >= 0.3 || marked) {
        const r = clamp(roadWidth * 0.55, 2, 4);
        circle(ctx, x, y, r);
        ctx.fillStyle = C.ground;
        ctx.fill();
        ctx.strokeStyle = '#c3ccd4';
        ctx.lineWidth = 1.5;
        ctx.stroke();
        rings(ctx, id, x, y, r + 1);
        hits.push({ id, type: 'point', x, y, r: 9, order: 2 });
      }
      const text = z >= 1.1 ? titleOf(node) : null;
      captionFor(node, x, y, 7, 8, text, { size: 10, color: C.inkMuted });
      if (waiting > 0 && z >= 0.5) queueBadge(ctx, x + 12, y - 11, waiting);
    }
  }

  // -- lower canvas: plant and equipment --------------------------------------

  const geoPoint = (twin) => (typeof twin?.attributes?.geo?.x === 'number' ? twin.attributes.geo : null);

  // The plant as a process: each unit of the chain where its twin stands
  // (plant.js), the links between them as pipes with the flow in t/h on
  // them, the state of every unit and its figures beside it.

  function drawPlant(ctx, z, size) {
    flows = [];
    millScreens = [];
    const substation = first('substation');
    const pipeWidth = clamp(2.2 * size, 2, 6);
    const detailed = z >= 0.24;         // each unit has its caption
    const valued = z >= 0.45;           // and there is room for its figures and the flows
    const big = z >= 0.6 ? 12 : 11;     // the plant view proper: larger type
    ctx.lineCap = 'round';
    const layout = plantLayout();

    const line = (twin, points, active, speed, load) => {
      const id = twin?.thingId;
      if (id && (isSelected(id) || isRelated(id))) {
        tracePath(ctx, points);
        ctx.strokeStyle = isSelected(id) ? 'rgba(90, 209, 255, 0.6)' : 'rgba(90, 209, 255, 0.34)';
        ctx.lineWidth = pipeWidth + 8;
        ctx.stroke();
      }
      if (id && affected.has(id)) {
        tracePath(ctx, points);
        ctx.strokeStyle = 'rgba(255, 106, 193, 0.38)';
        ctx.lineWidth = pipeWidth + 10;
        ctx.stroke();
      }
      tracePath(ctx, points);
      ctx.strokeStyle = C.outline;
      ctx.lineWidth = pipeWidth + 2;
      ctx.stroke();
      tracePath(ctx, points);
      ctx.strokeStyle = C.pipe;
      ctx.lineWidth = pipeWidth;
      ctx.stroke();
      if (typeof load === 'number' && load > 0) {
        // The ore on the belt: a stripe as wide as the belt is loaded.
        tracePath(ctx, points);
        ctx.strokeStyle = C.ore;
        ctx.lineWidth = Math.max(1, pipeWidth * clamp(load, 0.12, 1));
        ctx.stroke();
      }
      const screen = points.map(([x, y]) => [X(x), Y(y)]);
      flows.push({ points: screen, active, speed, width: Math.max(1.5, pipeWidth - 1.5) });
      keepOffLine(screen, pipeWidth / 2 + 1.5, id ?? '');
    };
    const flowPill = (tph, x, y, priority) => {
      if (!valued || typeof tph !== 'number') return;
      caption(priority, `${formatNumber(tph, tph >= 100 ? 0 : 1)} т/ч`, x, y,
        { sides: ['c', 't', 'b'], gap: 2, pill: true, border: tph > 0 ? C.pipe : C.warn, color: tph > 0 ? C.ink : C.warn, size: big - 1 });
    };

    // Pipes first, the units over them.
    const conveyorNode = layout.nodes.find((node) => node.kind === 'conveyor');
    for (const edge of layout.edges) {
      if (edge.from === conveyorNode || edge.to === conveyorNode) continue;
      const a = layout.pos.get(edge.from.key);
      const b = layout.pos.get(edge.to.key);
      if (!a || !b) continue;
      const running = (twin) => !['stopped', 'down'].includes(feature(twin, 'state'));
      line(null, [[a.x, a.y], [b.x, b.y]], edge.tph > 0 && running(edge.from.twin), 1);
      flowPill(edge.tph, (X(a.x) + X(b.x)) / 2, (Y(a.y) + Y(b.y)) / 2, 5.2);
    }
    const conveyor = conveyorNode?.twin;
    if (conveyor?.attributes?.geo?.path) {
      const path = conveyor.attributes.geo.path;
      const load = rate(conveyor, 'load_tph');
      const rated = first('crusher')?.attributes?.spec?.rated_tph || 4100;
      line(conveyor, path, feature(conveyor, 'load_tph') > 0 && feature(conveyor, 'state') !== 'stopped', 1.6,
        typeof load === 'number' ? load / rated : undefined);
      hits.push({ id: conveyor.thingId, type: 'path', points: path, order: 3 });
      const mid = midpoint(path);
      const text = z >= 0.7 ? `Конвейер${typeof load === 'number' ? ` · ${formatNumber(load, 0)} т/ч` : ''}` : null;
      const loadT = feature(conveyor, 'load_t');
      const more = z >= 0.7 && typeof loadT === 'number' ? `на ленте ${formatNumber(loadT, 0)} т · загрузка ${formatNumber(100 * clamp(load / rated, 0, 1), 0)} %` : '';
      captionFor(conveyor, X(mid.x), Y(mid.y), pipeWidth / 2 + 4, 5.5, text, { sides: ['t', 'b'], more, size: big - 1, owner: conveyor.thingId, far: true });
      if (z < 0.7) flowPill(load, X(mid.x), Y(mid.y), 5.1);
    }

    // The grinding circuit, when its mills are twins of their own: a frame
    // around them carrying the circuit's figures (feed, setpoint).
    const circuit = first('mill');
    const unitPlaces = [].concat(circuit?.attributes?.links?.units ?? []).map((id) => layout.pos.get(id)).filter(Boolean);
    if (circuit && unitPlaces.length > 1 && !layout.pos.has(circuit.thingId)) {
      const pad = Math.max(16, 34 * z) / z;
      const minX = Math.min(...unitPlaces.map((p) => p.x)) - pad;
      const maxX = Math.max(...unitPlaces.map((p) => p.x)) + pad;
      const minY = Math.min(...unitPlaces.map((p) => p.y)) - pad;
      const maxY = Math.max(...unitPlaces.map((p) => p.y)) + pad;
      const frame = [[minX, minY], [maxX, minY], [maxX, maxY], [minX, maxY]];
      roundRect(ctx, X(minX), Y(maxY), (maxX - minX) * z, (maxY - minY) * z, 8);
      ctx.fillStyle = 'rgba(32, 39, 45, 0.6)';
      ctx.fill();
      ctx.strokeStyle = stateColor(feature(circuit, 'state'));
      ctx.lineWidth = 1.5;
      ctx.setLineDash([5, 4]);
      ctx.stroke();
      ctx.setLineDash([]);
      outlineMarks(ctx, circuit.thingId, () => roundRect(ctx, X(minX) - 3, Y(maxY) - 3, (maxX - minX) * z + 6, (maxY - minY) * z + 6, 10));
      hits.push({ id: circuit.thingId, type: 'poly', points: frame, order: 6 });
      // Captions of the mills stay inside the frame or outside it, never across its edge.
      keepOffLine([...frame, frame[0]].map(([fx, fy]) => [X(fx), Y(fy)]), 1.5, circuit.thingId);
      plantAt.set(circuit.thingId, { x: X((minX + maxX) / 2), y: Y((minY + maxY) / 2), r: ((maxY - minY) * z) / 2 });
      const feed = rate(circuit, 'feed_tph');
      const setpoint = feature(circuit, 'feed_setpoint_tph');
      const circuitState = feature(circuit, 'state');
      const text = detailed ? `Измельчение${circuitState && circuitState !== 'running' ? ` · ${stateText(circuitState)}`
        : valued && typeof feed === 'number' ? ` · ${formatNumber(feed, 0)} т/ч` : ''}` : null;
      const more = valued && typeof setpoint === 'number' ? `уставка ${formatNumber(setpoint, 0)} т/ч` : '';
      captionFor(circuit, X((minX + maxX) / 2), Y(maxY), 3, 4.55, text, { sides: ['t', 'tl', 'tr'], color: C.ink, more, size: big, far: true });
    }

    for (const node of layout.nodes) {
      const at = layout.pos.get(node.key);
      if (!at || node.kind === 'conveyor') continue;
      const x = X(at.x);
      const y = Y(at.y);
      const twin = node.twin;
      const state = feature(twin, 'state');
      const stateNote = state && !['running', 'idle'].includes(state) ? ` · ${stateText(state)}` : '';
      // The state in words under the name, unless the name already carries it.
      const stateLead = state && !stateNote ? `${stateText(state)} · ` : '';
      switch (node.kind) {
        case 'crusher': {
          const s = Math.max(7.5 * size, 26 * z);
          const fill = clamp((feature(twin, 'hopper_t') ?? 0) / (twin.attributes.spec?.hopper_capacity_t || 300), 0, 1);
          // A hopper seen from the side: wide at the top, ore level inside.
          const hopper = () => {
            ctx.beginPath();
            ctx.moveTo(x - s * 1.25, y - s); ctx.lineTo(x + s * 1.25, y - s);
            ctx.lineTo(x + s * 0.6, y + s); ctx.lineTo(x - s * 0.6, y + s);
            ctx.closePath();
          };
          hopper();
          ctx.fillStyle = C.body;
          ctx.fill();
          ctx.save();
          hopper();
          ctx.clip();
          ctx.fillStyle = C.ore;
          ctx.fillRect(x - s * 1.3, y + s - 2 * s * fill, s * 2.6, 2 * s * fill);
          ctx.restore();
          hopper();
          ctx.strokeStyle = stateColor(state);
          ctx.lineWidth = 2;
          ctx.stroke();
          rings(ctx, twin.thingId, x, y, s * 1.25);
          keepOut(x, y, s * 1.3);
          plantAt.set(twin.thingId, { x, y, r: s * 1.3 });
          hits.push({ id: twin.thingId, type: 'point', x, y, r: s * 1.4 + 3, order: 1 });
          const queue = feature(twin, 'queue_len');
          if (queue > 0) queueBadge(ctx, x - s * 1.25 - 6, y - s - 6, queue);
          const flow = rate(twin, 'throughput_tph');
          let text = 'Дробилка';
          if (stateNote) text += stateNote;
          else if (valued && typeof flow === 'number') text += ` · ${formatNumber(flow, 0)} т/ч`;
          const hopperT = feature(twin, 'hopper_t');
          const more = valued && typeof hopperT === 'number'
            ? `${stateLead}бункер ${formatNumber(hopperT, 0)} из ${formatNumber(twin.attributes.spec?.hopper_capacity_t ?? 300, 0)} т${queue > 0 ? ` · очередь ${queue}` : ''}` : '';
          captionFor(twin, x, y, s * 1.3 + 4, 4.4, text, { sides: ['b', 't', 'l', 'r', 'bl', 'br', 'tl', 'tr'], color: C.ink, more, size: big, far: true });
          break;
        }
        case 'stockpile': {
          const r = Math.max(7.5 * size, (twin.attributes.spec?.radius_m ?? 55) * z);
          const level = clamp((feature(twin, 'level_pct') ?? 0) / 100, 0, 1);
          circle(ctx, x, y, r);
          ctx.fillStyle = C.body;
          ctx.fill();
          // The pile seen from above: its footprint grows with the tonnage.
          circle(ctx, x, y, Math.max(0, r - 1.5) * Math.sqrt(level));
          ctx.fillStyle = C.ore;
          ctx.fill();
          circle(ctx, x, y, r);
          ctx.strokeStyle = level < 0.15 ? C.bad : level < 0.3 ? C.warn : C.bodyEdge;
          ctx.lineWidth = 2;
          ctx.stroke();
          rings(ctx, twin.thingId, x, y, r);
          keepOut(x, y, r + 1);
          plantAt.set(twin.thingId, { x, y, r: r + 1 });
          hits.push({ id: twin.thingId, type: 'point', x, y, r: r + 4, order: 1 });
          const pct = feature(twin, 'level_pct');
          const text = detailed ? `Склад руды${valued && typeof pct === 'number' ? ` · ${formatNumber(pct, 0)} %` : ''}` : null;
          const tonnes = feature(twin, 'level_t');
          const hours = feature(twin, 'hours_of_feed');
          const more = valued && typeof tonnes === 'number'
            ? `${formatNumber(tonnes, 0)} т${typeof hours === 'number' ? ` · ${formatNumber(hours, 1)} ч подачи` : ''}` : '';
          captionFor(twin, x, y, r + 5, 4.5, text, { sides: ['t', 'b', 'tl', 'bl', 'l', 'r'], color: C.ink, more, size: big, far: true });
          break;
        }
        case 'mill': case 'sag-mill': case 'ball-mill': {
          // A mill seen from its end: a drum that turns while it is fed.
          const scale = node.kind === 'ball-mill' ? 15 : node.kind === 'sag-mill' ? 22 : 22;
          const r = Math.max((node.kind === 'ball-mill' ? 5.5 : 7) * size, scale * z);
          circle(ctx, x, y, r);
          ctx.fillStyle = C.body;
          ctx.fill();
          ctx.strokeStyle = stateColor(state);
          ctx.lineWidth = 2.5;
          ctx.stroke();
          rings(ctx, twin.thingId, x, y, r);
          keepOut(x, y, r + 1);
          plantAt.set(twin.thingId, { x, y, r: r + 1 });
          hits.push({ id: twin.thingId, type: 'point', x, y, r: r + 4, order: 1 });
          const feed = rate(twin, typeof feature(twin, 'feed_tph') === 'number' ? 'feed_tph' : 'throughput_tph');
          const name = node.kind === 'sag-mill' ? 'МПСИ' : node.kind === 'ball-mill' ? 'Шаровые' : 'Мельница';
          const power = feature(twin, 'power_mw');
          const energy = feature(twin, 'specific_energy_kwh_t');
          const inFrame = node.kind !== 'mill' && [].concat(first('mill')?.attributes?.links?.units ?? []).includes(twin.thingId);
          let text = null;
          let more = '';
          if (inFrame) {
            // The circuit's frame carries the feed; a mill of it says its power and how loaded its drive is.
            const drive = feature(twin, 'drive_load_pct');
            const charge = feature(twin, 'charge_pct');
            if (detailed) text = `${name}${stateNote || (valued && typeof power === 'number' ? ` · ${formatNumber(power, 1)} МВт` : '')}`;
            if (z >= 0.9 && typeof drive === 'number') {
              more = `привод ${formatNumber(drive, 0)} %${typeof charge === 'number' ? ` · заполнение ${formatNumber(charge, 0)} %` : ''}`;
            }
          } else {
            if (detailed) {
              text = name;
              if (stateNote) text += stateNote;
              else if (valued && typeof feed === 'number') text += ` · ${formatNumber(feed, 0)} т/ч`;
            }
            more = valued && typeof power === 'number'
              ? `${stateLead}${formatNumber(power, 1)} МВт${typeof energy === 'number' && z >= 0.9 ? ` · ${formatNumber(energy, 1)} кВт·ч/т` : ''}` : '';
          }
          captionFor(twin, x, y, r + 5, 4.6, text, { sides: ['b', 't', 'br', 'tl', 'bl', 'tr', 'r', 'l'], color: C.ink, more, size: big, far: true });
          millScreens.push({ x, y, r, running: (state === 'running' || state === undefined) && (feature(twin, 'feed_tph') ?? feature(twin, 'throughput_tph')) > 0 });
          break;
        }
        case 'flotation': {
          const cell = Math.max(5.2 * size, 20 * z);
          // A bank of three cells.
          for (let i = -1; i <= 1; i += 1) {
            ctx.fillStyle = C.body;
            ctx.strokeStyle = stateColor(state);
            ctx.lineWidth = 2;
            ctx.beginPath();
            ctx.rect(x + i * (cell + 1.5) - cell / 2, y - cell / 2, cell, cell);
            ctx.fill();
            ctx.stroke();
          }
          const r = cell * 1.7;
          rings(ctx, twin.thingId, x, y, r);
          keepOut(x, y, r);
          plantAt.set(twin.thingId, { x, y, r });
          hits.push({ id: twin.thingId, type: 'point', x, y, r: r + 3, order: 1 });
          const recovery = feature(twin, 'recovery_pct');
          let text = null;
          if (detailed) {
            text = 'Флотация';
            if (stateNote) text += stateNote;
            else if (valued && typeof recovery === 'number') text += ` · извлечение ${formatNumber(recovery, 1)} %`;
          }
          const copper = rate(twin, 'cu_tph');
          const grade = feature(twin, 'feed_grade_pct');
          const more = valued && typeof copper === 'number'
            ? `${stateLead}медь ${formatNumber(copper, 1)} т/ч${typeof grade === 'number' && z >= 0.9 ? ` · питание ${formatNumber(grade, 2)} % Cu` : ''}` : '';
          captionFor(twin, x, y, r + 4, 4.7, text, { sides: ['b', 't', 'r', 'br', 'tr', 'l', 'bl', 'tl'], color: C.ink, more, size: big, far: true });
          break;
        }
        case 'concentrate': case 'tailings': {
          const concentrate = node.kind === 'concentrate';
          const r = Math.max((concentrate ? 5.5 : 7) * size, (concentrate ? 15 : 24) * z);
          const polygon = node.virtual ? null : twin.attributes?.geo?.polygon;
          if (polygon) {
            tracePolygon(ctx, polygon);
            ctx.fillStyle = C.tails;
            ctx.fill();
            ctx.strokeStyle = C.tailsEdge;
            ctx.lineWidth = 1.5;
            ctx.setLineDash([6, 4]);
            ctx.stroke();
            ctx.setLineDash([]);
            hits.push({ id: twin.thingId, type: 'poly', points: polygon, order: 6 });
            // The discharge into the pond.
            circle(ctx, x, y, Math.max(3, r * 0.35));
          }
          else if (concentrate) circle(ctx, x, y, r);
          else { ctx.beginPath(); ctx.ellipse(x, y, r * 1.25, r * 0.8, -0.3, 0, TAU); }
          ctx.fillStyle = concentrate ? C.conc : C.tails;
          ctx.fill();
          ctx.strokeStyle = concentrate ? C.concEdge : C.tailsEdge;
          ctx.lineWidth = 1.5;
          ctx.stroke();
          if (concentrate) {
            // A thickener: the inner ring of its overflow launder.
            circle(ctx, x, y, r * 0.55);
            ctx.stroke();
          }
          if (!node.virtual) {
            rings(ctx, twin.thingId, x, y, r);
            plantAt.set(twin.thingId, { x, y, r: r + 1 });
          }
          keepOut(x, y, r + 1);
          hits.push({ id: twin.thingId, type: 'point', x, y, r: r + 3, order: 1 });
          const into = layout.edges.find((edge) => edge.to === node)?.tph;
          const { grade } = productFigures(node);
          const title = concentrate ? 'Концентрат' : 'Хвосты';
          const text = detailed ? `${title}${valued && typeof into === 'number' ? ` · ${formatNumber(into, into >= 100 ? 0 : 1)} т/ч` : ''}` : null;
          const more = valued && typeof grade === 'number' ? `${formatNumber(grade, grade >= 1 ? 1 : 3)} % Cu` : '';
          if (node.virtual) {
            if (text) caption(4.8, text, x, y, { gap: r + 5, sides: ['r', 'b', 't', 'br', 'tr', 'l', 'bl', 'tl'], color: C.inkSoft, more, size: big, far: true });
          } else {
            captionFor(twin, x, y, r + 5, 4.8, text, { sides: ['r', 'b', 't', 'br', 'tr', 'l', 'bl', 'tl'], color: C.ink, more, size: big, far: true });
          }
          break;
        }
        default: {
          // A unit of a kind this page does not draw yet: a plain box with its name.
          const s = Math.max(6 * size, 14 * z);
          roundRect(ctx, x - s, y - s, 2 * s, 2 * s, 3);
          ctx.fillStyle = C.body;
          ctx.fill();
          ctx.strokeStyle = stateColor(state);
          ctx.lineWidth = 2;
          ctx.stroke();
          rings(ctx, twin.thingId, x, y, s);
          keepOut(x, y, s + 1);
          plantAt.set(twin.thingId, { x, y, r: s + 1 });
          hits.push({ id: twin.thingId, type: 'point', x, y, r: s + 3, order: 1 });
          captionFor(twin, x, y, s + 5, 4.9, detailed ? `${titleOf(twin)}${stateNote}` : null, { color: C.ink, size: big, far: true });
        }
      }
    }

    if (!detailed) {
      // The name stands over the concentrator: not over the crusher, the conveyor or the tailings pond.
      const points = layout.nodes.filter((node) => !['crusher', 'conveyor', 'tailings'].includes(node.kind))
        .map((node) => layout.pos.get(node.key)).filter(Boolean);
      // The substation beside the plant is under the name too.
      const power = geoPoint(substation);
      if (power && points.some((p) => Math.hypot(p.x - power.x, p.y - power.y) < 800)) points.push(power);
      if (points.length) {
        const minX = Math.min(...points.map((p) => p.x));
        const maxX = Math.max(...points.map((p) => p.x));
        const maxY = Math.max(...points.map((p) => p.y));
        caption(4.3, 'Обогатительная фабрика', X((minX + maxX) / 2), Y(maxY), { gap: 8 * size, sides: ['t', 'tl', 'l', 'bl', 'b'], size: 12 });
      }
    }

    const service = (twin, text, pictogram) => {
      const geo = geoPoint(twin);
      if (!geo) return;
      const x = X(geo.x);
      const y = Y(geo.y);
      const s = Math.max(6 * size, 16 * z);
      roundRect(ctx, x - s, y - s, 2 * s, 2 * s, 2.5);
      ctx.fillStyle = C.body;
      ctx.fill();
      ctx.strokeStyle = C.bodyEdge;
      ctx.lineWidth = 1.5;
      ctx.stroke();
      ctx.strokeStyle = C.ink;
      ctx.fillStyle = C.ink;
      ctx.lineWidth = Math.max(1.4, s * 0.22);
      pictogram(x, y, s * 0.55);
      rings(ctx, twin.thingId, x, y, s * 1.2);
      keepOut(x, y, s + 2);
      hits.push({ id: twin.thingId, type: 'point', x, y, r: s + 5, order: 1 });
      captionFor(twin, x, y, s + 3, 5, text, { sides: ['b', 't', 'r', 'l'] });
      return { x, y, s };
    };

    if (substation) {
      const load = feature(substation, 'load_mw');
      service(substation, z >= 0.24 ? `Подстанция${typeof load === 'number' && z >= 0.4 ? ` · ${formatNumber(load, 1)} МВт` : ''}` : null, (x, y, k) => {
        // lightning bolt
        ctx.beginPath();
        ctx.moveTo(x + k * 0.35, y - k); ctx.lineTo(x - k * 0.5, y + k * 0.15); ctx.lineTo(x + k * 0.1, y + k * 0.15);
        ctx.lineTo(x - k * 0.35, y + k); ctx.lineTo(x + k * 0.55, y - k * 0.2); ctx.lineTo(x - k * 0.05, y - k * 0.2);
        ctx.closePath();
        ctx.fill();
      });
    }
    const fuel = first('fuel-station');
    if (fuel) {
      const at = service(fuel, 'Заправка', (x, y, k) => {
        // drop
        ctx.beginPath();
        ctx.moveTo(x, y - k);
        ctx.bezierCurveTo(x + k * 1.1, y + k * 0.1, x + k * 0.75, y + k, x, y + k);
        ctx.bezierCurveTo(x - k * 0.75, y + k, x - k * 1.1, y + k * 0.1, x, y - k);
        ctx.fill();
      });
      const queue = (feature(fuel, 'queue_len') ?? 0) + (feature(fuel, 'trucks_fuelling') ?? 0);
      if (at && queue > 0) queueBadge(ctx, at.x + at.s + 6, at.y - at.s - 5, queue);
    }
    const workshop = first('workshop');
    if (workshop) {
      const at = service(workshop, 'Мастерская', (x, y, k) => {
        // wrench: a handle and an open jaw
        ctx.lineCap = 'round';
        ctx.beginPath();
        ctx.moveTo(x - k * 0.8, y + k * 0.8); ctx.lineTo(x + k * 0.25, y - k * 0.25);
        ctx.stroke();
        ctx.beginPath();
        ctx.arc(x + k * 0.5, y - k * 0.5, k * 0.5, Math.PI * 0.15, Math.PI * 1.35);
        ctx.stroke();
      });
      const busy = (feature(workshop, 'bays_busy') ?? 0) + (feature(workshop, 'jobs_queued') ?? 0);
      if (at && busy > 0) queueBadge(ctx, at.x + at.s + 6, at.y - at.s - 5, busy);
    }
    // The clay-ore plant ОФ-2: its building, the desliming cyclones and the flotation beside it.
    const clay = first('clay-plant');
    if (clay) {
      const state = feature(clay, 'state');
      const note = state && state !== 'running' ? ` · ${stateText(state)}` : '';
      const at = service(clay, z >= 0.2 ? `ОФ-2${note}` : null, (x, y, k) => {
        // a plant: a saw-tooth roof over a wall
        ctx.beginPath();
        ctx.moveTo(x - k, y + k); ctx.lineTo(x - k, y - k * 0.2); ctx.lineTo(x - k * 0.3, y - k * 0.8); ctx.lineTo(x - k * 0.3, y - k * 0.2);
        ctx.lineTo(x + k * 0.3, y - k * 0.8); ctx.lineTo(x + k * 0.3, y - k * 0.2); ctx.lineTo(x + k, y - k * 0.8); ctx.lineTo(x + k, y + k);
        ctx.closePath();
        ctx.fill();
      });
      if (at) {
        ctx.beginPath();
        ctx.arc(at.x + at.s * 0.75, at.y - at.s * 0.75, Math.max(2.5, at.s * 0.28), 0, Math.PI * 2);
        ctx.fillStyle = stateColor(state);
        ctx.fill();
        if (feature(clay, 'queue_len') > 0) queueBadge(ctx, at.x - at.s - 6, at.y - at.s - 5, feature(clay, 'queue_len'));
      }
      for (const [kind, label] of [['desliming', 'Обесшламливание'], ['clay-flotation', 'Флотация ОФ-2']]) {
        const unit = first(kind);
        if (!unit) continue;
        service(unit, z >= 0.45 ? label : null, (x, y, k) => {
          if (kind === 'desliming') {
            // a cyclone: a cone under a short cylinder
            ctx.beginPath();
            ctx.moveTo(x - k * 0.7, y - k); ctx.lineTo(x + k * 0.7, y - k); ctx.lineTo(x + k * 0.7, y - k * 0.4); ctx.lineTo(x, y + k);
            ctx.lineTo(x - k * 0.7, y - k * 0.4);
            ctx.closePath();
            ctx.fill();
          } else {
            // flotation cells: three bubbles
            for (const [dx, dy] of [[-0.45, 0.35], [0.45, 0.35], [0, -0.4]]) {
              ctx.beginPath();
              ctx.arc(x + dx * k, y + dy * k, k * 0.42, 0, Math.PI * 2);
              ctx.fill();
            }
          }
        });
      }
    }
  }

  // Loaders and drill rigs stand where they report; the loader is drawn a
  // little towards its block so that the truck under its bucket stays visible.
  function equipmentScreen(twin, size) {
    const x = feature(twin, 'pos_x') ?? twin.attributes?.geo?.x;
    const y = feature(twin, 'pos_y') ?? twin.attributes?.geo?.y;
    if (typeof x !== 'number' || typeof y !== 'number') return null;
    let px = X(x);
    let py = Y(y);
    let angle = null;
    const block = twins.get(feature(twin, 'block'));
    if (kindOf(twin) === 'loader' && block?.attributes?.geo?.polygon) {
      const centre = centroid(block.attributes.geo.polygon);
      const dx = X(centre.x) - px;
      const dy = Y(centre.y) - py;
      const length = Math.hypot(dx, dy);
      if (length > 0.5) {
        angle = Math.atan2(dy, dx);
        const shift = Math.max(0, 9 * size - length * 0.5);
        px += Math.cos(angle) * shift;
        py += Math.sin(angle) * shift;
      }
    }
    return { x: px, y: py, angle };
  }

  function drawEquipment(ctx, z, size) {
    for (const loader of byKind('loader')) {
      const at = equipmentScreen(loader, size);
      if (!at) continue;
      const r = 5.6 * size;
      const state = feature(loader, 'state');
      // Boom and bucket reach away from the block, over the truck.
      const angle = at.angle === null ? -Math.PI / 2 : at.angle + Math.PI;
      const bx = at.x + Math.cos(angle) * r * 1.75;
      const by = at.y + Math.sin(angle) * r * 1.75;
      ctx.lineCap = 'round';
      ctx.strokeStyle = C.outline;
      ctx.lineWidth = 4.5;
      ctx.beginPath(); ctx.moveTo(at.x, at.y); ctx.lineTo(bx, by); ctx.stroke();
      ctx.strokeStyle = C.ink;
      ctx.lineWidth = 2.2;
      ctx.beginPath(); ctx.moveTo(at.x, at.y); ctx.lineTo(bx, by); ctx.stroke();
      circle(ctx, at.x, at.y, r);
      ctx.fillStyle = stateColor(state);
      ctx.fill();
      ctx.strokeStyle = C.outline;
      ctx.lineWidth = 1.5;
      ctx.stroke();
      circle(ctx, at.x, at.y, r * 0.38);
      ctx.fillStyle = C.outline;
      ctx.fill();
      if (state === 'down') cross(ctx, at.x, at.y, r * 0.55);
      rings(ctx, loader.thingId, at.x, at.y, r);
      keepOut(at.x, at.y, r + 2);
      hits.push({ id: loader.thingId, type: 'point', x: at.x, y: at.y, r: r + 5, order: 0.5 });
      const queue = feature(loader, 'queue_len');
      if (queue > 0) queueBadge(ctx, at.x + r + 5, at.y - r - 6, queue);
      const title = titleOf(loader);
      const text = z >= 0.55 ? `${title} · ${stateText(state)}` : shortTitle('loader', title);
      const rate = feature(loader, 'dig_rate_tph');
      const more = z >= 1 && typeof rate === 'number' ? `${formatNumber(rate, 0)} т/ч · состояние ${formatNumber(feature(loader, 'health_pct') ?? 0, 0)} %` : '';
      captionFor(loader, at.x, at.y, r + 4, 3, text, { color: C.ink, more });
    }
    for (const rig of byKind('drill-rig')) {
      const at = equipmentScreen(rig, size);
      if (!at) continue;
      const s = 5.4 * size;
      const state = feature(rig, 'state');
      // A mast on a base: a triangle with a dot.
      ctx.beginPath();
      ctx.moveTo(at.x, at.y - s * 1.15); ctx.lineTo(at.x + s, at.y + s * 0.8); ctx.lineTo(at.x - s, at.y + s * 0.8);
      ctx.closePath();
      ctx.fillStyle = stateColor(state);
      ctx.fill();
      ctx.strokeStyle = C.outline;
      ctx.lineWidth = 1.5;
      ctx.stroke();
      circle(ctx, at.x, at.y + s * 0.15, s * 0.28);
      ctx.fillStyle = C.outline;
      ctx.fill();
      if (state === 'down') cross(ctx, at.x, at.y, s * 0.5);
      rings(ctx, rig.thingId, at.x, at.y, s * 1.1);
      keepOut(at.x, at.y, s + 2);
      hits.push({ id: rig.thingId, type: 'point', x: at.x, y: at.y, r: s + 5, order: 0.6 });
      const title = titleOf(rig);
      const text = z >= 0.55 ? `${title} · ${stateText(state)}` : shortTitle('drill-rig', title);
      const holes = feature(rig, 'holes_done');
      const more = z >= 1 && typeof holes === 'number' ? `скважин ${formatNumber(holes, 0)} из ${formatNumber(feature(rig, 'holes_planned') ?? 0, 0)}` : '';
      captionFor(rig, at.x, at.y, s + 4, 5, text, { more });
    }
  }

  // The end state of an open forecast, quieter than the live captions: a
  // dashed pill that is left out when there is no free room for it.
  function drawNotes() {
    for (const note of notes) {
      const at = plantAt.get(note.id);
      if (!at) continue;
      caption(3.9, note.text, at.x, at.y, { gap: at.r + 4, pill: true, dashed: true, border: C.inkMuted, color: C.inkSoft, size: 10,
        sides: ['tr', 'br', 'tl', 'bl', 't', 'b', 'r', 'l'] });
    }
  }

  function cross(ctx, x, y, k) {
    ctx.strokeStyle = '#ffffff';
    ctx.lineWidth = Math.max(1.6, k * 0.45);
    ctx.lineCap = 'round';
    ctx.beginPath();
    ctx.moveTo(x - k, y - k); ctx.lineTo(x + k, y + k);
    ctx.moveTo(x + k, y - k); ctx.lineTo(x - k, y + k);
    ctx.stroke();
  }

  let rainTile = null;
  function drawWeather(ctx) {
    const weather = first('weather');
    if (feature(weather, 'condition') !== 'rain') return;
    if (!rainTile) {
      const tile = document.createElement('canvas');
      tile.width = 28;
      tile.height = 28;
      const t = tile.getContext('2d');
      t.strokeStyle = 'rgba(120, 180, 235, 0.22)';
      t.lineWidth = 1;
      t.beginPath();
      t.moveTo(8, 2); t.lineTo(5, 11);
      t.moveTo(22, 15); t.lineTo(19, 24);
      t.stroke();
      rainTile = ctx.createPattern(tile, 'repeat');
    }
    ctx.fillStyle = rainTile;
    ctx.fillRect(0, 0, width, height);
  }

  function drawStatic() {
    const ctx = sctx;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const z = view.scale;
    const size = glyphSize();
    related = selectedId ? relatedIds(selectedId) : new Set();
    affected = new Map();
    for (const scenario of activeScenarios()) {
      for (const id of scenario.affected) if (!affected.has(id)) affected.set(id, scenario);
    }
    hits = [];
    requests = [];
    boxes = [];
    glyphs = [];
    segments = [];
    placed = [];
    plantAt.clear();
    drawGround(ctx);
    drawAreas(ctx, z);
    drawPit(ctx, z);
    drawBlocks(ctx, z);
    drawRoads(ctx, z, size);
    drawBlast(ctx);
    drawPlant(ctx, z, size);
    drawEquipment(ctx, z, size);
    drawNotes();
    drawWeather(ctx);
    cctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    cctx.clearRect(0, 0, width, height);
    placeCaptions(cctx);
    staticBoxes = boxes;
    hits.sort((a, b) => a.order - b.order);
    if (onView) onView({ scale: view.scale, home: home.scale });
  }

  // -- upper canvas: what moves -----------------------------------------------

  const sprites = new Map();       // caption text -> small canvas, drawn once
  function sprite(text, strong) {
    const key = `${strong ? '!' : ''}${text}`;
    let item = sprites.get(key);
    if (!item) {
      const font = `600 11px ${FONT}`;
      const w = Math.ceil(textWidth(lctx, font, text)) + (strong ? 12 : 6);
      const h = strong ? 19 : 15;
      const canvas = document.createElement('canvas');
      canvas.width = Math.ceil(w * dpr);
      canvas.height = Math.ceil(h * dpr);
      const ctx = canvas.getContext('2d');
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      paintCaption(ctx, { text, pill: strong, border: C.select, color: C.ink }, strong ? 0.75 : 3, strong ? 0.75 : 0,
        strong ? w - 1.5 : w - 6, strong ? h - 1.5 : h, font);
      item = { canvas, w, h };
      sprites.set(key, item);
    }
    return item;
  }

  function touch(x, y, w, h) {
    liveRects.push([Math.floor(x) - 2, Math.floor(y) - 2, Math.ceil(w) + 4, Math.ceil(h) + 4]);
  }

  function drawTruck(ctx, x, y, heading, length, color, loaded, down) {
    const w = length * 0.58;
    ctx.save();
    ctx.translate(x, y);
    ctx.rotate(heading);
    roundRect(ctx, -w / 2 - 1.5, -length / 2 - 1.5, w + 3, length + 3, 3);
    ctx.fillStyle = C.outline;
    ctx.fill();
    // cab at the front
    roundRect(ctx, -w * 0.36, -length / 2, w * 0.72, length * 0.26, 1.2);
    ctx.fillStyle = color;
    ctx.fill();
    // dump body: filled when it carries a load, an outline when empty
    const top = -length / 2 + length * 0.31;
    const bodyLength = length / 2 - top;
    if (loaded) {
      roundRect(ctx, -w / 2, top, w, bodyLength, 1.8);
      ctx.fill();
    } else {
      const line = Math.max(1.6, length * 0.13);
      roundRect(ctx, -w / 2 + line / 2, top + line / 2, w - line, bodyLength - line, 1.2);
      ctx.strokeStyle = color;
      ctx.lineWidth = line;
      ctx.stroke();
    }
    ctx.restore();
    if (down) cross(ctx, x, y, length * 0.24);
  }

  function drawFlows(ctx, now) {
    // The moving marks pass under the flow figures written on the pipes.
    const pills = placed.filter((item) => item.pill);
    ctx.save();
    if (pills.length) {
      ctx.beginPath();
      ctx.rect(0, 0, width, height);
      for (const { rect } of pills) ctx.rect(rect[0] - 1, rect[1] - 1, rect[2] - rect[0] + 2, rect[3] - rect[1] + 2);
      ctx.clip('evenodd');
    }
    for (const flow of flows) {
      if (!flow.active) continue;
      const { points } = flow;
      let minX = Infinity; let minY = Infinity; let maxX = -Infinity; let maxY = -Infinity;
      ctx.beginPath();
      points.forEach(([x, y], i) => {
        if (i) ctx.lineTo(x, y); else ctx.moveTo(x, y);
        minX = Math.min(minX, x); maxX = Math.max(maxX, x); minY = Math.min(minY, y); maxY = Math.max(maxY, y);
      });
      if (maxX < 0 || minX > width || maxY < 0 || minY > height) continue;
      ctx.strokeStyle = C.flow;
      ctx.lineWidth = flow.width;
      ctx.lineCap = 'butt';
      ctx.setLineDash([4, 9]);
      ctx.lineDashOffset = -((now / 45) * flow.speed) % 13;
      ctx.stroke();
      ctx.setLineDash([]);
      touch(Math.max(0, minX) - 3, Math.max(0, minY) - 3, Math.min(width, maxX) - Math.max(0, minX) + 6, Math.min(height, maxY) - Math.max(0, minY) + 6);
    }
    ctx.restore();
    for (const { x, y, r, running } of millScreens) {
      if (!running) continue;
      const angle = now / 500;
      ctx.strokeStyle = C.good;
      ctx.lineWidth = Math.max(1.5, r * 0.22);
      ctx.lineCap = 'round';
      ctx.beginPath();
      for (let i = 0; i < 3; i += 1) {
        const a = angle + (i * TAU) / 3;
        ctx.moveTo(x, y);
        ctx.lineTo(x + Math.cos(a) * (r - 3), y + Math.sin(a) * (r - 3));
      }
      ctx.stroke();
      touch(x - r, y - r, 2 * r, 2 * r);
    }
  }

  function drawLive(now) {
    const ctx = lctx;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    for (const [x, y, w, h] of liveRects) ctx.clearRect(x, y, w, h);
    liveRects = [];
    if (!view) return;
    drawFlows(ctx, now);

    const size = glyphSize();
    const length = 12.5 * size;
    const trucks = byKind('truck');
    const standing = [];
    const drawn = [];
    for (const truck of trucks) {
      const at = motion.shown(truck.thingId, now);
      if (!at) continue;
      let place = truckAt.get(truck.thingId);
      if (!place) { place = { ox: 0, oy: 0 }; truckAt.set(truck.thingId, place); }
      const x = X(at.x);
      const y = Y(at.y);
      // Trucks standing on top of each other at a small scale (a queue) are
      // spread back along their direction so that each one can be seen.
      let ox = 0;
      let oy = 0;
      if (!at.moving) {
        const bx = -Math.sin(at.heading);
        const by = Math.cos(at.heading);
        for (let step = 0; step < 7; step += 1) {
          const tx = x + bx * step * length * 0.95;
          const ty = y + by * step * length * 0.95;
          if (standing.every((p) => Math.hypot(p[0] - tx, p[1] - ty) >= length * 0.85)) { ox = tx - x; oy = ty - y; break; }
        }
        standing.push([x + ox, y + oy]);
      }
      place.ox += (ox - place.ox) * 0.2;
      place.oy += (oy - place.oy) * 0.2;
      if (Math.abs(ox - place.ox) < 0.3) place.ox = ox;
      if (Math.abs(oy - place.oy) < 0.3) place.oy = oy;
      place.x = x + place.ox;
      place.y = y + place.oy;
      place.r = length * 0.7;
      place.heading = at.heading;
      if (place.x < -30 || place.x > width + 30 || place.y < -30 || place.y > height + 30) continue;
      drawn.push([truck, place]);
    }

    const taken = [];
    for (const [truck, place] of drawn) {
      const id = truck.thingId;
      const state = feature(truck, 'state');
      const { x, y } = place;
      let reach = length * 0.72;
      drawTruck(ctx, x, y, place.heading, length, truckColor(state), feature(truck, 'payload_t') > 1, state === 'down');
      if (isSelected(id) || isRelated(id)) {
        ctx.strokeStyle = C.select;
        ctx.lineWidth = isSelected(id) ? 2.5 : 1.5;
        ctx.setLineDash(isSelected(id) ? [] : [3, 3]);
        circle(ctx, x, y, reach + 3);
        ctx.stroke();
        reach += 5;
      }
      if (affected.has(id)) {
        ctx.strokeStyle = C.scenario;
        ctx.lineWidth = 2;
        ctx.setLineDash([5, 3]);
        circle(ctx, x, y, reach + 3);
        ctx.stroke();
        reach += 5;
      }
      ctx.setLineDash([]);
      touch(x - reach - 3, y - reach - 3, 2 * reach + 6, 2 * reach + 6);
    }
    // Captions after all glyphs, so that no truck is drawn over a caption. At
    // a small scale with a large fleet only the trucks that matter now are
    // named: selected, linked to the selection, touched by a scenario, broken down.
    const fewNames = view.scale < 0.2 && drawn.length > 16;
    for (const [truck, place] of drawn) {
      const id = truck.thingId;
      const strong = isSelected(id);
      if (fewNames && !strong && !isRelated(id) && !affected.has(id) && feature(truck, 'state') !== 'down') continue;
      const title = titleOf(truck);
      let text = shortTitle('truck', title);
      if (strong) text = title;
      else if (view.scale >= 0.8 && feature(truck, 'payload_t') > 1) text += ` · ${formatNumber(feature(truck, 'payload_t'), 0)} т`;
      const item = sprite(text, strong);
      const gap = place.r + (strong || isRelated(id) || affected.has(id) ? 9 : 2);
      const options = [[place.x + gap, place.y - item.h / 2 - gap * 0.6], [place.x - gap - item.w, place.y - item.h / 2 - gap * 0.6],
        [place.x + gap, place.y - item.h / 2 + gap * 0.6], [place.x - item.w / 2, place.y + gap]];
      for (let i = 0; i < options.length; i += 1) {
        const [lx, ly] = options[i];
        const rect = [lx, ly, lx + item.w, ly + item.h];
        // A name is kept whole on the plan: a place that would cut it at the edge is not taken.
        const clear = rect[0] >= 2 && rect[2] <= width - 2 && rect[1] >= 2 && rect[3] <= height - 2
          && isFree(rect, staticBoxes) && isFree(rect, taken) && isFree(rect, overlayRects)
          && drawn.every(([, other]) => other === place || !(other.x + other.r > rect[0] && other.x - other.r < rect[2] && other.y + other.r > rect[1] && other.y - other.r < rect[3]));
        if (clear || (strong && i === options.length - 1)) {
          const fx = clear ? lx : options[0][0];
          const fy = clear ? ly : options[0][1];
          ctx.drawImage(item.canvas, Math.round(fx), Math.round(fy), item.w, item.h);
          taken.push([fx, fy, fx + item.w, fy + item.h]);
          touch(fx, fy, item.w, item.h);
          break;
        }
      }
    }
  }

  // -- frames -----------------------------------------------------------------

  const stats = { frames: 0, cost: 0, worst: 0, interval: 0, longest: 0, statics: 0, staticCost: 0, staticWorst: 0, last: 0, since: performance.now() };

  let active = true;               // false while the 3D view is shown instead: nothing is drawn
  let rafId = 0;

  function frame(now) {
    if (!active) { rafId = 0; return; }
    const started = performance.now();
    if (view && width) {
      stepTween(now);
      let didStatic = false;
      if (staticDirty || (staticDue && now >= staticDue)) {
        drawStatic();
        staticDirty = false;
        staticDue = 0;
        liveDirty = true;
        didStatic = true;
      }
      if (liveDirty || motion.anyMoving() || flows.some((flow) => flow.active) || liveSettling()) {
        drawLive(now);
        liveDirty = false;
      }
      const cost = performance.now() - started;
      stats.frames += 1;
      stats.cost += cost;
      stats.worst = Math.max(stats.worst, cost);
      if (didStatic) {
        stats.statics += 1;
        stats.staticCost += cost;
        stats.staticWorst = Math.max(stats.staticWorst, cost);
      }
      if (stats.last) {
        stats.interval += now - stats.last;
        stats.longest = Math.max(stats.longest, now - stats.last);
      }
      stats.last = now;
    }
    rafId = requestAnimationFrame(frame);
  }

  function liveSettling() {
    for (const place of truckAt.values()) {
      if (place.x !== undefined && (Math.abs(place.ox) > 0.3 || Math.abs(place.oy) > 0.3)) return true;
    }
    return false;
  }

  // -- picking ----------------------------------------------------------------

  function distanceToPath(px, py, points) {
    let best = Infinity;
    for (let i = 1; i < points.length; i += 1) {
      const ax = X(points[i - 1][0]);
      const ay = Y(points[i - 1][1]);
      const bx = X(points[i][0]);
      const by = Y(points[i][1]);
      const lengthSq = (bx - ax) ** 2 + (by - ay) ** 2 || 1;
      const f = clamp(((px - ax) * (bx - ax) + (py - ay) * (by - ay)) / lengthSq, 0, 1);
      best = Math.min(best, Math.hypot(px - (ax + (bx - ax) * f), py - (ay + (by - ay) * f)));
    }
    return best;
  }

  function insidePolygon(px, py, points) {
    let hit = false;
    for (let i = 0, j = points.length - 1; i < points.length; j = i, i += 1) {
      const xi = X(points[i][0]);
      const yi = Y(points[i][1]);
      const xj = X(points[j][0]);
      const yj = Y(points[j][1]);
      if ((yi > py) !== (yj > py) && px < ((xj - xi) * (py - yi)) / (yj - yi) + xi) hit = !hit;
    }
    return hit;
  }

  // The object under the pointer: small things win over the areas beneath them.
  function pick(px, py) {
    if (!view) return null;
    let nearest = null;
    for (const [id, place] of truckAt) {
      if (place.x === undefined) continue;
      const d = Math.hypot(px - place.x, py - place.y);
      if (d <= place.r + 5 && (!nearest || d < nearest.d)) nearest = { id, d };
    }
    if (nearest) return twins.get(nearest.id) ?? null;
    for (const hit of hits) {
      if (hit.type === 'point' && Math.hypot(px - hit.x, py - hit.y) <= hit.r) return twins.get(hit.id) ?? null;
      if (hit.type === 'path' && distanceToPath(px, py, hit.points) <= 7) return twins.get(hit.id) ?? null;
      if (hit.type === 'poly' && insidePolygon(px, py, hit.points)) return twins.get(hit.id) ?? null;
    }
    return null;
  }

  const pointer = { down: false, moved: false, x: 0, y: 0, id: null };

  function local(event) {
    const box = liveCanvas.getBoundingClientRect();
    return [event.clientX - box.left, event.clientY - box.top];
  }

  liveCanvas.addEventListener('pointerdown', (event) => {
    if (event.button !== 0) return;
    pointer.down = true;
    pointer.moved = false;
    pointer.x = event.clientX;
    pointer.y = event.clientY;
    pointer.id = event.pointerId;
  });
  liveCanvas.addEventListener('pointermove', (event) => {
    if (pointer.down && view) {
      const dx = event.clientX - pointer.x;
      const dy = event.clientY - pointer.y;
      if (!pointer.moved && Math.hypot(dx, dy) < 4) return;
      if (!pointer.moved) {
        pointer.moved = true;
        liveCanvas.setPointerCapture(pointer.id);
        liveCanvas.classList.add('dragging');
        if (onHover) onHover(null);
      }
      pointer.x = event.clientX;
      pointer.y = event.clientY;
      const base = tween ? tween.to : view;
      setView({ scale: base.scale, cx: base.cx - dx / base.scale, cy: base.cy + dy / base.scale }, false);
      return;
    }
    if (!onHover) return;
    const [px, py] = local(event);
    const twin = pick(px, py);
    liveCanvas.style.cursor = twin ? 'pointer' : '';
    onHover(twin, event.clientX, event.clientY);
  });
  const release = (event) => {
    if (!pointer.down) return;
    pointer.down = false;
    liveCanvas.classList.remove('dragging');
    if (pointer.moved || event.type !== 'pointerup') return;
    const [px, py] = local(event);
    const twin = pick(px, py);
    if (twin) onSelect(twin.thingId);
  };
  liveCanvas.addEventListener('pointerup', release);
  liveCanvas.addEventListener('pointercancel', release);
  liveCanvas.addEventListener('pointerleave', () => { if (onHover) onHover(null); });
  liveCanvas.addEventListener('wheel', (event) => {
    event.preventDefault();
    const [px, py] = local(event);
    zoomAt(Math.exp(-clamp(event.deltaY, -240, 240) * 0.0022), px, py, false);
  }, { passive: false });
  liveCanvas.addEventListener('dblclick', (event) => {
    const [px, py] = local(event);
    zoomAt(1.8, px, py);
  });
  liveCanvas.addEventListener('keydown', (event) => {
    if (!view) return;
    const step = 70 / view.scale;
    const base = tween ? tween.to : view;
    const moves = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, step], ArrowDown: [0, -step] };
    if (moves[event.key]) {
      setView({ scale: base.scale, cx: base.cx + moves[event.key][0], cy: base.cy + moves[event.key][1] }, false);
    } else if (event.key === '+' || event.key === '=') zoomAt(1.5);
    else if (event.key === '-' || event.key === '_') zoomAt(1 / 1.5);
    else if (event.key === '0' || event.key === 'Home') api.fitAll();
    else return;
    event.preventDefault();
  });

  new ResizeObserver(resize).observe(staticCanvas.parentElement);
  rafId = requestAnimationFrame(frame);

  const api = {
    // The twins were loaded (again): take the geometry anew.
    reload() {
      motion.rebuild();
      truckAt.clear();
      refreshDestinationLegend(byKind('block'), twins);
      const before = worldBox;
      worldBox = boxOf([...twins.values()].filter((twin) => kindOf(twin) !== 'mine' && kindOf(twin) !== 'truck'));
      // Plant units placed after the unit that feeds them are part of the mine too.
      if (worldBox) {
        for (const p of plantLayout().pos.values()) {
          worldBox.minX = Math.min(worldBox.minX, p.x - 40); worldBox.maxX = Math.max(worldBox.maxX, p.x + 40);
          worldBox.minY = Math.min(worldBox.minY, p.y - 40); worldBox.maxY = Math.max(worldBox.maxY, p.y + 40);
        }
      }
      // Keep a view the visitor chose, unless the mine itself changed (a new model, another namespace).
      const moved = before && worldBox && ['minX', 'maxX', 'minY', 'maxY'].some((key) => Math.abs(before[key] - worldBox[key]) > 200);
      const keep = view && !atHome && !moved;
      width = 0;                       // force resize() to take the sizes again
      resize();
      if (!keep && home) { view = { ...home }; atHome = true; }
      staticDirty = true;
    },
    // One twin changed.
    patch(twin) {
      if (kindOf(twin) === 'truck') {
        motion.report(twin);
        if (twin.thingId !== selectedId) return;
      }
      if (!staticDue) staticDue = performance.now() + STATIC_DELAY_MS;
    },
    select(thingId) {
      selectedId = thingId;
      staticDirty = true;
    },
    // Draw (true) or rest while another view of the mine is shown (false).
    setActive(on) {
      active = on;
      captionCanvas.style.visibility = on ? '' : 'hidden';
      if (on) {
        width = 0;
        resize();
        staticDirty = true;
        liveRects = [[0, 0, width, height]];
        stats.last = 0;
        if (!rafId) rafId = requestAnimationFrame(frame);
      }
    },
    get layer() { return layer; },
    setLayer(name) {
      if (LAYERS[name]) { layer = name; staticDirty = true; }
    },
    setOverlays(rects) {
      overlayRects = rects;
      staticDirty = true;
    },
    // The parts of the plan the floating panels cover: views are fitted into the rest.
    setInsets(next) {
      const value = { top: 14, right: 14, bottom: 14, left: 14, ...next };
      if (['top', 'right', 'bottom', 'left'].every((key) => Math.abs(value[key] - INSET[key]) < 2)) return;
      INSET = value;
      if (worldBox && width) {
        home = viewFor(worldBox);
        if (atHome) { view = { ...home }; tween = null; }
      }
      staticDirty = true;
    },
    // Short texts to write next to objects of the plant, or [] to clear them.
    setNotes(list) {
      notes = list ?? [];
      staticDirty = true;
    },
    get notes() { return notes; },
    // What the last frame of the plan wrote and where, for the page check:
    // captions, the squares of the glyphs and the lines captions keep off.
    captions() {
      return { width, height, scale: view ? view.scale : 0, captions: placed.map((item) => ({ ...item })),
        glyphs: glyphs.map((rect) => [...rect]), lines: segments.map((segment) => segment.slice(0, 5)), overlays: overlayRects.map((rect) => [...rect]) };
    },
    zoomBy(factor) { zoomAt(factor); },
    fitAll() {
      if (!home) return;
      setView({ ...home });
      atHome = true;
    },
    // Show a group of twins: 'pit' or 'plant'.
    fitGroup(name) {
      let box = null;
      if (name === 'plant') {
        // The units of the process chain, including those placed after the
        // unit that feeds them; the crusher only when it stands near the
        // plant (with a long conveyor it belongs to the pit's side).
        const { nodes, pos } = plantLayout();
        const pile = pos.get(first('stockpile')?.thingId);
        for (const node of nodes) {
          const p = pos.get(node.key);
          if (!p || node.kind === 'tailings' || node.kind === 'conveyor') continue;
          if (node.kind === 'crusher' && pile && Math.hypot(p.x - pile.x, p.y - pile.y) > 1200) continue;
          if (!box) box = { minX: p.x, maxX: p.x, minY: p.y, maxY: p.y };
          box.minX = Math.min(box.minX, p.x - 30); box.maxX = Math.max(box.maxX, p.x + 30);
          box.minY = Math.min(box.minY, p.y - 30); box.maxY = Math.max(box.maxY, p.y + 30);
        }
        // The clay-ore plant ОФ-2 beside ОФ-1: «Фабрика» shows both plants.
        for (const kind of ['clay-plant', 'desliming', 'clay-flotation']) {
          const geo = first(kind)?.attributes?.geo;
          if (!box || typeof geo?.x !== 'number') continue;
          box.minX = Math.min(box.minX, geo.x - 40); box.maxX = Math.max(box.maxX, geo.x + 40);
          box.minY = Math.min(box.minY, geo.y - 40); box.maxY = Math.max(box.maxY, geo.y + 40);
        }
      } else {
        box = boxOf(byKind('pit'));
      }
      if (box) setView(viewFor(box, name === 'plant' ? 40 : 30));
    },
    // Bring a twin into view if it is outside; `closer` also zooms in on it.
    reveal(thingId, closer = false) {
      const twin = twins.get(thingId);
      if (!twin || !view) return false;
      const at = anchorOf(twin);
      if (!at) return false;
      const base = tween ? tween.to : view;
      if (closer) {
        const geo = twin.attributes?.geo ?? {};
        const box = geo.polygon || geo.path ? boxOf([twin]) : null;
        const next = box ? viewFor(box, 130, 1.6) : { scale: Math.max(base.scale, 0.9), cx: at.x, cy: at.y };
        if (!box) {
          // Keep the object in the middle of the free part of the plan.
          next.cy += (INSET.top - INSET.bottom) / 2 / next.scale;
        }
        setView(next);
        return true;
      }
      const px = width / 2 + (at.x - base.cx) * base.scale;
      const py = height / 2 - (at.y - base.cy) * base.scale;
      if (px < 40 || px > width - 40 || py < INSET.top + 20 || py > height - 30) {
        setView({ scale: base.scale, cx: at.x, cy: at.y });
      }
      return true;
    },
    hasPlace(thingId) {
      const twin = twins.get(thingId);
      return Boolean(twin && anchorOf(twin));
    },
    // Frame cost since the last call: milliseconds of drawing per frame and the time between frames.
    frameStats() {
      const out = {
        seconds: (performance.now() - stats.since) / 1000,
        frames: stats.frames,
        avgCostMs: stats.frames ? stats.cost / stats.frames : 0,
        worstCostMs: stats.worst,
        avgIntervalMs: stats.frames > 1 ? stats.interval / (stats.frames - 1) : 0,
        longestIntervalMs: stats.longest,
        staticRedraws: stats.statics,
        avgStaticFrameMs: stats.statics ? stats.staticCost / stats.statics : 0,
        worstStaticFrameMs: stats.staticWorst,
        trucksMoving: byKind('truck').filter((truck) => motion.shown(truck.thingId, performance.now())?.moving).length,
        canvas: `${width}x${height}@${dpr}`,
        scale: view ? view.scale : 0,
      };
      Object.assign(stats, { frames: 0, cost: 0, worst: 0, interval: 0, longest: 0, statics: 0, staticCost: 0, staticWorst: 0, last: 0, since: performance.now() });
      return out;
    },
  };
  return api;
}
