/* Tema claro/oscuro. Script externo y bloqueante en <head> (la CSP es script-src 'self', sin inline).
   Por defecto el sitio sigue al sistema. El botón alterna y guarda la elección en localStorage ('voz-tema').
   Si la elección coincide con el sistema se borra la clave: así se vuelve a «según el sistema» sin un tercer estado en la UI.
   Si localStorage falla (bloqueado, modo privado), la elección vale solo para esta página.
   Sin JS el botón queda oculto (atributo hidden) y manda prefers-color-scheme. */
(function () {
  if (window.__vozTema) return;
  window.__vozTema = 1;
  var KEY = 'voz-tema';
  var root = document.documentElement;
  var mq = window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null;

  function system() { return mq && mq.matches ? 'dark' : 'light'; }
  function read() {
    try {
      var v = localStorage.getItem(KEY);
      return v === 'light' || v === 'dark' ? v : null;
    } catch (e) { return null; }
  }
  function write(v) {
    try {
      if (v) localStorage.setItem(KEY, v); else localStorage.removeItem(KEY);
    } catch (e) { /* solo para esta página */ }
  }
  function current() { return root.getAttribute('data-theme') || system(); }

  /* theme-color: los dos <meta> llevan media por esquema. Con un tema forzado solo debe valer el del tema elegido. */
  function syncMeta() {
    var forced = root.getAttribute('data-theme');
    var metas = document.querySelectorAll('meta[name="theme-color"]');
    for (var i = 0; i < metas.length; i++) {
      var m = metas[i];
      if (m.getAttribute('data-media') === null) m.setAttribute('data-media', m.getAttribute('media') || '');
      var own = m.getAttribute('data-media').indexOf('dark') > -1 ? 'dark' : 'light';
      if (forced) m.setAttribute('media', own === forced ? 'all' : 'not all');
      else m.setAttribute('media', m.getAttribute('data-media'));
    }
  }
  function apply(theme) {
    if (theme) root.setAttribute('data-theme', theme); else root.removeAttribute('data-theme');
    syncMeta();
  }

  apply(read());

  function label(button) {
    var action = current() === 'dark' ? 'Cambiar a modo claro' : 'Cambiar a modo oscuro';
    button.setAttribute('aria-label', action);
    button.setAttribute('title', action);
  }

  document.addEventListener('DOMContentLoaded', function () {
    var button = document.querySelector('.theme');
    if (!button) return;
    button.hidden = false;
    label(button);
    button.addEventListener('click', function () {
      var next = current() === 'dark' ? 'light' : 'dark';
      var forced = next === system() ? null : next;
      apply(forced);
      write(forced);
      label(button);
    });
    if (mq) {
      var follow = function () { label(button); };
      if (mq.addEventListener) mq.addEventListener('change', follow); else if (mq.addListener) mq.addListener(follow);
    }
    window.addEventListener('storage', function (e) {
      if (e.key === KEY) { apply(read()); label(button); }
    });
  });
})();
