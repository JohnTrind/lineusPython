// App bootstrap: shared state, websocket wiring, tabs, connection and job bar.

import { WSClient } from './ws.js';
import { Board } from './canvas.js';
import { initLivePanel } from './panels/live.js';
import { initGcodePanel } from './panels/gcode.js';
import { initImagePanel } from './panels/image.js';
import { initRobotPanel } from './panels/robot.js';

const $ = (id) => document.getElementById(id);

const board = new Board($('board'), $('board-wrap'), $('board-coords'));

const app = {
  board,
  ws: null,
  status: null,        // last status payload from the server
  job: null,           // current preview payload (polylines etc.)
  liveActive: false,

  log(message, level = 'info') {
    const el = document.createElement('div');
    el.textContent = `${new Date().toLocaleTimeString()} ${message}`;
    if (level === 'error') el.classList.add('err');
    if (level === 'ok') el.classList.add('ok');
    const log = $('log');
    log.appendChild(el);
    while (log.childElementCount > 200) log.firstChild.remove();
    log.scrollTop = log.scrollHeight;
  },

  async api(path, { body, formData } = {}) {
    const opts = { method: 'POST' };
    if (formData) opts.body = formData;
    else if (body !== undefined) {
      opts.body = JSON.stringify(body);
      opts.headers = { 'Content-Type': 'application/json' };
    }
    const res = await fetch(path, opts);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || res.statusText);
    return data;
  },

  setJob(payload, { fit = false } = {}) {
    this.job = payload;
    board.setJob(payload);
    $('job-bar').hidden = false;
    $('job-name').textContent = payload.name;
    $('fit-check').checked = fit;
    $('progress-fill').style.width = '0%';
    $('job-progress-text').textContent =
      `${payload.paths} traços · ${payload.segments} segmentos`;
    updateJobButtons({ state: 'idle' });
  },
};

// ---- tabs ----

for (const tab of document.querySelectorAll('.tab')) {
  tab.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach((t) => t.classList.remove('active'));
    document.querySelectorAll('.panel').forEach((p) => p.classList.remove('active'));
    tab.classList.add('active');
    $(`panel-${tab.dataset.tab}`).classList.add('active');
  });
}

// ---- connection ----

const hostInput = $('host-input');
const simToggle = $('sim-toggle');
const connectBtn = $('connect-btn');

connectBtn.addEventListener('click', async () => {
  connectBtn.disabled = true;
  try {
    if (app.status?.connected) {
      await app.api('/api/disconnect');
      app.log('Desligado do robô');
    } else {
      const data = await app.api('/api/connect', {
        body: { host: hostInput.value.trim(), sim: simToggle.checked },
      });
      const v = data.hello?.VERSION || '?';
      app.log(`Ligado (firmware ${v})`, 'ok');
    }
  } catch (err) {
    app.log(err.message, 'error');
  } finally {
    connectBtn.disabled = false;
  }
});

function updateConnectionUI(status) {
  const dot = $('status-dot');
  dot.classList.toggle('on', status.connected && !status.simulator);
  dot.classList.toggle('sim', status.connected && status.simulator);
  let text = 'desligado';
  if (status.connected) {
    text = status.simulator ? 'simulação' : status.host;
    if (status.hello?.VERSION) text += ` · fw ${status.hello.VERSION}`;
  }
  $('status-text').textContent = text;
  connectBtn.textContent = status.connected ? 'Desligar' : 'Ligar';
  if (!hostInput.value && status.default_host) {
    hostInput.value = status.default_host;
  }
}

// ---- job bar ----

function updateJobButtons(progress) {
  const running = progress.state === 'running';
  const paused = progress.state === 'paused';
  $('job-start').hidden = running || paused;
  $('job-pause').hidden = !running;
  $('job-resume').hidden = !paused;
  $('job-stop').hidden = !(running || paused);
  $('fit-wrap').hidden = running || paused;
}

function updateProgress(progress) {
  const isCurrentJob = app.job && progress.job_id === app.job.job_id;
  updateJobButtons(progress);
  if (progress.state === 'error' && progress.error) {
    app.log(`Trabalho falhou: ${progress.error}`, 'error');
  } else if (progress.state === 'done' && isCurrentJob) {
    app.log('Desenho concluído 🎉', 'ok');
  }
  if (!isCurrentJob) return;
  const pct = progress.segments_total
    ? (100 * progress.segments_done) / progress.segments_total : 0;
  $('progress-fill').style.width = `${pct.toFixed(1)}%`;
  $('job-progress-text').textContent =
    `${progress.segments_done}/${progress.segments_total} segmentos`;
  board.setProgress(progress.segments_done);
}

$('job-start').addEventListener('click', async () => {
  if (!app.job) return;
  try {
    await app.api('/api/job/start', {
      body: { job_id: app.job.job_id, fit: $('fit-check').checked },
    });
    app.log(`A desenhar "${app.job.name}"…`);
  } catch (err) {
    app.log(err.message, 'error');
  }
});
$('job-pause').addEventListener('click', () => app.api('/api/job/pause').catch((e) => app.log(e.message, 'error')));
$('job-resume').addEventListener('click', () => app.api('/api/job/resume').catch((e) => app.log(e.message, 'error')));
$('job-stop').addEventListener('click', () => app.api('/api/job/stop').catch((e) => app.log(e.message, 'error')));

// ---- panels ----

const live = initLivePanel(app);
initGcodePanel(app);
initImagePanel(app);
const robot = initRobotPanel(app);

// ---- websocket ----

app.ws = new WSClient({
  onMessage(msg) {
    switch (msg.type) {
      case 'status':
        app.status = msg;
        updateConnectionUI(msg);
        live.setActive(msg.live);
        if (msg.job) updateProgress(msg.job);
        break;
      case 'position':
        board.setPosition(msg);
        robot.setPosition(msg);
        break;
      case 'progress':
        updateProgress(msg);
        break;
      case 'log':
        app.log(msg.message, msg.level || 'info');
        break;
    }
  },
  onOpen() { app.log('Ligação ao servidor estabelecida'); },
  onClose() { app.log('Ligação ao servidor perdida — a tentar de novo…', 'error'); },
});
