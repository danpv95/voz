// Parser puro de CHANGELOG.md (Keep a Changelog, en español).
// Sin dependencias ni acceso a disco: recibe texto y devuelve datos.

export type Kind = 'anadido' | 'cambiado' | 'corregido' | 'otro';

export interface Item {
  /** Markdown en línea tal como está en el CHANGELOG. */
  raw: string;
  /** Texto sin marcas de markdown. */
  plain: string;
  /** Frase corta para tarjetas. */
  summary: string;
}

export interface Section {
  title: string;
  kind: Kind;
  items: Item[];
  /** Primeros 3 resúmenes de la sección. */
  highlights: string[];
}

export interface Release {
  /** Número de versión publicado; null en «Unreleased». */
  version: string | null;
  /** En «Unreleased / 0.1.1», la versión a la que apunta (0.1.1). */
  target: string | null;
  /** Segmento de URL: la versión, o `proximo`. */
  slug: string;
  date: string | null;
  unreleased: boolean;
  intro: string[];
  sections: Section[];
  /** 3 destacados de toda la versión. */
  highlights: string[];
  itemCount: number;
  /** Versión publicada inmediatamente anterior, si existe. */
  previous: string | null;
  releaseUrl: string | null;
  compareUrl: string | null;
}

export interface ParseOptions {
  repo?: string;
}

const DEFAULT_REPO = 'https://github.com/danpv95/voz';
const SEMVER = /\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?/;

export function kindOf(title: string): Kind {
  const t = title.trim().toLowerCase();
  if (/^(a[ñn]adido|added)/.test(t)) return 'anadido';
  if (/^(cambiado|changed)/.test(t)) return 'cambiado';
  if (/^(corregido|fixed)/.test(t)) return 'corregido';
  return 'otro';
}

/** Quita markdown en línea: enlaces, código, negritas. */
export function toPlain(md: string): string {
  return md
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/\*\*([^*]+)\*\*/g, '$1')
    .replace(/\s+/g, ' ')
    .trim();
}

/** Primera frase; si es larga, la corta en ':' o ';' y, si no, en el último espacio. */
export function summarize(plain: string, max = 110): string {
  let s = plain.trim();
  const end = s.search(/[.!?](\s|$)/);
  if (end > 0) s = s.slice(0, end + 1);
  if (s.length > max) {
    s = s.replace(/\s*\([^)]*\)/g, ''); // fuera los paréntesis
    const cut = s.search(/[:;]/);
    if (cut >= 20) {
      s = s.slice(0, cut);
    } else if (s.length > max) {
      const sp = s.lastIndexOf(' ', max);
      s = s.slice(0, sp > 0 ? sp : max) + '…';
    }
  }
  const open = (s.match(/\(/g) ?? []).length;
  const close = (s.match(/\)/g) ?? []).length;
  if (open > close) s = s.slice(0, s.lastIndexOf('(')).trim();
  return s.replace(/[\s,:;]+$/, '').replace(/\.$/, '');
}

const escapeHtml = (s: string): string =>
  s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

/** Markdown en línea a HTML seguro: código, enlaces http(s) y negritas. */
export function renderInline(md: string): string {
  return md
    .split(/(`[^`]+`)/g)
    .map((part) => {
      if (part.length > 1 && part.startsWith('`') && part.endsWith('`')) {
        return `<code>${escapeHtml(part.slice(1, -1))}</code>`;
      }
      return escapeHtml(part)
        .replace(
          /\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g,
          '<a href="$2" rel="noopener">$1</a>',
        )
        .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    })
    .join('');
}

function makeItem(raw: string): Item {
  const plain = toPlain(raw);
  return { raw, plain, summary: summarize(plain) };
}

interface RawBlock {
  heading: string;
  lines: string[];
}

function splitBlocks(md: string, level: number): RawBlock[] {
  const marker = '#'.repeat(level) + ' ';
  const blocks: RawBlock[] = [];
  let current: RawBlock | null = null;
  let fenced = false;
  for (const line of md.split(/\r?\n/)) {
    if (/^\s*```/.test(line)) fenced = !fenced;
    if (!fenced && line.startsWith(marker)) {
      current = { heading: line.slice(marker.length).trim(), lines: [] };
      blocks.push(current);
    } else if (!fenced && /^#{1,2} /.test(line) && level === 3) {
      current = null; // un encabezado de nivel superior corta la sección
    } else if (current) {
      current.lines.push(line);
    }
  }
  return blocks;
}

