// Drawing board: renders the safe area, job previews, live traces and the
// arm cursor. Converts between screen pixels and Line-us machine coordinates.
//
// "Paper space" matches the backend convention: x 0..1800 left→right,
// y 0..900 top→bottom, top edge nearest the robot body.
// machine X = 800 + paperY (distance from body), machine Y = 900 - paperX.

export const PAPER_W = 1800;
export const PAPER_H = 900;
const X_MIN = 800;
const Y_MAX = 900;

export function paperToMachine(px, py) {
  return [X_MIN + py, Y_MAX - px];
}

export function machineToPaper(x, y) {
  return [Y_MAX - y, x - X_MIN];
}

export class Board {
  constructor(canvas, wrap, coordsEl) {
    this.canvas = canvas;
    this.wrap = wrap;
    this.coordsEl = coordsEl;
    this.ctx = canvas.getContext('2d');
    this.scale = 1;

    this.job = null;            // preview payload from the server
    this.segmentsDone = 0;
    this.position = null;       // {x, y, z} machine coords
    this.liveMode = false;
    this.liveStrokes = [];      // array of arrays of [machineX, machineY]
    this.onLivePoint = null;    // (machineX, machineY, pen) => void
    this._pointerDown = false;

    new ResizeObserver(() => this._resize()).observe(wrap);
    this._resize();
    this._bindPointer();
  }

  _resize() {
    const pad = 16;
    const w = this.wrap.clientWidth - pad;
    const h = this.wrap.clientHeight - pad;
    if (w <= 0 || h <= 0) return;
    this.scale = Math.min(w / PAPER_W, h / PAPER_H);
    const dpr = window.devicePixelRatio || 1;
    this.canvas.width = Math.round(PAPER_W * this.scale * dpr);
    this.canvas.height = Math.round(PAPER_H * this.scale * dpr);
    this.canvas.style.width = `${PAPER_W * this.scale}px`;
    this.canvas.style.height = `${PAPER_H * this.scale}px`;
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.render();
  }

  // ---- coordinate helpers ----

  _machineToPx(x, y) {
    const [px, py] = machineToPaper(x, y);
    return [px * this.scale, py * this.scale];
  }

  _eventToMachine(ev) {
    const rect = this.canvas.getBoundingClientRect();
    const px = (ev.clientX - rect.left) / this.scale;
    const py = (ev.clientY - rect.top) / this.scale;
    const cx = Math.min(Math.max(px, 0), PAPER_W);
    const cy = Math.min(Math.max(py, 0), PAPER_H);
    return paperToMachine(cx, cy);
  }

  // ---- pointer input (live mode) ----

  _bindPointer() {
    const c = this.canvas;
    c.addEventListener('pointerdown', (ev) => {
      const [x, y] = this._eventToMachine(ev);
      this._showCoords(x, y);
      if (!this.liveMode) return;
      c.setPointerCapture(ev.pointerId);
      this._pointerDown = true;
      this.liveStrokes.push([[x, y]]);
      this.onLivePoint?.(x, y, true);
      this.render();
    });
    c.addEventListener('pointermove', (ev) => {
      const [x, y] = this._eventToMachine(ev);
      this._showCoords(x, y);
      if (!this.liveMode || !this._pointerDown) return;
      this.liveStrokes[this.liveStrokes.length - 1].push([x, y]);
      this.onLivePoint?.(x, y, true);
      this.render();
    });
    const release = (ev) => {
      if (!this._pointerDown) return;
      this._pointerDown = false;
      const [x, y] = this._eventToMachine(ev);
      this.onLivePoint?.(x, y, false);
    };
    c.addEventListener('pointerup', release);
    c.addEventListener('pointercancel', release);
    c.addEventListener('pointerleave', (ev) => {
      this.coordsEl.textContent = '';
      release(ev);
    });
  }

  _showCoords(x, y) {
    this.coordsEl.textContent = `X ${x.toFixed(0)}  Y ${y.toFixed(0)}`;
  }

  setLiveMode(on) {
    this.liveMode = on;
    this.canvas.classList.toggle('live', on);
    if (!on) this._pointerDown = false;
    this.render();
  }

  clearLiveStrokes() {
    this.liveStrokes = [];
    this.render();
  }

  setJob(job) {
    this.job = job;
    this.segmentsDone = 0;
    this.render();
  }

  setProgress(done) {
    this.segmentsDone = done;
    this.render();
  }

  setPosition(pos) {
    this.position = pos;
    this.render();
  }

  // ---- rendering ----

