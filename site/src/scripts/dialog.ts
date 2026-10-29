// Utilidades para <dialog> nativo: foco atrapado, Esc, clic en el fondo y devolución del foco.
const FOCUSABLE =
  'a[href],button:not([disabled]),input:not([disabled]),select,textarea,summary,[tabindex]:not([tabindex="-1"])';

export interface ModalOptions {
  /** Se llama cuando el diálogo se cierra por cualquier vía. */
  onClose?: () => void;
  /** Se llama cuando el usuario pide cerrar (Esc, fondo, botón). Si devuelve true, el cierre lo hace quien llama. */
  onRequestClose?: () => boolean | void;
}

const openers = new WeakMap<HTMLDialogElement, HTMLElement | null>();

export function wireDialog(dialog: HTMLDialogElement, opts: ModalOptions = {}): void {
  const requestClose = () => {
    if (opts.onRequestClose?.() === true) return;
    dialog.close();
  };

  dialog.addEventListener('cancel', (e) => {
    e.preventDefault(); // Esc: pasa por requestClose para poder sincronizar la URL
    requestClose();
  });

  dialog.addEventListener('click', (e) => {
    if (e.target === dialog) requestClose(); // clic en el fondo
    else if ((e.target as Element).closest('[data-close]')) requestClose();
  });

  dialog.addEventListener('keydown', (e) => {
    if (e.key !== 'Tab') return;
    const items = [...dialog.querySelectorAll<HTMLElement>(FOCUSABLE)].filter((el) => el.offsetParent !== null);
    if (items.length === 0) return;
    const first = items[0]!;
    const last = items[items.length - 1]!;
    const active = document.activeElement;
    if (e.shiftKey && (active === first || active === dialog)) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && active === last) {
      e.preventDefault();
      first.focus();
    }
  });

  dialog.addEventListener('close', () => {
    const opener = openers.get(dialog);
    openers.delete(dialog);
    if (opener && document.contains(opener)) opener.focus();
    opts.onClose?.();
  });
}

export function openDialog(dialog: HTMLDialogElement, opener: HTMLElement | null): void {
  if (dialog.open) return;
  openers.set(dialog, opener);
  dialog.showModal();
  const target = dialog.querySelector<HTMLElement>('[data-autofocus]') ?? dialog.querySelector<HTMLElement>('[data-close]');
  target?.focus();
}
