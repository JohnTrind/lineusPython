// G-code panel: upload a file, preview the parsed paths, then draw it
// via the shared job bar.

import { bindDropzone, renderWarnings } from './shared.js';

export function initGcodePanel(app) {
  const info = document.getElementById('gcode-info');
  const warnings = document.getElementById('gcode-warnings');

  async function upload(file) {
    const formData = new FormData();
    formData.append('file', file);
    try {
      const data = await app.api('/api/gcode', { formData });
      info.hidden = false;
      info.textContent =
        `${data.name} — ${data.paths} traços, ${data.segments} segmentos` +
        (data.out_of_bounds ? ` · ${data.out_of_bounds} fora da área` : '');
      renderWarnings(warnings, data.warnings);
      app.setJob(data, { fit: data.out_of_bounds > 0 });
      app.log(`G-code carregado: ${data.name}`);
    } catch (err) {
      app.log(`Upload falhou: ${err.message}`, 'error');
    }
  }

  bindDropzone(
    document.getElementById('gcode-drop'),
    document.getElementById('gcode-file'),
    upload,
  );
}
