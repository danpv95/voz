import { test } from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { formatDate, kindOf, parseChangelog, renderInline, summarize } from '../src/lib/changelog.ts';

const REPO = 'https://github.com/danpv95/voz';

const SAMPLE = `# Changelog

Intro del archivo.

## Unreleased / 0.2.0

Pendiente, sin promesas.

### Correcciones prioritarias
1. Primer pendiente con \`codigo\`.
2. Segundo pendiente.

## 0.1.1 - 2026-11-02

### Corregido
- Arregla una cosa. Y otra frase.
- Otra línea
  que continúa abajo.

## [0.1.0] - 2026-10-29

Primera versión pública.

### Añadido
- Dictado en vivo con [mlx-whisper](https://pypi.org/project/mlx-whisper/), 100 % local.
- Segundo.
- Tercero.
- Cuarto.

### Cambiado
- Cambio uno.

### Corregido
- Arreglo uno.
`;

test('lee versiones, fechas y orden', () => {
  const r = parseChangelog(SAMPLE, { repo: REPO });
  assert.deepEqual(r.map((x) => x.slug), ['proximo', '0.1.1', '0.1.0']);
  assert.deepEqual(r.map((x) => x.date), [null, '2026-11-02', '2026-10-29']);
  assert.equal(r[2]!.version, '0.1.0');
});

test('Unreleased: sin versión ni fecha, apunta a la siguiente', () => {
  const [next] = parseChangelog(SAMPLE, { repo: REPO });
  assert.equal(next!.unreleased, true);
  assert.equal(next!.version, null);
  assert.equal(next!.target, '0.2.0');
  assert.equal(next!.releaseUrl, null);
  assert.equal(next!.compareUrl, null);
  assert.deepEqual(next!.intro, ['Pendiente, sin promesas.']);
  assert.equal(next!.itemCount, 2);
});

test('secciones, tipos y conteo de ítems', () => {
  const v = parseChangelog(SAMPLE, { repo: REPO }).find((x) => x.version === '0.1.0')!;
  assert.deepEqual(v.sections.map((s) => [s.title, s.kind, s.items.length]), [
    ['Añadido', 'anadido', 4],
    ['Cambiado', 'cambiado', 1],
    ['Corregido', 'corregido', 1],
  ]);
  assert.deepEqual(v.intro, ['Primera versión pública.']);
  assert.equal(v.itemCount, 6);
});

test('une líneas de continuación en un solo ítem', () => {
  const v = parseChangelog(SAMPLE, { repo: REPO }).find((x) => x.version === '0.1.1')!;
  assert.equal(v.sections[0]!.items[1]!.plain, 'Otra línea que continúa abajo.');
});

test('3 destacados, saliendo primero de Añadido', () => {
  const v = parseChangelog(SAMPLE, { repo: REPO }).find((x) => x.version === '0.1.0')!;
  assert.deepEqual(v.highlights, [
    'Dictado en vivo con mlx-whisper, 100 % local',
    'Segundo',
    'Tercero',
  ]);
});

test('enlaces de versión y de comparación con la anterior', () => {
  const r = parseChangelog(SAMPLE, { repo: REPO });
  const v011 = r.find((x) => x.version === '0.1.1')!;
  const v010 = r.find((x) => x.version === '0.1.0')!;
  assert.equal(v011.previous, '0.1.0');
  assert.equal(v011.compareUrl, `${REPO}/compare/v0.1.0...v0.1.1`);
  assert.equal(v011.releaseUrl, `${REPO}/releases/tag/v0.1.1`);
  assert.equal(v010.previous, null);
  assert.equal(v010.compareUrl, null);
});

test('ignora encabezados dentro de bloques de código', () => {
  const md = '## 1.0.0 - 2026-01-01\n\n### Añadido\n- x\n\n```\n## 9.9.9 - 2030-01-01\n```\n';
  assert.deepEqual(parseChangelog(md).map((x) => x.version), ['1.0.0']);
});

test('summarize corta donde debe', () => {
  assert.equal(summarize('Una frase corta. Otra más.'), 'Una frase corta');
  const long =
    'Medidor que distingue silencio de voz: puntos tenues (·) cuando no hay voz y barras de color (▁▂▃▅▇) cuando voz te está detectando.';
  assert.equal(summarize(long), 'Medidor que distingue silencio de voz');
  assert.ok(summarize('palabra '.repeat(40)).endsWith('…'));
});

test('kindOf reconoce los tipos de Keep a Changelog', () => {
  assert.equal(kindOf('Añadido'), 'anadido');
  assert.equal(kindOf('Added'), 'anadido');
  assert.equal(kindOf('Cambiado'), 'cambiado');
  assert.equal(kindOf('Corregido'), 'corregido');
  assert.equal(kindOf('Infraestructura'), 'otro');
});

test('renderInline escapa HTML y solo enlaza http(s)', () => {
  assert.equal(renderInline('usa `a<b` y **ojo**'), 'usa <code>a&lt;b</code> y <strong>ojo</strong>');
  assert.equal(
    renderInline('[doc](https://x.dev/a)'),
    '<a href="https://x.dev/a" rel="noopener">doc</a>',
  );
  assert.equal(renderInline('[mal](javascript:alert(1))'), '[mal](javascript:alert(1))');
  assert.equal(renderInline('<script>x</script>'), '&lt;script&gt;x&lt;/script&gt;');
});

test('formatDate', () => {
  assert.equal(formatDate('2026-10-29'), '29 de octubre de 2026');
  assert.equal(formatDate('2026-01-05'), '5 de enero de 2026');
});

// ---- CHANGELOG real del repo ----
const CHANGELOG = fileURLToPath(new URL('../../CHANGELOG.md', import.meta.url));

test('CHANGELOG real: «Próximo» primero y versiones con fecha', () => {
  const r = parseChangelog(readFileSync(CHANGELOG, 'utf8'), { repo: REPO });
  assert.equal(r[0]!.unreleased, true);
  assert.ok(r.filter((x) => !x.unreleased).length >= 1);
  for (const x of r.filter((y) => !y.unreleased)) {
    assert.match(x.date ?? '', /^\d{4}-\d{2}-\d{2}$/, `falta fecha en ${x.version}`);
    assert.ok(x.itemCount > 0, `${x.version} sin ítems`);
  }
});

test('CHANGELOG real: las versiones publicadas coinciden con `git tag`', (t) => {
  let tags: string[];
  try {
    tags = execFileSync('git', ['tag', '--list', 'v*'], {
      cwd: fileURLToPath(new URL('..', import.meta.url)),
      encoding: 'utf8',
    })
      .split('\n')
      .map((s) => s.trim())
      .filter(Boolean);
  } catch {
    return t.skip('git no disponible');
  }
  if (tags.length === 0) return t.skip('sin tags en este clon');
  const versions = parseChangelog(readFileSync(CHANGELOG, 'utf8'), { repo: REPO })
    .filter((x) => !x.unreleased)
    .map((x) => `v${x.version}`);
  assert.deepEqual(versions.sort(), tags.sort());
});

test('summarize quita paréntesis largos antes de cortar', () => {
  const s = summarize(
    'Interfaz de terminal con estados (Listo, Escuchando, En pausa, Revisión, Sin voz, Elegir) y teclas de una sola pulsación: hablar, pausar.',
  );
  assert.equal(s, 'Interfaz de terminal con estados y teclas de una sola pulsación');
});
