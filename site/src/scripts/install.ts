// Home: pestañas Hoy / Próximamente y modal de instalación.
import { openDialog, wireDialog } from './dialog.ts';

// --- Pestañas (sin JS se ven los dos paneles) ---
const list = document.getElementById('tablist');
if (list) {
  const tabs = [...list.querySelectorAll<HTMLElement>('[role=tab]')];
  const show = (tab: HTMLElement, focus = false) => {
    for (const t of tabs) {
      const on = t === tab;
      t.setAttribute('aria-selected', String(on));
      t.tabIndex = on ? 0 : -1;
      const panel = document.getElementById(t.getAttribute('aria-controls')!);
      if (panel) panel.hidden = !on;
    }
    if (focus) tab.focus();
  };
  list.hidden = false;
  show(tabs[0]!);
  tabs.forEach((tab, i) => {
    tab.addEventListener('click', () => show(tab));
    tab.addEventListener('keydown', (e) => {
      let next: HTMLElement | undefined;
      if (e.key === 'ArrowRight') next = tabs[(i + 1) % tabs.length];
      else if (e.key === 'ArrowLeft') next = tabs[(i + tabs.length - 1) % tabs.length];
      else if (e.key === 'Home') next = tabs[0];
      else if (e.key === 'End') next = tabs[tabs.length - 1];
      if (next) {
        e.preventDefault();
        show(next, true);
      }
    });
  });
}

// --- Modal de instalación ---
const dialog = document.getElementById('install-dialog') as HTMLDialogElement | null;
if (dialog && typeof dialog.showModal === 'function') {
  wireDialog(dialog);
  for (const opener of document.querySelectorAll<HTMLElement>('[data-open-install]')) {
    opener.addEventListener('click', (e) => {
      const ev = e as MouseEvent;
      if (ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.altKey || ev.button !== 0) return;
      e.preventDefault();
      openDialog(dialog, opener);
    });
  }
  // Los enlaces del diálogo a anclas de la página lo cierran antes de navegar.
  dialog.addEventListener('click', (e) => {
    const a = (e.target as Element).closest<HTMLAnchorElement>('a[href^="#"], a[href^="/#"]');
    if (a) dialog.close();
  });
}
