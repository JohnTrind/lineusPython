// Live mode panel: the robot follows the mouse on the board.
// Pointer events are throttled to ~30 Hz; pen up/down transitions are
// always sent immediately (the backend keeps only the latest target).

const THROTTLE_MS = 33;

export function initLivePanel(app) {
  const toggle = document.getElementById('live-toggle');
  const speed = document.getElementById('live-speed');
  const speedVal = document.getElementById('live-speed-val');
  const clear = document.getElementById('live-clear');

  let active = false;
  let lastSent = 0;
  let lastPen = false;

  app.board.onLivePoint = (x, y, pen) => {
    if (!active) return;
    const now = performance.now();
    if (pen === lastPen && now - lastSent < THROTTLE_MS) return;
    lastSent = now;
    lastPen = pen;
    app.ws.send({ type: 'live', x, y, pen });
  };

  toggle.addEventListener('click', () => {
    if (!app.status?.connected) {
      app.log('Liga-te ao robô primeiro', 'error');
      return;
    }
    app.ws.send(active
      ? { type: 'live_stop' }
      : { type: 'live_start', speed: Number(speed.value) });
  });

  speed.addEventListener('input', () => { speedVal.textContent = speed.value; });
  speed.addEventListener('change', () => {
    if (active) app.ws.send({ type: 'speed', value: Number(speed.value) });
  });

  clear.addEventListener('click', () => app.board.clearLiveStrokes());

  return {
    setActive(on) {
      if (on === active) return;
      active = on;
      app.liveActive = on;
      app.board.setLiveMode(on);
      toggle.textContent = on ? 'Desativar modo live' : 'Ativar modo live';
      toggle.classList.toggle('live-on', on);
      toggle.classList.toggle('primary', !on);
      app.log(on ? 'Modo live ativo — desenha no canvas' : 'Modo live desativado');
    },
  };
}
