// Carga el CHANGELOG en build y lo parsea. Una sola lectura por proceso.
import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { parseChangelog, type Release } from './changelog.ts';
import { REPO } from './site.ts';

// El build corre con `site/` como directorio de trabajo (también en Vercel con root directory `site/`).
const LOCAL = resolve(process.cwd(), '../CHANGELOG.md');
const REMOTE =
  process.env.CHANGELOG_URL || 'https://raw.githubusercontent.com/danpv95/voz/main/CHANGELOG.md';

let cache: Promise<Release[]> | undefined;

async function read(): Promise<string> {
  try {
    return await readFile(LOCAL, 'utf8');
  } catch {
    // Vercel con root directory `site/` puede no incluir archivos fuera de la raíz.
    console.warn(`[voz-site] No encuentro ${LOCAL}; uso ${REMOTE}`);
    const res = await fetch(REMOTE, { signal: AbortSignal.timeout(15000) });
    if (!res.ok) throw new Error(`CHANGELOG no disponible (${res.status}) en ${REMOTE}`);
    return await res.text();
  }
}

export function getReleases(): Promise<Release[]> {
  cache ??= read().then((md) => {
    const list = parseChangelog(md, { repo: REPO });
    if (!list.some((r) => !r.unreleased)) throw new Error('El CHANGELOG no tiene versiones publicadas.');
    return list;
  });
  return cache;
}

export async function getLatest(): Promise<Release> {
  const latest = (await getReleases()).find((r) => !r.unreleased);
  if (!latest) throw new Error('Sin versión publicada.');
  return latest;
}
