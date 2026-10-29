// Comprueba en build si el release tiene la guía PDF. Con respaldo si no hay red.
import { guideUrl, tagUrl } from './site.ts';

export interface GuideLink {
  href: string;
  /** true: el PDF existe; false: no se pudo comprobar y se enlaza al release. */
  direct: boolean;
}

const cache = new Map<string, Promise<GuideLink | null>>();

async function check(version: string): Promise<GuideLink | null> {
  try {
    const res = await fetch(guideUrl(version), { method: 'HEAD', signal: AbortSignal.timeout(8000) });
    if (res.ok) return { href: guideUrl(version), direct: true };
    if (res.status === 404) return null;
  } catch {
    // sin red o GitHub caído: se usa el respaldo
  }
  return { href: tagUrl(version), direct: false };
}

export function getGuide(version: string): Promise<GuideLink | null> {
  let p = cache.get(version);
  if (!p) cache.set(version, (p = check(version)));
  return p;
}