function parseSection(block: RawBlock): Section {
  const items: Item[] = [];
  for (const line of block.lines) {
    const bullet = line.match(/^\s{0,3}(?:[-*+]|\d+\.)\s+(.*)$/);
    if (bullet) {
      items.push(makeItem(bullet[1]!.trim()));
    } else if (/^\s{2,}\S/.test(line) && items.length > 0) {
      const last = items[items.length - 1]!;
      items[items.length - 1] = makeItem(`${last.raw} ${line.trim()}`);
    }
  }
  return {
    title: block.heading,
    kind: kindOf(block.heading),
    items,
    highlights: items.slice(0, 3).map((i) => i.summary),
  };
}

function parseIntro(lines: string[]): string[] {
  const paragraphs: string[] = [];
  let buf: string[] = [];
  const flush = () => {
    if (buf.length) paragraphs.push(buf.join(' ').trim());
    buf = [];
  };
  for (const line of lines) {
    if (/^\s*$/.test(line)) flush();
    else buf.push(line.trim());
  }
  flush();
  return paragraphs;
}

function pickHighlights(sections: Section[]): string[] {
  const ordered = [
    ...sections.filter((s) => s.kind === 'anadido'),
    ...sections.filter((s) => s.kind !== 'anadido'),
  ];
  const out: string[] = [];
  for (const s of ordered) {
    for (const h of s.highlights) {
      if (out.length < 3) out.push(h);
    }
  }
  return out;
}

export function parseChangelog(md: string, opts: ParseOptions = {}): Release[] {
  const repo = (opts.repo ?? DEFAULT_REPO).replace(/\/+$/, '');
  const releases: Release[] = [];

  for (const block of splitBlocks(md, 2)) {
    const h = block.heading;
    const unreleased = /^\[?(unreleased|pr[oó]ximo|sin publicar)/i.test(h);
    const dated = h.match(/^\[?(\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?)\]?(?:\s*[-\u2013\u2014]\s*(\d{4}-\d{2}-\d{2}))?/);
    if (!unreleased && !dated) continue;

    const firstSection = block.lines.findIndex((l) => l.startsWith('### '));
    const head = firstSection === -1 ? block.lines : block.lines.slice(0, firstSection);
    const body = firstSection === -1 ? [] : block.lines.slice(firstSection);
    const sections = splitBlocks(body.join('\n'), 3).map(parseSection);
    const version = unreleased ? null : dated![1]!;

    releases.push({
      version,
      target: unreleased ? (h.match(SEMVER)?.[0] ?? null) : null,
      slug: version ?? 'proximo',
      date: unreleased ? null : (dated![2] ?? null),
      unreleased,
      intro: parseIntro(head),
      sections,
      highlights: pickHighlights(sections),
      itemCount: sections.reduce((n, s) => n + s.items.length, 0),
      previous: null,
      releaseUrl: version ? `${repo}/releases/tag/v${version}` : null,
      compareUrl: null,
    });
  }

  const published = releases.filter((r) => !r.unreleased);
  published.forEach((r, i) => {
    const prev = published[i + 1];
    if (prev) {
      r.previous = prev.version;
      r.compareUrl = `${repo}/compare/v${prev.version}...v${r.version}`;
    }
  });
  return releases;
}

const MONTHS = [
  'enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio',
  'julio', 'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre',
];

/** `2026-10-29` a `29 de octubre de 2026`, sin depender de Intl. */
export function formatDate(iso: string): string {
  const m = iso.match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (!m) return iso;
  return `${Number(m[3])} de ${MONTHS[Number(m[2]) - 1]} de ${m[1]}`;
}
