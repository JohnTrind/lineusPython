// Image panel: upload an image, convert it to polylines server-side with
// adjustable parameters, preview and draw via the shared job bar.

import { bindDropzone, renderWarnings } from './shared.js';

export function initImagePanel(app) {
  const convertBtn = document.getElementById('img-convert');
  const info = document.getElementById('image-info');
  const warnings = document.getElementById('image-warnings');
  const detail = document.getElementById('img-detail');
  const simplify = document.getElementById('img-simplify');
  const hatching = document.getElementById('img-hatching');
  const density = document.getElementById('img-density');
  const densityField = document.getElementById('img-density-field');
  const invert = document.getElementById('img-invert');

  let currentFile = null;

  // slider value labels (simplify slider is 0-50 → epsilon 0-5.0 px)
  const bindLabel = (input, label, fmt = (v) => v) => {
    const update = () => { label.textContent = fmt(input.value); };
    input.addEventListener('input', update);
    update();
  };
  bindLabel(detail, document.getElementById('img-detail-val'));
  bindLabel(simplify, document.getElementById('img-simplify-val'),
    (v) => (v / 10).toFixed(1));
  bindLabel(density, document.getElementById('img-density-val'));

  hatching.addEventListener('change', () => {
    densityField.hidden = !hatching.checked;
  });

  async function convert() {
    if (!currentFile) return;
    convertBtn.disabled = true;
    convertBtn.textContent = 'A converter…';
    const formData = new FormData();
    formData.append('file', currentFile);
    formData.append('detail', detail.value);
    formData.append('simplify', String(simplify.value / 10));
    formData.append('hatching', hatching.checked);
    formData.append('hatch_density', density.value);
    formData.append('invert', invert.checked);
    try {
      const data = await app.api('/api/image', { formData });
      info.hidden = false;
      info.textContent = `${data.name} — ${data.paths} traços, ${data.segments} segmentos`;
      renderWarnings(warnings, data.warnings);
      app.setJob(data);
      app.log(`Imagem convertida: ${data.name}`);
    } catch (err) {
      app.log(`Conversão falhou: ${err.message}`, 'error');
    } finally {
      convertBtn.disabled = !currentFile;
      convertBtn.textContent = 'Reconverter';
    }
  }

  bindDropzone(
    document.getElementById('image-drop'),
    document.getElementById('image-file'),
    (file) => {
      currentFile = file;
      convertBtn.disabled = false;
      convertBtn.textContent = 'Converter';
      convert();
    },
  );

  convertBtn.addEventListener('click', convert);
}
