// Botón «Copiar» de los bloques de comandos. Delegado: funciona también dentro de los diálogos.
const LABEL = 'Copiar';

function legacyCopy(text: string, host: HTMLElement): boolean {
  const area = document.createElement('textarea');
  area.value = text;
  area.setAttribute('readonly', '');
  area.style.cssText = 'position:fixed;opacity:0;top:0;left:0';
  host.appendChild(area);
  area.select();
  let ok = false;
  try {
    ok = document.execCommand('copy');
  } catch {
    ok = false;
  }
  area.remove();
  return ok;
}

async function copyText(text: string, host: HTMLElement): Promise<boolean> {
  if (navigator.clipboard && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      // cae al método antiguo
    }
  }
  return legacyCopy(text, host);
}

document.addEventListener('click', async (event) => {
  const button = (event.target as Element).closest<HTMLButtonElement>('[data-copy]');
  if (!button) return;
  const block = button.closest('.block');
  const text = block?.querySelector('pre')?.textContent?.replace(/\n$/, '');
  if (!text) return;
  const ok = await copyText(text, button.parentElement ?? document.body);
  const live = document.getElementById('live');
  if (ok) {
    button.textContent = '✓ Copiado';
    button.classList.add('done');
    if (live) live.textContent = 'Copiado al portapapeles';
    window.setTimeout(() => {
      button.textContent = LABEL;
      button.classList.remove('done');
      if (live) live.textContent = '';
    }, 1800);
  } else {
    button.textContent = 'Selecciona y copia';
  }
});
