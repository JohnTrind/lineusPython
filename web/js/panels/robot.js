// Robot panel: direct controls (home, pen) plus a manual G-code console
// and the live position readout.

export function initRobotPanel(app) {
  const posEl = document.getElementById('robot-pos');
  const cmdInput = document.getElementById('gcode-cmd');

  const sendOrWarn = (msg) => {
    if (!app.status?.connected) {
      app.log('Liga-te ao robô primeiro', 'error');
      return;
    }
    app.ws.send(msg);
  };

  document.getElementById('robot-home')
    .addEventListener('click', () => sendOrWarn({ type: 'home' }));
  document.getElementById('robot-penup')
    .addEventListener('click', () => sendOrWarn({ type: 'pen_up' }));
  document.getElementById('robot-pendown')
    .addEventListener('click', () => sendOrWarn({ type: 'pen_down' }));

  document.getElementById('gcode-console').addEventListener('submit', (ev) => {
    ev.preventDefault();
    const command = cmdInput.value.trim();
    if (!command) return;
    app.log(`> ${command}`);
    sendOrWarn({ type: 'gcode', command });
    cmdInput.value = '';
  });

  return {
    setPosition({ x, y, z }) {
      const pen = z < 500 ? '✏️' : '·';
      posEl.textContent = `X ${x.toFixed(0)}  Y ${y.toFixed(0)}  Z ${z.toFixed(0)} ${pen}`;
    },
  };
}