  render() {
    const { ctx, scale } = this;
    const W = PAPER_W * scale;
    const H = PAPER_H * scale;
    ctx.clearRect(0, 0, W, H);

    // paper
    ctx.fillStyle = '#10151d';
    ctx.fillRect(0, 0, W, H);

    // grid every 100 machine units
    ctx.strokeStyle = 'rgba(255,255,255,0.045)';
    ctx.lineWidth = 1;
    ctx.beginPath();
    for (let gx = 0; gx <= PAPER_W; gx += 100) {
      ctx.moveTo(gx * scale, 0);
      ctx.lineTo(gx * scale, H);
    }
    for (let gy = 0; gy <= PAPER_H; gy += 100) {
      ctx.moveTo(0, gy * scale);
      ctx.lineTo(W, gy * scale);
    }
    ctx.stroke();

    // safe-area border
    ctx.strokeStyle = 'rgba(79,156,255,0.35)';
    ctx.lineWidth = 1.5;
    ctx.strokeRect(0.75, 0.75, W - 1.5, H - 1.5);

    // robot body indicator (top centre, machine Y=0)
    ctx.fillStyle = 'rgba(139,150,168,0.35)';
    ctx.beginPath();
    ctx.arc(W / 2, 0, 22 * scale, 0, Math.PI);
    ctx.fill();
    ctx.fillStyle = 'rgba(139,150,168,0.7)';
    ctx.font = `${Math.max(10, 11 * scale)}px ui-sans-serif, system-ui`;
    ctx.textAlign = 'center';
    ctx.fillText('robô', W / 2, 14 * scale + 10);

    if (this.job) this._renderJob();
    this._renderLiveStrokes();
    if (this.position) this._renderCursor();
  }

  _renderJob() {
    const { ctx } = this;
    let remaining = this.segmentsDone;
    ctx.lineWidth = 1.4;
    ctx.lineJoin = 'round';
    ctx.lineCap = 'round';
    for (const line of this.job.polylines) {
      if (line.length < 2) continue;
      // split each path into a drawn part and a pending part
      const segs = line.length - 1;
      const drawnSegs = Math.max(0, Math.min(segs, remaining));
      remaining -= drawnSegs;
      if (drawnSegs > 0) {
        this._strokePolyline(line.slice(0, drawnSegs + 1), '#4f9cff', 1.8);
      }
      if (drawnSegs < segs) {
        this._strokePolyline(line.slice(drawnSegs), 'rgba(230,235,242,0.38)', 1.2);
      }
    }
  }

  _renderLiveStrokes() {
    for (const stroke of this.liveStrokes) {
      if (stroke.length === 1) {
        const [cx, cy] = this._machineToPx(stroke[0][0], stroke[0][1]);
        this.ctx.fillStyle = '#3ddc84';
        this.ctx.beginPath();
        this.ctx.arc(cx, cy, 1.5, 0, Math.PI * 2);
        this.ctx.fill();
      } else {
        this._strokePolyline(stroke, '#3ddc84', 1.6);
      }
    }
  }

  _strokePolyline(points, color, width) {
    const { ctx } = this;
    ctx.strokeStyle = color;
    ctx.lineWidth = width;
    ctx.beginPath();
    points.forEach(([x, y], i) => {
      const [cx, cy] = this._machineToPx(x, y);
      if (i === 0) ctx.moveTo(cx, cy);
      else ctx.lineTo(cx, cy);
    });
    ctx.stroke();
  }

  _renderCursor() {
    const { ctx } = this;
    const { x, y, z } = this.position;
    const [cx, cy] = this._machineToPx(x, y);
    const penDown = z < 500;
    ctx.strokeStyle = penDown ? '#ff5d5d' : '#8b96a8';
    ctx.fillStyle = penDown ? 'rgba(255,93,93,0.85)' : 'rgba(139,150,168,0.25)';
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.arc(cx, cy, 5, 0, Math.PI * 2);
    ctx.fill();
    ctx.stroke();
    // crosshair
    ctx.strokeStyle = 'rgba(255,255,255,0.25)';
    ctx.beginPath();
    ctx.moveTo(cx - 11, cy); ctx.lineTo(cx - 6, cy);
    ctx.moveTo(cx + 6, cy); ctx.lineTo(cx + 11, cy);
    ctx.moveTo(cx, cy - 11); ctx.lineTo(cx, cy - 6);
    ctx.moveTo(cx, cy + 6); ctx.lineTo(cx, cy + 11);
    ctx.stroke();
  }
}
