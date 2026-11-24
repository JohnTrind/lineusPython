// Small helpers shared by the upload panels.

export function bindDropzone(zone, input, onFile) {
  zone.addEventListener('click', () => input.click());
  input.addEventListener('change', () => {
    if (input.files[0]) onFile(input.files[0]);
    input.value = '';
  });
  zone.addEventListener('dragover', (ev) => {
    ev.preventDefault();
    zone.classList.add('over');
  });
  zone.addEventListener('dragleave', () => zone.classList.remove('over'));
  zone.addEventListener('drop', (ev) => {
    ev.preventDefault();
    zone.classList.remove('over');
    if (ev.dataTransfer.files[0]) onFile(ev.dataTransfer.files[0]);
  });
}

export function renderWarnings(listEl, warnings) {
  listEl.replaceChildren(
    ...(warnings || []).map((w) => {
      const li = document.createElement('li');
      li.textContent = w;
      return li;
    }),
  );
}
