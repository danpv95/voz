// /cambios: filtros por tipo (?tipo=) y vista previa de versión en <dialog>.
import { openDialog, wireDialog } from './dialog.ts';

const TIPOS = ['anadido', 'cambiado', 'corregido'] as const;
type Tipo = (typeof TIPOS)[number];
const LABEL: Record<Tipo, string> = { anadido: 'Añadido', cambiado: 'Cambiado', corregido: 'Corregido' };

const main = document.getElementById('contenido') as HTMLElement;
const links = [...document.querySelectorAll<HTMLAnchorElement>('.filters a[data-tipo]')];
const status = document.getElementById('filter-status');

const fromSearch = (search: string): Tipo | null => {
  const t = new URLSearchParams(search).get('tipo');
  return (TIPOS as readonly string[]).includes(t ?? '') ? (t as Tipo) : null;
};

function apply(tipo: Tipo | null): void {
  if (tipo) main.dataset.tipo = tipo;
  else delete main.dataset.tipo;
  for (const a of links) {
    const on = (a.dataset.tipo || null) === tipo;
    if (on) a.setAttribute('aria-current', 'true');
    else a.removeAttribute('aria-current');
  }
  if (status) {
    const shown = [...document.querySelectorAll<HTMLElement>('.timeline .card:not(.next)')].filter(
      (c) => c.offsetParent !== null,
    ).length;
    status.textContent = tipo
      ? `${shown} ${shown === 1 ? 'versión con' : 'versiones con'} cambios de tipo ${LABEL[tipo]}.`
      : '';
  }
}

// Filtros
apply(fromSearch(location.search) ?? fromSearch(`?tipo=${main.dataset.tipo ?? ''}`));

for (const a of links) {
  a.addEventListener('click', (e) => {
    if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0) return;
    e.preventDefault();
    const tipo = (a.dataset.tipo || null) as Tipo | null;
    apply(tipo);
    history.pushState({ tipo }, '', a.getAttribute('href')!);
  });
}

// Vista previa
const dialog = document.getElementById('preview-dialog') as HTMLDialogElement | null;
if (dialog && typeof dialog.showModal === 'function') {
  const body = document.getElementById('preview-body')!;
  const title = document.getElementById('preview-title')!;
  let pushed = false;
  let prevTitle = document.title;

  wireDialog(dialog, {
    onRequestClose: () => {
      if (pushed) {
        history.back(); // popstate cierra el diálogo y deja la URL como estaba
        return true;
      }
    },
    onClose: () => {
      pushed = false;
      document.title = prevTitle;
    },
  });

  const show = (slug: string, opener: HTMLElement | null, push: boolean): boolean => {
    const tpl = document.getElementById(`tpl-${slug}`) as HTMLTemplateElement | null;
    if (!tpl) return false;
    body.replaceChildren(tpl.content.cloneNode(true));
    title.textContent = tpl.dataset.title ?? slug;
    prevTitle = document.title;
    document.title = `${tpl.dataset.title} · voz`;
    if (push) {
      history.pushState({ preview: slug }, '', `/cambios/${slug}`);
      pushed = true;
    }
    openDialog(dialog, opener);
    return true;
  };

  for (const a of document.querySelectorAll<HTMLAnchorElement>('[data-release-link]')) {
    a.addEventListener('click', (e) => {
      if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0) return;
      if (show(a.dataset.releaseLink!, a, true)) e.preventDefault();
    });
  }

  window.addEventListener('popstate', (e) => {
    const slug = (e.state as { preview?: string } | null)?.preview;
    if (dialog.open && !slug) {
      pushed = false;
      dialog.close();
    } else if (!dialog.open && slug) {
      show(slug, null, false);
      pushed = true;
    } else if (!slug) {
      apply(fromSearch(location.search));
    }
  });
}
